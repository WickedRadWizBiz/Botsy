"""Plumbing shared by the image generators (coloring_pages, stickers).

Each generator: check the brief (generators.shared.check_brief), plan prompts (all
blocklist-checked up front), check the image budget, generate one image per subject (a spare
subject replaces one the model refuses), post-process into a scratch build folder, then swap
the finished files in and write manifest.json (docs/CONTRACTS.md 2). Building aside means a
rerun that fails half way (network, image cap, refusals) leaves the previous item exactly as
it was; a rerun that succeeds drops the old QA report, mockups and listing.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from core.compliance import Blocklist, ComplianceResult
from core.config import today
from core.items import new_item_id, output_root
from generators.imaging.client import (
    FakeImageClient, GeminiImageClient, ImageCapExceeded, ImageClient, ImageGenerationError, run_client,
)
from generators.imaging.prompts import PlannedImage, guard, unlisted_style_clauses
from generators.shared import (
    ETSY_MAX_FILE_BYTES, ETSY_MAX_FILE_MB, ETSY_MAX_FILES, as_list, brief_price, check_brief, find_shop, item_date,
    keep_existing, recorded_brief_file, swap_in,
)

log = logging.getLogger(__name__)

T = TypeVar("T")

ITEM_SUBDIRS = ("files", "previews", "assets")


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
               date: str | None = None, brief_file=None, dry_run: bool = False) -> ItemContext:
    """Check the brief (generators.shared.check_brief) and compute the item's id and directory.

    Raises ValueError for a brief that breaks a compliance rule and for a dry run that
    would replace a real item; sets ctx.skip for an item that already has a real Etsy draft.
    """
    checked = check_brief(brief, niche, settings, brief_file)
    shop = find_shop(settings, shop_id)
    date = item_date(date or today())
    item_id = new_item_id(date, niche, checked.theme, salt=brief.get("brief_id") or "")
    d = (Path(out_root) if out_root else output_root(settings)) / item_id
    ctx = ItemContext(niche, checked.theme, item_id, d, date, checked.blocklist, checked.check, shop,
                      review_reasons=list(checked.review_reasons))
    ctx.skip = keep_existing(d, item_id, dry_run)
    unlisted = unlisted_style_clauses(brief.get("style_notes"))
    if unlisted:
        log.warning("%s: %d brief style_notes clause(s) are not on the prompt allowlist; left out of the "
                    "prompts", item_id, len(unlisted))
        ctx.review_reasons.append(f"{len(unlisted)} brief style_notes clause(s) are not on the prompt "
                                  "allowlist (generators/imaging/prompts.py STYLE_CLAUSES) and were left out "
                                  "of the prompts")
    return ctx


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
    """Swap the finished subfolders in (reruns leave no stale pages, QA report, mockups or
    listing; generators.shared.swap_in), then write the manifest."""
    swap_in(build, d, ITEM_SUBDIRS, manifest)


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
                   image_size: str, blocklist: Blocklist, process: Callable, max_failures: int,
                   item_id: str | None = None) -> tuple[list[tuple[PlannedImage, T]], list[dict]]:
    """Generate and process images in plan order until n succeed.

    `process(image, index)` turns a raw image into the stage's result and may raise
    ImageGenerationError (e.g. blank page). Refused or unusable subjects are replaced by the
    next planned one, up to max_failures; other errors propagate. Returns (results, skipped).
    `item_id` is given to a client that records spending (GeminiImageClient.item_id), so the
    spend ledger names the item each image was for.
    """
    if item_id is not None and hasattr(client, "item_id"):
        client.item_id = item_id
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


def check_file_count(n: int) -> None:
    """Refuse up front (before any image is paid for) a pack Etsy could not accept."""
    if n > ETSY_MAX_FILES:
        raise ValueError(f"{n} digital files exceed Etsy's limit of {ETSY_MAX_FILES} per listing")


def oversize_reasons(d: Path, files: list[dict]) -> list[str]:
    """Review reasons for files above Etsy's per-file limit."""
    reasons = []
    for f in files:
        size = (d / f["path"]).stat().st_size
        mb = size / 1e6
        if size > ETSY_MAX_FILE_BYTES:
            reasons.append(f"{f['path']} is {mb:.1f} MB; Etsy allows {ETSY_MAX_FILE_MB} MB per file")
    if len(files) > ETSY_MAX_FILES:
        reasons.append(f"{len(files)} digital files; Etsy allows {ETSY_MAX_FILES} per listing")
    return reasons


def base_manifest(ctx: ItemContext, brief: dict, *, brief_file, shop_id: str, settings: dict,
                  client: ImageClient, dry_run: bool, prompts: list[str], prompt_check: ComplianceResult) -> dict:
    """Manifest fields common to every image item; the caller adds files/previews/assets."""
    reasons = list(ctx.review_reasons)
    if prompt_check.flagged:
        reasons.append(f"prompts mention review terms {prompt_check.flagged}")
    price = brief_price(brief, ctx.shop, ctx.niche, reasons)
    return {
        "schema": "manifest/v1",
        "item_id": ctx.item_id,
        "niche": ctx.niche,
        "shop_id": shop_id,
        "brief_id": brief.get("brief_id"),
        "brief_file": recorded_brief_file(brief_file),
        "theme": ctx.theme,
        "keywords": as_list(brief.get("keywords")),
        "audience": brief.get("audience"),
        "price_usd": price,
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
