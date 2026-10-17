"""plan_pages for stationery: which page types, layouts and wording a pack gets.

A pack mixes page types in sensible proportions for its style (generators/stationery/
catalog.py STYLE_MIX): the style's core types first (a 5-page ADHD-friendly pack is a daily
planner, brain dump, task breakdown, habit tracker and focus sessions), and bigger packs add
more types plus natural repeats (a 20-page pack has two daily layouts). About 80% of a pack
(stationery.unique_type_share) are different types, the rest repeats, each repeat a
different layout variant where one exists.

Each page's `quality` follows that ranking (rank_quality): the pack engine curates a derived
pack by page-type variety and then quality, so a 5-page pack cut from a 20-page master set
is the same core mix a fresh 5-page plan gives, not a random handful of trackers.

A design set for a theme that already has sets (ctx["used_subjects"]: a fresh pack, or a
new master after the theme cooldown) brings new layouts, not reprints: a type keeps its slot
while it still has a layout the theme has not used, a type whose layouts are all used gives
its slot to the next type in the style's order that has one, and a layout repeats (as
"version 2", with its own title) only once the theme has used every layout.

Everything is deterministic for (ctx, n, seed): the seed only picks between our own title
wordings and decoration motifs.
"""
from __future__ import annotations

import hashlib
from collections import Counter

from core.compliance import normalize
from generators.stationery.catalog import (
    CATALOG, PACK_ORDER, PAGE_TYPES, STYLE_MIX, titles_for, variant, variant_order,
)
from generators.stationery.styles import SEASONS, motifs_for, style_for

MAX_ALT = 50
LAYOUT_COUNT = sum(len(v) for v in CATALOG.values())


def _h(*parts) -> int:
    return int.from_bytes(hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()[:8], "big")


def ranked_mix(style: str, n: int, *, unique_share: float = 0.8) -> list[str]:
    """n page types for a pack of this style, most wanted first: the style's core types
    (about unique_share of the pack), then its natural repeats."""
    if n < 1:
        raise ValueError(f"a stationery pack needs at least 1 page, not {n}")
    mix = STYLE_MIX.get(style) or STYLE_MIX["minimalist"]
    core, repeats = list(mix["core"]), list(mix["repeats"])
    unique = min(len(core), n if n <= 5 else max(5, round(n * unique_share)))
    types = core[:unique]
    i = 0
    while len(types) < n:
        types.append(repeats[i % len(repeats)])
        i += 1
    return types


def page_mix(style: str, n: int, *, unique_share: float = 0.8) -> list[str]:
    """n page types for a pack of this style, in pack order (repeats next to each other)."""
    return sorted(ranked_mix(style, n, unique_share=unique_share), key=PACK_ORDER.index)


def rank_quality(position: int) -> float:
    """A page's curation weight from its place in the style's ranking: 1.0 for the most
    wanted page, a little less down the list (never below 0.7, so always above
    packs.min_quality)."""
    return round(max(0.7, 1.0 - 0.005 * position), 3)


def subject_for(page_type: str, variant_name: str, alt: int) -> str:
    base = variant(page_type, variant_name).subject
    return base if alt == 0 else f"{base}, version {alt + 1}"


def _choices(style: str, page_type: str, count: int, used: set[str]) -> list[tuple[str, int]]:
    """(variant, alt) for each occurrence of a type: the style's preferred layouts first,
    skipping layouts (and versions) already in `used`."""
    order = variant_order(style, page_type)
    out: list[tuple[str, int]] = []
    alt = 0
    while len(out) < count:
        for name in order:
            if len(out) == count:
                break
            if alt < MAX_ALT and normalize(subject_for(page_type, name, alt)) in used:
                continue
            out.append((name, alt))
        alt += 1
    return out


def _slots(style: str, n: int, share: float, used: set[str]) -> list[tuple[str, str, int]]:
    """(type, variant, alt) for every page, most wanted first."""
    ranked = ranked_mix(style, n, unique_share=share)
    if not used:
        picks = {t: iter(_choices(style, t, k, set())) for t, k in Counter(ranked).items()}
        return [(t, *next(picks[t])) for t in ranked]
    new = {t: [v for v in variant_order(style, t) if normalize(subject_for(t, v, 0)) not in used]
           for t in PAGE_TYPES}
    slots: list[tuple[str, str, int]] = []
    short: list[str] = []
    for t in ranked:                              # the usual mix, where the type has a new layout
        if new[t]:
            slots.append((t, new[t].pop(0), 0))
        else:
            short.append(t)
    order = list((STYLE_MIX.get(style) or STYLE_MIX["minimalist"])["core"])
    while short and any(new[t] for t in order):   # the next types in the style's order with one
        for t in order:
            if short and new[t]:
                slots.append((t, new[t].pop(0), 0))
                short.pop()                       # the most wanted types keep their slots longest
    taken = used | {normalize(subject_for(t, v, a)) for t, v, a in slots}
    picks = {t: iter(_choices(style, t, k, taken)) for t, k in Counter(short).items()}
    slots += [(t, *next(picks[t])) for t in short]   # every layout used: new versions
    return _by_rank(slots, ranked, order)


def _by_rank(slots: list[tuple[str, str, int]], ranked: list[str], order: list[str]) -> list[tuple[str, str, int]]:
    """Slots sorted by how wanted their page is: the k-th page of a type takes the place of
    the type's k-th slot in the usual mix, pages beyond the usual mix follow in style order."""
    places: dict[str, list[int]] = {}
    for i, t in enumerate(ranked):
        places.setdefault(t, []).append(i)
    seen: Counter = Counter()

    def place(slot) -> tuple[int, int]:
        t = slot[0]
        k = seen[t]
        seen[t] += 1
        mine = places.get(t, [])
        return (mine[k], 0) if k < len(mine) else (len(ranked) + k * len(order) + order.index(t), 0)

    keyed = [(place(s), i, s) for i, s in enumerate(slots)]
    return [s for _, _, s in sorted(keyed)]


def page_spec(page_type: str, variant_name: str, *, style: str, season: str | None = None, alt: int = 0,
              seed: str = "", index: int = 1, page_id: str | None = None) -> dict:
    """One page dict {"page_id", "type", "subject", "spec"} for a layout variant in a style."""
    v = variant(page_type, variant_name)
    motifs = motifs_for(style, season)
    titles = titles_for(style, page_type, v)
    pick = _h(seed, page_type, variant_name)
    spec = {
        "variant": variant_name,
        "alt": alt,
        "title": titles[(pick + alt) % len(titles)],
        "subtitle": v.subtitles[(pick // 7 + alt) % len(v.subtitles)],
        "style": style,
        "season": season,
        "motif": motifs[(_h(seed, "motif") + index) % len(motifs)],
        "fields": list(v.fields),
        "weekdays": v.weekdays,
        "undated": True,
    }
    return {"page_id": page_id or f"p{index:02d}", "type": page_type,
            "subject": subject_for(page_type, variant_name, alt), "spec": spec}


def plan(ctx: dict, n: int, *, seed: str, cfg: dict) -> list[dict]:
    """n distinct page specs {"page_id", "type", "subject", "spec", "quality"} for one design
    set, in pack order (month down to the day, then lists, trackers and notes)."""
    if isinstance(n, bool) or int(n) != n:
        raise ValueError(f"a stationery pack needs a whole number of pages, not {n!r}")
    choice = style_for(ctx)
    style = choice.style
    used = {normalize(s) for s in ctx.get("used_subjects") or [] if isinstance(s, str)}
    fresh = ctx.get("set_kind") == "fresh"
    slots = _slots(style, int(n), float(cfg.get("unique_type_share", 0.8)), used)
    ordered = sorted(enumerate(slots), key=lambda r: (PACK_ORDER.index(r[1][0]), r[0]))
    pages = []
    for i, (position, (page_type, name, alt)) in enumerate(ordered, 1):
        season = choice.season
        if style == "seasonal" and season is None:
            season = SEASONS[(i - 1) % len(SEASONS)]
        page = page_spec(page_type, name, style=style, season=season, alt=alt, seed=seed, index=i)
        page["quality"] = rank_quality(position)
        if fresh and alt and normalize(subject_for(page_type, name, 0)) in used:
            # Every layout of the theme is used: this "new" page is an earlier layout with
            # a new title, so a person checks the fresh pack's "all-new pages" wording.
            page["spec"]["layout_reused"] = True
        pages.append(page)
    return pages


__all__ = ["CATALOG", "page_mix", "page_spec", "plan", "rank_quality", "ranked_mix", "subject_for"]
