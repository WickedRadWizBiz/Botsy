"""Safety layer rules, request shaping, retries and token refresh. All offline."""
from __future__ import annotations

import time

import pytest
import requests

from publisher.auth import AuthRequired, MemoryTokenStore, TokenStore
from publisher.client import EtsyAPIError, EtsyClient, RateLimited, retry_after_seconds
from publisher.fake import FakeEtsyClient, FakeEtsySession
from publisher.safety import Guard, SafetyViolation

from .conftest import SHOP_ID

API = "https://api.etsy.com/v3/application"
OTHER_SHOP = 99999999


@pytest.fixture
def guard() -> Guard:
    return Guard(SHOP_ID, owned_listing_ids=[555])


# --- (a) allowlist -------------------------------------------------------------------

@pytest.mark.parametrize("method, url", [
    ("GET", f"{API}/listings/active"),                         # findAllListingsActive (market search)
    ("GET", f"{API}/shops"),                                   # findShops
    ("GET", f"{API}/shops/{SHOP_ID}/receipts"),                # not needed, not allowed
    ("GET", f"{API}/users/12345"),                             # getUser of anyone
    ("GET", f"{API}/listings/555/reviews"),
    ("POST", f"{API}/shops/{SHOP_ID}/listings/555/variation-images"),
    ("GET", "https://www.etsy.com/shop/SomeoneElse"),          # the website: never
    ("GET", "http://api.etsy.com/v3/application/users/me"),   # plain http
    ("GET", "https://api.etsy.com.evil.example/v3/application/users/me"),
    ("GET", "https://user:pw@api.etsy.com/v3/application/users/me"),
])
def test_unknown_or_foreign_endpoints_are_refused(guard, method, url):
    with pytest.raises(SafetyViolation):
        guard.check(method, url)


def test_allowlisted_endpoints_pass(guard):
    shop = f"{API}/shops/{SHOP_ID}"
    assert guard.check("GET", f"{API}/users/me").name == "getMe"
    assert guard.check("GET", shop).name == "getShop"
    assert guard.check("POST", f"{shop}/listings", data={"title": "x"}).name == "createDraftListing"
    assert guard.check("GET", f"{shop}/listings", params={"limit": 100}).name == "getListingsByShop"
    assert guard.check("POST", f"{shop}/listings/555/images", data={"rank": "1"},
                       files={"image": ("a.jpg", b"x", "image/jpeg")}).name == "uploadListingImage"
    assert guard.check("POST", f"{shop}/listings/555/files", data={"name": "a.pdf"},
                       files={"file": ("a.pdf", b"x", "application/pdf")}).name == "uploadListingFile"
    assert guard.check("GET", f"{API}/listings/555").name == "getListing"
    assert guard.check("DELETE", f"{API}/listings/555").name == "deleteListing"
    assert guard.check("GET", f"{API}/seller-taxonomy/nodes").name == "getSellerTaxonomyNodes"
    assert guard.check("POST", "https://api.etsy.com/v3/public/oauth/token",
                       data={"grant_type": "refresh_token"}).name == "oauthToken"
    assert guard.check("GET", "https://openapi.etsy.com/v3/application/users/me").name == "getMe"


# --- (b) our shop and our listings only ------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("GET", ""), ("POST", "/listings"), ("GET", "/listings"),
    ("POST", "/listings/555/images"), ("POST", "/listings/555/files"),
])
def test_foreign_shop_id_is_refused(guard, method, path):
    with pytest.raises(SafetyViolation, match="not our shop"):
        guard.check(method, f"{API}/shops/{OTHER_SHOP}{path}")


def test_shop_requests_refused_without_configured_shop():
    with pytest.raises(SafetyViolation, match="ETSY_SHOP_ID is not configured"):
        Guard(None).check("GET", f"{API}/shops/{SHOP_ID}")
    assert Guard(None).check("GET", f"{API}/users/me").name == "getMe"


@pytest.mark.parametrize("method, url", [
    ("GET", f"{API}/listings/777"),
    ("DELETE", f"{API}/listings/777"),
    ("POST", f"{API}/shops/{SHOP_ID}/listings/777/images"),
    ("POST", f"{API}/shops/{SHOP_ID}/listings/777/files"),
])
def test_listings_not_created_by_us_are_refused(guard, method, url):
    with pytest.raises(SafetyViolation, match="not created by this tool"):
        guard.check(method, url)
    guard.own(777)
    guard.check(method, url)


# --- (c) drafts only, never activate -------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"data": {"title": "x", "state": "active"}},
    {"data": {"title": "x", "State": "ACTIVE"}},
    {"data": {"listing[state]": "active"}},
    {"data": [("state", "draft"), ("state", "active")]},
    {"data": {"state": ["draft", "active"]}},
    {"params": {"state": "active"}},
])
def test_state_other_than_draft_is_refused(guard, kwargs):
    with pytest.raises(SafetyViolation, match="only ever works with drafts"):
        guard.check("POST", f"{API}/shops/{SHOP_ID}/listings", **kwargs)


def test_state_in_query_string_or_smuggled_in_files_is_refused(guard):
    with pytest.raises(SafetyViolation):
        guard.check("GET", f"{API}/shops/{SHOP_ID}/listings?state=active")
    with pytest.raises(SafetyViolation):
        guard.check("POST", f"{API}/shops/{SHOP_ID}/listings/555/images",
                    files={"image": ("a.jpg", b"x", "image/jpeg"), "state": (None, "active")})
    with pytest.raises(SafetyViolation, match="does not take a multipart"):
        guard.check("POST", f"{API}/shops/{SHOP_ID}/listings", files={"image": ("a", b"x", "x")})


def test_state_draft_is_allowed(guard):
    assert guard.check("GET", f"{API}/shops/{SHOP_ID}/listings", params={"state": "draft"})
    assert guard.check("POST", f"{API}/shops/{SHOP_ID}/listings", data={"state": "draft"})


@pytest.mark.parametrize("method", ["PATCH", "PUT", "patch"])
def test_update_listing_is_never_sent(guard, method):
    with pytest.raises(SafetyViolation, match="never sent"):
        guard.check(method, f"{API}/shops/{SHOP_ID}/listings/555", data={"title": "x"})


def test_raw_bodies_and_odd_grants_are_refused(guard):
    with pytest.raises(SafetyViolation, match="cannot inspect"):
        guard.check("POST", f"{API}/shops/{SHOP_ID}/listings", data="state=active")
    with pytest.raises(SafetyViolation, match="grant_type"):
        guard.check("POST", "https://api.etsy.com/v3/public/oauth/token", data={"grant_type": "password"})


# --- the client enforces the guard and verifies the shop ------------------------------

def test_refused_requests_never_reach_the_network(client, session):
    with pytest.raises(SafetyViolation):
        client.request("PATCH", f"/shops/{SHOP_ID}/listings/1", data={"state": "active"})
    with pytest.raises(SafetyViolation):
        client.request("GET", f"/shops/{OTHER_SHOP}")
    with pytest.raises(SafetyViolation):
        client.get_listing(424242)
    assert session.requests == []


def test_shop_must_belong_to_the_authorized_user(settings):
    session = FakeEtsySession(SHOP_ID, me_shop_id=OTHER_SHOP)
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)
    with pytest.raises(SafetyViolation, match="not the shop of the authorized"):
        c.get_shop()
    assert [r["operation"] for r in session.requests] == ["getMe"]


class HeaderSession(FakeEtsySession):
    """Keeps the real headers so the x-api-key format can be checked."""

    def request(self, method, url, **kw):
        self.raw_headers = dict(kw.get("headers") or {})
        return super().request(method, url, **kw)


def test_headers_api_key_bearer_and_no_redirects(settings):
    session = HeaderSession(SHOP_ID)
    c = EtsyClient(session, keystring="KEYSTRING", shared_secret="SECRET", shop_id=SHOP_ID,
                   settings=settings, token_store=MemoryTokenStore(
                       {"access_token": "1.tok", "refresh_token": "1.r", "expires_at": time.time() + 3600}))
    c.get_me()
    assert session.raw_headers["x-api-key"] == "KEYSTRING:SECRET"
    assert session.raw_headers["Authorization"] == "Bearer 1.tok"
    assert session.requests[-1]["allow_redirects"] is False
    c.get_seller_taxonomy_nodes()  # API key only
    assert "Authorization" not in session.raw_headers


def test_create_and_multipart_uploads_are_shaped_for_etsy(client, session, tmp_path):
    img, pdf = tmp_path / "m.jpg", tmp_path / "pages.pdf"
    img.write_bytes(b"\xff\xd8jpeg")
    pdf.write_bytes(b"%PDF-1.4 x")
    lid = client.create_draft_listing({"title": "T", "quantity": "1"})["listing_id"]
    client.upload_listing_image(lid, img, rank=2, alt_text="A cow")
    client.upload_listing_file(lid, pdf, rank=1)
    create, image, file = (session.calls(op)[0] for op in
                           ("createDraftListing", "uploadListingImage", "uploadListingFile"))
    assert create["path"] == f"/v3/application/shops/{SHOP_ID}/listings" and create["files"] == {}
    assert image["path"] == f"/v3/application/shops/{SHOP_ID}/listings/{lid}/images"
    assert image["data"] == {"rank": "2", "overwrite": "true", "alt_text": "A cow"}
    assert image["files"] == {"image": {"filename": "m.jpg", "bytes": 6, "mime": "image/jpeg"}}
    assert file["path"] == f"/v3/application/shops/{SHOP_ID}/listings/{lid}/files"
    assert file["data"] == {"name": "pages.pdf", "rank": "1"}
    assert file["files"] == {"file": {"filename": "pages.pdf", "bytes": 10, "mime": "application/pdf"}}


def test_delete_listing_only_deletes_drafts(client, session):
    lid = client.create_draft_listing({"title": "T"})["listing_id"]
    session.listings[lid]["state"] = "active"  # the user activated it in Etsy
    with pytest.raises(SafetyViolation, match="not a draft"):
        client.delete_listing(lid)
    assert session.calls("deleteListing") == []
    session.listings[lid]["state"] = "draft"
    client.delete_listing(lid)
    assert len(session.calls("deleteListing")) == 1
    with pytest.raises(SafetyViolation):  # forgotten after deletion
        client.get_listing(lid)


# --- (d) retries ------------------------------------------------------------------------

def _client(settings, session, sleeps, **cfg):
    settings = {**settings, "publisher": {**settings.get("publisher", {}), **cfg}}
    return FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session, sleep=sleeps.append)


def test_429_honors_retry_after_then_succeeds(settings, session):
    sleeps = []
    c = _client(settings, session, sleeps, backoff_s=1.0)
    session.fail("getSellerTaxonomyNodes", 429, headers={"Retry-After": "7"})
    session.fail("getSellerTaxonomyNodes", 429)
    assert c.get_seller_taxonomy_nodes()
    assert sleeps == [7.0, 2.0]  # max(retry-after, backoff), then backoff 1 * 2**1
    assert len(session.calls("getSellerTaxonomyNodes")) == 3


def test_429_with_long_retry_after_fails_fast(settings, session):
    sleeps = []
    c = _client(settings, session, sleeps, max_wait_s=60)
    session.fail("getSellerTaxonomyNodes", 429, headers={"retry-after": "3600"})
    with pytest.raises(RateLimited):
        c.get_seller_taxonomy_nodes()
    assert sleeps == []


def test_5xx_retried_until_budget_spent(settings, session):
    sleeps = []
    c = _client(settings, session, sleeps, retries=2, backoff_s=1.0)
    session.fail("getMe", 503, times=3)
    with pytest.raises(EtsyAPIError) as e:
        c.get_me()
    assert e.value.status == 503 and sleeps == [1.0, 2.0] and len(session.calls("getMe")) == 3


def test_create_draft_is_not_retried_on_5xx_or_connection_errors(settings, session):
    sleeps = []
    c = _client(settings, session, sleeps)
    session.fail("createDraftListing", 500)
    with pytest.raises(EtsyAPIError):
        c.create_draft_listing({"title": "T"})
    session.crash("createDraftListing", requests.ConnectionError("reset"))
    with pytest.raises(EtsyAPIError):
        c.create_draft_listing({"title": "T"})
    assert len(session.calls("createDraftListing")) == 2 and sleeps == []
    session.fail("createDraftListing", 429)  # rate limited requests were never processed
    assert c.create_draft_listing({"title": "T"})["state"] == "draft"


def test_connection_errors_on_reads_are_retried(settings, session):
    sleeps = []
    c = _client(settings, session, sleeps)
    session.crash("getMe", requests.ConnectionError("reset"))
    assert c.get_me()["shop_id"] == SHOP_ID and len(sleeps) == 1


def test_4xx_is_not_retried(settings, session):
    sleeps = []
    c = _client(settings, session, sleeps)
    session.fail("getMe", 403, body={"error": "insufficient scope"})
    with pytest.raises(EtsyAPIError, match="insufficient scope"):
        c.get_me()
    assert sleeps == []


def test_retry_after_parsing():
    assert retry_after_seconds("12") == 12.0
    assert retry_after_seconds(None) is None and retry_after_seconds("soon") is None
    assert retry_after_seconds("Thu, 01 Jan 2026 00:00:30 GMT", now=1767225600.0) == 30.0


# --- tokens ---------------------------------------------------------------------------

def test_expired_token_is_refreshed_and_rotation_saved(settings, session):
    store = MemoryTokenStore({"access_token": "1.old", "refresh_token": "1.r-old", "expires_at": 0})
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session, token_store=store)
    c.get_me()
    (refresh,) = session.calls("oauthToken")
    assert refresh["data"] == {"grant_type": "refresh_token", "client_id": "dry-run-keystring",
                               "refresh_token": "1.r-old"}
    assert not refresh["has_bearer"] and refresh["has_api_key"]
    assert store.tokens["refresh_token"].startswith("1.fake-refresh-")  # rotated token kept
    assert store.tokens["expires_at"] > time.time() + 3000
    assert session.requests[-1]["operation"] == "getMe" and session.requests[-1]["has_bearer"]
    c.get_me()
    assert len(session.calls("oauthToken")) == 1  # fresh token reused


def test_401_refreshes_once_then_retries(settings, session):
    store = MemoryTokenStore({"access_token": "1.a", "refresh_token": "1.r", "expires_at": time.time() + 3600})
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session, token_store=store)
    session.fail("getMe", 401, times=2)
    with pytest.raises(EtsyAPIError) as e:
        c.get_me()
    assert e.value.status == 401 and len(session.calls("oauthToken")) == 1
    assert len(session.calls("getMe")) == 2


def test_rejected_refresh_token_asks_for_auth(settings, session):
    store = MemoryTokenStore({"refresh_token": "1.dead", "expires_at": 0})
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session, token_store=store)
    session.fail("oauthToken", 400, body={"error": "invalid_grant"})
    with pytest.raises(AuthRequired, match="publisher auth"):
        c.get_me()


def test_no_tokens_at_all(settings, session, tmp_path):
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session,
                       token_store=TokenStore(tmp_path / "missing.json"))
    with pytest.raises(AuthRequired):
        c.get_me()
    assert session.requests == []


def test_ci_refresh_token_from_env_is_saved_to_token_file(settings, session, tmp_path):
    path = tmp_path / "runner" / "etsy_tokens.json"
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session,
                       token_store=TokenStore(path, env_refresh_token="1.ci-secret"))
    c.get_me()
    assert session.calls("oauthToken")[0]["data"]["refresh_token"] == "1.ci-secret"
    assert path.exists() and "fake-access" in path.read_text()


def test_client_reads_config_from_env(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("ETSY_API_KEYSTRING", "envkey")
    monkeypatch.setenv("ETSY_SHARED_SECRET", "envsecret")
    monkeypatch.setenv("ETSY_SHOP_ID", str(SHOP_ID))
    monkeypatch.setenv("ETSY_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("ETSY_REFRESH_TOKEN", "")
    c = EtsyClient(FakeEtsySession(SHOP_ID), settings=settings)
    assert c.keystring == "envkey" and c.shop_id == SHOP_ID
    assert c.tokens.path == tmp_path / "t.json"


# --- review findings ---------------------------------------------------------------------

def test_bad_port_is_a_safety_violation(guard):
    with pytest.raises(SafetyViolation):
        guard.check("GET", "https://api.etsy.com:abc/v3/application/users/me")
    with pytest.raises(SafetyViolation):
        guard.check("GET", "https://api.etsy.com:8443/v3/application/users/me")


def test_listing_of_another_shop_is_refused_after_read(client, session):
    """A tampered publish.json could name someone else's listing: stop and forget it."""
    session.listings[777] = {"listing_id": 777, "shop_id": OTHER_SHOP, "state": "draft"}
    client.own(777)
    with pytest.raises(SafetyViolation, match="belongs to shop"):
        client.delete_listing(777)
    assert session.calls("deleteListing") == []
    with pytest.raises(SafetyViolation, match="not created by this tool"):
        client.get_listing(777)  # disowned


def test_file_upload_not_retried_on_5xx(settings, session, tmp_path):
    sleeps = []
    c = _client(settings, session, sleeps)
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF")
    lid = c.create_draft_listing({"title": "T"})["listing_id"]
    session.fail("uploadListingFile", 500)
    with pytest.raises(EtsyAPIError):
        c.upload_listing_file(lid, pdf, rank=1)
    assert len(session.calls("uploadListingFile")) == 1 and sleeps == []
    session.fail("uploadListingImage", 500)  # images use rank + overwrite: retried
    assert c.upload_listing_image(lid, pdf, rank=1)["listing_image_id"]
    assert len(session.calls("uploadListingImage")) == 2 and len(sleeps) == 1


def test_non_json_success_is_an_api_error(settings, session):
    from publisher.fake import FakeResponse

    class HtmlSession(FakeEtsySession):
        def _answer(self, op, groups, params, data):
            if op == "getSellerTaxonomyNodes":
                r = FakeResponse(200, None)
                r.content, r.text = b"<html>proxy</html>", "<html>proxy</html>"
                return r
            return super()._answer(op, groups, params, data)
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=HtmlSession(SHOP_ID))
    with pytest.raises(EtsyAPIError, match="not JSON"):
        c.get_seller_taxonomy_nodes()


def test_each_shop_reads_its_own_env_vars(settings, monkeypatch, tmp_path):
    settings = {**settings, "shops": [*settings["shops"], {
        "id": "second", "name": "Second", "etsy_shop_id_env": "ETSY_SHOP_ID_2",
        "etsy_token_file_env": "ETSY_TOKEN_FILE_2", "etsy_refresh_token_env": "ETSY_REFRESH_TOKEN_2"}]}
    monkeypatch.setenv("ETSY_API_KEYSTRING", "k")
    monkeypatch.setenv("ETSY_SHARED_SECRET", "s")
    monkeypatch.setenv("ETSY_SHOP_ID", "111")
    monkeypatch.setenv("ETSY_TOKEN_FILE", str(tmp_path / "one.json"))
    monkeypatch.setenv("ETSY_SHOP_ID_2", "222")
    monkeypatch.setenv("ETSY_TOKEN_FILE_2", str(tmp_path / "two.json"))
    monkeypatch.setenv("ETSY_REFRESH_TOKEN_2", "2.ci")
    first = EtsyClient(FakeEtsySession(111), settings=settings)
    second = EtsyClient(FakeEtsySession(222), settings=settings, shop_key="second")
    assert (first.shop_key, first.shop_id, first.tokens.path.name) == ("main", 111, "one.json")
    assert (second.shop_key, second.shop_id, second.tokens.path.name) == ("second", 222, "two.json")
    assert second.tokens.env_refresh_token == "2.ci"
    with pytest.raises(KeyError):
        EtsyClient(FakeEtsySession(1), settings=settings, shop_key="third")


def test_corrupt_token_file_asks_for_auth(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(AuthRequired, match="unreadable"):
        TokenStore(path).load()
