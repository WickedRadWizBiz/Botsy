"""The product facts a listing is written from: our own item data only.

Everything here comes from our manifest (built from our aggregated brief), our QA report
and our settings. Nothing from another seller's listing ever reaches this dict, and it is
the only thing the text model sees (CLAUDE.md compliance rules). It holds no file paths,
shop names or URLs.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from core.items import content_count

NICHES = {
    "coloring_pages": {"one": "coloring page", "many": "coloring pages", "tag": "coloring",
                       "product": "printable coloring pages"},
    "activity_sheets": {"one": "activity page", "many": "activity pages", "tag": "activity",
                        "product": "printable activity pages"},
    "stickers": {"one": "digital sticker", "many": "digital stickers", "tag": "stickers",
                 "product": "digital stickers (transparent PNG files) with a printable sticker sheet"},
    "stationery": {"one": "planner page", "many": "planner pages", "tag": "planner",
                   "product": "printable planner and productivity pages (PDF)"},
    "ttrpg": {"one": "RPG sheet", "many": "RPG sheets", "tag": "rpg",
              "product": "printable tabletop RPG sheets (PDF)"},
}
# Niches whose packs mix page types (stationery and ttrpg page types, docs/PACKS.md section 1).
TYPED_NICHES = ("stationery", "ttrpg")
PACK_SOURCES = ("master", "derived", "fresh")
OTHER_NICHE = {"one": "printable page", "many": "printable pages", "tag": "printable", "product": "digital download"}

ACTIVITY_LABELS = {
    "maze": ("maze", "mazes"),
    "word_search": ("word search", "word searches"),
    "tracing": ("tracing page", "tracing pages"),
    "dot_to_dot": ("dot-to-dot", "dot-to-dots"),
}

# (one, many) for the page types of the stationery and ttrpg generators. A type not listed
# here is named from its id ("npc_tracker" -> "NPC tracker", "NPC trackers"). Our own
# generic words only: no planner systems or method names (those are brands).
PAGE_TYPE_LABELS = {
    # stationery
    "daily_planner": ("daily planner", "daily planners"),
    "weekly_planner": ("weekly planner", "weekly planners"),
    "monthly_planner": ("monthly planner", "monthly planners"),
    "time_blocked_day": ("time-blocked day", "time-blocked days"),
    "brain_dump": ("brain dump page", "brain dump pages"),
    "task_breakdown": ("task breakdown page", "task breakdown pages"),
    "top3_priorities": ("top 3 priorities page", "top 3 priorities pages"),
    "priority_matrix": ("priority matrix", "priority matrices"),
    "habit_tracker": ("habit tracker", "habit trackers"),
    "routine_checklist": ("routine checklist", "routine checklists"),
    "focus_sessions": ("focus session tracker", "focus session trackers"),
    "mood_energy": ("mood and energy tracker", "mood and energy trackers"),
    "todo_list": ("to-do list", "to-do lists"),
    "meal_planner": ("meal planner", "meal planners"),
    "budget_tracker": ("budget tracker", "budget trackers"),
    "reading_log": ("reading log", "reading logs"),
    "gratitude": ("gratitude page", "gratitude pages"),
    "notes_lined": ("lined notes page", "lined notes pages"),
    "notes_dot": ("dot grid notes page", "dot grid notes pages"),
    "notes_grid": ("grid notes page", "grid notes pages"),
    # ttrpg
    "character_sheet": ("character sheet", "character sheets"),
    "spell_tracker": ("spell and ability tracker", "spell and ability trackers"),
    "ability_tracker": ("spell and ability tracker", "spell and ability trackers"),
    "inventory": ("inventory and loot sheet", "inventory and loot sheets"),
    "inventory_loot": ("inventory and loot sheet", "inventory and loot sheets"),
    "campaign_tracker": ("campaign tracker", "campaign trackers"),
    "session_notes": ("session notes page", "session notes pages"),
    "npc_tracker": ("NPC tracker", "NPC trackers"),
    "quest_log": ("quest log", "quest logs"),
    "initiative_tracker": ("initiative tracker", "initiative trackers"),
    "encounter_planner": ("encounter planner", "encounter planners"),
    "worldbuilding_town": ("town worksheet", "town worksheets"),
    "worldbuilding_faction": ("faction worksheet", "faction worksheets"),
    "worldbuilding_deity": ("deity worksheet", "deity worksheets"),
    "worldbuilding_region": ("region worksheet", "region worksheets"),
    "hex_map": ("hex map page", "hex map pages"),
    "square_grid": ("square grid map page", "square grid map pages"),
    "grid_map": ("square grid map page", "square grid map pages"),
    "dungeon_grid": ("dungeon grid page", "dungeon grid pages"),
    "dice_log": ("dice and roll log", "dice and roll logs"),
}
_TYPE_WORDS = {"npc": "NPC", "rpg": "RPG", "ttrpg": "TTRPG", "gm": "GM", "hp": "HP", "xp": "XP", "3": "3"}
_UNCOUNTED = ("paper", "notes", "info")


def page_type_label(kind, n: int | None = 2) -> str:
    """'habit_tracker' -> 'habit tracker' (n == 1) or 'habit trackers'."""
    if kind in PAGE_TYPE_LABELS:
        one, many = PAGE_TYPE_LABELS[kind]
    else:
        words = [_TYPE_WORDS.get(w, w) for w in str(kind or "page").replace("-", "_").split("_") if w]
        one = " ".join(words) or "page"
        many = one if one.endswith(("s", *_UNCOUNTED)) else one + "s"
    return one if n == 1 else many


_PLURALS = {one: many for one, many in PAGE_TYPE_LABELS.values()}


def plural_label(label: str) -> str:
    """'habit tracker' -> 'habit trackers'; a plural label stays as it is."""
    if label in _PLURALS:
        return _PLURALS[label]
    if label in _PLURALS.values() or label.endswith(("s", *_UNCOUNTED)):
        return label
    return label + "s"


_SINGULAR = {**{one: one for one, _ in PAGE_TYPE_LABELS.values()},
             **{many: one for one, many in PAGE_TYPE_LABELS.values()}}
_LABEL_TAILS = (" page", " pages", " sheet", " sheets", " worksheet", " worksheets", " paper")
_WORD_RE = re.compile(r"[a-z0-9]+")


def _squash(text: str) -> str:
    return "".join(_WORD_RE.findall(str(text).lower()))


def _type_aliases() -> dict[str, str]:
    """{squashed spelling: singular label} for every known page type: its id words
    ("hex_map" -> "hexmap") and its labels with and without a trailing page/sheet word."""
    out: dict[str, str] = {}
    for kind, (one, many) in PAGE_TYPE_LABELS.items():
        forms = {kind.replace("_", " "), one, many,
                 *(x[: -len(t)] for x in (one, many) for t in _LABEL_TAILS if x.endswith(t))}
        for form in forms:
            if len(_squash(form)) >= 6:   # long enough to name a page type, not an ordinary word
                out.setdefault(_squash(form), one)
    return out


_TYPE_ALIASES = _type_aliases()


def singular_label(label: str) -> str:
    """'habit trackers' -> 'habit tracker' (a label from page_type_label)."""
    if label in _SINGULAR:
        return _SINGULAR[label]
    return label[:-1] if label.endswith("s") and not label.endswith(("ss", *_UNCOUNTED)) else label


def named_types(text: str, facts: dict) -> list[tuple[str, int]]:
    """(singular label, pages of that type in this pack) for each page type `text` names
    ("dark fantasy character sheet" -> [("character sheet", 2)]), on whole words ("to-do
    list", "todo list"; "oatmeal planner" is not a meal planner), longest name first, each
    word counted once ("dot grid notes" is not also "grid notes"). Only for packs that mix
    page types (stationery, ttrpg, with page_types); [] otherwise or when it names none."""
    types = facts.get("page_types") or {}
    if facts.get("niche") not in TYPED_NICHES or not types:
        return []
    counts: dict[str, int] = {}
    for label, n in types.items():
        counts[singular_label(label)] = counts.get(singular_label(label), 0) + int(n or 0)
    aliases = dict(_TYPE_ALIASES)
    for one in counts:                     # page types without a built-in label
        aliases.setdefault(_squash(one), one)
    words = _WORD_RE.findall(str(text).lower())
    runs = []                              # (squashed run, first word, end word)
    for i in range(len(words)):
        for j in range(i + 1, min(len(words), i + 6) + 1):
            run = "".join(words[i:j])
            one = aliases.get(run) or (aliases.get(run[:-1]) if run.endswith("s") else None)
            if one:
                runs.append((-len(run), i, j, one))
    used: set[int] = set()
    found: dict[str, None] = {}
    for _, i, j, one in sorted(runs):
        if used.isdisjoint(range(i, j)):
            used.update(range(i, j))
            found.setdefault(one, None)
    return [(one, counts.get(one, 0)) for one in found]


def absent_types(text: str, facts: dict) -> list[str]:
    """Page types `text` names that this pack does not include (a brief keyword such as
    "rpg campaign tracker" on a pack without one): never in the title, which would promise
    a page the buyer does not get."""
    return [one for one, n in named_types(text, facts) if not n]


FILE_TYPE_LABELS = {"pdf": "PDF", "png": "PNG", "png_transparent": "PNG (transparent background)",
                    "zip": "ZIP", "svg": "SVG"}


def niche_labels(niche: str) -> dict:
    return NICHES.get(niche, OTHER_NICHE)


def safe_rel(path) -> str | None:
    """A path stored in our JSON, if it stays inside the item folder; otherwise None."""
    if not isinstance(path, str) or not path.strip() or "\\" in path:
        return None
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts:
        return None
    return p.as_posix()


def page_size_label(key: str, cfg: dict, settings: dict) -> str:
    labels = cfg.get("page_size_labels") or {}
    if key in labels:
        return labels[key]
    size = (settings.get("print", {}).get("page_sizes") or {}).get(key)
    name = key.replace("_", " ").title()
    return f"{name} ({size['width_in']:g} x {size['height_in']:g} in)" if size else name


def mockup_descriptions(manifest: dict, n_images: int, n_sizes: int, cfg: dict) -> list[str]:
    """What each listing photo shows, for alt text. QA's photo layouts come in a fixed
    order; when QA made every layout we can name each one. If any layout failed, the
    order is unknown, so we fall back to a generic description rather than risk alt
    text that describes the wrong picture."""
    layouts = cfg.get("mockup_layouts") or {}
    niche = manifest.get("niche")
    known = list(layouts.get(niche) or layouts.get("default") or [])
    if known and n_images == len(known):
        if niche != "stickers" and n_sizes < 2:  # QA shows a second close-up instead of the sizes card
            sizes_card = (layouts.get("default") or [None])[-1]
            at = known.index(sizes_card) if sizes_card in known else len(known) - 1
            known[at] = layouts.get("single_size_last") or known[at]
        return known
    return [f"listing photo {i} of {n_images}: a preview of the product" for i in range(1, n_images + 1)]


def pack_facts(manifest: dict) -> dict:
    """The pack this item is (docs/PACKS.md section 3): its size, how it was made (master,
    derived or fresh), its page types with counts (stationery, ttrpg) and the theme's other
    packs with whether their pages overlap. Older manifests without these give None / []."""
    pack = manifest.get("pack") if isinstance(manifest.get("pack"), dict) else {}
    size = _count(pack.get("size")) or None
    source = pack.get("source") if pack.get("source") in PACK_SOURCES else None
    page_types: dict[str, int] = {}
    if manifest.get("niche") in TYPED_NICHES:
        counts = pack.get("type_counts")
        if not isinstance(counts, dict) or not counts:
            types = pack.get("page_types")
            if not isinstance(types, list) or not types:
                types = [c.get("type") for c in pack.get("contents") or [] if isinstance(c, dict)]
            counts = Counter(t for t in types if isinstance(t, str) and t)
        for kind, n in counts.items():
            if isinstance(kind, str) and _count(n):
                label = page_type_label(kind, n)
                page_types[label] = page_types.get(label, 0) + n
    related = []
    for r in pack.get("related") or []:
        if isinstance(r, dict) and _count(r.get("size")):
            related.append({"size": r["size"], "source": r.get("source") if r.get("source") in PACK_SOURCES else None,
                             "same_set": bool(r.get("same_set"))})
    related.sort(key=lambda r: (r["size"], str(r["source"])))
    return {"pack_size": size, "pack_source": source, "page_types": page_types, "related_packs": related}


def gather(manifest: dict, image_paths: list[str], cfg: dict, settings: dict, *,
           game_system: dict | None = None) -> dict:
    """The model-facing facts for one item (JSON-serializable, deterministic).

    `game_system` ({"label", "names"}) is set only for a ttrpg item made for a system from
    config/ttrpg_systems.yaml, whose listing may name it (CLAUDE.md exception)."""
    niche = str(manifest.get("niche") or "")
    labels = niche_labels(niche)
    pack = manifest.get("pack") if isinstance(manifest.get("pack"), dict) else {}
    files = _entries(manifest, "files")

    size_keys = list(dict.fromkeys(e["page_size"] for e in files
                                   if e.get("kind") == "pdf" and isinstance(e.get("page_size"), str)))
    kinds = list(dict.fromkeys(e.get("kind") for e in files if e.get("kind")))
    pdf_pages = sorted({e["pages"] for e in files if e.get("kind") == "pdf" and isinstance(e.get("pages"), int)})
    dpis = sorted({e["dpi"] for e in files if isinstance(e.get("dpi"), int)})
    max_subjects = int(cfg.get("max_subjects", 12))

    contents = [c for c in pack.get("contents") or [] if isinstance(c, dict)]
    subjects = list(dict.fromkeys(str(c["subject"]).strip() for c in contents
                                  if isinstance(c.get("subject"), str) and c["subject"].strip()))
    # Activity mix with counts from the pack contents, or just the types (count None).
    activity: dict = dict(Counter(c.get("type") for c in contents if c.get("type") in ACTIVITY_LABELS))
    if not activity:
        activity = {t: None for t in pack.get("activity_types") or manifest.get("activity_types") or []
                    if t in ACTIVITY_LABELS}

    zips = _zips(manifest)
    descriptions = mockup_descriptions(manifest, len(image_paths), len(size_keys), cfg)
    facts = {
        "niche": niche,
        "product_type": labels["product"],
        "theme": str(manifest.get("theme") or "").strip(),
        "audience": str(manifest.get("audience") or "").strip(),
        "keywords": [str(k).strip() for k in manifest.get("keywords") or [] if str(k).strip()],
        "content_count": content_count(manifest),
        "content_unit": labels["many"],
        "answer_key_pages": _count(pack.get("answer_key_pages")),
        "title_pages": _count(pack.get("title_pages")),
        "activities": activity,
        "subjects": subjects[:max_subjects],
        "page_sizes": [page_size_label(k, cfg, settings) for k in size_keys],
        "pdf_pages": pdf_pages[0] if len(pdf_pages) == 1 else None,
        "file_types": [FILE_TYPE_LABELS.get(k, str(k).upper()) for k in kinds],
        "png_in_zip": sum(e["count"] for e in zips),
        "zip_files": len(zips),
        "dpi": dpis[0] if len(dpis) == 1 else None,
        "ai_assisted": bool(manifest.get("ai_assisted")),
        "images": [{"image": i, "shows": d} for i, d in enumerate(descriptions, 1)],
        **pack_facts(manifest),
    }
    if game_system:
        facts["game_system"] = str(game_system["label"])
        facts["game_system_names"] = [str(n) for n in game_system.get("names") or []]
    return facts


def _entries(manifest: dict, key: str) -> list[dict]:
    return [e for e in manifest.get(key) or [] if isinstance(e, dict)]


def _count(value) -> int:
    """A positive count from our JSON, else 0 (never a bool or a negative number)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _zips(manifest: dict) -> list[dict]:
    """Delivered ZIP files that say how many files they hold."""
    return [e for e in _entries(manifest, "files") if e.get("kind") == "zip" and _count(e.get("count"))]
