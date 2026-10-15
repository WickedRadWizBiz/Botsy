"""Pixel measurements on rendered pages: ink coverage, line weight, black/white, regions.

Coloring pages are rendered without anti-aliasing for these checks, so vector line art
comes out as pure black and white and any gray left means real shading (or an embedded
grayscale image), not edge smoothing.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from core.render import POINTS_PER_INCH
from qa.checks import WARNING, Item, page_list, result


def gray_array(img: Image.Image) -> np.ndarray:
    """uint8 luminance, with transparent pixels treated as white paper."""
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img.convert("RGBA"))
    return np.asarray(img.convert("L"))


def ink_coverage(img: Image.Image, light: int = 230, dark: int = 128) -> tuple[float, float]:
    """(share of pixels with any ink, share of pixels that are dark). A blank page has almost
    no ink; a page that came out solid black has almost all pixels dark."""
    g = gray_array(img)
    return float((g < light).mean()), float((g < dark).mean())


def iter_pdf_pages(path: str | Path, dpi: float, antialias: bool = True,
                   max_pages: int | None = None) -> Iterator[tuple[int, Image.Image]]:
    """Yield (1-based page number, RGB image) one page at a time.

    core.render.render_pdf_pages renders every page up front and always anti-aliases;
    line checks at 200 DPI need neither."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        n = len(doc) if max_pages is None else min(len(doc), max_pages)
        flags = {} if antialias else {"no_smoothpath": True, "no_smoothtext": True, "no_smoothimage": True}
        for i in range(n):
            yield i + 1, doc[i].render(scale=dpi / POINTS_PER_INCH, **flags).to_pil().convert("RGB")
    finally:
        doc.close()


def line_weight_px(gray: np.ndarray, dpi: float, min_component_in: float = 0.3,
                   percentile: float = 5.0) -> float | None:
    """Estimated thinnest line width in pixels, or None if there is no line art.

    Distance transform on the ink mask: on a stroke's centre line the distance to paper is
    half the stroke width, so 2 * distance at the ridge pixels is the local width. Small
    connected pieces (text glyphs, specks) are ignored so a footer line of small print
    does not count as thin line art. A low percentile instead of the minimum ignores the
    tapering ends of strokes."""
    ink = (gray < 128).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    big = np.zeros(n, dtype=bool)
    big[1:] = np.maximum(stats[1:, cv2.CC_STAT_WIDTH], stats[1:, cv2.CC_STAT_HEIGHT]) >= min_component_in * dpi
    if not big.any():
        return None
    dist = cv2.distanceTransform(ink, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    ridge = (dist > 0) & (dist >= cv2.dilate(dist, np.ones((3, 3), np.uint8))) & big[labels]
    if not ridge.any():
        return None
    return float(np.percentile(2.0 * dist[ridge], percentile))


def tone_fractions(rgb: np.ndarray) -> tuple[float, float]:
    """(share of mid-gray pixels, share of colored pixels) of an RGB array."""
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    total = float(g.size)
    gray = cv2.countNonZero(cv2.inRange(g, 41, 214)) / total
    r, gr, b = cv2.split(np.ascontiguousarray(rgb))
    spread = cv2.subtract(cv2.max(cv2.max(r, gr), b), cv2.min(cv2.min(r, gr), b))  # cv2: fast per-pixel ops
    return gray, cv2.countNonZero(cv2.inRange(spread, 41, 255)) / total


def enclosed_regions(gray: np.ndarray, min_area_px: int) -> int:
    """Paper regions fully enclosed by lines (not touching the page edge) and big enough to
    color in. Line art with gaps leaks into the background and has few of them."""
    paper = (gray >= 128).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(paper, connectivity=4)
    edge = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])).tolist())
    return sum(1 for i in range(1, n) if i not in edge and stats[i, cv2.CC_STAT_AREA] >= min_area_px)


def check_coloring(item: Item) -> list[dict]:
    """Coloring pages: thinnest line vs print.min_line_weight_pt, pure black/white, and
    enough enclosed regions. Measured on the PDF with the narrowest pages (art is fitted
    to the page width, so A4 prints it a little smaller than Letter): the thinnest lines a
    buyer will get."""
    pdfs = item.entries("files", "pdf")
    if not pdfs:
        return [result("line_weight", False, "no PDF file to measure", WARNING)]
    sizes = item.settings["print"]["page_sizes"]

    def short_side(e: dict) -> float:
        s = sizes.get(e.get("page_size"), {})
        return min(float(s.get("width_in", 0)), float(s.get("height_in", 0)))

    entry = min(pdfs, key=short_side)
    cfg = item.cfg
    dpi = float(cfg["line_check_dpi"])
    min_pt = float(item.settings["print"].get("min_line_weight_pt", 1.5))
    px_pt = POINTS_PER_INCH / dpi
    min_region_px = int(float(cfg["min_region_sq_in"]) * dpi * dpi)

    widths, thin, toned, sparse, pages = {}, [], [], [], 0
    for page, img in iter_pdf_pages(item.path(entry), dpi, antialias=False, max_pages=int(cfg["line_check_max_pages"])):
        pages += 1
        rgb = np.asarray(img)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        w = line_weight_px(gray, dpi, float(cfg["min_component_in"]), float(cfg["line_percentile"]))
        if w is not None:
            widths[page] = w * px_pt
            if (w + 1) * px_pt < min_pt:   # one pixel of slack for rasterizing at `dpi`
                thin.append(page)
        gray_share, color_share = tone_fractions(rgb)
        if gray_share > cfg["gray_max_fraction"] or color_share > cfg["color_max_fraction"]:
            toned.append(f"{page} ({gray_share:.1%} gray, {color_share:.1%} color)")
        if enclosed_regions(gray, min_region_px) < int(cfg["min_regions"]):
            sparse.append(page)

    rel = entry["path"]
    if widths:
        worst = min(widths, key=widths.get)
        lw = (f"{rel}: thinnest line about {widths[worst]:.2f}pt on page {worst} "
              f"(minimum {min_pt:g}pt, measured at {dpi:.0f} DPI, +/-{px_pt:.2f}pt)")
        if thin:
            lw += f"; thin pages: {page_list(thin)}"
    else:
        lw = f"{rel}: no line art found on {pages} pages"
    max_sparse = float(cfg["regions_max_bad_share"]) * max(pages, 1)
    return [
        result("line_weight", bool(widths) and not thin, lw, WARNING),
        result("pure_black_white", not toned,
               f"{rel}: gray or color on pages {', '.join(toned[:8])}" if toned else f"{rel}: black and white only",
               WARNING),
        result("coloring_regions", len(sparse) <= max_sparse,
               f"{rel}: fewer than {cfg['min_regions']} enclosed areas to color on pages {page_list(sparse)}"
               if sparse else f"{rel}: every page has closed areas to color", WARNING),
    ]

