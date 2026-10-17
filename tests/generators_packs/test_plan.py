"""plan(): pack sizes, the master set, the fresh policy and its counters (docs/PACKS.md section 3)."""
import datetime as dt

import pytest

from generators.packs import PackState, item_id_for, plan, theme_key
from generators.packs import sets as sets_mod
from generators.packs.model import fresh_every, pack_sizes

from .conftest import DATE, make_brief

NICHE = "activity_sheets"


def state_for(tmp_path, date=DATE, cooldown=60):
    return PackState(None, out_root=tmp_path, shop_id="main", date=date, cooldown_days=cooldown)


def made(state, brief, specs, date=DATE):
    """Record every planned pack as generated (what generate_packs does after each item)."""
    for spec in specs:
        iid = item_id_for(date, brief["niche"], brief["theme"], brief["brief_id"], spec)
        state.record_success(brief["niche"], brief["brief_id"], spec, iid, date)


def by_size(specs):
    return {s.size: s for s in specs}


def test_largest_size_is_the_always_fresh_master_and_the_fresh_policy_counts_per_size(tmp_path, settings):
    state = state_for(tmp_path)
    seen = {5: [], 20: [], 30: []}
    themes = ["farm", "ocean", "space", "dinosaurs", "jungle", "woodland", "cats", "dogs", "bugs", "birds",
              "trains", "robots"]
    for i, theme in enumerate(themes, 1):
        brief = make_brief(NICHE, theme, n=i)
        specs = plan(brief, settings, state)
        assert [s.size for s in specs] == [30, 5, 20]              # master first
        for s in specs:
            seen[s.size].append((s.source, s.n))
        master = specs[0]
        assert master.is_master and master.n == 0 and master.new_set
        for s in specs[1:]:
            if s.source == "derived":
                assert s.set_id == master.set_id and not s.new_set   # cut from this plan's master set
            else:
                assert s.new_set and s.set_id != master.set_id and s.reason == "fresh_every"
        made(state, brief, specs)
    # Out of every 3 five-page packs, 2 are derived and the 3rd is fresh.
    assert [src for src, _ in seen[5]] == ["derived", "derived", "fresh"] * 4
    # Out of every 6 twenty-page packs, 5 are derived and the 6th is fresh.
    assert [src for src, _ in seen[20]] == (["derived"] * 5 + ["fresh"]) * 2
    assert [n for _, n in seen[5]] == list(range(1, 13)) and [n for _, n in seen[20]] == list(range(1, 13))
    # The master is always fresh and never moves a counter.
    assert seen[30] == [("master", 0)] * 12
    assert state.counter(NICHE, 30) == 0 and state.counter(NICHE, 5) == 12 and state.counter(NICHE, 20) == 12


def test_counters_are_per_shop_and_niche(tmp_path, settings):
    state = state_for(tmp_path)
    for i in range(2):
        b = make_brief(NICHE, f"theme {i}", n=i + 1)
        made(state, b, plan(b, settings, state))
    other_niche = make_brief("coloring_pages", "owls")
    assert by_size(plan(other_niche, settings, state))[5].n == 1
    other_shop = PackState(state.data, out_root=tmp_path, shop_id="second", date=DATE)
    assert by_size(plan(make_brief(NICHE, "owls", n=9), settings, other_shop))[5].n == 1
    third = by_size(plan(make_brief(NICHE, "owls", n=9), settings, state))[5]
    assert (third.source, third.n, third.reason) == ("fresh", 3, "fresh_every")


def test_counters_advance_only_when_a_pack_was_made(tmp_path, settings):
    state = state_for(tmp_path)
    brief = make_brief(NICHE)
    first = plan(brief, settings, state)
    assert plan(brief, settings, state) == first                     # nothing made: same plan, same numbers
    made(state, brief, first[:1])                                    # only the master was made
    assert by_size(plan(make_brief(NICHE, "ocean", n=2), settings, state))[5].n == 1


def test_a_rerun_of_the_same_brief_keeps_its_packs(tmp_path, settings):
    state = state_for(tmp_path)
    brief = make_brief(NICHE)
    specs = plan(brief, settings, state)
    made(state, brief, specs)
    made(state, brief, specs)                                          # a rebuild counts once
    assert state.counter(NICHE, 5) == 1 and state.counter(NICHE, 20) == 1
    assert plan(brief, settings, state) == specs                       # same sources, numbers and sets


def test_two_sizes_make_the_larger_one_the_master(tmp_path, settings):
    specs = plan(make_brief(NICHE, sizes=[5, 20]), settings, state_for(tmp_path))
    assert [(s.size, s.source) for s in specs] == [(20, "master"), (5, "derived")]


def test_fresh_every_comes_from_settings(tmp_path, settings):
    settings["packs"] = {"fresh_every": {"5": 1}, "fresh_every_default": 2}
    assert fresh_every(settings, 5) == 1 and fresh_every(settings, 20) == 6 and fresh_every(settings, 12) == 2
    state = state_for(tmp_path)
    specs = by_size(plan(make_brief(NICHE, sizes=[5, 12, 30]), settings, state))
    assert specs[5].source == "fresh" and specs[12].source == "derived"


def test_pack_sizes_fall_back_to_page_count_and_refuse_bad_values(settings):
    assert pack_sizes({"page_count": 12}, default=10) == [12]
    assert pack_sizes({}, default=10) == [10]
    assert pack_sizes({"pack_sizes": [20, 5, 5, 20.0]}, default=10) == [5, 20]
    for bad in ({"pack_sizes": [5, "x"]}, {"pack_sizes": "5"}, {"pack_sizes": [0]}, {"pack_sizes": [5.5]},
                {"page_count": True}):
        with pytest.raises(ValueError):
            pack_sizes(bad, default=10, cap=50)
    with pytest.raises(ValueError, match="outside"):
        pack_sizes({"pack_sizes": [5, 51]}, default=10, cap=50)
    with pytest.raises(ValueError, match="outside"):
        plan(make_brief(NICHE, sizes=[5, 60]), settings)               # packs.max_size.activity_sheets is 50


def test_only_some_sizes(tmp_path, settings):
    state = state_for(tmp_path)
    brief = make_brief(NICHE)
    assert [s.size for s in plan(brief, settings, state, sizes=[30, 5])] == [30, 5]
    with pytest.raises(ValueError, match="not in the brief"):
        plan(brief, settings, state, sizes=[12])


def _master_set(tmp_path, *, theme="farm animals", brief_id="activity_sheets-2026-09-01-07", created="2026-10-01",
                pages=30, set_id="2026-10-01-activity_sheets-farm-animals-aaaa", dry_run=False, ai=False):
    doc = sets_mod.new_set_doc(set_id, niche=NICHE, theme=theme, theme_key=theme_key(theme), shop_id="main",
                               brief_id=brief_id, created=created, kind="master", size=pages, dry_run=dry_run,
                               model=None, ai_assisted=ai)
    doc["pages"] = [sets_mod.page_entry({"page_id": f"{set_id}-p{i}", "type": "maze", "subject": "maze",
                                         "spec": {}}) for i in range(pages)]
    sets_mod.save_set(tmp_path, doc)
    return doc


def test_a_derived_size_needs_a_master_set_of_the_theme_within_the_cooldown(tmp_path, settings):
    brief = make_brief(NICHE)
    # No master set and the master size is not being made: the pack is fresh.
    (spec,) = plan(brief, settings, state_for(tmp_path), sizes=[5])
    assert spec.source == "fresh" and spec.reason == "no master set"
    # Another brief's master set of the same theme made 14 days ago: cut from it.
    master = _master_set(tmp_path)
    (spec,) = plan(brief, settings, state_for(tmp_path), sizes=[5])
    assert spec.source == "derived" and spec.set_id == master["set_id"] and not spec.new_set
    # The same set 60 days later is outside the theme cooldown: fresh again.
    late = (dt.date.fromisoformat("2026-10-01") + dt.timedelta(days=60)).isoformat()
    (spec,) = plan(brief, settings, state_for(tmp_path, date=late), sizes=[5])
    assert spec.source == "fresh"
    # A set with too few pages, of another theme, or dry-run AI art for a real run does not count.
    for kw in ({"pages": 5}, {"theme": "ocean"}):
        other = tmp_path / f"o{len(kw)}{kw.get('pages', '')}"
        _master_set(other, **kw)
        (spec,) = plan(brief, settings, state_for(other), sizes=[5])
        assert spec.source == "fresh", kw
    fake_art = tmp_path / "fake"
    _master_set(fake_art, dry_run=True, ai=True)
    (spec,) = plan(brief, settings, state_for(fake_art), sizes=[5])
    assert spec.source == "fresh"


def test_the_briefs_own_master_set_counts_whatever_the_cooldown(tmp_path, settings):
    brief = make_brief(NICHE)
    own = _master_set(tmp_path, brief_id=brief["brief_id"], created="2026-06-01")
    (spec,) = plan(brief, settings, state_for(tmp_path, cooldown=0), sizes=[20])
    assert spec.source == "derived" and spec.set_id == own["set_id"]
