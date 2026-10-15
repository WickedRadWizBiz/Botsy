"""Manifest schema, path safety, Etsy limits and compliance checks."""
import os

import pytest

import qa
from core.compliance import Blocklist
from qa.checks import (
    check_ai_flag,
    check_compliance,
    check_etsy_limits,
    check_manifest,
    manifest_texts,
    unsafe_reason,
)

from .conftest import base_manifest, by_name, make_item, make_pdf


@pytest.fixture(scope="module")
def blocklist():
    return Blocklist.load()


def pdf_entry(rel="files/x-us_letter.pdf", pages=2):
    return {"path": rel, "kind": "pdf", "page_size": "us_letter", "pages": pages}


def test_etsy_limits_are_the_verified_values():
    assert qa.DEFAULTS["etsy_max_files"] == 5
    assert qa.DEFAULTS["etsy_max_file_bytes"] == 20_000_000


def test_manifest_schema_passes_for_complete_manifest(tmp_path, settings):
    d = tmp_path / "item-1"
    make_pdf(d / "files/x-us_letter.pdf")
    item = make_item(d, settings, files=[pdf_entry()])
    [r] = check_manifest(item)
    assert r["passed"], r["detail"]


@pytest.mark.parametrize("change, expect", [
    ({"schema": "manifest/v0"}, "schema"),
    ({"theme": "  "}, "theme is empty"),
    ({"niche": "candles"}, "unknown niche"),
    ({"ai_assisted": "yes"}, "ai_assisted should be a bool"),
    ({"files": []}, "files is empty"),
    ({"files": [{"path": "files/a.pdf", "kind": "docx"}]}, "kind 'docx'"),
    ({"files": [{"path": "files/a.pdf", "kind": "pdf", "page_size": "tabloid", "pages": 2}]}, "page_size 'tabloid'"),
    ({"files": [{"path": "files/a.pdf", "kind": "pdf", "page_size": "a4", "pages": 0}]}, "pages must be"),
    ({"status": "active"}, "unknown status"),
])
def test_manifest_schema_failures(tmp_path, settings, change, expect):
    item = make_item(tmp_path / "item-1", settings, **{"files": [pdf_entry()], **change})
    [r] = check_manifest(item)
    assert not r["passed"] and r["severity"] == "error"
    assert expect in r["detail"]


def test_missing_field_and_item_id_mismatch(tmp_path, settings):
    item = make_item(tmp_path / "item-1", settings, files=[pdf_entry()], item_id="other")
    del item.manifest["keywords"]
    [r] = check_manifest(item)
    assert "missing keywords" in r["detail"] and "does not match the folder" in r["detail"]


@pytest.mark.parametrize("rel", ["../outside.pdf", "files/../../outside.pdf", "/etc/passwd", "..\\outside.pdf",
                                 "C:\\Windows\\win.ini", "C:outside.pdf", ""])
def test_unsafe_paths_rejected(rel):
    assert unsafe_reason(rel)


@pytest.mark.parametrize("rel", ["files/a.pdf", "previews/page-01.png", "a..b/c.pdf"])
def test_plain_relative_paths_allowed(rel):
    assert unsafe_reason(rel) is None


def test_path_traversal_is_reported_and_never_opened(tmp_path, settings):
    d = tmp_path / "item-1"
    make_pdf(tmp_path / "outside.pdf")          # exists, but outside the item folder
    make_pdf(d / "files/x-us_letter.pdf")
    item = make_item(d, settings, files=[pdf_entry(), pdf_entry("../outside.pdf")],
                     previews=[{"path": "/etc/hostname", "page": 1}])
    checks = qa.run_checks(item, Blocklist([]))
    safe = by_name(checks, "paths_safe")[0]
    assert not safe["passed"] and "../outside.pdf" in safe["detail"] and "/etc/hostname" in safe["detail"]
    assert item.usable == {"files/x-us_letter.pdf"}
    opened = [c["detail"] for c in by_name(checks, "pdf_opens")]
    assert len(opened) == 1 and "outside" not in opened[0]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_symlink_escaping_item_dir_rejected(tmp_path, settings):
    d = tmp_path / "item-1"
    make_pdf(tmp_path / "secret.pdf")
    (d / "files").mkdir(parents=True)
    try:
        os.symlink(tmp_path / "secret.pdf", d / "files/link.pdf")
    except OSError:
        pytest.skip("cannot create symlinks here")
    item = make_item(d, settings, files=[pdf_entry("files/link.pdf")])
    assert "files/link.pdf" not in item.usable


def test_missing_file_reported(tmp_path, settings):
    d = tmp_path / "item-1"
    d.mkdir()
    item = make_item(d, settings, files=[pdf_entry()])
    checks = qa.run_checks(item, Blocklist([]))
    r = by_name(checks, "paths_exist")[0]
    assert not r["passed"] and "files/x-us_letter.pdf" in r["detail"]


def test_etsy_file_count_and_size(tmp_path, settings):
    d = tmp_path / "item-1"
    entries = []
    for i in range(6):
        rel = f"files/f{i}-us_letter.pdf"
        make_pdf(d / rel, pages=1)
        entries.append(pdf_entry(rel, 1))
    item = make_item(d, settings, files=entries)
    count, size = check_etsy_limits(item)
    assert not count["passed"] and "6 digital files" in count["detail"]
    assert size["passed"]

    item = make_item(d, settings, files=entries[:5])
    item.cfg["etsy_max_file_bytes"] = 500  # tiny limit instead of writing 20 MB files
    count, size = check_etsy_limits(item)
    assert count["passed"] and not size["passed"] and size["severity"] == "error"


def test_compliance_blocked_term_is_error(tmp_path, settings, blocklist):
    item = make_item(tmp_path / "item-1", settings, theme="Disney princess castles")
    r = by_name(check_compliance(item, blocklist), "compliance_blocklist")[0]
    assert not r["passed"] and r["severity"] == "error" and "Disney" in r["detail"]


def test_compliance_blocked_term_in_file_name(tmp_path, settings, blocklist):
    item = make_item(tmp_path / "item-1", settings, files=[pdf_entry("files/paw-patrol-us_letter.pdf")])
    assert "paw-patrol-us_letter.pdf" in manifest_texts(item.manifest)
    assert not by_name(check_compliance(item, blocklist), "compliance_blocklist")[0]["passed"]


def test_compliance_review_term_is_warning(tmp_path, settings, blocklist):
    item = make_item(tmp_path / "item-1", settings, keywords=["Peter Rabbit garden coloring"])
    checks = check_compliance(item, blocklist)
    assert by_name(checks, "compliance_blocklist")[0]["passed"]
    flagged = by_name(checks, "compliance_review_terms")[0]
    assert not flagged["passed"] and flagged["severity"] == "warning" and "Peter Rabbit" in flagged["detail"]


def test_compliance_urls_rejected(tmp_path, settings, blocklist):
    item = make_item(tmp_path / "item-1", settings, prompts=["copy the style of www.example-shop.com/listing/1"])
    r = by_name(check_compliance(item, blocklist), "compliance_urls")[0]
    assert not r["passed"] and r["severity"] == "error"


def test_ai_flag(tmp_path, settings):
    used_model = make_item(tmp_path / "a", settings, ai_assisted=False)
    assert not check_ai_flag(used_model)["passed"]
    assert check_ai_flag(make_item(tmp_path / "b", settings))["passed"]
    procedural = make_item(tmp_path / "c", settings, niche="activity_sheets", ai_assisted=False, prompts=[],
                           generator={"name": "activity_sheets", "dry_run": False})
    assert check_ai_flag(procedural)["passed"]
    unsure = make_item(tmp_path / "d", settings, ai_assisted=False, prompts=[],
                       generator={"name": "coloring_pages", "dry_run": False})
    r = check_ai_flag(unsure)
    assert not r["passed"] and r["severity"] == "warning"


def test_manifest_texts_cover_pack_contents():
    m = base_manifest("x", pack={"contents": [{"page": 1, "type": "maze", "title": "Elsa Maze"}]})
    assert "Elsa Maze" in manifest_texts(m)

