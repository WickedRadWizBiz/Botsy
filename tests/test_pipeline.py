"""Offline end-to-end run: research.demo exports -> briefs -> items -> QA -> listing -> dry-run draft.

Everything runs for real except the network: the image niches use the fake image client,
the listing stage writes template copy, and the publisher talks to FakeEtsyClient. All
paths are in tmp_path, so nothing is written to output/ or data/.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from core import items
from core.compliance import find_urls
from core.config import load_settings
from pipeline import run as prun
from research.demo import write_demo_inputs

DATE = "2026-10-15"
NICHES = ("activity_sheets", "coloring_pages", "stickers")
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


def _run(tmp_path: Path) -> dict:
    return prun.run(dry_run=True, inputs_dir=tmp_path / "inputs", briefs_dir=tmp_path / "briefs",
                    out_root=tmp_path / "out", max_items=3, date=DATE, settings=_settings())


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
    write_demo_inputs(tmp_path / "inputs")
    out = tmp_path / "out"

    summary = _run(tmp_path)
    assert prun.exit_code(summary) == 0, prun.format_summary(summary)
    assert not summary["errors"] and not summary["setup_errors"]
    assert sorted(Path(b).name for b in summary["briefs"]) == [f"{n}-{DATE}.json" for n in NICHES]

    # One item per niche (max_items=3, round-robin over the niches' brief files), each through
    # every stage: QA passed, listed, and a simulated draft.
    dirs = list(items.iter_item_dirs(out))
    assert sorted(items.read_manifest(d)["niche"] for d in dirs) == list(NICHES)
    for d in dirs:
        rec = summary["items"][d.name]
        assert all(rec[st]["ok"] and not rec[st].get("skipped") for st in ("generate", "qa", "listing", "publish")), rec
        manifest = items.read_manifest(d)
        assert manifest["status"] == "drafted" and manifest["generator"]["dry_run"] is True
        assert items.read_json(d / "qa_report.json")["passed"] is True
        listing = items.read_json(d / "listing.json")
        assert listing["dry_run"] is True and len(listing["tags"]) == 13 and len(listing["title"]) <= 140
        assert find_urls(listing) == [], find_urls(listing)
        publish = items.read_json(d / "publish.json")
        assert publish["dry_run"] is True and publish["state"] == "draft" and publish["complete"] is True
        assert len(publish["file_ids"]) == len(listing["file_paths"])
        assert len(publish["image_ids"]) == len(listing["image_paths"]) >= 1
        assert items.review_entry_path(d.name, out / "review").exists()

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
    again = _run(tmp_path)
    assert prun.exit_code(again) == 0, prun.format_summary(again)
    assert {d.name for d in items.iter_item_dirs(out)} == set(before)
    assert {d.name: (d / "publish.json").read_bytes() for d in dirs} == before
    for name in before:
        assert again["items"][name]["generate"]["skipped"] and "publish" not in again["items"][name]
    assert len(list((out / "runs").glob("run-*.json"))) == 2  # earlier summaries are kept
