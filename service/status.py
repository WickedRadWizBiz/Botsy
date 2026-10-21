"""What the server did, for people: outbox/status.txt, plus outbox/status.json.

status.txt is the user's window into the server. The Windows helper copies it to
Documents\\Botsy every hour, so it is plain text with CRLF line endings (Notepad) and
says in plain words what happened and what to do about problems.

status.json keeps recent tick records, so the weekly search list can quote last week's
numbers (`week`: exports, briefs, items, drafts) and status.txt can be rebuilt at any
time. It also remembers which research inputs were last researched (`last_research`),
so a daily forced tick does not rebuild the same briefs under a new date (new brief ids
would make new items for themes that already have items).

Files are written with mode 640: the upload account is in group botsy and reads the
outbox. Nothing here should hold a secret, and as a last line of defence any value of a
secret environment variable (API keys, tokens; loaded from .env) that turns up in an
error message is replaced by [secret] before either file is written.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
from pathlib import Path

SCHEMA = "status/v1"
TEXT_NAME = "status.txt"
JSON_NAME = "status.json"
FILE_MODE = 0o640
CRLF = "\r\n"
MAX_TICKS = 500
MAX_LINES_SHOWN = 15     # rejected files, failures and problems listed per section
MAX_DETAIL = 300         # characters of one error message in status.txt
WEEK_KEYS = ("ticks", "exports", "duplicates", "rejected", "briefs", "design_briefs",
             "items", "drafts", "practice_drafts", "errors")


_SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL", re.I)
_NOT_SECRET = re.compile(r"_(FILE|PATH|DIR|URI|URL|ISSUED)$", re.I)
# Secrets whose names the patterns above do not catch: the Discord webhook link holds a token.
ALWAYS_SECRET = ("DISCORD_WEBHOOK_URL",)
# Any Discord webhook link or path (its last part is a token), even one not in the environment
# (for example a library error that names the request path).
_WEBHOOK = re.compile(r"(?:https?://[\w.-]*discord(?:app)?\.com)?/api/(?:v\d+/)?webhooks/[^\s'\"<>]+", re.I)


def redact(text: str, environ=None) -> str:
    """Replace the value of every secret-looking environment variable, and any Discord
    webhook link, with [secret]."""
    env = os.environ if environ is None else environ
    values = {str(v).strip() for k, v in env.items()
              if (k in ALWAYS_SECRET or (_SECRET_NAME.search(k) and not _NOT_SECRET.search(k)))
              and len(str(v).strip()) >= 8}
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "[secret]")
    return _WEBHOOK.sub("[secret]", text)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _parse(ts) -> _dt.datetime | None:
    try:
        t = _dt.datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)


def _show_time(ts) -> str:
    t = _parse(ts)
    return t.astimezone().strftime("%Y-%m-%d %H:%M %Z").strip() if t else "never"


def new_record(*, date: str, forced: bool, live: bool, started: str | None = None) -> dict:
    """An empty tick record; tick.py fills it in as the steps run."""
    return {
        "started": started or now_iso(), "finished": None, "date": date,
        "forced": bool(forced), "live": bool(live), "own_stats": None,
        "ingest": {"accepted": [], "duplicates": [], "rejected": [], "routed": []},
        "research": {"ran": False, "stage": None, "brief_files": [], "briefs": 0, "notes": []},
        "run": {"ran": False, "dry_run": not live, "items": 0, "generated": 0, "drafts": 0,
                "awaiting_review": 0, "cooldown": 0, "budget_wait": 0, "failed": [], "summaries": [],
                "notes": [], "exit_code": 0},
        "errors": [], "exit_code": 0,
    }


def has_activity(rec: dict) -> bool:
    """A tick that found nothing and did nothing only moves `last_checked`."""
    ing = rec.get("ingest") or {}
    return bool(rec.get("forced") or rec.get("errors") or rec.get("own_stats")
                or ing.get("accepted") or ing.get("duplicates") or ing.get("rejected") or ing.get("routed")
                or (rec.get("research") or {}).get("ran") or (rec.get("run") or {}).get("ran"))


def load(outbox: str | Path) -> dict | None:
    """The status document, or None when there is none yet (or it is unreadable)."""
    try:
        doc = json.loads((Path(outbox) / JSON_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("ticks", []), list) else None


def week_totals(ticks: list[dict], *, now: _dt.datetime | None = None, days: int = 7) -> dict:
    """Totals over the ticks that started in the last `days` days.

    `briefs` counts distinct brief files and `design_briefs` the briefs inside them:
    research names a brief file after the niche and the date, so a second run on the
    same day rewrites the same files, and that must not count them twice. `drafts`
    counts real Etsy drafts and `practice_drafts` the simulated ones of a dry run.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    since = now - _dt.timedelta(days=days)
    out = dict.fromkeys(WEEK_KEYS, 0)
    out["since"] = since.date().isoformat()
    brief_files: set[str] = set()
    design_briefs: dict[frozenset, int] = {}   # files written together -> briefs in them (latest)
    for rec in ticks:
        started = _parse(rec.get("started"))
        if not started or started < since:
            continue
        ing, res, run = rec.get("ingest") or {}, rec.get("research") or {}, rec.get("run") or {}
        out["ticks"] += 1
        out["exports"] += len(ing.get("accepted") or [])
        out["duplicates"] += len(ing.get("duplicates") or [])
        out["rejected"] += len(ing.get("rejected") or [])
        files = frozenset(str(f) for f in res.get("brief_files") or [])
        brief_files |= files
        if files:
            design_briefs[files] = int(res.get("briefs") or 0)
        out["items"] += int(run.get("generated") or 0)
        out["drafts" if run.get("dry_run") is False else "practice_drafts"] += int(run.get("drafts") or 0)
        out["errors"] += len(rec.get("errors") or [])
    out["briefs"], out["design_briefs"] = len(brief_files), sum(design_briefs.values())
    return out


def _atomic_write(path: Path, data: bytes) -> None:
    # Temp file + rename, so the helper's hourly download never gets half a file.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, FILE_MODE)  # mkstemp makes 600; the upload account must read it
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write(outbox: str | Path, doc: dict) -> Path:
    """Save status.json and status.txt (rendered from it); return the status.txt path."""
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    text = redact(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    _atomic_write(outbox / JSON_NAME, text.encode("utf-8", "backslashreplace"))
    txt = outbox / TEXT_NAME
    _atomic_write(txt, redact(render(doc)).encode("utf-8", "backslashreplace"))
    return txt


def update(outbox: str | Path, record: dict, *, keep_days: int = 35,
           last_research: dict | None = None, now: _dt.datetime | None = None, plan: dict | None = None) -> dict:
    """Add one tick record (when it did anything), prune old ones, recount the week, write.
    `plan`: the weekly plan's summary (service.plan.summary), shown in its own section."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    doc = load(outbox) or {}
    if plan is not None:
        doc["plan"] = _plan_doc(plan)
    ticks = [t for t in doc.get("ticks") or [] if isinstance(t, dict)]
    if has_activity(record):
        ticks.append(record)
    cutoff = now - _dt.timedelta(days=max(7, int(keep_days)))
    ticks = [t for t in ticks if (_parse(t.get("started")) or now) >= cutoff][-MAX_TICKS:]
    doc.update({"schema": SCHEMA, "updated": now.isoformat(timespec="seconds"),
                "last_checked": record.get("finished") or now.isoformat(timespec="seconds"),
                "live": bool(record.get("live")), "ticks": ticks,
                "week": week_totals(ticks, now=now)})
    if last_research is not None:
        doc["last_research"] = last_research
    write(outbox, doc)
    return doc


def _plan_doc(plan: dict) -> dict:
    """The part of service.plan.summary() kept in status.json (fixed fields and its plain lines)."""
    keep = ("enabled", "mode", "approved_count", "approvals_before_auto", "week", "status", "order_id",
            "needs_approval", "approval_reason", "deadline", "auto_start", "progress", "tick")
    out = {k: plan.get(k) for k in keep if k in plan}
    out["lines"] = [" ".join(str(x).split())[:MAX_DETAIL] for x in (plan.get("lines") or [])][:MAX_LINES_SHOWN]
    out["updated"] = now_iso()
    return out


def set_plan(outbox: str | Path, plan: dict) -> Path:
    """Store the weekly plan's summary in status.json and rewrite status.txt (the plan
    commands call this; a tick passes it to update())."""
    doc = load(outbox) or {"schema": SCHEMA, "ticks": [], "last_checked": None}
    doc["plan"] = _plan_doc(plan)
    doc["updated"] = now_iso()
    return write(outbox, doc)


def refresh(outbox: str | Path, *, live: bool, now: _dt.datetime | None = None, plan: dict | None = None) -> Path:
    """`python -m service status`: rebuild status.txt from status.json (or say nothing ran yet)."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    doc = load(outbox) or {"schema": SCHEMA, "ticks": [], "last_checked": None}
    if plan is not None:
        doc["plan"] = _plan_doc(plan)
    doc.update({"updated": now.isoformat(timespec="seconds"), "live": bool(live),
                "week": week_totals(doc.get("ticks") or [], now=now)})
    return write(outbox, doc)


# ---------------------------------------------------------------- status.txt

def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _cap(items: list, indent: str) -> list[str]:
    lines = [f"{indent}{' '.join(str(x).split())[:MAX_DETAIL]}" for x in items[:MAX_LINES_SHOWN]]
    if len(items) > MAX_LINES_SHOWN:
        lines.append(f"{indent}... and {len(items) - MAX_LINES_SHOWN} more")
    return lines


def _tick_lines(rec: dict) -> list[str]:
    ing, res, run = rec.get("ingest") or {}, rec.get("research") or {}, rec.get("run") or {}
    accepted, dups, rejected = (ing.get("accepted") or [], ing.get("duplicates") or [],
                                ing.get("rejected") or [])
    lines = [(f"Exports received: {len(accepted)} new, {len(dups)} already received before, "
              f"{len(rejected)} not used")]
    lines += _cap([f"Not used: {r.get('name')}: {r.get('reason')}" for r in rejected], "  ")
    if rejected:
        where = ing.get("rejected_dir") or "data/inbox_rejected"
        lines.append(f"  (Copies and reasons are in {where} on the server.)")
    routed = ing.get("routed") or []
    if routed:
        lines.append(f"Book (KDP) files kept for the KDP reader: {len(routed)}")
        lines += _cap([f"Kept: {r.get('name')}" for r in routed], "  ")

    if res.get("ran"):
        files = res.get("brief_files") or []
        stage = f", {res['stage']} stage" if res.get("stage") else ""
        lines.append(f"Research: {_plural(len(files), 'brief file')} written "
                     f"({_plural(int(res.get('briefs') or 0), 'design brief')}{stage})")
    else:
        lines.append("Research: not run (no new exports)")
    lines += _cap(res.get("notes") or [], "  - ")

    if run.get("ran"):
        lines.append(f"Items made: {int(run.get('generated') or 0)}")
        cooled = int(run.get("cooldown") or 0)
        if cooled:
            lines.append(f"Skipped: {cooled} (same theme made recently)")
        waiting_cap = int(run.get("budget_wait") or 0)
        if waiting_cap:
            lines.append(f"Waiting for next month (spending cap reached): {waiting_cap}")
        lines += _cap([f"Note: {n}" for n in run.get("notes") or []], "  ")
        drafts = int(run.get("drafts") or 0)
        if run.get("dry_run") is False:
            lines.append(f"Etsy drafts created: {drafts} (approve them in Etsy; nothing goes live by itself)")
        else:
            lines.append(f"Practice drafts (test mode, nothing sent to Etsy): {drafts}")
        waiting = int(run.get("awaiting_review") or 0)
        if waiting:
            flag = "" if run.get("dry_run") is False else " --dry-run"
            lines.append(f"Waiting for your review: {waiting}. On the server type: botsy qa review list{flag}")
        failed = run.get("failed") or []
        if failed:
            lines.append(f"Items that failed a check: {len(failed)}")
            lines += _cap(failed, "  - ")
    else:
        lines.append("Items: none made this time")
        lines += _cap([f"Note: {n}" for n in run.get("notes") or []], "  ")

    plan = rec.get("plan")
    if isinstance(plan, dict) and plan.get("mode") and plan.get("mode") != "normal":
        lines.append(f"Weekly plan: {plan.get('note') or plan.get('mode')}")
        dropped = plan.get("dropped") or []
        lines += _cap([f"Plan line dropped: {d.get('brief_id')} ({d.get('reason')})" for d in dropped
                       if isinstance(d, dict)], "  ")
    if rec.get("own_stats"):
        lines.append(f"Our own shop stats: {rec['own_stats'].get('detail', '')}")
    cleanup = rec.get("cleanup")
    if isinstance(cleanup, dict) and cleanup:
        lines.append("Old files tidied away: " + ", ".join(
            f"{str(k).replace('_', ' ')} {n}" for k, n in cleanup.items()))
    lines += _cap([f"Could not tidy: {x}" for x in rec.get("cleanup_problems") or []], "  ")
    errors = rec.get("errors") or []
    lines.append("Problems: none" if not errors else f"Problems: {len(errors)}")
    lines += _cap(errors, "  ! ")
    return ["  " + ln for ln in lines]


def render(doc: dict) -> str:
    """status.txt: mode, last check, the latest tick that did something, last 7 days."""
    title = "Botsy server status"
    lines = [title, "=" * len(title), f"Updated: {_show_time(doc.get('updated'))}"]
    if doc.get("live"):
        lines.append("Mode: LIVE. Real images, listing text and Etsy drafts. "
                     "You approve every draft in Etsy.")
    else:
        lines.append('Mode: TEST (dry run). No API calls and no Etsy drafts. To go live, see '
                     '"Go live" in deploy/lightsail/README.md.')
    lines.append(f"Last checked for uploads: {_show_time(doc.get('last_checked'))}")
    plan = doc.get("plan")
    if isinstance(plan, dict) and plan.get("lines"):
        lines += ["", "Weekly plan", *_cap([str(x) for x in plan["lines"]], "  ")]
    ticks = [t for t in doc.get("ticks") or [] if isinstance(t, dict)]
    if not ticks:
        lines += ["", ("Nothing has happened yet. Make a research export (an eRank/EverBee CSV, or the "
                       "filled-in Etsy insights template saved as CSV) and the Botsy helper on your PC "
                       "uploads it.")]
    else:
        last = ticks[-1]
        why = "daily check" if last.get("forced") else "new upload"
        lines += ["", f"Last run: {_show_time(last.get('started'))} ({why})", *_tick_lines(last)]
    week = doc.get("week")
    if isinstance(week, dict) and week.get("ticks"):
        drafts = (_plural(int(week.get("drafts", 0)), "Etsy draft") if doc.get("live")
                  else _plural(int(week.get("practice_drafts", 0)), "practice draft"))
        lines += ["", f"Last 7 days ({_plural(int(week['ticks']), 'run')}):",
                  (f"  {_plural(int(week.get('exports', 0)), 'export')} received, "
                   f"{_plural(int(week.get('briefs', 0)), 'brief file')}, "
                   f"{_plural(int(week.get('items', 0)), 'item')} made, {drafts}, "
                   f"{_plural(int(week.get('errors', 0)), 'problem')}")]
    lines += ["", "Server log: sudo journalctl -u botsy-tick -u botsy-daily -n 100 --no-pager"]
    return CRLF.join(lines) + CRLF
