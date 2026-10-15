"""Our own shop's listing stats -> data/research_inputs/own_shop_stats.csv for research.

Only our own active listings (getListingsByShop on ETSY_SHOP_ID, which the client
enforces). The CSV holds titles, tags and numbers only: no URLs, listing ids or shop
names, so nothing identifying can leak into briefs. Column names match the research
stage's aliases (title, views, favorites, price, tags, age_days).

`views` is tabulated by Etsy once a day and only for active listings (spec note on
ShopListingWithAssociations.views); `num_favorers` is written as `favorites`. Titles and
tags are HTML-unescaped, since Etsy returns some text with entities such as &#39;.
"""
from __future__ import annotations

import csv
import html
import io
import time
from pathlib import Path

COLUMNS = ["title", "views", "favorites", "price", "currency", "quantity", "tags", "state", "age_days"]
PAGE_SIZE = 100  # spec maximum for getListingsByShop `limit`


def fetch_own_listings(client, max_pages: int = 100) -> list[dict]:
    """Every active listing of our shop, paging with limit/offset."""
    out: list[dict] = []
    for page in range(max_pages):
        resp = client.get_listings_by_shop(limit=PAGE_SIZE, offset=page * PAGE_SIZE) or {}
        results = resp.get("results") or []
        out += results
        if len(results) < PAGE_SIZE or len(out) >= int(resp.get("count") or 0):
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
