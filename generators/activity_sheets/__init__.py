"""Procedural children's activity sheets (mazes, word searches, tracing, dot-to-dot).

No image model is involved, so items are `ai_assisted: false`. One brief becomes one
pack: output/<item_id>/ with one vector PDF per page size, PNG previews of the first
pages and manifest.json (docs/CONTRACTS.md section 2).

Page count: brief["page_count"] is the number of activity pages. Each PDF also has a
title page in front and answer-key pages at the back, which are not counted; the
manifest's files[].pages is the real PDF page count including them.

Everything is seeded from the item id, which is deterministic for (date, theme,
brief_id), so rerunning the same brief on the same date gives byte-identical PDFs.
The brief is checked first (generators/shared.py: 10-listing evidence, no URLs, ids or
blocked terms). A rerun builds into a scratch folder and swaps it in only when every file
is written, dropping the old QA report, mockups and listing; it never touches an item
that already has a real Etsy draft (publish.json), and a dry run never replaces a real item.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from core.config import load_settings, section, today
from core.items import new_item_id, output_root, slugify
from core.render import POINTS_PER_INCH, page_size_points, pdf_info, render_pdf_pages
from generators.activity_sheets.layout import PageGeometry, Style
from generators.activity_sheets.pack import KINDS, plan_pack, render_pdf
from generators.shared import (
    as_list, brief_price, check_brief, file_name, find_shop, item_date, keep_existing, page_sizes_for,
    recorded_brief_file, swap_in,
)

__all__ = ["DEFAULTS", "KINDS", "NICHE", "generate", "page_sizes_for"]

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


def generate(brief: dict, *, brief_file: str | Path | None = None, shop_id: str = "main",
             out_root: str | Path | None = None, date: str | None = None, dry_run: bool = False,
             client=None, settings: dict | None = None) -> list[Path]:
    """Build one activity pack from one brief. Returns [item_dir].

    `dry_run` and `client` are accepted for the shared generator signature and ignored:
    nothing here calls a network service, so a dry run produces the same files.
    """
    settings = settings or load_settings()
    cfg = section(settings, NICHE, DEFAULTS)
    checked = check_brief(brief, NICHE, settings, brief_file)
    theme, blocklist = checked.theme, checked.blocklist
    shop = find_shop(settings, shop_id)

    date = item_date(date or today())
    item_id = new_item_id(date, NICHE, theme, salt=brief.get("brief_id") or "")
    d = (Path(out_root) if out_root else output_root(settings)) / item_id
    if keep_existing(d, item_id, dry_run):
        return [d]

    pack = plan_pack(brief, item_id, cfg, blocklist, settings)
    printed = blocklist.check(*pack.printed_text())
    if printed.blocked:
        raise ValueError(f"generated pages contain blocked terms {printed.blocked}")
    reasons = list(checked.review_reasons)
    if printed.flagged:
        reasons.append(f"printed pages mention review terms {printed.flagged}")
    price = brief_price(brief, shop, NICHE, reasons)

    pr = settings["print"]
    style = Style(min_line_pt=float(pr["min_line_weight_pt"]), footer_text=cfg["footer_text"],
                  trace_style=cfg["tracing"]["style"])
    d.parent.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix=f".build-{item_id}-", dir=d.parent))
    try:
        files = []
        for name in page_sizes_for(brief, settings):
            w, h = page_size_points(name, settings)
            geo = PageGeometry(name, w, h, float(pr["margin_in"]) * POINTS_PER_INCH)
            rel = "files/" + file_name(slugify(pack.theme), f"-activity-pack-{name}.pdf")
            render_pdf(pack, geo, build / rel, style, cfg)
            files.append({"path": rel, "kind": "pdf", "page_size": name,
                          "pages": pdf_info(build / rel)["pages"], "dpi": int(pr.get("dpi", 300))})
        (build / "previews").mkdir()
        previews = _write_previews(build, files[0], cfg)
        manifest = {
            "schema": "manifest/v1",
            "item_id": item_id,
            "niche": NICHE,
            "shop_id": shop_id,
            "brief_id": brief.get("brief_id"),
            "brief_file": recorded_brief_file(brief_file),
            "theme": theme,
            "keywords": as_list(brief.get("keywords")),
            "audience": brief.get("audience"),
            "price_usd": price,
            "created": date,
            "ai_assisted": False,
            "needs_review": bool(reasons),
            "generator": {"name": NICHE, "dry_run": bool(dry_run)},
            "prompts": [],
            "files": files,
            "previews": previews,
            "assets": [],
            "status": "generated",
            "pack": {
                "activity_pages": len(pack.pages),
                "title_pages": 1,
                "answer_key_pages": files[0]["pages"] - 1 - len(pack.pages),
                "difficulty": pack.difficulty,
                "activity_types": pack.kinds,
                "contents": [{"page": p.number, "type": p.kind, "title": p.title} for p in pack.pages],
            },
            "review_reasons": reasons,
        }
        swap_in(build, d, ("files", "previews"), manifest)   # reruns leave no stale pages or QA outputs
    finally:
        shutil.rmtree(build, ignore_errors=True)
    log.info("generated %s (%d activity pages, %s)", item_id, len(pack.pages), [f["page_size"] for f in files])
    return [d]


def _write_previews(d: Path, pdf: dict, cfg: dict) -> list[dict]:
    n = min(int(cfg["preview_pages"]), pdf["pages"])
    images = render_pdf_pages(d / pdf["path"], dpi=int(cfg["preview_dpi"]), pages=list(range(n)))
    out = []
    for i, img in enumerate(images, start=1):
        rel = f"previews/page-{i:02d}.png"
        img.save(d / rel, "PNG")
        out.append({"path": rel, "page": i})
    return out
