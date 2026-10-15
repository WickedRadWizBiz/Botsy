"""Dot-to-dot pictures: resample a shape to N dots and place numbers that do not collide.

Resampling keeps every sharp corner as a dot (so a star still looks like a star) and
spreads the remaining dots along the outline by arc length. Numbers are placed per page
size: each label tries positions around its dot, outward first, and must not touch the
path the child will draw, any dot, any detail line or another number.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from reportlab.pdfbase.pdfmetrics import stringWidth

from generators.activity_sheets.layout import CAP_HEIGHT, FONT, FONT_BOLD, INK, Box, Style, polyline, set_line
from generators.activity_sheets.shapes import Point, Shape

log = logging.getLogger(__name__)

CORNER_DEG = 35.0


@dataclass(frozen=True)
class DotPicture:
    """Size-independent content of one dot-to-dot page (normalized 0..1 coordinates)."""

    shape: str
    dots: tuple[Point, ...]
    closed: bool
    details: tuple[tuple[Point, ...], ...]


@dataclass(frozen=True)
class Label:
    number: int
    x: float          # bottom-left of the text box
    y: float
    w: float
    h: float
    collisions: int = 0


@dataclass(frozen=True)
class DotLayout:
    """A DotPicture fitted to a box on one page size."""

    dots: tuple[Point, ...]
    labels: tuple[Label, ...]
    details: tuple[tuple[Point, ...], ...]
    closed: bool
    dot_r: float
    font_size: float
    line_w: float


def make_picture(shape: Shape, n_dots: int) -> DotPicture:
    return DotPicture(shape.name, tuple(resample(list(shape.outline), n_dots, shape.closed)),
                      shape.closed, shape.details)


def _turn_deg(a: Point, b: Point, c: Point) -> float:
    v1 = (b[0] - a[0], b[1] - a[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 < 1e-12 or n2 < 1e-12:
        return 0.0
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def resample(points: list[Point], n: int, closed: bool, corner_deg: float = CORNER_DEG) -> list[Point]:
    """n dots along the outline starting at points[0] (more if corners and curves need them)."""
    pts = [p for i, p in enumerate(points) if i == 0 or math.dist(p, points[i - 1]) > 1e-9]
    if closed and len(pts) > 1 and math.dist(pts[0], pts[-1]) < 1e-9:
        pts.pop()
    m = len(pts)
    if m < 2:
        raise ValueError("an outline needs at least 2 points")
    if closed:
        corners = [i for i in range(m) if i == 0 or _turn_deg(pts[i - 1], pts[i], pts[(i + 1) % m]) > corner_deg]
        spans = [(corners[k], corners[(k + 1) % len(corners)] if k + 1 < len(corners) else m)
                 for k in range(len(corners))]
    else:
        corners = [0] + [i for i in range(1, m - 1) if _turn_deg(pts[i - 1], pts[i], pts[i + 1]) > corner_deg] + [m - 1]
        spans = list(zip(corners, corners[1:]))

    def span_points(a: int, b: int) -> list[Point]:
        return [pts[i % m] for i in range(a, b + 1)]

    # A curved span needs enough dots that the drawn chords still read as a curve
    # (one chord per ~60 degrees of turning), however short the span is.
    curve_min = []
    for a, b in spans:
        seg = span_points(a, b)
        turn = sum(_turn_deg(seg[i - 1], seg[i], seg[i + 1]) for i in range(1, len(seg) - 1))
        curve_min.append(max(0, math.ceil(turn / 60.0) - 1))
    n = max(n, len(corners) + sum(curve_min))
    lengths = [_length(span_points(a, b)) for a, b in spans]
    extra = n - len(corners) - sum(curve_min)
    total = sum(lengths) or 1.0
    shares = [extra * L / total for L in lengths]
    alloc = [int(s) for s in shares]
    for k in sorted(range(len(spans)), key=lambda k: (-(shares[k] - alloc[k]), k))[: extra - sum(alloc)]:
        alloc[k] += 1
    alloc = [a + c for a, c in zip(alloc, curve_min)]

    out: list[Point] = []
    for (a, b), k in zip(spans, alloc):
        seg = span_points(a, b)
        out.append(seg[0])
        L = _length(seg)
        out += [_point_at(seg, L * (j + 1) / (k + 1)) for j in range(k)]
    if not closed:
        out.append(pts[-1])
    return out


def _length(pts: list[Point]) -> float:
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def _point_at(pts: list[Point], dist: float) -> Point:
    for i in range(len(pts) - 1):
        seg = math.dist(pts[i], pts[i + 1])
        if dist <= seg or i == len(pts) - 2:
            t = 0.0 if seg < 1e-12 else min(dist / seg, 1.0)
            return (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t)
        dist -= seg
    return pts[-1]


def path_segments(dots, closed: bool) -> list[tuple[Point, Point]]:
    segs = list(zip(dots, dots[1:]))
    if closed and len(dots) > 2:
        segs.append((dots[-1], dots[0]))
    return segs


def layout_picture(pic: DotPicture, box: Box, style: Style) -> DotLayout:
    """Fit the picture into `box` (aspect kept) and place every number."""
    n = len(pic.dots)
    font_size = max(7.0, min(12.0, box.w / 42, 13.0 - n * 0.08))
    dot_r = max(2.2, font_size * 0.24)
    line_w = style.lw(2.0)
    pad = font_size * 2.2                               # room for labels outside the outline
    tf = fit_transform(list(pic.dots) + [p for d in pic.details for p in d], box.inset(pad))
    dots = tuple(tf(p) for p in pic.dots)
    details = tuple(tuple(tf(p) for p in d) for d in pic.details)
    labels = place_labels(dots, pic.closed, details, box, font_size, dot_r, line_w)
    return DotLayout(dots, labels, details, pic.closed, dot_r, font_size, line_w)


def fit_transform(points: list[Point], box: Box):
    """Map normalized points into `box`: uniform scale (aspect kept), centered."""
    x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
    y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
    scale = min(box.w / max(x1 - x0, 1e-6), box.h / max(y1 - y0, 1e-6))
    ox = box.cx - (x0 + x1) / 2 * scale
    oy = box.cy - (y0 + y1) / 2 * scale

    def tf(p: Point) -> Point:
        return ox + p[0] * scale, oy + p[1] * scale

    return tf


def _outward(dots, i: int, closed: bool, orientation: float, centroid: Point) -> float:
    """Angle (radians) pointing away from the shape at dot i."""
    n = len(dots)
    prev = dots[i - 1] if (closed or i > 0) else None
    nxt = dots[(i + 1) % n] if (closed or i < n - 1) else None
    nx = ny = 0.0
    for a, b in ((prev, dots[i]), (dots[i], nxt)):
        if a is None or b is None:
            continue
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        # right-hand normal of the travel direction points outward for a counter-clockwise outline
        nx += dy / L * orientation
        ny += -dx / L * orientation
    if not closed or math.hypot(nx, ny) < 1e-9:
        nx, ny = dots[i][0] - centroid[0], dots[i][1] - centroid[1]
    return math.atan2(ny, nx)


def _signed_area(pts) -> float:
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts))) / 2


def place_labels(dots, closed: bool, details, box: Box, font_size: float, dot_r: float,
                 line_w: float, clearance: float = 1.2) -> tuple[Label, ...]:
    """Greedy placement, number 1 first. Each label takes the first collision-free spot."""
    segs = path_segments(dots, closed) + [s for d in details for s in zip(d, d[1:])]
    orientation = 1.0 if _signed_area(dots) >= 0 else -1.0
    cen = (sum(p[0] for p in dots) / len(dots), sum(p[1] for p in dots) / len(dots))
    h = font_size * CAP_HEIGHT
    offsets = [0, 25, -25, 50, -50, 75, -75, 105, -105, 135, -135, 165, -165, 180]
    labels: list[Label] = []
    for i, (x, y) in enumerate(dots):
        text = str(i + 1)
        w = stringWidth(text, _label_font(i), font_size)
        base = _outward(dots, i, closed, orientation, cen)
        best: Label | None = None
        for gap in (dot_r + 1.5, dot_r + 4.5, dot_r + 9.0, dot_r + 15.0):
            for off in offsets:
                a = base + math.radians(off)
                ux, uy = math.cos(a), math.sin(a)
                reach = gap + abs(ux) * w / 2 + abs(uy) * h / 2
                lx, ly = x + ux * reach - w / 2, y + uy * reach - h / 2
                cand = Label(i + 1, lx, ly, w, h)
                hits = _collisions(cand, segs, dots, labels, box, dot_r, line_w, clearance)
                if hits == 0:
                    best = cand
                    break
                if best is None or hits < best.collisions:
                    best = Label(i + 1, lx, ly, w, h, hits)
            if best is not None and best.collisions == 0:
                break
        labels.append(best)
    return tuple(labels)


def _label_font(i: int) -> str:
    return FONT_BOLD if i == 0 else FONT


def _collisions(lab: Label, segs, dots, labels, box: Box, dot_r: float, line_w: float,
                clearance: float) -> int:
    hits = 0
    if not (box.x <= lab.x and lab.x + lab.w <= box.right and box.y <= lab.y and lab.y + lab.h <= box.top):
        hits += 1
    grow = clearance + line_w / 2
    rx0, ry0, rx1, ry1 = lab.x - grow, lab.y - grow, lab.x + lab.w + grow, lab.y + lab.h + grow
    hits += sum(segment_hits_rect(a, b, rx0, ry0, rx1, ry1) for a, b in segs)
    hits += sum(_circle_hits_rect(p, dot_r + clearance, lab) for p in dots)
    hits += sum(_rects_overlap(lab, o, clearance) for o in labels)
    return hits


def segment_hits_rect(a: Point, b: Point, x0: float, y0: float, x1: float, y1: float) -> bool:
    """Liang-Barsky clip: does segment a-b pass through the rectangle?"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def _circle_hits_rect(p: Point, r: float, lab: Label) -> bool:
    nx = min(max(p[0], lab.x), lab.x + lab.w)
    ny = min(max(p[1], lab.y), lab.y + lab.h)
    return math.hypot(p[0] - nx, p[1] - ny) < r


def _rects_overlap(a: Label, b: Label, gap: float) -> bool:
    return not (a.x + a.w + gap <= b.x or b.x + b.w + gap <= a.x or
                a.y + a.h + gap <= b.y or b.y + b.h + gap <= a.y)


def draw_dot_to_dot(c, box: Box, pic: DotPicture, style: Style, solution: bool = False,
                    faint_preview: bool = False) -> DotLayout:
    lay = layout_picture(pic, box, style)
    set_line(c, lay.line_w, INK)
    for d in lay.details:
        polyline(c, list(d))
    if solution:
        set_line(c, style.lw(2.5), INK)
        polyline(c, list(lay.dots), closed=lay.closed)
        return lay
    if faint_preview:
        set_line(c, style.lw(1.5), 0.88, dash=[2, 4])
        polyline(c, list(lay.dots), closed=lay.closed)
    crowded = [lab.number for lab in lay.labels if lab.collisions]
    if crowded:                       # best effort was drawn; a person should look at this page
        log.warning("dot-to-dot %s: numbers %s touch a line, dot or another number", pic.shape, crowded)
    c.setFillGray(INK)
    for x, y in lay.dots:
        c.circle(x, y, lay.dot_r, stroke=0, fill=1)
    for k, lab in enumerate(lay.labels):
        c.setFont(_label_font(k), lay.font_size)
        c.drawString(lab.x, lab.y, str(lab.number))
    return lay
