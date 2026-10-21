"""The Sunday plan (docs/OVERSEER.md "Scale-ups ... and the Sunday plan", Part B).

    python -m service plan prepare|check|status|approve|hold|start-now|again|auto [--force]

    prepare(settings=None, *, now=None, force=False, session=None) -> dict      (botsy-plan.timer)
    check(settings=None, *, now=None, run_check=None) -> dict                   (botsy-plan-check.timer)
    approve / hold / start_now / again / auto(settings=None, *, by="user", now=None, ...) -> dict
    submit_order(order, settings=None, *, now=None) -> dict        (Hermes's weekly_plan / plan_revision)
    for_tick(settings=None, *, now=None) -> dict ; record_run(settings, tick_plan, summaries, *, now=None)
    summary(settings=None, *, now=None) -> dict                    (status.txt and the daily log)

Every week has at most one plan (an order/v1 weekly_plan in data/orders/, service/orders.py).
This module is the clock around it. Hermes is optional: Botsy writes its own deterministic
draft plan, and you approve, hold or start it from the server (`botsy service plan approve`);
once Hermes is installed it only calls botsy-control, which hands its plans to submit_order().

1. prepare, Sunday 20:00 New York time (schedule.plan_post): reads Botsy's records only and
   never takes the main lock, so it never waits for a check that is running. It writes
   data/plans/<week>/facts.json (money left, the capacity check, the current briefs with their
   pack sizes and opportunity, KDP slots, last week's results) and Botsy's own draft,
   data/plans/<week>/plan.json: the briefs with the best opportunity across the niches (every
   niche's best brief first, then the rest by opportunity), only the pack sizes not made yet and
   not blocked by the theme cooldown, briefs at most plan.brief_max_age_days (7) old when the
   plan starts, at most plan.max_packs (12) packs, and an estimated cost that fits this week's
   share of the money left (the money left divided by the plan weeks left in that month, at
   most schedule.auto_start_max_cost_share of the cap, so the draft can start by itself). The
   draft becomes the week's weekly_plan order (created_by "botsy"), and a signed `plan_due`
   event goes to Hermes (data/plans/<week>/plan_due.json, and plan.hermes_events_url on
   127.0.0.1 when set). A run more than schedule.stale_skip_hours (24) late, after the server
   was off, is skipped: "this week's plan was missed" (normal rules that week).
2. A Hermes weekly_plan for a week that holds Botsy's draft becomes a plan_revision of it, and
   any plan goes through orders.validate with Botsy's own checks of its briefs (the brief exists,
   is at most 7 days old when the plan starts, has those pack sizes and no blocked theme), Botsy's
   own cost estimate and your approval count. At most schedule.max_revisions (3) revisions; a
   revision that starts by itself starts no sooner than schedule.revision_min_hours (2) after
   it, and never before Monday 08:00 (service/orders.py plan_start_at).
3. No codes (user, 2026-10-21: "I don't want the codes"). The first
   schedule.approvals_before_auto (4) plans wait for your plain approval: `botsy service plan
   approve` on the server, or Hermes (your allowlisted Discord user only) through
   botsy-control. Reminders Monday 08:00 and 19:00, expiry Tuesday 20:00 (the order is
   cancelled; normal rules for the rest of the week). Hold is always allowed; the deadline
   still applies.
4. From plan 5 a plan starts by itself on Monday 08:00 unless held (the 08:05 check starts it).
   Start now and approving a held plan are Level 2. A plan above the auto-start share of the
   cap (40%, $10 of $25) still waits for your approval. `plan again` goes back to approving
   every plan (any time; a plan not started yet then waits for your approval too). `plan auto`
   switches back to automatic, or goes automatic before 4 approvals.
   These Level 3 approvals (approving a plan that waits for you, going automatic) keep these
   guards: Hermes's count toward limits level3_per_day (10) in 24 hours (one counter shared
   with service/orders.py; yours are not counted, D1: only your approvals that lead to posting
   on Etsy or KDP are), each
   one posted at once to the main Discord channel (urgent: quiet hours do not hold it) with
   what changed, who asked (user | hermes) and how to undo it, and journaled with fixed fields
   (plan_approved, plan_mode_changed).
5. check, hourly at :05 (no paid calls of its own): reminders, the deadline, the automatic
   start, and the Sunday 21:00 "No plan this week" notice. It takes the main lock only to run a
   started plan's first check (also after approve and start-now: work begins that night) and a
   watchdog retry Hermes queued (service/fixes.py run_pending: at most one an hour, its
   preconditions checked again).
6. Holds (user, 2026-10-21, D3): a Hold records who set it (user | hermes | botsy). Your Hold
   sticks until you lift it: Hermes's Start now, approve or revision of a plan you hold is
   refused (held_by_user), the Monday automatic start never lifts it, and a revision or a new
   plan of that week inherits it. A Hold Hermes set can be lifted by either of you.

State: data/plans/state.json (plan.dir): approved_count, mode (approve | auto | null =
default), and per week the order, status (waiting | running | held | expired | done |
cancelled | none | missed), the deadlines and which messages went out. The order files
stay the truth; the state is synced from them on every run.

What the tick makes (service/tick.py, docs/OVERSEER.md B5): a running plan -> only its lines
(pipeline plan_lines: at most max_items new packs a run, plus QA, listing and drafts of its
items); a plan waiting for your approval or on hold -> only last week's plan lines; no plan, an
expired one, one more than plan.stale_days (8) old, or plan.enabled false -> the normal rules.
Uploads still run ingest and research at once; the new briefs wait for the next plan.

Messages go through service/notify.py with the kinds plan_posted, plan_reminder,
plan_expired, plan_started and no_plan (quiet hours apply), and plan_approved and
plan_mode_changed (urgent). Every message and event holds only our own data: brief ids,
theme names from our own briefs, counts, money and reason codes.

service/orders.py is the one place that decides whether a plan order waits for you (order/v1
needs_approval, status waiting_approval, checks.approval_reason). This module approves through
orders.approve(order, settings, by=, now=), which checks the order's state, the deadline, the
hold, the money and the daily limit again under its own lock and counts the approval.

While pytest runs, a plans folder inside the repo is refused (RuntimeError), like orders.
"""
from __future__ import annotations

import calendar
import copy
import json
import logging
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from core import events
from core.config import ROOT, SCHEDULE_DEFAULTS, load_settings, now_local, resolve, section, tz
from core.items import read_json, write_json

log = logging.getLogger("service.plan")

STATE_SCHEMA = "plan-state/v1"
FACTS_SCHEMA = "plan-facts/v1"
DRAFT_SCHEMA = "plan-draft/v1"
EVENT_SCHEMA = "plan-event/v1"

SETTINGS_DEFAULTS: dict = {
    "enabled": True,                 # false: no plans at all; every check runs on the normal rules
    "dir": "data/plans",             # state.json and data/plans/<week>/ (facts, draft, plan_due)
    "max_packs": 12,                 # packs in Botsy's own draft plan
    "brief_max_age_days": 7,         # a plan line whose brief is older than this is dropped
    "stale_days": 8,                 # a plan older than this no longer steers the checks
    "no_plan_after_minutes": 60,     # "No plan this week" this long after schedule.plan_post (21:00)
    "max_cost_usd": None,            # an extra ceiling for Botsy's draft (null: none)
    "first_check_lock_wait_s": 120,  # how long a plan start waits for a running check before the next try
    "hermes_events_url": None,       # Hermes's local webhook route for plan_due (http://127.0.0.1:PORT/...)
    "notify": True,                  # post the plan messages to Discord (service/notify.py)
}
ACTIONS = ("prepare", "check", "status", "approve", "hold", "start-now", "again", "auto")
STATUSES = ("waiting", "running", "held", "expired", "done", "cancelled", "none", "missed")
LIVE = ("waiting", "running", "held", "done")          # a plan in force
MODES = ("approve", "auto")
MESSAGE_KINDS = ("plan_posted", "plan_reminder", "plan_expired", "plan_started", "no_plan")
URGENT_KINDS = ("plan_approved", "plan_mode_changed")  # Level 3 approvals: posted at once, any hour
EVENT_KINDS = MESSAGE_KINDS + URGENT_KINDS + ("plan_due", "plan_missed", "plan_line_dropped", "plan_held",
                                              "plan_done")
BY = ("user", "hermes")                                 # who may ask (the server CLI / botsy-control)
# A plan order that waits for you (order/v1 status).
WAITING = ("waiting_approval",)
NEEDS_OK_REASONS = {"first_plans": "first_plans", "approve_each": "approve_each",
                    "plan_over_auto_share": "plan_over_auto_share", "limits_unavailable": "limits_unavailable",
                    "money_unknown": "money_unknown"}
# Botsy's own checks of a plan's lines (orders.validate stage "content").
CONTENT_REASONS = ("brief_unknown", "brief_too_old", "size_not_in_brief", "duplicate_line", "theme_blocked",
                   "brief_blocked", "kdp_not_built")
DROP_REASONS = ("brief_too_old", "brief_missing", "brief_changed", "size_not_in_brief", "not_this_shop",
                "duplicate_line", "bad_line")
_BRIEF_ID = re.compile(r"(?P<niche>[a-z][a-z0-9_]{0,40})-(?P<date>\d{4}-\d{2}-\d{2})-\d{1,4}")
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


# ---------------------------------------------------------------- settings, paths, time

def _settings(settings: dict | None) -> dict:
    return settings if settings is not None else load_settings()


def config(settings: dict | None = None) -> dict:
    return section(_settings(settings), "plan", SETTINGS_DEFAULTS)


def _schedule(settings: dict) -> dict:
    return section(settings, "schedule", SCHEDULE_DEFAULTS)


def enabled(settings: dict | None = None) -> bool:
    """plan.enabled; only a YAML false switches plans off."""
    return config(settings).get("enabled") is not False


def _int(value, default: int, low: int = 0, high: int = 10**6) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return default
    return int(min(high, max(low, value)))


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def plans_dir(settings: dict | None = None) -> Path:
    return resolve(config(settings).get("dir") or SETTINGS_DEFAULTS["dir"])


def state_path(settings: dict | None = None) -> Path:
    return plans_dir(settings) / "state.json"


def week_dir(settings: dict, week: str) -> Path:
    from service.orders import WEEK_RE

    if not WEEK_RE.fullmatch(str(week)):
        raise ValueError("bad plan week")
    return plans_dir(settings) / week


def _guard(path: Path) -> None:
    """While pytest runs, the real data/plans inside the repo is off limits."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return
    raise RuntimeError(f"test run: point settings plan.dir into tmp_path, not {path}")


def _off_limits(settings: dict) -> bool:
    """While pytest runs, a plans folder inside the repo (the user's real data/plans) is neither
    read nor written: the tick then runs on the normal rules, as if no plan existed."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        plans_dir(settings).resolve().relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return False
    return True


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds") if t is not None else None


def _parse(ts) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _day_time(raw, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """"Mon 08:00" -> (ISO weekday, hour, minute); anything else gives `default`."""
    m = re.fullmatch(r"\s*([A-Za-z]{3})[A-Za-z]*\s+(\d{1,2}):(\d{2})\s*", str(raw or ""))
    if m and m.group(1).lower() in _DAYS and int(m.group(2)) < 24 and int(m.group(3)) < 60:
        return _DAYS.index(m.group(1).lower()) + 1, int(m.group(2)), int(m.group(3))
    return default


def _at(day: date, hour: int, minute: int, settings: dict) -> datetime:
    """The wall-clock time on `day` in Botsy's zone, as UTC (the zone handles summer time)."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz(settings)).astimezone(timezone.utc)


def week_monday(week: str) -> date:
    return date.fromisocalendar(int(week[:4]), int(week[6:]), 1)


def _week_of(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def _prev_week(week: str) -> str:
    return _week_of(week_monday(week) - timedelta(days=7))


def post_time(week: str, settings: dict) -> datetime:
    """When the plan for `week` is posted: the last schedule.plan_post (Sun 20:00) before that
    week's Monday, on Botsy's clock."""
    wd, h, m = _day_time(_schedule(settings).get("plan_post"), (7, 20, 0))
    monday = week_monday(week)
    day = monday - timedelta(days=(1 - wd) % 7 or 7)
    return _at(day, h, m, settings)


def _after(start: datetime, raw, default: tuple[int, int, int], settings: dict) -> datetime:
    """The first `raw` ("Mon 08:00") on Botsy's clock at or after `start`."""
    wd, h, m = _day_time(raw, default)
    local = now_local(settings, start)
    day = local.date() + timedelta(days=(wd - local.isoweekday()) % 7)
    t = _at(day, h, m, settings)
    return t if t >= start else _at(day + timedelta(days=7), h, m, settings)


def _before(end: datetime, raw, default: tuple[int, int, int], settings: dict) -> datetime:
    """The last `raw` ("Sun 19:00") on Botsy's clock strictly before `end`, worked out on local
    dates (subtracting 7 days in UTC would land an hour off across a change of clocks)."""
    wd, h, m = _day_time(raw, default)
    local = now_local(settings, end)
    day = local.date() - timedelta(days=(local.isoweekday() - wd) % 7)
    t = _at(day, h, m, settings)
    return t if t < end else _at(day - timedelta(days=7), h, m, settings)


def schedule_for(week: str, settings: dict | None = None) -> dict:
    """The plan week's times (UTC datetimes): post, no_plan_at, reminders, auto_start, deadline,
    upload_cutoff, end (next Monday 00:00)."""
    settings = _settings(settings)
    sch = _schedule(settings)
    post = post_time(week, settings)
    reminders = sch.get("reminders")
    reminders = reminders if isinstance(reminders, (list, tuple)) else SCHEDULE_DEFAULTS["reminders"]
    after = _int(config(settings).get("no_plan_after_minutes"), 60, 0, 24 * 60)
    end_day = week_monday(week) + timedelta(days=7)
    return {"post": post, "no_plan_at": post + timedelta(minutes=after),
            "reminders": sorted(_after(post, r, (1, 8, 0), settings) for r in reminders),
            "auto_start": _after(post, sch.get("auto_start"), (1, 8, 0), settings),
            "deadline": _after(post, sch.get("deadline"), (2, 20, 0), settings),
            "upload_cutoff": _before(post, sch.get("upload_cutoff"), (7, 19, 0), settings),
            "end": _at(end_day, 0, 0, settings)}


def scheduled_post(settings: dict, now: datetime) -> tuple[str, datetime]:
    """(plan week, its post time) for the last schedule.plan_post at or before `now`."""
    week = _week_of(now_local(settings, now).date() + timedelta(days=7))
    for _ in range(3):
        post = post_time(week, settings)
        if post <= now:
            return week, post
        week = _prev_week(week)
    return week, post_time(week, settings)


def _when(t: datetime | None, settings: dict) -> str:
    if t is None:
        return "?"
    local = now_local(settings, t)
    return local.strftime("%a %d %b %H:%M")


def zone_name(settings: dict) -> str:
    key = getattr(tz(settings), "key", "") or ""
    return key.rsplit("/", 1)[-1].replace("_", " ") + " time" if "/" in key else f"{key} time"


def _local_day(settings: dict, now: datetime) -> date:
    return now_local(settings, now).date()


# ---------------------------------------------------------------- state

def _lock(settings: dict):
    from service.lock import service_lock

    d = plans_dir(settings)
    _guard(d)
    d.mkdir(parents=True, exist_ok=True)
    return service_lock(d / ".lock", 30)


def _empty_state() -> dict:
    return {"schema": STATE_SCHEMA, "approved_count": 0, "mode": None, "plans": {}}


def load_state(settings: dict | None = None) -> dict:
    """data/plans/state.json, or an empty state (a damaged file is kept aside as .bad)."""
    settings = _settings(settings)
    p = state_path(settings)
    if _off_limits(settings):
        return _empty_state()
    try:
        doc = read_json(p)
    except FileNotFoundError:
        return _empty_state()
    except (OSError, ValueError) as e:
        log.warning("plan state %s unreadable (%s); starting from an empty state (every plan waits for your "
                    "approval again)", p, type(e).__name__)
        if isinstance(e, ValueError) and not _off_limits(settings):
            try:                                   # keep the damaged file for a look
                bad = p.with_name(p.name + ".bad")
                if not bad.exists():
                    bad.write_bytes(p.read_bytes())
            except OSError:
                pass
        return _empty_state()
    if not isinstance(doc, dict) or doc.get("schema") != STATE_SCHEMA:
        return _empty_state()
    out = _empty_state()
    out.update({k: v for k, v in doc.items() if k in ("approved_count", "mode", "plans", "counted_weeks")})
    if not isinstance(out["plans"], dict):
        out["plans"] = {}
    out["approved_count"] = _int(out.get("approved_count"), 0)
    if out.get("mode") not in MODES:
        out["mode"] = None
    return out


def _save_state(settings: dict, st: dict) -> None:
    p = state_path(settings)
    _guard(p)
    # Keep the newest 12 weeks: older plans no longer steer anything.
    weeks = sorted(st.get("plans") or {})
    st["plans"] = {w: st["plans"][w] for w in weeks[-12:]}
    write_json(p, {**st, "schema": STATE_SCHEMA})


def approvals_needed(settings: dict) -> int:
    return _int(_schedule(settings).get("approvals_before_auto"), 4, 0, 100)


def effective_mode(st: dict, settings: dict) -> str:
    """"approve" (every plan waits for your approval) or "auto" (plans start by themselves)."""
    if st.get("mode") in MODES:
        return st["mode"]
    return "approve" if st.get("approved_count", 0) < approvals_needed(settings) else "auto"


def order_context(st: dict, settings: dict) -> dict:
    """orders.validate's context: your approval count (raised to the threshold after `plan
    auto`) and whether you asked to approve every plan (`plan again`)."""
    approved = _int(st.get("approved_count"), 0)
    if st.get("mode") == "auto":
        approved = max(approved, approvals_needed(settings))
    return {"approved_plans": approved, "approve_each": st.get("mode") == "approve"}


def _entry(st: dict, week: str, settings: dict) -> dict:
    plans = st.setdefault("plans", {})
    e = plans.get(week)
    if not isinstance(e, dict):
        times = schedule_for(week, settings)
        e = plans[week] = {"week": week, "status": "none", "order_id": None, "notices": {}, "reminders_sent": [],
                           "themes": {}, "dropped": {},
                           "times": {"post": _iso(times["post"]), "no_plan_at": _iso(times["no_plan_at"]),
                                     "reminders": [_iso(t) for t in times["reminders"]],
                                     "auto_start": _iso(times["auto_start"]), "deadline": _iso(times["deadline"])}}
    for key, empty in (("notices", {}), ("reminders_sent", []), ("themes", {}), ("dropped", {}), ("times", {})):
        if not isinstance(e.get(key), type(empty)):
            e[key] = copy.deepcopy(empty)
    return e


def _times(entry: dict, week: str, settings: dict) -> dict:
    """The entry's stored times (a late plan keeps a later deadline), else the schedule's."""
    times = schedule_for(week, settings)
    stored = entry.get("times") or {}
    for key in ("post", "no_plan_at", "auto_start", "deadline"):
        t = _parse(stored.get(key))
        if t is not None:
            times[key] = t
    if isinstance(stored.get("reminders"), list):      # a late plan may keep none
        times["reminders"] = sorted(t for t in (_parse(x) for x in stored["reminders"]) if t is not None)
    return times


def deadline_for(week: str, settings: dict | None = None) -> datetime:
    """The deadline of `week`'s plan (UTC): the one stored for it (a late plan made with
    --force keeps a later one), else schedule.deadline (Tuesday 20:00). After it a plan that
    waits for your approval may not start, whoever approves it (botsy-control too)."""
    settings = _settings(settings)
    entry = (load_state(settings).get("plans") or {}).get(week)
    stored = _parse(((entry or {}).get("times") or {}).get("deadline")) if isinstance(entry, dict) else None
    return stored or schedule_for(week, settings)["deadline"]


# ---------------------------------------------------------------- briefs

def _briefs_dir(settings: dict) -> Path:
    from service.paths import RESEARCH_PATH_DEFAULTS

    research = section(settings, "research", RESEARCH_PATH_DEFAULTS)
    return resolve(research.get("briefs_dir") or RESEARCH_PATH_DEFAULTS["briefs_dir"])


def _work_root(settings: dict) -> Path:
    from core.items import dry_run_root, output_root
    from service.paths import is_live

    return output_root(settings) if is_live(settings) else dry_run_root(settings)


def _shop_of(settings: dict) -> dict[str, str]:
    """niche -> the first shop that has it (a plan line names a niche, not a shop)."""
    out: dict[str, str] = {}
    for shop in settings.get("shops") or []:
        for niche in shop.get("niches") or []:
            out.setdefault(niche, shop.get("id"))
    return out


def brief_date(brief_id: str) -> date | None:
    m = _BRIEF_ID.fullmatch(str(brief_id or ""))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group("date"))
    except ValueError:
        return None


def brief_file(settings: dict, niche: str, brief_id: str) -> Path | None:
    """The brief file a brief id comes from: <briefs_dir>/<niche>-<date>.json."""
    d = brief_date(brief_id)
    if d is None or not str(brief_id).startswith(f"{niche}-"):
        return None
    return _briefs_dir(settings) / f"{niche}-{d.isoformat()}.json"


def _blocklist(settings: dict):
    from core.compliance import Blocklist

    comp = settings.get("compliance") or {}
    return Blocklist.load(comp.get("trademark_blocklist") or None, health_path=comp.get("health_terms") or None)


class _Briefs:
    """Brief files read once (validated like the pipeline does), looked up by brief id."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.blocklist = _blocklist(settings)
        self.docs: dict[Path, tuple[dict | None, str | None]] = {}

    def doc(self, path: Path) -> tuple[dict | None, str | None]:
        if path not in self.docs:
            from core.briefs import BriefComplianceError, validate_brief_doc

            try:
                doc = read_json(path)
                if not isinstance(doc, dict):
                    raise ValueError("not an object")
                validate_brief_doc(doc, [], [], blocklist=self.blocklist)
                self.docs[path] = (doc, None)
            except FileNotFoundError:
                self.docs[path] = (None, "brief_unknown")
            except BriefComplianceError:
                self.docs[path] = (None, "brief_blocked")
            except (OSError, ValueError):
                self.docs[path] = (None, "brief_unknown")
        return self.docs[path]

    def find(self, niche: str, brief_id: str) -> tuple[Path | None, dict | None, str | None]:
        path = brief_file(self.settings, niche, brief_id)
        if path is None:
            return None, None, "brief_unknown"
        doc, why = self.doc(path)
        if doc is None:
            return path, None, why
        brief = next((b for b in doc.get("briefs") or [] if isinstance(b, dict) and b.get("brief_id") == brief_id),
                     None)
        if brief is None or brief.get("niche", niche) != niche:
            return path, None, "brief_unknown"
        return path, brief, None


def _sizes(brief: dict, settings: dict) -> list[int]:
    from generators.packs.plan import brief_pack_sizes

    try:
        return list(brief_pack_sizes(brief, settings))
    except (ValueError, TypeError, ImportError):
        return []


def _opportunity(brief: dict) -> float:
    ev = brief.get("evidence") if isinstance(brief.get("evidence"), dict) else {}
    for key in ("opportunity", "score"):
        if _num(ev.get(key)):
            return float(ev[key])
    return 0.0


def _indexes(settings: dict) -> dict:
    from pipeline.run import PackIndex

    root = _work_root(settings)
    return {shop: PackIndex(root, shop_id=shop, settings=settings) for shop in set(_shop_of(settings).values())}


def current_briefs(settings: dict, *, start_day: date, indexes: dict | None = None) -> list[dict]:
    """The newest brief file of every shop niche, one row per brief: brief_id, niche, theme,
    sizes, opportunity, brief_date, age_days (on `start_day`), needs_review, and per size
    whether it is new, made or blocked by the theme cooldown. Only our own aggregated briefs."""
    from pipeline.run import latest_briefs

    niches = list(_shop_of(settings))
    indexes = indexes if indexes is not None else _indexes(settings)
    reader = _Briefs(settings)
    rows = []
    for path in latest_briefs(_briefs_dir(settings), niches):
        doc, why = reader.doc(path)
        if doc is None:
            continue
        for pos, b in enumerate(doc.get("briefs") or []):
            if not isinstance(b, dict):
                continue
            bid, niche = str(b.get("brief_id") or ""), str(b.get("niche") or doc.get("niche") or "")
            made_on = brief_date(bid)
            if made_on is None or niche not in niches or not bid.startswith(f"{niche}-"):
                continue
            sizes = _sizes(b, settings)
            idx = indexes.get(_shop_of(settings)[niche])
            packs = {}
            for s in sizes:
                state, item = idx.state(b, s, start_day.isoformat(), all_sizes=sizes) if idx else ("new", None)
                packs[str(s)] = {"state": state, "item_id": item}
            rows.append({"brief_id": bid, "niche": niche, "theme": str(b.get("theme") or "").strip(),
                         "sizes": sizes, "opportunity": round(_opportunity(b), 4), "brief_date": made_on.isoformat(),
                         "age_days": (start_day - made_on).days, "needs_review": bool(b.get("needs_review")),
                         "position": pos, "file": str(path), "packs": packs})
    return rows


def content_check(settings: dict, *, now: datetime):
    """orders.validate's content check for a plan: Botsy's own look at each line's brief."""
    def check(order: dict) -> list[str]:
        p = order.get("payload") or {}
        if p.get("kdp"):
            return ["kdp_not_built"]
        start = schedule_for(p.get("plan_week"), settings)["auto_start"] if p.get("plan_week") else now
        day = _local_day(settings, max(now, start))
        max_age = _int(config(settings).get("brief_max_age_days"), 7, 0, 365)
        reader = _Briefs(settings)
        bl = reader.blocklist
        reasons, seen = [], set()
        for line in p.get("lines") or []:
            bid, niche = line.get("brief_id"), line.get("niche")
            if bid in seen:
                reasons.append("duplicate_line")
                continue
            seen.add(bid)
            _, brief, why = reader.find(niche, bid)
            if brief is None:
                reasons.append(why or "brief_unknown")
                continue
            made_on = brief_date(bid)
            if made_on is None or (day - made_on).days > max_age:
                reasons.append("brief_too_old")
            if not set(line.get("sizes") or []) <= set(_sizes(brief, settings)):
                reasons.append("size_not_in_brief")
            if bl.check(str(brief.get("theme") or "")).blocked:
                reasons.append("theme_blocked")
        return list(dict.fromkeys(reasons))
    return check


def _themes(settings: dict, order: dict) -> dict[str, str]:
    reader = _Briefs(settings)
    out = {}
    for line in (order.get("payload") or {}).get("lines") or []:
        _, brief, _ = reader.find(line.get("niche"), line.get("brief_id"))
        if brief is not None:
            out[line["brief_id"]] = str(brief.get("theme") or "").strip()
    return out


# ---------------------------------------------------------------- money and the draft

def auto_share(settings: dict, lim=None) -> float:
    from service import limits as limits_mod

    raw = _schedule(settings).get("auto_start_max_cost_share", 0.40)
    share = float(raw) if _num(raw) and 0 <= raw <= 1 else 0.40
    lim = lim if lim is not None else limits_mod.load(settings)
    if lim.ok:
        share = min(share, float(lim.get("plan.max_cost_share_auto", 0.40)))
    return share


def budget(settings: dict, *, start: datetime, now: datetime, lim=None) -> dict:
    """This week's money for Botsy's draft: {"month", "spent", "cap", "money_left", "weeks_left",
    "budget_usd"} for the money month the plan starts in (UTC). budget_usd None = no cap."""
    from service import capacity

    when = max(now, start)
    m = capacity.money(settings, now=when)
    out = {"month": m["month"], "spent": m["spent"], "cap": m["cap"], "money_left": m["money_left"],
           "weeks_left": None, "budget_usd": None, "auto_share": auto_share(settings, lim)}
    if m["cap"] is None:
        extra = config(settings).get("max_cost_usd")
        out["budget_usd"] = round(float(extra), 2) if _num(extra) and extra >= 0 else None
        return out
    days = calendar.monthrange(when.year, when.month)[1]
    weeks_left = max(1, math.ceil((days - when.day + 1) / 7))
    left = max(0.0, float(m["money_left"]))
    b = min(left / weeks_left, out["auto_share"] * float(m["cap"]))
    extra = config(settings).get("max_cost_usd")
    if _num(extra) and extra >= 0:
        b = min(b, float(extra))
    out.update({"weeks_left": weeks_left, "budget_usd": round(max(0.0, b), 2)})
    return out


def build_draft(settings: dict, week: str, *, now: datetime, rows: list[dict], money: dict) -> dict:
    """Botsy's deterministic draft (see the module docstring). Returns {"lines", "skipped",
    "packs", "cost_usd", "budget_usd"}; lines are order/v1 plan lines plus theme and opportunity."""
    from service.orders import estimate_cost

    cfg = config(settings)
    max_packs = _int(cfg.get("max_packs"), 12, 1, 200)
    max_age = _int(cfg.get("brief_max_age_days"), 7, 0, 365)
    niches = list(_shop_of(settings))
    cap_usd = money.get("budget_usd")
    skipped, cands, themes_seen = [], [], set()
    for r in rows:
        new = [int(s) for s, p in r["packs"].items() if p["state"] == "new"]
        key = (r["niche"], r["theme"].lower())
        if r["age_days"] > max_age:
            skipped.append({"brief_id": r["brief_id"], "reason": "brief_too_old"})
        elif not r["sizes"]:
            skipped.append({"brief_id": r["brief_id"], "reason": "size_not_in_brief"})
        elif not new:
            states = {p["state"] for p in r["packs"].values()}
            skipped.append({"brief_id": r["brief_id"], "reason": "theme_cooldown" if "cooldown" in states
                            else "already_made"})
        elif key in themes_seen:
            skipped.append({"brief_id": r["brief_id"], "reason": "duplicate_theme"})
        else:
            themes_seen.add(key)
            cands.append({**r, "new": sorted(new)})
    rank = lambda c: (-c["opportunity"], niches.index(c["niche"]) if c["niche"] in niches else 99,  # noqa: E731
                      c["position"], c["brief_id"])
    best: dict[str, dict] = {}
    for c in sorted(cands, key=rank):
        best.setdefault(c["niche"], c)
    first = sorted(best.values(), key=rank)
    ordered = first + [c for c in sorted(cands, key=rank) if c not in first]
    lines, packs = [], 0
    for c in ordered:
        options = [c["new"]] + [[s] for s in c["new"]] if len(c["new"]) > 1 else [c["new"]]
        took = None
        for sizes in options:
            line = {"niche": c["niche"], "brief_id": c["brief_id"], "sizes": sizes}
            cost = estimate_cost({"lines": [*lines, line]})
            if packs + len(sizes) > max_packs or len(lines) >= 40:
                continue
            if cap_usd is not None and cost > cap_usd + 1e-9:
                continue
            took = line
            break
        if took is None:
            reason = "too_many_packs" if packs + min(1, len(c["new"])) > max_packs or len(lines) >= 40 else "over_budget"
            skipped.append({"brief_id": c["brief_id"], "reason": reason})
            continue
        lines.append(took)
        packs += len(took["sizes"])
    meta = {c["brief_id"]: c for c in cands}
    shown = [{**ln, "theme": meta[ln["brief_id"]]["theme"], "opportunity": meta[ln["brief_id"]]["opportunity"],
              "brief_date": meta[ln["brief_id"]]["brief_date"]} for ln in lines]
    return {"lines": shown, "skipped": skipped, "packs": packs, "cost_usd": estimate_cost({"lines": lines}),
            "budget_usd": cap_usd}


def _order_lines(lines: list[dict]) -> list[dict]:
    return [{"niche": ln["niche"], "brief_id": ln["brief_id"], "sizes": list(ln["sizes"])} for ln in lines]


# ---------------------------------------------------------------- messages and events

def _post(settings: dict, kind: str, text: str, *, now: datetime | None = None, urgent: bool = False,
          **fields) -> bool:
    """One plan message to Discord (service/notify.py) and its event in the journal. urgent:
    sent even in quiet hours, and even with plan.notify false (Level 3 approvals must
    always reach you at once)."""
    events.emit(kind, settings=settings, **fields)
    if not urgent and not config(settings).get("notify", True):
        return False
    from service import notify

    try:
        return bool(notify.send(text, settings=settings, kinds=[kind], urgent=urgent, now=now))
    except Exception as e:  # noqa: BLE001 - a message must never stop the plan service
        log.warning("plan message not sent: %s", type(e).__name__)
        return False


def _money(x) -> str:
    return f"${float(x):,.2f}" if _num(x) else "$?"


def _niche(n) -> str:
    return str(n or "?").replace("_", " ")


def _sizes_text(sizes) -> str:
    sizes = [str(s) for s in sizes]
    return sizes[0] if len(sizes) == 1 else ", ".join(sizes[:-1]) + " and " + sizes[-1]


def _lines_text(order: dict, themes: dict) -> list[str]:
    out = []
    for line in (order.get("payload") or {}).get("lines") or []:
        theme = themes.get(line.get("brief_id")) or line.get("brief_id")
        out.append(f"  - {_niche(line.get('niche'))} '{theme}' in {_sizes_text(line.get('sizes') or [])} pages")
    return out


def _how_to(entry: dict, settings: dict, st: dict) -> list[str]:
    t = _times(entry, entry["week"], settings)
    zone = zone_name(settings)
    if entry.get("needs_ok"):
        n, need = _int(st.get("approved_count"), 0) + 1, approvals_needed(settings)
        why = {"first_plans": f"Plan {n} of the first {need}: it waits for your approval.",
               "plan_over_auto_share": "This plan costs more than the share of the cap that may start by itself, "
                                       "so it waits for your approval.",
               "approve_each": "You asked to approve every plan (plan again)."}.get(entry.get("ok_reason"),
                                                                                   "This plan waits for your approval.")
        return [why,
                "Approve: botsy service plan approve (or tell Hermes). Hold: botsy service plan hold.",
                f"If it is not approved by {_when(t['deadline'], settings)} {zone}, it expires and Botsy runs on "
                "its normal rules this week."]
    start = t["auto_start"]
    try:
        from service import orders

        o = orders.load(entry.get("order_id"), settings)
        start = orders.plan_start_at(o, settings) or start if o else start
    except Exception:  # noqa: BLE001 - only the wording depends on it
        pass
    return [f"It starts by itself on {_when(start, settings)} {zone} unless you hold it.",
            "Hold: botsy service plan hold. Start now: botsy service plan start-now."]


def _posted_text(settings: dict, entry: dict, order: dict, st: dict, *, draft: dict | None = None,
                 money: dict | None = None) -> str:
    packs = sum(len(line.get("sizes") or []) for line in (order.get("payload") or {}).get("lines") or [])
    who = "Botsy's draft" if order.get("created_by") == "botsy" else "from Hermes, checked by Botsy"
    cost = (order.get("checks") or {}).get("cost_usd")
    lines = [f"Weekly plan {entry['week']} ({who}): {packs} pack{'s' if packs != 1 else ''}, about {_money(cost)} "
             "(estimate)."]
    lines += _lines_text(order, entry.get("themes") or {})
    if money:
        cap = money.get("cap")
        lines.append(f"Money: {_money(money.get('spent'))} spent in {money.get('month')}"
                     + (f" of the {_money(cap)} cap." if cap is not None else " (no monthly cap set)."))
    if draft and draft.get("skipped"):
        lines.append(f"Briefs left out: {len(draft['skipped'])} (already made, theme cooldown, too old or over "
                     "this week's money; see data/plans/" + entry["week"] + "/plan.json).")
    lines += _how_to(entry, settings, st)
    return "\n".join(lines)


def _signed_due(settings: dict, week: str, entry: dict, order: dict | None, facts: dict, *, now: datetime,
                session=None) -> dict:
    """The signed plan_due event for Hermes: fixed fields only (ids, counts, money, reason codes)."""
    t = _times(entry, week, settings)
    body = {"schema": EVENT_SCHEMA, "kind": "plan_due", "ts": _iso(now), "plan_week": week,
            "draft_order_id": order.get("id") if order else None,
            "draft_status": order.get("status") if order else None,
            "needs_approval": _needs_ok(order) if order else None,
            "approval_reason": _ok_reason(order) if order else None,
            "packs": sum(len(ln.get("sizes") or []) for ln in ((order or {}).get("payload") or {}).get("lines") or []),
            "cost_usd": ((order or {}).get("checks") or {}).get("cost_usd"),
            "money": {k: (facts.get("money") or {}).get(k) for k in ("month", "spent", "cap", "money_left")},
            "capacity_reasons": list((facts.get("capacity") or {}).get("reasons") or []),
            "kdp_slots_left": (facts.get("kdp") or {}).get("slots_left"),
            "briefs": len(facts.get("briefs") or []),
            "facts_digest": _digest(facts), "deadline": _iso(t["deadline"]), "auto_start": _iso(t["auto_start"]),
            "max_revisions": _int(_schedule(settings).get("max_revisions"), 3, 0, 20),
            "reason": entry.get("reason")}
    signed = events.sign(body)
    write_json(week_dir(settings, week) / "plan_due.json", signed)
    events.emit("plan_due", settings=settings, plan_week=week, draft_order_id=body["draft_order_id"],
                packs=body["packs"], cost_usd=body["cost_usd"], signed="sig" in signed)
    _deliver(settings, signed, session=session)
    return signed


def _digest(obj) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
                          .encode("utf-8")).hexdigest()


def _loopback(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        u = urlparse(url)
    except ValueError:
        return False
    try:
        port = u.port
    except ValueError:
        return False
    # An address, not a name: /etc/hosts could point "localhost" anywhere. No user:password.
    return (u.scheme in ("http", "https") and (u.hostname or "") in ("127.0.0.1", "::1") and port is not None
            and not u.username and not u.password)


def _deliver(settings: dict, event: dict, *, session=None) -> bool:
    """POST the signed event to Hermes's local webhook route (plan.hermes_events_url, on
    127.0.0.1 only). Off when unset; never raises; never the network while pytest runs."""
    url = config(settings).get("hermes_events_url")
    if not url:
        return False
    if not isinstance(url, str) or not _loopback(url):
        log.warning("plan.hermes_events_url must be on 127.0.0.1; plan_due not sent")
        return False
    if session is None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        import requests

        session = requests.Session()
        session.trust_env = False          # never through a proxy from the environment
    try:
        # No redirects: the signed event goes to Hermes's local route and nowhere else.
        r = session.post(url, json=event, timeout=10, allow_redirects=False)
        return 200 <= int(getattr(r, "status_code", 0) or 0) < 300
    except Exception as e:  # noqa: BLE001
        log.warning("plan_due not delivered to Hermes: %s", type(e).__name__)
        return False


# ---------------------------------------------------------------- sync with the orders

def _order_packs(order: dict) -> int:
    return sum(len(line.get("sizes") or []) for line in (order.get("payload") or {}).get("lines") or [])


def progress(settings: dict, entry: dict, order: dict | None, *, now: datetime, indexes=None) -> dict:
    """{"packs_total", "packs_made", "packs_skipped", "packs_left", "lines_total", "lines_done",
    "lines_dropped"} for the plan's lines in the output folder Botsy works on."""
    out = {"packs_total": 0, "packs_made": 0, "packs_skipped": 0, "packs_left": 0, "lines_total": 0,
           "lines_done": 0, "lines_dropped": 0}
    if not order:
        return out
    indexes = indexes if indexes is not None else _indexes(settings)
    shops = _shop_of(settings)
    day = _local_day(settings, now).isoformat()
    for line in (order.get("payload") or {}).get("lines") or []:
        bid, sizes = line.get("brief_id"), list(line.get("sizes") or [])
        out["lines_total"] += 1
        out["packs_total"] += len(sizes)
        brief = {"brief_id": bid, "niche": line.get("niche"), "theme": (entry.get("themes") or {}).get(bid, "")}
        idx = indexes.get(shops.get(line.get("niche")))
        made = skipped = 0
        for s in sizes:
            state = idx.state(brief, s, day, all_sizes=sizes)[0] if idx else "new"
            made += state == "made"
            skipped += state == "cooldown"
        dropped = bid in (entry.get("dropped") or {})
        out["packs_made"] += made
        out["packs_skipped"] += skipped
        left = len(sizes) - made - skipped
        out["lines_dropped"] += int(dropped and left > 0)
        if left <= 0 or dropped:
            out["lines_done"] += 1
        else:
            out["packs_left"] += left
    return out


def _sync(settings: dict, st: dict, week: str, entry: dict, *, now: datetime) -> dict | None:
    """Bring the entry in line with the week's order (Hermes may have revised, held or started
    it through botsy-control). Returns the order in force, or None."""
    from service import orders

    o = orders.current_plan(settings, week)
    if o is None:
        if entry.get("status") in LIVE:
            entry["status"] = "cancelled"
        return None
    if o.get("id") != entry.get("order_id"):
        entry["order_id"] = o["id"]
        entry["notices"].pop("start_failed", None)
    entry["created_by"] = o.get("created_by")
    entry["revision"] = o.get("revision")
    entry["needs_ok"] = _needs_ok(o)
    entry["ok_reason"] = _ok_reason(o)
    entry["cost_usd"] = (o.get("checks") or {}).get("cost_usd")
    entry["packs"] = _order_packs(o)
    themes = entry.setdefault("themes", {})
    missing = [ln for ln in (o.get("payload") or {}).get("lines") or [] if ln.get("brief_id") not in themes]
    if missing:
        themes.update({k: v for k, v in _themes(settings, o).items() if k not in themes})
    held = isinstance(o.get("held"), dict)
    entry["held_by"] = o["held"].get("by") if held else None
    if o.get("status") == "applied":
        if entry.get("started_order") != o["id"]:
            entry["started_order"] = o["id"]
            entry["started_at"] = o.get("applied_at") or _iso(now)
            entry["first_check"] = "pending"
            entry["first_check_tries"] = 0
            if not entry.get("counted"):
                entry["counted"] = True
                st["approved_count"] = _int(st.get("approved_count"), 0) + 1
            entry["notices"].setdefault("started_pending", _iso(now))
        if held:
            entry["status"] = "held"
        else:
            p = progress(settings, entry, o, now=now)
            entry["progress"] = p
            entry["status"] = "done" if p["lines_total"] and p["lines_done"] >= p["lines_total"] else "running"
    else:
        entry["status"] = "held" if held else "waiting"
    return o


def _governing(settings: dict, st: dict, now: datetime) -> tuple[str | None, dict | None, dict | None]:
    """(week, entry, order) of the newest plan in force among next week's and this week's."""
    from service import orders

    this, nxt = orders.plan_weeks(settings, now)
    for week in (nxt, this):
        e = (st.get("plans") or {}).get(week)
        if isinstance(e, dict):
            e = _entry(st, week, settings)
            o = _sync(settings, st, week, e, now=now)
            if e.get("status") in LIVE:
                return week, e, o
    return None, None, None


# ---------------------------------------------------------------- prepare

def _last_week(settings: dict, st: dict, week: str, *, now: datetime) -> dict:
    from service import orders

    prev = _prev_week(week)
    e = (st.get("plans") or {}).get(prev)
    if not isinstance(e, dict):
        return {"plan_week": prev, "status": "none"}
    e = _entry(st, prev, settings)
    o = orders.current_plan(settings, prev) or (orders.load(e.get("order_id"), settings) if e.get("order_id") else None)
    p = progress(settings, e, o, now=now) if o else {}
    return {"plan_week": prev, "status": e.get("status"), "packs_total": p.get("packs_total"),
            "packs_made": p.get("packs_made"), "lines_dropped": p.get("lines_dropped")}


def build_facts(settings: dict, week: str, *, now: datetime, st: dict) -> tuple[dict, list[dict], dict]:
    """(facts, brief rows, money) for the plan week: what Botsy and Hermes plan from."""
    from service import capacity, limits as limits_mod

    lim = limits_mod.load(settings)
    times = schedule_for(week, settings)
    start_day = _local_day(settings, max(now, times["auto_start"]))
    indexes = _indexes(settings)
    rows = current_briefs(settings, start_day=start_day, indexes=indexes)
    try:
        money = budget(settings, start=times["auto_start"], now=now, lim=lim)
    except (ValueError, OSError, TypeError, KeyError):
        money = {"month": None, "spent": None, "cap": None, "money_left": None, "budget_usd": None,
                 "weeks_left": None, "unknown": True}
    cap = capacity.check(settings, limits=lim, now=now)
    facts = {"schema": FACTS_SCHEMA, "plan_week": week, "made_at": _iso(now),
             "times": {k: _iso(v) for k, v in times.items() if isinstance(v, datetime)}
             | {"reminders": [_iso(t) for t in times["reminders"]]},
             "money": money,
             "capacity": {"ok": cap.get("ok"), "reasons": cap.get("reasons"),
                          "review_queue": (cap.get("facts") or {}).get("review_queue"),
                          "waiting_drafts": (cap.get("facts") or {}).get("waiting_drafts"),
                          "disk_free_mb": (cap.get("facts") or {}).get("disk_free_mb")},
             "limits": {"ok": lim.ok, "reason": lim.reason},
             "briefs": [{k: r[k] for k in ("brief_id", "niche", "theme", "sizes", "opportunity", "brief_date",
                                           "age_days", "needs_review", "packs")} for r in rows],
             "kdp": {"slots_left": (cap.get("facts") or {}).get("kdp_slots_left"), "built": False},
             "scale_ups": {"allowed": False, "reason": "no_winners_yet"},
             "last_week": _last_week(settings, st, week, now=now),
             "approvals": {"approved_count": _int(st.get("approved_count"), 0),
                           "approvals_before_auto": approvals_needed(settings), "mode": effective_mode(st, settings)},
             "max_packs": _int(config(settings).get("max_packs"), 12, 1, 200)}
    return facts, rows, money


def prepare(settings: dict | None = None, *, now: datetime | None = None, force: bool = False,
            session=None) -> dict:
    """The Sunday 20:00 run (see the module docstring). Returns {"ok", "week", "status",
    "order_id", "reason", "skipped", "message"}."""
    from service import orders

    settings = _settings(settings)
    now = _utc(now)
    if not enabled(settings):
        return {"ok": True, "week": None, "status": None, "skipped": "plans_disabled",
                "message": "Weekly plans are switched off (plan.enabled is false)."}
    week, post = scheduled_post(settings, now)
    late_h = (now - post).total_seconds() / 3600
    stale_h = _int(_schedule(settings).get("stale_skip_hours"), 24, 0, 24 * 14)
    messages: list[tuple[str, str, dict]] = []
    due = None
    with _lock(settings):
        st = load_state(settings)
        entry = _entry(st, week, settings)
        if entry.get("prepared_at") and not force:
            _sync(settings, st, week, entry, now=now)
            _save_state(settings, st)
            return {"ok": True, "week": week, "status": entry["status"], "order_id": entry.get("order_id"),
                    "skipped": "already_prepared", "message": f"The plan for {week} was already prepared."}
        if late_h > stale_h and not force:
            entry.update({"status": "missed", "reason": "server_was_off", "prepared_at": _iso(now)})
            _save_state(settings, st)
            messages.append(("plan_missed", f"This week's plan was missed: the server was off at "
                             f"{_when(post, settings)} {zone_name(settings)} (the plan run is {late_h:.0f} hours "
                             "late). Botsy runs on its normal rules this week. To make a plan anyway: "
                             "botsy service plan prepare --force",
                             {"plan_week": week, "hours_late": round(late_h, 1)}))
            out = {"ok": True, "week": week, "status": "missed", "order_id": None, "skipped": "missed",
                   "message": messages[-1][1]}
        else:
            # A late plan by hand (--force, and no plan in force yet): a fair deadline and Hold window.
            if force and late_h > stale_h and orders.current_plan(settings, week) is None:
                t = schedule_for(week, settings)
                entry["times"]["deadline"] = _iso(max(t["deadline"], now + (t["deadline"] - t["post"])))
                entry["times"]["auto_start"] = _iso(max(t["auto_start"], now + (t["auto_start"] - t["post"])))
                entry["times"]["reminders"] = [_iso(x) for x in t["reminders"] if x > now]
            facts, rows, money = build_facts(settings, week, now=now, st=st)
            d = week_dir(settings, week)
            write_json(d / "facts.json", facts)
            existing = orders.current_plan(settings, week)
            draft = build_draft(settings, week, now=now, rows=rows, money=money) if not money.get("unknown") \
                else {"lines": [], "skipped": [], "packs": 0, "cost_usd": 0.0, "budget_usd": None}
            blocking = [r for r in (facts["capacity"].get("reasons") or []) if r in orders.PLAN_CAPACITY_REASONS]
            res = None
            reason = None
            if existing is not None:
                reason = "plan_exists"
            elif money.get("unknown"):
                reason = "money_unknown"
            elif blocking:
                reason = blocking[0]
            elif not draft["lines"]:
                reason = "no_briefs" if not rows else ("no_money" if any(s["reason"] == "over_budget"
                                                                          for s in draft["skipped"]) else "nothing_new")
            else:
                order = {"schema": orders.SCHEMA, "kind": "weekly_plan", "created_by": "botsy",
                         "payload": {"plan_week": week, "lines": _order_lines(draft["lines"]), "kdp": []}}
                res = orders.submit(order, settings, now=now, cost_usd=draft["cost_usd"],
                                    context=order_context(st, settings), content_check=content_check(settings, now=now))
                if not res.get("ok"):
                    reason = (res.get("reasons") or ["refused"])[0]
            write_json(d / "plan.json", {"schema": DRAFT_SCHEMA, "plan_week": week, "made_at": _iso(now),
                                         "lines": draft["lines"], "kdp": [], "packs": draft["packs"],
                                         "cost_usd": draft["cost_usd"], "budget_usd": draft["budget_usd"],
                                         "skipped": draft["skipped"],
                                         "order": {"order_id": (res or {}).get("order_id"),
                                                   "status": (res or {}).get("status"),
                                                   "stage": (res or {}).get("stage"),
                                                   "reasons": (res or {}).get("reasons") or ([reason] if reason
                                                                                              else [])}})
            entry["prepared_at"] = _iso(now)
            entry["reason"] = reason if reason != "plan_exists" else None
            o = _sync(settings, st, week, entry, now=now)
            if o is None and entry["status"] not in ("expired", "cancelled"):
                entry["status"] = "none"
            _save_state(settings, st)
            if o is not None:
                messages.append(("plan_posted", _posted_text(settings, entry, o, st, draft=draft if o.get(
                    "created_by") == "botsy" else None, money=money),
                    {"plan_week": week, "order_id": o["id"], "packs": entry.get("packs"),
                     "cost_usd": entry.get("cost_usd"), "needs_approval": entry.get("needs_ok"),
                     "created_by": o.get("created_by")}))
            else:
                messages.append(("plan_posted", f"Botsy could not make a plan for {week}: {describe(reason)} "
                                 "Botsy keeps running on its normal rules.",
                                 {"plan_week": week, "order_id": None, "reason": reason}))
            due = (week, copy.deepcopy(entry), o, facts)
            out = {"ok": True, "week": week, "status": entry["status"], "order_id": entry.get("order_id"),
                   "reason": entry.get("reason"), "skipped": None, "message": messages[-1][1],
                   "packs": entry.get("packs"), "cost_usd": entry.get("cost_usd")}
    for kind, text, fields in messages:
        _post(settings, kind, text, now=now, **fields)
    if due is not None:
        try:
            _signed_due(settings, due[0], due[1], due[2], due[3], now=now, session=session)
        except (OSError, ValueError) as e:
            log.warning("plan_due not written: %s", type(e).__name__)
    return out


# ---------------------------------------------------------------- Hermes's plans

def submit_order(order, settings: dict | None = None, *, now: datetime | None = None) -> dict:
    """A weekly_plan or plan_revision from Hermes (botsy-control). A weekly_plan for a week that
    holds Botsy's own draft (not started yet) becomes a plan_revision of it. Then orders.submit
    with Botsy's content check, cost estimate and your approval count. Returns the orders result,
    with "converted": True when it became a revision."""
    from service import orders

    settings = _settings(settings)
    now = _utc(now)
    if not isinstance(order, dict) or order.get("kind") not in orders.PLAN_KINDS:
        return {"ok": False, "stage": "format", "reasons": ["unknown_kind"], "order_id": None, "status": "refused"}
    if not enabled(settings):
        return {"ok": False, "stage": "switches", "reasons": ["plans_disabled"], "order_id": None,
                "status": "refused"}
    messages = []
    with _lock(settings):
        st = load_state(settings)
        o = copy.deepcopy(order)
        payload = o.get("payload") if isinstance(o.get("payload"), dict) else {}
        week = payload.get("plan_week")
        converted = False
        if o.get("kind") == "weekly_plan" and isinstance(week, str) and orders.WEEK_RE.fullmatch(week):
            cur = orders.current_plan(settings, week)
            if cur is not None and cur.get("created_by") == "botsy" and cur.get("status") in WAITING + ("accepted",):
                o["kind"] = "plan_revision"
                o["payload"] = {**payload, "replaces": cur["id"]}
                converted = True
        cost = orders.estimate_cost(o.get("payload") or {}) if isinstance(o.get("payload"), dict) else None
        res = orders.submit(o, settings, now=now, cost_usd=cost, context=order_context(st, settings),
                            content_check=content_check(settings, now=now))
        res["converted"] = converted
        if res.get("ok") and not res.get("duplicate"):
            entry = _entry(st, week, settings)
            entry["prepared_at"] = entry.get("prepared_at") or _iso(now)
            entry["themes"] = {**(entry.get("themes") or {}), **_themes(settings, res["order"])}
            fresh = _sync(settings, st, week, entry, now=now)
            _save_state(settings, st)
            if fresh is not None:
                packs = _order_packs(fresh)
                messages.append(("plan_posted", f"Plan {week} checked: {packs} pack{'s' if packs != 1 else ''}, about "
                                 f"{_money(entry.get('cost_usd'))}, fits.\n" + "\n".join(_how_to(entry, settings, st)),
                                 {"plan_week": week, "order_id": fresh["id"], "packs": packs,
                                  "cost_usd": entry.get("cost_usd"), "needs_approval": entry.get("needs_ok"),
                                  "created_by": fresh.get("created_by")}))
        elif not res.get("ok"):
            messages.append(("plan_posted", f"Plan {week if isinstance(week, str) else '?'} from Hermes was not "
                             "accepted: " + "; ".join(describe(r) for r in res.get("reasons") or []),
                             {"plan_week": week if isinstance(week, str) else None, "order_id": None,
                              "reasons": res.get("reasons")}))
    for kind, text, fields in messages:
        _post(settings, kind, text, now=now, **fields)
    return res


# ---------------------------------------------------------------- the first check

def first_check(settings: dict, *, run_check=None) -> dict:
    """One check right after a plan starts (it does not count toward "run now"): a forced tick
    under the main lock, waiting at most plan.first_check_lock_wait_s for a running check."""
    from service import tick as tick_mod
    from service.lock import LockTimeout, service_lock
    from service.paths import service_paths

    paths = service_paths(settings)
    wait = _int(config(settings).get("first_check_lock_wait_s"), 120, 0, 3600)
    try:
        with service_lock(paths.lock, wait):
            rec = run_check(settings) if run_check else tick_mod.tick(settings, force=True)
    except LockTimeout:
        return {"ran": False, "reason": "busy"}
    except Exception as e:  # noqa: BLE001 - the plan started; the next check makes its items
        log.warning("first check of the plan failed: %s", type(e).__name__)
        return {"ran": False, "reason": "failed"}
    return {"ran": True, "exit_code": (rec or {}).get("exit_code") if isinstance(rec, dict) else None}


FIRST_CHECK_TRIES = 3      # a busy main lock: the next hourly checks try again, this many times in all


def _after_start(settings: dict, week: str, *, run_check=None) -> dict:
    """Run the started plan's first check. Done when it ran; a check that failed is not tried
    again (it could fail, and spend, every hour), and a busy lock is retried by the next
    hourly checks at most FIRST_CHECK_TRIES times in all (the daily check makes the items too)."""
    res = first_check(settings, run_check=run_check)
    with _lock(settings):
        st = load_state(settings)
        e = (st.get("plans") or {}).get(week)
        if isinstance(e, dict):
            e["first_check_tries"] = _int(e.get("first_check_tries"), 0) + 1
            if res.get("ran"):
                e["first_check"] = "done"
            elif res.get("reason") != "busy" or e["first_check_tries"] >= FIRST_CHECK_TRIES:
                e["first_check"] = "gave_up"
            _save_state(settings, st)
    return res


def _started_text(settings: dict, entry: dict, order: dict, how: str) -> str:
    packs = _order_packs(order)
    return (f"Weekly plan {entry['week']} started ({how}): {packs} pack{'s' if packs != 1 else ''}, about "
            f"{_money(entry.get('cost_usd'))}. Botsy makes only the plan's lines this week (at most 10 new packs "
            "a check). Hold: botsy service plan hold.")


# ---------------------------------------------------------------- check (hourly)

def _expire(settings: dict, week: str, entry: dict, o: dict, times: dict, *, now: datetime,
            messages: list) -> bool:
    """A plan still waiting for your approval at its deadline (Tuesday 20:00) expires: Botsy
    cancels the order and runs on its normal rules for the rest of the week."""
    from service import orders

    res = orders.submit({"kind": "order_cancel", "created_by": "botsy", "payload": {"order_id": o["id"]}},
                        settings, now=now)
    if not res.get("ok"):
        return False
    entry["status"] = "expired"
    entry["notices"]["expired"] = _iso(now)
    messages.append(("plan_expired", f"Weekly plan {week} expired: it was not approved by "
                     f"{_when(times['deadline'], settings)} {zone_name(settings)}. Botsy runs on "
                     "its normal rules for the rest of the week (the newest briefs, at most 10 "
                     "items a check). A new plan needs the same checks and your approval.",
                     {"plan_week": week, "order_id": o["id"]}))
    return True


def check(settings: dict | None = None, *, now: datetime | None = None, run_check=None, **_ignored) -> dict:
    """The hourly plan check (see the module docstring). Returns {"ok", "actions": [...]}."""
    from service import orders

    settings = _settings(settings)
    now = _utc(now)
    if not enabled(settings):
        return {"ok": True, "actions": [], "skipped": "plans_disabled"}
    actions: list[str] = []
    messages: list[tuple[str, str, dict]] = []
    start_week = None
    with _lock(settings):
        st = load_state(settings)
        this, nxt = orders.plan_weeks(settings, now)
        for week in (this, nxt):
            had = isinstance((st.get("plans") or {}).get(week), dict)
            times = schedule_for(week, settings)
            # "No plan this week" is said once, in the day after schedule.plan_post + 1 hour (Sun 21:00).
            window = times["no_plan_at"] <= now < times["no_plan_at"] + timedelta(hours=24)
            entry = _entry(st, week, settings) if had or window else None
            if entry is None:
                continue
            times = _times(entry, week, settings)
            o = _sync(settings, st, week, entry, now=now)
            status = entry.get("status")
            if status not in LIVE and window and not entry["notices"].get("no_plan") \
                    and status not in ("expired", "cancelled", "missed"):      # those said so already
                entry["notices"]["no_plan"] = _iso(now)
                actions.append(f"{week}: no plan")
                messages.append(("no_plan", "No plan this week. Botsy keeps running on its normal rules.",
                                 {"plan_week": week, "reason": entry.get("reason") or status}))
            if o is None:
                continue
            if status in ("waiting", "held") and _waiting(o):
                if now >= times["deadline"]:
                    if _expire(settings, week, entry, o, times, now=now, messages=messages):
                        actions.append(f"{week}: expired")
                    continue
                due = [r for r in times["reminders"] if r <= now and r >= times["post"]]
                sent = set(entry.get("reminders_sent") or [])
                fresh = [r for r in due if _iso(r) not in sent]
                if fresh:
                    entry["reminders_sent"] = sorted(sent | {_iso(r) for r in due})
                    n = len(due)
                    actions.append(f"{week}: reminder {n}")
                    held = " It is on hold." if status == "held" else ""
                    messages.append(("plan_reminder", f"Reminder {n}: weekly plan {week} is waiting for your OK."
                                     f"{held} It expires {_when(times['deadline'], settings)} {zone_name(settings)}.\n"
                                     + "\n".join(_how_to(entry, settings, st)[:2]),
                                     {"plan_week": week, "order_id": o["id"], "reminder": n}))
            elif status == "waiting" and o.get("status") == "accepted":
                start = orders.plan_start_at(o, settings)
                stored = _parse((entry.get("times") or {}).get("auto_start"))
                if start is not None and stored is not None:
                    start = max(start, stored)
                if start is not None and now >= start:
                    res = orders.apply(o["id"], settings, now=now)
                    if res.get("ok"):
                        o = _sync(settings, st, week, entry, now=now)
                        actions.append(f"{week}: started")
                        entry["notices"]["started"] = o["id"]          # announced, for this order
                        messages.append(("plan_started", _started_text(settings, entry, o, "by itself"),
                                         {"plan_week": week, "order_id": o["id"], "how": "auto"}))
                    else:
                        why = (res.get("reasons") or ["apply_failed"])[0]
                        _sync(settings, st, week, entry, now=now)
                        if entry["notices"].get("start_failed") != why:
                            entry["notices"]["start_failed"] = why
                            actions.append(f"{week}: not started ({why})")
                            extra = ("\n" + "\n".join(_how_to(entry, settings, st))) if why == "plan_needs_approval" \
                                else ""
                            messages.append(("plan_started", f"Weekly plan {week} did not start: {describe(why)}"
                                             + extra, {"plan_week": week, "order_id": o["id"], "reason": why}))
            if entry.get("status") in ("running", "done") and entry.get("first_check") == "pending":
                if entry["notices"].pop("started_pending", None) \
                        and entry["notices"].get("started") != entry.get("order_id"):
                    entry["notices"]["started"] = entry.get("order_id")
                    messages.append(("plan_started", _started_text(settings, entry, o, "approved"),
                                     {"plan_week": week, "order_id": entry.get("order_id"), "how": "approved"}))
                start_week = week
        _save_state(settings, st)
    for kind, text, fields in messages:
        _post(settings, kind, text, now=now, **fields)
    out = {"ok": True, "actions": actions}
    if start_week:
        out["first_check"] = _after_start(settings, start_week, run_check=run_check)
    # A watchdog retry Hermes asked for (service/fixes.py fix_retry_run) runs here, at most one an
    # hour, after Botsy checks its preconditions again.
    try:
        from service import fixes

        fix = fixes.run_pending(settings, now=now, run_check=run_check)
    except Exception as e:  # noqa: BLE001 - the plan check itself is done
        log.warning("queued fix not run: %s", type(e).__name__)
        fix = {"ran": False, "reason": "failed"}
    if fix.get("reason") != "none":
        out["fix"] = fix
        actions.append("fix: " + ("retried the failed check" if fix.get("ran") else f"not run ({fix.get('reason')})"))
    return out


# ---------------------------------------------------------------- approvals (no codes)

def _waiting(o) -> bool:
    """The plan order waits for your approval (order/v1 status waiting_approval)."""
    return isinstance(o, dict) and o.get("status") in WAITING


def _needs_ok(o) -> bool:
    return isinstance(o, dict) and bool(o.get("needs_approval"))


def _ok_reason(o) -> str | None:
    """Why the plan waits for you: first_plans, approve_each, plan_over_auto_share,
    limits_unavailable, money_unknown (needs_approval for anything else), or None."""
    if not _needs_ok(o):
        return None
    return NEEDS_OK_REASONS.get((o.get("checks") or {}).get("approval_reason"), "needs_approval")


def _requester(by) -> str:
    """Who asked: "user" (the server CLI) or "hermes" (botsy-control). Nothing else may approve."""
    if by not in BY:
        raise ValueError("by must be 'user' or 'hermes'")
    return by


def _asked(by: str) -> str:
    return {"user": "asked by: user (the server)", "hermes": "asked by: hermes (Discord)"}.get(by, f"asked by: {by}")


# ---------------------------------------------------------------- your commands

def _plan_now(settings: dict, st: dict, now: datetime) -> tuple[str | None, dict | None, dict | None]:
    week, entry, o = _governing(settings, st, now)
    return week, entry, o


def _lift_hold(settings: dict, o: dict, now: datetime, *, by: str = "user") -> bool:
    from service import orders

    held = o.get("held") if isinstance(o.get("held"), dict) else None
    if not held or not held.get("order_id"):
        return False
    res = orders.undo(held["order_id"], settings, by=by, now=now)
    return bool(res.get("ok"))


def _approved_text(settings: dict, entry: dict, order: dict, by: str) -> str:
    packs = _order_packs(order)
    why = {"first_plans": "one of the first plans that wait for you",
           "plan_over_auto_share": "it costs more than the share of the cap that may start by itself",
           "approve_each": "you asked to approve every plan (plan again)"}.get(entry.get("ok_reason"), "")
    return (f"Weekly plan {entry['week']} approved ({_asked(by)}): {packs} pack{'s' if packs != 1 else ''}, about "
            f"{_money(entry.get('cost_usd'))}" + (f"; it waited for you because {why}" if why else "") + ". "
            "It starts now: Botsy makes only the plan's lines this week (at most 10 new packs a check).\n"
            "Undo: botsy service plan hold (no new lines; packs already made stay and still go to drafts).")


def approve(settings: dict | None = None, *, by: str = "user", now: datetime | None = None,
            run_check=None, order_id: str | None = None, first_check: bool = True, **_ignored) -> dict:
    """`plan approve` (or Hermes for you, by="hermes"). A plan that waits for your approval
    starts at once and a hold on it is lifted: a Level 3 approval, so at most limits
    level3_per_day (10) in 24 hours (orders.approve counts it), never after the plan's deadline,
    and posted at once to Discord (urgent) with who asked and how to undo it. A held plan that
    starts by itself starts at once (Level 2).
    order_id: approve only that plan order; any other (another week's, a replaced draft) is
    refused with not_waiting and nothing is touched. first_check=False: do not run the plan's
    first check here (botsy-control); the next hourly plan check runs it.
    Returns {"ok", "step", "reason", "week", ...}."""
    from service import orders

    settings = _settings(settings)
    now = _utc(now)
    by = _requester(by)
    started = None
    approved = False
    out: dict
    with _lock(settings):
        st = load_state(settings)
        week, entry, o = _plan_now(settings, st, now)
        if o is None:
            _save_state(settings, st)
            return {"ok": False, "step": None, "reason": "no_plan", "week": None}
        if order_id is not None and o.get("id") != order_id:
            _save_state(settings, st)
            return {"ok": False, "step": None, "reason": "not_waiting", "week": week}
        if not orders.may_lift(orders.hold_of(o), by):
            _save_state(settings, st)          # D3: your Hold sticks until you lift it
            return {"ok": False, "step": "hold", "reason": "held_by_user", "week": week}
        if _waiting(o):
            times = _times(entry, week, settings)
            if now >= times["deadline"]:
                # Fail closed: an approval never starts a plan after its deadline, even when the
                # hourly check has not expired it yet (the server was off, or it runs at :05).
                expired: list = []
                _expire(settings, week, entry, o, times, now=now, messages=expired)
                _save_state(settings, st)
                for kind, text, fields in expired:
                    _post(settings, kind, text, now=now, **fields)
                return {"ok": False, "step": "deadline", "reason": "plan_expired", "week": week}
            res = orders.approve(o["id"], settings, by=by, now=now)
            if not res.get("ok"):
                _sync(settings, st, week, entry, now=now)
                _save_state(settings, st)
                return {"ok": False, "step": res.get("stage"), "reason": (res.get("reasons") or ["apply_failed"])[0],
                        "week": week}
            o = orders.load(o["id"], settings) or o
            _lift_hold(settings, o, now, by=by)
            approved = True
        elif o.get("status") == "accepted":
            if isinstance(o.get("held"), dict):
                res = orders.submit({"kind": "plan_start_now", "created_by": by, "payload": {"plan_week": week}},
                                    settings, now=now)
                plan_res = res.get("plan") or {}
                if not (res.get("ok") and plan_res.get("ok")):
                    _sync(settings, st, week, entry, now=now)
                    _save_state(settings, st)
                    reasons = res.get("reasons") or plan_res.get("reasons") or ["apply_failed"]
                    return {"ok": False, "step": "start", "reason": reasons[0], "week": week}
                started = "approved after a hold"
            else:
                _save_state(settings, st)
                start = orders.plan_start_at(o, settings)
                return {"ok": True, "step": "nothing_to_do", "reason": "starts_by_itself", "week": week,
                        "start_at": _iso(start)}
        else:
            if isinstance(o.get("held"), dict) and _lift_hold(settings, o, now, by=by):
                started = "resumed after a hold"
            else:
                _save_state(settings, st)
                return {"ok": True, "step": "nothing_to_do", "reason": "plan_already_started", "week": week}
        o = _sync(settings, st, week, entry, now=now)
        entry["notices"]["started"] = entry.get("order_id")
        entry["notices"].pop("started_pending", None)
        _save_state(settings, st)
        out = {"ok": True, "step": "started", "reason": None, "week": week, "order_id": o["id"] if o else None,
               "status": entry.get("status")}
        text = (_approved_text(settings, entry, o, by) if approved else _started_text(settings, entry, o, started)) \
            if o else ""
        fields = {"plan_week": week, "order_id": out["order_id"], "by": by}
    if approved:
        events.emit("plan_started", settings=settings, plan_week=week, order_id=out["order_id"], how="approved",
                    by=by)
        _post(settings, "plan_approved", text, now=now, urgent=True, reason=entry.get("ok_reason"),
              packs=entry.get("packs"), cost_usd=entry.get("cost_usd"), **fields)
    else:
        _post(settings, "plan_started", text, now=now, how=by, **{k: v for k, v in fields.items() if k != "by"})
    out["first_check"] = _after_start(settings, week, run_check=run_check) if first_check else \
        {"ran": False, "reason": "next_hourly_check"}
    return out


def start_now(settings: dict | None = None, *, by: str = "user", now: datetime | None = None, run_check=None,
              **_ignored) -> dict:
    """`plan start-now`: a plan that starts by itself starts at once (Level 2); a plan that
    waits for your approval is approved (plan approve, with its guards)."""
    from service import orders

    settings = _settings(settings)
    now = _utc(now)
    by = _requester(by)
    waiting = False
    with _lock(settings):
        st = load_state(settings)
        week, entry, o = _plan_now(settings, st, now)
        if o is None:
            _save_state(settings, st)
            return {"ok": False, "reason": "no_plan", "week": None}
        if not orders.may_lift(orders.hold_of(o), by):
            _save_state(settings, st)          # D3: your Hold sticks until you lift it
            return {"ok": False, "reason": "held_by_user", "week": week, "step": "hold"}
        if _waiting(o):                    # approve() takes the lock again and checks everything again
            _save_state(settings, st)
            waiting = True
        elif o.get("status") == "applied":
            if isinstance(o.get("held"), dict) and _lift_hold(settings, o, now, by=by):
                how = "resumed after a hold"
            else:
                _save_state(settings, st)
                return {"ok": True, "reason": "plan_already_started", "week": week, "step": "nothing_to_do"}
        else:
            res = orders.submit({"kind": "plan_start_now", "created_by": by, "payload": {"plan_week": week}},
                                settings, now=now)
            plan_res = res.get("plan") or {}
            if not (res.get("ok") and plan_res.get("ok")):
                _sync(settings, st, week, entry, now=now)
                _save_state(settings, st)
                reasons = res.get("reasons") or plan_res.get("reasons") or ["apply_failed"]
                return {"ok": False, "reason": reasons[0], "week": week}
            how = "Start now"
        if not waiting:
            o = _sync(settings, st, week, entry, now=now)
            entry["notices"]["started"] = entry.get("order_id")
            entry["notices"].pop("started_pending", None)
            _save_state(settings, st)
            text = _started_text(settings, entry, o, how)
    if waiting:
        return approve(settings, by=by, now=now, run_check=run_check)
    _post(settings, "plan_started", text, now=now, plan_week=week, order_id=o["id"], how="start_now")
    return {"ok": True, "reason": None, "week": week, "step": "started",
            "first_check": _after_start(settings, week, run_check=run_check)}


def hold(settings: dict | None = None, *, by: str = "user", now: datetime | None = None, **_ignored) -> dict:
    """`plan hold`: always allowed. A waiting plan keeps waiting (the deadline still applies);
    a running plan makes nothing new until you approve it again."""
    from service import orders

    settings = _settings(settings)
    now = _utc(now)
    by = _requester(by)
    with _lock(settings):
        st = load_state(settings)
        week, entry, o = _plan_now(settings, st, now)
        if o is None:
            _save_state(settings, st)
            return {"ok": False, "reason": "no_plan", "week": None}
        if isinstance(o.get("held"), dict):
            _save_state(settings, st)
            return {"ok": True, "reason": "already_held", "week": week}
        res = orders.submit({"kind": "plan_hold", "created_by": by, "payload": {"plan_week": week}}, settings,
                            now=now)
        if not res.get("ok"):
            _save_state(settings, st)
            return {"ok": False, "reason": (res.get("reasons") or ["refused"])[0], "week": week}
        _sync(settings, st, week, entry, now=now)
        _save_state(settings, st)
    events.emit("plan_held", settings=settings, plan_week=week, order_id=o["id"], by=by)
    return {"ok": True, "reason": None, "week": week, "status": "held"}


def again(settings: dict | None = None, *, by: str = "user", now: datetime | None = None, **_ignored) -> dict:
    """`plan again`: back to approving every plan (allowed any time: it is stricter). A plan
    that would start by itself and has not started yet is revised by Botsy into the same plan
    that waits for your approval; if that is refused, it is cancelled (normal rules this week)."""
    from service import orders

    settings = _settings(settings)
    now = _utc(now)
    by = _requester(by)
    note = None
    with _lock(settings):
        st = load_state(settings)
        before = effective_mode(st, settings)
        st["mode"] = "approve"
        week, entry, o = _plan_now(settings, st, now)
        if o is not None and o.get("status") == "accepted":
            p = o.get("payload") or {}
            rev = {"kind": "plan_revision", "created_by": "botsy",
                   "payload": {"plan_week": p.get("plan_week"), "lines": p.get("lines"), "kdp": p.get("kdp") or [],
                               "replaces": o["id"]}}
            res = orders.submit(rev, settings, now=now, cost_usd=(o.get("checks") or {}).get("cost_usd"),
                                context=order_context(st, settings))
            if res.get("ok") and _needs_ok(res):
                note = "revised"
            else:
                if res.get("ok"):
                    orders.submit({"kind": "order_cancel", "created_by": by,
                                   "payload": {"order_id": res["order_id"]}}, settings, now=now)
                cur = orders.current_plan(settings, week)
                if cur is not None:
                    orders.submit({"kind": "order_cancel", "created_by": by, "payload": {"order_id": cur["id"]}},
                                  settings, now=now)
                note = "cancelled"
            _sync(settings, st, week, entry, now=now)
        _save_state(settings, st)
    events.emit("plan_mode_changed", settings=settings, mode_from=before, mode_to="approve", by=by)
    return {"ok": True, "mode": "approve", "week": week, "plan": note}


def auto(settings: dict | None = None, *, by: str = "user", now: datetime | None = None, **_ignored) -> dict:
    """`plan auto`: plans start by themselves again (Monday 08:00, with Hold), also before 4
    approved plans. A Level 3 approval: at most limits level3_per_day (10) in 24
    hours, posted at once to Discord (urgent) with who asked and how to undo it (plan again).
    A plan already waiting for your approval keeps waiting."""
    settings = _settings(settings)
    now = _utc(now)
    by = _requester(by)
    with _lock(settings):
        st = load_state(settings)
        if effective_mode(st, settings) == "auto":
            return {"ok": True, "step": "nothing_to_do", "reason": "already_auto", "mode": "auto"}
        from service import orders

        if not orders.level3_take(settings, "plan_auto", by, now=now):     # counted on the shared counter
            return {"ok": False, "step": "limits", "reason": "level3_daily_limit", "mode": "approve"}
        before = effective_mode(st, settings)
        n, need = _int(st.get("approved_count"), 0), approvals_needed(settings)
        st["mode"] = "auto"
        _save_state(settings, st)
    early = f" {n} of the first {need} plans had been approved." if n < need else ""
    text = (f"Weekly plans now start by themselves ({_asked(by)}): Monday 08:00 {zone_name(settings)}, unless you "
            f"hold them.{early} A plan over the auto-start share of the cap still waits for your approval, and a "
            "plan already waiting keeps waiting.\nUndo: botsy service plan again (approve every plan).")
    _post(settings, "plan_mode_changed", text, now=now, urgent=True, mode_from=before, mode_to="auto", by=by,
          approved_plans=n)
    return {"ok": True, "step": "switched", "reason": None, "mode": "auto"}



# ---------------------------------------------------------------- what the tick makes

def for_tick(settings: dict | None = None, *, now: datetime | None = None) -> dict:
    """What this check makes (docs/OVERSEER.md B5), read-only:
    {"mode": "normal" | "plan" | "finish" | "wait", "week", "order_id", "status", "lines": [pipeline
    plan lines], "dropped": [{"week", "brief_id", "reason"}], "note"}."""
    from service import orders

    settings = _settings(settings)
    now = _utc(now)
    out = {"mode": "normal", "week": None, "order_id": None, "status": None, "lines": [], "dropped": [], "note": None}
    if not enabled(settings):
        out["note"] = "weekly plans are switched off (plan.enabled is false); normal rules"
        return out
    if _off_limits(settings):
        return out
    st = copy.deepcopy(load_state(settings))
    this, nxt = orders.plan_weeks(settings, now)
    stale_days = _int(config(settings).get("stale_days"), 8, 1, 60)

    def synced(week):
        if not isinstance((st.get("plans") or {}).get(week), dict):
            return None, None
        e = _entry(st, week, settings)
        return e, _sync(settings, st, week, e, now=now)

    def stale(e, o) -> bool:
        posted = _parse((e.get("times") or {}).get("post")) or _parse((o or {}).get("created_at"))
        return posted is None or now - posted > timedelta(days=stale_days)

    e_next, o_next = synced(nxt)
    e_this, o_this = synced(this)
    if e_next and e_next["status"] in ("running", "done") and not stale(e_next, o_next):
        week, e, o, mode = nxt, e_next, o_next, "plan"
    elif e_this and e_this["status"] in ("running", "done"):
        if stale(e_this, o_this):
            out.update(week=this, status=e_this["status"], note=f"plan {this} is more than {stale_days} days old; "
                                                                 "normal rules")
            return out
        week, e, o, mode = this, e_this, o_this, "plan"
    elif (e_this and e_this["status"] in ("waiting", "held")) or (e_next and e_next["status"] in ("waiting", "held")):
        # A plan waits (for your approval, its start time, or on hold): this week's, or the one
        # posted on Sunday evening for the coming week. Only last week's lines are finished.
        pw, pe = (this, e_this) if e_this and e_this["status"] in ("waiting", "held") else (nxt, e_next)
        out.update(week=pw, order_id=pe.get("order_id"), status=pe["status"])
        prev = _prev_week(pw)
        e_prev, o_prev = (e_this, o_this) if prev == this else synced(prev)
        if pe["status"] == "held" and pe.get("started_at"):
            out.update(mode="wait", note=f"plan {pw} is on hold; nothing new is made until you approve it "
                                         "(botsy service plan approve)")
            return out
        if e_prev and e_prev["status"] in ("running", "done") and o_prev and not stale(e_prev, o_prev):
            week, e, o, mode = prev, e_prev, o_prev, "finish"
        else:
            why = ("there was no plan last week" if not (e_prev and e_prev["status"] in ("running", "done"))
                   else f"last week's plan is more than {stale_days} days old")
            out.update(mode="wait", note=(f"plan {pw} " + ("waits for your approval" if pe.get("needs_ok")
                                                           else "is on hold" if pe["status"] == "held"
                                                           else "starts later") +
                                          f"; nothing new is made until it starts ({why})"))
            return out
    else:
        status = (e_this or {}).get("status")
        out.update(week=this, status=status,
                   note=f"no plan in force for {this}" + (f" ({status})" if status else "") + "; normal rules")
        return out
    out.update(mode=mode, week=week, order_id=o["id"], status=e["status"])
    lines, dropped = _tick_lines(settings, e, o, now=now)
    out["lines"], out["dropped"] = lines, dropped
    if mode == "finish":
        out["note"] = (f"plan {pw} " + ("waits for your approval" if pe.get("needs_ok") else "has not started")
                       + f"; finishing last week's plan {week}")
    else:
        out["note"] = f"weekly plan {week}: only its lines are made"
    return out


def _tick_lines(settings: dict, entry: dict, order: dict, *, now: datetime) -> tuple[list[dict], list[dict]]:
    """Pipeline plan lines for the plan's lines. A line whose brief is more than
    plan.brief_max_age_days old keeps only its packs already made (they still go on to QA,
    listing and drafts); with none it is dropped (brief_too_old)."""
    max_age = _int(config(settings).get("brief_max_age_days"), 7, 0, 365)
    day = _local_day(settings, now)
    indexes = _indexes(settings)
    shops = _shop_of(settings)
    lines, dropped = [], []
    for line in (order.get("payload") or {}).get("lines") or []:
        bid, niche, sizes = line.get("brief_id"), line.get("niche"), list(line.get("sizes") or [])
        theme = (entry.get("themes") or {}).get(bid)
        path = brief_file(settings, niche, bid)
        made_on = brief_date(bid)
        old = made_on is None or (day - made_on).days > max_age
        was = (entry.get("dropped") or {}).get(bid)
        if old or was:
            brief = {"brief_id": bid, "niche": niche, "theme": theme or ""}
            idx = indexes.get(shops.get(niche))
            made = [s for s in sizes if idx and idx.state(brief, s, day.isoformat(), all_sizes=sizes)[0] == "made"]
            if old and not was and len(made) < len(sizes):
                dropped.append({"week": entry["week"], "brief_id": bid, "reason": "brief_too_old"})
            sizes = made
        if (not theme or path is None) and not was and not old:
            # Botsy never saw this line's brief (its theme is unknown): it cannot be made.
            dropped.append({"week": entry["week"], "brief_id": bid, "reason": "brief_missing"})
            continue
        if not sizes or not theme or path is None:
            continue
        lines.append({"brief_file": str(path), "brief_id": bid, "niche": niche, "theme": theme, "sizes": sizes,
                      "origin": "plan", "order_id": order["id"], "shop_id": shops.get(niche)})
    return lines, dropped


def record_run(settings: dict, tick_plan: dict, summaries: list[dict], *, now: datetime | None = None) -> list[dict]:
    """After the tick's pipeline runs: remember dropped lines (each announced once as
    plan_line_dropped) and mark a plan done when every line is made, skipped or dropped.
    Returns the newly dropped lines."""
    now = _utc(now)
    week = (tick_plan or {}).get("week")
    if not tick_plan or tick_plan.get("mode") not in ("plan", "finish") or not week:
        return []
    found = list(tick_plan.get("dropped") or [])
    for s in summaries or []:
        for res in ((s or {}).get("plan") or {}).get("lines") or []:
            if res.get("dropped") and res.get("order_id") == tick_plan.get("order_id"):
                found.append({"week": week, "brief_id": res.get("brief_id"), "reason": res["dropped"]})
    new = []
    with _lock(settings):
        st = load_state(settings)
        e = (st.get("plans") or {}).get(week)
        if not isinstance(e, dict):
            return []
        e = _entry(st, week, settings)
        for d in found:
            if d["brief_id"] and d["brief_id"] not in e["dropped"]:
                e["dropped"][d["brief_id"]] = d["reason"]
                new.append(d)
        before = e.get("status")
        _sync(settings, st, week, e, now=now)
        _save_state(settings, st)
    for d in new:
        events.emit("plan_line_dropped", settings=settings, plan_week=d["week"], brief_id=d["brief_id"],
                    reason=d["reason"])
    if before != "done" and e.get("status") == "done":
        events.emit("plan_done", settings=settings, plan_week=week, order_id=e.get("order_id"))
    return new


# ---------------------------------------------------------------- status and the daily log

def summary(settings: dict | None = None, *, now: datetime | None = None) -> dict:
    """The plan for status.txt and the daily log: {"enabled", "mode", "approved_count",
    "approvals_before_auto", "week", "status", "order_id", "needs_approval", "approval_reason",
    "deadline", "auto_start", "progress", "tick", "lines": [plain text lines]}. Read-only."""
    from service import orders

    settings = _settings(settings)
    now = _utc(now)
    if not enabled(settings):
        return {"enabled": False, "lines": ["Weekly plans are switched off (plan.enabled is false)."]}
    if _off_limits(settings):
        return {"enabled": True, "lines": []}
    try:
        st = copy.deepcopy(load_state(settings))
        this, nxt = orders.plan_weeks(settings, now)
        week, entry, o = _governing(settings, st, now)
        if entry is None:
            for w in (nxt, this):
                if isinstance((st.get("plans") or {}).get(w), dict):
                    week, entry = w, _entry(st, w, settings)
                    break
        tick = for_tick(settings, now=now)
    except (OSError, ValueError, RuntimeError) as e:
        return {"enabled": True, "lines": [f"The plan could not be read: {type(e).__name__}."]}
    mode = effective_mode(st, settings)
    need = approvals_needed(settings)
    n = _int(st.get("approved_count"), 0)
    out = {"enabled": True, "mode": mode, "approved_count": n, "approvals_before_auto": need, "week": week,
           "status": (entry or {}).get("status"), "order_id": (entry or {}).get("order_id"),
           "needs_approval": (entry or {}).get("needs_ok"), "approval_reason": (entry or {}).get("ok_reason"),
           "deadline": None, "auto_start": None, "progress": None,
           "tick": {k: tick.get(k) for k in ("mode", "week", "note")}}
    zone = zone_name(settings)
    lines = [("Mode: you approve every plan" if mode == "approve" else
              "Mode: plans start by themselves on Monday 08:00 unless you hold them")
             + (f" ({n} of {need} plans approved so far)." if n < need and st.get("mode") != "auto" else ".")]
    if entry is None:
        nxt_post = post_time(nxt, settings)
        lines.append(f"No plan yet. The next plan is posted {_when(nxt_post, settings)} {zone}.")
    else:
        t = _times(entry, week, settings)
        out["deadline"], out["auto_start"] = _iso(t["deadline"]), _iso(t["auto_start"])
        status = entry.get("status")
        words = {"waiting": "waiting for your approval" if entry.get("needs_ok") else "waiting to start",
                 "running": "running", "held": "on hold", "expired": "expired (not approved in time)",
                 "done": "done (every line made)", "cancelled": "cancelled", "none": "no plan",
                 "missed": "missed (the server was off)"}.get(status, str(status))
        lines.append(f"Plan {week}: {words}" + (f", {entry.get('packs')} packs, about {_money(entry.get('cost_usd'))}"
                                                 if entry.get("packs") else "") + ".")
        if status in ("waiting", "held") and entry.get("needs_ok"):
            lines.append(f"  Deadline: {_when(t['deadline'], settings)} {zone}. Approve: botsy service plan approve")
        elif status == "waiting":
            start = orders.plan_start_at(o, settings) if o else t["auto_start"]
            lines.append(f"  Starts: {_when(start, settings)} {zone}. Hold: botsy service plan hold")
        if o is not None and status in ("running", "done", "held"):
            p = progress(settings, entry, o, now=now)
            out["progress"] = p
            lines.append(f"  Progress: {p['packs_made']} of {p['packs_total']} packs made"
                         + (f", {p['packs_skipped']} skipped (theme cooldown)" if p["packs_skipped"] else "")
                         + (f", {p['lines_dropped']} line(s) dropped" if p["lines_dropped"] else "") + ".")
        for bid, why in sorted((entry.get("dropped") or {}).items()):
            lines.append(f"  Dropped: {bid} ({describe(why)})")
    if tick.get("note"):
        lines.append(f"This check: {tick['note']}.")
    out["lines"] = lines
    return out


# ---------------------------------------------------------------- reasons and the CLI

DESCRIBE = {
    "no_plan": "There is no plan in force.",
    "plans_disabled": "Weekly plans are switched off (plan.enabled is false).",
    "no_briefs": "there are no current briefs (upload this week's exports first).",
    "nothing_new": "every current brief is already made or blocked by the theme cooldown.",
    "no_money": "no brief fits this week's share of the money left.",
    "server_was_off": "the server was off when the plan was due.",
    "plan_expired": "The plan's deadline has passed, so it expired: Botsy runs on its normal rules this week.",
    "starts_by_itself": "This plan does not wait for your approval: it starts by itself.",
    "level3_daily_limit": "Too many approvals in the last 24 hours (limits level3_per_day); try again later.",
    "approval_unavailable": "Botsy could not record the approval (see the journal); nothing was started.",
    "already_auto": "Plans already start by themselves.",
    "already_held": "The plan is already on hold.",
    "held_by_user": "You put this plan on hold. Only you lift your Hold (botsy service plan approve or "
                    "start-now on the server); Hermes cannot start, approve or revise it.",
    "brief_unknown": "A plan line names a brief Botsy does not have.",
    "brief_too_old": "A plan line's brief is more than 7 days old.",
    "brief_missing": "The brief file is gone.",
    "brief_changed": "Research rewrote that brief with another theme.",
    "size_not_in_brief": "A plan line asks for a pack size its brief does not have.",
    "duplicate_line": "The plan names the same brief twice.",
    "theme_blocked": "A plan line's theme has a blocked term.",
    "brief_blocked": "A brief file breaks the brief rules.",
    "kdp_not_built": "KDP books are not built yet, so a plan cannot hold KDP lines.",
    "not_this_shop": "That niche is not one of the shop's niches.",
    "bad_line": "The plan line is not well formed.",
}


def describe(reason) -> str:
    if reason in DESCRIBE:
        return DESCRIBE[reason]
    from service import orders

    return orders.describe(str(reason)) if reason else "ok"


def cli(action: str, settings: dict, *, force: bool = False, outbox: Path | None = None,
        now: datetime | None = None, out=print) -> int:
    """`python -m service plan <action>`. Exit 0 done, 1 refused or failed."""
    if action == "prepare":
        res = prepare(settings, now=now, force=force)
        out(res.get("message") or f"plan {res.get('week')}: {res.get('status')}")
        code_rc = 0 if res.get("ok") else 1
    elif action == "check":
        res = check(settings, now=now)
        out("plan check: " + ("; ".join(res.get("actions") or []) or "nothing to do"))
        fc = res.get("first_check")
        if fc:
            out("first check of the plan: " + ("done" if fc.get("ran") else f"later ({fc.get('reason')})"))
        code_rc = 0
    elif action == "status":
        code_rc = 0
    else:
        fn = {"approve": approve, "hold": hold, "start-now": start_now, "again": again, "auto": auto}[action]
        res = fn(settings, by="user", now=now)
        out(_result_text(action, res, settings))
        code_rc = 0 if res.get("ok") else 1
    s = summary(settings, now=now)
    if outbox is not None:
        # status.json is the tick's file too: write it only under the main lock, without
        # waiting. A check that is running writes the plan's summary itself when it ends.
        from service import status as status_mod
        from service.lock import LockTimeout, service_lock
        from service.paths import service_paths

        try:
            with service_lock(service_paths(settings).lock, 0):
                status_mod.set_plan(outbox, s)
        except LockTimeout:
            log.info("a check is running; it updates status.txt with the plan when it ends")
        except (OSError, ValueError) as e:
            log.warning("status.txt not updated with the plan: %s", type(e).__name__)
    if action == "status":
        out("Weekly plan")
        for ln in s.get("lines") or []:
            out("  " + ln)
    return code_rc


def _result_text(action: str, res: dict, settings: dict) -> str:
    reason = res.get("reason")
    if res.get("step") == "started":
        fc = res.get("first_check") or {}
        return (f"Plan {res.get('week')} started." + (" Its first check ran." if fc.get("ran") else
                                                     " Its first check runs at the next plan check."))
    if action == "approve" and reason == "starts_by_itself":
        return (f"Plan {res.get('week')} does not wait for your approval: it starts by itself"
                + (f" on {_when(_parse(res.get('start_at')), settings)} {zone_name(settings)}"
                   if res.get("start_at") else "") + ". To start it now: botsy service plan start-now")
    if action == "hold" and res.get("ok"):
        return f"Plan {res.get('week')} is on hold." if not reason else describe(reason)
    if action == "again":
        extra = {"revised": " This week's plan now waits for your approval too.",
                 "cancelled": " This week's plan could not be switched, so it was cancelled (normal rules this week)."
                 }.get(res.get("plan"), "")
        return "From now on every plan waits for your approval." + extra
    if action == "auto" and res.get("ok"):
        return ("Plans start by themselves from now on (Monday 08:00, unless you hold them). Posted in "
                "Discord. Undo: botsy service plan again") if not reason \
            else describe(reason)
    if res.get("ok"):
        return describe(reason) if reason else "done"
    return "Not done: " + describe(reason)


__all__ = ["ACTIONS", "EVENT_KINDS", "MESSAGE_KINDS", "SETTINGS_DEFAULTS", "STATUSES", "again", "approve", "auto",
           "budget", "build_draft", "check", "cli", "config", "current_briefs", "deadline_for", "describe", "effective_mode",
           "enabled", "for_tick", "hold", "load_state", "order_context", "post_time", "prepare", "progress",
           "record_run", "schedule_for", "scheduled_post", "start_now", "submit_order", "summary"]
