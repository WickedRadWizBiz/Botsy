"""Orders: checked requests to change what Botsy does (docs/OVERSEER.md "Four permission levels";
docs/CONTRACTS.md order/v1). No codes (user, 2026-10-21: "I don't want the codes").

    validate(order, settings=None, limits=None, capacity=None, *, store=True, now=None,
             cost_usd=None, context=None, content_check=None) -> dict
    submit(order, settings=None, *, ..., apply_now=None) -> dict
    apply(order_or_id, settings=None, *, now=None) -> dict          (an accepted order)
    approve(order_or_id, settings=None, *, by, now=None) -> dict    (a Level 3 approval: user | hermes)
    undo(order_id, settings=None, *, by="user", now=None) -> dict
    load(order_id, settings=None) -> dict | None ; list_orders(settings=None, *, week=None, kind=None, status=None)
    plan_needs_approval(cost_usd, settings=None, limits=None, *, approved_plans=0, approve_each=False) -> (bool, reason)
    plan_start_at(order, settings=None) -> datetime | None   (earliest start of a plan that starts by itself)
    level3_room(settings=None, *, now=None) -> bool ; level3_take(settings, action, by, *, now=None) -> bool
    estimate_cost(payload) -> float
    control_state(settings=None) -> dict ; paused(settings=None) -> dict | None
    effective_cap(settings=None) -> float | None ; paid_work_block(settings=None, *, now=None) -> str | None
    note_budget_stop(settings=None, *, month=None, now=None) -> bool
    register_handler(kind, apply=None, undo=None)

Who writes orders: Hermes (through botsy-control, which always sets created_by "hermes"),
you (the server CLI and the dashboard's Pause and Lower cap: "user") and Botsy itself (its own
deterministic weekly plan: "botsy"). Every order is checked here, in this order, and the first
stage that fails refuses it with fixed reason codes (never free text):

1. format    the order/v1 shape: a known kind, no unknown fields, every value of its fixed type
             (ids, niches, sizes, money and reason codes from fixed lists; there is no free-text
             field at all, so nothing Hermes types reaches a model or an event);
   content   the caller's own content check (the plan service checks its briefs), optional;
2. switches  the state allows it: limits file in force (fail closed), not paused, the plan or
             item it names exists and is in the right state (an item that failed QA or is
             not ready can never be approved), scale-ups refused (no_winners_yet);
3. limits    the admin-only limits file: Hermes's 20 changes an hour, 5 search ideas a week,
             the size of an approval batch (approve_batch_max) and the Level 3 approvals in
             any 24 hours (level3_per_day; rate counters in data/orders/counters.json);
4. capacity  service/capacity.py (plans, start-now and scale-ups only);
5. cost      the cap in force: a plan must fit in the money left this month; a cap raise stays
             at or under the ceiling; a lower cap must be lower.

Levels: 1 suggest (stored only), 2 small change (capped and undoable), 3 your approval (every
review approval, raising the cap, restarting paid work after a budget stop, resuming after
Botsy's own stop or Hermes lifting your pause, the first 4 weekly plans and plans over 40% of
the cap, a KDP kit approval). There are no codes: a Level 3 order sent by you (CLI, also
`python -m qa review approve`) or by Hermes (the allowlisted Discord user) is your approval
and is applied at once by submit(); a plan that waits for you is approved with approve().
Guards: hard-blocked items are never approved, raise_cap never goes above the root-only
ceiling, every approval is posted at once to the main Discord channel (urgent: it ignores
quiet hours) with what changed, who asked and how to undo it, and all journaled with fixed
fields. limits level3_per_day caps approvals in 24 hours (user, 2026-10-21, D1): every
approval Hermes makes counts; yours count only when they lead to posting on Etsy or KDP
(review_approve, batches included, and kdp_kit_approve: counts_toward_level3); your pause,
resume, cap and plan commands are never counted (still posted and journaled).

Plan Holds (D3): a plan_hold records who set it (user | hermes | botsy). A Hold sticks until
someone of the same or a higher rank lifts it (may_lift: user > hermes > botsy), so your Hold
can only be lifted by an order of yours: Hermes's plan_start_now, its approval of a waiting
plan and its plan_revision / weekly_plan of that week are refused with held_by_user, a Hold
Hermes sends never replaces yours, the automatic start never lifts it, and a revision or a new
plan of the week inherits it.

Fixes (D4.7): fix_retry_run, fix_clear_stale_lock, fix_rerun_qa, fix_resend_daily_log and
fix_retry_notify are Level 2 orders for the fixed watchdog recipes in service/fixes.py, which
checks their hard preconditions (stage "switches", with the facts journaled) and again when
it runs them. Hermes may send at most limits fixes_per_day (6) in 24 hours; a run retry also
counts toward run_now_per_day. Each applied fix is posted and journaled (fix_applied).

Accepted orders are written to data/orders/<week>/<id>.json (settings control.orders_dir);
refused ones are not stored. Every decision is journaled (core/events.py order_accepted,
order_refused, order_applied, order_approved, order_undone, order_failed) with fixed fields
only: ids, kinds, reason codes, counts. Each applied order records how to undo it.

Without a limits file (service/limits.py fail closed), only the orders that stop work or make
things stricter still pass: pause, order_cancel, plan_hold, lower_cap, review_hold. Level 1
search ideas, review approvals and restarting after a budget stop also still pass (with the
shipped level3_per_day); raise_cap does not, because its ceiling is in that file.

The pause flag, the cap in force (a raise or lower order) and the budget stop live in the
control state file data/control/state.json (settings control.state_file), written only here.
The tick reads paid_work_block() and core.spend reads effective_cap() (cross-component
wiring, docs/CONTRACTS.md order/v1).

While pytest runs, order and control files inside the repo are refused (RuntimeError):
tests point control.orders_dir and control.state_file into tmp_path.
"""
from __future__ import annotations

import contextlib
import contextvars
import copy
import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import events
from core.config import ROOT, load_settings, now_local, resolve, section
from core.items import read_json, valid_item_id, write_json

log = logging.getLogger("service.orders")

SCHEMA = "order/v1"
CONTROL_SCHEMA = "control/v1"
COUNTERS_SCHEMA = "order-counters/v1"
CREATORS = ("hermes", "user", "botsy")
STATUSES = ("waiting_approval", "accepted", "applied", "undone", "cancelled", "failed")
STAGES = ("format", "content", "switches", "limits", "capacity", "cost", "apply", "approval")

SETTINGS_DEFAULTS: dict = {
    "orders_dir": "data/orders",
    "state_file": "data/control/state.json",
    "winners_file": "data/performance/winners.json",
}

# kind -> level and id prefix ("su-20261115-0001", as in docs/OVERSEER.md A5).
KINDS: dict = {
    "weekly_plan": {"level": 2, "prefix": "pl"},
    "plan_revision": {"level": 2, "prefix": "pr"},
    "plan_hold": {"level": 2, "prefix": "ph"},
    "plan_start_now": {"level": 2, "prefix": "ps"},
    "scale_up": {"level": 2, "prefix": "su"},
    "order_cancel": {"level": 2, "prefix": "oc"},
    "pause": {"level": 2, "prefix": "pa"},
    "resume": {"level": 2, "prefix": "re"},
    "lower_cap": {"level": 2, "prefix": "lc"},
    "review_reject": {"level": 2, "prefix": "rj"},
    "review_hold": {"level": 2, "prefix": "rh"},
    "review_approve": {"level": 3, "prefix": "ra"},
    "raise_cap": {"level": 3, "prefix": "rc"},
    "resume_after_budget_stop": {"level": 3, "prefix": "rb"},
    "search_ideas": {"level": 1, "prefix": "si"},
    # A KDP kit approval (the step that leads to a KDP upload). A placeholder until the KDP build:
    # always refused with kdp_not_built.
    "kdp_kit_approve": {"level": 3, "prefix": "ka"},
    # Fixed watchdog fixes (service/fixes.py; user, 2026-10-21 D4.7). Level 2, Botsy checks the
    # preconditions itself, limits fixes_per_day.
    "fix_retry_run": {"level": 2, "prefix": "fr"},
    "fix_clear_stale_lock": {"level": 2, "prefix": "fl"},
    "fix_rerun_qa": {"level": 2, "prefix": "fq"},
    "fix_resend_daily_log": {"level": 2, "prefix": "fd"},
    "fix_retry_notify": {"level": 2, "prefix": "fn"},
}
FIX_KINDS = ("fix_retry_run", "fix_clear_stale_lock", "fix_rerun_qa", "fix_resend_daily_log", "fix_retry_notify")
PLAN_KINDS = ("weekly_plan", "plan_revision")
# Only stop work or make things stricter: allowed without a limits file, never rate-refused.
STOP_ONLY = ("pause", "order_cancel", "plan_hold", "lower_cap", "review_hold")
# Applied at once by submit() when no approval is needed.
IMMEDIATE = ("pause", "resume", "lower_cap", "review_reject", "review_hold", "order_cancel", "plan_hold",
             "plan_start_now", "search_ideas") + FIX_KINDS
# Level 3 orders that are your approval when you (CLI) or Hermes send them: submit() applies
# them at once through approve() (counted, posted urgent, journaled). From Botsy they wait.
DIRECT_APPROVAL = ("review_approve", "raise_cap", "resume_after_budget_stop", "resume", "kdp_kit_approve")
# Approvals that lead to posting on Etsy (a review approval lets the items become drafts) or KDP
# (a kit approval). User, 2026-10-21 (D1): limits level3_per_day counts your own approvals only
# when they are one of these; every approval Hermes makes counts, whatever its kind.
POSTING_KINDS = ("review_approve", "kdp_kit_approve")
# Who may lift whose plan Hold (D3): a Hold sticks until someone of the same or a higher rank
# lifts it. Your Hold can only be lifted by you; Hermes's or Botsy's by you or Hermes.
HOLD_RANK = {"botsy": 0, "hermes": 1, "user": 2}
REVIEW_KINDS = ("review_approve", "review_reject", "review_hold")
APPROVERS = ("user", "hermes")
CANCELLABLE = ("weekly_plan", "plan_revision", "search_ideas", "review_approve", "raise_cap",
               "resume_after_budget_stop", "resume")
# Capacity reasons that stop a plan (money is checked against the cap in the cost stage;
# the 80% warning line is for scale-ups only).
PLAN_CAPACITY_REASONS = ("review_queue_full", "drafts_waiting", "disk_low", "money_unknown")

REJECT_REASONS = ("print_defect", "off_theme", "low_quality", "lookalike", "trademark_risk", "medical_wording",
                  "duplicate", "wrong_size", "user_dislike", "other_rule")
HOLD_REASONS = ("needs_a_look", "possible_lookalike", "possible_trademark", "possible_medical", "print_check",
                "user_request", "undo_approval")
PAUSE_REASONS = ("user_request", "incident", "spend", "quality", "other")
SCALE_KINDS = ("pack_size", "sibling_niche", "adjacent_theme", "kdp_seed", "pod_product", "fresh_volume")
WINNER_REASONS = ("sales_winner", "interest_winner", "kdp_winner")

# Fixed reason codes (docs/CONTRACTS.md order/v1 has the meaning of each).
REASONS = (
    # format
    "bad_schema", "not_an_object", "unknown_kind", "bad_created_by", "unknown_field", "missing_field",
    "bad_field", "client_ref_reused",
    # switches
    "limits_unavailable", "paused", "already_paused", "not_paused", "no_budget_stop", "wrong_week",
    "control_state_unreadable", "plan_exists", "no_plan", "no_plan_to_revise", "too_many_revisions",
    "plan_needs_approval", "plan_already_started", "plan_held", "before_start_time", "plan_expired", "superseded",
    "brief_niche_mismatch", "no_winners_yet", "scale_up_not_built", "no_such_order", "not_hermes_order",
    "held_by_user", "kdp_not_built",
    "not_cancellable", "item_unknown", "item_not_pending", "item_failed_qa", "item_not_ready",
    "already_rejected", "already_drafted", "blocklisted_term", "health_claim",
    # limits
    "change_limit_reached", "search_ideas_limit", "batch_too_large", "level3_daily_limit", "fixes_limit",
    "run_now_limit",
    # fixes: service/fixes.py REASONS (preconditions, stage "switches" or "apply")
    # capacity: service/capacity.py REASONS
    # cost
    "over_cap", "above_ceiling", "not_a_raise", "not_lower", "money_unknown",
    # apply / undo
    "not_applicable", "approval_required", "not_waiting", "not_an_approver", "state_changed", "nothing_to_undo",
    "apply_failed",
)
REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,40}")

WEEK_RE = re.compile(r"\d{4}-W(0[1-9]|[1-4]\d|5[0-3])")
ORDER_ID_RE = re.compile(r"[a-z]{2}-\d{8}-\d{4}")
BRIEF_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,40}-\d{4}-\d{2}-\d{2}-\d{1,4}")
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
CLIENT_REF_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
WINNER_REF_RE = re.compile(r"win-[a-z0-9_-]{1,100}")
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?")
QUERY_RE = re.compile(r"[a-z0-9](?:[a-z0-9 '&-]{0,58}[a-z0-9])?")
ITEM_MAX = 160

# The fixed cost table of docs/OVERSEER.md A3 (used before Botsy has 30 days of real costs,
# and whenever the caller does not pass Botsy's own estimate). Estimates, not measurements.
COST_TABLE = {
    "coloring_pages": ("per_page", 0.15),     # a fresh 20-page coloring pack is about $3.00
    "stickers": ("per_page", 0.07),           # 20 stickers about $1.40
    "activity_sheets": ("per_pack", 0.20),    # procedural pack (listing copy)
    "stationery": ("per_pack", 0.20),
    "ttrpg": ("per_pack", 0.20),
}
DERIVED_PACK_USD = 0.15                       # a smaller pack cut from the master set (listing copy)
KDP_BOOK_USD = 3.00
OTHER_PACK_USD = 0.20

_HANDLERS: dict[str, dict] = {}


class _Refused(Exception):
    def __init__(self, stage: str, reasons, fields=(), facts=None):
        super().__init__(stage)
        self.stage = stage
        self.reasons = [r for r in dict.fromkeys(reasons)]
        self.fields = list(dict.fromkeys(fields))
        self.facts = facts


# ---------------------------------------------------------------- paths, time, files

def _cfg(settings: dict | None) -> dict:
    return section(settings or {}, "control", SETTINGS_DEFAULTS)


def orders_dir(settings: dict | None = None) -> Path:
    return resolve(_cfg(settings).get("orders_dir") or SETTINGS_DEFAULTS["orders_dir"])


def state_path(settings: dict | None = None) -> Path:
    return resolve(_cfg(settings).get("state_file") or SETTINGS_DEFAULTS["state_file"])


def winners_path(settings: dict | None = None) -> Path:
    return resolve(_cfg(settings).get("winners_file") or SETTINGS_DEFAULTS["winners_file"])


def _guard(path: Path) -> None:
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return
    raise RuntimeError(f"test run: point settings control.* into tmp_path, not {path}")


def _repo_file_in_test(path: Path) -> bool:
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return False
    return True


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(ts) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def iso_week(when: datetime, settings: dict | None = None) -> str:
    """The ISO week ("2026-W43") of `when` on Botsy's clock (schedule.timezone)."""
    local = now_local(settings, when)
    return f"{local:%G}-W{local:%V}"


def plan_weeks(settings: dict | None = None, now: datetime | None = None) -> tuple[str, str]:
    """(this week, next week) on Botsy's clock: the weeks a plan may be for."""
    now = _utc(now)
    return iso_week(now, settings), iso_week(now + timedelta(days=7), settings)


def _lock(settings: dict | None):
    from service.lock import service_lock

    d = orders_dir(settings)
    _guard(d)
    d.mkdir(parents=True, exist_ok=True)
    return service_lock(d / ".lock", 30)


def _order_path(settings: dict | None, week: str, order_id: str) -> Path:
    if not WEEK_RE.fullmatch(week) or not ORDER_ID_RE.fullmatch(order_id):
        raise ValueError("bad order path")
    return orders_dir(settings) / week / f"{order_id}.json"


def list_orders(settings: dict | None = None, *, week: str | None = None, kind: str | None = None,
                status=None) -> list[dict]:
    """Stored orders, oldest first; filtered by week, kind and status (a name or a list)."""
    base = orders_dir(settings)
    if week is not None and not (isinstance(week, str) and WEEK_RE.fullmatch(week)):
        return []                      # never a path: botsy-control may pass Hermes's text here
    if isinstance(status, str):
        status = {status}
    try:
        weeks = [base / week] if week else sorted(p for p in base.iterdir() if p.is_dir() and WEEK_RE.fullmatch(p.name))
    except OSError:
        return []
    out = []
    for d in weeks:
        try:
            files = sorted(d.glob("??-????????-????.json"))
        except OSError:
            continue
        for p in files:
            try:
                o = read_json(p)
            except (OSError, ValueError):
                log.warning("skipping unreadable order %s", p.name)
                continue
            if not isinstance(o, dict) or o.get("schema") != SCHEMA:
                continue
            if kind and o.get("kind") != kind:
                continue
            if status is not None and o.get("status") not in status:
                continue
            out.append(o)
    return sorted(out, key=lambda o: (str(o.get("created_at")), str(o.get("id"))))


def load(order_id: str, settings: dict | None = None) -> dict | None:
    """The stored order with this id, or None."""
    if not isinstance(order_id, str) or not ORDER_ID_RE.fullmatch(order_id):
        return None
    base = orders_dir(settings)
    try:
        for p in base.glob(f"*/{order_id}.json"):
            o = read_json(p)
            if isinstance(o, dict) and o.get("id") == order_id:
                return o
    except (OSError, ValueError):
        return None
    return None


def _save(settings: dict | None, order: dict) -> None:
    write_json(_order_path(settings, order["week"], order["id"]), order)


MAX_PER_KIND_AND_DAY = 9999      # ids have 4 digits; more orders of one kind in a day are refused


def _new_id(settings: dict | None, kind: str, now: datetime) -> str | None:
    """The next free id of this kind today, or None when the day's 9999 are used up."""
    local = now_local(settings, now)
    stem = f"{KINDS[kind]['prefix']}-{local:%Y%m%d}-"
    used = {p.stem for p in orders_dir(settings).glob(f"*/{stem}????.json")}
    n = 1
    while f"{stem}{n:04d}" in used:
        n += 1
    return f"{stem}{n:04d}" if n <= MAX_PER_KIND_AND_DAY else None


# ---------------------------------------------------------------- control state (pause, cap, budget stop)

def _empty_state() -> dict:
    return {"schema": CONTROL_SCHEMA, "paused": None, "cap_override_usd": None, "cap_changed": None,
            "budget_stop": None}


def control_state(settings: dict | None = None) -> dict:
    """data/control/state.json: {"paused", "cap_override_usd", "cap_changed", "budget_stop"}.
    A missing file means nothing is set. An unreadable one fails closed: paused by Botsy
    (reason control_state_unreadable), so a lost pause or lower cap never means "go"."""
    p = state_path(settings)
    if _repo_file_in_test(p):
        return _empty_state()          # a test never reads the real data/control (core.spend calls this)
    try:
        doc = read_json(p)
    except FileNotFoundError:
        return _empty_state()
    except (OSError, ValueError):
        doc = None
    if not isinstance(doc, dict) or doc.get("schema") != CONTROL_SCHEMA:
        st = _empty_state()
        st.update({"paused": {"by": "botsy", "since": None, "order_id": None,
                              "reason_code": "control_state_unreadable"}, "unreadable": True})
        return st
    st = _empty_state()
    st.update({k: doc.get(k) for k in st if k != "schema"})
    if any(st.get(k) is not None and not isinstance(st.get(k), dict) for k in ("paused", "budget_stop")):
        st["paused"] = {"by": "botsy", "since": None, "order_id": None, "reason_code": "control_state_unreadable"}
        st["budget_stop"] = st["budget_stop"] if isinstance(st.get("budget_stop"), dict) else None
        st["unreadable"] = True        # "paused": true or the like: damaged, so paused (fail closed)
    cap = st.get("cap_override_usd")
    if cap is not None and (isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap != cap or cap <= 0):
        st["cap_override_usd"] = None
        st["paused"] = st.get("paused") if isinstance(st.get("paused"), dict) else \
            {"by": "botsy", "since": None, "order_id": None, "reason_code": "control_state_unreadable"}
        st["unreadable"] = True
    return st


def _write_state(settings: dict | None, state: dict) -> None:
    p = state_path(settings)
    _guard(p)
    if state.get("unreadable") and p.exists():
        # Keep the damaged file for the admin: it may hold a pause, a lower cap or a budget stop
        # Botsy could not read. The pause by Botsy it stood for is written on (fail closed).
        keep = p.with_name(f"{p.name}.unreadable-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
        try:
            shutil.copy2(p, keep)
            log.warning("control state %s could not be read; kept a copy as %s", p.name, keep.name)
        except OSError as e:
            log.warning("control state %s could not be read or copied (%s)", p.name, type(e).__name__)
    write_json(p, {**_empty_state(), **{k: v for k, v in state.items() if k != "unreadable"},
                   "schema": CONTROL_SCHEMA})


def paused(settings: dict | None = None) -> dict | None:
    """The pause in force ({"by", "since", "order_id", "reason_code"}) or None."""
    p = control_state(settings).get("paused")
    return p if isinstance(p, dict) else None


def settings_cap(settings: dict | None = None) -> float | None:
    """spend.monthly_cap_usd as dollars (None = no cap). Raises ValueError for a typo."""
    from core import spend

    return spend.monthly_cap(section(settings if settings is not None else load_settings(), "spend",
                                     spend.DEFAULTS))


def effective_cap(settings: dict | None = None, *, limits=None) -> float | None:
    """The monthly cap in force: a raise_cap / lower_cap order's value when one is set, else
    spend.monthly_cap_usd. Never above the limits file's cap_ceiling_usd, whichever it comes
    from (a cap typed into settings.local.yaml cannot pass the admin's ceiling either). With the
    limits file missing or broken, an override can only lower the settings cap, and the shipped
    ceiling ($100) applies (fail closed). No cap in the settings (null) and no override stays
    no cap: check_budget then only records."""
    from service import limits as limits_mod

    base = settings_cap(settings)
    override = control_state(settings).get("cap_override_usd")
    lim = limits if limits is not None else limits_mod.load(settings)
    ceiling = float(lim.get("cap_ceiling_usd") if lim.ok else limits_mod.DEFAULTS["cap_ceiling_usd"])
    if override is None:
        cap = base
    elif lim.ok or base is None:
        cap = float(override)
    else:
        cap = min(float(override), base)
    if cap is None:
        return None
    return round(min(cap, ceiling), 2) if (override is not None or cap > ceiling) else cap


def paid_work_block(settings: dict | None = None, *, now: datetime | None = None) -> str | None:
    """Why paid work must not start: "paused", "budget_stop", or None. The tick checks this."""
    st = control_state(settings)
    if isinstance(st.get("paused"), dict):
        return "paused"
    stop = st.get("budget_stop")
    if isinstance(stop, dict):
        from core.spend import month_of

        if stop.get("month") == month_of(_utc(now)):
            was = stop.get("cap_usd")
            if _num(was):
                try:
                    cap = effective_cap(settings)
                except ValueError:
                    return "budget_stop"
                if cap is None or cap > float(was):
                    return None        # the admin raised the cap in the settings since: paid work may go on
            return "budget_stop"
    return None


def note_budget_stop(settings: dict | None = None, *, month: str | None = None, now: datetime | None = None,
                     reason_code: str = "budget_reached") -> bool:
    """Record that Botsy stopped paid work for the cap this month (the tick calls this when it
    records budget_reached). It clears itself when the month changes, and stops counting when
    the cap in force rises above the cap it stopped at (the admin raised spend.monthly_cap_usd);
    otherwise only a Level 3 order (raise_cap or resume_after_budget_stop) clears it. True when
    newly set."""
    from core.spend import month_of

    now = _utc(now)
    month = month or month_of(now)
    with _lock(settings):
        st = control_state(settings)
        stop = st.get("budget_stop")
        if isinstance(stop, dict) and stop.get("month") == month:
            return False
        try:
            cap = effective_cap(settings)
        except ValueError:
            cap = None
        st["budget_stop"] = {"month": month, "since": _iso(now), "cap_usd": cap,
                             "reason_code": reason_code if REASON_RE.fullmatch(str(reason_code)) else "budget_reached"}
        _write_state(settings, st)
    return True


# ---------------------------------------------------------------- counters

def _counters_path(settings: dict | None) -> Path:
    return orders_dir(settings) / "counters.json"


def _counters(settings: dict | None) -> list[dict]:
    try:
        doc = read_json(_counters_path(settings))
    except (OSError, ValueError):
        return []
    rows = doc.get("events") if isinstance(doc, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


def _count(rows: list[dict], counter: str, by: str | None, since: datetime) -> int:
    n = 0
    for r in rows:
        ts = _parse(r.get("ts"))
        if r.get("counter") == counter and (by is None or r.get("by") == by) and ts and ts >= since:
            v = r.get("n")
            n += v if isinstance(v, int) and not isinstance(v, bool) else 1
    return n


def _bump(settings: dict | None, now: datetime, counter: str, by: str, n: int = 1) -> None:
    rows = [r for r in _counters(settings) if (_parse(r.get("ts")) or now) >= now - timedelta(days=8)]
    rows.append({"ts": _iso(now), "counter": counter, "by": by, "n": int(n)})
    write_json(_counters_path(settings), {"schema": COUNTERS_SCHEMA, "events": rows})


def counts(settings: dict | None = None, *, now: datetime | None = None) -> dict:
    """Hermes's use of the rate limits right now, and the Level 3 approvals of the last 24 hours
    by anyone (for status pages and botsy-control)."""
    now = _utc(now)
    rows = _counters(settings)
    week_start = _week_start(now, settings)
    return {"changes_last_hour": _count(rows, "changes", "hermes", now - timedelta(hours=1)),
            "search_ideas_this_week": _count(rows, "search_ideas", "hermes", week_start),
            "page_redos_this_week": _count(rows, "page_redos", "hermes", week_start),
            "run_now_today": _count(rows, "run_now", "hermes", now - timedelta(days=1)),
            "fixes_today": _count(rows, "fixes", "hermes", now - timedelta(days=1)),
            "level3_last_24h": _count(rows, "level3", None, now - timedelta(hours=24))}


def counts_toward_level3(kind: str, by: str) -> bool:
    """Whether an approval counts toward limits level3_per_day (user, 2026-10-21, D1): every
    approval Hermes makes counts; yours count only when they lead to posting on Etsy or KDP
    (review approvals, KDP kit approvals). Your pause, cap, resume and plan commands are still
    posted and journaled, just not counted."""
    if by == "hermes":
        return True
    return by == "user" and kind in POSTING_KINDS


LEVEL3_PER_DAY = 10          # limits level3_per_day when the file is missing or broken


def _level3_limit(settings: dict | None, lim=None) -> int:
    lim = lim if lim is not None else _limits(settings or {}, None)
    raw = lim.get("level3_per_day", LEVEL3_PER_DAY) if lim.ok else LEVEL3_PER_DAY
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= 1000 else LEVEL3_PER_DAY


def _level3_used(settings: dict | None, now: datetime) -> int:
    """Level 3 approvals in the last 24 hours, whoever asked (a stamp later than now counts too:
    setting the clock back never frees the limit)."""
    return _count(_counters(settings), "level3", None, now - timedelta(hours=24))


def level3_room(settings: dict | None = None, *, now: datetime | None = None, limits=None) -> bool:
    """Whether one more Level 3 approval fits in limits level3_per_day (10) for the last 24 hours."""
    settings = settings if settings is not None else load_settings()
    return _level3_used(settings, _utc(now)) < _level3_limit(settings, limits)


def level3_take(settings: dict | None, action: str, by: str, *, now: datetime | None = None) -> bool:
    """Count one Level 3 approval that is not an order (service/plan.py `plan auto`), under the
    orders lock. False (nothing counted) when the day's limit is used up or `by` is not an
    approver. Your own `action` that does not lead to posting (counts_toward_level3: anything
    but review_approve / kdp_kit_approve) is allowed and not counted (D1). Never call it from
    inside an order handler (it takes data/orders/.lock)."""
    settings = settings if settings is not None else load_settings()
    if by not in APPROVERS or not isinstance(action, str) or not REASON_RE.fullmatch(action):
        return False
    if not counts_toward_level3(action, by):
        return True
    now = _utc(now)
    with _lock(settings):
        if not level3_room(settings, now=now):
            return False
        _bump(settings, now, "level3", by)
    return True


def _week_start(now: datetime, settings: dict | None) -> datetime:
    local = now_local(settings, now)
    start = (local - timedelta(days=local.isoweekday() - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


# ---------------------------------------------------------------- format

def _niches(settings: dict) -> tuple[str, ...]:
    n = settings.get("niches")
    return tuple(n) if isinstance(n, dict) else ()


def _item_ok(value) -> bool:
    return isinstance(value, str) and len(value) <= ITEM_MAX and valid_item_id(value)


def _specs(settings: dict) -> dict:
    niche = ("enum", _niches(settings))
    size = ("int", 1, 500)
    line = ("obj", {"niche": (True, niche), "brief_id": (True, ("re", BRIEF_ID_RE)),
                    "sizes": (True, ("list", size, 1, 8, True))})
    kdp = ("obj", {"brief_id": (True, ("re", BRIEF_ID_RE)), "template": (True, ("re", SLUG_RE))})
    plan = {"plan_week": (True, ("re", WEEK_RE)), "lines": (True, ("list", line, 1, 40, False)),
            "kdp": (False, ("list", kdp, 0, 2, False))}
    action = ("obj", {"kind": (True, ("enum", SCALE_KINDS)), "niche": (True, niche),
                      "brief_id": (True, ("re", BRIEF_ID_RE)), "size": (False, size),
                      "sizes": (False, ("list", size, 1, 8, True)), "adjacency_ref": (False, ("re", SLUG_RE)),
                      "set_id": (False, ("re", SLUG_RE)), "kdp_template": (False, ("re", SLUG_RE))})
    idea = ("obj", {"niche": (True, niche), "query": (True, ("re", QUERY_RE))})
    item = ("item",)
    return {
        "weekly_plan": plan,
        "plan_revision": {**plan, "replaces": (True, ("re", ORDER_ID_RE))},
        "plan_hold": {"plan_week": (True, ("re", WEEK_RE))},
        "plan_start_now": {"plan_week": (True, ("re", WEEK_RE))},
        "scale_up": {"plan_week": (True, ("re", WEEK_RE)), "winner_ref": (True, ("re", WINNER_REF_RE)),
                     "reason_code": (True, ("enum", WINNER_REASONS)),
                     "actions": (True, ("list", action, 1, 3, False)),
                     "max_cost_usd": (True, ("money", 0, 50))},
        "order_cancel": {"order_id": (True, ("re", ORDER_ID_RE))},
        "pause": {"reason_code": (False, ("enum", PAUSE_REASONS))},
        "resume": {},
        "lower_cap": {"cap_usd": (True, ("money", 1, 10000))},
        "raise_cap": {"cap_usd": (True, ("money", 1, 10000))},
        "resume_after_budget_stop": {},
        "review_reject": {"item_id": (True, item), "reason_code": (True, ("enum", REJECT_REASONS))},
        "review_hold": {"item_id": (True, item), "reason_code": (True, ("enum", HOLD_REASONS))},
        "review_approve": {"item_ids": (True, ("list", item, 1, 200, True)), "batch": (False, ("re", SLUG_RE))},
        "search_ideas": {"ideas": (True, ("list", idea, 1, 20, False))},
        "kdp_kit_approve": {"kit_id": (True, ("re", SLUG_RE))},
        "fix_retry_run": {"tick_started": (False, ("re", ISO_RE))},
        "fix_clear_stale_lock": {"lock": (True, ("enum", ("service", "plans")))},
        "fix_rerun_qa": {"item_id": (True, item)},
        "fix_resend_daily_log": {},
        "fix_retry_notify": {},
    }


def _value(spec, value, path: str, problems: list[tuple[str, str]]):
    kind = spec[0]
    if kind == "re":
        if isinstance(value, str) and spec[1].fullmatch(value):
            return value
    elif kind == "enum":
        if isinstance(value, str) and value in spec[1]:
            return value
    elif kind == "item":
        if _item_ok(value):
            return value
    elif kind == "int":
        if isinstance(value, int) and not isinstance(value, bool) and spec[1] <= value <= spec[2]:
            return value
    elif kind == "money":
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
                and spec[1] <= value <= spec[2]:
            return round(float(value), 2)
    elif kind == "list":
        _, item, low, high, unique = spec
        if isinstance(value, list) and low <= len(value) <= high:
            out = [_value(item, v, f"{path}[{i}]", problems) for i, v in enumerate(value)]
            if unique and len({json.dumps(v, sort_keys=True) for v in out}) != len(out):
                problems.append(("bad_field", path))
            return out
    elif kind == "obj":
        return _object(spec[1], value, path, problems)
    problems.append(("bad_field", path))
    return None


def _object(fields: dict, value, path: str, problems: list[tuple[str, str]]) -> dict | None:
    if not isinstance(value, dict):
        problems.append(("bad_field", path))
        return None
    out = {}
    for key in value:
        if key not in fields:
            problems.append(("unknown_field", path))       # never the name: it is the sender's text
    for key, (required, spec) in fields.items():
        if key not in value or value[key] is None:
            if required:
                problems.append(("missing_field", f"{path}.{key}"))
            continue
        out[key] = _value(spec, value[key], f"{path}.{key}", problems)
    return out


ORDER_FIELDS = ("schema", "kind", "created_by", "payload", "client_ref")


def _format(order, settings: dict) -> tuple[dict, list[tuple[str, str]]]:
    problems: list[tuple[str, str]] = []
    if not isinstance(order, dict):
        return {}, [("not_an_object", "order")]
    if any(k not in ORDER_FIELDS for k in order):
        problems.append(("unknown_field", "order"))
    if order.get("schema", SCHEMA) != SCHEMA:
        problems.append(("bad_schema", "order.schema"))
    kind = order.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        problems.append(("unknown_kind", "order.kind"))
        kind = None
    by = order.get("created_by")
    if by not in CREATORS:
        problems.append(("bad_created_by", "order.created_by"))
        by = None
    ref = order.get("client_ref")
    if ref is not None and not (isinstance(ref, str) and CLIENT_REF_RE.fullmatch(ref)):
        problems.append(("bad_field", "order.client_ref"))
    payload = {}
    if kind:
        raw = order.get("payload", {})
        payload = _object(_specs(settings)[kind], raw if raw is not None else {}, "payload", problems) or {}
    norm = {"schema": SCHEMA, "kind": kind, "created_by": by, "payload": payload}
    if ref is not None:
        norm["client_ref"] = ref
    return norm, problems


# ---------------------------------------------------------------- helpers the stages share

# The output root and review queue a review order of yours works on when you name them
# (`python -m qa review approve --out DIR / --review-dir DIR / --dry-run`). Set only by
# submit(target=...) from Python callers on the server (never from an order's fields, so
# Hermes cannot set it), stored on the order as "target" and used while that order is
# checked, applied or undone.
_TARGET: contextvars.ContextVar = contextvars.ContextVar("orders_target", default=None)


@contextlib.contextmanager
def _targeting(target):
    tok = _TARGET.set(target if isinstance(target, dict) and target.get("out") else None)
    try:
        yield
    finally:
        _TARGET.reset(tok)


def _norm_target(target) -> dict | None:
    if target is None:
        return None
    if not isinstance(target, dict) or not target.get("out"):
        raise ValueError("target must be {'out': DIR, 'review_dir': DIR?}")
    out = {"out": str(Path(target["out"]).resolve())}
    if target.get("review_dir"):
        out["review_dir"] = str(Path(target["review_dir"]).resolve())
    return out


def _work_root(settings: dict) -> Path:
    from core.items import dry_run_root, output_root
    from service.paths import is_live

    t = _TARGET.get()
    if t:
        return Path(t["out"])
    return output_root(settings) if is_live(settings) else dry_run_root(settings)


def review_dir(settings: dict) -> Path:
    """The review queue of the output Botsy works on (live: paths.review_dir; else the dry-run one)."""
    from core.items import review_dir_for_root

    t = _TARGET.get()
    if t and t.get("review_dir"):
        return Path(t["review_dir"])
    return review_dir_for_root(_work_root(settings), settings)


def _entry(settings: dict, item_id: str) -> dict | None:
    from qa.review import read_entry

    try:
        return read_entry(item_id, review_dir(settings))
    except (OSError, ValueError):
        return None


def _drafted(settings: dict, item_id: str) -> bool:
    p = _work_root(settings) / item_id / "publish.json"
    try:
        doc = read_json(p)
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True                    # unreadable counts as real (core.items.clear_downstream does the same)
    return isinstance(doc, dict) and doc.get("dry_run") is not True and bool(doc.get("listing_id"))


def _qa_passed(settings: dict, item_id: str) -> bool:
    try:
        rep = read_json(_work_root(settings) / item_id / "qa_report.json")
    except (OSError, ValueError):
        return False
    return isinstance(rep, dict) and rep.get("passed") is True


def current_plan(settings: dict | None, week: str) -> dict | None:
    """The newest weekly_plan / plan_revision order for `week` that is still in force."""
    live = [o for o in list_orders(settings, week=week) if o.get("kind") in PLAN_KINDS
            and o.get("status") in ("waiting_approval", "accepted", "applied")]
    return live[-1] if live else None


def _limits(settings: dict, limits):
    from service import limits as limits_mod

    return limits if limits is not None else limits_mod.load(settings)


def _max_revisions(settings: dict) -> int:
    from core.config import SCHEDULE_DEFAULTS

    raw = section(settings, "schedule", SCHEDULE_DEFAULTS).get("max_revisions", 3)
    return int(raw) if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else 3


def estimate_cost(payload: dict) -> float:
    """Botsy's fixed-table estimate (docs/OVERSEER.md A3) for a plan payload, in dollars.
    Per line: the largest size is made fresh (image niches per page, others per pack), each
    smaller size is cut from it ($0.15); each KDP book $3.00. An estimate, not a measurement."""
    total = 0.0
    for line in payload.get("lines") or []:
        sizes = sorted(s for s in line.get("sizes") or [] if isinstance(s, int))
        if not sizes:
            continue
        how, rate = COST_TABLE.get(line.get("niche"), ("per_pack", OTHER_PACK_USD))
        if how == "per_page":
            total += rate * sizes[-1] + DERIVED_PACK_USD * (len(sizes) - 1)
        else:
            total += rate * len(sizes)
    total += KDP_BOOK_USD * len(payload.get("kdp") or [])
    return round(total, 2)


def _auto_share(settings: dict, lim) -> float:
    from core.config import SCHEDULE_DEFAULTS

    raw = section(settings, "schedule", SCHEDULE_DEFAULTS).get("auto_start_max_cost_share", 0.40)
    share = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.40
    if lim.ok:
        share = min(share, float(lim.get("plan.max_cost_share_auto", 0.40)))
    return share


def plan_needs_approval(cost_usd: float, settings: dict | None = None, limits=None, *, approved_plans: int = 0,
                        approve_each: bool = False, cap: float | None = None) -> tuple[bool, str | None]:
    """Whether a weekly plan (or revision) waits for your approval, and why (docs/OVERSEER.md
    B3-B4): first_plans (fewer than schedule.approvals_before_auto approved plans),
    approve_each (you said "approve plans again"), limits_unavailable (fail closed),
    plan_over_auto_share (cost above the smaller of schedule.auto_start_max_cost_share and
    limits plan.max_cost_share_auto of the cap in force), money_unknown; else (False, None)."""
    from core.config import SCHEDULE_DEFAULTS

    settings = settings if settings is not None else load_settings()
    lim = _limits(settings, limits)
    raw = section(settings, "schedule", SCHEDULE_DEFAULTS).get("approvals_before_auto", 4)
    needed = raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else 4
    if not isinstance(approved_plans, int) or isinstance(approved_plans, bool) or approved_plans < needed:
        return True, "first_plans"
    if approve_each:
        return True, "approve_each"
    if not lim.ok:
        return True, "limits_unavailable"
    try:
        cap = cap if cap is not None else effective_cap(settings, limits=lim)
    except ValueError:
        return True, "money_unknown"
    if cap is not None and float(cost_usd) > _auto_share(settings, lim) * cap:
        return True, "plan_over_auto_share"
    return False, None


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _day_time(raw, default: tuple[int, int, int] = (1, 8, 0)) -> tuple[int, int, int]:
    """"Mon 08:00" -> (ISO weekday, hour, minute); anything else gives `default`."""
    m = re.fullmatch(r"\s*([A-Za-z]{3})[A-Za-z]*\s+(\d{1,2}):(\d{2})\s*", str(raw or ""))
    if m and m.group(1).lower() in _DAYS and int(m.group(2)) < 24 and int(m.group(3)) < 60:
        return _DAYS.index(m.group(1).lower()) + 1, int(m.group(2)), int(m.group(3))
    return default


def _week_auto_start(plan_week: str, settings: dict, key: str = "auto_start") -> datetime | None:
    """schedule.auto_start (Mon 08:00) in `plan_week`, on Botsy's clock, as UTC. With
    key="deadline": schedule.deadline (Tue 20:00) of that week."""
    from datetime import date

    from core.config import SCHEDULE_DEFAULTS, tz

    if not isinstance(plan_week, str) or not WEEK_RE.fullmatch(plan_week):
        return None
    day, hour, minute = _day_time(section(settings, "schedule", SCHEDULE_DEFAULTS).get(key))
    try:
        d = date.fromisocalendar(int(plan_week[:4]), int(plan_week[6:]), day)
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz(settings)).astimezone(timezone.utc)


def plan_deadline(plan_week: str, settings: dict) -> datetime | None:
    """The deadline of `plan_week`'s plan (UTC): service.plan.deadline_for (the stored one, a
    forced late plan keeps a later one), else schedule.deadline (Tue 20:00) of that week."""
    try:
        from service import plan as plan_mod

        return plan_mod.deadline_for(plan_week, settings)
    except Exception:  # noqa: BLE001 - the plan service or its state cannot be read: the schedule's own
        return _week_auto_start(plan_week, settings, "deadline")


def plan_start_at(order: dict, settings: dict | None = None) -> datetime | None:
    """The earliest time a plan that starts by itself may start (docs/OVERSEER.md B4):
    schedule.auto_start of its week (Mon 08:00 New York time), and at least
    schedule.revision_min_hours (2) after it was made, so neither a revision nor a new plan
    can skip your Hold window. A plan_start_now order lifts it; a plan you approve has none."""
    from core.config import SCHEDULE_DEFAULTS

    settings = settings if settings is not None else load_settings()
    start = _week_auto_start((order.get("payload") or {}).get("plan_week") or order.get("week"), settings)
    raw = section(settings, "schedule", SCHEDULE_DEFAULTS).get("revision_min_hours", 2)
    hours = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) and 0 <= raw <= 168 else 2.0
    made = _parse(order.get("created_at"))
    later = made + timedelta(hours=hours) if made else None
    times = [t for t in (start, later) if t is not None]
    return max(times) if times else None


def _own_money(settings: dict, when: datetime) -> tuple[float | None, float | None, str | None]:
    """(spent, cap in force, month) from Botsy's own ledger and control state; spent None when
    they cannot be read. Never from a capacity result a caller handed in."""
    from service import capacity as capacity_mod

    try:
        m = capacity_mod.money(settings, now=when)
    except (ValueError, OSError, TypeError, KeyError):
        return None, None, None
    return m.get("spent"), m.get("cap"), m.get("month")


def _plan_money_time(payload: dict, settings: dict, now: datetime) -> datetime:
    """The money month a plan is checked against: the month it starts in (docs/OVERSEER.md B7: a
    plan posted on the last Sunday of a month can start in the next one)."""
    start = _week_auto_start(payload.get("plan_week"), settings)
    return max(now, start) if start else now


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


# ---------------------------------------------------------------- plan holds (D3)

def hold_of(plan_order: dict | None) -> dict | None:
    """The plan order's Hold mark {"order_id", "by", "at"} or None."""
    h = (plan_order or {}).get("held")
    return h if isinstance(h, dict) else None


def held_by_user(plan_order: dict | None) -> bool:
    """A Hold you set (user, 2026-10-21, D3): only an order of yours lifts it."""
    return (hold_of(plan_order) or {}).get("by") == "user"


def may_lift(hold: dict | None, by: str | None) -> bool:
    """Whether `by` may lift (or start past) `hold`: a Hold sticks until someone of the same or
    a higher rank (user > hermes > botsy) lifts it."""
    if not hold:
        return True
    return HOLD_RANK.get(by, -1) >= HOLD_RANK.get(hold.get("by"), HOLD_RANK["user"])


def _week_hold(settings: dict, week: str | None, self_id: str | None = None) -> dict | None:
    """The Hold a new plan of `week` inherits: the one on the week's latest plan order (in force,
    replaced, cancelled or expired), so neither a revision nor cancel-and-replan drops it."""
    if not isinstance(week, str) or not WEEK_RE.fullmatch(week):
        return None
    plans = [o for o in list_orders(settings, week=week) if o.get("kind") in PLAN_KINDS and o.get("id") != self_id]
    return hold_of(plans[-1]) if plans else None


# ---------------------------------------------------------------- switches

def _switches(order: dict, settings: dict, lim, now: datetime, self_id: str | None = None) -> list[str]:
    kind, p = order["kind"], order["payload"]
    by = order.get("created_by")
    level = KINDS[kind]["level"]
    reasons: list[str] = []
    if kind == "kdp_kit_approve":
        return ["kdp_not_built"]          # a placeholder until the KDP build (D1 counts it as posting)
    if kind == "scale_up":
        # Not active until winners exist (about 20 listings live 30 days): the order type and its
        # refusal path are built, the winners engine is not. Fail closed whatever the file says.
        if not lim.scale_up_allowed or not winners_path(settings).is_file():
            return ["no_winners_yet"] + ([] if lim.ok else ["limits_unavailable"])
        return ["scale_up_not_built"]
    if not lim.ok and kind not in STOP_ONLY and (level == 2 or kind == "raise_cap"):
        return ["limits_unavailable"]
    state = control_state(settings)
    pause = state.get("paused") if isinstance(state.get("paused"), dict) else None
    if pause and (kind == "plan_start_now" or (kind == "resume_after_budget_stop" and pause.get("by") != "botsy")):
        reasons.append("paused")

    if kind in PLAN_KINDS:
        if p.get("plan_week") not in plan_weeks(settings, now):
            reasons.append("wrong_week")
        for line in p.get("lines") or []:
            if not str(line.get("brief_id", "")).startswith(str(line.get("niche")) + "-"):
                reasons.append("brief_niche_mismatch")
        plans = [o for o in list_orders(settings, week=p.get("plan_week")) if o.get("id") != self_id]
        live = [o for o in plans if o.get("kind") in PLAN_KINDS and o.get("status") in ("waiting_approval",
                                                                                       "accepted", "applied")]
        if by == "hermes" and not may_lift(_week_hold(settings, p.get("plan_week"), self_id), by):
            reasons.append("held_by_user")            # Hermes never revises or replaces past your Hold
            # (Botsy's own plan of a held week inherits the Hold instead: _validate)
        if kind == "weekly_plan" and live:
            reasons.append("plan_exists")
        elif kind == "weekly_plan" and len([o for o in plans if o.get("kind") in PLAN_KINDS]) \
                >= 1 + _max_revisions(settings):
            reasons.append("too_many_revisions")      # a new plan after a cancel counts as a version too
        if kind == "plan_revision":
            target = next((o for o in live if o.get("id") == p.get("replaces")), None)
            if target is None:
                reasons.append("no_plan_to_revise")
            done = [o for o in plans if o.get("kind") == "plan_revision"]     # every stored one, undone too
            if len(done) >= _max_revisions(settings):
                reasons.append("too_many_revisions")
    elif kind in ("plan_hold", "plan_start_now"):
        plan = current_plan(settings, p["plan_week"])
        if plan is None:
            reasons.append("no_plan")
        elif kind == "plan_start_now":
            if not may_lift(hold_of(plan), by):
                reasons.append("held_by_user")        # only you lift your Hold
            if plan.get("status") == "waiting_approval":
                reasons.append("plan_needs_approval")
            elif plan.get("status") == "applied":
                reasons.append("plan_already_started")
    elif kind == "order_cancel":
        target = load(p["order_id"], settings)
        if target is None:
            reasons.append("no_such_order")
        elif target.get("kind") not in CANCELLABLE or target.get("status") not in ("waiting_approval", "accepted") + (
                ("applied",) if target.get("kind") in PLAN_KINDS else ()):
            reasons.append("not_cancellable")
    elif kind == "pause":
        if pause:
            reasons.append("already_paused")
    elif kind == "resume":
        if not pause:
            reasons.append("not_paused")
        elif state.get("unreadable"):
            reasons.append("control_state_unreadable")     # resuming would forget what the file held
    elif kind == "resume_after_budget_stop":
        stop = state.get("budget_stop")
        from core.spend import month_of

        stopped = isinstance(stop, dict) and stop.get("month") == month_of(now)
        if state.get("unreadable"):
            reasons.append("control_state_unreadable")
        elif not stopped and not (pause and pause.get("by") == "botsy"):
            reasons.append("no_budget_stop")
    elif kind in ("review_reject", "review_hold"):
        entry = _entry(settings, p["item_id"])
        if entry is None:
            reasons.append("item_unknown")
        elif entry.get("status") == "rejected":
            reasons.append("already_rejected")
        elif _drafted(settings, p["item_id"]):
            reasons.append("already_drafted")
    elif kind == "review_approve":
        for item_id in p.get("item_ids") or []:
            entry = _entry(settings, item_id)
            if entry is None:
                reasons.append("item_unknown")
            elif entry.get("status") != "pending":
                reasons.append("item_not_pending")
            elif _drafted(settings, item_id):
                reasons.append("already_drafted")
            elif not _qa_passed(settings, item_id):
                reasons.append("item_failed_qa")
            elif not entry.get("content_digest"):
                reasons.append("item_not_ready")
    elif kind == "search_ideas":
        reasons += _idea_problems(p, settings)
    return list(dict.fromkeys(reasons))


def _idea_checks(payload: dict, settings: dict) -> list[dict]:
    """Per search idea: {"blocked", "flagged", "claim"} from the blocklist (with the health
    topics) and the health-claim check. Ideas are labelled "exploratory" on the search list."""
    from core.compliance import Blocklist
    from core.health import claim_problems

    comp = settings.get("compliance") or {}
    bl = Blocklist.load(comp.get("trademark_blocklist") or None, health_path=comp.get("health_terms") or None)
    out = []
    for idea in payload.get("ideas") or []:
        q = str(idea.get("query") or "")
        res = bl.check(q)
        out.append({"blocked": bool(res.blocked), "flagged": bool(res.flagged), "claim": bool(claim_problems(q))})
    return out


def _idea_problems(payload: dict, settings: dict) -> list[str]:
    reasons = []
    for c in _idea_checks(payload, settings):
        if c["blocked"]:
            reasons.append("blocklisted_term")
        if c["claim"]:
            reasons.append("health_claim")
    return reasons


# ---------------------------------------------------------------- limits, capacity, cost

def _limit_reasons(order: dict, settings: dict, lim, now: datetime) -> list[str]:
    kind, by, p = order["kind"], order["created_by"], order["payload"]
    reasons = []
    rows = _counters(settings)
    if by == "hermes" and KINDS[kind]["level"] == 2 and kind not in STOP_ONLY:
        if _count(rows, "changes", "hermes", now - timedelta(hours=1)) >= int(lim.get("changes_per_hour", 20)):
            reasons.append("change_limit_reached")
    if kind == "search_ideas" and by == "hermes":
        used = _count(rows, "search_ideas", "hermes", _week_start(now, settings))
        if used + len(p.get("ideas") or []) > int(lim.get("search_ideas_per_week", 5)):
            reasons.append("search_ideas_limit")
    if kind == "review_approve" and len(p.get("item_ids") or []) > int(lim.get("approve_batch_max", 20)):
        reasons.append("batch_too_large")
    if kind in DIRECT_APPROVAL and by in APPROVERS and _needs_approval(order, settings, {}) \
            and counts_toward_level3(kind, by) and not level3_room(settings, now=now, limits=lim):
        reasons.append("level3_daily_limit")
    if kind in FIX_KINDS and by == "hermes":
        if _count(rows, "fixes", "hermes", now - timedelta(days=1)) >= int(lim.get("fixes_per_day", 6)):
            reasons.append("fixes_limit")
    if kind == "fix_retry_run":
        # Retries count toward "run now" (limits run_now_per_day), whoever asks.
        if _count(rows, "run_now", None, now - timedelta(days=1)) >= int(lim.get("run_now_per_day", 3)):
            reasons.append("run_now_limit")
    return reasons


def _capacity_reasons(order: dict, cap_result: dict) -> list[str]:
    kind = order["kind"]
    got = list(cap_result.get("reasons") or [])
    if kind == "scale_up":
        return got
    if kind in PLAN_KINDS or kind == "plan_start_now":
        return [r for r in got if r in PLAN_CAPACITY_REASONS]
    return []


def _cost(order: dict, settings: dict, lim, cap_result: dict | None, cost_usd: float | None,
          context: dict, now: datetime | None = None) -> tuple[list[str], dict]:
    kind, p = order["kind"], order["payload"]
    info: dict = {}
    reasons: list[str] = []
    if kind in PLAN_KINDS:
        cost = round(float(cost_usd), 2) if _num(cost_usd) and cost_usd >= 0 else estimate_cost(p)
        info["cost_usd"] = cost
        # Money comes from Botsy's own read, for the month the plan starts in. A capacity result
        # the caller handed in can only make this stricter, never looser.
        spent, cap, month = _own_money(settings, _plan_money_time(p, settings, _utc(now)))
        facts = (cap_result or {}).get("facts") or {}
        if spent is not None and facts.get("month") in (None, month):
            if _num(facts.get("spent")):
                spent = max(spent, float(facts["spent"]))
            if _num(facts.get("cap")) and cap is not None:
                cap = min(cap, float(facts["cap"]))
        if spent is None:
            reasons.append("money_unknown")
        elif cap is not None and spent + cost > cap:
            reasons.append("over_cap")
        need, why = plan_needs_approval(cost, settings, lim, approved_plans=context.get("approved_plans", 0),
                                        approve_each=bool(context.get("approve_each")), cap=cap)
        info["needs_approval"], info["approval_reason"] = need, why
    elif kind == "review_approve":
        # What was checked is what gets approved: a regenerated item (new digest) is refused.
        info["items"] = {i: (_entry(settings, i) or {}).get("content_digest") for i in p.get("item_ids") or []}
    elif kind in ("raise_cap", "lower_cap"):
        try:
            current = effective_cap(settings, limits=lim)
        except ValueError:
            return ["money_unknown"], info
        new = p["cap_usd"]
        info.update({"cap_from_usd": current, "cap_to_usd": new})
        if kind == "raise_cap":
            ceiling = float(lim.get("cap_ceiling_usd", 100))
            info["ceiling_usd"] = ceiling
            if new > ceiling:
                reasons.append("above_ceiling")
            if current is not None and new <= current:
                reasons.append("not_a_raise")
        elif current is not None and new >= current:
            reasons.append("not_lower")
    return reasons, info


def _needs_approval(order: dict, settings: dict, info: dict) -> bool:
    kind = order["kind"]
    if KINDS[kind]["level"] == 3:
        return True
    if kind in PLAN_KINDS:
        return bool(info.get("needs_approval", True))
    if kind == "resume":
        # Botsy's own stop is a Level 3 approval; so is Hermes lifting a pause you set.
        p = paused(settings)
        return bool(p and (p.get("by") == "botsy" or (p.get("by") == "user" and order["created_by"] == "hermes")))
    return False


# ---------------------------------------------------------------- validate / submit

def _refuse(err: _Refused, norm: dict, settings: dict, store: bool) -> dict:
    reasons = [r for r in err.reasons if REASON_RE.fullmatch(r)]
    extra = {}
    if err.facts is not None:
        from service import fixes

        extra["facts"] = fixes.safe_facts(err.facts)
    if store:
        events.emit("order_refused", settings=settings, order_kind=norm.get("kind") or "unknown",
                    created_by=norm.get("created_by") or "unknown", stage=err.stage, reasons=reasons,
                    fields=err.fields, **extra)
    return {"ok": False, "stage": err.stage, "reasons": reasons, "fields": err.fields, "order": None,
            "order_id": None, "status": "refused", "needs_approval": False, **extra}


def _same_ref(settings: dict, norm: dict) -> dict | None:
    ref = norm.get("client_ref")
    if not ref:
        return None
    for o in list_orders(settings):
        if o.get("client_ref") == ref and o.get("created_by") == norm["created_by"]:
            return o
    return None


def validate(order, settings: dict | None = None, limits=None, capacity=None, *, store: bool = True,
             now: datetime | None = None, cost_usd: float | None = None, context: dict | None = None,
             content_check=None, target: dict | None = None) -> dict:
    """Check an order: format -> content -> switches -> limits -> capacity -> cost.

    Returns {"ok", "stage", "reasons", "fields", "needs_approval", "order_id", "status", "order",
    "duplicate", "capacity", "cost_usd"}. With store=True an accepted order is written to
    data/orders/<week>/<id>.json and the decision is journaled; store=False only checks.
    `limits`: a service.limits.Limits (default: read the file). `capacity`: a capacity/v1 result
    (default: service.capacity.check when the kind needs it). `cost_usd`: Botsy's own estimate of
    a plan (default: estimate_cost). `context`: {"approved_plans": int, "approve_each": bool}
    for plans (plan service; missing = 0 approved, so it waits for you). `content_check(order)`
    -> list of reason codes, run after the format stage (stage "content"). `target`:
    {"out": DIR, "review_dir": DIR?}, the output root and review queue a review order of yours
    works on (`python -m qa review approve --out/--review-dir/--dry-run`); only for orders with
    created_by "user", stored on the order (never an order field, so Hermes cannot set it)."""
    settings = settings if settings is not None else load_settings()
    now = _utc(now)
    context = dict(context or {})
    target = _norm_target(target)
    if target is not None:
        by = order.get("created_by") if isinstance(order, dict) else None
        kind = order.get("kind") if isinstance(order, dict) else None
        if by != "user" or kind not in REVIEW_KINDS:
            raise ValueError("a target is only for review orders you send (created_by 'user')")
    with _targeting(target):
        if store:
            with _lock(settings):          # checks and the write together: two plans cannot both pass
                res = _validate(order, settings, limits, capacity, True, now, cost_usd, context, content_check)
        else:
            res = _validate(order, settings, limits, capacity, False, now, cost_usd, context, content_check)
    return res


def _validate(order, settings: dict, limits, capacity, store: bool, now: datetime, cost_usd, context: dict,
              content_check) -> dict:
    norm, problems = _format(order, settings)
    try:
        if problems:
            raise _Refused("format", [c for c, _ in problems], [f for _, f in problems])
        dup = _same_ref(settings, norm)
        if dup is not None:
            if dup.get("kind") == norm["kind"] and dup.get("payload") == norm["payload"]:
                return {"ok": True, "duplicate": True, "stage": None, "reasons": [], "fields": [],
                        "needs_approval": bool(dup.get("needs_approval")), "order_id": dup["id"],
                        "status": dup.get("status"), "order": dup, "capacity": None,
                        "cost_usd": (dup.get("checks") or {}).get("cost_usd")}
            raise _Refused("format", ["client_ref_reused"], ["order.client_ref"])
        if content_check is not None:
            extra = [r for r in (content_check(copy.deepcopy(norm)) or []) if isinstance(r, str)
                     and REASON_RE.fullmatch(r)]
            if extra:
                raise _Refused("content", extra)
        lim = _limits(settings, limits)
        reasons = _switches(norm, settings, lim, now)
        if reasons:
            raise _Refused("switches", reasons)
        fix_facts = None
        if norm["kind"] in FIX_KINDS:
            from service import fixes

            reasons, fix_facts = fixes.check(norm["kind"], norm["payload"], settings, now=now)
            fix_facts = fixes.safe_facts(fix_facts)
            if reasons:
                raise _Refused("switches", reasons, facts=fix_facts)
        reasons = _limit_reasons(norm, settings, lim, now)
        if reasons:
            raise _Refused("limits", reasons)
        cap_result = capacity
        needs_capacity = norm["kind"] in PLAN_KINDS + ("plan_start_now", "scale_up")
        if cap_result is None and needs_capacity:
            from service import capacity as capacity_mod

            planned = cost_usd if norm["kind"] in PLAN_KINDS and isinstance(cost_usd, (int, float)) else 0.0
            cap_result = capacity_mod.check(settings, limits=lim, now=now, planned_usd=planned or 0.0)
        if needs_capacity:
            reasons = _capacity_reasons(norm, cap_result or {})
            if reasons:
                raise _Refused("capacity", reasons)
        reasons, info = _cost(norm, settings, lim, cap_result, cost_usd, context, now)
        if reasons:
            raise _Refused("cost", reasons)
    except _Refused as err:
        return _refuse(err, norm, settings, store)

    kind = norm["kind"]
    needs = _needs_approval(norm, settings, info)
    week = norm["payload"]["plan_week"] if "plan_week" in norm["payload"] else iso_week(now, settings)
    out = {**norm, "id": None, "level": KINDS[kind]["level"], "created_at": _iso(now), "week": week,
           "needs_approval": needs, "status": "waiting_approval" if needs else "accepted",
           "checks": {"limits_ok": lim.ok, **{k: v for k, v in info.items() if k != "needs_approval"}},
           "history": []}
    if kind in PLAN_KINDS and isinstance(context.get("approved_plans"), int):
        out["plan_number"] = context["approved_plans"] + 1
    if _TARGET.get():
        out["target"] = dict(_TARGET.get())
    if kind in PLAN_KINDS:
        inherited = _week_hold(settings, week)
        if inherited:                  # a revision or a new plan of a held week stays held (D3)
            out["held"] = {**inherited, "inherited": True}
    if kind in FIX_KINDS:
        out["checks"]["facts"] = fix_facts
    if cap_result:
        out["checks"]["capacity"] = {"ok": cap_result.get("ok"), "reasons": cap_result.get("reasons"),
                                     "spent": (cap_result.get("facts") or {}).get("spent"),
                                     "cap": (cap_result.get("facts") or {}).get("cap")}
    result = {"ok": True, "duplicate": False, "stage": None, "reasons": [], "fields": [], "needs_approval": needs,
              "order_id": None, "status": out["status"], "order": out, "capacity": cap_result,
              "cost_usd": info.get("cost_usd")}
    if not store:
        return result
    if kind == "plan_revision":
        out["revision"] = 1 + len([o for o in list_orders(settings, week=week, kind="plan_revision")])
    out["id"] = _new_id(settings, kind, now)
    if out["id"] is None:
        return _refuse(_Refused("limits", ["change_limit_reached"]), norm, settings, store)
    out["history"].append({"ts": _iso(now), "status": out["status"], "by": out["created_by"]})
    _save(settings, out)
    if kind == "plan_revision":
        old = load(norm["payload"]["replaces"], settings)
        if old is not None:
            old["superseded_by"] = out["id"]
            _save(settings, old)
    if out["created_by"] == "hermes" and out["level"] == 2 and kind not in STOP_ONLY:
        _bump(settings, now, "changes", "hermes")
    if kind == "search_ideas":
        _bump(settings, now, "search_ideas", out["created_by"], len(out["payload"]["ideas"]))
    if kind in FIX_KINDS and out["created_by"] == "hermes":
        _bump(settings, now, "fixes", "hermes")
    events.emit("order_accepted", settings=settings, order_id=out["id"], order_kind=kind, level=out["level"],
                created_by=out["created_by"], week=week, needs_approval=needs, status=out["status"])
    result["order_id"], result["order"] = out["id"], out
    return result


def submit(order, settings: dict | None = None, *, limits=None, capacity=None, now: datetime | None = None,
           cost_usd: float | None = None, context: dict | None = None, content_check=None,
           apply_now: bool | None = None, target: dict | None = None) -> dict:
    """validate() and store, then act. A Level 3 order you (CLI) or Hermes send (review_approve,
    raise_cap, resume_after_budget_stop, resume after Botsy's stop or of your pause by Hermes)
    is your approval: it is applied at once through approve() and added as "applied" (refused
    then: the order is marked failed). From Botsy it waits for approve(). An order that needs no
    approval and only changes a small thing (pause, resume, lower_cap, review_reject,
    review_hold, order_cancel, plan_hold, plan_start_now, search_ideas) is applied at once
    (`apply_now`). A weekly plan waits: it starts at its start time (apply) or when you approve
    it (approve). An applied plan_start_now also starts the week's plan at once and adds that
    apply() result as "plan" (it can still refuse, e.g. over_cap)."""
    settings = settings if settings is not None else load_settings()
    now = _utc(now)
    res = validate(order, settings, limits, capacity, store=True, now=now, cost_usd=cost_usd, context=context,
                   content_check=content_check, target=target)
    if not res["ok"] or res.get("duplicate"):
        return res
    o = res["order"]
    if o["needs_approval"]:
        if o["kind"] in DIRECT_APPROVAL and o["created_by"] in APPROVERS and apply_now is not False:
            res["applied"] = approve(o, settings, by=o["created_by"], now=now)
            if not res["applied"].get("ok"):
                _mark_failed(settings, o["id"], now)
                res["status"] = "failed"
            else:
                res["status"] = res["applied"].get("status", res["status"])
    elif apply_now if apply_now is not None else o["kind"] in IMMEDIATE:
        res["applied"] = apply(o, settings, now=now)
        res["status"] = res["applied"].get("status", res["status"])
        plan_id = (res["applied"].get("undo") or {}).get("plan_id")
        if o["kind"] == "plan_start_now" and res["applied"].get("ok") and plan_id:
            res["plan"] = apply(plan_id, settings, now=now)      # "Start now" starts the plan itself
    return res


def _mark_failed(settings: dict, order_id: str, now: datetime) -> None:
    """A direct approval that was refused: the order is not left waiting for a later approval."""
    with _lock(settings):
        o = load(order_id, settings)
        if o is not None and o.get("status") == "waiting_approval":
            o["status"] = "failed"
            o.setdefault("history", []).append({"ts": _iso(now), "status": "failed", "by": "botsy"})
            _save(settings, o)


# ---------------------------------------------------------------- apply

def register_handler(kind: str, apply=None, undo=None) -> None:
    """Let another module (the plan service) perform the effect of a plan kind:
    apply(order, settings, now) -> dict and undo(order, settings, now) -> dict. Without a
    handler, applying a plan kind only records the decision; the plan service reads it."""
    if kind not in KINDS:
        raise ValueError(f"unknown order kind {kind!r}")
    _HANDLERS[kind] = {"apply": apply, "undo": undo}


def _failed(order: dict, stage: str, reasons, settings: dict, *, journal: bool = True, facts=None) -> dict:
    reasons = [r for r in reasons if isinstance(r, str) and REASON_RE.fullmatch(r)]
    extra = {}
    if facts is not None:
        from service import fixes

        extra["facts"] = fixes.safe_facts(facts)
    if journal:
        events.emit("order_failed", settings=settings, order_id=order.get("id"), order_kind=order.get("kind"),
                    stage=stage, reasons=reasons, **extra)
    return {"ok": False, "stage": stage, "reasons": reasons, "order_id": order.get("id"),
            "status": order.get("status"), **extra}


def apply(order, settings: dict | None = None, *, now: datetime | None = None) -> dict:
    """Perform an accepted order's effect (one that needs no approval; a waiting one is refused
    with approval_required: use approve()). The state is checked again first; the checks and
    the effect happen under the orders lock, so nothing can change in between. Returns
    {"ok", "order_id", "status", "result", "undo"} or a refusal {"ok": False, "stage", "reasons"}.

    A plan that starts by itself starts only at plan_start_at() (Monday 8:00 am, and at least 2
    hours after it was made) unless a plan_start_now order lifted that, never while a plan_hold
    holds it, and never when it no longer fits the money (over_cap) or now needs your approval
    (it then waits for it: status waiting_approval, reason plan_needs_approval)."""
    return _run(order, settings, now, None)


def approve(order, settings: dict | None = None, *, by: str, now: datetime | None = None) -> dict:
    """Your approval of an order that waits for it (status waiting_approval): by "user" (the
    server CLI) or "hermes" (botsy-control, for the allowlisted Discord user). No codes. The
    same checks as apply() run again under the orders lock (state, deadline, hold, money, hard
    blocks), then: at most limits level3_per_day approvals in 24 hours (level3_daily_limit),
    the effect, the counter, order_approved in the journal, and (except plan kinds, which
    service/plan.py announces itself) an urgent post in the main Discord channel saying what
    changed, who asked and how to undo it. Adds "announced" (bool) to apply()'s result."""
    if by not in APPROVERS:
        oid = order if isinstance(order, str) else (order.get("id") if isinstance(order, dict) else None)
        return {"ok": False, "stage": "approval", "reasons": ["not_an_approver"], "order_id": oid, "status": None}
    return _run(order, settings, now, by)


def _run(order, settings: dict | None, now: datetime | None, by: str | None) -> dict:
    settings = settings if settings is not None else load_settings()
    now = _utc(now)
    oid = order if isinstance(order, str) else (order.get("id") if isinstance(order, dict) else None)
    stored = load(oid, settings)
    if stored is None:
        return {"ok": False, "stage": "apply", "reasons": ["no_such_order"], "order_id": None, "status": None}
    with _targeting(stored.get("target")):
        return _run_locked(oid, settings, now, by)


def _run_locked(oid: str, settings: dict, now: datetime, by: str | None) -> dict:
    with _lock(settings):
        o = load(oid, settings)
        if o is None:
            return {"ok": False, "stage": "apply", "reasons": ["no_such_order"], "order_id": None, "status": None}
        if by is None and o.get("status") == "waiting_approval":
            return _failed(o, "approval", ["approval_required"], settings, journal=False)
        if by is not None and o.get("status") != "waiting_approval":
            return _failed(o, "approval", ["not_waiting"], settings, journal=False)
        if o.get("status") not in ("waiting_approval", "accepted"):
            return _failed(o, "apply", ["not_applicable"], settings, journal=False)
        lim = _limits(settings, None)
        reasons = _switches(o, settings, lim, now, self_id=o["id"])
        reasons = [r for r in reasons if r not in ("plan_exists", "no_plan_to_revise", "too_many_revisions")]
        if o.get("superseded_by"):
            reasons.append("superseded")
        if reasons:
            return _failed(o, "switches", reasons, settings)
        if o["kind"] in ("raise_cap", "lower_cap"):
            reasons, _ = _cost(o, settings, lim, None, None, {}, now)
            if reasons:
                return _failed(o, "cost", reasons, settings)
        if o["kind"] in PLAN_KINDS:
            stage, reasons = _plan_ready(o, settings, lim, now)
            if reasons:
                return _failed(o, stage, reasons, settings)
        if o["kind"] == "resume" and not o.get("needs_approval") and _needs_approval(o, settings, {}):
            return _failed(o, "switches", ["state_changed"], settings)   # the pause is now yours or Botsy's
        if by is not None and o["kind"] in PLAN_KINDS and not may_lift(hold_of(o), by):
            return _failed(o, "switches", ["held_by_user"], settings)    # Hermes never approves past your Hold
        counted = by is not None and counts_toward_level3(o["kind"], by)
        if counted and not level3_room(settings, now=now, limits=lim):
            return _failed(o, "limits", ["level3_daily_limit"], settings)
        # Review orders also hold the review queue's lock (lock order: orders, then review queue),
        # so the entry that was checked is the one the decision is written to: a tick rewriting
        # the entry (under data/.service.lock only) waits, and never interleaves.
        qlock = _review_lock(settings) if o["kind"] in REVIEW_KINDS else contextlib.nullcontext()
        with qlock:
            try:
                result, undo_rec = _effect(o, settings, now, by)
            except _Refused as err:
                if o["kind"] in FIX_KINDS:        # a fix whose preconditions broke is done with, not left open
                    o["status"] = "failed"
                    o.setdefault("history", []).append({"ts": _iso(now), "status": "failed", "by": "botsy"})
                    _save(settings, o)
                return _failed(o, err.stage, err.reasons, settings, facts=err.facts)
            except Exception as e:  # noqa: BLE001 - recorded, never half-silent
                log.warning("order %s could not be applied: %s", o["id"], type(e).__name__)
                o["status"] = "failed"
                o.setdefault("history", []).append({"ts": _iso(now), "status": "failed", "by": "botsy"})
                _save(settings, o)
                return _failed(o, "apply", ["apply_failed"], settings)
        o["status"] = "applied"
        o["applied_at"] = _iso(now)
        o["result"] = result
        o["undo"] = undo_rec
        if by is not None:
            o["approved"] = {"by": by, "at": _iso(now), "counted": counted}
            if counted:
                _bump(settings, now, "level3", by)
        if o["kind"] == "fix_retry_run":
            _bump(settings, now, "run_now", o["created_by"])      # a retry counts toward "run now"
        o.setdefault("history", []).append({"ts": _iso(now), "status": "applied", "by": by or "botsy"})
        _save(settings, o)
    fields = {"approved_by": by} if by else {}
    events.emit("order_applied", settings=settings, order_id=o["id"], order_kind=o["kind"], created_by=o["created_by"],
                week=o["week"], **fields)
    out = {"ok": True, "stage": None, "reasons": [], "order_id": o["id"], "status": "applied", "result": result,
           "undo": undo_rec}
    if by is not None:
        announced = False if o["kind"] in PLAN_KINDS else _announce(o, settings, by, result, now)
        count = len((o.get("payload") or {}).get("item_ids") or []) or 1
        events.emit("order_approved", settings=settings, order_id=o["id"], order_kind=o["kind"], level=o["level"],
                    by=by, count=count, announced=announced, counted=counted)
        out["announced"] = announced
        if o["kind"] not in PLAN_KINDS:
            _note_announced(settings, o["id"], announced)
    elif o["kind"] in FIX_KINDS:
        from service import fixes

        facts = (o.get("checks") or {}).get("facts") or {}
        events.emit("fix_applied", settings=settings, order_id=o["id"], fix=o["kind"], recipe=fixes.RECIPES[o["kind"]],
                    created_by=o["created_by"], facts=facts, result=fixes.safe_facts(result))
        announced = _announce_fix(o, settings, result, facts, now)
        _note_announced(settings, o["id"], announced)
        out["announced"] = announced
    return out


def _note_announced(settings: dict, order_id: str, announced: bool) -> None:
    """Record whether Discord took the order's notice (fixes.retry_notify_outbox re-posts the
    ones it did not)."""
    try:
        with _lock(settings):
            o = load(order_id, settings)
            if o is not None:
                o["announced"] = bool(announced)
                _save(settings, o)
    except Exception as e:  # noqa: BLE001 - the journal has it either way
        log.warning("could not record the notice of %s: %s", order_id, type(e).__name__)


def _announce_fix(o: dict, settings: dict, result: dict, facts: dict, now: datetime) -> bool:
    """Post an applied fix to the main Discord channel with the facts Botsy checked."""
    from service import fixes, notify

    try:
        return bool(notify.send(fixes.post_text(o, result, facts), settings=settings, kinds=["fix_applied"],
                                now=now))
    except Exception as e:  # noqa: BLE001
        log.warning("fix notice for %s not sent: %s", o.get("id"), type(e).__name__)
        return False


def _asked(by: str) -> str:
    return {"user": "asked by: user (the server)", "hermes": "asked by: hermes (Discord)"}.get(by, f"asked by: {by}")


def _usd(x) -> str:
    return f"${float(x):,.2f}" if _num(x) else "no cap"


def approval_text(o: dict, by: str, result: dict) -> str:
    """The urgent main-channel post for a Level 3 approval: what changed, who asked, how to undo
    it. Fixed words, ids and money only (never text from Hermes)."""
    kind, oid = o["kind"], o["id"]
    undo = f"Undo: botsy service control undo {oid}"
    if kind == "review_approve":
        items = list(result.get("approved") or [])
        batch = (o.get("payload") or {}).get("batch")
        shown = ", ".join(items[:10]) + (f" and {len(items) - 10} more" if len(items) > 10 else "")
        return (f"Approved {len(items)} review item{'s' if len(items) != 1 else ''}"
                f"{f' (batch {batch})' if batch else ''} ({_asked(by)}): {shown}. They become Etsy drafts at the "
                "next check; you still publish each draft yourself in Etsy.\n"
                f"{undo} (puts them back on hold; not once a draft exists).")
    if kind == "raise_cap":
        return (f"Monthly cap raised from {_usd(result.get('cap_from_usd'))} to {_usd(result.get('cap_to_usd'))} "
                f"({_asked(by)}). It can never go above the admin's ceiling. Paid work that stopped for the budget "
                f"this month may go on.\n{undo} (back to the cap before).")
    if kind == "resume_after_budget_stop":
        return (f"Paid work restarted after the budget stop ({_asked(by)}). The cap itself is unchanged.\n"
                f"{undo} (stops paid work again).")
    if kind == "resume":
        before = (o.get("undo") or {}).get("paused") or {}
        return (f"Botsy resumed ({_asked(by)}); the pause was set by {before.get('by') or 'botsy'} "
                f"({before.get('reason_code') or 'no reason'}).\nUndo: botsy service control pause.")
    return f"Order {oid} ({kind}) approved ({_asked(by)}).\n{undo}"


def _announce(o: dict, settings: dict, by: str, result: dict, now: datetime) -> bool:
    """Post the approval to the main Discord channel at once (urgent: ignores quiet hours)."""
    from service import notify

    try:
        return bool(notify.send(approval_text(o, by, result), settings=settings, kinds=["order_approved"],
                                urgent=True, now=now))
    except Exception as e:  # noqa: BLE001 - a message never undoes an approval; the journal has it
        log.warning("approval notice for %s not sent: %s", o.get("id"), type(e).__name__)
        return False


def _plan_ready(o: dict, settings: dict, lim, now: datetime) -> tuple[str | None, list[str]]:
    """The plan checks apply() runs again at start time (stage, reasons)."""
    p = o.get("payload") or {}
    if o.get("needs_approval"):
        deadline = plan_deadline(p.get("plan_week") or o.get("week"), settings)
        if deadline is None or now >= deadline:   # unknown deadline: fail closed
            return "switches", ["plan_expired"]
    if not o.get("needs_approval"):
        if isinstance(o.get("held"), dict):
            return "switches", ["plan_held"]
        start = plan_start_at(o, settings)
        if not isinstance(o.get("start_now"), dict) and (start is None or now < start):
            return "switches", ["before_start_time"]
    cost = (o.get("checks") or {}).get("cost_usd")
    cost = float(cost) if _num(cost) else estimate_cost(p)
    spent, cap, _ = _own_money(settings, _plan_money_time(p, settings, now))
    if spent is None:
        return "cost", ["money_unknown"]
    if cap is not None and spent + cost > cap:
        return "cost", ["over_cap"]
    if not o.get("needs_approval"):
        need, why = plan_needs_approval(cost, settings, lim, approved_plans=1 << 30, cap=cap)
        if need:                       # the cap was lowered or the limits file broke since: ask you first
            o["needs_approval"], o["status"] = True, "waiting_approval"
            o.setdefault("checks", {})["approval_reason"] = why
            o.setdefault("history", []).append({"ts": _iso(now), "status": "waiting_approval", "by": "botsy"})
            _save(settings, o)
            return "cost", ["plan_needs_approval"]
    return None, []


def _effect(o: dict, settings: dict, now: datetime, by: str | None) -> tuple[dict, dict | None]:
    from qa import review

    kind, p = o["kind"], o["payload"]
    oid = o["id"]
    if kind == "pause":
        st = control_state(settings)
        st["paused"] = {"by": o["created_by"], "since": _iso(now), "order_id": oid,
                        "reason_code": p.get("reason_code") or "user_request"}
        _write_state(settings, st)
        return {"paused": True}, {"kind": "resume", "order_id": oid}
    if kind == "resume":
        st = control_state(settings)
        before = st.get("paused")
        st["paused"] = None
        _write_state(settings, st)
        return {"paused": False}, {"kind": "pause", "paused": before}
    if kind in ("lower_cap", "raise_cap"):
        st = control_state(settings)
        before_override = st.get("cap_override_usd")
        before_cap = effective_cap(settings)
        stop = st.get("budget_stop")
        st["cap_override_usd"] = p["cap_usd"]
        st["cap_changed"] = {"by": o["created_by"], "at": _iso(now), "order_id": oid, "from_usd": before_cap,
                             "to_usd": p["cap_usd"]}
        if kind == "raise_cap":
            st["budget_stop"] = None               # the one approval covers restarting paid work
        _write_state(settings, st)
        undo_rec = {"kind": "restore_cap", "cap_override_usd": before_override, "expect_usd": p["cap_usd"]}
        if kind == "raise_cap":
            undo_rec["budget_stop"] = stop
        return {"cap_from_usd": before_cap, "cap_to_usd": p["cap_usd"]}, undo_rec
    if kind == "resume_after_budget_stop":
        st = control_state(settings)
        before = {"budget_stop": st.get("budget_stop"), "paused": st.get("paused")}
        st["budget_stop"] = None
        if isinstance(st.get("paused"), dict) and st["paused"].get("by") == "botsy":
            st["paused"] = None
        _write_state(settings, st)
        return {"paid_work": "restarted"}, {"kind": "restore_stop", **before}
    if kind == "review_approve":
        rdir = review_dir(settings)
        covered = (o.get("checks") or {}).get("items")
        covered = {k: v for k, v in covered.items() if v} if isinstance(covered, dict) else {}
        for item_id in sorted(p["item_ids"]):          # all or none: every item still as it was checked
            e = review.read_entry(item_id, rdir)
            if not e or e.get("status") != "pending" or _drafted(settings, item_id):
                raise _Refused("apply", ["state_changed"])
            if item_id in covered and e.get("content_digest") != covered[item_id]:
                raise _Refused("apply", ["state_changed"])
        done = []
        for item_id in sorted(p["item_ids"]):
            expected = {"status": "pending"}
            if item_id in covered:
                expected["content_digest"] = covered[item_id]
            try:
                review.approve(item_id, rdir, note=f"approved by {by or o['created_by']} (order {oid})",
                               expected=expected)
            except review.StateChanged:
                raise _Refused("apply", ["state_changed"]) from None
            done.append(item_id)
        return {"approved": done}, {"kind": "hold_items", "item_ids": done, "reason_code": "undo_approval"}
    if kind == "review_reject":
        rdir = review_dir(settings)
        before = review.read_entry(p["item_id"], rdir)
        try:
            review.reject(p["item_id"], rdir, f"{p['reason_code']} (order {oid}, by {o['created_by']})",
                          expected={"status": (before or {}).get("status")})
        except review.StateChanged:
            raise _Refused("apply", ["state_changed"]) from None
        return {"rejected": p["item_id"]}, {"kind": "restore_entry", "item_id": p["item_id"], "entry": before}
    if kind == "review_hold":
        reason = _hold(settings, p["item_id"], p["reason_code"], oid)
        return {"held": p["item_id"]}, {"kind": "remove_reason", "item_id": p["item_id"], "reason": reason}
    if kind == "order_cancel":
        target = load(p["order_id"], settings)
        if target is None:
            raise _Refused("apply", ["no_such_order"])
        was = target.get("status")
        target["status"] = "cancelled"
        target["cancelled_at"] = _iso(now)
        target["cancelled_by"] = {"order_id": oid, "by": o["created_by"]}
        target.setdefault("history", []).append({"ts": _iso(now), "status": "cancelled", "by": o["created_by"]})
        _save(settings, target)
        h = _HANDLERS.get(target.get("kind"), {}).get("undo")
        if h and was == "applied":
            h(target, settings, now)
        return {"cancelled": target["id"], "was": was}, {"kind": "restore_status", "order_id": target["id"],
                                                         "status": was}
    if kind == "search_ideas":
        flags = [c["flagged"] for c in _idea_checks(p, settings)]
        return {"ideas": len(p["ideas"]), "flagged": [i for i, f in enumerate(flags) if f]}, {"kind": "cancel_order"}
    if kind in FIX_KINDS:
        from service import fixes

        try:
            result, facts = fixes.perform(o, settings, now=now)
        except fixes.FixRefused as err:
            raise _Refused("apply", err.reasons, facts=err.facts) from None
        o.setdefault("checks", {})["facts"] = fixes.safe_facts(facts)       # the facts at run time
        return result, None
    if kind == "kdp_kit_approve":
        raise _Refused("switches", ["kdp_not_built"])
    if kind in PLAN_KINDS + ("plan_hold", "plan_start_now"):
        plan_id = _mark_plan(o, settings, now) if kind in ("plan_hold", "plan_start_now") else None
        h = _HANDLERS.get(kind, {}).get("apply")
        result = h(o, settings, now) if h else {"recorded": True}
        undo_rec = {"weekly_plan": {"kind": "cancel_order"}, "plan_revision": {"kind": "cancel_order"},
                    "plan_hold": {"kind": "handler", "plan_id": plan_id},
                    "plan_start_now": {"kind": "handler", "plan_id": plan_id}}[kind]
        return result if isinstance(result, dict) else {"recorded": True}, undo_rec
    raise _Refused("apply", ["not_applicable"])


MARKS = {"plan_hold": "held", "plan_start_now": "start_now"}
# Undo records that only stop work or make things stricter (Hermes may undo these on any order).
STRICTER_UNDOS = ("pause", "hold_items", "cancel_order", "restore_stop")


def _mark_plan(o: dict, settings: dict, now: datetime) -> str | None:
    """plan_hold marks the week's plan "held" (apply() then refuses it with plan_held);
    plan_start_now marks it "start_now" (apply() may then start it before its start time) and
    lifts a hold. Returns the plan's id."""
    target = current_plan(settings, o["payload"]["plan_week"])
    if target is None:
        return None
    key = MARKS[o["kind"]]
    if key == "held" and not may_lift(hold_of(target), o["created_by"]):
        return target["id"]            # your Hold stays yours: Hermes's hold never replaces it (D3)
    if key == "start_now" and not may_lift(hold_of(target), o["created_by"]):
        raise _Refused("switches", ["held_by_user"])
    target[key] = {"order_id": o["id"], "by": o["created_by"], "at": _iso(now)}
    target.pop("start_now" if key == "held" else "held", None)
    target.setdefault("history", []).append({"ts": _iso(now), "status": target.get("status"), "by": o["created_by"],
                                             "mark": key, "order_id": o["id"]})
    _save(settings, target)
    return target["id"]


def _review_lock(settings: dict):
    from qa import review

    return review.queue_lock(review_dir(settings))


def _hold(settings: dict, item_id: str, reason_code: str, oid: str) -> str:
    with _review_lock(settings):
        return _hold_locked(settings, item_id, reason_code, oid)


def _hold_locked(settings: dict, item_id: str, reason_code: str, oid: str) -> str:
    from qa import review

    rdir = review_dir(settings)
    entry = review.read_entry(item_id, rdir)
    if entry is None:
        raise _Refused("apply", ["item_unknown"])
    if entry.get("status") == "rejected":
        raise _Refused("apply", ["already_rejected"])
    reason = f"hold: {reason_code} (order {oid})"
    reasons = [str(r) for r in entry.get("reasons") or []]
    if reason not in reasons:
        reasons.append(reason)
    if entry.get("status") == "approved":
        entry["previous_status"] = "approved"
    entry.update({"status": "pending", "needs_review": True, "reasons": reasons})
    write_json(review.entry_path(item_id, rdir), entry)
    return reason


# ---------------------------------------------------------------- undo

def undo(order_id: str, settings: dict | None = None, *, by: str = "user", now: datetime | None = None) -> dict:
    """Undo an applied order (see _undo). A review order you sent for a named output root
    (`qa review approve --out`) is undone there."""
    settings = settings if settings is not None else load_settings()
    stored = load(order_id, settings) if isinstance(order_id, str) else None
    with _targeting((stored or {}).get("target")):
        return _undo(order_id, settings, by=by, now=now)


def _undo(order_id: str, settings: dict | None = None, *, by: str = "user", now: datetime | None = None) -> dict:
    """Undo an applied order with the record it saved. Refused (state_changed) when someone
    changed the same thing since; an undo never approves an item or raises the cap above what
    was in force before the order. A Hermes undo counts toward its changes an hour.

    Hermes may undo only its own orders, except undos that only stop work or make things
    stricter (pause again, put approved items back on hold, cancel a plan, lower a raised cap,
    stop paid work again). So Hermes cannot lift your pause, raise the cap you lowered, take
    back your rejection or hold, or bring back what you cancelled by undoing your order
    (not_hermes_order); those go through their own orders (resume and raise_cap, which are
    Level 3 approvals: counted, posted at once and journaled)."""
    from qa import review
    from service import limits as limits_mod

    settings = settings if settings is not None else load_settings()
    now = _utc(now)
    if by not in CREATORS:
        return {"ok": False, "stage": "format", "reasons": ["bad_created_by"], "order_id": order_id}
    o = load(order_id, settings)
    if o is None:
        return {"ok": False, "stage": "apply", "reasons": ["no_such_order"], "order_id": order_id}
    rec = o.get("undo")
    if o.get("status") != "applied" or not isinstance(rec, dict):
        return _failed(o, "apply", ["nothing_to_undo"], settings, journal=False)
    lim = limits_mod.load(settings)
    with _lock(settings):
        o = load(order_id, settings) or o
        rec = o.get("undo")
        if o.get("status") != "applied" or not isinstance(rec, dict):
            return _failed(o, "apply", ["nothing_to_undo"], settings, journal=False)
        kind = rec.get("kind")
        stop_only = kind in STRICTER_UNDOS or o["kind"] == "raise_cap"
        if o["kind"] == "plan_hold" and not may_lift({"by": o.get("created_by")}, by):
            return _failed(o, "switches", ["held_by_user"], settings)     # only you lift your Hold (D3)
        if by == "hermes" and not stop_only:
            if o.get("created_by") != "hermes":
                return _failed(o, "switches", ["not_hermes_order"], settings)
            if not lim.ok:
                return _failed(o, "switches", ["limits_unavailable"], settings)
            if _count(_counters(settings), "changes", "hermes", now - timedelta(hours=1)) >= int(
                    lim.get("changes_per_hour", 20)):
                return _failed(o, "limits", ["change_limit_reached"], settings)
        try:
            if kind == "resume":
                st = control_state(settings)
                if not (isinstance(st.get("paused"), dict) and st["paused"].get("order_id") == o["id"]):
                    raise _Refused("apply", ["state_changed"])
                st["paused"] = None
                _write_state(settings, st)
            elif kind == "pause":
                st = control_state(settings)
                if st.get("paused"):
                    raise _Refused("apply", ["state_changed"])
                st["paused"] = rec.get("paused")
                _write_state(settings, st)
            elif kind == "restore_cap":
                st = control_state(settings)
                if st.get("cap_override_usd") != rec.get("expect_usd"):
                    raise _Refused("apply", ["state_changed"])
                back = rec.get("cap_override_usd")
                if back is not None and lim.ok and back > float(lim.get("cap_ceiling_usd", 100)):
                    raise _Refused("cost", ["above_ceiling"])
                if back is not None and not lim.ok and o["kind"] == "lower_cap":
                    raise _Refused("switches", ["limits_unavailable"])
                st["cap_override_usd"] = back
                st["cap_changed"] = {"by": by, "at": _iso(now), "order_id": o["id"], "undo": True}
                if o["kind"] == "raise_cap" and rec.get("budget_stop"):
                    st["budget_stop"] = rec["budget_stop"]
                _write_state(settings, st)
            elif kind == "restore_stop":
                st = control_state(settings)
                st["budget_stop"] = rec.get("budget_stop")
                if rec.get("paused") and not st.get("paused"):
                    st["paused"] = rec["paused"]
                _write_state(settings, st)
            elif kind == "hold_items":
                for item_id in rec.get("item_ids") or []:
                    if not _drafted(settings, item_id):
                        _hold(settings, item_id, rec.get("reason_code") or "undo_approval", o["id"])
            elif kind == "restore_entry":
                rdir = review_dir(settings)
                now_entry = review.read_entry(rec["item_id"], rdir)
                if not now_entry or now_entry.get("status") != "rejected" or _drafted(settings, rec["item_id"]):
                    raise _Refused("apply", ["state_changed"])
                before = rec.get("entry")
                if not isinstance(before, dict):
                    raise _Refused("apply", ["nothing_to_undo"])
                if before.get("status") == "approved":       # an undo never approves: back to pending
                    before = {**before, "status": "pending", "previous_status": "approved"}
                write_json(review.entry_path(rec["item_id"], rdir), before)
            elif kind == "remove_reason":
                rdir = review_dir(settings)
                entry = review.read_entry(rec["item_id"], rdir)
                if not entry or rec.get("reason") not in (entry.get("reasons") or []):
                    raise _Refused("apply", ["state_changed"])
                entry["reasons"] = [r for r in entry["reasons"] if r != rec["reason"]]
                write_json(review.entry_path(rec["item_id"], rdir), entry)
            elif kind == "cancel_order":
                h = _HANDLERS.get(o["kind"], {}).get("undo")
                if h:
                    h(o, settings, now)
            elif kind == "restore_status":
                target = load(rec.get("order_id"), settings)
                if target is None or target.get("status") != "cancelled" \
                        or (target.get("cancelled_by") or {}).get("order_id") != o["id"]:
                    raise _Refused("apply", ["state_changed"])
                if target.get("kind") in PLAN_KINDS and current_plan(settings, target["week"]) is not None:
                    raise _Refused("apply", ["state_changed"])      # another plan took its place
                if rec.get("status") not in ("waiting_approval", "accepted", "applied"):
                    raise _Refused("apply", ["nothing_to_undo"])
                target["status"] = rec["status"]
                target.pop("cancelled_by", None)
                target.setdefault("history", []).append({"ts": _iso(now), "status": rec["status"], "by": by})
                _save(settings, target)
                h = _HANDLERS.get(target.get("kind"), {}).get("apply")
                if h and rec["status"] == "applied":
                    h(target, settings, now)
            elif kind == "handler":
                key = MARKS.get(o["kind"])
                week = (o.get("payload") or {}).get("plan_week")
                # The mark may have moved on with the plan (a revision or a new plan of the week
                # inherits a Hold): lift it wherever this order's own mark is, and only there.
                for target in [x for x in list_orders(settings, week=week) if x.get("kind") in PLAN_KINDS] \
                        if key and week else []:
                    if (target.get(key) or {}).get("order_id") == o["id"]:
                        target.pop(key, None)
                        target.setdefault("history", []).append({"ts": _iso(now), "status": target.get("status"),
                                                                 "by": by, "unmark": key, "order_id": o["id"]})
                        _save(settings, target)
                h = _HANDLERS.get(o["kind"], {}).get("undo")
                if h:
                    h(o, settings, now)
            else:
                raise _Refused("apply", ["nothing_to_undo"])
        except _Refused as err:
            return _failed(o, err.stage, err.reasons, settings)
        o["status"] = "undone"
        o["undone_at"] = _iso(now)
        o.setdefault("history", []).append({"ts": _iso(now), "status": "undone", "by": by})
        _save(settings, o)
        if by == "hermes" and not stop_only:
            _bump(settings, now, "changes", "hermes")
    events.emit("order_undone", settings=settings, order_id=o["id"], order_kind=o["kind"], by=by)
    return {"ok": True, "stage": None, "reasons": [], "order_id": o["id"], "status": "undone"}


DESCRIBE = {
    "bad_schema": "The order is not order/v1.",
    "not_an_object": "The order is not a JSON object.",
    "unknown_kind": "Botsy does not know that kind of order.",
    "bad_created_by": "created_by must be hermes, user or botsy.",
    "unknown_field": "The order has a field Botsy does not accept (there are no free-text fields).",
    "missing_field": "A required field is missing.",
    "bad_field": "A field has a value Botsy does not accept.",
    "client_ref_reused": "That client_ref was already used for a different order.",
    "limits_unavailable": "The admin-only limits file is missing or broken, so small changes are off.",
    "paused": "Botsy is paused.",
    "already_paused": "Botsy is already paused.",
    "not_paused": "Botsy is not paused.",
    "control_state_unreadable": "Botsy's control file (data/control/state.json) cannot be read, so Botsy stays "
                                "paused. Resuming would forget a pause, a lower cap or a budget stop it may hold: "
                                "fix or remove the file on the server first (a copy is kept next to it).",
    "no_budget_stop": "Paid work is not stopped for the budget.",
    "wrong_week": "A plan can only be for this week or next week.",
    "plan_exists": "That week already has a plan; send a revision.",
    "no_plan": "That week has no plan.",
    "no_plan_to_revise": "The plan to revise does not exist or is no longer in force.",
    "too_many_revisions": "The plan was revised the most times allowed this week.",
    "plan_needs_approval": "This plan waits for your approval (botsy service plan approve, or ask Hermes).",
    "superseded": "A newer revision replaced this plan.",
    "plan_already_started": "The plan has already started.",
    "plan_expired": "The plan's deadline has passed, so it expired.",
    "plan_held": "The plan is on hold. Start it now, change it, or let next week's plan replace it.",
    "before_start_time": "The plan starts by itself on Monday at 8:00 am, and at least 2 hours after it was "
                         "made (your Hold window). Use Start now to start it earlier.",
    "not_hermes_order": "Hermes can undo only its own orders (or undo anything to stop work). Use resume, "
                        "raise_cap or a new order instead; those are your approvals and are posted at once.",
    "brief_niche_mismatch": "A plan line's brief is from another niche.",
    "no_winners_yet": "There are no winners yet (scale-ups start once listings have sold), so no scale-ups.",
    "scale_up_not_built": "Scale-ups are not built yet.",
    "no_such_order": "There is no such order.",
    "not_cancellable": "That order cannot be cancelled now.",
    "item_unknown": "There is no review entry for that item.",
    "item_not_pending": "That item is not waiting for review.",
    "item_failed_qa": "That item has no passing QA report.",
    "item_not_ready": "That item's review entry has no content digest yet (run QA again).",
    "already_rejected": "That item is already rejected.",
    "already_drafted": "That item is already an Etsy draft.",
    "blocklisted_term": "A search idea has a blocked term.",
    "health_claim": "A search idea makes a health claim.",
    "change_limit_reached": "Hermes has made the most changes allowed this hour.",
    "search_ideas_limit": "Hermes has added the most search ideas allowed this week.",
    "batch_too_large": "Too many items for one approval (limits approve_batch_max).",
    "level3_daily_limit": "The most approvals allowed in 24 hours were used (limits level3_per_day). Wait, or "
                          "approve on the server once the 24 hours have passed.",
    "over_cap": "The plan does not fit in the money left this month.",
    "above_ceiling": "That is above the cap ceiling only the server admin can change.",
    "not_a_raise": "That is not higher than the cap now.",
    "not_lower": "That is not lower than the cap now.",
    "money_unknown": "Botsy cannot read the cap or the spending log.",
    "not_applicable": "That order cannot be applied now.",
    "approval_required": "This waits for your approval (approve it; there are no codes).",
    "not_waiting": "That order is not waiting for an approval.",
    "not_an_approver": "Only you (the server CLI) or Hermes for you can approve.",
    "state_changed": "Something changed since; the undo would overwrite it.",
    "nothing_to_undo": "There is nothing to undo.",
    "apply_failed": "Botsy could not apply the order (the server log says why).",
    "held_by_user": "You put this plan on hold. Only you can lift your Hold (botsy service plan approve or "
                    "start-now on the server); Hermes cannot start, approve or revise it.",
    "kdp_not_built": "KDP kits are not built yet, so there is nothing to approve.",
    "fixes_limit": "Hermes has used the most watchdog fixes allowed in 24 hours (limits fixes_per_day).",
    "run_now_limit": "The most extra checks allowed in 24 hours were used (limits run_now_per_day).",
}


def describe(reason: str) -> str:
    """Plain words for a reason code (orders or capacity)."""
    if reason in DESCRIBE:
        return DESCRIBE[reason]
    from service import capacity, fixes, limits

    if fixes.describe(reason):
        return fixes.describe(reason)
    if reason in limits.REASONS:
        return DESCRIBE["limits_unavailable"]
    return capacity.DESCRIBE.get(reason) or reason


__all__ = ["APPROVERS", "CREATORS", "FIX_KINDS", "HOLD_RANK", "KINDS", "POSTING_KINDS", "counts_toward_level3",
           "held_by_user", "hold_of", "may_lift", "REASONS", "SCHEMA", "STATUSES", "apply", "approval_text", "approve",
           "control_state", "counts", "current_plan", "describe", "effective_cap", "estimate_cost", "iso_week",
           "level3_room", "level3_take", "list_orders", "load", "note_budget_stop", "paid_work_block", "paused",
           "plan_needs_approval", "plan_start_at", "plan_weeks", "register_handler", "review_dir", "settings_cap",
           "submit", "undo", "validate"]
