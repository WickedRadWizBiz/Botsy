"""Simple procedural decorations, drawn with reportlab paths: our own small line-art
motifs (leaf sprigs, corner flourishes, geometric marks, seasonal shapes, desk objects) and
page frames. No clip art, fonts with pictures or anyone else's artwork.

Every motif fits the square it is given, strokes at the light line weight or more and
fills only with the style's light tints, so pages stay print friendly.
"""
from __future__ import annotations

import math

from generators.stationery.layout import Box, Sheet

MOTIFS = ("sprig", "fern", "flower", "flourish", "heart", "mug", "laptop", "geometric", "lines", "dots", "blocks",
          "check", "bolt", "leaf", "acorn", "snowflake", "star", "sun", "wave", "pencil", "books", "apple")


def _square(box: Box) -> tuple[float, float, float]:
    s = min(box.w, box.h)
    return box.x + (box.w - s) / 2, box.y + (box.h - s) / 2, s


class _Pen:
    """Unit-square path drawing inside one motif box."""

    def __init__(self, sh: Sheet, box: Box):
        self.sh = sh
        self.c = sh.c
        self.x, self.y, self.s = _square(box)
        self.color = sh.style.accent
        self.fill = sh.tint(0)

    def p(self, u: float, v: float) -> tuple[float, float]:
        return self.x + u * self.s, self.y + v * self.s

    def stroke(self, kind: str = "medium") -> None:
        self.sh._stroke(kind, self.color)

    def path(self, points, *, close: bool = False, fill: bool = False, curves=None):
        """A polyline through unit-square points (curves: list of (c1, c2, end) after the
        first point instead)."""
        c = self.c
        path = c.beginPath()
        path.moveTo(*self.p(*points[0]))
        if curves:
            for c1, c2, end in curves:
                path.curveTo(*self.p(*c1), *self.p(*c2), *self.p(*end))
        else:
            for pt in points[1:]:
                path.lineTo(*self.p(*pt))
        if close:
            path.close()
        self.stroke()
        if fill and self.fill is not None:
            c.setFillColorRGB(*self.fill)
        c.drawPath(path, stroke=1, fill=int(fill and self.fill is not None))

    def circle(self, u: float, v: float, r: float, *, fill: bool = False, solid: bool = False) -> None:
        x, y = self.p(u, v)
        self.stroke()
        if solid:
            self.c.setFillColorRGB(*self.color)
        elif fill and self.fill is not None:
            self.c.setFillColorRGB(*self.fill)
        self.c.circle(x, y, r * self.s, stroke=0 if solid else 1, fill=int(solid or (fill and self.fill is not None)))

    def leaf(self, base: tuple[float, float], angle: float, length: float, width: float, *, vein: bool = False):
        """An almond-shaped leaf from `base` pointing at `angle` (degrees)."""
        a = math.radians(angle)
        dx, dy = math.cos(a), math.sin(a)
        nx, ny = -dy, dx
        bx, by = base
        tip = (bx + dx * length, by + dy * length)
        c1 = (bx + dx * length * 0.3 + nx * width, by + dy * length * 0.3 + ny * width)
        c2 = (bx + dx * length * 0.75 + nx * width, by + dy * length * 0.75 + ny * width)
        c3 = (bx + dx * length * 0.75 - nx * width, by + dy * length * 0.75 - ny * width)
        c4 = (bx + dx * length * 0.3 - nx * width, by + dy * length * 0.3 - ny * width)
        self.path([base], curves=[(c1, c2, tip), (c3, c4, base)], close=True, fill=True)
        if vein:
            self.path([base, (bx + dx * length * 0.85, by + dy * length * 0.85)])


def draw_motif(sh: Sheet, box: Box, motif: str) -> None:
    """Draw one motif centred in box (unknown names draw the geometric mark)."""
    if box.w < 6 or box.h < 6:
        return
    sh._check(Box(*_square(box)[:2], _square(box)[2], _square(box)[2]), f"motif {motif}")
    pen = _Pen(sh, box)
    _DRAW.get(motif, _geometric)(pen)


# --- motifs --------------------------------------------------------------------------------

def _sprig(p: _Pen) -> None:
    p.path([(0.18, 0.08)], curves=[((0.3, 0.45), (0.55, 0.7), (0.86, 0.92))])
    for t, side in ((0.25, 1), (0.42, -1), (0.58, 1), (0.74, -1)):
        bx, by = 0.18 + (0.86 - 0.18) * t, 0.08 + (0.92 - 0.08) * t ** 0.9
        p.leaf((bx, by), 50 + 70 * side + (-15 if side > 0 else 15), 0.26, 0.07)
    p.leaf((0.86, 0.92), 45, 0.06, 0.03)


def _fern(p: _Pen) -> None:
    p.path([(0.2, 0.06), (0.8, 0.94)])
    for i in range(6):
        t = 0.14 + i * 0.14
        bx, by = 0.2 + 0.6 * t, 0.06 + 0.88 * t
        size = 0.24 * (1 - t * 0.7)
        p.leaf((bx, by), 135, size, 0.045)
        p.leaf((bx, by), -10, size, 0.045)


def _flower(p: _Pen) -> None:
    p.path([(0.5, 0.05)], curves=[((0.46, 0.25), (0.54, 0.4), (0.5, 0.55))])
    p.leaf((0.49, 0.2), 30, 0.2, 0.06)
    for k in range(5):
        p.leaf((0.5, 0.66), 90 + k * 72, 0.26, 0.09)
    p.circle(0.5, 0.66, 0.07, fill=True)


def _flourish(p: _Pen) -> None:
    p.path([(0.05, 0.5)], curves=[((0.2, 0.92), (0.42, 0.92), (0.5, 0.5)), ((0.58, 0.08), (0.8, 0.08), (0.95, 0.5))])
    p.path([(0.05, 0.5)], curves=[((0.04, 0.38), (0.16, 0.36), (0.17, 0.46))])
    p.path([(0.95, 0.5)], curves=[((0.96, 0.62), (0.84, 0.64), (0.83, 0.54))])
    p.circle(0.5, 0.5, 0.035, solid=True)
    p.leaf((0.5, 0.5), 100, 0.18, 0.05)
    p.leaf((0.5, 0.5), -80, 0.18, 0.05)


def _heart(p: _Pen) -> None:
    p.path([(0.5, 0.18)], curves=[((0.1, 0.45), (0.2, 0.85), (0.5, 0.66)), ((0.8, 0.85), (0.9, 0.45), (0.5, 0.18))],
           close=True, fill=True)


def _mug(p: _Pen) -> None:
    p.path([(0.18, 0.62), (0.2, 0.18), (0.66, 0.18), (0.68, 0.62)], close=True, fill=True)
    p.path([(0.67, 0.52)], curves=[((0.9, 0.54), (0.9, 0.28), (0.66, 0.3))])
    for u in (0.33, 0.5):
        p.path([(u, 0.7)], curves=[((u - 0.06, 0.78), (u + 0.06, 0.84), (u, 0.92))])


def _laptop(p: _Pen) -> None:
    p.path([(0.2, 0.36), (0.2, 0.8), (0.8, 0.8), (0.8, 0.36)], close=True, fill=True)
    p.path([(0.08, 0.24), (0.92, 0.24), (0.84, 0.36), (0.16, 0.36)], close=True)
    p.path([(0.42, 0.29), (0.58, 0.29)])


def _geometric(p: _Pen) -> None:
    p.circle(0.38, 0.58, 0.26, fill=True)
    p.path([(0.62, 0.12), (0.92, 0.42), (0.62, 0.72), (0.32, 0.42)], close=True)
    for i in range(3):
        p.circle(0.12 + i * 0.1, 0.12, 0.025, solid=True)


def _lines(p: _Pen) -> None:
    for i, length in enumerate((0.8, 0.6, 0.4)):
        p.path([(0.1, 0.7 - i * 0.2), (0.1 + length, 0.7 - i * 0.2)])
    p.circle(0.85, 0.7, 0.04, solid=True)


def _dots(p: _Pen) -> None:
    for i in range(3):
        for j in range(3):
            p.circle(0.25 + i * 0.25, 0.25 + j * 0.25, 0.05 if (i + j) % 2 else 0.035, solid=True)


def _blocks(p: _Pen) -> None:
    sh = p.sh
    for i in range(3):
        cell = Box(p.x + p.s * (0.06 + i * 0.32), p.y + p.s * 0.34, p.s * 0.26, p.s * 0.26)
        sh.rect(cell, kind="medium", fill=sh.tint(i), radius=3, color=p.color)
        if i < 2:
            p.path([(0.1 + i * 0.32, 0.47), (0.17 + i * 0.32, 0.4), (0.28 + i * 0.32, 0.56)])


def _check(p: _Pen) -> None:
    p.sh.rect(Box(p.x + p.s * 0.18, p.y + p.s * 0.18, p.s * 0.64, p.s * 0.64), kind="medium", fill=p.fill,
              radius=4, color=p.color)
    p.path([(0.3, 0.5), (0.45, 0.34), (0.72, 0.68)])


def _bolt(p: _Pen) -> None:
    p.path([(0.58, 0.95), (0.25, 0.48), (0.48, 0.48), (0.4, 0.05), (0.76, 0.56), (0.53, 0.56)], close=True, fill=True)


def _leaf(p: _Pen) -> None:
    p.leaf((0.22, 0.18), 50, 0.72, 0.18, vein=True)
    for t in (0.3, 0.5, 0.68):
        bx, by = 0.22 + 0.72 * t * math.cos(math.radians(50)), 0.18 + 0.72 * t * math.sin(math.radians(50))
        p.path([(bx, by), (bx - 0.1, by + 0.08)])
        p.path([(bx, by), (bx + 0.11, by - 0.04)])
    p.path([(0.22, 0.18), (0.12, 0.06)])


def _acorn(p: _Pen) -> None:
    p.path([(0.5, 0.12)], curves=[((0.22, 0.2), (0.24, 0.52), (0.28, 0.56)), ((0.4, 0.58), (0.6, 0.58), (0.72, 0.56)),
                                  ((0.76, 0.52), (0.78, 0.2), (0.5, 0.12))], close=True, fill=True)
    p.path([(0.22, 0.56)], curves=[((0.25, 0.8), (0.75, 0.8), (0.78, 0.56))], close=True)
    p.path([(0.5, 0.74), (0.55, 0.9)])


def _snowflake(p: _Pen) -> None:
    for k in range(6):
        a = math.radians(90 + k * 60)
        ex, ey = 0.5 + 0.42 * math.cos(a), 0.5 + 0.42 * math.sin(a)
        p.path([(0.5, 0.5), (ex, ey)])
        mx, my = 0.5 + 0.26 * math.cos(a), 0.5 + 0.26 * math.sin(a)
        for side in (-1, 1):
            b = a + side * math.radians(40)
            p.path([(mx, my), (mx + 0.12 * math.cos(b), my + 0.12 * math.sin(b))])
    p.circle(0.5, 0.5, 0.05, fill=True)


def _star(p: _Pen) -> None:
    pts = []
    for k in range(10):
        r = 0.44 if k % 2 == 0 else 0.19
        a = math.pi / 2 + k * math.pi / 5
        pts.append((0.5 + r * math.cos(a), 0.48 + r * math.sin(a)))
    p.path(pts, close=True, fill=True)


def _sun(p: _Pen) -> None:
    p.circle(0.5, 0.5, 0.2, fill=True)
    for k in range(12):
        a = math.radians(k * 30)
        c, s = math.cos(a), math.sin(a)
        p.path([(0.5 + 0.28 * c, 0.5 + 0.28 * s), (0.5 + 0.44 * c, 0.5 + 0.44 * s)])


def _wave(p: _Pen) -> None:
    for i in range(3):
        v = 0.3 + i * 0.2
        p.path([(0.08, v)], curves=[((0.25, v + 0.12), (0.35, v - 0.12), (0.5, v)),
                                    ((0.65, v + 0.12), (0.75, v - 0.12), (0.92, v))])


def _pencil(p: _Pen) -> None:
    a = math.radians(35)
    dx, dy = math.cos(a), math.sin(a)
    nx, ny = -dy, dx
    w = 0.08

    def at(t, off):
        return 0.12 + dx * t + nx * off, 0.14 + dy * t + ny * off

    p.path([at(0.14, w), at(0.86, w), at(0.86, -w), at(0.14, -w)], close=True, fill=True)
    p.path([at(0.14, w), at(0.0, 0.0), at(0.14, -w)])
    p.path([at(0.74, w), at(0.74, -w)])


def _books(p: _Pen) -> None:
    sh = p.sh
    for i, (u, w, h) in enumerate(((0.14, 0.18, 0.62), (0.34, 0.16, 0.7), (0.52, 0.2, 0.56))):
        sh.rect(Box(p.x + p.s * u, p.y + p.s * 0.14, p.s * w, p.s * h), kind="medium", fill=sh.tint(i), radius=1.5,
                color=p.color)
    p.path([(0.76, 0.14), (0.9, 0.62)])
    p.path([(0.9, 0.62), (0.76, 0.66)])
    p.path([(0.06, 0.14), (0.94, 0.14)])


def _apple(p: _Pen) -> None:
    p.path([(0.5, 0.62)], curves=[((0.3, 0.76), (0.08, 0.6), (0.18, 0.32)), ((0.26, 0.12), (0.42, 0.1), (0.5, 0.16)),
                                  ((0.58, 0.1), (0.74, 0.12), (0.82, 0.32)), ((0.92, 0.6), (0.7, 0.76), (0.5, 0.62))],
           close=True, fill=True)
    p.path([(0.5, 0.62), (0.53, 0.84)])
    p.leaf((0.53, 0.78), 25, 0.2, 0.06)


_DRAW = {"sprig": _sprig, "fern": _fern, "flower": _flower, "flourish": _flourish, "heart": _heart, "mug": _mug,
         "laptop": _laptop, "geometric": _geometric, "lines": _lines, "dots": _dots, "blocks": _blocks,
         "check": _check, "bolt": _bolt, "leaf": _leaf, "acorn": _acorn, "snowflake": _snowflake, "star": _star,
         "sun": _sun, "wave": _wave, "pencil": _pencil, "books": _books, "apple": _apple}


# --- frames --------------------------------------------------------------------------------

FRAME_INSET = {"": 0.0, "brackets": 9.0, "rounded": 13.0, "dashed": 8.0}


def draw_frame(sh: Sheet, box: Box, frame: str) -> Box:
    """Draw the style's page frame around `box`; returns the area left inside it."""
    if not frame:
        return box
    color = sh.style.accent
    if frame == "brackets":
        arm = 16.0
        for (x, y, sx, sy) in ((box.x, box.y, 1, 1), (box.right, box.y, -1, 1), (box.x, box.top, 1, -1),
                               (box.right, box.top, -1, -1)):
            sh.line(x, y, x + sx * arm, y, kind="medium", color=color)
            sh.line(x, y, x, y + sy * arm, kind="medium", color=color)
    elif frame == "rounded":
        sh.rect(box, kind="medium", radius=10.0, color=color)
        sh.rect(box.inset(3.0), kind="light", radius=8.0, color=color)
        for (x, y) in ((box.x + 6, box.y + 6), (box.right - 6, box.y + 6), (box.x + 6, box.top - 6),
                       (box.right - 6, box.top - 6)):
            sh.circle(x, y, 1.6, kind="light", fill=color, color=color)
    elif frame == "dashed":
        sh.rect(box, kind="light", radius=4.0, color=color, dash=(3, 2))
    return box.inset(FRAME_INSET.get(frame, 0.0))
