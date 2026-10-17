"""Plumbing shared by the image niches (coloring_pages, stickers) under the pack engine.

The engine (generators/packs/engine.py) owns items, design sets and manifests. An image
plugin plans one prompt per page (all blocklist-checked before anything is paid for), checks
the run's image cap, then generates one image per subject (a spare subject replaces one the
model refuses or returns blank) and post-processes each into its design set's pages/ folder.
A set is built in a scratch folder and moved into place only when complete, so a build that
fails half way (network, image cap, refusals) leaves nothing behind and pays for nothing twice.
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from core.compliance import Blocklist
from core.config import section
from generators.imaging.client import (
    FakeImageClient, GeminiImageClient, ImageCapExceeded, ImageClient, ImageGenerationError, run_client,
)
from generators.imaging.prompts import PlannedImage, guard, unlisted_style_clauses

log = logging.getLogger(__name__)

T = TypeVar("T")


def image_params(settings: dict, kind: str) -> tuple[str, str]:
    """(aspect_ratio, image_size) for "coloring" or "sticker" from settings models.image."""
    image = settings.get("models", {}).get("image", {})
    aspect, size = {"coloring": ("3:4", "4K"), "sticker": ("1:1", "2K")}[kind]
    return str(image.get(f"aspect_ratio_{kind}", aspect)), str(image.get(f"image_size_{kind}", size))


def fake_long_side(settings: dict) -> int:
    """FakeImageClient size for dry runs: the smaller of imaging.fake_long_side_px and
    packs.dry_run_fake_long_side_px, so a 50-page dry-run pack stays fast."""
    img = section(settings, "imaging", {"fake_long_side_px": 1200})
    packs = section(settings, "packs", {"dry_run_fake_long_side_px": 640})
    sizes = [int(v) for v in (img.get("fake_long_side_px"), packs.get("dry_run_fake_long_side_px"))
             if isinstance(v, int) and not isinstance(v, bool) and v > 0]
    return min(sizes) if sizes else 640


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
    """Fail before spending anything if the run cap cannot cover a whole design set."""
    remaining = getattr(client, "remaining", None)
    if remaining is not None and remaining < needed:
        raise ImageCapExceeded(f"pack needs {needed} images but only {remaining} remain under the run cap")


def model_name(client: ImageClient) -> str:
    return str(getattr(client, "model", None) or type(client).__name__)


def style_review_reasons(brief: dict) -> list[str]:
    """Brief style_notes clauses that are not on the prompt allowlist: left out of every
    prompt, and the item is marked for review (brief free text never reaches a prompt)."""
    unlisted = unlisted_style_clauses(brief.get("style_notes"))
    if not unlisted:
        return []
    log.warning("%s: %d brief style_notes clause(s) are not on the prompt allowlist; left out of the prompts",
                brief.get("brief_id"), len(unlisted))
    return [f"{len(unlisted)} brief style_notes clause(s) are not on the prompt allowlist "
            "(generators/imaging/prompts.py STYLE_CLAUSES) and were left out of the prompts"]


def planned_images(candidates: list[dict]) -> list[PlannedImage]:
    """The engine's page candidates as prompts for generate_batch (same order)."""
    return [PlannedImage(str(p.get("subject") or ""), str((p.get("spec") or {}).get("prompt") or ""))
            for p in candidates]


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


def by_plan(candidates: list[dict], planned: list[PlannedImage]) -> dict[int, dict]:
    """id(PlannedImage) -> its page candidate (generate_batch returns the same objects)."""
    return {id(p): c for p, c in zip(planned, candidates)}


def copy_asset(src: str | Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
