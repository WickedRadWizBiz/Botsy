"""publish_draft gates, idempotency, resume after failures, dry runs, discard, run_all."""
from __future__ import annotations

import copy
import json

import pytest
import yaml

import publisher
from core.compliance import load_ai_disclosure
from core.items import read_json, read_manifest, write_json
from publisher import __main__ as cli
from publisher.client import EtsyAPIError
from publisher.fake import FakeEtsyClient, FakeEtsySession
from publisher.publish import (
    PublishError,
    PublishGateError,
    check_ready,
    discard_draft,
    publish_draft,
    run_all,
)

from .conftest import ITEM_ID, SHOP_ID, make_item


def ops(session) -> list[str]:
    return [r["operation"] for r in session.requests]


def test_publish_creates_draft_uploads_in_order_and_records(item, client, session, settings, review_dir):
    rec = publish_draft(item, client=client, settings=settings, review_dir=review_dir)
    assert ops(session) == ["getMe", "getShop", "createDraftListing", "uploadListingImage",
                            "uploadListingImage", "uploadListingFile"]
    saved = read_json(item / "publish.json")
    assert saved == rec
    assert {k: saved[k] for k in ("schema", "item_id", "shop_id", "state", "dry_run", "complete")} == {
        "schema": "publish/v1", "item_id": ITEM_ID, "shop_id": "main", "state": "draft",
        "dry_run": False, "complete": True}
    assert isinstance(saved["listing_id"], int) and saved["etsy_shop_id"] == SHOP_ID
    assert len(saved["image_ids"]) == 2 and len(saved["file_ids"]) == 1 and saved["created"]
    assert read_manifest(item)["status"] == "drafted"

    create = session.calls("createDraftListing")[0]["data"]
    assert create["type"] == "download" and create["price"] == "4.00" and create["quantity"] == "999"
    assert create["taxonomy_id"] == "1234" and create["who_made"] == "i_did"
    assert create["when_made"] == "made_to_order" and create["is_supply"] == "false"
    assert create["tags"].split(",")[0] == "farm coloring" and len(create["tags"].split(",")) == 13
    assert "state" not in create
    images = session.calls("uploadListingImage")
    assert [r["data"]["rank"] for r in images] == ["1", "2"]
    assert [r["files"]["image"]["filename"] for r in images] == ["mockup-01.jpg", "mockup-02.jpg"]
    assert images[0]["data"]["alt_text"] == "Farm animal coloring page mockup 1"
    assert session.calls("uploadListingFile")[0]["data"] == {"name": "farm-animals-1.pdf", "rank": "1"}
    assert all(r["method"] != "PATCH" and r["data"].get("state") in (None, "draft") for r in session.requests)


def test_second_run_is_a_no_op(item, client, session, settings, review_dir):
    first = publish_draft(item, client=client, settings=settings, review_dir=review_dir)
    n = len(session.requests)
    again = publish_draft(item, client=client, settings=settings, review_dir=review_dir)
    assert again == first and len(session.requests) == n
    assert run_all(item.parent, client=client, settings=settings, review_dir=review_dir) == []


def test_resume_after_failure_mid_upload(item, session, settings, review_dir):
    session.fail("uploadListingImage", 400, after=1)  # second photo fails
    with pytest.raises(EtsyAPIError):
        publish_draft(item, client=FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session),
                      settings=settings, review_dir=review_dir)
    partial = read_json(item / "publish.json")
    assert partial["listing_id"] and len(partial["image_ids"]) == 1 and partial["file_ids"] == []
    assert partial["complete"] is False and read_manifest(item)["status"] == "listed"

    # a new process: fresh client, same Etsy
    session.requests.clear()
    rec = publish_draft(item, client=FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session),
                        settings=settings, review_dir=review_dir)
    assert "createDraftListing" not in ops(session)
    assert ops(session)[-4:] == ["getShop", "getListing", "uploadListingImage", "uploadListingFile"]
    assert session.calls("uploadListingImage")[0]["data"]["rank"] == "2"
    assert rec["listing_id"] == partial["listing_id"] and rec["complete"] and len(rec["image_ids"]) == 2
    assert len([x for x in session.listings.values() if x["state"] == "draft"]) == 1
    assert read_manifest(item)["status"] == "drafted"


def test_resume_after_crash_right_after_create(item, session, settings, review_dir):
    session.crash("uploadListingImage", RuntimeError("process killed"))
    with pytest.raises(RuntimeError):
        publish_draft(item, client=FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session),
                      settings=settings, review_dir=review_dir)
    assert read_json(item / "publish.json")["image_ids"] == []
    run_all(item.parent, client=FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session),
            settings=settings, review_dir=review_dir)
    assert len(session.calls("createDraftListing")) == 1
    assert read_json(item / "publish.json")["complete"] is True


def test_resume_refuses_deleted_or_activated_listing(item, session, settings, review_dir):
    session.fail("uploadListingFile", 400)
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)
    with pytest.raises(EtsyAPIError):
        publish_draft(item, client=c, settings=settings, review_dir=review_dir)
    lid = read_json(item / "publish.json")["listing_id"]

    session.listings[lid]["state"] = "active"
    with pytest.raises(PublishError, match="only adds to drafts"):
        publish_draft(item, client=c, settings=settings, review_dir=review_dir)
    session.fail("getListing", 404)
    with pytest.raises(PublishError, match="discard"):
        publish_draft(item, client=c, settings=settings, review_dir=review_dir)
    assert len(session.calls("uploadListingFile")) == 1  # nothing uploaded to the live listing


def test_record_for_another_shop_is_not_resumed(item, client, settings, review_dir):
    write_json(item / "publish.json", {"schema": "publish/v1", "item_id": ITEM_ID, "shop_id": "main",
                                       "etsy_shop_id": 1111, "listing_id": 42, "state": "draft",
                                       "image_ids": [], "file_ids": [], "dry_run": False, "complete": False})
    with pytest.raises(PublishError, match="configured shop"):
        publish_draft(item, client=client, settings=settings, review_dir=review_dir)


# --- gates ----------------------------------------------------------------------------

def _gate(item, client, session, settings, review_dir, match):
    with pytest.raises(PublishGateError, match=match):
        publish_draft(item, client=client, settings=settings, review_dir=review_dir)
    assert session.requests == []
    assert not (item / "publish.json").exists()


@pytest.mark.parametrize("kwargs, match", [
    ({"qa": {"passed": False}}, "QA did not pass"),
    ({"manifest": {"status": "qa_passed"}}, "status is 'qa_passed'"),
    ({"listing": {"title": "Disney Farm Coloring Pages"}}, "blocked trademark terms: Disney"),
    ({"listing": {"compliance": {"blocked": ["Bluey"], "flagged": []}}}, "Bluey"),
    ({"listing": {"description": "No disclosure here."}}, "ai_disclosure"),
    ({"listing": {"taxonomy_id": None}}, "no taxonomy_id"),
    ({"listing": {"title": "Farm Coloring Pages $4 🐮"}}, "characters Etsy rejects"),
    ({"listing": {"title": "Farm: Coloring: Pages"}}, "':' more than once"),
    ({"listing": {"tags": ["farm, coloring"]}}, "tag 'farm, coloring'"),
    ({"listing": {"tags": ["a very long tag over twenty"]}}, "1-20 characters"),
    ({"listing": {"type": "physical"}}, "digital downloads"),
    ({"listing": {"who_made": "robot"}}, "not Etsy values"),
    ({"listing": {"price_usd": 0}}, "Etsy's range"),
    ({"listing": {"price_usd": 0.19}}, "Etsy's range"),
    ({"listing": {"price_usd": float("nan")}}, "Etsy's range"),
    ({"listing": {"quantity": 1000}}, "quantity 1000 must be 1-999"),
    ({"listing": {"quantity": 0}}, "quantity 0"),
    ({"listing": {"writer": {"kind": "template", "dry_run": True}}}, "template text"),  # listing stage format
    ({"listing": {"file_paths": ["files/disney-pages.pdf"]}}, "blocked trademark terms: Disney"),
    ({"listing": {"shop_id": "nope"}, "manifest": {"shop_id": "nope"}}, "unknown shop id 'nope'"),
    ({"listing": {"file_paths": ["../../secret.pdf"]}}, "outside the item folder"),
    ({"listing": {"image_paths": []}}, "listing photos"),
    ({"n_files": 6}, "1-5 digital files"),
    ({"dry_run_item": True}, "dry-run mode"),
    ({"listing": {"dry_run": True}}, "template text"),
])
def test_gates_block_before_any_request(out_root, settings, client, session, review_dir, kwargs, match):
    d = make_item(out_root, settings, **kwargs)
    _gate(d, client, session, settings, review_dir, match)


def test_missing_listing_json(item, client, session, settings, review_dir):
    (item / "listing.json").unlink()
    _gate(item, client, session, settings, review_dir, "listing.json missing")


def test_file_size_limit(item, client, session, settings, review_dir):
    s = copy.deepcopy(settings)
    s["publisher"]["max_file_bytes"] = 100
    with pytest.raises(PublishGateError, match="Etsy allows 100"):
        publish_draft(item, client=client, settings=s, review_dir=review_dir)


@pytest.mark.parametrize("kwargs", [
    {"manifest": {"needs_review": True}},
    {"listing": {"needs_review": True}},
    {"qa": {"needs_review": True}},
    {"listing": {"title": "Peter Pan Style Farm Coloring Pages"}},  # ?flag term in the blocklist
])
def test_needs_review_requires_approval(out_root, settings, client, session, review_dir, kwargs):
    d = make_item(out_root, settings, **kwargs)
    _gate(d, client, session, settings, review_dir, "needs review .*no entry")
    write_json(review_dir / f"{ITEM_ID}.json", {"item_id": ITEM_ID, "status": "pending"})
    _gate(d, client, session, settings, review_dir, "needs review .*pending")
    write_json(review_dir / f"{ITEM_ID}.json", {"item_id": ITEM_ID, "status": "approved"})
    assert publish_draft(d, client=client, settings=settings, review_dir=review_dir)["complete"]


def test_rejected_item_is_never_drafted(item, client, session, settings, review_dir):
    write_json(review_dir / f"{ITEM_ID}.json", {"item_id": ITEM_ID, "status": "rejected"})
    _gate(item, client, session, settings, review_dir, "rejected")


def test_taxonomy_from_settings_and_extra_fields(out_root, settings, client, session, review_dir):
    d = make_item(out_root, settings, listing={"taxonomy_id": None})
    s = copy.deepcopy(settings)
    s.setdefault("listing", {})["taxonomy_ids"] = {"coloring_pages": 6789}
    s["publisher"]["extra_fields"] = {"should_auto_renew": False, "title": "ignored", "shop_section_id": 5}
    plan = check_ready(d, settings=s, review_dir=review_dir)
    assert plan.fields["taxonomy_id"] == "6789" and plan.fields["should_auto_renew"] == "false"
    assert plan.fields["shop_section_id"] == "5" and plan.fields["title"].startswith("Farm Animal")
    s["publisher"]["extra_fields"] = {"state": "active"}  # can never sneak past the guard
    with pytest.raises(publisher.SafetyViolation):
        publish_draft(d, client=client, settings=s, review_dir=review_dir)
    assert "createDraftListing" not in ops(session)


def test_non_usd_shop_is_refused_before_create(item, settings, review_dir):
    session = FakeEtsySession(SHOP_ID, currency="EUR")
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)
    with pytest.raises(PublishGateError, match="currency is EUR"):
        publish_draft(item, client=c, settings=settings, review_dir=review_dir)
    assert "createDraftListing" not in ops(session)


def test_default_review_dir_is_next_to_items(item, client, settings):
    (item.parent / "review").mkdir()
    write_json(item.parent / "review" / f"{ITEM_ID}.json", {"status": "rejected"})
    with pytest.raises(PublishGateError, match="rejected"):
        publish_draft(item, client=client, settings=settings)


# --- dry runs -------------------------------------------------------------------------

def test_dry_run_end_to_end_on_dry_item(out_root, settings, review_dir):
    d = make_item(out_root, settings, dry_run_item=True)
    results = run_all(out_root, dry_run=True, settings=settings, review_dir=review_dir)
    assert results and results[0]["ok"] and "(dry run)" in results[0]["detail"]
    rec = read_json(d / "publish.json")
    assert rec["dry_run"] is True and rec["state"] == "draft" and rec["complete"]
    assert len(rec["image_ids"]) == 2 and len(rec["file_ids"]) == 1
    assert read_manifest(d)["status"] == "drafted"
    assert run_all(out_root, dry_run=True, settings=settings, review_dir=review_dir) == []


def test_preview_of_real_item_reports_a_dry_run_listing(out_root, settings, review_dir):
    d = make_item(out_root, settings, listing={"writer": {"kind": "template", "dry_run": True}})
    with pytest.raises(PublishGateError, match="template text"):
        publish_draft(d, dry_run=True, settings=settings, review_dir=review_dir)


def test_dry_run_on_real_item_previews_and_writes_nothing(item, settings, review_dir):
    before = read_manifest(item)
    rec = publish_draft(item, dry_run=True, settings=settings, review_dir=review_dir)
    assert rec["dry_run"] is True and rec["complete"] and len(rec["image_ids"]) == 2
    assert not (item / "publish.json").exists() and read_manifest(item) == before


def test_dry_run_record_never_blocks_a_real_draft(item, client, session, settings, review_dir):
    write_json(item / "publish.json", {"schema": "publish/v1", "item_id": ITEM_ID, "listing_id": 1,
                                       "dry_run": True, "complete": True})
    rec = publish_draft(item, client=client, settings=settings, review_dir=review_dir)
    assert rec["dry_run"] is False and "createDraftListing" in ops(session)


@pytest.fixture
def settings_file(settings, tmp_path) -> str:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    return str(path)


def test_cli_publish_dry_run(out_root, settings, settings_file, review_dir, capsys):
    make_item(out_root, settings, dry_run_item=True)
    base = ["--settings", settings_file, "publish", "--dry-run", "--out", str(out_root)]
    assert cli.main(base + ["--review-dir", str(review_dir)]) == 0
    assert f"ok   {ITEM_ID}" in capsys.readouterr().out
    assert cli.main(base + ["--item", ITEM_ID]) == 0
    assert cli.main(base + ["--item", "../etc"]) == 1


def test_cli_publish_reports_gate_failures(out_root, settings, settings_file, capsys):
    make_item(out_root, settings, dry_run_item=True, qa={"passed": False})
    assert cli.main(["--settings", settings_file, "publish", "--dry-run", "--out", str(out_root)]) == 1
    assert "QA did not pass" in capsys.readouterr().out


def test_placeholder_shop_name_blocks_real_drafts(out_root, settings, client, session, review_dir):
    s = copy.deepcopy(settings)
    s["shops"][0]["name"] = "YOUR_SHOP_NAME"
    d = make_item(out_root, s)
    with pytest.raises(PublishGateError, match="placeholder"):
        publish_draft(d, client=client, settings=s, review_dir=review_dir)
    with pytest.raises(PublishGateError, match="placeholder"):  # a preview tells the truth too
        publish_draft(d, dry_run=True, settings=s, review_dir=review_dir)
    assert session.requests == []


def test_simulated_item_without_taxonomy_still_runs(out_root, settings, review_dir):
    s = copy.deepcopy(settings)
    s["shops"][0]["name"] = "YOUR_SHOP_NAME"
    d = make_item(out_root, s, dry_run_item=True, listing={"taxonomy_id": None})
    client = FakeEtsyClient(settings=s)
    rec = publish_draft(d, client=client, dry_run=True, settings=s, review_dir=review_dir)
    assert rec["complete"] and "fake id" in rec["warnings"][0]
    assert client.session.calls("createDraftListing")[0]["data"]["taxonomy_id"] == "90000002"
    real = make_item(out_root, settings, "real-item", listing={"taxonomy_id": None})
    with pytest.raises(PublishGateError, match="no taxonomy_id"):
        publish_draft(real, dry_run=True, settings=settings, review_dir=review_dir)


# --- run_all and discard ----------------------------------------------------------------

def test_run_all_records_failures_and_continues(out_root, settings, client, session, review_dir):
    make_item(out_root, settings, "a-item", manifest={"needs_review": True})
    make_item(out_root, settings, "b-item")
    make_item(out_root, settings, "c-item", manifest={"status": "qa_failed"})
    results = run_all(out_root, client=client, settings=settings, review_dir=review_dir)
    assert [(r["item_id"], r["ok"]) for r in results] == [("a-item", False), ("b-item", True)]
    assert "needs review" in results[0]["detail"]
    assert publisher.owned_listing_ids(out_root) == {results[1]["listing_id"]}


def test_discard_deletes_our_draft_and_allows_a_fresh_one(item, client, session, settings, review_dir):
    first = publish_draft(item, client=client, settings=settings, review_dir=review_dir)
    out = discard_draft(item, client=client, settings=settings)
    assert ops(session)[-2:] == ["getListing", "deleteListing"]
    assert not (item / "publish.json").exists()
    assert json.loads((item / out["archived"]).read_text())["listing_id"] == first["listing_id"]
    assert read_manifest(item)["status"] == "listed"
    second = publish_draft(item, client=client, settings=settings, review_dir=review_dir)
    assert second["listing_id"] != first["listing_id"]


def test_discard_forgets_a_draft_already_deleted_in_etsy(item, client, session, settings, review_dir):
    rec = publish_draft(item, client=client, settings=settings, review_dir=review_dir)
    del session.listings[rec["listing_id"]]  # the user deleted it in Etsy
    out = discard_draft(item, client=client, settings=settings)
    # getListing 404 is double-checked against our drafts before the record is dropped
    assert ops(session)[-2:] == ["getListing", "getListingsByShop"]
    assert session.calls("getListingsByShop")[-1]["params"]["state"] == "draft"
    assert out["deleted"] is False and not (item / "publish.json").exists()


def test_discard_deletes_a_draft_getlisting_does_not_show(item, client, session, settings, review_dir):
    """If getListing hides drafts (undocumented), the draft is found via our drafts list."""
    rec = publish_draft(item, client=client, settings=settings, review_dir=review_dir)
    session.fail("getListing", 404)
    out = discard_draft(item, client=client, settings=settings)
    assert out["deleted"] is True and ops(session)[-1] == "deleteListing"
    assert rec["listing_id"] not in session.listings


def test_discard_never_deletes_an_activated_listing(item, client, session, settings, review_dir):
    rec = publish_draft(item, client=client, settings=settings, review_dir=review_dir)
    session.listings[rec["listing_id"]]["state"] = "active"
    with pytest.raises(publisher.SafetyViolation):
        discard_draft(item, client=client, settings=settings)
    assert (item / "publish.json").exists() and "deleteListing" not in ops(session)


# --- review findings: resume safety, shops, robustness ----------------------------------

def test_resume_refuses_when_listing_changed_since_create(item, session, settings, review_dir):
    session.fail("uploadListingImage", 400, after=1)
    with pytest.raises(EtsyAPIError):
        publish_draft(item, client=FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session),
                      settings=settings, review_dir=review_dir)
    partial = read_json(item / "publish.json")
    assert len(partial["plan_digest"]) == 64
    lst = read_json(item / "listing.json")
    write_json(item / "listing.json", {**lst, "title": "Barn Animal Coloring Pages for Kids"})
    n = len(session.requests)
    with pytest.raises(PublishError, match="changed since draft .* discard"):
        publish_draft(item, client=FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session),
                      settings=settings, review_dir=review_dir)
    assert [r["operation"] for r in session.requests[n:]] == ["getMe", "getShop"]  # no uploads
    write_json(item / "listing.json", lst)  # restored: resumes
    (item / "mockups" / "mockup-02.jpg").write_bytes((item / "mockups" / "mockup-01.jpg").read_bytes())
    with pytest.raises(PublishError, match="changed since draft"):  # a photo's bytes changed
        publish_draft(item, client=FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session),
                      settings=settings, review_dir=review_dir)


def test_resume_finds_a_draft_getlisting_does_not_show(item, session, settings, review_dir):
    session.fail("uploadListingFile", 400)
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)
    with pytest.raises(EtsyAPIError):
        publish_draft(item, client=c, settings=settings, review_dir=review_dir)
    session.fail("getListing", 404)
    rec = publish_draft(item, client=c, settings=settings, review_dir=review_dir)
    assert rec["complete"] and len(session.calls("createDraftListing")) == 1
    assert "getListingsByShop" in ops(session)


def test_file_upload_is_not_retried_and_says_check_etsy(item, session, settings, review_dir):
    session.fail("uploadListingFile", 503)
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)
    with pytest.raises(PublishError, match="may have stored it anyway"):
        publish_draft(item, client=c, settings=settings, review_dir=review_dir)
    assert len(session.calls("uploadListingFile")) == 1  # a resend could attach it twice
    assert read_json(item / "publish.json")["file_ids"] == []


def test_ambiguous_create_failure_warns_about_duplicates(item, session, settings, review_dir):
    session.fail("createDraftListing", 502)
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)
    with pytest.raises(PublishError, match="may have created the draft anyway"):
        publish_draft(item, client=c, settings=settings, review_dir=review_dir)
    assert not (item / "publish.json").exists()


def test_physical_listing_type_is_recorded_as_a_warning(item, session, settings, review_dir):
    class PhysicalSession(FakeEtsySession):
        def _answer(self, op, groups, params, data):
            resp = super()._answer(op, groups, params, data)
            if op == "createDraftListing":
                resp._body["listing_type"] = "physical"
            return resp
    s = PhysicalSession(SHOP_ID)
    rec = publish_draft(item, client=FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=s),
                        settings=settings, review_dir=review_dir)
    assert rec["listing_type"] == "physical" and "not 'download'" in rec["warnings"][0]
    assert s.calls("createDraftListing")[0]["data"]["type"] == "download"


def _second_shop_listing() -> dict:
    return {"shop_id": "second",
            "description": "Twenty farm animal coloring pages.\n\n" + load_ai_disclosure("Second Shop")}


def test_client_for_another_shop_is_refused(out_root, settings, session, review_dir):
    s = copy.deepcopy(settings)
    s["shops"].append({**s["shops"][0], "id": "second", "name": "Second Shop",
                       "etsy_shop_id_env": "ETSY_SHOP_ID_SECOND"})
    d = make_item(out_root, s, listing=_second_shop_listing(), manifest={"shop_id": "second"})
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=s, session=session)  # first shop's client
    with pytest.raises(PublishGateError, match="belongs to shop 'second'"):
        publish_draft(d, client=c, settings=s, review_dir=review_dir)
    assert session.requests == []


def test_run_all_uses_one_client_per_shop(out_root, settings, review_dir, monkeypatch):
    import publisher.publish as pub

    s = copy.deepcopy(settings)
    s["shops"].append({**s["shops"][0], "id": "second", "name": "Second Shop"})
    make_item(out_root, s, "a-item")
    make_item(out_root, s, "b-item", listing=_second_shop_listing(), manifest={"shop_id": "second"})
    make_item(out_root, s, "c-item")
    sessions = {"main": FakeEtsySession(SHOP_ID), "second": FakeEtsySession(SHOP_ID + 1)}
    made = []

    def fake_client(*, settings, shop_key=None):
        made.append(shop_key)
        return FakeEtsyClient(shop_id=sessions[shop_key].shop_id, settings=settings,
                              session=sessions[shop_key], shop_key=shop_key)
    monkeypatch.setattr(pub, "EtsyClient", fake_client)
    results = run_all(out_root, settings=s, review_dir=review_dir)
    assert [r["ok"] for r in results] == [True, True, True] and made == ["main", "second"]
    assert len(sessions["main"].calls("createDraftListing")) == 2
    assert len(sessions["second"].calls("createDraftListing")) == 1
    assert read_json(out_root / "b-item" / "publish.json")["etsy_shop_id"] == SHOP_ID + 1


def test_broken_item_files_fail_that_item_only(out_root, settings, client, session, review_dir):
    a = make_item(out_root, settings, "a-item")
    b = make_item(out_root, settings, "b-item")
    make_item(out_root, settings, "c-item")
    make_item(out_root, settings, "d-item")
    (a / "listing.json").write_text("{not json", encoding="utf-8")
    (b / "publish.json").write_text("[]", encoding="utf-8")
    write_json(review_dir / "c-item.json", ["not", "an", "object"])
    results = run_all(out_root, client=client, settings=settings, review_dir=review_dir)
    by_id = {r["item_id"]: r for r in results}
    assert not by_id["a-item"]["ok"] and "listing.json is unreadable" in by_id["a-item"]["detail"]
    assert not by_id["b-item"]["ok"] and "publish.json is not a JSON object" in by_id["b-item"]["detail"]
    assert not by_id["c-item"]["ok"] and "not a JSON object" in by_id["c-item"]["detail"]
    assert by_id["d-item"]["ok"] and len(session.calls("createDraftListing")) == 1


def test_review_entry_needs_review_counts(item, client, session, settings, review_dir):
    write_json(review_dir / f"{ITEM_ID}.json", {"item_id": ITEM_ID, "status": "pending",
                                                "needs_review": True, "reasons": ["listing review term"]})
    _gate(item, client, session, settings, review_dir, "needs review .*pending")


def test_bad_item_id_in_manifest_is_refused(out_root, settings, client, session, review_dir):
    d = make_item(out_root, settings, "x-item", manifest={"item_id": "../../escape"})
    _gate(d, client, session, settings, review_dir, "invalid item id")


def test_dry_run_preview_of_a_real_half_done_draft(item, session, settings, review_dir):
    session.fail("uploadListingImage", 400, after=1)
    with pytest.raises(EtsyAPIError):
        publish_draft(item, client=FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session),
                      settings=settings, review_dir=review_dir)
    before = (item / "publish.json").read_text()
    rec = publish_draft(item, dry_run=True, settings=settings, review_dir=review_dir)
    assert rec["preview"] and rec["complete"] and len(rec["image_ids"]) == 2
    assert (item / "publish.json").read_text() == before and read_manifest(item)["status"] == "listed"


def test_dry_run_resumes_a_simulated_half_done_record(out_root, settings, review_dir):
    d = make_item(out_root, settings, dry_run_item=True)
    s = FakeEtsySession(10000001)
    s.fail("uploadListingFile", 400)
    with pytest.raises(EtsyAPIError):
        publish_draft(d, client=FakeEtsyClient(settings=settings, session=s), dry_run=True,
                      settings=settings, review_dir=review_dir)
    assert read_json(d / "publish.json")["complete"] is False
    results = run_all(out_root, dry_run=True, settings=settings, review_dir=review_dir)  # new fake
    assert results[0]["ok"] and read_json(d / "publish.json")["complete"] is True


def test_cli_discard(item, settings, settings_file, out_root, capsys):
    write_json(item / "publish.json", {"schema": "publish/v1", "item_id": ITEM_ID, "shop_id": "main",
                                       "listing_id": 900000009, "dry_run": True, "complete": True})
    assert cli.main(["--settings", settings_file, "discard", "--item", ITEM_ID, "--out", str(out_root)]) == 0
    assert "Forgot dry-run draft 900000009" in capsys.readouterr().out
    assert not (item / "publish.json").exists()
    assert cli.main(["--settings", settings_file, "discard", "--item", ITEM_ID, "--out", str(out_root)]) == 1
