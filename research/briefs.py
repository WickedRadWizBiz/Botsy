"""Build the brief/v1 document for one niche (docs/CONTRACTS.md section 1).

Everything here comes from aggregated patterns: theme names are short n-grams,
keywords are keyword-tool phrases or tags shared by >= T listings, and audience
and style notes are our own templates. Nothing identifies a listing or a shop.

The document records the research stage and the thresholds it used (docs/SERVER.md
section 8). In the refined stage a history gate (research/stages.HistoryGate) also has to
pass each brief theme (seen in enough weeks, demand not falling); those briefs carry
evidence.weeks_seen and evidence.trend. Patterns use the listing and shop thresholds only.
"""
from __future__ import annotations

from research.aggregate import (Phrase, Row, Signal, Theme, collect_phrases, find_themes,
                                keyword_clusters, page_count_patterns, phrase_order,
                                price_band_patterns, select_distinct, tally)
from research.guard import Guard
from research.text import TextRules, head

ACTIVITY_TYPES = ("maze", "word_search", "tracing", "dot_to_dot")

# Our own keyword templates, used when the data has too few theme phrases.
KEYWORD_TEMPLATES = {
    "stickers": ("{t} stickers", "{t} digital stickers", "{t} sticker set"),
    "coloring_pages": ("{t} coloring pages", "{t} coloring sheets", "printable {t} coloring"),
    "activity_sheets": ("{t} activity sheets", "{t} worksheets", "{t} printable activities"),
}
MIN_KEYWORDS = 3


def nearest(value: float, options: list[int]) -> int:
    """Closest option; on a tie the smaller one (fewer pages to generate)."""
    return min(sorted(options), key=lambda o: abs(o - value))


def pack_options(niche: str, settings: dict, cfg: dict) -> list[int]:
    opts = (settings.get("niches", {}).get(niche) or {}).get("pages_per_pack") \
        or cfg["pack_sizes"].get(niche) or [cfg["default_pack"].get(niche, 20)]
    return [int(o) for o in opts]


def theme_page_count(theme: Theme, options: list[int], default: int) -> int:
    counts = [r.count for r in theme.rows if r.count]
    if not counts:
        return nearest(default, options)
    freq: dict[int, int] = {}
    for c in counts:
        freq[c] = freq.get(c, 0) + 1
    mode = min(freq, key=lambda c: (-freq[c], c))
    return nearest(mode, options)


def theme_price(theme: Theme, default_price: float, clamp: dict) -> float:
    """Median theme price, clamped to a band around the shop default, rounded to a step."""
    price = theme.median_price if theme.median_price is not None else default_price
    lo, hi = default_price * clamp["min_factor"], default_price * clamp["max_factor"]
    price = min(max(price, lo), hi)
    step = float(clamp["step_usd"]) or 0.01
    return round(round(price / step) * step, 2)


def theme_activities(theme: Theme, allowed: list[str]) -> list[str]:
    found = set().union(*(r.activities for r in theme.rows)) if theme.rows else set()
    picked = [a for a in allowed if a in found]
    return picked or list(allowed)


def theme_keywords(theme: Theme, phrases: list[Phrase], niche: str, guard: Guard,
                   rules: TextRules, limit: int) -> list[str]:
    """Phrases containing the theme, then phrases sharing its head word, then templates."""
    direct = sorted((p for p in phrases if theme.key in p.grams), key=phrase_order)
    h = head(theme.key)
    related = sorted((p for p in phrases if h in p.grams and theme.key not in p.grams),
                     key=phrase_order)
    out: list[str] = []
    for p in direct + (related if len(direct) < MIN_KEYWORDS else []):
        if p.text not in out:
            out.append(p.text)
    if len(out) < MIN_KEYWORDS:
        for tpl in KEYWORD_TEMPLATES.get(niche, ("{t}",)):
            kw = tpl.format(t=theme.name)
            if kw not in out and guard.ok(kw) and not rules.has_excluded(kw):
                out.append(kw)
    return out[:limit]


def build_doc(niche: str, rows: list[Row], sources: list[dict], *, settings: dict, cfg: dict,
              shop: dict, shop_id: str, date: str, guard: Guard, rules: TextRules,
              own: list[Signal] = (), trends: list[Signal] = (), stage: str = "discovery",
              thresholds: dict | None = None, gate=None) -> dict:
    """The brief document. `cfg` already holds the stage's numbers
    (research.stages.apply_thresholds); `gate` is the refined history gate or None."""
    t = cfg["min_listings_per_pattern"]
    s = cfg["min_shops_per_pattern"]
    own = [s for s in own if s.niche in (None, niche)]
    trends = [s for s in trends if s.niche in (None, niche)]
    themes = find_themes(rows, t, cfg, guard, rules, own, trends)
    phrases = collect_phrases(rows, t, guard, rules, min_shops=s)
    total = len(rows)

    patterns = {
        "themes": [_theme_entry(th, total) for th in themes[: cfg["themes_in_patterns"]]],
        "formats": [{"name": n, "listing_count": c}
                    for n, c in tally(rows, lambda r: sorted(r.formats), t, s)],
        "page_counts": page_count_patterns(rows, t, cfg["page_buckets"], s),
        "price_bands": price_band_patterns(rows, t, cfg["price_bands_usd"], s),
        "keyword_clusters": keyword_clusters(themes, phrases, t, cfg["keyword_clusters"],
                                             cfg["keywords_per_cluster"], s),
    }

    default_price = float((shop.get("default_price_usd") or {}).get(niche)
                          or cfg["fallback_price_usd"])
    options = pack_options(niche, settings, cfg)
    default_pack = int(cfg["default_pack"].get(niche, options[len(options) // 2]))
    page_sizes = list((settings.get("print") or {}).get("page_sizes") or {"us_letter": {}})
    if not (settings.get("print") or {}).get("deliver_both_sizes", True):
        page_sizes = page_sizes[:1]
    allowed = [a for a in (settings.get("niches", {}).get(niche) or {}).get("types", ACTIVITY_TYPES)
               if a in ACTIVITY_TYPES]

    # The history gate runs before the overlap rule, so a refused theme never blocks an
    # overlapping theme that passes ("farm" held back must not hide "farm animals").
    candidates = [th for th in themes if gate is None or gate.check(th) is None]
    briefs = []
    for theme in select_distinct(candidates, int(cfg["briefs_per_niche"])):
        brief = {
            "brief_id": f"{niche}-{date}-{len(briefs) + 1:02d}",
            "niche": niche,
            "theme": theme.name,
            "audience": cfg["audience"].get(niche, cfg["default_audience"]),
            "keywords": theme_keywords(theme, phrases, niche, guard, rules,
                                       int(cfg["keywords_per_brief"])),
            "page_count": theme_page_count(theme, options, default_pack),
            "price_usd": theme_price(theme, default_price, cfg["price_clamp"]),
            "page_sizes": page_sizes,
            "style_notes": cfg["style_notes"].get(niche, cfg["default_style_notes"]),
            "activity_types": theme_activities(theme, allowed) if niche == "activity_sheets" else [],
            "evidence": {"listing_count": len(theme.rows), "listing_rows": theme.listing_rows,
                         "keyword_rows": theme.keyword_rows, "shop_count": theme.shop_count,
                         "score": theme.demand, **(gate.evidence(theme) if gate is not None else {})},
        }
        result = guard.blocklist.check_obj(brief)
        if result.blocked:
            continue  # contract: a brief with any blocked term is dropped
        flagged = sorted(set(result.flagged) | set(theme.flagged))
        brief["compliance"] = {"blocked": [], "flagged": flagged}
        brief["needs_review"] = bool(flagged)
        briefs.append(brief)
    for i, brief in enumerate(briefs, 1):  # keep ids contiguous after any drop
        brief["brief_id"] = f"{niche}-{date}-{i:02d}"

    doc = {
        "schema": "brief/v1",
        "niche": niche,
        "created": date,
        "shop_id": shop_id,
        "stage": stage,
        "sources": sources,
        "listing_count": total,
        "min_listings_per_pattern": t,
        "min_shops_per_pattern": s,
    }
    if stage == "refined":
        doc["min_weeks_seen"] = int((thresholds or {}).get("min_weeks_seen") or 0)
    doc.update({"patterns": patterns, "briefs": briefs})
    return doc


def _theme_entry(theme: Theme, total: int) -> dict:
    entry = {
        "name": theme.name,
        "listing_count": len(theme.rows),
        "listing_rows": theme.listing_rows,
        "keyword_rows": theme.keyword_rows,
        "shop_count": theme.shop_count,
        "share": round(len(theme.rows) / total, 3) if total else 0.0,
        "median_price_usd": theme.median_price,
        "demand": theme.demand,
    }
    if theme.needs_review:
        entry["needs_review"] = True
        entry["flagged"] = list(theme.flagged)
    return entry
