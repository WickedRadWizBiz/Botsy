"""generate(): PDFs per page size, manifest contract, previews, determinism, compliance."""
import copy
import json

import pytest
from PIL import Image
from reportlab.pdfgen import canvas as rl_canvas

import generators
from core.items import read_manifest
from core.render import page_size_points, pdf_info
from generators.activity_sheets import generate, page_sizes_for
from generators.activity_sheets.pack import plan_pack, resolve_difficulty, rng_for

MANIFEST_KEYS = {"schema", "item_id", "niche", "shop_id", "brief_id", "brief_file", "theme", "keywords",
                 "audience", "price_usd", "created", "ai_assisted", "needs_review", "generator", "prompts",
                 "files", "previews", "assets", "status"}


def test_manifest_matches_contract(pack_dir, settings):
    m = read_manifest(pack_dir)
    assert MANIFEST_KEYS <= set(m)
    assert m["schema"] == "manifest/v1" and m["status"] == "generated"
    assert m["item_id"] == pack_dir.name and m["item_id"].startswith("2026-10-15-activity_sheets-under-the-sea-")
    assert m["niche"] == "activity_sheets" and m["shop_id"] == "main"
    assert m["brief_file"] == "data/briefs/activity_sheets-2026-10-15.json"
    assert m["ai_assisted"] is False and m["needs_review"] is False
    assert m["generator"] == {"name": "activity_sheets", "dry_run": False}
    assert m["prompts"] == [] and m["assets"] == [] and m["created"] == "2026-10-15"
    assert m["price_usd"] == 4.0 and m["keywords"] == ["ocean activity pages"]
    for f in m["files"]:
        assert set(f) == {"path", "kind", "page_size", "pages", "dpi"}
        assert f["kind"] == "pdf" and f["dpi"] == 300 and not f["path"].startswith("/")
    for p in m["previews"]:
        assert set(p) == {"path", "page"}
    json.dumps(m)  # plain JSON types only


def test_one_pdf_per_page_size_with_right_sizes_and_counts(pack_dir, settings):
    m = read_manifest(pack_dir)
    assert [f["page_size"] for f in m["files"]] == ["us_letter", "a4"]
    activity = 9
    keyed = sum(1 for c in m["pack"]["contents"] if c["type"] != "tracing")   # 7 pages with answer keys
    keys = -(-keyed // 4)
    for f in m["files"]:
        info = pdf_info(pack_dir / f["path"])
        assert info["pages"] == f["pages"] == 1 + activity + keys
        w, h = page_size_points(f["page_size"], settings)
        assert all(abs(pw - w) < 0.01 and abs(ph - h) < 0.01 for pw, ph in info["sizes_pt"])
    assert m["pack"]["activity_pages"] == activity and m["pack"]["answer_key_pages"] == keys


def test_pages_mix_types_round_robin(pack_dir):
    contents = read_manifest(pack_dir)["pack"]["contents"]
    assert [c["page"] for c in contents] == list(range(2, 11))
    assert [c["type"] for c in contents] == ["maze", "word_search", "tracing", "dot_to_dot"] * 2 + ["maze"]


def test_previews_are_rgb_pngs_of_the_first_pages(pack_dir):
    m = read_manifest(pack_dir)
    assert [p["page"] for p in m["previews"]] == [1, 2, 3, 4, 5, 6]
    for p in m["previews"]:
        with Image.open(pack_dir / p["path"]) as im:
            assert im.format == "PNG" and im.mode == "RGB"
            assert abs(im.width - 8.5 * 150) <= 2                 # 150 dpi of US Letter


def test_layouts_differ_per_page_size_but_content_is_shared(pack_dir):
    import pypdfium2 as pdfium

    m = read_manifest(pack_dir)

    def texts(path):
        doc = pdfium.PdfDocument(str(path))
        try:
            return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
        finally:
            doc.close()

    letter, a4 = (texts(pack_dir / f["path"]) for f in m["files"])
    ws = 2  # PDF index of the first word search page (title=0, maze=1)
    def chars(text):
        return sorted("".join(text.split()))

    assert chars(letter[ws]) == chars(a4[ws])                     # same puzzle letters and words


def test_rerun_is_byte_identical(tmp_path, brief, both_sizes):
    a = generate(brief, out_root=tmp_path / "a", date="2026-10-15", settings=both_sizes)[0]
    b = generate(brief, out_root=tmp_path / "b", date="2026-10-15", settings=both_sizes)[0]
    assert a.name == b.name
    ma, mb = read_manifest(a), read_manifest(b)
    assert ma == mb
    for f in ma["files"] + ma["previews"]:
        assert (a / f["path"]).read_bytes() == (b / f["path"]).read_bytes()


def test_dry_run_and_client_do_not_change_output(tmp_path, brief, both_sizes):
    a = generate(brief, out_root=tmp_path / "a", date="2026-10-15", settings=both_sizes)[0]
    b = generate(brief, out_root=tmp_path / "b", date="2026-10-15", settings=both_sizes, dry_run=True,
                 client=object())[0]
    assert read_manifest(b)["generator"] == {"name": "activity_sheets", "dry_run": True}
    for f in read_manifest(a)["files"]:
        assert (a / f["path"]).read_bytes() == (b / f["path"]).read_bytes()


def test_different_date_or_brief_gives_different_item(tmp_path, brief, both_sizes, blocklist, cfg):
    a = generate(brief, out_root=tmp_path, date="2026-10-15", settings=both_sizes)[0]
    b = generate(brief, out_root=tmp_path, date="2026-10-16", settings=both_sizes)[0]
    c = generate(dict(brief, brief_id="activity_sheets-2026-10-15-09"), out_root=tmp_path, date="2026-10-15",
                 settings=both_sizes)[0]
    assert len({a.name, b.name, c.name}) == 3
    pa = plan_pack(brief, a.name, cfg, blocklist, both_sizes)
    pb = plan_pack(brief, b.name, cfg, blocklist, both_sizes)
    assert pa.pages[0].content != pb.pages[0].content            # different seed, different maze


def test_single_size_when_deliver_both_sizes_is_off(tmp_path, brief, settings):
    s = copy.deepcopy(settings)
    s["print"]["deliver_both_sizes"] = False
    d = generate(brief, out_root=tmp_path, date="2026-10-15", settings=s)[0]
    assert [f["page_size"] for f in read_manifest(d)["files"]] == ["us_letter"]
    assert page_sizes_for({"page_sizes": ["a4"]}, s) == ["a4"]
    assert page_sizes_for({"page_sizes": ["tabloid"]}, s) == ["us_letter"]


def test_all_strokes_meet_min_line_weight(tmp_path, brief, both_sizes, monkeypatch):
    widths = []
    original = rl_canvas.Canvas.setLineWidth
    monkeypatch.setattr(rl_canvas.Canvas, "setLineWidth",
                        lambda self, w: (widths.append(w), original(self, w))[1])
    s = copy.deepcopy(both_sizes)
    s["activity_sheets"] = {"tracing": {"style": "dotted"}, "dot_to_dot": {"faint_preview": True}}
    generate(dict(brief, page_count=8), out_root=tmp_path, date="2026-10-15", settings=s)
    assert widths and min(widths) >= s["print"]["min_line_weight_pt"]


def test_blocked_brief_is_refused(tmp_path, brief, both_sizes):
    with pytest.raises(ValueError, match="blocked"):
        generate(dict(brief, theme="paw patrol"), out_root=tmp_path, date="2026-10-15", settings=both_sizes)
    assert not any(tmp_path.iterdir())


def test_flagged_brief_needs_review(tmp_path, brief, both_sizes):
    d = generate(dict(brief, keywords=["peter rabbit garden"]), out_root=tmp_path, date="2026-10-15",
                 settings=both_sizes)[0]
    assert read_manifest(d)["needs_review"] is True


def test_wrong_niche_or_missing_theme_is_an_error(tmp_path, brief, both_sizes):
    with pytest.raises(ValueError):
        generate(dict(brief, niche="stickers"), out_root=tmp_path, settings=both_sizes)
    with pytest.raises(ValueError):
        generate(dict(brief, theme=" "), out_root=tmp_path, settings=both_sizes)
    with pytest.raises(ValueError):
        generate(dict(brief, activity_types=["crossword"]), out_root=tmp_path, settings=both_sizes)


def test_rerun_replaces_stale_files_and_skips_drafted_items(tmp_path, brief, both_sizes):
    d = generate(brief, out_root=tmp_path, date="2026-10-15", settings=both_sizes)[0]
    (d / "files" / "old.pdf").write_bytes(b"stale")
    generate(brief, out_root=tmp_path, date="2026-10-15", settings=both_sizes)
    assert not (d / "files" / "old.pdf").exists()
    (d / "publish.json").write_text("{}")
    (d / "files" / "keep.pdf").write_bytes(b"drafted")
    assert generate(brief, out_root=tmp_path, date="2026-10-15", settings=both_sizes) == [d]
    assert (d / "files" / "keep.pdf").exists()


def test_subset_of_types_and_default_page_count(tmp_path, brief, both_sizes):
    b = dict(brief, activity_types=["tracing"], page_count=None)
    d = generate(b, out_root=tmp_path, date="2026-10-15", settings=both_sizes)[0]
    m = read_manifest(d)
    assert {c["type"] for c in m["pack"]["contents"]} == {"tracing"}
    assert m["pack"]["activity_pages"] == both_sizes["niches"]["activity_sheets"]["pages_per_pack"][0]
    assert m["pack"]["answer_key_pages"] == 0                     # tracing has no answer key
    assert m["files"][0]["pages"] == 1 + m["pack"]["activity_pages"]


def test_difficulty_from_settings_brief_or_audience(cfg):
    assert resolve_difficulty({"audience": "kids 4-8"}, dict(cfg, difficulty="auto")) == "easy"
    assert resolve_difficulty({"audience": "kids 7-10"}, dict(cfg, difficulty="auto")) == "medium"
    assert resolve_difficulty({"audience": "toddlers"}, dict(cfg, difficulty="auto")) == "easy"
    assert resolve_difficulty({"audience": "kids 7-10"}, dict(cfg, difficulty="easy")) == "easy"
    assert resolve_difficulty({"difficulty": "hard"}, cfg) == "hard"


def test_medium_level_sizes(tmp_path, brief, both_sizes, blocklist, cfg):
    p = plan_pack(dict(brief, audience="kids 8-10"), "x", cfg, blocklist, both_sizes)
    maze = p.pages[0].content
    assert (maze.cols, maze.rows) == (12, 15)
    assert len(p.pages[3].content.dots) == 35


def test_rng_streams_are_independent_and_stable():
    assert rng_for("item", "maze", 0).random() == rng_for("item", "maze", 0).random()
    assert rng_for("item", "maze", 0).random() != rng_for("item", "maze", 1).random()


def test_dispatch_through_generators_package(tmp_path, brief, both_sizes):
    d = generators.generate(brief, out_root=tmp_path, date="2026-10-15", settings=both_sizes)[0]
    assert (d / "manifest.json").exists()
