"""Read research exports (eRank, EverBee and similar) into one normalized table.

The user downloads CSVs from a licensed research tool into data/research_inputs/.
We have no sample yet, so matching is liberal: headers are compared after
lowercasing and dropping spaces and punctuation ("Avg. Searches" == "avg searches"
== "AVG_SEARCHES"), then a few keyword rules catch headers no alias lists.
settings research.column_overrides wins over both.

Header spellings checked against eRank's help center (hc.erank.com, "Keyword Tool"):
Keyword Ideas tab has Keywords, Avg. Searches, Avg. Clicks, Avg. CTR, Etsy Competition
(number of matching listings), KD, Tag Occurrences, Chars., Google Searches; the Top
Listings tab has Listing, Age (Days), Views, Daily Views, Est. Sales, Price,
Est. Revenue, Hearts, Tags. EverBee does not publish its export headers, so its
aliases below are plausible spellings to confirm against a real export.

Identifying columns (shop_name, url, listing_id, image) are read only so the
pipeline can dedupe rows and capture shop names for the compliance check; they are
dropped right after that (see research.pipeline).
"""
from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from core.compliance import normalize

NUMERIC = ("search_volume", "clicks", "ctr", "competition", "price", "sales",
           "revenue", "favorites", "reviews", "views")
TEXT = ("keyword", "title", "tags", "category")
IDENTIFYING = ("shop_name", "url", "listing_id", "image")
CANONICAL = TEXT + NUMERIC + ("listing_age",) + IDENTIFYING
LISTING_METRICS = ("price", "sales", "revenue", "favorites", "reviews", "views")

# canonical column -> header spellings, best first. When two headers map to the
# same column the earlier alias wins (monthly sales beat lifetime sales).
ALIASES: dict[str, tuple[str, ...]] = {
    "keyword": ("keyword", "keywords", "keyword phrase", "search term", "search terms",
                "search query", "query", "phrase", "tag", "term"),
    "search_volume": ("avg searches", "average searches", "avg monthly searches",
                      "monthly searches", "etsy searches", "avg etsy searches",
                      "search volume", "etsy search volume", "est searches", "searches",
                      "volume"),
    "clicks": ("avg clicks", "average clicks", "monthly clicks", "etsy clicks", "clicks"),
    "ctr": ("avg ctr", "average ctr", "ctr", "click through rate", "clickthrough rate",
            "click rate"),
    "competition": ("etsy competition", "competition", "competing listings",
                    "number of listings", "total listings", "listing count", "listings",
                    "results", "total results", "competitors"),
    "title": ("title", "listing title", "product title", "product name", "listing name",
              "item title", "item name", "listing", "product", "name"),
    "price": ("price", "price usd", "listing price", "current price", "sale price",
              "avg price", "average price"),
    "sales": ("mo sales", "monthly sales", "est mo sales", "est monthly sales",
              "estimated monthly sales", "sales per month", "30 day sales", "est sales",
              "estimated sales", "sales", "total sales", "est total sales", "lifetime sales"),
    "revenue": ("mo revenue", "monthly revenue", "est mo revenue", "est monthly revenue",
                "estimated monthly revenue", "est revenue", "estimated revenue", "revenue",
                "total revenue", "est total revenue"),
    "favorites": ("favorites", "favourites", "hearts", "total favorites", "num favorers",
                  "favorers", "likes", "saves"),
    "reviews": ("reviews", "listing reviews", "review count", "total reviews",
                "num reviews", "number of reviews", "ratings"),
    "views": ("views", "total views", "listing views", "monthly views", "mo views",
              "daily views", "visits"),
    "listing_age": ("listing age", "age days", "listing age days", "listing age months",
                    "age", "date created", "created", "creation date",
                    "original creation date", "listed", "listed date", "first listed"),
    "tags": ("tags", "listing tags", "tag list"),
    "category": ("category", "categories", "listing category", "taxonomy",
                 "taxonomy path"),
    "shop_name": ("shop name", "shop", "store", "store name", "seller", "seller name",
                  "shop title"),
    "url": ("url", "link", "listing url", "listing link", "product url", "product link",
            "etsy url", "etsy link", "href", "permalink"),
    "listing_id": ("listing id", "listingid", "etsy listing id", "product id",
                   "listing number", "id"),
    "image": ("image", "image url", "images", "main image", "thumbnail", "thumbnail url",
              "photo", "picture", "img", "image 1"),
}

# Fallback rules for headers no alias matched, tried in order on the spaced header.
FALLBACK_RULES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (c, re.compile(rx)) for c, rx in (
        ("url", r"\b(url|link|href|permalink)s?\b"),
        ("image", r"\b(image|img|thumbnail|photo|picture)s?\b"),
        ("listing_id", r"\blisting (id|number|no)\b"),
        ("shop_name", r"^(shop|store|seller)( name| title)?$"),
        ("ctr", r"\bctr\b|\bclick ?through\b"),
        ("clicks", r"\bclicks?\b"),
        ("competition", r"\bcompet"),
        ("search_volume", r"^(?!.*\b(trend|terms?|query|phrase)\b).*\b(search(es)?|volume)\b"),
        ("revenue", r"\b(revenue|income|earnings)\b"),
        ("sales", r"\b(sales?|sold|orders?)\b"),
        ("favorites", r"\b(favou?rites?|favorers?|hearts?|likes?)\b"),
        ("reviews", r"\b(reviews?|ratings?)\b"),
        ("views", r"\b(views?|visits?)\b"),
        ("price", r"\b(price|cost)\b"),
        ("listing_age", r"\b(age|created|listed)\b"),
        ("tags", r"^(listing |all |seo )?tags?( list| used)?$"),
        ("keyword", r"\b(keywords?|search terms?|query)\b"),
        ("title", r"\b(title|product|listing)\b"),
        ("category", r"\b(categor|taxonom)"),
    )
)
# Shop-level stats ("Shop Sales", "Shop Age") are not listing stats.
_SHOP_WORD = re.compile(r"\b(shop|store|seller)\b")
_SHOP_LEVEL_SKIP = set(NUMERIC) | {"title", "listing_age"}

# Header fingerprints used for tool detection (normalized headers).
TOOL_SIGNATURES = {
    "erank": {"avgsearches", "avgclicks", "avgctr", "etsycompetition", "googlesearches",
              "tagoccurrences", "chars", "kd", "hearts", "estsales", "agedays",
              "dailyviews", "searchtrend", "estrevenue"},
    "everbee": {"mosales", "morevenue", "estmosales", "estmorevenue", "totalsales",
                "listingage", "shopname", "conversionrate", "productname", "shopage",
                "totalfavorites", "estmonthlysales", "estmonthlyrevenue"},
}
KNOWN_TOOLS = ("erank", "everbee")


def norm_header(header) -> str:
    """'Avg. Searches ' -> 'avgsearches'. Also drops a UTF-8 BOM and accents."""
    return re.sub(r"[^a-z0-9]+", "", normalize(str(header)))


def spaced_header(header) -> str:
    """'Est. Mo. Sales' -> 'est mo sales' (for the fallback rules)."""
    return " ".join(re.findall(r"[a-z0-9]+", normalize(str(header))))


def _build_alias_index() -> dict[str, tuple[str, int]]:
    index: dict[str, tuple[str, int]] = {}
    for canon, aliases in ALIASES.items():
        for prio, alias in enumerate(aliases):
            key = norm_header(alias)
            if key in index and index[key][0] != canon:
                raise ValueError(f"alias {alias!r} maps to both {index[key][0]} and {canon}")
            index.setdefault(key, (canon, prio))
    return index


_ALIAS_INDEX = _build_alias_index()


def alias_for(header) -> str | None:
    """Canonical column for an exact (normalized) alias match, else None."""
    hit = _ALIAS_INDEX.get(norm_header(header))
    return hit[0] if hit else None


def map_columns(headers: list[str], overrides: dict | None = None) -> dict[str, str]:
    """Map canonical column -> original header. Overrides, then aliases, then rules."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for canon, wanted in (overrides or {}).items():
        if canon not in CANONICAL:
            raise ValueError(f"research.column_overrides: unknown column {canon!r}; "
                             f"known: {', '.join(CANONICAL)}")
        targets = {norm_header(w) for w in ([wanted] if isinstance(wanted, str) else wanted or [])}
        for h in headers:
            if h not in used and norm_header(h) in targets:
                mapping[canon] = h
                used.add(h)
                break

    best: dict[str, tuple[int, int, str]] = {}
    for pos, h in enumerate(headers):
        hit = _ALIAS_INDEX.get(norm_header(h))
        if h in used or not hit:
            continue
        used.add(h)  # an alias header never falls through to another column's rule
        canon, prio = hit
        if canon not in mapping and (canon not in best or (prio, pos) < best[canon][:2]):
            best[canon] = (prio, pos, h)
    mapping.update({canon: h for canon, (_, _, h) in best.items()})

    for h in headers:
        if h in used:
            continue
        spaced = spaced_header(h)
        for canon, rx in FALLBACK_RULES:
            if canon in mapping or (canon in _SHOP_LEVEL_SKIP and _SHOP_WORD.search(spaced)):
                continue
            if rx.search(spaced):
                mapping[canon] = h
                used.add(h)
                break
    return mapping


def detect_tool(headers: list[str], filename: str = "", preamble: str = "") -> str:
    """'erank', 'everbee' or 'unknown'. File name, then preamble text, then headers."""
    for text in (filename, preamble):
        low = normalize(text)
        for tool in KNOWN_TOOLS:
            if tool in low:
                return tool
    normed = {norm_header(h) for h in headers}
    scores = {tool: len(normed & sig) for tool, sig in TOOL_SIGNATURES.items()}
    tool, score = max(sorted(scores.items()), key=lambda kv: kv[1])
    return tool if score >= 2 else "unknown"


def detect_shape(mapping: dict[str, str]) -> str | None:
    """'listing' (one row per listing), 'keyword' (one row per search term) or None.

    A title column means listing-level data (EverBee Product Analytics, eRank Top
    Listings). Without titles, a keyword column means keyword-level data (eRank
    Keyword Tool). Anything else cannot be aggregated and is skipped.
    """
    if "title" in mapping:
        return "listing"
    if "keyword" in mapping:
        return "keyword"
    return None


# ---------------------------------------------------------------- value parsing

_NA = {"", "na", "n/a", "n.a.", "none", "null", "nan", "-", "--", "---", "—", "–",
       "unknown", "?", "no data", "not available", "tbd"}
_RANGE_RE = re.compile(r"^(.*\d.*?)\s*(?:-|–|—|\bto\b)\s*(.*\d.*)$")
_STRIP_RE = re.compile(r"us\$|usd|eur|gbp|approx\.?|about|[$€£¥~≈<>=+]")
_NUM_RE = re.compile(r"^(-?[\d.,' ]*\d[\d.,' ]*)\s*(k|m|b|thousand|million)?$")
_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9}


def parse_number(value) -> float | None:
    """Parse messy export numbers: "$4.99", "1,234", "1.2k", "12%", "4,99 EUR",
    "1,000 - 5,000" (midpoint), "<20" (20). Blank, "N/A", "Unknown" -> None.
    Percentages keep their written scale ("12%" -> 12.0)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(value) else float(value)
    s = str(value).strip().lower().replace(" ", " ")
    if s in _NA:
        return None
    m = _RANGE_RE.match(s)
    if m:
        lo, hi = _parse_single(m.group(1)), _parse_single(m.group(2))
        if lo is not None and hi is not None:
            return (lo + hi) / 2
    return _parse_single(s)


def _parse_single(s: str) -> float | None:
    s = _STRIP_RE.sub("", s).replace("%", "").strip()
    m = _NUM_RE.match(s)
    if not m:
        return None
    num = _to_float(m.group(1))
    if num is None:
        return None
    return num * _MULT.get(m.group(2) or "", 1.0)


def _to_float(num: str) -> float | None:
    num = num.replace(" ", "").replace("'", "")
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):          # 1.234,56
            num = num.replace(".", "").replace(",", ".")
        else:                                        # 1,234.56
            num = num.replace(",", "")
    elif "," in num:
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", num):  # 1,234 / 12,345,678
            num = num.replace(",", "")
        else:                                        # 4,99
            num = num.replace(",", ".")
    elif num.count(".") > 1:                          # 1.234.567
        num = num.replace(".", "")
    try:
        return float(num)
    except ValueError:
        return None


_AGE_UNITS = {"d": 1, "day": 1, "days": 1, "w": 7, "wk": 7, "wks": 7, "week": 7, "weeks": 7,
              "m": 30.4, "mo": 30.4, "mos": 30.4, "month": 30.4, "months": 30.4,
              "y": 365, "yr": 365, "yrs": 365, "year": 365, "years": 365}
_AGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")


def parse_age_days(value, header: str = "") -> float | None:
    """Listing age in days from "120", "3 months", "1y 2mo". A bare number uses the
    header's unit ("Listing Age (Months)"), else days (eRank's "Age (Days)")."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in _NA:
        return None
    parts = [(float(n), _AGE_UNITS.get(u)) for n, u in _AGE_RE.findall(s)]
    if parts and all(f is not None for _, f in parts):
        return round(sum(n * f for n, f in parts), 1)
    num = parse_number(s)
    if num is None:
        return None
    unit = spaced_header(header)
    factor = 365 if "year" in unit else 30.4 if "month" in unit else 7 if "week" in unit else 1
    return round(num * factor, 1)


# ---------------------------------------------------------------- CSV reading

DELIMITERS = (",", ";", "\t", "|")
MAX_PREAMBLE_LINES = 30


def decode(data: bytes) -> tuple[str, str]:
    """Bytes -> (text, encoding). UTF-16 BOM, UTF-8 (BOM stripped), cp1252, latin-1."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def _cells(line: str, delim: str) -> list[str]:
    try:
        return next(csv.reader([line], delimiter=delim))
    except (csv.Error, StopIteration):
        return []


def find_header(lines: list[str], extra: frozenset[str] = frozenset()) -> tuple[int, str]:
    """(line index, delimiter) of the header row, skipping preamble lines.

    The header is the line (within the first 30) whose cells match the most known
    aliases (plus `extra` normalized names), needing at least two. Without such a
    line, the first line with two or more cells is used.
    """
    best = (1, -1, ",")  # (score, index, delimiter); score must beat 1
    for i, line in enumerate(lines[:MAX_PREAMBLE_LINES]):
        for delim in DELIMITERS:
            cells = _cells(line, delim)
            if len(cells) < 2:
                continue
            score = sum(1 for c in cells if norm_header(c) in _ALIAS_INDEX or norm_header(c) in extra)
            if score > best[0]:
                best = (score, i, delim)
    if best[1] >= 0:
        return best[1], best[2]
    for i, line in enumerate(lines[:MAX_PREAMBLE_LINES]):
        counts = {d: len(_cells(line, d)) for d in DELIMITERS}
        delim = max(DELIMITERS, key=lambda d: counts[d])
        if counts[delim] >= 2:
            return i, delim
    return 0, ","


@dataclass
class RawTable:
    frame: pd.DataFrame      # all cells as strings ("" when missing), original headers
    preamble: str
    encoding: str
    text: str = ""           # the decoded file, for scans that must see every cell


def read_raw(path: str | Path, extra_headers: frozenset[str] = frozenset()) -> RawTable:
    """Read a CSV of unknown encoding, delimiter and preamble into string cells."""
    import io

    text, encoding = decode(Path(path).read_bytes())
    lines = text.splitlines(keepends=True)
    idx, delim = find_header(lines, extra_headers)
    body = "".join(lines[idx:])
    if not body.strip():
        return RawTable(pd.DataFrame(), "".join(lines[:idx]), encoding, text)
    frame = pd.read_csv(io.StringIO(body), sep=delim, dtype=str, keep_default_na=False,
                        na_filter=False, skip_blank_lines=True, on_bad_lines="skip",
                        engine="python")
    # A row with fewer cells than the header leaves NaN even with na_filter=False.
    # pandas 3 keeps it as NaN through astype(str) (a float in a text column) and
    # pandas 2 turns it into the text "nan"; both break text handling, so blank it.
    frame = frame.fillna("")
    frame.columns = _unique([str(c).strip() for c in frame.columns])
    return RawTable(frame, "".join(lines[:idx]), encoding, text)


def _unique(headers: list[str]) -> list[str]:
    """Stripping can make two headers equal ("Price" and "Price "); keep them apart."""
    seen: dict[str, int] = {}
    out = []
    for h in headers:
        n = seen.get(h, 0)
        seen[h] = n + 1
        out.append(h if n == 0 else f"{h}.{n}")
    return out


@dataclass
class Export:
    """One research export, normalized to canonical columns."""
    path: Path
    frame: pd.DataFrame          # canonical columns only
    mapping: dict[str, str]      # canonical -> original header
    tool: str
    shape: str | None
    shop_names: set[str] = field(default_factory=set)  # from etsy.com/shop/<name> links in any cell


# Etsy shop links ("etsy.com/shop/Name", "etsy.com/de/shop/Name") anywhere in the file,
# including columns no alias maps (e.g. "Shop URL" when "Listing URL" took the url slot).
SHOP_URL_RE = re.compile(r"etsy\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?shop/([A-Za-z0-9]+)", re.I)


_JUNK_ROWS = {"total", "totals", "summary", "average", "averages"}


def load_export(path: str | Path, overrides: dict | None = None,
                source_tool: str = "auto") -> Export:
    """Read a CSV and return canonical columns: numbers parsed, text stripped."""
    path = Path(path)
    raw = read_raw(path)
    headers = list(raw.frame.columns)
    mapping = map_columns(headers, overrides)
    tool = detect_tool(headers, path.name, raw.preamble) if source_tool in ("auto", "", None) \
        else str(source_tool)
    data: dict[str, pd.Series] = {}
    for canon, header in mapping.items():
        col = raw.frame[header].fillna("").astype(str).str.strip()
        if canon in NUMERIC:
            data[canon] = pd.to_numeric(col.map(parse_number), errors="coerce")
        elif canon == "listing_age":
            data[canon] = pd.to_numeric(col.map(lambda v: parse_age_days(v, header)),
                                        errors="coerce")
        else:
            data[canon] = col
    frame = pd.DataFrame(data, index=raw.frame.index)
    text_cols = [c for c in ("title", "keyword") if c in frame]
    if text_cols and not frame.empty:  # agg() on zero rows returns a frame, not a series
        joined = frame[text_cols].agg(" ".join, axis=1).str.strip()
        keep = joined.ne("") & ~joined.str.lower().isin(_JUNK_ROWS)
        frame = frame[keep].reset_index(drop=True)
    shops = set(SHOP_URL_RE.findall(raw.text))
    return Export(path, frame, mapping, tool, detect_shape(mapping), shops)
