"""Prompts come only from our brief fields, never carry blocked terms, and subjects never repeat."""
import pytest

from generators.imaging import subjects
from generators.imaging.prompts import (
    ORIGINALITY, BlockedPromptError, clean_style_notes, coloring_prompt, guard, plan_images, sticker_prompt,
)


def test_coloring_prompt_has_required_instructions(coloring_brief):
    p = coloring_prompt(coloring_brief, "a friendly cow")
    for phrase in ("black and white line art coloring page for kids", "thick clean outlines", "no shading",
                   "no gray", "no text", "white background", "single subject: a friendly cow",
                   "Theme: farm animals", ORIGINALITY):
        assert phrase in p


def test_sticker_prompt_has_required_instructions(sticker_brief):
    p = sticker_prompt(sticker_brief, "an apple")
    for phrase in ("die-cut sticker illustration", "bold outline", "flat colors", "centered",
                   "plain solid white background", "no text", "Subject: an apple", ORIGINALITY):
        assert phrase in p


def test_adult_audience_is_not_called_kids(coloring_brief):
    coloring_brief["audience"] = "adults who love relaxing patterns"
    assert "coloring page for adults who love relaxing patterns" in coloring_prompt(coloring_brief, "a rose")


def test_style_notes_that_fight_the_pipeline_are_dropped():
    notes = "bold outlines, transparent background, white die-cut border, flat colors"
    assert clean_style_notes(notes) == "bold outlines, flat colors"


def test_prompts_only_use_brief_fields(sticker_brief, blocklist):
    """Unknown brief keys (e.g. anything a future research version adds) never reach a prompt."""
    sticker_brief["competitor_title"] = "SECRET COMPETITOR TITLE"
    planned, _ = plan_images(sticker_brief, "sticker", 4, blocklist)
    assert all("SECRET" not in p.prompt for p in planned)


@pytest.mark.parametrize("kind", ["coloring", "sticker"])
@pytest.mark.parametrize("theme", ["farm animals", "ocean", "dinosaurs", "space", "christmas", "halloween",
                                   "construction trucks", "unicorns", "bugs", "abstract shapes"])
def test_planned_prompts_are_clean_and_distinct(kind, theme, coloring_brief, blocklist):
    brief = {**coloring_brief, "theme": theme, "keywords": []}
    planned, result = plan_images(brief, kind, 30, blocklist, spares=3)
    assert len(planned) == 33
    assert len({p.subject for p in planned}) == 33           # never repeat a subject in a pack
    assert result.blocked == []
    for p in planned:
        assert blocklist.check(p.prompt).blocked == []


def test_subject_table_is_trademark_clean(blocklist):
    texts = [s for v in subjects.TABLE.values() for s in v] + list(subjects.TEMPLATES) + list(subjects.POSES)
    result = blocklist.check(*texts)
    assert result.blocked == [] and result.flagged == []


def test_every_alias_points_at_a_table_entry():
    assert set(subjects.ALIASES.values()) <= set(subjects.TABLE)


def test_subjects_follow_theme_and_are_deterministic():
    pool = subjects.subject_pool("farm animals", ["barn animals"])
    assert pool[:3] == ["a friendly cow", "a happy pig in a mud puddle", "a chicken with her chicks"]
    assert pool == subjects.subject_pool("farm animals", ["barn animals"])
    assert subjects.matched_keys("cute puppies") == ["pets"]
    assert subjects.matched_keys("my theme", ["dinosaur coloring"]) == ["dinosaurs"]   # keywords as fallback


def test_unknown_theme_still_fills_a_big_sticker_pack():
    pool = subjects.subject_pool("abstract shapes")
    assert len(pool) >= 50 and len(set(pool)) == len(pool)
    assert "an abstract shape parade" in pool


def test_too_many_pages_for_theme_raises(coloring_brief, blocklist):
    with pytest.raises(ValueError, match="distinct subjects"):
        plan_images({**coloring_brief, "theme": "abstract shapes"}, "coloring", 500, blocklist)


@pytest.mark.parametrize("field,value", [
    ("theme", "Paw Patrol"),
    ("theme", "pawpatrol pups"),
    ("audience", "fans of Bluey"),
    ("style_notes", "in the style of Disney"),
])
def test_blocked_terms_raise_before_any_call(field, value, coloring_brief, blocklist):
    brief = {**coloring_brief, field: value}
    with pytest.raises(BlockedPromptError, match="blocked terms"):
        plan_images(brief, "coloring", 2, blocklist)


def test_flagged_terms_are_reported(coloring_brief, blocklist):
    brief = {**coloring_brief, "style_notes": "storybook feel like Peter Rabbit"}
    _, result = plan_images(brief, "coloring", 2, blocklist)
    assert result.flagged == ["Peter Rabbit"] and result.blocked == []


def test_guard_refuses_urls_and_blocked_terms(blocklist):
    with pytest.raises(BlockedPromptError, match="URLs"):
        guard("a cow, like www.example.com/listing/123", blocklist)
    with pytest.raises(BlockedPromptError, match="blocked"):
        guard("a cute Pikachu sticker", blocklist)
    assert guard("a cute cow sticker", blocklist).ok


@pytest.mark.parametrize("audience", ["adults ages 18+", "teens and adults", "teens ages 13-17"])
def test_grown_up_audiences_are_not_called_kids(audience, coloring_brief):
    coloring_brief["audience"] = audience
    p = coloring_prompt(coloring_brief, "a rose")
    assert f"coloring page for {audience}" in p and "for kids" not in p


def test_style_adjectives_do_not_pick_the_subjects():
    """'kawaii' and 'sweet' describe a style, not a subject: no food in an animal pack."""
    pool = subjects.subject_pool("kawaii animals")
    assert subjects.matched_keys("kawaii animals") == ["animals"]
    assert not set(pool[:16]) & set(subjects.TABLE["food"])
    assert subjects.matched_keys("sweet puppies") == ["pets"]
    assert subjects.matched_keys("kawaii food") == ["food"]
    assert subjects.matched_keys("christmas sweets") == ["christmas", "sweets"]


def test_generic_animals_kept_beside_a_non_animal_key():
    assert subjects.matched_keys("farm animals") == ["farm"]
    assert subjects.matched_keys("spring animals") == ["flowers", "animals"]
    assert "a lion" in subjects.subject_pool("spring animals")[:4]


def test_string_keywords_are_one_keyword_not_letters(coloring_brief, blocklist):
    brief = {**coloring_brief, "theme": "my theme", "keywords": "dinosaur coloring"}
    planned, _ = plan_images(brief, "coloring", 2, blocklist)
    assert planned[0].subject == "a friendly t-rex"


def test_non_string_brief_fields_do_not_crash(coloring_brief, blocklist):
    brief = {**coloring_brief, "style_notes": ["bold outlines", "no shading"], "audience": None}
    planned, _ = plan_images(brief, "coloring", 1, blocklist)
    assert "Style: bold outlines, no shading." in planned[0].prompt and "Audience:" not in planned[0].prompt
