"""Disk caps (docs/SERVER.md section 14): what the server deletes at the end of every tick.

    run(settings, *, now=None) -> CleanupResult   (a dict of what was removed)

Three folders, each with its own rule:
- Rejected uploads (service.rejected_dir, data/inbox_rejected/): a kept file and its
  `.reason.txt` go together. Entries older than service.rejected_keep_days (30) go
  first, then the oldest ones until they hold at most service.rejected_max_mb (200 MB).
  Only files named the way ingest names them (`<YYYY-MM-DD>-<name>`, the date they were
  refused) are counted or removed; anything else in the folder is left alone.
- Accepted exports (research.inputs_dir, data/research_inputs/): files ingest stored
  (named `...-<YYYY-MM-DD>-<sha8>.csv`, or listed in the ingest ledger) received more than
  research.keep_days (180) days ago, never fewer days than research.window_days, so an
  export research still counts is never removed. The received date is the one research
  uses (research.window.received_date: name, else ledger, else modification time). The
  file's sha256 stays in `.ingested.json`, marked `"deleted": "<date>"`, so uploading the
  same file again is still recognised as a duplicate. The ledger is saved BEFORE any file
  is removed (a crash in between leaves a file marked deleted, never a removed file the
  ledger does not know), and a file whose sha256 cannot be worked out is kept. Files
  ingest did not store there (the publisher's own_shop_stats.csv, files copied in by
  hand) are left alone.
- Practice items (pipeline.dry_run_output_dir, output/dry-run/): item folders made in
  a dry run (manifest generator.dry_run true) and created more than
  service.dry_run_keep_days (14) days ago, with their review entries, plus run summaries
  in runs/ older than that.

A days or MB setting of 0 switches that rule off; an empty one means the default.

Safety:
- Links are never followed and never deleted: each folder is opened with O_NOFOLLOW and
  everything is removed through that open folder, so nothing outside it can be reached.
  A folder that is itself a link is not cleaned.
- Nothing under the real output folder (paths.output_dir) is touched, and nothing with a
  real (not dry-run) publish.json: an Etsy draft record is never deleted. When the
  dry-run folder is the real output folder (or holds it), that part is skipped.
- The rejected folder is not cleaned when it is (or holds, or sits in) the exports,
  briefs, history, inbox or output folders: a settings mistake must not empty them.
- A problem in one folder is recorded and the others are still cleaned. run() never
  raises for a disk or settings problem.
"""
from __future__ import annotations

import datetime as _dt
import errno
import hashlib
import logging
import os
import re
import shutil
import stat
from pathlib import Path

from core import items
from core.config import check_date, section
from service.paths import RESEARCH_PATH_DEFAULTS, service_paths

log = logging.getLogger("service.cleanup")

# Read with core.config.section(settings, "service", DEFAULTS); settings win.
DEFAULTS: dict = {
    "rejected_max_mb": 200,      # data/inbox_rejected/ holds at most this much, oldest removed first
    "rejected_keep_days": 30,    # rejected uploads and reason files older than this are removed
    "dry_run_keep_days": 14,     # output/dry-run/ practice items older than this are removed
}
# Read with section(settings, "research", ...); research/defaults.py has the same values.
RESEARCH_DEFAULTS: dict = {"keep_days": 180, "window_days": 35}
LEDGER_NAME = ".ingested.json"        # service/inbox.py LEDGER_NAME
REASON_SUFFIX = ".reason.txt"
DATED_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")                       # rejected: <date>-<name>
INGEST_NAME_RE = re.compile(r"(?:^|[-_])(\d{4}-\d{2}-\d{2})-[0-9a-f]{8}\.csv$", re.I)   # accepted exports
RUN_NAME_RE = re.compile(r"^run-(\d{4}-\d{2}-\d{2})-[0-9]{6}(?:-\d+)?\.json$")
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


class CleanupResult(dict):
    """What run() removed, one list per rule (the keys of KEYS):
    rejected_uploads (each with its reason file), old_exports, practice_items (item ids)
    and practice_run_summaries. Only removals are dict items, so a caller that counts the
    values (status.txt: "Old files tidied away") counts nothing else. `freed_bytes` and
    `problems` (plain text, e.g. a folder that is a link) are attributes."""

    KEYS = ("rejected_uploads", "old_exports", "practice_items", "practice_run_summaries")

    def __init__(self):
        super().__init__({k: [] for k in self.KEYS})
        self.freed_bytes = 0
        self.problems: list[str] = []


def run(settings: dict, *, now: _dt.datetime | _dt.date | None = None) -> CleanupResult:
    """Apply the three disk rules (module docstring). `now` (default: now, server local
    time) sets "today". Never raises for a disk or settings problem: it is logged and
    kept in the result's `problems`."""
    today = _today(now)
    result = CleanupResult()
    for name, step in (("rejected uploads", _rejected), ("old exports", _exports),
                       ("practice items", _dry_run)):
        try:
            step(settings, today, result)
        except Exception as e:  # one folder's problem never stops the others (or the tick)
            log.exception("cleanup of %s failed", name)
            result.problems.append(f"{name}: not cleaned ({type(e).__name__}: {e})")
    text = describe(result)
    if text:
        log.info("cleanup: %s", text)
    for p in result.problems:
        log.warning("cleanup: %s", p)
    return result


def describe(result: dict) -> str:
    """One plain line about what run() removed ("" when nothing was removed)."""
    parts = []
    for key, what in (("rejected_uploads", "rejected upload(s)"), ("old_exports", "old export(s)"),
                      ("practice_items", "practice item(s)"),
                      ("practice_run_summaries", "old practice run summary(s)")):
        if result.get(key):
            parts.append(f"{len(result[key])} {what}")
    if not parts:
        return ""
    return f"removed {', '.join(parts)} ({getattr(result, 'freed_bytes', 0) / 1_000_000:.1f} MB freed)"


# ---------------------------------------------------------------- settings and dates

def _today(now) -> _dt.date:
    if now is None:
        return _dt.date.today()
    if isinstance(now, _dt.datetime):
        return now.date()
    if isinstance(now, _dt.date):
        return now
    raise TypeError(f"now must be a datetime or date, got {type(now).__name__}")


def _number(cfg: dict, key: str, defaults: dict, kind=int):
    """A setting as a number >= 0; empty means the default, 0 means "no limit"."""
    value = cfg.get(key)
    if value is None or value == "":
        value = defaults[key]
    if isinstance(value, bool):
        raise ValueError(f"settings {key} must be a number, got {value!r}")
    return max(kind(0), kind(value))


def _day(text: str | None) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(check_date(text))
    except ValueError:
        return None


def _mtime_day(st: os.stat_result) -> _dt.date:
    return _dt.date.fromtimestamp(st.st_mtime)


def _overlaps(a: Path, b: Path) -> bool:
    """True when a and b are the same folder or one holds the other."""
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


# ---------------------------------------------------------------- folder access (no links)

class _LinkedFolder(Exception):
    pass


def _open_dir(path: Path) -> int | None:
    """An fd for `path`, opened without following a link; None when it does not exist."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        raise _LinkedFolder(path)
    if not stat.S_ISDIR(st.st_mode):
        return None
    try:
        fd = os.open(path, _DIR_FLAGS)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):  # swapped for a link after the lstat
            raise _LinkedFolder(path) from None
        raise
    return fd


def _link_problem(path: Path) -> str:
    return f"{items.repo_relative(path)} is a link; the server never follows links, so it was not cleaned"


def _plain_files(fd: int) -> list[tuple[str, os.stat_result]]:
    """(name, stat) of the regular files in an open folder: no links, folders or dotfiles."""
    out = []
    with os.scandir(fd) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                out.append((e.name, st))
    return out


def _unlink(fd: int, name: str, result: dict) -> int:
    """Remove one regular file from an open folder; returns the bytes freed (0 if not removed)."""
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            return 0
        os.unlink(name, dir_fd=fd)
    except FileNotFoundError:
        return 0
    except OSError as e:
        result.problems.append(f"could not remove {name}: {e.strerror or e}")
        return 0
    result.freed_bytes += st.st_size
    return st.st_size


# ---------------------------------------------------------------- rejected uploads

def _rejected(settings: dict, today: _dt.date, result: dict) -> None:
    cfg = section(settings, "service", DEFAULTS)
    keep_days = _number(cfg, "rejected_keep_days", DEFAULTS)
    max_bytes = int(_number(cfg, "rejected_max_mb", DEFAULTS, float) * 1_000_000)
    paths = service_paths(settings)
    folder = paths.rejected
    for other in (paths.inputs, paths.briefs, paths.history, paths.incoming, paths.outbox,
                  items.output_root(settings), items.dry_run_root(settings)):
        if _overlaps(folder, other):
            result.problems.append(
                f"rejected uploads: service.rejected_dir ({items.repo_relative(folder)}) shares a folder with "
                f"{items.repo_relative(other)}, so it was not cleaned; give rejected uploads their own folder")
            return
    try:
        fd = _open_dir(folder)
    except _LinkedFolder:
        result.problems.append(_link_problem(folder))
        return
    if fd is None:
        return
    try:
        # A kept upload and its reason file are one entry; a link or oversize upload has
        # only the reason file.
        # Only `<YYYY-MM-DD>-<name>` entries (what ingest writes) are Botsy's to remove, so a
        # folder set by mistake (config/, a home folder) loses nothing of anyone else's.
        entries: dict[str, dict] = {}
        for name, st in _plain_files(fd):
            key = name[: -len(REASON_SUFFIX)] if name.endswith(REASON_SUFFIX) else name
            m = DATED_NAME_RE.match(key)
            day = _day(m.group(1)) if m else None
            if day is None:
                continue
            e = entries.setdefault(key, {"files": [], "size": 0, "mtime": st.st_mtime, "day": day})
            e["files"].append(name)
            e["size"] += st.st_size
            e["mtime"] = min(e["mtime"], st.st_mtime)
        order = sorted(entries.items(), key=lambda kv: (kv[1]["day"], kv[1]["mtime"], kv[0]))
        total = sum(e["size"] for _, e in order)
        for key, e in order:
            too_old = keep_days and (today - e["day"]).days > keep_days
            too_big = max_bytes and total > max_bytes
            if not (too_old or too_big):
                continue
            gone = False
            for name in sorted(e["files"]):
                freed = _unlink(fd, name, result)
                if freed or not os.path.lexists(folder / name):
                    gone = True
                    total -= freed
            if gone:
                result["rejected_uploads"].append(key)
    finally:
        os.close(fd)


# ---------------------------------------------------------------- accepted exports

def _received_day(path: Path, name: str, st: os.stat_result, ledger_received: dict[str, str]) -> _dt.date:
    """The day an export was received, the way research counts it (section 12)."""
    try:
        from research.window import received_date
    except ImportError:  # research.window not installed: the same rules, kept short
        m = INGEST_NAME_RE.search(name)
        day = _day(m.group(1)) if m else None
        stamp = ledger_received.get(name)
        if day is None and isinstance(stamp, str):
            day = _day(stamp.strip()[:10])
        return day or _mtime_day(st)
    return received_date(path, ledger_received)


def _sha256(fd: int, name: str) -> str | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        f = os.open(name, flags, dir_fd=fd)
    except OSError:
        return None
    h = hashlib.sha256()
    try:
        with os.fdopen(f, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                return None
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _exports(settings: dict, today: _dt.date, result: dict) -> None:
    rcfg = section(settings, "research", {**RESEARCH_PATH_DEFAULTS, **RESEARCH_DEFAULTS})
    keep_days = _number(rcfg, "keep_days", RESEARCH_DEFAULTS)
    if not keep_days:
        return
    window = _number(rcfg, "window_days", RESEARCH_DEFAULTS)
    try:  # the window research itself uses (it reads an empty or 0 value its own way)
        from research.window import window_config

        window = max(window, window_config(settings)["window_days"])
    except ImportError:
        pass
    if keep_days < window:
        log.warning("research.keep_days (%d) is shorter than research.window_days (%d); keeping exports "
                    "for %d days so research never loses one it still counts", keep_days, window, window)
        keep_days = window
    folder = service_paths(settings).inputs
    try:
        fd = _open_dir(folder)
    except _LinkedFolder:
        result.problems.append(_link_problem(folder))
        return
    if fd is None:
        return
    try:
        ledger_path = folder / LEDGER_NAME
        try:
            st = os.stat(LEDGER_NAME, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            st = None
        if st is not None and not stat.S_ISREG(st.st_mode):
            result.problems.append("old exports: the ingest ledger is not a normal file, so old exports "
                                      "were kept (a re-upload must still be recognised)")
            return
        try:
            ledger = items.read_json(ledger_path) if st is not None else {}
            if not isinstance(ledger, dict):
                raise ValueError("not a JSON object")
        except (OSError, ValueError):
            result.problems.append("old exports: the ingest ledger could not be read, so old exports "
                                      "were kept (a re-upload must still be recognised)")
            return
        by_file: dict[str, list[str]] = {}
        received: dict[str, str] = {}
        for sha, entry in ledger.items():
            if isinstance(entry, dict) and isinstance(entry.get("file"), str):
                by_file.setdefault(entry["file"], []).append(sha)
                if isinstance(entry.get("received"), str):
                    received[entry["file"]] = entry["received"]
        old: list[tuple[str, list[str]]] = []
        for name, st in sorted(_plain_files(fd)):
            if not name.lower().endswith(".csv") or not (name in by_file or INGEST_NAME_RE.search(name)):
                continue
            try:
                day = _received_day(folder / name, name, st, received)
            except OSError:
                continue  # gone, or no usable date: looked at again next time
            if (today - day).days <= keep_days:
                continue
            shas = by_file.get(name) or [s for s in [_sha256(fd, name)] if s]
            if not shas:
                result.problems.append(f"old exports: {name} could not be read, so it was kept "
                                       "(a re-upload of it must still be recognised)")
                continue
            old.append((name, shas))
        if not old:
            return
        # The ledger first: a crash before the files go leaves them marked deleted (harmless,
        # they are removed next time), never a removed export the ledger does not know.
        marked: dict[str, list[tuple[str, dict | None]]] = {}
        for name, shas in old:
            for sha in shas:
                before = ledger.get(sha)
                marked.setdefault(name, []).append((sha, dict(before) if isinstance(before, dict) else None))
                entry = ledger.get(sha)
                if not isinstance(entry, dict):
                    entry = ledger[sha] = {"file": name, "received": received.get(name), "original_name": name}
                entry["deleted"] = today.isoformat()
        items.write_json(ledger_path, ledger)
        kept = []
        for name, _ in old:
            if _unlink(fd, name, result) or not os.path.lexists(folder / name):
                result["old_exports"].append(name)
            else:
                kept.append(name)
        if kept:  # not removed after all: its ledger entry goes back to how it was (sha kept)
            for name in kept:
                for sha, before in marked[name]:
                    if before is not None:
                        ledger[sha] = before
                    else:
                        ledger[sha].pop("deleted", None)   # new entry: the file is still stored
            items.write_json(ledger_path, ledger)
    finally:
        os.close(fd)


# ---------------------------------------------------------------- practice (dry-run) items

def _dir_size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        for n in dirs + files:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


def _read_regular_json(fd: int, name: str):
    """JSON from a regular file in an open folder (never through a link); raises OSError/ValueError."""
    import json

    f = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
    with os.fdopen(f, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise ValueError("not a normal file")
        return json.loads(fh.read().decode("utf-8"))


def _practice_item(root: Path, name: str) -> tuple[bool, _dt.date | None, str]:
    """(removable, created day, why kept) for one folder in the dry-run root."""
    try:
        fd = _open_dir(root / name)
    except _LinkedFolder:
        return False, None, "a link"
    if fd is None:
        return False, None, "not a folder"
    try:
        try:
            manifest = _read_regular_json(fd, "manifest.json")
        except FileNotFoundError:
            return False, None, "not an item"
        except (OSError, ValueError):
            return False, None, "its manifest.json could not be read"
        if not isinstance(manifest, dict) or (manifest.get("generator") or {}).get("dry_run") is not True:
            return False, None, "not made in a dry run"
        try:
            record = _read_regular_json(fd, "publish.json")
        except FileNotFoundError:
            record = {"dry_run": True}
        except (OSError, ValueError):
            record = None  # unreadable: it may be a real draft record
        if not isinstance(record, dict) or record.get("dry_run") is not True:
            return False, None, "it has a real Etsy draft record (publish.json)"
        created = _day(manifest.get("created")) or _day(name[:10])
        if created is None:
            created = _mtime_day(os.stat("manifest.json", dir_fd=fd, follow_symlinks=False))
        return True, created, ""
    finally:
        os.close(fd)


def _dry_run(settings: dict, today: _dt.date, result: dict) -> None:
    cfg = section(settings, "service", DEFAULTS)
    keep_days = _number(cfg, "dry_run_keep_days", DEFAULTS)
    if not keep_days:
        return
    root, real = items.dry_run_root(settings), items.output_root(settings)
    if root.resolve() == real.resolve() or root.resolve() in real.resolve().parents:
        result.problems.append(
            f"practice items: pipeline.dry_run_output_dir ({items.repo_relative(root)}) is or holds the real "
            "output folder, so nothing there was removed")
        return
    try:
        fd = _open_dir(root)
    except _LinkedFolder:
        result.problems.append(_link_problem(root))
        return
    if fd is None:
        return
    try:
        with os.scandir(fd) as it:
            names = sorted(e.name for e in it if not e.name.startswith(".")
                           and e.is_dir(follow_symlinks=False) and items.valid_item_id(e.name))
        removed = []
        for name in names:
            removable, created, why = _practice_item(root, name)
            if not removable:
                if why not in ("not an item", "not a folder"):
                    log.info("cleanup: kept %s in the dry-run folder: %s", name, why)
                continue
            if (today - created).days <= keep_days:
                continue
            size = _dir_size(root / name)
            try:
                shutil.rmtree(name, dir_fd=fd)
            except OSError as e:
                result.problems.append(f"could not remove practice item {name}: {e.strerror or e}")
                continue
            result.freed_bytes += size
            result["practice_items"].append(name)
            removed.append(name)
        if removed:
            _drop_review_entries(root, removed, settings, result)
        _drop_run_summaries(root, today, keep_days, result)
    finally:
        os.close(fd)


def _drop_review_entries(root: Path, ids: list[str], settings: dict, result: dict) -> None:
    review = items.review_dir_for_root(root, settings)
    if not review.resolve().is_relative_to(root.resolve()):
        return  # the practice review queue lives in the dry-run folder; anything else is left alone
    try:
        fd = _open_dir(review)
    except _LinkedFolder:
        result.problems.append(_link_problem(review))
        return
    if fd is None:
        return
    try:
        for item_id in ids:
            _unlink(fd, f"{item_id}.json", result)
    finally:
        os.close(fd)


def _drop_run_summaries(root: Path, today: _dt.date, keep_days: int, result: dict) -> None:
    folder = root / "runs"
    try:
        fd = _open_dir(folder)
    except _LinkedFolder:
        result.problems.append(_link_problem(folder))
        return
    if fd is None:
        return
    try:
        for name, st in sorted(_plain_files(fd)):
            m = RUN_NAME_RE.match(name)
            day = (_day(m.group(1)) if m else None)
            if day is None or (today - day).days <= keep_days:
                continue
            if _unlink(fd, name, result):
                result["practice_run_summaries"].append(name)
    finally:
        os.close(fd)

