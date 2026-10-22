"""The D4 tools (user, 2026-10-21): view_item_images (our own product images for Hermes's
vision), trend_search (TinyFish Search, every marketplace and listing-like result filtered out)
and the fixed watchdog fixes (fix_* orders through the real service.orders.submit and
service/fixes.py). Offline: TinyFish is a fake and Discord posts go to a fake."""
from __future__ import annotations

import base64
import io
import json
import os
from collections import namedtuple
from pathlib import Path

import pytest

from control import tools, trend
from service import capacity, orders
from tests.control._support import ITEM_A, ITEM_B, NOW, ctx, item, journal, mcp_call, work_root

Usage = namedtuple("Usage", "total used free")
TF_KEY = "tf-live-SECRETKEY-0123456789abcdef"
SHOP = "SecretShopAlpha"


@pytest.fixture(autouse=True)
def big_disk(monkeypatch):
    monkeypatch.setattr(capacity.shutil, "disk_usage", lambda p: Usage(10**12, 0, 10**12))


@pytest.fixture(autouse=True)
def no_env(monkeypatch, tmp_path):
    """No TinyFish key and no Discord webhook unless a test sets one; .env is a temp file."""
    monkeypatch.delenv(trend.KEY_ENV, raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    env = tmp_path / "dotenv"
    env.write_text("")
    monkeypatch.setattr(trend, "ENV_FILE", env)
    monkeypatch.setattr(tools, "ENV_FILE", env)


@pytest.fixture
def s(tmp_path):
    return ctx(tmp_path)


def _png(w, h, color=(200, 80, 40)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def _noise_png(w, h, seed=1) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.frombytes("RGB", (w, h), os.urandom(w * h * 3)).save(buf, "PNG")
    return buf.getvalue()


def pages(s, item_id, n=3, make=lambda i: _png(2400, 1800)):
    """The item's previews/page-NN.png and their manifest entries."""
    d = work_root(s) / item_id
    item(s, item_id, image=_png(2000, 1500))
    (d / "previews").mkdir(exist_ok=True)
    m = json.loads((d / "manifest.json").read_text())
    m["previews"] = []
    for i in range(1, n + 1):
        rel = f"previews/page-{i:02d}.png"
        (d / rel).write_bytes(make(i))
        m["previews"].append({"path": rel, "page": i})
    (d / "manifest.json").write_text(json.dumps(m))
    return d


def read(s, name, args=None, **kw):
    return mcp_call(s, "read", name, args, **kw)


# ---------------------------------------------------------------- view_item_images

def test_view_pages_returns_our_images_downscaled(s):
    pages(s, ITEM_A, n=3)
    data, res = read(s, "view_item_images", {"item_id": ITEM_A})
    assert data["ok"] and data["kind"] == "pages" and data["available"] == 3 and data["shown"] == 3
    assert [r["page"] for r in data["images"]] == [1, 2, 3] and data["more"] is False
    imgs = [c for c in res.content if c.type == "image"]
    assert len(imgs) == 3 and all(c.mime_type == "image/jpeg" for c in imgs)
    from PIL import Image

    with Image.open(io.BytesIO(base64.b64decode(imgs[0].data))) as im:
        assert im.format == "JPEG" and max(im.size) == 1024 and not im.info.get("exif")


def test_view_mockups_from_the_review_entry(s):
    item(s, ITEM_A, image=_png(3000, 2000), mockups=["mockups/mockup-01.jpg"])
    data, res = read(s, "view_item_images", {"item_id": ITEM_A, "kind": "mockups"})
    assert data["ok"] and data["shown"] == 1 and "page" not in data["images"][0]
    assert sum(c.type == "image" for c in res.content) == 1


def test_view_pages_falls_back_to_the_previews_folder(s):
    d = pages(s, ITEM_A, n=2)
    m = json.loads((d / "manifest.json").read_text())
    m.pop("previews")
    (d / "manifest.json").write_text(json.dumps(m))
    data, _ = read(s, "view_item_images", {"item_id": ITEM_A})
    assert data["ok"] and data["shown"] == 2


def test_view_at_most_six_and_paging(s):
    pages(s, ITEM_A, n=8, make=lambda i: _png(300, 200))
    data, res = read(s, "view_item_images", {"item_id": ITEM_A})
    assert data["shown"] == 6 and data["more"] is True and sum(c.type == "image" for c in res.content) == 6
    data, _ = read(s, "view_item_images", {"item_id": ITEM_A, "start": 6})
    assert data["shown"] == 2 and [r["page"] for r in data["images"]] == [7, 8] and data["more"] is False
    bad, res = read(s, "view_item_images", {"item_id": ITEM_A, "max": 7})
    assert res.is_error and bad["reasons"] == ["bad_argument"]


def test_view_total_bytes_capped(s, monkeypatch):
    pages(s, ITEM_A, n=4, make=lambda i: _noise_png(900, 900))
    one = len(tools._jpeg_preview(_noise_png(900, 900), tools.VIEW_PX))
    monkeypatch.setattr(tools, "VIEW_MAX_TOTAL_BYTES", int(one * 2.5))
    data, res = read(s, "view_item_images", {"item_id": ITEM_A})
    assert data["ok"] and data["shown"] == 2 and data["truncated"] is True
    assert data["bytes"] <= int(one * 2.5)


def test_view_refuses_what_is_not_a_botsy_item(s, tmp_path):
    # no such folder; the review queue folder; a folder without a manifest; a manifest of another id
    item(s, ITEM_A)
    (work_root(s) / "loose").mkdir()
    (work_root(s) / "loose" / "previews").mkdir()
    (work_root(s) / "loose" / "previews" / "page-01.png").write_bytes(_png(10, 10))
    other = work_root(s) / "copy-of-a"
    other.mkdir()
    (other / "manifest.json").write_text(json.dumps({"item_id": ITEM_A}))
    for item_id in ("2026-10-20-nothing-here", "review", "loose", "copy-of-a"):
        data, res = read(s, "view_item_images", {"item_id": item_id})
        assert res.is_error and data["reasons"] == ["not_found"], item_id
    for bad in ("../research_inputs", "a/b", "x" * 161, ".hidden"):
        data, res = read(s, "view_item_images", {"item_id": bad})
        assert res.is_error and data["reasons"] == ["bad_argument"], bad


def test_view_never_follows_links_out_of_the_item(s, tmp_path):
    inputs = Path(s["research"]["inputs_dir"])
    inputs.mkdir(parents=True, exist_ok=True)
    competitor = inputs / "competitor.png"
    competitor.write_bytes(_png(500, 500, (1, 2, 3)))
    d = pages(s, ITEM_A, n=1)
    (d / "previews" / "page-01.png").unlink()
    (d / "previews" / "page-01.png").symlink_to(competitor)
    data, res = read(s, "view_item_images", {"item_id": ITEM_A})
    assert res.is_error and data["reasons"] == ["no_preview"]
    # a whole item folder that is a link to somewhere else
    outside = tmp_path / "elsewhere" / ITEM_B
    outside.mkdir(parents=True)
    (outside / "manifest.json").write_text(json.dumps({"item_id": ITEM_B, "previews": []}))
    (work_root(s) / ITEM_B).symlink_to(outside, target_is_directory=True)
    data, res = read(s, "view_item_images", {"item_id": ITEM_B})
    assert res.is_error and data["reasons"] == ["not_found"]
    # a manifest entry that points outside its folder is never read
    m = json.loads((d / "manifest.json").read_text())
    m["previews"] = [{"path": "../../research_inputs/competitor.png", "page": 1},
                     {"path": "mockups/mockup-01.jpg", "page": 2}]
    (d / "manifest.json").write_text(json.dumps(m))
    data, res = read(s, "view_item_images", {"item_id": ITEM_A})
    assert res.is_error and data["reasons"] == ["no_preview"]


def test_view_writes_nothing(s, tmp_path):
    pages(s, ITEM_A, n=2)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    read(s, "view_item_images", {"item_id": ITEM_A})
    read(s, "view_item_images", {"item_id": ITEM_A, "kind": "mockups"})
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before


# ---------------------------------------------------------------- trend_search: the filter

def R(url, title="A post", snippet="Some words about trends.", **kw):
    return {"url": url, "title": title, "snippet": snippet, **kw}


SAMPLE = [
    R("https://www.etsy.com/listing/123/frog-stickers", "Frog Stickers - SecretShopAlpha"),
    R("https://secretshopalpha.etsy.com/", "SecretShopAlpha on Etsy"),
    R("https://ETSY.me/abc", "short link"),
    R("https://www.amazon.co.uk/dp/B000/", "Coloring Book for Adults"),
    R("https://smile.amazon.com/gp/product/1", "Planner"),
    R("https://aws.amazon.com/blogs/x", "Cloud post"),
    R("https://www.goodreads.com/book/show/1", "A book"),
    R("https://www.abebooks.co.uk/x", "Used book"),
    R("https://www.pinterest.ca/pin/1/", "Pin"),
    R("https://pin.it/xyz", "Pin short"),
    R("https://www.ebay.de/itm/1", "Sticker"),
    R("https://shop.tiktok.com/view/product/1", "Tiktok product"),
    R("https://www.tiktok.com/shop/pdp/1", "Tiktok shop page"),
    R("https://someone.gumroad.com/l/planner", "Planner template"),
    R("https://www.listingsite.example/p/1", "Configured listing site"),
    R("https://craftblog.example/2026/10/frog-trend", "Frogs are everywhere this fall",
      "Shoppers loved one Etsy seller's frog planner for $4.99 with 2,000 sold."),
    R("https://news.example/autumn", "Autumn decor", "Shop now: $12.99, free shipping, in stock."),
    R("https://deals.example/x", "Add to cart today", "In stock and ready, add to cart"),
    R("https://magazine.example/disney-trend", "Disney colouring is back", "Mickey everywhere"),
    R("ftp://files.example/x", "ftp"),
    R("javascript:alert(1)", "js"),
    R("https://trend.example/story/cozy-mushrooms?utm_source=x&email=a@b.c#frag", "Cozy <b>mushrooms</b> rise",
      "Mushroom motifs grew 40% in fall decor, says <a href='https://x.example'>a report</a>; mail me@x.example",
      date="2026-10-12"),
    R("https://medium.example/@janedoe/axolotls-are-the-new-frogs", "Axolotls are the new frogs",
      "Readers keep asking for axolotl crafts."),
    R("https://trend.example/story/cozy-mushrooms", "duplicate of the cozy mushrooms page"),
]


def test_filter_drops_marketplaces_listings_and_blocklist(s):
    s.setdefault("trend_search", {})["blocked_domains"] = ["listingsite.example"]
    kept, dropped = trend.filter_results(SAMPLE, s)
    assert [k["page"] for k in kept] == ["trend.example/story/cozy-mushrooms",
                                         "medium.example/@/axolotls-are-the-new-frogs"]
    assert dropped["marketplace"] == 15 and dropped["bad_url"] == 2 and dropped["duplicate"] == 1
    assert dropped["names_marketplace"] == 1                 # the blog quoting an Etsy price
    assert dropped["listing_like"] == 2 and dropped["blocklist"] == 1
    first = kept[0]
    assert first == {"site": "trend.example", "page": "trend.example/story/cozy-mushrooms",
                     "title": "Cozy mushrooms rise", "date": "2026-10-12",
                     "snippet": "Mushroom motifs grew 40% in fall decor, says a report; mail [email]"}
    blob = json.dumps(kept)
    for bad in ("etsy", "amazon", "SecretShopAlpha", "utm_source", "janedoe", "http", "@x.example", "#frag"):
        assert bad not in blob.lower() if bad.islower() else bad not in blob, bad


TRICKY = [
    R("https://web.archive.org/web/2020/https://www.etsy.com/listing/9/cute-frog-pack", "Cute frog pack"),
    R("https://etsy-com.translate.goog/listing/9", "Translated page"),
    R("https://webcache.googleusercontent.com/search", "Cached page"),
    R("https://m.media-amazon.com/images/I/x.jpg", "An image"),
    R("https://www.teacherspayteachers.com/Product/maze-pack-1", "Maze pack"),
    R("https://www.creativefabrica.com/product/frog-stickers/", "Frog stickers"),
    R("https://somestore.myshopify.com/products/planner", "Planner"),
    R("https://www.google.com/shopping/product/1", "Shopping"),
    R("https://www.facebook.com/marketplace/item/1", "Marketplace item"),
    R("https://news.example/fullwidth", "Sticker trends", "\uff25\uff54\uff53\uff59 sellers say frogs sell"),
    R("https://news.example/zero-width", "Sticker trends", "E\u200btsy sellers say frogs sell"),
    R("https://news.example/kids/disney-coloring-ideas", "Coloring ideas for kids", "Cozy fall themes."),
    R("https://news.example/frog-trend", "Frog trend", "Frogs are big on Teachers Pay Teachers this fall."),
    R("https://good.example/autumn-planner-ideas", "Autumn planner ideas", "Mushrooms and cozy layouts."),
]


def test_filter_tricky_hosts_paths_and_text(s):
    """Archived or proxied marketplace pages, labels joined by hyphens, digital-product shops,
    shop paths of big sites, fullwidth or zero-width spelling, a blocked term in the page path."""
    kept, dropped = trend.filter_results(TRICKY, s)
    assert [k["page"] for k in kept] == ["good.example/autumn-planner-ideas"]
    assert dropped["marketplace"] == 9 and dropped["names_marketplace"] == 3 and dropped["blocklist"] == 1


@pytest.mark.parametrize("query", ["media-amazon frogs", "shopify planner", "tpt maze", "audible books"])
def test_query_naming_a_marketplace_part_is_refused(s, query):
    assert trend.check_query(query, s) == "query_marketplace"


@pytest.mark.parametrize("text,listing", [
    ("$12.99 add to cart", True), ("In stock. Free shipping.", True), ("2,145 sold - $3.50", True),
    ("£4.20 instant download", True), ("We sold 40% more planners", False),
    ("Prices rose 3% this year", False), ("A $5 budget craft idea", False),
])
def test_listing_like(text, listing):
    assert trend.listing_like(text) is listing


def test_strip_url_and_snippet_caps(s):
    assert trend.strip_url("https://user:pw@Example.COM:8443/a/b?q=1#x") == ("example.com", "example.com/a/b")
    assert trend.strip_url("http://www.example.org") == ("example.org", "example.org")
    assert trend.strip_url("https://192.168.0.1/x") is None
    assert trend.strip_url("https://example.com/a/b c/d") == ("example.com", "example.com/a")   # cut at the odd part
    long = trend.strip_url("https://example.com/" + "/".join(["segment"] * 40))
    assert len(long[1]) <= trend.PAGE_MAX
    s.setdefault("trend_search", {})["snippet_max"] = 60
    kept, _ = trend.filter_results([R("https://ok.example/x", "t" * 500, "word " * 200)], s)
    assert len(kept[0]["snippet"]) <= 60 and len(kept[0]["title"]) <= 120


# ---------------------------------------------------------------- trend_search: the tool

class FakeTinyFish:
    def __init__(self, status=200, results=None):
        self.status, self.calls = status, []
        self.results = SAMPLE if results is None else results

    def __call__(self, url, params, headers, timeout):
        self.calls.append({"url": url, "params": dict(params), "headers": dict(headers), "timeout": timeout})
        if self.status != 200:
            return self.status, {"error": {"code": "RATE_LIMITED", "message": "see https://x.example?key=1"}}
        return 200, {"query": params["query"], "results": self.results, "total_results": len(self.results),
                     "page": 0}


@pytest.fixture
def fake(monkeypatch):
    f = FakeTinyFish()
    monkeypatch.setattr(trend, "_http_get", f)
    return f


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(trend.KEY_ENV, TF_KEY)


def test_trend_search_filters_and_labels(s, fake, key):
    s.setdefault("trend_search", {})["blocked_domains"] = ["listingsite.example"]
    data, res = read(s, "trend_search", {"query": "cozy mushroom decor", "recency": "week"})
    assert data["ok"] and data["label"] == "trend reading only, never a brief by itself"
    assert data["count"] == 2 and data["received"] == len(SAMPLE) and data["searches_24h"] == 1
    assert data["limit"] == 30 and data["left"] == 29
    # the domain + path survives the last scrub (it is not a file path), nothing else does
    assert data["results"][0]["page"] == "trend.example/story/cozy-mushrooms"
    assert not [c for c in res.content if c.type == "image"]
    (call,) = fake.calls
    assert call["url"] == "https://api.search.tinyfish.ai" and call["headers"]["X-API-Key"] == TF_KEY
    assert call["params"]["query"] == "cozy mushroom decor" and call["params"]["recency_minutes"] == 10080
    assert "etsy.com" in call["params"]["exclude_domains"]
    blob = json.dumps(data) + "".join(c.text for c in res.content if c.type == "text")
    for bad in (TF_KEY, SHOP, "etsy.com/listing", "https://", "amazon"):
        assert bad not in blob, bad


def test_trend_usage_counter_in_data_control(s, fake, key):
    s.setdefault("trend_search", {})["blocked_domains"] = ["listingsite.example"]
    read(s, "trend_search", {"query": "axolotl crafts"})
    read(s, "trend_search", {"query": "frog crafts", "recency": "year"})
    p = Path(s["control"]["state_file"]).parent / "trend_search.json"
    doc = json.loads(p.read_text())
    day = doc["by_day"][NOW.date().isoformat()]
    assert doc["schema"] == "trend-usage/v1" and doc["total"] == 2 and len(doc["searches"]) == 2
    assert day["searches"] == 2 and day["ok"] == 2 and day["kept"] == 4
    assert fake.calls[1]["params"]["recency_minutes"] == 525600
    assert TF_KEY not in p.read_text() and "axolotl" not in p.read_text()       # no query, no key


@pytest.mark.parametrize("query,reason", [
    ("disney coloring pages", "query_blocked"),
    ("dungeons & dragons character sheet", "query_blocked"),
    ("blood pressure log", "query_blocked"),
    ("peter pan party", "query_needs_review"),
    ("etsy best sellers", "query_marketplace"),
    ("trending on amazon kdp", "query_marketplace"),
])
def test_trend_query_checked_before_anything_is_sent(s, fake, key, query, reason):
    data, res = read(s, "trend_search", {"query": query})
    assert res.is_error and data["reasons"] == [reason]
    assert fake.calls == [] and not (Path(s["control"]["state_file"]).parent / "trend_search.json").exists()
    assert query not in json.dumps(data)


@pytest.mark.parametrize("args", [{"query": "Cozy Mushrooms"}, {"query": "a" * 81}, {"query": "x site:etsy.com"},
                                  {"query": "ok", "recency": "day"}, {"query": "ok", "max": 11},
                                  {"query": "ok", "page": 2}, {}])
def test_trend_strict_schema(s, fake, key, args):
    data, res = read(s, "trend_search", args)
    assert res.is_error and fake.calls == []


def test_trend_without_key_is_not_configured(s, fake):
    data, res = read(s, "trend_search", {"query": "cozy mushrooms"})
    assert res.is_error and data["reasons"] == ["not_configured"] and fake.calls == []
    assert not (Path(s["control"]["state_file"]).parent / "trend_search.json").exists()


def test_trend_key_from_dotenv(s, fake, tmp_path, monkeypatch):
    env = tmp_path / "dotenv2"
    env.write_text(f"{trend.KEY_ENV}={TF_KEY}\n")
    monkeypatch.setattr(trend, "ENV_FILE", env)
    monkeypatch.setattr(tools, "ENV_FILE", env)
    data, _ = read(s, "trend_search", {"query": "cozy mushrooms"})
    assert data["ok"] and fake.calls[0]["headers"]["X-API-Key"] == TF_KEY and TF_KEY not in json.dumps(data)


def test_trend_daily_limit(s, fake, key, monkeypatch):
    for _ in range(30):
        assert trend.search("cozy mushrooms", "month", 10, s, now=NOW)["ok"]
    data, res = read(s, "trend_search", {"query": "cozy mushrooms"})
    assert res.is_error and data["reasons"] == ["trend_search_limit"] and len(fake.calls) == 30
    assert data["searches_24h"] == 30 and data["limit"] == 30
    # a day later the window has moved on
    later = NOW.replace(day=22, hour=16)
    assert trend.search("cozy mushrooms", "month", 10, s, now=later)["ok"]
    monkeypatch.setattr(trend, "per_day_limit", lambda settings: 0)
    assert trend.search("cozy mushrooms", "month", 10, s, now=later)["reasons"] == ["trend_search_limit"]


def test_trend_limit_comes_from_the_limits_file(s, fake, key, monkeypatch):
    from service import limits as limits_mod

    real = limits_mod.load

    def with_value(settings=None, **kw):
        lim = real(settings, **kw)
        return limits_mod.Limits(ok=lim.ok, values={**lim.values, "trend_searches_per_day": 1}, path=lim.path)

    monkeypatch.setattr(limits_mod, "load", with_value)
    assert read(s, "trend_search", {"query": "frogs"})[0]["ok"]
    assert read(s, "trend_search", {"query": "frogs"})[0]["reasons"] == ["trend_search_limit"]


def test_trend_http_errors_stop_without_retry(s, key, monkeypatch):
    for status, reason in ((429, "search_rate_limited"), (401, "search_auth_failed"), (402, "search_allowance_used"),
                           (403, "search_failed"), (500, "search_failed")):
        f = FakeTinyFish(status=status)
        monkeypatch.setattr(trend, "_http_get", f)
        data, res = read(s, "trend_search", {"query": "frogs"})
        assert res.is_error and data["reasons"] == [reason] and len(f.calls) == 1, status
        assert "https://" not in json.dumps(data)

    def boom(*a, **kw):
        raise ConnectionError(f"proxy said no for key {TF_KEY}")

    monkeypatch.setattr(trend, "_http_get", boom)
    data, _ = read(s, "trend_search", {"query": "frogs"})
    assert data["reasons"] == ["search_failed"] and TF_KEY not in json.dumps(data)
    day = json.loads((Path(s["control"]["state_file"]).parent / "trend_search.json").read_text())["by_day"]
    assert day[NOW.date().isoformat()]["failed"] == 6                         # counted, never retried


def test_trend_unreadable_counter_fails_closed(s, fake, key):
    p = Path(s["control"]["state_file"]).parent / "trend_search.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    data, _ = read(s, "trend_search", {"query": "frogs"})
    assert data["reasons"] == ["usage_unreadable"] and fake.calls == []


def test_trend_switched_off(s, fake, key):
    s["trend_search"] = {"enabled": False}
    assert read(s, "trend_search", {"query": "frogs"})[0]["reasons"] == ["trend_search_off"]


# ---------------------------------------------------------------- fix tools
# The real service.orders + service.fixes path (2026-10-22: the fix kinds exist, so no mock):
# the spy only records what botsy-control hands to orders.submit and then calls the real one.

@pytest.fixture
def submit_spy(monkeypatch):
    calls = []
    real = orders.submit

    def spy(order, settings=None, **kw):
        calls.append(json.loads(json.dumps(order)))
        return real(order, settings, **kw)

    monkeypatch.setattr(orders, "submit", spy)
    return calls


@pytest.fixture
def posts(monkeypatch):
    """A fake Discord that is set up and takes every post."""
    from service import notify

    got = []
    monkeypatch.setattr(notify, "send", lambda text, **kw: got.append(text) or True)
    monkeypatch.setattr(notify, "problem", lambda settings=None: None)
    return got


def _outbox(s) -> Path:
    from service.paths import service_paths

    p = service_paths(s).outbox
    p.mkdir(parents=True, exist_ok=True)
    return p


def _failed_tick(s, error="research: ConnectionError: the server could not reach the API"):
    rec = {"started": "2026-10-21T14:00:00+00:00", "finished": "2026-10-21T14:10:00+00:00", "errors": [error],
           "exit_code": 1, "run": {"ran": True, "failed": [], "exit_code": 1}}
    (_outbox(s) / "status.json").write_text(json.dumps({"schema": "status/v1", "ticks": [rec],
                                                       "last_checked": rec["finished"]}))
    return rec


@pytest.mark.parametrize("tool,args,payload,reason", [
    ("fix_retry_run", {}, {}, "no_failed_run"),
    ("fix_retry_run", {"tick_started": "2026-10-21T13:00:02+00:00"}, {"tick_started": "2026-10-21T13:00:02+00:00"},
     "no_failed_run"),
    ("fix_clear_stale_lock", {"lock": "plans"}, {"lock": "plans"}, "lock_missing"),
    ("fix_rerun_qa", {"item_id": ITEM_A}, {"item_id": ITEM_A}, "item_unknown"),
    ("fix_resend_daily_log", {}, {}, "no_daily_log"),
    ("fix_retry_notify", {}, {}, "nothing_unsent"),
])
def test_fix_tools_submit_one_order_each_and_preconditions_refuse(s, submit_spy, posts, tool, args, payload,
                                                                  reason):
    data, res = mcp_call(s, "act", tool, args)
    assert submit_spy == [{"schema": orders.SCHEMA, "kind": tool, "created_by": "hermes", "payload": payload}]
    assert res.is_error and not data["ok"] and data["kind"] == tool and reason in data["reasons"], data
    assert all(data["explain"].get(r) for r in data["reasons"]) and data["order_id"] is None
    assert posts == []                                            # a refusal is journaled, never posted
    refused = [e for e in journal(s) if e.get("kind") == "order_refused" and e.get("order_kind") == tool]
    assert refused and refused[-1]["created_by"] == "hermes"
    assert not orders.list_orders(s)


def test_fix_retry_run_applies_when_its_preconditions_hold(s, submit_spy, posts):
    rec = _failed_tick(s)
    data, res = mcp_call(s, "act", "fix_retry_run", {"tick_started": rec["started"]})
    assert not res.is_error and data["ok"] and data["status"] == "applied" and data["level"] == 2, data
    assert data["order_id"].startswith(orders.KINDS["fix_retry_run"]["prefix"] + "-")
    assert len(posts) == 1 and data["order_id"] in posts[0] and "hermes" in posts[0]
    ev = [e for e in journal(s) if e.get("kind") == "fix_applied"]
    assert ev and ev[-1]["created_by"] == "hermes" and ev[-1]["facts"]["retryable"] is True
    # events_recent shows the fix with its fixed-field facts, never error text.
    view, _ = mcp_call(s, "read", "events_recent", {"hours": 48})
    fixes_seen = [e for e in view["events"] if e["kind"] == "fix_applied"]
    assert fixes_seen and fixes_seen[-1]["fix"] == "fix_retry_run" and fixes_seen[-1]["facts"]["retryable"] is True
    assert "could not reach" not in json.dumps(view)
    # A second one is refused: a retry is already queued.
    data, res = mcp_call(s, "act", "fix_retry_run", {})
    assert res.is_error and "retry_pending" in data["reasons"]


def test_fix_retry_run_refuses_a_failure_that_names_a_non_retryable_class(s, submit_spy, posts):
    _failed_tick(s, "research: ConnectionError: wrapped BudgetExceeded: the cap was reached")
    data, res = mcp_call(s, "act", "fix_retry_run", {})
    assert res.is_error and "not_retryable" in data["reasons"] and posts == []


def test_fix_resend_daily_log_applies_and_posts(s, submit_spy, posts):
    log_file = _outbox(s) / "daily-log.txt"
    log_file.write_text("Botsy daily log\nAll quiet.\n", encoding="utf-8")
    t = NOW.timestamp() - 3600
    os.utime(log_file, (t, t))
    data, res = mcp_call(s, "act", "fix_resend_daily_log", {})
    assert data["ok"] and data["status"] == "applied", data
    assert posts[0].startswith("Botsy daily log") and any("Daily log sent again" in p for p in posts)
    data, res = mcp_call(s, "act", "fix_resend_daily_log", {})
    assert res.is_error and data["reasons"] == ["already_resent"]


def test_fixes_per_day_limit_through_botsy_control(s, submit_spy, posts):
    Path(s["control"]["limits_path"]).write_text("schema: limits/v1\nfixes_per_day: 1\n", encoding="utf-8")
    rec = _failed_tick(s)
    assert mcp_call(s, "act", "fix_retry_run", {"tick_started": rec["started"]})[0]["ok"]
    log_file = _outbox(s) / "daily-log.txt"                      # a second fix whose preconditions hold
    log_file.write_text("Botsy daily log\n", encoding="utf-8")
    os.utime(log_file, (NOW.timestamp() - 60, NOW.timestamp() - 60))
    data, res = mcp_call(s, "act", "fix_resend_daily_log", {})
    assert res.is_error and "fixes_limit" in data["reasons"], data


@pytest.mark.parametrize("tool,args", [
    ("fix_clear_stale_lock", {"lock": "tick"}), ("fix_clear_stale_lock", {}),
    ("fix_retry_run", {"tick_started": "yesterday"}), ("fix_rerun_qa", {"item_id": "../x"}),
    ("fix_resend_daily_log", {"channel": "main"}), ("fix_retry_notify", {"created_by": "user"}),
])
def test_fix_tools_bad_input_never_reaches_orders(s, submit_spy, tool, args):
    data, res = mcp_call(s, "act", tool, args)
    assert res.is_error and submit_spy == []
    assert [e for e in journal(s) if e.get("kind") == "control_refused" and e.get("tool") == tool]


def test_fix_kinds_match_service_orders():
    assert set(tools.FIX_TOOLS) <= set(tools.ORDER_KINDS)
    assert set(orders.FIX_KINDS) == set(tools.FIX_TOOLS)
    assert set(orders.KINDS) <= set(tools.ORDER_KINDS)            # every order kind is on the menu
    assert "kdp_kit_approve" in tools.LEVEL3_KINDS


def test_kdp_kit_approve_is_refused_until_the_kdp_build(s, posts):
    data, res = mcp_call(s, "act", "submit_order", {"kind": "kdp_kit_approve", "payload": {"kit_id": "undated-planner"}})
    assert res.is_error and data["reasons"] == ["kdp_not_built"] and posts == []
