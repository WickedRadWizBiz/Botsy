"""Render tabletop RPG pages to PDF and PNG, and measure what they print.

render() writes one vector PDF for one page size (reportlab, invariant=1, so the same pages
give byte-identical files) and returns the Metrics the pen recorded: the smallest font, the
smallest writing-line gap, line weights, every printed string, and anything drawn outside
the margins or too wide for its slot. Printed text is checked before the file is saved:
a blocked term, any game-system name or owner (the blocklist's `!` terms, with no system
allowed, plus every name in config/ttrpg_systems.yaml) or medical wording refuses the
render. A game system only changes which fields a page has, never what it says about itself.
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
from generators.ttrpg import decor, flow
from generators.ttrpg.layout import SOFT, Box, Metrics, Pen, Rules
from generators.ttrpg.styles import genre

HEADER_H = 46.0
FOOTER_H = 10.0

# Wording that would read as a medical or therapeutic claim (docs/PACKS.md section 4).
# Never on a printed page. (Game words such as a "Medicine" skill are not claims.)
MEDICAL_RE = re.compile(
    r"\b(treat(s|ed|ing|ment|ments)?|cure[sd]?|curing|heal(s|ed|ing)?|diagnos\w*|therap\w*|clinical(ly)?|"
    r"medical(ly)?|prescri\w*|symptoms?)\b", re.IGNORECASE)

_CACHE: OrderedDict[str, Metrics] = OrderedDict()
_CACHE_MAX = 32
_BLOCKLISTS: dict[str, tuple[tuple, Blocklist]] = {}


def _blocklist(settings: dict) -> Blocklist:
    """The trademark blocklist plus the health file (settings compliance.trademark_blocklist
    and compliance.health_terms), reloaded when either file changes (a long-running service
    picks up added terms without a restart)."""
    comp = settings.get("compliance") or {}
    path = str(comp.get("trademark_blocklist") or "")
    health_path = str(comp.get("health_terms") or "")
    stamp = []
    for given, default in ((path, "trademark_blocklist.txt"), (health_path, "health_terms.txt")):
        file = resolve(given) if given else CONFIG_DIR / default
        try:
            st = Path(file).stat()
            stamp.append((st.st_mtime_ns, st.st_size))
        except OSError:
            stamp.append(None)             # Blocklist.load raises the real error (a missing default health file is skipped)
    stamp = tuple(stamp)
    key = f"{path}|{health_path}"
    cached = _BLOCKLISTS.get(key)
    if cached is None or cached[0] != stamp:
        _BLOCKLISTS[key] = (stamp, Blocklist.load(path or None, health_path=health_path or None))
    return _BLOCKLISTS[key][1]


def text_problems(texts, settings: dict) -> list[str]:
    """Why these printed strings may not be printed: blocked terms (game-system names count
    as blocked: no system is allowed on a page), names of any registered game system or its
    owner, medical wording. [] when clean."""
    from core import systems

    texts = [t for t in texts if isinstance(t, str) and t.strip()]
    problems = []
    found = _blocklist(settings).check(*texts)
    if found.blocked:
        problems.append(f"printed text contains blocked terms {found.blocked}")
    # One pass over all strings: names never match across a line break.
    named = systems.detect_systems("\n".join(texts), settings) if texts else []
    if named:
        problems.append(f"printed text names game systems {named}")
    medical = sorted({m.group(0).lower() for t in texts for m in MEDICAL_RE.finditer(t)})
    if medical:
        problems.append(f"printed text uses medical wording {medical}")
    return problems


def review_terms(texts, settings: dict) -> list[str]:
    """Review (`?`) terms in printed text."""
    return list(_blocklist(settings).check(*[t for t in texts if t]).flagged)


def digest(pages: list[dict], page_size: str, settings: dict, cfg: dict) -> str:
    """What a render depends on: the pages' specs and assets, the page size, the print
    settings and the ttrpg settings."""
    body = {"pages": [{k: p.get(k) for k in ("page_id", "type", "spec", "asset_files")} for p in pages],
            "size": page_size, "print": settings.get("print"), "cfg": cfg}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _remember(key: str, metrics: Metrics) -> None:
    _CACHE[key] = metrics
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)


def draw_page(pen: Pen, page: dict, cfg: dict) -> None:
    spec = page.get("spec") or {}
    g = genre(spec.get("genre"))
    content = decor.frame(pen, pen.bounds, g)
    head, rest = content.take_top(HEADER_H, 8)
    accent = (page.get("asset_files") or {}).get("accent")
    decor.header(pen, head, g, str(spec.get("title") or ""), str(spec.get("subtitle") or ""),
                 str(spec.get("motif") or g.motifs[0]), accent if accent and Path(accent).is_file() else None)
    foot, body = rest.take_bottom(FOOTER_H, 6)
    footer = str(cfg.get("footer_text") or "")
    if footer:
        pen.text(foot.cx, foot.y + 1.5, footer, size=7.0, font=g.label_font, align="center", color=SOFT, max_w=foot.w)
    recipe = spec.get("layout") or {}
    ctx = flow.Ctx(pen, g, tints=bool(cfg.get("tints", True)))
    flow.layout(ctx, body, list(spec.get("sections") or []), cols=int(recipe.get("cols") or 2),
                strip=bool(recipe.get("strip")))


def render(pages: list[dict], out, *, page_size: str, settings: dict, cfg: dict, title: str = "Tabletop RPG Pages",
           check: bool = True) -> Metrics:
    """Draw `pages` into `out` (a path or a binary file object) for one page size. Raises
    ValueError (nothing saved) when printed text breaks a compliance rule or a page does not
    fit at the print rules (flow.LayoutError)."""
    w, h = page_size_points(page_size, settings)
    margin = float(settings["print"]["margin_in"]) * POINTS_PER_INCH
    bounds = Box(margin, margin, w - 2 * margin, h - 2 * margin)
    rules = Rules.from_cfg(cfg)
    metrics = Metrics()
    target = str(out) if isinstance(out, (str, Path)) else out
    c = canvas.Canvas(target, pagesize=(w, h), invariant=1, pageCompression=1)
    title = title if not text_problems([title], settings) else "Tabletop RPG Pages"
    c.setTitle(title)
    c.setCreator("etsy-agents ttrpg")
    for i, page in enumerate(pages, 1):
        pen = Pen(c, bounds, rules, metrics, page_label=f"page {i} ({page.get('type')})")
        draw_page(pen, page, cfg)
        c.showPage()
        metrics.pages += 1
    metrics.texts.append(title)
    if check:
        problems = text_problems(metrics.texts, settings)
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
    return render(pages, io.BytesIO(), page_size=page_size, settings=settings, cfg=cfg, check=False)


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
