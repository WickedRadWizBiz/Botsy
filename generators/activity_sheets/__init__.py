"""Procedural children's activity sheets (mazes, word searches, tracing, dot-to-dot), as packs.

A pack-engine plugin (generators/packs, docs/PACKS.md section 3). No image model is involved,
so items are `ai_assisted: false`. One brief becomes one item per size in brief["pack_sizes"]
(else [page_count]): output/<item_id>/ with one vector PDF per page size, PNG previews of the
first pages and manifest.json (docs/CONTRACTS.md section 2). A design set's page is a
procedural spec (type, grid sizes, seed, words, lesson rows or picture), rendered at any page
size, so a derived pack is its master's puzzles laid out again for its own pages.

Page count: the pack size is the number of activity pages. Each PDF also has a title page in
front and answer-key pages at the back, which are not counted; the manifest's files[].pages
is the real PDF page count including them.

Everything is seeded from the design set id, which is deterministic for (date, theme,
brief_id, pack), so rerunning the same brief on the same date gives byte-identical PDFs.
The brief is checked first (generators/shared.py: 10-listing evidence, no URLs, ids or
blocked terms). A rerun builds into a scratch folder and swaps it in only when every file
is written, dropping the old QA report, mockups and listing; it never touches an item
that already has a real Etsy draft (publish.json), and a dry run never replaces a real item.
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

from core.compliance import Blocklist
from core.config import load_settings, section
from core.render import POINTS_PER_INCH, page_size_points, pdf_info, render_pdf_pages
from generators.activity_sheets.layout import PageGeometry, Style
from generators.activity_sheets.pack import KINDS, build_pack, plan_specs, render_pdf
from generators.shared import page_sizes_for

__all__ = ["DEFAULTS", "KINDS", "NICHE", "PAGE_TYPES", "generate", "page_sizes_for", "plan_pages", "preview",
           "render_pages"]

log = logging.getLogger(__name__)

NICHE = "activity_sheets"

DEFAULTS: dict = {
    "difficulty": "auto",            # easy | medium | hard | auto (from the brief's audience ages)
    "auto_easy_max_age": 5,          # auto: youngest age <= this -> easy, otherwise medium
    "max_pages": 100,
    "levels": {
        "easy": {
            "maze": {"cols": 8, "rows": 10},
            "word_search": {"words": 8, "max_size": 10, "directions": ["right", "down"]},
            "dot_to_dot": {"dots": 20},
        },
        "medium": {
            "maze": {"cols": 12, "rows": 15},
            "word_search": {"words": 12, "max_size": 13, "directions": ["right", "down", "down_right"]},
            "dot_to_dot": {"dots": 35},
        },
        "hard": {
            "maze": {"cols": 16, "rows": 20},
            "word_search": {"words": 15, "max_size": 15,
                            "directions": ["right", "down", "down_right", "up_right",
                                           "left", "up", "up_left", "down_left"]},
            "dot_to_dot": {"dots": 50},
        },
    },
    "tracing": {"style": "gray", "letters_per_page": 2, "numbers_per_page": 5, "words_per_page": 5},
    "dot_to_dot": {"faint_preview": False},
    "answer_key_grid": [2, 2],       # answer keys per page: cols x rows
    "preview_pages": 6,
    "preview_dpi": 150,
    "footer_text": "For personal use only. Please do not share, resell or redistribute this file.",
}


PAGE_TYPES = KINDS
AI_ASSISTED = False
FILE_LABEL = "activity-pack"


def _cfg(settings: dict) -> dict:
    return section(settings, NICHE, DEFAULTS)


def generate(brief: dict, *, brief_file: str | Path | None = None, shop_id: str = "main",
             out_root: str | Path | None = None, date: str | None = None, dry_run: bool = False,
             client=None, settings: dict | None = None, **kw) -> list[Path]:
    """Build every activity pack of one brief. Returns one item dir per pack (master first).

    `dry_run` and `client` are accepted for the shared generator signature: nothing here
    calls a network service, so a dry run produces the same files.
    """
    from generators.packs import generate_packs

    return generate_packs(brief, plugin=sys.modules[__name__], brief_file=brief_file, shop_id=shop_id,
                          out_root=out_root, date=date, dry_run=dry_run, client=client,
                          settings=settings or load_settings(), **kw)


# --- plugin hooks ----------------------------------------------------------------------------

def default_pack_size(settings: dict) -> int:
    return int((settings.get("niches", {}).get(NICHE, {}).get("pages_per_pack") or [10])[0])


def max_pack_size(settings: dict) -> int:
    return int(_cfg(settings)["max_pages"])


def files_per_pack(page_sizes: list[str]) -> int:
    return len(page_sizes)


def plan_pages(ctx: dict, n: int, *, seed: str, settings: dict) -> list[dict]:
    """n activity page specs, types round-robin (generators/activity_sheets/pack.py plan_specs).
    Puzzle words are blocklist-checked as they are picked."""
    blocklist = Blocklist.load((settings.get("compliance") or {}).get("trademark_blocklist"))
    return plan_specs(ctx, n, seed=seed, cfg=_cfg(settings), blocklist=blocklist, settings=settings)


def _style(settings: dict, cfg: dict) -> Style:
    pr = settings["print"]
    return Style(min_line_pt=float(pr["min_line_weight_pt"]), footer_text=cfg["footer_text"],
                 trace_style=cfg["tracing"]["style"])


def render_pages(pages: list[dict], out_pdf: Path, *, page_size: str, settings: dict, ctx: dict) -> int:
    """Title page, the pack's activity pages (numbered from 2) and answer keys for one page
    size. Returns the PDF's page count."""
    cfg = _cfg(settings)
    pack = build_pack(pages, ctx)
    w, h = page_size_points(page_size, settings)
    geo = PageGeometry(page_size, w, h, float(settings["print"]["margin_in"]) * POINTS_PER_INCH)
    render_pdf(pack, geo, Path(out_pdf), _style(settings, cfg), cfg)
    return pdf_info(out_pdf)["pages"]


def preview(pages: list[dict], out_dir: Path, *, settings: dict, ctx: dict, limit: int = 6) -> list[Path]:
    """PNG renders of the first `limit` PDF pages (title page first), first page size."""
    cfg = _cfg(settings)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    size = (list(ctx.get("page_sizes") or []) or list(settings["print"]["page_sizes"]))[0]
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "preview.pdf"
        n = min(limit, render_pages(pages, pdf, page_size=size, settings=settings, ctx=ctx))
        out = []
        for i, img in enumerate(render_pdf_pages(pdf, dpi=int(cfg["preview_dpi"]), pages=list(range(n))), 1):
            p = out_dir / f"page-{i:02d}.png"
            img.convert("RGB").save(p, "PNG")
            out.append(p)
    return out


def manifest_previews(build: Path, files: list[dict], pages: list[dict], *, settings: dict, ctx: dict) -> list[dict]:
    """The first activity_sheets.preview_pages pages of the first PDF (title page first)."""
    from generators.packs.engine import pdf_previews

    cfg = _cfg(settings)
    return pdf_previews(build, files, int(cfg["preview_pages"]), dpi=int(cfg["preview_dpi"]))


def printed_text(pages: list[dict], ctx: dict) -> list[str]:
    """Every theme-derived string that ends up on a page, for the final blocklist check."""
    return build_pack(pages, ctx).printed_text()


def pack_fields(pages: list[dict], files: list[dict], *, settings: dict, ctx: dict, set_doc: dict) -> dict:
    pack = build_pack(pages, ctx)
    first = next((f for f in files if f.get("kind") == "pdf"), None)
    total = int(first["pages"]) if first else 1 + len(pages)
    return {
        "activity_pages": len(pack.pages),
        "title_pages": 1,
        "answer_key_pages": total - 1 - len(pack.pages),
        "difficulty": pack.difficulty,
        "activity_types": pack.kinds,
        "contents": [{"page": p.number, "type": p.kind, "title": p.title} for p in pack.pages],
    }
