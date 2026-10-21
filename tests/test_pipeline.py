"""Offline end-to-end run: research.demo exports -> briefs -> packs -> QA -> listing -> dry-run draft.

Everything runs for real except the network: the image niches use the fake image client,
the listing stage writes template copy, and the publisher talks to FakeEtsyClient. All
paths are in tmp_path, so nothing is written to output/ or data/.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

from core import items
from core.compliance import find_urls
from core.config import load_settings
from pipeline import run as prun
from research.demo import DEMO_NICHES, write_demo_inputs

DATE = "2026-10-15"
# Secret-looking values planted in the environment: none may reach any output file.
FAKE_SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-e2e-SECRET-0f3c9a1b7d",
    "GOOGLE_API_KEY": "AIzaE2E-SECRET-5b8e2c4d6f",
    "ETSY_API_KEYSTRING": "e2ekeystringSECRET91a2b3",
    "ETSY_SHARED_SECRET": "e2esharedSECRET44c55d",
    "ETSY_REFRESH_TOKEN": "424242.e2e-refresh-SECRET-7788",
}
SECRET_NAME_RE = re.compile(r"KEY|SECRET|TOKEN|PASSWORD", re.I)


def _settings() -> dict:
    """The repo's settings with the research stage pinned: with research.stage: auto, a
    checkout whose output/ holds 20+ real Etsy drafts would research in the refined stage
    (and read data/history)."""
    settings = load_settings()
    settings["research"]["stage"] = "discovery"
    return settings


def _niches() -> tuple[str, ...]:
    return tuple(sorted(load_settings()["shops"][0]["niches"]))


def _run(tmp_path: Path, max_items: int, stages=prun.STAGES) -> dict:
    return prun.run(dry_run=True, inputs_dir=tmp_path / "inputs", briefs_dir=tmp_path / "briefs",
                    out_root=tmp_path / "out", max_items=max_items, date=DATE, settings=_settings(), stages=stages)


def _first_briefs(tmp_path: Path) -> dict[str, dict]:
    """niche -> the first brief of its file (the one each niche's packs come from)."""
    return {n: items.read_json(tmp_path / "briefs" / f"{n}-{DATE}.json")["briefs"][0] for n in _niches()}


def _secret_values() -> set[str]:
    values = set(FAKE_SECRETS.values())
    values |= {v for k, v in os.environ.items()  # paths (e.g. *_KEY_FILE) are not secrets
               if SECRET_NAME_RE.search(k) and len(v.strip()) >= 12 and not v.startswith(("/", "~", "."))}
    return values


@pytest.mark.slow
def test_dry_run_end_to_end_all_niches_and_idempotent_rerun(tmp_path, monkeypatch):
    for name, value in FAKE_SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ETSY_TOKEN_FILE", str(tmp_path / "etsy_tokens.json"))  # never the repo's file
    write_demo_inputs(tmp_path / "inputs", niches=DEMO_NICHES)
    out = tmp_path / "out"
    niches = _niches()

    # Research first, to size max_items: every pack of each niche's first brief (themed packs,
    # docs/PACKS.md: the master plus the smaller sizes cut from it), and nothing more.
    research_only = _run(tmp_path, 0, stages=("research",))
    assert not research_only["errors"], research_only["errors"]
    first = _first_briefs(tmp_path)
    sizes = {n: sorted(set(b.get("pack_sizes") or [b["page_count"]])) for n, b in first.items()}
    assert all(len(v) >= 2 for v in sizes.values()), sizes          # research writes several pack sizes
    cap = sum(len(v) for v in sizes.values())

    started = time.monotonic()
    summary = _run(tmp_path, cap, stages=("generate", "qa", "listing", "publish"))
    elapsed = time.monotonic() - started
    assert prun.exit_code(summary) == 0, prun.format_summary(summary)
    assert not summary["errors"] and not summary["setup_errors"]
    assert sorted(Path(b).name for b in summary["briefs"]) == [f"{n}-{DATE}.json" for n in niches]

    # One item per pack of each niche's first brief, each through every stage: QA passed,
    # listed, and a simulated draft.
    dirs = list(items.iter_item_dirs(out))
    manifests = {d.name: items.read_manifest(d) for d in dirs}
    for n in niches:
        mine = [m for m in manifests.values() if m["niche"] == n]
        assert {m["brief_id"] for m in mine} == {first[n]["brief_id"]}
        assert sorted(m["pack"]["size"] for m in mine) == sizes[n]
        master = next(m for m in mine if m["pack"]["source"] == "master")
        assert master["pack"]["size"] == max(sizes[n])
        for m in mine:
            assert m["pack"]["source"] in ("master", "derived", "fresh")
            assert len(m["pack"]["page_ids"]) == len(set(m["pack"]["page_ids"])) == m["pack"]["size"]
            assert items.content_count(m) == m["pack"]["size"]
            if m["pack"]["source"] == "derived":
                assert set(m["pack"]["page_ids"]) < set(master["pack"]["page_ids"])
                assert {r["item_id"] for r in m["pack"]["related"] if r["source"] == "master"} == {master["item_id"]}
        assert {r["size"] for r in master["pack"]["related"]} == set(sizes[n]) - {max(sizes[n])}
        set_doc = items.read_json(out / "sets" / master["pack"]["set_id"] / "set.json")
        assert set_doc["schema"] == "set/v1" and len(set_doc["pages"]) == max(sizes[n])
        assert {it["item_id"] for it in set_doc["items"]} >= {master["item_id"]}
    for d in dirs:
        rec = summary["items"][d.name]
        assert all(rec[st]["ok"] and not rec[st].get("skipped") for st in ("generate", "qa", "listing", "publish")), rec
        manifest = manifests[d.name]
        assert manifest["status"] == "drafted" and manifest["generator"]["dry_run"] is True
        assert rec["generate"]["size"] == manifest["pack"]["size"]
        assert items.read_json(d / "qa_report.json")["passed"] is True
        listing = items.read_json(d / "listing.json")
        assert listing["dry_run"] is True and len(listing["tags"]) == 13 and len(listing["title"]) <= 140
        assert find_urls(listing) == [], find_urls(listing)
        publish = items.read_json(d / "publish.json")
        assert publish["dry_run"] is True and publish["state"] == "draft" and publish["complete"] is True
        assert len(publish["file_ids"]) == len(listing["file_paths"])
        assert len(publish["image_ids"]) == len(listing["image_paths"]) >= 1
        assert items.review_entry_path(d.name, out / "review").exists()
    assert len(summary["packs"]) == cap
    # Dry runs stay fast (fake images are small): the whole run took well under 3 minutes here.
    print(f"e2e dry run: {len(niches)} niches, {cap} packs, {elapsed:.0f}s")

    # The run summary is written and records where it is.
    summary_path = Path(summary["path"])
    assert summary_path.is_file() and summary_path.parent == out / "runs"
    assert items.read_json(summary_path)["items"].keys() == summary["items"].keys()

    # No secret-looking environment value reaches any file the run wrote.
    secrets = _secret_values()
    for p in tmp_path.rglob("*"):
        if p.is_file() and not p.is_relative_to(tmp_path / "inputs"):
            data = p.read_bytes()
            leaked = [s for s in secrets if s.encode() in data]
            assert not leaked, f"{p.relative_to(tmp_path)} contains a secret value"
    assert not (tmp_path / "etsy_tokens.json").exists()  # a dry run never touches the token file

    # A second identical run reuses every item: no new folder and no second draft.
    before = {d.name: (d / "publish.json").read_bytes() for d in dirs}
    again = _run(tmp_path, cap, stages=("generate", "qa", "listing", "publish"))
    assert prun.exit_code(again) == 0, prun.format_summary(again)
    assert {d.name for d in items.iter_item_dirs(out)} == set(before)
    assert {d.name: (d / "publish.json").read_bytes() for d in dirs} == before
    for name in before:
        assert again["items"][name]["generate"]["skipped"] and "publish" not in again["items"][name]
    assert len(list((out / "runs").glob("run-*.json"))) == 3  # earlier summaries are kept


# ---------------------------------------------------------------- weekly plan lines (fast, fake stages)

PLAN_DATE = "2026-10-26"
ORDER = "pl-20261025-0001"


@pytest.fixture
def planned(tmp_path, monkeypatch):
    """Fake generate / qa / listing / publish (as in tests/test_pipeline_cooldown.py) and a brief
    file with two stationery briefs of pack sizes 5, 12 and 20."""
    import generators
    import listing
    import publisher
    import qa
    from core import config
    from generators import packs

    settings = config.load_settings()
    config.deep_merge(settings, {"paths": {"output_dir": str(tmp_path / "real"),
                                           "review_dir": str(tmp_path / "real" / "review")},
                                 "pipeline": {"dry_run_output_dir": str(tmp_path / "dry")},
                                 "packs": {"state_file": str(tmp_path / "packs_state.json")}})
    calls = {"generate": [], "qa": [], "publish": []}

    def fake_generate(brief, *, brief_file=None, shop_id="main", out_root=None, date=None, dry_run=False,
                      settings=None, client=None, sizes=None):
        calls["generate"].append((brief["brief_id"], sizes))
        dirs = []
        for pk in packs.preview(brief, settings=settings, out_root=out_root, shop_id=shop_id, date=date,
                                dry_run=dry_run, sizes=sizes):
            d = items.item_dir(pk.item_id, out_root)
            items.write_manifest(d, {"item_id": d.name, "niche": brief["niche"], "shop_id": shop_id,
                                     "brief_id": brief["brief_id"], "theme": brief["theme"], "created": date,
                                     "status": "generated", "generator": {"name": brief["niche"], "dry_run": dry_run},
                                     "pack": {"size": pk.size, "source": pk.source, "set_id": pk.spec.set_id}})
            dirs.append(d)
        return dirs

    def fake_qa(d, *, settings=None, review_dir=None, mockups=True):
        calls["qa"].append(d.name)
        items.set_status(d, "qa_passed")
        return {"item_id": d.name, "passed": True, "needs_review": False, "checks": []}

    def fake_listing(d, *, client=None, dry_run=False, settings=None, review_dir=None):
        items.set_status(d, "listed")
        return {"title": f"title {d.name}"}

    def fake_publish(d, *, client=None, dry_run=False, settings=None, review_dir=None):
        calls["publish"].append(d.name)
        items.write_json(d / "publish.json", {"listing_id": 7, "dry_run": dry_run, "complete": True})
        items.set_status(d, "drafted")
        return {"listing_id": 7, "dry_run": dry_run}

    monkeypatch.setattr(generators, "generate", fake_generate)
    monkeypatch.setattr(qa, "run", fake_qa)
    monkeypatch.setattr(listing, "write_listing", fake_listing)
    monkeypatch.setattr(listing, "needs_listing",
                        lambda d, *, dry_run=False, settings=None: items.read_manifest(d)["status"] == "qa_passed")
    monkeypatch.setattr(publisher, "publish_draft", fake_publish)
    monkeypatch.setattr(publisher, "review_wait", lambda d, *, settings=None: None)
    themes = ["weekly reset", "habit grid", "focus blocks"]
    bf = items.write_json(tmp_path / "briefs" / "stationery-2026-10-24.json", {
        "schema": "brief/v1", "niche": "stationery", "created": "2026-10-24", "shop_id": "main",
        "listing_count": 50, "min_listings_per_pattern": 10, "patterns": {},
        "briefs": [{"brief_id": f"stationery-2026-10-24-0{i}", "niche": "stationery", "theme": t, "keywords": [t],
                    "pack_sizes": [5, 12, 20], "page_count": 20, "evidence": {"listing_count": 12},
                    "compliance": {"blocked": [], "flagged": []}, "needs_review": False}
                   for i, t in enumerate(themes, 1)]})

    def line(n, sizes, theme=None, **kw):
        return {"brief_file": str(bf), "brief_id": f"stationery-2026-10-24-0{n}", "niche": "stationery",
                "theme": theme or themes[n - 1], "sizes": sizes, "origin": "plan", "order_id": ORDER, **kw}

    def run(lines, **kw):
        kw.setdefault("stages", ("generate", "qa", "listing", "publish"))
        return prun.run(dry_run=True, out_root=tmp_path / "out", briefs_dir=tmp_path / "briefs", date=PLAN_DATE,
                        settings=settings, plan_lines=lines, **kw)

    return {"run": run, "line": line, "calls": calls, "out": tmp_path / "out", "bf": bf}


def test_plan_lines_make_only_their_sizes_and_tag_the_manifests(planned):
    summary = planned["run"]([planned["line"](2, [20, 5])])
    assert prun.exit_code(summary) == 0
    assert planned["calls"]["generate"] == [("stationery-2026-10-24-02", [20, 5])]   # never the brief's 12
    made = {items.read_manifest(d)["pack"]["size"]: items.read_manifest(d) for d in items.iter_item_dirs(planned["out"])}
    assert sorted(made) == [5, 20]
    assert all(m["origin"] == "plan" and m["order_id"] == ORDER for m in made.values())
    res = summary["plan"]["lines"][0]
    assert res["dropped"] is None and set(res["made"]) == {"5", "20"} and res["skipped"] == {}
    assert summary["briefs"] == [items.repo_relative(planned["bf"])]


def test_packs_already_made_take_no_slot_and_still_go_on_to_drafts(planned):
    first = planned["run"]([planned["line"](1, [20])])
    made = next(iter(first["plan"]["lines"][0]["made"].values()))
    planned["calls"]["generate"].clear()
    summary = planned["run"]([planned["line"](1, [20]), planned["line"](3, [5, 20])], max_items=2)
    assert planned["calls"]["generate"] == [("stationery-2026-10-24-03", [20, 5])]    # 2 new packs: the cap
    assert summary["items"][made]["generate"]["skipped"] and summary["plan"]["lines"][0]["made"] == {"20": made}
    assert made in planned["calls"]["publish"]                                        # drafts of made items go on
    capped = planned["run"]([planned["line"](2, [5, 12, 20])], max_items=1)
    assert len(capped["plan"]["lines"][0]["made"]) == 1                               # the rest waits for a later run


def test_a_rewritten_or_missing_brief_drops_the_line(planned, tmp_path):
    lines = [planned["line"](1, [5], theme="something else"),
             planned["line"](3, [5], brief_file=str(tmp_path / "briefs" / "stationery-2026-10-01.json")),
             planned["line"](2, [7, 12])]
    summary = planned["run"](lines)
    got = [(r["brief_id"], r["dropped"]) for r in summary["plan"]["lines"]]
    assert got == [("stationery-2026-10-24-01", "brief_changed"), ("stationery-2026-10-24-03", "brief_missing"),
                   ("stationery-2026-10-24-02", None)]
    assert summary["plan"]["lines"][2]["skipped"] == {"7": "size_not_in_brief"}
    assert planned["calls"]["generate"] == [("stationery-2026-10-24-02", [12])]


def test_bad_plan_lines_are_refused_and_other_shops_niches_ignored(planned):
    bad = planned["line"](1, [5])
    bad.pop("order_id")
    other = {**planned["line"](2, [5]), "niche": "bookmarks", "brief_id": "bookmarks-2026-10-24-02"}
    summary = planned["run"]([bad, other, planned["line"](1, [5])])
    assert [r["dropped"] for r in summary["plan"]["lines"]] == ["bad_line", "not_this_shop", None]
    assert any("not well formed" in e for e in summary["errors"])
    planned["calls"]["generate"].clear()
    empty = planned["run"]([])
    assert empty["plan"] == {"lines": []} and planned["calls"]["generate"] == []      # nothing new is made
    assert all(r["generate"].get("skipped") for r in empty["items"].values())        # made packs still go on


def test_the_theme_cooldown_still_applies_to_plan_lines(planned):
    d = items.item_dir(items.new_item_id("2026-10-01", "stationery", "Habit Grid", salt="old"), planned["out"])
    items.write_manifest(d, {"item_id": d.name, "niche": "stationery", "shop_id": "main", "brief_id": "old",
                             "theme": "Habit Grid", "created": "2026-10-01", "status": "drafted",
                             "pack": {"size": 5, "source": "master"}})
    summary = planned["run"]([planned["line"](2, [5, 20])])
    res = summary["plan"]["lines"][0]
    assert res["skipped"] == {"5": f"cooldown: {d.name}"} and list(res["made"]) == ["20"]


def test_items_made_before_the_plan_still_get_qa_listing_and_drafts(planned):
    """docs/OVERSEER.md B5: under a plan only its lines are made, but items already made from
    the newest briefs (under the normal rules, before the plan) still go on to drafts."""
    from pipeline import run as prun

    d = items.item_dir(items.new_item_id(PLAN_DATE, "stationery", "habit grid", salt="before"), planned["out"])
    items.write_manifest(d, {"item_id": d.name, "niche": "stationery", "shop_id": "main",
                             "brief_id": "stationery-2026-10-24-02", "theme": "habit grid", "created": PLAN_DATE,
                             "status": "generated", "generator": {"name": "stationery", "dry_run": True},
                             "pack": {"size": 12, "source": "master"}})
    summary = planned["run"]([planned["line"](1, [5])])
    assert planned["calls"]["generate"] == [("stationery-2026-10-24-01", [5])]   # brief 2's 5 and 20: not made
    assert d.name in planned["calls"]["publish"]                                 # but its made item is drafted
    assert items.read_manifest(d).get("origin") is None                          # and not tagged as a plan item
    assert prun.exit_code(summary) == 0
