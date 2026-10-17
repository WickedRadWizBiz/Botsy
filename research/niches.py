"""Assign each research row to one of our niches.

A file named "<niche>__anything.csv" puts every row in that niche (the user knows
best what they exported). Otherwise each row's title, keyword and category are
matched against the regex rules in settings research.niche_rules; the niche with
the most matching rules wins and ties go to the niche listed first. Rows that
match nothing are ignored.
"""
from __future__ import annotations

import re

from core.compliance import normalize

# Ordered: on a tie ("coloring and activity book") the earlier niche wins.
DEFAULT_RULES: dict[str, list[str]] = {
    "stickers": [
        r"\bstickers?\b",
        r"\bclip ?art\b",
        r"\bsticker (sheets?|packs?|bundles?)\b",
    ],
    "coloring_pages": [
        r"\bcolou?ring\b",
        r"\bcolou?r (by|in) (numbers?|the)\b",
    ],
    "activity_sheets": [
        r"\bmazes?\b",
        r"\bword ?(search|find)(es)?\b",
        r"\btracing\b",
        r"\bdot ?to ?dots?\b",
        r"\bconnect the dots\b",
        r"\bworksheets?\b",
        r"\bactivit(y|ies)\b",
        r"\bbusy book\b",
    ],
    # Printable planner and productivity pages. "planner stickers" is a sticker product,
    # so the planner rules skip it and the sticker rule wins.
    "stationery": [
        r"\bplanners?\b(?! stickers?\b)",
        r"\b(daily|weekly|monthly|undated|dated|hourly) (planners?|schedules?|agendas?|organizers?)\b(?! stickers?\b)",
        r"\b(planner|journal|notebook) (pages?|inserts?|printables?|refills?|templates?|sheets?)\b",
        r"\b(habit|mood|energy|routine|budget|expense|savings|reading|water|sleep|fitness|"
        r"cleaning|goal|focus) (trackers?|logs?|charts?)\b",
        r"\bto ?do (lists?|pages?|sheets?)\b",
        r"\b(brain dump|time block(ing|ed)?|pomodoro|priority matrix|task breakdown|"
        r"top 3 priorities)\b",
        r"\b(meal|gratitude|study|goal|lesson|teacher|student) (planners?|journals?|logs?|pages?)\b",
        r"\b(morning|evening|bedtime|chore|cleaning|daily) (routine|routines|checklists?|charts?)\b",
        r"\b(lined|dot|dotted|grid|graph) (paper|notes?|pages?)\b",
        # Generic words: they never claim a sticker listing ("ADHD planner stickers").
        r"^(?!.*\bstickers?\b).*\bproductivity\b",
        r"^(?!.*\bstickers?\b).*\badhd\b",
    ],
    # Tabletop RPG papers. Generic words only; game-system names are not needed to sort rows.
    "ttrpg": [
        r"\b(tt)?rpgs?\b",
        r"\btabletop\b",
        r"\bcharacter (sheets?|journals?|trackers?|portfolios?)\b",
        r"\bcampaign (journals?|trackers?|planners?|notebooks?|logs?|binders?|sheets?|notes)\b",
        r"\b(hex|hexagon) (maps?|paper|grids?|sheets?|crawl)\b|\bhexcrawl\b",
        r"\b(battle|dungeon|grid) maps?\b",
        r"\b(initiative|spell|spellbook|inventory|loot|quest|npc|encounter|session|dice|"
        r"monster|combat) (trackers?|logs?|sheets?|cards?|planners?|notes|journals?)\b",
        r"\b(game master|gm|dm) (screens?|notes|tools?|binders?|journals?|planners?)\b",
        r"\b(world ?building|worldbuilding) (worksheets?|templates?|pages?|workbooks?|journals?|sheets?)\b",
        r"\bone shot\b|\bworld ?building\b|\bworldbuilding\b",
    ],
}


def match_text(text: str) -> str:
    """Lowercase, accents and punctuation folded to single spaces ("Dot-to-Dot" -> "dot to dot")."""
    return " ".join(re.findall(r"[a-z0-9]+", normalize(text or "").replace("'", "")))


def compile_rules(rules: dict[str, list[str]]) -> dict[str, list[re.Pattern]]:
    return {niche: [re.compile(rx) for rx in patterns] for niche, patterns in rules.items()}


def niche_from_filename(name: str, known: list[str] | tuple[str, ...]) -> str | None:
    """'coloring_pages__erank-2026-10-14.csv' -> 'coloring_pages' (if it is a known niche)."""
    if "__" not in name:
        return None
    prefix = re.sub(r"[\s-]+", "_", name.split("__", 1)[0].strip().lower())
    return prefix if prefix in known else None


def assign_niche(text: str, rules: dict[str, list[re.Pattern]]) -> str | None:
    """Niche with the most rule hits in `text`, ties to the first listed; None if no hit."""
    t = match_text(text)
    best, best_hits = None, 0
    for niche, patterns in rules.items():
        hits = sum(1 for rx in patterns if rx.search(t))
        if hits > best_hits:
            best, best_hits = niche, hits
    return best
