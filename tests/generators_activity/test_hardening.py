"""Review fixes: real stroke widths in the PDF, long and non-Latin themes, input validation,
safe reruns and determinism across processes. All offline, outputs in tmp_path."""
import copy
import ctypes
import hashlib
import json
import os
import subprocess
import sys

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest

from core.config import ROOT
from core.items import read_manifest
from core.render import page_size_points
from generators.activity_sheets import generate, pack as pack_mod
from generators.activity_sheets.layout import fit_lines, printable, wrap_text
from generators.activity_sheets.tracing import plan_lessons

STROKE_TEXT_MODES = (pdfium_c.FPDF_TEXTRENDERMODE_STROKE, pdfium_c.FPDF_TEXTRENDERMODE_FILL_STROKE,
                     pdfium_c.FPDF_TEXTRENDERMODE_STROKE_CLIP, pdfium_c.FPDF_TEXTRENDERMODE_FILL_STROKE_CLIP)


def stroke_widths(path) -> list[float]:
    """Line width of every stroked path and stroked text object, as a PDF reader sees it.

    Catches strokes drawn with the PDF default width (1.0 pt after each showPage), which a
    spy on Canvas.setLineWidth cannot see."""
    doc = pdfium.PdfDocument(str(path))
    widths = []
    try:
        for i in range(len(doc)):
            for obj in doc[i].get_objects():
                stroked = False
                if obj.type == pdfium_c.FPDF_PAGEOBJ_PATH:
                    fill, stroke = ctypes.c_int(), ctypes.c_int()
                    pdfium_c.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke))
                    stroked = bool(stroke.value)
                elif obj.type == pdfium_c.FPDF_PAGEOBJ_TEXT:
                    stroked = pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw) in STROKE_TEXT_MODES
                if stroked:
                    w = ctypes.c_float()
                    assert pdfium_c.FPDFPageObj_GetStrokeWidth(obj.raw, ctypes.byref(w))
                    widths.append(w.value)
    finally:
        doc.close()
    return widths


def char_boxes(path, page_index: int):
    doc = pdfium.PdfDocument(str(path))
    try:
        tp = doc[page_index].get_textpage()
        text = tp.get_text_range()
        boxes = [tp.get_charbox(i) for i in range(tp.count_chars())]
    finally:
        doc.close()
    return text, boxes


def test_every_stroke_in_the_pdf_meets_min_line_weight(tmp_path, brief, both_sizes):
    s = copy.deepcopy(both_sizes)
    s["activity_sheets"] = {"tracing": {"style": "dotted"}, "dot_to_dot": {"faint_preview": True}}
    d = generate(dict(brief, page_count=8), out_root=tmp_path, date="2026-10-15", settings=s)[0]
    min_pt = s["print"]["min_line_weight_pt"]
    for f in read_manifest(d)["files"]:
        widths = stroke_widths(d / f["path"])
        assert len(widths) > 100                       # mazes, grids, guides, dotted letters ...
        assert min(widths) >= min_pt - 1e-6, f["path"]


def test_long_theme_wraps_and_stays_on_the_page(tmp_path, brief, both_sizes):
    theme = "outer space adventure for little astronauts and future rocket scientists"
    d = generate(dict(brief, theme=theme, keywords=["space maze"], page_count=2), out_root=tmp_path,
                 date="2026-10-15", settings=both_sizes)[0]
    for f in read_manifest(d)["files"]:
        w, h = page_size_points(f["page_size"], both_sizes)
        margin = both_sizes["print"]["margin_in"] * 72
        for page in (0, 1):                            # title page and the first page header
            text, boxes = char_boxes(d / f["path"], page)
            assert "Scientists" in text
            for left, bottom, right, top in boxes:
                if right > left:                       # skip zero-size boxes (spaces, line breaks)
                    assert margin - 1 <= left and right <= w - margin + 1, (f["page_size"], page)


def test_fit_lines_prefers_one_line_and_wraps_when_needed():
    size, lines = fit_lines("Farm Animals Word Search", "Helvetica-Bold", 28, 540, one_line_min=18)
    assert lines == ["Farm Animals Word Search"] and size == 28
    long = "Outer Space Adventure For Little Astronauts And Future Rocket Scientists Word Search"
    size, lines = fit_lines(long, "Helvetica-Bold", 28, 540, max_lines=2, one_line_min=18)
    assert len(lines) == 2 and size >= 18 and " ".join(lines) == long
    assert wrap_text("one two", "Helvetica", 12, 1) == ["one", "two"]


def test_printable_folds_or_drops_characters_helvetica_cannot_draw():
    assert printable("Día de los Muertos café") == "Día de los Muertos café"   # WinAnsi keeps accents
    assert printable("Łódź Ő") == "Lódz O"
    assert printable("🦕 dino   party") == "dino party"
    assert printable("恐竜") == ""


def test_non_winansi_theme_prints_without_missing_glyph_boxes(tmp_path, brief, both_sizes):
    d = generate(dict(brief, theme="🦕 Łódź dinosaurs", keywords=["dinosaur maze"], page_count=2),
                 out_root=tmp_path, date="2026-10-15", settings=both_sizes)[0]
    m = read_manifest(d)
    text, _ = char_boxes(d / m["files"][0]["path"], 0)
    assert "Lódz Dinosaurs" in text and "■" not in text
    with pytest.raises(ValueError, match="printable"):
        generate(dict(brief, theme="恐竜"), out_root=tmp_path, date="2026-10-15", settings=both_sizes)


def test_lone_strings_are_not_split_into_letters(tmp_path, brief, both_sizes):
    d = generate(dict(brief, activity_types="maze", keywords="robot maze", page_count=2), out_root=tmp_path,
                 date="2026-10-15", settings=both_sizes)[0]
    m = read_manifest(d)
    assert m["pack"]["activity_types"] == ["maze"] and m["keywords"] == ["robot maze"]


@pytest.mark.parametrize("bad", ["../escape", "2026/10/15", "2026-13-45", "today"])
def test_date_must_be_iso_so_it_cannot_become_a_path(tmp_path, brief, both_sizes, bad):
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        generate(brief, out_root=out, date=bad, settings=both_sizes)
    assert not out.exists() and sorted(p.name for p in tmp_path.iterdir()) == []


def test_unknown_shop_is_refused(tmp_path, brief, both_sizes):
    with pytest.raises(ValueError, match="unknown shop"):
        generate(brief, shop_id="nope", out_root=tmp_path, date="2026-10-15", settings=both_sizes)


def test_brief_with_url_or_marked_blocked_is_refused(tmp_path, brief, both_sizes):
    with pytest.raises(ValueError, match="URL"):
        generate(dict(brief, keywords=["farm maze www.example.com/listing/1"]), out_root=tmp_path,
                 date="2026-10-15", settings=both_sizes)
    with pytest.raises(ValueError, match="marked"):
        generate(dict(brief, compliance={"blocked": ["something"], "flagged": []}), out_root=tmp_path,
                 date="2026-10-15", settings=both_sizes)
    assert not any(tmp_path.iterdir())


def test_failed_rerun_keeps_the_previous_item_intact(tmp_path, brief, both_sizes, monkeypatch):
    d = generate(brief, out_root=tmp_path, date="2026-10-15", settings=both_sizes)[0]
    before = {p.relative_to(d): p.read_bytes() for p in d.rglob("*") if p.is_file()}

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("generators.activity_sheets.render_pdf", boom)
    with pytest.raises(RuntimeError):
        generate(brief, out_root=tmp_path, date="2026-10-15", settings=both_sizes)
    after = {p.relative_to(d): p.read_bytes() for p in d.rglob("*") if p.is_file()}
    assert after == before                                         # manifest still matches its files
    assert [p.name for p in tmp_path.iterdir()] == [d.name]        # no scratch folder left behind


def test_tracing_instruction_fits_both_trace_styles():
    for lesson in plan_lessons(4, ["cow"]):
        assert "gray" not in lesson.instruction and lesson.instruction.startswith("Start at the dot")


def test_keyword_filler_and_tool_names_are_not_puzzle_words(blocklist):
    from generators.activity_sheets import vocab

    pool = vocab.theme_words("farm animals", ["farm animals svg cricut bundle", "pre-k farm worksheets"], blocklist)
    assert not {"svg", "cricut", "bundle", "pre"} & set(pool)


def test_identical_files_across_processes_and_hash_seeds(tmp_path, brief):
    """Same brief and date in two fresh interpreters (different str hash seeds) -> same bytes."""
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(dict(brief, page_count=4)))
    digests = []
    for seed in ("1", "4242"):
        out = tmp_path / f"out{seed}"
        env = dict(os.environ, PYTHONHASHSEED=seed)
        subprocess.run([sys.executable, "-m", "generators.activity_sheets", "--brief", str(path), "--out",
                        str(out), "--date", "2026-10-15", "--dry-run"], cwd=ROOT, env=env, check=True,
                       capture_output=True)
        [d] = list(out.iterdir())
        digests.append({p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(d.rglob("*")) if p.is_file()})
    assert digests[0] == digests[1] and any(k.endswith(".pdf") for k in digests[0])


def test_word_pool_is_big_enough_for_the_level(blocklist, brief, both_sizes, cfg):
    p = pack_mod.plan_pack(dict(brief, theme="winter", keywords=[], difficulty="hard",
                                activity_types=["word_search"], page_count=1), "x", cfg, blocklist, both_sizes)
    assert len(p.pages[0].content.placements) == cfg["levels"]["hard"]["word_search"]["words"]
