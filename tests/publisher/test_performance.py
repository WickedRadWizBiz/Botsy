"""Own-shop performance files: daily snapshot, our sales, our reviews, the quota file, the
safety rules behind them and the `stats` command (docs/CONTRACTS.md section 7)."""
from __future__ import annotations

import json
import logging
import time

import pytest

from core.items import dry_run_root
from publisher import __main__ as cli
from publisher import own_stats, reviews, sales
from publisher.auth import MemoryTokenStore
from publisher.client import EtsyClient
from publisher.fake import FAKE_SCOPES, FakeEtsyClient, FakeEtsySession, FakeResponse
from publisher.safety import Guard, SafetyViolation

from .conftest import SHOP_ID

BASE = "https://api.etsy.com/v3/application"
DAY = 86400


@pytest.fixture(autouse=True)
def _fresh_warnings():
    sales.reset_warnings()
    reviews.reset_warnings()


# --- safety -----------------------------------------------------------------------

def test_transactions_and_reviews_allowed_for_our_shop_only():
    g = Guard(SHOP_ID)
    assert g.check("GET", f"{BASE}/shops/{SHOP_ID}/transactions", params={"limit": 100, "offset": 0}).name \
        == "getShopReceiptTransactionsByShop"
    route = g.check("GET", f"{BASE}/shops/{SHOP_ID}/reviews", params={"limit": 100})
    assert route.name == "getReviewsByShop" and route.auth is False
    for path in (f"/shops/{SHOP_ID + 1}/transactions", f"/shops/{SHOP_ID + 1}/reviews", "/shops/1/reviews"):
        with pytest.raises(SafetyViolation, match="not our shop"):
            g.check("GET", BASE + path)
    with pytest.raises(SafetyViolation, match="ETSY_SHOP_ID is not configured"):
        Guard(None).check("GET", f"{BASE}/shops/{SHOP_ID}/transactions")


@pytest.mark.parametrize("method,path", [
    ("GET", f"/shops/{SHOP_ID}/receipts"),
    ("GET", f"/shops/{SHOP_ID}/receipts/123"),
    ("GET", f"/shops/{SHOP_ID}/receipts/123/transactions"),
    ("GET", f"/shops/{SHOP_ID}/receipts/123/payments"),
    ("POST", f"/shops/{SHOP_ID}/receipts/123/tracking"),
    ("GET", f"/shops/{SHOP_ID}/transactions/123"),          # a single transaction: not allowed either
    ("GET", f"/shops/{SHOP_ID}/listings/1/transactions"),
    ("GET", f"/shops/{SHOP_ID}/listings/1/reviews"),        # only the shop-level reviews route
    ("GET", "/listings/1/reviews"),
    ("DELETE", f"/shops/{SHOP_ID}/transactions"),
    ("POST", f"/shops/{SHOP_ID}/reviews"),
])
def test_receipts_and_other_shapes_are_refused(method, path):
    g = Guard(SHOP_ID, owned_listing_ids=[1])
    with pytest.raises(SafetyViolation):
        g.check(method, BASE + path)


def test_receipt_paths_are_refused_by_name_before_anything_else():
    with pytest.raises(SafetyViolation, match="buyer names, emails and addresses"):
        Guard(SHOP_ID).check("GET", f"{BASE}/shops/{SHOP_ID}/receipts")
    with pytest.raises(SafetyViolation, match="buyer"):
        Guard(SHOP_ID).check("GET", f"{BASE}/shops/{SHOP_ID}/Receipts/5")


def test_refused_performance_requests_never_reach_the_network(client, session):
    with pytest.raises(SafetyViolation):
        client.request("GET", f"/shops/{SHOP_ID + 1}/transactions")
    with pytest.raises(SafetyViolation):
        client.request("GET", f"/shops/{SHOP_ID}/receipts")
    assert session.requests == []


# --- snapshot ---------------------------------------------------------------------

def test_snapshot_has_ids_states_and_drafts_count(client, session, tmp_path, settings):
    session.listings[42] = {"listing_id": 42, "shop_id": SHOP_ID, "state": "draft", "title": "Draft &amp; co",
                            "tags": ["t&#39;s"], "quantity": 999,
                            "price": {"amount": 350, "divisor": 100, "currency_code": "USD"},
                            "original_creation_timestamp": 1_760_000_000}
    path, snap = own_stats.write_snapshot(client, tmp_path / "perf", day="2026-10-17", settings=settings,
                                          now=1_792_238_400)
    assert path == tmp_path / "perf" / "etsy" / "2026-10-17.json"
    assert snap["schema"] == "performance/v1" and snap["day"] == "2026-10-17"
    assert snap["etsy_shop_id"] == SHOP_ID and snap["active"] == 2 and snap["drafts"] == 1
    by_id = {r["listing_id"]: r for r in snap["listings"]}
    assert set(by_id) == {900000001, 900000002, 42}
    assert by_id[900000001]["state"] == "active" and by_id[900000001]["views"] == 10
    assert by_id[900000001]["num_favorers"] == 1 and by_id[900000001]["price"] == 4.0
    assert by_id[900000001]["tags"] == ["dry run", "sample"]
    assert by_id[42] == {"listing_id": 42, "state": "draft", "title": "Draft & co", "tags": ["t's"],
                         "views": None, "num_favorers": None, "created": "2025-10-09", "price": 3.5,
                         "currency": "USD", "quantity": 999}
    assert set(by_id[900000002]) == {"listing_id", "state", "title", "tags", "views", "num_favorers",
                                     "created", "price", "currency", "quantity"}
    calls = session.calls("getListingsByShop")
    assert [c["params"].get("state") for c in calls] == [None, "draft"]
    assert all(c["path"] == f"/v3/application/shops/{SHOP_ID}/listings" for c in calls)
    assert json.loads(path.read_text()) == snap


def test_snapshot_is_idempotent_per_day_and_pages(client, session, tmp_path):
    session.listings.clear()
    for i in range(130):
        session.listings[i + 1] = {"listing_id": i + 1, "state": "active", "title": f"L{i}", "views": i,
                                   "num_favorers": 0, "tags": []}
    path1, snap1 = own_stats.write_snapshot(client, tmp_path, day="2026-10-17")
    session.listings[1]["views"] = 999
    path2, snap2 = own_stats.write_snapshot(client, tmp_path, day="2026-10-17")
    assert path1 == path2 and len(list((tmp_path / "etsy").iterdir())) == 1
    assert len(snap1["listings"]) == 130 and snap2["listings"][0]["views"] == 999
    assert [c["params"]["offset"] for c in session.calls("getListingsByShop")] == [0, 100, 0, 0, 100, 0]


def test_snapshot_day_is_new_york_time(settings):
    # 2026-10-18 02:30 UTC is still 2026-10-17 22:30 in New York.
    assert own_stats.local_day(settings, now=1_792_290_600) == "2026-10-17"
    settings = dict(settings, schedule={"timezone": "UTC"})
    assert own_stats.local_day(settings, now=1_792_290_600) == "2026-10-18"


def test_write_all_writes_csv_and_snapshot_from_one_pass(client, session, tmp_path):
    rec = own_stats.write_all(client, tmp_path / "ri" / "own.csv", tmp_path / "perf", day="2026-10-17")
    assert rec["csv_rows"] == 2 and rec["active"] == 2 and rec["drafts"] == 0
    text = (tmp_path / "ri" / "own.csv").read_text()
    assert text.startswith(",".join(own_stats.COLUMNS)) and "900000001" not in text  # CSV stays id-free
    assert (tmp_path / "perf" / "etsy" / "2026-10-17.json").exists()
    assert len(session.calls("getListingsByShop")) == 2


# --- sales ------------------------------------------------------------------------

def test_sales_keep_only_four_fields_and_never_buyer_data(client, session, tmp_path, settings):
    now = time.time()
    path, doc = sales.write_sales(client, tmp_path, settings=settings, now=now)
    assert path == tmp_path / "sales" / f"{own_stats.local_day(settings, now)}.json"
    assert doc["schema"] == "sales/v1" and doc["etsy_shop_id"] == SHOP_ID and doc["complete"] is True
    assert doc["window_days"] == 90 and doc["currency"] == "USD" and len(doc["sales"]) == 2
    for sale in doc["sales"]:
        assert tuple(sale) == sales.KEPT_FIELDS
        assert sale["quantity"] == 1 and sale["price"] == 4.0 and sale["listing_id"] in session.listings
    text = path.read_text()
    for forbidden in ("buyer", "receipt", "transaction_id", "coupon", "sku", "file_data", "x.pdf", "777"):
        assert forbidden not in text
    calls = session.calls("getShopReceiptTransactionsByShop")
    assert len(calls) == 1 and calls[0]["params"] == {"limit": 100, "offset": 0} and calls[0]["has_bearer"]
    assert calls[0]["path"] == f"/v3/application/shops/{SHOP_ID}/transactions"
    assert session.calls("getShopReceipts") == [] and not any("receipt" in c["path"] for c in session.requests)


def test_sales_window_stops_paging_at_old_rows(settings, tmp_path):
    session = FakeEtsySession(SHOP_ID, demo_listings=False)
    now = 1_792_238_400
    # 250 sales, newest first: 90 inside the 90-day window (days 0-89), the rest 190+ days old.
    for i in range(250):
        session.transactions.append(session.fake_transaction(100 + i % 3, created=now - DAY * (i if i < 90 else 100 + i)))
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)
    doc = sales.fetch_sales(c, settings=settings, now=now)
    assert len(doc["sales"]) == 90 and doc["complete"] is True and doc["pages"] == 2
    # Page 2 (rows 100-199) is entirely older than the window, so page 3 is never asked for.
    assert [k["params"]["offset"] for k in session.calls("getShopReceiptTransactionsByShop")] == [0, 100]
    assert doc["sales"] == sorted(doc["sales"], key=lambda s: (s["day"], s["listing_id"]))
    assert all(s["day"] >= doc["since"] for s in doc["sales"])


def test_sales_window_is_exactly_keep_days_local_days(settings):
    session = FakeEtsySession(SHOP_ID, demo_listings=False)
    now = 1_792_238_400  # 2026-10-17 08:00 New York
    for d in (0, 89, 90):
        session.transactions.append(session.fake_transaction(7, created=now - DAY * d))
    doc = sales.fetch_sales(FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session),
                            settings=settings, now=now)
    assert doc["since"] == "2026-07-20" and len(doc["sales"]) == 2  # today + 89 days before it


def test_sales_oldest_first_pages_never_stop_early(settings):
    session = FakeEtsySession(SHOP_ID, demo_listings=False)
    now = 1_792_238_400
    # Oldest first: the first page is entirely outside the window, recent sales come later.
    for i in range(250):
        session.transactions.append(session.fake_transaction(7, created=now - DAY * (300 - i)))
    doc = sales.fetch_sales(FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session),
                            settings=settings, now=now)
    assert len(doc["sales"]) == 39 and doc["pages"] == 3 and doc["complete"] is True


def test_sales_and_reviews_keep_paging_when_count_is_missing(settings):
    class NoCount(FakeEtsySession):
        def request(self, method, url, **kw):
            resp = super().request(method, url, **kw)
            if isinstance(resp._body, dict):
                resp._body.pop("count", None)
            return resp

    session = NoCount(SHOP_ID, demo_listings=False)
    now = 1_792_238_400
    for i in range(150):
        session.transactions.append(session.fake_transaction(7, created=now - 60 * i))
        session.reviews.append({"listing_id": 7, "rating": 4, "created_timestamp": now - 60 * i})
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)
    assert len(sales.fetch_sales(c, settings=settings, now=now)["sales"]) == 150
    assert reviews.fetch_reviews(c, settings=settings)["count"] == 150


def test_sales_page_cap_marks_the_file_incomplete(settings):
    session = FakeEtsySession(SHOP_ID, demo_listings=False)
    now = 1_792_238_400
    for i in range(350):
        session.transactions.append(session.fake_transaction(7, created=now - 60 * i))
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)
    doc = sales.fetch_sales(c, max_pages=2, settings=settings, now=now)
    assert len(doc["sales"]) == 200 and doc["complete"] is False and doc["pages"] == 2
    assert len(session.calls("getShopReceiptTransactionsByShop")) == 2


def test_sales_skip_without_scope_and_log_once(settings, session, caplog):
    store = MemoryTokenStore({"access_token": "1.a", "refresh_token": "1.r", "expires_at": time.time() + 9999,
                              "scope": "listings_r listings_w listings_d shops_r"})
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session, token_store=store)
    assert c.has_scope("transactions_r") is False and c.has_scope("listings_r") is True
    with caplog.at_level(logging.WARNING):
        assert sales.fetch_sales(c, settings=settings) is None
        assert sales.write_sales(c, "unused", settings=settings) == (None, None)
    assert session.calls("getShopReceiptTransactionsByShop") == []
    assert [r for r in caplog.records if "transactions_r" in r.getMessage()].__len__() == 1


def test_sales_skip_on_403_and_other_errors_raise(client, session, caplog):
    session.fail("getShopReceiptTransactionsByShop", 403, times=2, body={"error": "Insufficient permissions"})
    with caplog.at_level(logging.WARNING):
        assert sales.fetch_sales(client) is None
        assert sales.fetch_sales(client) is None
    assert sum("Insufficient permissions" in r.getMessage() for r in caplog.records) == 1
    session.fail("getShopReceiptTransactionsByShop", 404)
    with pytest.raises(Exception, match="HTTP 404"):
        sales.fetch_sales(client)


def test_unknown_scope_is_none_and_the_api_decides(settings, session):
    store = MemoryTokenStore({"access_token": "1.a", "refresh_token": "1.r", "expires_at": time.time() + 9999})
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session, token_store=store)
    assert c.has_scope("transactions_r") is None
    assert len(sales.fetch_sales(c)["sales"]) == 2


def test_reduce_transaction_drops_bad_rows_and_uses_local_day(settings):
    assert sales.reduce_transaction({"listing_id": "x", "created_timestamp": 1}) == (None, None)
    assert sales.reduce_transaction({"listing_id": 5}) == (None, None)
    # 2026-10-18 02:30 UTC -> 2026-10-17 in New York; price as amount/divisor.
    sale, cur = sales.reduce_transaction({"listing_id": "5", "created_timestamp": 1_792_290_600, "quantity": "2",
                                          "price": {"amount": 1250, "divisor": 100, "currency_code": "EUR"},
                                          "buyer_user_id": 9}, settings)
    assert sale == {"listing_id": 5, "day": "2026-10-17", "quantity": 2, "price": 12.5} and cur == "EUR"


# --- reviews ----------------------------------------------------------------------

def test_reviews_keep_listing_rating_day_only(client, session, tmp_path, settings):
    path, doc = reviews.write_reviews(client, tmp_path, settings=settings)
    assert doc["schema"] == "reviews/v1" and doc["count"] == 2 and doc["etsy_shop_id"] == SHOP_ID
    for r in doc["reviews"]:
        assert tuple(r) == reviews.KEPT_FIELDS and r["rating"] == 5
    text = path.read_text()
    assert "Dry run review text" not in text and "image_url" not in text and "language" not in text
    assert "buyer" not in text and "transaction" not in text and "777" not in text
    calls = session.calls("getReviewsByShop")
    assert len(calls) == 1 and calls[0]["path"] == f"/v3/application/shops/{SHOP_ID}/reviews"
    assert calls[0]["has_api_key"] and not calls[0]["has_bearer"]  # app key only, like the spec says


def test_reviews_page_and_skip_on_403(settings, caplog):
    session = FakeEtsySession(SHOP_ID, demo_listings=False)
    for i in range(120):
        session.reviews.append({"listing_id": 1 + i % 2, "rating": 1 + i % 5, "review": "text",
                                "created_timestamp": 1_760_000_000 + i})
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)
    doc = reviews.fetch_reviews(c, settings=settings)
    assert doc["count"] == 120 and [k["params"]["offset"] for k in session.calls("getReviewsByShop")] == [0, 100]
    assert reviews.reduce_review({"listing_id": 1, "rating": "five", "created_timestamp": 1}) is None
    session.fail("getReviewsByShop", 403)
    with caplog.at_level(logging.WARNING):
        assert reviews.write_reviews(c, "unused") == (None, None)
    assert any("refused getReviewsByShop" in r.getMessage() for r in caplog.records)


# --- quota --------------------------------------------------------------------------

class QuotaSession(FakeEtsySession):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.remaining = 10000

    def request(self, method, url, **kw):
        resp = super().request(method, url, **kw)
        self.remaining -= 1
        resp.headers["x-limit-per-day"] = "10000"
        resp.headers["x-remaining-today"] = str(self.remaining)
        return resp


def test_quota_headers_and_429s_are_recorded(settings, tmp_path):
    session = QuotaSession(SHOP_ID)
    path = tmp_path / "perf" / "etsy_quota.json"
    clock = [1_792_238_400.0]
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session, quota_path=path,
                       clock=lambda: clock[0])
    c.get_me()
    c.get_listings_by_shop()  # getMe (shop check) + the listings page
    q = json.loads(path.read_text())
    assert q["schema"] == "quota/v1" and q["etsy_shop_id"] == SHOP_ID and q["day"] == "2026-10-17"
    assert q["limit_per_day"] == 10000 and q["remaining_today"] == 9997 and q["requests_today"] == 3
    assert q["rate_limited_today"] == 0 and q["rate_limited_total"] == 0
    session.fail("getListingsByShop", 429, headers={"retry-after": "1"})
    c.get_listings_by_shop()  # 429 + retry
    q = json.loads(path.read_text())
    assert q["requests_today"] == 5 and q["rate_limited_today"] == 1 and q["rate_limited_total"] == 1
    assert q["last_rate_limited"].startswith("2026-10-17")
    # A new client the next day continues the total but resets the day's counters.
    clock[0] += DAY
    c2 = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session, quota_path=path,
                        clock=lambda: clock[0])
    c2.get_me()
    q = json.loads(path.read_text())
    assert q["day"] == "2026-10-18" and q["requests_today"] == 1 and q["rate_limited_today"] == 0
    assert q["rate_limited_total"] == 1 and q["remaining_today"] == 9994


def test_quota_in_memory_by_default_for_fakes_and_file_errors_never_break_requests(client, session, tmp_path):
    assert client.quota_path is None
    client.get_me()
    assert client.quota["requests_today"] == 1 and client.quota["limit_per_day"] is None
    bad = tmp_path / "file-not-dir"
    bad.write_text("x")
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=client.settings, session=session, quota_path=bad / "q.json")
    assert c.get_me()["shop_id"] == SHOP_ID  # write fails silently


def test_real_client_default_quota_path(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("ETSY_API_KEYSTRING", "k")
    monkeypatch.setenv("ETSY_SHARED_SECRET", "s")
    monkeypatch.setenv("ETSY_SHOP_ID", str(SHOP_ID))
    monkeypatch.setenv("ETSY_TOKEN_FILE", str(tmp_path / "etsy_tokens.json"))
    c = EtsyClient(settings=settings)  # no injected session: the real network client
    assert c.quota_path.name == "etsy_quota.json" and c.quota_path.parent.name == "performance"
    # A client with an injected session (tests, fakes) never writes into data/performance.
    assert EtsyClient(FakeEtsySession(SHOP_ID), settings=settings).quota_path is None


def test_quota_file_is_shared_by_clients_of_the_same_app(settings, tmp_path):
    path = tmp_path / "etsy_quota.json"
    a = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=QuotaSession(SHOP_ID), quota_path=path)
    b = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=QuotaSession(SHOP_ID), quota_path=path)
    a.get_me()
    b.get_me()
    a.get_me()
    assert json.loads(path.read_text())["requests_today"] == 3  # neither client overwrote the other


# --- CLI ----------------------------------------------------------------------------

def test_cli_stats_dry_run_writes_under_the_dry_run_root_only(settings, tmp_path, monkeypatch, capsys):
    settings = dict(settings, pipeline=dict(settings.get("pipeline") or {}, dry_run_output_dir=str(tmp_path / "dr")))
    monkeypatch.setattr(cli, "load_settings", lambda *a, **k: settings)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["stats", "--dry-run"]) == 0
    out = capsys.readouterr().out
    root = dry_run_root(settings)
    day = own_stats.local_day(settings)
    assert (root / "performance" / "etsy" / f"{day}.json").exists()
    assert (root / "performance" / "sales" / f"{day}.json").exists()
    assert (root / "performance" / "reviews" / f"{day}.json").exists()
    assert (root / "performance" / "etsy_quota.json").exists()
    assert (root / "research_inputs" / "own_shop_stats.csv").exists()
    assert not (tmp_path / "data").exists()
    assert "own-stats (dry run): 2 active" in out and "snapshot: 2 active, 0 draft(s)" in out
    assert "sales: 2 in the window" in out and "reviews: 2 ->" in out


def test_run_stats_reports_skipped_sales(settings, tmp_path, capsys):
    session = FakeEtsySession(SHOP_ID)
    store = MemoryTokenStore({"access_token": "1.a", "refresh_token": "1.r", "expires_at": time.time() + 9999,
                              "scope": FAKE_SCOPES.replace(" transactions_r", "")})
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session, token_store=store)
    rec = cli.run_stats(settings, dry_run=True, performance_dir=tmp_path / "p", csv_path=tmp_path / "own.csv",
                        client=c)
    assert rec["sales"] is None and rec["sales_rows"] is None and rec["reviews_rows"] == 2
    assert rec["csv_rows"] == 2 and (tmp_path / "p" / "etsy").exists() and not (tmp_path / "p" / "sales").exists()
    assert rec["quota"]["requests_today"] == 4  # getMe, two listing pages, reviews


def test_cli_stats_unknown_shop_is_a_setup_error(capsys):
    assert cli.main(["stats", "--shop", "nope"]) == 2
    assert "unknown shop id 'nope'" in capsys.readouterr().err


def test_transactions_r_is_asked_for_by_auth():
    from publisher.defaults import DEFAULTS
    assert "transactions_r" in DEFAULTS["scopes"]
    assert "transactions_r" in FAKE_SCOPES
