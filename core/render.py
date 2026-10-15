"""PDF inspection and rasterizing (pypdfium2), shared by generators, QA and mockups."""
from __future__ import annotations

from pathlib import Path

from core.config import load_settings

POINTS_PER_INCH = 72.0


def page_size_points(name: str, settings: dict | None = None) -> tuple[float, float]:
    """(width, height) in PDF points for a page size key in settings print.page_sizes."""
    settings = settings or load_settings()
    size = settings["print"]["page_sizes"][name]
    return size["width_in"] * POINTS_PER_INCH, size["height_in"] * POINTS_PER_INCH


def pdf_info(path: str | Path) -> dict:
    """{"pages": n, "sizes_pt": [(w, h), ...]} for a PDF."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        sizes = [tuple(doc[i].get_size()) for i in range(len(doc))]
    finally:
        doc.close()
    return {"pages": len(sizes), "sizes_pt": sizes}


def render_pdf_pages(path: str | Path, dpi: int = 100, pages: list[int] | None = None):
    """Rasterize PDF pages (0-based indexes, default all) to RGB PIL images."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        idx = range(len(doc)) if pages is None else pages
        return [doc[i].render(scale=dpi / POINTS_PER_INCH).to_pil().convert("RGB") for i in idx]
    finally:
        doc.close()
