"""Research for themed packs (docs/PACKS.md sections 1, 2 and 4): the stationery and ttrpg
niches, opportunity ranking, top-seller pack sizes, pack bounds and game systems."""
import json
import re
import sys
import types

import pytest

from core.compliance import Blocklist, iter_strings, system_mentions
from research import run
from research.defaults import DEFAULTS, PACK_BOUNDS, medical_terms, pack_bounds
from research.demo import DEFAULT_NICHES, DEMO_NICHES, THEMES, write_demo_inputs
from research.guard import extend_blocklist
from research.niches import assign_niche, compile_rules
from research.pipeline import niche_text_rules
from research.text import TextRules, clean, pack_count

from .conftest import (DATE, ERANK_KW, EVERBEE, SHOPS, SYSTEM_TERMS, SystemsBlocklist,
                       five_niche_settings, load, pinned_settings,
                       systems_blocklist, write_csv)

RULES = compile_rules(DEFAULTS["niche_rules"])


def _run(inputs, out, **kw):
    kw.setdefault("date", DATE)
    kw.setdefault("settings", five_niche_settings())
    return run(inputs_dir=inputs, briefs_dir=out, **kw)


def _doc(inputs, out, niche, **kw):
    paths = [p for p in _run(inputs, out, niche=niche, **kw) if p.name.startswith(niche)]
    assert paths, f"no {niche} brief file"
    return load(paths[0])


def _briefs(doc):
    return {b["theme"]: b for b in doc["briefs"]}


def _row(title, shop, *, sales=20, reviews=5, favorites="1,200", tags="", price="$3.99", n=[0]):
    n[0] += 1
    lid = 7_100_000_000 + n[0]
    return [title, shop, price, sales, reviews, favorites, "3 months", tags,
            f"https://www.etsy.com/listing/{lid}/x", f"https://i.etsystatic.com/1/il_570xN.{lid}.jpg"]


def _rows(title, count, *, shops=SHOPS, **kw):
    """`count` rows "<title>, Set <i>" rotating over `shops`."""
    return [_row(f"{title}, Set {i}", shops[i % len(shops)], **kw) for i in range(1, count + 1)]


# ---------------------------------------------------------------- niches and text rules

@pytest.mark.parametrize("text, niche", [
    ("ADHD Daily Planner Pages, Printable PDF", "stationery"),
    ("Minimalist Weekly Planner Printable", "stationery"),
    ("Habit Tracker and Mood Tracker, Undated", "stationery"),
    ("Brain Dump Sheet, To Do List Printable", "stationery"),
    ("Teacher Planner Inserts", "stationery"),
    ("Dot Grid Paper Printable", "stationery"),
    ("Weekly Planner Stickers, Digital Download", "stickers"),     # a sticker product
    ("ADHD Planner Stickers", "stickers"),
    ("ADHD Coloring Pages for Adults", "coloring_pages"),         # tie -> listed first
    ("Cyberpunk Character Sheet, Fillable PDF", "ttrpg"),
    ("Hex Map Paper Printable, TTRPG", "ttrpg"),
    ("Campaign Tracker and Session Notes for Game Masters", "ttrpg"),
    ("Worldbuilding Worksheets for Tabletop RPG", "ttrpg"),
    ("Initiative Tracker Cards", "ttrpg"),
    ("Dungeon Map Coloring Pages", "coloring_pages"),
    ("Farm Animals Coloring Pages for Kids", "coloring_pages"),   # unchanged
    ("Dinosaur Mazes for Kids", "activity_sheets"),
    ("Wedding invitation template", None),
])
def test_new_niche_assignment(text, niche):
    assert assign_niche(text, RULES) == niche


def test_niche_rules_name_no_game_system_or_planner_brand():
    bl = systems_blocklist()
    for niche in ("stationery", "ttrpg"):
        for rx in DEFAULTS["niche_rules"][niche]:
            assert not bl.check(rx.replace("\\b", " ")).blocked, rx


def test_stationery_themes_are_styles_not_page_types(blocklist):
    cfg = {**DEFAULTS}
    rules = niche_text_rules(cfg, "stationery", blocklist)
    assert set(rules.grams("ADHD Habit Tracker, Brain Dump and To Do List Printable").keys.values()) \
        == {"adhd"}
    assert set(rules.grams("Cottagecore Daily Planner Pages, Undated, Time Blocking").keys.values()) \
        == {"cottagecore"}
    # "teacher" is a stationery audience, and still a stopword in every other niche.
    assert "teacher" in rules.grams("Teacher Planner Inserts").keys.values()
    coloring = niche_text_rules(cfg, "coloring_pages", blocklist)
    assert set(coloring.grams("Teacher Appreciation Coloring Pages").keys.values()) == {"appreciation"}


def test_ttrpg_themes_are_genres_not_sheet_types(blocklist):
    rules = niche_text_rules({**DEFAULTS}, "ttrpg", blocklist)
    assert set(rules.grams("Cyberpunk RPG Character Sheet, Session Notes, NPC Tracker").keys.values()) \
        == {"cyberpunk"}
    assert set(rules.grams("Hex Map Paper for Tabletop Games").keys.values()) == set()
    assert "cosmic horror" in rules.grams("Cosmic Horror Campaign Journal").keys.values()


def test_work_from_home_is_one_stationery_theme(blocklist):
    rules = niche_text_rules({**DEFAULTS}, "stationery", blocklist)
    assert "work from home" in rules.grams("Work From Home Daily Planner Printable").keys.values()
    # "from" joins words in stationery only.
    coloring = niche_text_rules({**DEFAULTS}, "coloring_pages", blocklist)
    assert "work from home" not in coloring.grams("Work From Home Coloring Pages").keys.values()


def test_planner_brands_and_tabletop_apps_never_become_themes(blocklist):
    st = niche_text_rules({**DEFAULTS}, "stationery", blocklist)
    assert set(st.grams("Bullet Journal Printable Pages, BuJo Inserts, Notion Template").keys.values()) \
        == set()
    for text in ("panda planner pages", "clever fox weekly", "bullet journaling ideas"):
        assert st.has_excluded(text), text
    tt = niche_text_rules({**DEFAULTS}, "ttrpg", blocklist)
    assert set(tt.grams("Roll20 Cyberpunk Character Sheet, Foundry VTT, D&D Beyond").keys.values()) \
        == {"cyberpunk"}
    assert tt.has_excluded("owlbear rodeo hex map") and tt.has_excluded("world anvil template")
    # A publisher's trademarked titles never become a keyword; the generic word stays.
    for text in ("dungeon master gift", "Dungeon Master's Guide insert", "players handbook tabs",
                 "monster manual cards"):
        assert tt.has_excluded(text), text
    assert not tt.has_excluded("game master notebook")


def test_ttrpg_filler_words_are_not_themes(blocklist):
    rules = niche_text_rules({**DEFAULTS}, "ttrpg", blocklist)
    assert set(rules.grams("DM Screen Inserts, Town Generator, Homebrew Fan Tools").keys.values()) == set()
    assert set(rules.grams("Dark Fantasy Character Sheet for Fans").keys.values()) \
        == {"dark", "dark fantasy", "fantasy"}


@pytest.mark.parametrize("text, count", [
    ("Cyberpunk Character Sheets for D&D 5e", None),       # an edition, not 5 sheets
    ("Pathfinder 2e NPC Tracker Sheets", None),
    ("8.5 x 11 Planner Pages, Undated", None),             # a page size
    ("11 x 17 Battle Map Printable", None),
    ("1 inch Hex Map Paper", None),                         # a hex size
    ("30 Day Habit Tracker Printable", None),               # a time span on one page
    ("3 Month Budget Tracker", None),
    ("365 Days Planner Pages", None),
    ("2 inch Stickers, 20 Pack", 20),
    ("ADHD Daily Planner, 20 Planner Pages", 20),
    ("Set of 12 Planner Inserts", 12),
    ("15 Cyberpunk Character Sheets for 5e", 15),
    ("Weekly Planner 52 Pages", 52),
    ("Dinosaur Coloring Pages, 25 Printable Pages", 25),   # unchanged
    ("3D Stickers, 20 Pack", 20),                          # "3d" is a style, not 3 stickers
    ("3d animal stickers", None),
])
def test_pack_counts_in_new_niche_titles(text, count):
    assert pack_count(text) == count


# ---------------------------------------------------------------- medical claims

_MEDICAL_RE = re.compile(r"\b(therap\w*|relief|treat\w*|cur(e|es|ed|ing)|heal(s|ed|ing)?|"
                         r"diagnos\w*|medic\w*|clinical\w*|prescri\w*)\b")


def test_medical_claim_words_never_reach_a_brief(dirs):
    """CLAUDE.md: no medical claims. A theme never holds one, and a tag or keyword phrase
    that holds one is never a brief keyword, even with research.medical_terms emptied."""
    inputs, out = dirs
    rows = [_row(f"ADHD Daily Planner Pages, Therapy Planner, Anxiety Relief, Set {i}", SHOPS[i % 3],
                 tags="adhd planner,adhd therapy planner,anxiety relief journal,"
                      "adhd daily planner,planner to treat adhd")
            for i in range(12)]
    rows += [[f"{m} adhd planner for therapy".strip(), "900", "300", "25%", "1,500", "N/A"]
             for m in ("", "printable", "easy", "cute", "simple", "big", "bold", "fun", "free", "new")]
    write_csv(inputs / "everbee.csv", EVERBEE, rows[:12])
    write_csv(inputs / "erank-keywords.csv", ERANK_KW, rows[12:])
    settings = five_niche_settings()
    settings["research"]["medical_terms"] = []          # cannot loosen the built-in list
    doc = _doc(inputs, out, "stationery", settings=settings)
    brief = _briefs(doc)["adhd"]
    assert "adhd planner" in brief["keywords"] and brief["evidence"]["listing_rows"] == 12
    for s in iter_strings(doc):
        assert not _MEDICAL_RE.search(s.lower()), s


def test_medical_terms_settings_add_never_remove():
    assert medical_terms({"medical_terms": []}) == DEFAULTS["medical_terms"]
    assert medical_terms({"medical_terms": ["detox"]})[-1] == "detox"
    assert {"treat", "cure", "heal", "diagnose", "therapy", "therapeutic", "clinically",
            "medical", "prescription", "symptom relief"} <= set(DEFAULTS["medical_terms"])
    assert "adhd" not in DEFAULTS["medical_terms"]       # "ADHD-friendly" describes a layout


# ---------------------------------------------------------------- game systems

def test_system_terms_are_cut_but_the_row_is_not_licensed_property():
    rules = TextRules(blocklist=SystemsBlocklist(["Beholder"]))
    g = rules.grams("Cyberpunk Character Sheet for D&D 5e, Dungeons and Dragons, DnD's best")
    assert g.system and not g.blocked and not g.flagged
    assert not {"d", "dnd", "dungeons", "dragons", "5e"} & {w for k in g.keys for w in k}
    assert "cyberpunk" in g.keys.values()
    hard = rules.grams("Beholder Character Sheet")
    assert hard.blocked and not hard.system
    assert rules.cut_systems("dnd 5e character sheet") == "character sheet"
    assert rules.cut_systems("pathfinder2e npc tracker") == "npc tracker"   # an edition written on
    assert rules.cut_systems("dnd") == ""
    assert rules.system_ids("Sheet for D&D 5e and Pathfinder") == {"srd5", "pf2"}


def test_older_blocklist_without_system_terms_still_works():
    rules = TextRules(blocklist=Blocklist(["Disney"], ["Snow White"]))
    assert rules.systems == [] and rules.cut_systems("Frog Stickers!") == "frog stickers"
    assert not rules.grams("Frog Stickers").system
    assert rules.grams("Disney Frog Stickers").blocked


@pytest.mark.parametrize("text, cut", [
    ("cyberpunk character sheet for d&d", "cyberpunk character sheet"),   # no dangling "for"
    ("character sheet for dnd and pathfinder", "character sheet"),
    ("5e compatible character sheet", "character sheet"),
    ("d&d 5th edition character sheet", "character sheet"),               # the edition goes too
    ("dungeonsanddragons sheet", "sheet"),                                 # joined words
    ("dnd5e cyberpunk sheet", "cyberpunk sheet"),
    ("wotc character sheet", "character sheet"),                           # an owner spelling
    ("cyberpunk d&d beyond sheet", "cyberpunk sheet"),                     # the app named after it
    ("hex map for players", "hex map for players"),                        # nothing cut: unchanged
])
def test_cutting_a_system_name_leaves_a_clean_phrase(text, cut):
    rules = TextRules(["sheet"], DEFAULTS["excluded_terms"], SystemsBlocklist(),
                      system_names={"WotC": "srd5"})
    assert rules.cut_systems(text) == cut


def test_extend_blocklist_keeps_system_terms():
    bl = SystemsBlocklist(["Disney"], ["Snow White"])
    out = extend_blocklist(bl, hard=["Grinch", "Pathfinder"], review=["DnD", "Bluey"])
    assert out is not bl and out.system_terms == bl.system_terms
    assert out.check("grinch").blocked == ["Grinch"] and out.check("bluey").flagged == ["Bluey"]
    assert "DnD" not in out.check("dnd").flagged         # a system term keeps its kind
    assert [t for t, _ in out.hard].count("Pathfinder") == 1


def _fake_systems(monkeypatch, detect):
    mod = types.ModuleType("core.systems")
    mod.detect_systems = detect
    monkeypatch.setitem(sys.modules, "core.systems", mod)
    return mod


def _detect_srd(text):
    t = clean(text)
    return ["srd5"] if any(w in t for w in ("d and d", "dnd", "5e", "dungeons")) else []


def _ttrpg_rows(with_system: int, total: int = 12, theme="Cyberpunk"):
    rows = []
    for i in range(1, total + 1):
        name = (f"{theme} Character Sheet for D&D 5e, Printable PDF, Set {i}" if i <= with_system
                else f"{theme} Character Sheet, Printable PDF, Set {i}")
        rows.append(_row(name, SHOPS[i % 3], tags=f"dnd character sheet,{theme.lower()} dnd,dnd 5e"))
    return rows


def _no_system_text(doc, bl):
    rules = TextRules(blocklist=bl)
    for s in iter_strings(doc):
        assert not any(rx.search(clean(s)) for _, _, rx in rules.systems), s
    assert not bl.check_obj(doc).blocked


def test_ttrpg_brief_names_the_system_by_id_only(dirs, monkeypatch):
    inputs, out = dirs
    _fake_systems(monkeypatch, _detect_srd)
    write_csv(inputs / "everbee.csv", EVERBEE, _ttrpg_rows(with_system=8))
    bl = systems_blocklist()
    doc = _doc(inputs, out, "ttrpg", blocklist=bl)
    brief = _briefs(doc)["cyberpunk"]
    assert brief["system"] == "srd5"                     # 8 of 12 clean rows name it
    # Every row named the system and still counts: no licensed-property drop or flag.
    assert brief["evidence"]["listing_count"] == 12 and brief["needs_review"] is False
    assert "cyberpunk" in brief["keywords"]               # "cyberpunk dnd" tag, the name cut
    _no_system_text(doc, bl)


def test_ttrpg_brief_is_generic_below_half(dirs, monkeypatch):
    inputs, out = dirs
    _fake_systems(monkeypatch, _detect_srd)
    write_csv(inputs / "everbee.csv", EVERBEE, _ttrpg_rows(with_system=5))
    bl = systems_blocklist()
    doc = _doc(inputs, out, "ttrpg", blocklist=bl)
    assert _briefs(doc)["cyberpunk"]["system"] == "generic"
    _no_system_text(doc, bl)


def test_hard_blocked_name_still_drops_the_theme(dirs, monkeypatch):
    """The same rows with "Pathfinder" hard-blocked instead of a system name: every row is
    licensed-property, so the theme is dropped (blocked co-occurrence)."""
    inputs, out = dirs
    _fake_systems(monkeypatch, lambda text: [])
    rows = [_row(f"Cyberpunk Character Sheet for Pathfinder, Set {i}", SHOPS[i % 3]) for i in range(12)]
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    as_system = _doc(inputs, out, "ttrpg", blocklist=systems_blocklist())
    assert "cyberpunk" in _briefs(as_system)
    hard = SystemsBlocklist(["Pathfinder"], systems={k: v for k, v in SYSTEM_TERMS.items()
                                                     if k != "Pathfinder"})
    as_hard = _doc(inputs, out, "ttrpg", blocklist=hard)
    assert "cyberpunk" not in _briefs(as_hard)


def test_system_detection_falls_back_to_the_blocklist(dirs, monkeypatch, caplog):
    inputs, out = dirs
    monkeypatch.setitem(sys.modules, "core.systems", None)   # not importable
    write_csv(inputs / "everbee.csv", EVERBEE, _ttrpg_rows(with_system=12))
    doc = _doc(inputs, out, "ttrpg", blocklist=systems_blocklist())
    assert _briefs(doc)["cyberpunk"]["system"] == "srd5"     # the blocklist's !term -> id

    def broken(text):
        raise FileNotFoundError("config/ttrpg_systems.yaml")

    _fake_systems(monkeypatch, broken)
    with caplog.at_level("WARNING", logger="research.pipeline"):
        doc = _doc(inputs, out, "ttrpg", blocklist=systems_blocklist())
    assert _briefs(doc)["cyberpunk"]["system"] == "srd5"
    assert caplog.text.count("detect_systems failed") == 1


@pytest.mark.parametrize("bad_id", ["dnd_5e", "D&D 5e", "x" * 41, "dnd5e", "pathfinder2e"])
def test_an_unsafe_system_id_is_never_written(dirs, monkeypatch, caplog, bad_id):
    """Without a systems registry to vouch for an id, one that spells a name is refused."""
    inputs, out = dirs
    _fake_systems(monkeypatch, lambda text: [bad_id])
    write_csv(inputs / "everbee.csv", EVERBEE, _ttrpg_rows(with_system=12))
    bl = systems_blocklist()
    with caplog.at_level("WARNING", logger="research.briefs"):
        doc = _doc(inputs, out, "ttrpg", blocklist=bl)
    assert _briefs(doc)["cyberpunk"]["system"] == "generic"
    assert "cannot go into a brief" in caplog.text
    _no_system_text(doc, bl)


def test_names_from_the_systems_file_are_cut_too(dirs, monkeypatch):
    """A system name in config/ttrpg_systems.yaml that the blocklist does not list yet is
    still cut (core.systems.load_systems); one the blocklist hard-blocks stays blocked."""
    inputs, out = dirs
    mod = _fake_systems(monkeypatch, lambda text: ["osr1"] if "shadow realm" in clean(text) else [])
    mod.load_systems = lambda settings=None: {
        "osr1": {"id": "osr1", "names": ["Shadow Realm RPG", "Shadow Realm"], "owner": "Example Co"},
        "wh": {"id": "wh", "names": ["Warhammer"], "owner": "Example Co"},
        "generic": {"id": "generic", "names": ["Generic"]},
    }
    rows = ([_row(f"Cyberpunk Character Sheet for Shadow Realm, Set {i}", SHOPS[i % 3],
                  tags="shadow realm sheet,cyberpunk shadow realm") for i in range(12)]
            + [_row(f"Grimdark Character Sheet for Warhammer, Set {i}", SHOPS[i % 3]) for i in range(12)])
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    bl = SystemsBlocklist(["Warhammer"])
    doc = _doc(inputs, out, "ttrpg", blocklist=bl)
    briefs = _briefs(doc)
    assert briefs["cyberpunk"]["system"] == "osr1" and briefs["cyberpunk"]["evidence"]["listing_count"] == 12
    assert "grimdark" not in briefs                      # licensed-property rows: dropped
    text = json.dumps(doc).lower()
    assert "shadow" not in text and "realm" not in text and "warhammer" not in text


def _registry(monkeypatch, ids=("dnd5e", "pf2e")):
    """A fake core.systems whose registry lists `ids` (plus the default) and detects them."""
    entries = {"generic": {"id": "generic", "names": [], "owner": None, "owner_names": [],
                           "default": True},
               "dnd5e": {"id": "dnd5e", "names": ["D&D 5e", "DnD", "D&D", "5e"],
                         "owner": "Wizards of the Coast", "owner_names": ["WotC"], "default": False},
               "pf2e": {"id": "pf2e", "names": ["Pathfinder 2e", "PF2e", "Pathfinder"],
                        "owner": "Paizo Inc.", "owner_names": ["Paizo"], "default": False}}
    found = {"homebrew9": ["homebrew9"]}

    def detect(text, settings=None):
        t = clean(text)
        hits = [sid for sid, e in entries.items() if any(clean(n) in t for n in e["names"] if n)]
        return hits + [sid for sid, words in found.items() if any(w in t for w in words)]

    mod = _fake_systems(monkeypatch, detect)
    mod.load_systems = lambda settings=None: {k: v for k, v in entries.items()
                                              if k == "generic" or k in ids}
    return mod


def test_a_registry_id_goes_into_the_brief(dirs, monkeypatch):
    """The registry's own ids ("dnd5e") are what every later stage looks up; core.compliance
    does not match them as names, so the brief carries them as they are."""
    inputs, out = dirs
    _registry(monkeypatch)
    write_csv(inputs / "everbee.csv", EVERBEE, _ttrpg_rows(with_system=9))
    doc = _doc(inputs, out, "ttrpg", blocklist=Blocklist.load())
    assert _briefs(doc)["cyberpunk"]["system"] == "dnd5e"


def test_an_id_the_registry_does_not_list_is_never_written(dirs, monkeypatch, caplog):
    inputs, out = dirs
    _registry(monkeypatch, ids=("pf2e",))        # the registry no longer lists dnd5e
    write_csv(inputs / "everbee.csv", EVERBEE, _ttrpg_rows(with_system=9))
    with caplog.at_level("WARNING", logger="research.briefs"):
        doc = _doc(inputs, out, "ttrpg", blocklist=Blocklist.load())
    assert _briefs(doc)["cyberpunk"]["system"] == "generic"
    assert "does not list it" in caplog.text


def test_real_registry_and_blocklist_no_system_name_in_any_brief_string(dirs):
    """config/ttrpg_systems.yaml and config/trademark_blocklist.txt as shipped: the brief
    names the system by its registry id, and no other string of the document holds any
    spelling of a system name, owner or edition (core.compliance.system_mentions)."""
    from core import systems

    inputs, out = dirs
    dnd = systems.detect_systems("Character Sheet for D&D 5e")
    pf = systems.detect_systems("Pathfinder 2e NPC Tracker")
    assert len(dnd) == 1 and len(pf) == 1 and dnd != pf
    rows = [_row(f"Cyberpunk Character Sheet for D&D 5e, Printable PDF, Set {i}", SHOPS[i % 3],
                 tags="dnd5e character sheet,wotc character sheet,cyberpunk character sheet for d&d,"
                      "dungeonsanddragons sheet,5e compatible cyberpunk sheet")
            for i in range(12)]
    rows += [_row(f"Dark Fantasy NPC Tracker, Pathfinder 2e RPG, Set {i}", SHOPS[i % 3],
                  tags="pf2e npc tracker,dark fantasy npc tracker for pathfinder,paizo rpg")
             for i in range(12)]
    rows += [_row(f"Cosmic Horror RPG Session Notes, Call of Cthulhu 7e, Set {i}", SHOPS[i % 3],
                  tags="coc7e session notes,callofcthulhu rpg,cosmic horror rpg for chaosium")
             for i in range(12)]
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    bl = Blocklist.load()
    doc = _doc(inputs, out, "ttrpg", blocklist=bl)
    briefs = _briefs(doc)
    assert briefs["cyberpunk"]["system"] == dnd[0] and briefs["dark fantasy"]["system"] == pf[0]
    assert {"cyberpunk", "dark fantasy", "cosmic horror"} <= set(briefs)
    assert "cyberpunk character sheet" in briefs["cyberpunk"]["keywords"]
    ids = {b["system"] for b in doc["briefs"]}
    others = [s for b in doc["briefs"] for k, v in b.items() if k != "system"
              for s in iter_strings({k: v})]
    others += [s for k, v in doc.items() if k != "briefs" for s in iter_strings({k: v})]
    for s in others:
        assert not system_mentions([s]), s
        assert not bl.check(s).blocked, s
        assert s.split()[-1:] != ["for"] and s.split()[:1] != ["compatible"], s
    assert not bl.check_obj(doc).blocked
    assert ids <= {*systems.load_systems(), "generic"}


def test_a_system_spelling_the_cut_misses_is_still_never_used(dirs, monkeypatch, caplog):
    """Safety net: with research's own name list empty (only the blocklist's `!` terms), the
    registry spelling "CoC 7e" (no `!` line: it would match the id coc7e) is not cut, yet
    core.compliance.system_mentions still keeps it out of every theme and keyword."""
    from research import pipeline

    inputs, out = dirs
    monkeypatch.setattr(pipeline, "game_system_names", lambda settings: {})
    rows = [_row(f"Cyberpunk Character Sheet, CoC 7e Style, Printable, Set {i}", SHOPS[i % 3],
                 tags="coc 7e character sheet,cyberpunk character sheet") for i in range(12)]
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    with caplog.at_level("WARNING", logger="research.briefs"):
        doc = _doc(inputs, out, "ttrpg", blocklist=Blocklist.load(), history_dir=dirs[1] / "hist")
    assert "cyberpunk" in _briefs(doc) and "still named a game system" in caplog.text
    snaps = list((dirs[1] / "hist").rglob("ttrpg.json"))
    assert snaps                                  # the history the search list reads
    for s in [*iter_strings(doc), *(p.read_text(encoding="utf-8") for p in snaps)]:
        assert "coc 7e" not in s.lower(), s


def test_trick_or_treat_keywords_stay_but_medical_words_never_do(dirs):
    """The listing validator allows "trick or treat" (listing medical_ok_phrases), so research
    keeps such keywords; "treat" alone and "healing ..." never reach a brief."""
    inputs, out = dirs
    rows = [_row(f"Trick or Treat Halloween Coloring Pages, Set {i}", SHOPS[i % 3],
                 tags="trick or treat halloween coloring,halloween coloring,healing halloween,"
                      "halloween treat coloring")
            for i in range(12)]
    rows += [_row(f"Sweet Treats Coloring Pages, Set {i}", SHOPS[i % 3]) for i in range(12)]
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    doc = _doc(inputs, out, "coloring_pages", settings=pinned_settings())
    briefs = _briefs(doc)
    assert "trick or treat halloween coloring" in briefs["halloween"]["keywords"]
    assert "sweet treats" in briefs
    names = {t["name"] for t in doc["patterns"]["themes"]}
    assert not {"treat", "treats", "trick"} & names
    for s in iter_strings(doc):
        assert "healing" not in s and "halloween treat" not in s, s
    # The listing stage's own list decides: emptied there, research keeps no such keyword.
    settings = pinned_settings()
    settings.setdefault("listing", {})["medical_ok_phrases"] = []
    doc = _doc(inputs, out, "coloring_pages", settings=settings)
    assert not any("treat" in s for s in iter_strings(doc))


def test_medical_words_match_the_listing_validator():
    """Research cuts at least every word the listing validator refuses, and keeps the same
    ordinary phrases (listing/__init__.py DEFAULTS)."""
    from listing import DEFAULTS as LISTING
    from research.defaults import MEDICAL_OK_PHRASES, medical_ok_phrases

    research = {clean(t) for t in DEFAULTS["medical_terms"]}
    assert {clean(t) for t in LISTING["medical_terms"]} <= research
    assert {clean(p) for p in LISTING["medical_ok_phrases"]} <= {clean(p) for p in MEDICAL_OK_PHRASES}
    assert medical_ok_phrases({}) == list(MEDICAL_OK_PHRASES)
    assert medical_ok_phrases({"listing": {"medical_ok_phrases": ["sweet treat"]}}) == ["sweet treat", "sweet treats"]
    # Settings can drop an exemption, never add one (the listing stage reads the key the same way).
    loose = {"listing": {"medical_ok_phrases": ["Treat Bag", "anxiety treatment", "therapy journal"]}}
    assert medical_ok_phrases(loose) == ["treat bag", "treat bags"]
    assert medical_ok_phrases({"listing": {"medical_ok_phrases": []}}) == []


def test_system_terms_never_reach_other_niches(dirs, monkeypatch):
    inputs, out = dirs
    _fake_systems(monkeypatch, _detect_srd)
    rows = [_row(f"D&D Dragon Coloring Pages, Pathfinder Fan Art, Set {i}", SHOPS[i % 3],
                 tags="dnd coloring,pathfinder dragon,dragon coloring") for i in range(12)]
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    bl = systems_blocklist()
    doc = _doc(inputs, out, "coloring_pages", blocklist=bl)
    brief = _briefs(doc)["dragon"]
    assert "system" not in brief and brief["needs_review"] is False
    _no_system_text(doc, bl)


def test_keyword_rows_cut_system_names(dirs, monkeypatch):
    inputs, out = dirs
    _fake_systems(monkeypatch, _detect_srd)
    mods = ["", "printable", "easy", "cute", "simple", "big", "bold", "fun", "free", "new"]
    rows = [[f"{m} dnd cyberpunk character sheet".strip(), "900", "300", "25%", "1,500", "N/A"]
            for m in mods]
    write_csv(inputs / "erank-keywords.csv", ERANK_KW, rows)
    bl = systems_blocklist()
    doc = _doc(inputs, out, "ttrpg", blocklist=bl)
    brief = _briefs(doc)["cyberpunk"]
    assert brief["system"] == "srd5" and brief["evidence"]["keyword_rows"] == 10
    assert "cyberpunk character sheet" in brief["keywords"]
    _no_system_text(doc, bl)


# ---------------------------------------------------------------- opportunity

def _kw(theme, competition, volume, n):
    mods = ["", "printable", "easy", "cute", "simple", "big", "bold", "fun", "free", "new",
            "best", "mini"]
    return [[f"{mods[i]} {theme} coloring pages".strip(), volume, "300", "25%", competition, "N/A"]
            for i in range(n)]


def test_low_competition_beats_high_competition_at_equal_demand(dirs):
    inputs, out = dirs
    rows = (_kw("llama", "50,000", "1,200", 12) + _kw("sloth", "80", "1,200", 12)
            + _kw("otter", "100", "250", 10))
    write_csv(inputs / "erank-keywords.csv", ERANK_KW, rows)
    doc = _doc(inputs, out, "coloring_pages", settings=pinned_settings())
    order = [b["theme"] for b in doc["briefs"]]
    assert order == ["sloth", "llama", "otter"]
    b = _briefs(doc)
    assert b["sloth"]["evidence"]["score"] == b["llama"]["evidence"]["score"]   # same demand
    assert b["sloth"]["evidence"]["competition"] < b["llama"]["evidence"]["competition"]
    assert b["sloth"]["evidence"]["opportunity"] > b["llama"]["evidence"]["opportunity"]
    themes = {t["name"]: t for t in doc["patterns"]["themes"]}
    assert themes["llama"]["competition"] == b["llama"]["evidence"]["competition"]


def test_reviews_mark_a_crowded_theme_without_keyword_data(dirs):
    """Listing rows only: competition is the theme's review-weighted share of the niche's
    listings. Many established reviews raise demand a little, but crowd the theme more."""
    inputs, out = dirs
    rows = (_rows("Walrus Coloring Pages", 12, reviews=2000)
            + _rows("Narwhal Coloring Pages", 12, reviews=1))
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    doc = _doc(inputs, out, "coloring_pages", settings=pinned_settings())
    b = _briefs(doc)
    assert b["walrus"]["evidence"]["score"] > b["narwhal"]["evidence"]["score"]
    assert b["walrus"]["evidence"]["competition"] > b["narwhal"]["evidence"]["competition"]
    assert [x["theme"] for x in doc["briefs"]] == ["narwhal", "walrus"]


def test_opportunity_never_lifts_a_theme_past_the_support_rules(dirs):
    inputs, out = dirs
    rows = _kw("sloth", "15", "9,000", 9) + _kw("llama", "50,000", "100", 10)
    write_csv(inputs / "erank-keywords.csv", ERANK_KW, rows)
    doc = _doc(inputs, out, "coloring_pages", settings=pinned_settings())
    assert [b["theme"] for b in doc["briefs"]] == ["llama"]   # 9 rows: not a pattern


def test_opportunity_weights_are_renormalized(dirs):
    inputs, out = dirs
    write_csv(inputs / "erank-keywords.csv", ERANK_KW,
              _kw("llama", "50,000", "1,200", 12) + _kw("sloth", "80", "1,200", 12))
    settings = pinned_settings()
    settings["research"]["opportunity_weights"] = {"demand": 1.0, "sales_per_listing": 0,
                                                   "low_competition": 0}
    doc = _doc(inputs, out, "coloring_pages", settings=settings)
    for b in doc["briefs"]:   # demand only: opportunity is the demand score
        assert b["evidence"]["opportunity"] == b["evidence"]["score"]


# ---------------------------------------------------------------- pack sizes

def _counted(theme, count, n, *, sales, shops=SHOPS, **kw):
    return _rows(f"{theme} Coloring Pages, {count} Printable Pages", n, shops=shops, sales=sales, **kw)


def test_top_seller_pack_size_joins_the_base_packs(dirs):
    inputs, out = dirs
    rows = _counted("Octopus", 30, 6, sales=100) + _counted("Octopus", 10, 6, sales=5)
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    brief = _briefs(_doc(inputs, out, "coloring_pages"))["octopus"]
    assert brief["pack_sizes"] == [5, 20, 30] and brief["page_count"] == 30
    assert brief["evidence"]["top_seller_sizes"] == [30]


def test_second_size_needs_thirty_percent(dirs):
    inputs, out = dirs
    rows = (_counted("Octopus", 30, 6, sales=100) + _counted("Octopus", 12, 4, sales=100)
            + _counted("Octopus", 50, 6, sales=3))
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    brief = _briefs(_doc(inputs, out, "coloring_pages"))["octopus"]
    assert brief["pack_sizes"] == [5, 12, 20, 30]           # 12 has 40% of the top sellers

    rows = (_counted("Narwhal", 30, 8, sales=100) + _counted("Narwhal", 12, 3, sales=100)
            + _counted("Narwhal", 50, 6, sales=3))
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    brief = _briefs(_doc(inputs, out, "coloring_pages"))["narwhal"]
    assert brief["pack_sizes"] == [5, 20, 30]               # 12 has 27%: not used


def test_top_seller_size_from_fewer_than_three_shops_is_ignored(dirs):
    inputs, out = dirs
    two = ("ShopOne", "ShopTwo")
    rows = (_counted("Octopus", 30, 6, sales=100, shops=two)
            + _counted("Octopus", 10, 6, sales=5))           # the theme itself has 5 shops
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    brief = _briefs(_doc(inputs, out, "coloring_pages"))["octopus"]
    assert brief["evidence"]["shop_count"] == 5
    assert brief["pack_sizes"] == [5, 20] and brief["evidence"]["top_seller_sizes"] == []


def test_top_sellers_need_five_rows_and_use_favorites_without_sales(dirs):
    inputs, out = dirs
    rows = _counted("Octopus", 30, 4, sales=100) + _rows("Octopus Coloring Pages", 8, sales=1)
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    brief = _briefs(_doc(inputs, out, "coloring_pages"))["octopus"]
    assert brief["pack_sizes"] == [5, 20]                   # only 4 rows state a count

    rows = (_counted("Narwhal", 40, 6, sales="", favorites="9,000")
            + _counted("Narwhal", 10, 6, sales="", favorites="50"))
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    brief = _briefs(_doc(inputs, out, "coloring_pages"))["narwhal"]
    assert brief["pack_sizes"] == [5, 20, 40]               # ranked by favorites


def test_pack_sizes_stay_within_the_niche_bounds(dirs):
    inputs, out = dirs
    rows = (_counted("Octopus", 80, 6, sales=100) + _counted("Octopus", 10, 6, sales=5)
            + _counted("Walrus", 2, 6, sales=100) + _counted("Walrus", 10, 6, sales=5))
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    briefs = _briefs(_doc(inputs, out, "coloring_pages"))
    assert briefs["octopus"]["pack_sizes"] == [5, 20, 50]   # coloring_pages max 50
    assert briefs["walrus"]["pack_sizes"] == [5, 20]        # 2 -> min_size 5
    settings = five_niche_settings()
    settings["packs"] = {"min_size": 8, "max_size": 15}
    briefs = _briefs(_doc(inputs, out, "coloring_pages", settings=settings))
    assert briefs["octopus"]["pack_sizes"] == [8, 15] and briefs["octopus"]["page_count"] == 15


def test_editions_are_not_top_seller_sizes(dirs, monkeypatch):
    inputs, out = dirs
    _fake_systems(monkeypatch, _detect_srd)
    rows = [_row(f"Cyberpunk Character Sheets for D&D 5e, Set {i}", SHOPS[i % 3], sales=100 - i)
            for i in range(12)]
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    brief = _briefs(_doc(inputs, out, "ttrpg", blocklist=systems_blocklist()))["cyberpunk"]
    assert brief["evidence"]["top_seller_sizes"] == [] and brief["pack_sizes"] == [5, 20]


def test_pack_bounds_settings():
    assert pack_bounds({}, "ttrpg") == (5, 40) and pack_bounds({}, "stickers") == (5, 60)
    assert pack_bounds({}, "unknown_niche") == (5, PACK_BOUNDS["default_max_size"])
    assert pack_bounds({"packs": {"max_size": 30}}, "stationery") == (5, 30)
    assert pack_bounds({"packs": {"max_size": {"ttrpg": 24}}}, "ttrpg") == (5, 24)
    assert pack_bounds({"packs": {"max_size": {"ttrpg": 24}}}, "stickers") == (5, 60)
    assert pack_bounds({"packs": {"min_size": None, "max_size": None}}, "ttrpg") == (5, 40)
    assert pack_bounds({"packs": {"min_size": 0, "max_size": 3}}, "ttrpg") == (1, 3)
    assert pack_bounds({"packs": {"min_size": 30, "max_size": 10}}, "ttrpg") == (30, 30)
    # Values that are not whole numbers mean the defaults; numbers typed as text work.
    assert pack_bounds({"packs": {"min_size": "x", "max_size": {"ttrpg": "lots"}}}, "ttrpg") == (5, 40)
    assert pack_bounds({"packs": {"min_size": "8", "max_size": "30"}}, "stationery") == (8, 30)
    assert pack_bounds({"packs": {"max_size": {"ttrpg": 12.5}}}, "ttrpg") == (5, 40)


def test_base_pack_sizes_from_settings_never_crash_the_run():
    from research.briefs import brief_pack_sizes

    assert brief_pack_sizes("coloring_pages", [30], {}, {"base_pack_sizes": [5, "x", 20]}) == [5, 20, 30]
    assert brief_pack_sizes("stickers", [12], {}, {"base_pack_sizes": ["8", 30.0]}) == [8, 12, 30]
    for junk in ("lots", [], [0, -3], None):              # no usable size: the PACKS.md defaults
        assert brief_pack_sizes("stickers", [], {}, {"base_pack_sizes": junk}) == [5, 20], junk
    assert brief_pack_sizes("ttrpg", [80], {}, {"base_pack_sizes": [5, 20]}) == [5, 20, 40]


# ---------------------------------------------------------------- whole runs

def test_five_niche_demo_run(tmp_path, monkeypatch):
    _fake_systems(monkeypatch, _detect_srd)
    write_demo_inputs(tmp_path / "in", niches=DEMO_NICHES)
    paths = _run(tmp_path / "in", tmp_path / "out")
    assert sorted(p.name for p in paths) == sorted(f"{n}-{DATE}.json" for n in DEMO_NICHES)
    for p in paths:
        doc = load(p)
        n = doc["niche"]
        assert len(doc["briefs"]) == 5, n
        opp = [b["evidence"]["opportunity"] for b in doc["briefs"]]
        assert opp == sorted(opp, reverse=True), n
        for b in doc["briefs"]:
            assert b["pack_sizes"] == sorted(set(b["pack_sizes"])) and b["page_count"] == max(b["pack_sizes"])
            assert {5, 20} <= set(b["pack_sizes"]) and max(b["pack_sizes"]) <= pack_bounds({}, n)[1]
            assert ("system" in b) == (n == "ttrpg")
            assert b["audience"] == DEFAULTS["audience"][n]
            assert b["style_notes"] == DEFAULTS["style_notes"][n]
    stationery = load(next(p for p in paths if p.name.startswith("stationery")))
    assert {b["theme"] for b in stationery["briefs"]} == {"adhd", "minimalist", "cottagecore",
                                                          "botanical", "student"}
    ttrpg = load(next(p for p in paths if p.name.startswith("ttrpg")))
    assert {b["theme"] for b in ttrpg["briefs"]} == {"classic fantasy", "dark fantasy", "sci fi",
                                                     "cosmic horror", "cyberpunk"}
    # The demo's "classic fantasy" rows mostly name a system ("for D&D 5e"); "dark fantasy"
    # rows rarely do, so only the first brief names one (by id).
    systems = {b["theme"]: b["system"] for b in ttrpg["briefs"]}
    assert systems.pop("classic fantasy") == "srd5"
    assert set(systems.values()) == {"generic"}
    _no_system_text(ttrpg, systems_blocklist())


def test_default_demo_keeps_its_three_niches(tmp_path):
    write_demo_inputs(tmp_path / "in")
    paths = _run(tmp_path / "in", tmp_path / "out")
    assert sorted(p.name.split("-")[0] for p in paths) == sorted(DEFAULT_NICHES)
    with pytest.raises(ValueError, match="no demo data"):
        write_demo_inputs(tmp_path / "x", niches=("planners",))


def test_demo_has_no_brand_text_and_names_systems_only_in_ttrpg_rows(tmp_path):
    """The demo names no brand; only ttrpg rows name a game system (as competitor titles do)."""
    bl = Blocklist.load()
    systems = set(bl.system_terms)
    for p in write_demo_inputs(tmp_path, niches=DEMO_NICHES):
        for line in p.read_text(encoding="utf-8").splitlines():
            found = bl.check(line)
            assert not found.flagged and set(found.blocked) <= systems, line
            if found.blocked:
                assert any(t in line.lower() for t in THEMES["ttrpg"]), line
    text = (tmp_path / "everbee-product-analytics-demo.csv").read_text(encoding="utf-8")
    assert "for D&D 5e" in text


def test_new_niche_docs_pass_the_brief_check_with_system_terms(dirs, monkeypatch):
    """Every new-niche document passes core.briefs with `!` terms counted as blocked."""
    inputs, out = dirs
    _fake_systems(monkeypatch, _detect_srd)
    rows = (_ttrpg_rows(with_system=12)
            + [_row(f"ADHD Daily Planner Pages for D&D Fans, 20 Pages, Set {i}", SHOPS[i % 3],
                    tags="adhd planner,dnd planner") for i in range(12)])
    write_csv(inputs / "everbee.csv", EVERBEE, rows)
    bl = systems_blocklist()
    paths = _run(inputs, out, blocklist=bl)
    assert sorted(p.name.split("-")[0] for p in paths) == ["stationery", "ttrpg"]
    for p in paths:
        doc = load(p)
        assert doc["briefs"]
        _no_system_text(doc, bl)
        assert "dnd" not in json.dumps(doc).lower()
