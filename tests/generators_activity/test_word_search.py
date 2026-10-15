"""Word search placement, uniqueness, answer key, and the theme word pool."""
import random

import pytest

from generators.activity_sheets import vocab, word_search
from generators.activity_sheets.layout import Box, Style
from generators.activity_sheets.word_search import (
    DIRECTIONS, Placement, _valid_placements, clean_word_list, find_all, make_word_search,
)

WORDS = ["cow", "pig", "horse", "sheep", "goat", "chicken", "duck", "tractor", "barn", "farmer"]


@pytest.mark.parametrize("directions, max_size", [
    (["right", "down"], 10),
    (["right", "down", "down_right"], 13),
    (list(DIRECTIONS), 15),
])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_every_word_is_placed_once_in_allowed_directions(directions, max_size, seed):
    ws = make_word_search(WORDS, directions, max_size, random.Random(seed))
    assert ws.size <= max_size and len(ws.grid) == ws.size
    assert all(len(row) == ws.size and row.isalpha() and row.isupper() for row in ws.grid)
    assert sorted(p.word for p in ws.placements) == sorted(clean_word_list(WORDS, max_size))
    for p in ws.placements:
        assert p.direction in directions
        assert "".join(ws.grid[r][c] for r, c in p.cells()) == p.word
        assert find_all(ws.grid, p.word, directions) == [p]      # exactly one occurrence


def test_easy_uses_only_right_and_down():
    ws = make_word_search(WORDS, ["right", "down"], 10, random.Random(5))
    assert {p.direction for p in ws.placements} <= {"right", "down"}


def test_word_bank_is_alphabetical_uppercase():
    ws = make_word_search(["zebra", "ant", "moose"], ["right", "down"], 10, random.Random(0))
    assert ws.words == ["ANT", "MOOSE", "ZEBRA"]


def test_placement_rejects_collisions_and_hidden_words():
    size = 5
    cells = [[""] * size for _ in range(size)]
    for c, ch in enumerate("CATS"):
        cells[0][c] = ch
    opts = _valid_placements("CAR", cells, size, ["right", "down"])
    assert Placement("CAR", 0, 0, "right") not in opts            # R would overwrite T
    assert Placement("CAR", 0, 0, "down") in opts                 # shares the C: allowed
    hidden = _valid_placements("CAT", cells, size, ["right"])
    assert Placement("CAT", 0, 0, "right") not in hidden          # fully inside CATS: rejected


def test_clean_word_list_filters_and_drops_substrings():
    out = clean_word_list(["cat", "Catfish", "a1b", "ox", "hippopotamus", "dog", "DOG", "frog"], 10)
    assert out == ["CAT", "DOG", "FROG"]


def test_answer_key_circles_exactly_the_placed_words(monkeypatch):
    ws = make_word_search(WORDS, ["right", "down", "down_right"], 13, random.Random(9))
    calls = []
    monkeypatch.setattr(word_search, "capsule", lambda c, p, q, r, stroke, fill: calls.append((p, q, stroke)))

    class FakeCanvas:
        def __getattr__(self, name):
            return lambda *a, **k: None

    geo = word_search.draw_word_search(FakeCanvas(), Box(0, 0, 500, 500), ws, Style(), solution=True,
                                       word_bank=False)
    cell, (ox, top) = geo["cell"], geo["origin"]

    def center(r, c):
        return ox + (c + 0.5) * cell, top - (r + 0.5) * cell

    expected = {(center(*p.cells()[0]), center(*p.cells()[-1])) for p in ws.placements}
    outlines = {(p, q) for p, q, stroke in calls if stroke}
    assert outlines == expected


def test_theme_words_are_kid_friendly_and_blocklist_clean(blocklist):
    pool = vocab.theme_words("Frozen winter Elsa", ["paw patrol snow", "cars and trucks"], blocklist)
    assert pool, "pool must not be empty"
    for w in pool:
        assert w.isalpha() and w.isascii() and 3 <= len(w) <= 10
        assert blocklist.check(w).ok and not blocklist.check(w).needs_review
    assert not {"frozen", "elsa", "cars"} & set(pool)
    assert "snow" in pool and "snowman" in pool                     # winter topic matched


def test_theme_words_prefer_the_best_topic(blocklist):
    pool = vocab.theme_words("farm animals", ["barn animals"], blocklist)
    assert "cow" in pool and "tractor" in pool
    assert "giraffe" not in pool                                     # weaker "animals" topic not needed
    assert not set(pool) & vocab.STOPWORDS


def test_unknown_theme_falls_back_to_generic_words(blocklist):
    pool = vocab.theme_words("robots", [], blocklist)
    assert pool[0] == "robots" and len(pool) >= 12


def test_builtin_vocabulary_is_clean(blocklist):
    words = [w for t in vocab.VOCABULARY.values() for w in t.words] + list(vocab.GENERIC)
    assert [w for w in words if not vocab.is_kid_word(w, blocklist)] == []
    for name in ("animals", "ocean", "farm", "space", "dinosaurs", "spring", "summer", "autumn", "winter",
                 "halloween", "christmas", "bugs", "food", "vehicles"):
        assert name in vocab.VOCABULARY


def test_unknown_direction_is_an_error():
    with pytest.raises(ValueError):
        make_word_search(WORDS, ["sideways"], 10, random.Random(0))


def test_palindromes_count_once_when_words_can_run_backwards():
    words = ["kayak", "level", "noon", "radar", "cow", "sheep"]
    ws = make_word_search(words, list(DIRECTIONS), 12, random.Random(3))
    assert sorted(p.word for p in ws.placements) == sorted(w.upper() for w in words)
    occ = word_search.occurrences(ws.grid, [p.word for p in ws.placements], list(DIRECTIONS))
    assert all(len(cells) == 1 for cells in occ.values()), occ
    kayak = next(p for p in ws.placements if p.word == "KAYAK")
    both_ways = find_all(ws.grid, "KAYAK", list(DIRECTIONS))
    assert len(both_ways) == 2 and {frozenset(p.cells()) for p in both_ways} == {frozenset(kayak.cells())}


def test_reversed_overlaps_are_dropped_only_when_backwards_is_allowed():
    assert clean_word_list(["star", "rats", "tar"], 10) == ["STAR", "RATS"]
    assert clean_word_list(["star", "rats", "tar"], 10, backwards=True) == ["STAR"]
    assert word_search.reversible(["right", "left"]) and not word_search.reversible(["right", "down", "down_right"])


@pytest.mark.parametrize("level", ["easy", "medium", "hard"])
def test_builtin_topics_give_unambiguous_puzzles_with_every_word(level, blocklist):
    """Regression: about 8% of hard puzzles used to contain a listed word twice."""
    from generators.activity_sheets import DEFAULTS

    w = DEFAULTS["levels"][level]["word_search"]
    for topic in vocab.VOCABULARY:
        pool = vocab.theme_words(topic, [], blocklist, min_pool=w["words"] + 4)
        words = clean_word_list(pool, w["max_size"], word_search.reversible(w["directions"]))[: w["words"]]
        for seed in (0, 1):
            ws = make_word_search(words, w["directions"], w["max_size"], random.Random(seed))
            assert len(ws.placements) == len(words), (topic, seed)
            occ = word_search.occurrences(ws.grid, words, w["directions"])
            assert all(len(cells) == 1 for cells in occ.values()), (topic, seed)


def test_overfull_word_list_falls_back_without_ambiguous_words():
    rng = random.Random(0)
    words = ["".join(rng.choice("ABCDEFGHIJ") for _ in range(8)) for _ in range(14)]
    ws = make_word_search(words, ["right"], 8, random.Random(0))
    assert 1 <= len(ws.placements) < len(clean_word_list(words, 8))
    occ = word_search.occurrences(ws.grid, [p.word for p in ws.placements], ["right"])
    assert all(len(cells) == 1 for cells in occ.values())
