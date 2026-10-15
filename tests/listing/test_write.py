"""write_listing / run_all with a fake Anthropic client, and the dry-run writer."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import listing
from core.compliance import has_ai_disclosure, load_ai_disclosure
from core.items import read_json, read_manifest, write_json
from listing import model, rules
from listing.__main__ import main

from .conftest import GOOD, ITEM_ID, FakeClient, good, make_item, response


def _write(item, settings, client, **kw):
    return listing.write_listing(item, client=client, settings=settings, **kw)


# ------------------------------------------------------------------ the request

def test_good_response_writes_contract_listing(item, settings):
    client = FakeClient(GOOD)
    lst = _write(item, settings, client)

    assert lst == read_json(item / "listing.json")
    assert read_manifest(item)["status"] == "listed"
    for key in ("schema", "item_id", "shop_id", "title", "tags", "description", "alt_texts", "price_usd", "quantity",
                "who_made", "when_made", "taxonomy_id", "type", "image_paths", "file_paths",
                "ai_disclosure_included", "compliance", "needs_review", "model", "created"):
        assert key in lst
    assert lst["schema"] == "listing/v1" and lst["item_id"] == ITEM_ID and lst["shop_id"] == "main"
    assert lst["title"] == GOOD["title"] and lst["tags"] == GOOD["tags"] and lst["alt_texts"] == GOOD["alt_texts"]
    assert (lst["who_made"], lst["when_made"], lst["type"], lst["quantity"]) == ("i_did", "made_to_order", "download", 999)
    assert lst["price_usd"] == 4.0 and lst["taxonomy_id"] is None
    assert lst["image_paths"] == [f"mockups/mockup-{i:02d}.jpg" for i in range(1, 6)]
    assert lst["file_paths"] == ["files/farm-animals-coloring-pages-us_letter.pdf",
                                 "files/farm-animals-coloring-pages-a4.pdf"]
    assert lst["model"] == "claude-opus-5-5"
    assert lst["writer"] == {"kind": "model", "dry_run": False, "attempts": 1, "repaired": []}
    assert lst["needs_review"] is False and lst["compliance"] == {"blocked": [], "flagged": []}
    assert lst["description"].startswith(GOOD["hook"])
    assert "What's included:\n- 20 farm animals coloring pages" in lst["description"]
    assert "Terms of use:\nFor personal use only." in lst["description"]
    assert len(client.calls) == 1


def test_request_shape_and_inputs(item, settings):
    client = FakeClient(GOOD)
    _write(item, settings, client)
    call = client.calls[0]
    assert call["model"] == settings["models"]["text"]["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == settings["models"]["text"]["max_tokens"]
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": model.SCHEMA}
    assert call["output_config"]["effort"] == "low"
    assert call["betas"] == [model.FALLBACK_BETA] and call["fallbacks"] == "default"
    assert "thinking" not in call and "temperature" not in call

    prompt = call["messages"][0]["content"]
    facts = json.loads(prompt.split("Product facts:\n", 1)[1].split("\n\nWrite the listing.", 1)[0])
    assert facts["theme"] == "farm animals" and facts["keywords"] == ["farm animal coloring pages", "barn animals"]
    assert facts["audience"] == "kids 4-8" and facts["content_count"] == 20 and facts["ai_assisted"] is True
    assert facts["page_sizes"] == ["US Letter (8.5 x 11 in)", "A4 (210 x 297 mm)"] and facts["file_types"] == ["PDF"]
    assert len(facts["images"]) == 5 and facts["images"][0]["shows"].startswith("cover photo")
    # only our own item data: no paths, ids, shop names or URLs
    for leak in ("files/", "mockups/", ITEM_ID, "Test Shop", "brief_file", "http"):
        assert leak not in prompt
    assert "13 different search phrases" in call["system"] and "at most 140 characters" in call["system"]


def test_without_refusal_fallback_uses_plain_messages_api(item, settings):
    settings["listing"]["refusal_fallback"] = False
    client = FakeClient(GOOD)
    client.beta = None  # would raise if used
    _write(item, settings, client)
    assert "betas" not in client.calls[0] and "fallbacks" not in client.calls[0]


def test_text_after_fallback_block_is_used(item, settings):
    blocks = [SimpleNamespace(type="text", text='{"title": "partial'),
              SimpleNamespace(type="fallback"),
              SimpleNamespace(type="text", text=json.dumps(GOOD))]
    client = FakeClient(response(None, blocks=blocks, model="claude-opus-5"))
    lst = _write(item, settings, client)
    assert lst["title"] == GOOD["title"] and lst["model"] == "claude-opus-5"


# ------------------------------------------------------------------ retry with feedback

def test_too_long_title_is_re_asked_with_the_error(item, settings):
    long_title = "Farm Animal Coloring Pages " + "with lots of extra words " * 6
    client = FakeClient(good(title=long_title), GOOD)
    lst = _write(item, settings, client)
    assert len(client.calls) == 2
    retry = client.calls[1]["messages"][0]["content"]
    assert f"title is {len(long_title.strip())} characters; the limit is 140" in retry
    assert "Previous draft:" in retry and "Product facts:" in retry
    assert lst["title"] == GOOD["title"]
    assert lst["writer"]["attempts"] == 2 and lst["writer"]["repaired"] == []


def test_fifteen_tags_retried_then_repaired(item, settings):
    tags15 = GOOD["tags"] + ["farm party", "cow lover gift"]
    client = FakeClient(*[good(tags=tags15)] * 3)
    lst = _write(item, settings, client)
    assert len(client.calls) == 1 + listing.DEFAULTS["max_retries"]
    for call in client.calls[1:]:
        assert "15 tags given; exactly 13 are required" in call["messages"][0]["content"]
    assert lst["tags"] == GOOD["tags"]                      # first 13 valid ones kept
    assert lst["writer"]["kind"] == "model" and lst["writer"]["repaired"] == ["15 tags given; exactly 13 are required"]


def test_twenty_one_char_tag_feedback_and_fallback_repair(item, settings):
    tags = GOOD["tags"][:12] + ["farm animal coloring!"[:-1] + "s"]  # 21 chars
    assert len(tags[-1]) == 21
    client = FakeClient(*[good(tags=tags)] * 3)
    lst = _write(item, settings, client)
    assert "is 21 characters; the limit is 20" in client.calls[1]["messages"][0]["content"]
    assert len(lst["tags"]) == 13 and all(len(t) <= 20 for t in lst["tags"])
    assert lst["tags"][:12] == GOOD["tags"][:12]
    assert rules.validate({**lst, "hook": "h", "about": "a", "suggested_uses": ["a", "b", "c"]}, 5,
                          listing.DEFAULTS) == []


def test_bad_json_and_wrong_shape_are_retried(item, settings):
    client = FakeClient("not json at all", good(tags="one, two"), GOOD)
    lst = _write(item, settings, client)
    assert len(client.calls) == 3
    assert "not the requested JSON object" in client.calls[1]["messages"][0]["content"]
    assert "'tags' must be a list of strings" in client.calls[2]["messages"][0]["content"]
    assert lst["title"] == GOOD["title"]


def test_cut_off_response_asks_for_shorter_copy(item, settings):
    client = FakeClient(response('{"title": "Farm', stop_reason="max_tokens"), GOOD)
    _write(item, settings, client)
    assert "cut off" in client.calls[1]["messages"][0]["content"]


def test_refusal_falls_back_to_template_without_retrying(item, settings):
    client = FakeClient(response("", stop_reason="refusal"))
    lst = _write(item, settings, client)
    assert len(client.calls) == 1
    assert lst["writer"]["kind"] == "template" and lst["writer"]["attempts"] == 1 and lst["model"] is None
    assert rules.validate(rules.normalize_draft({**lst, "hook": "h", "about": "a",
                                                 "suggested_uses": ["a", "b", "c"]}), 5, listing.DEFAULTS) == []


def test_all_attempts_unparseable_uses_template(item, settings):
    client = FakeClient("{", "{", "{")
    lst = _write(item, settings, client)
    assert len(client.calls) == 3 and lst["writer"]["kind"] == "template"
    assert len(lst["tags"]) == 13


# ------------------------------------------------------------------ compliance

def test_blocked_term_raises_and_writes_nothing(item, settings):
    client = FakeClient(good(title="Disney Style Farm Animal Coloring Pages"), GOOD)
    with pytest.raises(listing.ListingComplianceError, match="Disney") as exc:
        _write(item, settings, client)
    assert exc.value.terms == ["Disney"]
    assert len(client.calls) == 1                          # no retry around a trademark
    assert not (item / "listing.json").exists()
    assert read_manifest(item)["status"] == "qa_passed"


def test_blocked_term_in_item_data_is_never_sent(tmp_path, settings):
    d = make_item(tmp_path / "out", keywords=["paw patrol coloring"])
    client = FakeClient(GOOD)
    with pytest.raises(listing.ListingComplianceError, match="Paw Patrol"):
        _write(d, settings, client)
    assert client.calls == [] and not (d / "listing.json").exists()


def test_url_in_item_data_is_refused(tmp_path, settings):
    d = make_item(tmp_path / "out", theme="farm animals www.example.com")
    with pytest.raises(listing.ListingComplianceError, match="URL"):
        listing.write_listing(d, dry_run=True, settings=settings)


def test_flagged_term_sets_needs_review_and_queues_it(item, settings):
    rdir = item.parent / "review"
    client = FakeClient(good(about="A sweet set inspired by Cinderella's farm friends. Big shapes for small hands."))
    lst = _write(item, settings, client, review_dir=rdir)
    assert lst["needs_review"] is True and lst["compliance"]["flagged"] == ["Cinderella"]
    assert any("Cinderella" in r for r in lst["review_reasons"])
    entry = read_json(rdir / f"{ITEM_ID}.json")
    assert entry["status"] == "pending" and entry["needs_review"] is True
    assert any("Cinderella" in r for r in entry["reasons"])


def test_flagged_listing_reopens_an_earlier_approval(item, settings):
    rdir = item.parent / "review"
    write_json(rdir / f"{ITEM_ID}.json", {"item_id": ITEM_ID, "niche": "coloring_pages", "status": "approved",
                                          "needs_review": False, "reasons": [], "mockups": []})
    _write(item, settings, FakeClient(good(hook="Color a whole Snow White farm scene with these cute pages.")),
           review_dir=rdir)
    entry = read_json(rdir / f"{ITEM_ID}.json")
    assert entry["status"] == "pending" and entry["previous_status"] == "approved"
    # the same copy again does not reset a fresh approval
    entry["status"] = "approved"
    write_json(rdir / f"{ITEM_ID}.json", entry)
    _write(item, settings, FakeClient(good(hook="Color a whole Snow White farm scene with these cute pages.")),
           review_dir=rdir)
    assert read_json(rdir / f"{ITEM_ID}.json")["status"] == "approved"


def test_clean_listing_leaves_review_queue_alone(item, settings):
    _write(item, settings, FakeClient(GOOD), review_dir=item.parent / "review")
    assert not (item.parent / "review").exists()


def test_no_urls_in_any_field_after_repair(item, settings):
    bad = good(hook="See shop.com", suggested_uses=["Find more at https://example.org", "Quiet time", "Party fun",
                                                   "Travel fun"], alt_texts=["www.x.com"] + GOOD["alt_texts"][1:])
    lst = _write(item, settings, FakeClient(bad, bad, bad))
    from core.compliance import find_urls

    assert find_urls([lst["title"], *lst["tags"], lst["description"], *lst["alt_texts"]]) == []


# ------------------------------------------------------------------ AI disclosure

@pytest.mark.parametrize("ai_assisted, always, included", [
    (True, True, True), (True, False, True), (False, True, True), (False, False, False)])
def test_disclosure_inclusion(tmp_path, settings, ai_assisted, always, included):
    settings["listing"]["always_disclose_ai"] = always
    d = make_item(tmp_path / "out", ai_assisted=ai_assisted)
    lst = listing.write_listing(d, dry_run=True, settings=settings)
    assert lst["ai_disclosure_included"] is included
    assert has_ai_disclosure(lst["description"], "Test Shop") is included
    if included:
        assert lst["description"].endswith(load_ai_disclosure("Test Shop"))  # verbatim, at the end
    else:
        assert "No physical item will be shipped." in lst["description"]


# ------------------------------------------------------------------ dry run and other fields

@pytest.mark.parametrize("niche", ["coloring_pages", "activity_sheets", "stickers"])
def test_dry_run_is_valid_and_deterministic(tmp_path, settings, niche, monkeypatch):
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: pytest.fail("dry run built an API client"))
    d = make_item(tmp_path / "out", niche=niche)
    first = listing.write_listing(d, dry_run=True, settings=settings)
    assert first["writer"]["kind"] == "template" and first["model"] is None
    assert first["writer"]["repaired"] == []                 # template copy passes the validator as is
    cfg = listing.DEFAULTS
    assert len(first["title"]) <= cfg["title_max_chars"] and rules.title_errors(first["title"], cfg) == []
    assert len(first["tags"]) == 13 and len({rules.tag_key(t) for t in first["tags"]}) == 13
    assert all(rules.tag_errors(t, cfg) == [] for t in first["tags"])
    assert len(first["alt_texts"]) == 5 and all(0 < len(a) <= 500 for a in first["alt_texts"])
    assert first["ai_disclosure_included"] is True
    write_json(d / "manifest.json", {**read_manifest(d), "status": "qa_passed"})
    again = listing.write_listing(d, dry_run=True, settings=settings)
    assert again == first


def test_dry_run_niche_specific_content(tmp_path, settings):
    act = listing.write_listing(make_item(tmp_path / "a", niche="activity_sheets"), dry_run=True, settings=settings)
    assert "5 mazes and 5 word searches" in act["description"] and "Answer keys (3 pages)" in act["description"]
    assert "word search" in act["tags"]
    st = listing.write_listing(make_item(tmp_path / "s", niche="stickers"), dry_run=True, settings=settings)
    assert "ZIP file with 12 transparent PNG files" in st["description"]
    assert "Printable sticker sheet PDF in US Letter (8.5 x 11 in) and A4 (210 x 297 mm), 1 page each" in st["description"]
    assert "happy cow stickers" in st["tags"] and "farm animals digital" not in st["tags"]
    assert "Transparent PNG Stickers" in st["title"]


def test_alt_texts_follow_mockups(tmp_path, settings):
    d = make_item(tmp_path / "out", mockups=3)  # fewer than QA's 5 layouts: generic descriptions
    lst = listing.write_listing(d, dry_run=True, settings=settings)
    assert len(lst["alt_texts"]) == 3 and lst["alt_texts"][0].startswith("Listing photo 1 of 3")
    d0 = make_item(tmp_path / "none", mockups=0)
    lst0 = listing.write_listing(d0, dry_run=True, settings=settings)
    assert lst0["alt_texts"] == [] and lst0["image_paths"] == []


def test_missing_mockup_file_is_left_out(item, settings):
    (item / "mockups" / "mockup-03.jpg").unlink()
    lst = listing.write_listing(item, dry_run=True, settings=settings)
    assert "mockups/mockup-03.jpg" not in lst["image_paths"] and len(lst["alt_texts"]) == 4


def test_price_fallback_and_taxonomy(tmp_path, settings):
    settings["listing"]["taxonomy_ids"] = {"coloring_pages": 1234}
    d = make_item(tmp_path / "out", price_usd=None)
    lst = listing.write_listing(d, dry_run=True, settings=settings)
    assert lst["price_usd"] == settings["shops"][0]["default_price_usd"]["coloring_pages"]
    assert lst["taxonomy_id"] == 1234 and not any("taxonomy" in w for w in lst["warnings"])
    settings["listing"]["taxonomy_ids"] = {"coloring_pages": "abc"}
    with pytest.raises(listing.ListingError, match="taxonomy_ids"):
        listing.write_listing(make_item(tmp_path / "out2"), dry_run=True, settings=settings)


def test_placeholder_shop_name_is_a_warning(item, settings):
    settings["shops"][0]["name"] = "YOUR_SHOP_NAME"
    lst = listing.write_listing(item, dry_run=True, settings=settings)
    assert any("placeholder" in w for w in lst["warnings"])


def test_needs_review_carries_earlier_flags(tmp_path, settings):
    d = make_item(tmp_path / "out", needs_review=True)
    lst = listing.write_listing(d, dry_run=True, settings=settings)
    assert lst["needs_review"] is True and lst["review_reasons"]


@pytest.mark.parametrize("status, passed, match", [
    ("generated", True, "only qa_passed or listed"), ("qa_failed", False, "only qa_passed"),
    ("drafted", True, "only qa_passed"), ("qa_passed", False, "no passing qa_report")])
def test_only_qa_passed_items_are_listed(tmp_path, settings, status, passed, match):
    d = make_item(tmp_path / "out", status=status, qa_passed=passed)
    with pytest.raises(listing.ListingError, match=match):
        listing.write_listing(d, dry_run=True, settings=settings)


def test_unsafe_paths_are_refused(tmp_path, settings):
    d = make_item(tmp_path / "out")
    report = read_json(d / "qa_report.json")
    write_json(d / "qa_report.json", {**report, "mockups": ["../elsewhere.jpg"]})
    with pytest.raises(listing.ListingError, match="unsafe mockup path"):
        listing.write_listing(d, dry_run=True, settings=settings)


# ------------------------------------------------------------------ batch and CLI

def test_run_all_lists_qa_passed_items_only(tmp_path, settings):
    out = tmp_path / "out"
    make_item(out, item_id="a-1")
    make_item(out, item_id="b-2", status="generated")
    make_item(out, item_id="c-3", keywords=["peppa pig coloring"])
    make_item(out, item_id="d-4")
    write_json(out / "review" / "d-4.json", {"item_id": "d-4", "status": "rejected", "reasons": ["no"]})
    results = listing.run_all(out, dry_run=True, settings=settings)
    assert [(r["item_id"], r["ok"]) for r in results] == [("a-1", True), ("c-3", False)]
    assert "ListingComplianceError" in results[1]["error"]
    assert read_manifest(out / "a-1")["status"] == "listed"


def test_run_all_shares_one_client(tmp_path, settings):
    out = tmp_path / "out"
    make_item(out, item_id="a-1")
    make_item(out, item_id="b-2")
    client = FakeClient(GOOD, GOOD)
    results = listing.run_all(out, client=client, settings=settings)
    assert all(r["ok"] for r in results) and len(client.calls) == 2


def test_cli_dry_run(tmp_path, settings, capsys):
    out = tmp_path / "out"
    make_item(out)
    settings_file = tmp_path / "settings.yaml"
    import yaml

    settings_file.write_text(yaml.safe_dump(settings), encoding="utf-8")
    assert main(["--out", str(out), "--dry-run", "--settings", str(settings_file)]) == 0
    printed = capsys.readouterr().out
    assert "OK    " + ITEM_ID in printed and "1/1 listed" in printed
    assert main(["--out", str(out), "--item", ITEM_ID, "--dry-run", "--settings", str(settings_file)]) == 0
    assert main(["--out", str(out), "--item", "../escape", "--dry-run"]) == 2
    make_item(out, item_id="z-9", keywords=["minecraft coloring"])
    assert main(["--out", str(out), "--item", "z-9", "--dry-run", "--settings", str(settings_file)]) == 1


# ------------------------------------------------------------------ dry runs on real items

REAL_GEN = {"name": "coloring_pages", "model": "image-model-x", "dry_run": False}


def test_dry_run_on_a_real_item_only_previews(tmp_path, settings):
    d = make_item(tmp_path / "out", generator=REAL_GEN, keywords=["cinderella coloring pages"])
    lst = listing.write_listing(d, dry_run=True, settings=settings)
    assert lst["preview"] is True and lst["dry_run"] is True and lst["writer"]["kind"] == "template"
    assert lst["needs_review"] is True  # the preview still runs every check
    assert not (d / "listing.json").exists() and read_manifest(d)["status"] == "qa_passed"
    assert not (tmp_path / "out" / "review").exists()  # no review entry for copy nobody will use
    # a dry-run generated item still completes the simulated pipeline
    sim = make_item(tmp_path / "sim")
    assert "preview" not in listing.write_listing(sim, dry_run=True, settings=settings)
    assert read_manifest(sim)["status"] == "listed" and (sim / "listing.json").exists()


def test_dry_run_never_replaces_a_real_listing(tmp_path, settings):
    d = make_item(tmp_path / "out", generator=REAL_GEN)
    real = _write(d, settings, FakeClient(GOOD))
    before = (d / "listing.json").read_bytes()
    assert listing.run_all(tmp_path / "out", dry_run=True, settings=settings) == []  # listed: nothing to do
    assert listing.write_listing(d, dry_run=True, settings=settings)["preview"] is True
    assert (d / "listing.json").read_bytes() == before and read_json(d / "listing.json") == real
    assert read_manifest(d)["status"] == "listed"


def test_real_run_relists_a_real_item_stuck_with_dry_run_copy(tmp_path, settings):
    out = tmp_path / "out"
    stuck = make_item(out, item_id="a-1", generator=REAL_GEN, status="listed")
    write_json(stuck / "listing.json", {"title": "T", "dry_run": True, "writer": {"kind": "template", "dry_run": True}})
    old_style = make_item(out, item_id="b-2", generator=REAL_GEN, status="listed")  # only writer.dry_run set
    write_json(old_style / "listing.json", {"title": "T", "writer": {"kind": "template", "dry_run": True}})
    real = make_item(out, item_id="c-3", generator=REAL_GEN, status="listed")
    write_json(real / "listing.json", {"title": "T", "shop_id": "main", "description": load_ai_disclosure("Test Shop"),
                                       "dry_run": False, "writer": {"kind": "model", "dry_run": False}})
    sim = make_item(out, item_id="d-4", status="listed")  # dry-run generated: its template copy is expected
    write_json(sim / "listing.json", {"title": "T", "dry_run": True, "writer": {"kind": "template", "dry_run": True}})
    assert [listing.needs_listing(d, settings=settings) for d in (stuck, old_style, real, sim)] == \
        [True, True, False, False]
    assert not listing.needs_listing(stuck, dry_run=True)  # a dry run would only preview it again
    client = FakeClient(GOOD, GOOD)
    results = listing.run_all(out, client=client, settings=settings)
    assert [(r["item_id"], r["ok"]) for r in results] == [("a-1", True), ("b-2", True)] and len(client.calls) == 2
    for d in (stuck, old_style):
        lst = read_json(d / "listing.json")
        assert lst["dry_run"] is False and lst["writer"]["kind"] == "model" and lst["title"] == GOOD["title"]
    assert read_json(sim / "listing.json")["title"] == "T"


def test_real_run_relists_copy_written_before_the_shop_name_was_set(tmp_path, settings):
    """The publisher refuses copy whose disclosure names another shop or that shows the
    placeholder; a real run re-lists it once the name is set, instead of leaving it stuck."""
    out = tmp_path / "out"
    old_name = make_item(out, item_id="a-1", generator=REAL_GEN, status="listed")
    write_json(old_name / "listing.json", {"title": "T", "shop_id": "main", "description": load_ai_disclosure("Old Shop"),
                                           "writer": {"kind": "model", "dry_run": False}})
    placeholder = make_item(out, item_id="b-2", generator=REAL_GEN, status="listed")
    write_json(placeholder / "listing.json", {"title": "T", "shop_id": "main",
                                              "description": load_ai_disclosure("Test Shop") + " YOUR_SHOP_NAME",
                                              "writer": {"kind": "model", "dry_run": False}})
    assert listing.needs_listing(old_name, settings=settings) and listing.needs_listing(placeholder, settings=settings)
    assert not listing.needs_listing(old_name, dry_run=True, settings=settings)
    unset = json.loads(json.dumps(settings))
    unset["shops"][0]["name"] = "YOUR_SHOP_NAME"  # re-listing now would only write the placeholder again
    assert not listing.needs_listing(old_name, settings=unset) and not listing.needs_listing(placeholder, settings=unset)
    client = FakeClient(GOOD, GOOD)
    results = listing.run_all(out, client=client, settings=settings)
    assert [r["ok"] for r in results] == [True, True] and len(client.calls) == 2
    for d in (old_name, placeholder):
        assert has_ai_disclosure(read_json(d / "listing.json")["description"], "Test Shop")
        assert not listing.needs_listing(d, settings=settings)


def test_cli_dry_run_on_a_real_item_says_preview(tmp_path, settings, capsys):
    import yaml

    out = tmp_path / "out"
    d = make_item(out, generator=REAL_GEN)
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(yaml.safe_dump(settings), encoding="utf-8")
    assert main(["--out", str(out), "--dry-run", "--settings", str(settings_file)]) == 0
    printed = capsys.readouterr().out
    assert "preview (dry run on a real item; nothing written)" in printed and "1 preview only" in printed
    assert not (d / "listing.json").exists() and read_manifest(d)["status"] == "qa_passed"


# ------------------------------------------------------------------ review queue folder

def _configured(tmp_path, settings):
    """Settings whose configured output dir is tmp_path/out and review queue tmp_path/queue."""
    import yaml

    settings["paths"] = {**settings["paths"], "output_dir": str(tmp_path / "out"), "review_dir": str(tmp_path / "queue")}
    f = tmp_path / "settings.yaml"
    f.write_text(yaml.safe_dump(settings), encoding="utf-8")
    return f


def test_cli_out_is_the_configured_output_dir_uses_the_configured_queue(tmp_path, settings):
    settings_file = _configured(tmp_path, settings)
    out, queue = tmp_path / "out", tmp_path / "queue"
    make_item(out, keywords=["cinderella coloring pages"])
    write_json(queue / f"{ITEM_ID}.json", {"item_id": ITEM_ID, "niche": "coloring_pages", "status": "approved",
                                           "needs_review": False, "reasons": [], "mockups": []})
    make_item(out, item_id="r-1")
    write_json(queue / "r-1.json", {"item_id": "r-1", "status": "rejected", "reasons": ["no"]})
    assert main(["--out", str(out), "--dry-run", "--settings", str(settings_file)]) == 0
    entry = read_json(queue / f"{ITEM_ID}.json")  # the queue QA and the publisher read
    assert entry["status"] == "pending" and entry["previous_status"] == "approved"
    assert any("Cinderella" in r for r in entry["reasons"])
    assert not (out / "review").exists()
    assert read_manifest(out / "r-1")["status"] == "qa_passed"  # rejection read from the same queue


def test_cli_review_dir_option_still_wins(tmp_path, settings):
    settings_file = _configured(tmp_path, settings)
    out, other = tmp_path / "out", tmp_path / "other-queue"
    make_item(out, keywords=["cinderella coloring pages"])
    assert main(["--out", str(out), "--item", ITEM_ID, "--dry-run", "--review-dir", str(other),
                 "--settings", str(settings_file)]) == 0
    assert (other / f"{ITEM_ID}.json").exists() and not (tmp_path / "queue").exists()
