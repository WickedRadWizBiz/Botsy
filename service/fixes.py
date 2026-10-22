"""Fixed watchdog fixes (docs/OVERSEER.md Part 1, "7. Incident helper"; user, 2026-10-21, D4.7).

Hermes may ask for one of five fixed recipes through botsy-control. Each is an order/v1 kind
(Level 2, service/orders.py) and Botsy itself checks hard preconditions before it does
anything, and again at run time; Hermes never names a file, a process, a command or a time:

    kind                    payload                                   recipe
    fix_retry_run           {"tick_started"?: ISO time}               retry_last_failed_run
    fix_clear_stale_lock    {"lock": "service" | "plans"}             clear_stale_lock
    fix_rerun_qa            {"item_id": ITEM_ID}                      rerun_qa_for_item
    fix_resend_daily_log    {}                                        resend_daily_log
    fix_retry_notify        {}                                        retry_notify_outbox

    orders.submit({"kind": "fix_...", "created_by": "hermes", "payload": {...}})

- retry_last_failed_run: only when the last check (outbox/status.json) failed and every error
  in it is of a retryable class (a network timeout, a dropped connection, a busy lock, an API
  overload; never a bad file, a compliance stop or a budget stop), no check ran since, paid
  work is not stopped (pause or budget stop: retries count toward the cap), and at most limits
  run_now_per_day retries in 24 hours (they count toward "run now"). The retry is queued and
  the next hourly plan check (botsy-plan-check.timer, :05) runs it under the main lock, after
  checking the same preconditions again (run_pending).
- clear_stale_lock: only when the lock's process (the PID noted in it) is dead AND the lock
  is older than both service.lock_timeout_s and the longest allowed run (fixes.longest_run_s,
  4 h: botsy-tick.service TimeoutStartSec) AND nobody holds it (a non-blocking flock
  succeeds). Botsy's locks are flock locks the kernel drops with the process, so clearing
  only empties the stale PID note; a held lock is never touched.
- rerun_qa_for_item: an item whose QA crashed (every failed error check of its qa_report.json
  is "check crashed", or QA raised before writing a report and the last check recorded the
  crash), never one that failed real checks, a drafted item or a rejected one. QA runs at
  once (local, no network, no money) under the main lock, taken without waiting.
- resend_daily_log: posts outbox/daily-log.txt again when it exists, is at most
  fixes.daily_log_max_age_hours (36) old, Discord is set up, and it was not resent already.
- retry_notify_outbox: re-posts the approval and fix notices that Discord did not take
  (orders whose `announced` is false, in the last fixes.notify_lookback_hours, at most
  fixes.notify_max a time).

Every fix is rate-limited (limits fixes_per_day, 6 in 24 hours for Hermes), posted to Discord
and journaled (fix_applied, fix_run) with the precondition facts: fixed fields only (counts,
ages, booleans, ids, ISO times and reason codes, never a path, an error message or text from
Hermes). A refused fix is journaled by orders (order_refused) with the same facts.
"""
from __future__ import annotations

import errno
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import events
from core.config import resolve, section
from core.items import read_json, valid_item_id, write_json

log = logging.getLogger("service.fixes")

REQUEST_SCHEMA = "fix-request/v1"

# order kind -> recipe
RECIPES = {
    "fix_retry_run": "retry_last_failed_run",
    "fix_clear_stale_lock": "clear_stale_lock",
    "fix_rerun_qa": "rerun_qa_for_item",
    "fix_resend_daily_log": "resend_daily_log",
    "fix_retry_notify": "retry_notify_outbox",
}
KINDS = tuple(RECIPES)
LOCKS = ("service", "plans")

# Settings section "fixes" (core.config.section(settings, "fixes", SETTINGS_DEFAULTS)).
SETTINGS_DEFAULTS: dict = {
    "longest_run_s": 14400,             # the longest allowed run (botsy-tick.service TimeoutStartSec=4h)
    "requests_dir": None,               # null: fix_requests/ next to the control state file (data/control/)
    "lock_wait_s": 120,                 # a queued retry waits this long for a running check
    "max_tries": 3,                     # a queued retry that finds the lock busy this often is dropped
    "daily_log_max_age_hours": 36,
    "notify_lookback_hours": 48,
    "notify_max": 5,
}

# Exception classes a retry can fix: the network, a busy lock, an overloaded API.
RETRYABLE = (
    "ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "TimeoutError", "ConnectionResetError",
    "ConnectionAbortedError", "RemoteDisconnected", "ProtocolError", "ChunkedEncodingError",
    "APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError", "OverloadedError",
    "ServiceUnavailableError", "LockTimeout", "BrokenPipeError",
)
# Other classes shown by name in the facts; anything else is "other".
KNOWN = RETRYABLE + ("ValueError", "TypeError", "KeyError", "OSError", "FileNotFoundError", "PermissionError",
                     "RuntimeError", "BudgetExceeded", "BudgetReached", "ComplianceError", "HTTPError",
                     "AuthenticationError", "PermissionDeniedError", "BadRequestError", "NotFoundError",
                     "JSONDecodeError", "AttributeError", "IndexError", "ZeroDivisionError")
_CLASS_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Timeout|Exceeded|Reached|Disconnected))\b")
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?")
CRASH_PREFIX = "check crashed:"

REASONS = (
    "no_failed_run", "not_retryable", "newer_run_ok", "paid_work_stopped", "retry_pending", "run_changed",
    "lock_missing", "lock_no_pid", "lock_process_alive", "lock_too_young", "lock_held", "lock_unreadable",
    "item_unknown", "already_drafted", "already_rejected", "qa_not_crashed", "service_busy",
    "no_daily_log", "daily_log_stale", "already_resent", "discord_off", "send_failed", "nothing_unsent",
)
DESCRIBE = {
    "no_failed_run": "The last check did not fail, so there is nothing to retry.",
    "not_retryable": "The last check failed for a reason a retry cannot fix (only network, busy-lock and API "
                     "overload errors are retried).",
    "newer_run_ok": "A check ran after the failed one.",
    "paid_work_stopped": "Paid work is paused or stopped for the budget, so a retry would not run (and "
                         "retries count toward the cap).",
    "retry_pending": "A retry is already queued for the next plan check.",
    "run_changed": "The check that failed is no longer the last one.",
    "lock_missing": "There is no such lock file.",
    "lock_no_pid": "The lock file names no process.",
    "lock_process_alive": "The process holding the lock is still running; it is never cleared.",
    "lock_too_young": "The lock is not older than the lock timeout and the longest allowed run.",
    "lock_held": "Someone holds the lock right now; it is never cleared.",
    "lock_unreadable": "The lock file cannot be read.",
    "item_unknown": "There is no such item.",
    "already_drafted": "That item is already an Etsy draft.",
    "already_rejected": "That item is rejected in the review queue.",
    "qa_not_crashed": "QA did not crash for that item (it passed, or it failed real checks: a rerun is "
                      "not a way past them).",
    "service_busy": "A check is running; try again when it ends.",
    "no_daily_log": "There is no daily log in the outbox.",
    "daily_log_stale": "The daily log is too old to send again.",
    "already_resent": "This daily log was sent again already.",
    "discord_off": "Discord is not set up or is switched off.",
    "send_failed": "Discord did not take the message (the server log says why).",
    "nothing_unsent": "Every approval and fix notice was posted.",
}


class FixRefused(Exception):
    def __init__(self, reasons, facts=None):
        super().__init__(",".join(reasons))
        self.reasons = list(dict.fromkeys(reasons))
        self.facts = dict(facts or {})


# ---------------------------------------------------------------- helpers

def config(settings: dict | None) -> dict:
    return section(settings or {}, "fixes", SETTINGS_DEFAULTS)


def _int(value, default: int, low: int = 0, high: int = 10**7) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else default


def _utc(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(ts) -> datetime | None:
    if not isinstance(ts, str) or not _ISO_RE.fullmatch(ts.strip()):
        return None
    try:
        t = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _iso_or_none(ts) -> str | None:
    t = _parse(ts)
    return _iso(t) if t else None


def requests_dir(settings: dict) -> Path:
    from service.orders import _guard

    from service.orders import state_path

    raw = config(settings).get("requests_dir")
    d = resolve(raw) if isinstance(raw, str) and raw.strip() else state_path(settings).parent / "fix_requests"
    _guard(d)
    return d


def _paths(settings: dict):
    from service.paths import service_paths

    return service_paths(settings)


def _status_doc(settings: dict) -> dict:
    from service import status

    return status.load(_paths(settings).outbox) or {}


def error_class(text) -> str:
    """The exception class a recorded error names: a known class name, else "other". When the
    text names several classes (a wrapped error, a chained cause), the first one a retry cannot
    fix wins, so "ConnectionError: ... BudgetExceeded" is never called retryable (fail closed)."""
    names = _CLASS_RE.findall(str(text or ""))
    if not names:
        return "other"
    for name in names:
        if name not in RETRYABLE:
            return name if name in KNOWN else "other"
    return names[0]


def _last_tick(settings: dict) -> tuple[dict | None, datetime | None]:
    doc = _status_doc(settings)
    ticks = [t for t in doc.get("ticks") or [] if isinstance(t, dict)]
    return (ticks[-1] if ticks else None), _parse(doc.get("last_checked"))


def _failures(rec: dict) -> list[str]:
    out = [str(e) for e in rec.get("errors") or []]
    out += [str(f) for f in ((rec.get("run") or {}).get("failed") or [])]
    return out


def _pending(settings: dict) -> list[Path]:
    d = requests_dir(settings)
    return sorted(p for p in d.glob("*.json") if p.is_file() and not p.name.startswith(".")) if d.is_dir() else []


# ---------------------------------------------------------------- preconditions

def _retry_facts(settings: dict, payload: dict, now: datetime, *, queued: str | None = None) -> tuple[list, dict]:
    from service import orders

    rec, last_checked = _last_tick(settings)
    facts: dict = {"tick_started": None, "tick_exit_code": None, "error_classes": [], "retryable": False,
                   "newer_run": False, "paid_work_block": None, "retry_pending": False}
    reasons: list[str] = []
    code = rec.get("exit_code") if isinstance(rec, dict) else None
    code = code if isinstance(code, int) and not isinstance(code, bool) else None
    failures = _failures(rec) if isinstance(rec, dict) else []
    if rec is None or (not code and not failures):
        reasons.append("no_failed_run")
    else:
        started = _iso_or_none(rec.get("started"))
        facts["tick_started"], facts["tick_exit_code"] = started, code
        classes = [error_class(f) for f in failures]
        facts["error_classes"] = sorted(set(classes))[:10]
        facts["retryable"] = bool(classes) and all(c in RETRYABLE for c in classes)
        if not facts["retryable"]:
            reasons.append("not_retryable")
        finished = _parse(rec.get("finished"))
        if last_checked and finished and last_checked > finished + timedelta(seconds=5):
            facts["newer_run"] = True
            reasons.append("newer_run_ok")
        want = payload.get("tick_started")
        if want and _iso_or_none(want) != started:
            reasons.append("run_changed")
    try:
        block = orders.paid_work_block(settings, now=now)
    except Exception:  # noqa: BLE001 - the control state cannot be read: fail closed
        block = "control_state_unreadable"
    facts["paid_work_block"] = block if isinstance(block, str) and orders.REASON_RE.fullmatch(block) else (
        "other" if block else None)
    if block:
        reasons.append("paid_work_stopped")
    others = [p.stem for p in _pending(settings) if p.stem != queued]
    if others and queued is None:
        facts["retry_pending"] = True
        reasons.append("retry_pending")
    return reasons, facts


def _lock_path(settings: dict, name: str) -> Path:
    if name == "service":
        return _paths(settings).lock
    from service import plan

    return plan.plans_dir(settings) / ".lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return True                     # never treat init (or nonsense) as dead
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _min_lock_age(settings: dict) -> int:
    from service.paths import load_config

    timeout = load_config(settings).get("lock_timeout_s", 1800)
    timeout = int(timeout) if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) else 1800
    longest = _int(config(settings).get("longest_run_s"), 14400, 60)
    return max(timeout, longest)


def _try_flock(fd: int) -> bool:
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK) or isinstance(e, BlockingIOError):
            return False
        raise
    return True


def _lock_facts(settings: dict, payload: dict, now: datetime, *, clear: bool = False) -> tuple[list, dict]:
    """The facts about one lock; with clear=True and every precondition met, empty its PID note
    while holding the flock (so nobody can take it in between)."""
    import fcntl

    name = payload.get("lock")
    if name not in LOCKS:                # orders' format check refuses it first; never guess a path
        return ["lock_missing"], {"lock": None, "exists": False, "cleared": False}
    path = _lock_path(settings, name)
    facts: dict = {"lock": name, "exists": path.is_file(), "pid": None, "pid_alive": None, "age_s": None,
                   "min_age_s": _min_lock_age(settings), "flock_free": None, "cleared": False}
    if not facts["exists"]:
        return ["lock_missing"], facts
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return ["lock_unreadable"], facts
    try:
        try:
            raw = os.read(fd, 64).decode("ascii", "replace").strip()
            age = now.timestamp() - os.fstat(fd).st_mtime
        except OSError:
            return ["lock_unreadable"], facts
        facts["age_s"] = max(0, int(age))
        reasons = []
        if not raw.isdigit():
            reasons.append("lock_no_pid")
        else:
            pid = int(raw)
            facts["pid"] = pid
            facts["pid_alive"] = _pid_alive(pid)
            if facts["pid_alive"]:
                reasons.append("lock_process_alive")
        if age <= facts["min_age_s"]:
            reasons.append("lock_too_young")
        free = _try_flock(fd)
        facts["flock_free"] = free
        if not free:
            reasons.append("lock_held")
        try:
            if clear and not reasons:
                os.ftruncate(fd, 0)
                facts["cleared"] = True
        finally:
            if free:
                fcntl.flock(fd, fcntl.LOCK_UN)
        return reasons, facts
    finally:
        os.close(fd)


def _item_dir(settings: dict, item_id: str) -> Path:
    from service import orders

    return orders._work_root(settings) / item_id


def _qa_facts(settings: dict, payload: dict, now: datetime) -> tuple[list, dict]:
    from core.items import read_manifest
    from service import orders

    item_id = payload.get("item_id")
    facts: dict = {"item_id": item_id, "manifest_status": None, "report": "missing", "crashed_checks": 0,
                   "failed_checks": 0, "tick_recorded_crash": False, "drafted": False, "review_status": None}
    if not isinstance(item_id, str) or not valid_item_id(item_id):
        return ["item_unknown"], facts
    d = _item_dir(settings, item_id)
    try:
        manifest = read_manifest(d)
    except (OSError, ValueError):
        return ["item_unknown"], facts
    status = manifest.get("status") if isinstance(manifest, dict) else None
    facts["manifest_status"] = status if isinstance(status, str) and orders.REASON_RE.fullmatch(status) else None
    reasons = []
    if orders._drafted(settings, item_id):
        facts["drafted"] = True
        reasons.append("already_drafted")
    entry = orders._entry(settings, item_id)
    facts["review_status"] = (entry or {}).get("status") if (entry or {}).get("status") in (
        "pending", "approved", "rejected") else None
    if facts["review_status"] == "rejected":
        reasons.append("already_rejected")
    try:
        rep = read_json(d / "qa_report.json")
        facts["report"] = "present" if isinstance(rep, dict) else "unreadable"
    except FileNotFoundError:
        rep = None
    except (OSError, ValueError):
        rep, facts["report"] = None, "unreadable"
    crashed = False
    if isinstance(rep, dict):
        errs = [c for c in rep.get("checks") or [] if isinstance(c, dict) and not c.get("passed")
                and c.get("severity", "error") == "error"]
        crash = [c for c in errs if str(c.get("detail") or "").startswith(CRASH_PREFIX)]
        facts["crashed_checks"], facts["failed_checks"] = len(crash), len(errs) - len(crash)
        crashed = rep.get("passed") is not True and bool(crash) and len(crash) == len(errs)
    elif facts["report"] == "missing" and status in ("generated", None):
        rec, _ = _last_tick(settings)
        mark = f"{item_id}: qa:"
        hit = any(str(f).startswith(mark) for f in ((rec or {}).get("run") or {}).get("failed") or [])
        facts["tick_recorded_crash"] = hit
        crashed = hit
    if not crashed:
        reasons.append("qa_not_crashed")
    return reasons, facts


def _daily_log(settings: dict) -> Path:
    from service import digest

    return _paths(settings).outbox / digest.LATEST_NAME


def _resent_today(settings: dict, log_mtime: str | None, now: datetime) -> int:
    from service import orders

    n = 0
    for o in orders.list_orders(settings, kind="fix_resend_daily_log"):
        made = _parse(o.get("created_at"))
        if o.get("status") == "applied" and made and made >= now - timedelta(hours=24) \
                and (o.get("result") or {}).get("log_mtime") == log_mtime:
            n += 1
    return n


def _discord_ready(settings: dict) -> bool:
    from service import notify

    try:
        return notify.problem(settings) is None
    except Exception:  # noqa: BLE001
        return False


def _daily_facts(settings: dict, payload: dict, now: datetime) -> tuple[list, dict]:
    p = _daily_log(settings)
    max_age = _int(config(settings).get("daily_log_max_age_hours"), 36, 1, 24 * 14)
    facts: dict = {"exists": p.is_file(), "age_h": None, "max_age_h": max_age, "log_mtime": None,
                   "resent": 0, "discord_ready": _discord_ready(settings)}
    reasons = []
    if not facts["exists"]:
        reasons.append("no_daily_log")
    else:
        mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
        facts["log_mtime"] = _iso(mtime)
        facts["age_h"] = round(max(0.0, (now - mtime).total_seconds() / 3600), 1)
        if facts["age_h"] > max_age:
            reasons.append("daily_log_stale")
        facts["resent"] = _resent_today(settings, facts["log_mtime"], now)
        if facts["resent"]:
            reasons.append("already_resent")
    if not facts["discord_ready"]:
        reasons.append("discord_off")
    return reasons, facts


def unsent(settings: dict, now: datetime) -> list[dict]:
    """Orders whose approval or fix notice Discord did not take, newest last."""
    from service import orders

    hours = _int(config(settings).get("notify_lookback_hours"), 48, 1, 24 * 14)
    since = now - timedelta(hours=hours)
    out = []
    for o in orders.list_orders(settings):
        at = _parse((o.get("approved") or {}).get("at") or o.get("applied_at"))
        if o.get("announced") is False and o.get("status") in ("applied", "undone") and at and at >= since \
                and o.get("kind") not in orders.PLAN_KINDS:
            out.append(o)
    return out


def _notify_facts(settings: dict, payload: dict, now: datetime) -> tuple[list, dict]:
    rows = unsent(settings, now)
    facts = {"unsent": len(rows), "lookback_h": _int(config(settings).get("notify_lookback_hours"), 48, 1, 336),
             "discord_ready": _discord_ready(settings)}
    reasons = []
    if not rows:
        reasons.append("nothing_unsent")
    if not facts["discord_ready"]:
        reasons.append("discord_off")
    return reasons, facts


_CHECKS = {
    "fix_retry_run": _retry_facts,
    "fix_clear_stale_lock": _lock_facts,
    "fix_rerun_qa": _qa_facts,
    "fix_resend_daily_log": _daily_facts,
    "fix_retry_notify": _notify_facts,
}


def check(kind: str, payload: dict, settings: dict, *, now: datetime | None = None) -> tuple[list[str], dict]:
    """(reasons, facts) for one fix: no reasons means every precondition holds right now."""
    now = _utc(now)
    try:
        return _CHECKS[kind](settings, payload or {}, now)
    except FixRefused as e:
        return e.reasons, e.facts


# ---------------------------------------------------------------- performing (orders._effect)

def perform(order: dict, settings: dict, *, now: datetime | None = None) -> tuple[dict, dict]:
    """Do the fix of an accepted fix order, checking its preconditions once more first.
    Returns (result, facts); raises FixRefused. Runs under the orders lock (never waits on
    the main lock: the QA rerun takes it without waiting, the run retry is queued)."""
    now = _utc(now)
    kind, p = order["kind"], order.get("payload") or {}
    if kind == "fix_clear_stale_lock":
        reasons, facts = _lock_facts(settings, p, now, clear=True)
        if reasons or not facts.get("cleared"):
            raise FixRefused(reasons or ["lock_held"], facts)
        return {"cleared": p.get("lock")}, facts
    reasons, facts = check(kind, p, settings, now=now)
    if reasons:
        raise FixRefused(reasons, facts)
    if kind == "fix_retry_run":
        d = requests_dir(settings)
        d.mkdir(parents=True, exist_ok=True)
        write_json(d / f"{order['id']}.json", {"schema": REQUEST_SCHEMA, "order_id": order["id"],
                                              "recipe": RECIPES[kind], "tick_started": facts.get("tick_started"),
                                              "created_at": _iso(now), "tries": 0})
        return {"queued": True, "runs_at": "next_plan_check"}, facts
    if kind == "fix_rerun_qa":
        return _rerun_qa(settings, p["item_id"], facts)
    if kind == "fix_resend_daily_log":
        from service import digest, notify

        path = _daily_log(settings)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            raise FixRefused(["no_daily_log"], facts) from None
        if not notify.send(text, settings=settings, urgent=True, filename=digest.LATEST_NAME, now=now):
            raise FixRefused(["send_failed"], facts)
        return {"sent": True, "log_mtime": facts.get("log_mtime")}, facts
    if kind == "fix_retry_notify":
        return _retry_notices(settings, now, facts)
    raise FixRefused(["not_applicable"], facts)


def _rerun_qa(settings: dict, item_id: str, facts: dict) -> tuple[dict, dict]:
    import qa
    from service.lock import LockTimeout, service_lock

    try:
        with service_lock(_paths(settings).lock, 0):
            report = qa.run(_item_dir(settings, item_id), settings=settings)
    except LockTimeout:
        raise FixRefused(["service_busy"], facts) from None
    failed = [c for c in report.get("checks") or [] if not c.get("passed") and c.get("severity") == "error"]
    return {"item_id": item_id, "passed": bool(report.get("passed")), "checks_failed": len(failed)}, facts


def _retry_notices(settings: dict, now: datetime, facts: dict) -> tuple[dict, dict]:
    from service import notify, orders

    limit = _int(config(settings).get("notify_max"), 5, 1, 50)
    sent, failed = [], 0
    for o in unsent(settings, now)[-limit:]:
        text = notice_text(o)
        ok = notify.send(text, settings=settings, kinds=[o.get("notice_kind") or "order_approved"], urgent=True,
                         now=now)
        if ok:
            fresh = orders.load(o["id"], settings) or o
            fresh["announced"] = True
            fresh["announced_retry_at"] = _iso(now)
            orders._save(settings, fresh)
            sent.append(o["id"])
        else:
            failed += 1
    if not sent:
        raise FixRefused(["send_failed"], {**facts, "failed": failed})
    return {"resent": sent, "failed": failed}, facts


def notice_text(o: dict) -> str:
    """The Discord text of an order's approval or fix notice (fixed words, ids and numbers)."""
    from service import orders

    if o.get("kind") in KINDS:
        return post_text(o, o.get("result") or {}, (o.get("checks") or {}).get("facts") or {})
    by = (o.get("approved") or {}).get("by") or o.get("created_by") or "user"
    return orders.approval_text(o, by, o.get("result") or {})


# ---------------------------------------------------------------- texts and events

_FACT_LABELS = {
    "tick_started": "failed check started", "tick_exit_code": "exit code", "error_classes": "errors",
    "paid_work_block": "paid work stopped", "lock": "lock", "pid": "PID", "pid_alive": "process alive",
    "age_s": "age (s)", "min_age_s": "minimum age (s)", "flock_free": "free", "item_id": "item",
    "crashed_checks": "crashed checks", "failed_checks": "failed checks", "age_h": "age (h)",
    "unsent": "unsent notices",
}


def post_text(o: dict, result: dict, facts: dict) -> str:
    """The Discord post for an applied fix: which fix, who asked, the facts Botsy checked."""
    from service import orders

    kind = o.get("kind")
    by = o.get("created_by")
    what = {
        "fix_retry_run": "Retry of the failed check queued; it runs at the next plan check (hourly at :05)",
        "fix_clear_stale_lock": f"Stale {result.get('cleared') or 'the'} lock note cleared (its process was gone)",
        "fix_rerun_qa": (f"QA rerun for {result.get('item_id')}: "
                         + ("passed, back in the review queue" if result.get("passed")
                            else f"failed {result.get('checks_failed', 0)} check(s)")),
        "fix_resend_daily_log": "Daily log sent again",
        "fix_retry_notify": f"{len(result.get('resent') or [])} approval/fix notice(s) posted again",
    }.get(kind, f"Fix {kind} done")
    shown = [f"{_FACT_LABELS[k]}: {v}" for k, v in facts.items() if k in _FACT_LABELS and v not in (None, [], "")]
    return (f"{what} ({orders._asked(by)}, order {o.get('id')}).\n"
            + ("Checked: " + "; ".join(shown) + "." if shown else "Checked by Botsy before it ran."))


def safe_facts(facts: dict) -> dict:
    """Facts for the journal: numbers, booleans, short codes, ids and ISO times only."""
    from service import orders

    out = {}
    for k, v in (facts or {}).items():
        if not isinstance(k, str) or not orders.REASON_RE.fullmatch(k):
            continue
        if v is None or isinstance(v, (bool, int, float)):
            out[k] = v
        elif isinstance(v, str) and len(v) <= 160 and re.fullmatch(r"[A-Za-z0-9_:+.\-]+", v):
            out[k] = v
        elif isinstance(v, list):
            out[k] = [x for x in v if isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_]{1,60}", x)][:10]
    return out


# ---------------------------------------------------------------- the queued retry (plan check)

def run_pending(settings: dict, *, now: datetime | None = None, run_check=None) -> dict:
    """Run the oldest queued run retry (service/plan.py check calls this every hour): its
    preconditions are checked again first (the failed check is still the last one, every error
    retryable, paid work not stopped); then a forced check runs under the main lock, waiting at
    most fixes.lock_wait_s. A busy lock leaves it queued (fixes.max_tries in all). Never raises."""
    from service import notify
    from service.lock import LockTimeout, service_lock

    now = _utc(now)
    try:
        pending = _pending(settings)
    except Exception as e:  # noqa: BLE001
        log.warning("fix requests not read: %s", type(e).__name__)
        return {"ran": False, "reason": "unreadable"}
    if not pending:
        return {"ran": False, "reason": "none"}
    path = pending[0]
    try:
        req = read_json(path)
    except (OSError, ValueError):
        req = None
    if not isinstance(req, dict) or req.get("schema") != REQUEST_SCHEMA:
        path.unlink(missing_ok=True)
        return {"ran": False, "reason": "bad_request"}
    oid = str(req.get("order_id") or path.stem)
    reasons, facts = _retry_facts(settings, {"tick_started": req.get("tick_started")}, now, queued=path.stem)
    result: dict = {"ran": False, "order_id": oid}
    if reasons:
        path.unlink(missing_ok=True)
        result.update(reason=reasons[0], reasons=reasons)
    else:
        wait = _int(config(settings).get("lock_wait_s"), 120, 0, 3600)
        try:
            with service_lock(_paths(settings).lock, wait):
                from service import tick as tick_mod

                rec = run_check(settings) if run_check else tick_mod.tick(settings, force=True)
            code = rec.get("exit_code") if isinstance(rec, dict) else None
            result.update(ran=True, exit_code=code if isinstance(code, int) else None)
            path.unlink(missing_ok=True)
        except LockTimeout:
            tries = _int(req.get("tries"), 0) + 1
            if tries >= _int(config(settings).get("max_tries"), 3, 1, 24):
                path.unlink(missing_ok=True)
                result.update(reason="service_busy", reasons=["service_busy"])
            else:
                req["tries"] = tries
                write_json(path, req)
                return {"ran": False, "reason": "busy", "order_id": oid}
        except Exception as e:  # noqa: BLE001 - recorded; the daily check still runs
            log.warning("queued retry %s failed: %s", oid, type(e).__name__)
            path.unlink(missing_ok=True)
            result.update(ran=True, exit_code=None, reason="apply_failed")
    events.emit("fix_run", settings=settings, order_id=oid, fix="fix_retry_run", ran=result["ran"],
                exit_code=result.get("exit_code"), reasons=result.get("reasons") or [], facts=safe_facts(facts))
    if result["ran"]:
        ok = result.get("exit_code") == 0
        text = (f"Retried check (order {oid}) " + ("finished fine." if ok else
                f"ended with exit code {result.get('exit_code')}; see outbox/status.txt."))
    else:
        text = f"Queued retry (order {oid}) not run: {DESCRIBE.get(result.get('reason'), result.get('reason'))}"
    try:
        notify.send(text, settings=settings, kinds=["fix_run"], now=now)
    except Exception:  # noqa: BLE001
        pass
    return result


def describe(reason: str) -> str | None:
    return DESCRIBE.get(reason)


__all__ = ["DESCRIBE", "FixRefused", "KINDS", "LOCKS", "REASONS", "RECIPES", "RETRYABLE", "SETTINGS_DEFAULTS",
           "check", "config", "describe", "error_class", "notice_text", "perform", "post_text", "run_pending",
           "safe_facts", "unsent"]
