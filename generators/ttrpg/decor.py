"""Procedural decoration for tabletop RPG pages: page frames, header dividers, section
heads and small emblems, all drawn from lines, arcs and simple shapes (our own art; no
fonts, images or symbols of any game or publisher).

Everything is drawn through the Pen, so it stays inside the print margins and keeps the
line-weight floor.
"""
from __future__ import annotations

import math

from generators.ttrpg.layout import INK, LIGHT, SOFT, Box, Pen, text_width
from generators.ttrpg.styles import Genre

FRAME_PAD = 10.0          # frame to content
HEAD_H = 13.0             # section heading strip


def _upper(text: str, on: bool) -> str:
    return text.upper() if on else text


# --- page frames ------------------------------------------------------------------------------

def _diamond(pen: Pen, x: float, y: float, r: float, fill=None, weight: float | None = None) -> None:
    pen.polygon([(x, y + r), (x + r, y), (x, y - r), (x - r, y)], fill=fill, weight=weight)


def _star(pen: Pen, x: float, y: float, r: float, points: int = 5, inner: float = 0.45, fill=None,
          weight: float | None = None) -> None:
    pts = []
    for i in range(points * 2):
        a = math.pi / 2 + i * math.pi / points
        rr = r if i % 2 == 0 else r * inner
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    pen.polygon(pts, fill=fill, weight=weight)


def _corners(box: Box, inset: float = 0.0) -> list[tuple[float, float, int, int]]:
    """(x, y, sx, sy) for each corner; sx/sy point into the page."""
    return [(box.x + inset, box.top - inset, 1, -1), (box.right - inset, box.top - inset, -1, -1),
            (box.x + inset, box.y + inset, 1, 1), (box.right - inset, box.y + inset, -1, 1)]


def frame_double_diamond(pen: Pen, box: Box, g: Genre) -> None:
    r = pen.rules
    o = box.inset(5.5)
    pen.rect(o, weight=r.frame_pt)
    pen.rect(o.inset(3.5), weight=r.light_pt)
    for x, y, _sx, _sy in _corners(o):
        _diamond(pen, x, y, 4.2, fill=g.tint, weight=r.rule_pt)
    for x, y in ((o.cx, o.top), (o.cx, o.y), (o.x, o.cy), (o.right, o.cy)):
        _diamond(pen, x, y, 3.0, fill=(1, 1, 1), weight=r.light_pt + 0.2)


def frame_thorns(pen: Pen, box: Box, g: Genre) -> None:
    r = pen.rules
    o = box.inset(4.0)
    pen.rect(o, weight=r.frame_pt + 0.6)
    inner = o.inset(4.5)
    pen.rect(inner, weight=r.light_pt)
    for x, y, sx, sy in _corners(inner):
        for k, (dx, dy) in enumerate(((10, 0), (0, 10), (6, 6))):
            bx, by = x + sx * dx, y + sy * dy
            tip = (bx + sx * (5 if k == 2 else 3), by + sy * (5 if k == 2 else 3))
            pen.polygon([(bx - 2.2, by), (bx + 2.2, by), tip] if k != 1 else [(bx, by - 2.2), (bx, by + 2.2), tip],
                        fill=INK, weight=r.light_pt)
    for x, y, d in ((inner.cx, inner.top, (0, -1)), (inner.cx, inner.y, (0, 1)),
                    (inner.x, inner.cy, (1, 0)), (inner.right, inner.cy, (-1, 0))):
        if d[1]:
            pen.polygon([(x - 3, y), (x + 3, y), (x, y + d[1] * 6)], fill=INK, weight=r.light_pt)
        else:
            pen.polygon([(x, y - 3), (x, y + 3), (x + d[0] * 6, y)], fill=INK, weight=r.light_pt)


def _chamfered(box: Box, c: float) -> list[tuple[float, float]]:
    return [(box.x + c, box.top), (box.right - c, box.top), (box.right, box.top - c), (box.right, box.y + c),
            (box.right - c, box.y), (box.x + c, box.y), (box.x, box.y + c), (box.x, box.top - c)]


def frame_chamfer(pen: Pen, box: Box, g: Genre) -> None:
    r = pen.rules
    o = box.inset(4.0)
    pen.polygon(_chamfered(o, 16), weight=r.frame_pt)
    inner = o.inset(4.0)
    pen.polygon(_chamfered(inner, 13), weight=r.light_pt)
    x = inner.x + 30
    while x < inner.right - 30:
        pen.line(x, o.top - 0.8, x, o.top - 3.2, weight=r.light_pt)
        pen.line(x, o.y + 0.8, x, o.y + 3.2, weight=r.light_pt)
        x += 18
    for x, y, sx, sy in _corners(inner):
        pen.line(x + sx * 14, y + sy * 3, x + sx * 40, y + sy * 3, weight=r.rule_pt + 0.6, color=g.accent)


def _spiral(pen: Pen, x: float, y: float, r: float, turns: float, sx: int, sy: int, weight: float) -> None:
    pts = []
    steps = int(24 * turns)
    for i in range(steps + 1):
        t = i / steps
        a = t * turns * 2 * math.pi
        rr = r * (1 - t * 0.85)
        pts.append((x + sx * rr * math.cos(a), y + sy * rr * math.sin(a)))
    pen.curve(pts, weight=weight)


def frame_tendrils(pen: Pen, box: Box, g: Genre) -> None:
    r = pen.rules
    o = box.inset(4.0)
    pen.rect(o, weight=r.rule_pt)
    inner = o.inset(3.0)
    pen.rect(inner, weight=r.light_pt)
    for x, y, sx, sy in _corners(inner):
        _spiral(pen, x + sx * 9, y + sy * 9, 7.0, 1.6, sx, sy, r.light_pt + 0.3)
        pen.curve([(x + sx * 16, y + sy * 2), (x + sx * 26, y + sy * 5), (x + sx * 38, y + sy * 2.5)],
                  weight=r.light_pt + 0.2)
        pen.curve([(x + sx * 2, y + sy * 16), (x + sx * 5, y + sy * 26), (x + sx * 2.5, y + sy * 38)],
                  weight=r.light_pt + 0.2)
    for x, y in ((inner.cx, inner.top), (inner.cx, inner.y)):
        pen.circle(x, y, 3.5, weight=r.light_pt, fill=(1, 1, 1))
        pen.circle(x, y, 1.4, fill=INK, weight=r.light_pt)


def _notched(box: Box, n: float) -> list[tuple[float, float]]:
    return [(box.x + n, box.top), (box.right - 2 * n, box.top), (box.right - 2 * n, box.top - n * 0.5),
            (box.right - n, box.top - n * 0.5), (box.right, box.top - n), (box.right, box.y + n),
            (box.right - n, box.y), (box.x + 2 * n, box.y), (box.x + 2 * n, box.y + n * 0.5),
            (box.x + n, box.y + n * 0.5), (box.x, box.y + n), (box.x, box.top - n)]


def frame_circuit(pen: Pen, box: Box, g: Genre) -> None:
    r = pen.rules
    o = box.inset(4.0)
    pen.polygon(_notched(o, 12), weight=r.frame_pt)
    inner = o.inset(4.5)
    for x, y, sx, sy in _corners(inner):
        for k in range(3):
            d = 6 + k * 5
            pts = [(x + sx * d, y + sy * 2), (x + sx * d, y + sy * (10 + k * 4)),
                   (x + sx * (d + 9), y + sy * (16 + k * 4))]
            pen.polygon(pts, weight=r.light_pt, close=False, color=g.accent)
            pen.circle(pts[-1][0], pts[-1][1], 1.4, fill=(1, 1, 1), weight=r.light_pt, color=g.accent)


def frame_rope(pen: Pen, box: Box, g: Genre) -> None:
    r = pen.rules
    o = box.inset(4.0)
    inner = o.inset(5.0)
    pen.rect(o, weight=r.light_pt + 0.2)
    pen.rect(inner, weight=r.light_pt + 0.2)
    step = 5.0
    # twisted strokes between the two rules
    x = o.x + 8
    while x < o.right - 8:
        pen.line(x, o.top - 0.8, x + 3.2, inner.top + 0.8, weight=r.light_pt)
        pen.line(x, inner.y - 0.8, x + 3.2, o.y + 0.8, weight=r.light_pt)
        x += step
    y = o.y + 8
    while y < o.top - 8:
        pen.line(o.x + 0.8, y, inner.x - 0.8, y + 3.2, weight=r.light_pt)
        pen.line(inner.right + 0.8, y, o.right - 0.8, y + 3.2, weight=r.light_pt)
        y += step
    for x, y, sx, sy in _corners(o):
        cx, cy = x + sx * 2.5, y + sy * 2.5
        pen.circle(cx, cy, 2.6, fill=(1, 1, 1), weight=r.rule_pt)


def frame_rivets(pen: Pen, box: Box, g: Genre) -> None:
    r = pen.rules
    o = box.inset(4.5)
    inner = o.inset(5.0)
    pen.rect(o, weight=r.frame_pt)
    pen.rect(inner, weight=r.light_pt)
    for x, y, sx, sy in _corners(o):
        _star(pen, x + sx * 2.5, y + sy * 2.5, 4.6, fill=g.tint, weight=r.light_pt + 0.2)
    mid = 2.5
    x = o.x + 24
    while x < o.right - 20:
        pen.circle(x, o.top - mid, 0.9, fill=INK, weight=r.light_pt)
        pen.circle(x, o.y + mid, 0.9, fill=INK, weight=r.light_pt)
        x += 30
    y = o.y + 24
    while y < o.top - 20:
        pen.circle(o.x + mid, y, 0.9, fill=INK, weight=r.light_pt)
        pen.circle(o.right - mid, y, 0.9, fill=INK, weight=r.light_pt)
        y += 30


def _leaf(pen: Pen, x: float, y: float, length: float, angle: float, fill=None, weight: float = 0.6) -> None:
    a = math.radians(angle)
    ux, uy = math.cos(a), math.sin(a)
    px, py = -uy, ux
    w = length * 0.32
    tip = (x + ux * length, y + uy * length)
    mid = (x + ux * length * 0.5, y + uy * length * 0.5)
    pts = [(x, y), (mid[0] + px * w, mid[1] + py * w), tip, (mid[0] - px * w, mid[1] - py * w)]
    pen.polygon(pts, fill=fill, weight=weight)
    pen.line(x, y, mid[0], mid[1], weight=weight)


def frame_rounded_vine(pen: Pen, box: Box, g: Genre) -> None:
    r = pen.rules
    o = box.inset(4.0)
    pen.rect(o, weight=r.rule_pt + 0.2, radius=14)
    pen.rect(o.inset(4.0), weight=r.light_pt, radius=10, dash=(1.5, 2.5))
    for x, y, sx, sy in _corners(o, 6.0):
        base_angle = math.degrees(math.atan2(sy, sx))
        for k, da in enumerate((-35, 0, 35)):
            _leaf(pen, x + sx * 1.5, y + sy * 1.5, 9.0 - abs(da) / 12, base_angle + da,
                  fill=g.tint if k != 1 else (1, 1, 1), weight=r.light_pt)


FRAMES = {"double_diamond": frame_double_diamond, "thorns": frame_thorns, "chamfer": frame_chamfer,
          "tendrils": frame_tendrils, "circuit": frame_circuit, "rope": frame_rope, "rivets": frame_rivets,
          "rounded_vine": frame_rounded_vine}


def frame(pen: Pen, box: Box, g: Genre) -> Box:
    """Draw the page frame around `box` (the print margins). Returns the content box."""
    FRAMES.get(g.frame, frame_double_diamond)(pen, box, g)
    return box.inset(FRAME_PAD + 4)


# --- header -----------------------------------------------------------------------------------

def divider(pen: Pen, x0: float, x1: float, y: float, g: Genre) -> None:
    r = pen.rules
    cx = (x0 + x1) / 2
    kind = g.divider
    if kind == "diamond":
        pen.line(x0, y, cx - 7, y, weight=r.light_pt + 0.2)
        pen.line(cx + 7, y, x1, y, weight=r.light_pt + 0.2)
        _diamond(pen, cx, y, 3.6, fill=g.tint, weight=r.light_pt + 0.2)
    elif kind == "spikes":
        pen.line(x0, y, x1, y, weight=r.rule_pt)
        for k in range(-2, 3):
            x = cx + k * 9
            pen.polygon([(x - 2.2, y), (x + 2.2, y), (x, y - 4.5 + abs(k))], fill=INK, weight=r.light_pt)
    elif kind == "segments":
        x, k = x0, 0
        while x < x1 - 1:
            seg = min(22.0 if k % 4 else 6.0, x1 - x)
            pen.line(x, y, x + seg, y, weight=r.rule_pt + (0.8 if k % 4 == 0 else 0), color=g.accent)
            x += seg + 3
            k += 1
    elif kind == "circle":
        pen.line(x0, y, cx - 8, y, weight=r.light_pt)
        pen.line(cx + 8, y, x1, y, weight=r.light_pt)
        pen.circle(cx, y, 4.5, weight=r.light_pt)
        pen.circle(cx, y, 1.6, fill=INK, weight=r.light_pt)
    elif kind == "nodes":
        pen.line(x0, y, x1, y, weight=r.light_pt, dash=(6, 2))
        for x in (x0 + 2, cx, x1 - 2):
            pen.circle(x, y, 1.8, fill=(1, 1, 1), weight=r.light_pt, color=g.accent)
    elif kind == "rope":
        x = x0
        while x < x1 - 3:
            pen.line(x, y - 1.6, x + 2.6, y + 1.6, weight=r.light_pt)
            x += 3.4
    elif kind == "star":
        pen.line(x0, y + 1.3, cx - 9, y + 1.3, weight=r.light_pt)
        pen.line(x0, y - 1.3, cx - 9, y - 1.3, weight=r.light_pt)
        pen.line(cx + 9, y + 1.3, x1, y + 1.3, weight=r.light_pt)
        pen.line(cx + 9, y - 1.3, x1, y - 1.3, weight=r.light_pt)
        _star(pen, cx, y, 5.0, fill=g.tint, weight=r.light_pt + 0.2)
    else:  # dots
        x = x0
        while x <= x1:
            pen.circle(x, y, 0.8, fill=SOFT, weight=r.light_pt, color=SOFT)
            x += 5.5


def header(pen: Pen, box: Box, g: Genre, title: str, subtitle: str, motif: str,
           accent_image: str | None = None) -> None:
    """Title, subtitle, emblems either side and the divider, inside `box`."""
    emblem = min(26.0, box.h - 10)
    title_size = 19.0
    ty = box.top - 6 - title_size * 0.72
    side = emblem + 10
    max_w = box.w - 2 * side
    pen.text(box.cx, ty, _upper(title, g.upper_title), size=title_size, font=g.title_font, align="center",
             max_w=max_w)
    if subtitle:
        pen.text(box.cx, ty - 13, subtitle, size=8.5, font=g.label_font, align="center", color=SOFT, max_w=max_w)
    ey = box.top - 4 - emblem / 2
    if accent_image:
        pen.image(accent_image, Box(box.x + 2, ey - emblem / 2, emblem, emblem))
        pen.image(accent_image, Box(box.right - 2 - emblem, ey - emblem / 2, emblem, emblem))
    else:
        emblem_at(pen, motif, box.x + 2 + emblem / 2, ey, emblem, g)
        emblem_at(pen, motif, box.right - 2 - emblem / 2, ey, emblem, g, mirror=True)
    divider(pen, box.x + side, box.right - side, box.y + 3, g)


# --- section heads and boxes ----------------------------------------------------------------

def section_box(pen: Pen, box: Box, g: Genre) -> None:
    r = pen.rules
    if g.chamfer:
        pen.polygon(_chamfered(box, g.chamfer), weight=r.rule_pt)
    else:
        pen.rect(box, weight=r.rule_pt, radius=g.radius)


def section_head(pen: Pen, box: Box, g: Genre, title: str, *, tints: bool = True) -> Box:
    """Draw a section's frame and heading. Returns the content box under the heading."""
    r = pen.rules
    head, body = box.take_top(HEAD_H)
    room = head.w - {"band": 10, "tab": 18, "bracket": 16}.get(g.heads, 20)
    text = _upper(title, g.upper_heads)
    if text != title and text_width(text, g.head_font, r.min_font_pt) > room:
        text = title                        # mixed case is narrower than capitals
    fill = g.tint if tints else (0.92, 0.92, 0.92)
    size = 8.0
    if g.heads == "band":
        pen.rect(head, fill=fill, stroke=False, radius=0)
        section_box(pen, box, g)
        pen.line(box.x, head.y, box.right, head.y, weight=r.light_pt)
        pen.text(head.x + 5, head.y + 3.6, text, size=size, font=g.head_font, max_w=head.w - 10)
    elif g.heads == "tab":
        section_box(pen, box, g)
        w = min(head.w - 8, pen.fit_width(text, g.head_font, size, head.w - 18) + 12)
        tab = Box(head.x + 4, head.y + 1.5, w, head.h - 3)
        pen.rect(tab, fill=fill, stroke=False, radius=3)
        pen.text(tab.x + 6, head.y + 3.6, text, size=size, font=g.head_font, max_w=head.w - 18)
    elif g.heads == "bracket":
        section_box(pen, box, g)
        pen.text(head.x + 6, head.y + 3.6, text, size=size, font=g.head_font, max_w=head.w - 16)
        L = 9.0
        for x, y, sx, sy in _corners(box, 2.0):
            pen.line(x, y, x + sx * L, y, weight=r.rule_pt + 0.8, color=g.accent)
            pen.line(x, y, x, y + sy * L, weight=r.rule_pt + 0.8, color=g.accent)
        pen.line(box.x + 4, head.y, box.right - 4, head.y, weight=r.light_pt, color=LIGHT)
    else:  # rule
        section_box(pen, box, g)
        w = pen.text(head.x + 5, head.y + 3.6, text, size=size, font=g.head_font, max_w=head.w - 20)
        pen.line(head.x + 9 + w, head.y + 6.2, head.right - 5, head.y + 6.2, weight=r.light_pt)
    return body


# --- emblems ------------------------------------------------------------------------------------

def emblem_at(pen: Pen, name: str, x: float, y: float, size: float, g: Genre, mirror: bool = False) -> None:
    fn = EMBLEMS.get(name) or EMBLEMS["diamond"]
    fn(pen, x, y, size / 2, g, -1 if mirror else 1)


def _e_diamond(pen, x, y, s, g, m):
    _diamond(pen, x, y, s * 0.9, fill=g.tint, weight=pen.rules.rule_pt)
    _diamond(pen, x, y, s * 0.45, fill=INK, weight=pen.rules.light_pt)


def _e_shield(pen, x, y, s, g, m):
    w = s * 0.8
    pts = [(x - w, y + s * 0.85), (x + w, y + s * 0.85), (x + w, y + s * 0.1), (x + w * 0.6, y - s * 0.55),
           (x, y - s * 0.95), (x - w * 0.6, y - s * 0.55), (x - w, y + s * 0.1)]
    pen.polygon(pts, fill=g.tint, weight=pen.rules.rule_pt)
    pen.line(x, y + s * 0.85, x, y - s * 0.95, weight=pen.rules.light_pt)
    pen.line(x - w, y + s * 0.25, x + w, y + s * 0.25, weight=pen.rules.light_pt)


def _e_swords(pen, x, y, s, g, m):
    for d in (1, -1):
        x0, y0, x1, y1 = x - d * s * 0.8, y - s * 0.8, x + d * s * 0.8, y + s * 0.85
        pen.line(x0, y0, x1, y1, weight=pen.rules.rule_pt + 0.4)
        length = math.hypot(x1 - x0, y1 - y0)
        px, py = -(y1 - y0) / length, (x1 - x0) / length
        gx, gy = x0 + (x1 - x0) * 0.22, y0 + (y1 - y0) * 0.22
        pen.line(gx - px * s * 0.26, gy - py * s * 0.26, gx + px * s * 0.26, gy + py * s * 0.26,
                 weight=pen.rules.rule_pt)
    pen.circle(x, y + s * 0.05, s * 0.12, fill=INK, weight=pen.rules.light_pt)


def _e_crown(pen, x, y, s, g, m):
    pts = [(x - s * 0.85, y - s * 0.5), (x + s * 0.85, y - s * 0.5), (x + s * 0.85, y + s * 0.45),
           (x + s * 0.42, y + 0.0), (x, y + s * 0.65), (x - s * 0.42, y + 0.0), (x - s * 0.85, y + s * 0.45)]
    pen.polygon(pts, fill=g.tint, weight=pen.rules.rule_pt)
    for px in (-0.85, 0.0, 0.85):
        pen.circle(x + px * s, y + (s * 0.6 if px else s * 0.78), s * 0.1, fill=INK, weight=pen.rules.light_pt)
    pen.line(x - s * 0.85, y - s * 0.3, x + s * 0.85, y - s * 0.3, weight=pen.rules.light_pt)


def _e_skull(pen, x, y, s, g, m):
    pen.ellipse(x - s * 0.62, y - s * 0.25, x + s * 0.62, y + s * 0.8, fill=g.tint, weight=pen.rules.rule_pt)
    pen.rect(Box(x - s * 0.34, y - s * 0.62, s * 0.68, s * 0.42), fill=g.tint, weight=pen.rules.rule_pt, radius=1.5)
    for dx in (-0.25, 0.25):
        pen.circle(x + dx * s, y + s * 0.25, s * 0.15, fill=INK, weight=pen.rules.light_pt)
    for dx in (-0.12, 0.12):
        pen.line(x + dx * s, y - s * 0.62, x + dx * s, y - s * 0.35, weight=pen.rules.light_pt)


def _e_dagger(pen, x, y, s, g, m):
    pen.polygon([(x, y + s * 0.95), (x + s * 0.16, y - s * 0.1), (x - s * 0.16, y - s * 0.1)], fill=g.tint,
                weight=pen.rules.rule_pt)
    pen.line(x - s * 0.45, y - s * 0.12, x + s * 0.45, y - s * 0.12, weight=pen.rules.rule_pt + 0.4)
    pen.line(x, y - s * 0.12, x, y - s * 0.75, weight=pen.rules.rule_pt + 0.8)
    pen.circle(x, y - s * 0.82, s * 0.1, fill=INK, weight=pen.rules.light_pt)


def _e_moon(pen, x, y, s, g, m):
    pen.circle(x, y, s * 0.82, fill=g.tint, weight=pen.rules.rule_pt)
    pen.circle(x + m * s * 0.32, y + s * 0.18, s * 0.6, fill=(1, 1, 1), weight=pen.rules.light_pt)
    _star(pen, x - m * s * 0.55, y - s * 0.4, s * 0.16, 4, 0.4, fill=INK, weight=pen.rules.light_pt)


def _e_planet(pen, x, y, s, g, m):
    pen.circle(x, y, s * 0.5, fill=g.tint, weight=pen.rules.rule_pt)
    pen.ellipse(x - s * 0.95, y - s * 0.22, x + s * 0.95, y + s * 0.22, weight=pen.rules.light_pt + 0.2)
    pen.circle(x + m * s * 0.7, y + s * 0.65, s * 0.08, fill=INK, weight=pen.rules.light_pt)


def _e_star4(pen, x, y, s, g, m):
    _star(pen, x, y, s * 0.9, 4, 0.3, fill=g.tint, weight=pen.rules.rule_pt)
    _star(pen, x + m * s * 0.6, y + s * 0.6, s * 0.22, 4, 0.35, fill=INK, weight=pen.rules.light_pt)


def _e_chevron(pen, x, y, s, g, m):
    for k in range(3):
        yy = y + s * 0.6 - k * s * 0.55
        pen.polygon([(x - s * 0.8, yy - s * 0.25), (x, yy + s * 0.15), (x + s * 0.8, yy - s * 0.25),
                     (x + s * 0.8, yy - s * 0.5), (x, yy - s * 0.1), (x - s * 0.8, yy - s * 0.5)],
                    fill=g.tint if k else INK, weight=pen.rules.light_pt)


def _e_sigil(pen, x, y, s, g, m):
    pen.circle(x, y, s * 0.9, weight=pen.rules.rule_pt)
    pen.circle(x, y, s * 0.72, weight=pen.rules.light_pt)
    angles = [math.pi / 2 + k * 2 * math.pi / 3 for k in range(3)]
    pts = [(x + s * 0.72 * math.cos(a), y + s * 0.72 * math.sin(a)) for a in angles]
    pen.polygon(pts, weight=pen.rules.light_pt)
    pen.circle(x, y, s * 0.12, fill=INK, weight=pen.rules.light_pt)


def _e_spiral(pen, x, y, s, g, m):
    _spiral(pen, x, y, s * 0.85, 2.4, m, 1, pen.rules.rule_pt)


def _e_eye(pen, x, y, s, g, m):
    pen.polygon([(x - s * 0.9, y), (x - s * 0.45, y + s * 0.42), (x + s * 0.45, y + s * 0.42), (x + s * 0.9, y),
                 (x + s * 0.45, y - s * 0.42), (x - s * 0.45, y - s * 0.42)], fill=g.tint, weight=pen.rules.rule_pt)
    pen.circle(x, y, s * 0.3, fill=(1, 1, 1), weight=pen.rules.light_pt)
    pen.circle(x, y, s * 0.13, fill=INK, weight=pen.rules.light_pt)


def _e_chip(pen, x, y, s, g, m):
    pen.rect(Box(x - s * 0.55, y - s * 0.55, s * 1.1, s * 1.1), fill=g.tint, weight=pen.rules.rule_pt)
    for k in (-0.3, 0.0, 0.3):
        pen.line(x + k * s, y + s * 0.55, x + k * s, y + s * 0.85, weight=pen.rules.light_pt)
        pen.line(x + k * s, y - s * 0.55, x + k * s, y - s * 0.85, weight=pen.rules.light_pt)
        pen.line(x + s * 0.55, y + k * s, x + s * 0.85, y + k * s, weight=pen.rules.light_pt)
        pen.line(x - s * 0.55, y + k * s, x - s * 0.85, y + k * s, weight=pen.rules.light_pt)
    pen.rect(Box(x - s * 0.22, y - s * 0.22, s * 0.44, s * 0.44), fill=INK, weight=pen.rules.light_pt)


def _e_bolt(pen, x, y, s, g, m):
    pts = [(x + m * s * 0.15, y + s * 0.9), (x - m * s * 0.5, y - s * 0.05), (x - m * s * 0.05, y - s * 0.05),
           (x - m * s * 0.2, y - s * 0.9), (x + m * s * 0.5, y + s * 0.15), (x + m * s * 0.05, y + s * 0.15)]
    pen.polygon(pts, fill=g.tint, weight=pen.rules.rule_pt)


def _e_bars(pen, x, y, s, g, m):
    for k, ww in enumerate((1.6, 1.1, 1.4, 0.7)):
        yy = y + s * 0.6 - k * s * 0.42
        x0 = x - s * 0.8 if m > 0 else x + s * 0.8 - s * ww
        pen.rect(Box(x0, yy - s * 0.12, s * ww, s * 0.24), fill=INK if k % 2 else g.tint, weight=pen.rules.light_pt)


def _e_compass(pen, x, y, s, g, m):
    pen.circle(x, y, s * 0.9, weight=pen.rules.light_pt + 0.2)
    _star(pen, x, y, s * 0.82, 4, 0.22, fill=g.tint, weight=pen.rules.rule_pt)
    _star(pen, x, y, s * 0.5, 4, 0.3, fill=INK, weight=pen.rules.light_pt)


def _e_anchor(pen, x, y, s, g, m):
    pen.line(x, y + s * 0.6, x, y - s * 0.75, weight=pen.rules.rule_pt + 0.6)
    pen.circle(x, y + s * 0.72, s * 0.15, weight=pen.rules.rule_pt)
    pen.line(x - s * 0.35, y + s * 0.35, x + s * 0.35, y + s * 0.35, weight=pen.rules.rule_pt)
    pen.curve([(x - s * 0.75, y - s * 0.25), (x - s * 0.5, y - s * 0.65), (x, y - s * 0.8), (x + s * 0.5, y - s * 0.65),
               (x + s * 0.75, y - s * 0.25)], weight=pen.rules.rule_pt + 0.4)


def _e_wheel(pen, x, y, s, g, m):
    pen.circle(x, y, s * 0.62, weight=pen.rules.rule_pt + 0.4)
    pen.circle(x, y, s * 0.18, fill=g.tint, weight=pen.rules.light_pt)
    for k in range(8):
        a = k * math.pi / 4
        pen.line(x + s * 0.18 * math.cos(a), y + s * 0.18 * math.sin(a), x + s * 0.92 * math.cos(a),
                 y + s * 0.92 * math.sin(a), weight=pen.rules.light_pt + 0.3)


def _e_star5(pen, x, y, s, g, m):
    pen.circle(x, y, s * 0.92, weight=pen.rules.light_pt)
    _star(pen, x, y, s * 0.82, 5, 0.45, fill=g.tint, weight=pen.rules.rule_pt)
    pen.circle(x, y, s * 0.12, fill=INK, weight=pen.rules.light_pt)


def _e_horseshoe(pen, x, y, s, g, m):
    pts = [(x - s * 0.55, y - s * 0.8), (x - s * 0.75, y + s * 0.1), (x - s * 0.45, y + s * 0.7), (x, y + s * 0.85),
           (x + s * 0.45, y + s * 0.7), (x + s * 0.75, y + s * 0.1), (x + s * 0.55, y - s * 0.8)]
    pen.curve(pts, weight=pen.rules.rule_pt + 1.2)
    for px, py in ((-0.62, 0.0), (0.62, 0.0), (-0.4, 0.55), (0.4, 0.55)):
        pen.circle(x + px * s, y + py * s, s * 0.06, fill=(1, 1, 1), weight=pen.rules.light_pt)


def _e_cactus(pen, x, y, s, g, m):
    pen.rect(Box(x - s * 0.17, y - s * 0.85, s * 0.34, s * 1.7), fill=g.tint, weight=pen.rules.rule_pt, radius=s * 0.17)
    pen.rect(Box(x - s * 0.62, y - s * 0.1, s * 0.26, s * 0.65), fill=g.tint, weight=pen.rules.light_pt + 0.2,
             radius=s * 0.13)
    pen.rect(Box(x + s * 0.36, y - s * 0.3, s * 0.26, s * 0.6), fill=g.tint, weight=pen.rules.light_pt + 0.2,
             radius=s * 0.13)
    pen.line(x - s * 0.36, y - s * 0.05, x - s * 0.17, y - s * 0.05, weight=pen.rules.light_pt + 0.2)
    pen.line(x + s * 0.17, y - s * 0.25, x + s * 0.36, y - s * 0.25, weight=pen.rules.light_pt + 0.2)


def _e_leaf(pen, x, y, s, g, m):
    _leaf(pen, x - m * s * 0.55, y - s * 0.6, s * 1.5, 50 if m > 0 else 130, fill=g.tint, weight=pen.rules.rule_pt)
    _leaf(pen, x - m * s * 0.2, y - s * 0.75, s * 0.9, 95 if m > 0 else 85, fill=(1, 1, 1), weight=pen.rules.light_pt)


def _e_mushroom(pen, x, y, s, g, m):
    pen.rect(Box(x - s * 0.2, y - s * 0.85, s * 0.4, s * 0.85), fill=(1, 1, 1), weight=pen.rules.light_pt + 0.2,
             radius=s * 0.12)
    pen.polygon([(x - s * 0.9, y - s * 0.02), (x - s * 0.62, y + s * 0.55), (x, y + s * 0.82),
                 (x + s * 0.62, y + s * 0.55),
                 (x + s * 0.9, y - s * 0.02)], fill=g.tint, weight=pen.rules.rule_pt)
    for px, py in ((-0.4, 0.3), (0.25, 0.5), (0.5, 0.15)):
        pen.circle(x + px * s, y + py * s, s * 0.1, fill=(1, 1, 1), weight=pen.rules.light_pt)


def _e_mug(pen, x, y, s, g, m):
    pen.rect(Box(x - s * 0.6, y - s * 0.75, s * 0.95, s * 1.1), fill=g.tint, weight=pen.rules.rule_pt, radius=2)
    pen.curve([(x + s * 0.35, y + s * 0.15), (x + s * 0.75, y + s * 0.05), (x + s * 0.72, y - s * 0.4),
               (x + s * 0.35, y - s * 0.45)], weight=pen.rules.rule_pt)
    for dx in (-0.35, -0.1):
        pen.curve([(x + dx * s, y + s * 0.45), (x + (dx + 0.08) * s, y + s * 0.65), (x + dx * s, y + s * 0.88)],
                  weight=pen.rules.light_pt)


EMBLEMS = {"diamond": _e_diamond, "shield": _e_shield, "swords": _e_swords, "crown": _e_crown, "skull": _e_skull,
           "dagger": _e_dagger, "moon": _e_moon, "planet": _e_planet, "star4": _e_star4, "chevron": _e_chevron,
           "sigil": _e_sigil, "spiral": _e_spiral, "eye": _e_eye, "chip": _e_chip, "bolt": _e_bolt, "bars": _e_bars,
           "compass": _e_compass, "anchor": _e_anchor, "wheel": _e_wheel, "star5": _e_star5,
           "horseshoe": _e_horseshoe, "cactus": _e_cactus, "leaf": _e_leaf, "mushroom": _e_mushroom, "mug": _e_mug}


# --- dungeon map symbols ------------------------------------------------------------------------

def symbol(pen: Pen, name: str, x: float, y: float, s: float) -> None:
    """A map-key symbol centered at (x, y), about s points across (our own icons)."""
    r = pen.rules
    h = s / 2
    if name == "door":
        pen.line(x - h, y, x + h, y, weight=r.rule_pt + 0.6)
        pen.rect(Box(x - h * 0.45, y - h * 0.35, h * 0.9, h * 0.7), fill=(1, 1, 1), weight=r.light_pt + 0.2)
    elif name == "locked_door":
        pen.line(x - h, y, x + h, y, weight=r.rule_pt + 0.6)
        pen.rect(Box(x - h * 0.45, y - h * 0.35, h * 0.9, h * 0.7), fill=INK, weight=r.light_pt + 0.2)
    elif name == "secret_door":
        pen.line(x - h, y, x + h, y, weight=r.rule_pt + 0.6)
        pen.text(x, y - 2.5, "S", size=7.0, font="Helvetica-Bold", align="center")
    elif name == "stairs":
        for k in range(5):
            xx = x - h + k * s / 4
            pen.line(xx, y - h * 0.7, xx, y + h * 0.7, weight=r.light_pt + 0.2)
        pen.rect(Box(x - h, y - h * 0.7, s, h * 1.4), weight=r.light_pt + 0.2)
    elif name == "trap":
        pen.rect(Box(x - h * 0.8, y - h * 0.8, h * 1.6, h * 1.6), weight=r.light_pt + 0.2)
        pen.line(x - h * 0.8, y - h * 0.8, x + h * 0.8, y + h * 0.8, weight=r.light_pt + 0.2)
        pen.line(x - h * 0.8, y + h * 0.8, x + h * 0.8, y - h * 0.8, weight=r.light_pt + 0.2)
    elif name == "pillar":
        pen.circle(x, y, h * 0.6, fill=INK, weight=r.light_pt)
    elif name == "water":
        for k in (-0.4, 0.0, 0.4):
            pen.curve([(x - h, y + k * h), (x - h / 2, y + k * h + 1.5), (x, y + k * h), (x + h / 2, y + k * h - 1.5),
                       (x + h, y + k * h)], weight=r.light_pt + 0.1)
    elif name == "rubble":
        for px, py, rr in ((-0.5, -0.3, 0.22), (0.2, -0.45, 0.18), (0.45, 0.25, 0.25), (-0.15, 0.35, 0.16)):
            pen.circle(x + px * h, y + py * h, rr * h, fill=(1, 1, 1), weight=r.light_pt)
    else:  # chest
        pen.rect(Box(x - h * 0.8, y - h * 0.55, h * 1.6, h * 1.1), fill=(1, 1, 1), weight=r.light_pt + 0.2, radius=1)
        pen.line(x - h * 0.8, y + h * 0.1, x + h * 0.8, y + h * 0.1, weight=r.light_pt)


SYMBOLS = (("door", "Door"), ("locked_door", "Locked Door"), ("secret_door", "Secret Door"), ("stairs", "Stairs"),
           ("trap", "Trap"), ("pillar", "Pillar"), ("water", "Water"), ("rubble", "Rubble"), ("chest", "Treasure"))
