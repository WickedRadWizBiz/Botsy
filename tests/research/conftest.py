"""Synthetic CSV builders for research tests (all data invented, written to tmp_path)."""
import csv
import io
import json
from pathlib import Path

import pytest

from core.compliance import Blocklist
from core.config import load_settings

DATE = "2026-10-15"
EVERBEE = ["Product Name", "Shop Name", "Price", "Mo. Sales", "Reviews", "Favorites",
           "Listing Age", "Tags", "Link", "Thumbnail"]
ERANK_KW = ["Keywords", "Avg. Searches", "Avg. Clicks", "Avg. CTR", "Etsy Competition",
            "Google Searches"]


def write_csv(path: Path, headers, rows, encoding="utf-8", preamble=(), delimiter=","):
    buf = io.StringIO()
    for line in preamble:
        buf.write(line + "\n")
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    w.writerow(headers)
    w.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue().encode(encoding))
    return path


_ids = iter(range(5_000_000_000, 6_000_000_000))


def listing_rows(theme_title: str, n: int, shop="MaplePrintCo", price="$3.99", sales=20,
                 suffix="Coloring Pages for Kids, Printable PDF"):
    """n EverBee-style rows whose titles differ only by a set number."""
    rows = []
    for i in range(1, n + 1):
        lid = next(_ids)
        rows.append([f"{theme_title} {suffix}, Set {i}", shop, price, sales, 5, "1,200",
                     "3 months", f"{theme_title.lower()} coloring", f"https://www.etsy.com/listing/{lid}/x",
                     f"https://i.etsystatic.com/1/il_570xN.{lid}.jpg"])
    return rows


def keyword_rows(theme: str, n: int, competition="1,500", noun="coloring pages"):
    mods = ["", "printable", "easy", "cute", "simple", "big", "bold", "fun", "free", "new",
            "best", "mini"]
    return [[f"{mods[i]} {theme} {noun}".strip(), "1.2k", "300", "25%", competition, "N/A"]
            for i in range(n)]


def pinned_settings() -> dict:
    """config/settings.yaml with the shop, price and pack inputs the tests assert on pinned.

    The real shop defaults are the user's to change; a test that checks a computed
    price or page count must not break when they do.
    """
    s = load_settings()
    s["shops"] = [{**s["shops"][0], "id": "main",
                   "niches": ["stickers", "coloring_pages", "activity_sheets"],
                   "default_price_usd": {"stickers": 3.50, "coloring_pages": 4.00,
                                         "activity_sheets": 4.00}}]
    for niche, opts in (("coloring_pages", [10, 20, 30]), ("activity_sheets", [10, 20, 30])):
        s["niches"][niche]["pages_per_pack"] = opts
    s["niches"]["activity_sheets"]["types"] = ["maze", "word_search", "tracing", "dot_to_dot"]
    s["research"].update({
        "price_clamp": {"min_factor": 0.75, "max_factor": 1.5, "step_usd": 0.25},
        "pack_sizes": {"stickers": [6, 12, 20, 30, 50]},
        "default_pack": {"stickers": 12, "coloring_pages": 20, "activity_sheets": 20},
        "briefs_per_niche": 5,
    })
    s["print"]["page_sizes"] = {"us_letter": {"width_in": 8.5, "height_in": 11.0},
                                "a4": {"width_in": 8.2677, "height_in": 11.6929}}
    s["print"]["deliver_both_sizes"] = True
    return s


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def blocklist():
    return Blocklist.load()


@pytest.fixture
def dirs(tmp_path):
    inputs, out = tmp_path / "inputs", tmp_path / "briefs"
    inputs.mkdir()
    return inputs, out
