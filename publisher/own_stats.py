"""Our own shop's listing stats -> data/research_inputs/own_shop_stats.csv for research.

Only our own active listings (getListingsByShop on ETSY_SHOP_ID, which the client
enforces). The CSV holds titles, tags and numbers only: no URLs, listing ids or shop
names, so nothing identifying can leak into briefs. Column names match the research
stage's aliases (title, views, favorites, price, tags, age_days).

`views` is tabulated by Etsy once a day and only for active listings (spec note on
ShopListingWithAssociations.views); `num_favorers` is written as `favorites`. Titles and
tags are HTML-unescaped, since Etsy returns some text with entities such as &#39;.

Daily snapshot (docs/OVERSEER.md "Scale-ups", docs/CONTRACTS.md section 7): the CSV is
overwritten every day, so `write_snapshot` also keeps a per-listing record with ids in
`<performance_dir>/etsy/<YYYY-MM-DD>.json` (schema performance/v1, gitignored): our
active and draft listings with listing_id, state, title, tags, views, num_favorers,
created day and price, plus a drafts count for the waiting-drafts check. Etsy's numbers
are running totals; "views in the last 30 days" is today's count minus the count 30 days
ago. The day is Botsy's local day (schedule.timezone, America/New_York); writing the
same day twice replaces the file, so a rerun is harmless.
"""
from __future__ import annotations

import csv
import html
import io
import time
from datetime import datetime, timezone
from pathlib import Path

from core.config import now_local, tz
from core.items import write_json

COLUMNS = ["title", "views", "favorites", "price", "currency", "quantity", "tags", "state", "age_days"]
PAGE_SIZE = 100  # spec maximum for getListingsByShop `limit`
SNAPSHOT_SCHEMA = "performance/v1"


def fetch_own_listings(client, max_pages: int = 100, state: str | None = None) -> list[dict]:
    """Every listing of our shop in `state` (None: Etsy's default, active), paging with
    limit/offset. The guard allows only "draft" as an explicit state."""
    out: list[dict] = []
    for page in range(max_pages):
        resp = client.get_listings_by_shop(limit=PAGE_SIZE, offset=page * PAGE_SIZE, state=state) or {}
        results = resp.get("results") or []
        out += results
        try:
            count = int(resp.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if len(results) < PAGE_SIZE or (count > 0 and len(out) >= count):
            break
    return out


def _price(money) -> tuple[str, str]:
    if isinstance(money, dict) and money.get("divisor"):
        return f"{money['amount'] / money['divisor']:.2f}", str(money.get("currency_code", ""))
    return ("" if money is None else str(money)), ""


def to_rows(listings: list[dict], now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    rows = []
    for x in listings:
        price, currency = _price(x.get("price"))
        created = x.get("original_creation_timestamp") or x.get("created_timestamp") or x.get("creation_timestamp")
        rows.append({
            "title": html.unescape(x.get("title") or ""),
            "views": x.get("views", ""),
            "favorites": x.get("num_favorers", ""),
            "price": price,
            "currency": currency,
            "quantity": x.get("quantity", ""),
            "tags": ", ".join(html.unescape(str(t)) for t in x.get("tags") or []),
            "state": x.get("state", ""),
            "age_days": int((now - created) // 86400) if created else "",
        })
    return rows


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def write_own_stats(client, path: str | Path) -> tuple[Path, int]:
    """Fetch and write the CSV. Returns (path, row count)."""
    rows = to_rows(fetch_own_listings(client))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(to_csv(rows), encoding="utf-8")
    tmp.replace(path)
    return path, len(rows)


# --- daily snapshot ------------------------------------------------------------------

def local_day(settings: dict | None = None, now: float | None = None) -> str:
    """Today's date in Botsy's time zone (schedule.timezone), for file names and `day` fields."""
    when = None if now is None else datetime.fromtimestamp(now, timezone.utc)
    return now_local(settings, when).date().isoformat()


def _day_of(timestamp, settings: dict | None) -> str | None:
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(tz(settings)).date().isoformat()


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def snapshot_listing(x: dict, settings: dict | None = None) -> dict | None:
    """One performance/v1 listing record, or None for a row without a numeric listing_id."""
    lid = _number(x.get("listing_id"))
    if lid is None or lid <= 0:
        return None
    price, currency = _price(x.get("price"))
    created = x.get("original_creation_timestamp") or x.get("created_timestamp") or x.get("creation_timestamp")
    return {
        "listing_id": lid,
        "state": str(x.get("state") or ""),
        "title": html.unescape(str(x.get("title") or "")),
        "tags": [html.unescape(str(t)) for t in x.get("tags") or []],
        "views": _number(x.get("views")),
        "num_favorers": _number(x.get("num_favorers")),
        "created": _day_of(created, settings),
        "price": float(price) if price not in ("", None) else None,
        "currency": currency or None,
        "quantity": _number(x.get("quantity")),
    }


def build_snapshot(active: list[dict], drafts: list[dict], *, day: str, etsy_shop_id,
                   settings: dict | None = None, now: float | None = None) -> dict:
    rows = [r for r in (snapshot_listing(x, settings) for x in [*active, *drafts]) if r]
    seen: set[int] = set()
    listings = []
    for r in rows:  # a listing never appears twice (a draft that also came back as active)
        if r["listing_id"] not in seen:
            seen.add(r["listing_id"])
            listings.append(r)
    taken = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
    return {
        "schema": SNAPSHOT_SCHEMA,
        "day": day,
        "taken": taken.isoformat(timespec="seconds"),
        "etsy_shop_id": int(etsy_shop_id) if str(etsy_shop_id).isdigit() else etsy_shop_id,
        "active": sum(1 for r in listings if r["state"] == "active"),
        "drafts": sum(1 for r in listings if r["state"] == "draft"),
        "listings": listings,
    }


def snapshot_path(performance_dir: str | Path, day: str) -> Path:
    return Path(performance_dir) / "etsy" / f"{day}.json"


def write_snapshot(client, performance_dir: str | Path, *, day: str | None = None,
                   settings: dict | None = None, now: float | None = None) -> tuple[Path, dict]:
    """Fetch our active and draft listings and write `<performance_dir>/etsy/<day>.json`.
    Idempotent per day (the file is replaced). Returns (path, snapshot)."""
    day = day or local_day(settings, now)
    active = fetch_own_listings(client)
    drafts = fetch_own_listings(client, state="draft")
    snap = build_snapshot(active, drafts, day=day, etsy_shop_id=client.shop_id, settings=settings, now=now)
    path = snapshot_path(performance_dir, day)
    write_json(path, snap)
    return path, snap


def write_all(client, csv_path: str | Path, performance_dir: str | Path, *, day: str | None = None,
              settings: dict | None = None, now: float | None = None) -> dict:
    """The research CSV (id-free) and the daily snapshot from one pass over our listings."""
    day = day or local_day(settings, now)
    active = fetch_own_listings(client)
    drafts = fetch_own_listings(client, state="draft")
    rows = to_rows(active, now=now)
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = csv_path.with_name(f".{csv_path.name}.tmp")
    tmp.write_text(to_csv(rows), encoding="utf-8")
    tmp.replace(csv_path)
    snap = build_snapshot(active, drafts, day=day, etsy_shop_id=client.shop_id, settings=settings, now=now)
    path = write_json(snapshot_path(performance_dir, day), snap)
    return {"csv": csv_path, "csv_rows": len(rows), "snapshot": path, "active": snap["active"],
            "drafts": snap["drafts"]}
