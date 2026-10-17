"""Build the brief/v1 document for one niche (docs/CONTRACTS.md section 1).

Everything here comes from aggregated patterns: theme names are short n-grams,
keywords are keyword-tool phrases or tags shared by >= T listings, and audience
and style notes are our own templates. Nothing identifies a listing or a shop.

The document records the research stage and the thresholds it used (docs/SERVER.md
section 8). In the refined stage a history gate (research/stages.HistoryGate) also has to
pass each brief theme (seen in enough weeks, demand not falling); those briefs carry
evidence.weeks_seen and evidence.trend. Patterns use the listing and shop thresholds only.

Themed packs (docs/PACKS.md section 2): briefs are chosen best opportunity first (demand
with little competition, research/aggregate.py), still only among themes that meet every
support and stage rule. Each brief carries `pack_sizes` (5, 20 and the top sellers' sizes
within the niche's pack bounds) with `page_count` = the largest, plus
evidence.opportunity / competition. A ttrpg brief also has `system`: the id
(config/ttrpg_systems.yaml) of the game system most of the theme's clean rows name
(>= research.system_share of them), else "generic". Only the registry id is stored, never a
system's name; the name is cut from every theme, keyword and pattern.
"""
from __future__ import annotations

import logging
import re

from research.aggregate import (Phrase, Row, Signal, Theme, by_opportunity, collect_phrases,
                                find_themes, keyword_clusters, page_count_patterns, phrase_order,
                                price_band_patterns, score_opportunity, select_distinct, tally,
                                theme_system, top_seller_sizes)
from research.defaults import DEFAULTS, pack_bounds, whole_number
from research.guard import Guard
from research.text import TextRules, head

log = logging.getLogger(__name__)

ACTIVITY_TYPES = ("maze", "word_search", "tracing", "dot_to_dot")
GENERIC_SYSTEM = "generic"
SYSTEM_NICHES = ("ttrpg",)
_SYSTEM_ID_RE = re.compile(r"[a-z0-9_]{1,40}")

# Our own keyword templates, used when the data has too few theme phrases.
KEYWORD_TEMPLATES = {
    "stickers": ("{t} stickers", "{t} digital stickers", "{t} sticker set"),
    "coloring_pages": ("{t} coloring pages", "{t} coloring sheets", "printable {t} coloring"),
    "activity_sheets": ("{t} activity sheets", "{t} worksheets", "{t} printable activities"),
    "stationery": ("{t} planner pages", "{t} printable planner", "{t} planner printable"),
    "ttrpg": ("{t} rpg sheets", "{t} character sheet", "{t} ttrpg printable"),
}
MIN_KEYWORDS = 3


def brief_pack_sizes(niche: str, top: list[int], settings: dict, cfg: dict) -> list[int]:
    """Sorted unique sizes: research.base_pack_sizes (5, 20) plus the top sellers' sizes,
    each moved inside the niche's pack bounds (settings packs.min_size / max_size)."""
    lo, hi = pack_bounds(settings, niche)
    base = cfg.get("base_pack_sizes")
    base = [n for n in (base if isinstance(base, (list, tuple)) else [base]) if (whole_number(n) or 0) > 0]
    if not base:  # empty, or no positive whole number: the PACKS.md default packs
        base = list(DEFAULTS["base_pack_sizes"])
    sizes = {min(max(whole_number(n), lo), hi) for n in [*base, *top] if (whole_number(n) or 0) > 0}
    return sorted(sizes) or [lo]


def brief_system(theme: Theme, guard: Guard, share: float, rules: TextRules | None = None,
                 known: set[str] | frozenset[str] | None = None) -> str:
    """The theme's game system id, or "generic".

    The id must be a plain id (a-z, 0-9, _) that the brief guard accepts (blocklist,
    `!` terms included, and shop names). `known` is the set of ids config/ttrpg_systems.yaml
    lists (research.pipeline.known_system_ids): the brief names only one of them, so the
    generators, listing and publisher can look it up ("dnd5e" is the registry's own key,
    which core.compliance deliberately does not match as a name). Without a registry
    (None) an id that spells a system name ("pathfinder2e") is refused too. A refused id
    is never written: the brief says "generic" and the log says why.
    """
    sid = theme_system(theme, share)
    if sid is None or sid == GENERIC_SYSTEM:
        return GENERIC_SYSTEM
    if not _SYSTEM_ID_RE.fullmatch(sid) or not guard.ok(sid):
        why = "ids must be plain a-z, 0-9, _ and name no blocklist term"
    elif known is not None and sid not in known:
        why = "config/ttrpg_systems.yaml does not list it"
    elif known is None and rules is not None and rules.names_system(sid):
        why = "it spells a game system's name and no systems registry vouches for it"
    else:
        return sid
    log.warning("ttrpg: system id %r cannot go into a brief (%s); theme %r uses %r",
                sid, why, theme.name, GENERIC_SYSTEM)
    return GENERIC_SYSTEM


def _system_mentions(texts: list[str]) -> list[str]:
    """core.compliance.system_mentions(texts), or [] when it cannot be used."""
    try:
        from core.compliance import system_mentions
    except ImportError:  # an older core.compliance: research's own cut rules are all there is
        return []
    try:
        return list(system_mentions(texts))
    except Exception as e:  # noqa: BLE001 - a broken registry must not stop research
        log.debug("system_mentions failed (%s); research's own cut rules apply", e)
        return []


def names_a_system(texts, rules: TextRules | None = None) -> set[str]:
    """The texts that name a game system: by research's own cut rules (rules.names_system)
    or by core.compliance.system_mentions (every spelling config/ttrpg_systems.yaml lists,
    with the blocklist's word rules). The last check before themes and keywords are used;
    such names are cut long before, so this only catches a spelling the cut missed. One
    system_mentions call for all texts, then one per text only when something matched."""
    texts = list(dict.fromkeys(t for t in texts if t))
    hit = {t for t in texts if rules is not None and rules.names_system(t)}
    rest = [t for t in texts if t not in hit]
    if rest and _system_mentions(rest):
        hit |= {t for t in rest if _system_mentions([t])}
    return hit


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
              thresholds: dict | None = None, gate=None, system_ids=None) -> dict:
    """The brief document. `cfg` already holds the stage's numbers
    (research.stages.apply_thresholds); `gate` is the refined history gate or None;
    `system_ids` the game system ids the registry lists (None: no registry)."""
    t = cfg["min_listings_per_pattern"]
    s = cfg["min_shops_per_pattern"]
    own = [s for s in own if s.niche in (None, niche)]
    trends = [s for s in trends if s.niche in (None, niche)]
    themes = find_themes(rows, t, cfg, guard, rules, own, trends)
    phrases = collect_phrases(rows, t, guard, rules, min_shops=s)
    # Safety net: a theme or phrase that still names a game system is never used.
    named = names_a_system([th.name for th in themes] + [p.text for p in phrases], rules)
    if named:
        log.warning("%s: %d theme(s) or keyword(s) still named a game system and were left out",
                    niche, len(named))
        themes = [th for th in themes if th.name not in named]
        phrases = [p for p in phrases if p.text not in named]
    score_opportunity(themes, rows, cfg)
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
    page_sizes = list((settings.get("print") or {}).get("page_sizes") or {"us_letter": {}})
    if not (settings.get("print") or {}).get("deliver_both_sizes", True):
        page_sizes = page_sizes[:1]
    allowed = [a for a in (settings.get("niches", {}).get(niche) or {}).get("types", ACTIVITY_TYPES)
               if a in ACTIVITY_TYPES]

    # The history gate runs before the overlap rule, so a refused theme never blocks an
    # overlapping theme that passes ("farm" held back must not hide "farm animals").
    # Every candidate already meets the support rules; among them, opportunity decides.
    candidates = by_opportunity(th for th in themes if gate is None or gate.check(th) is None)
    share = float(cfg.get("system_share") or 0.5)
    briefs = []
    for theme in select_distinct(candidates, int(cfg["briefs_per_niche"])):
        top = top_seller_sizes(theme, cfg, s)
        sizes = brief_pack_sizes(niche, top, settings, cfg)
        brief = {
            "brief_id": f"{niche}-{date}-{len(briefs) + 1:02d}",
            "niche": niche,
            "theme": theme.name,
            "audience": cfg["audience"].get(niche, cfg["default_audience"]),
            "keywords": theme_keywords(theme, phrases, niche, guard, rules,
                                       int(cfg["keywords_per_brief"])),
            "pack_sizes": sizes,
            "page_count": max(sizes),
            "price_usd": theme_price(theme, default_price, cfg["price_clamp"]),
            "page_sizes": page_sizes,
            "style_notes": cfg["style_notes"].get(niche, cfg["default_style_notes"]),
            "activity_types": theme_activities(theme, allowed) if niche == "activity_sheets" else [],
        }
        if niche in SYSTEM_NICHES:
            brief["system"] = brief_system(theme, guard, share, rules, system_ids)
        brief["evidence"] = {
            "listing_count": len(theme.rows), "listing_rows": theme.listing_rows,
            "keyword_rows": theme.keyword_rows, "shop_count": theme.shop_count,
            "score": theme.demand, "opportunity": theme.opportunity,
            "competition": theme.competition, "top_seller_sizes": top,
            **(gate.evidence(theme) if gate is not None else {})}
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
        "competition": theme.competition,
        "opportunity": theme.opportunity,
    }
    if theme.needs_review:
        entry["needs_review"] = True
        entry["flagged"] = list(theme.flagged)
    return entry
