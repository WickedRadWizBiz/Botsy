"""Our own shop's sales -> `<performance_dir>/sales/<YYYY-MM-DD>.json` (schema sales/v1).

Source: getShopReceiptTransactionsByShop on ETSY_SHOP_ID (scope transactions_r, approved by
the user on 2026-10-17). Etsy's API Terms allow this for our own shop; the safety layer
pins the path to our shop id and refuses every receipt endpoint (publisher/safety.py).

What is kept per sale: listing_id, day (Botsy's local day of created_timestamp), quantity
and price (unit price as a number; the shop currency once per file). Never stored:
buyer_user_id, receipt_id, transaction_id, names, addresses, coupon fields, notes, SKUs,
file names. The endpoint has no date filter and Etsy does not document the sort order
(newest first in practice), so paging stops at the first page that is entirely older than
the window (90 days, `publisher.sales_keep_days`), at a short page, or at the page cap
(`publisher.sales_max_pages`, 50); `complete` is false when the cap cut the window short.

A token without the scope (issued before transactions_r was added) is skipped with one
log line, not an error: `python -m publisher auth` once more grants it.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import tz
from core.items import write_json
from publisher.client import EtsyAPIError
from publisher.own_stats import local_day

log = logging.getLogger("publisher.sales")

SCHEMA = "sales/v1"
SCOPE = "transactions_r"
PAGE_SIZE = 100  # spec maximum for `limit`
KEPT_FIELDS = ("listing_id", "day", "quantity", "price")
_warned: set[str] = set()


def _warn_once(key: str, msg: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(msg, *args)


def reset_warnings() -> None:  # tests
    _warned.clear()


def _money(value) -> tuple[float | None, str | None]:
    if isinstance(value, dict) and value.get("divisor"):
        try:
            return round(float(value["amount"]) / float(value["divisor"]), 2), value.get("currency_code") or None
        except (TypeError, ValueError, ZeroDivisionError):
            return None, None
    try:
        return (None if value is None else round(float(value), 2)), None
    except (TypeError, ValueError):
        return None, None


def _count(resp: dict) -> int | None:
    """Etsy's `count` (total rows), or None when missing or not a positive number, so a
    missing count never ends paging after the first page."""
    try:
        n = int(resp.get("count"))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _timestamp(tx: dict) -> float | None:
    for key in ("created_timestamp", "create_timestamp", "paid_timestamp"):
        try:
            if tx.get(key) is not None:
                return float(tx[key])
        except (TypeError, ValueError):
            continue
    return None


def reduce_transaction(tx: dict, settings: dict | None = None) -> tuple[dict | None, str | None]:
    """(kept sale, currency) from one ShopReceiptTransaction, or (None, None) when it has
    no listing id or no time stamp. Only KEPT_FIELDS survive."""
    lid = str(tx.get("listing_id", "")).strip()
    ts = _timestamp(tx)
    if not lid.isdigit() or int(lid) <= 0 or ts is None:
        return None, None
    price, currency = _money(tx.get("price"))
    try:
        quantity = int(tx.get("quantity") or 0)
    except (TypeError, ValueError):
        quantity = 0
    day = datetime.fromtimestamp(ts, timezone.utc).astimezone(tz(settings)).date().isoformat()
    return {"listing_id": int(lid), "day": day, "quantity": quantity, "price": price}, currency


def fetch_sales(client, *, keep_days: int = 90, max_pages: int = 50, settings: dict | None = None,
                now: float | None = None) -> dict | None:
    """Page our sales and keep the window. None when the token lacks the scope (logged once)."""
    if client.has_scope(SCOPE) is False:
        _warn_once("scope", "sales: the Etsy token lacks the %s scope; run `python -m publisher auth` "
                   "once more to grant it. Sales are skipped until then.", SCOPE)
        return None
    now = time.time() if now is None else now
    # keep_days local days including today (90 -> today and the 89 days before it).
    since = (datetime.fromtimestamp(now, timezone.utc).astimezone(tz(settings)).date()
             - timedelta(days=max(int(keep_days), 1) - 1)).isoformat()
    sales: list[dict] = []
    currency: str | None = None
    complete, pages, count = True, 0, None
    for page in range(int(max_pages)):
        try:
            resp = client.get_shop_receipt_transactions(limit=PAGE_SIZE, offset=page * PAGE_SIZE) or {}
        except EtsyAPIError as e:
            if e.status == 403:
                _warn_once("403", "sales: Etsy refused getShopReceiptTransactionsByShop (%s); the token "
                           "probably lacks %s. Run `python -m publisher auth` once more.", e.detail, SCOPE)
                return None
            raise
        pages += 1
        results = resp.get("results") or []
        if count is None:
            count = _count(resp)
        older = newer = 0
        stamps = [t for t in (_timestamp(tx) for tx in results) if t is not None]
        for tx in results:
            sale, cur = reduce_transaction(tx, settings)
            if sale is None:
                continue
            currency = currency or cur
            if sale["day"] < since:
                older += 1
                continue
            newer += 1
            sales.append(sale)
        if not results or len(results) < PAGE_SIZE or (count is not None and (page + 1) * PAGE_SIZE >= count):
            break
        # The whole page is before the window and the page itself runs newest first, so
        # nothing newer follows. A page in any other order never stops paging early (then
        # only the page cap does), so an unexpected sort order cannot drop recent sales.
        if older and not newer and stamps == sorted(stamps, reverse=True):
            break
    else:
        complete = False  # the page cap stopped us before the window was exhausted
    sales.sort(key=lambda s: (s["day"], s["listing_id"]))
    return {"schema": SCHEMA, "day": local_day(settings, now), "etsy_shop_id": client.shop_id,
            "window_days": int(keep_days), "since": since, "complete": complete, "pages": pages,
            "currency": currency, "sales": sales}


def sales_path(performance_dir: str | Path, day: str) -> Path:
    return Path(performance_dir) / "sales" / f"{day}.json"


def write_sales(client, performance_dir: str | Path, *, keep_days: int = 90, max_pages: int = 50,
                settings: dict | None = None, now: float | None = None) -> tuple[Path | None, dict | None]:
    """Fetch and write `<performance_dir>/sales/<day>.json` (replaced on a rerun the same
    day). (None, None) when sales were skipped for a missing scope."""
    doc = fetch_sales(client, keep_days=keep_days, max_pages=max_pages, settings=settings, now=now)
    if doc is None:
        return None, None
    path = write_json(sales_path(performance_dir, doc["day"]), doc)
    return path, doc
