"""EtsyClient: Etsy Open API v3 for our own shop, with the safety layer on every request.

Every request goes through `Guard.check()` (publisher/safety.py) before it is sent, and
the first shop-scoped request checks via getMe that ETSY_SHOP_ID really is the shop of
the user who authorized the token. Redirects are never followed, because the x-api-key
header carries the shared secret.

Retries (developer.etsy.com/documentation/essentials/rate-limits): Etsy answers 429
with a `retry-after` header in seconds and recommends backoff on top of it. 429 is
retried for every request (Etsy rejects it before doing any work); 5xx and connection
errors are retried except for createDraftListing and uploadListingFile, where a retry
after a server-side success would leave a second draft or a duplicate download. Image
uploads are safe to resend because they use rank + overwrite.

Multiple shops: each shop in settings `shops` names its env vars. `etsy_shop_id_env`
(default ETSY_SHOP_ID), and optionally `etsy_token_file_env` (default ETSY_TOKEN_FILE)
and `etsy_refresh_token_env` (default ETSY_REFRESH_TOKEN). Every Etsy shop belongs to a
different Etsy user, so a second shop needs its own token file.
"""
from __future__ import annotations

import email.utils
import logging
import mimetypes
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.config import get_shop, load_env, load_settings, require_env, resolve
from publisher.auth import (AuthRequired, TokenStore, carry_refresh_issued, refresh_age_warning,
                            tokens_from_response)
from publisher.defaults import load_cfg
from publisher.safety import Guard, Route, SafetyViolation

log = logging.getLogger("publisher.client")


class EtsyAPIError(RuntimeError):
    def __init__(self, operation: str, status: int | None, detail: str):
        self.operation, self.status, self.detail = operation, status, detail
        super().__init__(f"{operation}: HTTP {status if status is not None else '-'}: {detail[:500]}")


class RateLimited(EtsyAPIError):
    """429 that did not clear within the retry budget (e.g. the daily quota is spent)."""


def retry_after_seconds(value: str | None, now: float | None = None) -> float | None:
    """`retry-after` as seconds (Etsy sends seconds; an HTTP date is accepted too)."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
    return max(0.0, when - (time.time() if now is None else now))


def _detail(resp) -> str:
    try:
        body = resp.json()
    except ValueError:
        return (getattr(resp, "text", "") or "").strip()
    if isinstance(body, dict):
        return str(body.get("error_description") or body.get("error") or body)
    return str(body)


class EtsyClient:
    """Etsy client for one shop (`shop_key`, default the first in settings). Unset
    arguments come from .env / the environment: ETSY_API_KEYSTRING, ETSY_SHARED_SECRET and
    the shop's ETSY_SHOP_ID, ETSY_TOKEN_FILE and ETSY_REFRESH_TOKEN variables."""

    def __init__(self, session=None, *, keystring: str | None = None,
                 shared_secret: str | None = None, shop_id: int | str | None = None,
                 token_store=None, settings: dict | None = None, shop_key: str | None = None,
                 owned_listing_ids=(), sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time, verify_shop: bool = True):
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.settings = settings or load_settings()
        self.cfg = load_cfg(self.settings)
        shop = get_shop(self.settings, shop_key)
        self.shop_key: str = shop["id"]  # publish refuses items of another configured shop
        if None in (keystring, shared_secret, shop_id, token_store):
            load_env()  # .env fills in what was not passed (fakes pass everything)
        self.keystring = keystring or require_env("ETSY_API_KEYSTRING")
        self._api_key = f"{self.keystring}:{shared_secret or require_env('ETSY_SHARED_SECRET')}"
        if shop_id is None:
            env_name = shop.get("etsy_shop_id_env", "ETSY_SHOP_ID")
            shop_id = os.environ.get(env_name, "").strip() or None  # optional for auth/taxonomy
        self.guard = Guard(shop_id, owned_listing_ids)
        if token_store is None:
            file_env = shop.get("etsy_token_file_env", "ETSY_TOKEN_FILE")
            token_store = TokenStore(resolve(os.environ.get(file_env) or ".etsy_tokens.json"),
                                     os.environ.get(shop.get("etsy_refresh_token_env", "ETSY_REFRESH_TOKEN")),
                                     os.environ.get("ETSY_REFRESH_TOKEN_ISSUED"))
        self.tokens = token_store
        self._token: dict | None = None
        self._sleep, self._clock = sleep, clock
        self.verify_shop = verify_shop
        self._shop_verified = False
        self.shop_info: dict | None = None  # getShop result, cached by publish
        self._own_titles: dict[str, dict] | None = None  # own_listing_titles() cache

    @property
    def shop_id(self) -> int | None:
        return self.guard.shop_id

    def own(self, listing_id: int | str) -> None:
        """Allow requests on a listing this tool created (id read from publish.json)."""
        self.guard.own(listing_id)

    # --- transport ----------------------------------------------------------------

    def request(self, method: str, path: str, *, params: Any = None, data: Any = None,
                files: Any = None) -> Any:
        url = path if path.startswith("https://") else self.cfg["api_base"] + path
        route = self.guard.check(method, url, params=params, data=data, files=files)
        if "shop_id" in route.pattern.groupindex:
            self._ensure_own_shop()
        return self._send(route, method.upper(), url, params, data, files)

    def _send(self, route: Route, method: str, url: str, params, data, files) -> Any:
        import requests

        retries, attempt, refreshed = int(self.cfg["retries"]), 0, False
        while True:
            headers = {"x-api-key": self._api_key, "Accept": "application/json"}
            if route.auth:
                headers["Authorization"] = f"Bearer {self._access_token()}"
            try:
                resp = self.session.request(method, url, params=params, data=data, files=files,
                                            headers=headers, timeout=self.cfg["timeout_s"],
                                            allow_redirects=False)
            except requests.RequestException as e:
                if not route.retry_5xx or attempt >= retries:
                    raise EtsyAPIError(route.name, None, f"{type(e).__name__}: {e}") from e
                self._wait(attempt, None)
                attempt += 1
                continue
            status = resp.status_code
            if status == 401 and route.auth and not refreshed:
                refreshed = True  # expired or revoked early: refresh once, then give up
                self._refresh()
                continue
            if status == 429 or (status >= 500 and route.retry_5xx):
                wait = retry_after_seconds(resp.headers.get("retry-after"), self._clock())
                if attempt >= retries or (wait is not None and wait > self.cfg["max_wait_s"]):
                    cls = RateLimited if status == 429 else EtsyAPIError
                    raise cls(route.name, status, f"{_detail(resp)} (retry-after={wait})")
                log.info("%s: HTTP %s, retrying (attempt %d/%d)", route.name, status, attempt + 1, retries)
                self._wait(attempt, wait)
                attempt += 1
                continue
            if status >= 300:
                raise EtsyAPIError(route.name, status, _detail(resp))
            if status == 204 or not getattr(resp, "content", b"x"):
                return None
            try:
                return resp.json()
            except ValueError as e:  # e.g. an HTML page from a proxy
                raise EtsyAPIError(route.name, status, f"response is not JSON: {_detail(resp)}") from e

    def _wait(self, attempt: int, retry_after: float | None) -> None:
        backoff = float(self.cfg["backoff_s"]) * (2 ** attempt)
        self._sleep(min(max(backoff, retry_after or 0.0), float(self.cfg["max_wait_s"])))

    # --- tokens -------------------------------------------------------------------

    def _access_token(self) -> str:
        tok = self._token or self.tokens.load()
        if not tok:
            raise AuthRequired("no Etsy tokens: run `python -m publisher auth` "
                               "(or set ETSY_REFRESH_TOKEN in CI)")
        if self._token is None:  # once per client
            warning = refresh_age_warning(tok, self._clock())
            if warning:
                log.warning(warning)
        self._token = tok
        if not tok.get("access_token") or float(tok.get("expires_at") or 0) - self.cfg["token_skew_s"] <= self._clock():
            tok = self._refresh()
        return tok["access_token"]

    def _refresh(self) -> dict:
        old = self._token or self.tokens.load()
        if not old or not old.get("refresh_token"):
            raise AuthRequired("no refresh token: run `python -m publisher auth`")
        try:
            resp = self.request("POST", self.cfg["token_url"], data={
                "grant_type": "refresh_token", "client_id": self.keystring,
                "refresh_token": old["refresh_token"]})
        except EtsyAPIError as e:
            if e.status in (400, 401):
                raise AuthRequired(f"Etsy rejected the refresh token ({e.detail}). It expires 90 days "
                                   "after issue and may be single-use: run `python -m publisher auth` "
                                   "again (and update ETSY_REFRESH_TOKEN in CI)") from e
            raise
        new = carry_refresh_issued(tokens_from_response(resp, self._clock()), old)
        if new["refresh_token"] != old["refresh_token"] and old.get("source") == "env":
            log.warning("Etsy issued a new refresh token; it is saved to the token file only. If "
                        "the old one stops working, update the ETSY_REFRESH_TOKEN secret.")
        self.tokens.save(new)  # save before use: a rotated old token may already be dead
        self._token = new
        return new

    def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> dict:
        resp = self.request("POST", self.cfg["token_url"], data={
            "grant_type": "authorization_code", "client_id": self.keystring,
            "redirect_uri": redirect_uri, "code": code, "code_verifier": verifier})
        tok = carry_refresh_issued(tokens_from_response(resp, self._clock()), None)  # newly issued
        self.tokens.save(tok)
        self._token = tok
        return tok

    def _ensure_own_shop(self) -> None:
        if not self.verify_shop or self._shop_verified:
            return
        me = self.get_me()
        if str(me.get("shop_id")) != str(self.shop_id):
            raise SafetyViolation(f"ETSY_SHOP_ID {self.shop_id} is not the shop of the authorized "
                                  f"Etsy user (their shop_id is {me.get('shop_id')}); refusing")
        self._shop_verified = True

    # --- endpoints (each one on the allowlist) -------------------------------------

    def get_me(self) -> dict:
        return self.request("GET", "/users/me")

    def get_shop(self) -> dict:
        return self.request("GET", f"/shops/{self.shop_id}")

    def create_draft_listing(self, fields: dict) -> dict:
        resp = self.request("POST", f"/shops/{self.shop_id}/listings", data=fields)
        self.guard.own(resp["listing_id"])
        if self._own_titles is not None and title_key(fields.get("title")):
            self._own_titles[title_key(fields.get("title"))] = {"listing_id": int(resp["listing_id"]),
                                                                "state": resp.get("state", "draft")}
        return resp

    def own_listing_titles(self, max_pages: int = 50) -> dict[str, dict]:
        """Our own shop's draft and active listings by title_key(title) ->
        {listing_id, state}, read once per client (getListingsByShop on our ETSY_SHOP_ID,
        which CLAUDE.md allows) and kept current by create_draft_listing. The publisher
        checks it before creating a draft, so a run without the earlier publish.json (a
        fresh CI checkout) does not draft the same listing twice."""
        if self._own_titles is None:
            found: dict[str, dict] = {}
            page_size = 100  # spec maximum for `limit`
            for state in ("draft", None):  # None: Etsy's default, active listings
                for page in range(max_pages):
                    resp = self.get_listings_by_shop(limit=page_size, offset=page * page_size, state=state) or {}
                    results = resp.get("results") or []
                    for x in results:
                        key = title_key(x.get("title"))
                        if key and str(x.get("listing_id", "")).isdigit():
                            found.setdefault(key, {"listing_id": int(x["listing_id"]),
                                                   "state": x.get("state") or state or "active"})
                    if len(results) < page_size or (page + 1) * page_size >= int(resp.get("count") or 0):
                        break
            self._own_titles = found
        return self._own_titles

    def upload_listing_image(self, listing_id: int, path: str | Path, *, rank: int,
                             alt_text: str | None = None) -> dict:
        """Multipart `image` + rank. overwrite=true replaces whatever sits at that rank,
        so a resumed or retried upload never stacks duplicates."""
        path = Path(path)
        data = {"rank": str(rank), "overwrite": "true"}
        if alt_text:
            data["alt_text"] = alt_text
        files = {"image": (path.name, path.read_bytes(), _mime(path, "image/jpeg"))}
        return self.request("POST", f"/shops/{self.shop_id}/listings/{listing_id}/images",
                            data=data, files=files)

    def upload_listing_file(self, listing_id: int, path: str | Path, *, rank: int,
                            name: str | None = None) -> dict:
        path = Path(path)
        name = name or path.name
        files = {"file": (name, path.read_bytes(), _mime(path, "application/octet-stream"))}
        return self.request("POST", f"/shops/{self.shop_id}/listings/{listing_id}/files",
                            data={"name": name, "rank": str(rank)}, files=files)

    def get_listing(self, listing_id: int) -> dict:
        """One of our listings. A listing of another shop (a tampered publish.json) stops here."""
        live = self.request("GET", f"/listings/{listing_id}") or {}
        self._check_listing_shop(live, listing_id)
        return live

    def _check_listing_shop(self, live: dict, listing_id: int) -> None:
        if live.get("shop_id") is not None and str(live["shop_id"]) != str(self.shop_id):
            self.guard.disown(listing_id)
            raise SafetyViolation(f"listing {listing_id} belongs to shop {live['shop_id']}, not ours "
                                  f"({self.shop_id}); refusing to touch it")

    def find_own_draft(self, listing_id: int, max_pages: int = 50) -> dict | None:
        """Our shop's draft with this id, from getListingsByShop(state=draft), or None."""
        page_size = 100  # spec maximum for `limit`
        for page in range(max_pages):
            resp = self.get_listings_by_shop(limit=page_size, offset=page * page_size, state="draft") or {}
            results = resp.get("results") or []
            for x in results:
                if str(x.get("listing_id")) == str(listing_id):
                    self._check_listing_shop(x, listing_id)
                    return x
            if len(results) < page_size or (page + 1) * page_size >= int(resp.get("count") or 0):
                break
        return None

    def listing_state(self, listing_id: int) -> dict | None:
        """Our listing as Etsy has it now, or None if it no longer exists.

        Whether getListing shows drafts to their owner is not documented (the spec gives
        it api_key security only), so a 404 is double-checked against our shop's drafts
        before a draft is declared gone."""
        try:
            return self.get_listing(listing_id)
        except EtsyAPIError as e:
            if e.status != 404:
                raise
        return self.find_own_draft(listing_id)

    def delete_listing(self, listing_id: int) -> bool:
        """Delete one of our own drafts (needs the listings_d scope). Anything that is not a
        draft is refused here as well, so a listing the user activated is never removed.
        Returns False when the listing is already gone."""
        live = self.listing_state(listing_id)
        if live is None:
            self._forget_title(listing_id)
            self.guard.disown(listing_id)
            return False
        if live.get("state") != "draft":
            raise SafetyViolation(f"listing {listing_id} is {live.get('state')!r}, not a draft; "
                                  "not deleting it")
        try:
            self.request("DELETE", f"/listings/{listing_id}")
        except EtsyAPIError as e:
            if e.status != 404:
                raise
        self._forget_title(listing_id)
        self.guard.disown(listing_id)
        return True

    def _forget_title(self, listing_id: int) -> None:
        if self._own_titles:
            self._own_titles = {k: v for k, v in self._own_titles.items()
                                if str(v["listing_id"]) != str(listing_id)}

    def get_seller_taxonomy_nodes(self) -> list[dict]:
        return (self.request("GET", "/seller-taxonomy/nodes") or {}).get("results", [])

    def get_listings_by_shop(self, *, limit: int = 100, offset: int = 0,
                             state: str | None = None) -> dict:
        """Our own listings. Without `state` Etsy returns active ones; the guard allows only
        `state=draft` as an explicit value."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if state:
            params["state"] = state
        return self.request("GET", f"/shops/{self.shop_id}/listings", params=params)


def title_key(title) -> str:
    """Comparison form of a listing title: case-insensitive, whitespace collapsed."""
    return " ".join(str(title or "").casefold().split())


def _mime(path: Path, default: str) -> str:
    return mimetypes.guess_type(path.name)[0] or default
