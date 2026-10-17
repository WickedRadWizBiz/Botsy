"""Synthetic research exports for dry runs, CI and the end-to-end test.

Real exports are licensed and gitignored, so the GitHub Action and tests need
stand-ins. Everything here is invented (generic themes, fake shops and links)
and shaped like an EverBee listing export and an eRank keyword export, so the
whole parser path runs, including dropping the identifying columns.

The ttrpg rows of one theme (SYSTEM_THEMES) mostly name a game system the way competitor
titles do ("... for D&D 5e"), so research gives that brief a system id and the listing
stage names the system with its disclaimer (docs/PACKS.md sections 2 and 4). Research cuts
the names from themes and keywords; nothing else in the demo names a brand.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

THEMES = {
    "coloring_pages": ["farm animals", "ocean animals", "dinosaurs", "outer space", "garden flowers"],
    "activity_sheets": ["dinosaurs", "alphabet", "ocean animals", "farm animals", "outer space"],
    "stickers": ["frogs", "mushrooms", "cats", "rainbows", "outer space"],
    "stationery": ["adhd", "minimalist", "cottagecore", "botanical", "student"],
    "ttrpg": ["classic fantasy", "dark fantasy", "sci fi", "cosmic horror", "cyberpunk"],
}
# The niches write_demo_inputs fills by default (the first three, so tests and dry runs
# that count brief files keep their numbers); DEMO_NICHES is every niche above. Each niche
# outside DEFAULT_NICHES draws from its own random stream, so the first three niches' rows
# are the same whichever niches are written.
DEFAULT_NICHES = ("coloring_pages", "activity_sheets", "stickers")
DEMO_NICHES = tuple(THEMES)
# ttrpg themes whose rows name a game system: (system label as competitors write it, share
# of the theme's rows). "classic fantasy" names it in most rows (the brief gets the system);
# "dark fantasy" in a few (below research.system_share, so its brief stays generic).
SYSTEM_THEMES = {"classic fantasy": ("D&D 5e", 0.8), "dark fantasy": ("5e", 0.15)}
ACTIVITIES = ["Mazes", "Tracing Worksheets", "Word Search", "Dot to Dot"]
# Our own page-type names (docs/PACKS.md section 1); no game systems or planner brands.
PLANNER_PAGES = ["Daily Planner", "Habit Tracker", "Weekly Planner", "To Do List", "Brain Dump"]
RPG_PAGES = ["Character Sheet", "Campaign Tracker", "Session Notes", "NPC Tracker", "Quest Log"]
ADJECTIVES = ["Cute", "Simple", "Easy", "Bold", "Fun", "Big"]
LISTING_HEADERS = ["Product Name", "Shop Name", "Price", "Mo. Sales", "Mo. Revenue", "Reviews",
                   "Favorites", "Listing Age", "Category", "Tags", "Link", "Image"]
KEYWORD_HEADERS = ["Keywords", "Avg. Searches", "Avg. Clicks", "Avg. CTR", "Etsy Competition",
                   "Google Searches"]
NOUNS = {"coloring_pages": "coloring pages", "activity_sheets": "activity sheets",
         "stickers": "stickers", "stationery": "planner pages", "ttrpg": "rpg sheets"}
# Who the tags and searches name ("dinosaurs for kids"); planners and RPG sheets are for grown-ups.
FOR_WHOM = {"stationery": "adults", "ttrpg": "players"}


def _title(niche: str, theme: str, i: int, rng: random.Random) -> tuple[str, int]:
    adj = rng.choice(ADJECTIVES)
    t = theme.title()
    if niche == "coloring_pages":
        n = rng.choice([10, 12, 20, 25, 30])
        return f"{t} Coloring Pages for Kids, {n} Printable {adj} Pages, PDF Instant Download, Set {i}", n
    if niche == "activity_sheets":
        n = rng.choice([10, 15, 20, 30])
        act = ACTIVITIES[i % len(ACTIVITIES)]
        return f"{t} {act} for Kids, {n} Printable Pages, {adj} PDF Activity Pack {i}", n
    if niche == "stationery":
        n = rng.choice([5, 10, 20, 30])
        page = PLANNER_PAGES[i % len(PLANNER_PAGES)]
        return f"{t} {page} Pages, {n} Printable Planner Pages, Undated PDF, {adj} Set {i}", n
    if niche == "ttrpg":
        n = rng.choice([5, 10, 12, 20])
        page = RPG_PAGES[i % len(RPG_PAGES)]
        system, share = SYSTEM_THEMES.get(theme, ("", 0.0))
        named = f" for {system}" if system and rng.random() < share else ""
        return f"{t} RPG {page}{named}, {n} Printable Pages, Printable PDF, {adj} Set {i}", n
    n = rng.choice([12, 20, 24, 40])
    return f"{adj} {t} Digital Stickers, {n} PNG Clipart, Transparent Background, Pack {i}", n


def write_demo_inputs(inputs_dir: str | Path, listings_per_theme: int = 14, seed: int = 7,
                      niches=DEFAULT_NICHES) -> list[Path]:
    """Write a synthetic EverBee-style listing CSV and eRank-style keyword CSV.

    niches: which niches get rows (default DEFAULT_NICHES; DEMO_NICHES for all five).
    """
    unknown = [n for n in niches if n not in THEMES]
    if unknown:
        raise ValueError(f"no demo data for {', '.join(unknown)}; known: {', '.join(THEMES)}")
    wanted = {n: THEMES[n] for n in THEMES if n in niches}
    main = random.Random(seed)
    # str seeds are hashed with sha512, never with PYTHONHASHSEED: the same rows every run.
    streams = {n: main if n in DEFAULT_NICHES else random.Random(f"{seed}-{n}") for n in wanted}
    out = Path(inputs_dir)
    out.mkdir(parents=True, exist_ok=True)
    listing_path = out / "everbee-product-analytics-demo.csv"
    keyword_path = out / "erank-keyword-tool-demo.csv"
    lid = 9_100_000_000
    with listing_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(LISTING_HEADERS)
        for niche, themes in wanted.items():
            rng = streams[niche]
            for rank, theme in enumerate(themes):
                for i in range(1, listings_per_theme + 1):
                    lid += 1
                    title, _ = _title(niche, theme, i, rng)
                    price = rng.choice([2.5, 3.0, 3.49, 3.99, 4.5, 4.99, 5.99])
                    sales = max(1, int(rng.gauss(60 - 10 * rank, 15)))
                    w.writerow([
                        title, f"DemoShop{rng.randint(1, 40):02d}", f"${price:.2f}", sales,
                        f"${sales * price:,.2f}", rng.randint(0, 900), f"{rng.randint(5, 2500):,}",
                        f"{rng.randint(1, 36)} months", "Paper & Party Supplies",
                        f"{theme} {NOUNS[niche]},printable {theme},"
                        f"{theme} for {FOR_WHOM.get(niche, 'kids')}",
                        f"https://www.etsy.com/listing/{lid}/demo-item",
                        f"https://i.etsystatic.com/demo/il_570xN.{lid}.jpg",
                    ])
    with keyword_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["eRank Keyword Tool export (synthetic demo data)"])
        w.writerow([])
        w.writerow(KEYWORD_HEADERS)
        for niche, themes in wanted.items():
            rng, noun = streams[niche], NOUNS[niche]
            for theme in themes:
                for pattern in ("{t} {n}", "printable {t} {n}", "{t} {n} for {w}", "easy {t} {n}",
                                "{t} {n} pdf", "cute {t} {n}", "{t} {n} bundle", "simple {t} {n}",
                                "{t} {n} set", "big {t} {n}", "{t} printable", "{t} {n} download"):
                    searches = rng.randint(40, 4000)
                    clicks = int(searches * rng.uniform(0.3, 1.2))
                    kw = pattern.format(t=theme, n=noun, w=FOR_WHOM.get(niche, "kids"))
                    system, share = SYSTEM_THEMES.get(theme, ("", 0.0)) if niche == "ttrpg" else ("", 0.0)
                    if system and rng.random() < share:
                        kw = f"{kw} {system.lower()}"
                    w.writerow([kw, f"{searches:,}", f"{clicks:,}",
                                f"{clicks / searches:.0%}", f"{rng.randint(150, 40000):,}",
                                rng.choice(["1.2k", "880", "N/A", "3,400"])])
    return [listing_path, keyword_path]
