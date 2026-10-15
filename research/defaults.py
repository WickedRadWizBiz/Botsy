"""Defaults for the research stage, merged with settings `research:` (settings win)."""
from __future__ import annotations

from core.config import section
from research.guard import HARD_FLOOR
from research.niches import DEFAULT_RULES

DEFAULTS: dict = {
    "inputs_dir": "data/research_inputs",
    "briefs_dir": "data/briefs",
    "source_tool": "auto",              # auto | erank | everbee | <label>
    "min_listings_per_pattern": 10,     # compliance floor; values below 10 are raised to 10
    "briefs_per_niche": 5,
    "themes_in_patterns": 25,
    "keyword_clusters": 15,
    "keywords_per_cluster": 15,
    "keywords_per_brief": 10,
    "column_overrides": {},             # {canonical column: header or [headers]}
    "niche_rules": DEFAULT_RULES,       # {niche: [regex, ...]}, matched on lowercase text
    "extra_stopwords": [],              # added to the built-in theme stopwords (research/text.py)
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
                       "sizzix"],
    # Stricter-only supplement to config/trademark_blocklist.txt for franchises common in
    # these niches that the file does not list yet. Research treats them exactly like
    # file terms (hard = cut and co-occurrence drop, review = needs_review). Move them
    # into the blocklist file so later stages see them too, then empty these lists.
    "extra_blocked_terms": ["Grinch", "Labubu", "Squishmallow", "Taylor Swift", "Swiftie",
                            "Eras Tour", "Dr Seuss", "Cat in the Hat", "Elf on the Shelf",
                            "Pete the Cat", "Very Hungry Caterpillar", "Lisa Frank", "Sailor Moon",
                            "Naruto", "Demon Slayer", "Studio Ghibli", "Totoro", "Power Rangers",
                            "TMNT", "Teletubbies", "Godzilla", "Tamagotchi", "Pac-Man", "Tetris",
                            "Coca-Cola", "McDonald's", "Starbucks", "Stanley Cup", "Huggy Wuggy",
                            "Poppy Playtime", "Skibidi", "Wordle", "KPop Demon Hunters",
                            "Hungry Hungry Hippos", "Monopoly", "Jenga"],
    "extra_review_terms": ["Mario", "Candy Land", "Stanley", "Wicked", "Uno"],
    "audience": {
        "stickers": "digital planner and journaling fans, teens and adults",
        "coloring_pages": "kids 4-8",
        "activity_sheets": "kids 4-7",
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
    },
    "default_audience": "kids 4-8",
    "default_style_notes": "clean bold line work, simple shapes, print-friendly white background",
    # Pack sizes when settings niches.<niche>.pages_per_pack is not set (stickers per pack).
    "pack_sizes": {"stickers": [6, 12, 20, 30, 50]},
    "default_pack": {"stickers": 12, "coloring_pages": 20, "activity_sheets": 20},
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


def load_config(settings: dict) -> dict:
    """settings `research:` over DEFAULTS, with the compliance floor enforced."""
    cfg = section(settings, "research", DEFAULTS)
    cfg["min_listings_per_pattern"] = max(HARD_FLOOR, int(cfg["min_listings_per_pattern"]))
    return cfg
