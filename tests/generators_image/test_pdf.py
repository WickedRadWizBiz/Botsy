"""PDF page sizes, page counts and placement for coloring pages and sticker sheets."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.render import page_size_points, pdf_info, render_pdf_pages
from generators.imaging import cleanup
from generators.imaging.pdf import Box, coloring_pdf, fit, load_svg, sheet_grid, sticker_sheet_pdf


def test_fit_keeps_aspect_and_centers():
    placed = fit(100, 200, Box(0, 0, 400, 400))
    assert (placed.w, placed.h) == (200, 400) and placed.x == 100 and placed.y == 0


@pytest.mark.parametrize("size", ["us_letter", "a4"])
def test_coloring_pdf_pages_and_size(tmp_path, base_settings, size):
    art = Image.new("L", (300, 400), 255)
    ImageDraw.Draw(art).ellipse([50, 50, 250, 350], outline=0, width=10)
    svg = tmp_path / "a.svg"
    cleanup.vectorize_line_art(art, svg, "potrace" if cleanup.potrace_path() else "vtracer_binary")
    pages = [load_svg(svg), art, load_svg(svg)]          # vector and raster pages mix freely
    out = coloring_pdf(pages, tmp_path / f"c-{size}.pdf", size, base_settings, title="Test")
    info = pdf_info(out)
    w, h = page_size_points(size, base_settings)
    assert info["pages"] == 3
    assert all(abs(pw - w) < 0.01 and abs(ph - h) < 0.01 for pw, ph in info["sizes_pt"])
    # art stays inside the margins: the outer 0.5in band is blank
    img = render_pdf_pages(out, dpi=50, pages=[0])[0].convert("L")
    m = int(0.5 * 50) - 2
    arr = np.asarray(img)
    assert arr[:m].min() > 250 and arr[-m:].min() > 250
    assert img.getextrema()[0] < 50                      # but the art is there


def test_coloring_pdf_is_reproducible(tmp_path, base_settings):
    art = Image.new("L", (100, 100), 255)
    ImageDraw.Draw(art).line([(10, 10), (90, 90)], fill=0, width=5)
    a = coloring_pdf([art], tmp_path / "a.pdf", "us_letter", base_settings)
    b = coloring_pdf([art], tmp_path / "b.pdf", "us_letter", base_settings)
    assert a.read_bytes() == b.read_bytes()


def test_sheet_grid_pages():
    assert sheet_grid(13, 3, 4) == [list(range(12)), [12]]
    assert sheet_grid(0, 3, 4) == []


def test_sticker_sheet_pages_and_transparency(tmp_path, base_settings):
    sticker = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(sticker).ellipse([20, 20, 180, 180], fill=(30, 120, 220, 255))
    stickers = [sticker] * 14
    out = sticker_sheet_pdf(stickers, tmp_path / "s.pdf", "a4", base_settings, cols=3, rows=4, gap_in=0.25)
    info = pdf_info(out)
    w, h = page_size_points("a4", base_settings)
    assert info["pages"] == 2
    assert all(abs(pw - w) < 0.01 and abs(ph - h) < 0.01 for pw, ph in info["sizes_pt"])
    page = render_pdf_pages(out, dpi=40, pages=[0])[0]
    # first cell center is blue; the transparent corner of the first cell renders white (no black box)
    margin, cell_w = 0.5 * 40, (8.27 - 1.0 - 0.5) / 3 * 40
    cx = int(margin + cell_w / 2)
    cell_h = (11.69 - 1.0 - 0.75) / 4 * 40
    cy = int(margin + cell_h / 2)
    r, g, b = page.getpixel((cx, cy))
    assert b > 150 and r < 100
    assert page.getpixel((int(margin + 2), int(margin + 2))) == (255, 255, 255)


def test_sticker_grid_that_does_not_fit_raises(tmp_path, base_settings):
    with pytest.raises(ValueError, match="does not fit"):
        sticker_sheet_pdf([Image.new("RGBA", (10, 10))], tmp_path / "x.pdf", "us_letter", base_settings,
                          cols=40, rows=1, gap_in=0.5)


def _noisy_stickers(n: int, px: int = 400) -> list[Image.Image]:
    """Textured stickers, like real model art (flat test shapes would hide the size problem)."""
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:px, 0:px]
    out = []
    for i in range(n):
        shade = 150 + 60 * np.sin(xx / (20 + i)) * np.cos(yy / 30)          # smooth painted shading
        rgb = np.stack([shade, shade * 0.7, 255 - shade], axis=2) + rng.normal(0, 4, (px, px, 3))  # grain
        a = np.zeros((px, px, 4), np.uint8)
        a[..., :3] = np.clip(rgb, 0, 255)
        a[40:-40, 40:-40, 3] = 255
        out.append(Image.fromarray(a, "RGBA"))
    return out


def test_sticker_sheet_embeds_jpeg_and_stays_small(tmp_path, base_settings):
    stickers = _noisy_stickers(6)
    jpeg = sticker_sheet_pdf(stickers, tmp_path / "j.pdf", "us_letter", base_settings, cols=3, rows=2)
    flat = sticker_sheet_pdf(stickers, tmp_path / "f.pdf", "us_letter", base_settings, cols=3, rows=2,
                             jpeg_quality=None)
    assert b"DCTDecode" in jpeg.read_bytes() and b"DCTDecode" not in flat.read_bytes()
    assert b"SMask" in flat.read_bytes()                       # lossless path keeps the alpha
    assert jpeg.stat().st_size < flat.stat().st_size / 2
    again = sticker_sheet_pdf(stickers, tmp_path / "j2.pdf", "us_letter", base_settings, cols=3, rows=2)
    assert again.read_bytes() == jpeg.read_bytes()             # still reproducible
