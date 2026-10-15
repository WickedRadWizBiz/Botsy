"""core/: blocklist matching, URL detection, AI disclosure, item helpers."""
import pytest

from core import compliance, config, items
from core.compliance import Blocklist


@pytest.fixture(scope="module")
def bl():
    return Blocklist.load()


def test_blocklist_loads_hard_and_review_terms(bl):
    hard = {t for t, _ in bl.hard}
    review = {t for t, _ in bl.review}
    assert "Disney" in hard and "Paw Patrol" in hard
    assert "Winnie the Pooh" in review and "Winnie the Pooh" not in hard


@pytest.mark.parametrize("text, term", [
    ("PAW PATROL coloring book", "Paw Patrol"),
    ("pawpatrol stickers", "Paw Patrol"),
    ("paw-patrol party", "Paw Patrol"),
    ("spider man mask", "Spider-Man"),
    ("Pokémon cards", "Pokemon"),
    ("the minions' goggles", "Minions"),
    ("barbies dream house", "Barbie"),
    ("wheres waldo puzzle", "Where's Waldo"),
])
def test_blocklist_matches_variants(bl, text, term):
    assert term in bl.check(text).blocked


@pytest.mark.parametrize("text", [
    "farm animal coloring pages",
    "dinosaur maze for kids",
    "Elsewhere in the forest",   # 'Elsa' must not match inside a longer word
    "barbecue party stickers",  # 'Barbie' must not match 'barbecue'
])
def test_blocklist_no_false_positive_inside_words(bl, text):
    assert bl.check(text).ok


def test_review_terms_flag_not_block(bl):
    r = bl.check("Peter Rabbit garden coloring page")
    assert r.ok and r.needs_review and r.flagged == ["Peter Rabbit"]


def test_check_obj_walks_nested(bl):
    r = bl.check_obj({"briefs": [{"keywords": ["cute", "Hello Kitty bow"]}]})
    assert r.blocked == ["Hello Kitty"]


def test_find_urls():
    assert compliance.find_urls({"a": ["see https://x.example/y", "etsy.com/listing/1"]})
    assert not compliance.find_urls({"a": "farm animals, 20 pages, e.g. cows"})


def test_ai_disclosure_roundtrip():
    text = compliance.load_ai_disclosure("Sunny Prints")
    assert "Sunny Prints" in text and "{shop_name}" not in text
    desc = "Twenty pages.\n\n" + text.replace(" ", "  ")
    assert compliance.has_ai_disclosure(desc, "Sunny Prints")
    assert not compliance.has_ai_disclosure("Twenty pages.", "Sunny Prints")


def test_item_ids_deterministic_and_slugged():
    a = items.new_item_id("2026-10-15", "stickers", "Cute Frogs & Toads!")
    assert a == items.new_item_id("2026-10-15", "stickers", "Cute Frogs & Toads!")
    assert a.startswith("2026-10-15-stickers-cute-frogs-toads-")
    assert a != items.new_item_id("2026-10-15", "stickers", "Cute Frogs & Toads!", salt="2")


def test_manifest_roundtrip_and_status(tmp_path):
    d = tmp_path / "item1"
    items.write_manifest(d, {"item_id": "item1", "status": "generated"})
    assert [p.name for p in items.iter_item_dirs(tmp_path)] == ["item1"]
    assert items.set_status(d, "qa_passed")["status"] == "qa_passed"
    with pytest.raises(ValueError):
        items.set_status(d, "active")


def test_section_merges_defaults():
    s = {"qa": {"min_dpi": 300, "nested": {"b": 2}}}
    merged = config.section(s, "qa", {"min_dpi": 150, "max": 5, "nested": {"a": 1}})
    assert merged == {"min_dpi": 300, "max": 5, "nested": {"a": 1, "b": 2}}
    assert config.section(s, "missing", {"x": 1}) == {"x": 1}


def test_require_env_missing(monkeypatch):
    monkeypatch.delenv("ETSY_AGENTS_TEST_UNSET", raising=False)
    with pytest.raises(config.MissingSecretError):
        config.require_env("ETSY_AGENTS_TEST_UNSET")
