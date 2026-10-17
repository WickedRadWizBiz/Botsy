"""plan_pages and style mapping (docs/PACKS.md sections 1 and 3)."""
from collections import Counter

import pytest

import generators.stationery as stationery
from generators.stationery.catalog import CATALOG, PACK_ORDER, PAGE_TYPES, STYLE_MIX, VARIANT_PREFS
from generators.stationery.plan import page_mix, ranked_mix
from generators.stationery.styles import STYLES, spec_for, style_for

from .conftest import make_ctx

SPEC_TYPES = ("daily_planner", "weekly_planner", "monthly_planner", "time_blocked_day", "brain_dump",
              "task_breakdown", "top3_priorities", "priority_matrix", "habit_tracker", "routine_checklist",
              "focus_sessions", "mood_energy", "todo_list", "meal_planner", "budget_tracker", "reading_log",
              "gratitude", "notes_lined", "notes_dot", "notes_grid")


def test_page_types_match_the_spec():
    assert stationery.PAGE_TYPES == SPEC_TYPES == PAGE_TYPES
    assert set(CATALOG) == set(SPEC_TYPES) == set(PACK_ORDER)
    for style, mix in STYLE_MIX.items():
        assert sorted(mix["core"]) == sorted(SPEC_TYPES), style          # every style can use every type
        assert set(mix["repeats"]) <= set(SPEC_TYPES)
    for variants in CATALOG.values():
        assert len(variants) >= 2                                          # repeats get a different layout
        assert len({v.name for v in variants}) == len(variants)


@pytest.mark.parametrize("n", [5, 20, 30])
@pytest.mark.parametrize("theme", ["adhd", "minimalist", "cottagecore", "botanical", "autumn", "student", "teacher",
                                   "work from home"])
def test_plan_pages_counts_and_distinct(settings, theme, n):
    pages = stationery.plan_pages(make_ctx(theme), n, seed="2026-10-16-stationery-x-1a2b", settings=settings)
    assert len(pages) == n
    assert len({p["page_id"] for p in pages}) == n
    assert len({(p["type"], p["spec"]["variant"], p["spec"]["alt"]) for p in pages}) == n
    assert len({p["subject"] for p in pages}) == n
    for p in pages:
        assert p["type"] in SPEC_TYPES
        spec = p["spec"]
        assert spec["title"] and spec["subtitle"] and spec["undated"] is True
        assert spec["variant"] in {v.name for v in CATALOG[p["type"]]}
        assert spec["style"] in STYLES
    # pack order: month down to the day, then lists, trackers and notes; repeats side by side
    order = [PACK_ORDER.index(p["type"]) for p in pages]
    assert order == sorted(order)


def test_adhd_five_pack_is_the_core_set(settings):
    pages = stationery.plan_pages(make_ctx("adhd"), 5, seed="s", settings=settings)
    assert {p["type"] for p in pages} == {"daily_planner", "brain_dump", "task_breakdown", "habit_tracker",
                                          "focus_sessions"}
    assert all(p["spec"]["style"] == "adhd_friendly" for p in pages)


def test_bigger_packs_cover_more_types_with_natural_repeats(settings):
    counts = {}
    for n in (5, 20, 30):
        pages = stationery.plan_pages(make_ctx("adhd"), n, seed="s", settings=settings)
        counts[n] = Counter(p["type"] for p in pages)
    assert len(counts[5]) == 5 and max(counts[5].values()) == 1
    assert len(counts[20]) == 16                      # ~80% different types, the rest repeats
    assert counts[20]["daily_planner"] == 2           # two daily pages ...
    assert len(counts[30]) == 20 and sum(counts[30].values()) == 30
    pages = stationery.plan_pages(make_ctx("adhd"), 20, seed="s", settings=settings)
    dailies = [p["spec"]["variant"] for p in pages if p["type"] == "daily_planner"]
    assert len(set(dailies)) == 2                     # ... with two different layouts


def test_page_mix_large_and_small():
    assert page_mix("minimalist", 1) == ["daily_planner"]
    assert ranked_mix("adhd_friendly", 5) == ["daily_planner", "brain_dump", "task_breakdown", "habit_tracker",
                                              "focus_sessions"]
    assert sorted(ranked_mix("cozy", 20), key=PACK_ORDER.index) == page_mix("cozy", 20)
    big = page_mix("adhd_friendly", 60)
    assert len(big) == 60 and set(big) == set(SPEC_TYPES)
    with pytest.raises(ValueError):
        page_mix("minimalist", 0)
    with pytest.raises(ValueError):
        stationery.plan_pages(make_ctx("adhd"), 2.5, seed="s", settings={})


def test_sixty_page_plan_stays_distinct(settings):
    pages = stationery.plan_pages(make_ctx("adhd"), 60, seed="s", settings=settings)
    assert len({(p["type"], p["spec"]["variant"], p["spec"]["alt"]) for p in pages}) == 60


def test_plan_is_deterministic_and_seeded(settings):
    ctx = make_ctx("botanical")
    a = stationery.plan_pages(ctx, 20, seed="set-a", settings=settings)
    assert a == stationery.plan_pages(dict(ctx), 20, seed="set-a", settings=settings)
    b = stationery.plan_pages(ctx, 20, seed="set-b", settings=settings)
    assert [p["type"] for p in a] == [p["type"] for p in b]         # same mix for the theme
    assert [p["spec"]["title"] for p in a] != [p["spec"]["title"] for p in b] or \
        [p["spec"]["motif"] for p in a] != [p["spec"]["motif"] for p in b]


def _layouts(pages):
    return {(p["type"], p["spec"]["variant"]) for p in pages}


def test_fresh_set_brings_new_layouts_not_reprints(settings):
    master = stationery.plan_pages(make_ctx("adhd"), 20, seed="m", settings=settings)
    used = [p["subject"] for p in master]
    fresh = stationery.plan_pages(make_ctx("adhd", used_subjects=used, set_kind="fresh"), 5, seed="f",
                                  settings=settings)
    assert not {p["subject"] for p in fresh} & set(used)
    # every page is a layout the master does not have (not the same layout with a new title)
    assert not _layouts(fresh) & _layouts(master)
    assert all(p["spec"]["alt"] == 0 and not p["spec"].get("layout_reused") for p in fresh)
    # the daily page keeps its slot (it still has an unused layout); types whose layouts the
    # master used up give way to the next types in the ADHD-friendly order
    types = [p["type"] for p in fresh]
    assert "daily_planner" in types and "brain_dump" not in types and "task_breakdown" not in types
    core = STYLE_MIX["adhd_friendly"]["core"]
    assert all(core.index(t) < 8 for t in types)
    assert stationery.review_reasons(fresh, settings=settings) == []


def test_layouts_repeat_only_when_the_theme_has_used_them_all(settings):
    used, sets = [], []
    for k in range(4):
        pages = stationery.plan_pages(make_ctx("adhd", used_subjects=list(used), set_kind="master"), 20,
                                      seed=f"m{k}", settings=settings)
        sets.append(pages)
        used += [p["subject"] for p in pages]
    assert len(set(used)) == len(used)                           # every page of every set is new
    layouts = sum(len(v) for v in CATALOG.values())
    new_layouts = [(p["type"], p["spec"]["variant"]) for pages in sets for p in pages if p["spec"]["alt"] == 0]
    assert len(new_layouts) == len(set(new_layouts)) == layouts   # each layout once before any repeats
    assert not any(p["spec"].get("layout_reused") for pages in sets for p in pages)   # masters: no flag
    fresh = stationery.plan_pages(make_ctx("adhd", used_subjects=used, set_kind="fresh"), 5, seed="f",
                                  settings=settings)
    assert all(p["spec"]["alt"] > 0 and p["spec"]["layout_reused"] for p in fresh)
    assert {p["type"] for p in fresh} == {"daily_planner", "brain_dump", "task_breakdown", "habit_tracker",
                                          "focus_sessions"}                # the usual mix again
    reasons = stationery.review_reasons(fresh, settings=settings)
    assert any("repeat layouts" in r for r in reasons)


@pytest.mark.parametrize("theme, k", [("adhd", 5), ("cottagecore", 5), ("student", 5), ("adhd", 10)])
def test_derived_pack_from_a_master_keeps_the_core_mix(settings, theme, k):
    """The pack engine curates a derived pack by type variety, then quality: the ranking in
    each page's quality makes a 5-page pack cut from the 20-page master the same core mix a
    fresh 5-page plan has, and a bigger derived pack the style's most wanted k types."""
    curate = pytest.importorskip("generators.packs.curate").curate
    master = stationery.plan_pages(make_ctx(theme), 20, seed="2026-10-16-stationery-x-1a2b", settings=settings)
    assert all(0.7 <= p["quality"] <= 1.0 for p in master)
    doc = {"pages": [dict(p, page_id=f"set-{p['page_id']}") for p in master], "items": []}
    picked = curate(doc, k, size=k, seed="s")
    style = master[0]["spec"]["style"]
    assert sorted(p["type"] for p in picked) == sorted(STYLE_MIX[style]["core"][:k])
    if k <= 5:
        planned = stationery.plan_pages(make_ctx(theme), k, seed="other", settings=settings)
        assert sorted(p["type"] for p in picked) == sorted(p["type"] for p in planned)
    # a second derived pack of the same size takes the next pages, not the same ones
    for p in doc["pages"]:
        if p["page_id"] in {q["page_id"] for q in picked}:
            p["used_in"] = ["item-1"]
    doc["items"] = [{"item_id": "item-1", "size": k, "source": "derived"}]
    second = curate(doc, k, size=k, seed="s2")
    assert not {p["page_id"] for p in second} & {p["page_id"] for p in picked}


def test_quality_follows_the_ranking(settings):
    pages = stationery.plan_pages(make_ctx("adhd"), 20, seed="s", settings=settings)
    best = max(pages, key=lambda p: p["quality"])
    assert best["type"] == "daily_planner" and best["quality"] == 1.0
    assert len({p["quality"] for p in pages}) == 20


def test_style_preferences_apply(settings):
    teacher = stationery.plan_pages(make_ctx("teacher"), 5, seed="s", settings=settings)
    weekly = [p for p in teacher if p["type"] == "weekly_planner"]
    assert weekly and weekly[0]["spec"]["variant"] == VARIANT_PREFS["teacher"]["weekly_planner"][0]
    assert weekly[0]["spec"]["title"] in ("Weekly Lesson Plan", "Lesson Planner")
    student = stationery.plan_pages(make_ctx("student"), 20, seed="s", settings=settings)
    focus = [p for p in student if p["type"] == "focus_sessions"]
    assert focus and focus[0]["spec"]["title"] in ("Study Sessions", "Study Planner")


@pytest.mark.parametrize("theme, style", [
    ("adhd", "adhd_friendly"), ("ADHD Friendly", "adhd_friendly"), ("neurodivergent", "adhd_friendly"),
    ("adhd student", "adhd_friendly"),
    ("minimalist", "minimalist"), ("simple", "minimalist"),
    ("cottagecore", "cozy"), ("cozy", "cozy"), ("hygge", "cozy"),
    ("botanical", "botanical"), ("floral", "botanical"), ("wildflower", "botanical"),
    ("autumn", "seasonal"), ("christmas", "seasonal"), ("cozy fall", "seasonal"), ("summer", "seasonal"),
    ("student", "student"), ("college", "student"),
    ("teacher", "teacher"), ("homeschool", "teacher"), ("classroom", "teacher"),
    ("work from home", "wfh"), ("home office", "wfh"), ("remote work", "wfh"),
    ("galaxy", "minimalist"), ("", "minimalist"),
])
def test_theme_words_map_to_a_style(theme, style):
    assert style_for(make_ctx(theme)).style == style


def test_style_from_explicit_id_then_keywords():
    assert style_for(make_ctx("galaxy", style="botanical")).style == "botanical"
    assert style_for(make_ctx("galaxy", style="adhd-friendly")).style == "adhd_friendly"
    # research's long default style notes are not a style id
    notes = "clean printable layouts with clear zones, big writing boxes and roomy line spacing"
    assert style_for(make_ctx("galaxy", style=notes)).style == "minimalist"
    assert style_for(make_ctx("galaxy", keywords=["teacher planner printable"])).style == "teacher"
    # the theme wins over keywords
    assert style_for(make_ctx("botanical", keywords=["adhd planner printable"])).style == "botanical"


def test_seasons():
    assert style_for(make_ctx("autumn")).season == "autumn"
    assert style_for(make_ctx("christmas")).season == "winter"
    assert style_for(make_ctx("easter")).season == "spring"
    assert style_for(make_ctx("adhd")).season is None
    assert spec_for("seasonal", "winter").motifs != spec_for("seasonal", "summer").motifs


def test_seasonal_without_a_season_rotates_through_all(settings):
    pages = stationery.plan_pages(make_ctx("seasonal"), 8, seed="s", settings=settings)
    assert {p["spec"]["season"] for p in pages} == {"autumn", "winter", "spring", "summer"}


def test_adhd_style_is_roomier():
    adhd, mini = spec_for("adhd_friendly"), spec_for("minimalist")
    assert adhd.line_gap_mm > mini.line_gap_mm and adhd.checkbox_pt > mini.checkbox_pt
    assert adhd.gap_pt > mini.gap_pt and adhd.box_lines and adhd.time_bars and len(adhd.tints) >= 4
    for rgb in adhd.tints:                            # print-friendly light tints
        assert min(rgb) >= 0.75
