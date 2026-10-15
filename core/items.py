"""Item directories, ids and JSON I/O shared by generators, QA, listing and publisher.

An "item" is one sellable product: output/<item_id>/ holding manifest.json and,
as stages run, qa_report.json, listing.json and publish.json. See docs/CONTRACTS.md.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from core.compliance import normalize
from core.config import load_settings, resolve

STATUSES = ("generated", "qa_failed", "qa_passed", "listed", "drafted")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Files later stages write into an item dir. A regenerated item must not keep them.
DOWNSTREAM_FILES = ("qa_report.json", "listing.json")
DOWNSTREAM_DIRS = ("mockups",)


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(text)).strip("-")
    return slug[:max_len].rstrip("-") or "item"


def check_date(date: str) -> str:
    """Validate an ISO date (YYYY-MM-DD). Dates end up in folder and file names."""
    if not isinstance(date, str) or not DATE_RE.match(date):
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
    _dt.date.fromisoformat(date)
    return date


def new_item_id(date: str, niche: str, theme: str, salt: str = "") -> str:
    """Deterministic id: <date>-<niche>-<theme-slug>-<4 hex>. `salt` separates variants."""
    check_date(date)
    if not re.fullmatch(r"[a-z][a-z0-9_]*", niche):
        raise ValueError(f"bad niche {niche!r}")
    digest = hashlib.sha1(f"{date}|{niche}|{theme}|{salt}".encode()).hexdigest()[:4]
    return f"{date}-{niche}-{slugify(theme)}-{digest}"


def output_root(settings: dict | None = None) -> Path:
    settings = settings or load_settings()
    return resolve(settings["paths"]["output_dir"])


def review_root(settings: dict | None = None) -> Path:
    settings = settings or load_settings()
    return resolve(settings["paths"]["review_dir"])


def review_dir_for(d: str | Path, settings: dict | None = None) -> Path:
    """Review queue folder for an item: settings paths.review_dir for items in the
    configured output dir, else <item parent>/review (tmp dirs, dry runs)."""
    settings = settings or load_settings()
    d = Path(d).resolve()
    if d.parent == output_root(settings).resolve():
        return review_root(settings)
    return d.parent / "review"


def repo_relative(path: str | Path) -> str:
    """POSIX path relative to the repo root when inside it, else the absolute path."""
    p = Path(path).resolve()
    try:
        return p.relative_to(resolve(".").resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def clear_downstream(d: str | Path) -> None:
    """Remove QA/listing outputs after an item is regenerated, so stale reports and
    mockups never describe new files. Refuses once an Etsy draft exists."""
    import shutil

    d = Path(d)
    if (d / "publish.json").exists():
        raise RuntimeError(f"{d.name} already has an Etsy draft (publish.json); not touching it")
    for name in DOWNSTREAM_FILES:
        (d / name).unlink(missing_ok=True)
    for name in DOWNSTREAM_DIRS:
        shutil.rmtree(d / name, ignore_errors=True)


def item_dir(item_id: str, root: str | Path | None = None) -> Path:
    return (Path(root) if root else output_root()) / item_id


def iter_item_dirs(root: str | Path | None = None):
    """Every output/<item_id>/ that has a manifest.json, sorted by id."""
    base = Path(root) if root else output_root()
    if not base.exists():
        return
    for d in sorted(base.iterdir()):
        if d.is_dir() and (d / "manifest.json").exists():
            yield d


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, data) -> Path:
    """Atomic JSON write (temp file + rename), UTF-8, indented."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The ".tmp" suffix keeps half-written files out of glob("*.json").
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


def read_manifest(d: str | Path) -> dict:
    return read_json(Path(d) / "manifest.json")


def write_manifest(d: str | Path, manifest: dict) -> Path:
    return write_json(Path(d) / "manifest.json", manifest)


def set_status(d: str | Path, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    m = read_manifest(d)
    m["status"] = status
    write_manifest(d, m)
    return m
