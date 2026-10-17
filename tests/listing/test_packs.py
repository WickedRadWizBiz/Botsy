"""Packs, new niches and the game-system exception in the listing stage (docs/PACKS.md
sections 4 and 5): pack titles, overlap statements, stationery and ttrpg copy, the system
disclaimer, and the medical-claims rule."""
from __future__ import annotations

import pytest

import listing
from core.compliance import Blocklist
from core.items import read_json
from listing import ListingComplianceError, ListingError, ListingValidationError, rules, template
from listing.facts import page_type_label

from .conftest import FakeClient, SYSTEMS, good, make_item, pack_item

CFG = listing.DEFAULTS
DND_DISCLAIMER = SYSTEMS["dnd5e"]["disclaimer"]


def _dry(d, settings):
    return listing.write_listing(d, dry_run=True, settings=settings)


def _texts(lst) -> list[str]:
    return [lst["title"], *lst["tags"], lst["description"], *lst["alt_texts"]]


def _check_rules(lst):
    assert rules.title_errors(lst["title"], CFG) == []
    assert len(lst["title"]) <= 140 and len(lst["tags"]) == 13
    assert all(rules.tag_errors(t, CFG) == [] for t in lst["tags"])
    assert len({rules.tag_key(t) for t in lst["tags"]}) == 13
    assert lst["writer"]["repaired"] == [] or lst["writer"]["kind"] == "model"


# ------------------------------------------------------------------ titles

@pytest.mark.parametrize("niche, count", [("coloring_pages", "20 Pages"), ("activity_sheets", "20 Pages"),
                                          ("stationery", "20 Pages"), ("ttrpg", "20 Pages"),
                                          ("stickers", "Set of 20")])
def test_pack_titles_carry_the_pack_size(tmp_path, settings, niche, count):
    lst = _dry(pack_item(tmp_path / "out", niche=niche, size=20), settings)
    assert f", {count}" in lst["title"], lst["title"]
    _check_rules(lst)


def test_small_sticker_set_title_and_legacy_items_keep_their_count(tmp_path, settings):
    st = _dry(pack_item(tmp_path / "s", niche="stickers", size=5), settings)
    assert "Set of 5" in st["title"] and "Transparent PNG" in st["title"]
    legacy = _dry(make_item(tmp_path / "l"), settings)            # no pack.size: the older count stays
    assert "20 Printable Pages" in legacy["title"]


def test_ttrpg_title_names_the_system_only_when_set(tmp_path, settings, fake_systems):
    dnd = _dry(pack_item(tmp_path / "a", niche="ttrpg", system="dnd5e", keywords=["fantasy rpg printable"],
                         theme="fantasy rpg"), settings)
    assert dnd["title"].startswith("Fantasy RPG Printable for D&D 5e, 20 Pages"), dnd["title"]
    generic = _dry(pack_item(tmp_path / "b", niche="ttrpg", system="generic", keywords=["fantasy rpg printable"]),
                   settings)
    assert "D&D" not in generic["title"] and "5e" not in generic["title"]
    assert Blocklist.load().check(*_texts(generic)).ok


def _set_types(d, types):
    """Give a stationery/ttrpg pack item these page types."""
    from collections import Counter

    from core.items import write_json

    m = read_json(d / "manifest.json")
    m["pack"].update(page_types=list(types), type_counts=dict(Counter(types)),
                     contents=[{"page": i, "type": t, "title": f"Page {i}"} for i, t in enumerate(types, 1)])
    write_json(d / "manifest.json", m)
    return d


def test_mixed_pack_title_never_reads_as_one_page_type(tmp_path, settings, fake_systems):
    # 8 of 20 pages are character sheets: "Farm Character Sheet, 20 Pages" would promise 20 of them.
    d = pack_item(tmp_path / "a", niche="ttrpg", system="dnd5e", keywords=["farm character sheet", "rpg npc tracker"])
    lst = _dry(d, settings)
    assert lst["title"].startswith("Farm Animals RPG Sheets for D&D 5e, 20 Pages, Character Sheet, RPG NPC Tracker"), \
        lst["title"]
    _check_rules(lst)


def test_title_and_tags_never_name_a_page_type_the_pack_lacks(tmp_path, settings):
    d = pack_item(tmp_path / "a", niche="stationery", keywords=["farm daily planner", "farm meal planner",
                                                                 "weekly planner printable"])
    lst = _dry(d, settings)
    low = lst["title"].lower()
    assert "meal planner" not in low and "weekly planner" not in low, lst["title"]
    assert not any("meal planner" in t or "weekly planner" in t for t in lst["tags"]), lst["tags"]
    assert "daily planner" in lst["tags"]                      # a type the pack has stays a tag
    _check_rules(lst)


def test_a_type_that_is_most_of_the_pack_may_lead(tmp_path, settings):
    d = _set_types(pack_item(tmp_path / "a", niche="stationery", keywords=["farm habit tracker"]),
                   ["habit_tracker"] * 12 + ["brain_dump"] * 8)
    lst = _dry(d, settings)
    assert lst["title"].startswith("Farm Habit Tracker for"), lst["title"]


def test_generic_lead_does_not_repeat_the_unit():
    assert template.theme_unit("fantasy rpg", "RPG sheets") == "fantasy rpg sheets"
    assert template.theme_unit("cozy planner", "planner pages") == "cozy planner pages"
    assert template.theme_unit("summer", "coloring pages") == "summer coloring pages"


def test_model_title_naming_a_missing_page_type_is_repaired(tmp_path, settings):
    d = pack_item(tmp_path / "a", niche="ttrpg")                # character sheets, session notes, NPC trackers
    draft = good(title="Farm RPG Campaign Tracker, 20 Pages, Printable PDF")
    lst = listing.write_listing(d, client=FakeClient(draft, draft, draft), settings=settings)
    assert "campaign tracker" not in lst["title"].lower(), lst["title"]
    assert any("does not include (campaign tracker)" in p for p in lst["writer"]["repaired"]), lst["writer"]
    from listing.facts import absent_types, named_types

    facts = {"niche": "ttrpg", "page_types": {"character sheets": 8, "session notes pages": 12}}
    assert named_types("Dark Character Sheet Bundle", facts) == [("character sheet", 8)]
    assert absent_types("hex map paper printable", facts) == ["hex map page"]
    assert absent_types("dot grid notes", {"niche": "stationery", "page_types": {"dot grid notes page": 2}}) == []
    assert absent_types("hex map paper", {"niche": "coloring_pages", "page_types": {}}) == []  # other niches: none
    planner = {"niche": "stationery", "page_types": {"daily planners": 4, "to-do lists": 1}}
    assert absent_types("oatmeal planner pages", planner) == []          # whole words: not a meal planner
    assert named_types("To Do List, Daily Planners", planner) == [("daily planner", 4), ("to-do list", 1)]
    assert absent_types("priority matrices", planner) == ["priority matrix"]


def test_cover_page_is_listed_so_the_page_count_adds_up(tmp_path, settings):
    lst = _dry(make_item(tmp_path / "a", niche="activity_sheets"), settings)  # 10 activities + 1 cover + 3 keys
    included = lst["description"].split("What's included:\n", 1)[1].split("\n\n", 1)[0]
    assert "- A cover page" in included and "Answer keys (3 pages)" in included and "14 pages each" in included


# ------------------------------------------------------------------ overlap statements

DERIVED_REL = [{"size": 20, "source": "master", "item_id": "m", "same_set": True},
               {"size": 10, "source": "derived", "item_id": "d10", "same_set": True},
               {"size": 30, "source": "fresh", "item_id": "f", "same_set": False}]


def _packs_block(lst) -> str:
    desc = lst["description"]
    assert "Other packs in this theme:" in desc
    return desc.split("Other packs in this theme:\n", 1)[1].split("\n\n", 1)[0]


def test_derived_pack_says_its_pages_are_in_the_master_pack(tmp_path, settings):
    lst = _dry(pack_item(tmp_path / "out", size=5, source="derived", related=DERIVED_REL), settings)
    block = _packs_block(lst)
    assert "These 5 pages are also included in our 20-page farm animals pack" in block
    assert "Some of them may also appear in our 10-page farm animals pack." in block
    assert "This theme also comes in packs of 10, 20 and 30 pages." in block


def test_derived_pack_without_a_known_master_still_warns(tmp_path, settings):
    lst = _dry(pack_item(tmp_path / "out", size=5, source="derived"), settings)
    assert "These 5 pages are also included in our larger farm animals pack" in _packs_block(lst)


def test_master_pack_names_the_smaller_packs_it_contains(tmp_path, settings):
    rel = [{"size": 5, "source": "derived", "item_id": "a", "same_set": True},
           {"size": 10, "source": "derived", "item_id": "b", "same_set": True},
           {"size": 5, "source": "fresh", "item_id": "c", "same_set": False}]
    lst = _dry(pack_item(tmp_path / "out", size=20, source="master", related=rel), settings)
    block = _packs_block(lst)
    assert "This pack contains every page of our 5-page and 10-page farm animals packs" in block
    assert "packs of 5 and 10 pages" in block


def test_fresh_pack_says_all_new(tmp_path, settings):
    rel = [{"size": 20, "source": "master", "item_id": "m", "same_set": False}]
    lst = _dry(pack_item(tmp_path / "out", size=5, source="fresh", related=rel), settings)
    block = _packs_block(lst)
    assert "All-new pages, not included in our other packs." in block
    assert "also included" not in block and "This theme also comes in packs of 20 pages." in block


def test_sticker_overlap_counts_stickers(tmp_path, settings):
    rel = [{"size": 20, "source": "master", "item_id": "m", "same_set": True}]
    lst = _dry(pack_item(tmp_path / "out", niche="stickers", size=5, source="derived", related=rel), settings)
    assert "These 5 stickers are also included in our 20-sticker farm animals pack" in _packs_block(lst)


def test_items_without_pack_data_have_no_overlap_block(tmp_path, settings):
    assert "Other packs in this theme" not in _dry(make_item(tmp_path / "out"), settings)["description"]


def test_model_copy_gets_the_fixed_overlap_block_too(tmp_path, settings):
    d = pack_item(tmp_path / "out", size=5, source="derived", related=DERIVED_REL)
    lst = listing.write_listing(d, client=FakeClient(good()), settings=settings)
    assert lst["writer"]["kind"] == "model"
    assert "These 5 pages are also included in our 20-page farm animals pack" in lst["description"]


# ------------------------------------------------------------------ stationery and ttrpg copy

def test_stationery_copy_lists_page_types_with_counts(tmp_path, settings):
    d = pack_item(tmp_path / "out", niche="stationery", size=20, theme="adhd friendly",
                  keywords=["adhd planner printable"], audience="adults")
    lst = _dry(d, settings)
    _check_rules(lst)
    assert "20 ADHD friendly planner pages: 8 daily planners, 6 brain dump pages and 6 habit trackers" \
        in lst["description"]
    assert "undated" in lst["description"]
    assert {"daily planner", "habit tracker", "printable planner"} <= set(lst["tags"])
    assert rules.medical(lst["description"], CFG) == [] and Blocklist.load().check(*_texts(lst)).ok


def test_ttrpg_generic_copy(tmp_path, settings):
    lst = _dry(pack_item(tmp_path / "out", niche="ttrpg", size=20, theme="dark fantasy",
                         keywords=["ttrpg character sheet"]), settings)
    _check_rules(lst)
    assert "8 character sheets, 6 NPC trackers and 6 session notes pages" in lst["description"]
    assert "character sheet" in lst["tags"]
    assert "unofficial" not in lst["description"].lower()        # no system named, no disclaimer needed


def test_page_type_labels():
    assert page_type_label("habit_tracker", 1) == "habit tracker"
    assert page_type_label("habit_tracker", 3) == "habit trackers"
    assert page_type_label("npc_tracker", 2) == "NPC trackers"
    assert page_type_label("hex_map_paper", 2) == "hex map paper"           # unknown id: named from the id
    assert page_type_label("world_town", 2) == "world towns"


def test_template_phrases_are_clean():
    for table in (template.GENERIC_TAGS, template.SUGGESTED_USES):
        for niche in ("stationery", "ttrpg"):
            texts = table[niche]
            assert Blocklist.load().check(*texts).ok, niche
            assert not [t for t in texts if rules.medical(t, CFG) or rules.misleading(t, CFG)], niche


# ------------------------------------------------------------------ the game-system exception

def test_ttrpg_listing_names_its_system_with_the_disclaimer(tmp_path, settings, fake_systems):
    d = pack_item(tmp_path / "out", niche="ttrpg", system="dnd5e", theme="dark fantasy",
                  keywords=["ttrpg character sheet"])
    lst = listing.write_listing(d, dry_run=True, settings=settings)
    _check_rules(lst)
    desc = lst["description"]
    assert "for D&D 5e" in lst["title"]
    assert DND_DISCLAIMER in desc
    # the disclaimer comes before the AI disclosure, which still ends the description
    assert desc.index(DND_DISCLAIMER) < desc.index("How this was made")
    assert any(t.startswith(("dnd", "5e")) for t in lst["tags"])
    assert "D&D" in lst["compliance"]["allowed"] and lst["compliance"]["blocked"] == []
    bl = Blocklist.load(catalog=SYSTEMS)
    assert not bl.check(*lst["alt_texts"]).blocked                  # alt text never names it
    for t in [lst["title"], *lst["tags"], *desc.splitlines()]:
        assert bl.check(t, allow_systems=("dnd5e",)).ok
    assert read_json(d / "listing.json")["title"] == lst["title"]


def test_model_may_name_the_system_in_copy_but_not_alt_text(tmp_path, settings, fake_systems):
    d = pack_item(tmp_path / "out", niche="ttrpg", system="dnd5e", mockups=5)
    draft = good(title="Dark Fantasy Character Sheet for D&D 5e, 20 Printable Pages",
                 tags=["dnd 5e sheet", *good()["tags"][1:]],
                 about="Original sheets for D&D 5e players, with big boxes and room to write.",
                 alt_texts=["A D&D character sheet on a desk", *[f"Photo {i} of the sheets" for i in range(2, 6)]])
    client = FakeClient(draft, draft, draft)
    lst = listing.write_listing(d, client=client, settings=settings)
    assert lst["title"] == draft["title"] and "dnd 5e sheet" in lst["tags"]
    assert "D&D 5e players" in lst["description"] and DND_DISCLAIMER in lst["description"]
    assert any("alt text 1 names the game system" in p for p in lst["writer"]["repaired"])
    assert "D&D" not in lst["alt_texts"][0] and lst["alt_texts"][1] == "Photo 2 of the sheets"
    # the model was told about the exception, and the retry carried the alt-text problem
    assert "Game system exception" in client.calls[0]["system"] and "D&D 5e" in client.calls[0]["system"]
    assert "alt text 1 names the game system" in client.calls[1]["messages"][0]["content"]


def test_joined_system_spelling_in_alt_text_is_repaired(tmp_path, settings, fake_systems):
    d = pack_item(tmp_path / "out", niche="ttrpg", system="dnd5e")
    draft = good(alt_texts=["DnD5e sheet photo", *[f"Photo {i}" for i in range(2, 6)]])
    lst = listing.write_listing(d, client=FakeClient(draft, draft, draft), settings=settings)
    assert "DnD5e" not in lst["alt_texts"][0]


def test_ttrpg_listing_never_names_another_system(tmp_path, settings, fake_systems):
    d = pack_item(tmp_path / "out", niche="ttrpg", system="dnd5e")
    draft = good(title="Character Sheet for D&D 5e and Pathfinder, 20 Pages")
    with pytest.raises(ListingComplianceError, match="Pathfinder"):
        listing.write_listing(d, client=FakeClient(draft), settings=settings)
    assert not (d / "listing.json").exists()


def test_joined_spelling_of_another_system_is_refused(tmp_path, settings, fake_systems):
    d = pack_item(tmp_path / "out", niche="ttrpg", system="dnd5e")
    draft = good(about="Works with PF2e too, with big boxes and room to write.")
    with pytest.raises(ListingComplianceError, match="pf2e"):
        listing.write_listing(d, client=FakeClient(draft), settings=settings)


@pytest.mark.parametrize("niche, system", [("ttrpg", "generic"), ("ttrpg", None), ("coloring_pages", "dnd5e"),
                                           ("stationery", "dnd5e")])
def test_system_names_blocked_without_an_allowed_system(tmp_path, settings, fake_systems, niche, system):
    extra = {"system": system} if system else {}
    d = pack_item(tmp_path / "out", niche=niche, **extra)
    with pytest.raises(ListingComplianceError, match="D&D"):
        listing.write_listing(d, client=FakeClient(good(title="Sheets for D&D Fans, 20 Pages")), settings=settings)


def test_unknown_system_or_unusable_disclaimer_stops_the_listing(tmp_path, settings, fake_systems):
    with pytest.raises(ListingError, match="cannot be read"):
        _dry(pack_item(tmp_path / "a", niche="ttrpg", system="nope"), settings)
    fake_systems.registry["dnd5e"]["disclaimer"] = "Fan-made sheets."
    with pytest.raises(ListingError, match="not affiliated"):
        _dry(pack_item(tmp_path / "b", niche="ttrpg", system="dnd5e"), settings)
    # the owner must be named in the disclaimer ("not affiliated with <owner>")
    fake_systems.registry["dnd5e"]["disclaimer"] = "Unofficial fan-made product, not affiliated with anyone."
    with pytest.raises(ListingError, match="trademark owner"):
        _dry(pack_item(tmp_path / "b2", niche="ttrpg", system="dnd5e"), settings)
    # a disclaimer that names another system's trademark fails the compliance check
    fake_systems.registry["dnd5e"]["disclaimer"] = DND_DISCLAIMER + " Also try Pathfinder."
    with pytest.raises(ListingComplianceError, match="Pathfinder"):
        _dry(pack_item(tmp_path / "c", niche="ttrpg", system="dnd5e"), settings)


def test_item_data_never_names_a_system_even_for_ttrpg(tmp_path, settings, fake_systems):
    d = pack_item(tmp_path / "out", niche="ttrpg", system="dnd5e", keywords=["dnd character sheet"])
    with pytest.raises(ListingComplianceError, match="item data"):
        _dry(d, settings)


# ------------------------------------------------------------------ medical claims

def test_medical_words_and_safe_phrases():
    assert rules.medical("Helps treat anxiety", CFG) == ["treat"]
    assert set(rules.medical("A healing, therapeutic planner; clinically proven", CFG)) == \
        {"healing", "therapeutic", "clinically"}
    assert rules.medical("for symptom relief", CFG) == ["symptom relief"]
    assert rules.medical("Diagnosed? Use our prescription tracker", CFG) == ["diagnosed", "prescription"]
    assert rules.medical("ADHD-friendly layout for ADHD brains", CFG) == []
    assert rules.medical("Trick or Treat coloring pages and sweet treats", CFG) == []
    assert rules.medical("a cute treat for kids", CFG) == ["treat"]


def test_medical_claims_in_model_copy_are_repaired(tmp_path, settings):
    d = pack_item(tmp_path / "out", niche="stationery", theme="adhd friendly", keywords=["adhd planner printable"])
    bad = good(title="ADHD Therapy Planner, 20 Pages",
               tags=["adhd therapy", *good()["tags"][1:]],
               about="These pages help treat ADHD symptoms and heal focus.",
               suggested_uses=["A medical-grade routine", "Plan the morning", "Track habits"])
    client = FakeClient(bad, bad, bad)
    lst = listing.write_listing(d, client=client, settings=settings)
    assert any("no medical or therapeutic claims" in p for p in lst["writer"]["repaired"])
    assert rules.medical(lst["title"], CFG) == [] and "therapy" not in lst["title"].lower()
    assert "adhd therapy" not in lst["tags"]
    assert rules.medical(lst["description"], CFG) == []
    assert "help treat" not in lst["description"]
    assert "medical" not in lst["description"].lower()
    assert "No medical or therapeutic claims" in client.calls[0]["system"]


def test_medical_wording_in_item_data_refuses_the_listing_before_any_call(tmp_path, settings):
    d = pack_item(tmp_path / "out", niche="stationery", theme="therapy session", keywords=["session planner"])
    client = FakeClient()
    with pytest.raises(ListingValidationError, match="item data has medical wording"):
        listing.write_listing(d, client=client, settings=settings)
    assert client.calls == [] and not (d / "listing.json").exists()


def test_medical_wording_in_fixed_sections_refuses_the_listing(tmp_path, settings, monkeypatch):
    # a fixed section that is never shipped like this: no repair can touch fixed text
    monkeypatch.setattr(template, "how_it_works", lambda facts, d: ["Print at home for a healing routine"])
    d = pack_item(tmp_path / "out", niche="stationery")
    with pytest.raises(ListingValidationError, match="medical wording"):
        _dry(d, settings)


def test_subjects_with_medical_words_are_left_out_not_refused(tmp_path, settings):
    d = pack_item(tmp_path / "out", theme="community helpers", keywords=["community helpers coloring pages"])
    m = read_json(d / "manifest.json")
    m["pack"]["contents"] = [{"page": 1, "subject": "doctor with a medical bag"}, {"page": 2, "subject": "firefighter"}]
    from core.items import write_json

    write_json(d / "manifest.json", m)
    lst = _dry(d, settings)
    assert "Designs include firefighter." in lst["description"] and "medical" not in lst["description"]
    assert lst["writer"]["repaired"] == []


def test_trick_or_treat_theme_lists(tmp_path, settings):
    d = pack_item(tmp_path / "out", theme="trick or treat", keywords=["trick or treat coloring pages"])
    lst = _dry(d, settings)
    assert "Trick or Treat" in lst["title"] and lst["writer"]["repaired"] == []


def test_official_claims_are_misleading():
    assert rules.misleading("Official D&D sheets", CFG) == ["official"]
    assert rules.misleading("Unofficial fan-made product", CFG) == []


# ------------------------------------------------------------------ review fixes

def test_owner_is_named_only_in_the_description(tmp_path, settings, fake_systems):
    """The trademark owner reads as an official product in a title or tag: those are
    repaired; the description's disclaimer names the owner as usual."""
    d = pack_item(tmp_path / "out", niche="ttrpg", system="dnd5e")
    draft = good(title="Wizards of the Coast Character Sheet for D&D 5e, 20 Pages",
                 tags=["wizards of the coast", *good()["tags"][1:]])
    client = FakeClient(draft, draft, draft)
    lst = listing.write_listing(d, client=client, settings=settings)
    assert any("names the trademark owner" in p for p in lst["writer"]["repaired"])
    owner = Blocklist([], [], {"Wizards of the Coast": "dnd5e"})
    assert owner.check(lst["title"], *lst["tags"]).ok
    assert "for D&D 5e" in lst["title"] and DND_DISCLAIMER in lst["description"]
    assert "names the trademark owner" in client.calls[1]["messages"][0]["content"]  # the model was told
    assert "trademark owner" in client.calls[0]["system"]


def test_system_tags_never_a_bare_edition(tmp_path, settings, fake_systems):
    d = pack_item(tmp_path / "out", niche="ttrpg", system="dnd5e", theme="dark fantasy",
                  keywords=["dark fantasy rpg"])
    lst = _dry(d, settings)
    named = [t for t in lst["tags"] if Blocklist.load(catalog=SYSTEMS).check(t).blocked]
    assert 1 <= len(named) <= template.SYSTEM_TAGS_MAX, lst["tags"]
    assert "5e" not in lst["tags"]
    assert "5e character sheet" in lst["tags"] and "dnd 5e" in lst["tags"]


def test_long_page_mix_is_summarized_in_the_about_text(tmp_path, settings):
    facts = {"niche": "ttrpg", "theme": "dark fantasy", "subjects": [],
             "page_types": {"character sheets": 2, "NPC trackers": 2, "quest log": 1, "hex map page": 1,
                            "dice and roll log": 1, "town worksheet": 1}}
    text = template.about(facts, CFG)
    assert "6 kinds of pages, among them character sheets, NPC trackers, dice and roll logs and more" in text
    assert template.type_mix(facts).startswith("2 character sheets, 2 NPC trackers, 1 dice and roll log")
    five = dict(facts, page_types={k: v for k, v in list(facts["page_types"].items())[:5]})
    assert "The pack mixes 2 character sheets, 2 NPC trackers, 1 dice and roll log" in template.about(five, CFG)
    short = dict(facts, page_types={"daily planners": 3, "brain dump pages": 2})
    assert "The pack mixes 3 daily planners and 2 brain dump pages." in template.about(dict(short, niche="stationery"))


def test_one_answer_key_page_is_singular(tmp_path, settings):
    d = pack_item(tmp_path / "out", niche="activity_sheets", size=5)
    m = read_json(d / "manifest.json")
    m["pack"]["answer_key_pages"] = 1
    from core.items import write_json

    write_json(d / "manifest.json", m)
    desc = _dry(d, settings)["description"]
    assert "Answer keys (1 page)" in desc and "(1 pages)" not in desc


def test_settings_can_drop_but_never_add_safe_phrases(settings):
    s = dict(settings, listing={**(settings.get("listing") or {}),
                                "medical_ok_phrases": ["trick or treat", "treat anxiety", "healing journal"]})
    cfg = listing.listing_config(s)
    assert cfg["medical_ok_phrases"] == ["trick or treat"]          # the built-in one kept, new ones ignored
    assert rules.medical("helps treat anxiety", cfg) == ["treat"]
    assert rules.medical("a healing journal", cfg) == ["healing"]
    assert rules.medical("trick or treat bags", cfg) == []
    default = listing.listing_config(settings)
    assert default["medical_ok_phrases"] == listing.DEFAULTS["medical_ok_phrases"]


def test_settings_can_add_but_never_remove_claim_words(settings):
    s = dict(settings, listing={**(settings.get("listing") or {}), "medical_terms": ["detox"],
                                "misleading_phrases": []})
    cfg = listing.listing_config(s)
    assert "treat" in cfg["medical_terms"] and "detox" in cfg["medical_terms"]
    assert "official" in cfg["misleading_phrases"] and "hand drawn" in cfg["misleading_phrases"]


@pytest.mark.parametrize("text, word", [("needs no medication", "medication"), ("for anxiety relief", "anxiety relief"),
                                        ("a clinical layout", "clinical"), ("ask your therapist", "therapist")])
def test_listing_claim_words_cover_the_research_words(text, word):
    assert rules.medical(text, CFG) == [word]


def test_every_research_claim_word_is_a_listing_claim_word():
    """Research cuts these words from themes and keywords; the listing rule is never looser."""
    from research.defaults import DEFAULTS as RESEARCH

    missed = [w for w in RESEARCH["medical_terms"] if not rules.medical(w, CFG)]
    assert missed == []


# ------------------------------------------------------------------ health topics (docs/HEALTH_TERMS.md, decided 2026-10-17)

def test_medical_topic_in_model_copy_is_repaired_not_stopped(tmp_path, settings):
    d = pack_item(tmp_path / "out", niche="stationery", theme="daily planner", keywords=["daily planner printable"])
    bad = good(about="A daily planner with a blood pressure log and a medication tracker page.")
    client = FakeClient(bad, bad, bad)
    lst = listing.write_listing(d, client=client, settings=settings)
    assert any("no medical or therapeutic claims" in p for p in lst["writer"]["repaired"])
    low = lst["description"].lower()
    assert "blood pressure" not in low and "medication" not in low
    assert Blocklist.load().check(*_texts(lst)).ok


def test_result_claims_in_model_copy_are_repaired_and_stress_relief_stays(tmp_path, settings):
    d = pack_item(tmp_path / "out", theme="mandala", keywords=["mandala coloring pages"])
    bad = good(about="Calm, detailed patterns on every page. Reduces anxiety and improves sleep.",
               suggested_uses=["Color to manage your ADHD symptoms", "Stress relief coloring on a quiet evening",
                               "Frame a finished page"])
    client = FakeClient(bad, bad, bad)
    lst = listing.write_listing(d, client=client, settings=settings)
    assert any("no medical or therapeutic claims" in p for p in lst["writer"]["repaired"])
    low = lst["description"].lower()
    assert "reduces anxiety" not in low and "improves sleep" not in low and "manage your adhd" not in low
    assert "stress relief coloring on a quiet evening" in low and "frame a finished page" in low
    assert rules.medical(lst["description"], CFG) == []
    assert "reduces anxiety" in client.calls[0]["system"] and "stress relief" in client.calls[0]["system"]


def test_medical_topic_in_item_data_is_refused_as_a_health_topic(tmp_path, settings):
    d = pack_item(tmp_path / "out", niche="stationery", theme="blood pressure log", keywords=["log pages"])
    client = FakeClient()
    with pytest.raises(ListingComplianceError, match=r"blood pressure \(health topic\)"):
        listing.write_listing(d, client=client, settings=settings)
    assert client.calls == [] and not (d / "listing.json").exists()


def test_borderline_health_topic_sends_the_item_to_review(tmp_path, settings):
    d = pack_item(tmp_path / "out", niche="stationery", theme="period tracker", keywords=["period tracker printable"])
    lst = _dry(d, settings)
    assert lst["needs_review"]
    assert any("period tracker (health topic)" in r for r in lst["review_reasons"]), lst["review_reasons"]


def test_lifestyle_theme_lists_without_review(tmp_path, settings):
    d = pack_item(tmp_path / "out", niche="stationery", theme="meditation journal", keywords=["meditation journal pages"])
    lst = _dry(d, settings)
    assert not lst["needs_review"] and rules.medical(lst["description"], CFG) == []
    assert "meditation" in lst["title"].lower()
