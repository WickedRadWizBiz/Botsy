"""Synthetic research exports for dry runs, CI and the end-to-end test.

Real exports are licensed and gitignored, so the GitHub Action and tests need
stand-ins. Everything here is invented (generic themes, fake shops and links)
and shaped like an EverBee listing export and an eRank keyword export, so the
whole parser path runs, including dropping the identifying columns.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

THEMES = {
    "coloring_pages": ["farm animals", "ocean animals", "dinosaurs", "outer space", "garden flowers"],
    "activity_sheets": ["dinosaurs", "alphabet", "ocean animals", "farm animals", "outer space"],
    "stickers": ["frogs", "mushrooms", "cats", "rainbows", "outer space"],
}
ACTIVITIES = ["Mazes", "Tracing Worksheets", "Word Search", "Dot to Dot"]
ADJECTIVES = ["Cute", "Simple", "Easy", "Bold", "Fun", "Big"]
LISTING_HEADERS = ["Product Name", "Shop Name", "Price", "Mo. Sales", "Mo. Revenue", "Reviews",
                   "Favorites", "Listing Age", "Category", "Tags", "Link", "Image"]
KEYWORD_HEADERS = ["Keywords", "Avg. Searches", "Avg. Clicks", "Avg. CTR", "Etsy Competition",
                   "Google Searches"]
NOUNS = {"coloring_pages": "coloring pages", "activity_sheets": "activity sheets",
         "stickers": "stickers"}


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
    n = rng.choice([12, 20, 24, 40])
    return f"{adj} {t} Digital Stickers, {n} PNG Clipart, Transparent Background, Pack {i}", n


def write_demo_inputs(inputs_dir: str | Path, listings_per_theme: int = 14, seed: int = 7) -> list[Path]:
    """Write a synthetic EverBee-style listing CSV and eRank-style keyword CSV."""
    rng = random.Random(seed)
    out = Path(inputs_dir)
    out.mkdir(parents=True, exist_ok=True)
    listing_path = out / "everbee-product-analytics-demo.csv"
    keyword_path = out / "erank-keyword-tool-demo.csv"
    lid = 9_100_000_000
    with listing_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(LISTING_HEADERS)
        for niche, themes in THEMES.items():
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
                        f"{theme} {NOUNS[niche]},printable {theme},{theme} for kids",
                        f"https://www.etsy.com/listing/{lid}/demo-item",
                        f"https://i.etsystatic.com/demo/il_570xN.{lid}.jpg",
                    ])
    with keyword_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["eRank Keyword Tool export (synthetic demo data)"])
        w.writerow([])
        w.writerow(KEYWORD_HEADERS)
        for niche, themes in THEMES.items():
            noun = NOUNS[niche]
            for theme in themes:
                for pattern in ("{t} {n}", "printable {t} {n}", "{t} {n} for kids", "easy {t} {n}",
                                "{t} {n} pdf", "cute {t} {n}", "{t} {n} bundle", "simple {t} {n}",
                                "{t} {n} set", "big {t} {n}", "{t} printable", "{t} {n} download"):
                    searches = rng.randint(40, 4000)
                    clicks = int(searches * rng.uniform(0.3, 1.2))
                    w.writerow([pattern.format(t=theme, n=noun), f"{searches:,}", f"{clicks:,}",
                                f"{clicks / searches:.0%}", f"{rng.randint(150, 40000):,}",
                                rng.choice(["1.2k", "880", "N/A", "3,400"])])
    return [listing_path, keyword_path]
