"""Weekly search list (docs/SERVER.md section 4): seeds, near misses, rechecks, blocklist, CRLF."""
import json
import os
import re
import stat

import pytest

from core.compliance import Blocklist
from research import run
from service import searchlist
from tests.research.conftest import EVERBEE, listing_rows, pinned_settings, write_csv

DAY = "2026-10-18"


def _settings(**search_list):
    s = pinned_settings()
    if search_list:
        s.setdefault("service", {})["search_list"] = search_list
    return s


def _snap(hist, day, niche, themes, thresholds=None):
    doc = {"schema": "history/v1", "niche": niche, "date": day,
           "thresholds": thresholds or {"listings": 10, "shops": 3},
           "sources": [{"tool": "everbee", "shape": "listing", "rows": 50}], "themes": themes}
    path = hist / day / f"{niche}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _theme(name, rows=5, shops=2, demand=0.5, qualified=False, **extra):
    return {"name": name, "listing_rows": rows, "keyword_rows": 0, "shop_count": shops,
            "demand": demand, "qualified": qualified, **extra}


def _section(text, title):
    """The search lines of one niche section (notes in brackets on their own are left out)."""
    lines = text.split("\r\n")
    i = lines.index(title)
    assert lines[i + 1] == "-" * len(title)
    out = []
    for ln in lines[i + 2:]:
        if not ln:
            break
        if not ln.strip().startswith("("):
            out.append(ln)
    return out


def test_empty_history_gives_seeds_and_a_note(tmp_path):
    text = searchlist.build(_settings(), tmp_path / "history", date=DAY)
    assert re.search(r"(?<!\r)\n", text) is None and text.endswith("\r\n")   # CRLF only
    assert "Marketplace Insights" in text and "etsy-insights-template.csv" in text
    assert "Save As" in text and "Downloads" in text and "eRank Basic" in text
    assert "EverBee" not in text and "10 minutes" not in text
    assert "(check: health?)" in text                     # the legend explains the note
    assert "smarter after your first exports" in text
    assert "Sunday 18 October 2026" in text
    assert _section(text, "COLORING PAGES") == [
        "  coloring pages for kids  (every week)", "  printable coloring pages  (every week)"]
    assert _section(text, "STICKERS") == [
        "  digital stickers  (every week)", "  digital planner stickers  (every week)"]
    assert _section(text, "ACTIVITY SHEETS") == [
        "  kids activity sheets  (every week)", "  printable mazes for kids  (every week)"]
    assert "goodnotes" not in text.lower()
    assert "Last week: no numbers yet" in text
    assert "No exports for this niche yet" not in text    # the header note covers it


def test_default_seeds_are_blocklist_clean():
    blocklist = Blocklist.load()
    for niche, seeds in searchlist.DEFAULTS["search_list"]["seeds"].items():
        assert seeds, niche
        for seed in seeds:
            result = blocklist.check(seed)
            assert not result.blocked and not result.flagged, seed


def test_sections_follow_the_shop_niches(tmp_path):
    s = _settings()
    s["shops"][0]["niches"] = ["coloring_pages"]
    text = searchlist.build(s, tmp_path, date=DAY)
    assert "COLORING PAGES" in text and "STICKERS" not in text and "ACTIVITY SHEETS" not in text


def test_near_misses_with_progress_best_demand_first(tmp_path):
    hist = tmp_path / "history"
    _snap(hist, "2026-10-11", "coloring_pages", [_theme("narwhal", 3, 1, 0.2)])
    _snap(hist, "2026-10-17", "coloring_pages", [
        _theme("farm animals", 23, 7, 0.81, qualified=True),
        _theme("narwhal", 4, 2, 0.2),
        _theme("axolotl", 7, 2, 0.4),
        _theme("hedgehog", 12, 2, 0.3),
        _theme("platypus", 9, 1, 0.9),      # one shop's catalog: not a lead (section 12)
    ])
    lines = _section(searchlist.build(_settings(), hist, date=DAY), "COLORING PAGES")
    assert lines == [
        "  coloring pages for kids  (every week)",
        "  printable coloring pages  (every week)",
        "  axolotl coloring pages  (7 of 10 listings, 2 of 3 shops so far)",
        "  hedgehog coloring pages  (enough listings, 2 of 3 shops so far)",
        "  narwhal coloring pages  (4 of 10 listings, 2 of 3 shops so far)",  # newest numbers
    ]


def test_niche_without_history_says_so(tmp_path):
    hist = tmp_path / "history"
    _snap(hist, "2026-10-17", "coloring_pages", [_theme("axolotl", 7, 2, 0.4)])
    text = searchlist.build(_settings(), hist, date=DAY)
    assert "smarter after your first exports" not in text
    lines = text.split("\r\n")
    sticker_block = lines[lines.index("STICKERS"):lines.index("COLORING PAGES")]
    assert any("No exports for this niche yet" in ln for ln in sticker_block)


def test_recheck_lists_qualified_themes_not_seen_for_two_weeks(tmp_path):
    hist = tmp_path / "history"
    _snap(hist, "2026-09-27", "coloring_pages", [_theme("farm animals", 23, 7, 0.8, True),
                                                 _theme("ocean animals", 15, 4, 0.6, True)])
    _snap(hist, "2026-10-08", "coloring_pages", [_theme("dinosaurs", 14, 5, 0.7, True),
                                                 _theme("ocean animals", 15, 4, 0.6, True)])
    _snap(hist, "2026-10-17", "coloring_pages", [_theme("ocean animals", 16, 4, 0.6, True)])
    lines = _section(searchlist.build(_settings(), hist, date=DAY), "COLORING PAGES")
    assert "  farm animals coloring pages  (recheck: last seen 3 weeks ago)" in lines
    assert not any("dinosaurs" in ln for ln in lines)        # seen 10 days ago
    assert not any("ocean animals" in ln for ln in lines)    # qualified and current


def test_blocked_phrases_dropped_and_flagged_marked(tmp_path):
    hist = tmp_path / "history"
    _snap(hist, "2026-10-17", "stickers", [
        _theme("bluey", 8, 2, 0.9),                       # hard-blocked: never listed
        _theme("snow white", 6, 2, 0.8, needs_review=True),
        _theme("peter rabbit", 5, 2, 0.7),                # review term found by the phrase check
    ])
    s = _settings(seeds={"stickers": ["disney stickers", "goodnotes stickers", "frog stickers"]})
    text = searchlist.build(s, hist, date=DAY)
    assert "bluey" not in text.lower() and "disney" not in text.lower()
    lines = _section(text, "STICKERS")
    assert lines[:2] == ["  goodnotes stickers  (every week)  (check: trademark?)",
                         "  frog stickers  (every week)"]
    assert "  snow white stickers  (6 of 10 listings, 2 of 3 shops so far)  (check: trademark?)" in lines
    assert "  peter rabbit stickers  (5 of 10 listings, 2 of 3 shops so far)  (check: trademark?)" in lines


def test_max_per_niche_keeps_a_slot_for_rechecks(tmp_path):
    hist = tmp_path / "history"
    _snap(hist, "2026-09-20", "activity_sheets", [_theme("alphabet", 20, 5, 0.5, True)])
    _snap(hist, "2026-10-17", "activity_sheets",
          [_theme(f"theme{c}", 5, 2, 0.9 - i / 100) for i, c in enumerate("abcdefghij")])
    lines = _section(searchlist.build(_settings(), hist, date=DAY), "ACTIVITY SHEETS")
    assert len(lines) == 6
    assert [ln.split("  (")[0].strip() for ln in lines] == [
        "kids activity sheets", "printable mazes for kids", "themea activity sheets",
        "themeb activity sheets", "themec activity sheets", "alphabet activity sheets"]
    assert "recheck" in lines[-1]

    small = _section(searchlist.build(_settings(max_per_niche=3), hist, date=DAY), "ACTIVITY SHEETS")
    assert len(small) == 3 and "themea" in small[2]


def test_repeated_phrases_listed_once(tmp_path):
    hist = tmp_path / "history"
    _snap(hist, "2026-10-17", "coloring_pages", [_theme("farm", 5, 2, 0.4)])
    s = _settings(seeds={"coloring_pages": ["Farm  Coloring Pages", "farm coloring pages"]})
    lines = _section(searchlist.build(s, hist, date=DAY), "COLORING PAGES")
    assert lines == ["  Farm Coloring Pages  (every week)"]


def test_footer_shows_last_weeks_numbers(tmp_path):
    status = {"exports": 3, "briefs": 1, "items": 5, "drafts": 0}
    text = searchlist.build(_settings(), tmp_path, date=DAY, status=status)
    assert ("Last week: 3 exports received, 1 brief file written, 5 items made, "
            "0 Etsy drafts created.\r\n") in text
    longer = {"exports_received": 1, "drafts_created": 2}
    text = searchlist.build(_settings(), tmp_path, date=DAY, status=longer)
    assert "Last week: 1 export received, 2 Etsy drafts created." in text


def test_write_saves_latest_and_dated_copy_with_crlf(tmp_path):
    outbox = tmp_path / "outbox"
    path = searchlist.write(outbox, "line one\nline two\r\nthree\n", DAY)
    assert path == outbox / "search-list.txt"
    dated = outbox / f"search-list-{DAY}.txt"
    assert path.read_bytes() == dated.read_bytes() == b"line one\r\nline two\r\nthree\r\n"
    assert sorted(p.name for p in outbox.iterdir()) == [
        "etsy-insights-template.csv", "kdp-manual-template.csv",   # the blank templates ride along
        "search-list-2026-10-18.txt", "search-list.txt"]           # no temp files left
    for name in searchlist.TEMPLATE_NAMES:
        assert (outbox / name).read_bytes() == (searchlist.TEMPLATES_DIR / name).read_bytes()
        assert (outbox / name).stat().st_mode & 0o777 == searchlist.FILE_MODE
    only = tmp_path / "only-list"
    searchlist.write(only, "x\n", DAY, templates=False)
    assert sorted(p.name for p in only.iterdir()) == ["search-list-2026-10-18.txt", "search-list.txt"]
    with pytest.raises(ValueError):
        searchlist.write(outbox, "x", "18-10-2026")
    with pytest.raises(ValueError):
        searchlist.build(_settings(), tmp_path, date="2026-02-30")


def test_build_from_a_real_research_run(tmp_path):
    inputs, briefs, hist = tmp_path / "inputs", tmp_path / "briefs", tmp_path / "history"
    write_csv(inputs / "everbee.csv", EVERBEE, listing_rows("Octopus", 10) + listing_rows("Hedgehog", 9))
    run(niche="coloring_pages", inputs_dir=inputs, briefs_dir=briefs, date="2026-10-17",
        settings=pinned_settings(), history_dir=hist)
    lines = _section(searchlist.build(_settings(), hist, date=DAY), "COLORING PAGES")
    assert "  hedgehog coloring pages  (9 of 10 listings, 3 of 3 shops so far)" in lines
    assert not any("octopus" in ln for ln in lines)          # qualified and current


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes (the server runs Linux)")
def test_outbox_files_are_readable_by_the_upload_group(tmp_path):
    # The outbox is group botsy (mode 2750); the upload account downloads from it.
    # A temp-file write is mode 600 unless it is changed before the rename.
    outbox = tmp_path / "outbox"
    latest = searchlist.write(outbox, "x\n", DAY)
    for p in (latest, outbox / f"search-list-{DAY}.txt"):
        assert stat.S_IMODE(p.stat().st_mode) == 0o640, p.name


def test_keyword_only_near_miss_says_where_it_was_seen(tmp_path):
    hist = tmp_path / "history"
    _snap(hist, "2026-10-17", "coloring_pages", [
        {"name": "unicorn", "listing_rows": 0, "keyword_rows": 6, "shop_count": 0,
         "demand": 0.4, "qualified": False},
        {"name": "", "listing_rows": 5, "keyword_rows": 0, "shop_count": 2,
         "demand": 0.9, "qualified": False},                    # nameless: skipped
    ])
    lines = _section(searchlist.build(_settings(), hist, date=DAY), "COLORING PAGES")
    assert lines[2:] == ["  unicorn coloring pages  (no listings yet, seen in 6 keyword searches)"]


def test_footer_explains_practice_drafts(tmp_path):
    status = {"exports": 2, "briefs": 1, "items": 4, "drafts": 0, "practice_drafts": 4}
    text = searchlist.build(_settings(), tmp_path, date=DAY, status=status)
    assert ("0 Etsy drafts created, 4 practice drafts (not sent to Etsy while live mode "
            "is off).") in text


def test_empty_settings_values_and_a_single_seed(tmp_path):
    # "max_per_niche:" left empty means the default (6), and a seed given as one phrase
    # instead of a list must not turn into one search per letter.
    s = _settings(max_per_niche=None, recheck_weeks=None,
                  seeds={"coloring_pages": "farm coloring pages"})
    cfg = searchlist.search_config(s)
    assert cfg["max_per_niche"] == 6 and cfg["recheck_weeks"] == 2
    hist = tmp_path / "history"
    _snap(hist, "2026-10-17", "coloring_pages",
          [_theme(f"theme{c}", 5, 2, 0.9 - i / 100) for i, c in enumerate("abcdefgh")])
    lines = _section(searchlist.build(s, hist, date=DAY), "COLORING PAGES")
    assert lines[0] == "  farm coloring pages  (every week)"
    assert len(lines) == 6

    for empty in ({"service": {"search_list": None}}, {"service": {"search_list": {"seeds": None}}}):
        s = {**pinned_settings(), **empty}
        cfg = searchlist.search_config(s)
        assert cfg["seeds"] == searchlist.DEFAULTS["search_list"]["seeds"]
        assert cfg["max_per_niche"] == 6


def test_date_in_the_title_has_no_leading_zero(tmp_path):
    text = searchlist.build(_settings(), tmp_path, date="2026-10-04")
    assert text.startswith("BOTSY WEEKLY SEARCH LIST - Sunday 4 October 2026\r\n")


# ---------------------------------------------------------------- stages and weekly evidence
# (docs/SERVER.md sections 8 and 12)

def test_refined_progress_shows_stage_thresholds_and_weeks(tmp_path):
    hist = tmp_path / "history"
    _snap(hist, "2026-10-10", "coloring_pages", [_theme("axolotl", 4, 2, 0.4, fresh_rows=4)])
    _snap(hist, "2026-10-17", "coloring_pages", [
        _theme("dinosaurs", 16, 4, 0.9, qualified=True, fresh_rows=3),
        _theme("farm animals", 20, 5, 0.8, qualified=True, fresh_rows=5),
        _theme("hedgehog", 12, 3, 0.6, fresh_rows=12),
        _theme("axolotl", 7, 2, 0.4, fresh_rows=3),
        {"name": "unicorn", "listing_rows": 0, "keyword_rows": 6, "shop_count": 0,
         "fresh_rows": 6, "demand": 0.3, "qualified": False},
    ], thresholds={"listings": 15, "shops": 3, "weeks": 3})
    _snap(hist, "2026-10-03", "coloring_pages", [_theme("dinosaurs", 16, 4, 0.9, True, fresh_rows=3)])
    _snap(hist, "2026-09-26", "coloring_pages", [_theme("dinosaurs", 16, 4, 0.9, True, fresh_rows=3)])
    text = searchlist.build(_settings(), hist, date=DAY, stage="refined")
    assert "stricter stage" in text and "seen in 3 different weeks" in text
    assert "Botsy needs 15 listings from 3 different" in text
    lines = _section(text, "COLORING PAGES")
    assert lines[2:] == [
        "  farm animals coloring pages  (enough listings and shops, seen 1 of 3 weeks)",
        "  hedgehog coloring pages  (12 of 15 listings, 3 of 3 shops, seen 1 of 3 weeks)",
        "  axolotl coloring pages  (7 of 15 listings, 2 of 3 shops, seen 2 of 3 weeks)",
        "  unicorn coloring pages  (no listings yet, in 6 keyword searches, seen 1 of 3 weeks)",
    ]                                    # dinosaurs: seen in 3 weeks, ready (not listed)

    # The same history in the discovery stage: 10 listings, no weeks.
    text = searchlist.build(_settings(), hist, date=DAY, stage="discovery")
    assert "stricter stage" not in text and "Botsy needs 10 listings" in text
    lines = _section(text, "COLORING PAGES")
    assert lines[2:] == ["  axolotl coloring pages  (7 of 10 listings, 2 of 3 shops so far)",
                         "  unicorn coloring pages  (no listings yet, seen in 6 keyword searches)"]


def test_progress_text_in_the_refined_stage():
    th = {"min_listings": 15, "min_shops": 3, "min_weeks_seen": 3}
    kw = {"listing_rows": 0, "keyword_rows": 16, "shop_count": 0}
    assert searchlist._progress(kw, th, 5) == "(enough keyword searches, seen 3 of 3 weeks)"
    one = {"listing_rows": 1, "keyword_rows": 0, "shop_count": 2}
    assert searchlist._progress(one, th, 0) == "(1 of 15 listings, 2 of 3 shops, seen 0 of 3 weeks)"
    one_kw = {"listing_rows": 0, "keyword_rows": 1, "shop_count": 0}
    assert searchlist._progress(one_kw, th, 1) == "(no listings yet, in 1 keyword search, seen 1 of 3 weeks)"


def test_stage_comes_from_settings_and_the_draft_count(tmp_path):
    s = _settings()
    s["research"]["stage"] = "auto"
    s["paths"] = {**s["paths"], "output_dir": str(tmp_path / "output")}
    for i in range(19):
        d = tmp_path / "output" / f"item-{i}"
        d.mkdir(parents=True)
        (d / "publish.json").write_text(json.dumps({"listing_id": 4_000_000_000 + i, "dry_run": False}))
    assert "stricter stage" not in searchlist.build(s, tmp_path / "history", date=DAY)
    d = tmp_path / "output" / "item-19"
    d.mkdir()
    (d / "publish.json").write_text(json.dumps({"listing_id": 4_100_000_000, "dry_run": False}))
    assert "stricter stage" in searchlist.build(s, tmp_path / "history", date=DAY)
    s["research"]["stage"] = "strict"              # a settings mistake must not stop the list
    assert "Botsy needs 10 listings" in searchlist.build(s, tmp_path / "history", date=DAY)


def test_near_miss_needs_two_shops_or_keyword_rows(tmp_path):
    hist = tmp_path / "history"
    _snap(hist, "2026-10-17", "stickers", [
        _theme("platypus", 9, 1, 0.9),                      # one seller's catalog
        _theme("narwhal", 3, 2, 0.5),
        {"name": "mushroom", "listing_rows": 3, "keyword_rows": 4, "shop_count": 1,
         "demand": 0.4, "qualified": False},
    ])
    lines = _section(searchlist.build(_settings(), hist, date=DAY), "STICKERS")
    assert [ln.split("  (")[0].strip() for ln in lines[2:]] == ["narwhal stickers", "mushroom stickers"]


def test_stalled_near_misses_move_down(tmp_path):
    hist = tmp_path / "history"
    for day, axolotl in (("2026-10-03", 5), ("2026-10-10", 6), ("2026-10-17", 7)):
        _snap(hist, day, "coloring_pages", [
            _theme("hedgehog", 8, 2, 0.9),                  # no growth for 3 snapshots
            _theme("axolotl", axolotl, 2, 0.4),
            _theme("walrus", 4, 2, 0.2),                    # flat, but only in this snapshot
        ] if day == "2026-10-17" else [_theme("hedgehog", 8, 2, 0.9), _theme("axolotl", axolotl, 2, 0.4)])
    lines = _section(searchlist.build(_settings(), hist, date=DAY), "COLORING PAGES")
    assert [ln.split("  (")[0].strip() for ln in lines[2:]] == [
        "axolotl coloring pages", "walrus coloring pages", "hedgehog coloring pages"]
    snaps = [json.loads((hist / d / "coloring_pages.json").read_text())
             for d in ("2026-10-03", "2026-10-10", "2026-10-17")]
    assert searchlist.stalled("hedgehog", snaps) and not searchlist.stalled("axolotl", snaps)
    assert not searchlist.stalled("hedgehog", snaps[1:])     # only 2 snapshots so far
    # More shops is growth too.
    snaps[-1]["themes"][0]["shop_count"] = 3
    assert not searchlist.stalled("hedgehog", snaps)


def test_recheck_uses_fresh_rows_not_presence(tmp_path):
    # Inside the 35-day window a theme stays in every snapshot after its last export, so
    # "last seen" must mean "last export with rows for it".
    hist = tmp_path / "history"
    _snap(hist, "2026-09-27", "coloring_pages", [_theme("farm animals", 23, 7, 0.8, True, fresh_rows=23),
                                                 _theme("ocean animals", 15, 4, 0.6, True, fresh_rows=15)])
    _snap(hist, "2026-10-08", "coloring_pages", [_theme("farm animals", 23, 7, 0.8, True, fresh_rows=0),
                                                 _theme("ocean animals", 15, 4, 0.6, True, fresh_rows=2)])
    _snap(hist, "2026-10-17", "coloring_pages", [_theme("farm animals", 23, 7, 0.8, True, fresh_rows=0),
                                                 _theme("ocean animals", 16, 4, 0.6, True, fresh_rows=1)])
    _snap(hist, "2026-10-17", "stickers", [_theme("frogs", 12, 3, 0.7, True, fresh_rows=0)])
    text = searchlist.build(_settings(), hist, date=DAY)
    lines = _section(text, "COLORING PAGES")
    assert "  farm animals coloring pages  (recheck: last seen 3 weeks ago)" in lines
    assert not any("ocean animals" in ln for ln in lines)     # fresh rows this week
    # Never fresh at all: counted from the first snapshot it is in (1 day here).
    assert not any("frogs" in ln for ln in _section(text, "STICKERS"))


def test_an_unreadable_output_folder_still_sends_the_list(tmp_path, monkeypatch):
    from research import stages

    def broken(settings, *, out_root=None):
        raise PermissionError("output folder not readable")

    monkeypatch.setattr(stages, "resolve_stage", broken)
    s = _settings()
    s["research"]["stage"] = "auto"
    text = searchlist.build(s, tmp_path / "history", date=DAY)
    assert "Botsy needs 10 listings" in text and "stricter stage" not in text
    assert "stricter stage" in searchlist.build(s, tmp_path / "history", date=DAY, stage="refined")


def test_stalled_is_counted_week_to_week(tmp_path):
    # Exported on Monday (growth), then two forced re-research days with nothing new: the
    # theme is not stalled, because week to week it grew.
    hist = tmp_path / "history"
    _snap(hist, "2026-10-08", "coloring_pages", [_theme("hedgehog", 5, 2, 0.9), _theme("axolotl", 6, 2, 0.4)])
    for day, axolotl in (("2026-10-12", 6), ("2026-10-13", 7), ("2026-10-14", 8)):
        _snap(hist, day, "coloring_pages", [_theme("hedgehog", 8, 2, 0.9), _theme("axolotl", axolotl, 2, 0.4)])
    lines = _section(searchlist.build(_settings(), hist, date=DAY), "COLORING PAGES")
    assert [ln.split("  (")[0].strip() for ln in lines[2:]] == [
        "hedgehog coloring pages", "axolotl coloring pages"]
    snaps = [json.loads((hist / d / "coloring_pages.json").read_text())
             for d in ("2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14")]
    assert searchlist.stalled("hedgehog", snaps)                       # day to day: flat
    assert [s["date"] for s in searchlist.weekly(snaps)] == ["2026-10-08", "2026-10-14"]
    assert not searchlist.stalled("hedgehog", searchlist.weekly(snaps))


# ---------------------------------------------------------------- stationery and ttrpg (docs/PACKS.md)

def test_new_niches_get_their_seeds_and_nouns(tmp_path):
    from tests.research.conftest import five_niche_settings

    hist = tmp_path / "history"
    _snap(hist, "2026-10-17", "stationery", [_theme("cottagecore", 6, 2, 0.6)])
    _snap(hist, "2026-10-17", "ttrpg", [_theme("cosmic horror", 7, 2, 0.5)])
    text = searchlist.build(five_niche_settings(), hist, date=DAY)
    assert _section(text, "STATIONERY") == [
        "  adhd planner printable  (every week)",
        "  productivity planner pages  (every week)",
        "  cottagecore planner pages  (6 of 10 listings, 2 of 3 shops so far)",
    ]
    assert _section(text, "TTRPG") == [
        "  ttrpg character sheet  (every week)",
        "  hex map paper printable  (every week)",
        "  cosmic horror rpg sheets  (7 of 10 listings, 2 of 3 shops so far)",
    ]


def test_new_niche_seeds_name_no_game_system_or_planner_brand():
    from research.text import TextRules, phrase
    from tests.research.conftest import systems_blocklist

    bl = systems_blocklist()
    rules = TextRules(blocklist=bl)
    cfg = searchlist.DEFAULTS["search_list"]
    for niche in ("stationery", "ttrpg"):
        assert cfg["nouns"][niche] and cfg["seeds"][niche], niche
        for text in [*cfg["seeds"][niche], cfg["nouns"][niche]]:
            result = bl.check(text)
            assert not result.blocked and not result.flagged, text
            assert rules.cut_systems(text) == phrase(text), text


def test_ttrpg_near_miss_from_research_names_no_game_system(tmp_path, monkeypatch):
    """Rows naming a game system feed the history like any row; the system's name is cut,
    so the search line is our own theme plus the niche noun."""
    import sys
    import types

    from tests.research.conftest import SHOPS, five_niche_settings, systems_blocklist

    systems = types.ModuleType("core.systems")
    systems.detect_systems = lambda text: ["srd5"] if "d&d" in text.lower() else []
    monkeypatch.setitem(sys.modules, "core.systems", systems)
    inputs, briefs, hist = tmp_path / "inputs", tmp_path / "briefs", tmp_path / "history"
    rows = [[f"Cosmic Horror Character Sheet for D&D 5e, Set {i}", SHOPS[i % 2], "$3.99", 20, 5,
             "1,200", "3 months", "dnd character sheet,cosmic horror dnd",
             f"https://www.etsy.com/listing/{7_200_000_000 + i}/x", ""] for i in range(1, 8)]
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    run(niche="ttrpg", inputs_dir=inputs, briefs_dir=briefs, date="2026-10-17",
        settings=five_niche_settings(), blocklist=systems_blocklist(), history_dir=hist)
    text = searchlist.build(five_niche_settings(), hist, date=DAY)
    assert "  cosmic horror rpg sheets  (7 of 10 listings, 2 of 3 shops so far)" in _section(text, "TTRPG")
    assert not re.search(r"\b(dnd|d ?& ?d|5e|dungeons)\b", text.lower())


# ---------------------------------------------------------------- instructions and health notes

def test_instructions_can_be_replaced_in_settings(tmp_path):
    text = searchlist.build(_settings(instructions=["Step one.", "Step two."]), tmp_path / "history", date=DAY)
    assert "What to do:\r\n  Step one.\r\n  Step two.\r\n" in text
    assert "Marketplace Insights" not in text
    text = searchlist.build(_settings(instructions="One line\nTwo line\n"), tmp_path / "history", date=DAY)
    assert "  One line\r\n  Two line\r\n" in text
    cfg = searchlist.search_config(_settings(instructions=""))
    assert cfg["instructions"] == list(searchlist.INSTRUCTIONS)


class _HealthBlocklist(Blocklist):
    """The duck-typed `health_terms` path, with placeholder words. The real health file
    never lists lifestyle words like these (test_the_shipped_health_file_keeps_lifestyle_
    searches_clean checks the shipped config)."""

    health_terms = {"sleep", "workout", "Mood"}


def test_health_terms_get_a_review_note_not_a_block(tmp_path, monkeypatch):
    base = searchlist.load_blocklist(_settings())
    monkeypatch.setattr(searchlist, "load_blocklist",
                        lambda settings: _HealthBlocklist([t for t, _ in base.hard], [t for t, _ in base.review]))
    hist = tmp_path / "history"
    _snap(hist, "2026-10-17", "stationery", [
        _theme("sleep tracker", 6, 2, 0.9),
        _theme("workout log", 5, 2, 0.8),
        _theme("blood pressure log", 5, 2, 0.75),      # a medical log: research's blocklist drops it
        _theme("budget", 5, 2, 0.7),
    ])
    from tests.research.conftest import five_niche_settings

    s = five_niche_settings()
    s.setdefault("service", {})["search_list"] = {
        "seeds": {"stationery": ["mood journal printable", "adhd planner printable"]}}
    text = searchlist.build(s, hist, date=DAY)
    lines = _section(text, "STATIONERY")
    assert lines[:2] == ["  mood journal printable  (every week)  (check: health?)",
                         "  adhd planner printable  (every week)"]
    assert "  sleep tracker planner pages  (6 of 10 listings, 2 of 3 shops so far)  (check: health?)" in lines
    assert "  workout log planner pages  (5 of 10 listings, 2 of 3 shops so far)  (check: health?)" in lines
    assert "blood pressure log" not in text
    assert "  budget planner pages  (5 of 10 listings, 2 of 3 shops so far)" in lines
    assert "sleeping" not in text  # whole words only: the pattern is word-bounded


def test_borderline_health_review_terms_get_the_health_note_not_the_trademark_one(tmp_path):
    """The compliance Blocklist (core/compliance.py): medical topics are hard terms and
    borderline ones `?` review terms, both listed in Blocklist.health / is_health()."""
    if not hasattr(Blocklist, "is_health"):
        pytest.skip("Blocklist.is_health not built yet")
    bl = Blocklist(["disney", "blood pressure"], ["period tracker", "peter rabbit"],
                   health=["blood pressure", "period tracker"])
    pick = searchlist._Picker(bl)
    assert pick.line("period tracker planner pages", "(every week)").notes == ["(every week)", "(check: health?)"]
    assert pick.line("peter rabbit stickers", "(every week)").notes == ["(every week)", "(check: trademark?)"]
    assert pick.line("blood pressure log pages", "(every week)") is None     # medical: dropped
    assert pick.line("sleep journal pages", "(every week)").notes == ["(every week)"]
    assert searchlist.health_patterns(bl) and not searchlist.health_patterns(Blocklist(["x"], []))


def test_health_patterns_accept_the_shapes_a_blocklist_may_offer():
    class A:
        health_terms = {"sleep": "lifestyle", "blood sugar": "medical"}

    class B:
        health_terms = None

    class C:
        pass

    pats = searchlist.health_patterns(A())
    assert [bool(rx.search("blood sugar log")) for rx in pats].count(True) == 1
    assert not any(rx.search("sleepy owl") for rx in pats)
    assert searchlist.health_patterns(B()) == [] and searchlist.health_patterns(C()) == []

    class D:                      # the compliance Blocklist's own attribute name
        health = frozenset({"weight loss"})

    assert [bool(rx.search("weight loss tracker")) for rx in searchlist.health_patterns(D())] == [True]


def test_publish_templates_copies_fixed_files_once(tmp_path):
    outbox = tmp_path / "outbox"
    written = searchlist.publish_templates(outbox)
    assert sorted(p.name for p in written) == sorted(searchlist.TEMPLATE_NAMES)
    etsy = (outbox / "etsy-insights-template.csv").read_text(encoding="utf-8")
    assert etsy == "# botsy-template: etsy_insights v1\nkeyword,searches,competition,checked_on\n"
    assert (outbox / "kdp-manual-template.csv").read_text(encoding="utf-8").startswith(
        "# botsy-template: kdp_manual v1\nsearch_term,page_type,position,title,author,")
    assert all(stat.S_IMODE((outbox / n).stat().st_mode) == 0o640 for n in searchlist.TEMPLATE_NAMES)
    assert searchlist.publish_templates(outbox) == []                       # unchanged: nothing rewritten
    (outbox / "etsy-insights-template.csv").write_text("damaged", encoding="utf-8")
    assert [p.name for p in searchlist.publish_templates(outbox)] == ["etsy-insights-template.csv"]
    assert [p.name for p in searchlist.publish_templates(outbox, tmp_path / "nowhere")] == []


def test_the_shipped_health_file_keeps_lifestyle_searches_clean():
    """User decisions of 2026-10-17 with the real config: lifestyle terms and "stress relief"
    carry no note, borderline topics get the health note, medical logs are dropped."""
    pick = searchlist._Picker(Blocklist.load())
    for phrase in ("stress relief coloring pages", "adhd planner printable", "anxiety journal pages",
                   "sleep tracker planner", "mindfulness journal", "calm coloring pages",
                   "sugar cookie stickers"):
        assert pick.line(phrase, "(every week)").notes == ["(every week)"], phrase
    for phrase in ("period tracker printable", "weight loss tracker pages"):
        assert pick.line(phrase, "(every week)").notes == ["(every week)", "(check: health?)"], phrase
    for phrase in ("blood pressure log pages", "pill stickers", "medication tracker printable"):
        assert pick.line(phrase, "(every week)") is None, phrase
