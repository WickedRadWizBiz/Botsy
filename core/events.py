"""Event journal (docs/SERVER.md section 9): what the server did, one JSON line per event.

    emit(kind, *, settings=None, **fields) -> None     (never raises)
    read(since, until=None, kinds=None, *, settings=None, path=None) -> list[dict]
    events_path(settings=None) -> Path
    sign(event, *, key=None) -> dict / verify(event, *, key=None) -> bool
                                  (HMAC-SHA256 with .env BOTSY_EVENT_KEY, for events sent to Hermes)

Each line is {"ts": ISO 8601 UTC, "kind": ..., **fields}. The file is settings
service.events_file (data/events.jsonl). It rotates at service.events_max_mb (5 MB) to
events-1.jsonl (newest) ... events-5.jsonl (oldest; service.events_keep), so the journal
never grows without limit. The digest (service/digest.py) and the tick's Discord message
are built from it.

Events hold only our own data: theme names, titles, item ids, counts, our own error
summaries. Never a competitor's shop name, listing link, title or image, and never a
secret. Callers pass text that is already scrubbed (service/tick.py quiet_error).

Writing is best effort: a full disk or a bad value is logged and never stops the caller.
While pytest runs, a journal inside the repo (the user's real data/) is neither read nor
written; tests point service.events_file into tmp_path (the same rule as core/spend.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from core.config import ROOT, load_settings, resolve, section

log = logging.getLogger("core.events")

# Read from settings "service" with core.config.section(settings, "service", DEFAULTS).
DEFAULTS: dict = {
    "events_file": "data/events.jsonl",
    "events_max_mb": 5,       # rotate when the file would grow past this
    "events_keep": 5,         # rotated files kept: events-1.jsonl (newest) ... events-5.jsonl
}

# The kinds the service emits (docs/SERVER.md section 9). Others are allowed, for new needs.
KINDS = (
    "export_received", "export_rejected",
    "export_routed",          # a book (KDP/BookBeam) file kept in research_inputs/kdp/ for the KDP reader
    "briefs_written", "stage_changed",
    "item_generated", "qa_failed", "listing_written", "draft_created", "held_for_review",
    "spend_warning", "budget_reached", "token_expiring",
    "error", "search_list_written",
    "notification_held",      # a Discord message held during quiet hours (it goes into the digest)
    # Orders (service/orders.py; docs/CONTRACTS.md order/v1). No codes (user, 2026-10-21).
    # Fixed fields only: ids, kinds, counts, money and reason codes; never text from Hermes.
    "order_accepted",         # order_id, order_kind, level, created_by, week, needs_approval, status
    "order_refused",          # order_kind, created_by, stage, reasons, fields
    "order_applied",          # order_id, order_kind, created_by, week, approved_by?
    "order_approved",         # order_id, order_kind, level, by (user | hermes), count, announced (posted urgent)
    "order_undone",           # order_id, order_kind, by
    "order_failed",           # order_id, order_kind, stage, reasons
    "control_refused",        # tool, reasons (botsy-control: an act call refused before it became an order)
    # The weekly plan (service/plan.py; docs/CONTRACTS.md plan/v1 files).
    "plan_due",               # plan_week, draft_order_id, packs, cost_usd (signed; the signed copy is data/plans/<week>/plan_due.json)
    "plan_posted",            # plan_week, order_id, packs, cost_usd, needs_approval, created_by | reason | reasons
    "plan_reminder",          # plan_week, order_id, reminder
    "plan_expired",           # plan_week, order_id
    "plan_started",           # plan_week, order_id, how | reason, by (how=approved)
    "plan_approved",          # plan_week, order_id, by (user | hermes), reason, packs, cost_usd (posted urgent)
    "no_plan",                # plan_week, reason
    "plan_missed",            # plan_week, hours_late
    "plan_line_dropped",      # plan_week, brief_id, reason
    "plan_mode_changed",      # mode_from, mode_to, by, approved_plans? (to auto: posted urgent)
    "plan_held",              # plan_week, order_id, by
    "plan_done",              # plan_week, order_id
)
KIND_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
RESERVED = ("ts", "kind")


def _cfg(settings: dict | None) -> dict:
    return section(settings if settings is not None else load_settings(), "service", DEFAULTS)


def events_path(settings: dict | None = None) -> Path:
    """The journal file (settings service.events_file, relative to the repo root)."""
    return resolve(_cfg(settings).get("events_file") or DEFAULTS["events_file"])


def rotated_path(path: Path, n: int) -> Path:
    """events.jsonl -> events-<n>.jsonl in the same folder."""
    return path.with_name(f"{path.stem}-{n}{path.suffix}")


def _number(value, default, minimum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(minimum, value)


def emit(kind: str, *, settings: dict | None = None, **fields) -> None:
    """Append one event. Never raises: a failure is logged and the caller carries on."""
    try:
        if not isinstance(kind, str) or not KIND_RE.fullmatch(kind):
            log.warning("event kind %r is not a plain name; event not recorded", kind)
            return
        cfg = _cfg(settings)
        path = resolve(cfg.get("events_file") or DEFAULTS["events_file"])
        if _off_limits(path):
            log.debug("test run: not writing the real event journal %s", path)
            return
        line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind}
        line.update((k, v) for k, v in fields.items() if k not in RESERVED)
        data = (json.dumps(line, ensure_ascii=False, default=str) + "\n").encode("utf-8", "backslashreplace")
        max_bytes = float(_number(cfg.get("events_max_mb"), DEFAULTS["events_max_mb"], 0.0001)) * 1_000_000
        keep = int(_number(cfg.get("events_keep"), DEFAULTS["events_keep"], 0))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size and size + len(data) > max_bytes:
            _rotate(path, keep)
        elif size and not _ends_with_newline(path):
            data = b"\n" + data                      # a line cut short (full disk, crash): keep ours whole
        with open(path, "ab", buffering=0) as f:     # one write per line, at the end
            f.write(data)
    except Exception as e:  # noqa: BLE001 - best effort by contract
        log.warning("could not record event %r: %s: %s", kind, type(e).__name__, e)


def _ends_with_newline(path: Path) -> bool:
    with open(path, "rb") as f:
        f.seek(-1, os.SEEK_END)
        return f.read(1) == b"\n"


def _rotate(path: Path, keep: int) -> None:
    """events.jsonl -> events-1.jsonl, events-1 -> events-2 ... the oldest beyond `keep` goes
    (also older copies left from a larger events_keep). Only events-<n>.jsonl files next to
    the journal are ever removed."""
    for n, old in _rotated(path):
        if n >= max(keep, 1):
            old.unlink(missing_ok=True)
    if keep <= 0:
        path.unlink(missing_ok=True)
        return
    for n in range(keep - 1, 0, -1):
        src = rotated_path(path, n)
        if src.exists():
            os.replace(src, rotated_path(path, n + 1))
    os.replace(path, rotated_path(path, 1))


def _utc(when: datetime) -> datetime:
    if not isinstance(when, datetime):
        raise TypeError(f"expected a datetime, got {type(when).__name__}")
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)


def _rotated(path: Path) -> list[tuple[int, Path]]:
    """(n, events-<n>.jsonl) for the rotated files next to the journal, in any order."""
    pattern = re.compile(re.escape(path.stem) + r"-(\d{1,6})" + re.escape(path.suffix))
    rotated = []
    try:
        for p in path.parent.iterdir():
            m = pattern.fullmatch(p.name)
            if m:
                rotated.append((int(m.group(1)), p))
    except OSError:
        pass
    return rotated


def _files(path: Path) -> list[Path]:
    """Rotated files oldest first (events-5 ... events-1), then the current file."""
    return [p for _, p in sorted(_rotated(path), reverse=True)] + [path]


def read(since: datetime, until: datetime | None = None, kinds=None, *,
         settings: dict | None = None, path: str | Path | None = None) -> list[dict]:
    """Events with since <= ts < until (naive datetimes count as UTC), oldest first.

    `kinds` limits the result to these kinds (a name or a list). Lines that cannot be read
    (a crash mid-write, a hand edit) are skipped. A missing journal gives []."""
    since = _utc(since)
    until = _utc(until) if until is not None else None
    if isinstance(kinds, str):
        kinds = {kinds}
    elif kinds is not None:
        kinds = set(kinds)
    path = Path(path) if path is not None else events_path(settings)
    if _off_limits(path):
        return []
    out: list[tuple[datetime, int, dict]] = []
    bad = 0
    for f in _files(path):
        try:
            if f.stat().st_mtime < since.timestamp() - 1:   # every line in it is older
                continue
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for raw in fh:
                if not raw.strip():
                    continue
                try:
                    event = json.loads(raw)
                    ts = _utc(datetime.fromisoformat(event["ts"]))
                except (ValueError, TypeError, KeyError):
                    bad += 1
                    continue
                if not isinstance(event, dict) or not isinstance(event.get("kind"), str):
                    bad += 1
                    continue
                if ts < since or (until is not None and ts >= until):
                    continue
                if kinds is not None and event["kind"] not in kinds:
                    continue
                out.append((ts, len(out), event))
    if bad:
        log.warning("event journal %s: skipped %d unreadable line(s)", path, bad)
    return [e for _, _, e in sorted(out, key=lambda t: (t[0], t[1]))]


def _off_limits(path: Path) -> bool:
    """While pytest runs, the journal inside the repo (the user's real data/) is off limits."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return False
    return True


# ---------------------------------------------------------------- signed events for Hermes
#
# Events Botsy sends to Hermes (docs/OVERSEER.md "How Botsy and Hermes talk") are signed with
# HMAC-SHA256 over the event's fixed fields, with the key in .env BOTSY_EVENT_KEY (at least 32
# characters; `openssl rand -hex 32`). Hermes holds the same key and checks the signature, so
# a message on its local webhook route that Botsy did not write is ignored.
#
#   sign(event, *, key=None) -> dict      a copy with "sig" added (unsigned copy + one warning without a key)
#   verify(event, *, key=None) -> bool    True only for a valid "sig" made with the key
#
# What is signed: the canonical JSON (sorted keys, no spaces, UTF-8) of every field except
# "sig". Only plain JSON values may appear (text, numbers, true/false, null, lists and objects
# of them), so the bytes Hermes checks are the bytes Botsy signed. The signature is
# "v1=<64 hex>"; v1 names this scheme so it can change later.

KEY_ENV = "BOTSY_EVENT_KEY"
MIN_KEY_CHARS = 32
SIG_FIELD = "sig"
SIG_PREFIX = "v1="
_sign_warned: set[str] = set()


def _event_key(key: str | bytes | None) -> bytes | None:
    if key is None:
        from core.config import load_env

        load_env()
        key = os.environ.get(KEY_ENV, "")
    if isinstance(key, str):
        key = key.strip().encode("utf-8")
    if not key:
        if "missing" not in _sign_warned:
            _sign_warned.add("missing")
            log.warning("%s is not set in .env: events for Hermes are sent unsigned", KEY_ENV)
        return None
    if len(key) < MIN_KEY_CHARS:
        if "short" not in _sign_warned:
            _sign_warned.add("short")
            log.warning("%s is shorter than %d characters; events are sent unsigned "
                        "(make one with: openssl rand -hex 32)", KEY_ENV, MIN_KEY_CHARS)
        return None
    return key


def _plain(value, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, (list, tuple)):
        return all(_plain(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _plain(v, depth + 1) for k, v in value.items())
    return False


def canonical(event: dict) -> bytes:
    """The bytes that are signed: every field except "sig", as canonical JSON.
    Raises ValueError for a value that is not plain JSON."""
    if not isinstance(event, dict):
        raise ValueError("an event must be a JSON object")
    body = {k: v for k, v in event.items() if k != SIG_FIELD}
    if not _plain(body):
        raise ValueError("an event to sign may hold only plain JSON values")
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(event: dict, *, key: str | bytes | None = None) -> dict:
    """A copy of `event` with "sig" (HMAC-SHA256 of canonical(event)). Without BOTSY_EVENT_KEY
    (or with one that is too short) the copy is returned unsigned and a warning is logged once,
    so Hermes can tell (it should ignore unsigned events once it has a key)."""
    import hashlib
    import hmac

    out = {k: v for k, v in dict(event).items() if k != SIG_FIELD}
    k = _event_key(key)
    if k is None:
        return out
    out[SIG_FIELD] = SIG_PREFIX + hmac.new(k, canonical(out), hashlib.sha256).hexdigest()
    return out


def verify(event: dict, *, key: str | bytes | None = None) -> bool:
    """True when `event` carries a "sig" made with the key over exactly these fields.
    False for a missing or wrong signature, a changed field, no key, or a malformed event."""
    import hashlib
    import hmac

    try:
        sig = event.get(SIG_FIELD) if isinstance(event, dict) else None
        k = _event_key(key)
        if not isinstance(sig, str) or not sig.startswith(SIG_PREFIX) or k is None:
            return False
        want = SIG_PREFIX + hmac.new(k, canonical(event), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig.encode("ascii", "replace"), want.encode("ascii"))
    except (ValueError, TypeError):
        return False


__all__ = ["DEFAULTS", "KEY_ENV", "KINDS", "canonical", "emit", "events_path", "read", "rotated_path", "sign",
           "verify"]
