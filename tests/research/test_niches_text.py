"""Niche assignment and theme text processing."""
import pytest

from research.defaults import DEFAULTS
from research.niches import assign_niche, compile_rules, niche_from_filename
from research.text import TextRules, formats, pack_count

RULES = compile_rules(DEFAULTS["niche_rules"])
NICHES = ["stickers", "coloring_pages", "activity_sheets"]


@pytest.mark.parametrize("text, niche", [
    ("Farm Animals Coloring Pages for Kids", "coloring_pages"),
    ("Colouring Book PDF", "coloring_pages"),
    ("Dinosaur Mazes for Kids", "activity_sheets"),
    ("Ocean Word Search Printable", "activity_sheets"),
    ("Alphabet Tracing Worksheets", "activity_sheets"),
    ("Space Dot-to-Dot", "activity_sheets"),
    ("Cute Frog Digital Stickers", "stickers"),
    ("Mushroom clipart png bundle", "stickers"),
    ("Coloring and Activity Book", "coloring_pages"),   # tie -> first listed of the two
    ("Wedding invitation template", None),
])
def test_assign_niche(text, niche):
    assert assign_niche(text, RULES) == niche


def test_filename_prefix():
    assert niche_from_filename("coloring_pages__erank.csv", NICHES) == "coloring_pages"
    assert niche_from_filename("coloring-pages__x.csv", NICHES) == "coloring_pages"
    assert niche_from_filename("planners__x.csv", NICHES) is None
    assert niche_from_filename("erank.csv", NICHES) is None


def test_grams_drop_stopwords_and_split(blocklist):
    rules = TextRules(excluded_terms=DEFAULTS["excluded_terms"], blocklist=blocklist)
    g = rules.grams("Farm Animals Coloring Pages for Kids, 20 Printable Pages | PDF Instant Download")
    assert set(g.keys.values()) == {"farm", "animals", "farm animals"}
    assert rules.grams("GoodNotes Frog Stickers").keys == {("frog",): "frog"}


def test_grams_cut_blocked_terms_and_mark_rows(blocklist):
    rules = TextRules(blocklist=blocklist)
    g = rules.grams("Bluey Family Coloring Pages")
    assert g.blocked and set(g.keys.values()) == {"family"}
    g = rules.grams("Snow White Coloring Pages")
    assert g.flagged and "snow white" in g.keys.values()


def test_formats_and_pack_counts():
    assert formats("Printable PDF, A4 + US Letter, instant download") == {
        "printable", "pdf", "a4", "us letter", "digital download"}
    assert pack_count("Dinosaur Coloring Pages, 25 Printable Pages") == 25
    assert pack_count("Set of 12 frog stickers") == 12
    assert pack_count("Coloring pages for ages 4-8") is None
