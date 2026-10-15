"""Blocklist over the text inside delivered files and over every manifest text field."""
import zipfile

import pytest
from reportlab.pdfgen import canvas

import qa
from core.compliance import Blocklist
from qa.checks import manifest_texts
from qa.file_text import check_file_text
from qa.mockups import content_count

from .conftest import LETTER, base_manifest, by_name, coloring_item, make_item


@pytest.fixture(scope="module")
def blocklist():
    return Blocklist.load()


def text_pdf(path, lines, title=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=LETTER)
    if title:
        c.setTitle(title)
    for i, line in enumerate(lines):
        c.drawString(72, 700 - 20 * i, line)
    c.showPage()
    c.save()
    return path


def pdf_item(tmp_path, settings, lines, title=""):
    d = tmp_path / "item-1"
    text_pdf(d / "files/pack-us_letter.pdf", lines, title)
    return make_item(d, settings, files=[{"path": "files/pack-us_letter.pdf", "kind": "pdf",
                                          "page_size": "us_letter", "pages": 1}])


def test_clean_printed_text_passes(tmp_path, settings, blocklist):
    # word-search grids come out letter-spaced, so "C A R S" is not the film title
    item = pdf_item(tmp_path, settings, ["Ocean Animals Word Search", "C A R S H A R K", "DOLPHIN"],
                    title="Ocean Animals Activity Pack")
    checks = check_file_text(item, blocklist)
    assert all(c["passed"] for c in checks), checks


def test_blocked_term_printed_on_a_page_is_an_error(tmp_path, settings, blocklist):
    item = pdf_item(tmp_path, settings, ["Winter Word Search", "SNOW", "ELSA"])
    r = by_name(check_file_text(item, blocklist), "compliance_file_text")[0]
    assert not r["passed"] and r["severity"] == "error"
    assert "files/pack-us_letter.pdf: Elsa" in r["detail"]


def test_blocked_term_in_pdf_title_property_is_an_error(tmp_path, settings, blocklist):
    item = pdf_item(tmp_path, settings, ["Farm Animals"], title="Mickey Mouse Coloring Book")
    assert not by_name(check_file_text(item, blocklist), "compliance_file_text")[0]["passed"]


def test_review_term_or_url_in_a_file_is_a_warning(tmp_path, settings, blocklist):
    item = pdf_item(tmp_path, settings, ["A Peter Rabbit garden", "more at www.example-shop.com"])
    checks = check_file_text(item, blocklist)
    assert by_name(checks, "compliance_file_text")[0]["passed"]
    r = by_name(checks, "compliance_file_review_terms")[0]
    assert not r["passed"] and r["severity"] == "warning"
    assert "Peter Rabbit" in r["detail"] and "www.example-shop.com" in r["detail"]


def test_blocked_term_in_zip_member_name(tmp_path, settings, blocklist):
    d = tmp_path / "item-1"
    (d / "files").mkdir(parents=True)
    with zipfile.ZipFile(d / "files/stickers.zip", "w") as z:
        z.writestr("stickers/paw-patrol-01.png", b"x")
    item = make_item(d, settings, niche="stickers", files=[{"path": "files/stickers.zip", "kind": "zip"}])
    r = by_name(check_file_text(item, blocklist), "compliance_file_text")[0]
    assert not r["passed"] and "Paw Patrol" in r["detail"]


def test_damaged_file_is_skipped_not_crashed(tmp_path, settings, blocklist):
    d = tmp_path / "item-1"
    (d / "files").mkdir(parents=True)
    (d / "files/x.pdf").write_bytes(b"%PDF-1.4 nonsense")
    item = make_item(d, settings, files=[{"path": "files/x.pdf", "kind": "pdf", "page_size": "us_letter",
                                          "pages": 1}])
    r = by_name(check_file_text(item, blocklist), "compliance_file_text")[0]
    assert r["passed"] and "1 unreadable" in r["detail"]


def test_item_with_trademark_printed_in_its_pdf_fails_qa(tmp_path, settings):
    d = coloring_item(tmp_path / "out")
    text_pdf(d / "files/farm-animals-a4.pdf", ["Bluey and friends"] * 2)  # theme and file names are clean
    report = qa.run(d, settings=settings, review_dir=tmp_path / "review", mockups=False)
    assert not report["passed"]
    assert not by_name(report["checks"], "compliance_file_text")[0]["passed"]
    assert not (tmp_path / "review" / f"{d.name}.json").exists()


def test_manifest_texts_cover_new_fields_but_not_other_checks_notes():
    m = base_manifest("x", subtitle="Elsa adventures", compliance={"flagged": ["Peter Pan"]},
                      review_reasons=["brief mentions review terms ['Peter Rabbit']"])
    texts = manifest_texts(m)
    assert "Elsa adventures" in texts and "Peter Pan" in texts
    assert not any("Peter Rabbit" in t for t in texts)
    assert "fake-image-model" not in texts and m["brief_file"] not in texts


def test_sticker_count_is_the_whole_pack_not_the_mockup_sample():
    assets = [{"path": f"assets/s-{i}.png", "kind": "png_transparent"} for i in range(40)]
    assert content_count({"niche": "stickers", "assets": assets, "files": []}, 12) == 40
    assert content_count({"niche": "stickers", "assets": [], "files": [{"kind": "zip", "count": 30}]}, 0) == 30
    assert content_count({"niche": "stickers", "pack": {"stickers": 24}, "assets": assets}, 12) == 24
