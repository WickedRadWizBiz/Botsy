"""core.systems: the game-system registry (config/ttrpg_systems.yaml), its validation and
system-name detection (CLAUDE.md game-system exception, docs/PACKS.md sections 1 and 4)."""
import copy

import pytest
import yaml

from core import systems
from core.compliance import Blocklist, normalize
from core.config import CONFIG_DIR

SHIPPED = CONFIG_DIR / "ttrpg_systems.yaml"


@pytest.fixture(scope="module")
def shipped_doc() -> dict:
    return yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))


def write(tmp_path, doc, name="systems.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return p


def entry(doc, sid):
    return next(e for e in doc["systems"] if e["id"] == sid)


# --- the shipped registry ------------------------------------------------------------------------

def test_shipped_registry_is_valid_and_lists_the_systems(shipped_doc):
    assert systems.validate(shipped_doc, Blocklist.load()) == []
    reg = systems.load_systems()
    assert list(reg) == ["generic", "dnd5e", "pf2e", "coc7e"]
    assert systems.default_system() == "generic"
    assert reg["generic"]["default"] is True and reg["dnd5e"]["default"] is False
    for sid, e in reg.items():
        assert e["id"] == sid
        assert set(e) >= {"id", "label", "names", "owner", "owner_names", "disclaimer", "sheet_fields"}
        assert set(e["sheet_fields"]) >= {"core", "details", "spells", "combat", "currency", "spell_card"}


def test_dnd5e_entry_matches_the_brief():
    e = systems.get_system("dnd5e")
    assert e["label"] == "D&D 5e"
    for name in ("D&D 5e", "Dungeons & Dragons 5e", "5e", "DnD 5e", "Fifth Edition"):
        assert name in e["names"]
    assert e["names"][0] == "D&D 5e"
    assert e["owner"] == "Wizards of the Coast"
    assert e["disclaimer"] == ("Unofficial fan-made product, not affiliated with or endorsed by Wizards of the "
                               "Coast. Dungeons & Dragons and D&D are trademarks of Wizards of the Coast.")
    labels = _labels(e["sheet_fields"]["core"]) + _labels(e["sheet_fields"]["spells"])
    for field in ("Strength", "Charisma", "Modifier", "Proficiency Bonus", "Saving Throws", "Skills", "Armor Class",
                  "Initiative", "Speed", "Hit Point Maximum", "Hit Dice", "Death Saves", "Successes", "Failures",
                  "Attacks", "Spellcasting Ability", "Spell Save DC", "Spell Attack Bonus", "Spell Slots"):
        assert field in labels, field
    details = _labels(e["sheet_fields"]["details"])
    assert "Equipment" in details and "Features and Traits" in details


def test_other_systems_name_their_owners():
    assert systems.owner_for("pf2e") == "Paizo Inc."
    assert systems.owner_for("coc7e") == "Chaosium Inc."
    assert systems.label_for("pf2e") == "Pathfinder 2e"
    assert systems.label_for("coc7e") == "Call of Cthulhu 7e"
    coc = _labels(systems.sheet_fields_for("coc7e")["core"])
    assert {"Characteristics", "Sanity", "Luck", "Half", "Fifth"} <= set(coc)


def test_every_named_system_has_a_compliant_disclaimer():
    """CLAUDE.md: every listing naming a system carries an "unofficial, not affiliated with
    <owner>" line."""
    for sid, e in systems.load_systems().items():
        if e["default"]:
            assert e["names"] == [] and e["owner"] is None and e["disclaimer"] == ""
            continue
        text = normalize(systems.disclaimer_for(sid))
        assert "unofficial" in text and "not affiliated with" in text
        assert normalize(e["owner"]) in text
        # a disclaimer names only its own system
        assert systems.detect_systems(e["disclaimer"]) == [sid]


def test_generic_names_and_disclaimer_are_empty():
    assert systems.names_for("generic") == []
    assert systems.disclaimer_for("generic") == ""
    assert systems.is_known("generic") and not systems.is_known("srd5") and not systems.is_known(None)


def test_unknown_id_raises_key_error():
    with pytest.raises(KeyError, match="unknown game system id 'nope'"):
        systems.names_for("nope")
    with pytest.raises(KeyError):
        systems.disclaimer_for("nope")


def _labels(sections) -> list[str]:
    out = []
    for s in sections:
        out += [s.get("title")] if s.get("title") else []
        for f in s.get("fields") or []:
            out.append(f["label"] if isinstance(f, dict) else f)
        out += list(s.get("columns") or []) + list(s.get("sub") or []) + list(s.get("marks") or [])
    return [x for x in out if x]


def test_sheet_labels_never_name_a_system_or_a_blocked_term():
    bl = Blocklist.load()
    for sid, e in systems.load_systems().items():
        sf = e["sheet_fields"]
        labels = []
        for group in ("core", "details", "spells"):
            labels += _labels(sf[group])
        labels += list(sf["combat"].values()) + list(sf["currency"]) + list(sf["spell_card"])
        assert bl.check(*labels).blocked == [], sid          # no `!` term allowed on a printed label
        assert systems.detect_systems("\n".join(labels)) == [], sid


def test_field_lists_differ_per_system():
    core = {sid: tuple(_labels(systems.sheet_fields_for(sid)["core"])) for sid in systems.load_systems()}
    assert len(set(core.values())) == len(core)
    assert "Might" in core["generic"] and "Might" not in core["dnd5e"]
    assert "Fortitude" in core["pf2e"] and "Fortitude" not in core["dnd5e"]
    assert "Sanity" in _labels(systems.sheet_fields_for("coc7e")["core"])


def test_load_returns_copies():
    a = systems.load_systems()
    a["dnd5e"]["names"].append("changed")
    assert "changed" not in systems.names_for("dnd5e")


# --- detection ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("DnD 5e character sheet", ["dnd5e"]),
    ("dnd5e sheet", ["dnd5e"]),
    ("D&D5e", ["dnd5e"]),
    ("d&d 5e", ["dnd5e"]),
    ("5E printable", ["dnd5e"]),
    ("5e", ["dnd5e"]),
    ("D & D campaign", ["dnd5e"]),
    ("dnd", ["dnd5e"]),
    ("DND notes", ["dnd5e"]),
    ("Dungeons and Dragons", ["dnd5e"]),
    ("Dungeons & Dragons fifth edition", ["dnd5e"]),
    ("D–D", ["dnd5e"]),                         # an en dash is a dash
    ("Wizards of the Coast", ["dnd5e"]),
    ("Pathfinder 2E sheets", ["pf2e"]),
    ("PF2e", ["pf2e"]),
    ("pathfinder second edition", ["pf2e"]),
    ("Call of Cthulhu 7th Edition", ["coc7e"]),
    ("coc 7e investigator", ["coc7e"]),
    ("Chaosium", ["coc7e"]),
    ("dnd 5e and pathfinder", ["dnd5e", "pf2e"]),
])
def test_detect_systems_variants(text, expected):
    assert systems.detect_systems(text) == expected


@pytest.mark.parametrize("text", ["dd form", "odd job", "AD&D", "hex map paper", "5em spacing", "add notes",
                                  "dragon adventure", "", "   ", "starfinder"])
def test_detect_systems_ignores_other_words(text):
    assert systems.detect_systems(text) == []


def test_detect_systems_ignores_non_text():
    assert systems.detect_systems(None) == [] and systems.detect_systems(42) == []


def test_system_terms_map_every_name_and_owner():
    terms = systems.system_terms()
    assert terms["D&D 5e"] == "dnd5e" and terms["Wizards of the Coast"] == "dnd5e"
    assert terms["PF2e"] == "pf2e" and terms["Paizo"] == "pf2e"
    assert terms["Call of Cthulhu"] == "coc7e"
    assert "generic" not in terms.values()


def test_name_pattern_matches_like_the_blocklist():
    rx = systems.name_pattern("D&D")
    assert rx.search(normalize("D&D")) and rx.search(normalize("d & d")) and not rx.search("dd")
    with pytest.raises(ValueError):
        systems.name_pattern("&&")
    own = systems._own_pattern("Dungeons & Dragons")
    assert own.search("dungeons and dragons") and own.search("dungeons&dragons") and not own.search("dungeonsxdragons")


def test_blocklist_allows_a_systems_names_only_for_that_system():
    """The compliance side of the exception: a dnd5e listing may carry its names and
    disclaimer; another system id (or none) may not."""
    bl = Blocklist.load()
    title = "Dark Fantasy Character Sheet for D&D 5e"
    disclaimer = systems.disclaimer_for("dnd5e")
    allowed = bl.check(title, disclaimer, allow_systems=("dnd5e",))
    assert allowed.blocked == []
    assert bl.check(title, disclaimer).blocked
    assert bl.check(title, allow_systems=("pf2e",)).blocked


# --- validation ---------------------------------------------------------------------------------------

def test_validate_reports_each_problem(shipped_doc):
    doc = copy.deepcopy(shipped_doc)
    entry(doc, "dnd5e")["disclaimer"] = "Fan-made product."
    entry(doc, "pf2e")["names"].append("5e")                       # another system's name
    entry(doc, "coc7e")["owner"] = ""
    entry(doc, "generic")["names"] = ["Generic RPG"]
    entry(doc, "generic")["sheet_fields"]["core"][1]["fields"][0] = "D&D Might"   # a system name on a label
    entry(doc, "generic")["sheet_fields"]["details"].append({"kind": "spinner", "title": "X"})
    doc["systems"].append({"id": "Bad Id", "label": "x"})
    problems = systems.validate(doc)
    text = "\n".join(problems)
    assert "systems.dnd5e.disclaimer: must say 'unofficial'" in text
    assert "systems.dnd5e.disclaimer: must say 'not affiliated with'" in text
    assert "is also a name of dnd5e" in text
    assert "systems.coc7e.owner: the trademark owner is required" in text
    assert "systems.generic: the default (system-neutral) entry has no names" in text
    assert "label 'D&D Might' names a game system" in text
    assert "kind must be one of" in text
    assert "systems[4].id: must match" in text


def test_validate_requires_the_default_groups_and_owner_in_disclaimer(shipped_doc):
    doc = copy.deepcopy(shipped_doc)
    del entry(doc, "generic")["sheet_fields"]["spells"]
    entry(doc, "pf2e")["disclaimer"] = "Unofficial fan-made product, not affiliated with anyone."
    doc["default"] = "generic"
    problems = systems.validate(doc)
    assert any("generic.sheet_fields.spells: is required" in p for p in problems)
    assert any("pf2e.disclaimer: must name the owner 'Paizo Inc.'" in p for p in problems)
    doc2 = copy.deepcopy(shipped_doc)
    doc2["default"] = "nothing"
    assert any("default 'nothing' is not one of the systems" in p for p in systems.validate(doc2))
    assert systems.validate([]) == ["the file must hold a mapping with `default` and `systems`"]
    assert systems.validate({"systems": []}) == ["`systems` must be a non-empty list"]


def test_validate_with_a_blocklist_checks_labels(shipped_doc):
    doc = copy.deepcopy(shipped_doc)
    entry(doc, "generic")["sheet_fields"]["details"][1]["title"] = "Frozen Lands"
    assert systems.validate(doc) == []
    assert any("blocked terms ['Frozen']" in p for p in systems.validate(doc, Blocklist.load()))


def test_validate_checks_section_shapes(shipped_doc):
    doc = copy.deepcopy(shipped_doc)
    core = entry(doc, "generic")["sheet_fields"]["core"]
    core.append({"kind": "table", "title": "T", "columns": ["A", "B"], "weights": [1]})
    core.append({"kind": "slots", "title": "S"})
    core.append({"kind": "track", "title": "Tr", "fields": [{"label": "A"}]})
    core.append({"kind": "checklist", "title": "C", "fields": ["A", "A"], "marks": ["ABC"]})
    core.append({"kind": "notes", "title": "N", "rows": 0, "span": "half"})
    core.append({"kind": "boxes", "fields": ["Aé中"]})
    problems = "\n".join(systems.validate(doc))
    for want in ("weights: one positive number per column", "levels: is required", "count: is required",
                 "repeats a label", "each mark is one or two letters", "rows: must be a whole number",
                 "span: must be full or column", "a boxes section needs a title",
                 "uses characters a printed label may not have"):
        assert want in problems, want


def test_missing_groups_are_inherited_from_the_default(tmp_path, shipped_doc):
    doc = copy.deepcopy(shipped_doc)
    del entry(doc, "coc7e")["sheet_fields"]["spells"]
    del entry(doc, "coc7e")["sheet_fields"]["currency"]
    p = write(tmp_path, doc)
    fields = systems.sheet_fields_for("coc7e", path=p)
    assert fields["spells"] == entry(doc, "generic")["sheet_fields"]["spells"]
    assert fields["currency"] == entry(doc, "generic")["sheet_fields"]["currency"]
    assert fields["core"] == entry(doc, "coc7e")["sheet_fields"]["core"]


def test_a_broken_file_raises_systems_error(tmp_path, shipped_doc):
    bad = tmp_path / "bad.yaml"
    bad.write_text("systems: [\n", encoding="utf-8")
    with pytest.raises(systems.SystemsError, match="not valid YAML"):
        systems.load_systems(path=bad)
    with pytest.raises(systems.SystemsError, match="cannot read"):
        systems.load_systems(path=tmp_path / "missing.yaml")
    doc = copy.deepcopy(shipped_doc)
    entry(doc, "dnd5e")["disclaimer"] = ""
    with pytest.raises(systems.SystemsError) as err:
        systems.detect_systems("dnd", path=write(tmp_path, doc))
    assert any("disclaimer: required" in p for p in err.value.problems)


def test_settings_choose_the_file_and_changes_are_reloaded(tmp_path, shipped_doc):
    doc = copy.deepcopy(shipped_doc)
    entry(doc, "pf2e")["names"].append("Pathfinder Remaster")
    p = write(tmp_path, doc)
    settings = {"ttrpg": {"systems_file": str(p)}}
    assert systems.systems_path(settings) == p
    assert "Pathfinder Remaster" in systems.names_for("pf2e", settings)
    entry(doc, "pf2e")["names"].remove("Pathfinder Remaster")
    entry(doc, "pf2e")["names"].append("PF Remastered Rules")
    p.write_text(yaml.safe_dump(doc, sort_keys=False) + "\n# changed\n", encoding="utf-8")
    assert "PF Remastered Rules" in systems.names_for("pf2e", settings)
    assert systems.detect_systems("pf remastered rules", settings) == ["pf2e"]
    # the shipped registry is untouched
    assert "PF Remastered Rules" not in systems.names_for("pf2e")


def test_systems_keyed_by_id_are_accepted(tmp_path, shipped_doc):
    doc = copy.deepcopy(shipped_doc)
    doc["systems"] = {e["id"]: {k: v for k, v in e.items() if k != "id"} for e in doc["systems"]}
    assert systems.validate(doc) == []
    assert list(systems.load_systems(path=write(tmp_path, doc))) == ["generic", "dnd5e", "pf2e", "coc7e"]
