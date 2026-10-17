"""Pre-flight for game-system names (CLAUDE.md exception, docs/PACKS.md section 4): a ttrpg
item may name the system its manifest names in title, tags and description, with that
system's disclaimer; nothing else may name a system anywhere."""
from __future__ import annotations

import pytest

from core.items import read_json, write_json
from publisher import PublishGateError
from publisher.publish import check_ready, publish_draft, review_wait
from tests.listing.conftest import SYSTEMS, FakeSystems

from .conftest import make_item

TTRPG_ID = "2026-10-16-ttrpg-dark-fantasy-1a2b"
DISCLAIMER = SYSTEMS["dnd5e"]["disclaimer"]
TAGS = ["dnd 5e sheet", "character sheet", "rpg printable", "ttrpg printable", "campaign tracker",
        "session notes", "npc tracker", "dark fantasy", "game master tools", "rpg journal", "quest log",
        "digital download", "fantasy rpg"]


@pytest.fixture
def systems(monkeypatch) -> FakeSystems:
    import core.systems

    fake = FakeSystems(SYSTEMS)
    for name in ("load_systems", "default_system", "names_for", "label_for", "disclaimer_for", "detect_systems"):
        monkeypatch.setattr(core.systems, name, getattr(fake, name))
    return fake


def ttrpg_item(out_root, settings, *, system="dnd5e", niche="ttrpg", disclaimer=True, **listing):
    from core.compliance import load_ai_disclosure

    desc = "Dark fantasy character sheets for D&D 5e players.\n\n"
    if disclaimer:
        desc += "Made for use with D&D 5e.\n" + DISCLAIMER + "\n\n"
    desc += load_ai_disclosure(settings["shops"][0]["name"])
    lst = {"title": "Dark Fantasy Character Sheet for D&D 5e, 20 Pages", "tags": TAGS, "description": desc}
    lst.update(listing)
    manifest = {"niche": niche, "theme": "dark fantasy"}
    if system is not None:
        manifest["system"] = system
    return make_item(out_root, settings, TTRPG_ID, manifest=manifest, listing=lst)


def _problems(d, settings, review_dir) -> list[str]:
    with pytest.raises(PublishGateError) as err:
        check_ready(d, settings=settings, review_dir=review_dir)
    return err.value.problems


def test_ttrpg_item_may_name_its_system_with_the_disclaimer(out_root, settings, review_dir, systems):
    d = ttrpg_item(out_root, settings)
    plan = check_ready(d, settings=settings, review_dir=review_dir)
    assert "for D&D 5e" in plan.fields["title"] and DISCLAIMER in plan.fields["description"]
    assert review_wait(d, settings=settings, review_dir=review_dir) is None


def test_ttrpg_item_drafts_end_to_end_in_a_dry_run(out_root, settings, review_dir, systems):
    d = ttrpg_item(out_root, settings)
    m = read_json(d / "manifest.json")
    m["generator"]["dry_run"] = True
    write_json(d / "manifest.json", m)
    rec = publish_draft(d, settings=settings, review_dir=review_dir, dry_run=True)
    assert rec["dry_run"] is True and rec["state"] == "draft"


def test_system_names_require_the_disclaimer(out_root, settings, review_dir, systems):
    d = ttrpg_item(out_root, settings, disclaimer=False)
    assert any("lacks its disclaimer" in p for p in _problems(d, settings, review_dir))


def test_no_system_named_needs_no_disclaimer(out_root, settings, review_dir, systems):
    d = ttrpg_item(out_root, settings, disclaimer=False, title="Dark Fantasy Character Sheet, 20 Pages",
                   tags=[t if t != "dnd 5e sheet" else "rpg sheets" for t in TAGS],
                   description="Dark fantasy character sheets.\n\n")
    from core.compliance import load_ai_disclosure

    lst = read_json(d / "listing.json")
    lst["description"] += load_ai_disclosure(settings["shops"][0]["name"])
    write_json(d / "listing.json", lst)
    assert check_ready(d, settings=settings, review_dir=review_dir).item_id == TTRPG_ID


@pytest.mark.parametrize("niche, system", [("ttrpg", "generic"), ("ttrpg", None), ("coloring_pages", "dnd5e"),
                                           ("stationery", "dnd5e")])
def test_system_names_blocked_without_an_allowed_system(out_root, settings, review_dir, systems, niche, system):
    d = ttrpg_item(out_root, settings, niche=niche, system=system)
    assert any("blocked trademark terms" in p and "D&D" in p for p in _problems(d, settings, review_dir))


def test_never_in_alt_text_or_file_names(out_root, settings, review_dir, systems):
    d = ttrpg_item(out_root, settings, alt_texts=["A D&D character sheet", "Sheets on a desk"])
    assert any("blocked trademark terms: D&D" in p for p in _problems(d, settings, review_dir))
    d2 = ttrpg_item(out_root / "b", settings)
    lst = read_json(d2 / "listing.json")
    (d2 / "files/farm-animals-1.pdf").rename(d2 / "files/sheets-dnd5e.pdf")
    lst["file_paths"] = ["files/sheets-dnd5e.pdf"]
    write_json(d2 / "listing.json", lst)
    assert any("file names name a game system (dnd5e)" in p for p in _problems(d2, settings, review_dir))


def test_never_another_system(out_root, settings, review_dir, systems):
    d = ttrpg_item(out_root, settings, title="Character Sheet for D&D 5e and Pathfinder, 20 Pages")
    assert any("Pathfinder" in p for p in _problems(d, settings, review_dir))
    d2 = ttrpg_item(out_root / "b", settings)
    lst = read_json(d2 / "listing.json")
    lst["description"] = lst["description"].replace("players.", "players, and PF2e too.")
    write_json(d2 / "listing.json", lst)
    assert any("may not name: pf2e" in p for p in _problems(d2, settings, review_dir))


def test_unknown_system_is_refused_with_every_other_problem(out_root, settings, review_dir, systems):
    d = ttrpg_item(out_root, settings, system="nope", taxonomy_id=None)
    problems = _problems(d, settings, review_dir)
    assert any("cannot be read" in p for p in problems)
    assert any("blocked trademark terms" in p for p in problems)        # nothing allowed then
    assert any("taxonomy_id" in p for p in problems)                    # the other gates still report


def test_owner_never_in_title_or_tags(out_root, settings, review_dir, systems):
    """The disclaimer names the owner in the description; a title or tag naming it would
    read as an official product (CLAUDE.md: never implying the product is official)."""
    d = ttrpg_item(out_root, settings, tags=[*TAGS[:-1], "wizards of the coast"])
    assert any("name the trademark owner (Wizards of the Coast)" in p for p in _problems(d, settings, review_dir))
    d2 = ttrpg_item(out_root / "b", settings, title="Wizards of the Coast Sheet for D&D 5e, 20 Pages")
    assert any("trademark owner" in p for p in _problems(d2, settings, review_dir))


@pytest.mark.parametrize("field, value", [
    ("title", "ADHD Therapy Planner for D&D 5e, 20 Pages"),
    ("description", "These sheets help treat stress."),
    ("alt_texts", ["A clinically tested sheet"]),
])
def test_medical_wording_is_refused(out_root, settings, review_dir, systems, field, value):
    d = ttrpg_item(out_root, settings)
    lst = read_json(d / "listing.json")
    lst[field] = value + "\n\n" + lst[field] if field == "description" else value
    write_json(d / "listing.json", lst)
    assert any("medical or therapeutic wording" in p for p in _problems(d, settings, review_dir))


def test_safe_phrases_are_not_medical(out_root, settings, review_dir, systems):
    d = ttrpg_item(out_root, settings)
    lst = read_json(d / "listing.json")
    lst["description"] = "Trick or treat night sheets and sweet treats for the table.\n\n" + lst["description"]
    write_json(d / "listing.json", lst)
    assert check_ready(d, settings=settings, review_dir=review_dir).item_id == TTRPG_ID
