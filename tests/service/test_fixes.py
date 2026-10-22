"""service/fixes.py: the fixed watchdog fixes Hermes may ask for (user, 2026-10-21, D4.7).
Orders, control state, locks, outbox and journal all in tmp_path; Discord posts go to a fake."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.config import ROOT
from core.items import write_manifest
from service import capacity, fixes, orders, plan
from service.paths import service_paths
from tests.service._support import make_settings

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)
ITEM = "2026-10-20-coloring_pages-axolotl-garden-1a2b"
Usage = namedtuple("Usage", "total used free")


@pytest.fixture(autouse=True)
def big_disk(monkeypatch):
    monkeypatch.setattr(capacity.shutil, "disk_usage", lambda p: Usage(10**12, 0, 10**12))


@pytest.fixture
def posts(monkeypatch):
    from service import notify

    got = []
    state = {"ok": True}

    def fake(text, *, settings=None, kinds=(), urgent=False, now=None, **kw):
        got.append((text, urgent, tuple(kinds or ())))
        return state["ok"]

    monkeypatch.setattr(notify, "send", fake)
    monkeypatch.setattr(notify, "problem", lambda settings=None: None)      # "Discord is set up"
    return got, state


def ctx(tmp_path: Path, limits_doc: str | None = None) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    lim = tmp_path / "limits.yaml"
    if limits_doc is None:
        shutil.copy(ROOT / "config" / "limits.example.yaml", lim)
    elif limits_doc != "MISSING":
        lim.write_text(limits_doc, encoding="utf-8")
    if lim.exists():
        os.chmod(lim, 0o644)
    s, _ = make_settings(tmp_path)
    return s


def journal(s: dict, kind: str | None = None) -> list[dict]:
    p = Path(s["service"]["events_file"])
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
    return [r for r in rows if kind is None or r["kind"] == kind]


def fix(kind: str, payload: dict | None = None, by: str = "hermes") -> dict:
    return {"schema": "order/v1", "kind": kind, "created_by": by, "payload": payload or {}}


def submit(s, o, now=NOW):
    return orders.submit(o, s, now=now)


def outbox(s) -> Path:
    return service_paths(s).outbox


def failed_tick(s, errors=("research: ConnectionError: the server could not reach the API",), exit_code=1,
                started="2026-10-21T14:00:00+00:00", finished="2026-10-21T14:10:00+00:00", last_checked=None,
                failed=()):
    rec = {"started": started, "finished": finished, "errors": list(errors), "exit_code": exit_code,
           "run": {"ran": True, "failed": list(failed), "exit_code": exit_code}}
    doc = {"schema": "status/v1", "ticks": [rec], "last_checked": last_checked or finished}
    outbox(s).mkdir(parents=True, exist_ok=True)
    (outbox(s) / "status.json").write_text(json.dumps(doc), encoding="utf-8")
    return rec


# ---------------------------------------------------------------- the order side

def test_fix_kinds_are_level_2_orders_with_fixed_payloads(tmp_path, posts):
    s = ctx(tmp_path)
    for kind in orders.FIX_KINDS:
        assert orders.KINDS[kind]["level"] == 2 and kind in fixes.RECIPES
    bad = [fix("fix_clear_stale_lock", {"lock": "/etc/passwd"}), fix("fix_clear_stale_lock", {}),
           fix("fix_rerun_qa", {"item_id": "../x"}), fix("fix_resend_daily_log", {"text": "hi"}),
           fix("fix_retry_run", {"tick_started": "yesterday"}), fix("fix_retry_notify", {"order_ids": []})]
    for o in bad:
        res = submit(s, o)
        assert not res["ok"] and res["stage"] == "format", o
    assert not orders.list_orders(s)


def test_fixes_are_off_without_the_limits_file(tmp_path, posts):
    s = ctx(tmp_path, limits_doc="MISSING")
    failed_tick(s)
    res = submit(s, fix("fix_retry_run"))
    assert not res["ok"] and res["reasons"] == ["limits_unavailable"]


def test_every_fix_reason_is_described():
    for r in fixes.REASONS:
        assert orders.describe(r) and orders.describe(r) != r, r
    assert set(fixes.DESCRIBE) == set(fixes.REASONS)


# ---------------------------------------------------------------- retry_last_failed_run

def test_retry_of_a_retryable_failure_is_queued_counted_posted_and_run_by_the_plan_check(tmp_path, posts):
    got, _ = posts
    s = ctx(tmp_path)
    rec = failed_tick(s, failed=["2026-10-21-stickers-x-0001: generate: APITimeoutError: timed out"])
    res = submit(s, fix("fix_retry_run", {"tick_started": rec["started"]}))
    assert res["ok"] and res["status"] == "applied", res
    o = orders.load(res["order_id"], s)
    facts = o["checks"]["facts"]
    assert facts["retryable"] is True and facts["error_classes"] == ["APITimeoutError", "ConnectionError"]
    assert facts["tick_exit_code"] == 1 and facts["paid_work_block"] is None
    assert orders.counts(s, now=NOW)["run_now_today"] == 1 and orders.counts(s, now=NOW)["fixes_today"] == 1
    assert [p.name for p in fixes._pending(s)] == [f"{res['order_id']}.json"]
    text = got[-1][0]
    assert "Retry of the failed check queued" in text and "asked by: hermes" in text and res["order_id"] in text
    ev = journal(s, "fix_applied")[-1]
    assert ev["fix"] == "fix_retry_run" and ev["recipe"] == "retry_last_failed_run" and ev["facts"]["retryable"]
    assert "could not reach" not in json.dumps(journal(s))                     # never the error text
    # A second retry while one is queued is refused.
    again = submit(s, fix("fix_retry_run"))
    assert not again["ok"] and "retry_pending" in again["reasons"] and again["facts"]["retry_pending"]
    # The hourly plan check runs it, under the main lock.
    calls = []

    def run_check(settings):
        calls.append(1)
        return {"exit_code": 0}

    out = plan.check(s, now=NOW + timedelta(minutes=5), run_check=run_check)
    assert calls == [1] and out["fix"]["ran"] and out["fix"]["exit_code"] == 0
    assert not fixes._pending(s) and journal(s, "fix_run")[-1]["ran"] is True
    assert "finished fine" in got[-1][0]
    assert plan.check(s, now=NOW + timedelta(minutes=65), run_check=run_check).get("fix") is None
    assert calls == [1]


def test_a_queued_retry_is_checked_again_before_it_runs(tmp_path, posts):
    s = ctx(tmp_path)
    failed_tick(s)
    assert submit(s, fix("fix_retry_run"))["ok"]
    failed_tick(s, last_checked="2026-10-21T14:50:00+00:00")                   # a check ran fine since
    calls = []
    out = fixes.run_pending(s, now=NOW, run_check=lambda st: calls.append(1))
    assert not out["ran"] and out["reason"] == "newer_run_ok" and not calls and not fixes._pending(s)
    assert journal(s, "fix_run")[-1]["reasons"] == ["newer_run_ok"]


def test_a_busy_lock_leaves_the_retry_queued_a_few_times(tmp_path, posts):
    s = ctx(tmp_path)
    s["fixes"] = {"lock_wait_s": 0, "max_tries": 2}
    failed_tick(s)
    assert submit(s, fix("fix_retry_run"))["ok"]
    path = service_paths(s).lock
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert fixes.run_pending(s, now=NOW, run_check=lambda st: {})["reason"] == "busy"
        assert fixes._pending(s)
        assert fixes.run_pending(s, now=NOW, run_check=lambda st: {})["reason"] == "service_busy"
        assert not fixes._pending(s)
    finally:
        os.close(fd)


@pytest.mark.parametrize("tick, reason", [
    ({"errors": ["ingest: ValueError: bad header"]}, "not_retryable"),
    ({"errors": ["research: ConnectionError: x", "publish: BudgetReached: cap"]}, "not_retryable"),
    ({"errors": ["setup: something odd"]}, "not_retryable"),
    ({"errors": ["research: ConnectionError: raised while handling BudgetReached"]}, "not_retryable"),
    ({"errors": [], "exit_code": 0}, "no_failed_run"),
    ({"last_checked": "2026-10-21T14:40:00+00:00"}, "newer_run_ok"),
])
def test_retry_preconditions(tmp_path, posts, tick, reason):
    s = ctx(tmp_path)
    failed_tick(s, **tick)
    res = submit(s, fix("fix_retry_run"))
    assert not res["ok"] and res["stage"] == "switches" and reason in res["reasons"], res
    ev = journal(s, "order_refused")[-1]
    assert reason in ev["reasons"] and "facts" in ev and not fixes._pending(s)
    assert orders.counts(s, now=NOW)["fixes_today"] == 0                       # a refusal is not counted


def test_no_retry_while_paid_work_is_stopped_or_past_run_now(tmp_path, posts):
    s = ctx(tmp_path)
    failed_tick(s)
    assert submit(s, fix("pause", {"reason_code": "incident"}, by="user"))["ok"]
    res = submit(s, fix("fix_retry_run"))
    assert "paid_work_stopped" in res["reasons"] and res["facts"]["paid_work_block"] == "paused"
    s2 = ctx(tmp_path / "b", limits_doc="schema: limits/v1\nrun_now_per_day: 0\n")
    failed_tick(s2)
    res = submit(s2, fix("fix_retry_run"))
    assert not res["ok"] and res["stage"] == "limits" and res["reasons"] == ["run_now_limit"]
    res = submit(s2, fix("fix_retry_run", {"tick_started": "2026-10-20T01:00:00+00:00"}))
    assert "run_changed" in res["reasons"]


def test_fixes_per_day_limits_hermes(tmp_path, posts):
    s = ctx(tmp_path, limits_doc="schema: limits/v1\nfixes_per_day: 1\n")
    outbox(s).mkdir(parents=True, exist_ok=True)
    (outbox(s) / "daily-log.txt").write_text("Daily log\r\n", encoding="utf-8")
    assert submit(s, fix("fix_resend_daily_log"))["status"] == "applied"
    failed_tick(s)
    res = submit(s, fix("fix_retry_run"))
    assert not res["ok"] and res["reasons"] == ["fixes_limit"]


# ---------------------------------------------------------------- clear_stale_lock

def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _lock(s, pid, age_s):
    path = service_paths(s).lock
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n")
    t = NOW.timestamp() - age_s
    os.utime(path, (t, t))
    return path


def test_a_stale_lock_note_is_cleared_only_when_every_precondition_holds(tmp_path, posts):
    got, _ = posts
    s = ctx(tmp_path)
    old = 5 * 3600                                     # older than 4 h (the longest run) and the 5 s timeout
    path = _lock(s, os.getpid(), old)                  # alive
    res = submit(s, fix("fix_clear_stale_lock", {"lock": "service"}))
    assert res["reasons"] == ["lock_process_alive"] and res["facts"]["pid_alive"] is True
    dead = _dead_pid()
    path = _lock(s, dead, 3600)                        # dead, but younger than the longest run
    res = submit(s, fix("fix_clear_stale_lock", {"lock": "service"}))
    assert res["reasons"] == ["lock_too_young"] and res["facts"]["min_age_s"] == 14400
    path = _lock(s, dead, old)
    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)                     # someone holds it after all
    try:
        res = submit(s, fix("fix_clear_stale_lock", {"lock": "service"}))
        assert res["reasons"] == ["lock_held"] and path.read_text().strip() == str(dead)
    finally:
        os.close(fd)
    res = submit(s, fix("fix_clear_stale_lock", {"lock": "service"}))
    assert res["status"] == "applied" and res["applied"]["result"] == {"cleared": "service"}
    assert path.read_text() == ""
    facts = orders.load(res["order_id"], s)["checks"]["facts"]
    assert facts["pid"] == dead and facts["pid_alive"] is False and facts["flock_free"] and facts["cleared"]
    assert "lock note cleared" in got[-1][0] and f"PID: {dead}" in got[-1][0]
    res = submit(s, fix("fix_clear_stale_lock", {"lock": "service"}))
    assert res["reasons"] == ["lock_no_pid"] or "lock_no_pid" in res["reasons"]
    res = submit(s, fix("fix_clear_stale_lock", {"lock": "plans"}))
    assert res["reasons"] == ["lock_missing"]


def test_a_lock_that_turns_live_between_check_and_run_is_never_cleared(tmp_path, posts, monkeypatch):
    s = ctx(tmp_path)
    path = _lock(s, _dead_pid(), 5 * 3600)
    o = orders.submit(fix("fix_clear_stale_lock", {"lock": "service"}), s, now=NOW, apply_now=False)
    assert o["ok"] and o["status"] == "accepted"
    # Re-run the effect with a process that came back to life: refused at run time.
    monkeypatch.setattr(fixes, "_pid_alive", lambda pid: True)
    with pytest.raises(fixes.FixRefused) as err:
        fixes.perform(orders.load(o["order_id"], s), s, now=NOW)
    assert err.value.reasons == ["lock_process_alive"] and path.read_text().strip().isdigit()
    res = orders.apply(o["order_id"], s, now=NOW)
    assert not res["ok"] and res["reasons"] == ["lock_process_alive"] and res["facts"]["pid_alive"] is True
    assert orders.load(o["order_id"], s)["status"] == "failed"


# ---------------------------------------------------------------- rerun_qa_for_item

def _item(s, report: dict | None, *, status="qa_failed", drafted=False):
    d = orders._work_root(s) / ITEM
    d.mkdir(parents=True, exist_ok=True)
    write_manifest(d, {"item_id": ITEM, "niche": "coloring_pages", "status": status, "files": []})
    if report is not None:
        (d / "qa_report.json").write_text(json.dumps({"schema": "qa/v1", "item_id": ITEM, **report}))
    if drafted:
        (d / "publish.json").write_text(json.dumps({"listing_id": 900000001}))
    return d


CRASHED = {"passed": False, "checks": [
    {"name": "mockups", "passed": False, "severity": "error", "detail": "check crashed: OSError: disk"},
    {"name": "pdf_pages", "passed": True, "severity": "error", "detail": ""}]}
FAILED = {"passed": False, "checks": [
    {"name": "mockups", "passed": False, "severity": "error", "detail": "check crashed: OSError: disk"},
    {"name": "trademark", "passed": False, "severity": "error", "detail": "blocked term"}]}


def test_qa_is_rerun_only_for_an_item_whose_qa_crashed(tmp_path, posts, monkeypatch):
    import qa

    got, _ = posts
    s = ctx(tmp_path)
    ran = []
    monkeypatch.setattr(qa, "run", lambda d, settings=None, **kw: ran.append(Path(d).name) or {
        "passed": True, "checks": []})
    _item(s, FAILED)
    res = submit(s, fix("fix_rerun_qa", {"item_id": ITEM}))
    assert res["reasons"] == ["qa_not_crashed"] and res["facts"]["failed_checks"] == 1 and not ran
    _item(s, {"passed": True, "checks": []})
    assert submit(s, fix("fix_rerun_qa", {"item_id": ITEM}))["reasons"] == ["qa_not_crashed"]
    _item(s, CRASHED, drafted=True)
    assert "already_drafted" in submit(s, fix("fix_rerun_qa", {"item_id": ITEM}))["reasons"]
    shutil.rmtree(orders._work_root(s) / ITEM)
    assert submit(s, fix("fix_rerun_qa", {"item_id": ITEM}))["reasons"] == ["item_unknown"]
    _item(s, CRASHED)
    res = submit(s, fix("fix_rerun_qa", {"item_id": ITEM}))
    assert res["status"] == "applied" and ran == [ITEM]
    assert res["applied"]["result"] == {"item_id": ITEM, "passed": True, "checks_failed": 0}
    assert "QA rerun for" in got[-1][0] and "crashed checks: 1" in got[-1][0]


def test_qa_that_crashed_before_writing_a_report_is_rerun(tmp_path, posts, monkeypatch):
    import qa

    s = ctx(tmp_path)
    monkeypatch.setattr(qa, "run", lambda d, settings=None, **kw: {"passed": False, "checks": [
        {"name": "x", "passed": False, "severity": "error"}]})
    _item(s, None, status="generated")
    assert submit(s, fix("fix_rerun_qa", {"item_id": ITEM}))["reasons"] == ["qa_not_crashed"]
    failed_tick(s, errors=[], failed=[f"{ITEM}: qa: MemoryError: out of memory"])
    res = submit(s, fix("fix_rerun_qa", {"item_id": ITEM}))
    assert res["status"] == "applied" and res["applied"]["result"]["checks_failed"] == 1


def test_qa_rerun_never_waits_for_a_running_check(tmp_path, posts, monkeypatch):
    import qa

    s = ctx(tmp_path)
    monkeypatch.setattr(qa, "run", lambda *a, **k: pytest.fail("QA must not run while a check runs"))
    _item(s, CRASHED)
    path = service_paths(s).lock
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        res = submit(s, fix("fix_rerun_qa", {"item_id": ITEM}))
    finally:
        os.close(fd)
    assert res["ok"] and res["status"] == "failed" and res["applied"]["reasons"] == ["service_busy"]
    assert orders.load(res["order_id"], s)["status"] == "failed"
    assert journal(s, "order_failed")[-1]["reasons"] == ["service_busy"]


# ---------------------------------------------------------------- resend_daily_log, retry_notify_outbox

def test_the_daily_log_is_resent_once(tmp_path, posts, monkeypatch):
    got, state = posts
    s = ctx(tmp_path)
    assert submit(s, fix("fix_resend_daily_log"))["reasons"] == ["no_daily_log"]
    log_file = outbox(s) / "daily-log.txt"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("Botsy daily log\r\nAll quiet.\r\n", encoding="utf-8")
    t = NOW.timestamp() - 3600
    os.utime(log_file, (t, t))
    res = submit(s, fix("fix_resend_daily_log"))
    assert res["status"] == "applied" and res["applied"]["result"]["sent"] is True
    assert got[-2][0].startswith("Botsy daily log") and got[-2][1] is True and "Daily log sent again" in got[-1][0]
    assert submit(s, fix("fix_resend_daily_log"))["reasons"] == ["already_resent"]
    t = NOW.timestamp() - 40 * 3600
    os.utime(log_file, (t, t))
    assert "daily_log_stale" in submit(s, fix("fix_resend_daily_log"))["reasons"]
    from service import notify

    monkeypatch.setattr(notify, "problem", lambda settings=None: "no webhook")
    assert "discord_off" in submit(s, fix("fix_resend_daily_log"))["reasons"]


def test_unsent_approval_notices_are_posted_again(tmp_path, posts):
    got, state = posts
    s = ctx(tmp_path)
    state["ok"] = False                                                       # Discord is down
    raised = submit(s, fix("raise_cap", {"cap_usd": 40}, by="user"))
    assert raised["status"] == "applied" and not raised["applied"]["announced"]
    assert orders.load(raised["order_id"], s)["announced"] is False
    assert [o["id"] for o in fixes.unsent(s, NOW)] == [raised["order_id"]]
    res = submit(s, fix("fix_retry_notify"))
    assert res["ok"] and res["status"] == "failed" and res["applied"]["reasons"] == ["send_failed"]
    state["ok"] = True
    res = submit(s, fix("fix_retry_notify"))
    assert res["status"] == "applied" and res["applied"]["result"]["resent"] == [raised["order_id"]]
    assert any("Monthly cap raised" in t for t, *_ in got)
    assert orders.load(raised["order_id"], s)["announced"] is True
    assert submit(s, fix("fix_retry_notify"))["reasons"] == ["nothing_unsent"]


def test_error_classes_come_from_a_fixed_list():
    assert fixes.error_class("research: ConnectionError: x") == "ConnectionError"
    assert fixes.error_class("x: SomeSecretTokenError: y") == "other"
    assert fixes.error_class("nothing here") == "other"
    # A retryable class that wraps a non-retryable cause is not retryable (the worst one wins).
    assert fixes.error_class("spend: ConnectionError: after BudgetExceeded: cap") == "BudgetExceeded"
    assert fixes.error_class("x: ReadTimeout: caused by WeirdLocalError") == "other"
    assert fixes.error_class("x: ReadTimeout: then ConnectionError") == "ReadTimeout"
    assert fixes.safe_facts({"a": "x y", "b": 1, "c": ["ok", "no way"], "D": 2}) == {"b": 1, "c": ["ok"]}


def test_a_fix_notice_held_for_quiet_hours_is_not_posted_again_at_night(tmp_path, posts, monkeypatch):
    from service import notify

    got, state = posts
    s = ctx(tmp_path)
    state["ok"] = False                                    # send() holds it for the morning log
    monkeypatch.setattr(notify, "in_quiet_hours", lambda settings=None, now=None: True)
    failed_tick(s)
    queued = submit(s, fix("fix_retry_run"))
    assert queued["status"] == "applied"
    assert orders.load(queued["order_id"], s)["announced"] is True       # held for the morning log
    state["ok"] = True
    assert submit(s, fix("fix_retry_notify"))["reasons"] == ["nothing_unsent"]
    # Outside quiet hours a notice Discord did not take is still re-posted later.
    state["ok"] = False
    monkeypatch.setattr(notify, "in_quiet_hours", lambda settings=None, now=None: False)
    for p in fixes.requests_dir(s).glob("*.json"):
        p.unlink()
    again = submit(s, fix("fix_retry_run"), now=NOW + timedelta(minutes=1))
    assert again["status"] == "applied"
    assert orders.load(again["order_id"], s)["announced"] is False
    state["ok"] = True
    res = submit(s, fix("fix_retry_notify"), now=NOW + timedelta(minutes=2))
    assert res["applied"]["result"]["resent"] == [again["order_id"]]
