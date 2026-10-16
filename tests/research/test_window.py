"""Evidence window and weekly evidence (docs/SERVER.md section 12): received dates from the
ingest name, the ingest ledger or the file time; the 35-day window; fresh_rows and
weeks_seen across ISO weeks."""
import datetime
import json
import logging

import pytest

from research import history, run, window

from .conftest import EVERBEE, listing_rows, load, pinned_settings, set_received, write_csv

NICHE = "coloring_pages"
RUN = "2026-10-15"                 # Thursday of ISO week 42


def _run(inputs, out, hist=None, date=RUN, settings=None):
    return run(niche=NICHE, inputs_dir=inputs, briefs_dir=out, date=date,
               settings=settings or pinned_settings(), history_dir=hist)


def _themes(doc):
    return {t["name"]: t for t in doc["patterns"]["themes"]}


def _ledger(inputs, entries):
    data = {f"{i:064x}": {"file": name, "received": stamp, "original_name": "export.csv"}
            for i, (name, stamp) in enumerate(entries.items(), 1)}
    (inputs / ".ingested.json").write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------- received dates

def test_received_date_sources_in_order(tmp_path):
    named = write_csv(tmp_path / "coloring_pages__everbee-2026-10-14-0123abcd.csv", EVERBEE, [])
    plain = write_csv(tmp_path / "plain.csv", EVERBEE, [])
    set_received(named, "2026-01-02")
    set_received(plain, "2026-09-03")
    ledger = {named.name: "2026-05-05T10:00:00+00:00", plain.name: "2026-10-01T23:30:00"}
    assert window.received_date(named, ledger) == datetime.date(2026, 10, 14)   # name wins
    assert window.received_date(plain, ledger) == datetime.date(2026, 10, 1)    # then the ledger
    assert window.received_date(plain, {}) == datetime.date(2026, 9, 3)         # then the file time
    own = write_csv(tmp_path / "own_shop-everbee-2026-10-10-ffffffff.csv", EVERBEE, [])
    assert window.received_date(own) == datetime.date(2026, 10, 10)
    bad = write_csv(tmp_path / "everbee-2026-02-30-0123abcd.csv", EVERBEE, [])   # not a real date
    set_received(bad, "2026-09-09")
    assert window.received_date(bad, {bad.name: "not a date"}) == datetime.date(2026, 9, 9)


def test_ledger_stamps_are_read_as_local_days(tmp_path):
    stamp = "2026-10-12T12:00:00+00:00"
    local = datetime.datetime.fromisoformat(stamp).astimezone().date()
    p = write_csv(tmp_path / "x.csv", EVERBEE, [])
    assert window.received_date(p, {"x.csv": stamp}) == local
    assert window.received_date(p, {"x.csv": "2026-10-12"}) == datetime.date(2026, 10, 12)


def test_unreadable_ledger_falls_back_to_file_times(tmp_path):
    (tmp_path / ".ingested.json").write_text("{broken", encoding="utf-8")
    assert window.load_ledger(tmp_path) == {}
    assert (tmp_path / ".ingested.json").read_text() == "{broken"     # research never repairs it
    (tmp_path / ".ingested.json").write_text('["a"]', encoding="utf-8")
    assert window.load_ledger(tmp_path) == {}


def test_window_settings():
    s = pinned_settings()
    assert window.window_config(s) == {"window_days": 35, "keep_days": 180}
    s["research"].update(window_days=None, keep_days=0)
    assert window.window_config(s) == {"window_days": 35, "keep_days": 1}


# ---------------------------------------------------------------- the window in research.run

def test_window_by_ingest_name_date(dirs):
    inputs, out = dirs
    write_csv(inputs / "everbee-2026-09-01-aaaaaaaa.csv", EVERBEE, listing_rows("Walrus", 12))
    write_csv(inputs / "everbee-2026-10-14-bbbbbbbb.csv", EVERBEE, listing_rows("Octopus", 12))
    doc = load(_run(inputs, out)[0])            # both files have today's mtime
    assert set(_themes(doc)) == {"octopus"}
    assert [(s["file"], s["received"]) for s in doc["sources"]] == [
        ("everbee-2026-10-14-bbbbbbbb.csv", "2026-10-14")]


def test_window_by_ledger_date(dirs):
    inputs, out = dirs
    write_csv(inputs / "old.csv", EVERBEE, listing_rows("Walrus", 12))
    write_csv(inputs / "new.csv", EVERBEE, listing_rows("Octopus", 12))
    _ledger(inputs, {"old.csv": "2026-08-20T09:00:00+00:00", "new.csv": "2026-10-10T09:00:00+00:00"})
    assert set(_themes(load(_run(inputs, out)[0]))) == {"octopus"}


def test_window_by_file_time_with_the_edge_day(dirs):
    inputs, out = dirs
    set_received(write_csv(inputs / "a.csv", EVERBEE, listing_rows("Walrus", 12)), "2026-09-09")
    set_received(write_csv(inputs / "b.csv", EVERBEE, listing_rows("Octopus", 12)), "2026-09-10")
    set_received(write_csv(inputs / "c.csv", EVERBEE, listing_rows("Narwhal", 12)), "2026-10-20")
    # 2026-09-10 is exactly 35 days before the run: still in. A later file is newer evidence.
    assert set(_themes(load(_run(inputs, out)[0]))) == {"octopus", "narwhal"}
    s = pinned_settings()
    s["research"]["window_days"] = 60
    assert set(_themes(load(_run(inputs, out, settings=s)[0]))) == {"walrus", "octopus", "narwhal"}


def test_old_exports_still_guard_the_output(dirs):
    # A shop name only an out-of-window export holds is still refused in the brief.
    inputs, out = dirs
    old = [r for shop in ("SunnyDayPrints", "TidePoolPress", "MaplePrintCo")
           for r in listing_rows("Narwhal", 4, shop=shop)]
    write_csv(inputs / "everbee-2026-06-01-cccccccc.csv", EVERBEE, old)
    fresh = [r for shop in ("BirchPaperCo", "CedarInkShop", "OakLeafStudio")
             for r in listing_rows("SunnyDayPrints Octopus", 4, shop=shop)]
    write_csv(inputs / "everbee-2026-10-14-dddddddd.csv", EVERBEE, fresh)
    doc = load(_run(inputs, out)[0])
    assert "sunny" not in json.dumps(doc).lower() and "octopus" in _themes(doc)


def test_all_exports_too_old_writes_nothing_and_says_why(dirs, tmp_path, caplog):
    inputs, out = dirs
    write_csv(inputs / "everbee-2026-08-01-eeeeeeee.csv", EVERBEE, listing_rows("Octopus", 12))
    with caplog.at_level(logging.WARNING):
        assert _run(inputs, out, tmp_path / "history") == []
    assert "1 of 1 research exports were received more than 35 days before 2026-10-15" in caplog.text
    assert not out.exists() and not (tmp_path / "history").exists()


# ---------------------------------------------------------------- fresh rows and weeks

def test_fresh_rows_count_exports_received_in_the_snapshot_week(dirs, tmp_path):
    inputs, out = dirs
    hist = tmp_path / "history"
    same = listing_rows("Narwhal", 6)
    write_csv(inputs / "everbee-2026-10-09-11111111.csv", EVERBEE,      # Friday of week 41
              listing_rows("Hedgehog", 10) + same)
    write_csv(inputs / "everbee-2026-10-12-22222222.csv", EVERBEE,      # Monday of week 42
              listing_rows("Octopus", 10) + same)                       # the narwhal rows again
    _run(inputs, out, hist)
    snap = load(hist / RUN / f"{NICHE}.json")
    fresh = {t["name"]: (t["listing_rows"], t["fresh_rows"]) for t in snap["themes"]}
    assert fresh["octopus"] == (10, 10)
    assert fresh["hedgehog"] == (10, 0)          # still in the window, but not this week's
    assert fresh["narwhal"] == (6, 6)            # re-exported this week: fresh again


def test_weeks_seen_counts_weeks_with_fresh_rows(dirs, tmp_path):
    inputs, out = dirs
    hist = tmp_path / "history"
    write_csv(inputs / "everbee-2026-09-28-33333333.csv", EVERBEE,
              listing_rows("Octopus", 10) + listing_rows("Hedgehog", 10))
    _run(inputs, out, hist, date="2026-09-30")                         # week 40
    write_csv(inputs / "everbee-2026-10-06-44444444.csv", EVERBEE, listing_rows("Octopus", 3))
    _run(inputs, out, hist, date="2026-10-07")                         # week 41
    write_csv(inputs / "everbee-2026-10-14-55555555.csv", EVERBEE,
              listing_rows("Octopus", 2) + listing_rows("Walrus", 4))
    _run(inputs, out, hist, date=RUN)                                  # week 42
    _run(inputs, out, hist, date="2026-10-16")                         # same week again
    tl = history.timeline(hist, NICHE)
    # octopus: fresh 10, 3, 2 -> weeks 40 and 41 (2 < min_rows 3 in week 42)
    assert tl["octopus"]["weeks_seen"] == 2 and tl["octopus"]["weeks_qualified"] == 3
    assert tl["octopus"]["last_fresh"] == "2026-10-16" and tl["octopus"]["last_seen"] == "2026-10-16"
    # hedgehog: in every snapshot (inside the window), fresh only in week 40
    assert tl["hedgehog"]["weeks_seen"] == 1 and tl["hedgehog"]["weeks_qualified"] == 3
    assert tl["hedgehog"]["last_fresh"] == "2026-09-30"
    assert tl["walrus"]["weeks_seen"] == 1
    assert history.timeline(hist, NICHE, min_rows=2)["octopus"]["weeks_seen"] == 3
    s = pinned_settings()
    s["research"]["history"] = {"min_rows": 11}
    assert history.timeline(hist, NICHE, settings=s)["octopus"]["weeks_seen"] == 0


def test_weekly_qualification_uses_each_weeks_window(dirs, tmp_path):
    # An export drops out of the window, and the theme stops qualifying that week.
    inputs, out = dirs
    hist = tmp_path / "history"
    write_csv(inputs / "everbee-2026-09-01-66666666.csv", EVERBEE, listing_rows("Octopus", 10))
    _run(inputs, out, hist, date="2026-09-02")
    write_csv(inputs / "everbee-2026-10-14-77777777.csv", EVERBEE, listing_rows("Octopus", 4))
    _run(inputs, out, hist, date=RUN)
    tl = history.timeline(hist, NICHE)
    assert tl["octopus"]["weeks_qualified"] == 1 and tl["octopus"]["weeks_seen"] == 2
    assert tl["octopus"]["last"] == {**tl["octopus"]["last"], "listing_rows": 4, "qualified": False}


def test_old_snapshots_without_fresh_rows_count_as_seen(tmp_path):
    hist = tmp_path / "history"
    for day, rows in (("2026-10-05", 4), ("2026-10-12", 5)):
        doc = {"schema": "history/v1", "niche": NICHE, "date": day,
               "thresholds": {"listings": 10, "shops": 3}, "sources": [],
               "themes": [{"name": "owl", "listing_rows": rows, "keyword_rows": 0, "shop_count": 2,
                           "demand": 0.5, "qualified": False}]}
        (hist / day).mkdir(parents=True)
        (hist / day / f"{NICHE}.json").write_text(json.dumps(doc), encoding="utf-8")
    tl = history.timeline(hist, NICHE)
    assert tl["owl"]["weeks_seen"] == 2 and tl["owl"]["last_fresh"] == "2026-10-12"


@pytest.mark.parametrize("day, week", [("2026-12-31", (2026, 53)), ("2027-01-04", (2027, 1)),
                                       ("2026-10-12", (2026, 42)), ("2026-10-18", (2026, 42))])
def test_iso_week(day, week):
    assert window.iso_week(day) == week


def test_recent_applies_the_same_rule(tmp_path):
    a = write_csv(tmp_path / "everbee-2026-09-01-aaaaaaaa.csv", EVERBEE, [])
    b = write_csv(tmp_path / "b.csv", EVERBEE, [])
    c = set_received(write_csv(tmp_path / "c.csv", EVERBEE, []), "2026-10-01")
    _ledger(tmp_path, {"b.csv": "2026-10-14T08:00:00"})
    assert window.recent([a, b, c, tmp_path / "gone.csv"], RUN, pinned_settings()) == [b, c]
    assert window.recent([], RUN, pinned_settings()) == []


# ---------------------------------------------------------------- review fixes

def test_old_trend_files_stop_boosting_but_own_shop_stats_stay(dirs):
    inputs, out = dirs
    write_csv(inputs / "everbee-2026-10-14-12121212.csv", EVERBEE,
              listing_rows("Octopus", 10) + listing_rows("Narwhal", 10))
    old = write_csv(inputs / "trends-google-2026-08-01-abababab.csv", ["Keyword", "Interest"],
                    [["narwhal", 100]] * 5)
    own = set_received(write_csv(inputs / "own_shop_stats.csv", ["Title", "Views", "Favorites", "Sales"],
                                 [["Octopus Coloring Pages", 900, 50, 9]]), "2026-01-05")
    doc = load(_run(inputs, out)[0])
    boosts = {s["file"] for s in doc["sources"] if "role" in s}
    assert boosts == {own.name}                      # own stats kept, the old trend file not
    plain = _themes(doc)
    old.rename(inputs / "trends-google-2026-10-13-abababab.csv")      # a fresh trend file
    doc = load(_run(inputs, out)[0])
    assert {s["file"] for s in doc["sources"] if "role" in s} == {
        own.name, "trends-google-2026-10-13-abababab.csv"}
    assert _themes(doc)["narwhal"]["demand"] > plain["narwhal"]["demand"]


def test_odd_ledger_stamps_and_file_times_never_crash(tmp_path):
    p = set_received(write_csv(tmp_path / "x.csv", EVERBEE, []), "2026-10-03")
    for stamp in ("0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59-12:00", 12345, None, ""):
        day = window.received_date(p, {"x.csv": stamp})
        assert isinstance(day, datetime.date)
    assert window.received_date(p, {"x.csv": 12345}) == datetime.date(2026, 10, 3)
    with pytest.raises(OSError):
        window.received_date(tmp_path / "gone.csv", {})
