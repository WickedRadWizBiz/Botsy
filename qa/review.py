"""Human review queue: output/review/<item_id>.json (docs/CONTRACTS.md section 3).

QA writes a `pending` entry for every item that passes. Items with needs_review must be
`approved` here before the publisher drafts them. A decision (approved / rejected) is the
user's, so a QA rerun keeps it, with two exceptions. An approval covers only the reasons the
person saw, so when a rerun finds a new reason the entry goes back to `pending` (the same
rule listing/review.py applies to new listing review terms). It also covers only the content
the person saw: the entry keeps the QA report's `content_digest` (files, previews, assets and
listing photos), and when a regenerated item has a different one, or the approval records
none, the entry goes back to `pending` too. A rejection always stays.

Other stages add reasons to the entry too (the listing stage's review terms). QA records
which reasons are its own in `qa_reasons` and replaces only those on a rerun.

Every read-modify-write of an entry runs under queue_lock(review_dir) (an flock on
<review_dir>/.queue.lock, re-entrant inside one process), so a decision made through an
order (service/orders.py, which holds only data/orders/.lock) and a tick rewriting the entry
(which holds data/.service.lock) never overwrite each other. decide(expected=...) is a
compare-and-set: it raises StateChanged when the fresh entry no longer matches.
"""
from __future__ import annotations

import fcntl
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from core.config import today
from core.items import read_json, review_entry_path, write_json

log = logging.getLogger("qa.review")

DECISIONS = ("approved", "rejected")
STATUSES = ("pending", *DECISIONS)
# Path of an item's entry. Ids come from the CLI too; anything that could leave the folder is refused.
entry_path = review_entry_path
LOCK_NAME = ".queue.lock"

_held: dict[str, list] = {}          # lock path -> [fd, depth] (re-entrant within this process)
_held_guard = threading.Lock()


class StateChanged(Exception):
    """decide(expected=...): the entry changed since the caller looked at it."""


@contextmanager
def queue_lock(review_dir: str | Path):
    """Hold the review queue's lock (blocking). Re-entrant in one process; lock order for
    callers that also hold other locks: data/orders/.lock, then this one."""
    d = Path(review_dir)
    d.mkdir(parents=True, exist_ok=True)
    key = str((d / LOCK_NAME).resolve())
    with _held_guard:
        slot = _held.get(key)
        if slot is not None:
            slot[1] += 1
    if slot is None:
        fd = os.open(key, os.O_RDWR | os.O_CREAT, 0o640)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except BaseException:
            os.close(fd)
            raise
        with _held_guard:
            _held[key] = [fd, 1]
    try:
        yield
    finally:
        with _held_guard:
            slot = _held[key]
            slot[1] -= 1
            done = slot[1] == 0
            if done:
                del _held[key]
        if done:
            try:
                fcntl.flock(slot[0], fcntl.LOCK_UN)
            finally:
                os.close(slot[0])


def read_entry(item_id: str, review_dir: str | Path) -> dict | None:
    """The entry, or None when there is none. Raises ValueError for an unreadable entry:
    it may hold a decision, so callers must not silently replace it."""
    p = entry_path(item_id, review_dir)
    if not p.exists():
        return None
    entry = read_json(p)
    if not isinstance(entry, dict):
        raise ValueError(f"review entry {p} is not a JSON object")
    return entry


def submit(report: dict, manifest: dict, review_dir: str | Path) -> dict:
    """Queue a passing item as pending. Keeps a rejection, and an approval unless QA now
    reports a reason or content (report `content_digest`) the approval did not cover.
    Keeps reasons other stages added."""
    with queue_lock(review_dir):
        return _submit(report, manifest, review_dir)


def _submit(report: dict, manifest: dict, review_dir: str | Path) -> dict:
    item_id = report["item_id"]
    old = read_entry(item_id, review_dir)
    qa_reasons = [str(r) for r in report.get("reasons", [])]
    digest = report.get("content_digest")
    if old and old.get("status") == "rejected":
        return old
    old_reasons = [str(r) for r in old.get("reasons") or []] if old else []
    own = set(old.get("qa_reasons") or []) if old else set()
    others = [r for r in old_reasons if r not in own and r not in qa_reasons]
    if old and old.get("status") == "approved":
        new = [r for r in qa_reasons if r not in old_reasons]
        changed = not digest or old.get("content_digest") != digest  # no digest: cannot tell, so ask
        if not new and not changed:
            return old
        if new:
            log.warning("%s: QA reports reasons the approval did not cover; back to pending: %s", item_id, new)
        if changed:
            log.warning("%s: files or listing photos differ from the ones approved; back to pending", item_id)
    entry = {
        **(old or {}),  # keep fields other stages added (listing: previous_status, reopened)
        "item_id": item_id,
        "niche": manifest.get("niche"),
        "status": "pending",
        "needs_review": bool(report.get("needs_review")) or bool(others),
        "reasons": qa_reasons + others,
        "qa_reasons": qa_reasons,
        "mockups": list(report.get("mockups", [])),
        "content_digest": digest,
        "queued": report.get("checked") or today(),
    }
    if old and old.get("status") == "approved":
        entry.update({"previous_status": "approved", "reopened": today()})
    write_json(entry_path(item_id, review_dir), entry)
    return entry


def withdraw(item_id: str, review_dir: str | Path) -> bool:
    """Drop a stale pending entry when an item no longer passes QA. Decisions stay."""
    if not entry_path(item_id, review_dir).exists():
        return False
    with queue_lock(review_dir):
        old = read_entry(item_id, review_dir)
        if old and old.get("status") == "pending":
            entry_path(item_id, review_dir).unlink()
            return True
        return False


def decide(item_id: str, status: str, review_dir: str | Path, note: str = "", *,
           expected: dict | None = None) -> dict:
    """Record the user's approve / reject decision on an existing entry. With `expected`
    (e.g. {"status": "pending", "content_digest": <digest the approval was checked against>}), every key must
    still match the entry read fresh under the queue lock, or StateChanged is raised and
    nothing is written."""
    if status not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, not {status!r}")
    if status == "rejected" and not note.strip():
        raise ValueError("a rejection needs a reason")
    with queue_lock(review_dir):
        entry = read_entry(item_id, review_dir)
        if entry is None:
            raise FileNotFoundError(f"no review entry for {item_id} in {review_dir} (run QA first)")
        for key, want in (expected or {}).items():
            if entry.get(key) != want:
                raise StateChanged(f"{item_id}: {key} changed")
        entry.update({"status": status, "decided": today(), "decision_note": note.strip()})
        write_json(entry_path(item_id, review_dir), entry)
        return entry


def approve(item_id: str, review_dir: str | Path, note: str = "", *, expected: dict | None = None) -> dict:
    return decide(item_id, "approved", review_dir, note, expected=expected)


def reject(item_id: str, review_dir: str | Path, reason: str, *, expected: dict | None = None) -> dict:
    return decide(item_id, "rejected", review_dir, reason, expected=expected)


def list_entries(review_dir: str | Path, status: str | None = None) -> list[dict]:
    d = Path(review_dir)
    if not d.is_dir():
        return []
    entries = []
    for p in sorted(d.glob("*.json")):
        if p.name.startswith("."):  # write_json's temp files
            continue
        try:
            e = read_json(p)
        except (OSError, ValueError):
            log.warning("skipping unreadable review entry %s", p)
            continue
        if isinstance(e, dict) and (status is None or e.get("status") == status):
            entries.append(e)
    return entries
