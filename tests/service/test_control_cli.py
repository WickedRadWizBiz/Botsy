"""`python -m service control ...` (service/control_cli.py): your orders from the server, no codes.
Everything in tmp_path; Discord posts are captured, never sent."""
from __future__ import annotations

import os
import shutil
from collections import namedtuple

import pytest

from core.config import ROOT
from service import __main__ as cli
from service import capacity, orders
from tests.service._support import make_settings

Usage = namedtuple("Usage", "total used free")
ITEM = "2026-10-20-coloring_pages-axolotl-garden-1a2b"


@pytest.fixture(autouse=True)
def big_disk(monkeypatch):
    monkeypatch.setattr(capacity.shutil, "disk_usage", lambda p: Usage(10**12, 0, 10**12))


@pytest.fixture
def posts(monkeypatch):
    from service import notify

    got = []
    monkeypatch.setattr(notify, "send", lambda text, **kw: got.append((text, kw.get("urgent"))) or True)
    return got


def setup(tmp_path, limits=True):
    if limits:
        shutil.copy(ROOT / "config" / "limits.example.yaml", tmp_path / "limits.yaml")
        os.chmod(tmp_path / "limits.yaml", 0o644)
    s, cfg = make_settings(tmp_path)
    return s, str(cfg)


def item(s):
    import json

    root = s["pipeline"]["dry_run_output_dir"]
    d = os.path.join(root, ITEM)
    os.makedirs(os.path.join(root, "review"), exist_ok=True)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "qa_report.json"), "w") as f:
        json.dump({"schema": "qa/v1", "item_id": ITEM, "passed": True}, f)
    with open(os.path.join(root, "review", f"{ITEM}.json"), "w") as f:
        json.dump({"item_id": ITEM, "niche": "coloring_pages", "status": "pending", "needs_review": True,
                   "reasons": ["qa warning"], "content_digest": "d1", "mockups": []}, f)


def test_pause_resume_and_caps(tmp_path, capsys, posts):
    s, cfg = setup(tmp_path)
    assert cli.main(["control", "pause", "--reason", "incident", "--settings", cfg]) == 0
    assert orders.paused(s)["by"] == "user"
    assert cli.main(["control", "pause", "--settings", cfg]) == 1
    assert "already paused" in capsys.readouterr().out
    assert cli.main(["control", "resume", "--settings", cfg]) == 0 and orders.paused(s) is None
    assert cli.main(["control", "lower-cap", "20", "--settings", cfg]) == 0 and orders.effective_cap(s) == 20.0
    assert not posts                                                          # Level 2: no urgent post
    assert cli.main(["control", "raise-cap", "150", "--settings", cfg]) == 1
    assert "above the cap ceiling" in capsys.readouterr().out
    assert cli.main(["control", "raise-cap", "40", "--settings", cfg]) == 0 and orders.effective_cap(s) == 40.0
    out = capsys.readouterr().out
    assert "Posted in the main Discord channel" in out and posts[-1][1] is True
    assert "asked by: user" in posts[-1][0]


def test_approve_items_and_undo(tmp_path, capsys, posts):
    s, cfg = setup(tmp_path)
    item(s)
    assert cli.main(["control", "approve", ITEM, "--batch", "week-43", "--settings", cfg]) == 0
    from qa import review

    rdir = os.path.join(s["pipeline"]["dry_run_output_dir"], "review")
    assert review.read_entry(ITEM, rdir)["status"] == "approved"
    oid = orders.list_orders(s, kind="review_approve")[0]["id"]
    assert cli.main(["control", "orders", "--settings", cfg]) == 0
    assert f"{oid}" in capsys.readouterr().out
    assert cli.main(["control", "undo", oid, "--settings", cfg]) == 0
    assert review.read_entry(ITEM, rdir)["status"] == "pending"
    assert cli.main(["control", "approve", "--settings", cfg]) == 2                # nothing named


def test_a_missing_limits_file_is_a_setup_problem(tmp_path, capsys):
    s, cfg = setup(tmp_path, limits=False)
    assert cli.main(["control", "pause", "--settings", cfg]) == 0               # stopping always works
    assert cli.main(["control", "resume", "--settings", cfg]) == 2
    assert "limits file" in capsys.readouterr().out
    assert cli.main(["control", "limits", "--settings", cfg]) == 2


def test_control_never_waits_for_the_service_lock():
    assert "control" in cli.NO_LOCK
    with pytest.raises(SystemExit):
        cli.main(["control", "approve", "--code", "123456"])                    # there are no codes
