"""service/capacity.py: "Botsy has room". Pure reads; everything in tmp_path, the disk is faked."""
from __future__ import annotations

import json
import os
import shutil
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core import spend
from core.config import ROOT
from service import capacity, limits
from tests.service._support import make_settings

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)
Usage = namedtuple("Usage", "total used free")


def big_disk(path):
    return Usage(10**12, 0, 10**12)


def ctx(tmp_path: Path, limits_doc: str | None = None, live: bool = False, **sections):
    tmp_path.mkdir(parents=True, exist_ok=True)
    lim = tmp_path / "limits.yaml"
    if limits_doc is None:
        shutil.copy(ROOT / "config" / "limits.example.yaml", lim)
    else:
        lim.write_text(limits_doc, encoding="utf-8")
    os.chmod(lim, 0o644)
    control = {"orders_dir": str(tmp_path / "data" / "orders"),
               "state_file": str(tmp_path / "data" / "control" / "state.json"), "limits_path": str(lim),
               "limits_require_root": False, **sections.pop("control", {})}
    s, _ = make_settings(tmp_path, service={"live": live}, control=control, **sections)
    return s


def spend_usd(s: dict, dollars: float) -> None:
    """Record `dollars` of Opus 5.5 input tokens ($4 per million) in the tmp ledger."""
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=int(dollars * 250_000),
                 settings=s, now=NOW)


def pending(review_dir: Path, n: int) -> None:
    review_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (review_dir / f"2026-10-20-stickers-frog-{i:04x}.json").write_text(json.dumps(
            {"item_id": f"2026-10-20-stickers-frog-{i:04x}", "status": "pending", "reasons": []}))


def perf_dir(s: dict) -> Path:
    return Path(s["pipeline"]["dry_run_output_dir"]) / "performance"


def check(s, **kw):
    kw.setdefault("disk_usage", big_disk)
    kw.setdefault("now", NOW)
    return capacity.check(s, **kw)


def test_an_empty_server_has_room(tmp_path):
    s = ctx(tmp_path)
    got = check(s)
    assert got["schema"] == "capacity/v1" and got["ok"] and got["reasons"] == []
    f = got["facts"]
    assert f["month"] == "2026-10" and f["spent"] == 0 and f["cap"] == 25.0 and f["money_left"] == 25.0
    assert f["warn_line"] == 20.0 and f["projected"] == 0 and f["review_queue"] == 0
    assert f["waiting_drafts"] == 0 and f["etsy_quota"] is None and f["kdp_slots_left"] is None
    assert f["disk_free_mb"] == 1_000_000.0
    assert got["thresholds"] == limits.DEFAULTS["capacity"]


def test_money_below_the_warn_line(tmp_path):
    s = ctx(tmp_path)
    spend_usd(s, 18.0)
    assert check(s)["ok"]
    got = check(s, planned_usd=2.5)
    assert got["reasons"] == ["money_below_warn_line"]
    assert got["facts"]["projected"] == pytest.approx(20.5) and got["facts"]["money_left"] == pytest.approx(7.0)


def test_the_cap_in_force_counts_not_just_the_setting(tmp_path):
    s = ctx(tmp_path)
    spend_usd(s, 9.0)
    state = tmp_path / "data" / "control" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"schema": "control/v1", "cap_override_usd": 10.0}))   # a lower_cap order
    got = check(s)
    assert got["facts"]["cap"] == 10.0 and got["facts"]["warn_line"] == 8.0
    assert got["reasons"] == ["money_below_warn_line"]


def test_no_cap_means_no_money_reason(tmp_path):
    s = ctx(tmp_path, spend={"monthly_cap_usd": None})
    spend_usd(s, 50.0)
    got = check(s, planned_usd=100)
    assert got["ok"] and got["facts"]["cap"] is None and got["facts"]["warn_line"] is None


def test_a_cap_typo_is_money_unknown(tmp_path):
    s = ctx(tmp_path, spend={"monthly_cap_usd": "lots"})
    got = check(s)
    assert got["reasons"] == ["money_unknown"] and got["facts"]["spent"] is None


def test_review_queue_full(tmp_path):
    s = ctx(tmp_path)
    rdir = Path(s["pipeline"]["dry_run_output_dir"]) / "review"
    pending(rdir, 31)
    got = check(s)
    assert got["reasons"] == ["review_queue_full"] and got["facts"]["review_queue"] == 31
    s2 = ctx(tmp_path / "b", limits_doc="capacity: {max_review_queue: 2}\n")
    pending(Path(s2["pipeline"]["dry_run_output_dir"]) / "review", 3)
    assert check(s2)["reasons"] == ["review_queue_full"]


def test_the_live_queue_is_used_when_live(tmp_path):
    s = ctx(tmp_path, live=True)
    pending(Path(s["paths"]["review_dir"]), 31)
    assert check(s)["reasons"] == ["review_queue_full"]


def test_waiting_drafts_come_from_the_newest_snapshot(tmp_path):
    s = ctx(tmp_path)
    etsy = perf_dir(s) / "etsy"
    etsy.mkdir(parents=True)
    (etsy / "2026-10-19.json").write_text(json.dumps({"schema": "performance/v1", "drafts": 99}))
    (etsy / "2026-10-20.json").write_text(json.dumps({"schema": "performance/v1", "drafts": 41}))
    got = check(s)
    assert got["reasons"] == ["drafts_waiting"]
    assert got["facts"]["waiting_drafts"] == 41 and got["facts"]["waiting_drafts_source"] == "snapshot"
    (etsy / "2026-10-21.json").write_text(json.dumps({"schema": "performance/v1", "listings": [
        {"listing_id": 1, "state": "draft"}, {"listing_id": 2, "state": "active"}]}))
    got = check(s)
    assert got["ok"] and got["facts"]["waiting_drafts"] == 1


def test_waiting_drafts_fall_back_to_publish_records(tmp_path):
    s = ctx(tmp_path, live=True, limits_doc="capacity: {max_waiting_drafts: 1}\n")
    out = Path(s["paths"]["output_dir"])
    for i in range(2):
        d = out / f"2026-10-20-stickers-frog-{i:04x}"
        d.mkdir(parents=True)
        (d / "publish.json").write_text(json.dumps({"listing_id": 900 + i}))
    got = check(s)
    assert got["reasons"] == ["drafts_waiting"] and got["facts"]["waiting_drafts_source"] == "publish_records"


def quota(s, **doc):
    p = perf_dir(s)
    p.mkdir(parents=True, exist_ok=True)
    (p / "etsy_quota.json").write_text(json.dumps({"schema": "quota/v1", "day": "2026-10-21", "limit_per_day": 10000,
                                                   "remaining_today": 9000, "rate_limited_today": 0, **doc}))


def test_etsy_quota_low(tmp_path):
    s = ctx(tmp_path)
    quota(s)
    got = check(s)
    assert got["ok"] and got["facts"]["etsy_quota"]["share_left"] == 0.9
    quota(s, remaining_today=2000)
    assert check(s)["reasons"] == ["etsy_quota_low"]
    quota(s, rate_limited_today=1)
    assert check(s)["reasons"] == ["etsy_quota_low"]


def test_an_old_quota_day_is_not_counted(tmp_path):
    s = ctx(tmp_path)
    quota(s, day="2026-10-19", remaining_today=0, rate_limited_today=5)
    got = check(s)
    assert got["ok"] and got["facts"]["etsy_quota"]["share_left"] is None


def test_disk_low(tmp_path):
    s = ctx(tmp_path)
    got = check(s, disk_usage=lambda p: Usage(10**10, 10**10 - 1_500_000_000, 1_500_000_000))
    assert got["reasons"] == ["disk_low"] and got["facts"]["disk_free_mb"] == 1500.0


def test_disk_uses_the_tightest_folder(tmp_path):
    s = ctx(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    sizes = {str(a): 5_000_000_000, str(b.parent): 1_000_000_000}
    got = check(s, paths={"disk": [a, b / "not-made-yet"]}, disk_usage=lambda p: Usage(0, 0, sizes[p]))
    assert got["facts"]["disk_free_mb"] == 1000.0 and got["reasons"] == ["disk_low"]


def test_kdp_slots_are_read_when_the_queue_exists(tmp_path):
    s = ctx(tmp_path)
    q = tmp_path / "kdp_queue.json"
    q.write_text(json.dumps({"slots_left": 1}))
    assert check(s, paths={"kdp_queue": q})["facts"]["kdp_slots_left"] == 1
    q.write_text("{broken")
    assert check(s, paths={"kdp_queue": q})["facts"]["kdp_slots_left"] is None


def test_several_reasons_at_once(tmp_path):
    s = ctx(tmp_path)
    spend_usd(s, 21.0)
    pending(Path(s["pipeline"]["dry_run_output_dir"]) / "review", 31)
    got = check(s, disk_usage=lambda p: Usage(0, 0, 10))
    assert got["reasons"] == ["money_below_warn_line", "review_queue_full", "disk_low"] and not got["ok"]
    assert set(got["reasons"]) <= set(capacity.REASONS)


def test_a_broken_limits_file_keeps_the_default_thresholds(tmp_path):
    s = ctx(tmp_path, limits_doc="capacity: [oops\n")
    got = check(s)
    assert got["thresholds"] == limits.DEFAULTS["capacity"] and got["ok"]


def test_the_check_writes_nothing(tmp_path):
    s = ctx(tmp_path)
    spend_usd(s, 1.0)
    quota(s)

    def tree():
        return sorted((str(p), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file())

    before = tree()
    check(s, planned_usd=3)
    check(s)
    assert tree() == before


def test_every_reason_has_plain_words():
    assert set(capacity.DESCRIBE) == set(capacity.REASONS)
