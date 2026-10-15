"""Plumbing shared by the image generators (coloring_pages, stickers).

Each generator: check the brief, plan prompts (all blocklist-checked up front), check the
image budget, generate one image per subject (a spare subject replaces one the model
refuses), post-process into a scratch build folder, then swap the finished files in and
write manifest.json (docs/CONTRACTS.md 2). Building aside means a rerun that fails half way
(network, image cap, refusals) leaves the previous item exactly as it was.
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import TypeVar

from core.compliance import Blocklist, ComplianceResult, find_urls
from core.config import ROOT, today
from core.items import new_item_id, output_root, write_manifest
from generators.imaging.client import (
    FakeImageClient, GeminiImageClient, ImageCapExceeded, ImageClient, ImageGenerationError, run_client,
)
from generators.imaging.prompts import PlannedImage, as_list, guard

log = logging.getLogger(__name__)

T = TypeVar("T")

# Etsy digital listings: up to 5 files, 20 MB each, file names of at most 70 letters, digits,
# periods, underscores or hyphens, not editable after upload (help.etsy.com, "How to Manage
# Your Digital Listings"). QA reads 20 MB as 20,000,000 bytes, so we do too.
ETSY_MAX_FILES = 5
ETSY_MAX_FILE_MB = 20
ETSY_MAX_NAME_CHARS = 70

ITEM_SUBDIRS = ("files", "previews", "assets")
BRIEF_TEXT_FIELDS = ("theme", "keywords", "audience", "style_notes")


@dataclass
class ItemContext:
    niche: str
    theme: str
    item_id: str
    dir: Path
    date: str
    blocklist: Blocklist
    brief_check: ComplianceResult
    shop: dict = field(default_factory=dict)
    skip: bool = False                    # already drafted on Etsy: leave untouched
    review_reasons: list[str] = field(default_factory=list)


def start_item(brief: dict, niche: str, settings: dict, *, shop_id: str = "main", out_root=None,
               date: str | None = None) -> ItemContext:
    """Validate the brief, refuse blocked terms and URLs, and compute the item's id and directory."""
    if brief.get("niche", niche) != niche:
        raise ValueError(f"brief niche {brief.get('niche')!r} is not {niche!r}")
    theme = str(brief.get("theme") or "").strip()
    if not theme:
        raise ValueError("brief has no theme")
    shop = find_shop(settings, shop_id)
    blocklist = Blocklist.load(settings.get("compliance", {}).get("trademark_blocklist"))
    texts = {k: brief.get(k) for k in BRIEF_TEXT_FIELDS}
    check = blocklist.check_obj(texts)
    if check.blocked:
        raise ValueError(f"brief contains blocked terms {check.blocked}; not generating")
    marked = (brief.get("compliance") or {}).get("blocked") or []
    if marked:
        raise ValueError(f"brief is marked with blocked terms {marked}; not generating")
    urls = find_urls(texts)
    if urls:
        raise ValueError(f"brief contains URLs {urls[:3]}; briefs must not reference listings")
    date = check_date(date or today())
    item_id = new_item_id(date, niche, theme, salt=brief.get("brief_id") or "")
    d = (Path(out_root) if out_root else output_root(settings)) / item_id
    ctx = ItemContext(niche, theme, item_id, d, date, blocklist, check, shop)
    if check.flagged:
        ctx.review_reasons.append(f"brief mentions review terms {check.flagged}")
    if brief.get("needs_review"):
        ctx.review_reasons.append("brief marked needs_review by research")
    if (d / "publish.json").exists():
        log.warning("%s already has an Etsy draft; leaving its files untouched", item_id)
        ctx.skip = True
    return ctx


def check_date(value: str) -> str:
    """The item date becomes part of a folder name, so it must be a plain, real YYYY-MM-DD."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
        raise ValueError(f"date {value!r} is not YYYY-MM-DD")
    try:
        _date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"date {value!r} is not a real date: {exc}") from None
    return value


def find_shop(settings: dict, shop_id: str) -> dict:
    """The settings entry for shop_id. An unknown shop is an error, not a pack without a price."""
    for shop in settings.get("shops") or []:
        if shop.get("id") == shop_id:
            return shop
    raise ValueError(f"unknown shop id {shop_id!r}; known: {[s.get('id') for s in settings.get('shops') or []]}")


@contextmanager
def build_dir(ctx: ItemContext) -> Iterator[Path]:
    """A scratch folder next to the item (no manifest.json, so other stages ignore it),
    removed afterwards whether or not the build succeeded."""
    ctx.dir.parent.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix=f".build-{ctx.item_id}-", dir=ctx.dir.parent))
    try:
        for sub in ITEM_SUBDIRS:
            (build / sub).mkdir()
        yield build
    finally:
        shutil.rmtree(build, ignore_errors=True)


def commit(build: Path, d: Path, manifest: dict) -> None:
    """Swap the finished subfolders in (reruns leave no stale pages), then write the manifest."""
    d.mkdir(parents=True, exist_ok=True)
    for sub in ITEM_SUBDIRS:
        shutil.rmtree(d / sub, ignore_errors=True)
        (build / sub).replace(d / sub)
    write_manifest(d, manifest)


def image_params(settings: dict, kind: str) -> tuple[str, str]:
    """(aspect_ratio, image_size) for "coloring" or "sticker" from settings models.image."""
    image = settings.get("models", {}).get("image", {})
    aspect, size = {"coloring": ("3:4", "4K"), "sticker": ("1:1", "2K")}[kind]
    return str(image.get(f"aspect_ratio_{kind}", aspect)), str(image.get(f"image_size_{kind}", size))


def resolve_client(client: ImageClient | None, dry_run: bool, settings: dict, style: str,
                   fake_long_side_px: int) -> ImageClient:
    """Injected client > fake (dry run) > the run's shared Gemini client.

    A dry run never spends money: an injected real Gemini client is swapped for the fake.
    """
    if dry_run and isinstance(client, GeminiImageClient):
        log.warning("dry run: ignoring the injected Gemini client and using the offline fake")
        client = None
    if client is not None:
        return client
    if dry_run:
        return FakeImageClient(style, long_side_px=fake_long_side_px)
    return run_client(settings)


def check_budget(client: ImageClient, needed: int) -> None:
    """Fail before spending anything if the run cap cannot cover a whole pack."""
    remaining = getattr(client, "remaining", None)
    if remaining is not None and remaining < needed:
        raise ImageCapExceeded(f"pack needs {needed} images but only {remaining} remain under the run cap")


def generate_batch(client: ImageClient, planned: list[PlannedImage], n: int, *, aspect_ratio: str,
                   image_size: str, blocklist: Blocklist, process: Callable, max_failures: int
                   ) -> tuple[list[tuple[PlannedImage, T]], list[dict]]:
    """Generate and process images in plan order until n succeed.

    `process(image, index)` turns a raw image into the stage's result and may raise
    ImageGenerationError (e.g. blank page). Refused or unusable subjects are replaced by the
    next planned one, up to max_failures; other errors propagate. Returns (results, skipped).
    """
    done: list[tuple[PlannedImage, T]] = []
    skipped: list[dict] = []
    for plan in planned:
        if len(done) == n:
            break
        guard(plan.prompt, blocklist)                       # re-checked right before the API call
        try:
            image = client.generate(plan.prompt, aspect_ratio=aspect_ratio, image_size=image_size)
            done.append((plan, process(image, len(done) + 1)))
        except ImageGenerationError as exc:
            skipped.append({"subject": plan.subject, "reason": str(exc)})
            log.warning("subject %r skipped: %s", plan.subject, exc)
            if len(skipped) > max_failures:
                raise ImageGenerationError(f"{len(skipped)} subjects failed (max {max_failures}); "
                                           f"last: {exc}") from exc
    if len(done) < n:
        raise ImageGenerationError(f"only {len(done)} of {n} images succeeded; skipped {skipped}")
    return done, skipped


def file_name(slug: str, suffix: str) -> str:
    """'<slug><suffix>' that Etsy accepts as a digital file name: only letters, digits, '.',
    '_' and '-', and at most 70 characters (the theme slug is shortened to fit)."""
    suffix = re.sub(r"[^A-Za-z0-9._-]+", "-", suffix)
    room = ETSY_MAX_NAME_CHARS - len(suffix)
    if room < 3:
        raise ValueError(f"file name suffix {suffix!r} leaves no room for a name under {ETSY_MAX_NAME_CHARS} chars")
    return (slug[:room].rstrip("-") or "item") + suffix


def check_file_count(n: int) -> None:
    """Refuse up front (before any image is paid for) a pack Etsy could not accept."""
    if n > ETSY_MAX_FILES:
        raise ValueError(f"{n} digital files exceed Etsy's limit of {ETSY_MAX_FILES} per listing")


def oversize_reasons(d: Path, files: list[dict]) -> list[str]:
    """Review reasons for files above Etsy's per-file limit."""
    reasons = []
    for f in files:
        mb = (d / f["path"]).stat().st_size / 1e6
        if mb > ETSY_MAX_FILE_MB:
            reasons.append(f"{f['path']} is {mb:.1f} MB; Etsy allows {ETSY_MAX_FILE_MB} MB per file")
    if len(files) > ETSY_MAX_FILES:
        reasons.append(f"{len(files)} digital files; Etsy allows {ETSY_MAX_FILES} per listing")
    return reasons


def page_sizes_for(brief: dict, settings: dict) -> list[str]:
    """Brief's page sizes that settings know (default: all); only the first unless
    print.deliver_both_sizes is true."""
    known = list(settings["print"]["page_sizes"])
    asked = [s for s in as_list(brief.get("page_sizes")) if s in known] or known
    return asked if settings["print"].get("deliver_both_sizes", True) else asked[:1]


def repo_relative(path: str | Path | None) -> str | None:
    if path is None:
        return None
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(ROOT)
        except ValueError:
            pass
    return p.as_posix()


def base_manifest(ctx: ItemContext, brief: dict, *, brief_file, shop_id: str, settings: dict,
                  client: ImageClient, dry_run: bool, prompts: list[str], prompt_check: ComplianceResult) -> dict:
    """Manifest fields common to every image item; the caller adds files/previews/assets."""
    reasons = list(ctx.review_reasons)
    if prompt_check.flagged:
        reasons.append(f"prompts mention review terms {prompt_check.flagged}")
    price = brief.get("price_usd")
    return {
        "schema": "manifest/v1",
        "item_id": ctx.item_id,
        "niche": ctx.niche,
        "shop_id": shop_id,
        "brief_id": brief.get("brief_id"),
        "brief_file": repo_relative(brief_file),
        "theme": ctx.theme,
        "keywords": as_list(brief.get("keywords")),
        "audience": brief.get("audience"),
        "price_usd": price if price is not None else (ctx.shop.get("default_price_usd") or {}).get(ctx.niche),
        "created": ctx.date,
        "ai_assisted": True,
        "needs_review": bool(reasons),
        "generator": {"name": ctx.niche, "model": getattr(client, "model", type(client).__name__),
                      "dry_run": bool(dry_run)},
        "prompts": prompts,
        "files": [],
        "previews": [],
        "assets": [],
        "status": "generated",
        "review_reasons": reasons,
    }
