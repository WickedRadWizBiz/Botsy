"""The ttrpg niche plugin around the pack engine: generate() delegation, engine hooks, the
optional AI accents, and one dry run through the real pack engine (all in tmp_path)."""
import json
import sys

import pytest

import generators
import generators.ttrpg as ttrpg
from core import systems
from core.render import pdf_info
from generators.imaging.client import FakeImageClient, GeminiImageClient

from .conftest import make_ctx, pdf_texts


def test_registered_and_shaped_like_a_plugin():
    assert generators.NICHE_MODULES["ttrpg"] == "generators.ttrpg"
    assert ttrpg.NICHE == "ttrpg" and ttrpg.AI_ASSISTED is False and ttrpg.FILE_LABEL == "rpg"
    for name in ("PAGE_TYPES", "plan_pages", "render_pages", "preview", "generate"):
        assert hasattr(ttrpg, name)
    assert ttrpg.files_per_pack(["us_letter", "a4"]) == 2


def test_generate_delegates_to_the_pack_engine(monkeypatch, tmp_path):
    import generators.packs as packs

    seen = {}

    def fake_generate_packs(brief, *, plugin, **kw):
        seen.update(brief=brief, plugin=plugin, kw=kw)
        return [tmp_path / "item"]

    monkeypatch.setattr(packs, "generate_packs", fake_generate_packs)
    brief = {"niche": "ttrpg", "theme": "dark fantasy", "system": "dnd5e"}
    out = ttrpg.generate(brief, out_root=tmp_path, date="2026-10-16", dry_run=True, sizes=[5])
    assert out == [tmp_path / "item"]
    assert seen["plugin"] is sys.modules["generators.ttrpg"]
    assert seen["brief"] is brief
    assert seen["kw"] == {"out_root": tmp_path, "date": "2026-10-16", "dry_run": True, "sizes": [5]}
    seen.clear()
    generators.generate(brief, dry_run=True)
    assert seen["plugin"] is ttrpg


def test_max_pack_size_follows_settings(settings):
    assert ttrpg.max_pack_size(settings) == 40
    settings["ttrpg"] = {"max_pages": 30}
    assert ttrpg.max_pack_size(settings) == 30


def test_pack_fields_record_contents_and_print_rules(tmp_path, settings):
    ctx = make_ctx("dark fantasy", "dnd5e")
    pages = ttrpg.plan_pages(ctx, 5, seed="s", settings=settings)
    files = []
    for size in ("us_letter", "a4"):
        out = tmp_path / f"{size}.pdf"
        assert ttrpg.render_pages(pages, out, page_size=size, settings=settings, ctx=ctx) == 5
        files.append({"path": out.name, "kind": "pdf", "page_size": size, "pages": pdf_info(out)["pages"]})
    fields = ttrpg.pack_fields(pages, files, settings=settings, ctx=ctx, set_doc={})
    assert fields["pages"] == 5 and fields["title_pages"] == 0 and fields["undated"] is True
    assert fields["genre"] == "dark_fantasy" and fields["focus"] == "campaign"
    assert fields["kit"] == "campaign starter" and fields["system_fields"] is True
    assert fields["ai_accents"] is False
    assert [c["page"] for c in fields["contents"]] == [1, 2, 3, 4, 5]
    assert [c["type"] for c in fields["contents"]] == [p["type"] for p in pages]
    assert fields["type_labels"]["npc_tracker"] == "NPC Tracker"
    assert fields["min_font_pt"] >= 7.0 and fields["min_line_gap_mm"] >= 6.0
    assert set(fields["print_checks"]) == {"us_letter", "a4"}
    assert all(c["out_of_bounds"] == 0 and c["overflow"] == 0 for c in fields["print_checks"].values())
    text = json.dumps(fields)
    assert systems.detect_systems(text) == [] and "dnd5e" not in text
    generic = ttrpg.pack_fields(ttrpg.plan_pages(make_ctx("hex map"), 8, seed="s", settings=settings), files,
                                settings=settings, ctx=make_ctx("hex map"), set_doc={})
    assert generic["kit"] is None and generic["system_fields"] is False and generic["focus"] == "maps"


def test_printed_text_and_review_reasons(settings, blocklist):
    ctx = make_ctx("cozy", "pf2e")
    pages = ttrpg.plan_pages(ctx, 20, seed="s", settings=settings)
    texts = ttrpg.printed_text(pages, ctx, settings)
    assert "Cozy RPG Pages" in texts and any("Fortitude" in t for t in texts)
    assert blocklist.check(*texts).blocked == [] and systems.detect_systems("\n".join(texts)) == []
    assert ttrpg.review_reasons(pages, settings=settings) == []


def test_brief_review_reasons_flag_an_unknown_system(settings):
    assert ttrpg.brief_review_reasons({"system": "dnd5e"}, settings) == []
    assert ttrpg.brief_review_reasons({"system": "generic"}, settings) == []
    assert ttrpg.brief_review_reasons({}, settings) == []
    reasons = ttrpg.brief_review_reasons({"system": "srd99"}, settings)
    assert len(reasons) == 1 and "srd99" in reasons[0] and "system-neutral" in reasons[0]


def test_no_image_client_unless_accents_are_on_in_a_real_run(settings):
    assert ttrpg.resolve_image_client(None, dry_run=False, settings=settings) is None
    settings["ttrpg"] = {"ai_accents": True}
    assert ttrpg.resolve_image_client(None, dry_run=True, settings=settings) is None
    fake = FakeImageClient("coloring", long_side_px=256)
    assert ttrpg.resolve_image_client(fake, dry_run=False, settings=settings) is fake


def test_accents_are_a_no_op_by_default(tmp_path, settings, blocklist):
    pages = ttrpg.plan_pages(make_ctx(), 5, seed="s", settings=settings)
    fake = FakeImageClient("coloring", long_side_px=256)
    out = ttrpg.build_pages(pages, 5, tmp_path, settings=settings, ctx=make_ctx(), client=fake, blocklist=blocklist)
    assert out["model"] is None and out["skipped"] == [] and fake.calls == []
    assert [p["page_id"] for p in out["pages"]] == [p["page_id"] for p in pages]
    assert [p["quality"] for p in out["pages"]] == [p["quality"] for p in pages]


def test_accents_when_enabled_use_our_own_prompts(tmp_path, settings, blocklist):
    settings["ttrpg"] = {"ai_accents": True, "accent_images": 2}
    ctx = make_ctx("pirate", "dnd5e")
    pages = ttrpg.plan_pages(ctx, 5, seed="s", settings=settings)
    (tmp_path / "pages").mkdir()
    fake = FakeImageClient("coloring", long_side_px=256)
    out = ttrpg.build_pages(pages, 5, tmp_path, settings=settings, ctx=ctx, client=fake, blocklist=blocklist,
                            item_id="item", dry_run=False)
    assert len(fake.calls) == 2 and out["model"] == "fake-image"
    for call in fake.calls:
        assert blocklist.check(call["prompt"]).blocked == [] and systems.detect_systems(call["prompt"]) == []
        assert "no text" in call["prompt"] and "pirate" not in call["prompt"]
    for p in out["pages"]:
        assert p["ai_assisted"] is True and (tmp_path / p["asset"]["accent"]).is_file()
        assert p["spec"]["prompt"] and p["spec"]["accent_subject"]
    # the accent is printed in the header slot; render still passes every rule
    pages_with_files = [dict(p, asset_files={"accent": str(tmp_path / p["asset"]["accent"])}) for p in out["pages"]]
    pdf = tmp_path / "x.pdf"
    ttrpg.render_pages(pages_with_files, pdf, page_size="us_letter", settings=settings, ctx=ctx)
    assert pdf_info(pdf)["pages"] == 5
    fields = ttrpg.pack_fields(out["pages"], [], settings=settings, ctx=ctx, set_doc={})
    assert fields["ai_accents"] is True
    # dry runs never pay for accents
    fake2 = FakeImageClient("coloring", long_side_px=256)
    ttrpg.build_pages(pages, 5, tmp_path, settings=settings, ctx=ctx, client=fake2, blocklist=blocklist, dry_run=True)
    assert fake2.calls == []


def test_dry_run_through_the_pack_engine(tmp_path, settings, monkeypatch):
    """One brief, packs of 5 and 8 pages, through generators.packs (dry run, tmp_path only)."""
    from generators.imaging import common

    monkeypatch.setattr(common, "run_client", lambda *a, **k: pytest.fail("no real image client in tests"))
    assert not isinstance(ttrpg.resolve_image_client(None, dry_run=True, settings=settings), GeminiImageClient)
    settings.setdefault("packs", {})["state_file"] = str(tmp_path / "packs_state.json")
    brief = {"brief_id": "ttrpg-2026-10-16-01", "niche": "ttrpg", "theme": "dark fantasy", "audience": "adults",
             "keywords": ["dark fantasy"], "price_usd": 5.0, "page_sizes": ["us_letter", "a4"], "pack_sizes": [5, 8],
             "page_count": 8, "style_notes": "", "evidence": {"listing_count": 23, "score": 0.8}, "system": "dnd5e",
             "compliance": {"blocked": [], "flagged": []}, "needs_review": False}
    dirs = generators.generate(brief, out_root=tmp_path / "out", date="2026-10-16", dry_run=True, settings=settings)
    assert len(dirs) == 2
    by_size = {}
    for d in dirs:
        m = json.loads((d / "manifest.json").read_text())
        by_size[m["pack"]["size"]] = m
        assert m["niche"] == "ttrpg" and m["system"] == "dnd5e" and m["ai_assisted"] is False
        assert m["needs_review"] is False and m["review_reasons"] == []
        assert m["generator"] == {"name": "ttrpg", "dry_run": True}
        assert len(m["files"]) == 2 and m["previews"]
        for f in m["files"]:
            assert (d / f["path"]).is_file() and f["pages"] == m["pack"]["size"]
            assert systems.detect_systems(f["path"]) == []
            texts = pdf_texts(d / f["path"])
            assert systems.detect_systems("\n".join(texts)) == []
        assert m["pack"]["min_font_pt"] >= 7.0 and m["pack"]["min_line_gap_mm"] >= 6.0
    assert by_size[8]["pack"]["source"] == "master"
    assert by_size[5]["pack"]["source"] == "derived"
    assert by_size[5]["pack"]["kit"] == "campaign starter"       # curated to the starter mix
    assert set(by_size[5]["pack"]["page_ids"]) <= set(by_size[8]["pack"]["page_ids"])
