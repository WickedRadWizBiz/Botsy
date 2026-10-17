"""Optional AI line-art accents for stationery pages (settings stationery.ai_accents,
default false).

When enabled, and only in a real (not dry) run, a design set gets a few small black line-art
images from the shop's image client (generators/imaging), one per accent subject, cleaned
to pure black on white with lines thick enough for the light-line rule at the printed size.
Each page then shows one of them in its header slot instead of the procedural motif, and
the set and its items are marked ai_assisted (the listing carries the AI disclosure).

Prompts hold only our own words from ACCENT_SUBJECTS (no theme text, brands or characters)
and are blocklist-checked right before each call (generators/imaging/prompts.py guard).
Disabled or in a dry run, nothing is generated and no client is built.
"""
from __future__ import annotations

import logging
from pathlib import Path

from generators.stationery.styles import SEASONS

log = logging.getLogger(__name__)

ACCENT_SUBJECTS: dict[str, tuple[str, ...]] = {
    "adhd_friendly": ("a row of three ticked checkboxes", "a simple lightning bolt", "a small alarm clock"),
    "minimalist": ("a single simple leaf", "a few overlapping circles and lines", "a small paper plane"),
    "cozy": ("a steaming mug", "a small teapot", "a lit candle"),
    "botanical": ("a small sprig of leaves", "a fern frond", "a small wildflower"),
    "student": ("a pencil", "a stack of books", "a desk lamp"),
    "teacher": ("an apple", "a stack of books", "a cup of pencils"),
    "wfh": ("a laptop", "a coffee mug", "a potted desk plant"),
    "autumn": ("a maple leaf", "an acorn", "a small pumpkin"),
    "winter": ("a snowflake", "a knitted mitten", "a pine branch"),
    "spring": ("a tulip", "a small bird on a branch", "a blossom branch"),
    "summer": ("a seashell", "a sun", "a slice of watermelon"),
}

PROMPT = ("Simple decorative line art of {subject} for the corner of a printable planner page. Clean black "
          "outlines on a plain white background, no shading, no gray, no color, no text, no letters, no "
          "numbers, no logos, one small centered object with lots of white space around it.")


def enabled(cfg: dict) -> bool:
    return bool(cfg.get("ai_accents"))


def accent_prompt(subject: str) -> str:
    return PROMPT.format(subject=subject)


def accent_subjects(style: str, season: str | None) -> tuple[str, ...]:
    if style == "seasonal":
        if season in ACCENT_SUBJECTS:
            return ACCENT_SUBJECTS[season]
        return tuple(ACCENT_SUBJECTS[s][0] for s in SEASONS)
    return ACCENT_SUBJECTS.get(style) or ACCENT_SUBJECTS["minimalist"]


def build(candidates: list[dict], n: int, build_dir: Path, *, settings: dict, cfg: dict, ctx: dict, client,
          blocklist, item_id: str | None = None, dry_run: bool = False) -> dict:
    """The design set's pages ({"pages", "skipped", "model"} for the pack engine). Without
    accents (the default, a dry run or no client) the planned pages as they are."""
    pages = [dict(p, quality=float(p.get("quality", 1.0))) for p in candidates[:n]]
    if not enabled(cfg) or dry_run or client is None or not pages:
        return {"pages": pages, "skipped": [], "model": None}

    from generators.imaging import cleanup
    from generators.imaging.common import check_budget, generate_batch, model_name
    from generators.imaging.prompts import PlannedImage
    from generators.stationery.layout import FLOOR_LIGHT_PT

    spec = pages[0].get("spec") or {}
    subjects = list(accent_subjects(str(spec.get("style") or ""), spec.get("season")))
    k = max(1, min(int(cfg.get("accent_images", 2)), len(pages), len(subjects)))
    check_budget(client, k)
    planned = [PlannedImage(s, accent_prompt(s)) for s in subjects]
    box_pt = float(cfg.get("accent_box_in", 0.95)) * 72.0
    light_pt = max(FLOOR_LIGHT_PT, float(cfg.get("light_line_pt", 0.6)))
    dpi = int(settings.get("print", {}).get("dpi", 300))

    def process(image, i: int) -> dict:
        clean = cleanup.clean_line_art(image, threshold=int(cfg.get("accent_threshold", 180)),
                                       despeckle_px=int(cfg.get("accent_despeckle_px", 12)), min_line_px=2,
                                       min_line_frac=light_pt / box_pt, aspect=1.0, pad_frac=0.05)
        rel = f"pages/accent-{i:02d}.png"
        cleanup.save_png(clean, Path(build_dir) / rel, dpi=dpi)
        return {"png": rel, "ink": cleanup.ink_fraction(clean)}

    results, skipped = generate_batch(client, planned, k, aspect_ratio="1:1",
                                      image_size=str(cfg.get("accent_image_size", "1K")), blocklist=blocklist,
                                      process=process, max_failures=int(cfg.get("accent_max_failures", 2)),
                                      item_id=item_id)
    out = []
    for j, page in enumerate(pages):
        plan_, r = results[j % len(results)]
        out.append(dict(page, asset={"accent": r["png"]}, ai_assisted=True,
                        spec=dict(page.get("spec") or {}, prompt=plan_.prompt, accent_subject=plan_.subject)))
    log.info("stationery: %d AI accent image(s) for %d pages", len(results), len(out))
    return {"pages": out, "skipped": skipped, "model": model_name(client)}
