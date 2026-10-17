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
