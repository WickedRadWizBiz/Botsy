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
    assert "EverBee Product Analytics" in text and "Export CSV" in text
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
        _theme("narwhal", 4, 1, 0.2),
        _theme("axolotl", 7, 2, 0.4),
        _theme("hedgehog", 12, 2, 0.3),
    ])
    lines = _section(searchlist.build(_settings(), hist, date=DAY), "COLORING PAGES")
    assert lines == [
        "  coloring pages for kids  (every week)",
        "  printable coloring pages  (every week)",
        "  axolotl coloring pages  (7 of 10 listings, 2 of 3 shops so far)",
        "  hedgehog coloring pages  (enough listings, 2 of 3 shops so far)",
        "  narwhal coloring pages  (4 of 10 listings, 1 of 3 shops so far)",  # newest numbers
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
    assert sorted(p.name for p in outbox.iterdir()) == ["search-list-2026-10-18.txt",
                                                       "search-list.txt"]   # no temp files left
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
