"""Pack checks, stationery and ttrpg print rules, game-system names and pack mockups in QA
(docs/PACKS.md sections 4 and 5)."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

import qa
from core.compliance import Blocklist
from core.items import read_json, read_manifest, write_manifest
from qa import mockups as mk
from qa.packs import check_pack, check_print_rules, hamming, min_font_size, min_line_gap_mm, phash

from .conftest import A4, LETTER, base_manifest, by_name, coloring_item, make_item, sticker_item

ROOT_ID = "2026-10-16-coloring_pages-farm-animals-"


@pytest.fixture
def psettings(settings) -> dict:
    """Settings that know the new niches (config/settings.yaml gains them in this build)."""
    s = copy.deepcopy(settings)
    for niche in ("stationery", "ttrpg"):
        s["niches"].setdefault(niche, {"generator": "procedural", "output": ["pdf"]})
    return s


def with_pack(d: Path, **pack) -> dict:
    m = read_manifest(d)
    m["pack"] = {**(m.get("pack") or {}), **pack}
    write_manifest(d, m)
    return m


def ids(n: int, prefix: str = "s1") -> list[str]:
    return [f"{prefix}-p{i:02d}" for i in range(1, n + 1)]


def checks_for(d: Path, settings: dict) -> list[dict]:
    from qa.checks import Item, check_paths
    from core.config import section

    item = Item(d, read_manifest(d), settings, section(settings, "qa", qa.DEFAULTS))
    check_paths(item)
    return check_pack(item) + check_print_rules(item)


# ------------------------------------------------------------------ pack page count and ids

def test_items_without_pack_size_get_no_pack_checks(tmp_path, settings):
    d = coloring_item(tmp_path)
    assert checks_for(d, settings) == []


def test_pack_page_count_and_ids_pass(tmp_path, settings):
    d = coloring_item(tmp_path, pages=2)
    with_pack(d, size=2, pages=2, source="master", page_ids=ids(2))
    checks = checks_for(d, settings)
    for name in ("pack_page_count", "pack_page_ids", "pack_fresh_pages"):
        assert by_name(checks, name)[0]["passed"], checks
    assert by_name(checks, "pack_page_count")[0]["detail"].startswith("pack.size 2")
    # the fixture prints the same drawing on every page: the duplicate check sees it
    assert not by_name(checks, "pack_duplicates")[0]["passed"]


def test_pack_page_count_mismatch_fails(tmp_path, settings):
    d = coloring_item(tmp_path, pages=2)
    with_pack(d, size=3, pages=2, source="master", page_ids=ids(2))
    checks = checks_for(d, settings)
    count = by_name(checks, "pack_page_count")[0]
    assert not count["passed"] and "holds 2 designs" in count["detail"] and "has 2 pages" in count["detail"]
    assert not by_name(checks, "pack_page_ids")[0]["passed"]          # 2 ids for size 3


def test_declared_title_and_answer_pages_count(tmp_path, settings):
    d = coloring_item(tmp_path, pages=2)
    m = with_pack(d, size=1, activity_pages=1, title_pages=1, answer_key_pages=0, source="fresh", page_ids=ids(1))
    m["niche"] = "activity_sheets"
    write_manifest(d, m)
    assert by_name(checks_for(d, settings), "pack_page_count")[0]["passed"]
    m["pack"]["title_pages"] = 0                                     # an undeclared extra page
    write_manifest(d, m)
    assert not by_name(checks_for(d, settings), "pack_page_count")[0]["passed"]


def test_repeated_page_ids_fail(tmp_path, settings):
    d = coloring_item(tmp_path, pages=2)
    with_pack(d, size=2, pages=2, source="master", page_ids=["s1-p01", "s1-p01"])
    pid = by_name(checks_for(d, settings), "pack_page_ids")[0]
    assert not pid["passed"] and "repeated: s1-p01" in pid["detail"]
    with_pack(d, page_ids="s1-p01")
    assert not by_name(checks_for(d, settings), "pack_page_ids")[0]["passed"]


def test_sticker_pack_counts_stickers_not_sheet_pages(tmp_path, settings):
    d = sticker_item(tmp_path, n=4)
    m = read_manifest(d)
    m["files"][1]["count"] = 4
    write_manifest(d, m)
    with_pack(d, size=4, stickers=4, source="master", page_ids=ids(4))
    checks = checks_for(d, settings)
    assert by_name(checks, "pack_page_count")[0]["passed"]          # 1 sheet page, 4 stickers
    m = read_manifest(d)
    m["files"][1]["count"] = 3
    write_manifest(d, m)
    count = by_name(checks_for(d, settings), "pack_page_count")[0]
    assert not count["passed"] and "ZIP files hold 3" in count["detail"]


# ------------------------------------------------------------------ near-duplicate pages

def _art(path: Path, shape: str, shift: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (400, 520), "white")
    d = ImageDraw.Draw(img)
    if shape == "circles":
        for cx, cy in ((120, 150), (280, 150), (200, 360)):
            d.ellipse((cx - 70 + shift, cy - 70, cx + 70 + shift, cy + 70), outline="black", width=8)
    elif shape == "house":
        d.rectangle((90, 240, 310, 460), outline="black", width=8)
        d.polygon([(70, 250), (200, 90), (330, 250)], outline="black", width=8)
        d.rectangle((170, 340, 230, 460), outline="black", width=8)
    else:  # stripes
        for y in range(40, 520, 70):
            d.line((20, y, 380, y + 60), fill="black", width=10)
    img.save(path)
    return path


def _with_assets(d: Path, shapes: list[tuple[str, int]]) -> None:
    m = read_manifest(d)
    m["assets"] = []
    for i, (shape, shift) in enumerate(shapes, 1):
        rel = f"assets/page-{i:02d}.png"
        _art(d / rel, shape, shift)
        m["assets"].append({"path": rel, "kind": "png", "page": i})
    write_manifest(d, m)


def test_phash_tells_pages_apart(tmp_path):
    h = {k: phash(Image.open(_art(tmp_path / f"{k}.png", k))) for k in ("circles", "house", "stripes")}
    nudged = phash(Image.open(_art(tmp_path / "nudged.png", "circles", shift=2)))
    moved = phash(Image.open(_art(tmp_path / "moved.png", "circles", shift=8)))
    cfg = qa.DEFAULTS["packs"]
    assert hamming(h["circles"], nudged) <= cfg["duplicate_distance"]               # a duplicate
    assert cfg["duplicate_distance"] < hamming(h["circles"], moved) <= cfg["near_distance"]  # review
    assert min(hamming(h["circles"], h["house"]), hamming(h["house"], h["stripes"]),
               hamming(h["circles"], h["stripes"])) > cfg["near_distance"]
    clear = Image.new("RGBA", (100, 100), (0, 0, 0, 0))          # a sticker is laid on white
    assert phash(clear) == phash(Image.new("RGB", (100, 100), "white"))


def test_duplicate_pages_fail_and_similar_pages_go_to_review(tmp_path, settings):
    d = coloring_item(tmp_path, pages=3)
    with_pack(d, size=3, pages=3, source="master", page_ids=ids(3))
    _with_assets(d, [("circles", 0), ("house", 0), ("stripes", 0)])
    checks = checks_for(d, settings)
    assert by_name(checks, "pack_duplicates")[0]["passed"], checks
    assert by_name(checks, "pack_near_duplicates")[0]["passed"]

    _with_assets(d, [("circles", 0), ("house", 0), ("circles", 0)])
    dup = by_name(checks_for(d, settings), "pack_duplicates")[0]
    assert not dup["passed"] and dup["severity"] == "error" and "page 1 and page 3" in dup["detail"]

    s = copy.deepcopy(settings)
    s["qa"]["packs"] = {"duplicate_distance": -1, "near_distance": 64}   # everything "looks alike"
    near = by_name(checks_for(d, s), "pack_near_duplicates")[0]
    assert not near["passed"] and near["severity"] == "warning"


def test_sticker_duplicates_use_the_cut_outs(tmp_path, settings):
    d = sticker_item(tmp_path, n=3)                     # the fixture draws the same sticker 3 times
    m = read_manifest(d)
    m["files"][1]["count"] = 3
    write_manifest(d, m)
    with_pack(d, size=3, stickers=3, source="master", page_ids=ids(3))
    dup = by_name(checks_for(d, settings), "pack_duplicates")[0]
    assert not dup["passed"] and "sticker" in dup["detail"]


# ------------------------------------------------------------------ fresh pages stay in one pack

def test_fresh_pack_pages_are_exclusive(tmp_path, settings):
    root = tmp_path / "out"
    master = coloring_item(root, ROOT_ID + "0001", pages=2)
    with_pack(master, size=2, pages=2, source="master", page_ids=ids(2, "m"))
    derived = coloring_item(root, ROOT_ID + "0002", pages=1)
    with_pack(derived, size=1, pages=1, source="derived", page_ids=ids(1, "m"))
    fresh = coloring_item(root, ROOT_ID + "0003", pages=1)
    with_pack(fresh, size=1, pages=1, source="fresh", page_ids=ids(1, "f"))
    for d in (master, derived, fresh):            # a derived pack may reuse its master's pages
        assert by_name(checks_for(d, settings), "pack_fresh_pages")[0]["passed"]

    with_pack(fresh, page_ids=["m-p01"])          # a "fresh" page that is really the master's
    clash = by_name(checks_for(fresh, settings), "pack_fresh_pages")[0]
    assert not clash["passed"] and ROOT_ID + "0001" in clash["detail"] and ROOT_ID + "0002" in clash["detail"]
    with_pack(fresh, page_ids=["f-p01"])
    with_pack(derived, page_ids=["f-p01"])        # another pack reusing a fresh page
    assert not by_name(checks_for(derived, settings), "pack_fresh_pages")[0]["passed"]


def test_other_output_roots_do_not_count(tmp_path, settings):
    a = coloring_item(tmp_path / "one", ROOT_ID + "0001", pages=1)
    with_pack(a, size=1, pages=1, source="fresh", page_ids=["x-p01"])
    b = coloring_item(tmp_path / "two", ROOT_ID + "0002", pages=1)
    with_pack(b, size=1, pages=1, source="master", page_ids=["x-p01"])
    assert by_name(checks_for(a, settings), "pack_fresh_pages")[0]["passed"]


# ------------------------------------------------------------------ stationery / ttrpg print rules

def planner_pdf(path: Path, size=LETTER, *, font_pt: float = 9, line_gap_mm: float = 7, scale: float = 1.0,
                pages: int = 2, extra=None) -> Path:
    """Our own simple planner page: a title, a box, and ruled writing lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=size)
    w, h = size
    for _ in range(pages):
        c.saveState()
        c.scale(scale, scale)
        c.setFont("Helvetica", font_pt)
        c.drawString(40 / scale, (h - 60) / scale, "Daily Plan - write the top three tasks")
        c.restoreState()
        c.setFont("Helvetica-Bold", 16)
        c.drawString(40, h - 40, "Today")
        c.setLineWidth(1.2)
        c.rect(40, h - 300, w - 80, 200)                       # a box: its borders are not writing lines
        c.setFillColorRGB(0.85, 0.92, 0.85)
        c.rect(40, h - 340, w - 80, 24, stroke=0, fill=1)      # a filled header bar
        c.setStrokeGray(0.58)
        c.setLineWidth(0.6)
        y = h - 380
        for _ in range(10):
            c.line(60, y, w - 60, y)
            y -= line_gap_mm * mm
        if extra:
            extra(c, size)
        c.showPage()
    c.save()
    return path


def planner_item(root: Path, settings: dict, niche: str = "stationery", **pdf_kw) -> Path:
    item_id = f"2026-10-16-{niche}-adhd-friendly-0001"
    d = root / item_id
    files = []
    for name, size in (("us_letter", LETTER), ("a4", A4)):
        rel = f"files/adhd-friendly-planner-2-pages-{name}.pdf"
        planner_pdf(d / rel, size, **pdf_kw)
        files.append({"path": rel, "kind": "pdf", "page_size": name, "pages": 2, "dpi": 300})
    m = base_manifest(item_id, niche, theme="adhd friendly", keywords=["adhd planner"], ai_assisted=False,
                      generator={"name": niche, "dry_run": True}, prompts=[], files=files,
                      pack={"pages": 2, "title_pages": 0, "size": 2, "source": "master", "page_ids": ids(2),
                            "page_types": ["daily_planner", "brain_dump"]})
    write_manifest(d, m)
    return d


def test_print_rules_pass_for_clean_pages(tmp_path, psettings):
    d = planner_item(tmp_path, psettings)
    checks = checks_for(d, psettings)
    font = by_name(checks, "print_font_size")[0]
    gap = by_name(checks, "print_line_spacing")[0]
    assert font["passed"] and "9.0 pt" in font["detail"]
    measured = float(re.search(r"about ([\d.]+) mm", gap["detail"]).group(1))
    assert gap["passed"] and abs(measured - 7.0) < 0.35, gap


@pytest.mark.parametrize("kw, check", [({"font_pt": 6}, "print_font_size"),
                                       ({"font_pt": 10, "scale": 0.5}, "print_font_size"),   # 5 pt drawn
                                       ({"line_gap_mm": 5}, "print_line_spacing")])
def test_print_rules_fail(tmp_path, psettings, kw, check):
    d = planner_item(tmp_path, psettings, **kw)
    c = by_name(checks_for(d, psettings), check)[0]
    assert not c["passed"] and c["severity"] == "error", c


def test_print_rules_only_for_stationery_and_ttrpg(tmp_path, psettings):
    d = planner_item(tmp_path, psettings, niche="ttrpg", font_pt=6)
    assert not by_name(checks_for(d, psettings), "print_font_size")[0]["passed"]
    m = read_manifest(d)
    m["niche"] = "activity_sheets"
    write_manifest(d, m)
    assert not [c for c in checks_for(d, psettings) if c["name"].startswith("print_")]


def test_declared_measurements_are_checked(tmp_path, psettings):
    d = planner_item(tmp_path, psettings)
    with_pack(d, min_font_pt=6.5, min_line_gap_mm=5.0)
    checks = checks_for(d, psettings)
    assert "generator measured 6.5 pt" in by_name(checks, "print_font_size")[0]["detail"]
    assert "generator measured lines 5 mm" in by_name(checks, "print_line_spacing")[0]["detail"]


def test_font_and_line_measures(tmp_path):
    pdf = planner_pdf(tmp_path / "p.pdf", font_pt=8, line_gap_mm=6.5, pages=1)
    size, page = min_font_size(pdf)
    assert abs(size - 8) < 0.01 and page == 1
    gap, _ = min_line_gap_mm(pdf)
    assert abs(gap - 6.5) < 0.35
    # two lines only (a box) and dotted rows are not a block of writing lines
    c = canvas.Canvas(str(tmp_path / "box.pdf"), pagesize=LETTER)
    c.rect(40, 400, 500, 10)
    for x in range(40, 540, 12):
        for y in (200, 210, 220):
            c.circle(x, y, 0.8, fill=1)
    c.showPage()
    c.save()
    assert min_line_gap_mm(tmp_path / "box.pdf") is None


@pytest.mark.parametrize("gray, found", [(0.0, False), (0.58, True)])
def test_only_light_lines_are_writing_lines(tmp_path, gray, found):
    """A table's last row, its box border and the next box's border can be evenly spaced and
    close; they are dark structure, not a block of writing lines."""
    c = canvas.Canvas(str(tmp_path / "rules.pdf"), pagesize=LETTER)
    c.setStrokeGray(gray)
    c.setLineWidth(1.2 if gray == 0 else 0.6)
    for i in range(3):
        c.line(60, 400 - i * 3 * mm, 550, 400 - i * 3 * mm)
    c.showPage()
    c.save()
    gap = min_line_gap_mm(tmp_path / "rules.pdf")
    assert (gap is not None) == found
    if found:
        assert abs(gap[0] - 3.0) < 0.35


# ------------------------------------------------------------------ game-system names

@pytest.fixture
def fake_detect(monkeypatch):
    """A small game-system registry in place of config/ttrpg_systems.yaml."""
    import core.systems

    catalog = {"generic": {"id": "generic", "names": [], "owner": None, "owner_names": []},
               "dnd5e": {"id": "dnd5e", "names": ["D&D 5e", "DnD 5e", "D&D", "DnD", "5e"],
                         "owner": "Wizards of the Coast", "owner_names": []}}
    monkeypatch.setattr(core.systems, "load_systems", lambda *a, **k: copy.deepcopy(catalog))
    return catalog


def test_system_names_never_printed(tmp_path, psettings, fake_detect):
    def stamp(c, size):
        c.setFont("Helvetica", 9)
        c.drawString(40, 40, "Character sheet for D&D 5e")
    d = planner_item(tmp_path, psettings, niche="ttrpg", extra=stamp)
    m = read_manifest(d)
    m["system"] = "dnd5e"                     # the listing may name it; the files never do
    write_manifest(d, m)
    report = qa.run(d, settings=psettings, review_dir=tmp_path / "review", mockups=False)
    ft = by_name(report["checks"], "compliance_file_text")[0]
    assert not ft["passed"] and "D&D" in ft["detail"]


def test_system_spellings_in_file_names_fail(tmp_path, psettings, fake_detect):
    d = planner_item(tmp_path, psettings, niche="ttrpg")
    m = read_manifest(d)
    m["system"] = "dnd5e"
    old = d / m["files"][0]["path"]
    new = "files/dark-fantasy-dnd5e-sheets-us_letter.pdf"
    old.rename(d / new)
    m["files"][0]["path"] = new
    write_manifest(d, m)
    report = qa.run(d, settings=psettings, review_dir=tmp_path / "review", mockups=False)
    comp = by_name(report["checks"], "compliance_blocklist")[0]
    assert not comp["passed"] and "game system dnd5e" in comp["detail"]


def test_letter_rows_are_not_a_system_name(tmp_path, psettings, fake_detect):
    def letters(c, size):
        c.setFont("Helvetica", 12)
        c.drawString(40, 60, "d d d d")                 # a letter-tracing row
        c.drawString(40, 80, "Date: dd/mm")
    d = planner_item(tmp_path, psettings, extra=letters)
    report = qa.run(d, settings=psettings, review_dir=tmp_path / "review", mockups=False)
    assert by_name(report["checks"], "compliance_file_text")[0]["passed"]


def test_system_id_in_manifest_is_not_a_mention(tmp_path, psettings, fake_detect):
    d = planner_item(tmp_path, psettings, niche="ttrpg")
    m = read_manifest(d)
    m["system"] = "dnd5e"
    write_manifest(d, m)
    report = qa.run(d, settings=psettings, review_dir=tmp_path / "review", mockups=False)
    assert by_name(report["checks"], "compliance_blocklist")[0]["passed"]


@pytest.mark.parametrize("value", ["D&D 5e", "DnD5e", "pathfinder"])
def test_a_system_name_in_the_system_field_is_checked_as_text(tmp_path, psettings, fake_detect, value):
    """Only a registry id is skipped; a name written into manifest["system"] is caught."""
    d = planner_item(tmp_path, psettings, niche="ttrpg")
    m = read_manifest(d)
    m["system"] = value
    write_manifest(d, m)
    report = qa.run(d, settings=psettings, review_dir=tmp_path / "review", mockups=False)
    assert not by_name(report["checks"], "compliance_blocklist")[0]["passed"]


def test_system_names_never_on_mockups(tmp_path, psettings, fake_detect):
    item = make_item(tmp_path / "x", psettings, theme="dnd dungeon crawl", niche="ttrpg")
    shots, checks = mk.build(item, Blocklist.load())
    assert shots == [] and not by_name(checks, "mockup_text")[0]["passed"]


# ------------------------------------------------------------------ mockups for packs

def test_pack_badge_text():
    assert mk.pack_badge({"niche": "coloring_pages", "pack": {"size": 20, "pages": 20}}, 20) == "20 PAGES"
    assert mk.pack_badge({"niche": "stickers", "pack": {"size": 5}}, 5) == "SET OF 5"
    assert mk.pack_badge({"niche": "coloring_pages"}, 20) == ""                 # not a pack item
    assert mk.pack_badge({"niche": "stationery", "pack": {"size": 1}}, 1) == "1 PAGE"


def test_size_badge_sits_on_the_card_never_on_the_pages(psettings):
    import numpy as np
    from PIL import Image

    fake = type("I", (), {"manifest": {"niche": "stationery", "theme": "adhd friendly", "item_id": "x",
                                       "pack": {"size": 20, "pages": 20}}, "settings": psettings})()
    W, H = 1350, 1012
    pal = mk.PALETTES["stationery"]
    pages = [Image.new("RGB", (850, 1100), (20, 40, 230)) for _ in range(3)]       # solid blue pages
    ctx = mk.Ctx((W, H), "stationery", pages, [], mk._texts(fake, [], 0), pal, (), 1)
    assert ctx.text["badge"] == "20 PAGES"
    img = np.asarray(mk.hero(ctx).convert("RGB")).astype(int)
    accent = np.all(np.abs(img - np.array(pal["accent"])) <= 12, axis=2)
    blue = np.all(np.abs(img - np.array([20, 40, 230])) <= 12, axis=2)
    xs = np.flatnonzero(blue.any(axis=0))
    assert xs.size and accent[:, : xs.max() + 1].sum() < 0.002 * H * W              # no badge over the pages
    cx, cy = mk.badge_center(ctx, (int(0.585 * W), int(0.25 * H), int(0.955 * W), int(0.75 * H)))
    r = mk.BADGE_RADIUS * ctx.u
    assert cx - r > xs.max() and cx + r <= W and cy - r >= 0                       # clear of the pages, on canvas
    assert accent[int(cy - r): int(cy + r), int(cx - r): int(cx + r)].sum() > 0.3 * r * r


def test_stationery_pack_gets_badge_and_desk_photo(tmp_path, psettings):
    d = planner_item(tmp_path, psettings)
    m = read_manifest(d)
    from .conftest import write_previews

    m["previews"] = write_previews(d, d / m["files"][0]["path"])
    write_manifest(d, m)
    report = qa.run(d, settings=psettings, review_dir=tmp_path / "review")
    assert report["passed"], [c for c in report["checks"] if not c["passed"]]
    assert len(report["mockups"]) == 6                                        # five layouts + the desk
    item_text = mk._texts(type("I", (), {"manifest": read_manifest(d), "settings": psettings})(), [], 0)
    assert item_text["badge"] == "2 PAGES" and item_text["title"] == "ADHD Friendly"
    assert "listing" not in read_json(d / "qa_report.json")                   # QA writes no copy


def test_listing_alt_texts_cover_the_desk_photo():
    import listing

    layouts = listing.DEFAULTS["mockup_layouts"]
    for niche in ("stationery", "ttrpg"):
        assert len(layouts[niche]) == 6 and "desk" in layouts[niche][-1]
        assert layouts[niche][4] == layouts["default"][-1]                     # the sizes card, same place


def test_hero_page_preview_leads_the_photos(tmp_path, settings):
    """manifest pack.hero_page (stationery: the page its style ranks highest) comes first,
    so the cover photo fans it in front; the other previews keep their order."""
    from core.config import section
    from qa.checks import Item, check_paths

    def pages(manifest):
        item = Item(d, manifest, settings, section(settings, "qa", qa.DEFAULTS))
        check_paths(item)
        return mk._load_pages(item, item.cfg["mockups"], (900, 600))

    d = coloring_item(tmp_path, pages=3)
    plain = pages(read_manifest(d))
    lead = pages(with_pack(d, hero_page=3))
    assert len(lead) == len(plain) == 3
    assert [p.tobytes() for p in lead] == [plain[2].tobytes(), plain[0].tobytes(), plain[1].tobytes()]
    same = pages(with_pack(d, hero_page=9))             # no preview of it: page order
    assert [p.tobytes() for p in same] == [p.tobytes() for p in plain]
