"""Offline stand-ins for dry runs and tests: no network, no secrets.

FakeEtsySession replaces requests.Session and answers like Etsy with fake ids.
FakeEtsyClient is the real EtsyClient on top of it, so dry runs still pass every request
through the safety layer and record exactly what a live run would send.
"""
from __future__ import annotations

import itertools
import json
import time
from typing import Any
from urllib.parse import urlsplit

from publisher.auth import MemoryTokenStore
from publisher.client import EtsyClient
from publisher.defaults import load_cfg
from publisher.safety import ROUTES

FAKE_USER_ID = 1
FAKE_SCOPES = "listings_r listings_w listings_d shops_r transactions_r"
DRY_RUN_TAXONOMY_ID = 90000002  # "Dry Run Paper Goods > Coloring Pages" below
# Obviously fake ids, never real Etsy taxonomy: dry runs must not suggest real categories.
FAKE_TAXONOMY = [
    {"id": 90000001, "level": 0, "name": "Dry Run Paper Goods", "parent_id": None,
     "full_path_taxonomy_ids": [90000001], "children": [
         {"id": 90000002, "level": 1, "name": "Coloring Pages", "parent_id": 90000001,
          "full_path_taxonomy_ids": [90000001, 90000002], "children": []},
         {"id": 90000003, "level": 1, "name": "Stickers", "parent_id": 90000001,
          "full_path_taxonomy_ids": [90000001, 90000003], "children": []},
         {"id": 90000004, "level": 1, "name": "Activity Sheets", "parent_id": 90000001,
          "full_path_taxonomy_ids": [90000001, 90000004], "children": []},
     ]},
]


class _Headers(dict):
    """Case-insensitive .get() like requests' headers."""

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class FakeResponse:
    def __init__(self, status_code: int = 200, body: Any = None, headers: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = _Headers({k.lower(): v for k, v in (headers or {}).items()})
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body


class FakeEtsySession:
    """Records every request and answers per Etsy operation.

    Tests script failures per operation with `fail()` and `crash()`, applied in order.
    Unknown listing ids get a 404 like on Etsy, unless `adopt_unknown`: then they are taken
    to be drafts of ours, so a dry run can preview or resume a draft recorded in
    publish.json that this fake never created."""

    def __init__(self, shop_id: int, user_id: int = FAKE_USER_ID, me_shop_id: int | None = None,
                 currency: str = "USD", demo_listings: bool = True, adopt_unknown: bool = False):
        self.shop_id, self.user_id = int(shop_id), user_id
        self.adopt_unknown = adopt_unknown
        self.me_shop_id = int(me_shop_id if me_shop_id is not None else shop_id)
        self.currency = currency
        self.requests: list[dict] = []
        self.listings: dict[int, dict] = {}
        self.transactions: list[dict] = []  # newest first, like Etsy answers in practice
        self.reviews: list[dict] = []
        self._ids = itertools.count(900000001)
        self._tx_ids = itertools.count(800000001)  # transactions have their own ids
        self._scripted: dict[str, list] = {}
        if demo_listings:  # two active listings so own-stats has something in dry runs
            now = int(time.time())
            for n in (1, 2):
                lid = next(self._ids)
                self.listings[lid] = {
                    "listing_id": lid, "shop_id": self.shop_id, "state": "active",
                    "title": f"Dry run sample listing {n}", "tags": ["dry run", "sample"],
                    "views": 10 * n, "num_favorers": n, "quantity": 999,
                    "price": {"amount": 400, "divisor": 100, "currency_code": currency},
                    "original_creation_timestamp": now - 86400 * 30 * n}
                # A sale and a review per listing, with the buyer fields Etsy really sends,
                # so tests can prove sales.py and reviews.py drop them.
                self.transactions.append(self.fake_transaction(lid, created=now - 86400 * (3 * n)))
                self.reviews.append({"shop_id": self.shop_id, "listing_id": lid, "rating": 5,
                                     "transaction_id": 800000100 + n, "buyer_user_id": 777000 + n,
                                     "review": "Dry run review text, never stored", "language": "en",
                                     "image_url_fullxfull": None, "create_timestamp": now - 86400 * n,
                                     "created_timestamp": now - 86400 * n})

    def fake_transaction(self, listing_id: int, *, created: int, quantity: int = 1,
                         amount: int = 400) -> dict:
        tid = next(self._tx_ids)
        return {"transaction_id": tid, "title": self.listings.get(listing_id, {}).get("title"),
                "seller_user_id": self.user_id, "buyer_user_id": 777000 + tid % 100,
                "create_timestamp": created, "created_timestamp": created, "paid_timestamp": created,
                "shipped_timestamp": None, "quantity": quantity, "listing_image_id": None,
                "receipt_id": 555000 + tid % 1000, "is_digital": True, "file_data": "x.pdf",
                "listing_id": listing_id, "sku": "", "product_id": tid, "transaction_type": "listing",
                "price": {"amount": amount, "divisor": 100, "currency_code": self.currency},
                "shipping_cost": {"amount": 0, "divisor": 100, "currency_code": self.currency},
                "variations": [], "product_data": [], "buyer_coupon": 0, "shop_coupon": 0}

    def fail(self, operation: str, status: int, *, times: int = 1, after: int = 0,
             headers: dict | None = None, body: Any = None) -> None:
        """Answer the next `times` calls of `operation` with `status`, after letting
        `after` calls succeed normally."""
        body = body if body is not None else {"error": f"scripted HTTP {status}"}
        queue = self._scripted.setdefault(operation, [])
        queue.append([after, FakeResponse(status, body, headers)])
        queue.extend([0, FakeResponse(status, body, headers)] for _ in range(times - 1))

    def crash(self, operation: str, exc: BaseException, *, after: int = 0) -> None:
        """Raise `exc` from the transport (like a dropped connection or a killed process)."""
        self._scripted.setdefault(operation, []).append([after, exc])

    def calls(self, operation: str | None = None) -> list[dict]:
        return [r for r in self.requests if operation is None or r["operation"] == operation]

    def request(self, method, url, *, params=None, data=None, files=None, headers=None,
                timeout=None, allow_redirects=True):
        parts = urlsplit(url)
        route = next((r for r in ROUTES if r.method == method.upper() and r.pattern.fullmatch(parts.path)),
                     None)
        op = route.name if route else "unknown"
        groups = route.pattern.fullmatch(parts.path).groupdict() if route else {}
        self.requests.append({
            "operation": op, "method": method.upper(), "url": url, "path": parts.path,
            "params": dict(params or {}), "data": dict(data or {}),
            "files": {k: {"filename": v[0], "bytes": len(v[1]), "mime": v[2]}
                      for k, v in (files or {}).items()},
            "headers": {k: ("<redacted>" if k.lower() in ("x-api-key", "authorization") else v)
                        for k, v in (headers or {}).items()},
            "has_api_key": bool((headers or {}).get("x-api-key")),
            "has_bearer": str((headers or {}).get("Authorization", "")).startswith("Bearer "),
            "allow_redirects": allow_redirects,
        })
        queue = self._scripted.get(op)
        if queue and queue[0][0] > 0:
            queue[0][0] -= 1
        elif queue:
            item = queue.pop(0)[1]
            if isinstance(item, BaseException):
                raise item
            return item
        return self._answer(op, groups, dict(params or {}), dict(data or {}))

    def _answer(self, op: str, groups: dict, params: dict, data: dict) -> FakeResponse:
        if op == "oauthToken":
            n = next(self._ids)
            return FakeResponse(200, {"access_token": f"{self.user_id}.fake-access-{n}",
                                      "token_type": "Bearer", "expires_in": 3600,
                                      "refresh_token": f"{self.user_id}.fake-refresh-{n}",
                                      "scope": FAKE_SCOPES})
        if op == "getMe":
            return FakeResponse(200, {"user_id": self.user_id, "shop_id": self.me_shop_id})
        if op == "getShop":
            return FakeResponse(200, {"shop_id": self.shop_id, "shop_name": "DryRunShop",
                                      "currency_code": self.currency})
        if op == "createDraftListing":
            lid = next(self._ids)
            price = float(data.get("price", 0))
            self.listings[lid] = {
                "listing_id": lid, "shop_id": self.shop_id, "state": "draft",
                "title": data.get("title"), "tags": [t for t in str(data.get("tags", "")).split(",") if t],
                "listing_type": data.get("type") or "physical", "views": 0, "num_favorers": 0,
                "quantity": int(data.get("quantity", 1)),
                "price": {"amount": round(price * 100), "divisor": 100, "currency_code": self.currency},
                "original_creation_timestamp": int(time.time()), "images": [], "files": []}
            return FakeResponse(201, {k: v for k, v in self.listings[lid].items()
                                      if k not in ("images", "files")})
        if op in ("uploadListingImage", "uploadListingFile", "getListing", "deleteListing"):
            lid = int(groups["listing_id"])
            if lid not in self.listings and self.adopt_unknown:
                self.listings[lid] = {"listing_id": lid, "shop_id": self.shop_id, "state": "draft",
                                      "listing_type": "download", "images": [], "files": []}
            if lid not in self.listings:
                return FakeResponse(404, {"error": f"listing {lid} not found"})
        if op in ("uploadListingImage", "uploadListingFile"):
            new_id = next(self._ids)
            if op == "uploadListingImage":
                rec = {"listing_image_id": new_id, "listing_id": lid, "rank": int(data.get("rank", 1)),
                       "alt_text": data.get("alt_text")}
                self.listings[lid]["images"].append(rec)
            else:
                rec = {"listing_file_id": new_id, "listing_id": lid, "rank": int(data.get("rank", 1)),
                       "filename": data.get("name")}
                self.listings[lid]["files"].append(rec)
            return FakeResponse(201, rec)
        if op == "getListing":
            found = self.listings[int(groups["listing_id"])]
            return FakeResponse(200, {k: v for k, v in found.items() if k not in ("images", "files")})
        if op == "deleteListing":
            self.listings.pop(int(groups["listing_id"]))
            return FakeResponse(204)
        if op == "getSellerTaxonomyNodes":
            return FakeResponse(200, {"count": len(FAKE_TAXONOMY), "results": FAKE_TAXONOMY})
        if op == "getListingsByShop":
            state = params.get("state", "active")
            rows = [{k: v for k, v in x.items() if k not in ("images", "files")}
                    for x in self.listings.values() if x["state"] == state]
            off, lim = int(params.get("offset", 0)), int(params.get("limit", 25))
            return FakeResponse(200, {"count": len(rows), "results": rows[off:off + lim]})
        if op in ("getShopReceiptTransactionsByShop", "getReviewsByShop"):
            rows = self.transactions if op == "getShopReceiptTransactionsByShop" else self.reviews
            off, lim = int(params.get("offset", 0)), min(int(params.get("limit", 25)), 100)
            return FakeResponse(200, {"count": len(rows), "results": rows[off:off + lim]})
        return FakeResponse(404, {"error": f"fake Etsy has no answer for {op}"})


class FakeEtsyClient(EtsyClient):
    """EtsyClient on a FakeEtsySession with in-memory tokens. Needs no secrets."""

    def __init__(self, *, shop_id: int | None = None, settings: dict | None = None,
                 session: FakeEtsySession | None = None, token_store=None, **kwargs):
        shop_id = int(shop_id if shop_id is not None else load_cfg(settings)["dry_run_shop_id"])
        session = session or FakeEtsySession(shop_id, adopt_unknown=True)
        token_store = token_store or MemoryTokenStore({
            "access_token": f"{FAKE_USER_ID}.dry-run-access", "refresh_token": f"{FAKE_USER_ID}.dry-run-refresh",
            "scope": FAKE_SCOPES, "expires_at": time.time() + 10 * 365 * 86400})
        kwargs.setdefault("sleep", lambda s: None)
        kwargs.setdefault("quota_path", None)  # memory only; a dry run names its own file
        super().__init__(session, keystring="dry-run-keystring", shared_secret="dry-run-secret",
                         shop_id=shop_id, token_store=token_store, settings=settings, **kwargs)

    @property
    def requests(self) -> list[dict]:
        return self.session.requests
