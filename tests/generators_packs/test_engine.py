"""generate_packs() with a minimal plugin (the API stationery and ttrpg implement), and the
engine's own rules: refused page plans, partial failures, paid art, the pack state file."""
import types

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from core.items import read_json, read_manifest
from core.render import page_size_points, pdf_info
from generators import coloring_pages, packs
from generators.imaging.client import FakeImageClient
from generators.packs import PackPlanError, generate_packs

from .conftest import DATE, make_brief


def make_plugin(niche="stationery", *, fail_on_size=None, pages=None):
    """A plugin with only the required API: PAGE_TYPES, plan_pages, render_pages, preview."""
    mod = types.ModuleType(f"fake_{niche}")
    mod.NICHE = niche
    mod.PAGE_TYPES = ("daily_planner", "brain_dump", "habit_tracker")
    mod.seen_ctx = []

    def plan_pages(ctx, n, *, seed, settings):
        mod.seen_ctx.append(dict(ctx))
        if pages is not None:
            return pages(ctx, n, seed)
        return [{"page_id": f"pg{i:02d}", "type": mod.PAGE_TYPES[i % 3], "subject": f"page {i}",
                 "spec": {"layout": mod.PAGE_TYPES[i % 3], "seed": f"{seed}|{i}"}} for i in range(n)]

    def render_pages(pages_, out_pdf, *, page_size, settings, ctx):
        if fail_on_size and ctx["pack"]["size"] == fail_on_size:
            raise RuntimeError("disk full")
        c = canvas.Canvas(str(out_pdf), pagesize=page_size_points(page_size, settings), invariant=1)
        for p in pages_:
            c.drawString(72, 72, p["type"].replace("_", " ").title())
            c.showPage()
        c.save()
        return len(pages_)

    def preview(pages_, out_dir, *, settings, ctx, limit=6):
        out_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for i, _ in enumerate(pages_[:limit], 1):
            path = out_dir / f"page-{i:02d}.png"
            Image.new("L", (85, 110), 255).save(path)
            out.append(path)
        return out

    mod.plan_pages, mod.render_pages, mod.preview = plan_pages, render_pages, preview
    return mod


def test_a_minimal_plugin_makes_every_pack_with_the_default_hooks(tmp_path, settings):
    plugin = make_plugin()
    brief = make_brief("stationery", "adhd friendly", sizes=(5, 20, 30))
    planned = packs.preview(brief, settings=settings, out_root=tmp_path, date=DATE, plugin=plugin)
    dirs = generate_packs(brief, plugin=plugin, out_root=tmp_path, date=DATE, settings=settings)
    assert [d.name for d in dirs] == [p.item_id for p in planned]          # preview names what is made
    assert [(p.size, p.source) for p in planned] == [(30, "master"), (5, "derived"), (20, "derived")]
    for d in dirs:
        m = read_manifest(d)
        n = m["pack"]["size"]
        assert m["pack"]["pages"] == n and m["ai_assisted"] is False and m["prompts"] == []
        assert m["generator"] == {"name": "stationery", "dry_run": False} and "system" not in m
        assert [f["path"] for f in m["files"]] == [f"files/adhd-friendly-stationery-{n}-pages-{s}.pdf"
                                                   for s in ("us_letter", "a4")]
        assert all(pdf_info(d / f["path"])["pages"] == f["pages"] == n for f in m["files"])
        assert len(m["previews"]) == min(n, 8) and all(Image.open(d / p["path"]).mode == "RGB" for p in m["previews"])
        assert m["pack"]["type_counts"] == {t: m["pack"]["page_types"].count(t) for t in plugin.PAGE_TYPES}
    ctx = plugin.seen_ctx[0]
    assert set(ctx) >= {"theme", "niche", "audience", "keywords", "style", "system", "item_seed", "page_sizes"}
    assert ctx["system"] == "generic" and ctx["item_seed"].startswith("2026-10-15-stationery-adhd-friendly-")
    assert len(plugin.seen_ctx) == 1                                        # one design set for three packs


def test_ttrpg_items_record_the_game_system_id(tmp_path, settings):
    plugin = make_plugin("ttrpg")
    brief = make_brief("ttrpg", "dark fantasy", sizes=(5, 8), system="dnd5e")
    dirs = generate_packs(brief, plugin=plugin, out_root=tmp_path, date=DATE, settings=settings)
    assert all(read_manifest(d)["system"] == "dnd5e" for d in dirs)
    assert plugin.seen_ctx[0]["system"] == "dnd5e"
    doc = read_json(tmp_path / "sets" / read_manifest(dirs[0])["pack"]["set_id"] / "set.json")
    assert doc["system"] == "dnd5e"
    other = generate_packs(make_brief("ttrpg", "space opera", sizes=(5,)), plugin=make_plugin("ttrpg"),
                           out_root=tmp_path, date=DATE, settings=settings)
    assert read_manifest(other[0])["system"] == "generic"


@pytest.fixture
def blocklisted(tmp_path, settings):
    path = tmp_path / "blocklist.txt"
    path.write_text("Disney\n!Dungeons & Dragons\n?Erin Condren\n", encoding="utf-8")
    settings["compliance"] = dict(settings["compliance"], trademark_blocklist=str(path))
    return settings


@pytest.mark.parametrize("pages,match", [
    (lambda ctx, n, seed: [{"page_id": "a", "type": "x", "subject": "s", "spec": {}}], "gave 1 pages"),
    (lambda ctx, n, seed: [{"page_id": "a", "type": "x", "subject": f"{i}", "spec": {}} for i in range(n)], "repeated"),
    (lambda ctx, n, seed: [{"page_id": f"{i}", "type": "x", "subject": "a Disney castle", "spec": {}}
                           for i in range(n)], "blocked"),
    (lambda ctx, n, seed: [{"page_id": f"{i}", "type": "x", "subject": "s",
                            "spec": {"heading": "Dungeons & Dragons Character Sheet"}} for i in range(n)], "blocked"),
])
def test_a_page_plan_the_engine_cannot_use_is_refused_before_anything_is_built(tmp_path, blocklisted, pages, match):
    out = tmp_path / "out"
    with pytest.raises(PackPlanError, match=match):
        generate_packs(make_brief("stationery", sizes=(3, 5)), plugin=make_plugin(pages=pages), out_root=out,
                       date=DATE, settings=blocklisted)
    assert not out.exists() or not any(out.iterdir())


def test_review_terms_in_printed_text_mark_the_item(tmp_path, blocklisted):
    def pages(ctx, n, seed):
        return [{"page_id": f"{i}", "type": "x", "subject": "s", "spec": {"note": "Erin Condren style"}}
                for i in range(n)]

    (d,) = generate_packs(make_brief("stationery", sizes=(3,)), plugin=make_plugin(pages=pages),
                          out_root=tmp_path / "out", date=DATE, settings=blocklisted)
    m = read_manifest(d)
    assert m["needs_review"] is True and any("Erin Condren" in r for r in m["review_reasons"])


def test_printed_text_gets_the_run_settings_and_system_spellings_are_refused(tmp_path, settings):
    """printed_text(pages, ctx, settings=) gets the run's settings when it takes them, and a
    spelling of a game system no "!" line covers ("DnD5e") is still refused on a page."""
    seen = []

    def with_settings(footer):
        plugin = make_plugin()

        def printed_text(pages_, ctx, settings=None):
            seen.append(settings)
            return [p["subject"] for p in pages_] + [footer]

        plugin.printed_text = printed_text
        return plugin

    run_settings = dict(settings, marker={"run": True})
    (d,) = generate_packs(make_brief("stationery", sizes=(3,)), plugin=with_settings("For personal use only"),
                          out_root=tmp_path / "a", date=DATE, settings=run_settings)
    assert seen and all(s is run_settings for s in seen)
    with pytest.raises(ValueError, match="name game systems"):
        generate_packs(make_brief("stationery", "focus", sizes=(3,)), plugin=with_settings("A DnD5e tracker"),
                       out_root=tmp_path / "b", date=DATE, settings=settings)

    old_style = make_plugin()                                      # printed_text(pages, ctx) still works
    old_style.printed_text = lambda pages_, ctx: [p["subject"] for p in pages_]
    assert generate_packs(make_brief("stationery", "calm", sizes=(3,)), plugin=old_style,
                          out_root=tmp_path / "c", date=DATE, settings=settings)


def test_a_failed_pack_keeps_the_packs_made_before_it(tmp_path, settings):
    brief = make_brief("stationery", sizes=(5, 8))
    with pytest.raises(RuntimeError, match="disk full") as exc:
        generate_packs(brief, plugin=make_plugin(fail_on_size=5), out_root=tmp_path, date=DATE, settings=settings)
    (master,) = exc.value.pack_dirs
    assert read_manifest(master)["pack"]["source"] == "master"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []   # no scratch folder left
    state = read_json(tmp_path / "sets" / "packs_state.json")
    assert state["counters"] == {}                                   # the 5-page pack was not made: no count
    dirs = generate_packs(brief, plugin=make_plugin(), out_root=tmp_path, date=DATE, settings=settings)
    assert dirs[0] == master and len(dirs) == 2
    assert read_json(tmp_path / "sets" / "packs_state.json")["counters"]["main"]["stationery"] == {"5": 1}


def test_a_dry_run_never_replaces_paid_art(tmp_path, settings):
    brief = make_brief("coloring_pages", sizes=(2, 3))
    paid = FakeImageClient("coloring", long_side_px=300)
    dirs = coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=False, client=paid, settings=settings)
    assert read_manifest(dirs[0])["generator"]["dry_run"] is False
    with pytest.raises(ValueError, match="real"):
        coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings)
    for d in dirs:                                              # the real items are still there, untouched
        assert read_manifest(d)["generator"]["dry_run"] is False


def test_the_configured_output_folder_keeps_its_counters_in_packs_state_file(tmp_path, settings):
    real = tmp_path / "real"
    settings["paths"] = dict(settings["paths"], output_dir=str(real))
    settings["packs"] = {"state_file": str(tmp_path / "state" / "packs_state.json")}
    generate_packs(make_brief("stationery", sizes=(3, 5)), plugin=make_plugin(), out_root=real, date=DATE,
                   settings=settings)
    assert read_json(tmp_path / "state" / "packs_state.json")["counters"]["main"]["stationery"] == {"3": 1}
    assert not (real / "sets" / "packs_state.json").exists()


def test_tests_never_touch_the_repos_own_pack_state(tmp_path, settings):
    from core.config import ROOT

    real_state = ROOT / "data" / "packs_state.json"
    before = real_state.read_bytes() if real_state.exists() else None
    settings["paths"] = dict(settings["paths"], output_dir=str(tmp_path))     # packs.state_file: the default
    generate_packs(make_brief("stationery", sizes=(3, 5)), plugin=make_plugin(), out_root=tmp_path, date=DATE,
                   settings=settings)
    assert (real_state.read_bytes() if real_state.exists() else None) == before
