"""Synthetic CSV builders for research tests (all data invented, written to tmp_path)."""
import csv
import datetime
import io
import json
import os
from pathlib import Path

import pytest

from core import items
from core.compliance import Blocklist
from core.config import ROOT, load_settings

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


SHOPS = ("MaplePrintCo", "BirchPaperCo", "CedarInkShop")


def listing_rows(theme_title: str, n: int, shop=None, price="$3.99", sales=20,
                 suffix="Coloring Pages for Kids, Printable PDF"):
    """n EverBee-style rows whose titles differ only by a set number. By default the rows
    rotate over three shops (research.min_shops_per_pattern is 3); pass `shop` to put
    every row in one shop."""
    rows = []
    for i in range(1, n + 1):
        lid = next(_ids)
        row_shop = shop if shop is not None else SHOPS[i % len(SHOPS)]
        rows.append([f"{theme_title} {suffix}, Set {i}", row_shop, price, sales, 5, "1,200",
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
        "base_pack_sizes": [5, 20],
        "briefs_per_niche": 5,
    })
    s["packs"] = {**(s.get("packs") or {}), "min_size": 5,
                  "max_size": {"stickers": 60, "coloring_pages": 50, "activity_sheets": 50,
                               "stationery": 60, "ttrpg": 40}}
    s["print"]["page_sizes"] = {"us_letter": {"width_in": 8.5, "height_in": 11.0},
                                "a4": {"width_in": 8.2677, "height_in": 11.6929}}
    s["print"]["deliver_both_sizes"] = True
    # Pinned so a test never counts the drafts in the repo's own output/ folder; the stage
    # tests (test_stages.py) set it themselves.
    s["research"]["stage"] = "discovery"
    return s


NEW_NICHES = ("stationery", "ttrpg")


def five_niche_settings() -> dict:
    """pinned_settings() with the stationery and ttrpg niches in settings and in shop main
    (docs/PACKS.md section 1), whatever config/settings.yaml lists today."""
    s = pinned_settings()
    for niche in NEW_NICHES:
        s["niches"].setdefault(niche, {})
    s["shops"][0]["niches"] = [*s["shops"][0]["niches"], *NEW_NICHES]
    return s


# Game-system `!term` entries for tests (docs/PACKS.md section 4), with our own test ids.
SYSTEM_TERMS = {"Dungeons & Dragons": "srd5", "D&D": "srd5", "DnD": "srd5", "5e": "srd5",
                "Fifth Edition": "srd5", "Wizards of the Coast": "srd5",
                "Pathfinder": "pf2", "Paizo": "pf2"}


class SystemsBlocklist(Blocklist):
    """A Blocklist with `!term` game-system entries, whatever the installed core.compliance
    does with them: they are blocked in check() (the brief rule: never in a brief) and
    listed in system_terms (research cuts them, and a row naming one still counts)."""

    def __init__(self, hard=(), review=(), systems=None):
        self._systems = dict(SYSTEM_TERMS if systems is None else systems)
        super().__init__([*hard, *self._systems], review)

    @property
    def system_terms(self) -> dict:
        return dict(self._systems)

    @system_terms.setter
    def system_terms(self, value) -> None:   # a newer Blocklist.__init__ may assign it
        pass


def systems_blocklist(**kw) -> SystemsBlocklist:
    """The repo's blocklist (hard and review terms) plus the test game systems."""
    base = Blocklist.load()
    names = {n.lower() for n in SYSTEM_TERMS}
    return SystemsBlocklist([t for t, _ in base.hard if t.lstrip("!").lower() not in names],
                            [t for t, _ in base.review], **kw)


def set_received(path: Path, day: str) -> Path:
    """Give an export the received date `day` through its modification time (the last of
    research's three date sources), at noon local time so the calendar day is certain."""
    t = datetime.datetime.fromisoformat(f"{day}T12:00:00").timestamp()
    os.utime(path, (t, t))
    return path


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


@pytest.fixture(autouse=True)
def _never_count_the_repos_drafts(monkeypatch):
    """research.run() with research.stage auto counts the real Etsy drafts in the configured
    output dir. Tests that use the repo's settings must not read the repo's own output/
    folder, so the count there is 0 (any other folder, such as tmp_path, is counted)."""
    real = items.count_drafts
    repo_out = (ROOT / "output").resolve()

    def count(out_root=None, settings=None):
        base = Path(out_root) if out_root else items.output_root(settings)
        return 0 if base.resolve() == repo_out else real(out_root, settings)

    monkeypatch.setattr(items, "count_drafts", count)

