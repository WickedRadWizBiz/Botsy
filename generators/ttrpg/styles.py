"""Genre styles for tabletop RPG pages (docs/PACKS.md section 1: themes are genre and tone).

A style is our own procedural look: the page frame and its corner ornaments, the header
divider, the section heading treatment, the emblems beside the title and the built-in PDF
fonts (Helvetica, Times, Courier; nothing is embedded). Tints are light and print-friendly.
Styles never carry names, logos or art of any game, setting or publisher.

genre_for(ctx) picks a style from our brief's theme, then its style notes and keywords
(first match wins, longer phrases first); classic_fantasy otherwise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.compliance import normalize


@dataclass(frozen=True)
class Genre:
    name: str
    label: str
    title_font: str
    head_font: str
    label_font: str = "Helvetica"
    bold_font: str = "Helvetica-Bold"
    upper_title: bool = False
    upper_heads: bool = True
    frame: str = "double_diamond"      # decor.FRAMES
    divider: str = "diamond"           # decor.DIVIDERS
    heads: str = "band"                # band | rule | bracket | tab
    radius: float = 0.0                # section box corners
    chamfer: float = 0.0               # cut corners (overrides radius)
    tint: tuple = (0.93, 0.93, 0.93)   # header bands, emblem fills
    accent: tuple = (0.30, 0.30, 0.30)
    motifs: tuple[str, ...] = ("diamond",)
    words: tuple[str, ...] = ()        # theme words and phrases that pick this style


GENRES: dict[str, Genre] = {g.name: g for g in (
    Genre("classic_fantasy", "Classic Fantasy", "Times-Bold", "Times-Bold", frame="double_diamond",
          divider="diamond", heads="band", tint=(0.94, 0.92, 0.86), accent=(0.35, 0.28, 0.16),
          motifs=("shield", "swords", "crown"),
          words=("classic fantasy", "high fantasy", "fantasy", "medieval", "dragon", "dragons", "knight", "castle",
                 "dungeon crawl", "sword and sorcery", "adventure", "adventurer", "quest")),
    Genre("dark_fantasy", "Dark Fantasy", "Times-BoldItalic", "Times-Bold", frame="thorns", divider="spikes",
          heads="rule", tint=(0.90, 0.89, 0.89), accent=(0.22, 0.16, 0.18), motifs=("skull", "dagger", "moon"),
          words=("dark fantasy", "grimdark", "gothic", "grim", "dark", "necromancer", "necromancy", "vampire",
                 "undead", "cursed", "doom")),
    Genre("scifi", "Science Fiction", "Helvetica-Bold", "Helvetica-Bold", upper_title=True, frame="chamfer",
          divider="segments", heads="bracket", chamfer=5.0, tint=(0.89, 0.93, 0.96), accent=(0.13, 0.30, 0.42),
          motifs=("planet", "star4", "chevron"),
          words=("science fiction", "sci fi", "scifi", "space opera", "space", "starship", "spaceship", "galaxy",
                 "galactic", "futuristic", "mech", "mecha", "alien", "aliens", "planet", "rocket")),
    Genre("cosmic_horror", "Cosmic Horror", "Times-Italic", "Times-Italic", upper_heads=False, frame="tendrils",
          divider="circle", heads="rule", tint=(0.91, 0.92, 0.90), accent=(0.18, 0.26, 0.22),
          motifs=("sigil", "spiral", "eye"),
          words=("cosmic horror", "horror", "eldritch", "lovecraftian", "occult", "investigator", "investigation",
                 "mythos", "1920s", "haunted", "mystery", "creepy")),
    Genre("cyberpunk", "Cyberpunk", "Courier-Bold", "Courier-Bold", upper_title=True, frame="circuit",
          divider="nodes", heads="bracket", chamfer=4.0, tint=(0.94, 0.90, 0.95), accent=(0.38, 0.12, 0.40),
          motifs=("chip", "bolt", "bars"),
          words=("cyberpunk", "cyber", "neon", "hacker", "dystopian", "dystopia", "synthwave", "android", "netrun",
                 "megacity", "chrome")),
    Genre("pirate", "Pirate", "Times-Bold", "Times-BoldItalic", upper_heads=False, frame="rope", divider="rope",
          heads="tab", radius=2.0, tint=(0.90, 0.93, 0.94), accent=(0.12, 0.26, 0.36),
          motifs=("compass", "anchor", "wheel"),
          words=("pirate", "pirates", "nautical", "high seas", "seafaring", "sea", "ocean", "sailing", "sailor",
                 "swashbuckling", "swashbuckler", "treasure map", "ship")),
    Genre("western", "Western", "Courier-Bold", "Times-Bold", upper_title=True, frame="rivets", divider="star",
          heads="band", tint=(0.95, 0.91, 0.84), accent=(0.42, 0.25, 0.10), motifs=("star5", "horseshoe", "cactus"),
          words=("weird west", "wild west", "western", "cowboy", "cowboys", "frontier", "outlaw", "outlaws",
                 "gunslinger", "desert", "ranch")),
    Genre("cozy", "Cozy", "Helvetica-Bold", "Helvetica-Bold", upper_heads=False, frame="rounded_vine",
          divider="dots", heads="tab", radius=6.0, tint=(0.93, 0.95, 0.90), accent=(0.27, 0.38, 0.22),
          motifs=("leaf", "mushroom", "mug"),
          words=("cozy", "cosy", "cottagecore", "cottage", "cute", "kawaii", "whimsical", "wholesome", "pastel",
                 "tavern", "tea", "fairy tale", "fairytale", "storybook", "kids", "family friendly")),
)}

DEFAULT_GENRE = "classic_fantasy"


def _phrase_rx(words) -> list[tuple[str, re.Pattern]]:
    return [(w, re.compile(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])")) for w in words]


# Longer phrases first, so "dark fantasy" wins over "fantasy" and "space opera" over "space".
_MATCHERS = sorted(((w, rx, g.name) for g in GENRES.values() for w, rx in _phrase_rx(g.words)),
                   key=lambda t: (-len(t[0]), t[0]))


def _clean(text) -> str:
    if isinstance(text, (list, tuple)):
        text = " , ".join(str(t) for t in text)
    return re.sub(r"[^a-z0-9]+", " ", normalize(str(text or ""))).strip()


def genre_in(text) -> str | None:
    """The style a text names (the longest matching phrase), or None."""
    t = _clean(text)
    if not t:
        return None
    for _w, rx, name in _MATCHERS:
        if rx.search(t):
            return name
    return None


def genre_for(ctx: dict | None, default: str = DEFAULT_GENRE) -> str:
    """The style for a design set: our theme first, then style notes, then keywords."""
    ctx = ctx or {}
    for field in ("theme", "style", "keywords"):
        found = genre_in(ctx.get(field))
        if found:
            return found
    return default if default in GENRES else DEFAULT_GENRE


def genre(name: str | None) -> Genre:
    return GENRES.get(str(name or ""), GENRES[DEFAULT_GENRE])
