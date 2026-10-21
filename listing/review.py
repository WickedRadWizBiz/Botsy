"""Put listings with review terms in front of a person (docs/CONTRACTS.md section 3).

QA owns the review queue (output/review/<item_id>.json). The listing stage only adds to
it: when the listing copy has a `?term` from the blocklist, the entry gets the reason and
needs_review, so the publisher waits for an approval. An earlier approval did not cover
this copy, so it goes back to pending; a rejection stays a rejection.
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.config import today
from core.items import read_json, review_entry_path, write_json

log = logging.getLogger("listing.review")

# The queue folder for an item is core.items.review_dir_for (the same rule QA and the
# publisher use); entry paths come from core.items.review_entry_path.


def read_status(item_id: str, review_dir: str | Path) -> str | None:
    p = review_entry_path(item_id, review_dir)
    return read_json(p).get("status") if p.exists() else None


def flag(item_id: str, niche: str | None, reasons: list[str], mockups: list[str], review_dir: str | Path) -> dict:
    """Add listing review reasons to the item's queue entry (creating it if needed)."""
    from qa.review import queue_lock

    with queue_lock(review_dir):
        return _flag(item_id, niche, reasons, mockups, review_dir)


def _flag(item_id: str, niche: str | None, reasons: list[str], mockups: list[str], review_dir: str | Path) -> dict:
    p = review_entry_path(item_id, review_dir)
    entry = read_json(p) if p.exists() else {
        "item_id": item_id, "niche": niche, "status": "pending", "needs_review": True,
        "reasons": [], "mockups": list(mockups), "queued": today(),
    }
    new = [r for r in reasons if r not in entry.get("reasons", [])]
    if not new and entry.get("needs_review"):
        return entry
    entry["reasons"] = [*entry.get("reasons", []), *new]
    entry["needs_review"] = True
    if entry.get("status") == "approved" and new:
        log.warning("%s: listing copy has new review terms; approval reset to pending", item_id)
        entry.update({"status": "pending", "previous_status": "approved", "reopened": today()})
    elif entry.get("status") == "rejected":
        log.warning("%s is rejected in the review queue; listing written but it will not be drafted", item_id)
    write_json(p, entry)
    return entry
