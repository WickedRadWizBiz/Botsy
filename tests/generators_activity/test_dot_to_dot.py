"""Dot-to-dot: shape library, resampling, sequential numbering, labels clear of lines and dots."""
import math
import random

import pytest

from generators.activity_sheets import DEFAULTS
from generators.activity_sheets.dot_to_dot import (
    _circle_hits_rect, _rects_overlap, layout_picture, make_picture, path_segments, resample,
    segment_hits_rect,
)
from generators.activity_sheets.layout import Box, PageGeometry, Style
from generators.activity_sheets.shapes import SHAPES, SHAPES_BY_NAME, rank_shapes

REQUIRED = {"star", "heart", "house", "fish", "rocket", "butterfly", "cat", "tree", "sailboat", "balloon",
            "apple", "sun"}
PAGES = [PageGeometry("us_letter", 612.0, 792.0, 36.0), PageGeometry("a4", 595.44, 841.68, 36.0)]


def test_library_has_required_shapes_in_unit_square():
    assert len(SHAPES) >= 10 and REQUIRED <= set(SHAPES_BY_NAME)
    for s in SHAPES:
        assert len(s.outline) >= 3 and s.tags
        for x, y in list(s.outline) + [p for d in s.details for p in d]:
            assert -0.1 <= x <= 1.1 and -0.1 <= y <= 1.1, s.name


@pytest.mark.parametrize("n", [20, 35, 50])
def test_resample_hits_target_and_starts_at_first_point(n):
    for s in SHAPES:
        dots = resample(list(s.outline), n, s.closed)
        assert len(dots) >= n and dots[0] == s.outline[0], s.name
        if s.name not in ("sun", "butterfly"):        # these have more corners than 20 dots
            assert len(dots) == n, s.name


def test_resample_keeps_sharp_corners():
    star = SHAPES_BY_NAME["star"]
    dots = resample(list(star.outline), 20, True)
    for corner in star.outline:
        assert any(math.dist(corner, d) < 1e-9 for d in dots)


def test_open_outline_keeps_both_ends():
    line = [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0)]
    dots = resample(line, 5, closed=False)
    assert dots[0] == (0.0, 0.0) and dots[-1] == (1.0, 0.0) and len(dots) == 5


@pytest.mark.parametrize("level", ["easy", "medium", "hard"])
@pytest.mark.parametrize("geo", PAGES, ids=lambda g: g.name)
def test_numbers_sequential_in_bounds_and_collision_free(level, geo):
    style = Style()
    box = geo.content.split_top(98)[1]           # below a page header
    n = DEFAULTS["levels"][level]["dot_to_dot"]["dots"]
    for shape in SHAPES:
        lay = layout_picture(make_picture(shape, n), box, style)
        assert [lab.number for lab in lay.labels] == list(range(1, len(lay.dots) + 1)), shape.name
        for x, y in lay.dots:
            assert box.contains(x, y) and geo.page_box.contains(x, y), shape.name
        segs = path_segments(lay.dots, lay.closed) + [s for d in lay.details for s in zip(d, d[1:])]
        for k, lab in enumerate(lay.labels):
            assert box.contains(lab.x, lab.y) and box.contains(lab.x + lab.w, lab.y + lab.h), (shape.name, k)
            assert lab.collisions == 0, (shape.name, lab.number)
            assert not any(segment_hits_rect(a, b, lab.x, lab.y, lab.x + lab.w, lab.y + lab.h) for a, b in segs)
            assert not any(_circle_hits_rect(p, lay.dot_r, lab) for p in lay.dots)
            assert not any(_rects_overlap(lab, o, 0.0) for o in lay.labels[:k])


def test_layout_adapts_to_page_size():
    pic = make_picture(SHAPES_BY_NAME["fish"], 20)
    a, b = (layout_picture(pic, g.content.split_top(98)[1], Style()) for g in PAGES)
    assert a.dots != b.dots                      # recomputed for each page, not one scaled copy


def test_segment_rect_intersection():
    assert segment_hits_rect((0, 0), (10, 10), 4, 4, 6, 6)
    assert not segment_hits_rect((0, 0), (10, 0), 4, 1, 6, 3)
    assert segment_hits_rect((5, -5), (5, 5), 4, -1, 6, 1)


def test_theme_picks_matching_shapes():
    matched, others = rank_shapes(["under", "the", "sea", "fish"])
    assert matched[0].name == "fish" and "sailboat" in {s.name for s in matched}
    assert {s.name for s in matched} | {s.name for s in others} == set(SHAPES_BY_NAME)
    assert rank_shapes(["robots"])[0] == []


def test_better_matches_stay_first_when_ties_are_shuffled():
    for seed in range(20):
        matched, _ = rank_shapes(["ocean", "animals", "sea"], random.Random(seed))
        assert {s.name for s in matched[:2]} == {"fish", "sailboat"}      # score 2 beats cat's 1


def test_box_contains_helper():
    assert Box(0, 0, 10, 10).contains(10, 10) and not Box(0, 0, 10, 10).contains(10.1, 5)
