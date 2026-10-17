"""Rendering tabletop RPG pages: every page type and layout on both page sizes, the print
rules (7 pt text, 6 mm lines, inside the margins), and the printed-text rule: no game-system
name, owner or other blocked term is ever printed, whatever system the sheets are for."""
import io
import re

import pytest

import generators.ttrpg as ttrpg
from core import systems
from core.render import page_size_points, pdf_info
from generators.ttrpg import flow
from generators.ttrpg.catalog import PAGE_TYPES, VARIANTS, Build, variant
from generators.ttrpg.layout import MM, Box, Metrics, Pen, Rules
from generators.ttrpg.plan import page_spec, system_fields
from generators.ttrpg.render import measure, render, text_problems
from generators.ttrpg.styles import GENRES

from .conftest import SIZES, every_layout, make_ctx, pdf_texts, pdf_title

N_LAYOUTS = sum(len(v) for v in VARIANTS.values())


def _render(pages, path, size, settings, **kw):
    return render(pages, path, page_size=size, settings=settings, cfg=ttrpg.config(settings), **kw)


@pytest.mark.parametrize("system", ["generic", "dnd5e"])
@pytest.mark.parametrize("size", SIZES)
def test_every_page_type_renders_within_the_print_rules(tmp_path, settings, system, size):
    pages = every_layout(system, settings=settings)
    assert {p["type"] for p in pages} == set(PAGE_TYPES) and len(pages) == N_LAYOUTS
    out = tmp_path / f"{system}-{size}.pdf"
    m = _render(pages, out, size, settings)
    info = pdf_info(out)
    assert info["pages"] == N_LAYOUTS == m.pages
    w, h = page_size_points(size, settings)
    assert all(abs(pw - w) < 0.5 and abs(ph - h) < 0.5 for pw, ph in info["sizes_pt"])
    assert m.out_of_bounds == [] and m.overflow == []
    assert m.min_font_pt >= 7.0
    assert m.min_line_gap_pt / MM >= 6.0
    assert m.min_line_weight_pt >= 0.5


@pytest.mark.parametrize("system", ["pf2e", "coc7e"])
def test_other_systems_render_every_layout(tmp_path, settings, system):
    pages = every_layout(system, settings=settings)
    for size in SIZES:
        m = _render(pages, tmp_path / f"{size}.pdf", size, settings)
        assert m.out_of_bounds == [] and m.overflow == [] and m.min_font_pt >= 7.0


@pytest.mark.parametrize("genre", sorted(GENRES))
def test_every_genre_renders_cleanly(tmp_path, settings, genre):
    pages = [p for p in every_layout("dnd5e", genre=genre, settings=settings)
             if p["type"] in ("character_sheet", "spell_tracker", "worldbuilding_faction", "hex_map", "dice_log")]
    for size in SIZES:
        m = _render(pages, tmp_path / f"{genre}-{size}.pdf", size, settings)
        assert m.out_of_bounds == [] and m.overflow == [], (genre, size)
        assert m.min_font_pt >= 7.0 and m.min_line_gap_pt / MM >= 6.0


def _all_names() -> list[str]:
    names = []
    for e in systems.load_systems().values():
        names += e["names"] + ([e["owner"]] if e["owner"] else []) + e["owner_names"]
    return names


@pytest.mark.parametrize("system", ["generic", "dnd5e", "pf2e", "coc7e"])
def test_printed_text_never_names_a_game_system(tmp_path, settings, blocklist, system):
    """Extracted PDF text (pypdfium2) carries no `!` term, no system name or owner and no
    other blocked term, for sheets made for any system."""
    pages = ttrpg.plan_pages(make_ctx("dark fantasy", system), 40, seed="s", settings=settings)
    pages += every_layout(system, settings=settings)
    out = tmp_path / "pack.pdf"
    ttrpg.render_pages(pages, out, page_size="us_letter", settings=settings, ctx=make_ctx("dark fantasy", system))
    texts = pdf_texts(out)
    assert len(texts) == len(pages) and all(t.strip() for t in texts)
    joined = "\n".join(texts)
    assert blocklist.check(*texts).blocked == []
    assert systems.detect_systems(joined) == []
    flat = re.sub(r"\s+", " ", joined).lower()
    for name in _all_names():
        assert not re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", flat), name
    assert systems.detect_systems(pdf_title(out)) == []


def test_extracted_field_lists_differ_per_system(tmp_path, settings):
    texts = {}
    for system in ("generic", "dnd5e", "pf2e", "coc7e"):
        fields, ff = system_fields(system, settings)
        page = page_spec("character_sheet", "core", genre="classic_fantasy", fields=fields, fields_from=ff, seed="s")
        out = tmp_path / f"{system}.pdf"
        ttrpg.render_pages([page], out, page_size="a4", settings=settings, ctx=make_ctx(system=system))
        texts[system] = re.sub(r"\s+", " ", pdf_texts(out)[0]).lower()   # some styles print headings in capitals
    assert len(set(texts.values())) == 4
    assert "might" in texts["generic"] and "might" not in texts["dnd5e"]
    for field in ("proficiency bonus", "death saves", "hit dice", "sleight of hand (dex)"):
        assert field in texts["dnd5e"] and field not in texts["generic"]
    assert "fortitude" in texts["pf2e"] and "hero points" in texts["pf2e"]
    assert "sanity" in texts["coc7e"] and "credit rating" in texts["coc7e"]
    # the page itself never says which system it is for
    for t in texts.values():
        assert systems.detect_systems(t) == []


def test_spell_and_inventory_pages_follow_the_system(tmp_path, settings):
    out = {}
    for system in ("generic", "dnd5e"):
        fields, ff = system_fields(system, settings)
        pages = [page_spec("spell_tracker", "spellbook", genre="scifi", fields=fields, fields_from=ff, seed="s"),
                 page_spec("inventory", "pack", genre="scifi", fields=fields, fields_from=ff, seed="s")]
        path = tmp_path / f"{system}.pdf"
        ttrpg.render_pages(pages, path, page_size="us_letter", settings=settings, ctx=make_ctx(system=system))
        out[system] = [re.sub(r"\s+", " ", t) for t in pdf_texts(path)]
    assert "Spell Save DC" in out["dnd5e"][0] and "Spell Save DC" not in out["generic"][0]
    assert "Level 9" in out["dnd5e"][0] and "Tier 5" in out["generic"][0]
    assert "Electrum" in out["dnd5e"][1] and "Electrum" not in out["generic"][1]


def test_layouts_adapt_to_the_page_size(settings):
    """A4 is taller and narrower than US Letter: its own row counts, not a scaled copy."""
    page = every_layout("generic", settings=settings)
    lined = next(p for p in page if p["type"] == "npc_tracker")
    cfg = ttrpg.config(settings)
    letter = measure([lined], "us_letter", settings, cfg)
    a4 = measure([lined], "a4", settings, cfg)
    assert letter.min_line_gap_pt == a4.min_line_gap_pt          # same gaps ...
    assert len(a4.texts) == len(letter.texts)
    buf_l, buf_a = io.BytesIO(), io.BytesIO()
    render([lined], buf_l, page_size="us_letter", settings=settings, cfg=cfg)
    render([lined], buf_a, page_size="a4", settings=settings, cfg=cfg)
    assert buf_l.getvalue() != buf_a.getvalue()


def test_render_is_byte_identical_for_the_same_pages(tmp_path, settings):
    pages = ttrpg.plan_pages(make_ctx("western", "dnd5e"), 20, seed="s", settings=settings)
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    for p in (a, b):
        ttrpg.render_pages(pages, p, page_size="a4", settings=settings, ctx=make_ctx("western"))
    assert a.read_bytes() == b.read_bytes()


def _with_title(page, title):
    page = dict(page, spec=dict(page["spec"], title=title))
    return page


@pytest.mark.parametrize("title, why", [
    ("Character Sheet for D&D 5e", "blocked terms"),
    ("Pathfinder Hero", "blocked terms"),
    ("PF2e Hero Sheet", "names game systems ['pf2e']"),
    ("Frozen Kingdom Record", "blocked terms ['Frozen']"),
    ("Healing Tracker", "medical wording"),
])
def test_render_refuses_printed_system_names_and_blocked_words(tmp_path, settings, title, why):
    page = _with_title(every_layout("generic", settings=settings)[0], title)
    out = tmp_path / "x.pdf"
    with pytest.raises(ValueError, match=re.escape(why)):
        ttrpg.render_pages([page], out, page_size="us_letter", settings=settings, ctx=make_ctx())
    assert not out.exists()


def test_check_printed_text_reports_every_problem(settings):
    assert ttrpg.check_printed_text(["Hex Map", "Armor Class"], settings) == []
    problems = ttrpg.check_printed_text(["for dnd5e", "Wizards of the Coast", "therapy notes"], settings)
    assert any("names game systems ['dnd5e']" in p for p in problems)
    assert any("Wizards of the Coast" in p for p in problems)
    assert any("medical wording" in p for p in problems)
    assert text_problems([], settings) == []


def test_pdf_title_falls_back_when_the_theme_is_not_printable(tmp_path, settings):
    page = every_layout("generic", settings=settings)[0]
    good = tmp_path / "good.pdf"
    ttrpg.render_pages([page], good, page_size="a4", settings=settings, ctx=make_ctx("dark fantasy"))
    assert pdf_title(good) == "Dark Fantasy RPG Pages"
    bad = tmp_path / "bad.pdf"
    ttrpg.render_pages([page], bad, page_size="a4", settings=settings, ctx=make_ctx("dnd 5e"))
    assert pdf_title(bad) == "Tabletop RPG Pages"


def test_settings_cannot_lower_the_print_floors(tmp_path, settings):
    settings["ttrpg"] = {"min_font_pt": 4, "min_line_gap_mm": 3, "line_gap_mm": 3, "row_gap_mm": 2,
                         "light_line_pt": 0.1}
    pages = every_layout("dnd5e", settings=settings)[:6]
    m = _render(pages, tmp_path / "x.pdf", "us_letter", settings)
    assert m.min_font_pt >= 7.0 and m.min_line_gap_pt / MM >= 6.0 and m.min_line_weight_pt >= 0.5
    rules = Rules.from_cfg(ttrpg.config(settings))
    assert rules.min_font_pt == 7.0 and rules.min_gap_pt == pytest.approx(6 * MM)


def test_wider_line_gaps_from_settings_are_used(settings):
    settings["ttrpg"] = {"line_gap_mm": 9.0, "row_gap_mm": 8.0}
    page = next(p for p in every_layout("generic", settings=settings) if p["page_id"] == "session_notes-lined")
    m = measure([page], "us_letter", settings, ttrpg.config(settings))
    assert m.min_line_gap_pt / MM == pytest.approx(9.0, abs=0.01)


def test_pen_refuses_lines_closer_than_the_floor():
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(io.BytesIO())
    pen = Pen(c, Box(36, 36, 540, 720), Rules(), Metrics(), page_label="t")
    with pytest.raises(ValueError, match="mm apart"):
        pen.writing_lines(Box(40, 40, 100, 100), gap=3 * MM, first_at=3 * MM)
    pen.text(500, 700, "far too wide a label for this slot", size=9, max_w=20)
    assert pen.m.overflow and pen.m.min_font_pt == 7.0
    pen.line(10, 10, 20, 20)
    assert pen.m.out_of_bounds


def test_sections_that_cannot_fit_raise_layout_error(tmp_path, settings):
    page = every_layout("generic", settings=settings)[0]
    huge = {"kind": "checklist", "title": "Too Many", "fields": [f"Item {i}" for i in range(80)]}
    page = dict(page, spec=dict(page["spec"], sections=[*page["spec"]["sections"], huge], layout={"cols": 1}))
    with pytest.raises(flow.LayoutError, match="sections need"):
        _render([page], tmp_path / "x.pdf", "us_letter", settings)


def test_column_packing_falls_back_to_the_best_assignment():
    hs = [80.0, 120.0, 80.0, 40.0, 70.0, 340.0, 100.0]
    contiguous, _ = flow._contiguous(hs, 2)
    best, cols = flow._best_assignment(hs, 2)
    assert best <= contiguous and sorted(i for c in cols for i in c) == list(range(len(hs)))
    assert all(c == sorted(c) for c in cols)                 # each column keeps reading order


def test_previews_are_rgb_pngs(tmp_path, settings):
    pages = ttrpg.plan_pages(make_ctx("cozy"), 5, seed="s", settings=settings)
    paths = ttrpg.preview(pages, tmp_path / "prev", settings=settings, ctx=make_ctx("cozy"), limit=3)
    assert [p.name for p in paths] == ["page-01.png", "page-02.png", "page-03.png"]
    from PIL import Image

    with Image.open(paths[0]) as im:
        assert im.mode == "RGB" and im.width > 1000
    assert ttrpg.preview(pages, tmp_path / "none", settings=settings, ctx=make_ctx(), limit=0) == []


class _Recorder(Pen):
    """A pen that also records the boxes, ellipses and writing areas it draws."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.rects, self.ellipses, self.writing = [], [], []

    def rect(self, box, **kw):
        self.rects.append(box)
        super().rect(box, **kw)

    def ellipse(self, *a, **kw):
        self.ellipses.append(a)
        super().ellipse(*a, **kw)

    def writing_lines(self, box, **kw):
        n = super().writing_lines(box, **kw)
        self.writing.append(n)
        return n


def _recorder() -> "_Recorder":
    from reportlab.pdfgen import canvas

    return _Recorder(canvas.Canvas(io.BytesIO()), Box(36, 36, 540, 720), Rules(), Metrics(), page_label="t")


def _overlap(a: Box, b: Box) -> bool:
    return a.x < b.right and b.x < a.right and a.y < b.top and b.y < a.top


@pytest.mark.parametrize("width", [50.0, 62.0, 90.0])
def test_score_sub_values_are_separate_boxes_not_ovals(width):
    """Sub values (Modifier; Half and Fifth) are small square-cornered boxes of our own look
    that never touch, even in a narrow cell."""
    pen = _recorder()
    ctx = flow.Ctx(pen, GENRES["cosmic_horror"])
    cell = Box(100, 100, width, 56)
    flow._score_box(ctx, cell, "Strength", ["Half", "Fifth"])
    assert pen.ellipses == []
    subs = [r for r in pen.rects if r.h == pytest.approx(11)]
    assert len(subs) == 2 and not _overlap(subs[0], subs[1])
    assert all(cell.x <= r.x and r.right <= cell.right for r in subs)
    assert not pen.m.out_of_bounds and not pen.m.overflow


def test_card_spare_lines_are_shared_by_every_multi_line_field():
    pen = _recorder()
    ctx = flow.Ctx(pen, GENRES["classic_fantasy"])
    sections, _ = variant("quest_log", "cards").build(Build(fields=systems.sheet_fields_for("generic")))
    quest_card = sections[0]["card"]
    flow._draw_card(ctx, quest_card, Box(60, 60, 250, 300))
    objective, complications = pen.writing        # in card order
    assert objective >= 3 and abs(objective - complications) <= 1


def test_printed_text_check_reloads_an_edited_blocklist(tmp_path, settings):
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("Frozen\n!D&D\n", encoding="utf-8")
    settings.setdefault("compliance", {})["trademark_blocklist"] = str(blocklist)
    assert text_problems(["Zorblax Keep"], settings) == []
    blocklist.write_text("Frozen\nZorblax\n!D&D\n", encoding="utf-8")
    assert any("Zorblax" in p for p in text_problems(["Zorblax Keep"], settings))
