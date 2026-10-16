"""Evidence window: when each export was received, and which exports a run may use
(docs/SERVER.md section 12).

Every market export has a received date, taken from (first match wins):
1. the ingest name `[<niche>__][own_shop-|trends-]<tool>-<YYYY-MM-DD>-<sha8>.csv`
   (service/inbox.py writes it; the date is the server's day the upload arrived);
2. the ingest ledger `<inputs>/.ingested.json` (`{sha256: {"file", "received", ...}}`);
3. the file's modification time.
Dates are the server's local calendar days, like the run date.

Briefs and history snapshots use only exports received within research.window_days
(35) of the run date. Older exports stay on disk until research.keep_days (180), when the
service's cleanup deletes them; it can use `received_date` for the same answer.
An export dated after the run date (a run for an earlier --date) is kept: it is newer
evidence, not stale evidence. Trend files are windowed the same way (an old one stops
boosting demand). Own-shop files are not: they are our own numbers, and the publisher
rewrites own_shop_stats.csv every day. "Fresh" rows are rows
from exports received in the run date's ISO week; history counts weeks by them
(research/history.py).
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from pathlib import Path

from core.config import check_date, section
from core.items import read_json
from research.defaults import DEFAULTS as RESEARCH_DEFAULTS

log = logging.getLogger(__name__)

# Read with core.config.section(settings, "research", DEFAULTS); settings win.
# window_days (35): briefs use exports received within this many days of the run date;
# keep_days (180): accepted exports older than this are deleted by the service cleanup.
DEFAULTS: dict = {k: RESEARCH_DEFAULTS[k] for k in ("window_days", "keep_days")}
LEDGER_NAME = ".ingested.json"   # service/inbox.py LEDGER_NAME
INGEST_NAME_RE = re.compile(r"(?:^|[-_])(\d{4}-\d{2}-\d{2})-[0-9a-f]{8}\.csv$", re.I)


def window_config(settings: dict) -> dict:
    """{"window_days", "keep_days"}; an empty value in settings.yaml means the default."""
    cfg = section(settings, "research", DEFAULTS)

    def num(key: str) -> int:
        value = cfg.get(key)
        return max(1, int(DEFAULTS[key] if value is None else value))

    return {"window_days": num("window_days"), "keep_days": num("keep_days")}


def load_ledger(inputs_dir: str | Path) -> dict[str, str]:
    """{stored file name: received timestamp} from the ingest ledger; {} when missing or
    unreadable (research only reads it; ingest owns and repairs it)."""
    path = Path(inputs_dir) / LEDGER_NAME
    try:
        if path.is_symlink() or not path.is_file():
            return {}
        data = read_json(path)
    except (OSError, ValueError):
        log.warning("research: %s could not be read; using file dates instead", path.name)
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for entry in data.values():
        if isinstance(entry, dict) and isinstance(entry.get("file"), str) \
                and isinstance(entry.get("received"), str):
            out[entry["file"]] = entry["received"]
    return out


def _from_name(name: str) -> _dt.date | None:
    m = INGEST_NAME_RE.search(name)
    if not m:
        return None
    try:
        return _dt.date.fromisoformat(check_date(m.group(1)))
    except ValueError:
        return None


def _from_ledger(stamp: str | None) -> _dt.date | None:
    if not stamp:
        return None
    try:
        when = _dt.datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            return _dt.date.fromisoformat(check_date(stamp.strip()[:10]))
        except ValueError:
            return None
    if when.tzinfo is not None:
        try:
            when = when.astimezone()  # the server's local day, like ingest names and the run date
        except (OverflowError, OSError, ValueError):  # a stamp at the edge of the calendar
            pass
    return when.date()


def received_date(path: str | Path, ledger: dict[str, str] | None = None) -> _dt.date:
    """When the export at `path` was received: ingest name, else ledger, else mtime.

    Raises OSError only (the file is gone, or its time cannot be read), so callers need
    one fallback."""
    path = Path(path)
    stamp = (ledger or {}).get(path.name)
    day = _from_name(path.name) or _from_ledger(stamp if isinstance(stamp, str) else None)
    if day is not None:
        return day
    mtime = path.stat().st_mtime
    try:
        return _dt.date.fromtimestamp(mtime)
    except (OverflowError, ValueError) as e:
        raise OSError(f"{path.name}: file time {mtime!r} is not a date") from e


def in_window(received: _dt.date, run_date: str, window_days: int) -> bool:
    """True when `received` is at most `window_days` days before the run date."""
    return (_dt.date.fromisoformat(run_date) - received).days <= window_days


def iso_week(day: str | _dt.date) -> tuple[int, int]:
    """(ISO year, ISO week) of a date; two days in one ISO week give the same answer."""
    d = _dt.date.fromisoformat(day) if isinstance(day, str) else day
    iso = d.isocalendar()
    return iso[0], iso[1]


def recent(paths, run_date: str, settings: dict, inputs_dir: str | Path | None = None) -> list[Path]:
    """The `paths` received within research.window_days of `run_date`: the exports a
    research run on that date counts (same rule as research.run). `inputs_dir` (default:
    the folder of the first path) holds the ingest ledger."""
    paths = [Path(p) for p in paths]
    if not paths:
        return []
    ledger = load_ledger(inputs_dir if inputs_dir is not None else paths[0].parent)
    days = window_config(settings)["window_days"]
    out = []
    for p in paths:
        try:
            if in_window(received_date(p, ledger), check_date(run_date), days):
                out.append(p)
        except OSError:
            continue
    return out
