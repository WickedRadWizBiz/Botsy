"""Sections and the page flow: how a page's sections are measured, placed and drawn.

A page spec holds a list of sections (dicts with a `kind`, see KINDS) and a layout recipe
({"cols": n, "strip": bool}). The flow lays them out on the page body for the page size it
renders at, so US Letter and A4 each get their own row, line and cell counts:

1. leading untitled `lines` sections (name, player, ...) go across the top;
2. with "strip", the first `scores` section becomes a narrow column of score boxes on the
   left (anything left below it becomes a notes area);
3. the rest becomes bands, top to bottom: a full-width section (span: full, lines, scores,
   grid) is its own band; runs of other sections share a band of `cols` columns, split in
   reading order so the tallest column is as short as possible (or, when that cannot fit,
   each section in the shortest column);
4. spare height goes to the flexible sections (tables gain rows, notes and prompts gain
   lines, grids gain cells, cards grow); a column with no flexible section gets a notes
   area when the spare height is big enough for one.

A page whose sections cannot fit at the print rules raises LayoutError (never shrinks a
line gap or a font below the floor).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from generators.ttrpg import decor
from generators.ttrpg.layout import FAINT, INK, LIGHT, MM, SOFT, Box, Pen, text_width
from generators.ttrpg.styles import Genre

GAP = 7.0              # between sections
PAD = 4.0              # inside a section box
STRIP_W = 86.0         # the score strip of a "strip" layout
HEAD = decor.HEAD_H
FRAME_V = HEAD + 2.0 + PAD
LABEL_PT = 7.0
TEXT_PT = 7.5
KINDS = ("lines", "scores", "boxes", "checklist", "track", "table", "slots", "notes", "prompts", "cards", "grid",
         "sketch", "legend", "tally", "rounds")
FLEX = {"table": 1.0, "notes": 1.0, "prompts": 1.0, "cards": 1.0, "grid": 3.0}
NO_PAD = ("table",)


class LayoutError(ValueError):
    """The sections do not fit the page at the print rules."""


@dataclass
class Ctx:
    pen: Pen
    genre: Genre
    tints: bool = True

    @property
    def L(self) -> float:          # writing-line gap
        return self.pen.rules.line_gap_pt

    @property
    def G(self) -> float:          # row gap
        return self.pen.rules.row_gap_pt


# --- per-kind geometry ----------------------------------------------------------------------------

def framed(sec: dict) -> bool:
    return sec["kind"] not in ("lines", "grid") or bool(sec.get("title"))


def flex(sec: dict) -> float:
    if "flex" in sec:
        return max(0.0, float(sec.get("flex") or 0))
    return FLEX.get(sec["kind"], 0.0)


def _n(sec: dict, key: str = "fields") -> int:
    return len(sec.get(key) or [])


def _lines_cols(sec: dict) -> int:
    return max(1, min(int(sec.get("cols") or min(_n(sec), 3)), _n(sec)))


def _score_per_row(sec: dict, w: float) -> int:
    return max(1, min(_n(sec), int(w // 56)))


def _score_h(sec: dict) -> float:
    return 56.0 if sec.get("sub") else 44.0


def _fit_cols(labels: list[str], w: float, font: str, want: int, *, pad: float = 7.0, gap: float = 4.0) -> int:
    """At most `want` cells per row, few enough that the widest label fits at LABEL_PT."""
    need = max((text_width(str(t), font, LABEL_PT) for t in labels), default=0.0) + pad
    fit = int((w + gap) // (max(need, 44.0) + gap))
    return max(1, min(want, len(labels), fit))


def _box_cols(ctx: Ctx, sec: dict, w: float) -> int:
    n = _n(sec)
    want = int(sec.get("cols") or min(n, max(1, int(w // 76))))
    return _fit_cols(list(sec["fields"]), w, ctx.genre.label_font, want)


def _track_item_w(ctx: Ctx, f: dict) -> float:
    return text_width(str(f["label"]), ctx.genre.label_font, TEXT_PT) + int(f["count"]) * 11.5 + 18


def _track_per_row(ctx: Ctx, sec: dict, w: float) -> int:
    widest = max(_track_item_w(ctx, f) for f in sec["fields"])
    return max(1, min(_n(sec), int(w // widest)))


def _slot_cols(sec: dict, w: float) -> int:
    levels = int(sec.get("levels") or 1)
    if w < 300:
        return 1
    return min(levels, 5 if levels > 9 else 3)


def _card_w(sec: dict, w: float) -> float:
    cols = max(1, int(sec.get("cols") or 2))
    return (w - (cols - 1) * 6) / cols


def _card_min(ctx: Ctx, card: list, w: float) -> float:
    inner = w - 2 * (PAD + 1)
    return sum(_el_h(ctx, el, inner) for el in card) + 2 * PAD + 2


def _el_boxes(ctx: Ctx, el: dict, w: float) -> tuple[int, int]:
    labels = list(el.get("labels") or [])
    per = _fit_cols(labels, w, ctx.genre.label_font, len(labels), pad=6.0)
    return per, math.ceil(len(labels) / per)


def _el_h(ctx: Ctx, el: dict, w: float) -> float:
    t = el.get("t")
    if t == "line":
        return ctx.L
    if t == "lines":
        return 10 + int(el.get("n", 1)) * ctx.L
    if t == "boxes":
        return 29.0 * _el_boxes(ctx, el, w)[1] + 1
    if t in ("checks", "uses"):
        return ctx.G
    return 0.0          # portrait: beside the first elements


def content_min(ctx: Ctx, sec: dict, w: float) -> float:
    """The smallest content height of a section `w` wide (without its frame)."""
    kind = sec["kind"]
    L, G = ctx.L, ctx.G
    if kind == "lines":
        rows = math.ceil(_n(sec) / _lines_cols(sec))
        return rows * (L + 10)
    if kind == "scores":
        per = _score_per_row(sec, w)
        rows = math.ceil(_n(sec) / per)
        return rows * _score_h(sec) + (rows - 1) * 4
    if kind == "boxes":
        rows = math.ceil(_n(sec) / _box_cols(ctx, sec, w))
        return rows * 30 + (rows - 1) * 4
    if kind == "checklist":
        cols = max(1, int(sec.get("cols") or 1))
        header = 10 if (sec.get("marks") or sec.get("values")) else 0
        return header + math.ceil(_n(sec) / cols) * G
    if kind == "track":
        return math.ceil(_n(sec) / _track_per_row(ctx, sec, w)) * G
    if kind == "table":
        return 13 + int(sec.get("rows") or 3) * G
    if kind == "slots":
        cols = _slot_cols(sec, w)
        levels = int(sec.get("levels") or 1)
        if cols == 1:
            return levels * max(G, 24.0)
        rows = math.ceil(levels / cols)
        return rows * 26 + (rows - 1) * 4
    if kind == "notes":
        return int(sec.get("lines") or 3) * L
    if kind == "prompts":
        n = _n(sec)
        return n * (10 + int(sec.get("lines") or 2) * L) + (n - 1) * 3
    if kind == "cards":
        cols = max(1, int(sec.get("cols") or 2))
        rows = math.ceil(int(sec.get("count") or 1) / cols)
        return rows * _card_min(ctx, sec.get("card") or [], _card_w(sec, w)) + (rows - 1) * 6
    if kind == "grid":
        return 4 * float(sec.get("pitch_in") or 0.25) * 72
    if kind == "sketch":
        return max(float(sec.get("min_mm") or 30) * MM, 40.0)
    if kind == "legend":
        per = max(1, int(w // 92))
        return math.ceil(len(decor.SYMBOLS) / per) * G
    if kind == "tally":
        n = _n(sec, "dice")
        return n * 30 + (n - 1) * 3
    if kind == "rounds":
        return 20.0
    raise LayoutError(f"unknown section kind {kind!r}")


def height(ctx: Ctx, sec: dict, w: float) -> float:
    """The smallest height of a section `w` wide, frame included."""
    inner_w = w - (0 if sec["kind"] in NO_PAD else 2 * PAD)
    h = content_min(ctx, sec, inner_w)
    return h + FRAME_V if framed(sec) else h


# --- drawing ---------------------------------------------------------------------------------------

def draw(ctx: Ctx, sec: dict, box: Box) -> None:
    if framed(sec):
        body = decor.section_head(ctx.pen, box, ctx.genre, str(sec.get("title") or ""), tints=ctx.tints)
        if sec["kind"] in NO_PAD:
            content = body
        else:
            content = Box(body.x + PAD, body.y + PAD, max(body.w - 2 * PAD, 0), max(body.h - PAD - 2, 0))
    else:
        content = box
    DRAW[sec["kind"]](ctx, sec, content)


def _label(ctx: Ctx, x: float, y: float, s: str, *, max_w: float, size: float = LABEL_PT, color=SOFT,
           bold: bool = False, align: str = "left") -> float:
    font = ctx.genre.bold_font if bold else ctx.genre.label_font
    return ctx.pen.text(x, y, s, size=size, font=font, color=color, max_w=max_w, align=align)


def draw_lines(ctx: Ctx, sec: dict, box: Box) -> None:
    pen, L = ctx.pen, ctx.L
    fields = list(sec["fields"])
    cols = _lines_cols(sec)
    rows = math.ceil(len(fields) / cols)
    row_h = L + 10
    for i, label in enumerate(fields):
        r, c = divmod(i, cols)
        # the last row's fields share the full width
        in_row = cols if r < rows - 1 else len(fields) - cols * (rows - 1)
        cw = (box.w - (in_row - 1) * 10) / in_row
        x = box.x + c * (cw + 10)
        y_line = box.top - r * row_h - L
        pen.line(x, y_line, x + cw, y_line, weight=pen.rules.light_pt + 0.2)
        _label(ctx, x, y_line - 8, label, max_w=cw)
    pen.gap(L)


def _score_upper(ctx: Ctx, labels: list[str], cell_w: float) -> bool:
    """Capitals for every score label of a section, or for none (mixed case is narrower)."""
    return ctx.genre.upper_heads and all(
        text_width(t.upper(), ctx.genre.bold_font, ctx.pen.rules.min_font_pt) <= cell_w - 6 for t in labels)


def _score_box(ctx: Ctx, cell: Box, label: str, sub: list[str], upper: bool = False) -> None:
    pen = ctx.pen
    pen.rect(cell, radius=ctx.genre.radius + 2, weight=pen.rules.rule_pt)
    _label(ctx, cell.cx, cell.top - 9.5, label.upper() if upper else label, max_w=cell.w - 6, size=TEXT_PT,
           color=INK, bold=True, align="center")
    sub_h = 22.0 if sub else 0.0
    value = Box(cell.x + 6, cell.y + sub_h + 4, cell.w - 12, max(cell.h - 13 - sub_h - 6, 6))
    pen.rect(value, weight=pen.rules.light_pt, color=LIGHT, radius=3)
    if sub:
        # Small square-cornered boxes side by side (our own look; no score "ovals"), a gap
        # between them so two sub values never touch in a narrow cell.
        k = len(sub)
        slots = Box(cell.x + 4, cell.y + 3, cell.w - 8, 19).cols(k, 4)
        for slot, name in zip(slots, sub):
            ow = max(min(slot.w - 2, 34.0), 6.0)
            pen.rect(Box(slot.cx - ow / 2, slot.y + 8, ow, 11), weight=pen.rules.light_pt + 0.2, radius=1.0)
            _label(ctx, slot.cx, slot.y + 1.2, name, max_w=slot.w, align="center")


def draw_scores(ctx: Ctx, sec: dict, box: Box) -> None:
    fields, sub = list(sec["fields"]), list(sec.get("sub") or [])
    per = _score_per_row(sec, box.w)
    rows = math.ceil(len(fields) / per)
    row_boxes = box.rows(rows, 4)
    upper = _score_upper(ctx, fields, (box.w - (per - 1) * 5) / per)
    for r, rb in enumerate(row_boxes):
        chunk = fields[r * per:(r + 1) * per]
        for cell, label in zip(rb.cols(per, 5)[:len(chunk)], chunk):
            _score_box(ctx, cell, label, sub, upper)


def draw_score_strip(ctx: Ctx, sec: dict, box: Box) -> Box:
    """Score boxes stacked in a narrow column; returns the space left below them."""
    fields, sub = list(sec["fields"]), list(sec.get("sub") or [])
    n = len(fields)
    min_box = _score_h(sec)
    need = FRAME_V + n * min_box + (n - 1) * 4
    if box.h + 0.01 < need:
        raise LayoutError(f"score strip needs {need:.0f} pt, has {box.h:.0f}")
    each = min(66.0, (box.h - FRAME_V - (n - 1) * 4) / n)
    h = FRAME_V + n * each + (n - 1) * 4
    sbox, rest = box.take_top(h, GAP)
    body = decor.section_head(ctx.pen, sbox, ctx.genre, str(sec.get("title") or ""), tints=ctx.tints)
    content = Box(body.x + PAD, body.y + PAD, body.w - 2 * PAD, body.h - PAD - 2)
    upper = _score_upper(ctx, fields, content.w)
    for cell, label in zip(content.rows(n, 4), fields):
        _score_box(ctx, cell, label, sub, upper)
    return rest


def draw_boxes(ctx: Ctx, sec: dict, box: Box) -> None:
    pen = ctx.pen
    fields = list(sec["fields"])
    cols = _box_cols(ctx, sec, box.w)
    rows = math.ceil(len(fields) / cols)
    cells = Box(box.x, box.top - (rows * 30 + (rows - 1) * 4), box.w, rows * 30 + (rows - 1) * 4).grid(cols, rows, 4)
    for cell, label in zip(cells, fields):
        pen.rect(cell, radius=ctx.genre.radius, weight=pen.rules.light_pt + 0.2)
        _label(ctx, cell.x + 3.5, cell.top - 8.5, label, max_w=cell.w - 7)


def draw_checklist(ctx: Ctx, sec: dict, box: Box) -> None:
    pen, G = ctx.pen, ctx.G
    fields = list(sec["fields"])
    marks, values = list(sec.get("marks") or []), list(sec.get("values") or [])
    cols = max(1, int(sec.get("cols") or 1))
    per = math.ceil(len(fields) / cols)
    header = 10 if (marks or values) else 0
    col_boxes = box.cols(cols, 10)
    mark_w = 9.0
    for c, cb in enumerate(col_boxes):
        chunk = fields[c * per:(c + 1) * per]
        if not chunk:
            continue
        left = len(marks) * mark_w if marks else 9.0
        vbox_w = 24.0
        if header:
            for k, mk in enumerate(marks):
                _label(ctx, cb.x + k * mark_w + 3.7, cb.top - 7.5, mk, max_w=mark_w, align="center")
            for k, v in enumerate(values):
                vx = cb.right - (len(values) - k) * (vbox_w + 3) + 3
                _label(ctx, vx + vbox_w / 2, cb.top - 7.5, v, max_w=vbox_w + 2, align="center")
            if marks and not values:
                _label(ctx, cb.x + left + 4 + vbox_w / 2, cb.top - 7.5, "Bonus", max_w=vbox_w + 4, align="center")
        for i, label in enumerate(chunk):
            top = cb.top - header - i * G
            cy = top - G / 2
            if marks:
                for k in range(len(marks)):
                    pen.checkbox(cb.x + k * mark_w + 0.5, cy - 3.6, 7.2)
            else:
                pen.checkbox(cb.x + 0.5, cy - 3.6, 7.2, round_=True)
            box_h = min(G - 5, 13.0)
            if values:
                for k in range(len(values)):
                    vx = cb.right - (len(values) - k) * (vbox_w + 3) + 3
                    pen.rect(Box(vx, cy - box_h / 2, vbox_w, box_h), weight=pen.rules.light_pt, radius=1.5)
                lx = cb.x + left + 5
                max_w = cb.right - len(values) * (vbox_w + 3) - lx - 2
            else:
                pen.rect(Box(cb.x + left + 4, cy - box_h / 2, vbox_w, box_h), weight=pen.rules.light_pt, radius=1.5)
                lx = cb.x + left + 4 + vbox_w + 5
                max_w = cb.right - lx - 1
            _label(ctx, lx, cy - 2.6, label, max_w=max_w, size=TEXT_PT, color=INK)
            pen.line(lx, top - G + 1.5, cb.right, top - G + 1.5, color=FAINT)
    pen.gap(G)


def draw_track(ctx: Ctx, sec: dict, box: Box) -> None:
    pen, G = ctx.pen, ctx.G
    fields = list(sec["fields"])
    per = _track_per_row(ctx, sec, box.w)
    cw = box.w / per
    for i, f in enumerate(fields):
        r, c = divmod(i, per)
        x = box.x + c * cw
        cy = box.top - r * G - G / 2
        w = _label(ctx, x, cy - 2.6, str(f["label"]), max_w=cw - int(f["count"]) * 11.5 - 12, size=TEXT_PT, color=INK)
        bx = x + w + 6
        for k in range(int(f["count"])):
            pen.checkbox(bx + k * 11.5, cy - 4, 8.0, round_=ctx.genre.radius > 0 or ctx.genre.heads == "rule")
    if math.ceil(len(fields) / per) > 1:
        pen.gap(G)


def draw_table(ctx: Ctx, sec: dict, box: Box) -> None:
    pen, G = ctx.pen, ctx.G
    cols = list(sec["columns"])
    weights = [float(w) for w in sec.get("weights") or [1.0] * len(cols)]
    head, body = box.take_top(13)
    fill = ctx.genre.tint if ctx.tints else (0.93, 0.93, 0.93)
    pen.rect(head, fill=fill, stroke=False)
    cells = head.cols(len(cols), 0, weights)
    for cell, name in zip(cells, cols):
        _label(ctx, cell.x + 3, head.y + 3.8, name, max_w=cell.w - 6, size=TEXT_PT, color=INK, bold=True)
    rows = max(1, int((body.h + 0.01) // G))
    y = body.top
    pen.line(box.x, y, box.right, y, weight=pen.rules.light_pt + 0.2)
    for _ in range(rows):
        y -= G
        pen.line(box.x, y, box.right, y, color=LIGHT)
    for cell in cells[1:]:
        pen.line(cell.x, head.top, cell.x, y, color=LIGHT)
    pen.gap(G)


def draw_slots(ctx: Ctx, sec: dict, box: Box) -> None:
    pen = ctx.pen
    levels = int(sec.get("levels") or 1)
    count = int(sec.get("boxes") or 3)
    name = str(sec.get("label") or "Level")
    cols = _slot_cols(sec, box.w)
    if cols == 1:
        cells = box.rows(levels, 0)
    else:
        rows = math.ceil(levels / cols)
        grid_h = rows * 26 + (rows - 1) * 4
        cells = Box(box.x, box.top - grid_h, box.w, grid_h).grid(cols, rows, 4)
    for lvl, cell in zip(range(1, levels + 1), cells):
        pen.rect(cell, weight=pen.rules.light_pt, radius=ctx.genre.radius)
        _label(ctx, cell.x + 4, cell.top - 9.5, f"{name} {lvl}", max_w=cell.w * 0.5, size=TEXT_PT, color=INK,
               bold=True)
        tw = _label(ctx, cell.right - 40, cell.top - 9.5, "Total", max_w=20)
        pen.line(cell.right - 40 + tw + 2, cell.top - 10.5, cell.right - 4, cell.top - 10.5, color=LIGHT)
        size = 8.0
        for k in range(count):
            pen.checkbox(cell.x + 5 + k * (size + 4), cell.y + 4, size, round_=True)


def draw_notes(ctx: Ctx, sec: dict, box: Box) -> None:
    ctx.pen.writing_lines(box)


def draw_prompts(ctx: Ctx, sec: dict, box: Box) -> None:
    L = ctx.L
    fields = list(sec["fields"])
    base = int(sec.get("lines") or 2)
    n = len(fields)
    lines = [base] * n
    spare = box.h - (n * (10 + base * L) + (n - 1) * 3)
    i = 0
    while spare >= L and n:
        lines[i % n] += 1
        spare -= L
        i += 1
    y = box.top
    for label, k in zip(fields, lines):
        _label(ctx, box.x, y - 8, label, max_w=box.w, size=TEXT_PT, color=INK, bold=True)
        ctx.pen.writing_lines(Box(box.x, y - 10 - k * L, box.w, k * L))
        y -= 10 + k * L + 3


def _draw_card(ctx: Ctx, card: list, box: Box) -> None:
    pen, L, G = ctx.pen, ctx.L, ctx.G
    pen.rect(box, radius=ctx.genre.radius + 1, weight=pen.rules.rule_pt)
    inner = box.inset(PAD + 1)
    portrait = next((el for el in card if el.get("t") == "portrait"), None)
    p = min(float(portrait.get("mm", 20)) * MM, inner.h * 0.6) if portrait else 0.0
    if p:
        pbox = Box(inner.right - p, inner.top - p, p, p)
        pen.rect(pbox, weight=pen.rules.light_pt, radius=2)
        _label(ctx, pbox.cx, pbox.y + 3, "Sketch", max_w=p - 4, align="center", color=FAINT)
    elements = [el for el in card if el.get("t") != "portrait"]
    spare = inner.h - sum(_el_h(ctx, el, inner.w) for el in elements)
    # Spare writing lines go round-robin to every multi-line field, fewest lines first
    # (a quest card's Objective and Complications grow together).
    multi = [i for i, el in enumerate(elements) if el.get("t") == "lines"]
    extra = dict.fromkeys(multi, 0)
    for _ in range(int(max(spare, 0.0) // L) if multi else 0):
        i = min(multi, key=lambda j: (int(elements[j].get("n", 1)) + extra[j], j))
        extra[i] += 1
    y = inner.top
    for i, el in enumerate(elements):
        t = el.get("t")
        h = _el_h(ctx, el, inner.w) + extra.get(i, 0) * L
        w = inner.w - (p + 6 if p and inner.top - y < p else 0)
        if t == "line":
            lw = _label(ctx, inner.x, y - L + 4, str(el["label"]), max_w=w * 0.45)
            pen.line(inner.x + lw + 4, y - L + 2.5, inner.x + w, y - L + 2.5, color=LIGHT)
            pen.gap(L)
        elif t == "lines":
            _label(ctx, inner.x, y - 8, str(el["label"]), max_w=w, color=INK, bold=True)
            pen.writing_lines(Box(inner.x, y - h, w, h - 10))
        elif t == "boxes":
            labels = list(el["labels"])
            per, nrows = _el_boxes(ctx, el, inner.w)
            for r in range(nrows):
                chunk = labels[r * per:(r + 1) * per]
                row = Box(inner.x, y - 2 - (r + 1) * 29 + 4, w, 25)
                for cell, name in zip(row.cols(per, 4), chunk):
                    pen.rect(cell, weight=pen.rules.light_pt, radius=ctx.genre.radius)
                    _label(ctx, cell.x + 3, cell.top - 8.5, name, max_w=cell.w - 6)
        elif t == "checks":
            labels = list(el["labels"])
            for cell, name in zip(Box(inner.x, y - G, w, G).cols(len(labels), 4), labels):
                pen.checkbox(cell.x, cell.cy - 3.6, 7.2)
                _label(ctx, cell.x + 11, cell.cy - 2.6, name, max_w=cell.w - 12, size=TEXT_PT, color=INK)
        elif t == "uses":
            lw = _label(ctx, inner.x, y - G / 2 - 2.6, str(el["label"]), max_w=w * 0.4, size=TEXT_PT, color=INK)
            count = int(el.get("count", 6))
            step = min(12.0, (w - lw - 8) / max(count, 1))
            for k in range(count):
                pen.checkbox(inner.x + lw + 6 + k * step, y - G / 2 - 3.6, min(7.2, step - 2), round_=True)
        y -= h


def draw_cards(ctx: Ctx, sec: dict, box: Box) -> None:
    count = int(sec.get("count") or 1)
    cols = max(1, int(sec.get("cols") or 2))
    rows = math.ceil(count / cols)
    for cell in box.grid(cols, rows, 6)[:count]:
        _draw_card(ctx, sec.get("card") or [], cell)


def draw_grid(ctx: Ctx, sec: dict, box: Box) -> None:
    pen = ctx.pen
    shape = str(sec.get("shape") or "square")
    pitch = float(sec.get("pitch_in") or 0.25) * 72
    major = int(sec.get("major") or 0)
    dotted = sec.get("style") == "dotted"
    dash = (1.2, 1.8) if dotted else None
    w8 = pen.rules.light_pt
    if shape in ("square", "dots"):
        nx, ny = int(box.w // pitch), int(box.h // pitch)
        gw, gh = nx * pitch, ny * pitch
        x0, y0 = box.x + (box.w - gw) / 2, box.y + (box.h - gh) / 2
        if shape == "dots":
            for i in range(nx + 1):
                for j in range(ny + 1):
                    pen.circle(x0 + i * pitch, y0 + j * pitch, 0.6, fill=SOFT, color=SOFT, weight=w8)
            pen.rect(Box(x0, y0, gw, gh), weight=w8, color=LIGHT)
        else:
            for i in range(nx + 1):
                heavy = major and i % major == 0
                pen.line(x0 + i * pitch, y0, x0 + i * pitch, y0 + gh, weight=w8 + (0.4 if heavy else 0),
                         color=SOFT if heavy else LIGHT, dash=None if heavy else dash)
            for j in range(ny + 1):
                heavy = major and j % major == 0
                pen.line(x0, y0 + j * pitch, x0 + gw, y0 + j * pitch, weight=w8 + (0.4 if heavy else 0),
                         color=SOFT if heavy else LIGHT, dash=None if heavy else dash)
            pen.rect(Box(x0, y0, gw, gh), weight=pen.rules.rule_pt)
        pen.gap(pitch)
        return
    if shape == "iso":
        row = pitch * math.sqrt(3) / 2
        ny = int(box.h // row)
        nx = int((box.w - pitch / 2) // pitch)
        gw, gh = nx * pitch + pitch / 2, ny * row
        x0, y0 = box.x + (box.w - gw) / 2, box.y + (box.h - gh) / 2
        for j in range(ny + 1):
            off = pitch / 2 if j % 2 else 0.0
            for i in range(nx + 1):
                pen.circle(x0 + off + i * pitch, y0 + j * row, 0.65, fill=SOFT, color=SOFT, weight=w8)
        pen.gap(row)
        return
    # hexes: pitch = distance between parallel sides
    s = pitch
    R = s / math.sqrt(3)
    pointy = shape == "hex_pointy"
    if pointy:
        dx, dy = s, 1.5 * R
        nx = int((box.w - s / 2) // dx)
        ny = int((box.h - 0.5 * R) // dy)
        gw, gh = nx * dx + s / 2, ny * dy + 0.5 * R
    else:
        dx, dy = 1.5 * R, s
        nx = int((box.w - 0.5 * R) // dx)
        ny = int((box.h - s / 2) // dy)
        gw, gh = nx * dx + 0.5 * R, ny * dy + s / 2
    x0, y0 = box.x + (box.w - gw) / 2, box.y + (box.h - gh) / 2
    color = LIGHT if not major else SOFT
    for i in range(nx):
        for j in range(ny):
            if pointy:
                cx = x0 + s / 2 + i * dx + (s / 2 if j % 2 else 0)
                cy = y0 + R + j * dy
                if cx + s / 2 > x0 + gw + 0.01:
                    continue
                pts = [(cx + R * math.cos(math.radians(30 + 60 * k)), cy + R * math.sin(math.radians(30 + 60 * k)))
                       for k in range(6)]
            else:
                cx = x0 + R + i * dx
                cy = y0 + s / 2 + j * dy + (s / 2 if i % 2 else 0)
                if cy + s / 2 > y0 + gh + 0.01:
                    continue
                pts = [(cx + R * math.cos(math.radians(60 * k)), cy + R * math.sin(math.radians(60 * k)))
                       for k in range(6)]
            pen.polygon(pts, weight=w8, color=color)
            if sec.get("centers"):
                pen.circle(cx, cy, 0.6, fill=FAINT, color=FAINT, weight=w8)
    pen.gap(s)


def draw_sketch(ctx: Ctx, sec: dict, box: Box) -> None:
    pen = ctx.pen
    if sec.get("dots"):
        draw_grid(ctx, {"shape": "dots", "pitch_in": 0.25}, box.inset(2))
    else:
        pen.rect(box, weight=pen.rules.light_pt, color=LIGHT, radius=ctx.genre.radius, dash=(2, 2))


def draw_legend(ctx: Ctx, sec: dict, box: Box) -> None:
    G = ctx.G
    per = max(1, int(box.w // 92))
    cw = box.w / per
    for i, (key, name) in enumerate(decor.SYMBOLS):
        r, c = divmod(i, per)
        x = box.x + c * cw
        cy = box.top - r * G - G / 2
        decor.symbol(ctx.pen, key, x + 8, cy, 12.0)
        _label(ctx, x + 19, cy - 2.6, name, max_w=cw - 22, size=TEXT_PT, color=INK)


def draw_tally(ctx: Ctx, sec: dict, box: Box) -> None:
    pen = ctx.pen
    dice = list(sec.get("dice") or [])
    for k, die in enumerate(dice):
        top = box.top - k * 33
        faces = int(str(die).lstrip("dD") or 6)
        buckets = faces if faces <= 20 else 10
        _label(ctx, box.x, top - 18, str(die), max_w=26, size=9.0, color=INK, bold=True)
        row = Box(box.x + 28, top - 30, box.w - 28, 30)
        for i, cell in enumerate(row.cols(buckets, 0)):
            pen.rect(cell, weight=pen.rules.light_pt, color=LIGHT)
            if faces <= 20:
                lab = str(i + 1)
            else:
                step = faces // buckets
                lab = f"{i * step + 1}-{(i + 1) * step}"
            _label(ctx, cell.cx, cell.top - 8, lab, max_w=cell.w - 2, align="center")


def draw_rounds(ctx: Ctx, sec: dict, box: Box) -> None:
    pen = ctx.pen
    count = int(sec.get("count") or 10)
    size = min(18.0, (box.w - 4 * (count - 1)) / count)
    for i in range(count):
        x = box.x + i * (size + 4)
        pen.rect(Box(x, box.top - size - 1, size, size), weight=pen.rules.light_pt + 0.2, radius=2)
        _label(ctx, x + size / 2, box.top - size / 2 - 3.5, str(i + 1), max_w=size - 2, align="center", color=SOFT)


DRAW = {"lines": draw_lines, "scores": draw_scores, "boxes": draw_boxes, "checklist": draw_checklist,
        "track": draw_track, "table": draw_table, "slots": draw_slots, "notes": draw_notes, "prompts": draw_prompts,
        "cards": draw_cards, "grid": draw_grid, "sketch": draw_sketch, "legend": draw_legend, "tally": draw_tally,
        "rounds": draw_rounds}


# --- the flow ----------------------------------------------------------------------------------------

def _notes_min(ctx: Ctx) -> float:
    return FRAME_V + 2 * ctx.L


def _is_full(sec: dict) -> bool:
    return sec.get("span") == "full" or (sec["kind"] in ("lines", "scores", "grid") and sec.get("span") != "column")


def _bands(sections: list[dict], cols: int) -> list[list[dict]]:
    """Each band is [sec] (full width) or a run of column sections."""
    bands: list[list[dict]] = []
    run: list[dict] = []
    for s in sections:
        if _is_full(s) or cols <= 1:
            if run:
                bands.append(run)
                run = []
            bands.append([s])
        else:
            run.append(s)
    if run:
        bands.append(run)
    return bands


def _contiguous(hs: list[float], k: int) -> tuple[float, list[list[int]]]:
    """Split indexes 0..n-1 into k reading-order runs minimizing the tallest run."""
    n = len(hs)
    best = (math.inf, [list(range(n))])

    def run_h(a: int, b: int) -> float:
        return sum(hs[a:b]) + GAP * max(b - a - 1, 0)

    def rec(start: int, parts: int, acc: list[list[int]], worst: float) -> None:
        nonlocal best
        if parts == 1:
            h = max(worst, run_h(start, n))
            if h < best[0] - 1e-9:
                best = (h, acc + [list(range(start, n))])
            return
        for end in range(start + 1, n - parts + 2):
            h = run_h(start, end)
            if max(h, worst) >= best[0]:
                continue
            rec(end, parts - 1, acc + [list(range(start, end))], max(worst, h))

    rec(0, min(k, n), [], 0.0)
    return best


def _best_assignment(hs: list[float], k: int) -> tuple[float, list[list[int]]]:
    """Any split of the sections into k columns (each column keeps reading order) that
    makes the tallest column shortest: exhaustive for up to 10 sections, else each section
    in the shortest column so far."""
    n = len(hs)

    def tallest(cols: list[list[int]]) -> float:
        return max((sum(hs[i] for i in c) + GAP * max(len(c) - 1, 0)) for c in cols)

    if n <= 10 and k ** n <= 60000:
        best_h, best = math.inf, None
        for code in range(k ** n):
            cols: list[list[int]] = [[] for _ in range(k)]
            x = code
            for i in range(n):
                cols[x % k].append(i)
                x //= k
            if any(not c for c in cols):
                continue
            h = tallest(cols)
            if h < best_h - 1e-9:
                best_h, best = h, cols
        if best is not None:
            return best_h, best
    cols = [[] for _ in range(k)]
    tall = [0.0] * k
    for i, h in enumerate(hs):
        j = min(range(k), key=lambda c: (tall[c], c))
        tall[j] += h + (GAP if cols[j] else 0)
        cols[j].append(i)
    return max(tall), cols


def layout(ctx: Ctx, body: Box, sections: list[dict], *, cols: int = 2, strip: bool = False) -> None:
    """Lay out and draw `sections` on `body` (see the module docstring)."""
    secs = [s for s in sections if isinstance(s, dict) and s.get("kind") in KINDS]
    area = body
    while secs and secs[0]["kind"] == "lines" and not secs[0].get("title"):
        s = secs.pop(0)
        box, area = area.take_top(height(ctx, s, area.w), GAP)
        draw(ctx, s, box)
    if strip:
        idx = next((i for i, s in enumerate(secs) if s["kind"] == "scores"), None)
        if idx is not None:
            sc = secs.pop(idx)
            sbox, area = area.take_left(STRIP_W, GAP)
            rest = draw_score_strip(ctx, sc, sbox)
            if rest.h >= _notes_min(ctx):
                draw(ctx, {"kind": "notes", "title": "Notes"}, rest)
    _flow(ctx, area, secs, cols)


def _flow(ctx: Ctx, area: Box, secs: list[dict], cols: int) -> None:
    plans = []                 # (kind, h_min, flex, payload)
    for band in _bands(secs, cols):
        if len(band) == 1 and (_is_full(band[0]) or cols <= 1):
            s = band[0]
            plans.append(["full", height(ctx, s, area.w), flex(s), s])
            continue
        k = min(cols, len(band))
        cw = (area.w - (k - 1) * GAP) / k
        hs = [height(ctx, s, cw) for s in band]
        h, groups = _contiguous(hs, k)
        plans.append(["cols", h, max(flex(s) for s in band), (band, hs, groups, k, cw)])
    total = sum(p[1] for p in plans) + GAP * max(len(plans) - 1, 0)
    if total > area.h + 0.01:
        for p in plans:          # pack columns tighter when reading order cannot fit
            if p[0] == "cols":
                band, hs, _g, k, cw = p[3]
                h, groups = _best_assignment(hs, k)
                if h < p[1]:
                    p[1], p[3] = h, (band, hs, groups, k, cw)
        total = sum(p[1] for p in plans) + GAP * max(len(plans) - 1, 0)
    if total > area.h + 0.01:
        names = ", ".join(str(s.get("title") or s["kind"]) for s in secs)
        raise LayoutError(f"{ctx.pen.page_label}: sections need {total:.0f} pt, the page has {area.h:.0f} pt ({names})")
    spare = area.h - total
    weights = sum(p[2] for p in plans)
    if weights <= 0 and spare >= _notes_min(ctx) + GAP:
        plans.append(["full", spare - GAP, 1.0, {"kind": "notes", "title": "Notes"}])
        spare, weights = 0.0, 1.0
    y = area.top
    for p in plans:
        h = p[1] + (spare * p[2] / weights if weights > 0 else 0.0)
        box = Box(area.x, y - h, area.w, h)
        if p[0] == "full":
            draw(ctx, p[3], box)
        else:
            _draw_columns(ctx, box, *p[3])
        y -= h + GAP


def _draw_columns(ctx: Ctx, box: Box, band: list[dict], hs: list[float], groups: list[list[int]], k: int,
                  cw: float) -> None:
    for c, idxs in enumerate(groups):
        col = Box(box.x + c * (cw + GAP), box.y, cw, box.h)
        used = sum(hs[i] for i in idxs) + GAP * max(len(idxs) - 1, 0)
        spare = col.h - used
        weights = sum(flex(band[i]) for i in idxs)
        items = [(band[i], hs[i]) for i in idxs]
        if weights <= 0 and spare >= _notes_min(ctx) + GAP:
            items.append(({"kind": "notes", "title": "Notes"}, spare - GAP))
            spare, weights = 0.0, 0.0
        y = col.top
        for sec, h in items:
            if weights > 0:
                h += spare * flex(sec) / weights
            draw(ctx, sec, Box(col.x, y - h, col.w, h))
            y -= h + GAP
