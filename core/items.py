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
from core.config import DATE_RE, check_date, load_settings, resolve, today  # noqa: F401 (re-exported)

STATUSES = ("generated", "qa_failed", "qa_passed", "listed", "drafted")
# Where dry runs keep their items unless --out says otherwise (settings pipeline.dry_run_output_dir).
DRY_RUN_OUTPUT_DIR = "output/dry-run"
# Item ids name folders and review-queue files, so nothing that could leave a folder.
ITEM_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Files later stages write into an item dir. A regenerated item must not keep them.
DOWNSTREAM_FILES = ("qa_report.json", "listing.json")
DOWNSTREAM_DIRS = ("mockups",)


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(text)).strip("-")
    return slug[:max_len].rstrip("-") or "item"


def valid_item_id(item_id) -> bool:
    return isinstance(item_id, str) and bool(ITEM_ID_RE.fullmatch(item_id)) and ".." not in item_id


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


def dry_run_root(settings: dict | None = None) -> Path:
    """settings pipeline.dry_run_output_dir (default output/dry-run): where dry runs write."""
    settings = settings or load_settings()
    return resolve((settings.get("pipeline") or {}).get("dry_run_output_dir") or DRY_RUN_OUTPUT_DIR)


def stage_out_root(out: str | Path | None, settings: dict | None = None, *, dry_run: bool = False) -> Path:
    """Output root for a stage CLI or pipeline run: `out` when given, else the dry-run
    folder for a dry run (so dry records never land in output/), else paths.output_dir."""
    if out:
        return Path(out)
    return dry_run_root(settings) if dry_run else output_root(settings)


def review_root(settings: dict | None = None) -> Path:
    settings = settings or load_settings()
    return resolve(settings["paths"]["review_dir"])


def review_dir_for_root(out_root: str | Path, settings: dict | None = None) -> Path:
    """Review queue folder for the items under `out_root`: settings paths.review_dir for
    the configured output dir, else <out_root>/review (tmp dirs, dry runs)."""
    settings = settings or load_settings()
    if Path(out_root).resolve() == output_root(settings).resolve():
        return review_root(settings)
    return Path(out_root).resolve() / "review"


def review_dir_for(d: str | Path, settings: dict | None = None) -> Path:
    """Review queue folder for one item (see review_dir_for_root)."""
    return review_dir_for_root(Path(d).resolve().parent, settings)


def review_entry_path(item_id: str, review_dir: str | Path) -> Path:
    """<review_dir>/<item_id>.json. Refuses ids that could escape the folder."""
    if not valid_item_id(item_id):
        raise ValueError(f"invalid item id {item_id!r}")
    return Path(review_dir) / f"{item_id}.json"


def repo_relative(path: str | Path) -> str:
    """POSIX path relative to the repo root when inside it, else the absolute path."""
    p = Path(path).resolve()
    try:
        return p.relative_to(resolve(".").resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def clear_downstream(d: str | Path, settings: dict | None = None, review_dir: str | Path | None = None) -> None:
    """Remove what later stages made from an item's old files after it is regenerated, so
    stale reports, mockups and listings never describe new files: qa_report.json,
    listing.json, mockups/ and a dry-run publish.json (a fake draft of the old files).

    The item's review-queue entry (review_dir, default review_dir_for(d, settings)) loses
    an approval: it covered the old art, so it goes back to `pending` (previous_status /
    reopened, like a QA reopen). A rejection stays. Refuses (RuntimeError) once a real
    Etsy draft exists; an unreadable publish.json counts as real."""
    import shutil

    d = Path(d)
    record = d / "publish.json"
    if record.exists():
        try:
            dry = read_json(record).get("dry_run") is True
        except (OSError, ValueError, AttributeError):
            dry = False
        if not dry:
            raise RuntimeError(f"{d.name} already has an Etsy draft (publish.json); not touching it")
        record.unlink()
    for name in DOWNSTREAM_FILES:
        (d / name).unlink(missing_ok=True)
    for name in DOWNSTREAM_DIRS:
        shutil.rmtree(d / name, ignore_errors=True)
    _reopen_review_entry(d, settings, review_dir)


def _reopen_review_entry(d: Path, settings: dict | None, review_dir: str | Path | None) -> None:
    if not valid_item_id(d.name):
        return
    path = review_entry_path(d.name, review_dir if review_dir else review_dir_for(d, settings))
    if not path.exists():
        return
    entry = read_json(path)  # unreadable: raise, it may hold a decision nobody should lose
    if not isinstance(entry, dict) or entry.get("status") != "approved":
        return
    entry.update({"status": "pending", "previous_status": "approved", "reopened": today(),
                  "content_digest": None})
    write_json(path, entry)


def content_count(manifest: dict, fallback: int | None = None) -> int | None:
    """How many designs the buyer gets (pages or stickers; no title or answer-key pages),
    for listing copy and the listing photos. `fallback` is used only when the manifest
    does not say.

    Order: pack activity_pages / pages / stickers (what the generators write); for
    stickers without pack.stickers, the transparent PNG assets (one per sticker), else
    the ZIP counts summed over every part (a big pack is split), else transparent PNG
    files; for other niches, the first PDF's page count."""
    pack = manifest.get("pack") if isinstance(manifest.get("pack"), dict) else {}
    for key in ("activity_pages", "pages", "stickers"):
        if _positive_int(pack.get(key)):
            return pack[key]

    def entries(group: str, kind: str) -> list[dict]:
        return [e for e in manifest.get(group) or [] if isinstance(e, dict) and e.get("kind") == kind]

    if manifest.get("niche") == "stickers":
        n = (len(entries("assets", "png_transparent"))
             or sum(_positive_int(e.get("count")) for e in entries("files", "zip"))
             or len(entries("files", "png_transparent")))
        return n or _positive_int(fallback) or None
    for e in entries("files", "pdf"):
        if _positive_int(e.get("pages")):
            return e["pages"]
    return _positive_int(fallback) or None


def _positive_int(value) -> int:
    """A positive count from our JSON, else 0 (never a bool or a negative number)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


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
