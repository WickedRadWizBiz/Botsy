"""PDF assembly for image-based products (reportlab).

Coloring pages: one art per page, vector (SVG via svglib) when available, else the cleaned
raster, scaled to fit inside the print margins and centered. Sticker sheets: a grid of
transparent PNGs with cut spacing between cells. Page sizes come from settings
print.page_sizes via core.render.page_size_points, so US Letter and A4 share one code path.

Canvases are built with invariant=1 so the same inputs give byte-identical PDFs.

Sticker art from an image model is textured, so a sheet embedded losslessly runs about
1 MB per sticker and a 24-sticker pack passes Etsy's 20 MB per-file limit. Sheets are
printed on white paper, so each sticker is flattened onto white and embedded as a
high-quality JPEG (4:4:4, no chroma blur on the outlines) instead, about 10x smaller.
The transparent originals ship losslessly in the ZIP.
"""
from __future__ import annotations

import io
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from reportlab import rl_config
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from core.render import POINTS_PER_INCH, page_size_points

Art = Drawing | Image.Image


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float


def printable_box(page_size: str, settings: dict, bottom_reserve_pt: float = 0.0) -> tuple[float, float, Box]:
    """(page_w, page_h, box inside the margins) in points."""
    w, h = page_size_points(page_size, settings)
    m = float(settings["print"]["margin_in"]) * POINTS_PER_INCH
    return w, h, Box(m, m + bottom_reserve_pt, w - 2 * m, h - 2 * m - bottom_reserve_pt)


def fit(src_w: float, src_h: float, box: Box) -> Box:
    """Largest src-aspect rectangle centered in box."""
    s = min(box.w / src_w, box.h / src_h)
    w, h = src_w * s, src_h * s
    return Box(box.x + (box.w - w) / 2, box.y + (box.h - h) / 2, w, h)


def load_svg(path: str | Path) -> Drawing | None:
    """svglib Drawing, or None if svglib cannot read the file."""
    from svglib.svglib import svg2rlg

    try:
        drawing = svg2rlg(str(path))
    except Exception:          # malformed SVG: the caller falls back to the raster
        return None
    if drawing is None or not drawing.width or not drawing.height:
        return None
    return drawing


def draw_art(c: canvas.Canvas, art: Art, box: Box) -> Box:
    """Draw a Drawing or PIL image scaled to fit box; returns where it landed."""
    if isinstance(art, Drawing):
        placed = fit(art.width, art.height, box)
        c.saveState()
        c.translate(placed.x, placed.y)
        c.scale(placed.w / art.width, placed.h / art.height)
        renderPDF.draw(art, c, 0, 0)
        c.restoreState()
        return placed
    placed = fit(art.width, art.height, box)
    img = art if art.mode in ("RGB", "RGBA", "L") else art.convert("RGB")
    c.drawImage(ImageReader(img), placed.x, placed.y, placed.w, placed.h,
                mask="auto" if img.mode == "RGBA" else None)
    return placed


def _footer(c: canvas.Canvas, text: str, page_w: float, margin: float) -> None:
    if not text:
        return
    c.saveState()
    c.setFont("Helvetica", 8)
    c.setFillGray(0.45)
    c.drawCentredString(page_w / 2, margin / 2, text)
    c.restoreState()


def coloring_pdf(art: list[Art], out_path: str | Path, page_size: str, settings: dict, *,
                 title: str = "", footer_text: str = "") -> Path:
    """One page per art, fit inside the print margins."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h, box = printable_box(page_size, settings)
    margin = float(settings["print"]["margin_in"]) * POINTS_PER_INCH
    c = canvas.Canvas(str(out_path), pagesize=(page_w, page_h), invariant=1)
    c.setTitle(title)
    c.setCreator("etsy-agents")
    for a in art:
        draw_art(c, a, box)
        _footer(c, footer_text, page_w, margin)
        c.showPage()
    c.save()
    return out_path


def sheet_grid(n: int, cols: int, rows: int) -> list[list[int]]:
    """Sticker indexes per sheet page: n stickers in pages of cols*rows."""
    per = max(1, cols * rows)
    return [list(range(i, min(n, i + per))) for i in range(0, n, per)]


def flat_jpeg(img: Image.Image, quality: int = 92) -> ImageReader:
    """RGBA art flattened onto white, as a JPEG reportlab embeds as-is (DCTDecode)."""
    rgba = img.convert("RGBA")
    flat = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba).convert("RGB")
    buf = io.BytesIO()
    flat.save(buf, "JPEG", quality=int(quality), subsampling=0)
    return ImageReader(io.BytesIO(buf.getvalue()))


@contextmanager
def binary_jpeg_streams() -> Iterator[None]:
    """Embed JPEG data as is (DCTDecode only) instead of ASCII85 text: a fifth smaller, and
    without reportlab's C accelerator the pure-Python ASCII85 encoder is the slowest step."""
    old = rl_config.useA85
    rl_config.useA85 = 0
    try:
        yield
    finally:
        rl_config.useA85 = old


def sheet_cell_px(page_size: str, settings: dict, *, cols: int = 3, rows: int = 4, gap_in: float = 0.25,
                  footer_text: str = "") -> int:
    """Pixels a sticker needs on its long side to print at print.dpi in a sheet cell."""
    footer_pt = 12.0 if footer_text else 0.0
    _, _, box = printable_box(page_size, settings, bottom_reserve_pt=footer_pt)
    gap = gap_in * POINTS_PER_INCH
    cell_w = (box.w - (cols - 1) * gap) / cols
    cell_h = (box.h - (rows - 1) * gap) / rows
    return math.ceil(max(cell_w, cell_h) / POINTS_PER_INCH * int(settings["print"].get("dpi", 300)))


def sticker_sheet_pdf(stickers: list[Image.Image], out_path: str | Path, page_size: str, settings: dict, *,
                      cols: int = 3, rows: int = 4, gap_in: float = 0.25, title: str = "",
                      footer_text: str = "", jpeg_quality: int | None = 92) -> Path:
    """Grid of stickers, gap_in apart, each downsampled to print dpi for its cell.
    jpeg_quality=None embeds the transparent PNG data losslessly (larger files)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    footer_pt = 12.0 if footer_text else 0.0
    page_w, page_h, box = printable_box(page_size, settings, bottom_reserve_pt=footer_pt)
    margin = float(settings["print"]["margin_in"]) * POINTS_PER_INCH
    dpi = int(settings["print"].get("dpi", 300))
    gap = gap_in * POINTS_PER_INCH
    cell_w = (box.w - (cols - 1) * gap) / cols
    cell_h = (box.h - (rows - 1) * gap) / rows
    if cell_w <= 0 or cell_h <= 0:
        raise ValueError(f"sticker grid {cols}x{rows} with {gap_in}in gaps does not fit on {page_size}")
    max_px = math.ceil(max(cell_w, cell_h) / POINTS_PER_INCH * dpi)
    c = canvas.Canvas(str(out_path), pagesize=(page_w, page_h), invariant=1)
    c.setTitle(title)
    c.setCreator("etsy-agents")
    with binary_jpeg_streams():
        for page in sheet_grid(len(stickers), cols, rows):
            for slot, idx in enumerate(page):
                r, col = divmod(slot, cols)
                cell = Box(box.x + col * (cell_w + gap), box.y + box.h - (r + 1) * cell_h - r * gap, cell_w, cell_h)
                img = stickers[idx]
                if max(img.size) > max_px:
                    img = img.copy()
                    img.thumbnail((max_px, max_px), Image.LANCZOS)
                if jpeg_quality:
                    placed = fit(img.width, img.height, cell)
                    c.drawImage(flat_jpeg(img, jpeg_quality), placed.x, placed.y, placed.w, placed.h)
                else:
                    draw_art(c, img.convert("RGBA"), cell)
            _footer(c, footer_text, page_w, margin)
            c.showPage()
        c.save()
    return out_path
