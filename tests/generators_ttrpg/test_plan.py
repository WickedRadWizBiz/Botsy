"""plan_pages for tabletop RPG packs: page mixes for 5/20/30/40 packs, focus and genre from
the theme, system-aware fields, fresh-set variety and determinism (docs/PACKS.md)."""
import json
from collections import Counter

import pytest

import generators.ttrpg as ttrpg
from core import systems
from core.compliance import iter_strings
from generators.ttrpg.catalog import PAGE_TYPES, SYSTEM_AWARE, VARIANTS, subject_for, titles_for
from generators.ttrpg.plan import RANKS, STARTER, focus_for, page_mix, rank_quality, ranked_mix
from generators.ttrpg.styles import GENRES, genre_for

from .conftest import make_ctx

EXPECTED_TYPES = ("character_sheet", "spell_tracker", "inventory", "campaign_tracker", "session_notes",
                  "npc_tracker", "quest_log", "initiative_tracker", "encounter_planner", "worldbuilding_town",
                  "worldbuilding_faction", "worldbuilding_deity", "worldbuilding_region", "hex_map", "grid_map",
                  "dungeon_grid", "dice_log")
EXPECTED_GENRES = {"classic_fantasy", "dark_fantasy", "scifi", "cosmic_horror", "cyberpunk", "pirate", "western",
                   "cozy"}


def test_page_types_and_genres_are_the_specified_ones():
    assert ttrpg.PAGE_TYPES == PAGE_TYPES == EXPECTED_TYPES
    assert set(GENRES) == EXPECTED_GENRES
    assert set(VARIANTS) == set(PAGE_TYPES)


def test_five_page_pack_is_the_campaign_starter(settings):
    pages = ttrpg.plan_pages(make_ctx("dark fantasy"), 5, seed="s", settings=settings)
    assert [p["type"] for p in pages] == ["character_sheet", "session_notes", "npc_tracker", "quest_log", "hex_map"]
    assert pages[0]["spec"]["variant"] == "core"
    assert sorted(p["type"] for p in pages) == sorted(STARTER)


def test_twenty_page_pack_has_every_type_and_sensible_repeats(settings):
    pages = ttrpg.plan_pages(make_ctx("classic fantasy"), 20, seed="s", settings=settings)
    counts = Counter(p["type"] for p in pages)
    assert set(counts) == set(PAGE_TYPES)
    assert counts["character_sheet"] == counts["session_notes"] == counts["npc_tracker"] == 2
    # a repeated type uses a different layout
    variants = [(p["type"], p["spec"]["variant"]) for p in pages]
    assert len(set(variants)) == len(variants)
    assert {p["spec"]["variant"] for p in pages if p["type"] == "character_sheet"} == {"core", "details"}


@pytest.mark.parametrize("n", [1, 2, 5, 8, 12, 20, 30, 40, 55])
def test_n_distinct_pages_in_pack_order(settings, n):
    pages = ttrpg.plan_pages(make_ctx(), n, seed="s", settings=settings)
    assert len(pages) == n
    assert len({p["page_id"] for p in pages}) == n
    assert len({p["subject"] for p in pages}) == n
    order = [PAGE_TYPES.index(p["type"]) for p in pages]
    assert order == sorted(order)
    for p in pages:
        assert set(p) >= {"page_id", "type", "subject", "spec"}
        assert p["type"] in PAGE_TYPES and 0.7 <= p["quality"] <= 1.0
        json.dumps(p)


def test_thirty_and_forty_page_packs_vary_layouts(settings):
    for n in (30, 40):
        pages = ttrpg.plan_pages(make_ctx(), n, seed="s", settings=settings)
        variants = [(p["type"], p["spec"]["variant"], p["spec"]["alt"]) for p in pages]
        assert len(set(variants)) == n
        assert max(Counter(p["type"] for p in pages).values()) <= 4
        if n == 30:
            assert all(p["spec"]["alt"] == 0 for p in pages)      # enough layouts: no "version 2" yet
        # a repeat is a new version of a layout only once every layout of its type is in the pack
        for p in pages:
            if p["spec"]["alt"]:
                used = {q["spec"]["variant"] for q in pages if q["type"] == p["type"] and q["spec"]["alt"] == 0}
                assert used == {v.name for v in VARIANTS[p["type"]]}


def test_bigger_packs_repeat_a_layout_as_a_new_version(settings):
    pages = ttrpg.plan_pages(make_ctx("hex map"), 40, seed="s", settings=settings)
    alts = [p for p in pages if p["spec"]["alt"]]
    assert alts and all(p["subject"].endswith(f", version {p['spec']['alt'] + 1}") for p in alts)
    assert all(p["page_id"].endswith(f"-v{p['spec']['alt'] + 1}") for p in alts)


def test_plan_is_deterministic_and_the_seed_only_changes_wording(settings):
    ctx = make_ctx("pirate", "dnd5e")
    a = ttrpg.plan_pages(ctx, 20, seed="set-1", settings=settings)
    b = ttrpg.plan_pages(dict(ctx), 20, seed="set-1", settings=settings)
    assert a == b
    seeds = [ttrpg.plan_pages(ctx, 20, seed=f"set-{i}", settings=settings) for i in range(6)]
    assert {tuple(p["type"] for p in s) for s in seeds} == {tuple(p["type"] for p in a)}
    assert len({tuple((p["spec"]["title"], p["spec"]["motif"]) for p in s) for s in seeds}) > 1


def test_rank_quality_lets_curation_keep_the_mix():
    assert rank_quality(0) == 1.0 and rank_quality(4) > rank_quality(5) > 0.7
    assert rank_quality(500) == 0.7
    ranked = ranked_mix("campaign", 20)
    assert ranked[:5] == list(STARTER)


@pytest.mark.parametrize("theme, focus", [
    ("dark fantasy", "campaign"), ("campaign tracker", "campaign"), ("hex map paper", "maps"),
    ("battle map grid", "maps"), ("character sheet", "character"), ("spellbook pages", "character"),
    ("gm screen notes", "gm"), ("initiative tracker", "gm"), ("worldbuilding", "worldbuilding"),
    ("fantasy town builder", "worldbuilding"), ("session notes", "journal"), ("campaign journal", "journal"),
])
def test_focus_comes_from_the_theme(theme, focus):
    assert focus_for({"theme": theme}) == focus


def test_focus_ignores_keywords_so_genre_themes_get_the_campaign_starter(settings):
    """Research's ttrpg keyword templates add "<theme> character sheet"; a genre theme must
    still get the campaign mix (the spec's 5-page campaign starter), not a character pack."""
    assert focus_for({"theme": "cozy", "keywords": ["hex map printable"]}) == "campaign"
    assert focus_for({"theme": "cozy"}) == "campaign" and focus_for(None) == "campaign"
    ctx = make_ctx("dark fantasy", "dnd5e", keywords=["dark fantasy rpg sheets", "dark fantasy character sheet",
                                                      "dark fantasy ttrpg printable", "campaign journal"])
    pages = ttrpg.plan_pages(ctx, 5, seed="s", settings=settings)
    assert sorted(p["type"] for p in pages) == sorted(STARTER)


@pytest.mark.parametrize("focus", sorted(RANKS))
def test_each_focus_gives_a_full_sensible_mix(focus):
    mix = page_mix(focus, 20)
    assert len(mix) == 20 and len(set(mix)) >= (5 if focus == "maps" else 12)
    assert max(Counter(mix).values()) <= 5
    assert len(page_mix(focus, 45)) == 45
    with pytest.raises(ValueError):
        page_mix(focus, 0)


def test_maps_focus_is_mostly_maps(settings):
    pages = ttrpg.plan_pages(make_ctx("hex map paper"), 20, seed="s", settings=settings)
    maps = sum(p["type"] in ("hex_map", "grid_map", "dungeon_grid", "worldbuilding_region") for p in pages)
    assert maps >= 12
    assert len(ttrpg.plan_pages(make_ctx("hex map paper"), 5, seed="s", settings=settings)) == 5


@pytest.mark.parametrize("theme, genre", [
    ("dark fantasy", "dark_fantasy"), ("high fantasy", "classic_fantasy"), ("space opera", "scifi"),
    ("sci-fi", "scifi"), ("cosmic horror", "cosmic_horror"), ("eldritch investigator", "cosmic_horror"),
    ("cyberpunk", "cyberpunk"), ("neon city", "cyberpunk"), ("pirate", "pirate"), ("high seas", "pirate"),
    ("wild west", "western"), ("cozy tavern", "cozy"), ("cottagecore", "cozy"), ("hex map", "classic_fantasy"),
])
def test_genre_comes_from_the_theme(theme, genre):
    assert genre_for({"theme": theme}) == genre


def test_genre_falls_back_to_style_then_keywords_then_default():
    assert genre_for({"theme": "hex map", "style": "dark and grim"}) == "dark_fantasy"
    assert genre_for({"theme": "hex map", "keywords": ["pirate maps"]}) == "pirate"
    assert genre_for({"theme": "hex map"}, default="scifi") == "scifi"
    assert genre_for({"theme": "hex map"}, default="nope") == "classic_fantasy"


def test_genre_sets_titles_and_emblems(settings):
    pages = ttrpg.plan_pages(make_ctx("cyberpunk"), 5, seed="s", settings=settings)
    assert {p["spec"]["genre"] for p in pages} == {"cyberpunk"}
    assert pages[0]["spec"]["title"] in titles_for("cyberpunk", "character_sheet")
    assert {p["spec"]["motif"] for p in pages} <= set(GENRES["cyberpunk"].motifs)


def _section_labels(spec) -> list[str]:
    return [s for s in iter_strings(spec.get("sections"))]


def test_system_changes_only_the_fields(settings):
    plans = {sid: ttrpg.plan_pages(make_ctx("dark fantasy", sid), 20, seed="s", settings=settings)
             for sid in ("generic", "dnd5e", "pf2e", "coc7e")}
    # same pages, titles and layouts for every system
    shape = {sid: [(p["page_id"], p["type"], p["spec"]["title"], p["spec"]["variant"]) for p in ps]
             for sid, ps in plans.items()}
    assert len({json.dumps(v) for v in shape.values()}) == 1
    sheets = {sid: next(p for p in ps if p["type"] == "character_sheet") for sid, ps in plans.items()}
    labels = {sid: _section_labels(p["spec"]) for sid, p in sheets.items()}
    assert len({json.dumps(v) for v in labels.values()}) == 4
    assert "Might" in labels["generic"] and "Proficiency Bonus" in labels["dnd5e"]
    assert "Fortitude" in labels["pf2e"] and "Sanity" in labels["coc7e"]
    assert sheets["generic"]["spec"]["fields_from"] == "generic"
    assert sheets["dnd5e"]["spec"]["fields_from"] == "system"
    # system-neutral page types are identical
    towns = {sid: next(p for p in ps if p["type"] == "worldbuilding_town")["spec"] for sid, ps in plans.items()}
    assert len({json.dumps(v, sort_keys=True) for v in towns.values()}) == 1
    assert all(t["fields_from"] == "generic" for t in towns.values())
    # trackers use the system's combat labels
    init = {sid: _section_labels(next(p for p in ps if p["type"] == "initiative_tracker")["spec"])
            for sid, ps in plans.items()}
    assert "Armor Class" in init["dnd5e"] and "Dodge" in init["coc7e"] and "Defense" in init["generic"]


def test_unknown_system_falls_back_to_neutral_fields(settings, caplog):
    generic = ttrpg.plan_pages(make_ctx("x", "generic"), 5, seed="s", settings=settings)
    unknown = ttrpg.plan_pages(make_ctx("x", "srd99"), 5, seed="s", settings=settings)
    assert generic == unknown
    assert "srd99" in caplog.text
    assert ttrpg.plan_pages(make_ctx("x", None), 5, seed="s", settings=settings) == generic


def test_planned_pages_never_carry_a_system_name_or_blocked_term(settings, blocklist):
    """The engine blocklist-checks every planned string (no system allowed); system ids are
    not names."""
    for sid in systems.load_systems():
        for theme in ("dark fantasy", "character sheet", "hex map", "gm screen"):
            pages = ttrpg.plan_pages(make_ctx(theme, sid), 40, seed="s", settings=settings)
            strings = list(iter_strings([{k: v for k, v in p.items() if k != "page_id"} for p in pages]))
            assert blocklist.check(*strings).blocked == [], (sid, theme)
            assert systems.detect_systems("\n".join(strings)) == [], (sid, theme)
            assert all(sid not in json.dumps(p) or sid == "generic" for p in pages)


def test_fresh_set_avoids_layouts_of_the_themes_other_sets(settings):
    master = ttrpg.plan_pages(make_ctx(), 20, seed="m", settings=settings)
    used = [p["subject"] for p in master]
    fresh = ttrpg.plan_pages(make_ctx(used_subjects=used), 5, seed="f", settings=settings)
    assert sorted(p["type"] for p in fresh) == sorted(STARTER)
    assert not {p["subject"] for p in fresh} & set(used)
    sheet = next(p for p in fresh if p["type"] == "character_sheet")
    assert sheet["spec"]["variant"] == "core_alt"        # still a stats sheet, a layout the master lacks
    # every starter type has a layout the master lacks: no "version 2" repeat in the first fresh pack
    assert all(p["spec"]["alt"] == 0 for p in fresh), [p["subject"] for p in fresh]
    # a second fresh set avoids both
    fresh2 = ttrpg.plan_pages(make_ctx(used_subjects=used + [p["subject"] for p in fresh]), 5, seed="g",
                              settings=settings)
    assert not {p["subject"] for p in fresh2} & (set(used) | {p["subject"] for p in fresh})


def test_subjects_are_our_own_words():
    assert subject_for("character_sheet", "core") == "character sheet: core stats"
    assert subject_for("hex_map", "small", 1) == "hex map paper: 1/2 in (13 mm) hexes, version 2"


def test_system_aware_types_are_marked():
    assert set(SYSTEM_AWARE) <= set(PAGE_TYPES)
    assert "character_sheet" in SYSTEM_AWARE and "hex_map" not in SYSTEM_AWARE


@pytest.mark.parametrize("n", [0, -1, 2.5, True, "5"])
def test_bad_pack_sizes_are_refused(settings, n):
    with pytest.raises(ValueError):
        ttrpg.plan_pages(make_ctx(), n, seed="s", settings=settings)
