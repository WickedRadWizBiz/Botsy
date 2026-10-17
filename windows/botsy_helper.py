"""Botsy helper for Windows: sends research exports to the Botsy server.

It runs at logon from Task Scheduler with pythonw.exe (no window). Every poll it looks
in the Downloads folder for new CSV files that look like research exports (eRank,
EverBee, BookBeam and similar tools, or a filled-in Botsy template) and uploads each one,
once, into the server inbox with the Windows OpenSSH sftp client. The server sorts them:
Etsy data goes to research, book (KDP) data waits in its own folder.
Once an hour it fetches the weekly search list, the status file and the daily log into
Documents\\Botsy, and opens the search list when it changed (only the search list: the
status file and the daily log are just kept up to date). The same fetch brings Botsy's
blank templates into Documents\\Botsy\\templates; a copy the user has typed in is never
replaced. See docs/SERVER.md sections 7 and 11, and windows/README.md.

Rules this file keeps:
- Standard library only, so it runs on a plain python.org install with nothing else.
- It never deletes, moves or edits the user's files. It reads them, and uploads a
  snapshot copied into a temporary folder, so the bytes sent are the bytes checked.
- It never uploads a CSV that does not look like a research export (a bank statement,
  an order history, the shop's own listing download ...).
- It talks only to the user's own server. It never contacts etsy.com.

The module imports on Linux too, so the tests run there: Windows-only calls
(os.startfile, msvcrt, the no-window flag) are looked up at run time and guarded.
"""
from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

log = logging.getLogger("botsy_helper")

# Windows' own OpenSSH client (Settings > System > Optional features > OpenSSH Client).
WINDOWS_SFTP = r"C:\Windows\System32\OpenSSH\sftp.exe"
# A 32-bit Python on 64-bit Windows sees System32 redirected and finds OpenSSH only here.
SYSNATIVE_SFTP = r"C:\Windows\Sysnative\OpenSSH\sftp.exe"
CONFIG_PATH = r"%APPDATA%\Botsy\helper.json"

# Defaults for %APPDATA%\Botsy\helper.json. The installer writes the first six keys;
# the rest are here so an older or hand-written config keeps working.
DEFAULTS: dict = {
    "host": "",
    "user": "botsy-upload",
    "key": r"%USERPROFILE%\.ssh\botsy_upload",
    "watch_dir": r"%USERPROFILE%\Downloads",
    "poll_seconds": 30,
    "max_age_days": 14,
    "port": 22,
    "outbox_dir": r"%USERPROFILE%\Documents\Botsy",
    "outbox_every_seconds": 3600,
    # A status refresh this long after an upload, so the result shows up sooner.
    "fetch_after_upload_seconds": 900,
    "remote_incoming": "incoming",
    "remote_outbox": "outbox",
    "sftp": "",  # empty: WINDOWS_SFTP when it exists, else "sftp" on PATH
    "timeout_seconds": 300,
    "max_upload_bytes": 25_000_000,  # the server's service.max_upload_bytes
    "state_file": r"%APPDATA%\Botsy\state.json",
    # What the last fetch got from the server, so a list the user wrote notes in is not
    # replaced (or opened again) until the server has a new one.
    "outbox_state_file": r"%APPDATA%\Botsy\outbox.json",
    "log_file": r"%APPDATA%\Botsy\helper.log",
    "lock_file": r"%APPDATA%\Botsy\helper.lock",
}
PATH_KEYS = ("key", "watch_dir", "outbox_dir", "state_file", "outbox_state_file", "log_file",
             "lock_file")
INT_MINIMUMS = {"poll_seconds": 5, "max_age_days": 1, "port": 1, "outbox_every_seconds": 60,
                "fetch_after_upload_seconds": 60, "timeout_seconds": 10,
                "max_upload_bytes": 1}

BACKOFF_CAP_SECONDS = 600       # upload retries back off up to 10 minutes
QUIET_SECONDS = 5.0             # a file modified more recently may still be downloading
# A slow home connection still moves at least this much, so a big export gets more time.
MIN_UPLOAD_BYTES_PER_SECOND = 20_000
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3
# Fetched from the server's outbox into Documents\Botsy. Only SEARCH_LIST is opened.
# Fetched hourly from the server's outbox. The templates have fixed content: they are
# saved under templates\\ and a local copy that differs (the user typed in it) is kept.
TEMPLATE_FILES = ("etsy-insights-template.csv", "kdp-manual-template.csv")
TEMPLATES_SUBDIR = "templates"
OUTBOX_FILES = ("search-list.txt", "status.txt", "daily-log.txt", *TEMPLATE_FILES)
SEARCH_LIST = "search-list.txt"

# Host and user end up on the sftp command line: a value starting with "-" would be
# read as an option, and "host:22" as a remote path, so only plain names, IPv4
# addresses and [bracketed] IPv6 addresses are accepted.
HOST_RE = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]*|\[[0-9A-Fa-f:.]+\])$")
USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
REMOTE_DIR_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")


class ConfigError(RuntimeError):
    """helper.json is missing or wrong. The message says what to do, in plain words."""


# ------------------------------------------------------------------ configuration

_PCT_VAR = re.compile(r"%([A-Za-z_][A-Za-z0-9_()]*)%")


def _lookup(name: str, env: Mapping[str, str]) -> str | None:
    """Environment variable, case-insensitive like Windows, with fallbacks off Windows."""
    upper = {k.upper(): v for k, v in env.items()}
    value = upper.get(name.upper())
    if value:
        return value
    # Only reached off Windows (tests, development): sensible stand-ins.
    home = upper.get("USERPROFILE") or str(Path.home())
    fallbacks = {"USERPROFILE": home,
                 "APPDATA": os.path.join(home, "AppData", "Roaming"),
                 "LOCALAPPDATA": os.path.join(home, "AppData", "Local")}
    return fallbacks.get(name.upper())


def expand_path(value: str | os.PathLike, env: Mapping[str, str] | None = None) -> Path:
    r"""'%USERPROFILE%\Downloads' -> Path. Expands %VAR% (Windows style) and ~.

    os.path.expandvars only knows %VAR% on Windows, so the expansion is done here,
    which also lets the Linux tests check it. Off Windows, backslashes become "/".
    """
    env = os.environ if env is None else env

    def sub(m: re.Match) -> str:
        found = _lookup(m.group(1), env)
        return found if found is not None else m.group(0)

    text = os.path.expanduser(_PCT_VAR.sub(sub, str(value)))
    if os.sep == "/":
        text = text.replace("\\", "/")
    return Path(text)


def default_sftp() -> str:
    """Windows' OpenSSH sftp.exe when present, else whatever "sftp" is on PATH."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    for candidate in (WINDOWS_SFTP.replace("C:\\Windows", root, 1),
                      SYSNATIVE_SFTP.replace("C:\\Windows", root, 1)):
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("sftp") or "sftp"


def load_config(path: str | os.PathLike | None = None,
                env: Mapping[str, str] | None = None) -> dict:
    """Read helper.json, fill in DEFAULTS, expand paths. Raises ConfigError.

    Read as utf-8-sig because Windows PowerShell 5.1 writes UTF-8 files with a BOM.
    """
    cfg_path = expand_path(path or CONFIG_PATH, env)
    try:
        raw = cfg_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ConfigError(f"No settings file at {cfg_path}. Run install_helper.ps1 first.") from None
    except OSError as e:
        raise ConfigError(f"Could not read {cfg_path}: {e}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{cfg_path} is not valid JSON ({e}). Run install_helper.ps1 "
                          "again to rewrite it.") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{cfg_path} should hold one JSON object. Run install_helper.ps1 again.")

    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in data.items() if v is not None})
    cfg["host"] = str(cfg["host"]).strip()
    cfg["user"] = str(cfg["user"]).strip()
    if not cfg["host"]:
        raise ConfigError(f"No server address in {cfg_path}. Run install_helper.ps1 again "
                          "and type the server address when it asks.")
    if not HOST_RE.match(cfg["host"]):
        raise ConfigError(f"The server address {cfg['host']!r} in {cfg_path} looks wrong. "
                          "It should look like 3.120.45.67 or botsy.example.com.")
    if not USER_RE.match(cfg["user"]):
        raise ConfigError(f"The user name {cfg['user']!r} in {cfg_path} looks wrong "
                          "(it is normally botsy-upload).")
    for key in ("remote_incoming", "remote_outbox"):
        cfg[key] = str(cfg[key]).strip().strip("/")
        if not REMOTE_DIR_RE.match(cfg[key]) or ".." in cfg[key].split("/"):
            raise ConfigError(f"{key} {cfg[key]!r} in {cfg_path} is not a plain folder name.")
    for key, minimum in INT_MINIMUMS.items():
        try:
            cfg[key] = max(minimum, int(cfg[key]))
        except (TypeError, ValueError):
            raise ConfigError(f"{key} in {cfg_path} should be a whole number.") from None
    for key in PATH_KEYS:
        cfg[key] = expand_path(cfg[key], env)
    cfg["sftp"] = str(expand_path(cfg["sftp"], env)) if cfg.get("sftp") else default_sftp()
    cfg["config_file"] = cfg_path
    return cfg


# ------------------------------------------------------------------ export sniffing

def _norm(cell: str) -> str:
    """'Avg. Searches ' -> 'avgsearches': case, spaces, punctuation, accents, BOM ignored."""
    folded = unicodedata.normalize("NFKD", cell).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


# Headers that only research exports carry: demand numbers from eRank, EverBee and
# similar tools, Etsy's Marketplace Insights numbers typed into the Botsy template, and
# book data (BookBeam exports, the kdp_manual template: BSR, print length ...).
SIGNAL_HEADERS = frozenset(_norm(h) for h in (
    "Mo. Sales", "Monthly Sales", "Est. Mo. Sales", "Est. Monthly Sales",
    "Estimated Monthly Sales", "Est. Sales", "Estimated Sales", "Est. Total Sales",
    "Total Sales", "30 Day Sales", "Sales Per Month",
    "Revenue", "Mo. Revenue", "Monthly Revenue", "Est. Mo. Revenue", "Est. Monthly Revenue",
    "Estimated Monthly Revenue", "Est. Revenue", "Estimated Revenue", "Total Revenue",
    "Favorites", "Favourites", "Total Favorites", "Num Favorers", "Hearts",
    "Reviews", "Total Reviews", "Review Count", "Number of Reviews",
    "Listing Age", "Listing Age (Days)", "Age (Days)", "Shop Age", "Conversion Rate",
    "Daily Views", "Avg. Searches", "Average Searches", "Avg. Monthly Searches",
    "Etsy Searches", "Est. Searches", "Searches", "Monthly Searches", "Search Volume",
    "Google Searches", "Search Trend", "Avg. Clicks", "Average Clicks", "CTR", "Avg. CTR",
    "Click Through Rate", "Etsy Competition", "Competition", "Competing Listings",
    "Tag Occurrences", "Search Results",
    "BSR", "Best Sellers Rank", "Best Seller Rank", "Sales Rank", "ASIN", "Print Length",
    "Page Count", "Publication Date", "Trim Size", "Ratings Count",
))
# Headers research exports share with many other CSVs (order histories, the shop's own
# listing download). They count toward the two, but cannot carry a file on their own.
GENERIC_HEADERS = frozenset(_norm(h) for h in (
    "Product Name", "Title", "Listing Title", "Product Title", "Listing Name", "Item Title",
    "Shop Name", "Price", "Tags", "Category", "Keywords", "Keyword", "Keyword Phrase",
    "Search Term", "Search Terms", "Author", "Dimensions", "Ratings",
))
# The first line of a Botsy template ("# botsy-template: etsy_insights v1"); Excel may
# pad it with empty cells or quote it when it saves the file as CSV.
TEMPLATE_MARKER_RE = re.compile(
    r'^\ufeff?\s*"?\s*#\s*botsy-template\s*:\s*[a-z0-9_]+(?:\s+v\d+)?\s*"?\s*[,;\t|]*\s*$', re.I)
KNOWN_HEADERS = SIGNAL_HEADERS | GENERIC_HEADERS
# Column names only order, customer or payment files carry. An Amazon order history has
# ASIN and Product Name (so it would pass the header test) next to the shipping address:
# a header row with one of these is never uploaded (no personal data leaves this PC;
# research/columns.py PERSONAL_HEADERS is the same list and the server refuses them too).
PERSONAL_HEADERS = frozenset((
    "shippingaddress", "billingaddress", "shipaddress", "shipaddress1", "shipaddress2",
    "shippingaddress1", "shippingaddress2", "streetaddress", "address", "address1", "address2",
    "addressline1", "addressline2", "shipname", "shiptoname", "shipto", "shipcity",
    "shipzipcode", "shippostalcode", "zipcode", "postalcode", "postcode", "recipient",
    "recipientname", "buyer", "buyername", "buyeruserid", "buyeremail", "buyerusername",
    "customer", "customername", "customeremail", "fullname", "firstname", "lastname",
    "email", "emailaddress", "phone", "phonenumber", "telephone", "orderid", "ordernumber",
    "transactionid", "paymentmethod", "paymenttype", "paymentinstrumenttype", "cardnumber",
    "trackingnumber", "carriernametrackingnumber", "giftmessage", "ipaddress"))
MIN_KNOWN_HEADERS = 2
SNIFF_LINES = 20
SNIFF_BYTES = 256 * 1024
DELIMITERS = (",", ";", "\t", "|")


def _looks_utf16_without_bom(data: bytes) -> str | None:
    """'utf-16-le' / 'utf-16-be' when every other byte is mostly NUL, else None."""
    sample = data[:400]
    if len(sample) < 8:
        return None
    half = len(sample) // 2
    if sample[1::2].count(0) > half * 0.7:
        return "utf-16-le"
    if sample[0::2].count(0) > half * 0.7:
        return "utf-16-be"
    return None


def decode_head(data: bytes) -> str | None:
    """The start of a file as text, or None when it is empty or binary.

    Handles the encodings Excel and the research tools write: UTF-8 with or without a
    BOM, UTF-16 (Excel's "Unicode text"), and cp1252 (older Windows "CSV" saves).
    """
    if not data:
        return None
    if data[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        text = data[: len(data) // 2 * 2].decode("utf-16", errors="ignore")
    elif (enc := _looks_utf16_without_bom(data)) is not None:
        text = data[: len(data) // 2 * 2].decode(enc, errors="ignore")
    elif b"\x00" in data:
        return None  # a zip (.xlsx renamed .csv), a picture, a PDF ...
    else:
        if data.startswith(codecs.BOM_UTF8):
            data = data[len(codecs.BOM_UTF8):]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            if e.start >= len(data) - 3:  # the read limit cut a character in half
                text = data[: e.start].decode("utf-8")
            else:
                text = data.decode("cp1252", errors="replace")
    text = text.lstrip("\ufeff")
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\r\n")
    if "\x00" in text or controls > max(2, len(text) // 50):
        return None
    return text


def is_header_row(line: str) -> bool:
    """True when some delimiter split of `line` has 2+ known headers, one a demand number."""
    for delim in DELIMITERS:
        try:
            cells = next(csv.reader([line], delimiter=delim))
        except (csv.Error, StopIteration):
            continue
        known = {_norm(c) for c in cells} & KNOWN_HEADERS
        if len(known) >= MIN_KNOWN_HEADERS and known & SIGNAL_HEADERS:
            return True
    return False


def has_personal_headers(line: str) -> bool:
    """True when some delimiter split of `line` names a personal-data column."""
    for delim in DELIMITERS:
        try:
            cells = next(csv.reader([line], delimiter=delim))
        except (csv.Error, StopIteration):
            continue
        if {_norm(c) for c in cells} & PERSONAL_HEADERS:
            return True
    return False


def is_template_marker(line: str) -> bool:
    """True for the first line of a Botsy template, trailing commas or quotes included."""
    return bool(TEMPLATE_MARKER_RE.match(line))


def looks_like_export(path: str | os.PathLike) -> bool:
    """True when one of the first 20 lines is a research-export header row, or the
    "# botsy-template:" line of a Botsy template.

    A header row needs at least two known research headers, and one of them has to be a
    demand number (sales, favorites, searches, competition, BSR ...). A bank statement
    or an order history has neither, so it is never uploaded. A header row that also
    names personal data (Shipping Address, Buyer, Order ID ...: an Amazon order history
    has ASIN and Product Name) is never uploaded either, template marker or not.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read(SNIFF_BYTES)
    except OSError:
        return False
    text = decode_head(data)
    if not text:
        return False
    marker = False
    for line in text.splitlines()[:SNIFF_LINES]:
        if is_template_marker(line):
            marker = True
        elif is_header_row(line):
            return not has_personal_headers(line)
        elif has_personal_headers(line):
            return False   # an order file whose header row the test above did not catch
    return marker


# ------------------------------------------------------------------ finding new files

@dataclass(frozen=True)
class Candidate:
    """A finished CSV in the watch folder that has not been uploaded yet."""
    path: Path
    sha256: str
    size: int
    mtime: float


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def is_csv_name(name: str) -> bool:
    """*.csv, but not dotfiles or Excel's "~$name.csv" lock files."""
    return name.lower().endswith(".csv") and not name.startswith((".", "~$"))


def find_candidates(watch_dir: str | os.PathLike, state: Mapping[str, dict],
                    max_age_days: float, *, seen: dict | None = None,
                    now: float | None = None, quiet_seconds: float = QUIET_SECONDS,
                    max_bytes: int = DEFAULTS["max_upload_bytes"]) -> list[Candidate]:
    """CSV files in `watch_dir` (not subfolders) that are recent, finished and new.

    - recent: modified within `max_age_days`;
    - finished: not modified in the last `quiet_seconds`, and the same size as at the
      previous poll (a browser may still be writing it);
    - new: its sha256 is not in `state`, so a re-downloaded identical file is skipped.

    `seen` is the caller's memory between polls: {path: (size, mtime_ns, sha256)}.
    It holds the previous sizes and saves hashing an unchanged file again.
    """
    now = time.time() if now is None else now
    seen = {} if seen is None else seen
    try:
        entries = list(os.scandir(watch_dir))
    except OSError as e:
        log.warning("Cannot read the folder %s: %s", watch_dir, e)
        return []
    found: list[Candidate] = []
    shas: set[str] = set()
    current: set[str] = set()
    for entry in entries:
        if not is_csv_name(entry.name):
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if now - st.st_mtime > max_age_days * 86400:
            continue
        key = os.path.normcase(entry.path)
        current.add(key)
        prev = seen.get(key)
        same = prev is not None and prev[0] == st.st_size and prev[1] == st.st_mtime_ns
        seen[key] = (st.st_size, st.st_mtime_ns, prev[2] if same else None)
        if prev is not None and prev[0] != st.st_size:
            continue  # grew or shrank since the last poll: still being written
        if now - st.st_mtime < quiet_seconds or st.st_size == 0 or st.st_size > max_bytes:
            continue
        sha = seen[key][2]
        if sha is None:
            try:
                sha = sha256_file(entry.path)
            except OSError:
                continue  # locked by another program; try again next poll
            seen[key] = (st.st_size, st.st_mtime_ns, sha)
        if sha in state or sha in shas:
            continue
        shas.add(sha)
        found.append(Candidate(Path(entry.path), sha, st.st_size, st.st_mtime))
    for key in list(seen):
        if key not in current:
            del seen[key]
    found.sort(key=lambda c: (c.mtime, c.path.name))
    return found


# ------------------------------------------------------------------ state file

def load_state(path: Path) -> dict[str, dict]:
    """{sha256: {"name", "uploaded_at"}}. A damaged file is set aside, not trusted."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("The state file %s could not be read (%s); starting a new one.", path, e)
        try:
            os.replace(path, path.with_name(path.name + ".bad"))
        except OSError:
            pass
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via a temp file in the same folder + rename, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_state(path: Path, state: Mapping[str, dict]) -> None:
    _atomic_write_bytes(path, json.dumps(state, indent=2, sort_keys=True).encode("utf-8"))


# ------------------------------------------------------------------ sftp

@dataclass
class SftpResult:
    ok: bool
    message: str = ""
    stderr: str = ""


@dataclass
class UploadResult:
    ok: bool
    remote_name: str = ""
    message: str = ""
    backoff: bool = True  # False when retrying sooner would not help a network problem


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def remote_name(original: str, sha256: str) -> str:
    """'EverBee Export (3).csv' -> 'EverBee-Export-3-1a2b3c4d.csv'.

    Only [A-Za-z0-9._-], so nothing needs escaping in the sftp batch file. The short
    sha keeps two different files with the same name apart. It goes at the end so a
    leading "<niche>__" or role word stays where the server's ingest looks for it.
    """
    stem = original[:-4] if original.lower().endswith(".csv") else original
    folded = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"-{2,}", "-", _UNSAFE.sub("-", folded)).strip(".-")[:80].rstrip(".-")
    return f"{stem or 'export'}-{sha256[:8]}.csv"


def sftp_argv(cfg: Mapping, batch: str | os.PathLike) -> list[str]:
    """The sftp command line from docs/SERVER.md section 7, plus a few safety options.

    IdentitiesOnly: use only this key (a loaded ssh-agent would otherwise offer others
    first and hit "Too many authentication failures"). ConnectTimeout and ServerAlive:
    ssh gives up by itself on a dead connection (about a minute) instead of hanging.
    """
    argv = [str(cfg["sftp"]), "-b", str(batch), "-i", str(cfg["key"]),
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=30",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]
    if int(cfg.get("port", 22)) != 22:
        argv += ["-P", str(int(cfg["port"]))]
    argv.append(f"{cfg['user']}@{cfg['host']}")
    return argv


def friendly_error(stderr: str, cfg: Mapping) -> str:
    """Turn OpenSSH's error text into one plain sentence with what to do next."""
    text = stderr or ""
    low = text.lower()
    host = cfg.get("host", "the server")
    if "permission denied" in low and "publickey" in low:
        return ("The server did not accept this computer's key. Run the server command that "
                "install_helper.ps1 printed (sudo botsy-setup --upload-pubkey ...), then try again.")
    if "remote host identification has changed" in low or "host key verification failed" in low:
        return (f"The server's identity changed (was it rebuilt?). If you expected that, run "
                f"ssh-keygen -R {host} in PowerShell and try again.")
    if "unprotected private key" in low or "bad permissions" in low:
        return (f"Windows lets other users read the key file {cfg.get('key')}. Run "
                "install_helper.ps1 again, or fix the file's permissions.")
    if "no such identity" in low or ("identity file" in low and "not accessible" in low):
        return f"The key file {cfg.get('key')} is missing. Run install_helper.ps1 again."
    if "could not resolve" in low or "name or service not known" in low:
        return f"The server name {host} could not be found. Check the address in helper.json."
    if any(s in low for s in ("timed out", "connection refused", "network is unreachable",
                               "no route to host", "connection reset")):
        return (f"Could not reach the server at {host}. Check that this computer is online, "
                "the address is right and the server is running.")
    if "connection closed" in low or "connection lost" in low:
        return ("The server closed the connection. If this keeps happening, run the server "
                "setup again: sudo botsy-setup")
    if "no such file" in low or "not found" in low:
        return "A folder on the server is missing. On the server, run the setup again: sudo botsy-setup"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else "sftp failed without saying why."


def _read_text(fh) -> str:
    fh.seek(0)
    return fh.read().decode("utf-8", errors="replace")


def run_sftp(cfg: Mapping, commands: list[str], cwd: str | os.PathLike, *,
             timeout: float | None = None) -> SftpResult:
    """Write `commands` to a batch file in `cwd` and run sftp there. Never raises.

    sftp's output goes to files in `cwd`, not to pipes. On Windows sftp.exe starts
    ssh.exe, which inherits the output handles; after a timeout Python kills sftp.exe
    and then waits for every holder of a pipe to close it, so a stuck ssh.exe would
    freeze the helper for good. A file has nothing to wait for.
    """
    cwd = Path(cwd)
    batch = cwd / "botsy.batch"
    # Bytes with "\n": the same file on every system, whatever Windows' text mode does.
    batch.write_bytes(("\n".join(commands) + "\n").encode("utf-8"))
    limit = int(timeout if timeout is not None else cfg["timeout_seconds"])
    kwargs: dict = {}
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows only
    if no_window:
        kwargs["creationflags"] = no_window  # pythonw has no console; do not flash one
    try:
        with open(cwd / "sftp-out.txt", "w+b") as out, open(cwd / "sftp-err.txt", "w+b") as err:
            try:
                proc = subprocess.run(sftp_argv(cfg, batch), cwd=str(cwd),
                                      stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                      timeout=limit, **kwargs)
            except FileNotFoundError:
                return SftpResult(False, f"The sftp program was not found at {cfg['sftp']}. "
                                         "Turn on Windows' OpenSSH Client (Settings > System "
                                         "> Optional features) and run install_helper.ps1 "
                                         "again.")
            except subprocess.TimeoutExpired:
                return SftpResult(False, f"The server did not answer within {limit} seconds.",
                                  _read_text(err))
            stdout, stderr = _read_text(out), _read_text(err)
    except OSError as e:
        return SftpResult(False, f"Could not start sftp: {e}")
    if proc.returncode == 0:
        return SftpResult(True, "", stderr)
    return SftpResult(False, friendly_error(stderr or stdout, cfg), stderr)


def upload(path: str | os.PathLike, cfg: Mapping, *, sha256: str | None = None) -> UploadResult:
    """Upload one export as incoming/<name>.part, then rename it to incoming/<name>.

    The file is first copied into a temporary folder and the copy is what gets checked
    and sent, so a file that changes halfway cannot slip through. `sha256` is the hash
    the caller saw; if the copy differs, nothing is sent and the caller tries later.
    Never raises: a problem on this PC comes back as a failed UploadResult.
    """
    src = Path(path)
    try:
        tmp = tempfile.mkdtemp(prefix="botsy-upload-")
    except OSError as e:
        return UploadResult(False, "", f"could not make a temporary folder: {e}")
    try:
        snapshot = Path(tmp) / "snapshot.csv"
        try:
            shutil.copyfile(src, snapshot)
            digest = sha256_file(snapshot)
            size = snapshot.stat().st_size
        except OSError as e:
            return UploadResult(False, "", f"could not read {src.name}: {e}", backoff=False)
        if sha256 is not None and digest != sha256:
            return UploadResult(False, "", f"{src.name} changed while it was being read",
                                backoff=False)
        if not looks_like_export(snapshot):
            return UploadResult(False, "", f"{src.name} does not look like a research export",
                                backoff=False)
        if size > int(cfg["max_upload_bytes"]):
            return UploadResult(False, "", f"{src.name} is larger than the server accepts",
                                backoff=False)
        name = remote_name(src.name, digest)
        try:
            snapshot.rename(Path(tmp) / name)
        except OSError as e:
            return UploadResult(False, "", f"could not prepare {src.name}: {e}")
        part = f"{cfg['remote_incoming']}/{name}.part"
        final = f"{cfg['remote_incoming']}/{name}"
        limit = max(int(cfg["timeout_seconds"]), 60 + size // MIN_UPLOAD_BYTES_PER_SECOND)
        res = run_sftp(cfg, [f'put "{name}" "{part}"', f'rename "{part}" "{final}"'], tmp,
                       timeout=limit)
        return UploadResult(res.ok, name, res.message)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def open_file(path: Path) -> bool:
    """Open `path` with its usual program (Notepad for .txt). Windows only."""
    starter = getattr(os, "startfile", None)
    if starter is None:
        log.info("Would open %s (only possible on Windows).", path)
        return False
    try:
        starter(str(path))
        return True
    except OSError as e:
        log.warning("Could not open %s: %s", path, e)
        return False


def _load_json_dict(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _same_bytes(path: Path, data: bytes) -> bool:
    try:
        return path.read_bytes() == data
    except OSError:
        return False


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def local_outbox_path(out_dir: str | os.PathLike, name: str) -> Path:
    """Where a fetched outbox file goes: Documents\\Botsy\\<name>, templates under templates\\."""
    out_dir = Path(out_dir)
    return out_dir / TEMPLATES_SUBDIR / name if name in TEMPLATE_FILES else out_dir / name


def fetch_outbox(cfg: Mapping) -> dict[str, str]:
    """Copy outbox/search-list.txt, status.txt, daily-log.txt and the blank templates
    into Documents\\Botsy (templates into its templates\\ subfolder).

    Returns {file name: "changed" | "same" | "kept" | "missing" | "error"}. "changed"
    means the server has content this PC has not fetched before: only then is the local
    copy replaced, and the search list opened. Fetches are compared with the last fetch
    (recorded in outbox_state_file), not with the local copy, so a list the user ticked
    off or wrote notes in is left alone and not opened again every hour. A local copy
    the user deleted is put back quietly. Only the search list is ever opened; the
    status file and the daily log are saved for the user to open when they like.
    A template has fixed content, so a local copy that is neither the server's copy nor
    the blank one from the last fetch is one the user typed in: it is never replaced
    ("kept"), whatever the server sends. A blank copy is updated when the server's
    template changes.
    """
    out_dir = Path(cfg["outbox_dir"])
    record_path = Path(cfg["outbox_state_file"])
    fetched = _load_json_dict(record_path)
    before = dict(fetched)
    results: dict[str, str] = {}
    tmp = tempfile.mkdtemp(prefix="botsy-outbox-")
    try:
        # "-get": a file the server has not written yet is not an error.
        cmds = [f'-get "{cfg["remote_outbox"]}/{name}" "{name}"' for name in OUTBOX_FILES]
        res = run_sftp(cfg, cmds, tmp)
        if not res.ok:
            log.warning("Could not fetch the search list, status and daily log: %s", res.message)
            return {name: "error" for name in OUTBOX_FILES}
        for name in OUTBOX_FILES:
            got = Path(tmp) / name
            dest = local_outbox_path(out_dir, name)
            try:
                if not got.is_file():
                    results[name] = "missing"
                    continue
                new = got.read_bytes()
                digest = hashlib.sha256(new).hexdigest()
                if name in TEMPLATE_FILES and dest.is_file() and not _same_bytes(dest, new) \
                        and _file_sha256(dest) != fetched.get(name):
                    # Not the server's new copy and not the blank one from the last fetch:
                    # the user typed in it, so it stays as it is. (A blank copy from the last
                    # fetch is replaced below when the server's template changes.)
                    results[name] = "kept"
                    fetched[name] = digest
                    continue
                seen_before = (fetched.get(name) == digest if name in fetched
                               else _same_bytes(dest, new))  # first run of this version
                if seen_before:
                    if not dest.exists():
                        _atomic_write_bytes(dest, new)
                    results[name] = "same"
                else:
                    _atomic_write_bytes(dest, new)
                    results[name] = "changed"
                fetched[name] = digest
            except OSError as e:
                log.warning("Could not save %s: %s", dest, e)
                results[name] = "error"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if fetched != before:
        try:
            _atomic_write_bytes(record_path, json.dumps(fetched, indent=2,
                                                        sort_keys=True).encode("utf-8"))
        except OSError as e:
            log.warning("Could not save %s: %s", record_path, e)
    if results.get(SEARCH_LIST) == "changed":
        log.info("New search list saved to %s", out_dir / SEARCH_LIST)
        open_file(out_dir / SEARCH_LIST)
    return results


def check_connection(cfg: Mapping) -> tuple[bool, str]:
    """Log in, look at the upload folder, change nothing. (ok, message for the user)."""
    if not os.path.isfile(cfg["key"]):
        return False, f"The key file {cfg['key']} is missing. Run install_helper.ps1 again."
    tmp = tempfile.mkdtemp(prefix="botsy-check-")
    try:
        res = run_sftp(cfg, [f'cd "{cfg["remote_incoming"]}"', "pwd"], tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if res.ok:
        return True, (f"OK: connected to {cfg['user']}@{cfg['host']} and found the upload "
                      "folder. The Botsy helper is ready.")
    return False, f"Not working yet: {res.message}"


# ------------------------------------------------------------------ the loop

def backoff_seconds(failures: int, base: float, cap: float = BACKOFF_CAP_SECONDS) -> float:
    """Wait after the n-th failure in a row: base, 2x, 4x ... never more than `cap`."""
    if failures <= 0:
        return 0.0
    return float(min(cap, base * 2 ** min(failures - 1, 30)))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Helper:
    """What the loop remembers between polls: the state file, sizes, failures, timers."""

    def __init__(self, cfg: Mapping, *, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.clock = clock
        self.state = load_state(Path(cfg["state_file"]))
        self.seen: dict = {}
        self.ignored: set[str] = set()  # sha256 of CSVs that are not exports (logged once)
        self.deferred: dict[str, str] = {}  # sha256 -> last "not yet" reason (logged once)
        self.failures = 0
        self.next_upload_at = 0.0
        self.next_fetch_at = 0.0

    def poll(self) -> dict[str, list[str]]:
        """One pass: upload what is new, fetch the outbox when due. Returns a summary."""
        cfg = self.cfg
        now = self.clock()
        summary: dict[str, list[str]] = {"uploaded": [], "ignored": [], "failed": [],
                                         "waiting": []}
        candidates = find_candidates(cfg["watch_dir"], self.state, cfg["max_age_days"],
                                     seen=self.seen, now=now,
                                     max_bytes=int(cfg["max_upload_bytes"]))
        for cand in candidates:
            if cand.sha256 in self.ignored:
                continue
            if not looks_like_export(cand.path):
                self.ignored.add(cand.sha256)
                summary["ignored"].append(cand.path.name)
                log.info("Not uploading %s: it does not look like a research export.",
                         cand.path.name)
                continue
            if now < self.next_upload_at:
                summary["waiting"].append(cand.path.name)
                continue
            res = upload(cand.path, cfg, sha256=cand.sha256)
            if res.ok:
                self.deferred.pop(cand.sha256, None)
                self.state[cand.sha256] = {"name": cand.path.name, "uploaded_at": _now_iso()}
                save_state(Path(cfg["state_file"]), self.state)
                self.failures, self.next_upload_at = 0, 0.0
                self.next_fetch_at = min(self.next_fetch_at,
                                         now + cfg["fetch_after_upload_seconds"])
                summary["uploaded"].append(cand.path.name)
                log.info("Uploaded %s as %s", cand.path.name, res.remote_name)
            elif res.backoff:
                self.failures += 1
                wait = backoff_seconds(self.failures, cfg["poll_seconds"])
                self.next_upload_at = now + wait
                summary["failed"].append(cand.path.name)
                log.warning("Upload of %s failed: %s Trying again in %d seconds.",
                            cand.path.name, res.message, wait)
            else:
                summary["failed"].append(cand.path.name)
                if self.deferred.get(cand.sha256) != res.message:  # a locked file: once
                    self.deferred[cand.sha256] = res.message
                    log.info("Not uploading %s yet: %s", cand.path.name, res.message)
        if now >= self.next_fetch_at:
            # Set first, so a fetch that fails, or crashes, is retried in 10 minutes and
            # not at every poll.
            self.next_fetch_at = now + min(cfg["outbox_every_seconds"], BACKOFF_CAP_SECONDS)
            results = fetch_outbox(cfg)
            if not all(v == "error" for v in results.values()):
                self.next_fetch_at = now + cfg["outbox_every_seconds"]
        return summary


# ------------------------------------------------------------------ single instance

class SingleInstance:
    """A lock file held for as long as the helper runs, so two copies never race.

    The operating system drops the lock when the process ends, even after a crash, so
    a stale lock file never blocks the next logon.
    """

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(str(os.getpid()))
            fh.flush()
        except OSError:
            pass
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


# ------------------------------------------------------------------ entry point

class QuietRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A rotating log that never prints a traceback about itself.

    Windows will not rename a log another copy has open (the background helper while you
    run --check), so a rotation can fail. The next line tries again; nothing to show.
    """

    def handleError(self, record: logging.LogRecord) -> None:
        pass


def setup_logging(log_file: Path, verbose: bool = False) -> None:
    """Rotating log (about 1 MB, 3 old copies) plus the console when there is one."""
    log.handlers.clear()
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = QuietRotatingFileHandler(
            log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
        handler.setFormatter(fmt)
        log.addHandler(handler)
    except OSError:
        pass
    if sys.stderr is not None:  # pythonw has no console at all
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        log.addHandler(console)


def close_logging() -> None:
    for handler in list(log.handlers):
        handler.close()
        log.removeHandler(handler)


def _say(message: str) -> None:
    if sys.stdout is not None:  # pythonw: no console, print() would have nowhere to go
        try:
            print(message, flush=True)
        except (OSError, ValueError):
            pass


def _log_config_problem(message: str) -> None:
    """Put a settings problem in the usual log, which is the only place pythonw can.

    Only when %APPDATA%\\Botsy already exists (the installer made it), so a mistyped
    --config never creates folders.
    """
    log_file = expand_path(DEFAULTS["log_file"])
    if not log_file.parent.is_dir():
        return
    setup_logging(log_file)
    try:
        log.error("The Botsy helper cannot start: %s", message)
    finally:
        close_logging()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="botsy_helper",
                                description="Uploads research exports to the Botsy server.")
    p.add_argument("--config", help=f"settings file (default {CONFIG_PATH})")
    p.add_argument("--once", action="store_true", help="do one pass and exit (for testing)")
    p.add_argument("--check", action="store_true",
                   help="test the connection to the server and print the result")
    p.add_argument("--verbose", action="store_true", help="log more detail")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        _say(str(e))
        _log_config_problem(str(e))
        return 2
    setup_logging(cfg["log_file"], args.verbose)
    try:
        if args.check:
            ok, message = check_connection(cfg)
            log.info("Connection check: %s", message)
            _say(message)
            return 0 if ok else 1
        lock = SingleInstance(cfg["lock_file"])
        try:
            got_lock = lock.acquire()
        except OSError as e:
            log.error("Cannot use the lock file %s: %s", cfg["lock_file"], e)
            _say(f"Cannot use the lock file {cfg['lock_file']}: {e}")
            return 1
        if not got_lock:
            log.info("Another copy of the Botsy helper is already running; this one stops.")
            _say("The Botsy helper is already running.")
            return 1 if args.once else 0
        try:
            helper = Helper(cfg)
            log.info("Botsy helper started: watching %s, sending to %s@%s",
                     cfg["watch_dir"], cfg["user"], cfg["host"])
            while True:
                try:
                    summary = helper.poll()
                    if args.once:
                        _say(json.dumps(summary))
                except Exception:  # keep running; the next poll may work
                    log.exception("Something went wrong during a check of the folder.")
                if args.once:
                    break
                time.sleep(cfg["poll_seconds"])
        except KeyboardInterrupt:
            pass
        finally:
            lock.release()
        return 0
    finally:
        close_logging()


if __name__ == "__main__":
    sys.exit(main())
