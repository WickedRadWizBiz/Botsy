"""Research history snapshots: data/history/<date>/<niche>.json (docs/SERVER.md section 3)."""
import json
import re

import pytest

from core.briefs import Guard
from research import history, run
from research.__main__ import main as cli_main
from research.guard import BriefComplianceError

from .conftest import (DATE, ERANK_KW, EVERBEE, keyword_rows, listing_rows, load,
                       pinned_settings, write_csv)

NICHE = "coloring_pages"


def _run(inputs, out, hist=None, **kw):
    kw.setdefault("date", DATE)
    kw.setdefault("settings", pinned_settings())
    kw.setdefault("niche", NICHE)
    return run(inputs_dir=inputs, briefs_dir=out, history_dir=hist, **kw)


def _snap(hist, day=DATE, niche=NICHE):
    return load(hist / day / f"{niche}.json")


def _themes(snap):
    return {t["name"]: t for t in snap["themes"]}


def _two_shops(theme, n):
    """n rows split over two shops (the brief rule needs three)."""
    half = n // 2
    return (listing_rows(theme, half, shop="MaplePrintCo")
            + listing_rows(theme, n - half, shop="BirchPaperCo"))


def test_history_written_only_with_history_dir(dirs, tmp_path):
    inputs, out = dirs
    hist = tmp_path / "history"
    write_csv(inputs / "everbee.csv", EVERBEE, listing_rows("Octopus", 10))

    paths = _run(inputs, out)
    assert not hist.exists()
    assert _run(inputs, out, hist, dry_run=True) == paths
    assert not hist.exists()                                  # dry run: no history

    assert _run(inputs, out, hist) == paths                   # return value unchanged
    assert sorted(p.name for p in (hist / DATE).iterdir()) == [f"{NICHE}.json"]
    snap = _snap(hist)
    assert snap["schema"] == "history/v1"
    assert snap["niche"] == NICHE and snap["date"] == DATE
    assert snap["thresholds"] == {"listings": 10, "shops": 3}
    assert snap["sources"] == [{"tool": "everbee", "shape": "listing", "rows": 10}]
    assert set(snap["themes"][0]) == {"name", "listing_rows", "keyword_rows", "shop_count",
                                      "demand", "qualified"}


def test_only_niches_with_rows_get_a_snapshot(dirs, tmp_path):
    inputs, out = dirs
    hist = tmp_path / "history"
    write_csv(inputs / "everbee.csv", EVERBEE, listing_rows("Octopus", 10))
    paths = _run(inputs, out, hist, niche=None)
    assert [p.name for p in paths] == [f"{NICHE}-{DATE}.json"]
    assert [p.name for p in (hist / DATE).iterdir()] == [f"{NICHE}.json"]


def test_near_misses_and_qualified_flag(dirs, tmp_path):
    inputs, out = dirs
    hist = tmp_path / "history"
    rows = (listing_rows("Octopus", 10)          # 10 listings, 3 shops: qualified
            + listing_rows("Hedgehog", 9)        # 9 listings: near miss
            + _two_shops("Narwhal", 10))         # 10 listings, 2 shops: near miss
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    write_csv(inputs / "erank.csv", ERANK_KW, keyword_rows("axolotl", 4))
    brief = load(_run(inputs, out, hist)[0])
    themes = _themes(_snap(hist))

    octopus, hedgehog, narwhal = themes["octopus"], themes["hedgehog"], themes["narwhal"]
    assert octopus["qualified"] is True
    assert (octopus["listing_rows"], octopus["shop_count"]) == (10, 3)
    assert hedgehog["qualified"] is False and hedgehog["listing_rows"] == 9
    assert hedgehog["shop_count"] == 3
    assert narwhal["qualified"] is False
    assert (narwhal["listing_rows"], narwhal["shop_count"]) == (10, 2)
    axolotl = themes["axolotl"]                  # keyword-tool rows count separately
    assert axolotl["qualified"] is False
    assert (axolotl["keyword_rows"], axolotl["listing_rows"], axolotl["shop_count"]) == (4, 0, 0)

    # The qualified themes are exactly the brief's themes; near misses never reach a brief.
    qualified = {n for n, t in themes.items() if t["qualified"]}
    assert qualified == {t["name"] for t in brief["patterns"]["themes"]}
    assert [b["theme"] for b in brief["briefs"]] == ["octopus"]
    demands = [t["demand"] for t in _snap(hist)["themes"]]
    assert demands == sorted(demands, reverse=True)


def test_near_miss_needs_min_rows(dirs, tmp_path):
    inputs, out = dirs
    hist = tmp_path / "history"
    rows = listing_rows("Octopus", 10) + listing_rows("Walrus", 2) + listing_rows("Penguin", 3)
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    _run(inputs, out, hist)
    themes = _themes(_snap(hist))
    assert "penguin" in themes and "walrus" not in themes

    settings = pinned_settings()
    settings["research"]["history"] = {"min_rows": 4}
    _run(inputs, out, hist, settings=settings)
    assert "penguin" not in _themes(_snap(hist))


def test_max_themes_cap_keeps_best_demand(dirs, tmp_path):
    inputs, out = dirs
    hist = tmp_path / "history"
    rows = (listing_rows("Octopus", 10, sales=90) + listing_rows("Penguin", 5, sales=50)
            + listing_rows("Walrus", 4, sales=1) + listing_rows("Badger", 3, sales=1))
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    settings = pinned_settings()
    settings["research"]["history"] = {"max_themes": 2}
    _run(inputs, out, hist, settings=settings)
    assert [t["name"] for t in _snap(hist)["themes"]] == ["octopus", "penguin"]


def test_no_shop_names_titles_urls_or_blocked_terms(dirs, tmp_path, blocklist):
    inputs, out = dirs
    hist = tmp_path / "history"
    shops = ("SunnyDayPrints", "TidePoolPress", "MaplePrintCo")
    rows = [r for shop in shops for r in listing_rows("SunnyDayPrints Octopus", 4, shop=shop)]
    # One seller's brand word ("SunnyDay") in a single-shop near miss.
    rows += listing_rows("SunnyDay Turtle", 4, shop="SunnyDayPrints")
    rows += listing_rows("Bluey Family", 4) + listing_rows("Snow White", 4)
    rows += [["Narwhal Under The Sea Coloring Pages", "TidePoolPress", "$4", 3, 1, 9, "", "",
              "https://www.etsy.com/listing/1234567890/narwhal", "il_570xN.1234567890.jpg"]] * 3
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    _run(inputs, out, hist)
    snap = _snap(hist)
    text = json.dumps(snap)
    low = text.lower()
    for bad in ("http", "etsy.com", "etsystatic", ".jpg", "sunny", "tidepool", "maple", "bluey",
                "under the sea coloring pages"):
        assert bad not in low, bad
    assert not re.search(r"\d{9,}", text)
    themes = _themes(snap)
    assert "octopus" in themes and "turtle" in themes
    assert "family" not in themes                 # only ever seen next to a blocked name
    assert themes["snow white"]["needs_review"] is True
    assert "needs_review" not in themes["turtle"]
    titles = [r[0] for r in rows]
    guard = Guard(shops, titles, blocklist)
    assert history.snapshot_problems(snap, guard) == []


def test_snapshot_problem_stops_every_write(dirs, tmp_path, monkeypatch):
    inputs, out = dirs
    hist = tmp_path / "history"
    write_csv(inputs / "everbee.csv", EVERBEE, listing_rows("Octopus", 10))
    real = history.build_snapshot

    def leaky(*args, **kwargs):
        snap = real(*args, **kwargs)
        snap["themes"].append({"name": "see www.example.com", "listing_rows": 3,
                               "keyword_rows": 0, "shop_count": 1, "demand": 0.1,
                               "qualified": False})
        snap["themes"].append({"name": "fake", "listing_rows": 4, "keyword_rows": 0,
                               "shop_count": 1, "demand": 0.1, "qualified": True})
        return snap

    monkeypatch.setattr(history, "build_snapshot", leaky)
    with pytest.raises(BriefComplianceError) as e:
        _run(inputs, out, hist)
    assert any("url" in p for p in e.value.problems)
    assert any("marked qualified below the thresholds" in p for p in e.value.problems)
    assert not hist.exists() and not out.exists()             # no brief written either


def test_timeline_weeks_and_trend(dirs, tmp_path):
    inputs, out = dirs
    hist = tmp_path / "history"
    week42, week43, week43b = "2026-10-12", "2026-10-19", "2026-10-21"
    write_csv(inputs / "a.csv", EVERBEE, listing_rows("Octopus", 10) + listing_rows("Hedgehog", 6))
    _run(inputs, out, hist, date=week42)
    write_csv(inputs / "b.csv", EVERBEE, listing_rows("Hedgehog", 4) + listing_rows("Walrus", 3))
    _run(inputs, out, hist, date=week43)

    assert [s["date"] for s in history.load(hist, NICHE)] == [week42, week43]
    tl = history.timeline(hist, NICHE)
    hedgehog = tl["hedgehog"]
    assert hedgehog["weeks_seen"] == 2 and hedgehog["weeks_qualified"] == 1
    assert (hedgehog["first_seen"], hedgehog["last_seen"]) == (week42, week43)
    assert hedgehog["trend"] == 4
    assert hedgehog["last"]["qualified"] is True and hedgehog["last"]["listing_rows"] == 10
    assert tl["octopus"]["weeks_qualified"] == 2 and tl["octopus"]["trend"] == 0
    assert tl["walrus"]["weeks_seen"] == 1 and tl["walrus"]["trend"] is None
    assert set(hedgehog) == {"weeks_seen", "weeks_qualified", "first_seen", "last_seen",
                             "last", "trend"}

    # A second snapshot in the same ISO week adds no week.
    _run(inputs, out, hist, date=week43b)
    tl = history.timeline(hist, NICHE)
    assert tl["hedgehog"]["weeks_seen"] == 2 and tl["hedgehog"]["last_seen"] == week43b
    assert tl["hedgehog"]["trend"] == 0


def test_load_skips_bad_folders_and_files(tmp_path):
    hist = tmp_path / "history"
    assert history.load(hist, NICHE) == []
    good = {"schema": "history/v1", "niche": NICHE, "thresholds": {"listings": 10, "shops": 3},
            "sources": [], "themes": []}
    for day in ("2026-10-19", "2026-10-05"):
        (hist / day).mkdir(parents=True)
        (hist / day / f"{NICHE}.json").write_text(json.dumps({**good, "date": day}))
    (hist / "notes").mkdir()
    (hist / "notes" / f"{NICHE}.json").write_text(json.dumps({**good, "date": "2026-10-01"}))
    (hist / "2026-13-45").mkdir()
    (hist / "2026-13-45" / f"{NICHE}.json").write_text(json.dumps({**good, "date": "2026-13-45"}))
    (hist / "2026-10-07").mkdir()
    (hist / "2026-10-07" / f"{NICHE}.json").write_text("{not json")
    (hist / "2026-10-08").mkdir()
    (hist / "2026-10-08" / f"{NICHE}.json").write_text(json.dumps({**good, "date": "2026-10-01"}))
    (hist / "2026-10-09").mkdir()
    (hist / "2026-10-09" / f"{NICHE}.json").write_text(json.dumps({**good, "date": "2026-10-09",
                                                                   "niche": "stickers"}))
    assert [s["date"] for s in history.load(hist, NICHE)] == ["2026-10-05", "2026-10-19"]
    assert history.load(hist, "stickers") == []


def test_snapshot_path_refuses_odd_niche_names(tmp_path):
    with pytest.raises(ValueError):
        history.snapshot_path(tmp_path, DATE, "../x")
    with pytest.raises(ValueError):
        history.snapshot_path(tmp_path, "20261015", NICHE)


def test_cli_history_flag(dirs, tmp_path, capsys):
    inputs, out = dirs
    hist = tmp_path / "history"
    write_csv(inputs / "everbee.csv", EVERBEE, listing_rows("Octopus", 10))
    args = ["--inputs", str(inputs), "--out", str(out), "--date", DATE, "--niche", NICHE,
            "--history", str(hist)]
    assert cli_main(args + ["--dry-run"]) == 0
    assert not hist.exists()
    assert "would write 1 history snapshot" in capsys.readouterr().out
    assert cli_main(args) == 0
    assert (hist / DATE / f"{NICHE}.json").is_file()
    assert "wrote 1 history snapshot" in capsys.readouterr().out


def test_cap_keeps_qualified_themes_first(dirs, tmp_path):
    # Penguin (a near miss) has better demand than the qualified octopus. Cutting octopus
    # would make timeline() think a theme research uses right now was "not seen".
    inputs, out = dirs
    hist = tmp_path / "history"
    rows = (listing_rows("Octopus", 10, sales=1) + listing_rows("Penguin", 9, sales=500)
            + listing_rows("Walrus", 8, sales=400))
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    settings = pinned_settings()
    _run(inputs, out, hist, settings=settings)
    assert [t["name"] for t in _snap(hist)["themes"]] == ["penguin", "octopus", "walrus"]

    settings["research"]["history"] = {"max_themes": 1}
    _run(inputs, out, hist, settings=settings)
    assert [(t["name"], t["qualified"]) for t in _snap(hist)["themes"]] == [("octopus", True)]
    settings["research"]["history"] = {"max_themes": 2}
    _run(inputs, out, hist, settings=settings)
    assert [t["name"] for t in _snap(hist)["themes"]] == ["penguin", "octopus"]   # demand order


def test_niche_with_only_near_misses_still_gets_a_snapshot(dirs, tmp_path):
    inputs, out = dirs
    hist = tmp_path / "history"
    write_csv(inputs / "everbee.csv", EVERBEE, listing_rows("Hedgehog", 9))
    brief = load(_run(inputs, out, hist)[0])
    assert brief["briefs"] == []
    assert [(t["name"], t["qualified"]) for t in _snap(hist)["themes"]] == [("hedgehog", False)]


def test_empty_history_settings_mean_defaults(dirs, tmp_path):
    # "min_rows:" left empty in settings.yaml must not stop research.
    settings = pinned_settings()
    settings["research"]["history"] = {"min_rows": None, "max_themes": None}
    settings["research"]["history_dir"] = None
    assert history.history_config(settings) == {"history_dir": "data/history", "min_rows": 3,
                                                 "max_themes": 200}
    inputs, out = dirs
    hist = tmp_path / "history"
    write_csv(inputs / "everbee.csv", EVERBEE, listing_rows("Octopus", 10) + listing_rows("Walrus", 3))
    _run(inputs, out, hist, settings=settings)
    assert {"octopus", "walrus"} <= set(_themes(_snap(hist)))


def _write_snap(hist, day, themes, niche=NICHE):
    doc = {"schema": "history/v1", "niche": niche, "date": day,
           "thresholds": {"listings": 10, "shops": 3}, "sources": [], "themes": themes}
    (hist / day).mkdir(parents=True, exist_ok=True)
    (hist / day / f"{niche}.json").write_text(json.dumps(doc), encoding="utf-8")


def _t(name, rows, qualified=False):
    return {"name": name, "listing_rows": rows, "keyword_rows": 0, "shop_count": 3,
            "demand": 0.5, "qualified": qualified}


def test_timeline_iso_weeks_across_the_new_year(tmp_path):
    # 2026-12-31 (Thu) and 2027-01-01 (Fri) are both ISO week 53 of 2026; 2027-01-04 is
    # week 1 of 2027. Calendar years or week numbers alone would count this wrongly.
    hist = tmp_path / "history"
    _write_snap(hist, "2026-12-31", [_t("snowman", 4)])
    _write_snap(hist, "2027-01-01", [_t("snowman", 6)])
    tl = history.timeline(hist, NICHE)
    assert tl["snowman"]["weeks_seen"] == 1 and tl["snowman"]["trend"] == 2
    _write_snap(hist, "2027-01-04", [_t("snowman", 11, qualified=True)])
    tl = history.timeline(hist, NICHE)
    assert tl["snowman"]["weeks_seen"] == 2 and tl["snowman"]["weeks_qualified"] == 1
    assert tl["snowman"]["trend"] == 5
    assert (tl["snowman"]["first_seen"], tl["snowman"]["last_seen"]) == ("2026-12-31", "2027-01-04")
    # 2027-12-27 is ISO week 52 of 2027, not week 52 of 2026 (2026-12-21) again.
    _write_snap(hist, "2026-12-21", [_t("snowman", 1)])
    _write_snap(hist, "2027-12-27", [_t("snowman", 12, qualified=True)])
    assert history.timeline(hist, NICHE)["snowman"]["weeks_seen"] == 4


def test_load_refuses_odd_niches_and_skips_symlinks(tmp_path):
    hist = tmp_path / "history"
    _write_snap(hist, "2026-10-05", [_t("owl", 3)])
    for bad in ("../outbox", "coloring pages", "", "Stickers"):
        with pytest.raises(ValueError):
            history.load(hist, bad)
    elsewhere = tmp_path / "elsewhere"
    _write_snap(elsewhere, "2026-10-12", [_t("planted", 3)])
    try:
        (hist / "2026-10-12").symlink_to(elsewhere / "2026-10-12", target_is_directory=True)
        (hist / "2026-10-19").mkdir()
        (hist / "2026-10-19" / f"{NICHE}.json").symlink_to(elsewhere / "2026-10-12" / f"{NICHE}.json")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    assert [s["date"] for s in history.load(hist, NICHE)] == ["2026-10-05"]


def test_snapshot_thresholds_keep_the_brief_floor(blocklist):
    guard = Guard((), (), blocklist)
    doc = {"schema": "history/v1", "niche": NICHE, "date": DATE,
           "thresholds": {"listings": 3, "shops": 1}, "sources": [],
           "themes": [_t("owl", 3, qualified=True)]}
    problems = history.snapshot_problems(doc, guard)
    assert any("below the floor" in p for p in problems)
    doc["thresholds"] = {"listings": 10, "shops": 3}
    assert any("marked qualified below the thresholds" in p
               for p in history.snapshot_problems(doc, guard))
    doc["themes"] = [_t("owl", 3)]
    assert history.snapshot_problems(doc, guard) == []


def test_joined_brand_forms_of_one_seller_are_refused(dirs, tmp_path):
    # Sellers put their brand in titles in joined forms the whole-name guard misses:
    # "CraftyFoxCo" (shop TheCraftyFox), "ByMoonbeam" and "Moonbeamkids" (shop Moonbeam...).
    inputs, out = dirs
    hist = tmp_path / "history"
    rows = (listing_rows("Octopus", 10)
            + listing_rows("CraftyFoxCo Owl", 4, shop="TheCraftyFox")
            + listing_rows("ByMoonbeam Fairy", 4, shop="MoonbeamStudio")
            + listing_rows("Moonbeamkids Pony", 4, shop="Moonbeam"))
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    _run(inputs, out, hist)
    snap = _snap(hist)
    low = json.dumps(snap).lower()
    for bad in ("crafty", "moonbeam"):
        assert bad not in low, bad
    assert {"octopus", "owl", "fairy", "pony"} <= set(_themes(snap))   # the design words stay
