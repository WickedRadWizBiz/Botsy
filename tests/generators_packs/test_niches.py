"""Every niche makes its packs from brief.pack_sizes (docs/PACKS.md section 3): page counts in the
PDFs, manifest pack fields, related siblings, design sets, fresh packs and reruns."""
import math
import zipfile

import pytest

import generators
from core.items import read_json, read_manifest, write_json
from core.render import pdf_info
from generators.imaging.client import FakeImageClient

from .conftest import DATE, make_brief

NICHES = ("activity_sheets", "coloring_pages", "stickers")
SIZES = (3, 5, 8)


def counts(niche: str, m: dict, d) -> int:
    """The designs in each file of the item (pages without title and answer keys, or stickers)."""
    n = m["pack"]["size"]
    for f in m["files"]:
        if f["kind"] == "zip":
            with zipfile.ZipFile(d / f["path"]) as z:
                assert len(z.namelist()) == f["count"] == n
        elif niche == "stickers":
            assert pdf_info(d / f["path"])["pages"] == f["pages"] == math.ceil(n / 12)
        elif niche == "activity_sheets":
            assert f["pages"] == pdf_info(d / f["path"])["pages"] == 1 + n + m["pack"]["answer_key_pages"]
            assert m["pack"]["activity_pages"] == n and m["pack"]["answer_key_pages"] >= 1
        else:
            assert pdf_info(d / f["path"])["pages"] == f["pages"] == n == m["pack"]["pages"]
    return n


def fake_client(niche):
    return None if niche == "activity_sheets" else FakeImageClient("sticker" if niche == "stickers" else "coloring",
                                                                   long_side_px=360)


@pytest.mark.parametrize("niche", NICHES)
def test_each_niche_makes_one_item_per_pack_size(tmp_path, settings, niche):
    brief = make_brief(niche, sizes=SIZES)
    dirs = generators.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings,
                               client=fake_client(niche))
    ms = {d.name: read_manifest(d) for d in dirs}
    by_size = {m["pack"]["size"]: m for m in ms.values()}
    assert sorted(by_size) == list(SIZES) and dirs[0].name == by_size[8]["item_id"]       # master first
    assert [by_size[s]["pack"]["source"] for s in SIZES] == ["derived", "derived", "master"]
    for d in dirs:
        m = ms[d.name]
        assert counts(niche, m, d) == m["pack"]["size"]
        pk = m["pack"]
        assert len(pk["page_ids"]) == len(set(pk["page_ids"])) == pk["size"] == len(pk["page_types"])
        assert pk["theme"] == "farm animals" and pk["set_id"] == by_size[8]["pack"]["set_id"]
        assert len(m["previews"]) >= 1 and all((d / p["path"]).is_file() for p in m["previews"])
        assert m["ai_assisted"] is (niche != "activity_sheets")
        assert len(m["files"]) <= 5 and all(len(f["path"].split("/")[-1]) <= 70 for f in m["files"])
        assert m["review_reasons"] == [] and m["needs_review"] is False
    master = by_size[8]["pack"]
    for s in (3, 5):
        derived = by_size[s]["pack"]
        assert set(derived["page_ids"]) < set(master["page_ids"])
        assert {(r["size"], r["source"], r["same_set"]) for r in derived["related"]} == \
            {(8, "master", True), (({3, 5} - {s}).pop(), "derived", True)}
    assert {(r["size"], r["source"]) for r in master["related"]} == {(3, "derived"), (5, "derived")}
    # The two derived packs share no page (different sizes may; these are small enough not to).
    assert not set(by_size[3]["pack"]["page_ids"]) & set(by_size[5]["pack"]["page_ids"])
    # The design set records which item used which page.
    doc = read_json(tmp_path / "sets" / master["set_id"] / "set.json")
    assert doc["schema"] == "set/v1" and doc["kind"] == "master" and len(doc["pages"]) == 8
    for p in doc["pages"]:
        assert set(p) >= {"page_id", "type", "subject", "spec", "asset", "quality", "used_in"}
        assert by_size[8]["item_id"] in p["used_in"]
        assert 0.0 <= p["quality"] <= 1.0
    assert sorted(it["size"] for it in doc["items"]) == list(SIZES)
    assert read_json(tmp_path / "sets" / "packs_state.json")["counters"]["main"][niche] == {"3": 1, "5": 1}


@pytest.mark.parametrize("niche", NICHES)
def test_a_fresh_pack_gets_pages_of_its_own(tmp_path, settings, niche):
    # Two five-page packs were made before: the third is fresh (packs.fresh_every {5: 3}).
    write_json(tmp_path / "sets" / "packs_state.json",
               {"schema": "packs_state/v1", "counters": {"main": {niche: {"5": 2}}}, "assignments": {}})
    brief = make_brief(niche, sizes=(5, 8))
    client = fake_client(niche)
    dirs = generators.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings, client=client)
    ms = [read_manifest(d) for d in dirs]
    master, fresh = (next(m for m in ms if m["pack"]["source"] == s) for s in ("master", "fresh"))
    assert fresh["pack"]["size"] == 5 and fresh["pack"]["n"] == 3
    assert fresh["pack"]["set_id"] != master["pack"]["set_id"]
    assert not set(fresh["pack"]["page_ids"]) & set(master["pack"]["page_ids"])     # exclusive pages
    assert all(r["same_set"] is False for r in fresh["pack"]["related"])
    doc = read_json(tmp_path / "sets" / fresh["pack"]["set_id"] / "set.json")
    assert doc["kind"] == "fresh" and len(doc["pages"]) == 5
    assert all(p["used_in"] == [fresh["item_id"]] for p in doc["pages"])
    if niche != "activity_sheets":
        # New subjects where the theme has enough of them, so new art.
        mine = {c["subject"] for c in fresh["pack"]["contents"]}
        assert not mine & {c["subject"] for c in master["pack"]["contents"]}
        assert len(client.calls) == 8 + 5
    counters = read_json(tmp_path / "sets" / "packs_state.json")["counters"]["main"][niche]
    assert counters == {"5": 3}                       # the master never counts


@pytest.mark.parametrize("niche", ("coloring_pages", "stickers"))
def test_a_rerun_reuses_the_design_set_and_pays_nothing_twice(tmp_path, settings, niche):
    brief = make_brief(niche, sizes=(3, 6))
    first = fake_client(niche)
    dirs = generators.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings, client=first)
    assert len(first.calls) == 6
    before = {d.name: read_manifest(d)["pack"]["page_ids"] for d in dirs}
    again = fake_client(niche)
    dirs2 = generators.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings, client=again)
    assert again.calls == [] and [d.name for d in dirs2] == [d.name for d in dirs]
    assert {d.name: read_manifest(d)["pack"]["page_ids"] for d in dirs2} == before
    assert read_json(tmp_path / "sets" / "packs_state.json")["counters"]["main"][niche] == {"3": 1}


def test_a_later_brief_cuts_a_new_size_from_the_themes_master_set(tmp_path, settings):
    first = make_brief("coloring_pages", sizes=(5, 8))
    (m_dir, _) = generators.generate(first, out_root=tmp_path, date=DATE, dry_run=True, settings=settings)
    later = make_brief("coloring_pages", n=2, sizes=(4, 8))
    client = FakeImageClient("coloring", long_side_px=360)
    (d,) = generators.generate(later, out_root=tmp_path, date="2026-10-22", dry_run=True, settings=settings,
                               client=client, sizes=[4])
    m, master = read_manifest(d), read_manifest(m_dir)
    assert client.calls == [] and m["pack"]["source"] == "derived"
    assert m["pack"]["set_id"] == master["pack"]["set_id"] and m["brief_id"] == later["brief_id"]
    assert {(r["item_id"], r["source"]) for r in m["pack"]["related"]} == {(master["item_id"], "master")}
    assert set(m["pack"]["page_ids"]) < set(master["pack"]["page_ids"])


def test_activity_packs_are_byte_identical_on_a_rerun(tmp_path, settings):
    brief = make_brief("activity_sheets", sizes=(4, 9))
    a = tmp_path / "a"
    b = tmp_path / "b"
    da = generators.generate(brief, out_root=a, date=DATE, settings=settings)
    db = generators.generate(brief, out_root=b, date=DATE, settings=settings)
    for x, y in zip(da, db):
        for f in read_manifest(x)["files"]:
            assert (x / f["path"]).read_bytes() == (y / f["path"]).read_bytes()


def test_activity_types_mix_in_sensible_proportions(tmp_path, settings):
    brief = make_brief("activity_sheets", sizes=(5, 20))
    dirs = generators.generate(brief, out_root=tmp_path, date=DATE, settings=settings)
    for d in dirs:
        types = read_manifest(d)["pack"]["type_counts"]
        assert set(types) == {"maze", "word_search", "tracing", "dot_to_dot"}
        assert max(types.values()) - min(types.values()) <= 1


def test_sticker_packs_stay_within_etsys_five_files(tmp_path, settings):
    settings["print"]["page_sizes"]["a5"] = {"width_in": 5.83, "height_in": 8.27}
    settings["print"]["page_sizes"]["a3"] = {"width_in": 11.69, "height_in": 16.54}
    settings["print"]["page_sizes"]["tabloid"] = {"width_in": 11, "height_in": 17}
    client = FakeImageClient("sticker")
    brief = make_brief("stickers", sizes=(3, 5), page_sizes=["us_letter", "a4", "a5", "a3", "tabloid"])
    with pytest.raises(ValueError, match="Etsy"):
        generators.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings, client=client)
    assert client.calls == [] and not tmp_path.joinpath("sets").exists()


@pytest.mark.parametrize("niche", NICHES)
def test_every_niche_implements_the_plugin_api(tmp_path, settings, niche):
    """PAGE_TYPES, plan_pages, render_pages and preview work on a design set's pages alone."""
    import importlib

    from generators.packs import sets as sets_mod

    plugin = importlib.import_module(f"generators.{niche}")
    (d, _) = generators.generate(make_brief(niche, sizes=(3, 4)), out_root=tmp_path, date=DATE, dry_run=True,
                                 settings=settings, client=fake_client(niche))
    m = read_manifest(d)
    doc = read_json(tmp_path / "sets" / m["pack"]["set_id"] / "set.json")
    base = sets_mod.set_dir(tmp_path, doc["set_id"])
    pages = [dict(p, asset_files={k: str(base / v) for k, v in (p.get("asset") or {}).items()})
             for p in doc["pages"]]
    assert {p["type"] for p in pages} <= set(plugin.PAGE_TYPES)
    ctx = dict(theme="farm animals", niche=niche, audience="kids 4-8", keywords=["farm animals"], style="",
               system="generic", item_seed="x", page_sizes=["us_letter"], pack={"size": 4, "source": "master"})
    n = plugin.render_pages(pages, tmp_path / "x.pdf", page_size="a4", settings=settings, ctx=ctx)
    assert n == pdf_info(tmp_path / "x.pdf")["pages"] >= 1
    shots = plugin.preview(pages, tmp_path / "shots", settings=settings, ctx=ctx, limit=2)
    assert 1 <= len(shots) <= 2 and all(p.suffix == ".png" and p.is_file() for p in shots)
    again = plugin.plan_pages(dict(ctx, subjects=[p["subject"] for p in pages] + ["a spare cow"]), 4,
                              seed=doc["set_id"], settings=settings)
    assert len(again) == 4 and len({p["page_id"] for p in again}) == 4
    assert again == plugin.plan_pages(dict(ctx, subjects=[p["subject"] for p in pages] + ["a spare cow"]), 4,
                                      seed=doc["set_id"], settings=settings)      # deterministic


def test_no_two_word_searches_of_a_pack_share_a_word_list(settings):
    """A 50-page pack has 13 word searches from a ~20-word theme pool: the lists are disjoint
    while the pool lasts, then mixed, and never repeat; a fresh set avoids the master's lists."""
    from generators import activity_sheets

    ctx = {"theme": "farm animals", "audience": "kids 4-8", "keywords": ["farm animals"],
           "activity_types": ["maze", "word_search", "tracing", "dot_to_dot"], "used_subjects": []}
    pages = activity_sheets.plan_pages(ctx, 50, seed="set-a", settings=settings)
    lists = [p["subject"] for p in pages if p["type"] == "word_search"]
    assert len(lists) == 13 and len(set(lists)) == 13
    first = [p["spec"]["words"] for p in pages if p["type"] == "word_search"][:2]
    assert first[0][:2] == ["FARM", "ANIMALS"] and not set(first[0]) & set(first[1])   # theme words first
    assert pages == activity_sheets.plan_pages(ctx, 50, seed="set-a", settings=settings)  # deterministic
    fresh = activity_sheets.plan_pages(dict(ctx, used_subjects=lists), 8, seed="set-b", settings=settings)
    assert not {p["subject"] for p in fresh if p["type"] == "word_search"} & set(lists)


def test_a_theme_with_few_subjects_gets_fewer_spares_not_a_failure(tmp_path, settings, monkeypatch):
    """Spare subjects only replace refused images: a theme whose tables run out between the
    pack size and pack size + spares still makes its pack."""
    import importlib

    subjects_mod = importlib.import_module("generators.packs.subjects")   # the module, not packs.subjects()
    real = subjects_mod.subject_pool
    monkeypatch.setattr(subjects_mod, "subject_pool", lambda theme, keywords=(): real(theme, keywords)[:9])
    client = FakeImageClient("sticker", long_side_px=360)
    (d,) = generators.generate(make_brief("stickers", sizes=(8,)), out_root=tmp_path, date=DATE, dry_run=True,
                               settings=settings, client=client)
    assert read_manifest(d)["pack"]["stickers"] == 8 and len(client.calls) == 8
    monkeypatch.setattr(subjects_mod, "subject_pool", lambda theme, keywords=(): real(theme, keywords)[:7])
    with pytest.raises(ValueError, match="distinct subjects"):
        generators.generate(make_brief("stickers", n=2, sizes=(8,)), out_root=tmp_path / "x", date=DATE,
                            dry_run=True, settings=settings, client=FakeImageClient("sticker", long_side_px=360))


def test_every_activity_pack_of_a_theme_gets_its_on_theme_picture(tmp_path, settings):
    brief = make_brief("activity_sheets", "dinosaurs", sizes=(5, 10, 20))
    dirs = generators.generate(brief, out_root=tmp_path, date=DATE, settings=settings)
    for d in dirs:
        m = read_manifest(d)
        shapes = [c["title"] for c in m["pack"]["contents"] if c["type"] == "dot_to_dot"]
        assert shapes, m["pack"]["size"]
        doc = read_json(tmp_path / "sets" / m["pack"]["set_id"] / "set.json")
        picked = {p["page_id"]: p for p in doc["pages"]}
        dots = [picked[pid] for pid in m["pack"]["page_ids"] if picked[pid]["type"] == "dot_to_dot"]
        assert any(p["spec"]["shape"] == "dinosaur" for p in dots), (m["pack"]["size"], [p["subject"] for p in dots])


def test_a_fresh_activity_pack_repeats_no_puzzle_of_the_master(tmp_path, settings):
    """'All-new pages': a fresh pack's word lists, tracing lessons and pictures are not the
    master set's (mazes are new random grids anyway)."""
    write_json(tmp_path / "sets" / "packs_state.json",
               {"schema": "packs_state/v1", "counters": {"main": {"activity_sheets": {"20": 5}}}, "assignments": {}})
    dirs = generators.generate(make_brief("activity_sheets", sizes=(20, 30)), out_root=tmp_path, date=DATE,
                               settings=settings)
    sets = {}
    for d in dirs:
        pk = read_manifest(d)["pack"]
        sets[pk["source"]] = read_json(tmp_path / "sets" / pk["set_id"] / "set.json")["pages"]
    assert set(sets) == {"master", "fresh"}
    subjects = {k: {p["subject"] for p in v if p["type"] != "maze"} for k, v in sets.items()}
    assert len(subjects["fresh"]) == sum(1 for p in sets["fresh"] if p["type"] != "maze")
    assert not subjects["fresh"] & subjects["master"]


@pytest.mark.parametrize("niche, theme, extra", [("stationery", "minimalist", {}),
                                                 ("ttrpg", "dark fantasy", {"system": "generic"})])
def test_new_niche_cli_makes_one_item_per_pack(tmp_path, capsys, niche, theme, extra):
    """python -m generators.stationery / generators.ttrpg (generators/cli.py options)."""
    import importlib
    import json
    from pathlib import Path

    brief = make_brief(niche, theme, sizes=(3, 5), audience="adults", **extra)
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    cli = importlib.import_module(f"generators.{niche}.__main__")
    out = tmp_path / "out"
    assert cli.main(["--brief", str(path), "--dry-run", "--out", str(out), "--date", "2026-10-15"]) == 0
    dirs = [Path(line) for line in capsys.readouterr().out.split()]
    assert sorted(read_manifest(d)["pack"]["size"] for d in dirs) == [3, 5]
    assert all(d.parent == out and read_manifest(d)["niche"] == niche for d in dirs)
