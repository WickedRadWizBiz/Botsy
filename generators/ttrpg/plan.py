"""plan_pages for tabletop RPG packs: which page types, layouts and titles a pack gets.

A pack's focus comes from our brief's theme only (focus_for): a campaign kit by default,
or a character, maps, game-master, world-building or journal pack when the theme says so
("hex map paper" -> maps). Keywords never pick the focus: research's ttrpg keywords are
shared tags and templates ("<theme> character sheet") that would turn almost every genre
theme into a character pack. Each focus ranks page types so that every prefix is a
sensible pack:
- 5 pages (default focus): the campaign starter: character sheet, session notes, NPC
  tracker, quest log, hex map;
- 20 pages: every page type once (17), plus a second character sheet, session page and
  NPC page;
- 30 and 40 pages: second and third layouts of the most used types, then everything again.
The k-th page of a type gets that type's k-th layout variant (its focus may prefer another
order); past the last variant a layout repeats as "version 2" with a different title and
emblem (map repeats also switch line style).

Each page's `quality` follows the ranking (rank_quality), so a derived pack the engine
curates from a master set keeps the same mix (its 5 pages are the campaign starter).

A fresh design set prefers layouts the theme's other sets have not used
(ctx["used_subjects"]), so a fresh pack brings new pages. Everything is deterministic for
(ctx, n, seed); the seed only picks between our own title wordings and emblems. The
game system (ctx["system"], an id from config/ttrpg_systems.yaml) only changes which fields
the system-aware pages carry; its names are never part of a page.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter

from core.compliance import normalize
from generators.ttrpg.catalog import (
    PAGE_TYPES, SYSTEM_AWARE, TYPE_LABELS, VARIANTS, Build, subject_for, titles_for, variant,
)
from generators.ttrpg.styles import GENRES, genre_for

log = logging.getLogger(__name__)

MAX_ALT = 40

CS, SP, INV, CAMP, SESS, NPC, QUEST, INIT, ENC, TOWN, FAC, DEITY, REGION, HEX, GRID, DUNGEON, DICE = PAGE_TYPES

_REST_20 = [INV, SP, CAMP, INIT, ENC, TOWN, FAC, DEITY, REGION, GRID, DUNGEON, DICE, CS, SESS, NPC]
_REST_30 = [SP, INV, CAMP, SESS, QUEST, INIT, ENC, HEX, GRID, CS]
_REST_40 = [DUNGEON, TOWN, FAC, DEITY, REGION, DICE, INV, CAMP, NPC, SESS]

# Page types by rank: any prefix is a pack. After the list, the campaign ranking repeats.
RANKS: dict[str, list[str]] = {
    "campaign": [CS, SESS, NPC, QUEST, HEX] + _REST_20 + _REST_30 + _REST_40,
    "character": [CS, CS, SP, INV, CS, SP, INV, DICE, CS, SP, INV, QUEST, SESS, NPC, CAMP, DICE, ENC, INIT, HEX,
                  GRID, SP, INV, CS, SESS, QUEST, NPC, CAMP, TOWN, FAC, DEITY],
    "maps": [HEX, GRID, DUNGEON, HEX, GRID, DUNGEON, HEX, GRID, DUNGEON, HEX, GRID, DUNGEON, REGION, TOWN, ENC, REGION,
             TOWN, HEX, GRID, ENC, NPC, SESS, QUEST, CS, FAC, DEITY, INIT, DICE, CAMP, INV],
    "gm": [SESS, INIT, ENC, NPC, QUEST, CAMP, SESS, ENC, INIT, NPC, QUEST, CAMP, TOWN, FAC, REGION, HEX, GRID, DUNGEON,
           SESS, DEITY, CS, SP, INV, DICE, CAMP, ENC, NPC, TOWN, HEX, GRID],
    "worldbuilding": [TOWN, FAC, DEITY, REGION, HEX, TOWN, FAC, DEITY, REGION, NPC, CAMP, HEX, GRID, DUNGEON, QUEST,
                      SESS, NPC, ENC, CS, SP, INV, INIT, DICE, CAMP, SESS, HEX, GRID, DUNGEON, QUEST, NPC],
    "journal": [SESS, SESS, CAMP, NPC, QUEST, SESS, CAMP, NPC, QUEST, CS, INV, DICE, SESS, HEX, TOWN, FAC, DEITY,
                REGION, ENC, INIT, SP, GRID, DUNGEON, CS, SESS, NPC, QUEST, CAMP, INV, DICE],
}

# A focus may prefer another layout order for some types.
FOCUS_VARIANTS: dict[str, dict[str, tuple[str, ...]]] = {
    "gm": {SESS: ("prep", "structured", "lined")},
    "worldbuilding": {HEX: ("region", "medium", "small", "large", "pointy"), TOWN: ("overview", "map")},
    "journal": {CAMP: ("timeline", "overview", "party")},
    "maps": {REGION: ("map", "overview"), TOWN: ("map", "overview")},
    "character": {CS: ("core", "details", "backstory", "core_alt")},
}

# Theme words that pick a focus (longest phrase first; the default is campaign).
FOCUS_WORDS: dict[str, tuple[str, ...]] = {
    "maps": ("hex map", "hex maps", "hex paper", "hex grid", "battle map", "battle maps", "dungeon map", "dungeon maps",
             "grid paper", "graph paper", "map paper", "mapping", "maps", "map", "cartography", "hexcrawl",
             "hex crawl", "dungeon grid", "isometric"),
    "character": ("character sheet", "character sheets", "character", "characters", "player sheet", "spellbook",
                  "spell tracker", "spell", "spells", "inventory", "hero sheet"),
    "gm": ("game master", "gm", "dm", "encounter", "encounters", "initiative", "combat tracker", "game prep",
           "session prep", "gm screen", "gm notes", "planner"),
    "worldbuilding": ("worldbuilding", "world building", "world builder", "setting", "town", "towns", "city",
                      "faction", "factions", "deity", "deities", "pantheon", "region", "kingdom", "lore"),
    "journal": ("journal", "campaign journal", "session notes", "session log", "notes", "notebook", "logbook",
                "log", "diary"),
    "campaign": ("campaign", "campaign tracker", "campaign kit", "starter", "bundle", "kit", "tracker"),
}
_FOCUS_RX = sorted(((w, re.compile(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])"), f)
                    for f, ws in FOCUS_WORDS.items() for w in ws), key=lambda t: (-len(t[0]), t[0]))

STARTER = (CS, SESS, NPC, QUEST, HEX)
# Pack order: how pages read in a pack (character pages first, maps last).
PACK_ORDER = PAGE_TYPES


def _h(*parts) -> int:
    return int.from_bytes(hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()[:8], "big")


def _clean(text) -> str:
    if isinstance(text, (list, tuple)):
        text = " , ".join(str(t) for t in text)
    return re.sub(r"[^a-z0-9]+", " ", normalize(str(text or ""))).strip()


def focus_for(ctx: dict | None) -> str:
    """The pack's focus from our theme (default "campaign"). Keywords are not read: a
    genre theme ("dark fantasy") keeps the campaign mix, so its 5-page pack is the
    campaign starter, whatever page names its keyword tags mention."""
    t = _clean((ctx or {}).get("theme"))
    if t:
        for _w, rx, focus in _FOCUS_RX:
            if rx.search(t):
                return focus
    return "campaign"


def ranked_mix(focus: str, n: int) -> list[str]:
    """n page types for a pack of this focus, most wanted first."""
    if n < 1:
        raise ValueError(f"a ttrpg pack needs at least 1 page, not {n}")
    rank = RANKS.get(focus) or RANKS["campaign"]
    types = list(rank[:n])
    fill = RANKS["campaign"]
    i = 0
    while len(types) < n:
        types.append(fill[i % len(fill)])
        i += 1
    return types


def page_mix(focus: str, n: int) -> list[str]:
    """n page types for a pack of this focus, in pack order (same types next to each other)."""
    return sorted(ranked_mix(focus, n), key=PACK_ORDER.index)


def rank_quality(position: int) -> float:
    """A page's curation weight from its place in the focus ranking: 1.0 for the most wanted
    page, a little less down the list (never below 0.7, above packs.min_quality). The pack
    engine curates a derived pack by page-type variety and then quality, so a 5-page pack
    cut from a master set is the same sensible mix plan_pages would give a fresh one."""
    return round(max(0.7, 1.0 - 0.005 * position), 3)


def variant_order(focus: str, page_type: str) -> tuple[str, ...]:
    pref = (FOCUS_VARIANTS.get(focus) or {}).get(page_type)
    names = tuple(v.name for v in VARIANTS[page_type])
    if not pref:
        return names
    return tuple(dict.fromkeys([*pref, *names]))


def _choices(focus: str, page_type: str, count: int, used: set[str]) -> list[tuple[str, int]]:
    """(variant, alt) for each page of a type: the preferred layouts first, skipping
    layouts the theme's other sets already have."""
    order = variant_order(focus, page_type)
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


def system_fields(system: str | None, settings: dict | None) -> tuple[dict, str]:
    """(sheet fields, "system" | "generic") for a system id. An id the registry does not
    list falls back to the system-neutral fields (logged; the brief hook marks it for review)."""
    from core import systems

    sid = str(system or systems.GENERIC)
    default = systems.default_system(settings)
    if sid != default and not systems.is_known(sid, settings):
        log.warning("ttrpg: game system id %r is not in config/ttrpg_systems.yaml; using system-neutral fields", sid)
        sid = default
    return systems.sheet_fields_for(sid, settings), ("generic" if sid == default else "system")


def page_spec(page_type: str, variant_name: str, *, genre: str, fields: dict, fields_from: str = "generic",
              alt: int = 0, seed: str = "", index: int = 1, page_id: str | None = None) -> dict:
    """One page dict {"page_id", "type", "subject", "spec"} for a layout variant."""
    v = variant(page_type, variant_name)
    sections, recipe = v.build(Build(fields=fields, alt=alt))
    titles = titles_for(genre, page_type)
    pick = _h(seed, page_type, variant_name)
    motifs = GENRES[genre].motifs
    spec = {
        "variant": variant_name,
        "alt": alt,
        "genre": genre,
        "title": titles[(pick + alt) % len(titles)],
        "subtitle": f"{TYPE_LABELS[page_type]} - {v.label}",
        "motif": motifs[(_h(seed, "motif") + (alt + index if alt else 0)) % len(motifs)],
        "sections": sections,
        "layout": recipe,
        "fields_from": fields_from if page_type in SYSTEM_AWARE else "generic",
    }
    suffix = f"-v{alt + 1}" if alt else ""
    return {"page_id": page_id or f"{index:02d}-{page_type}-{variant_name}{suffix}", "type": page_type,
            "subject": subject_for(page_type, variant_name, alt), "spec": spec}


def plan(ctx: dict, n: int, *, seed: str, cfg: dict, settings: dict | None = None) -> list[dict]:
    """n distinct page specs for one design set."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError(f"a ttrpg pack needs a whole number of pages, at least 1, not {n!r}")
    genre = genre_for(ctx, str(cfg.get("default_genre") or "classic_fantasy"))
    focus = focus_for(ctx)
    fields, fields_from = system_fields((ctx or {}).get("system"), settings)
    used = {normalize(str(s)) for s in (ctx or {}).get("used_subjects") or [] if isinstance(s, str)}
    ranked = ranked_mix(focus, n)
    positions: dict[str, list[int]] = {}
    for pos, t in enumerate(ranked):
        positions.setdefault(t, []).append(pos)
    types = sorted(ranked, key=PACK_ORDER.index)
    counts = Counter(types)
    picks = {t: iter(_choices(focus, t, c, used)) for t, c in counts.items()}
    seen: Counter = Counter()
    pages = []
    for i, t in enumerate(types, 1):
        name, alt = next(picks[t])
        page = page_spec(t, name, genre=genre, fields=fields, fields_from=fields_from, alt=alt, seed=seed, index=i)
        page["quality"] = rank_quality(positions[t][seen[t]])
        seen[t] += 1
        pages.append(page)
    return pages


def is_starter(types: list[str]) -> bool:
    """True for the 5-page campaign starter mix."""
    return sorted(types) == sorted(STARTER)
