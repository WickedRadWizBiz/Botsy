"""research.run(): threshold, compliance, keyword-level rule, briefs, determinism."""
import json
import re

import pytest

import research
from research import NoInputsError, run
from research.__main__ import main as cli_main
from research.demo import write_demo_inputs

from .conftest import DATE, ERANK_KW, EVERBEE, keyword_rows, listing_rows, load, write_csv


def _themes(doc):
    return {t["name"]: t for t in doc["patterns"]["themes"]}


def _run(inputs, out, **kw):
    kw.setdefault("date", DATE)
    return run(inputs_dir=inputs, briefs_dir=out, **kw)


def test_ten_listing_threshold(dirs):
    inputs, out = dirs
    rows = listing_rows("Octopus", 10) + listing_rows("Hedgehog", 9)
    write_csv(inputs / "everbee-products.csv", EVERBEE, rows)
    paths = _run(inputs, out, niche="coloring_pages")
    assert [p.name for p in paths] == [f"coloring_pages-{DATE}.json"]
    doc = load(paths[0])
    themes = _themes(doc)
    assert "octopus" in themes and themes["octopus"]["listing_count"] == 10
    assert "hedgehog" not in themes
    assert [b["theme"] for b in doc["briefs"]] == ["octopus"]
    assert doc["min_listings_per_pattern"] == 10
    for kind, entries in doc["patterns"].items():
        assert all(e["listing_count"] >= 10 for e in entries), kind


def test_threshold_setting_cannot_go_below_ten(dirs):
    from core.config import load_settings

    inputs, out = dirs
    settings = load_settings()
    settings["research"]["min_listings_per_pattern"] = 3
    write_csv(inputs / "x.csv", EVERBEE, listing_rows("Hedgehog", 9))
    doc = load(_run(inputs, out, settings=settings)[0])
    assert doc["min_listings_per_pattern"] == 10 and doc["briefs"] == []


def test_blocked_theme_dropped_and_flagged_theme_marked(dirs):
    inputs, out = dirs
    rows = (listing_rows("Bluey Family", 12) + listing_rows("Snow White", 12)
            + listing_rows("Octopus", 12))
    write_csv(inputs / "x.csv", EVERBEE, rows)
    doc = load(_run(inputs, out)[0])
    text = json.dumps(doc).lower()
    assert "bluey" not in text
    themes = _themes(doc)
    assert "family" not in themes          # only ever seen next to a blocked name
    assert themes["snow white"]["needs_review"] is True
    briefs = {b["theme"]: b for b in doc["briefs"]}
    assert briefs["snow white"]["needs_review"] is True
    assert "Snow White" in briefs["snow white"]["compliance"]["flagged"]
    assert briefs["octopus"]["needs_review"] is False
    assert all(b["compliance"]["blocked"] == [] for b in doc["briefs"])


def test_no_urls_shop_names_ids_or_verbatim_titles(dirs):
    inputs, out = dirs
    rows = (listing_rows("SunnyDayPrints Octopus", 12, shop="SunnyDayPrints")
            + listing_rows("Narwhal", 12, shop="TidePoolPress"))
    rows += [["Narwhal Coloring Pages", "TidePoolPress", "$4", 3, 1, 9, "", "", "", ""]] * 1
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    doc = load(_run(inputs, out)[0])
    text = json.dumps(doc)
    low = text.lower()
    assert "http" not in low and "etsy.com" not in low and "etsystatic" not in low
    assert "sunny" not in low and "tidepool" not in low
    assert not re.search(r"\d{7,}", text) and ".jpg" not in low
    for r in rows:
        assert r[0].lower() not in low or len(r[0].split()) < 4
    for key in ("shop_name", "url", "listing_id", "image", "title"):
        assert f'"{key}"' not in text
    assert {"octopus", "narwhal"} <= set(_themes(doc))


def test_keyword_level_rule_with_competition(dirs):
    inputs, out = dirs
    sloth = keyword_rows("sloth", 10)
    sloth[3][4] = "5"            # competition below 10: this row does not count
    otter = keyword_rows("otter", 10)
    otter[0][4] = "Unknown"      # unknown competition does not count either
    write_csv(inputs / "erank-keywords.csv", ERANK_KW, keyword_rows("llama", 10) + sloth + otter)
    doc = load(_run(inputs, out)[0])
    themes = _themes(doc)
    assert themes["llama"]["listing_count"] == 10 and themes["llama"]["keyword_rows"] == 10
    assert "sloth" not in themes and "otter" not in themes
    src = doc["sources"][0]
    assert src["tool"] == "erank" and src["shape"] == "keyword"
    assert "competition >= 10" in src["rule"] and src["counted_rows"] == 28
    brief = doc["briefs"][0]
    assert brief["theme"] == "llama" and "llama coloring pages" in brief["keywords"]
    cluster = doc["patterns"]["keyword_clusters"][0]
    assert cluster["label"] == "llama" and cluster["search_volume"] == 12000


def test_keyword_level_rule_without_competition_column(dirs):
    inputs, out = dirs
    headers = ["Keyword", "Searches"]
    rows = [r[:2] for r in keyword_rows("llama", 10) + keyword_rows("sloth", 9)]
    write_csv(inputs / "kw.csv", headers, rows)
    doc = load(_run(inputs, out)[0])
    assert set(_themes(doc)) == {"llama"}
    assert "no competition column" in doc["sources"][0]["rule"]


def test_listing_and_keyword_rows_are_not_added_to_reach_threshold(dirs):
    inputs, out = dirs
    write_csv(inputs / "a.csv", EVERBEE, listing_rows("Walrus", 6))
    write_csv(inputs / "b.csv", ERANK_KW, keyword_rows("walrus", 6))
    doc = load(_run(inputs, out)[0])
    assert "walrus" not in _themes(doc)


def test_own_shop_and_trends_never_count_but_boost(dirs):
    inputs, out = dirs
    write_csv(inputs / "x.csv", EVERBEE, listing_rows("Hedgehog", 9) + listing_rows("Octopus", 10)
              + listing_rows("Narwhal", 10))
    write_csv(inputs / "own_shop.csv", ["Title", "Views", "Favorites", "Sales"],
              [[f"Hedgehog Coloring Page {i}", 900, 50, 9] for i in range(20)]
              + [["Narwhal Coloring Pages", 1000, 80, 12]])
    write_csv(inputs / "trends-2026.csv", ["Keyword", "Interest"], [["hedgehog", 100]] * 20)
    doc = load(_run(inputs, out)[0])
    themes = _themes(doc)
    assert "hedgehog" not in themes
    assert themes["narwhal"]["demand"] > themes["octopus"]["demand"]
    roles = [s for s in doc["sources"] if s["tool"] in ("own_shop", "trends")]
    assert len(roles) == 2 and all("not counted" in s["role"] for s in roles)
    assert doc["listing_count"] == 29


def test_filename_prefix_wins(dirs):
    inputs, out = dirs
    write_csv(inputs / "stickers__export.csv", EVERBEE, listing_rows("Octopus", 10))
    paths = _run(inputs, out)
    assert [p.name for p in paths] == [f"stickers-{DATE}.json"]
    brief = load(paths[0])["briefs"][0]
    assert brief["page_count"] == 12 and brief["activity_types"] == []   # sticker default pack


def test_brief_fields_follow_contract(dirs):
    inputs, out = dirs
    rows = listing_rows("Octopus", 12, price="$9.99", suffix="Mazes for Kids, 25 Printable Pages")
    write_csv(inputs / "x.csv", EVERBEE, rows)
    doc = load(_run(inputs, out)[0])
    assert doc["niche"] == "activity_sheets" and doc["schema"] == "brief/v1"
    assert doc["created"] == DATE and doc["shop_id"] == "main"
    b = doc["briefs"][0]
    assert b["brief_id"] == f"activity_sheets-{DATE}-01"
    assert b["page_count"] == 20               # 25 is equidistant from 20 and 30 -> smaller
    assert b["price_usd"] == 6.0               # median 9.99 clamped to 1.5 x shop default 4.00
    assert b["page_sizes"] == ["us_letter", "a4"]
    assert b["activity_types"] == ["maze"]
    assert b["audience"] and b["style_notes"] and b["keywords"]
    assert set(b) >= {"brief_id", "niche", "theme", "audience", "keywords", "page_count",
                      "price_usd", "page_sizes", "style_notes", "activity_types", "evidence",
                      "compliance", "needs_review"}
    assert b["evidence"]["listing_count"] == 12
    assert doc["patterns"]["page_counts"] == [{"bucket": "21-30", "listing_count": 12}]
    assert doc["patterns"]["price_bands"][0]["band"] == "8-12"


def test_deterministic_output(tmp_path):
    inputs = tmp_path / "in"
    write_demo_inputs(inputs)
    a = _run(inputs, tmp_path / "a")
    b = _run(inputs, tmp_path / "b")
    assert [p.name for p in a] == [p.name for p in b] and len(a) == 3
    for pa, pb in zip(a, b):
        assert pa.read_bytes() == pb.read_bytes()


def test_demo_inputs_produce_valid_briefs(tmp_path):
    inputs = tmp_path / "in"
    write_demo_inputs(inputs)
    for p in _run(inputs, tmp_path / "out"):
        doc = load(p)
        assert len(doc["briefs"]) == 5
        assert all(b["evidence"]["listing_count"] >= 10 for b in doc["briefs"])


def test_dry_run_writes_nothing(dirs):
    inputs, out = dirs
    write_csv(inputs / "x.csv", EVERBEE, listing_rows("Octopus", 10))
    paths = _run(inputs, out, dry_run=True)
    assert paths and not any(p.exists() for p in paths)


def test_no_inputs_raises_clear_error(dirs):
    inputs, out = dirs
    with pytest.raises(NoInputsError, match="No research exports found"):
        _run(inputs, out)


def test_cli_no_inputs_prints_message(dirs, capsys):
    inputs, out = dirs
    assert cli_main(["--inputs", str(inputs), "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "No research exports found" in err and "Traceback" not in err


def test_cli_demo_dry_run(tmp_path, capsys):
    assert cli_main(["--demo", "--dry-run", "--out", str(tmp_path), "--date", DATE]) == 0
    assert "would write" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_bad_date_and_niche_rejected(dirs):
    inputs, out = dirs
    write_csv(inputs / "x.csv", EVERBEE, listing_rows("Octopus", 10))
    with pytest.raises(ValueError):
        _run(inputs, out, date="tomorrow")
    with pytest.raises(ValueError, match="unknown niche"):
        _run(inputs, out, niche="planners")


def test_package_exports():
    assert research.run is run and callable(research.validate_brief_doc)
