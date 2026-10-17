"""The niche plugin API around the pack engine: generate() delegation, engine hooks, the
optional AI accents, and one dry run through the real pack engine (all in tmp_path)."""
import copy
import json
import sys

import pytest

import generators
import generators.stationery as stationery
from core.render import pdf_info
from generators.imaging.client import FakeImageClient, GeminiImageClient

from .conftest import every_layout, make_ctx


def test_registered_and_shaped_like_a_plugin():
    assert generators.NICHE_MODULES["stationery"] == "generators.stationery"
    assert stationery.NICHE == "stationery" and stationery.AI_ASSISTED is False
    for name in ("PAGE_TYPES", "plan_pages", "render_pages", "preview", "generate"):
        assert hasattr(stationery, name)


def test_generate_delegates_to_the_pack_engine(monkeypatch, tmp_path):
    import generators.packs as packs

    seen = {}

    def fake_generate_packs(brief, *, plugin, **kw):
        seen.update(brief=brief, plugin=plugin, kw=kw)
        return [tmp_path / "item"]

    monkeypatch.setattr(packs, "generate_packs", fake_generate_packs)
    brief = {"niche": "stationery", "theme": "adhd"}
    out = stationery.generate(brief, out_root=tmp_path, date="2026-10-16", dry_run=True, sizes=[5])
    assert out == [tmp_path / "item"]
    assert seen["plugin"] is sys.modules["generators.stationery"]
    assert seen["brief"] is brief
    assert seen["kw"] == {"out_root": tmp_path, "date": "2026-10-16", "dry_run": True, "sizes": [5]}
    # the shared dispatcher reaches it too
    seen.clear()
    generators.generate(brief, dry_run=True)
    assert seen["plugin"] is stationery


def test_pack_fields_record_contents_and_print_rules(tmp_path, settings):
    pages = stationery.plan_pages(make_ctx("adhd"), 5, seed="s", settings=settings)
    files = []
    for size in ("us_letter", "a4"):
        out = tmp_path / f"{size}.pdf"
        stationery.render_pages(pages, out, page_size=size, settings=settings, ctx=make_ctx("adhd"))
        files.append({"path": out.name, "kind": "pdf", "page_size": size, "pages": pdf_info(out)["pages"]})
    fields = stationery.pack_fields(pages, files, settings=settings, ctx=make_ctx("adhd"), set_doc={})
    assert fields["pages"] == 5 and fields["title_pages"] == 0 and fields["undated"] is True
    assert fields["style"] == "adhd_friendly" and fields["ai_accents"] is False
    assert [c["page"] for c in fields["contents"]] == [1, 2, 3, 4, 5]
    assert [c["type"] for c in fields["contents"]] == [p["type"] for p in pages]
    assert fields["min_font_pt"] >= 7.0 and fields["min_line_gap_mm"] >= 6.0
    assert set(fields["print_checks"]) == {"us_letter", "a4"}
    assert pages[fields["hero_page"] - 1]["type"] == "daily_planner"     # the ADHD-friendly mix's first pick
    json.dumps(fields)


def test_review_reasons_empty_for_clean_pages(settings):
    assert stationery.review_reasons(every_layout("cozy"), settings=settings) == []


def test_hooks_for_sizes_and_files(settings):
    assert stationery.max_pack_size(settings) == 60
    assert stationery.files_per_pack(["us_letter", "a4"]) == 2


# --- AI accents ---------------------------------------------------------------------------------

def _accent_settings(settings, on=True):
    s = copy.deepcopy(settings)
    s["stationery"] = dict(s.get("stationery") or {}, ai_accents=on, accent_images=2)
    return s


def test_no_image_client_unless_accents_are_on_and_real(settings, monkeypatch):
    import generators.imaging.common as common

    monkeypatch.setattr(common, "run_client", lambda s: pytest.fail("no real client may be built"))
    assert stationery.resolve_image_client(None, dry_run=False, settings=settings) is None        # default off
    on = _accent_settings(settings)
    assert stationery.resolve_image_client(None, dry_run=True, settings=on) is None             # dry run
    fake = FakeImageClient("coloring", long_side_px=256)
    assert stationery.resolve_image_client(fake, dry_run=False, settings=on) is fake


def test_build_pages_without_accents_keeps_pages(tmp_path, settings):
    cands = stationery.plan_pages(make_ctx("botanical"), 5, seed="s", settings=settings)
    fake = FakeImageClient("coloring", long_side_px=256)
    for s, dry in ((settings, False), (_accent_settings(settings), True)):
        res = stationery.build_pages(cands, 5, tmp_path, settings=s, ctx=make_ctx("botanical"), client=fake,
                                     blocklist=None, dry_run=dry)
        assert [p["page_id"] for p in res["pages"]] == [p["page_id"] for p in cands]
        assert res["model"] is None
        assert [p["quality"] for p in res["pages"]] == [p["quality"] for p in cands]   # the plan's ranking
        assert not any(p.get("ai_assisted") for p in res["pages"])
    assert fake.calls == []


def test_build_pages_with_accents_draws_them(tmp_path, settings, blocklist):
    s = _accent_settings(settings)
    cands = stationery.plan_pages(make_ctx("botanical"), 5, seed="s", settings=s)
    (tmp_path / "pages").mkdir()
    fake = FakeImageClient("coloring", long_side_px=256)
    res = stationery.build_pages(cands, 5, tmp_path, settings=s, ctx=make_ctx("botanical"), client=fake,
                                 blocklist=blocklist, item_id="2026-10-16-stationery-botanical-0001")
    assert len(fake.calls) == 2 and res["model"] == "fake-image"
    for call in fake.calls:
        assert call["aspect_ratio"] == "1:1" and "no text" in call["prompt"] and "botanical" not in call["prompt"]
    pages = res["pages"]
    assert all(p["ai_assisted"] and p["asset"]["accent"].startswith("pages/accent-") for p in pages)
    assert all(p["spec"]["prompt"] in [c["prompt"] for c in fake.calls] for p in pages)
    for p in pages:
        assert (tmp_path / p["asset"]["accent"]).is_file()
    # the renderer places the accent from asset_files (as the engine hands pages over)
    with_files = [dict(p, asset_files={"accent": str(tmp_path / p["asset"]["accent"])}) for p in pages]
    out = tmp_path / "accent.pdf"
    stationery.render_pages(with_files, out, page_size="us_letter", settings=s, ctx=make_ctx("botanical"))
    assert pdf_info(out)["pages"] == 5
    fields = stationery.pack_fields(with_files, [{"kind": "pdf", "page_size": "us_letter"}], settings=s,
                                    ctx=make_ctx("botanical"), set_doc={})
    assert fields["ai_accents"] is True


def test_dry_run_never_swaps_in_a_paid_client(settings):
    on = _accent_settings(settings)
    real = GeminiImageClient.__new__(GeminiImageClient)
    assert stationery.resolve_image_client(real, dry_run=True, settings=on) is None


# --- through the real pack engine ---------------------------------------------------------------

@pytest.fixture
def brief():
    return {
        "brief_id": "stationery-2026-10-16-01", "niche": "stationery", "theme": "adhd", "audience": "adults",
        "keywords": ["adhd planner printable"], "page_count": 20, "pack_sizes": [5, 20], "price_usd": 4.5,
        "page_sizes": ["us_letter", "a4"], "style_notes": "",
        "evidence": {"listing_count": 23, "listing_rows": 23, "shop_count": 5, "score": 0.8},
        "compliance": {"blocked": [], "flagged": []}, "needs_review": False,
    }


def test_dry_run_through_the_pack_engine(tmp_path, settings, brief):
    pytest.importorskip("generators.packs")
    out = tmp_path / "out"
    dirs = stationery.generate(brief, brief_file="data/briefs/stationery-2026-10-16.json", out_root=out,
                               date="2026-10-16", dry_run=True, settings=settings)
    assert len(dirs) == 2
    sizes = set()
    for d in dirs:
        m = json.loads((d / "manifest.json").read_text())
        pack = m["pack"]
        sizes.add(pack["size"])
        assert m["niche"] == "stationery" and m["ai_assisted"] is False and m["prompts"] == []
        assert pack["pages"] == pack["size"] == len(pack["page_ids"]) == len(set(pack["page_ids"]))
        assert pack["style"] == "adhd_friendly" and pack["min_font_pt"] >= 7 and pack["min_line_gap_mm"] >= 6
        pdfs = [f for f in m["files"] if f["kind"] == "pdf"]
        assert {f["page_size"] for f in pdfs} == {"us_letter", "a4"}
        for f in pdfs:
            assert pdf_info(d / f["path"])["pages"] == pack["size"]
            assert "planner" in f["path"]
        assert m["previews"] and all((d / p["path"]).is_file() for p in m["previews"])
    assert sizes == {5, 20}
    # nothing was written outside the output folder
    assert not (tmp_path / "data").exists()
