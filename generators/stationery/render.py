"""Render stationery pages to PDF and PNG, and measure what they print.

render() writes one vector PDF for one page size (reportlab, invariant=1, so the same pages
give byte-identical files) and returns the Metrics the pen recorded: the smallest font,
line weights, the smallest writing-line gap, every printed string and anything drawn
outside the margins. Printed text is checked before the file is saved: a blocked term
(including game-system "!" names, which never go on a printed page) or medical wording
refuses the render, and so does a blocked term in the PDF's title metadata.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
from collections import OrderedDict
from pathlib import Path

from reportlab.pdfgen import canvas

from core.compliance import Blocklist
from core.config import CONFIG_DIR, resolve
from core.render import POINTS_PER_INCH, page_size_points, render_pdf_pages
from generators.stationery.layout import MM, Box, Metrics, Rules, Sheet
from generators.stationery.pages import RenderCtx, draw_page, week_start_for
from generators.stationery.styles import spec_for

FOOTER_H = 16.0

# Listing-copy words that would make a medical or therapeutic claim (docs/PACKS.md section
# 4), plus a few more that only belong in health claims. Never on a printed page.
MEDICAL_RE = re.compile(
    r"\b(treat(s|ed|ing|ment|ments)?|cure[sd]?|curing|heal(s|ed|ing)?|diagnos\w*|therap\w*|clinical(ly)?|"
    r"medical(ly)?|medicat\w*|prescri\w*|symptoms?|disorders?|anxiety|depression|mental health|stress relief)\b",
    re.IGNORECASE)

_CACHE: OrderedDict[str, Metrics] = OrderedDict()
_CACHE_MAX = 24
_BLOCKLISTS: dict[str, Blocklist] = {}


def render_ctx(settings: dict, cfg: dict, page_size: str) -> RenderCtx:
    return RenderCtx(page_size=page_size, week_start=week_start_for(str(cfg.get("week_start", "auto")), page_size),
                     clock="24h" if str(cfg.get("clock", "12h")) == "24h" else "12h",
                     day_start_hour=int(cfg.get("day_start_hour", 7)),
                     grid_pitch_pt=max(float(cfg.get("grid_pitch_mm", 6.35)), float(cfg.get("min_line_gap_mm", 6.0)),
                                       6.0) * MM,
                     footer_text=str(cfg.get("footer_text") or ""),
                     accent_box_pt=float(cfg.get("accent_box_in", 0.95)) * POINTS_PER_INCH)


def _blocklist(settings: dict) -> Blocklist:
    """The settings' blocklist, loaded again whenever its file changes (a long-running
    service picks up newly added terms)."""
    path = str((settings.get("compliance") or {}).get("trademark_blocklist") or "")
    file = resolve(path) if path else CONFIG_DIR / "trademark_blocklist.txt"
    try:
        st = file.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    key = f"{path}|{stamp}"
    if key not in _BLOCKLISTS:
        _BLOCKLISTS.clear()
        _BLOCKLISTS[key] = Blocklist.load(path or None)
    return _BLOCKLISTS[key]


def text_problems(texts, settings: dict) -> list[str]:
    """Why these printed strings may not be printed (blocked terms, medical wording)."""
    texts = [t for t in texts if t]
    problems = []
    found = _blocklist(settings).check(*texts)
    if found.blocked:
        problems.append(f"printed text contains blocked terms {found.blocked}")
    medical = sorted({m.group(0).lower() for t in texts for m in MEDICAL_RE.finditer(t)})
    if medical:
        problems.append(f"printed text uses medical wording {medical}")
    return problems


def digest(pages: list[dict], page_size: str, settings: dict, cfg: dict) -> str:
    """What a render depends on: the pages' specs and assets, the page size, the print
    settings and the stationery settings."""
    body = {"pages": [{k: p.get(k) for k in ("page_id", "type", "spec", "asset_files")} for p in pages],
            "size": page_size, "print": settings.get("print"), "cfg": cfg}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _remember(key: str, metrics: Metrics) -> None:
    _CACHE[key] = metrics
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)


def render(pages: list[dict], out, *, page_size: str, settings: dict, cfg: dict, title: str = "Planner Pages",
           check: bool = True) -> Metrics:
    """Draw `pages` into `out` (a path or a binary file object) for one page size."""
    w, h = page_size_points(page_size, settings)
    margin = float(settings["print"]["margin_in"]) * POINTS_PER_INCH
    bounds = Box(margin, margin, w - 2 * margin, h - 2 * margin)
    rules = Rules.from_settings(settings, cfg)
    rc = render_ctx(settings, cfg, page_size)
    target = str(out) if isinstance(out, (str, Path)) else out
    c = canvas.Canvas(target, pagesize=(w, h), invariant=1, pageCompression=1)
    c.setTitle(title)
    c.setCreator("etsy-agents stationery")
    metrics = Metrics()
    for page in pages:
        spec = page.get("spec") or {}
        sheet = Sheet(c, bounds, spec_for(str(spec.get("style") or ""), spec.get("season")), rules, metrics,
                      tints=bool(cfg.get("color_zones", True)))
        draw_page(sheet, page, rc, FOOTER_H)
        c.showPage()
    if check:
        problems = text_problems(metrics.texts, settings)
        title_check = _blocklist(settings).check(title)
        if title_check.blocked:
            problems.append(f"PDF title contains blocked terms {title_check.blocked}")
        if problems:
            raise ValueError("; ".join(problems))
    c.save()
    _remember(digest(pages, page_size, settings, cfg), metrics)
    return metrics


def measure(pages: list[dict], page_size: str, settings: dict, cfg: dict) -> Metrics:
    """The Metrics of rendering `pages` (from the last render of the same pages, else an
    in-memory render)."""
    key = digest(pages, page_size, settings, cfg)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    metrics = render(pages, io.BytesIO(), page_size=page_size, settings=settings, cfg=cfg, check=False)
    return metrics


def previews(pages: list[dict], out_dir: Path, *, page_size: str, settings: dict, cfg: dict, limit: int,
             dpi: int, title: str) -> list[Path]:
    """RGB PNG renders of the first `limit` pages (page-01.png, ...)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chosen = list(pages)[:max(0, int(limit))]
    if not chosen:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "preview.pdf"
        render(chosen, pdf, page_size=page_size, settings=settings, cfg=cfg, title=title)
        out = []
        for i, img in enumerate(render_pdf_pages(pdf, dpi=dpi, pages=list(range(len(chosen)))), 1):
            p = out_dir / f"page-{i:02d}.png"
            img.convert("RGB").save(p, "PNG")
            out.append(p)
    return out
