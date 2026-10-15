"""Risky paths found in review: blocklist field boundaries, blocked terms in every field,
API failures, stale listings, multi-part ZIPs, path escapes and what reaches the model."""
from __future__ import annotations

import copy
import json
import os

import anthropic
import httpx2
import pytest
from anthropic import DefaultHttpxClient

import listing
from core.compliance import Blocklist
from core.items import read_json, read_manifest, write_json
from listing import rules, template
from listing.__main__ import main

from .conftest import GOOD, ITEM_ID, FakeClient, good, make_item, response

CFG = copy.deepcopy(listing.DEFAULTS)


def _write(item, settings, client, **kw):
    return listing.write_listing(item, client=client, settings=settings, **kw)


# ------------------------------------------------------------------ blocklist field boundaries

def test_blocklist_does_not_read_a_term_across_fields():
    # The listing stage passes each field (and each description line) to Blocklist.check
    # as its own string; core checks each one on its own.
    bl = Blocklist.load()
    assert bl.check("wooden toy", "story time").blocked == []
    assert bl.check("a toy story night").blocked == ["Toy Story"]  # within a field it still hits
    assert bl.check("", "  ", "Cinderella castle").flagged == ["Cinderella"]


def test_adjacent_tags_that_spell_a_trademark_are_not_blocked(item, settings):
    tags = GOOD["tags"][:11] + ["wooden toy", "story time"]
    lst = _write(item, settings, FakeClient(good(tags=tags)))
    assert lst["tags"][-2:] == ["wooden toy", "story time"] and lst["compliance"]["blocked"] == []


@pytest.mark.parametrize("field, value", [
    ("tags", GOOD["tags"][:12] + ["lego coloring"]),
    ("alt_texts", ["A Mickey Mouse farm scene"] + GOOD["alt_texts"][1:]),
    ("suggested_uses", ["Peppa Pig party fun", "Quiet time", "Rainy days"]),
    ("hook", "Bluey and friends visit the farm."),
])
def test_blocked_term_in_any_model_field_raises(item, settings, field, value):
    client = FakeClient(good(**{field: value}))
    with pytest.raises(listing.ListingComplianceError):
        _write(item, settings, client)
    assert len(client.calls) == 1 and not (item / "listing.json").exists()


def test_blocked_term_on_a_retry_stops_there(item, settings):
    client = FakeClient(good(title="x" * 150), good(title="Disney Farm Coloring Pages"), GOOD)
    with pytest.raises(listing.ListingComplianceError, match="Disney"):
        _write(item, settings, client)
    assert len(client.calls) == 2 and not (item / "listing.json").exists()
    assert read_manifest(item)["status"] == "qa_passed"


def test_rerun_refusal_withdraws_the_earlier_listing(item, settings):
    first = _write(item, settings, FakeClient(GOOD))
    assert read_manifest(item)["status"] == "listed"
    with pytest.raises(listing.ListingComplianceError):
        _write(item, settings, FakeClient(good(hook="Frozen fun on the farm.")))
    assert not (item / "listing.json").exists()
    assert read_json(item / "listing.withdrawn.json") == first
    assert read_manifest(item)["status"] == "qa_passed"  # not left ready for the publisher


def test_handmade_claims_are_rejected():
    for text in ("Handmade farm pages", "hand-made with love", "Each page is made by hand"):
        assert rules.misleading(text, CFG), text


# ------------------------------------------------------------------ model input and output

def test_only_product_facts_reach_the_model(tmp_path, settings):
    secret = "Simple coloring page of a friendly cow, PROMPT-TEXT-7731"
    d = make_item(tmp_path / "out", prompts=[secret], brief_id="coloring_pages-2026-10-15-99",
                  generator={"name": "coloring_pages", "model": "image-model-x", "dry_run": False})
    client = FakeClient(GOOD)
    _write(d, settings, client)
    sent = json.dumps(client.calls[0], default=str)
    for leak in ("PROMPT-TEXT-7731", "coloring_pages-2026-10-15-99", "image-model-x", "data/briefs", "4.0"):
        assert leak not in client.calls[0]["messages"][0]["content"], leak
    assert "Test Shop" not in sent


def test_model_name_is_the_one_that_wrote_the_kept_draft(item, settings):
    one_error = good(tags=GOOD["tags"][:12])                   # 12 tags: one problem
    two_errors = good(tags=GOOD["tags"][:12], hook="")          # two problems
    client = FakeClient(response(one_error, model="claude-opus-5"), response(two_errors), response(two_errors))
    lst = _write(item, settings, client)
    assert lst["writer"]["attempts"] == 3 and lst["model"] == "claude-opus-5"
    assert len(lst["tags"]) == 13 and lst["tags"][:12] == GOOD["tags"][:12]


def test_dry_run_flag_is_top_level_for_the_publisher(item, settings, tmp_path):
    assert listing.write_listing(item, dry_run=True, settings=settings)["dry_run"] is True
    d = make_item(tmp_path / "other")
    assert _write(d, settings, FakeClient(GOOD))["dry_run"] is False


def test_missing_text_model_setting_is_a_listing_error(item, settings):
    settings["models"]["text"].pop("model")
    with pytest.raises(listing.ListingError, match="models.text.model"):
        _write(item, settings, FakeClient(GOOD))


# ------------------------------------------------------------------ API failures

class _Failing:
    def __init__(self, exc):
        self.calls = []
        create = self.create
        self.messages = type("M", (), {"create": staticmethod(create)})()
        self.beta = type("B", (), {"messages": self.messages})()
        self.exc = exc

    def create(self, **kwargs):
        self.calls.append(kwargs)
        raise self.exc


def test_api_error_through_the_real_sdk_is_a_listing_error(item, settings):
    def handler(request):
        return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}})

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    with pytest.raises(listing.ListingError, match="text model request failed: InternalServerError"):
        _write(item, settings, client)
    assert not (item / "listing.json").exists() and read_manifest(item)["status"] == "qa_passed"


def test_connection_error_is_a_listing_error_and_run_all_continues(tmp_path, settings):
    out = tmp_path / "out"
    make_item(out, item_id="a-1")
    make_item(out, item_id="b-2")
    err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    client = _Failing(err)
    results = listing.run_all(out, client=client, settings=settings)
    assert [r["ok"] for r in results] == [False, False] and len(client.calls) == 2
    assert all(r["error"].startswith("ListingError") for r in results)


# ------------------------------------------------------------------ item data edge cases

def test_sticker_pack_split_over_zip_parts(tmp_path, settings):
    d = make_item(tmp_path / "out", niche="stickers")
    m = read_manifest(d)
    zips = [{"path": f"files/farm-animals-stickers-png-part-{k}.zip", "kind": "zip", "count": 12, "dpi": 300,
             "part": k, "parts": 2} for k in (1, 2)]
    for z in zips:
        (d / z["path"]).write_bytes(b"placeholder")
    m["files"] = zips + [f for f in m["files"] if f["kind"] == "pdf"]
    m["pack"].pop("stickers")  # count must come from all parts, not the first
    write_json(d / "manifest.json", m)
    lst = listing.write_listing(d, dry_run=True, settings=settings)
    assert "2 ZIP files with 24 transparent PNG files in total" in lst["description"]
    assert "24 farm animals digital stickers" in lst["description"]
    assert lst["file_paths"][:2] == [z["path"] for z in zips]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_symlink_out_of_the_item_folder_is_refused(tmp_path, settings):
    d = make_item(tmp_path / "out")
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"not ours")
    link = d / "files" / "farm-animals-coloring-pages-a4.pdf"
    link.unlink()
    link.symlink_to(outside)
    with pytest.raises(listing.ListingError, match="unsafe or missing file path"):
        listing.write_listing(d, dry_run=True, settings=settings)


def test_corrupt_manifest_is_a_listing_error(item, settings):
    (item / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(listing.ListingError, match="unreadable"):
        listing.write_listing(item, dry_run=True, settings=settings)


def test_unreadable_review_entry_blocks_a_flagged_listing(item, settings):
    rdir = item.parent / "review"
    rdir.mkdir()
    (rdir / f"{ITEM_ID}.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(listing.ListingError, match="review queue"):
        _write(item, settings, FakeClient(good(hook="A Cinderella farm ball for little ones.")), review_dir=rdir)
    assert not (item / "listing.json").exists()
    assert (rdir / f"{ITEM_ID}.json").read_text(encoding="utf-8") == "{broken"  # never overwritten


def test_empty_theme_keywords_and_audience_still_list(tmp_path, settings):
    d = make_item(tmp_path / "out", theme="", keywords=[], audience=None, pack={})
    lst = listing.write_listing(d, dry_run=True, settings=settings)
    assert lst["writer"]["repaired"] == [] and len(lst["tags"]) == 13
    assert rules.title_errors(lst["title"], CFG) == []


def test_bad_counts_in_the_manifest_are_not_claimed(tmp_path, settings):
    d = make_item(tmp_path / "out", niche="activity_sheets")
    m = read_manifest(d)
    m["pack"].update(answer_key_pages=-1, activity_pages=True)  # generator math gone wrong
    write_json(d / "manifest.json", m)
    lst = listing.write_listing(d, dry_run=True, settings=settings)
    assert "Answer keys" not in lst["description"]
    assert "14 farm animals activity pages" in lst["description"]  # falls back to the PDF page count


def test_template_title_does_not_stuff_keyword_variations(tmp_path, settings):
    kws = ["alphabet activity sheets", "alphabet activity sheets bundle", "simple alphabet activity sheets",
           "abc tracing worksheets"]
    d = make_item(tmp_path / "out", niche="activity_sheets", theme="alphabet", keywords=kws)
    lst = listing.write_listing(d, dry_run=True, settings=settings)
    title = lst["title"]
    assert title.lower().count("alphabet activity sheets") == 1, title
    assert "ABC Tracing Worksheets" in title and "10 Printable Pages" in title


def test_template_title_keeps_the_count_even_when_words_repeat(settings):
    facts = {"niche": "coloring_pages", "keywords": ["printable coloring pages"], "audience": "", "theme": "farm",
             "content_count": 20, "page_sizes": [], "activities": {}, "subjects": []}
    assert template.title(facts, CFG).startswith("Printable Coloring Pages, 20 Printable Pages")


# ------------------------------------------------------------------ CLI

def test_cli_single_item_reports_unexpected_errors(tmp_path, settings, capsys):
    import yaml

    out = tmp_path / "out"
    d = make_item(out)
    (d / "manifest.json").write_text("[]", encoding="utf-8")
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(yaml.safe_dump(settings), encoding="utf-8")
    assert main(["--out", str(out), "--item", ITEM_ID, "--dry-run", "--settings", str(settings_file)]) == 1
    assert "FAIL  " + ITEM_ID in capsys.readouterr().out


# ------------------------------------------------------------------ setup errors and fixtures

@pytest.fixture
def no_api_key(monkeypatch):
    import core.config

    monkeypatch.setattr(core.config, "load_env", lambda: None)  # never read a real .env
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_missing_api_key_is_a_setup_error(no_api_key, monkeypatch):
    from core.config import MissingSecretError

    with pytest.raises(MissingSecretError, match="ANTHROPIC_API_KEY"):
        listing.default_client()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert listing.default_client().api_key == "test-key"  # passed explicitly, no request made


def test_cli_without_api_key_stops_before_any_item(no_api_key, tmp_path, settings, capsys, caplog):
    import yaml

    out = tmp_path / "out"
    a, b = make_item(out, item_id="a-1"), make_item(out, item_id="b-2")
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(yaml.safe_dump(settings), encoding="utf-8")
    assert main(["--out", str(out), "--settings", str(settings_file)]) == 2
    assert main(["--out", str(out), "--item", "a-1", "--settings", str(settings_file)]) == 2
    captured = capsys.readouterr()
    assert captured.err.count("ANTHROPIC_API_KEY is not set") == 2 and "FAIL" not in captured.out
    assert "crashed" not in caplog.text and "Traceback" not in captured.err
    for d in (a, b):
        assert not (d / "listing.json").exists() and read_manifest(d)["status"] == "qa_passed"
    assert main(["--out", str(out), "--dry-run", "--settings", str(settings_file)]) == 0  # dry run needs no key


def test_settings_fixture_ignores_the_values_the_user_fills_in(base_settings):
    from .conftest import pinned

    filled = copy.deepcopy(base_settings)
    filled["shops"][0]["name"] = "Maple Lane Prints"
    filled["shops"][0]["default_price_usd"] = {"stickers": 2.75, "coloring_pages": 5.25, "activity_sheets": 6.0}
    filled["listing"]["taxonomy_ids"] = {"stickers": 111, "coloring_pages": 222, "activity_sheets": 333}
    assert pinned(filled) == pinned(base_settings)
