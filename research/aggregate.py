"""Aggregate normalized research rows into per-niche patterns.

Support rule (compliance, CLAUDE.md): a pattern counts only rows that stand for
real listings.
- Listing-level rows (one row per competitor listing) always count. A pattern
  needs >= T distinct listing rows from >= S distinct shops (S is
  research.min_shops_per_pattern), so one seller's catalog is never a pattern.
  Shops are compared by an opaque hash of the name, never written out; rows from an
  export without shop names belong to no shop and cannot meet S > 1.
- Keyword-level rows (one row per search term, no listings) count only when the
  row's competition (number of listings matching the term) is known and >= T. A row
  with unknown competition does not count, and neither does any row of an export
  with no competition column (it shows no listings at all). A pattern needs >= T
  such distinct keyword rows.
- The two kinds are not added together to reach T. `listing_count` reports all
  supporting rows (listing rows + counted keyword rows).
- A row whose text names a hard-blocked term (a licensed-property listing) never
  counts toward T for a theme or a tag, and a theme is dropped when such rows make
  up >= blocked_cooccurrence.drop_share of all rows naming it (flagged >= flag_share).
  Theme evidence (price, page counts, demand) comes from the clean rows only.
T is settings research.min_listings_per_pattern, never below 10. Own-shop stats
and trend files only boost demand and never count toward T.

Demand score (0-1) per theme: each metric is summed over the theme's supporting
rows, then percentile-ranked across the niche's themes (pandas rank, average
ties, pct=True), so one export's scale (estimated sales vs hearts vs searches)
does not dominate another's. The score is the weighted mean of those ranks over
the metrics the niche actually has (weights in research.demand_weights, renormalized;
"support" is the row count). Own-shop and trend signals then close a small
fraction of the remaining gap to 1.0:
    demand = 1 - (1 - base) * (1 - own_shop_boost * own) * (1 - trend_boost * trend)
where `own` is the best percentile performance of our own listings that mention
the theme and `trend` is the highest interest (0-1) of a matching trend term.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import pandas as pd

from research.guard import Guard
from research.text import TextRules, contains, head

DEMAND_METRICS = ("sales", "favorites", "reviews", "search_volume")


@dataclass
class Row:
    """One counted research row, with identifying data already removed."""
    key: str                                  # opaque dedupe hash, never written out
    kind: str                                 # "listing" | "keyword"
    grams: dict[tuple[str, ...], str]
    blocked: bool = False                     # text named a hard-blocked term
    flagged: bool = False                     # text named a review-flagged term
    formats: frozenset[str] = frozenset()
    count: int | None = None                  # pages / stickers per pack, if stated
    activities: frozenset[str] = frozenset()
    phrase: str | None = None                 # keyword-tool phrase (normalized)
    tags: tuple[str, ...] = ()
    price: float | None = None
    sales: float | None = None
    favorites: float | None = None
    reviews: float | None = None
    search_volume: float | None = None
    shop: str = ""                            # opaque hash of the shop name ("" = unknown); never written out


@dataclass
class Signal:
    """An own-shop listing or trend term: n-grams plus a 0-1 strength."""
    grams: dict[tuple[str, ...], str]
    strength: float
    niche: str | None = None


@dataclass
class Theme:
    key: tuple[str, ...]
    name: str
    rows: list[Row]
    flagged: list[str] = field(default_factory=list)
    demand: float = 0.0

    @property
    def listing_rows(self) -> int:
        return sum(1 for r in self.rows if r.kind == "listing")

    @property
    def keyword_rows(self) -> int:
        return sum(1 for r in self.rows if r.kind == "keyword")

    @property
    def shop_count(self) -> int:
        return shop_count(self.rows)

    @property
    def needs_review(self) -> bool:
        return bool(self.flagged)

    @property
    def median_price(self) -> float | None:
        return median([r.price for r in self.rows])


@dataclass
class Phrase:
    text: str
    grams: dict[tuple[str, ...], str]
    rows: dict[str, Row] = field(default_factory=dict)
    search_volume: float = 0.0
    from_keyword_tool: bool = False


def shop_count(rows) -> int:
    """Distinct known shops among listing rows."""
    return len({r.shop for r in rows if r.kind == "listing" and r.shop})


def listing_supported(rows, threshold: int, min_shops: int = 1) -> bool:
    listing = [r for r in rows if r.kind == "listing"]
    return len(listing) >= threshold and (min_shops <= 1 or shop_count(listing) >= min_shops)


def supported(rows, threshold: int, min_shops: int = 1) -> bool:
    rows = list(rows)
    keyword = sum(1 for r in rows if r.kind == "keyword")
    return listing_supported(rows, threshold, min_shops) or keyword >= threshold


def median(values) -> float | None:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    return round(statistics.median(vals), 2) if vals else None


def _surface(counter: Counter) -> str:
    """Most common surface form; ties broken alphabetically for determinism."""
    return min(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]


# ---------------------------------------------------------------- themes

def find_themes(rows: list[Row], threshold: int, cfg: dict, guard: Guard, rules: TextRules,
                own: list[Signal] = (), trends: list[Signal] = ()) -> list[Theme]:
    """Supported, compliant, collapsed themes, sorted by demand (best first)."""
    index: dict[tuple[str, ...], list[Row]] = defaultdict(list)
    surfaces: dict[tuple[str, ...], Counter] = defaultdict(Counter)
    for r in rows:
        for key, surface in r.grams.items():
            index[key].append(r)
            surfaces[key][surface] += 1

    co = cfg["blocked_cooccurrence"]
    themes: dict[tuple[str, ...], Theme] = {}
    for key in sorted(index):
        support = index[key]
        clean_rows = [r for r in support if not r.blocked]
        if not supported(clean_rows, threshold, cfg["min_shops_per_pattern"]):
            continue
        name = _surface(surfaces[key])
        if not guard.ok(name) or rules.has_excluded(name):
            continue
        theme = Theme(key, name, clean_rows, flagged=list(guard.blocklist.check(name).flagged))
        blocked_share = (len(support) - len(clean_rows)) / len(support)
        flagged_share = sum(r.flagged for r in support) / len(support)
        if blocked_share >= co["drop_share"]:
            continue  # mostly built from licensed-property listings
        if blocked_share >= co["flag_share"]:
            theme.flagged.append(f"co-occurs with blocked terms in {blocked_share:.0%} of rows")
        if flagged_share >= co["flag_share"] and not theme.flagged:
            theme.flagged.append(f"co-occurs with review terms in {flagged_share:.0%} of rows")
        themes[key] = theme

    themes = collapse(themes, cfg["collapse_ratio"])
    score_demand(themes, cfg, own, trends)
    return sorted(themes, key=lambda t: (-t.demand, -len(t.rows), t.name))


def collapse(themes: dict[tuple[str, ...], Theme], ratio: float) -> list[Theme]:
    """Drop a shorter n-gram when a longer one containing it has >= ratio of its rows.

    "farm" (30 rows) is absorbed by "farm animals" (23 rows) at ratio 0.5, but
    "animals" (80 rows) stays as its own, broader theme.
    """
    absorbed = set()
    for key, theme in themes.items():
        for n in range(1, len(key)):
            for i in range(len(key) - n + 1):
                sub = key[i:i + n]
                if sub in themes and len(theme.rows) >= ratio * len(themes[sub].rows):
                    absorbed.add(sub)
    return [t for k, t in sorted(themes.items()) if k not in absorbed]


def overlaps(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    return contains(a, b) or contains(b, a)


def score_demand(themes: list[Theme], cfg: dict, own=(), trends=()) -> None:
    if not themes:
        return
    weights = cfg["demand_weights"]
    data = {"support": [float(len(t.rows)) for t in themes]}
    for m in DEMAND_METRICS:
        sums = [_sum([getattr(r, m) for r in t.rows]) for t in themes]
        if any(s is not None and s > 0 for s in sums):
            data[m] = [s or 0.0 for s in sums]
    frame = pd.DataFrame(data)
    used = [m for m in frame.columns if weights.get(m, 0) > 0] or ["support"]
    w = {m: float(weights.get(m) or 1.0) for m in used}
    ranks = frame[used].rank(method="average", pct=True)
    base = sum(ranks[m] * w[m] for m in used) / sum(w.values())
    b_own, b_trend = float(cfg["own_shop_boost"]), float(cfg["trend_boost"])
    for theme, b in zip(themes, base.tolist()):
        o = max((s.strength for s in own if theme.key in s.grams), default=0.0)
        tr = max((s.strength for s in trends
                  if theme.key in s.grams or any(contains(theme.key, g) for g in s.grams)),
                 default=0.0)
        theme.demand = round(1 - (1 - b) * (1 - b_own * o) * (1 - b_trend * tr), 3)


def _sum(values) -> float | None:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    return float(sum(vals)) if vals else None


def select_distinct(themes: list[Theme], limit: int) -> list[Theme]:
    """Best themes in order, skipping any that overlaps an already chosen one."""
    chosen: list[Theme] = []
    for t in themes:
        if len(chosen) >= limit:
            break
        if not any(overlaps(t.key, c.key) for c in chosen):
            chosen.append(t)
    return chosen


# ---------------------------------------------------------------- other patterns

def tally(rows: list[Row], labels_of, threshold: int, min_shops: int = 1) -> list[tuple[str, int]]:
    """[(label, supporting rows)] for labels that meet the support rule."""
    by_label: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        for label in labels_of(r):
            by_label[label].append(r)
    out = [(label, len(rs)) for label, rs in by_label.items() if supported(rs, threshold, min_shops)]
    return sorted(out, key=lambda x: (-x[1], x[0]))


def bucket_label(n: int, edges: list[int]) -> str:
    lo = 1
    for edge in edges:
        if n <= edge:
            return f"{lo}-{edge}"
        lo = edge + 1
    return f"{lo}+"


def page_count_patterns(rows, threshold, edges, min_shops: int = 1) -> list[dict]:
    order = {bucket_label(e, edges): i for i, e in enumerate(edges)}
    counted = tally(rows, lambda r: [bucket_label(r.count, edges)] if r.count else [], threshold, min_shops)
    return [{"bucket": b, "listing_count": n}
            for b, n in sorted(counted, key=lambda x: order.get(x[0], len(order)))]


def price_band_patterns(rows, threshold, edges, min_shops: int = 1) -> list[dict]:
    """Bands [edges[i], edges[i+1]) in USD; the last band is open-ended."""
    bands = list(zip(edges, list(edges[1:]) + [None]))

    def band_of(r: Row):
        if r.price is None or math.isnan(r.price):
            return []
        for lo, hi in bands:
            if r.price >= lo and (hi is None or r.price < hi):
                return [_band_name(lo, hi)]
        return []

    counts = dict(tally(rows, band_of, threshold, min_shops))
    out = []
    for lo, hi in bands:
        name = _band_name(lo, hi)
        if name in counts:
            out.append({"band": name, "min_usd": float(lo),
                        "max_usd": None if hi is None else float(hi), "listing_count": counts[name]})
    return out


def _band_name(lo, hi) -> str:
    return f"{lo:g}+" if hi is None else f"{lo:g}-{hi:g}"


# ---------------------------------------------------------------- keywords

def collect_phrases(rows: list[Row], threshold: int, guard: Guard, rules: TextRules,
                    max_words: int = 8, min_shops: int = 1) -> list[Phrase]:
    """Keyword-tool phrases, plus tags used by >= threshold distinct listing rows
    from >= min_shops shops.

    Keyword-tool phrases are rows of a keyword-level export. A keyword column in a
    listing-level export holds per-listing data, so the pipeline passes it as a tag
    and it needs the same >= threshold listings as any tag.

    Phrases with a URL, shop name, verbatim title, blocked or excluded term are
    dropped. Flagged phrases stay (the brief that uses one is marked for review).
    """
    phrases: dict[str, Phrase] = {}

    def add(text: str, row: Row, volume: float | None, tool: bool):
        p = phrases.get(text)
        if p is None:
            p = phrases[text] = Phrase(text, {})
        p.rows[row.key] = row
        p.from_keyword_tool |= tool
        if volume is not None and not math.isnan(volume):
            p.search_volume = max(p.search_volume, float(volume))

    for r in rows:
        if r.phrase:
            add(r.phrase, r, r.search_volume, True)
        if not r.blocked:  # tags of licensed-property listings never count toward T
            for tag in r.tags:
                add(tag, r, None, False)

    keep = []
    for text in sorted(phrases):
        p = phrases[text]
        if not p.from_keyword_tool and not listing_supported(p.rows.values(), threshold, min_shops):
            continue
        if len(text.split()) > max_words or len(text) > 60:
            continue
        if not guard.ok(text) or rules.has_excluded(text):
            continue
        p.grams = rules.grams(text).keys  # only for survivors: tag sets can be large
        if p.grams:
            keep.append(p)
    return keep


def phrase_order(p: Phrase):
    return (-p.search_volume, -len(p.rows), p.text)


def keyword_clusters(themes: list[Theme], phrases: list[Phrase], threshold: int,
                     limit: int, per_cluster: int, min_shops: int = 1) -> list[dict]:
    """Group phrases by the head word (last word) of a theme they contain.

    "farm animal coloring pages" and "barn animals" share the head "animal", so
    they land in one cluster labelled with the best theme ending in "animal".
    Generic last words are skipped ("valentines day" groups on "valentine").
    """
    clusters = []
    seen_heads = set()
    for theme in themes:
        h = head(theme.key)
        if h in seen_heads:
            continue
        seen_heads.add(h)
        members = sorted((p for p in phrases if h in p.grams), key=phrase_order)
        rows = {k: r for p in members for k, r in p.rows.items()}
        if not members or not supported(rows.values(), threshold, min_shops):
            continue
        clusters.append({
            "label": theme.name,
            "keywords": [p.text for p in members[:per_cluster]],
            "listing_count": len(rows),
            "shop_count": shop_count(rows.values()),
            "search_volume": int(round(sum(p.search_volume for p in members))),
        })
    clusters.sort(key=lambda c: (-c["search_volume"], -c["listing_count"], c["label"]))
    return clusters[:limit]
