"""Procedural children's activity sheets (mazes, word searches, tracing, dot-to-dot).

No image model is involved, so items are `ai_assisted: false`. One brief becomes one
pack: output/<item_id>/ with one vector PDF per page size, PNG previews of the first
pages and manifest.json (docs/CONTRACTS.md section 2).

Page count: brief["page_count"] is the number of activity pages. Each PDF also has a
title page in front and answer-key pages at the back, which are not counted; the
manifest's files[].pages is the real PDF page count including them.

Everything is seeded from the item id, which is deterministic for (date, theme,
brief_id), so rerunning the same brief on the same date gives byte-identical PDFs.
A rerun builds into a scratch folder and swaps it in only when every file is written,
and never touches an item that already has an Etsy draft (publish.json).
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from datetime import date as _date
from pathlib import Path

from core.compliance import Blocklist, find_urls
from core.config import ROOT, load_settings, section, today
from core.items import new_item_id, output_root, slugify, write_manifest
from core.render import POINTS_PER_INCH, page_size_points, pdf_info, render_pdf_pages
from generators.activity_sheets.layout import PageGeometry, Style
from generators.activity_sheets.pack import KINDS, as_list, plan_pack, render_pdf

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
    if brief.get("niche", NICHE) != NICHE:
        raise ValueError(f"brief niche {brief.get('niche')!r} is not {NICHE!r}")
    theme = str(brief.get("theme") or "").strip()
    if not theme:
        raise ValueError("brief has no theme")

    shop = _shop(settings, shop_id)
    blocklist = Blocklist.load(settings.get("compliance", {}).get("trademark_blocklist"))
    texts = {k: brief.get(k) for k in ("theme", "keywords", "audience", "style_notes")}
    check = blocklist.check_obj(texts)
    if check.blocked:
        raise ValueError(f"brief contains blocked terms {check.blocked}; not generating")
    marked = (brief.get("compliance") or {}).get("blocked") or []
    if marked:
        raise ValueError(f"brief is marked with blocked terms {marked}; not generating")
    urls = find_urls(texts)
    if urls:
        raise ValueError(f"brief contains URLs {urls[:3]}; briefs must not reference listings")

    date = _check_date(date or today())
    item_id = new_item_id(date, NICHE, theme, salt=brief.get("brief_id") or "")
    d = (Path(out_root) if out_root else output_root(settings)) / item_id
    if (d / "publish.json").exists():
        log.warning("%s already has an Etsy draft; leaving its files untouched", item_id)
        return [d]

    pack = plan_pack(brief, item_id, cfg, blocklist, settings)
    printed = blocklist.check(*pack.printed_text())
    if printed.blocked:
        raise ValueError(f"generated pages contain blocked terms {printed.blocked}")

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
            rel = f"files/{slugify(pack.theme)}-activity-pack-{name}.pdf"
            render_pdf(pack, geo, build / rel, style, cfg)
            files.append({"path": rel, "kind": "pdf", "page_size": name,
                          "pages": pdf_info(build / rel)["pages"], "dpi": int(pr.get("dpi", 300))})
        (build / "previews").mkdir()
        previews = _write_previews(build, files[0], cfg)
        d.mkdir(exist_ok=True)
        for sub in ("files", "previews"):            # reruns must not leave stale pages behind
            shutil.rmtree(d / sub, ignore_errors=True)
            (build / sub).replace(d / sub)
    finally:
        shutil.rmtree(build, ignore_errors=True)

    manifest = {
        "schema": "manifest/v1",
        "item_id": item_id,
        "niche": NICHE,
        "shop_id": shop_id,
        "brief_id": brief.get("brief_id"),
        "brief_file": _repo_relative(brief_file),
        "theme": theme,
        "keywords": as_list(brief.get("keywords")),
        "audience": brief.get("audience"),
        "price_usd": brief["price_usd"] if brief.get("price_usd") is not None
        else (shop.get("default_price_usd") or {}).get(NICHE),
        "created": date,
        "ai_assisted": False,
        "needs_review": bool(brief.get("needs_review") or check.needs_review or printed.needs_review),
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
    }
    write_manifest(d, manifest)
    log.info("generated %s (%d activity pages, %s)", item_id, len(pack.pages), [f["page_size"] for f in files])
    return [d]


def page_sizes_for(brief: dict, settings: dict) -> list[str]:
    """Brief's page sizes that settings know (default: all of them); only the first unless
    print.deliver_both_sizes is true."""
    known = list(settings["print"]["page_sizes"])
    asked = [s for s in as_list(brief.get("page_sizes")) if s in known] or known
    return asked if settings["print"].get("deliver_both_sizes", True) else asked[:1]


def _write_previews(d: Path, pdf: dict, cfg: dict) -> list[dict]:
    n = min(int(cfg["preview_pages"]), pdf["pages"])
    images = render_pdf_pages(d / pdf["path"], dpi=int(cfg["preview_dpi"]), pages=list(range(n)))
    out = []
    for i, img in enumerate(images, start=1):
        rel = f"previews/page-{i:02d}.png"
        img.save(d / rel, "PNG")
        out.append({"path": rel, "page": i})
    return out


def _repo_relative(path: str | Path | None) -> str | None:
    if path is None:
        return None
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(ROOT)
        except ValueError:
            pass
    return p.as_posix()


def _shop(settings: dict, shop_id: str) -> dict:
    """The settings entry for shop_id. An unknown shop is an error, not a pack without a price."""
    for shop in settings.get("shops") or []:
        if shop.get("id") == shop_id:
            return shop
    known = [s.get("id") for s in settings.get("shops") or []]
    raise ValueError(f"unknown shop id {shop_id!r}; known: {known}")


def _check_date(value: str) -> str:
    """The item date becomes part of a folder name, so it must be a plain YYYY-MM-DD."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
        raise ValueError(f"date {value!r} is not YYYY-MM-DD")
    try:
        _date.fromisoformat(value)                   # rejects 2026-13-45
    except ValueError as exc:
        raise ValueError(f"date {value!r} is not a real YYYY-MM-DD date: {exc}") from None
    return value
