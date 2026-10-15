"""Built-in dot-to-dot pictures as point lists in a normalized 0..1 square (y up).

`outline` is the path the child draws; its first point becomes dot 1. `details` are
small solid lines drawn for them (eyes, windows) so the finished picture reads well.
`tags` are theme words used to pick shapes that fit a brief. All shapes are generic,
original line art; none depict a character or brand.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True)
class Shape:
    name: str
    outline: tuple[Point, ...]
    closed: bool
    tags: tuple[str, ...]
    details: tuple[tuple[Point, ...], ...] = ()


def _circle(cx: float, cy: float, r: float, n: int = 16, a0: float = 90.0, a1: float = 450.0) -> list[Point]:
    """Points on an arc from a0 to a1 degrees (inclusive), counter-clockwise when a1 > a0."""
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * k / n))) for k in range(n + 1)]


def _ring(cx: float, cy: float, r: float, n: int = 16) -> tuple[Point, ...]:
    """Closed circle as a polyline (last point repeats the first)."""
    return tuple(_circle(cx, cy, r, n))


def _star() -> list[Point]:
    return [(0.5 + (0.5 if k % 2 == 0 else 0.2) * math.cos(math.radians(90 + 36 * k)),
             0.5 + (0.5 if k % 2 == 0 else 0.2) * math.sin(math.radians(90 + 36 * k))) for k in range(10)]


def _heart(n: int = 64) -> list[Point]:
    pts = []
    for k in range(n):
        t = 2 * math.pi * k / n
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((0.5 + x / 34, 0.55 + y / 34))
    return pts


def _sun() -> list[Point]:
    return [(0.5 + (0.5 if k % 2 == 0 else 0.36) * math.cos(math.radians(90 - 15 * k)),
             0.5 + (0.5 if k % 2 == 0 else 0.36) * math.sin(math.radians(90 - 15 * k))) for k in range(24)]


def _flower(n: int = 80) -> list[Point]:
    pts = []
    for k in range(n):
        th = math.pi / 2 - 2 * math.pi * k / n          # start at the top petal, clockwise
        r = 0.12 + 0.22 * abs(math.cos(2.5 * (th - math.pi / 2)))
        pts.append((0.5 + r * math.cos(th), 0.62 + r * math.sin(th)))
    return pts


def _balloon() -> list[Point]:
    body = _circle(0.5, 0.62, 0.3, 28, 90, -75)           # clockwise from the top to the knot
    knot = [(0.55, 0.25), (0.45, 0.25)]
    rest = _circle(0.5, 0.62, 0.3, 28, 255, 90)[:-1]      # back up the left side
    return body + knot + rest


def _moon() -> list[Point]:
    outer = _circle(0.5, 0.5, 0.46, 24, 60, 300)          # tips at 60 and 300 degrees
    cx2, r2 = 0.8305, 0.4105                              # inner arc through both tips
    a = math.degrees(math.atan2(0.5 - 0.102, 0.73 - cx2))
    inner = _circle(cx2, 0.5, r2, 12, 360 - a, a)[1:-1]
    return outer + inner


def _car() -> list[Point]:
    right_arch = _circle(0.74, 0.3, 0.11, 8, 0, 180)
    left_arch = _circle(0.28, 0.3, 0.11, 8, 0, 180)
    return ([(0.33, 0.7), (0.67, 0.7), (0.8, 0.5), (0.93, 0.46), (0.93, 0.3)]
            + right_arch + left_arch + [(0.07, 0.3), (0.07, 0.45), (0.2, 0.5)])


SHAPES: tuple[Shape, ...] = (
    Shape("star", tuple(_star()), True,
          ("star", "space", "night", "sky", "christmas", "galaxy", "celebration")),
    Shape("heart", tuple(_heart()), True,
          ("heart", "love", "valentine", "mother", "wedding", "friendship", "kindness")),
    Shape("house", ((0.5, 0.95), (0.68, 0.79), (0.68, 0.92), (0.78, 0.92), (0.78, 0.70), (0.95, 0.55),
                    (0.85, 0.55), (0.85, 0.05), (0.15, 0.05), (0.15, 0.55), (0.05, 0.55)), True,
          ("house", "home", "farm", "barn", "town", "city", "family", "neighborhood"),
          details=(((0.42, 0.05), (0.42, 0.3), (0.58, 0.3), (0.58, 0.05)),
                   ((0.22, 0.34), (0.34, 0.34), (0.34, 0.46), (0.22, 0.46), (0.22, 0.34)),
                   ((0.66, 0.34), (0.78, 0.34), (0.78, 0.46), (0.66, 0.46), (0.66, 0.34)))),
    Shape("fish", ((0.05, 0.5), (0.12, 0.64), (0.24, 0.74), (0.38, 0.79), (0.52, 0.78), (0.64, 0.71),
                   (0.74, 0.58), (0.96, 0.78), (0.88, 0.5), (0.96, 0.22), (0.74, 0.42), (0.64, 0.29),
                   (0.52, 0.22), (0.38, 0.21), (0.24, 0.26), (0.12, 0.36)), True,
          ("fish", "ocean", "sea", "underwater", "beach", "lake", "pond", "aquarium", "marine", "pet"),
          details=(_ring(0.2, 0.56, 0.03, 10), ((0.33, 0.67), (0.36, 0.5), (0.33, 0.33)))),
    Shape("rocket", ((0.5, 0.97), (0.58, 0.88), (0.63, 0.75), (0.64, 0.4), (0.78, 0.25), (0.78, 0.12),
                     (0.63, 0.2), (0.58, 0.2), (0.6, 0.08), (0.4, 0.08), (0.42, 0.2), (0.37, 0.2),
                     (0.22, 0.12), (0.22, 0.25), (0.36, 0.4), (0.37, 0.75), (0.42, 0.88)), True,
          ("rocket", "space", "astronaut", "planet", "galaxy", "star", "vehicle", "science"),
          details=(_ring(0.5, 0.6, 0.06, 12),)),
    Shape("butterfly", ((0.5, 0.6), (0.6, 0.78), (0.75, 0.92), (0.9, 0.9), (0.95, 0.75), (0.88, 0.6),
                        (0.72, 0.52), (0.85, 0.4), (0.88, 0.25), (0.78, 0.12), (0.64, 0.15), (0.55, 0.3),
                        (0.5, 0.2), (0.45, 0.3), (0.36, 0.15), (0.22, 0.12), (0.12, 0.25), (0.15, 0.4),
                        (0.28, 0.52), (0.12, 0.6), (0.05, 0.75), (0.1, 0.9), (0.25, 0.92), (0.4, 0.78)), True,
          ("butterfly", "bug", "insect", "garden", "spring", "flower", "nature"),
          details=(((0.5, 0.58), (0.5, 0.24)),
                   ((0.49, 0.62), (0.44, 0.76), (0.4, 0.82)), ((0.51, 0.62), (0.56, 0.76), (0.6, 0.82)))),
    Shape("cat", ((0.2, 0.95), (0.38, 0.72), (0.62, 0.72), (0.8, 0.95), (0.84, 0.62), (0.88, 0.45),
                  (0.82, 0.25), (0.67, 0.12), (0.5, 0.08), (0.33, 0.12), (0.18, 0.25), (0.12, 0.45),
                  (0.16, 0.62)), True,
          ("cat", "kitten", "pet", "animal", "halloween", "farm"),
          details=(_ring(0.37, 0.48, 0.045, 10), _ring(0.63, 0.48, 0.045, 10),
                   ((0.46, 0.36), (0.54, 0.36), (0.5, 0.31), (0.46, 0.36)),
                   ((0.5, 0.31), (0.5, 0.265)),
                   ((0.41, 0.28), (0.455, 0.24), (0.5, 0.265), (0.545, 0.24), (0.59, 0.28)),
                   ((0.3, 0.33), (0.2, 0.35)), ((0.3, 0.29), (0.2, 0.27)),
                   ((0.7, 0.33), (0.8, 0.35)), ((0.7, 0.29), (0.8, 0.27)))),
    Shape("tree", ((0.5, 0.97), (0.68, 0.75), (0.6, 0.75), (0.8, 0.5), (0.68, 0.5), (0.9, 0.25),
                   (0.58, 0.25), (0.58, 0.05), (0.42, 0.05), (0.42, 0.25), (0.1, 0.25), (0.32, 0.5),
                   (0.2, 0.5), (0.4, 0.75), (0.32, 0.75)), True,
          ("tree", "forest", "woods", "christmas", "winter", "nature", "camping", "farm", "garden")),
    Shape("sailboat", ((0.5, 0.95), (0.86, 0.37), (0.54, 0.37), (0.54, 0.26), (0.93, 0.26), (0.78, 0.08),
                       (0.22, 0.08), (0.07, 0.26), (0.46, 0.26), (0.46, 0.37), (0.16, 0.37)), True,
          ("boat", "sailboat", "ocean", "sea", "beach", "summer", "vehicle", "lake", "nautical", "pirate"),
          details=(((0.05, -0.05), (0.2, -0.02), (0.35, -0.05), (0.5, -0.02), (0.65, -0.05), (0.8, -0.02),
                    (0.95, -0.05)),)),
    Shape("balloon", tuple(_balloon()), True,
          ("balloon", "birthday", "party", "celebration", "holiday", "fair", "circus"),
          details=(((0.5, 0.25), (0.46, 0.16), (0.54, 0.08), (0.5, 0.0)),)),
    Shape("apple", ((0.5, 0.78), (0.62, 0.84), (0.76, 0.82), (0.88, 0.7), (0.92, 0.52), (0.88, 0.32),
                    (0.78, 0.14), (0.64, 0.05), (0.5, 0.08), (0.36, 0.05), (0.22, 0.14), (0.12, 0.32),
                    (0.08, 0.52), (0.12, 0.7), (0.24, 0.82), (0.38, 0.84)), True,
          ("apple", "fruit", "food", "orchard", "autumn", "fall", "school", "farm", "teacher"),
          details=(((0.5, 0.78), (0.52, 0.9), (0.55, 0.96)),
                   ((0.53, 0.88), (0.62, 0.95), (0.72, 0.95), (0.64, 0.87), (0.53, 0.88)))),
    Shape("sun", tuple(_sun()), True,
          ("sun", "summer", "sunny", "weather", "beach", "season", "farm", "spring", "space"),
          details=(_ring(0.5, 0.5, 0.25, 24),)),
    Shape("moon", tuple(_moon()), True,
          ("moon", "night", "space", "bedtime", "sleep", "sky", "halloween", "astronaut")),
    Shape("flower", tuple(_flower()), True,
          ("flower", "garden", "spring", "nature", "easter", "mother", "bug", "insect", "plant"),
          details=(_ring(0.5, 0.62, 0.08, 14), ((0.5, 0.5), (0.5, 0.02)),
                   ((0.5, 0.14), (0.62, 0.22), (0.72, 0.2), (0.6, 0.12), (0.5, 0.14)))),
    Shape("dinosaur", ((0.84, 0.9), (0.93, 0.88), (0.96, 0.82), (0.9, 0.79), (0.84, 0.78), (0.8, 0.65),
                       (0.76, 0.52), (0.74, 0.42), (0.72, 0.3), (0.72, 0.08), (0.62, 0.08), (0.62, 0.28),
                       (0.5, 0.27), (0.4, 0.28), (0.38, 0.08), (0.28, 0.08), (0.28, 0.32), (0.18, 0.34),
                       (0.02, 0.3), (0.12, 0.4), (0.24, 0.48), (0.36, 0.56), (0.5, 0.58), (0.62, 0.54),
                       (0.7, 0.62), (0.76, 0.78), (0.8, 0.88)), True,
          ("dinosaur", "dino", "prehistoric", "fossil", "reptile", "jungle"),
          details=(_ring(0.88, 0.85, 0.014, 8),)),
    Shape("car", tuple(_car()), True,
          ("car", "vehicle", "transport", "road", "truck", "city", "travel", "town"),
          details=(_ring(0.28, 0.3, 0.075, 14), _ring(0.74, 0.3, 0.075, 14),
                   ((0.37, 0.52), (0.37, 0.65), (0.49, 0.65), (0.49, 0.52), (0.37, 0.52)),
                   ((0.53, 0.52), (0.53, 0.65), (0.65, 0.65), (0.74, 0.52), (0.53, 0.52)))),
)

SHAPES_BY_NAME = {s.name: s for s in SHAPES}


def singular(word: str) -> str:
    """Crude singular form for matching theme words to tags ("dinosaurs" -> "dinosaur")."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def rank_shapes(theme_tokens: list[str], rng=None) -> tuple[list[Shape], list[Shape]]:
    """(shapes whose tags match the theme, best match first; all other shapes).

    With `rng`, ties and the unmatched shapes are shuffled so packs on the same theme vary,
    while a better match still always comes first.
    """
    tokens = {singular(t) for t in theme_tokens}
    tie = [rng.random() for _ in SHAPES] if rng else list(range(len(SHAPES)))
    scored = sorted(((-sum(t in tokens for t in s.tags), tie[i], s) for i, s in enumerate(SHAPES)),
                    key=lambda x: (x[0], x[1]))
    matched = [s for score, _, s in scored if score < 0]
    others = [s for score, _, s in scored if score == 0]
    return matched, others
