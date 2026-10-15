"""Etsy rule checks and deterministic repair (listing/rules.py)."""
from __future__ import annotations

import copy

import pytest

import listing
from core.compliance import Blocklist
from listing import rules, template
from listing.facts import gather

from .conftest import GOOD, good

CFG = copy.deepcopy(listing.DEFAULTS)


def draft(**changes) -> dict:
    return rules.normalize_draft(good(**changes))


# ------------------------------------------------------------------ title

def test_good_draft_has_no_errors():
    assert rules.validate(draft(), 5, CFG) == []


@pytest.mark.parametrize("title, expect", [
    ("x" * 141, "141 characters; the limit is 140"),
    ("Farm $5 Pages", "characters Etsy does not allow in titles: $"),
    ("Farm ^ Pages", "characters Etsy does not allow"),
    ("Farm Pages: Cows: Pigs", "uses ':' 2 times"),
    ("Cows & Pigs & Hens", "uses '&' 2 times"),
    ("100% Fun 50% Off", "uses '%' 2 times"),
    ("Farm Pages™", "trademark or copyright symbols"),
    ("- Farm Pages", "must start with a letter or a number"),
    ("", "title is empty"),
])
def test_title_rules(title, expect):
    errs = rules.title_errors(title, CFG)
    assert any(expect in e for e in errs), errs


def test_title_allows_etsy_punctuation_and_math_symbols():
    # | and + are math symbols (Sm), commas and dashes punctuation: all allowed by Etsy's regex
    assert rules.title_errors("Farm Coloring Pages | Kids 4-8, Cows + Pigs: 20 Pages", CFG) == []
    assert rules.title_errors("Día de campo: páginas para colorear", CFG) == []  # letters in any script


def test_clean_title_fixes_characters_once_only_symbols_and_length():
    t = rules.clean_title("** Cows & Pigs & Hens: Farm: Fun $$ ™ " + "word " * 40, CFG)
    assert rules.title_errors(t, CFG) == []
    assert t.startswith("Cows & Pigs and Hens: Farm, Fun")
    assert len(t) <= 140 and not t.endswith(" ")


# ------------------------------------------------------------------ tags

@pytest.mark.parametrize("tag, expect", [
    ("a" * 21, "21 characters; the limit is 20"),
    ("cows & pigs", "not allowed in tags: &"),
    ("cows: pigs", "not allowed in tags: :"),
    ("Farm Animals", "must be lowercase"),
    ("farm animals®", "not allowed in tags: ®"),
])
def test_tag_rules(tag, expect):
    assert any(expect in e for e in rules.tag_errors(tag, CFG))


def test_tags_allow_hyphen_and_apostrophe():
    assert rules.tag_errors("dot-to-dot", CFG) == []
    assert rules.tag_errors("kid's activity", CFG) == []


def test_normalize_lowercases_and_straightens_apostrophes():
    d = draft(tags=[" Farm  Animals ", "Kid’s Coloring", *GOOD["tags"][2:]])
    assert d["tags"][:2] == ["farm animals", "kid's coloring"]


def test_validate_counts_duplicates_and_title_repeat():
    tags = GOOD["tags"][:12] + ["Coloring-Pages"]  # same as "coloring pages" to a shopper
    errs = rules.validate(draft(tags=tags), 5, CFG)
    assert any("is repeated" in e for e in errs)
    errs = rules.validate(draft(tags=GOOD["tags"] + ["farm fun", "cow fun"]), 5, CFG)
    assert any("15 tags given; exactly 13" in e for e in errs)
    errs = rules.validate(draft(title="Barn Animals", tags=GOOD["tags"]), 5, CFG)
    assert any("repeats the whole title" in e for e in errs)


def test_clean_tag_shortens_word_by_word():
    assert rules.clean_tag("farm animal coloring pages", CFG) == "farm animal coloring"
    assert rules.clean_tag("coloring pages for kids and adults", CFG) == "coloring for kids"  # no dangling "and"
    assert rules.clean_tag("fruit digital stickers", CFG) == "fruit stickers"  # filler words go first
    assert rules.clean_tag("a happy pig in a mud puddle", CFG, shorten=False) is None
    assert rules.clean_tag("cows & pigs!", CFG) == "cows pigs"
    assert rules.clean_tag("supercalifragilisticexpialidocious", CFG) is None


def test_pick_tags_dedupes_and_skips_title():
    tags = rules.pick_tags(["Barn Animals", "barn-animals", "farm fun", "x" * 30, "farm fun"], "Farm Fun", CFG)
    assert tags == ["barn animals"]


# ------------------------------------------------------------------ alt text, sections, URLs, claims

def test_alt_text_count_and_length():
    errs = rules.validate(draft(alt_texts=["one", "two"]), 5, CFG)
    assert any("2 alt texts given; exactly 5" in e for e in errs)
    errs = rules.validate(draft(alt_texts=["a" * 501] + GOOD["alt_texts"][1:]), 5, CFG)
    assert any("alt text 1 is 501 characters; the limit is 500" in e for e in errs)
    assert rules.validate(draft(alt_texts=[]), 0, CFG) == []


def test_urls_and_misleading_claims_are_errors():
    errs = rules.validate(draft(hook="Visit www.example.com for more."), 5, CFG)
    assert any("hook contains a URL" in e for e in errs)
    errs = rules.validate(draft(about="Every page is hand-drawn with love."), 5, CFG)
    assert any("'hand drawn'" in e for e in errs)
    errs = rules.validate(draft(suggested_uses=["Commercial use allowed", "b" * 10, "c" * 10]), 5, CFG)
    assert any("commercial use" in e for e in errs)


def test_section_limits():
    errs = rules.validate(draft(hook="", suggested_uses=["one"]), 5, CFG)
    assert any("'hook' is empty" in e for e in errs)
    assert any("suggested_uses has 1 items; give 3 to 6" in e for e in errs)


def test_shape_errors():
    with pytest.raises(rules.DraftShapeError, match="'tags' must be a list of strings"):
        rules.normalize_draft(good(tags="a, b"))
    with pytest.raises(rules.DraftShapeError):
        rules.normalize_draft(["not", "an", "object"])


# ------------------------------------------------------------------ repair

def _facts(settings, item):
    from core.items import read_json, read_manifest

    report = read_json(item / "qa_report.json")
    return gather(read_manifest(item), report["mockups"], CFG, settings)


def test_repair_keeps_good_parts_and_fixes_the_rest(settings, item):
    facts = _facts(settings, item)
    fallback = rules.normalize_draft(template.draft(facts, CFG))
    bad = draft(
        title="Farm Animal Coloring Pages: Kids: Fun " + "really " * 30,
        tags=GOOD["tags"][:10] + ["printable farm animal coloring pages", "cows & pigs", "barn animals",
                                  "farm fun", "x" * 25],
        hook="Visit www.example.com today.",
        about="Hand drawn pages.",
        alt_texts=["photo"] * 2 + ["a" * 600],
    )
    fixed = rules.repair(bad, fallback, template.tag_candidates(facts, CFG), 5, CFG)
    assert rules.validate(fixed, 5, CFG) == []
    assert fixed["tags"][:10] == GOOD["tags"][:10]          # valid model tags kept in order
    assert "farm animal" not in fixed["tags"]               # shortened copy of a kept tag is a duplicate
    assert "printable farm animal coloring pages" not in fixed["tags"]
    assert "cows pigs" in fixed["tags"]
    assert len(fixed["tags"]) == 13
    assert fixed["title"].startswith("Farm Animal Coloring Pages: Kids, Fun")
    assert "www" not in fixed["hook"] and fixed["hook"]
    assert fixed["about"] == fallback["about"]               # misleading claim replaced by template copy
    assert fixed["alt_texts"][:2] == ["photo", "photo"]
    assert len(fixed["alt_texts"][2]) <= 500
    assert fixed["alt_texts"][3:] == fallback["alt_texts"][3:]  # missing ones filled in


def test_repair_pads_tags_from_manifest_keywords(settings, item):
    facts = _facts(settings, item)
    fallback = rules.normalize_draft(template.draft(facts, CFG))
    fixed = rules.repair(draft(tags=["farm fun"]), fallback, template.tag_candidates(facts, CFG), 5, CFG)
    assert fixed["tags"][:3] == ["farm fun", "farm animal coloring", "barn animals"]
    assert rules.validate(fixed, 5, CFG) == []


def test_generic_template_phrases_pass_rules_and_blocklist():
    bl = Blocklist.load()
    lists = [*template.GENERIC_TAGS.values(), template.GENERIC_TAGS_OTHER, *template.ACTIVITY_TAGS.values()]
    for tags in lists:
        for t in tags:
            assert rules.tag_errors(t, CFG) == [], t
        assert not bl.check(*tags).blocked and not bl.check(*tags).flagged
    for tags in [*template.GENERIC_TAGS.values(), template.GENERIC_TAGS_OTHER]:
        assert len({rules.tag_key(t) for t in tags}) >= CFG["tags_count"]
    uses = [u for v in template.SUGGESTED_USES.values() for u in v] + template.SUGGESTED_USES_OTHER
    layouts = [x for v in CFG["mockup_layouts"].values() for x in (v if isinstance(v, list) else [v])]
    assert not bl.check(*uses, *layouts, CFG["terms"]).blocked
    assert not rules.misleading(" ".join(uses + layouts), CFG)
