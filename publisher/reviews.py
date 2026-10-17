"""Our own listings' star ratings -> `<performance_dir>/reviews/<YYYY-MM-DD>.json` (reviews/v1).

Source: getReviewsByShop on ETSY_SHOP_ID. The endpoint answers for any shop with just the
app key, so the safety layer pins it to our own shop id (publisher/safety.py). Kept per
review: listing_id, rating and the day (Botsy's local day of created_timestamp). Never
stored: the review text, language, photo links, buyer or reviewer details. Reviews are
few, so every one is kept (at most 50 pages of 100). A 403 (an app key without access)
is skipped with one log line, not an error.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from core.config import tz
from core.items import write_json
from publisher.client import EtsyAPIError
from publisher.own_stats import local_day
from publisher.sales import _count

log = logging.getLogger("publisher.reviews")

SCHEMA = "reviews/v1"
PAGE_SIZE = 100
KEPT_FIELDS = ("listing_id", "rating", "day")
_warned: set[str] = set()


def reset_warnings() -> None:  # tests
    _warned.clear()


def reduce_review(rv: dict, settings: dict | None = None) -> dict | None:
    lid = str(rv.get("listing_id", "")).strip()
    ts = rv.get("created_timestamp", rv.get("create_timestamp"))
    try:
        ts = float(ts)
        rating = int(rv.get("rating"))
    except (TypeError, ValueError):
        return None
    if not lid.isdigit() or int(lid) <= 0:
        return None
    day = datetime.fromtimestamp(ts, timezone.utc).astimezone(tz(settings)).date().isoformat()
    return {"listing_id": int(lid), "rating": rating, "day": day}


def fetch_reviews(client, *, max_pages: int = 50, settings: dict | None = None,
                  now: float | None = None) -> dict | None:
    reviews: list[dict] = []
    count = None
    for page in range(int(max_pages)):
        try:
            resp = client.get_reviews_by_shop(limit=PAGE_SIZE, offset=page * PAGE_SIZE) or {}
        except EtsyAPIError as e:
            if e.status == 403:
                if "403" not in _warned:
                    _warned.add("403")
                    log.warning("reviews: Etsy refused getReviewsByShop (%s); reviews are skipped.", e.detail)
                return None
            raise
        results = resp.get("results") or []
        if count is None:
            count = _count(resp)
        reviews += [r for r in (reduce_review(x, settings) for x in results) if r]
        if not results or len(results) < PAGE_SIZE or (count is not None and (page + 1) * PAGE_SIZE >= count):
            break
    reviews.sort(key=lambda r: (r["day"], r["listing_id"]))
    return {"schema": SCHEMA, "day": local_day(settings, now), "etsy_shop_id": client.shop_id,
            "count": len(reviews), "reviews": reviews}


def reviews_path(performance_dir: str | Path, day: str) -> Path:
    return Path(performance_dir) / "reviews" / f"{day}.json"


def write_reviews(client, performance_dir: str | Path, *, max_pages: int = 50, settings: dict | None = None,
                  now: float | None = None) -> tuple[Path | None, dict | None]:
    doc = fetch_reviews(client, max_pages=max_pages, settings=settings, now=now)
    if doc is None:
        return None, None
    return write_json(reviews_path(performance_dir, doc["day"]), doc), doc
