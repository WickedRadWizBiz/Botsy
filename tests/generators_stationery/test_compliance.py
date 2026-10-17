"""Printed text compliance (CLAUDE.md, docs/PACKS.md section 4): no brands, no game-system
("!") names and no medical claims on any page; render refuses a page that would print them."""
import copy
import re

import pytest

import generators.stationery as stationery
from core.compliance import Blocklist, iter_strings
from generators.stationery.accents import ACCENT_SUBJECTS, accent_prompt
from generators.stationery.catalog import CATALOG, TITLE_OVERRIDES
from generators.stationery.render import MEDICAL_RE, measure, text_problems

from .conftest import ALL_STYLES, every_layout, make_ctx

# Brand-like or claim-like words our own copy never prints.
NEVER_PRINTED = ("pomodoro", "bullet journal", "bujo", "eisenhower", "getting things done", "gtd", "happy planner",
                 "erin condren", "hobonichi", "passion planner", "panda planner", "clever fox", "goodnotes", "adhd",
                 "deep work", "the one thing", "cornell")


# docs/PACKS.md section 4: game-system names ("!" terms) are never printed.
GAME_SYSTEM_NAMES = ("dungeons & dragons", "d&d", "dnd", "5e", "fifth edition", "pathfinder", "starfinder",
                     "call of cthulhu", "wizards of the coast", "paizo", "chaosium")


@pytest.fixture(scope="module")
def printed(base_settings) -> list[str]:
    """Every string printed by every layout in every style (labels hardly change with the
    page size, so one size per style plus both sizes for one style)."""
    texts = []
    cfg = stationery.config(base_settings)
    for i, (style, season) in enumerate(ALL_STYLES):
        sizes = ("us_letter", "a4") if i == 0 else (("us_letter", "a4")[i % 2],)
        for size in sizes:
            texts += measure(every_layout(style, season), size, base_settings, cfg).texts
    return list(dict.fromkeys(texts))


def test_every_printed_string_is_blocklist_clean(printed, base_settings, blocklist):
    assert len(printed) > 200
    found = blocklist.check(*printed)
    assert found.blocked == [] and found.flagged == []
    assert text_problems(printed, base_settings) == []


def test_no_medical_brand_or_game_system_words_on_any_page(printed):
    lower = " | ".join(printed).lower()
    for word in NEVER_PRINTED:
        assert word not in lower, word
    for term in GAME_SYSTEM_NAMES:
        assert not re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", lower), term
    assert not [t for t in printed if MEDICAL_RE.search(t)]


def test_catalog_copy_is_clean(blocklist):
    strings = list(iter_strings([[v.__dict__ for v in vs] for vs in CATALOG.values()]))
    strings += list(iter_strings([list(d.values()) for d in TITLE_OVERRIDES.values()]))
    found = blocklist.check(*strings)
    assert found.blocked == [] and found.flagged == []
    assert not [s for s in strings if MEDICAL_RE.search(s)]
    joined = " ".join(strings).lower()
    for word in NEVER_PRINTED:
        assert word not in joined, word


def test_accent_prompts_are_our_own_clean_words(blocklist):
    prompts = [accent_prompt(s) for subjects in ACCENT_SUBJECTS.values() for s in subjects]
    found = blocklist.check(*prompts)
    assert found.blocked == [] and found.flagged == []
    assert all("no text" in p and "no logos" in p for p in prompts)


@pytest.mark.parametrize("word", ["Disney Planner", "Therapy Notes", "Symptom Tracker", "Helps treat focus"])
def test_render_refuses_blocked_or_medical_text(tmp_path, settings, word):
    pages = every_layout("minimalist")[:1]
    pages[0]["spec"]["title"] = word
    with pytest.raises(ValueError, match="blocked terms|medical wording"):
        stationery.render_pages(pages, tmp_path / "x.pdf", page_size="a4", settings=settings, ctx=make_ctx())
    assert not (tmp_path / "x.pdf").exists()


def test_game_system_names_are_never_printed(tmp_path, settings):
    """A "!" (game-system) term is blocked in printed text even though ttrpg listings may
    name it (core.compliance treats "!" terms as blocked unless a system is allowed)."""
    path = tmp_path / "blocklist.txt"
    path.write_text("Disney\n!Dungeons & Dragons\n!5e\n?Happy Planner\n", encoding="utf-8")
    bl = Blocklist.load(path)
    if not hasattr(bl, "system_terms"):
        pytest.skip("core.compliance does not support '!' game-system terms yet")
    s = copy.deepcopy(settings)
    s.setdefault("compliance", {})["trademark_blocklist"] = str(path)
    pages = every_layout("minimalist")[:1]
    pages[0]["spec"]["subtitle"] = "Works for Dungeons & Dragons"
    with pytest.raises(ValueError, match="blocked terms"):
        stationery.render_pages(pages, tmp_path / "x.pdf", page_size="a4", settings=s, ctx=make_ctx())
    assert text_problems(["Fifth level 5e session"], s)


def test_printed_text_hook_returns_what_is_drawn(settings):
    pages = stationery.plan_pages(make_ctx("adhd"), 5, seed="s", settings=settings)
    texts = stationery.printed_text(pages, make_ctx("adhd"), settings)
    for p in pages:
        assert p["spec"]["title"] in texts
    assert stationery.config(settings)["footer_text"] in texts
    # no prompt, page id or theme text is "printed"
    assert not [t for t in texts if "p01" in t]
    # the hook also works with only (pages, ctx), as the pack engine calls it
    assert stationery.printed_text(pages, make_ctx("adhd")) == texts
