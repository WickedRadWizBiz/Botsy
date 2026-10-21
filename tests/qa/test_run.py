"""qa.run / run_all end to end: report, status, mockups, review queue, CLI."""
import copy
import json
from pathlib import Path

import pytest
from PIL import Image

import qa
from core.compliance import Blocklist
from core.items import read_json, read_manifest, set_status, write_manifest
from qa import mockups, review
from qa.__main__ import main

from .conftest import by_name, coloring_item, make_item, make_pdf, sticker_item


def with_orders(settings, tmp_path, monkeypatch):
    """D2: `qa review approve` is an order. Its order files, control state and journal go into
    tmp_path, and Discord posts are captured: returns (settings, posts)."""
    from service import notify

    s = copy.deepcopy(settings)
    s["control"] = {**(s.get("control") or {}), "orders_dir": str(tmp_path / "data" / "orders"),
                    "state_file": str(tmp_path / "data" / "control" / "state.json"),
                    "limits_path": str(tmp_path / "limits.yaml"), "limits_require_root": False}
    s["service"] = {**(s.get("service") or {}), "events_file": str(tmp_path / "data" / "events.jsonl")}
    posts = []
    monkeypatch.setattr(notify, "send", lambda text, **kw: posts.append((text, kw.get("urgent"))) or True)
    return s, posts


def journal(s):
    p = Path(s["service"]["events_file"])
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_clean_coloring_item_passes(tmp_path, settings):
    d = coloring_item(tmp_path / "out")
    report = qa.run(d, settings=settings, review_dir=tmp_path / "review")

    assert report["passed"] and not report["needs_review"], [c for c in report["checks"] if not c["passed"]]
    on_disk = read_json(d / "qa_report.json")
    assert on_disk == report
    assert set(report) >= {"schema", "item_id", "checked", "passed", "needs_review", "checks", "mockups"}
    assert report["schema"] == "qa/v1" and report["item_id"] == d.name
    for c in report["checks"]:
        assert set(c) == {"name", "passed", "severity", "detail"} and c["severity"] in ("error", "warning")
    assert read_manifest(d)["status"] == "qa_passed"

    assert report["mockups"] == [f"mockups/mockup-{i:02d}.jpg" for i in range(1, 6)]
    for rel in report["mockups"]:
        with Image.open(d / rel) as img:
            assert img.format == "JPEG" and img.size == (800, 600)

    entry = read_json(tmp_path / "review" / f"{d.name}.json")
    assert entry == {"item_id": d.name, "niche": "coloring_pages", "status": "pending", "needs_review": False,
                     "reasons": [], "qa_reasons": [], "mockups": report["mockups"], "queued": report["checked"],
                     "content_digest": report["content_digest"]}
    assert len(report["content_digest"]) == 64


@pytest.mark.slow
def test_default_mockup_size_is_etsy_ready(tmp_path, base_settings):
    d = sticker_item(tmp_path / "out")
    report = qa.run(d, settings=base_settings, review_dir=tmp_path / "review")
    assert report["passed"], [c for c in report["checks"] if not c["passed"]]
    assert len(report["mockups"]) == 5
    for rel in report["mockups"]:
        with Image.open(d / rel) as img:
            assert img.size == (2700, 2025) and min(img.size) >= 2000


def test_failing_item_gets_no_mockups_or_review_entry(tmp_path, settings):
    d = coloring_item(tmp_path / "out")
    review_dir = tmp_path / "review"
    assert qa.run(d, settings=settings, review_dir=review_dir)["passed"]
    assert (review_dir / f"{d.name}.json").exists()

    make_pdf(d / "files/farm-animals-us_letter.pdf", pages=2, blank=(1,))  # break the item
    report = qa.run(d, settings=settings, review_dir=review_dir)
    assert not report["passed"] and report["mockups"] == []
    assert not list((d / "mockups").glob("*.jpg"))
    assert read_manifest(d)["status"] == "qa_failed"
    assert not (review_dir / f"{d.name}.json").exists()  # stale pending entry withdrawn


def test_blocked_theme_fails_and_flagged_theme_needs_review(tmp_path, settings):
    blocked = coloring_item(tmp_path / "out", "item-blocked", theme="Paw Patrol pups")
    flagged = coloring_item(tmp_path / "out", "item-flagged", theme="Peter Rabbit garden")
    rb = qa.run(blocked, settings=settings, review_dir=tmp_path / "review")
    rf = qa.run(flagged, settings=settings, review_dir=tmp_path / "review")

    assert not rb["passed"] and not by_name(rb["checks"], "compliance_blocklist")[0]["passed"]
    assert not (tmp_path / "review" / "item-blocked.json").exists()

    assert rf["passed"] and rf["needs_review"]
    entry = read_json(tmp_path / "review" / "item-flagged.json")
    assert entry["needs_review"] and any("Peter Rabbit" in r for r in entry["reasons"])
    # the flagged title went onto the mockups too, and was reported
    assert not by_name(rf["checks"], "mockup_review_terms")[0]["passed"]


def test_thin_lines_pass_with_review(tmp_path, settings):
    d = coloring_item(tmp_path / "out", line_pt=0.5)
    report = qa.run(d, settings=settings, review_dir=tmp_path / "review")
    assert report["passed"] and report["needs_review"]
    assert any(r.startswith("line_weight:") for r in report["reasons"])


def test_review_lifecycle_and_rerun_safety(tmp_path, settings):
    out, rdir = tmp_path / "out", tmp_path / "review"
    d = coloring_item(out)
    qa.run(d, settings=settings, review_dir=rdir)
    assert review.read_entry(d.name, rdir)["status"] == "pending"

    approved = review.approve(d.name, rdir, note="looks good")
    assert approved["status"] == "approved" and approved["decision_note"] == "looks good"
    before = (rdir / f"{d.name}.json").read_text()
    set_status(d, "generated")
    qa.run(d, settings=settings, review_dir=rdir)
    assert (rdir / f"{d.name}.json").read_text() == before  # decision kept on rerun

    with pytest.raises(ValueError):
        review.reject(d.name, rdir, reason="  ")
    rejected = review.reject(d.name, rdir, reason="cow looks off")
    assert rejected["status"] == "rejected"
    qa.run(d, settings=settings, review_dir=rdir)
    assert review.read_entry(d.name, rdir)["status"] == "rejected"

    make_pdf(d / "files/farm-animals-us_letter.pdf", pages=2, black=(0,))
    qa.run(d, settings=settings, review_dir=rdir)  # failing now: the decision still stays
    assert review.read_entry(d.name, rdir)["status"] == "rejected"

    assert [e["item_id"] for e in review.list_entries(rdir, "rejected")] == [d.name]
    assert review.list_entries(rdir, "pending") == []


def test_pending_entry_refreshed_on_rerun(tmp_path, settings):
    rdir = tmp_path / "review"
    d = coloring_item(tmp_path / "out")
    qa.run(d, settings=settings, review_dir=rdir)
    m = read_manifest(d)
    m["needs_review"] = True
    write_manifest(d, m)
    qa.run(d, settings=settings, review_dir=rdir)
    entry = review.read_entry(d.name, rdir)
    assert entry["status"] == "pending" and entry["needs_review"]
    assert any(r.startswith("generator_needs_review") for r in entry["reasons"])


def test_review_ids_cannot_escape(tmp_path):
    with pytest.raises(ValueError):
        review.entry_path("../outside", tmp_path)
    with pytest.raises(FileNotFoundError):
        review.approve("no-such-item", tmp_path)


def test_default_review_dir_stays_next_to_custom_out_root(tmp_path, settings):
    d = coloring_item(tmp_path / "out")
    qa.run(d, settings=settings, mockups=False)
    assert (tmp_path / "out" / "review" / f"{d.name}.json").exists()


def test_run_all_skips_listed_items_and_handles_broken_ones(tmp_path, settings):
    out = tmp_path / "out"
    ok = coloring_item(out, "item-a")
    done = coloring_item(out, "item-b")
    set_status(done, "drafted")
    broken = out / "item-c"
    broken.mkdir()
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")

    reports = qa.run_all(out, tmp_path / "review", settings=settings, mockups=False)
    by_id = {r["item_id"]: r for r in reports}
    assert set(by_id) == {"item-a", "item-c"}
    assert by_id["item-a"]["passed"]
    assert not by_id["item-c"]["passed"] and by_id["item-c"]["checks"][0]["name"] == "manifest_readable"
    assert read_manifest(done)["status"] == "drafted" and not (done / "qa_report.json").exists()
    assert read_manifest(ok)["status"] == "qa_passed"


def test_rerun_with_mockups_off_keeps_existing_photos(tmp_path, settings):
    d = coloring_item(tmp_path / "out")
    first = qa.run(d, settings=settings, review_dir=tmp_path / "review")
    again = qa.run(d, settings=settings, review_dir=tmp_path / "review", mockups=False)
    assert again["mockups"] == first["mockups"]


def test_mockup_text_goes_through_blocklist(tmp_path, settings):
    d = coloring_item(tmp_path / "out")
    item = make_item(d, settings, **{k: v for k, v in read_manifest(d).items() if k != "item_id"})
    paths, checks = mockups.build(item, Blocklist(["Instant"]))
    assert paths == [] and not by_name(checks, "mockup_text")[0]["passed"]
    paths, checks = mockups.build(item, Blocklist([], ["Up close"]))
    assert len(paths) == 5 and not by_name(checks, "mockup_review_terms")[0]["passed"]


def test_mockups_are_deterministic(tmp_path, settings):
    d = coloring_item(tmp_path / "out")
    first = qa.run(d, settings=settings, review_dir=tmp_path / "r")
    a = (d / first["mockups"][0]).read_bytes()
    qa.run(d, settings=settings, review_dir=tmp_path / "r")
    assert (d / first["mockups"][0]).read_bytes() == a


def test_mockups_render_pdf_when_no_previews(tmp_path, settings):
    d = coloring_item(tmp_path / "out")
    m = read_manifest(d)
    m["previews"] = []
    write_manifest(d, m)
    report = qa.run(d, settings=settings, review_dir=tmp_path / "review")
    assert report["passed"] and len(report["mockups"]) == 5


def test_text_helpers():
    assert mockups.title_case("farm animals") == "Farm Animals"
    assert mockups.content_count({"pack": {"activity_pages": 12}, "files": []}, 0) == 12
    assert mockups.content_count({"niche": "stickers", "files": []}, 7) == 7
    assert mockups.content_count({"files": [{"kind": "pdf", "pages": 20}]}, 0) == 20


def test_cli_run_and_review(tmp_path, settings, capsys, monkeypatch):
    settings, posts = with_orders(settings, tmp_path, monkeypatch)
    monkeypatch.setattr("qa.__main__.load_settings", lambda path=None: settings)
    out = tmp_path / "out"
    d = coloring_item(out)
    assert main(["--out", str(out), "--item", d.name, "--no-mockups"]) == 0
    assert f"PASS  {d.name}" in capsys.readouterr().out

    assert main(["review", "list", "--out", str(out)]) == 0
    assert d.name in capsys.readouterr().out
    assert main(["review", "approve", d.name, "--out", str(out), "--note", "ok"]) == 0
    printed = capsys.readouterr().out
    e = review.read_entry(d.name, out / "review")
    assert e["status"] == "approved" and "approved by user (order ra-" in e["decision_note"]
    # D2: an order, posted at once (urgent) with how to undo it, journaled and counted.
    assert "Undo: botsy service control undo ra-" in printed
    assert len(posts) == 1 and posts[0][1] is True and d.name in posts[0][0] and "Undo:" in posts[0][0]
    ev = [x for x in journal(settings) if x["kind"] == "order_approved"]
    assert ev and ev[-1]["by"] == "user" and ev[-1]["counted"] is True
    from service import orders

    assert orders.counts(settings)["level3_last_24h"] == 1
    assert main(["review", "approve", d.name, "--out", str(out)]) == 1        # not pending any more
    with pytest.raises(SystemExit):
        main(["review", "reject", d.name, "--out", str(out)])  # --reason is required
    assert main(["review", "reject", d.name, "--out", str(out), "--reason", "blurry"]) == 0
    assert json.loads((out / "review" / f"{d.name}.json").read_text())["status"] == "rejected"
    assert main(["review", "approve", "../etc", "--out", str(out)]) == 2
    assert main(["--out", str(out), "--item", "missing-item"]) == 2
    assert main(["--out", str(out), "--no-mockups", "--dry-run"]) == 0  # run_all


def test_mockup_quality_steps_down_for_etsy_upload_limit(tmp_path, settings):
    img = Image.effect_noise((400, 300), 80).convert("RGB")  # noise: JPEG cannot shrink it much
    full = mockups.save_jpeg(img, tmp_path / "a.jpg", 90, 0)
    capped = mockups.save_jpeg(img, tmp_path / "b.jpg", 90, 1)
    assert capped < full
    with Image.open(tmp_path / "b.jpg") as b:
        assert b.size == (400, 300)

    s = {**settings, "qa": {"mockups": {**settings["qa"]["mockups"], "max_bytes": 1}}}
    report = qa.run(coloring_item(tmp_path / "out"), settings=s, review_dir=tmp_path / "review")
    assert report["passed"] and report["needs_review"] and len(report["mockups"]) == 5
    r = by_name(report["checks"], "mockup_file_size")[0]
    assert r["severity"] == "warning" and "mockup-01.jpg" in r["detail"]
