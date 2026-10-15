"""Item directories, ids and JSON I/O shared by generators, QA, listing and publisher.

An "item" is one sellable product: output/<item_id>/ holding manifest.json and,
as stages run, qa_report.json, listing.json and publish.json. See docs/CONTRACTS.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from core.compliance import normalize
from core.config import load_settings, resolve

STATUSES = ("generated", "qa_failed", "qa_passed", "listed", "drafted")


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(text)).strip("-")
    return slug[:max_len].rstrip("-") or "item"


def new_item_id(date: str, niche: str, theme: str, salt: str = "") -> str:
    """Deterministic id: <date>-<niche>-<theme-slug>-<4 hex>. `salt` separates variants."""
    digest = hashlib.sha1(f"{date}|{niche}|{theme}|{salt}".encode()).hexdigest()[:4]
    return f"{date}-{niche}-{slugify(theme)}-{digest}"


def output_root(settings: dict | None = None) -> Path:
    settings = settings or load_settings()
    return resolve(settings["paths"]["output_dir"])


def review_root(settings: dict | None = None) -> Path:
    settings = settings or load_settings()
    return resolve(settings["paths"]["review_dir"])


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
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
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
