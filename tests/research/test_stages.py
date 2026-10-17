"""Research stages (docs/SERVER.md section 8): draft count, stage resolution, thresholds and
floors, and the refined brief rules (weeks seen, demand not falling, own-shop weight)."""
import hashlib
import json
import logging

import pytest

from core import items
from core.briefs import Guard, doc_problems
from research import history, run, stages
from research.__main__ import main as cli_main

from .conftest import EVERBEE, listing_rows, load, pinned_settings, write_csv

NICHE = "coloring_pages"
# ISO weeks 40, 41 and 42 of 2026 (all Mondays).
WEEK40, WEEK41, WEEK42 = "2026-09-28", "2026-10-05", "2026-10-12"


def _settings(tmp_path=None, stage="refined", **research):
    s = pinned_settings()
    s["research"]["stage"] = stage
    s["research"].update(research)
    if tmp_path is not None:
        s["paths"] = {**s["paths"], "output_dir": str(tmp_path / "output"),
                      "review_dir": str(tmp_path / "output" / "review")}
    return s


def _publish(root, name, **record):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    (d / "publish.json").write_text(json.dumps({"schema": "publish/v1", **record}), encoding="utf-8")
    return d


def _drafts(root, real, dry=0):
    for i in range(real):
        _publish(root, f"2026-10-01-stickers-real-{i:04x}", listing_id=4_000_000_000 + i, dry_run=False)
    for i in range(dry):
        _publish(root, f"2026-10-01-stickers-dry-{i:04x}", listing_id=1 + i, dry_run=True)


def _export(inputs, day, rows, tag="a"):
    """An export with the ingest name <tool>-<date>-<sha8>.csv (received on `day`)."""
    sha8 = hashlib.sha1(f"{day}|{tag}".encode()).hexdigest()[:8]
    return write_csv(inputs / f"everbee-{day}-{sha8}.csv", EVERBEE, rows)


def _run(inputs, out, hist, date, settings, **kw):
    return run(niche=NICHE, inputs_dir=inputs, briefs_dir=out, date=date, settings=settings,
               history_dir=hist, **kw)


def _snap(hist, day, themes):
    doc = {"schema": "history/v1", "niche": NICHE, "date": day, "stage": "refined",
           "thresholds": {"listings": 15, "shops": 3, "weeks": 3}, "sources": [], "themes": themes}
    (hist / day).mkdir(parents=True, exist_ok=True)
    (hist / day / f"{NICHE}.json").write_text(json.dumps(doc), encoding="utf-8")


def _t(name, rows, demand, fresh=5, shops=3, qualified=True):
    return {"name": name, "listing_rows": rows, "keyword_rows": 0, "shop_count": shops,
            "fresh_rows": fresh, "demand": demand, "qualified": qualified}


# ---------------------------------------------------------------- draft count

def test_count_drafts_counts_only_real_etsy_drafts(tmp_path):
    out = tmp_path / "output"
    assert items.count_drafts(out) == 0                      # no folder yet
    _drafts(out, real=2, dry=3)
    _publish(out, "no-listing-id", dry_run=False)            # not created on Etsy
    _publish(out, "zero-id", listing_id=0, dry_run=False)
    _publish(out, "string-id", listing_id="4123456789")      # dry_run missing: a real record
    (out / "broken").mkdir()
    (out / "broken" / "publish.json").write_text("{not json", encoding="utf-8")
    (out / "list").mkdir()
    (out / "list" / "publish.json").write_text("[1, 2]", encoding="utf-8")
    _publish(out / "dry-run", "2026-10-01-stickers-x-aaaa", listing_id=5, dry_run=False)  # nested
    assert items.count_drafts(out) == 3


def test_count_drafts_defaults_to_the_configured_output_dir(tmp_path):
    settings = _settings(tmp_path)
    _drafts(tmp_path / "output", real=4, dry=1)
    assert items.count_drafts(settings=settings) == 4
    assert items.count_drafts(tmp_path / "elsewhere", settings) == 0


# ---------------------------------------------------------------- stage resolution

@pytest.mark.parametrize("real, dry, expected", [(0, 0, "discovery"), (19, 5, "discovery"),
                                                 (20, 0, "refined"), (25, 3, "refined")])
def test_auto_stage_turns_refined_at_twenty_real_drafts(tmp_path, real, dry, expected):
    settings = _settings(tmp_path, stage="auto")
    _drafts(tmp_path / "output", real=real, dry=dry)
    assert stages.resolve_stage(settings) == expected


def test_nineteen_real_drafts_plus_dry_runs_stay_discovery_until_the_twentieth(tmp_path):
    settings = _settings(tmp_path, stage="auto")
    out = tmp_path / "output"
    _drafts(out, real=19, dry=30)                    # practice drafts never count
    assert stages.resolve_stage(settings) == "discovery"
    _publish(out, "2026-10-02-stickers-twentieth-beef", listing_id=4_100_000_000, dry_run=False)
    assert stages.resolve_stage(settings) == "refined"


def test_resolve_stage_settings_and_out_root(tmp_path):
    settings = _settings(tmp_path, stage="auto")
    other = tmp_path / "other"
    _drafts(other, real=20)
    assert stages.resolve_stage(settings) == "discovery"
    assert stages.resolve_stage(settings, out_root=other) == "refined"
    for stage in ("discovery", "refined", "Refined ", None, ""):
        settings["research"]["stage"] = stage
        expected = {"discovery": "discovery", "refined": "refined", "Refined ": "refined"}.get(stage)
        assert stages.resolve_stage(settings) == (expected or "discovery")
    settings["research"]["stage"] = "auto"
    settings["research"]["stages"] = {"refined": {"after_drafts": 3}}
    _drafts(tmp_path / "output", real=3)
    assert stages.resolve_stage(settings) == "refined"
    settings["research"]["stage"] = "strict"
    with pytest.raises(ValueError, match="auto, discovery or refined"):
        stages.resolve_stage(settings)


def test_run_resolves_the_stage_from_the_draft_count(tmp_path, dirs):
    inputs, out = dirs
    _export(inputs, WEEK42, listing_rows("Octopus", 16))
    settings = _settings(tmp_path, stage="auto")
    doc = load(run(niche=NICHE, inputs_dir=inputs, briefs_dir=out, date="2026-10-14", settings=settings)[0])
    assert doc["stage"] == "discovery" and [b["theme"] for b in doc["briefs"]] == ["octopus"]
    _drafts(tmp_path / "output", real=20)
    doc = load(_run(inputs, out, tmp_path / "history", "2026-10-14", settings)[0])
    assert doc["stage"] == "refined" and doc["briefs"] == []          # one week of history
    doc = load(_run(inputs, out, tmp_path / "history", "2026-10-14", settings, stage="discovery")[0])
    assert doc["stage"] == "discovery"                                 # an explicit stage wins
    with pytest.raises(ValueError):
        _run(inputs, out, None, "2026-10-14", settings, stage="strict")


# ---------------------------------------------------------------- thresholds and floors

def test_default_thresholds():
    s = pinned_settings()
    assert stages.stage_thresholds(s, "discovery") == {
        "min_listings": 10, "min_shops": 3, "min_weeks_seen": 0, "demand_not_falling": False,
        "falling_tolerance": 0.0, "own_shop_boost": 0.1}
    assert stages.stage_thresholds(s, "refined") == {
        "min_listings": 15, "min_shops": 3, "min_weeks_seen": 3, "demand_not_falling": True,
        "falling_tolerance": 0.10, "own_shop_boost": 0.25}
    with pytest.raises(ValueError):
        stages.stage_thresholds(s, "auto")


def test_floors_never_drop_below_ten_listings_three_shops_or_discovery():
    s = pinned_settings()
    s["research"].update(min_listings_per_pattern=4, min_shops_per_pattern=1)
    s["research"]["stages"] = {"refined": {"min_listings": 5, "min_shops": 0, "min_weeks_seen": 0,
                                           "falling_tolerance": 7, "own_shop_boost": -1}}
    d, r = stages.stage_thresholds(s, "discovery"), stages.stage_thresholds(s, "refined")
    assert (d["min_listings"], d["min_shops"]) == (10, 3)
    assert (r["min_listings"], r["min_shops"], r["min_weeks_seen"]) == (10, 3, 1)
    assert (r["falling_tolerance"], r["own_shop_boost"]) == (1.0, 0.0)
    # The discovery values are the floor of the refined ones.
    s["research"].update(min_listings_per_pattern=20, min_shops_per_pattern=5)
    s["research"]["stages"] = {"refined": {"min_listings": 15, "min_shops": 3}}
    r = stages.stage_thresholds(s, "refined")
    assert (r["min_listings"], r["min_shops"]) == (20, 5)


def test_empty_stage_settings_mean_defaults():
    s = pinned_settings()
    s["research"]["stages"] = {"refined": {k: None for k in stages.DEFAULTS["stages"]["refined"]}}
    assert stages.stage_thresholds(s, "refined")["min_listings"] == 15
    assert stages.after_drafts(s) == 20
    s["research"]["stages"] = None
    assert stages.stage_thresholds(s, "refined")["min_weeks_seen"] == 3


def test_refined_thresholds_apply_to_patterns_tags_and_briefs(tmp_path, dirs):
    inputs, out = dirs
    rows = listing_rows("Octopus", 16) + listing_rows("Hedgehog", 12)
    _export(inputs, WEEK42, rows)
    doc = load(_run(inputs, out, tmp_path / "h", "2026-10-14", _settings())[0])
    assert (doc["min_listings_per_pattern"], doc["min_shops_per_pattern"], doc["min_weeks_seen"]) == (15, 3, 3)
    assert [t["name"] for t in doc["patterns"]["themes"]] == ["octopus"]   # hedgehog 12 < 15
    for kind, entries in doc["patterns"].items():
        assert all(e["listing_count"] >= 15 for e in entries), kind
    clusters = doc["patterns"]["keyword_clusters"]
    assert all("hedgehog" not in kw for c in clusters for kw in c["keywords"])
    assert "from >= 3 shops" in doc["sources"][0]["rule"] and ">= 15 distinct" in doc["sources"][0]["rule"]
    assert doc_problems(doc, Guard((), ())) == []              # core/briefs.py reads the 15 floor
    snap = load(tmp_path / "h" / "2026-10-14" / f"{NICHE}.json")
    assert snap["stage"] == "refined" and snap["thresholds"] == {"listings": 15, "shops": 3, "weeks": 3}
    assert {t["name"]: t["qualified"] for t in snap["themes"]} == {"octopus": True, "hedgehog": False}


def test_discovery_docs_record_the_stage_without_history_fields(dirs):
    inputs, out = dirs
    _export(inputs, WEEK42, listing_rows("Octopus", 12))
    doc = load(run(niche=NICHE, inputs_dir=inputs, briefs_dir=out, date="2026-10-14",
                   settings=_settings(stage="discovery"))[0])
    assert doc["stage"] == "discovery" and "min_weeks_seen" not in doc
    assert (doc["min_listings_per_pattern"], doc["min_shops_per_pattern"]) == (10, 3)
    assert set(doc["briefs"][0]["evidence"]) == {"listing_count", "listing_rows", "keyword_rows",
                                                 "shop_count", "score", "opportunity",
                                                 "competition", "top_seller_sizes"}


# ---------------------------------------------------------------- refined brief selection

def test_refined_brief_needs_three_weeks_seen_counting_this_run(tmp_path, dirs, caplog):
    inputs, out = dirs
    hist = tmp_path / "history"
    s = _settings()
    _export(inputs, WEEK40, listing_rows("Octopus", 16) + listing_rows("Hedgehog", 16), "w40")
    with caplog.at_level(logging.INFO, logger="research.pipeline"):
        doc = load(_run(inputs, out, hist, WEEK40, s)[0])
    assert doc["briefs"] == [] and {t["name"] for t in doc["patterns"]["themes"]} == {"octopus", "hedgehog"}
    assert "refined stage: no brief yet" in caplog.text and "seen in 1 of 3 weeks" in caplog.text

    _export(inputs, WEEK41, listing_rows("Octopus", 16), "w41")      # hedgehog not exported again
    assert load(_run(inputs, out, hist, WEEK41, s)[0])["briefs"] == []

    _export(inputs, WEEK42, listing_rows("Octopus", 16), "w42")
    doc = load(_run(inputs, out, hist, WEEK42, s)[0])
    assert [b["theme"] for b in doc["briefs"]] == ["octopus"]
    evidence = doc["briefs"][0]["evidence"]
    assert evidence["weeks_seen"] == 3 and evidence["trend"] == 16       # 48 rows, 32 a week ago
    assert evidence["listing_count"] == 48
    # Hedgehog still has 16 listings in the evidence window, but was seen in one week only.
    assert "hedgehog" in {t["name"] for t in doc["patterns"]["themes"]}
    tl = history.timeline(hist, NICHE)
    assert (tl["octopus"]["weeks_seen"], tl["hedgehog"]["weeks_seen"]) == (3, 1)
    assert tl["hedgehog"]["weeks_qualified"] == 3                        # qualified, not seen


def test_a_dry_run_or_no_history_dir_still_reads_history(tmp_path, dirs):
    inputs, out = dirs
    hist = tmp_path / "history"
    _snap(hist, WEEK40, [_t("octopus", 16, 0.8)])
    _snap(hist, WEEK41, [_t("octopus", 16, 0.8)])
    _export(inputs, WEEK42, listing_rows("Octopus", 16))
    s = _settings(history_dir=str(hist))
    doc = load(run(niche=NICHE, inputs_dir=inputs, briefs_dir=out, date=WEEK42, settings=s)[0])
    assert [b["theme"] for b in doc["briefs"]] == ["octopus"]           # read from settings
    assert not (hist / WEEK42).exists()                                  # but not written
    paths = _run(inputs, tmp_path / "dry", hist, WEEK42, s, dry_run=True)
    assert paths and not paths[0].exists() and not (hist / WEEK42).exists()


def test_weeks_with_too_few_fresh_rows_do_not_count(tmp_path, dirs):
    inputs, out = dirs
    hist = tmp_path / "history"
    _snap(hist, WEEK40, [_t("octopus", 16, 0.8, fresh=2)])               # below min_rows (3)
    _snap(hist, WEEK41, [_t("octopus", 16, 0.8, fresh=3)])
    _export(inputs, WEEK42, listing_rows("Octopus", 16))
    s = _settings()
    assert load(_run(inputs, out, hist, WEEK42, s)[0])["briefs"] == []
    s["research"]["history"] = {"min_rows": 2}
    assert [b["theme"] for b in load(_run(inputs, out, hist, WEEK42, s)[0])["briefs"]] == ["octopus"]


def test_snapshots_on_or_after_the_run_date_are_not_earlier_weeks(tmp_path, dirs):
    inputs, out = dirs
    hist = tmp_path / "history"
    _snap(hist, WEEK40, [_t("octopus", 16, 0.8)])
    _snap(hist, "2026-10-19", [_t("octopus", 16, 0.8)])                  # after the run date
    _export(inputs, WEEK42, listing_rows("Octopus", 16))
    assert load(_run(inputs, out, None, WEEK42, _settings(history_dir=str(hist)))[0])["briefs"] == []


def _falling_setup(tmp_path, inputs):
    hist = tmp_path / "history"
    week40 = [_t("octopus", 28, 0.5), _t("walrus", 15, 0.9), _t("penguin", 15, 0.99)]
    # Newest earlier snapshot: octopus had 30 listings, walrus 17; penguin was the best theme.
    week41 = [_t("penguin", 16, 0.99), _t("walrus", 17, 0.9), _t("octopus", 30, 0.5)]
    _snap(hist, WEEK40, week40)
    _snap(hist, WEEK41, week41)
    rows = (listing_rows("Octopus", 16, sales=20) + listing_rows("Walrus", 16, sales=90)
            + listing_rows("Penguin", 16, sales=1))
    _export(inputs, WEEK42, rows)
    return hist


def test_refined_refuses_falling_listing_rows_and_demand_rank(tmp_path, dirs, caplog):
    inputs, out = dirs
    hist = _falling_setup(tmp_path, inputs)
    with caplog.at_level(logging.INFO, logger="research.pipeline"):
        doc = load(_run(inputs, out, None, "2026-10-14", _settings(history_dir=str(hist)))[0])
    # octopus: 30 -> 16 listings (more than 10% down); penguin: best -> worst demand rank;
    # walrus: 17 -> 16 is within the 10% tolerance, and its rank rose.
    assert [b["theme"] for b in doc["briefs"]] == ["walrus"]
    assert doc["briefs"][0]["evidence"]["weeks_seen"] == 3
    assert doc["briefs"][0]["evidence"]["trend"] == -1
    assert "octopus: listing rows fell from 30 to 16" in caplog.text
    assert "penguin: demand rank fell" in caplog.text
    assert {t["name"] for t in doc["patterns"]["themes"]} == {"octopus", "walrus", "penguin"}


def test_demand_not_falling_can_be_turned_off(tmp_path, dirs):
    inputs, out = dirs
    hist = _falling_setup(tmp_path, inputs)
    s = _settings(history_dir=str(hist))
    s["research"]["stages"] = {"refined": {"demand_not_falling": False}}
    doc = load(_run(inputs, out, None, "2026-10-14", s)[0])
    assert [b["theme"] for b in doc["briefs"]] == ["walrus", "octopus", "penguin"]
    s["research"]["stages"] = {"refined": {"falling_tolerance": 0.9}}   # 30 -> 16 is under 90%
    doc = load(_run(inputs, out, None, "2026-10-14", s)[0])
    assert [b["theme"] for b in doc["briefs"]] == ["walrus", "octopus", "penguin"]


def test_a_refused_theme_does_not_block_an_overlapping_one(tmp_path, dirs):
    # The broad theme "farm" (43 rows) ranks first but is new (seen in 1 week), so it is
    # held back; "farm animals" overlaps it and has three weeks: it must still get a brief.
    inputs, out = dirs
    hist = tmp_path / "history"
    _snap(hist, WEEK40, [_t("farm animals", 16, 0.8)])
    _snap(hist, WEEK41, [_t("farm animals", 16, 0.8)])
    rows = (listing_rows("Farm Animals", 16, sales=1) + listing_rows("Farm Barn", 9, sales=90)
            + listing_rows("Farm Tractor", 9, sales=90) + listing_rows("Farm Truck", 9, sales=90))
    _export(inputs, WEEK42, rows)
    s = _settings(history_dir=str(hist))
    s["research"]["stages"] = {"refined": {"demand_not_falling": False}}   # about overlap only
    doc = load(_run(inputs, out, None, WEEK42, s)[0])
    assert [t["name"] for t in doc["patterns"]["themes"]][:2] == ["farm", "farm animals"]
    assert [b["theme"] for b in doc["briefs"]] == ["farm animals"]
    discovery = load(_run(inputs, out, None, WEEK42, _settings(stage="discovery"))[0])
    assert [b["theme"] for b in discovery["briefs"]] == ["farm"]          # overlap rule as before


def test_refined_weighs_our_own_sales_more(tmp_path, dirs):
    inputs, out = dirs
    _export(inputs, WEEK42, listing_rows("Octopus", 16, sales=1) + listing_rows("Walrus", 16, sales=90))
    write_csv(inputs / "own_shop_stats.csv", ["Title", "Views", "Favorites", "Sales"],
              [["Octopus Coloring Pages for Kids", 900, 50, 30]])
    demand = {}
    for stage in ("discovery", "refined"):
        doc = load(_run(inputs, out, tmp_path / stage, "2026-10-14", _settings(stage=stage))[0])
        demand[stage] = {t["name"]: t["demand"] for t in doc["patterns"]["themes"]}
    assert demand["refined"]["octopus"] > demand["discovery"]["octopus"]
    assert demand["refined"]["walrus"] == demand["discovery"]["walrus"]   # no own listing


def test_refined_docs_pass_the_shared_brief_check(tmp_path, dirs, blocklist):
    inputs, out = dirs
    hist = _falling_setup(tmp_path, inputs)
    path = _run(inputs, out, None, "2026-10-14", _settings(history_dir=str(hist)))[0]
    doc = load(path)
    assert doc_problems(doc, Guard((), (), blocklist)) == []
    from generators.shared import brief_problems

    assert brief_problems(doc["briefs"][0], path, blocklist) == []


# ---------------------------------------------------------------- CLI

def test_cli_stage_flag(tmp_path, dirs, capsys):
    inputs, out = dirs
    _export(inputs, WEEK42, listing_rows("Octopus", 16))
    base = ["--inputs", str(inputs), "--out", str(out), "--date", "2026-10-14", "--niche", NICHE,
            "--history", str(tmp_path / "history")]
    assert cli_main(base + ["--stage", "refined"]) == 0
    assert "0 briefs (0 need review), 16 rows, refined stage" in capsys.readouterr().out
    assert load(out / f"{NICHE}-2026-10-14.json")["stage"] == "refined"
    assert cli_main(base + ["--stage", "discovery"]) == 0
    assert "1 briefs (0 need review), 16 rows, discovery stage" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli_main(base + ["--stage", "strict"])


# ---------------------------------------------------------------- review fixes

def test_a_new_theme_ranked_above_is_not_falling_demand(tmp_path, dirs):
    # Octopus was the best theme of the newest earlier snapshot. This week a new theme
    # (penguin) is found with more demand. Octopus's own demand did not fall: compared
    # with the themes both snapshots hold, it is still first.
    inputs, out = dirs
    hist = tmp_path / "history"
    for day in (WEEK40, "2026-10-06"):
        _snap(hist, day, [_t("octopus", 16, 0.9), _t("walrus", 16, 0.5)])
    rows = (listing_rows("Octopus", 16, sales=50) + listing_rows("Walrus", 16, sales=1)
            + listing_rows("Penguin", 16, sales=90))
    _export(inputs, WEEK42, rows)
    doc = load(_run(inputs, out, None, "2026-10-14", _settings(history_dir=str(hist)))[0])
    themes = [t["name"] for t in doc["patterns"]["themes"]]
    assert themes[:2] == ["penguin", "octopus"]                       # penguin is ahead now
    assert "octopus" in [b["theme"] for b in doc["briefs"]]
    assert "penguin" not in [b["theme"] for b in doc["briefs"]]       # seen in 1 week only


def _row(i, kind="listing", shop=None):
    from research.aggregate import Row

    return Row(key=f"{kind}-{i}", kind=kind, grams={}, shop=shop or f"shop-{i % 3}",
               received="2026-10-12")


def _theme(name, listing=0, keyword=0):
    from research.aggregate import Theme

    rows = [_row(i) for i in range(listing)] + [_row(i, "keyword", shop="") for i in range(keyword)]
    return Theme(key=(name,), name=name, rows=rows)


def _gate(previous: dict, current_themes=(), min_listings=15):
    th = {**stages.stage_thresholds(pinned_settings(), "refined"), "min_listings": min_listings}
    earlier = [{"date": "2026-10-05", "themes": [{"name": "octopus", "fresh_rows": 5, **previous}]}]
    current = {"date": "2026-10-14", "themes": list(current_themes)}
    return stages.HistoryGate.build(earlier, current, th, 3)


@pytest.mark.parametrize("previous, now, falling", [
    # gained listings while a few keyword rows left the window: not falling
    ({"listing_rows": 0, "keyword_rows": 12}, {"listing": 20, "keyword": 5}, None),
    # a keyword-backed theme whose handful of listing rows went 4 -> 3: not falling
    ({"listing_rows": 4, "keyword_rows": 20}, {"listing": 3, "keyword": 20}, None),
    # keyword-backed and its keyword rows fell: falling
    ({"listing_rows": 0, "keyword_rows": 30}, {"listing": 0, "keyword": 16}, "keyword rows fell from 30 to 16"),
    # listing rows fell by more than 10%: falling (the spec's rule)
    ({"listing_rows": 30, "keyword_rows": 0}, {"listing": 16, "keyword": 0}, "listing rows fell from 30 to 16"),
    ({"listing_rows": 17, "keyword_rows": 0}, {"listing": 16, "keyword": 0}, None),
])
def test_falling_compares_the_rows_that_count(previous, now, falling):
    gate = _gate(previous)
    assert gate.falling(_theme("octopus", **now)) == falling


def test_damaged_history_values_do_not_stop_research(tmp_path, dirs):
    inputs, out = dirs
    hist = tmp_path / "history"
    bad = {"name": "octopus", "listing_rows": "lots", "keyword_rows": None, "shop_count": 3,
           "fresh_rows": "x", "demand": "high", "qualified": True}
    _snap(hist, WEEK40, [bad, _t("walrus", 16, float("nan"))])
    _snap(hist, WEEK41, [dict(bad, fresh_rows=5)])
    _export(inputs, WEEK42, listing_rows("Octopus", 16))
    doc = load(_run(inputs, out, None, WEEK42, _settings(history_dir=str(hist)))[0])
    assert doc["briefs"] == []                       # a damaged fresh_rows is not "seen"
    _snap(hist, WEEK40, [dict(bad, fresh_rows=5)])
    doc = load(_run(inputs, out, None, WEEK42, _settings(history_dir=str(hist)))[0])
    assert [b["theme"] for b in doc["briefs"]] == ["octopus"]
    assert doc["briefs"][0]["evidence"]["trend"] == 16          # "lots" reads as 0
    tl = history.timeline(hist, NICHE)
    assert tl["octopus"]["weeks_seen"] == 2 and tl["octopus"]["trend"] == 0


def test_count_drafts_reads_text_ids_only_when_they_are_numbers(tmp_path):
    out = tmp_path / "output"
    _publish(out, "a", listing_id=" 4123456789 ", dry_run=False)
    _publish(out, "b", listing_id="draft", dry_run=False)
    _publish(out, "c", listing_id="0", dry_run=False)
    _publish(out, "d", listing_id=True, dry_run=False)
    assert items.count_drafts(out) == 1
