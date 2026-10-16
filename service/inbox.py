"""Take finished uploads out of the inbox (docs/SERVER.md sections 1 and 2).

The upload account can put anything into incoming/, so every entry is untrusted until
it has been read once, safely, and recognized as a research export:
- Links are never followed: a link could point at .env or the Etsy token file. Only
  plain files are opened, with O_NOFOLLOW, and checked again on the open handle.
- The size limit is checked before reading, and the read itself is capped.
- What lands in data/research_inputs is the server's own copy of the exact bytes that
  were checked, so the uploader cannot swap the file after the check.
- A sha256 ledger (data/research_inputs/.ingested.json) drops repeat uploads, so the
  same export sent twice does not start another research run.
- `<name>.part` files (an upload in progress) and dotfiles are left alone. A `.part`
  file untouched for service.part_max_age_h (2 days) is an upload that was given up on,
  so it is deleted: nothing else would ever remove it.
- An uploaded name can hold anything, including bytes that are not UTF-8 and terminal
  control characters. Names are turned into plain printable text (display_name) before
  they go into logs, the ledger, reason files or status.txt; one odd name must never
  stop the rest of the inbox from being processed.

A rejected file is moved to data/inbox_rejected/<date>-<name> with a .reason.txt next
to it in plain words. Links, special files and files over the size limit are deleted
instead of kept (only the reason file is written): the server never reads them, and a
huge upload cannot fill the disk twice. The user's own copy on their PC is untouched.
"""
from __future__ import annotations

import datetime as _dt
import errno
import hashlib
import logging
import os
import re
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.config import check_date, today
from core.items import read_json, slugify, write_json
from research import columns
from research.defaults import load_config as research_config
from research.niches import niche_from_filename
from service.paths import load_config

log = logging.getLogger("service.inbox")

LEDGER_NAME = ".ingested.json"
PART_SUFFIX = ".part"
SHAPES = ("listing", "keyword")       # what research.columns can aggregate
FILE_MODE = 0o640                     # the botsy group may read stored files
_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")
_LINK = "it is a link (shortcut) to another file, and the server never follows links"
_HELP = ("The server only uses CSV files exported from EverBee or eRank (their Export CSV "
         "button). Your own copy of the file was not changed.")


@dataclass
class IngestResult:
    accepted: list[Path] = field(default_factory=list)       # stored files in inputs_dir
    duplicates: list[str] = field(default_factory=list)      # original names
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (original name, reason)

    def to_dict(self) -> dict:
        return {"accepted": [p.name for p in self.accepted], "duplicates": list(self.duplicates),
                "rejected": [{"name": n, "reason": r} for n, r in self.rejected]}

    def empty(self) -> bool:
        return not (self.accepted or self.duplicates or self.rejected)


class _Refused(Exception):
    """An upload that is not used. `data` is the content when it was safe to read."""

    def __init__(self, reason: str, data: bytes | None = None):
        super().__init__(reason)
        self.reason = reason
        self.data = data


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def is_finished_upload(name: str) -> bool:
    """False for dotfiles and `*.part` (the helper renames a .part file once it is complete)."""
    return not (name.startswith(".") or name.lower().endswith(PART_SUFFIX))


def display_name(name: str) -> str:
    """The uploaded name as printable text: bytes that are not UTF-8 become \\xNN and
    control characters (newlines, terminal escapes, direction marks) become '?'."""
    text = os.fsencode(name).decode("utf-8", "backslashreplace")
    return "".join(ch if ch.isprintable() else "?" for ch in text)


def safe_name(name: str, max_len: int = 120) -> str:
    """A file name made only of plain characters (uploaded names can hold anything)."""
    cleaned = _UNSAFE.sub("_", name).strip(" .")
    return cleaned[:max_len].rstrip(" .") or "upload"


# ---------------------------------------------------------------- ledger

def load_ledger(path: Path) -> dict:
    """{sha256: {"file", "received", "original_name"}}. A broken ledger is set aside, not fatal:
    the worst case is that a repeat upload gets stored once more (research dedupes rows)."""
    if not path.exists():
        return {}
    try:
        data = read_json(path)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as e:
        backup = path.with_name(f"{path.name}.broken-{time.strftime('%Y%m%d-%H%M%S')}")
        log.warning("ingest ledger %s is unreadable (%s); moved to %s, starting a new one",
                    path, e, backup.name)
        os.replace(path, backup)
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


# ---------------------------------------------------------------- file handling

def _write_bytes(path: Path, data: bytes, mode: int = FILE_MODE) -> Path:
    """Atomic write; the temp name starts with a dot so research never reads half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def _remove(path: Path) -> None:
    """Delete an inbox entry (the link itself for a link). Leaving it is logged, not fatal."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("could not remove %s from the inbox: %s", display_name(path.name), e)


def _drop_stale_part(path: Path, name: str, max_age_s: float, now: float) -> None:
    """Delete an abandoned `<name>.part` (an upload the helper gave up on)."""
    try:
        st = os.lstat(path)
    except OSError:
        return
    if (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)) and now - st.st_mtime > max_age_s:
        _remove(path)
        log.info("removed %s: an unfinished upload last changed %.0f hours ago",
                 display_name(name), (now - st.st_mtime) / 3600)


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f}"


def _read_upload(path: Path, name: str, max_bytes: int) -> bytes:
    """The bytes of a plain file in the inbox, read without following links (or _Refused)."""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise _Refused(_LINK)
    if not stat.S_ISREG(st.st_mode):
        raise _Refused("it is not a normal file")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_BINARY", 0))
    try:
        fd = os.open(path, flags)
    except OSError as e:
        if e.errno == errno.ELOOP:  # swapped for a link after the lstat above
            raise _Refused(_LINK) from None
        if e.errno in (errno.EACCES, errno.EPERM):
            raise _Refused("the server is not allowed to read it (file permissions)") from None
        raise
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise _Refused("it is not a normal file")
        if st.st_nlink > 1:
            raise _Refused("it is a hard link to another file; upload a normal copy instead")
        too_big = (f"it is larger than the {_mb(max_bytes)} MB limit "
                   f"({_mb(st.st_size)} MB); the server did not keep it")
        if st.st_size > max_bytes:
            raise _Refused(too_big)
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:  # it grew while it was being read
        raise _Refused(too_big)
    return data


def _check_export(data: bytes, name: str, overrides: dict, source_tool: str) -> columns.Export:
    """Read the bytes the way research will, from a private temp copy that keeps the
    original name (the tool is detected from the name, then the header row)."""
    with tempfile.TemporaryDirectory(prefix="botsy-ingest-") as tmp:
        p = Path(tmp) / safe_name(name)
        p.write_bytes(data)
        try:
            export = columns.load_export(p, overrides, source_tool)
        except Exception as e:  # pandas, csv or decoding errors: all mean "not a readable table"
            detail = display_name(" ".join(str(e).split()))[:160]
            raise _Refused(f"it could not be read as a CSV table ({type(e).__name__}: {detail})",
                           data) from None
    if export.shape not in SHAPES:
        raise _Refused("no product-title or keyword column was found, so it does not look like "
                       "an EverBee or eRank export", data)
    if export.frame.empty:
        raise _Refused("it has column names but no rows of data", data)
    return export


def accepted_name(name: str, tool: str, sha256: str, date: str, known_niches) -> str:
    """`[<niche>__][own_shop-|trends-]<tool>-<date>-<sha8>.csv`.

    The `<niche>__` prefix keeps the filename niche override working, and an own-shop or
    trends file keeps its role word, because research tells those files apart by name.
    """
    niche = niche_from_filename(name, known_niches)
    base = name.split("__", 1)[1] if niche else name
    role = re.sub(r"[\s-]+", "_", base.strip().lower())
    prefix = ("own_shop-" if role.startswith(("own_shop", "ownshop"))
              else "trends-" if role.startswith("trends") else "")
    final = f"{prefix}{slugify(tool or 'unknown', 20)}-{check_date(date)}-{sha256[:8]}.csv"
    return f"{niche}__{final}" if niche else final


def _unique_stem(folder: Path, name: str) -> Path:
    """folder/name, or name-2, name-3 ... when it (or its reason file) is taken."""
    first = folder / name
    stem, suffix = first.stem, first.suffix
    candidate, n = first, 1
    while candidate.exists() or candidate.with_name(candidate.name + ".reason.txt").exists():
        n += 1
        candidate = folder / f"{stem}-{n}{suffix}"
    return candidate


def _reject(path: Path, name: str, refused: _Refused, rejected_dir: Path, date: str,
            received: str) -> None:
    rejected_dir.mkdir(parents=True, exist_ok=True)
    kept = _unique_stem(rejected_dir, f"{date}-{safe_name(name)}")
    if refused.data is not None:
        _write_bytes(kept, refused.data)
    note = [f"File: {display_name(name)}", f"Received: {received}", f"Not used because {refused.reason}.",
            (f"A copy is kept here as {kept.name}." if refused.data is not None
             else "The server did not keep a copy."), "", _HELP]
    _write_bytes(kept.with_name(kept.name + ".reason.txt"), ("\n".join(note) + "\n").encode("utf-8"))
    _remove(path)
    log.info("rejected %s: %s", display_name(name), refused.reason)


# ---------------------------------------------------------------- ingest

def ingest(incoming: str | Path, inputs_dir: str | Path, rejected_dir: str | Path, *,
           settings: dict, date: str | None = None) -> IngestResult:
    """Move every finished upload out of `incoming` (docs/SERVER.md section 2).

    `date` (default today) goes into the stored and rejected file names. A mistake in
    settings research.column_overrides raises ValueError before any upload is touched.
    """
    incoming, inputs_dir, rejected_dir = Path(incoming), Path(inputs_dir), Path(rejected_dir)
    cfg = load_config(settings)
    rcfg = research_config(settings)
    overrides, source_tool = rcfg["column_overrides"], rcfg["source_tool"]
    columns.map_columns([], overrides)  # raises on a settings mistake
    known = list(settings.get("niches") or rcfg["niche_rules"])
    max_bytes = int(cfg["max_upload_bytes"])
    part_max_age_s = float(cfg["part_max_age_h"]) * 3600
    day = check_date(date) if date else today()
    if not incoming.is_dir():
        raise FileNotFoundError(f"the upload folder {incoming} does not exist "
                                "(deploy/lightsail/setup.sh creates it)")

    ledger_path = inputs_dir / LEDGER_NAME
    ledger = load_ledger(ledger_path)
    result = IngestResult()
    with os.scandir(incoming) as it:
        entries = sorted(it, key=lambda e: e.name)
    started = time.time()
    for entry in entries:
        name, shown = entry.name, display_name(entry.name)
        if not is_finished_upload(name):
            if not name.startswith(".") and part_max_age_s > 0:
                _drop_stale_part(incoming / name, name, part_max_age_s, started)
            continue
        if entry.is_dir(follow_symlinks=False):
            log.warning("ignoring folder %s in the inbox", shown)
            continue
        path = incoming / name
        received = _now()
        try:
            data = _read_upload(path, name, max_bytes)
            if not name.lower().endswith(".csv"):
                ext = display_name(Path(name).suffix) or "(no extension)"
                raise _Refused(f"only .csv files are accepted and this one is {ext}", data)
            if not data.strip():
                raise _Refused("the file is empty", data)
            sha = hashlib.sha256(data).hexdigest()
            if sha in ledger:
                _remove(path)
                result.duplicates.append(shown)
                log.info("duplicate %s (already received as %s)", shown, ledger[sha].get("file"))
                continue
            export = _check_export(data, name, overrides, source_tool)
            dest = _write_bytes(inputs_dir / accepted_name(name, export.tool, sha, day, known), data)
            ledger[sha] = {"file": dest.name, "received": received, "original_name": shown}
            # Saved before the upload is removed: a crash in between means a duplicate later, never a loss.
            write_json(ledger_path, ledger)
            _remove(path)
            result.accepted.append(dest)
            log.info("accepted %s as %s (%s %s export, %d rows)", shown, dest.name, export.tool,
                     export.shape, len(export.frame))
        except FileNotFoundError:
            continue  # renamed or removed by the uploader while we looked; the next tick sees it
        except _Refused as refused:
            _reject(path, name, refused, rejected_dir, day, received)
            result.rejected.append((shown, refused.reason))
    return result


# ---------------------------------------------------------------- waiting for a batch

def _snapshot(incoming: Path) -> frozenset:
    """Every entry with size and time, uploads in progress included (not dotfiles)."""
    out = set()
    try:
        with os.scandir(incoming) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                out.add((e.name, st.st_size, st.st_mtime_ns))
    except OSError:
        pass
    return frozenset(out)


def wait_for_quiet(incoming: str | Path, quiet_s: float, *, max_wait_s: float,
                   poll_s: float = 5.0, clock=time.monotonic, sleep=time.sleep) -> float:
    """Return once nothing in `incoming` has changed for quiet_s seconds, or after max_wait_s.

    A user working down the weekly search list exports one file after another; waiting
    for a quiet inbox lets one research run see the whole batch. Returns seconds waited.
    """
    incoming = Path(incoming)
    start = clock()
    last, quiet_since = _snapshot(incoming), start
    while True:
        now = clock()
        if now - quiet_since >= quiet_s or now - start >= max_wait_s:
            return now - start
        sleep(max(0.01, min(poll_s, quiet_s - (now - quiet_since), max_wait_s - (now - start))))
        snap = _snapshot(incoming)
        if snap != last:
            last, quiet_since = snap, clock()
