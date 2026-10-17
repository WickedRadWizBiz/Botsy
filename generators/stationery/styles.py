"""Stationery styles: how a theme looks on the page (docs/PACKS.md section 1).

Stationery themes are styles and audiences ("adhd", "cottagecore", "botanical", "student").
style_for(ctx) maps the theme's words to one of STYLES (then an explicit style id in
ctx["style"], then the brief keywords), defaulting to minimalist. Every style is our own:
spacing, fonts, light print-friendly zone tints and simple procedural decorations
(generators/stationery/decor.py). Nothing here comes from another seller's design.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from core.compliance import normalize

STYLES = ("adhd_friendly", "minimalist", "cozy", "botanical", "seasonal", "student", "teacher", "wfh")
DEFAULT_STYLE = "minimalist"
SEASONS = ("autumn", "winter", "spring", "summer")


@dataclass(frozen=True)
class StyleSpec:
    name: str
    title_font: str = "Helvetica-Bold"
    body_font: str = "Helvetica"
    label_font: str = "Helvetica-Bold"
    title_pt: float = 20.0
    subtitle_pt: float = 9.5
    label_pt: float = 9.0
    small_pt: float = 8.0
    line_gap_mm: float = 7.5        # writing lines
    row_mm: float = 7.0             # table and checklist rows
    box_lines: int = 0              # most writing lines per zone (0: fill it)
    list_rows: int = 0              # most checklist rows per zone (0: fill it)
    checkbox_pt: float = 9.0
    gap_pt: float = 9.0             # between zones
    pad_pt: float = 6.0             # inside zones
    radius_pt: float = 4.0
    structural_scale: float = 1.0
    header_pt: float = 72.0
    tints: tuple = ((0.94, 0.94, 0.94),)
    accent: tuple = (0.25, 0.25, 0.25)   # decoration strokes
    motifs: tuple = ("geometric",)
    frame: str = ""                 # "", "brackets", "rounded", "dashed"
    footer_motifs: bool = False
    time_bars: bool = False         # visual time bars on schedules
    big_boxes: bool = False         # fewer, larger zones


_ADHD_TINTS = ((0.85, 0.92, 1.0), (1.0, 0.95, 0.78), (0.87, 0.96, 0.87), (1.0, 0.89, 0.89), (0.92, 0.89, 1.0))

STYLE_SPECS: dict[str, StyleSpec] = {
    "adhd_friendly": StyleSpec(
        "adhd_friendly", title_pt=22.0, subtitle_pt=10.0, label_pt=10.5, small_pt=8.5, line_gap_mm=9.0,
        row_mm=9.0, box_lines=4, list_rows=7, checkbox_pt=12.0, gap_pt=12.0, pad_pt=8.0, radius_pt=6.0,
        structural_scale=1.25,
        header_pt=80.0, tints=_ADHD_TINTS, accent=(0.2, 0.35, 0.6), motifs=("blocks", "check", "bolt"),
        time_bars=True, big_boxes=True),
    "minimalist": StyleSpec(
        "minimalist", title_pt=19.0, label_pt=8.5, small_pt=7.5, line_gap_mm=7.0, row_mm=7.0, radius_pt=0.0,
        tints=((0.95, 0.95, 0.95),), accent=(0.2, 0.2, 0.2), motifs=("geometric", "lines", "dots"),
        frame="brackets"),
    "cozy": StyleSpec(
        "cozy", title_font="Times-BoldItalic", title_pt=22.0, label_pt=9.0, line_gap_mm=8.0, row_mm=7.5,
        radius_pt=8.0, tints=((0.99, 0.93, 0.86), (0.96, 0.91, 0.86), (0.98, 0.9, 0.9)),
        accent=(0.48, 0.32, 0.22), motifs=("mug", "heart", "flourish"), frame="rounded"),
    "botanical": StyleSpec(
        "botanical", title_font="Times-BoldItalic", title_pt=22.0, label_pt=9.0, line_gap_mm=7.5, row_mm=7.5,
        radius_pt=6.0, tints=((0.9, 0.95, 0.89), (0.94, 0.96, 0.9), (0.97, 0.95, 0.9)),
        accent=(0.24, 0.42, 0.28), motifs=("sprig", "fern", "flower"), footer_motifs=True),
    "seasonal": StyleSpec(
        "seasonal", title_pt=21.0, label_pt=9.0, line_gap_mm=7.5, row_mm=7.5, radius_pt=5.0,
        tints=((0.95, 0.95, 0.95),), accent=(0.3, 0.3, 0.3), motifs=("leaf", "snowflake", "flower", "sun"),
        footer_motifs=True),
    "student": StyleSpec(
        "student", title_pt=20.0, label_pt=9.0, line_gap_mm=7.1, row_mm=7.0, radius_pt=3.0,
        tints=((0.88, 0.93, 1.0), (1.0, 0.96, 0.84), (0.9, 0.97, 0.92)), accent=(0.18, 0.3, 0.55),
        motifs=("pencil", "books", "star")),
    "teacher": StyleSpec(
        "teacher", title_pt=20.0, label_pt=9.0, line_gap_mm=7.5, row_mm=7.5, radius_pt=4.0,
        tints=((1.0, 0.92, 0.9), (0.9, 0.96, 0.92), (1.0, 0.96, 0.85)), accent=(0.6, 0.2, 0.18),
        motifs=("apple", "star", "books")),
    "wfh": StyleSpec(
        "wfh", title_pt=20.0, label_pt=9.0, line_gap_mm=7.5, row_mm=7.5, radius_pt=4.0,
        tints=((0.91, 0.94, 0.98), (0.95, 0.95, 0.92), (0.92, 0.96, 0.95)), accent=(0.22, 0.32, 0.42),
        motifs=("mug", "laptop", "geometric"), frame="dashed"),
}

SEASON_LOOK = {
    "autumn": {"tints": ((1.0, 0.91, 0.82), (0.98, 0.95, 0.85)), "accent": (0.6, 0.32, 0.12),
               "motifs": ("leaf", "acorn")},
    "winter": {"tints": ((0.89, 0.94, 0.99), (0.94, 0.95, 0.98)), "accent": (0.2, 0.35, 0.55),
               "motifs": ("snowflake", "star")},
    "spring": {"tints": ((0.99, 0.91, 0.95), (0.91, 0.97, 0.91)), "accent": (0.55, 0.25, 0.42),
               "motifs": ("flower", "sprig")},
    "summer": {"tints": ((1.0, 0.96, 0.8), (0.86, 0.95, 0.99)), "accent": (0.7, 0.45, 0.05),
               "motifs": ("sun", "wave")},
}

# Theme words per style, checked in this order (the first style with a match wins).
STYLE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("adhd_friendly", ("adhd", "adhd friendly", "neurodivergent", "neurodiverse", "neurospicy",
                       "executive function", "low energy", "scattered", "big boxes")),
    ("teacher", ("teacher", "teachers", "teaching", "classroom", "lesson", "lessons", "educator", "homeschool",
                 "homeschooling", "home school")),
    ("student", ("student", "students", "study", "studying", "college", "university", "school", "homework",
                 "exam", "exams", "academic", "semester", "campus")),
    ("wfh", ("work from home", "wfh", "remote", "remote work", "home office", "office", "work", "workday",
             "freelance", "freelancer", "business", "small business", "entrepreneur", "corporate")),
    ("seasonal", ("seasonal", "season", "autumn", "fall", "winter", "spring", "summer", "christmas", "holiday",
                  "holidays", "halloween", "thanksgiving", "easter", "new year", "snow", "pumpkin", "harvest",
                  "valentine", "valentines")),
    ("botanical", ("botanical", "floral", "flower", "flowers", "plant", "plants", "garden", "gardening", "leaf",
                   "leaves", "fern", "wildflower", "wildflowers", "greenery", "boho", "nature", "herb", "herbs")),
    ("cozy", ("cozy", "cosy", "cottagecore", "cottage", "hygge", "coffee", "tea", "rainy day", "comfy", "warm",
              "homey", "mushroom", "mushrooms", "kawaii", "cute", "pastel", "self care", "selfcare", "home",
              "homemaking", "family", "mom", "mum")),
    ("minimalist", ("minimalist", "minimal", "minimalism", "simple", "clean", "modern", "aesthetic",
                    "black and white", "monochrome", "neutral", "scandi", "basic")),
)

# Seasonal theme words that name a holiday: the pages get the season's look, not holiday
# pages (no gift lists or party planners), so such a theme goes to review.
HOLIDAY_WORDS = ("christmas", "holiday", "holidays", "halloween", "thanksgiving", "easter", "new year", "valentine",
                 "valentines")

SEASON_WORDS = {
    "autumn": ("autumn", "fall", "halloween", "thanksgiving", "pumpkin", "harvest"),
    "winter": ("winter", "christmas", "holiday", "holidays", "snow", "new year"),
    "spring": ("spring", "easter", "valentine", "valentines", "blossom"),
    "summer": ("summer", "beach", "sunshine"),
}

ALIASES = {"adhd": "adhd_friendly", "adhd-friendly": "adhd_friendly", "adhd friendly": "adhd_friendly",
           "minimal": "minimalist", "cottagecore": "cozy", "cosy": "cozy", "work from home": "wfh",
           "work-from-home": "wfh", "work_from_home": "wfh", "remote": "wfh"}


@dataclass(frozen=True)
class StyleChoice:
    style: str
    season: str | None = None
    matched: str | None = None          # the word that chose it (None: default)
    source: str = "default"             # theme | style | keywords | default


def _has(text: str, word: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None


def _match(text: str) -> tuple[str, str] | None:
    for style, words in STYLE_WORDS:
        for w in words:
            if _has(text, w):
                return style, w
    return None


def season_for(*texts: str) -> str | None:
    for text in texts:
        norm = normalize(text or "")
        for season, words in SEASON_WORDS.items():
            if any(_has(norm, w) for w in words):
                return season
    return None


def explicit_style(value) -> str | None:
    """A style id named outright ("adhd_friendly", "cozy", "work from home"), else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = normalize(value).strip()
    if v.replace(" ", "_") in STYLES:
        return v.replace(" ", "_")
    return ALIASES.get(v)


def style_for(ctx: dict) -> StyleChoice:
    """The style for a pack: theme words, else an explicit style id in ctx["style"], else
    the keywords; default minimalist."""
    theme = normalize(str(ctx.get("theme") or ""))
    keywords = [normalize(str(k)) for k in ctx.get("keywords") or [] if isinstance(k, str)]
    season = season_for(theme, *keywords)
    hit = _match(theme)
    if hit:
        return StyleChoice(hit[0], season if hit[0] == "seasonal" else None, hit[1], "theme")
    explicit = explicit_style(ctx.get("style"))
    if explicit:
        return StyleChoice(explicit, season if explicit == "seasonal" else None, explicit, "style")
    for kw in keywords:
        hit = _match(kw)
        if hit:
            return StyleChoice(hit[0], season if hit[0] == "seasonal" else None, hit[1], "keywords")
    return StyleChoice(DEFAULT_STYLE)


def spec_for(style: str, season: str | None = None) -> StyleSpec:
    """The StyleSpec for a style id (unknown ids fall back to minimalist); a seasonal style
    takes its season's tints, accent colour and motifs."""
    spec = STYLE_SPECS.get(style) or STYLE_SPECS[DEFAULT_STYLE]
    if spec.name == "seasonal" and season in SEASON_LOOK:
        look = SEASON_LOOK[season]
        spec = replace(spec, tints=look["tints"], accent=look["accent"], motifs=look["motifs"])
    return spec


def motifs_for(style: str, season: str | None = None) -> tuple[str, ...]:
    return spec_for(style, season).motifs


__all__ = ["ALIASES", "DEFAULT_STYLE", "HOLIDAY_WORDS", "SEASONS", "STYLES", "STYLE_SPECS", "STYLE_WORDS",
           "StyleChoice", "StyleSpec", "explicit_style", "motifs_for", "season_for", "spec_for", "style_for"]
