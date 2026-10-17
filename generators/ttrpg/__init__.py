"""Printable tabletop RPG packs: character sheets, trackers, world-building worksheets and
map papers (docs/PACKS.md section 1).

A pack-engine plugin (generators/packs, docs/PACKS.md section 3). Pages are procedural
reportlab layouts of our own (character sheet, spell or ability tracker, inventory and
loot, campaign tracker, session notes, NPC tracker, quest log, initiative tracker,
encounter planner, town, faction, deity and region worksheets, hex map paper, square grid
map paper, dungeon grid, dice and roll log), so items are `ai_assisted: false` unless the
optional AI line-art accents are switched on (ttrpg.ai_accents, off by default; never in a
dry run).

    PAGE_TYPES
    plan_pages(ctx, n, *, seed, settings) -> list[dict]     n distinct page specs, deterministic
    render_pages(pages, out_pdf, *, page_size, settings, ctx) -> int   one vector PDF
    preview(pages, out_dir, *, settings, ctx, limit=6) -> list[Path]  PNG previews
    generate(brief, **kw) -> list[Path]   generators.packs.generate_packs(brief, plugin=<this module>, **kw)

Game systems (CLAUDE.md exception, 2026-10-16): ctx["system"] is an id from
config/ttrpg_systems.yaml (core.systems), or "generic". It only decides which fields the
character sheet, spell tracker, inventory and combat trackers carry (the system's own
sheet_fields, our wording). Printed pages, file names and PDF metadata never carry a game
system's name or owner: every printed string is checked against the blocklist with no
system allowed and against every registered system name, and a page that fails is refused.
Only the listing may name the system, as a compatibility reference with its disclaimer.

Style: the theme's words pick a genre (generators/ttrpg/styles.py: classic_fantasy,
dark_fantasy, scifi, cosmic_horror, cyberpunk, pirate, western, cozy) and a focus
(generators/ttrpg/plan.py: campaign, character, maps, gm, worldbuilding, journal). A page
spec carries its type, layout variant, genre, our own title and subtitle, its emblem and
its sections, so a design set renders the same at any page size.

Print rules (checked by the pen in generators/ttrpg/layout.py and recorded in the
manifest's pack fields): printed text at least ttrpg.min_font_pt (7 pt), writing lines,
rows and grid pitches at least ttrpg.min_line_gap_mm (6 mm) apart, light lines at least
0.5 pt, everything inside print.margin_in. Pages are undated fill-in sheets.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from core.config import load_settings, section
from generators.ttrpg import accents
from generators.ttrpg.catalog import PAGE_TYPES, TYPE_LABELS, VARIANTS
from generators.ttrpg.plan import focus_for, is_starter, page_mix
from generators.ttrpg.plan import plan as _plan
from generators.ttrpg.render import measure, previews, text_problems
from generators.ttrpg.render import render as render_pdf
from generators.ttrpg.styles import GENRES, genre_for

__all__ = ["DEFAULTS", "GENRES", "NICHE", "PAGE_TYPES", "TYPE_LABELS", "VARIANTS", "brief_review_reasons",
           "build_pages", "check_printed_text", "config", "focus_for", "generate", "genre_for", "max_pack_size",
           "pack_fields", "page_mix", "plan_pages", "preview", "printed_text", "render_pages",
           "resolve_image_client", "review_reasons"]

NICHE = "ttrpg"
AI_ASSISTED = False
FILE_LABEL = "rpg"

# Settings section "ttrpg" (report: settings_yaml). Read with core.config.section().
# core.systems reads ttrpg.systems_file from the same section.
DEFAULTS: dict = {
    "max_pages": 40,                  # largest pack (packs.max_size.ttrpg wins when set)
    "min_font_pt": 7.0,               # smallest printed text (never below 7)
    "min_line_gap_mm": 6.0,           # writing lines, rows and grids at least this far apart (never below 6)
    "line_gap_mm": 7.0,               # writing lines (notes, prompts, write-in fields)
    "row_gap_mm": 6.4,                # table, checklist and tracker rows
    "light_line_pt": 0.6,             # writing and grid lines (never below 0.5)
    "structural_line_pt": 1.0,        # section boxes
    "frame_line_pt": 1.6,             # page frames
    "tints": True,                    # light print-friendly tints in headings (false: light gray)
    "default_genre": "classic_fantasy",
    "preview_dpi": 150,
    "footer_text": "For personal use only. Please do not share or resell this file.",
    "ai_accents": False,              # optional AI line-art accents (real runs only, image client)
    "accent_images": 2,               # accent images per design set
    "accent_image_size": "1K",
    "accent_max_failures": 2,
    "accent_box_in": 0.36,            # printed accent size (the header emblem slot)
    "accent_threshold": 180,
    "accent_despeckle_px": 12,
}


def config(settings: dict) -> dict:
    return section(settings, NICHE, DEFAULTS)


def generate(brief: dict, **kw) -> list[Path]:
    """Build every ttrpg pack of one brief: generators.packs.generate_packs with this module
    as the plugin. Returns one item dir per pack (master first)."""
    from generators.packs import generate_packs

    return generate_packs(brief, plugin=sys.modules[__name__], **kw)


# --- plugin API ------------------------------------------------------------------------------

def plan_pages(ctx: dict, n: int, *, seed: str, settings: dict) -> list[dict]:
    """n distinct page specs for one design set (generators/ttrpg/plan.py)."""
    return _plan(ctx, n, seed=seed, cfg=config(settings), settings=settings)


def _title(ctx: dict | None) -> str:
    """The PDF's title metadata: "<Theme> RPG Pages" (render falls back to a neutral title
    if that would print a blocked or game-system term)."""
    words = str((ctx or {}).get("theme") or "").split()
    theme = " ".join(w[:1].upper() + w[1:] for w in words)
    return f"{theme} RPG Pages" if theme else "Tabletop RPG Pages"


def render_pages(pages: list[dict], out_pdf: Path, *, page_size: str, settings: dict, ctx: dict | None = None) -> int:
    """One vector PDF of the pack's pages for one page size (no title pages). Returns its
    page count. ValueError when printed text would break a compliance rule or a page does
    not fit at the print rules."""
    render_pdf(pages, Path(out_pdf), page_size=page_size, settings=settings, cfg=config(settings), title=_title(ctx))
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


def brief_review_reasons(brief: dict, settings: dict) -> list[str]:
    """A person looks when the brief names a game system id the registry does not list
    (the sheets then use the system-neutral fields)."""
    from core import systems

    system = brief.get("system")
    if not isinstance(system, str) or not system.strip() or system.strip() == systems.default_system(settings):
        return []
    if systems.is_known(system.strip(), settings):
        return []
    return [f"brief names game system id {system.strip()!r}, which config/ttrpg_systems.yaml does not list; "
            "the sheets use system-neutral fields"]


def printed_text(pages: list[dict], ctx: dict, settings: dict | None = None) -> list[str]:
    """Every string printed on the pages (titles, labels, footer, PDF title), for the
    engine's final blocklist check."""
    settings = settings or load_settings()
    cfg = config(settings)
    texts: list[str] = []
    for size in dict.fromkeys([_first_size(settings, ctx), *((ctx or {}).get("page_sizes") or [])]):
        texts += measure(pages, size, settings, cfg).texts
    return list(dict.fromkeys(texts + [_title(ctx)]))


def review_reasons(pages: list[dict], *, settings: dict) -> list[str]:
    """A person looks when a page breaks a print rule (drawn outside the margins, a label
    that does not fit). Review terms in printed text are the engine's (printed_text)."""
    cfg = config(settings)
    reasons = []
    for size in settings["print"]["page_sizes"]:
        m = measure(pages, size, settings, cfg)
        if m.out_of_bounds:
            reasons.append(f"{size}: {len(m.out_of_bounds)} drawing(s) outside the print margins")
        if m.overflow:
            reasons.append(f"{size}: {len(m.overflow)} label(s) too wide for their space")
    return reasons


def pack_fields(pages: list[dict], files: list[dict], *, settings: dict, ctx: dict, set_doc: dict) -> dict:
    """The manifest's pack fields: page count, genre, focus, contents and the measured print
    rules (QA's ttrpg checks: min font 7 pt, writing lines 6 mm apart). The game system is
    the manifest's own `system` field (an id); nothing here names it."""
    cfg = config(settings)
    sizes = [f["page_size"] for f in files if f.get("kind") == "pdf" and f.get("page_size")] \
        or [_first_size(settings, ctx)]
    checks = {s: measure(pages, s, settings, cfg).summary() for s in dict.fromkeys(sizes)}

    def low(key):
        vals = [c[key] for c in checks.values() if c.get(key) is not None]
        return min(vals) if vals else None

    specs = [p.get("spec") or {} for p in pages]
    types = [p.get("type") for p in pages]
    return {
        "pages": len(pages),
        "title_pages": 0,
        "genre": specs[0].get("genre") if specs else None,
        "focus": focus_for(ctx),
        "kit": "campaign starter" if is_starter(types) else None,
        "system_fields": any(s.get("fields_from") == "system" for s in specs),
        "undated": True,
        "ai_accents": any((p.get("asset") or {}).get("accent") for p in pages if isinstance(p.get("asset"), dict)),
        "type_labels": {t: TYPE_LABELS[t] for t in dict.fromkeys(types) if t in TYPE_LABELS},
        "type_counts": dict(Counter(types)),
        "contents": [{"page": i, "type": p.get("type"), "variant": s.get("variant"), "title": s.get("title"),
                      "subtitle": s.get("subtitle")}
                     for i, (p, s) in enumerate(zip(pages, specs), 1)],
        "min_font_pt": low("min_font_pt"),
        "min_line_gap_mm": low("min_line_gap_mm"),
        "print_checks": checks,
    }


def resolve_image_client(client, *, dry_run: bool, settings: dict):
    """The image client for AI accents: None unless ttrpg.ai_accents is on and this is a
    real run (nothing is built or paid for otherwise)."""
    if dry_run or not accents.enabled(config(settings)):
        return None
    from generators.imaging.common import fake_long_side, resolve_client

    return resolve_client(client, False, settings, "coloring", fake_long_side(settings))


def build_pages(candidates: list[dict], n: int, build: Path, *, settings: dict, ctx: dict, client, blocklist,
                item_id: str | None = None, dry_run: bool = False) -> dict:
    """The design set's pages; with AI accents on (real runs only) each page also gets an
    accent image in the set's pages/ folder (generators/ttrpg/accents.py)."""
    return accents.build(candidates, n, build, settings=settings, cfg=config(settings), ctx=ctx, client=client,
                         blocklist=blocklist, item_id=item_id, dry_run=dry_run)


def check_printed_text(texts, settings: dict) -> list[str]:
    """Why these strings may not be printed (blocked terms, game-system names, medical
    wording); [] when clean."""
    return text_problems(texts, settings)
