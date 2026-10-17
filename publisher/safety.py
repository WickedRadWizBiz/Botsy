"""Safety layer: every Etsy request passes `Guard.check()` before it is sent.

CLAUDE.md allows Etsy API use only for our own shop (drafts, images, digital files, our
own stats) and forbids activating listings. The guard enforces that mechanically, so a
bug elsewhere cannot turn into a request Etsy's terms (or the user) did not allow:

(a) only the endpoints in ROUTES, on Etsy's API hosts over https;
(b) a shop id in the path must be our configured ETSY_SHOP_ID, and a listing id must be
    one this tool created (recorded in publish.json), so other sellers' shops and
    listings, and our own hand-made listings, are never touched;
(c) no request may carry `state` other than "draft", and PUT/PATCH (updateListing,
    the only way to activate) is never sent;
plus: no raw bodies (the guard must be able to read every field) and multipart uploads
only on the two upload endpoints.

Own-shop performance (docs/OVERSEER.md "Scale-ups", user approval of transactions_r on
2026-10-17): GET /shops/{own}/transactions (getShopReceiptTransactionsByShop) and
GET /shops/{own}/reviews (getReviewsByShop) are allowed, pinned to ETSY_SHOP_ID like
every shop route. Every `/receipts` path is refused by name: receipts carry the buyer's
name, email and address, which Botsy never reads (publisher/sales.py keeps only
listing_id, day, quantity and price).
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

API_HOSTS = frozenset({"api.etsy.com", "openapi.etsy.com"})  # documented as equivalent
UPLOAD_FIELDS = {"uploadListingImage": "image", "uploadListingFile": "file"}
GRANT_TYPES = frozenset({"authorization_code", "refresh_token"})


class SafetyViolation(RuntimeError):
    """A request was refused before it left the machine."""


@dataclass(frozen=True)
class Route:
    name: str            # Etsy operationId
    method: str
    pattern: re.Pattern
    auth: bool = True    # needs the OAuth bearer token
    retry_5xx: bool = True  # safe to resend after a server error (see client.py)


_SHOP = r"/v3/application/shops/(?P<shop_id>[^/]+)"
_LISTING = _SHOP + r"/listings/(?P<listing_id>[^/]+)"
ROUTES: tuple[Route, ...] = (
    Route("oauthToken", "POST", re.compile(r"/v3/public/oauth/token"), auth=False),
    Route("getMe", "GET", re.compile(r"/v3/application/users/me")),
    Route("getShop", "GET", re.compile(_SHOP), auth=False),
    # A retried create after a server-side success would leave a second draft.
    Route("createDraftListing", "POST", re.compile(_SHOP + r"/listings"), retry_5xx=False),
    Route("getListingsByShop", "GET", re.compile(_SHOP + r"/listings")),
    # Images are resent safely (rank + overwrite=true). A file upload has no overwrite, so a
    # resend after a server-side success would attach the same download twice.
    Route("uploadListingImage", "POST", re.compile(_LISTING + r"/images")),
    Route("uploadListingFile", "POST", re.compile(_LISTING + r"/files"), retry_5xx=False),
    Route("getListing", "GET", re.compile(r"/v3/application/listings/(?P<listing_id>[^/]+)")),
    Route("deleteListing", "DELETE", re.compile(r"/v3/application/listings/(?P<listing_id>[^/]+)")),
    Route("getSellerTaxonomyNodes", "GET", re.compile(r"/v3/application/seller-taxonomy/nodes"),
          auth=False),
    # Our own sales (scope transactions_r) and our own listings' star ratings (app key only).
    Route("getShopReceiptTransactionsByShop", "GET", re.compile(_SHOP + r"/transactions")),
    Route("getReviewsByShop", "GET", re.compile(_SHOP + r"/reviews"), auth=False),
)
# Never fetched, whatever the method or shop: receipts hold buyer names, emails and addresses.
BUYER_DATA_SEGMENTS = frozenset({"receipts", "receipt"})


def _as_id(value: Any, what: str) -> int:
    text = str(value).strip()
    if not text.isdigit() or int(text) <= 0:
        raise SafetyViolation(f"{what} must be a positive number, got {value!r}")
    return int(text)


def _pairs(source: Any, what: str) -> Iterator[tuple[str, Any]]:
    if source is None:
        return
    if isinstance(source, dict):
        yield from ((str(k), v) for k, v in source.items())
    elif isinstance(source, (list, tuple)):
        for pair in source:
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                raise SafetyViolation(f"{what} must be key/value pairs")
            yield str(pair[0]), pair[1]
    else:
        raise SafetyViolation(f"{what} must be a dict or key/value pairs, not a raw "
                              f"{type(source).__name__} the guard cannot inspect")


def _is_state_key(key: str) -> bool:
    """'state', 'state[]' or 'listing[state]' all set a listing state."""
    return "state" in re.split(r"[\[\]\s]+", key.lower())


def _values(value: Any) -> Iterable[Any]:
    return value if isinstance(value, (list, tuple, set)) else [value]


class Guard:
    def __init__(self, shop_id: int | str | None, owned_listing_ids: Iterable[int | str] = ()):
        self.shop_id = _as_id(shop_id, "ETSY_SHOP_ID") if shop_id not in (None, "") else None
        self.owned: set[int] = {_as_id(i, "listing id") for i in owned_listing_ids}

    def own(self, listing_id: int | str) -> None:
        """Mark a listing as created by this tool (after createDraftListing, or from publish.json)."""
        self.owned.add(_as_id(listing_id, "listing id"))

    def disown(self, listing_id: int | str) -> None:
        self.owned.discard(_as_id(listing_id, "listing id"))

    def check(self, method: str, url: str, *, params: Any = None, data: Any = None,
              files: Any = None) -> Route:
        """Return the matching route, or raise SafetyViolation."""
        method = method.upper()
        parts = urlsplit(url)
        try:
            port = parts.port
        except ValueError:  # e.g. https://api.etsy.com:abc/
            port = -1
        if (parts.scheme != "https" or parts.hostname not in API_HOSTS
                or port not in (None, 443) or parts.username or parts.password):
            raise SafetyViolation(f"refusing request to {parts.scheme}://{parts.netloc}: "
                                  f"only https://{' or '.join(sorted(API_HOSTS))}")
        if method in ("PUT", "PATCH"):
            raise SafetyViolation(f"{method} is never sent: updateListing could activate or "
                                  "change a listing (the user activates drafts in Etsy)")
        if BUYER_DATA_SEGMENTS & set(parts.path.lower().split("/")):
            raise SafetyViolation(f"{method} {parts.path} is refused: receipt endpoints carry buyer "
                                  "names, emails and addresses, which Botsy never reads")
        route = next((r for r in ROUTES if r.method == method and r.pattern.fullmatch(parts.path)),
                     None)
        if route is None:
            raise SafetyViolation(f"{method} {parts.path} is not on the publisher allowlist")
        match = route.pattern.fullmatch(parts.path)
        groups = match.groupdict() if match else {}

        fields = [*parse_qsl(parts.query, keep_blank_values=True),
                  *_pairs(params, "params"), *_pairs(data, "data")]
        upload_field = UPLOAD_FIELDS.get(route.name)
        for key, _ in _pairs(files, "files"):
            if key != upload_field:  # also refuses (None, "active") form fields smuggled in files
                raise SafetyViolation(f"{route.name} does not take a multipart field {key!r}")
        for key, value in fields:
            if _is_state_key(key) and any(str(v).strip().lower() != "draft" for v in _values(value)):
                raise SafetyViolation(f"refusing {route.name} with {key}={value!r}: the publisher "
                                      "only ever works with drafts")

        if "shop_id" in groups:
            if self.shop_id is None:
                raise SafetyViolation("ETSY_SHOP_ID is not configured; no shop request is allowed")
            if _as_id(groups["shop_id"], "shop id in path") != self.shop_id:
                raise SafetyViolation(f"shop {groups['shop_id']} is not our shop ({self.shop_id}); "
                                      "other sellers' shops are never accessed")
        if "listing_id" in groups and _as_id(groups["listing_id"], "listing id") not in self.owned:
            raise SafetyViolation(f"listing {groups['listing_id']} was not created by this tool "
                                  "(not in any publish.json); refusing to touch it")
        if route.name == "oauthToken":
            grants = [v for k, v in fields if k == "grant_type"]
            if len(grants) != 1 or grants[0] not in GRANT_TYPES:
                raise SafetyViolation(f"unsupported OAuth grant_type {grants!r}")
        return route
