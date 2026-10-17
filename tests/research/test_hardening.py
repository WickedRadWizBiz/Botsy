"""Regression tests: messy files, brand/filler words, threshold edge cases, CLI safety."""
import datetime
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from core.config import ROOT, load_settings
from research import BriefComplianceError, run, validate_brief_doc
from research import pipeline
from research.__main__ import main as cli_main
from research.columns import load_export
from research.defaults import DEFAULTS
from research.guard import extend_blocklist
from research.text import TextRules, pack_count

from .conftest import DATE, ERANK_KW, EVERBEE, keyword_rows, listing_rows, load, write_csv
from .test_guard import clean_doc


def _themes(doc):
    return {t["name"]: t for t in doc["patterns"]["themes"]}


def _run(inputs, out, **kw):
    kw.setdefault("date", DATE)
    return run(inputs_dir=inputs, briefs_dir=out, **kw)


@pytest.fixture(scope="module")
def rules(blocklist):
    full = extend_blocklist(blocklist, DEFAULTS["extra_blocked_terms"], DEFAULTS["extra_review_terms"])
    return TextRules(excluded_terms=DEFAULTS["excluded_terms"], blocklist=full)


# ---------------------------------------------------------------- messy files

def test_short_rows_become_blank_text_not_nan(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("Title,Shop Name,Price,Tags\nFrog Coloring Pages\nToad Coloring Pages,ShopA\n")
    rows = load_export(p).frame.to_dict("records")
    assert [r["shop_name"] for r in rows] == ["", "ShopA"]
    assert all(isinstance(r["tags"], str) for r in rows)


def test_short_rows_do_not_crash_a_run(dirs):
    inputs, out = dirs
    rows = listing_rows("Octopus", 10) + [["Narwhal Coloring Pages"]]   # one truncated row
    write_csv(inputs / "x.csv", EVERBEE + ["Category"], rows)
    doc = load(_run(inputs, out)[0])
    assert "octopus" in _themes(doc)


def test_header_only_and_unreadable_files_are_skipped(dirs):
    inputs, out = dirs
    (inputs / "a-header-only.csv").write_text("Title,Price\n")
    (inputs / "b-empty.csv").write_bytes(b"")
    (inputs / "c-binary.csv").write_bytes(b"PK\x03\x04" + bytes(range(256)) * 8)
    write_csv(inputs / "d.csv", EVERBEE, listing_rows("Octopus", 10))
    doc = load(_run(inputs, out)[0])
    assert "octopus" in _themes(doc)
    assert [s["file"] for s in doc["sources"]] == ["d.csv"]


def test_shop_names_are_captured_from_unmapped_link_columns(tmp_path):
    p = write_csv(tmp_path / "x.csv", ["Title", "Listing URL", "Shop URL"],
                  [["Frog Stickers", "https://www.etsy.com/listing/123/x",
                    "https://www.etsy.com/de/shop/TidePoolPress?ref=x"]])
    e = load_export(p)
    assert "Shop URL" not in e.mapping.values()
    assert e.shop_names == {"TidePoolPress"}


@pytest.mark.parametrize("bad", ["20261015", "2026-W42-4", "2026-02-30", "15/10/2026",
                                 "2026-10-15\n", datetime.date(2026, 10, 15)])
def test_date_must_be_plain_iso(dirs, bad):
    inputs, out = dirs
    write_csv(inputs / "x.csv", EVERBEE, listing_rows("Octopus", 10))
    with pytest.raises(ValueError):
        _run(inputs, out, date=bad)


# ---------------------------------------------------------------- theme text

@pytest.mark.parametrize("title, expected", [
    ("Unicorn Coloring Pages, Includes 20 Pages, 300 DPI, Instant Access, Pre-K",
     {"unicorn"}),
    ("Mermaid Coloring Pages, KDP Interior, Commercial License, Bestseller, Bundle Deal",
     {"mermaid"}),
    ("Ocean Coloring Pages, Black & White Line Drawings, Gift Idea, Stocking Stuffer",
     {"ocean"}),
    ("Back to School Coloring Pages", {"school", "back to school"}),   # "back" never alone
    ("Day of the Dead Coloring Pages", {"day of the dead"}),
    ("Cats & Dogs Stickers", {"cats", "dogs", "cats and dogs"}),
    ("The Farm and Coloring Pages", {"farm"}),          # connectors never start or end a theme
])
def test_filler_words_never_become_themes(rules, title, expected):
    assert set(rules.grams(title).keys.values()) == expected


@pytest.mark.parametrize("title", [
    "Frog Stickers for Happy Planner", "Frog Stickers, Fits Classic Happy Planner",
    "Frog Stickers Erin Condren", "Frog Stickers Hobonichi", "Frog Activity for Google Slides",
    "Frog Boom Cards", "Frog Stickers Traveler's Notebook", "Frog Stickers Precropped Weekly Kit",
])
def test_platform_brands_are_cut_out(rules, title):
    assert set(rules.grams(title).keys.values()) == {"frog"}


@pytest.mark.parametrize("title", ["Grinch Christmas Stickers", "Taylor Swift Eras Stickers",
                                   "Labubu Coloring Pages", "Squishmallows Coloring Pages"])
def test_research_supplement_terms_are_blocked(rules, title):
    g = rules.grams(title)
    assert g.blocked
    assert not {"grinch", "taylor", "swift", "labubu", "squishmallows"} & set(g.keys.values())


def test_extend_blocklist_is_stricter_only(blocklist):
    bl = extend_blocklist(blocklist, hard=["Snow White", "Grinch"], review=["Disney"])
    assert bl.check("snow white").flagged == ["Snow White"]   # stays a review term
    assert bl.check("disney").blocked == ["Disney"]            # stays a hard term
    assert bl.check("grinch").blocked == ["Grinch"]
    assert extend_blocklist(blocklist) is blocklist


def test_generic_heads_do_not_pool_holidays(dirs):
    """Alone, "day" would collect every holiday, rank first and overlap all of them."""
    inputs, out = dirs
    holidays = ["Valentines Day", "Mothers Day", "Fathers Day", "Earth Day", "Day of the Dead"]
    rows = [r for h in holidays for r in listing_rows(h, 10)]
    write_csv(inputs / "x.csv", EVERBEE, rows)
    doc = load(_run(inputs, out)[0])
    assert "day" not in _themes(doc)
    assert sorted(b["theme"] for b in doc["briefs"]) == sorted(h.lower() for h in holidays)


def test_holiday_theme_keeps_its_keyword_cluster(dirs):
    inputs, out = dirs
    write_csv(inputs / "kw.csv", ERANK_KW, keyword_rows("valentines day", 10))
    doc = load(_run(inputs, out)[0])
    cluster = doc["patterns"]["keyword_clusters"][0]
    assert cluster["label"] == "valentines day" and len(cluster["keywords"]) == 10
    assert "valentines day coloring pages" in doc["briefs"][0]["keywords"]


def test_ordinals_are_not_pack_counts():
    assert pack_count("2nd Grade Worksheets") is None
    assert pack_count("1st Day of School, 20 Pages") == 20


# ---------------------------------------------------------------- compliance

def test_blocklist_terms_do_not_match_across_separate_strings(blocklist):
    doc = clean_doc()
    doc["briefs"][0]["keywords"] = ["ocean animals baby", "shark coloring pages"]
    validate_brief_doc(doc, [], [], blocklist=blocklist)   # no "Baby Shark" across strings
    assert blocklist.check("cute baby", "shark").blocked == []
    doc["briefs"][0]["keywords"].append("baby shark coloring pages")
    with pytest.raises(BriefComplianceError, match="Baby Shark"):
        validate_brief_doc(doc, [], [], blocklist=blocklist)


def test_blocked_rows_do_not_count_toward_threshold(dirs):
    inputs, out = dirs
    rows = (listing_rows("Castle", 9) + listing_rows("Frozen Castle", 3)
            + listing_rows("Tower", 10) + listing_rows("Frozen Tower", 2))
    write_csv(inputs / "x.csv", EVERBEE, rows)
    themes = _themes(load(_run(inputs, out)[0]))
    assert "castle" not in themes          # 12 rows, only 9 without a blocked name
    assert themes["tower"]["listing_count"] == 10
    assert "needs_review" not in themes["tower"]   # 2 of 12 rows (17%) < flag_share


def test_competitor_tags_need_ten_clean_listings(dirs):
    inputs, out = dirs
    rows = listing_rows("Octopus", 10) + listing_rows("Bluey Octopus", 6)
    for i, r in enumerate(rows):
        r[7] = "octopus rare tag" if i < 4 else "octopus sea art"
    write_csv(inputs / "x.csv", EVERBEE, rows)
    text = json.dumps(load(_run(inputs, out)[0]))
    assert "octopus rare tag" not in text                 # 4 listings
    assert "octopus sea art" not in text                  # 6 clean + 6 naming a blocked term
    rows2 = listing_rows("Octopus", 10)
    for r in rows2:
        r[7] = "octopus sea art"
    write_csv(inputs / "x.csv", EVERBEE, rows2)
    assert "octopus sea art" in json.dumps(load(_run(inputs, out)[0]))   # 10 clean listings


def test_keyword_column_in_listing_export_is_not_a_keyword_tool_phrase(dirs):
    inputs, out = dirs
    rows = [r + [f"octopus coloring secret {i}"] for i, r in enumerate(listing_rows("Octopus", 10))]
    rows += [r + ["octopus coloring shared"] for r in listing_rows("Octopus", 10)]
    write_csv(inputs / "x.csv", EVERBEE + ["Keyword"], rows)
    text = json.dumps(load(_run(inputs, out)[0]))
    assert "secret" not in text                # one listing each: never used
    assert "octopus coloring shared" in text   # shared by 10 listings: like a tag


def test_compliance_failure_writes_no_niche(dirs, monkeypatch):
    inputs, out = dirs
    write_csv(inputs / "x.csv", EVERBEE, listing_rows("Octopus", 10)
              + listing_rows("Octopus", 10, suffix="Digital Stickers, PNG Clipart"))
    real = pipeline.build_doc

    def leaky(niche, *a, **kw):
        doc = real(niche, *a, **kw)
        if niche == "coloring_pages":
            doc["briefs"][0]["style_notes"] += " see https://example.com/shop"
        return doc

    monkeypatch.setattr(pipeline, "build_doc", leaky)
    with pytest.raises(BriefComplianceError, match="url"):
        _run(inputs, out)
    assert not out.exists() or not list(out.iterdir())
    assert cli_main(["--inputs", str(inputs), "--out", str(out), "--date", DATE]) == 3


# ---------------------------------------------------------------- own shop and trends

def test_publisher_own_stats_csv_is_boost_only(dirs):
    """The publisher writes own_shop_stats.csv with these columns (publisher/own_stats.py)."""
    inputs, out = dirs
    write_csv(inputs / "x.csv", EVERBEE, listing_rows("Octopus", 10) + listing_rows("Narwhal", 10))
    write_csv(inputs / "own_shop_stats.csv",
              ["title", "views", "favorites", "price", "currency", "quantity", "tags", "state", "age_days"],
              [["Narwhal Coloring Pages for Kids", 900, 40, "4.00", "USD", 999, "narwhal", "active", 30]]
              + [[f"Walrus Coloring Page {i}", 10, 1, "4.00", "USD", 999, "", "active", 5]
                 for i in range(12)])
    doc = load(_run(inputs, out)[0])
    themes = _themes(doc)
    assert "walrus" not in themes            # 12 own listings never count
    assert themes["narwhal"]["demand"] > themes["octopus"]["demand"]
    own = [s for s in doc["sources"] if s["tool"] == "own_shop"]
    assert own == [{"tool": "own_shop", "file": "own_shop_stats.csv", "rows": 13,
                    "role": "demand boost only (not counted)"}]


def test_google_trends_timeline_boosts(dirs):
    inputs, out = dirs
    write_csv(inputs / "x.csv", EVERBEE, listing_rows("Octopus", 10) + listing_rows("Narwhal", 10))
    (inputs / "trends-multiTimeline.csv").write_text(
        "Category: All categories\n\nWeek,narwhal: (United States),octopus: (United States)\n"
        "2026-09-20,80,<1\n2026-09-27,90,1\n2026-10-04,100,2\n")
    themes = _themes(load(_run(inputs, out)[0]))
    assert themes["narwhal"]["demand"] > themes["octopus"]["demand"]


# ---------------------------------------------------------------- CLI and determinism

def test_cli_demo_needs_out_or_dry_run(tmp_path, capsys):
    assert cli_main(["--demo", "--date", DATE]) == 2
    assert "--out" in capsys.readouterr().err
    assert cli_main(["--demo", "--inputs", str(tmp_path), "--out", str(tmp_path / "o")]) == 2
    assert cli_main(["--demo", "--out", str(tmp_path / "o"), "--date", DATE]) == 0
    # The demo has rows for every niche; the run writes one file per niche of shop main.
    from research.demo import DEMO_NICHES
    settings = load_settings()
    shop = [n for n in settings["shops"][0].get("niches") or settings["niches"]
            if n in DEMO_NICHES and n in settings["niches"]]
    assert sorted(p.name.split("-")[0] for p in (tmp_path / "o").glob(f"*-{DATE}.json")) == sorted(shop)


def test_output_is_identical_across_hash_seeds(tmp_path):
    outs = []
    for seed in ("1", "4242"):
        out = tmp_path / f"seed{seed}"
        env = {**os.environ, "PYTHONHASHSEED": seed}
        subprocess.run([sys.executable, "-m", "research", "--demo", "--out", str(out),
                        "--date", DATE], cwd=ROOT, env=env, check=True, capture_output=True)
        outs.append({p.name: p.read_bytes() for p in sorted(out.iterdir())})
    from research.demo import DEMO_NICHES
    settings = load_settings()
    shop = [n for n in settings["shops"][0].get("niches") or settings["niches"]
            if n in DEMO_NICHES and n in settings["niches"]]
    assert outs[0] == outs[1] and len(outs[0]) == len(shop)


_FIVE_NICHE_RUN = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("rconf", sys.argv[1])
rconf = importlib.util.module_from_spec(spec); spec.loader.exec_module(rconf)
from research import run
from research.demo import DEMO_NICHES, write_demo_inputs
inputs, out = Path(sys.argv[2]), Path(sys.argv[3])
write_demo_inputs(inputs, niches=DEMO_NICHES)
rows = [[f"Pirate RPG Character Sheet for D&D 5e, Set {i}", rconf.SHOPS[i % 3], "$4.50", 30 - i, 5,
         "900", "3 months", "dnd character sheet,pirate rpg sheet", "", ""] for i in range(14)]
rconf.write_csv(inputs / "everbee-extra.csv", rconf.EVERBEE, rows)
settings = rconf.five_niche_settings()
settings["research"]["briefs_per_niche"] = 6
run(inputs_dir=inputs, briefs_dir=out, date=rconf.DATE, settings=settings)
"""


def test_five_niche_output_is_identical_across_hash_seeds(tmp_path):
    """Stationery and ttrpg too, with the shipped systems registry (a ttrpg brief names one)."""
    outs = []
    conf = str(Path(__file__).with_name("conftest.py"))
    for seed in ("1", "4242"):
        base = tmp_path / f"seed{seed}"
        env = {**os.environ, "PYTHONHASHSEED": seed}
        subprocess.run([sys.executable, "-c", _FIVE_NICHE_RUN, conf, str(base / "in"), str(base / "out")],
                       cwd=ROOT, env=env, check=True, capture_output=True)
        outs.append({p.name: p.read_bytes() for p in sorted((base / "out").iterdir())})
    assert outs[0] == outs[1] and len(outs[0]) == 5
    ttrpg = json.loads(next(v for k, v in outs[0].items() if k.startswith("ttrpg")))
    assert {b["theme"]: b["system"] for b in ttrpg["briefs"]}["pirate"] != "generic"


def test_demo_docs_follow_contract_types(tmp_path):
    from research.demo import write_demo_inputs

    write_demo_inputs(tmp_path / "in")
    settings = load_settings()
    allowed = set(settings["niches"]["activity_sheets"]["types"])
    for p in _run(tmp_path / "in", tmp_path / "out"):
        doc = load(p)
        assert doc["schema"] == "brief/v1" and isinstance(doc["listing_count"], int)
        assert set(doc["patterns"]) == {"themes", "formats", "page_counts", "price_bands",
                                        "keyword_clusters"}
        for i, b in enumerate(doc["briefs"], 1):
            assert b["brief_id"] == f"{doc['niche']}-{DATE}-{i:02d}" and b["niche"] == doc["niche"]
            assert isinstance(b["page_count"], int) and b["page_count"] > 0
            assert isinstance(b["price_usd"], float) and b["price_usd"] > 0
            assert b["page_sizes"] == list(settings["print"]["page_sizes"])
            assert all(isinstance(k, str) and k for k in b["keywords"])
            assert isinstance(b["needs_review"], bool)
            assert set(b["compliance"]) == {"blocked", "flagged"} and b["compliance"]["blocked"] == []
            if doc["niche"] == "activity_sheets":
                assert b["activity_types"] and set(b["activity_types"]) <= allowed
            else:
                assert b["activity_types"] == []
