"""Defaults for the research stage, merged with settings `research:` (settings win)."""
from __future__ import annotations

from core import health
from core.config import section
from research.guard import HARD_FLOOR
from research.niches import DEFAULT_RULES

SHOP_FLOOR = 3   # user rule (2026-10-16): listing support spans >= 3 shops in every stage

DEFAULTS: dict = {
    "inputs_dir": "data/research_inputs",
    "briefs_dir": "data/briefs",
    "source_tool": "auto",              # auto | erank | everbee | <label>
    "min_listings_per_pattern": 10,     # compliance floor; values below 10 are raised to 10
    "min_shops_per_pattern": 3,         # listing-level support must span >= this many shops (user, 2026-10-16);
                                        # values below 3 are raised to 3
    # Evidence window (docs/SERVER.md section 12, research/window.py): briefs use exports
    # received within window_days of the run date; the service deletes them after keep_days.
    "window_days": 35,
    "keep_days": 180,
    "kdp_keep_days": 90,               # book files the inbox routed to research_inputs/kdp/ are deleted
                                        # after this many days (service/cleanup.py RESEARCH_DEFAULTS mirrors it)
    "briefs_per_niche": 5,
    "themes_in_patterns": 25,
    "keyword_clusters": 15,
    "keywords_per_cluster": 15,
    "keywords_per_brief": 10,
    "column_overrides": {},             # {canonical column: header or [headers]}
    "niche_rules": DEFAULT_RULES,       # {niche: [regex, ...]}, matched on lowercase text
    "extra_stopwords": [],              # added to the built-in theme stopwords (research/text.py)
    # Per niche, on top of the built-in and extra stopwords: words that describe the
    # product type there, not a design theme. Stationery and TTRPG themes are styles,
    # audiences and genres ("adhd", "cottagecore", "cyberpunk"), never page types.
    "niche_stopwords": {
        "stationery": [
            "tracker", "log", "insert", "refill", "undated", "dated", "yearly", "annual",
            "hourly", "schedule", "agenda", "organizer", "organiser", "organization",
            "calendar", "checklist", "list", "todo", "habit", "mood", "energy", "routine",
            "budget", "expense", "savings", "meal", "gratitude", "priority", "priorities",
            "matrix", "task", "pomodoro", "focus", "brain", "dump", "productivity",
            "productive", "lined", "dot", "dotted", "grid", "graph", "paper", "note", "notes",
            "goal", "reading", "water", "sleep", "cleaning", "chore", "friendly", "layout",
            "spread", "fillable", "hyperlinked", "half", "block", "blocking", "blocked",
            "lesson", "morning", "evening", "bedtime", "breakdown", "urgent", "important",
            "eisenhower", "bill", "bills", "debt", "payoff", "finance", "financial", "spending",
            "sinking", "fund", "funds", "binder", "dashboard", "session", "fan", "fans",
            "to do", "to do list", "brain dump", "time block", "time blocking",
            "top 3 priorities", "half letter",
        ],
        "ttrpg": [
            "rpg", "ttrpg", "tabletop", "character", "tracker", "log", "campaign", "session",
            "note", "notes", "npc", "quest", "initiative", "encounter", "spell", "spellbook",
            "inventory", "loot", "map", "hex", "hexagon", "hexcrawl", "grid", "square",
            "dungeon", "dice", "roll", "gm", "dm", "master", "player", "screen", "binder",
            "worldbuilding", "tool", "accessory", "accessories", "fillable", "form",
            "role", "playing", "system", "monster", "combat", "stat", "stats", "level",
            "class", "race", "adventure", "beginner", "starter", "tile", "paper", "graph",
            "insert", "inserts", "generator", "homebrew", "fan", "fans", "ability",
            "abilities", "town", "faction", "deity", "region", "record", "keeper",
            "game master", "role playing", "role playing game", "one shot", "world building",
            "battle map", "hex map", "character sheet", "stat block",
        ],
    },
    # Per niche: extra words kept inside a theme n-gram (research/text.py CONNECTORS):
    # "work from home" is a stationery theme (docs/PACKS.md section 1).
    "niche_connectors": {"stationery": ["from"]},
    # Per niche: words that ARE themes there although they are global stopwords
    # (stationery audiences from docs/PACKS.md section 1).
    "niche_keep_words": {"stationery": ["teacher", "student"]},
    # Platform, app and planner brands: they describe compatibility, not a design, and
    # are trademarks, so they are cut out of themes and keywords (never a brief theme).
    "excluded_terms": ["goodnotes", "good notes", "procreate", "notability", "cricut",
                       "silhouette cameo", "silhouette studio", "canva", "etsy", "ipad",
                       "iphone", "apple pencil", "android", "samsung", "kindle", "amazon", "kdp",
                       "teachers pay teachers", "tpt", "google", "google slides",
                       "google classroom", "google docs", "google sheets", "google forms",
                       "google drive", "boom cards", "boom learning",
                       "seesaw", "microsoft", "powerpoint", "onenote", "adobe", "photoshop",
                       "pinterest", "instagram", "tiktok", "youtube", "facebook",
                       "happy planner", "classic happy planner",
                       "mini happy planner", "big happy planner", "erin condren", "hobonichi", "travelers notebook",
                       "filofax", "moleskine", "passion planner", "plum paper", "recollections",
                       "zinnia", "noteshelf", "penly", "xodo", "avery", "glowforge", "scanncut",
                       "sizzix",
                       # Planner brands and apps (docs/PACKS.md section 4: review terms).
                       "panda planner", "clever fox", "bullet journal", "bullet journals",
                       "bullet journaling", "bujo", "notion", "rocketbook", "supernote",
                       # Virtual tabletops and TTRPG apps: compatibility, not a design.
                       "roll20", "foundry vtt", "foundryvtt", "fantasy grounds", "owlbear rodeo",
                       "world anvil", "dnd beyond", "dndbeyond", "d and d beyond",
                       # Game publishers' trademarked titles and product names ("game master"
                       # is the generic word and stays).
                       "dungeon master", "dungeon masters", "dungeon masters guide",
                       "players handbook", "monster manual"],
    # Medical and therapeutic claim words (CLAUDE.md: no medical claims; docs/PACKS.md
    # section 4). Cut out of themes like excluded_terms, and a keyword or tag phrase that
    # holds one is never a brief keyword. Settings can add words, never remove these.
    # "ADHD-friendly" describes a layout and stays allowed.
    # The ordinary phrases the listing stage allows ("trick or treat", MEDICAL_OK_PHRASES)
    # keep their word.
    # Plus core.health's noun-form and proof claims ("anxiety relief", "pain relief", "mood
    # booster", "anti-anxiety", "clinically proven"; docs/HEALTH_TERMS.md "Decided
    # 2026-10-17"). NOT cut: "stress relief" (a descriptive term, user 2026-10-17), "anxiety",
    # "mental health", "sleep", "mood" and the other lifestyle topics. Medical TOPICS (blood
    # pressure, medication logs) arrive as hard blocklist terms (config/health_terms.txt): a
    # row holding one is marked blocked and supports no theme.
    "medical_terms": list(dict.fromkeys([
        "treat", "treats", "treated", "treating", "treatment", "treatments",
        "cure", "cures", "cured", "curing", "heal", "heals", "healed", "healing",
        "healings", "diagnose", "diagnosed", "diagnoses", "diagnosing", "diagnosis",
        "diagnostic", "therapy", "therapies", "therapist", "therapists",
        "therapeutic", "therapeutics", "therapeutically", "clinical", "clinically",
        "medical", "medically", "medication", "medications", "medicine",
        "prescription", "prescriptions", "prescribed", *health.research_cut_terms()])),
    # Stricter-only supplement to config/trademark_blocklist.txt (hard, review). The
    # former entries now live in the file so every stage sees them; add new ones there.
    "extra_blocked_terms": [],
    "extra_review_terms": [],
    "audience": {
        "stickers": "digital planner and journaling fans, teens and adults",
        "coloring_pages": "kids 4-8",
        "activity_sheets": "kids 4-7",
        "stationery": "adults and teens who like structured printable planning pages",
        "ttrpg": "tabletop roleplaying players and game masters",
    },
    # Written in our own words; never derived from competitor text.
    "style_notes": {
        "stickers": ("bold clean outlines with a white die-cut border, flat cheerful colors, "
                     "simple shapes that stay readable at small sizes, transparent background, "
                     "one consistent style across the set"),
        "coloring_pages": ("bold thick outlines, large simple shapes with roomy spaces to color, "
                           "no shading or gray fills, one main subject per page, plain white "
                           "background"),
        "activity_sheets": ("clean high-contrast black line work, large easy-to-read lettering, "
                            "generous margins, one activity per page, answer keys at the back, "
                            "small theme doodles in the corners"),
        "stationery": ("clean printable layouts with clear zones, big writing boxes and roomy "
                       "line spacing, checkboxes, simple line icons, black line work on white, "
                       "one page type per page"),
        "ttrpg": ("our own clean layout with clearly labeled boxes, roomy writing space, light "
                  "genre-themed borders and corner art, black line work on white, no official "
                  "logos or trade dress"),
    },
    "default_audience": "kids 4-8",
    "default_style_notes": "clean bold line work, simple shapes, print-friendly white background",
    # Pack sizes (docs/PACKS.md section 2): every brief gets these, plus the top sellers'
    # sizes, each kept within settings packs.min_size / packs.max_size.<niche> (PACK_BOUNDS).
    # page_count = the largest size. Stickers count stickers, other niches pages.
    "base_pack_sizes": [5, 20],
    # Top-seller pack sizes per theme: listing rows with a stated count, the top quartile
    # by sales (by favorites when sales are missing), at least min_rows of them, ties at
    # the cut included. Their most common count is used, plus the second when its share
    # is at least second_share. A size needs min_shops_per_pattern (3+) shops among them.
    "top_sellers": {"quantile": 0.75, "min_rows": 5, "second_share": 0.3},
    # Opportunity (docs/PACKS.md section 2), the brief order: demand plus sales per listing
    # plus low competition, weights renormalized over the metrics a theme has.
    "opportunity_weights": {"demand": 0.4, "sales_per_listing": 0.3, "low_competition": 0.3},
    # ttrpg: a brief names a game system (by id, from config/ttrpg_systems.yaml through
    # core.systems) when at least this share of the theme's clean rows name it; else "generic".
    "system_share": 0.5,
    # price_usd = theme median price, clamped to [min_factor, max_factor] x shop default
    # price for the niche, then rounded to step_usd. No price data -> the shop default.
    "price_clamp": {"min_factor": 0.75, "max_factor": 1.5, "step_usd": 0.25},
    "fallback_price_usd": 4.0,
    "price_bands_usd": [0, 2, 3, 5, 8, 12, 20],
    "page_buckets": [5, 10, 20, 30, 50, 100],
    "demand_weights": {"sales": 0.35, "favorites": 0.15, "reviews": 0.15,
                       "search_volume": 0.25, "support": 0.10},
    "own_shop_boost": 0.1,               # own listings close up to 10% of the gap to 1.0
    "trend_boost": 0.1,                  # trend interest closes up to 10% of the gap to 1.0
    "collapse_ratio": 0.5,
    # Share of a theme's rows that named a blocked term: flag for review / drop.
    "blocked_cooccurrence": {"flag_share": 0.2, "drop_share": 0.5},
    "verbatim_title_min_words": 4,
}


# Read with core.config.section(settings, "packs", PACK_BOUNDS); settings win. The pack
# engine (generators/packs) owns the packs: section; research only keeps sizes inside it.
PACK_BOUNDS: dict = {
    "min_size": 5,
    "max_size": {"stickers": 60, "coloring_pages": 50, "activity_sheets": 50,
                 "stationery": 60, "ttrpg": 40},
    "default_max_size": 50,             # a niche missing from max_size
}


def pack_bounds(settings: dict, niche: str) -> tuple[int, int]:
    """(min, max) pack size for `niche` from settings packs: over PACK_BOUNDS.

    packs.max_size may be one number for every niche or {niche: number}. Empty values
    mean the default; min is at least 1 and max at least min.
    """
    cfg = section(settings or {}, "packs", PACK_BOUNDS)
    lo = whole_number(cfg.get("min_size"))
    lo = max(1, PACK_BOUNDS["min_size"] if lo is None else lo)
    hi = cfg.get("max_size")
    hi = whole_number(hi.get(niche) if isinstance(hi, dict) else hi)
    if hi is None:
        hi = PACK_BOUNDS["max_size"].get(niche) or whole_number(cfg.get("default_max_size")) \
            or PACK_BOUNDS["default_max_size"]
    return lo, max(lo, hi)


def whole_number(value) -> int | None:
    """A whole number from settings (30, "30", 30.0), else None (empty or not a number)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return int(n) if n.is_integer() else None


def medical_terms(cfg: dict) -> list[str]:
    """research.medical_terms from settings plus every built-in one (never fewer)."""
    extra = [str(t) for t in (cfg.get("medical_terms") or []) if str(t).strip()]
    return list(dict.fromkeys([*DEFAULTS["medical_terms"], *extra]))


# Ordinary phrases holding a medical word that make no claim. The same list as the listing
# stage's built-in listing.medical_ok_phrases (listing/__init__.py DEFAULTS, plus plurals),
# so research keeps exactly the keywords ("trick or treat coloring pages") the listing
# validator accepts. Not a research setting: settings listing.medical_ok_phrases, when set,
# can only shrink it, the way the listing stage reads that key (listing.listing_config).
MEDICAL_OK_PHRASES: tuple[str, ...] = (
    "trick or treat", "trick or treating", "trick or treaters", "sweet treat", "sweet treats",
    "treat bag", "treat bags", "treat box", "treat boxes", "treat jar", "treat jars",
    "holiday treat", "holiday treats",
)


def medical_ok_phrases(settings: dict | None) -> list[str]:
    """MEDICAL_OK_PHRASES, or, when settings listing.medical_ok_phrases is a list, only the
    built-in phrases it keeps (listed itself, or as the singular of a built-in plural). A
    setting may drop an exemption from the medical-claims rule, never add one (CLAUDE.md)."""
    raw = ((settings or {}).get("listing") or {}).get("medical_ok_phrases")
    if not isinstance(raw, (list, tuple)):
        return list(MEDICAL_OK_PHRASES)
    keep = {str(p).strip().casefold() for p in raw if str(p).strip()}

    def kept(phrase: str) -> bool:
        p = phrase.casefold()
        return p in keep or any(p.startswith(k) and p[len(k):] in ("s", "es") for k in keep)

    return [p for p in MEDICAL_OK_PHRASES if kept(p)]


def load_config(settings: dict) -> dict:
    """settings `research:` over DEFAULTS, with the floors enforced (10 listings, 3 shops).

    These are the discovery-stage numbers; research.run swaps in the active stage's
    thresholds (research/stages.py), which never go below them.
    """
    cfg = section(settings, "research", DEFAULTS)
    for key, floor in (("min_listings_per_pattern", HARD_FLOOR), ("min_shops_per_pattern", SHOP_FLOOR)):
        value = cfg.get(key)
        cfg[key] = max(floor, int(DEFAULTS[key] if value is None else value))
    # Derived, not a research setting (see MEDICAL_OK_PHRASES).
    cfg["medical_ok_phrases"] = medical_ok_phrases(settings)
    return cfg
