"""The overnight log: outbox/daily-log.txt (docs/SERVER.md section 11).

    build(since, until, settings) -> str        plain text, CRLF line endings (Notepad)
    write(outbox, text, date) -> Path           outbox/daily-log-<date>.txt and daily-log.txt

`python -m service digest [--hours 24]` builds the log for the last day from the event
journal (core.events) and the spend ledger (core.spend), writes it to the outbox, where
the Windows helper copies it to Documents\\Botsy, and posts it to Discord. The systemd
timer botsy-digest.timer runs it every day at 07:07 New York time, just after the quiet
hours end, so it also carries the Discord messages held overnight.

Every time in the log, and the date in daily-log-<date>.txt (`log_date(settings)`), is on
the clock of schedule.timezone (core.config.tz; America/New_York by default), whatever the
server's own zone is. The journal's time stamps stay UTC.

Everything in it is our own data (theme names, titles, item ids, counts, our own error
summaries); events never hold competitor text. Secret values are replaced by [secret].
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

from core.config import check_date, now_local, tz

CRLF = "\r\n"
LATEST_NAME = "daily-log.txt"
DATED_PREFIX = "daily-log-"
KEEP_DATED = 35            # dated logs kept in the outbox (the oldest beyond this are removed)
MAX_LINES = 10             # lines listed per section before "... and N more"
MAX_DETAIL = 200           # characters of one listed line

# When the automatic jobs run, on the wall clock of schedule.timezone (the timers name the
# same zone). Keep in step with the OnCalendar lines in deploy/lightsail/systemd/
# botsy-daily.timer, botsy-digest.timer and botsy-weekly.timer (Saturday = weekday 5).
SCHEDULE = {"daily_check": (6, 17), "daily_log": (7, 7), "search_list": (5, 9, 7),  # (weekday, h, m)
            "weekly_plan": (6, 20, 0)}    # botsy-plan.timer, Sunday 20:00 (botsy-plan-check runs hourly at :05)
# The zone the OnCalendar lines name. The times above are worked out on this clock (that is
# when systemd runs them) and then shown on schedule.timezone's clock, so a server whose
# settings name another zone still shows the true run times.
TIMER_ZONE = "America/New_York"

TOOLS = {"everbee": "EverBee", "erank": "eRank"}


# ---------------------------------------------------------------- helpers

def _utc(when: _dt.datetime) -> _dt.datetime:
    return when.replace(tzinfo=_dt.timezone.utc) if when.tzinfo is None else when.astimezone(_dt.timezone.utc)


def _local(when: _dt.datetime, zone: _dt.tzinfo) -> _dt.datetime:
    """`when` (naive = UTC) on Botsy's clock, `zone` (core.config.tz(settings))."""
    return _utc(when).astimezone(zone)


def _show(when: _dt.datetime, zone: _dt.tzinfo) -> str:
    return _local(when, zone).strftime("%a %d %b %H:%M")


def _zone_name(zone: _dt.tzinfo) -> str:
    key = getattr(zone, "key", None) or str(zone)
    city = key.rsplit("/", 1)[-1].replace("_", " ")
    return f"{city} time" if "/" in key else f"{key} time"


def log_date(settings: dict, now: _dt.datetime | None = None) -> str:
    """The date for daily-log-<date>.txt: today on schedule.timezone's clock (YYYY-MM-DD).

    With no `now`, PIPELINE_DATE (core.config.today) still wins, for reproducible runs."""
    if now is None and os.environ.get("PIPELINE_DATE"):
        return check_date(os.environ["PIPELINE_DATE"])
    return now_local(settings, now).date().isoformat()


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _clip(text) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_DETAIL else text[:MAX_DETAIL - 3] + "..."


def _capped(items: list[str], indent: str = "    - ") -> list[str]:
    lines = [f"{indent}{_clip(x)}" for x in items[:MAX_LINES]]
    if len(items) > MAX_LINES:
        lines.append(f"{indent}... and {len(items) - MAX_LINES} more")
    return lines


def _niche(name) -> str:
    return str(name or "?").replace("_", " ")


def _ts(event: dict) -> _dt.datetime | None:
    try:
        return _utc(_dt.datetime.fromisoformat(str(event.get("ts"))))
    except (TypeError, ValueError):
        return None


def _latest_by(events: list[dict], key: str) -> dict[str, dict]:
    """The newest event per value of `key` (events are oldest first), in first-seen order."""
    out: dict[str, dict] = {}
    for e in events:
        k = str(e.get(key) or "")
        if k:
            out.pop(k, None)
            out[k] = e
    return out


def review_command(item_id: str, dry_run: bool) -> str:
    """The exact server command that approves one held item."""
    return f"botsy qa review approve {item_id}" + (" --dry-run" if dry_run else "")


def _next_run(after: _dt.datetime, hour: int, minute: int, weekday: int | None = None,
              zone: _dt.tzinfo | None = None) -> _dt.datetime:
    # Worked out on the zone's wall clock, then given that day's offset, so a summer-time
    # change between now and the run does not shift the time shown by an hour (the timers
    # name the same zone, so this is when systemd runs them).
    zone = zone if zone is not None else tz(None)
    local = _local(after, zone).replace(tzinfo=None)
    t = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    while t <= local or (weekday is not None and t.weekday() != weekday):
        t += _dt.timedelta(days=1)
    return t.replace(tzinfo=zone)


# ---------------------------------------------------------------- sections

def _exports(by: dict[str, list[dict]]) -> list[str]:
    got, rejected = by.get("export_received", []), by.get("export_rejected", [])
    routed = by.get("export_routed", [])
    lines = ["Exports"]
    if not got and not rejected and not routed:
        return lines + ["  None received."]
    tools = Counter(TOOLS.get(str(e.get("tool") or "").lower(), str(e.get("tool") or "unknown")) for e in got)
    rows = sum(int(e.get("rows") or 0) for e in got if isinstance(e.get("rows"), int))
    detail = ", ".join(f"{n} from {t}" for t, n in tools.most_common())
    lines.append(f"  Received: {len(got)}" + (f" ({detail}; {_plural(rows, 'row')})" if got else ""))
    if rejected:
        lines.append(f"  Not used: {len(rejected)}")
        lines += _capped([f"{e.get('name')}: {e.get('reason')}" for e in rejected])
    if routed:
        lines.append(f"  Book (KDP) files kept for the KDP reader: {len(routed)}")
        lines += _capped([str(e.get("name")) for e in routed])
    return lines


def _research(by: dict[str, list[dict]]) -> list[str]:
    written, changed = by.get("briefs_written", []), by.get("stage_changed", [])
    lines = ["Research"]
    for e in changed:
        lines.append(f"  Research stage changed from {e.get('from')} to {e.get('to')}"
                     + (f" (after {_plural(int(e['drafts']), 'Etsy draft')})"
                        if isinstance(e.get("drafts"), int) else "") + ".")
    if not written:
        return lines + ["  Research did not run (no new exports)."]
    latest = _latest_by(written, "niche")
    stage = written[-1].get("stage")
    if stage:
        lines.append(f"  Stage: {stage}" + (" (stricter: themes must keep showing up week after week)"
                                             if stage == "refined" else ""))
    lines.append("  Design briefs: " + ", ".join(f"{_niche(n)} {int(e.get('count') or 0)}"
                                                 for n, e in latest.items()))
    return lines


def _items(by: dict[str, list[dict]]) -> list[str]:
    made = _latest_by(by.get("item_generated", []), "item_id")
    listed = _latest_by(by.get("listing_written", []), "item_id")
    failed = _latest_by(by.get("qa_failed", []), "item_id")
    lines = ["Items", f"  Made: {len(made)}", f"  Listings written: {len(listed)}"]
    if failed:
        lines.append(f"  Failed a quality check: {len(failed)}")
        lines += _capped([f"{i}: {', '.join(map(str, e.get('checks') or [])) or 'see status.txt'}"
                          for i, e in failed.items()])
    return lines


def _drafts(by: dict[str, list[dict]]) -> list[str]:
    drafts = list(_latest_by(by.get("draft_created", []), "item_id").values())
    real = [e for e in drafts if not e.get("dry_run")]
    practice = [e for e in drafts if e.get("dry_run")]
    lines = ["Drafts"]
    if not drafts:
        return lines + ["  No new drafts."]
    if real:
        lines.append(f"  New Etsy drafts: {len(real)}. Approve them in Etsy; nothing goes live by itself.")
        lines += _capped([str(e.get("title") or e.get("item_id")) for e in real])
    if practice:
        lines.append(f"  Practice drafts (test mode, nothing sent to Etsy): {len(practice)}")
        lines += _capped([str(e.get("title") or e.get("item_id")) for e in practice])
    return lines


def _review(by: dict[str, list[dict]], live: bool) -> list[str]:
    # Every pipeline run records the items still waiting; one that became a draft later in
    # the window is no longer waiting.
    drafted = {str(e.get("item_id")): _ts(e) for e in by.get("draft_created", [])}
    held = {i: e for i, e in _latest_by(by.get("held_for_review", []), "item_id").items()
            if not (drafted.get(i) and _ts(e) and drafted[i] >= _ts(e))}
    if not held:
        return ["Waiting for your review", "  Nothing."]
    lines = ["Waiting for your review", f"  {_plural(len(held), 'item')}. On the server type the approve line "
                                         "to let one become a draft:"]
    shown = list(held.items())[:MAX_LINES]
    for item_id, e in shown:
        dry = e.get("dry_run") if isinstance(e.get("dry_run"), bool) else not live
        reasons = e.get("reasons")
        reasons = "; ".join(map(str, reasons)) if isinstance(reasons, list) else str(reasons or "")
        lines.append(f"  - {item_id}" + (f": {_clip(reasons)}" if reasons else ""))
        lines.append(f"    Approve: {review_command(item_id, dry)}")
    if len(held) > MAX_LINES:
        lines.append(f"  ... and {len(held) - MAX_LINES} more")
    flag = "" if live else " --dry-run"
    lines.append(f"  See them all: botsy qa review list{flag}")
    lines.append(f'  Say no to one: botsy qa review reject ITEM_ID --reason "why"{flag}')
    return lines


def _spend(settings: dict, since: _dt.datetime, until: _dt.datetime) -> tuple[list[str], bool]:
    """(lines, cap reached)."""
    from core import spend

    lines = ["Spending (estimates; your provider bills are what count)"]
    try:
        window = sum(e["cost_usd"] for e in spend.entries(settings, since=since, until=until))
        state = spend.budget_state(settings)
        month = spend.month_to_date(settings, state["month"])
    except Exception as e:  # noqa: BLE001 - a settings mistake must not stop the log
        return lines + [f"  Could not be read: {type(e).__name__}: {_clip(e)}"], False
    hours = round((until - since).total_seconds() / 3600)
    lines.append(f"  Last {_plural(hours, 'hour')}: {_money(window)}")
    total = float(month.get("total", state["mtd"]))
    if state.get("cap"):
        lines.append(f"  This month ({state['month']}): {_money(total)} of {_money(state['cap'])} cap "
                     f"({total / state['cap']:.0%})")
    else:
        lines.append(f"  This month ({state['month']}): {_money(total)} (no monthly cap set)")
    if state.get("reached"):
        lines.append("  The cap is reached: new images and listing text are paused until next month, "
                     "or until you raise spend.monthly_cap_usd.")
    elif state.get("warn"):
        lines.append("  Most of this month's cap is used.")
    return lines, bool(state.get("reached"))


def _problems(by: dict[str, list[dict]]) -> list[str]:
    items: list[str] = []
    for e in by.get("budget_reached", []):
        items.append(f"Monthly spending cap reached: {_money(float(e.get('month_to_date') or 0))} of "
                     f"{_money(float(e.get('cap') or 0))}.")
    for e in by.get("spend_warning", []):
        pct = e.get("pct")
        items.append(f"Spending warning: {_money(float(e.get('month_to_date') or 0))} of "
                     f"{_money(float(e.get('cap') or 0))} used this month"
                     + (f" ({float(pct):.0%})." if isinstance(pct, (int, float)) else "."))
    for e in by.get("token_expiring", []):
        items.append(f"The Etsy connection runs out in {_plural(int(e.get('days_left') or 0), 'day')}: "
                     "on the server type  botsy publisher auth --paste")
    errors = Counter(f"{e.get('where')}: {e.get('message')}" for e in by.get("error", []))
    for text, n in errors.items():
        items.append(text + (f" ({n} times)" if n > 1 else ""))
    lines = ["Problems and warnings"]
    if not items:
        return lines + ["  None."]
    return lines + _capped(items, "  ! ") + ["  More detail: status.txt, or on the server: "
                                             "sudo journalctl -u botsy-tick -u botsy-daily -n 100 --no-pager"]


def _held(by: dict[str, list[dict]]) -> list[str]:
    held = by.get("notification_held", [])
    if not held:
        return []
    shown: list[str] = []
    for e in held:
        lines = [ln for ln in str(e.get("text") or "").splitlines() if ln.strip()]
        if lines and lines[0].strip() == "Botsy:":      # the tick message's first line says nothing
            lines = lines[1:]
        # The main lines; the indented ones under them are commands and details, shown above.
        main = [ln.split(" On the server type:")[0] for ln in lines if not ln[:1].isspace()]
        shown += main or lines[:1] or ["(empty)"]
    return ["Discord messages held during quiet hours (not sent again)"] + _capped(shown, "  - ")


PLAN_EVENT_TEXT = {
    "plan_posted": "posted", "plan_started": "started", "plan_expired": "expired (not approved in time)",
    "no_plan": "no plan this week", "plan_missed": "missed (the server was off)", "plan_held": "put on hold",
    "plan_done": "every line made", "plan_reminder": "reminder sent", "plan_approved": "approved",
}
_BY = ("user", "hermes", "botsy")


def _plan(by: dict[str, list[dict]], settings: dict, until: _dt.datetime) -> list[str]:
    """The weekly plan (service/plan.py): what happened to it in the window, its state now
    (deadline, progress) and the lines dropped. Approvals and mode changes say who asked."""
    lines = ["Weekly plan"]
    happened = []
    for kind in ("plan_posted", "plan_reminder", "plan_approved", "plan_started", "plan_held", "plan_expired",
                 "no_plan", "plan_missed", "plan_done"):
        for e in by.get(kind, []):
            week = e.get("plan_week") or "?"
            who = e.get("by") if e.get("by") in _BY else None
            happened.append(f"{week}: {PLAN_EVENT_TEXT[kind]}"
                            + (f" ({e.get('reason')})" if kind == "plan_posted" and e.get("reason") else "")
                            + (f", asked by {who}" if who and kind in ("plan_approved", "plan_held") else ""))
    for e in by.get("plan_line_dropped", []):
        happened.append(f"{e.get('plan_week')}: line {e.get('brief_id')} dropped ({e.get('reason')})")
    for e in by.get("plan_mode_changed", []):
        who = e.get("by") if e.get("by") in _BY else None
        happened.append("plans now " + ("wait for your approval" if e.get("mode_to") == "approve"
                                        else "start by themselves") + (f", asked by {who}" if who else ""))
    lines += _capped(happened, "  - ")
    try:
        from service import plan

        now_lines = plan.summary(settings, now=until).get("lines") or []
    except Exception as e:  # noqa: BLE001 - the plan must never stop the log
        now_lines = [f"Could not be read: {type(e).__name__}"]
    lines += [f"  {_clip(x)}" for x in now_lines[:MAX_LINES]]
    return lines if len(lines) > 1 else []


_CODE = re.compile(r"[a-z][a-z0-9_]{0,40}")


def _orders(by: dict[str, list[dict]]) -> list[str]:
    """Orders (service/orders.py) from the journal: counts by kind, the approvals and who asked,
    and the reason codes of refusals. Fixed fields only (never text from Hermes)."""
    def kind_of(e) -> str:
        k = e.get("order_kind")
        return k if isinstance(k, str) and _CODE.fullmatch(k) else "unknown"

    rows = []
    for ev, word in (("order_applied", "applied"), ("order_refused", "refused"), ("order_failed", "failed")):
        counts: dict[str, int] = {}
        for e in by.get(ev, []):
            counts[kind_of(e)] = counts.get(kind_of(e), 0) + 1
        if counts:
            rows.append(f"{word}: " + ", ".join(f"{k} {n}" for k, n in sorted(counts.items())))
    for e in by.get("order_approved", []):
        who = e.get("by") if e.get("by") in _BY else "?"
        rows.append(f"approved: {kind_of(e)} {e.get('order_id')} (asked by {who}"
                    + ("" if e.get("announced") or kind_of(e) in ("weekly_plan", "plan_revision")
                       else "; the Discord post failed") + ")")
    reasons: dict[str, int] = {}
    for e in by.get("order_refused", []) + by.get("order_failed", []):
        for r in e.get("reasons") or []:
            if isinstance(r, str) and _CODE.fullmatch(r):
                reasons[r] = reasons.get(r, 0) + 1
    if reasons:
        rows.append("refusal reasons: " + ", ".join(f"{r} {n}" for r, n in sorted(reasons.items())))
    return ["Orders"] + _capped(rows, "  - ") if rows else []


def _next(by: dict[str, list[dict]], until: _dt.datetime, reached: bool, zone: _dt.tzinfo) -> list[str]:
    timers = ZoneInfo(TIMER_ZONE)
    h, m = SCHEDULE["daily_check"]
    lines = ["What runs next", f"  Daily check: {_show(_next_run(until, h, m, zone=timers), zone)}"]
    h, m = SCHEDULE["daily_log"]
    lines.append(f"  Next daily log: {_show(_next_run(until, h, m, zone=timers), zone)}")
    wd, h, m = SCHEDULE["search_list"]
    lines.append(f"  Weekly search list: {_show(_next_run(until, h, m, weekday=wd, zone=timers), zone)}")
    wd, h, m = SCHEDULE["weekly_plan"]
    lines.append(f"  Weekly plan: {_show(_next_run(until, h, m, weekday=wd, zone=timers), zone)}")
    lines.append("  New exports are taken in a few minutes after the helper uploads them.")
    if reached:
        lines.append("  Paid steps stay paused until next month (spending cap).")
    return lines


# ---------------------------------------------------------------- build / write

def build(since: _dt.datetime, until: _dt.datetime, settings: dict) -> str:
    """The daily log for since <= time < until (naive datetimes count as UTC)."""
    from core import events
    from service.paths import is_live
    from service.status import redact

    since, until = _utc(since), _utc(until)
    zone = tz(settings)
    by: dict[str, list[dict]] = {}
    for e in events.read(since, until, settings=settings):
        by.setdefault(e["kind"], []).append(e)
    live = is_live(settings)
    title = "Botsy daily log"
    lines = [title, "=" * len(title),
             f"What happened from {_show(since, zone)} to {_show(until, zone)} ({_zone_name(zone)}).",
             ("Mode: LIVE. Real images, listing text and Etsy drafts; you approve every draft in Etsy."
              if live else "Mode: TEST (dry run). No API calls and no Etsy drafts.")]
    spend_lines, reached = _spend(settings, since, until)
    sections = [_exports(by), _research(by), _plan(by, settings, until), _orders(by), _items(by), _drafts(by),
                _review(by, live), spend_lines, _problems(by), _held(by), _next(by, until, reached, zone)]
    for section in sections:
        if section:
            lines += [""] + section
    return redact(CRLF.join(lines) + CRLF)


def write(outbox: str | Path, text: str, date: str) -> Path:
    """Save outbox/daily-log-<date>.txt and outbox/daily-log.txt (mode 640, temp + rename).
    Keeps the newest KEEP_DATED dated logs. Returns the daily-log.txt path."""
    from service.status import _atomic_write, redact

    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    data = redact(text).encode("utf-8", "backslashreplace")
    _atomic_write(outbox / f"{DATED_PREFIX}{check_date(date)}.txt", data)
    latest = outbox / LATEST_NAME
    _atomic_write(latest, data)
    dated = sorted(p for p in outbox.glob(f"{DATED_PREFIX}????-??-??.txt") if p.is_file())
    for old in dated[:-KEEP_DATED]:
        old.unlink(missing_ok=True)
    return latest


__all__ = ["LATEST_NAME", "SCHEDULE", "TIMER_ZONE", "build", "log_date", "review_command", "write"]
