"""Review fixes: themes that match no style go to review, month pages keep a row for every
day on both sizes, and the QA stage's own print-rule measurements pass on every layout."""
import json

import pytest

import generators.stationery as stationery
from core.render import pdf_info
from generators.stationery.render import measure
from generators.stationery.styles import style_for

from .conftest import ALL_STYLES, every_layout, make_ctx

SIZES = ("us_letter", "a4")


@pytest.mark.parametrize("theme", ["adhd", "cottagecore", "work from home", "autumn", "winter", "pastel", "mom"])
def test_brief_review_reasons_quiet_when_the_theme_names_a_style(settings, theme):
    brief = {"theme": theme, "keywords": ["planner printable"], "style_notes": "clean printable layouts"}
    assert stationery.brief_review_reasons(brief, settings) == []


def test_brief_review_reasons_flag_a_theme_the_pages_do_not_show(settings):
    [reason] = stationery.brief_review_reasons({"theme": "galaxy", "keywords": [], "style_notes": ""}, settings)
    assert "'galaxy'" in reason and "minimalist" in reason and "the default" in reason
    # a style found only in the keywords still leaves the theme itself unshown
    [reason] = stationery.brief_review_reasons({"theme": "wedding", "keywords": ["adhd planner printable"]},
                                               settings)
    assert "adhd_friendly" in reason and "keywords" in reason
    # odd brief fields never break it
    assert stationery.brief_review_reasons({"theme": None, "keywords": "x", "style_notes": 3}, settings)


@pytest.mark.parametrize("theme, season", [("christmas", "winter"), ("halloween", "autumn"),
                                           ("valentines", "spring")])
def test_brief_review_reasons_flag_holiday_themes(settings, theme, season):
    """A holiday theme gets its season's look but no holiday pages (gift lists, party plans)."""
    [reason] = stationery.brief_review_reasons({"theme": theme, "keywords": []}, settings)
    assert "names a holiday" in reason and season in reason and theme in reason


@pytest.mark.parametrize("theme, style", [
    ("pastel", "cozy"), ("self care", "cozy"), ("mom", "cozy"), ("family", "cozy"),
    ("home school", "teacher"), ("home office", "wfh"), ("homework", "student"),
])
def test_more_theme_words_map_to_a_style(theme, style):
    assert style_for(make_ctx(theme)).style == style


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("style, season", ALL_STYLES)
def test_month_pages_have_a_row_for_every_day(settings, style, season, size):
    """"One line for every day of the month" / "One row per day": 31 rows on both sizes."""
    cfg = stationery.config(settings)
    for page_id in ("monthly_planner-list", "habit_tracker-monthly", "mood_energy-monthly"):
        page = [p for p in every_layout(style, season) if p["page_id"] == page_id]
        texts = measure(page, size, settings, cfg).texts
        days = [t for t in texts if t.isdigit()]
        assert [str(d) for d in range(1, 32)] == days[:31], (page_id, days)


@pytest.mark.parametrize("style, size", [("adhd_friendly", "a4"), ("minimalist", "us_letter"),
                                         ("cozy", "us_letter"), ("botanical", "a4")])
def test_qa_print_rule_measurements_pass(tmp_path, settings, style, size):
    """The QA stage reads the font sizes back from the PDF and estimates writing-line spacing
    from a render (qa/packs.py); every layout passes both."""
    qa_packs = pytest.importorskip("qa.packs")
    pages = every_layout(style, "spring" if style == "seasonal" else None)
    out = tmp_path / f"{style}-{size}.pdf"
    stationery.render_pages(pages, out, page_size=size, settings=settings, ctx=make_ctx())
    font = qa_packs.min_font_size(out)
    assert font is not None and font[0] >= 7.0 - 0.05
    gap = qa_packs.min_line_gap_mm(out, dpi=150, dark=235, min_run_mm=20, max_pages=60, light=90)
    assert gap is not None and gap[0] >= 6.0 - 0.4


def test_unmatched_theme_goes_to_review_through_the_pack_engine(tmp_path, settings):
    pytest.importorskip("generators.packs")
    brief = {
        "brief_id": "stationery-2026-10-16-02", "niche": "stationery", "theme": "galaxy", "audience": "adults",
        "keywords": ["galaxy planner printable"], "page_count": 5, "pack_sizes": [5], "price_usd": 4.5,
        "page_sizes": ["us_letter", "a4"], "style_notes": "",
        "evidence": {"listing_count": 23, "listing_rows": 23, "shop_count": 5, "score": 0.8},
        "compliance": {"blocked": [], "flagged": []}, "needs_review": False,
    }
    [d] = stationery.generate(brief, out_root=tmp_path / "out", date="2026-10-16", dry_run=True, settings=settings)
    m = json.loads((d / "manifest.json").read_text())
    assert m["needs_review"] is True
    assert any("matches no planner style" in r for r in m["review_reasons"])
    assert m["pack"]["style"] == "minimalist"
    for f in m["files"]:
        if f["kind"] == "pdf":
            assert pdf_info(d / f["path"])["pages"] == 5


def test_printed_text_check_picks_up_new_blocklist_terms(tmp_path, settings):
    """A long-running service re-reads the blocklist when its file changes."""
    import copy
    import os

    from generators.stationery.render import text_problems

    path = tmp_path / "blocklist.txt"
    path.write_text("Disney\n", encoding="utf-8")
    s = copy.deepcopy(settings)
    s.setdefault("compliance", {})["trademark_blocklist"] = str(path)
    assert text_problems(["Galaxy Notes"], s) == []
    path.write_text("Disney\nGalaxy\n", encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    assert text_problems(["Galaxy Notes"], s)
