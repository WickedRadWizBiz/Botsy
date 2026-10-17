"""Rendering: every page type on both page sizes, print rules, margins, adaptation and
determinism (docs/PACKS.md sections 1 and 5)."""
import copy

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
import pytest

import generators.stationery as stationery
from core.render import pdf_info, render_pdf_pages
from generators.stationery.layout import MM
from generators.stationery.render import measure, render

from .conftest import ALL_STYLES, every_layout, make_ctx

SIZES = ("us_letter", "a4")
PT_PER_MM = 72 / 25.4


def pdf_font_sizes(path) -> list[float]:
    """The font size of every printed character, read back from the PDF."""
    doc = pdfium.PdfDocument(str(path))
    sizes = []
    try:
        for i in range(len(doc)):
            tp = doc[i].get_textpage()
            for k in range(tp.count_chars()):
                if not chr(pdfium_raw.FPDFText_GetUnicode(tp.raw, k)).isspace():
                    sizes.append(pdfium_raw.FPDFText_GetFontSize(tp.raw, k))
            tp.close()
    finally:
        doc.close()
    return sizes


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("style, season", ALL_STYLES)
def test_every_layout_renders_within_the_print_rules(tmp_path, settings, style, season, size):
    pages = every_layout(style, season)
    out = tmp_path / f"{style}-{size}.pdf"
    n = stationery.render_pages(pages, out, page_size=size, settings=settings, ctx=make_ctx())
    assert n == len(pages) == pdf_info(out)["pages"]
    w, h = (612.0, 792.0) if size == "us_letter" else (595.3, 841.9)
    for pw, ph in pdf_info(out)["sizes_pt"]:
        assert abs(pw - w) < 0.5 and abs(ph - h) < 0.5
    m = measure(pages, size, settings, stationery.config(settings))
    assert m.out_of_bounds == []                                  # nothing inside the margins' no-print zone
    assert m.min_font_pt >= 7.0
    assert m.min_line_gap_mm >= 6.0 - 1e-6                        # writing lines, rows and grids
    assert m.min_light_pt >= 0.5
    assert m.min_structural_pt >= settings["print"]["min_line_weight_pt"]
    assert min(pdf_font_sizes(out)) >= 7.0 - 1e-3                 # read back from the file itself


def test_every_page_type_in_a_planned_pack_renders_on_both_sizes(tmp_path, settings):
    pages = stationery.plan_pages(make_ctx("adhd"), 30, seed="s", settings=settings)
    assert {p["type"] for p in pages} == set(stationery.PAGE_TYPES)
    for size in SIZES:
        out = tmp_path / f"pack-{size}.pdf"
        assert stationery.render_pages(pages, out, page_size=size, settings=settings, ctx=make_ctx("adhd")) == 30
        assert pdf_info(out)["pages"] == 30


def _line_rows(img, dark=200, min_cover=0.5):
    """y positions (px) of horizontal lines that cross most of the image width."""
    a = np.asarray(img.convert("L"))
    cover = (a < dark).mean(axis=1)
    rows = np.where(cover >= min_cover)[0]
    groups, start = [], None
    for i, y in enumerate(rows):
        if start is None:
            start = y
        if i + 1 == len(rows) or rows[i + 1] != y + 1:
            groups.append((start + y) / 2)
            start = None
    return groups


@pytest.mark.parametrize("style", ["minimalist", "adhd_friendly"])
def test_writing_lines_measured_on_the_page_are_6mm_apart(tmp_path, settings, style):
    page = [p for p in every_layout(style) if p["page_id"] == "notes_lined-lined"]
    out = tmp_path / "lined.pdf"
    stationery.render_pages(page, out, page_size="us_letter", settings=settings, ctx=make_ctx())
    dpi = 150
    [img] = render_pdf_pages(out, dpi=dpi)
    rows = _line_rows(img)
    assert len(rows) > 20
    gaps = np.diff(rows) / dpi * 25.4
    writing = gaps[gaps > 2.0]                        # header rule pairs sit closer; lines do not
    assert writing.min() >= 6.0 - 0.3                 # rasterising costs up to a pixel


def test_layouts_adapt_to_the_page_size_not_just_scale(settings):
    cfg = stationery.config(settings)
    lined = [p for p in every_layout("minimalist") if p["page_id"] == "notes_lined-lined"]
    letter = measure(lined, "us_letter", settings, cfg)
    a4 = measure(lined, "a4", settings, cfg)
    # A4 is taller: more writing lines at the same spacing (not the same lines stretched)
    assert len(a4.light_lines) > len(letter.light_lines)
    assert letter.min_line_gap_mm == pytest.approx(a4.min_line_gap_mm)
    # A4 is narrower: the weekly block grid drops a column (columns stay at least 28 mm wide)
    periods = [p for p in every_layout("adhd_friendly") if p["page_id"] == "weekly_planner-periods"]
    assert "Block 6" in measure(periods, "us_letter", settings, cfg).texts
    assert "Block 6" not in measure(periods, "a4", settings, cfg).texts
    # undated week starts: Sunday on US Letter, Monday on A4
    weekly = [p for p in every_layout("minimalist") if p["page_id"] == "weekly_planner-columns"]
    t_letter = measure(weekly, "us_letter", settings, cfg).texts
    t_a4 = measure(weekly, "a4", settings, cfg).texts
    assert t_letter.index("Sunday") < t_letter.index("Monday")
    assert t_a4.index("Monday") < t_a4.index("Sunday")


def test_margins_follow_settings(tmp_path, settings):
    wide = copy.deepcopy(settings)
    wide["print"]["margin_in"] = 0.85
    pages = every_layout("adhd_friendly")
    m = measure(pages, "us_letter", wide, stationery.config(wide))
    assert m.out_of_bounds == []
    assert m.min_line_gap_mm >= 6.0 - 1e-6


def test_settings_cannot_lower_the_floors(settings):
    loose = copy.deepcopy(settings)
    loose["stationery"] = {"min_font_pt": 4, "min_line_gap_mm": 3, "light_line_pt": 0.1, "grid_pitch_mm": 3}
    loose["print"]["min_line_weight_pt"] = 1.0
    m = measure(every_layout("minimalist"), "a4", loose, stationery.config(loose))
    assert m.min_font_pt >= 7.0 and m.min_line_gap_mm >= 6.0 - 1e-6 and m.min_light_pt >= 0.5
    assert m.min_structural_pt >= 1.0


def test_render_is_byte_identical(tmp_path, settings):
    pages = stationery.plan_pages(make_ctx("cottagecore"), 20, seed="s", settings=settings)
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    stationery.render_pages(pages, a, page_size="a4", settings=settings, ctx=make_ctx("cottagecore"))
    stationery.render_pages(copy.deepcopy(pages), b, page_size="a4", settings=settings, ctx=make_ctx("cottagecore"))
    assert a.read_bytes() == b.read_bytes()


def test_color_zones_can_be_turned_off(tmp_path, settings):
    gray = copy.deepcopy(settings)
    gray["stationery"] = {"color_zones": False}
    pages = [p for p in every_layout("adhd_friendly") if p["type"] == "daily_planner"]
    out = tmp_path / "gray.pdf"
    stationery.render_pages(pages, out, page_size="us_letter", settings=gray, ctx=make_ctx())
    img = np.asarray(render_pdf_pages(out, dpi=50)[0].convert("RGB")).astype(int)
    spread = (img.max(axis=2) - img.min(axis=2))
    colourful = (spread > 12).mean()
    assert colourful < 0.01                            # only the small motif keeps its accent colour


def test_preview_pngs(tmp_path, settings):
    pages = stationery.plan_pages(make_ctx("botanical"), 20, seed="s", settings=settings)
    paths = stationery.preview(pages, tmp_path / "previews", settings=settings, ctx=make_ctx("botanical"), limit=6)
    assert [p.name for p in paths] == [f"page-{i:02d}.png" for i in range(1, 7)]
    from PIL import Image
    with Image.open(paths[0]) as im:
        assert im.mode == "RGB" and im.width >= 8.5 * 100
    assert stationery.preview(pages, tmp_path / "none", settings=settings, ctx=None, limit=0) == []


def test_render_refuses_an_unknown_layout(tmp_path, settings):
    page = every_layout("minimalist")[:1]
    page[0]["spec"]["variant"] = "nope"
    with pytest.raises(ValueError, match="no stationery layout"):
        render(page, tmp_path / "x.pdf", page_size="a4", settings=settings, cfg=stationery.config(settings))


def test_title_metadata(tmp_path, settings):
    out = tmp_path / "t.pdf"
    stationery.render_pages(every_layout("minimalist")[:1], out, page_size="a4", settings=settings,
                            ctx=make_ctx("adhd"))
    doc = pdfium.PdfDocument(str(out))
    try:
        assert doc.get_metadata_dict().get("Title") == "ADHD Planner Pages"
    finally:
        doc.close()


def test_grid_pitch_is_at_least_6mm(settings):
    cfg = stationery.config(settings)
    for pid in ("notes_dot-dots", "notes_grid-grid"):
        page = [p for p in every_layout("minimalist") if p["page_id"] == pid]
        m = measure(page, "us_letter", settings, cfg)
        assert m.min_line_gap_mm == pytest.approx(cfg["grid_pitch_mm"], abs=0.01)
        assert m.min_line_gap_mm >= 6.0
    assert 6.0 * MM == pytest.approx(6.0 * PT_PER_MM)
