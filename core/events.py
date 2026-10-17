"""Event journal (docs/SERVER.md section 9): what the server did, one JSON line per event.

    emit(kind, *, settings=None, **fields) -> None     (never raises)
    read(since, until=None, kinds=None, *, settings=None, path=None) -> list[dict]
    events_path(settings=None) -> Path

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


__all__ = ["DEFAULTS", "KINDS", "emit", "events_path", "read", "rotated_path"]
