"""Printable stationery packs: planner and productivity pages (docs/PACKS.md section 1).

A pack-engine plugin (generators/packs, docs/PACKS.md section 3). Pages are procedural
reportlab layouts of our own (daily, weekly and monthly planners, time blocks, brain dumps,
task breakdowns, priorities, trackers, lists, meal and budget pages, reading logs, gratitude
and notes), so items are `ai_assisted: false` unless the optional AI line-art accents are
switched on (stationery.ai_accents, off by default; never in a dry run).

    PAGE_TYPES
    plan_pages(ctx, n, *, seed, settings) -> list[dict]     n distinct page specs, deterministic
    render_pages(pages, out_pdf, *, page_size, settings, ctx) -> int   one vector PDF
    preview(pages, out_dir, *, settings, ctx, limit=6) -> list[Path]  PNG previews
    generate(brief, **kw) -> list[Path]   generators.packs.generate_packs(brief, plugin=<this module>, **kw)

Style: the theme's words pick one of generators/stationery/styles.py STYLES (adhd_friendly,
minimalist, cozy, botanical, seasonal, student, teacher, wfh; default minimalist). A page
spec carries its type, layout variant, style, our own title and subtitle, and its
decoration motif, so a design set renders the same at any page size and a derived pack is
its master's pages laid out again for its own files.

Print rules (checked by the pen in generators/stationery/layout.py and recorded in the
manifest's pack fields): printed text at least stationery.min_font_pt (7 pt), writing lines
and rows at least stationery.min_line_gap_mm (6 mm) apart, light lines at least 0.5 pt,
structural lines at least print.min_line_weight_pt, everything inside print.margin_in.
Pages are undated (fill-in dates), so packs never go stale. Printed text never carries
brands, game-system names or medical wording; render refuses a page that would.

Review: a theme whose own words match no style (pages in the plain default look, nothing of
the theme on them) or that names a holiday (the season's look, no holiday pages) marks every
pack of the brief for review (brief_review_reasons), so its listing is checked by a person.
"""
from __future__ import annotations

import sys
from pathlib import Path

from core.config import load_settings, section
from generators.stationery import accents
from generators.stationery.catalog import CATALOG, PAGE_TYPES
from generators.stationery.plan import page_mix
from generators.stationery.plan import plan as _plan
from generators.stationery.render import measure, previews, render, text_problems
from generators.stationery.styles import HOLIDAY_WORDS, STYLES, style_for

__all__ = ["CATALOG", "DEFAULTS", "NICHE", "PAGE_TYPES", "STYLES", "brief_review_reasons", "build_pages",
           "check_printed_text", "config",
           "generate", "page_mix", "pack_fields", "plan_pages", "preview", "printed_text", "render_pages",
           "resolve_image_client", "review_reasons", "style_for"]

NICHE = "stationery"
AI_ASSISTED = False
FILE_LABEL = "planner"

# Settings section "stationery" (report: settings_yaml). Read with core.config.section().
DEFAULTS: dict = {
    "max_pages": 60,                  # largest pack (packs.max_size.stationery wins when set)
    "min_font_pt": 7.0,               # smallest printed text (never below 7)
    "min_line_gap_mm": 6.0,           # writing lines, rows and grids at least this far apart (never below 6)
    "light_line_pt": 0.6,             # writing and grid lines (never below 0.5)
    "structural_line_pt": 0.0,        # box borders; 0 = print.min_line_weight_pt
    "color_zones": True,              # light print-friendly tints in zone headers (false: light gray)
    "week_start": "auto",             # auto (US Letter: Sunday, A4: Monday) | monday | sunday
    "clock": "12h",                   # schedule labels: 12h ("7 am") | 24h ("07:00")
    "day_start_hour": 7,              # first hour on schedules
    "grid_pitch_mm": 6.35,            # dot and square grids (a quarter inch)
    "unique_type_share": 0.8,         # share of a pack's pages that are different page types
    "preview_dpi": 150,
    "footer_text": "For personal use only. Please do not share or resell this file.",
    "ai_accents": False,              # optional AI line-art accents (real runs only, image client)
    "accent_images": 2,               # accent images per design set
    "accent_image_size": "1K",
    "accent_max_failures": 2,
    "accent_box_in": 0.95,            # printed accent size (also the procedural motif slot)
    "accent_threshold": 180,
    "accent_despeckle_px": 12,
}


def config(settings: dict) -> dict:
    return section(settings, NICHE, DEFAULTS)


def generate(brief: dict, **kw) -> list[Path]:
    """Build every stationery pack of one brief: generators.packs.generate_packs with this
    module as the plugin. Returns one item dir per pack (master first)."""
    from generators.packs import generate_packs

    return generate_packs(brief, plugin=sys.modules[__name__], **kw)


# --- plugin API ------------------------------------------------------------------------------

def plan_pages(ctx: dict, n: int, *, seed: str, settings: dict) -> list[dict]:
    """n distinct page specs for one design set (generators/stationery/plan.py)."""
    return _plan(ctx, n, seed=seed, cfg=config(settings))


ACRONYMS = {"adhd": "ADHD", "wfh": "WFH"}


def _title(ctx: dict | None) -> str:
    """The PDF's title metadata: "<Theme> Planner Pages"."""
    words = str((ctx or {}).get("theme") or "").split()
    theme = " ".join(ACRONYMS.get(w.lower(), w[:1].upper() + w[1:]) for w in words)
    return f"{theme} Planner Pages" if theme else "Planner Pages"


def render_pages(pages: list[dict], out_pdf: Path, *, page_size: str, settings: dict, ctx: dict | None = None) -> int:
    """One vector PDF of the pack's pages for one page size (no title or answer pages).
    Returns its page count. ValueError when printed text would break a compliance rule."""
    render(pages, Path(out_pdf), page_size=page_size, settings=settings, cfg=config(settings), title=_title(ctx))
    return len(pages)


def _first_size(settings: dict, ctx: dict | None) -> str:
    sizes = list((ctx or {}).get("page_sizes") or []) or list(settings["print"]["page_sizes"])
    return sizes[0]


def preview(pages: list[dict], out_dir: Path, *, settings: dict, ctx: dict | None = None, limit: int = 6) -> list[Path]:
    """RGB PNG previews of the first `limit` pages at the first page size."""
    cfg = config(settings)
    return previews(pages, Path(out_dir), page_size=_first_size(settings, ctx), settings=settings, cfg=cfg,
                    limit=limit, dpi=int(cfg["preview_dpi"]), title=_title(ctx))


# --- optional engine hooks -------------------------------------------------------------------

def max_pack_size(settings: dict) -> int:
    return int(config(settings)["max_pages"])


def files_per_pack(page_sizes: list[str]) -> int:
    return len(page_sizes)


def printed_text(pages: list[dict], ctx: dict, settings: dict | None = None) -> list[str]:
    """Every string printed on the pages (titles, labels, footer), for the engine's final
    blocklist check."""
    settings = settings or load_settings()
    metrics = measure(pages, _first_size(settings, ctx), settings, config(settings))
    return list(dict.fromkeys(metrics.texts))


def brief_review_reasons(brief: dict, settings: dict) -> list[str]:
    """A person looks when the theme's own words match no stationery style: the pages then
    use a plain style (minimalist by default, or one taken from the style notes or keywords)
    and nothing on them shows the theme, so a "<theme> Planner Pages" listing may promise
    more than the pages give (a "galaxy" or "wedding" planner with no galaxy or wedding). A
    holiday theme ("christmas") gets its season's look but no holiday pages, so it is
    checked too."""
    theme = str(brief.get("theme") or "")
    style = brief.get("style_notes")
    keywords = brief.get("keywords")
    choice = style_for({"theme": theme, "style": style if isinstance(style, str) else "",
                        "keywords": [k for k in keywords if isinstance(k, str)] if isinstance(keywords, list) else []})
    if choice.source == "theme":
        if choice.style == "seasonal" and choice.matched in HOLIDAY_WORDS:
            return [f"stationery theme {theme!r} names a holiday: the pages are {choice.season or 'seasonal'} "
                    f"planner pages with the season's look and no {choice.matched}-specific pages; check that the "
                    "listing title and photos fit"]
        return []
    origin = {"style": "named in the style notes", "keywords": "taken from the keywords"}.get(choice.source,
                                                                                         "the default")
    return [f"stationery theme {theme!r} matches no planner style: the pages use the {choice.style} style "
            f"({origin}) and nothing on them shows the theme; check that the listing title and photos fit"]


def _reused_layouts(pages: list[dict]) -> list[int]:
    return [i for i, p in enumerate(pages, 1) if (p.get("spec") or {}).get("layout_reused")]


def review_reasons(pages: list[dict], *, settings: dict) -> list[str]:
    """A person looks when a page breaks a print rule (drawn outside the margins), or when a
    fresh pack had to reuse one of the theme's earlier layouts (every layout was used)."""
    cfg = config(settings)
    reasons = []
    for size in settings["print"]["page_sizes"]:
        m = measure(pages, size, settings, cfg)
        if m.out_of_bounds:
            reasons.append(f"{size}: {len(m.out_of_bounds)} drawing(s) outside the print margins")
    reused = _reused_layouts(pages)
    if reused:
        reasons.append(f"fresh pack pages {reused} repeat layouts from the theme's other packs with new titles "
                       "(every layout was used); check the listing's all-new pages wording")
    return reasons


def pack_fields(pages: list[dict], files: list[dict], *, settings: dict, ctx: dict, set_doc: dict) -> dict:
    """The manifest's pack fields: page count, style, contents and the measured print rules
    (QA's stationery checks: min font 7 pt, writing lines 6 mm apart)."""
    cfg = config(settings)
    sizes = [f["page_size"] for f in files if f.get("kind") == "pdf" and f.get("page_size")] \
        or [_first_size(settings, ctx)]
    checks = {s: measure(pages, s, settings, cfg).summary() for s in dict.fromkeys(sizes)}

    def low(key):
        vals = [c[key] for c in checks.values() if c.get(key) is not None]
        return min(vals) if vals else None

    specs = [p.get("spec") or {} for p in pages]
    qualities = [float(p.get("quality", 0.0)) if isinstance(p.get("quality"), (int, float)) else 0.0 for p in pages]
    return {
        "pages": len(pages),
        "title_pages": 0,
        "style": specs[0].get("style") if specs else None,
        "seasons": sorted({s.get("season") for s in specs if s.get("season")}),
        "undated": True,
        "ai_accents": any((p.get("asset") or {}).get("accent") for p in pages if isinstance(p.get("asset"), dict)),
        "contents": [{"page": i, "type": p.get("type"), "variant": s.get("variant"), "title": s.get("title")}
                     for i, (p, s) in enumerate(zip(pages, specs), 1)],
        # The style's most wanted page in this pack (1-based), a good front page for photos.
        "hero_page": qualities.index(max(qualities)) + 1 if qualities else None,
        "layout_reused_pages": _reused_layouts(pages),
        "min_font_pt": low("min_font_pt"),
        "min_line_gap_mm": low("min_line_gap_mm"),
        "print_checks": checks,
    }


def resolve_image_client(client, *, dry_run: bool, settings: dict):
    """The image client for AI accents: None unless stationery.ai_accents is on and this is
    a real run (nothing is built or paid for otherwise)."""
    if dry_run or not accents.enabled(config(settings)):
        return None
    from generators.imaging.common import fake_long_side, resolve_client

    return resolve_client(client, False, settings, "coloring", fake_long_side(settings))


def build_pages(candidates: list[dict], n: int, build: Path, *, settings: dict, ctx: dict, client, blocklist,
                item_id: str | None = None, dry_run: bool = False) -> dict:
    """The design set's pages; with AI accents on (real runs only) each page also gets an
    accent image in the set's pages/ folder (generators/stationery/accents.py)."""
    return accents.build(candidates, n, build, settings=settings, cfg=config(settings), ctx=ctx, client=client,
                         blocklist=blocklist, item_id=item_id, dry_run=dry_run)


def check_printed_text(texts, settings: dict) -> list[str]:
    """Why these strings may not be printed (blocked terms, medical wording); [] when clean."""
    return text_problems(texts, settings)
