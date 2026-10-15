"""Put listings with review terms in front of a person (docs/CONTRACTS.md section 3).

QA owns the review queue (output/review/<item_id>.json). The listing stage only adds to
it: when the listing copy has a `?term` from the blocklist, the entry gets the reason and
needs_review, so the publisher waits for an approval. An earlier approval did not cover
this copy, so it goes back to pending; a rejection stays a rejection.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from core.config import load_settings, resolve, today
from core.items import read_json, write_json

log = logging.getLogger("listing.review")

ITEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def default_dir(item_dir: str | Path, settings: dict | None = None) -> Path:
    """Same rule as QA: settings paths.review_dir for items in the configured output dir,
    else a `review/` folder next to the item (tests and dry runs never touch output/)."""
    settings = settings or load_settings()
    item_dir = Path(item_dir).resolve()
    if item_dir.parent == resolve(settings["paths"]["output_dir"]).resolve():
        return resolve(settings["paths"]["review_dir"])
    return item_dir.parent / "review"


def entry_path(item_id: str, review_dir: str | Path) -> Path:
    if not ITEM_ID_RE.match(item_id or "") or ".." in item_id:
        raise ValueError(f"invalid item id {item_id!r}")
    return Path(review_dir) / f"{item_id}.json"


def read_status(item_id: str, review_dir: str | Path) -> str | None:
    p = entry_path(item_id, review_dir)
    return read_json(p).get("status") if p.exists() else None


def flag(item_id: str, niche: str | None, reasons: list[str], mockups: list[str], review_dir: str | Path) -> dict:
    """Add listing review reasons to the item's queue entry (creating it if needed)."""
    p = entry_path(item_id, review_dir)
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
