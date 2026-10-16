"""Server folders and switches: settings `service:` over DEFAULTS (docs/SERVER.md).

Every path the service touches is worked out here, so the server layout lives in one
place and a test can point a whole tick at tmp_path with one settings file (or --inbox).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.config import resolve, section

# Read with core.config.section(settings, "service", DEFAULTS); settings win.
DEFAULTS: dict = {
    # SFTP chroot of the upload-only account botsy-upload (root-owned, mode 755, as OpenSSH requires).
    "inbox_dir": "/srv/botsy-inbox",
    "incoming_subdir": "incoming",      # the Windows helper uploads <name>.part, then renames it
    "outbox_subdir": "outbox",          # status.txt, status.json, search-list.txt (the helper reads them)
    "rejected_dir": "data/inbox_rejected",
    "lock_file": "data/.service.lock",
    "live": False,                      # false = dry runs: no API calls and no Etsy drafts
    "max_upload_bytes": 25_000_000,
    "part_max_age_h": 48,               # an unfinished <name>.part this old was given up on: deleted
    "lock_timeout_s": 1800,             # a second service command waits this long, then exits with code 3
    # A search-list session is many exports in a row. After an upload, wait until the inbox
    # has been quiet this long, so research sees the whole batch instead of one file at a time.
    "settle_s": 300,
    "settle_max_s": 3600,               # ... but never wait longer than this
    "max_rounds": 3,                    # research + run again for uploads that land during a run
    "status_keep_days": 35,             # tick records kept in outbox/status.json
}
# settings research.history_dir (docs/SERVER.md section 3); research/history.py has the same default.
HISTORY_DIR_DEFAULT = "data/history"
RESEARCH_PATH_DEFAULTS = {"inputs_dir": "data/research_inputs", "briefs_dir": "data/briefs",
                          "history_dir": HISTORY_DIR_DEFAULT}


@dataclass(frozen=True)
class ServicePaths:
    inbox: Path        # the chroot
    incoming: Path     # uploads arrive here
    outbox: Path       # the server's notes for the user
    inputs: Path       # research.inputs_dir: accepted exports are stored here
    rejected: Path     # rejected uploads and their reason files
    history: Path      # research history snapshots
    briefs: Path       # research.briefs_dir
    lock: Path         # one service command at a time


def load_config(settings: dict) -> dict:
    return section(settings, "service", DEFAULTS)


def is_live(settings: dict) -> bool:
    """Only a real YAML `true` turns live mode on. Anything else ("false" as text, 1,
    null) stays a dry run, because live mode spends money and creates Etsy drafts."""
    return load_config(settings).get("live") is True


def service_paths(settings: dict, inbox: str | Path | None = None) -> ServicePaths:
    """All service folders. `inbox` overrides service.inbox_dir (tests, the --inbox flag)."""
    cfg = load_config(settings)
    research = section(settings, "research", RESEARCH_PATH_DEFAULTS)
    root = Path(inbox) if inbox else resolve(cfg["inbox_dir"])
    return ServicePaths(
        inbox=root,
        incoming=root / cfg["incoming_subdir"],
        outbox=root / cfg["outbox_subdir"],
        inputs=resolve(research.get("inputs_dir") or RESEARCH_PATH_DEFAULTS["inputs_dir"]),
        rejected=resolve(cfg["rejected_dir"]),
        history=resolve(research.get("history_dir") or HISTORY_DIR_DEFAULT),
        briefs=resolve(research.get("briefs_dir") or RESEARCH_PATH_DEFAULTS["briefs_dir"]),
        lock=resolve(cfg["lock_file"]),
    )
