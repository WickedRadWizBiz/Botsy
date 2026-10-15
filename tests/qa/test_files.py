"""PDF, PNG, ZIP and coloring-page line-art checks on small generated files."""
import zipfile

import pytest
from PIL import Image, ImageDraw

from qa.lineart import check_coloring, line_weight_px
from qa.pdf_checks import check_pdf
from qa.raster import check_assets, check_png_file, check_zip, intended_size_in

from .conftest import A4, LETTER, by_name, make_item, make_pdf, sticker_png


def pdf_item(tmp_path, settings, size=LETTER, declared="us_letter", pages=2, declared_pages=None, **kw):
    d = tmp_path / "item-1"
    rel = f"files/x-{declared}.pdf"
    make_pdf(d / rel, size, pages=pages, **kw)
    entry = {"path": rel, "kind": "pdf", "page_size": declared, "pages": declared_pages or pages, "dpi": 300}
    return make_item(d, settings, files=[entry]), entry


def test_pdf_correct_sizes_pass(tmp_path, settings):
    for size, name in ((LETTER, "us_letter"), (A4, "a4"), ((612.6, 791.5), "us_letter"), ((792.0, 612.0), "us_letter")):
        item, entry = pdf_item(tmp_path / name, settings, size=size, declared=name)
        checks = check_pdf(item, entry)
        assert all(c["passed"] for c in checks), checks


def test_pdf_wrong_size_fails(tmp_path, settings):
    item, entry = pdf_item(tmp_path, settings, size=A4, declared="us_letter")
    r = by_name(check_pdf(item, entry), "pdf_page_size")[0]
    assert not r["passed"] and r["severity"] == "error" and "595.4x841.7pt" in r["detail"]


def test_pdf_page_count_mismatch(tmp_path, settings):
    item, entry = pdf_item(tmp_path, settings, pages=2, declared_pages=3)
    r = by_name(check_pdf(item, entry), "pdf_page_count")[0]
    assert not r["passed"] and "2 pages, manifest says 3" in r["detail"]


@pytest.mark.parametrize("kw, word", [({"blank": (1,)}, "nearly blank pages: 2"),
                                      ({"black": (0,)}, "nearly all-black pages: 1")])
def test_pdf_blank_and_black_pages(tmp_path, settings, kw, word):
    item, entry = pdf_item(tmp_path, settings, **kw)
    r = by_name(check_pdf(item, entry), "pdf_blank_pages")[0]
    assert not r["passed"] and r["severity"] == "error" and word in r["detail"]


def test_pdf_damaged_file(tmp_path, settings):
    d = tmp_path / "item-1"
    (d / "files").mkdir(parents=True)
    (d / "files/x.pdf").write_bytes(b"%PDF-1.4 this is not a pdf")
    item = make_item(d, settings, files=[{"path": "files/x.pdf", "kind": "pdf", "page_size": "us_letter", "pages": 1}])
    [r] = check_pdf(item, item.entries("files")[0])
    assert r["name"] == "pdf_opens" and not r["passed"]


def test_pdf_embedded_low_res_image_warns(tmp_path, settings):
    art = Image.new("L", (120, 160), 255)
    ImageDraw.Draw(art).ellipse((20, 20, 100, 140), outline=0, width=4)
    item, entry = pdf_item(tmp_path, settings, image=art)
    r = by_name(check_pdf(item, entry), "pdf_image_dpi")[0]
    assert not r["passed"] and r["severity"] == "warning" and "below 300 DPI" in r["detail"]


def test_pdf_embedded_high_res_image_passes(tmp_path, settings):
    art = Image.new("L", (2400, 3150), 255)  # ~machine width 7.5in x 10in at 320 DPI
    ImageDraw.Draw(art).ellipse((200, 200, 2200, 2900), outline=0, width=30)
    item, entry = pdf_item(tmp_path, settings, image=art, pages=1)
    r = by_name(check_pdf(item, entry), "pdf_image_dpi")[0]
    assert r["passed"] and "1 embedded images" in r["detail"]


def png_item(tmp_path, settings, kind="png_transparent", entry_extra=None, **png_kw):
    d = tmp_path / "item-1"
    sticker_png(d / "files/s.png", **png_kw)
    entry = {"path": "files/s.png", "kind": kind, **(entry_extra or {})}
    return make_item(d, settings, niche="stickers", files=[entry]), entry


def test_png_good_sticker(tmp_path, settings):
    item, entry = png_item(tmp_path, settings, entry_extra={"print_size_in": [2, 2]})
    checks = check_png_file(item, entry)
    assert all(c["passed"] for c in checks), checks
    assert "300 DPI" in by_name(checks, "png_resolution")[0]["detail"]


def test_png_too_small_for_print_size(tmp_path, settings):
    item, entry = png_item(tmp_path, settings, entry_extra={"print_size_in": [4, 4]})
    r = by_name(check_png_file(item, entry), "png_resolution")[0]
    assert not r["passed"] and r["severity"] == "error" and "150 DPI" in r["detail"]


def test_png_full_page_uses_page_size(tmp_path, settings):
    d = tmp_path / "item-1"
    d.joinpath("files").mkdir(parents=True)
    Image.new("RGB", (2550, 3300), "white").save(d / "files/p.png", dpi=(300, 300))
    Image.new("RGB", (1275, 1650), "white").save(d / "files/q.png", dpi=(300, 300))
    files = [{"path": "files/p.png", "kind": "png", "page_size": "us_letter"},
             {"path": "files/q.png", "kind": "png", "page_size": "us_letter"}]
    item = make_item(d, settings, files=files)
    assert by_name(check_png_file(item, files[0]), "png_resolution")[0]["passed"]
    assert not by_name(check_png_file(item, files[1]), "png_resolution")[0]["passed"]


def test_png_dpi_from_metadata_and_missing_metadata(tmp_path, settings):
    item, entry = png_item(tmp_path / "a", settings, dpi=72)
    checks = check_png_file(item, entry)
    assert not by_name(checks, "png_resolution")[0]["passed"]
    assert not by_name(checks, "png_dpi_metadata")[0]["passed"]

    item, entry = png_item(tmp_path / "b", settings, dpi=None)
    checks = check_png_file(item, entry)
    res, meta = by_name(checks, "png_resolution")[0], by_name(checks, "png_dpi_metadata")[0]
    assert not res["passed"] and res["severity"] == "warning"
    assert not meta["passed"] and meta["severity"] == "warning"


@pytest.mark.parametrize("png_kw, expect", [
    ({"alpha": False}, "no alpha channel"),
    ({"opaque_border": True}, "edge pixels are transparent"),
    ({"empty": True}, "subject covers only"),
])
def test_png_transparency_failures(tmp_path, settings, png_kw, expect):
    item, entry = png_item(tmp_path, settings, **png_kw)
    r = by_name(check_png_file(item, entry), "png_transparency")[0]
    assert not r["passed"] and r["severity"] == "error" and expect in r["detail"]


def test_asset_transparency_summarized(tmp_path, settings):
    d = tmp_path / "item-1"
    sticker_png(d / "assets/a.png")
    sticker_png(d / "assets/b.png", alpha=False)
    item = make_item(d, settings, niche="stickers", assets=[{"path": "assets/a.png", "kind": "png_transparent"},
                                                           {"path": "assets/b.png", "kind": "png_transparent"}])
    checks = check_assets(item)
    r = by_name(checks, "asset_transparency")[0]
    assert not r["passed"] and "assets/b.png" in r["detail"] and "assets/a.png" not in r["detail"]


def test_zip_checks(tmp_path, settings):
    d = tmp_path / "item-1"
    (d / "files").mkdir(parents=True)
    with zipfile.ZipFile(d / "files/good.zip", "w") as z:
        z.writestr("sticker-01.png", b"x")
    with zipfile.ZipFile(d / "files/evil.zip", "w") as z:
        z.writestr("../evil.png", b"x")
    (d / "files/broken.zip").write_bytes(b"PK\x03\x04 not really")
    files = [{"path": f"files/{n}.zip", "kind": "zip"} for n in ("good", "evil", "broken")]
    item = make_item(d, settings, niche="stickers", files=files)
    good, evil, broken = (by_name(check_zip(item, e), "zip_valid")[0] for e in files)
    assert good["passed"]
    assert not evil["passed"] and "unsafe member" in evil["detail"]
    assert not broken["passed"]


def zip_of(path, members: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, src in members.items():
            z.write(src, name)
    return path


def test_zip_png_members_checked_like_the_buyer_gets_them(tmp_path, settings):
    d = tmp_path / "item-1"
    good = sticker_png(tmp_path / "src/good.png")
    flat = sticker_png(tmp_path / "src/flat.png", alpha=False)
    no_dpi = sticker_png(tmp_path / "src/nodpi.png", dpi=None)
    zip_of(d / "files/ok.zip", {"frog-01.png": good, "frog-02.png": good})
    zip_of(d / "files/bad.zip", {"frog-01.png": good, "frog-02.png": flat, "frog-03.png": no_dpi})
    files = [{"path": "files/ok.zip", "kind": "zip", "count": 2},
             {"path": "files/bad.zip", "kind": "zip", "count": 2}]
    item = make_item(d, settings, niche="stickers", files=files)

    ok = check_zip(item, files[0])
    assert all(c["passed"] for c in ok), ok
    assert "clean transparent edges" in by_name(ok, "zip_png")[0]["detail"]

    bad = check_zip(item, files[1])
    valid, png, dpi = (by_name(bad, n)[0] for n in ("zip_valid", "zip_png", "zip_png_dpi"))
    assert not valid["passed"] and "3 files, manifest count says 2" in valid["detail"]
    assert not png["passed"] and png["severity"] == "error" and "frog-02.png: no alpha" in png["detail"]
    assert not dpi["passed"] and dpi["severity"] == "warning" and "frog-03.png" in dpi["detail"]


def test_zip_png_members_need_no_alpha_outside_stickers(tmp_path, settings):
    d = tmp_path / "item-1"
    zip_of(d / "files/pages.zip", {"page-01.png": sticker_png(tmp_path / "src/p.png", alpha=False)})
    files = [{"path": "files/pages.zip", "kind": "zip"}]
    item = make_item(d, settings, niche="coloring_pages", files=files)
    assert all(c["passed"] for c in check_zip(item, files[0]))


def test_zip_member_that_is_not_a_png_image(tmp_path, settings):
    d = tmp_path / "item-1"
    (d / "files").mkdir(parents=True)
    with zipfile.ZipFile(d / "files/s.zip", "w") as z:
        z.writestr("frog.png", b"not an image")
    files = [{"path": "files/s.zip", "kind": "zip"}]
    r = by_name(check_zip(make_item(d, settings, niche="stickers", files=files), files[0]), "zip_png")[0]
    assert not r["passed"] and "frog.png" in r["detail"]


@pytest.mark.parametrize("extra, expect", [
    ({"print_size_in": [2, 2]}, (2.0, 2.0)),
    ({"print_size_in": [0, 2], "width_in": 3, "height_in": 3}, (3.0, 3.0)),
    ({"print_size_in": ["a", "b"], "page_size": "us_letter"}, (8.5, 11.0)),
    ({"width_in": -1, "height_in": 2}, None),
    ({"page_size": ["us_letter"]}, None),
    ({}, None),
])
def test_intended_size_ignores_nonsense(settings, extra, expect):
    assert intended_size_in(extra, settings) == expect


def test_png_with_zero_print_size_falls_back_to_metadata(tmp_path, settings):
    item, entry = png_item(tmp_path, settings, entry_extra={"print_size_in": [0, 0]})
    r = by_name(check_png_file(item, entry), "png_resolution")[0]
    assert r["passed"] and "from metadata" in r["detail"]


def coloring(tmp_path, settings, **pdf_kw):
    d = tmp_path / "item-1"
    make_pdf(d / "files/c-us_letter.pdf", LETTER, **pdf_kw)
    make_pdf(d / "files/c-a4.pdf", A4, **pdf_kw)
    files = [{"path": "files/c-us_letter.pdf", "kind": "pdf", "page_size": "us_letter", "pages": 2},
             {"path": "files/c-a4.pdf", "kind": "pdf", "page_size": "a4", "pages": 2}]
    return make_item(d, settings, files=files)


def test_coloring_thick_lines_pass(tmp_path, settings):
    checks = check_coloring(coloring(tmp_path, settings, line_pt=3.0))
    assert all(c["passed"] for c in checks), checks
    lw = by_name(checks, "line_weight")[0]
    assert "c-a4.pdf" in lw["detail"]  # measured on the smaller page size


def test_coloring_thin_lines_warn(tmp_path, settings):
    lw = by_name(check_coloring(coloring(tmp_path, settings, line_pt=0.5)), "line_weight")[0]
    assert not lw["passed"] and lw["severity"] == "warning" and "thin pages: 1, 2" in lw["detail"]


def test_coloring_gray_fill_warns(tmp_path, settings):
    r = by_name(check_coloring(coloring(tmp_path, settings, gray_fill=True)), "pure_black_white")[0]
    assert not r["passed"] and r["severity"] == "warning"


def test_coloring_open_page_has_few_regions(tmp_path, settings):
    d = tmp_path / "item-1"
    from reportlab.pdfgen import canvas
    (d / "files").mkdir(parents=True)
    c = canvas.Canvas(str(d / "files/c-us_letter.pdf"), pagesize=LETTER)
    for _ in range(2):  # only open strokes: nothing enclosed to color
        c.setLineWidth(4)
        c.line(100, 100, 500, 700)
        c.line(100, 700, 300, 300)
        c.showPage()
    c.save()
    item = make_item(d, settings, files=[{"path": "files/c-us_letter.pdf", "kind": "pdf", "page_size": "us_letter",
                                          "pages": 2}])
    r = by_name(check_coloring(item), "coloring_regions")[0]
    assert not r["passed"] and r["severity"] == "warning"


def test_line_weight_estimate_tracks_stroke_width():
    import numpy as np

    dpi = 200
    widths = {}
    for px in (2, 4, 8):
        img = Image.new("L", (800, 800), 255)
        ImageDraw.Draw(img).rectangle((100, 100, 700, 700), outline=0, width=px)
        widths[px] = line_weight_px(np.asarray(img), dpi)
    assert widths[2] < widths[4] < widths[8]
    assert all(abs(widths[px] - px) <= 1 for px in widths), widths
    assert line_weight_px(np.full((100, 100), 255, np.uint8), dpi) is None
