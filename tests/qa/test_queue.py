"""Review-queue safety on reruns, cleanup of failed items, and the CLI's review folder."""
import copy

import pytest
from PIL import Image

import qa
from core.items import clear_downstream, read_manifest, set_status, write_json, write_manifest
from qa import mockups, review
from qa.__main__ import main

from .conftest import by_name, coloring_item


def rerun(d, settings, rdir, **manifest_changes):
    """Change the manifest the way a regenerated item would, then QA it again."""
    m = read_manifest(d)
    m.update(manifest_changes)
    m["status"] = "generated"
    write_manifest(d, m)
    return qa.run(d, settings=settings, review_dir=rdir, mockups=False)


def test_new_review_reason_reopens_an_approval(tmp_path, settings):
    """An approval covers the reasons the person saw. A rerun that finds a new one (here a
    review-listed character in the keywords) must not ride on the old approval."""
    rdir = tmp_path / "review"
    d = coloring_item(tmp_path / "out")
    qa.run(d, settings=settings, review_dir=rdir, mockups=False)
    review.approve(d.name, rdir, note="clean")

    report = rerun(d, settings, rdir, keywords=["farm animal coloring pages", "Peter Rabbit garden"])
    assert report["passed"] and report["needs_review"]
    entry = review.read_entry(d.name, rdir)
    assert entry["status"] == "pending" and entry["previous_status"] == "approved"
    assert entry["needs_review"] and any("Peter Rabbit" in r for r in entry["reasons"])

    review.approve(d.name, rdir, note="original character, fine")
    rerun(d, settings, rdir)  # same reasons as approved: the decision stands
    assert review.read_entry(d.name, rdir)["status"] == "approved"


def test_rejection_survives_new_reasons(tmp_path, settings):
    rdir = tmp_path / "review"
    d = coloring_item(tmp_path / "out")
    qa.run(d, settings=settings, review_dir=rdir, mockups=False)
    review.reject(d.name, rdir, reason="off-brand")
    rerun(d, settings, rdir, keywords=["Peter Rabbit garden"])
    assert review.read_entry(d.name, rdir)["status"] == "rejected"


def test_rerun_keeps_reasons_other_stages_added(tmp_path, settings):
    """The listing stage appends its review terms to the entry (listing/review.py). A QA
    rerun replaces only QA's own reasons."""
    rdir = tmp_path / "review"
    d = coloring_item(tmp_path / "out", line_pt=0.5)  # thin lines: a QA warning
    qa.run(d, settings=settings, review_dir=rdir, mockups=False)
    entry = review.read_entry(d.name, rdir)
    assert any(r.startswith("line_weight:") for r in entry["qa_reasons"])
    listing_reason = "listing copy has review terms: Peter Rabbit"
    entry["reasons"].append(listing_reason)
    write_json(rdir / f"{d.name}.json", entry)

    coloring_item(tmp_path / "out", line_pt=3.0)  # regenerated with good lines
    report = qa.run(d, settings=settings, review_dir=rdir, mockups=False)
    assert report["passed"] and not report["needs_review"]
    entry = review.read_entry(d.name, rdir)
    assert entry["reasons"] == [listing_reason] and entry["qa_reasons"] == []
    assert entry["needs_review"] and entry["status"] == "pending"


@pytest.mark.parametrize("content", ["{not json", "[]"])
def test_unreadable_review_entry_is_never_overwritten(tmp_path, settings, content):
    """It may hold the user's decision; QA logs it and leaves it for a person to fix."""
    rdir = tmp_path / "review"
    rdir.mkdir()
    d = coloring_item(tmp_path / "out")
    (rdir / f"{d.name}.json").write_text(content, encoding="utf-8")
    report = qa.run(d, settings=settings, review_dir=rdir, mockups=False)
    assert report["passed"]
    assert (rdir / f"{d.name}.json").read_text(encoding="utf-8") == content
    with pytest.raises(ValueError):
        review.approve(d.name, rdir)
    assert review.list_entries(rdir) == []


def test_broken_manifest_drops_queue_entry_and_photos(tmp_path, settings):
    rdir = tmp_path / "review"
    d = coloring_item(tmp_path / "out")
    assert qa.run(d, settings=settings, review_dir=rdir)["mockups"]
    (d / "manifest.json").write_text("{broken", encoding="utf-8")

    report = qa.run(d, settings=settings, review_dir=rdir)
    assert not report["passed"] and report["checks"][0]["name"] == "manifest_readable"
    assert not list((d / "mockups").glob("*.jpg"))
    assert review.read_entry(d.name, rdir) is None


def test_crash_while_building_photos_leaves_none_behind(tmp_path, settings, monkeypatch):
    d = coloring_item(tmp_path / "out")

    def half_built(item, blocklist):
        (item.dir / "mockups").mkdir(exist_ok=True)
        Image.new("RGB", (8, 8)).save(item.dir / "mockups/mockup-01.jpg")
        raise OSError("disk full")

    monkeypatch.setattr(mockups, "build", half_built)
    report = qa.run(d, settings=settings, review_dir=tmp_path / "review")
    assert not report["passed"] and report["mockups"] == []
    assert "check crashed: OSError" in by_name(report["checks"], "mockups")[0]["detail"]
    assert not list((d / "mockups").glob("*.jpg"))
    assert read_manifest(d)["status"] == "qa_failed"


def test_recheck_of_a_listed_item_keeps_its_status(tmp_path, settings):
    d = coloring_item(tmp_path / "out")
    set_status(d, "listed")
    report = qa.run(d, settings=settings, review_dir=tmp_path / "review", mockups=False)
    assert report["passed"] and (d / "qa_report.json").exists()
    assert read_manifest(d)["status"] == "listed"


def test_cli_review_commands_find_the_entries_qa_wrote(tmp_path, settings, monkeypatch, capsys):
    """With --out pointing at the configured output dir, `qa` queues into settings
    paths.review_dir, so `qa review` must look there too (not in <out>/review)."""
    s = copy.deepcopy(settings)
    out, queue = tmp_path / "out", tmp_path / "queue"
    s["paths"] = {"output_dir": str(out), "review_dir": str(queue)}
    monkeypatch.setattr("qa.__main__.load_settings", lambda path=None: s)
    d = coloring_item(out)

    assert main(["--out", str(out), "--item", d.name, "--no-mockups"]) == 0
    assert (queue / f"{d.name}.json").exists() and not (out / "review").exists()
    assert main(["review", "approve", d.name, "--out", str(out)]) == 0
    assert review.read_entry(d.name, queue)["status"] == "approved"
    capsys.readouterr()
    assert main(["review", "list"]) == 0
    assert d.name in capsys.readouterr().out


def test_regenerated_content_reopens_an_approval(tmp_path, settings):
    """An approval covers the files and listing photos the person looked at. When the item is
    regenerated with new art but QA finds no new reason (the flag came from the listing copy),
    the approval must not carry over to pictures nobody reviewed."""
    rdir = tmp_path / "review"
    d = coloring_item(tmp_path / "out")
    first = qa.run(d, settings=settings, review_dir=rdir)
    entry = review.read_entry(d.name, rdir)
    entry["reasons"].append("listing copy has review terms: Wicked")  # what listing/review.py adds
    entry["needs_review"] = True
    write_json(rdir / f"{d.name}.json", entry)
    review.approve(d.name, rdir, note="reviewed")

    again = qa.run(d, settings=settings, review_dir=rdir)  # plain recheck: same content
    assert again["content_digest"] == first["content_digest"]
    assert review.read_entry(d.name, rdir)["status"] == "approved"

    clear_downstream(d)
    coloring_item(tmp_path / "out", line_pt=4.0)  # the image model returned different art
    report = qa.run(d, settings=settings, review_dir=rdir)
    assert report["passed"] and not report["needs_review"]
    assert report["content_digest"] != first["content_digest"]
    entry = review.read_entry(d.name, rdir)
    assert entry["status"] == "pending" and entry["previous_status"] == "approved" and entry["reopened"]
    assert entry["needs_review"] and "listing copy has review terms: Wicked" in entry["reasons"]
    assert entry["mockups"] == report["mockups"] and entry["content_digest"] == report["content_digest"]

    review.approve(d.name, rdir, note="new art is fine")
    qa.run(d, settings=settings, review_dir=rdir)
    assert review.read_entry(d.name, rdir)["status"] == "approved"


def test_approval_without_a_content_digest_is_reopened(tmp_path, settings):
    """An approval that does not record what it covered (written before digests, or by hand)
    cannot be matched to the current files, so QA asks again."""
    rdir = tmp_path / "review"
    d = coloring_item(tmp_path / "out")
    write_json(rdir / f"{d.name}.json", {"item_id": d.name, "niche": "coloring_pages", "status": "approved",
                                         "needs_review": True, "reasons": [], "mockups": []})
    qa.run(d, settings=settings, review_dir=rdir, mockups=False)
    entry = review.read_entry(d.name, rdir)
    assert entry["status"] == "pending" and entry["previous_status"] == "approved"


def test_cli_exits_1_when_an_item_fails(tmp_path, settings, monkeypatch, capsys):
    """Like the listing and publisher CLIs, so CI shows a failed item."""
    monkeypatch.setattr("qa.__main__.load_settings", lambda path=None: settings)
    out = tmp_path / "out"
    good = coloring_item(out, "item-a")
    bad = coloring_item(out, "item-b")
    pdf = bad / "files/farm-animals-us_letter.pdf"
    pdf.write_bytes(pdf.read_bytes()[:200])  # truncated download
    assert main(["--out", str(out), "--item", good.name, "--no-mockups"]) == 0
    assert main(["--out", str(out), "--item", bad.name, "--no-mockups"]) == 1
    assert main(["--out", str(out), "--no-mockups"]) == 1  # run_all
    assert "FAIL  item-b" in capsys.readouterr().out
