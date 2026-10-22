"""Trend web search for Hermes (user, 2026-10-21, D4.8): botsy-read's trend_search tool.

    search(query, recency, max_results, settings, *, now=None) -> dict     (never raises)
    filter_results(raw_results, settings, blocklist=None) -> (kept, dropped)
    check_query(query, settings, blocklist=None) -> reason code | None
    usage(settings, now=None) -> dict ; usage_path(settings) -> Path

Hermes's own web and search toolsets stay off. This is the only web search it has, and it goes
through TinyFish Search (GET https://api.search.tinyfish.ai, X-API-Key, free up to 12,000
requests a day; config/platform_policies.yaml, tinyfish entry). Botsy filters every result
before Hermes sees it, so no competitor listing's title, description or image reaches the
model (CLAUDE.md compliance rules):

- the query must pass the trademark blocklist and the health terms (hard, game-system and
  review terms are all refused) and may not name a marketplace;
- a result is dropped when its domain is a marketplace or listing site (any host label etsy,
  amazon / amzn, goodreads, abebooks, audible, pinterest, ebay, redbubble, teepublic, zazzle,
  creativemarket, gumroad, society6, spreadshirt, walmart, temu, aliexpress, shein, and the
  digital-product shops in MARKET_LABELS such as teacherspayteachers or creativefabrica; a host
  or path part such as media-amazon.com or /web/2020/https:/www.etsy.com/...; pin.it, a.co;
  archive, cache and translation proxies; tiktok.com/shop, google.com/shopping,
  facebook.com/marketplace; plus settings trend_search.blocked_domains), when its
  title or snippet names a marketplace, when its title or snippet looks like a product listing
  (a price with a shop cue such as "add to cart", "sold", "in stock", "free shipping", or two
  shop cues), when it or its page path hits the blocklist, or when its link is not a plain
  http(s) link (text is NFKC-normalized and zero-width characters removed before the checks);
- a kept result is only {site, page (domain + path, no query, no @handles), title, snippet,
  date}, with HTML, e-mail addresses and links taken out and lengths capped;
- results are labelled "trend reading only, never a brief by itself": a brief still needs the
  10-listing / 3-shop patterns from the user's licensed exports.

At most limits trend_searches_per_day (default 30) searches in any 24 hours, counted before the
request is sent, in a usage file next to the control state file (data/control/trend_search.json)
that also keeps a per-day counter of TinyFish use. No TINYFISH_API_KEY (environment or .env):
"not_configured". The key goes only into the request header; it is never logged or returned.
"""
from __future__ import annotations

import fcntl
import html
import json
import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from core.config import ROOT, resolve, section

log = logging.getLogger("control.trend")

ENDPOINT = "https://api.search.tinyfish.ai"          # fixed: never from settings, an order or Hermes
KEY_ENV = "TINYFISH_API_KEY"
ENV_FILE = ROOT / ".env"
LABEL = "trend reading only, never a brief by itself"
USAGE_SCHEMA = "trend-usage/v1"
DEFAULT_PER_DAY = 30
RECENCY_MINUTES = {"week": 7 * 24 * 60, "month": 30 * 24 * 60, "year": 365 * 24 * 60}
MAX_RESULTS = 10
MAX_RESPONSE_BYTES = 1_000_000
USAGE_KEEP_DAYS = 62

SETTINGS_DEFAULTS: dict = {
    "enabled": True,
    "location": "US",
    "language": "en",
    "timeout_s": 20,
    "title_max": 120,
    "snippet_max": 280,
    "blocked_domains": [],        # more listing sites (suffix match: "example.com" also drops shop.example.com)
    "usage_file": None,           # null: trend_search.json next to the control state file (data/control/)
}

# Reason codes of this module (control/tools.py describes them).
REASONS = ("not_configured", "trend_search_off", "trend_search_limit", "query_blocked", "query_needs_review",
           "query_marketplace", "search_failed", "search_rate_limited", "search_auth_failed",
           "search_allowance_used", "usage_unreadable")

# Any host label equal to one of these is a marketplace or listing site (etsy.com, shop.etsy.com,
# etsy.me, amazon.co.uk, smile.amazon.com, aws.amazon.com, ebay.de, pinterest.ca, x.gumroad.com).
MARKET_LABELS = frozenset({
    "etsy", "etsystatic", "amazon", "amzn", "goodreads", "abebooks", "audible", "pinterest", "pinimg", "ebay",
    "ebayimg", "redbubble", "teepublic", "zazzle", "creativemarket", "gumroad", "society6", "spreadshirt",
    "walmart", "temu", "aliexpress", "shein",
    # Marketplaces that sell the same digital products (review 2026-10-22): printables, stickers,
    # coloring and activity packs, planners, books.
    "teacherspayteachers", "creativefabrica", "designbundles", "thehungryjpeg", "payhip", "sellfy",
    "bigcartel", "myshopify", "cafepress", "spoonflower", "threadless", "displate", "mercari", "poshmark",
    "depop", "alibaba", "barnesandnoble", "thriftbooks", "bookdepository", "kobo"})
# Hosts that are a marketplace, a link shortener for one, or a cache / archive / translation proxy
# that can serve a marketplace page under its own host (web.archive.org/web/.../etsy.com/listing/...).
MARKET_HOSTS = frozenset({"pin.it", "a.co", "shop.tiktok.com", "shopping.google.com", "tpt.com",
                          "archive.org", "archive.ph", "archive.today", "archive.is", "archive.li", "archive.md",
                          "translate.goog", "googleusercontent.com", "cachedview.nl", "ghostarchive.org"})
MARKET_PATHS = {"tiktok.com": ("/shop",), "google.com": ("/shopping",),   # host -> path prefixes that are a shop
                "facebook.com": ("/marketplace",), "ko-fi.com": ("/s",)}
# Sent to TinyFish as exclude_domains so the 10 results are mostly useful ones (Botsy filters anyway).
EXCLUDE_DOMAINS = ("etsy.com", "amazon.com", "amazon.co.uk", "pinterest.com", "ebay.com", "redbubble.com",
                   "teepublic.com", "zazzle.com", "creativemarket.com", "gumroad.com", "goodreads.com",
                   "abebooks.com", "society6.com", "walmart.com", "temu.com", "aliexpress.com")

_MARKET_WORDS = re.compile(
    r"\b(?:etsy|amazon|amzn|kdp|goodreads|abebooks|pinterest|ebay|redbubble|teepublic|zazzle|"
    r"creative\s?market|gumroad|society6|spreadshirt|walmart|temu|aliexpress|shein|tiktok\s?shop|audible|"
    r"teachers\s?pay\s?teachers|tpt|creative\s?fabrica|design\s?bundles|hungry\s?jpeg|payhip|sellfy|big\s?cartel|"
    r"shopify|cafepress|spoonflower|threadless|displate|mercari|poshmark|depop|alibaba)\b", re.I)
_PRICE = re.compile(r"(?:[$£€¥]\s?\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*(?:\.\d{1,2})?\s?"
                    r"(?:usd|eur|gbp|cad|aud|dollars?)\b)", re.I)
_SHOP_CUES = re.compile(
    r"\b(?:add(?:ed)? to (?:cart|basket|bag)|buy (?:it )?now|in stock|out of stock|only \d+ left|"
    r"free (?:shipping|delivery|returns)|\d[\d,.]*k?\+?\s+sold|sold by|sold out|\bsold\b|best ?seller|"
    r"instant download|digital download|ships? (?:from|in)|sale price|original price|\d{1,2}% off|"
    r"\(\d[\d,]*\)\s*reviews?|\d[\d,]* reviews?|star seller|shop now)", re.I)
_TAGS = re.compile(r"<[^>]{0,500}>")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+")
_LINKISH = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b(?:[a-z0-9-]+\.)+(?:com|net|org|co|io|me|ai|uk|de|ca|au)"
                      r"(?:/\S*)?")
HOST_RE = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_SEGMENT_RE = re.compile(r"[A-Za-z0-9._~%!$&'()*+,;=:-]{1,80}")
PAGE_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?:/[A-Za-z0-9._~%!$&'()*+,;=:\[\]-]*)*")
PAGE_MAX = 160


def config(settings: dict | None) -> dict:
    return section(settings or {}, "trend_search", SETTINGS_DEFAULTS)


# ---------------------------------------------------------------- the key

def api_key() -> str | None:
    """TINYFISH_API_KEY from the environment, else from .env; None when not set or not key-like."""
    key = os.environ.get(KEY_ENV)
    if not key:
        try:
            from dotenv import dotenv_values

            if ENV_FILE.is_file():
                key = dotenv_values(ENV_FILE).get(KEY_ENV)
        except Exception as e:  # noqa: BLE001 - no key is "not configured"
            log.warning("trend search: .env not read (%s)", type(e).__name__)
            key = None
    key = (key or "").strip()
    return key if 8 <= len(key) <= 300 and re.fullmatch(r"[\x21-\x7e]+", key) else None


# ---------------------------------------------------------------- checks

def _blocklist(settings: dict):
    from core.compliance import Blocklist

    comp = settings.get("compliance") or {}
    return Blocklist.load(comp.get("trademark_blocklist") or None, health_path=comp.get("health_terms") or None)


def check_query(query: str, settings: dict, blocklist=None) -> str | None:
    """None when the query may be sent; else a reason code (never the term)."""
    if _MARKET_WORDS.search(query) or any(_market_token(w) for w in query.split()):
        return "query_marketplace"
    bl = blocklist if blocklist is not None else _blocklist(settings)
    res = bl.check(query)
    if res.blocked:
        return "query_blocked"
    if res.flagged:
        return "query_needs_review"
    return None


_PARTS = re.compile(r"[.\-_~%+]+")


def _market_token(text: str) -> bool:
    """A host or path segment with a marketplace word as one of its parts (media-amazon.com,
    etsy-com.translate.goog, /web/2020/https:/www.etsy.com/listing/...)."""
    return any(part in MARKET_LABELS for part in _PARTS.split(text.lower()) if part)


def _host_blocked(host: str, path: str, extra: tuple) -> bool:
    if any(host == h or host.endswith("." + h) for h in MARKET_HOSTS) or _market_token(host):
        return True
    if any(_market_token(seg) for seg in path.split("/") if seg):
        return True
    for h, prefixes in MARKET_PATHS.items():
        if (host == h or host.endswith("." + h)) and any(path == p or path.startswith(p + "/") for p in prefixes):
            return True
    return any(host == d or host.endswith("." + d) for d in extra)


def _extra_domains(settings: dict) -> tuple:
    raw = config(settings).get("blocked_domains") or []
    out = []
    for d in raw if isinstance(raw, list) else []:
        d = str(d).strip().lower().lstrip(".")
        if d.startswith("www."):
            d = d[4:]
        if HOST_RE.fullmatch(d):
            out.append(d)
    return tuple(out)


def strip_url(url) -> tuple[str, str] | None:
    """(host, "host/path") of a plain http(s) link: no scheme, user, port, query or fragment;
    path segments that are @handles become "@"; at most PAGE_MAX characters. None otherwise."""
    if not isinstance(url, str) or len(url) > 2000:
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not HOST_RE.fullmatch(host):
        return None
    if host.startswith("www."):
        host = host[4:]
    segs = []
    for seg in parts.path.split("/"):
        if not seg:
            continue
        if seg.startswith("@") or "@" in seg:
            segs.append("@")            # a person's handle is personal data
            continue
        if not _SEGMENT_RE.fullmatch(seg):
            break
        segs.append(seg)
    page = host + ("/" + "/".join(segs) if segs else "")
    if len(page) > PAGE_MAX:
        page = page[:PAGE_MAX].rsplit("/", 1)[0] if "/" in page[:PAGE_MAX] else host
    return host, page


def clean_text(v, limit: int) -> str:
    """Plain text: no HTML, control characters, e-mail addresses or links; one line; capped."""
    if not isinstance(v, str):
        return ""
    s = html.unescape(_TAGS.sub(" ", v[:4000]))
    s = _CTRL.sub(" ", s)
    s = _EMAIL.sub("[email]", s)
    s = _LINKISH.sub("[link]", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", " ".join(s.split()))
    if len(s) > limit:
        s = s[: max(0, limit - 1)].rsplit(" ", 1)[0] + "…"
    return s


def _plain(text: str) -> str:
    """Text as the checks see it: tags out, entities decoded, NFKC (fullwidth letters), zero-width
    and control characters out, so "Ｅｔｓｙ" or "E\u200btsy" still read as Etsy."""
    import unicodedata

    s = html.unescape(_TAGS.sub(" ", text))
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180e\u200b-\u200f\u202a-\u202e"
               r"\u2060-\u206f\ufe00-\ufe0f\ufeff]", "", s)
    return _CTRL.sub(" ", s)


def listing_like(text: str) -> bool:
    """A price with a shop cue, or two shop cues: how a product listing reads."""
    cues = len(_SHOP_CUES.findall(text))
    return cues >= 2 or (cues >= 1 and bool(_PRICE.search(text)))


def _date(v):
    return v if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]{5,15}(?:Z|[+-]\d{2}:?\d{2})?)?",
                                                   v.strip()) else None


def filter_results(raw, settings: dict, blocklist=None) -> tuple[list[dict], dict]:
    """(kept results, {reason: count dropped}). Reasons: marketplace, names_marketplace,
    listing_like, blocklist, bad_url, duplicate."""
    cfg = config(settings)
    title_max = max(20, min(int(cfg.get("title_max") or 120), 200))
    snippet_max = max(40, min(int(cfg.get("snippet_max") or 280), 500))
    extra = _extra_domains(settings)
    bl = blocklist if blocklist is not None else _blocklist(settings)
    dropped = dict.fromkeys(("marketplace", "names_marketplace", "listing_like", "blocklist", "bad_url",
                             "duplicate"), 0)
    kept, seen = [], set()
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            dropped["bad_url"] += 1
            continue
        stripped = strip_url(r.get("url"))
        if stripped is None:
            dropped["bad_url"] += 1
            continue
        host, page = stripped
        path = page[len(host):] or "/"
        if _host_blocked(host, path, extra):
            dropped["marketplace"] += 1
            continue
        raw_title = r.get("title") if isinstance(r.get("title"), str) else ""
        raw_snip = r.get("snippet") if isinstance(r.get("snippet"), str) else ""
        site_name = r.get("site_name") if isinstance(r.get("site_name"), str) else ""
        whole = " ".join((raw_title[:4000], raw_snip[:4000], site_name[:200]))
        plain = _plain(whole)
        slug = " ".join(_PARTS.split(path.replace("/", " ")))       # the page path goes to Hermes too
        if _MARKET_WORDS.search(plain) or _host_blocked_in_text(plain, extra):
            dropped["names_marketplace"] += 1
            continue
        if listing_like(plain):
            dropped["listing_like"] += 1
            continue
        res, res_slug = bl.check(plain), bl.check(slug)
        if res.blocked or res.flagged or res_slug.blocked or res_slug.flagged:
            dropped["blocklist"] += 1
            continue
        if page in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(page)
        item = {"site": host, "page": page, "title": clean_text(raw_title, title_max),
                "snippet": clean_text(raw_snip, snippet_max)}
        d = _date(r.get("date"))
        if d:
            item["date"] = d
        kept.append(item)
    return kept, dropped


def _host_blocked_in_text(text: str, extra: tuple) -> bool:
    low = text.lower()
    return any(d in low for d in extra)


# ---------------------------------------------------------------- usage file and the daily limit

def usage_path(settings: dict) -> Path:
    raw = config(settings).get("usage_file")
    if isinstance(raw, str) and raw:
        return resolve(raw)
    from service import orders

    return orders.state_path(settings).parent / "trend_search.json"


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path.with_name(path.name + ".lock")), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_usage(path: Path) -> dict | None:
    """The usage file, a fresh one when missing, None when unreadable (fail closed)."""
    if not path.exists():
        return {"schema": USAGE_SCHEMA, "searches": [], "by_day": {}, "total": 0}
    try:
        if path.is_symlink() or path.stat().st_size > 1_000_000:
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != USAGE_SCHEMA:
        return None
    doc.setdefault("searches", [])
    doc.setdefault("by_day", {})
    doc.setdefault("total", 0)
    if not isinstance(doc["searches"], list) or not isinstance(doc["by_day"], dict):
        return None
    return doc


def _parse(ts) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _recent(doc: dict, now: datetime) -> list[str]:
    cut = now - timedelta(hours=24)
    return [s for s in doc["searches"] if (t := _parse(s)) is not None and cut < t <= now + timedelta(minutes=5)]


def per_day_limit(settings: dict) -> int:
    try:
        from service import limits as limits_mod

        v = limits_mod.load(settings).get("trend_searches_per_day", DEFAULT_PER_DAY)
    except Exception as e:  # noqa: BLE001 - the shipped default
        log.warning("trend search: limits not read (%s)", type(e).__name__)
        v = DEFAULT_PER_DAY
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 1000 else DEFAULT_PER_DAY


def usage(settings: dict, now: datetime | None = None) -> dict:
    """{"searches_24h", "limit", "left", "today": {...}, "total"} (read only)."""
    now = now or datetime.now(timezone.utc)
    limit = per_day_limit(settings)
    doc = _read_usage(usage_path(settings))
    if doc is None:
        return {"ok": False, "limit": limit}
    n = len(_recent(doc, now))
    day = doc["by_day"].get(now.date().isoformat()) if isinstance(doc["by_day"].get(now.date().isoformat()), dict) \
        else {}
    return {"ok": True, "searches_24h": n, "limit": limit, "left": max(0, limit - n),
            "today": {k: v for k, v in day.items() if isinstance(v, int)}, "total": doc.get("total", 0)}


def _bump(day: dict, key: str, n: int = 1) -> None:
    v = day.get(key)
    day[key] = (v if isinstance(v, int) and not isinstance(v, bool) else 0) + n


def _reserve(settings: dict, now: datetime) -> tuple[str | None, int, int]:
    """Count one search before it is sent. (reason or None, searches in 24 h incl. this one, limit)."""
    from core.items import write_json

    limit = per_day_limit(settings)
    path = usage_path(settings)
    with _locked(path):
        doc = _read_usage(path)
        if doc is None:
            return "usage_unreadable", 0, limit
        recent = _recent(doc, now)
        if len(recent) >= limit:
            return "trend_search_limit", len(recent), limit
        recent.append(now.isoformat(timespec="seconds"))
        doc["searches"] = recent
        day = doc["by_day"].setdefault(now.date().isoformat(), {})
        if not isinstance(day, dict):
            day = doc["by_day"][now.date().isoformat()] = {}
        _bump(day, "searches")
        doc["total"] = (doc["total"] if isinstance(doc["total"], int) else 0) + 1
        keep = (now.date() - timedelta(days=USAGE_KEEP_DAYS)).isoformat()
        doc["by_day"] = {k: v for k, v in doc["by_day"].items() if isinstance(k, str) and k >= keep}
        write_json(path, doc)
        return None, len(recent), limit


def _record(settings: dict, now: datetime, outcome: str, results: int = 0, kept: int = 0) -> None:
    from core.items import write_json

    path = usage_path(settings)
    try:
        with _locked(path):
            doc = _read_usage(path)
            if doc is None:
                return
            day = doc["by_day"].setdefault(now.date().isoformat(), {})
            if isinstance(day, dict):
                _bump(day, outcome)
                _bump(day, "results", results)
                _bump(day, "kept", kept)
                write_json(path, doc)
    except OSError as e:
        log.warning("trend search: usage not recorded (%s)", type(e).__name__)


# ---------------------------------------------------------------- TinyFish

def _http_get(url: str, params: dict, headers: dict, timeout: float) -> tuple[int, object]:
    """(HTTP status, parsed JSON or None). Tests replace this; it is the only network call."""
    import requests

    with requests.get(url, params=params, headers=headers, timeout=timeout, stream=True,
                      allow_redirects=False) as r:
        body = b""
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) > MAX_RESPONSE_BYTES:
                return r.status_code, None
        try:
            return r.status_code, json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return r.status_code, None


def _status_reason(status: int, doc) -> str:
    code = str(doc.get("code") or "") if isinstance(doc, dict) else ""
    if isinstance(doc, dict) and isinstance(doc.get("error"), dict):
        code = str(doc["error"].get("code") or code)
    if status == 429:
        return "search_rate_limited"
    if status == 401:
        return "search_auth_failed"
    if status == 402 or "INSUFFICIENT" in code.upper() or "ALLOWANCE" in code.upper():
        return "search_allowance_used"
    return "search_failed"


def search(query: str, recency: str, max_results: int, settings: dict, *, now: datetime | None = None) -> dict:
    """One filtered trend search. Never raises; refusals carry one reason code."""
    now = now or datetime.now(timezone.utc)
    cfg = config(settings)
    if cfg.get("enabled") is False:
        return {"ok": False, "reasons": ["trend_search_off"]}
    try:
        bl = _blocklist(settings)
    except Exception as e:  # noqa: BLE001 - no blocklist, no search (fail closed)
        log.warning("trend search: blocklist not loaded (%s)", type(e).__name__)
        return {"ok": False, "reasons": ["query_blocked"]}
    reason = check_query(query, settings, bl)
    if reason:
        return {"ok": False, "reasons": [reason]}
    key = api_key()
    if key is None:
        return {"ok": False, "reasons": ["not_configured"]}
    try:
        reason, used, limit = _reserve(settings, now)
    except OSError as e:
        log.warning("trend search: usage file (%s)", type(e).__name__)
        return {"ok": False, "reasons": ["usage_unreadable"]}
    if reason:
        return {"ok": False, "reasons": [reason], "searches_24h": used, "limit": limit}
    params = {"query": query, "location": str(cfg.get("location") or "US")[:2],
              "language": str(cfg.get("language") or "en")[:5],
              "recency_minutes": RECENCY_MINUTES[recency], "exclude_domains": ",".join(EXCLUDE_DOMAINS)}
    headers = {"X-API-Key": key, "Accept": "application/json", "User-Agent": "Botsy-trend-search/1"}
    timeout = float(cfg.get("timeout_s") or 20)
    try:
        status, doc = _http_get(ENDPOINT, params, headers, max(1.0, min(timeout, 60.0)))
    except Exception as e:  # noqa: BLE001 - never the message: it can quote the request
        log.warning("trend search: request failed (%s)", type(e).__name__)
        _record(settings, now, "failed")
        return {"ok": False, "reasons": ["search_failed"], "searches_24h": used, "limit": limit}
    if status != 200 or not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        reason = _status_reason(status, doc) if status != 200 else "search_failed"
        log.info("trend search: HTTP %s (%s)", status, reason)
        _record(settings, now, "failed")
        return {"ok": False, "reasons": [reason], "searches_24h": used, "limit": limit}
    raw = doc["results"][:50]
    kept, dropped = filter_results(raw, settings, bl)
    kept = kept[:max_results]
    _record(settings, now, "ok", len(raw), len(kept))
    return {"ok": True, "label": LABEL, "recency": recency, "results": kept, "kept": len(kept),
            "received": len(raw), "dropped": dropped, "searches_24h": used, "limit": limit,
            "left": max(0, limit - used)}


__all__ = ["ENDPOINT", "KEY_ENV", "LABEL", "REASONS", "SETTINGS_DEFAULTS", "api_key", "check_query", "clean_text",
           "filter_results", "listing_like", "search", "strip_url", "usage", "usage_path"]
