"""Page geometry and the drawing pen every tabletop RPG page uses.

All sizes are PDF points (origin bottom-left). Every page is laid out from its own page
size and margins, so US Letter and A4 each get their own row, line and cell counts instead
of a scaled copy.

The pen (Pen) enforces the print rules for everything it draws and records what it drew
(Metrics), so the generator, its tests and the manifest can state them:
- printed text is never smaller than Rules.min_font_pt (7 pt floor);
- writing lines, table rows, checklist rows and grid pitches are at least
  Rules.min_gap_pt apart (6 mm floor);
- light lines are at least 0.5 pt;
- nothing is drawn outside the print margins (out_of_bounds stays empty), and a label that
  does not fit its slot even at the smallest font is recorded (overflow).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from reportlab.pdfbase.pdfmetrics import stringWidth

MM = 72.0 / 25.4
CAP = 0.72                    # cap height as a fraction of the font size (standard fonts)
DESCENT = 0.22
INK = (0.08, 0.08, 0.08)      # text and structural lines
SOFT = (0.36, 0.36, 0.36)     # small labels, subtitles
LIGHT = (0.55, 0.55, 0.55)    # writing lines, grid lines
FAINT = (0.70, 0.70, 0.70)    # minor grid lines, dots

# Hard floors (docs/PACKS.md section 5): settings may raise them, never lower them.
FLOOR_FONT_PT = 7.0
FLOOR_LINE_GAP_MM = 6.0
FLOOR_LIGHT_PT = 0.5
TOL = 0.05                    # points of tolerance for the bounds check


@dataclass(frozen=True)
class Box:
    """Axis-aligned rectangle, origin bottom-left."""

    x: float
    y: float
    w: float
    h: float

    @property
    def top(self) -> float:
        return self.y + self.h

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def inset(self, dx: float, dy: float | None = None) -> Box:
        dy = dx if dy is None else dy
        return Box(self.x + dx, self.y + dy, max(self.w - 2 * dx, 0.0), max(self.h - 2 * dy, 0.0))

    def take_top(self, h: float, gap: float = 0.0) -> tuple[Box, Box]:
        """(strip of height h at the top, the rest below it after `gap`)."""
        h = min(max(h, 0.0), self.h)
        rest = max(self.h - h - gap, 0.0)
        return Box(self.x, self.top - h, self.w, h), Box(self.x, self.y, self.w, rest)

    def take_bottom(self, h: float, gap: float = 0.0) -> tuple[Box, Box]:
        """(strip of height h at the bottom, the rest above it after `gap`)."""
        h = min(max(h, 0.0), self.h)
        rest = max(self.h - h - gap, 0.0)
        return Box(self.x, self.y, self.w, h), Box(self.x, self.top - rest, self.w, rest)

    def take_left(self, w: float, gap: float = 0.0) -> tuple[Box, Box]:
        w = min(max(w, 0.0), self.w)
        rest = max(self.w - w - gap, 0.0)
        return Box(self.x, self.y, w, self.h), Box(self.right - rest, self.y, rest, self.h)

    def take_right(self, w: float, gap: float = 0.0) -> tuple[Box, Box]:
        w = min(max(w, 0.0), self.w)
        rest = max(self.w - w - gap, 0.0)
        return Box(self.right - w, self.y, w, self.h), Box(self.x, self.y, rest, self.h)

    def rows(self, n: int, gap: float = 0.0, weights: list[float] | None = None) -> list[Box]:
        """n boxes stacked from the top down."""
        weights = weights or [1.0] * n
        total = sum(weights)
        free = max(self.h - gap * (n - 1), 0.0)
        out, top = [], self.top
        for wgt in weights:
            h = free * wgt / total
            out.append(Box(self.x, top - h, self.w, h))
            top -= h + gap
        return out

    def cols(self, n: int, gap: float = 0.0, weights: list[float] | None = None) -> list[Box]:
        """n boxes side by side from the left."""
        weights = weights or [1.0] * n
        total = sum(weights)
        free = max(self.w - gap * (n - 1), 0.0)
        out, x = [], self.x
        for wgt in weights:
            w = free * wgt / total
            out.append(Box(x, self.y, w, self.h))
            x += w + gap
        return out

    def grid(self, cols: int, rows: int, gap: float = 0.0) -> list[Box]:
        """Cells row by row from the top-left."""
        return [cell for row in self.rows(rows, gap) for cell in row.cols(cols, gap)]

    def contains_pt(self, x: float, y: float, tol: float = TOL) -> bool:
        return self.x - tol <= x <= self.right + tol and self.y - tol <= y <= self.top + tol


@dataclass(frozen=True)
class Rules:
    """Print rules for one render, from the ttrpg settings (floors cannot be lowered)."""

    min_font_pt: float = FLOOR_FONT_PT
    min_gap_pt: float = FLOOR_LINE_GAP_MM * MM      # the floor every line, row or grid keeps
    line_gap_pt: float = 7.0 * MM                   # writing lines (notes, prompts, write-in fields)
    row_gap_pt: float = 6.4 * MM                    # table, checklist and tracker rows
    light_pt: float = 0.6
    rule_pt: float = 1.0
    frame_pt: float = 1.6

    @classmethod
    def from_cfg(cls, cfg: dict) -> Rules:
        floor_gap = max(float(cfg.get("min_line_gap_mm", FLOOR_LINE_GAP_MM)), FLOOR_LINE_GAP_MM) * MM
        return cls(
            min_font_pt=max(float(cfg.get("min_font_pt", FLOOR_FONT_PT)), FLOOR_FONT_PT),
            min_gap_pt=floor_gap,
            line_gap_pt=max(float(cfg.get("line_gap_mm", 7.0)) * MM, floor_gap),
            row_gap_pt=max(float(cfg.get("row_gap_mm", 6.4)) * MM, floor_gap),
            light_pt=max(float(cfg.get("light_line_pt", 0.6)), FLOOR_LIGHT_PT),
            rule_pt=max(float(cfg.get("structural_line_pt", 1.0)), FLOOR_LIGHT_PT),
            frame_pt=max(float(cfg.get("frame_line_pt", 1.6)), FLOOR_LIGHT_PT),
        )


@dataclass
class Metrics:
    """What one render drew: every printed string and the measured print rules."""

    texts: list[str] = field(default_factory=list)
    min_font_pt: float | None = None
    min_line_gap_pt: float | None = None
    min_line_weight_pt: float | None = None
    out_of_bounds: list[str] = field(default_factory=list)
    overflow: list[str] = field(default_factory=list)
    pages: int = 0

    def font(self, size: float) -> None:
        self.min_font_pt = size if self.min_font_pt is None else min(self.min_font_pt, size)

    def gap(self, pt: float) -> None:
        self.min_line_gap_pt = pt if self.min_line_gap_pt is None else min(self.min_line_gap_pt, pt)

    def weight(self, pt: float) -> None:
        self.min_line_weight_pt = pt if self.min_line_weight_pt is None else min(self.min_line_weight_pt, pt)

    def summary(self) -> dict:
        gap_mm = None if self.min_line_gap_pt is None else round(self.min_line_gap_pt / MM, 2)
        return {"pages": self.pages, "min_font_pt": None if self.min_font_pt is None else round(self.min_font_pt, 2),
                "min_line_gap_mm": gap_mm,
                "min_line_weight_pt": None if self.min_line_weight_pt is None else round(self.min_line_weight_pt, 2),
                "out_of_bounds": len(self.out_of_bounds), "overflow": len(self.overflow)}


def text_width(s: str, font: str, size: float) -> float:
    return stringWidth(s, font, size)


class Pen:
    """Draws on one reportlab canvas page inside `bounds` (the print margins), keeping the
    print rules and recording Metrics."""

    def __init__(self, c, bounds: Box, rules: Rules, metrics: Metrics, *, page_label: str = ""):
        self.c = c
        self.bounds = bounds
        self.rules = rules
        self.m = metrics
        self.page_label = page_label

    # --- bookkeeping --------------------------------------------------------------------------

    def _mark(self, x0: float, y0: float, x1: float, y1: float, what: str) -> None:
        b = self.bounds
        if x0 < b.x - TOL or y0 < b.y - TOL or x1 > b.right + TOL or y1 > b.top + TOL:
            self.m.out_of_bounds.append(f"{self.page_label}: {what} at ({x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f})")

    def _stroke(self, weight: float, color) -> float:
        weight = max(float(weight), FLOOR_LIGHT_PT)
        self.c.setLineWidth(weight)
        self.c.setStrokeColorRGB(*color)
        self.m.weight(weight)
        return weight

    def gap(self, pt: float) -> None:
        """Record the spacing of writing lines, rows or a grid (refuses one below the floor)."""
        if pt < self.rules.min_gap_pt - 1e-6:
            raise ValueError(f"{self.page_label}: lines {pt / MM:.2f} mm apart; the floor is "
                             f"{self.rules.min_gap_pt / MM:.2f} mm")
        self.m.gap(pt)

    # --- text ---------------------------------------------------------------------------------

    def fit_size(self, s: str, font: str, size: float, max_w: float | None) -> float:
        size = max(float(size), self.rules.min_font_pt)
        if max_w is None or max_w <= 0:
            return size
        while size > self.rules.min_font_pt and text_width(s, font, size) > max_w:
            size = max(self.rules.min_font_pt, size - 0.25)
        return size

    def fit_width(self, s: str, font: str, size: float, max_w: float | None = None) -> float:
        """The width `s` takes at the size text() would use for it."""
        return text_width(str(s), font, self.fit_size(str(s), font, size, max_w))

    def text(self, x: float, y: float, s: str, *, size: float = 8.0, font: str = "Helvetica",
             align: str = "left", color=INK, max_w: float | None = None) -> float:
        """Draw one line of text with its baseline at y. Returns the width drawn."""
        s = str(s)
        if not s.strip():
            return 0.0
        size = self.fit_size(s, font, size, max_w)
        w = text_width(s, font, size)
        if max_w is not None and w > max_w + TOL:
            self.m.overflow.append(f"{self.page_label}: {s!r} needs {w:.1f} pt, has {max_w:.1f}")
        x0 = x - w / 2 if align == "center" else (x - w if align == "right" else x)
        self._mark(x0, y - DESCENT * size, x0 + w, y + CAP * size, f"text {s!r}")
        self.c.setFillColorRGB(*color)
        self.c.setFont(font, size)
        self.c.drawString(x0, y, s)
        self.m.font(size)
        self.m.texts.append(s)
        return w

    # --- lines and shapes ---------------------------------------------------------------------

    def line(self, x1: float, y1: float, x2: float, y2: float, *, weight: float | None = None, color=INK,
             dash: tuple | None = None) -> None:
        wgt = self._stroke(self.rules.light_pt if weight is None else weight, color)
        half = wgt / 2
        self._mark(min(x1, x2) - half, min(y1, y2) - half, max(x1, x2) + half, max(y1, y2) + half, "line")
        if dash:
            self.c.setDash(*dash)
        self.c.line(x1, y1, x2, y2)
        if dash:
            self.c.setDash()

    def rect(self, box: Box, *, weight: float | None = None, color=INK, fill=None, radius: float = 0.0,
             stroke: bool = True, dash: tuple | None = None) -> None:
        wgt = self._stroke(self.rules.rule_pt if weight is None else weight, color) if stroke else 0.0
        half = wgt / 2
        self._mark(box.x - half, box.y - half, box.right + half, box.top + half, "box")
        if fill is not None:
            self.c.setFillColorRGB(*fill)
        if dash:
            self.c.setDash(*dash)
        r = min(radius, box.w / 2, box.h / 2)
        if r > 0:
            self.c.roundRect(box.x, box.y, box.w, box.h, r, stroke=1 if stroke else 0, fill=1 if fill else 0)
        else:
            self.c.rect(box.x, box.y, box.w, box.h, stroke=1 if stroke else 0, fill=1 if fill else 0)
        if dash:
            self.c.setDash()

    def polygon(self, pts: list[tuple[float, float]], *, weight: float | None = None, color=INK, fill=None,
                stroke: bool = True, close: bool = True) -> None:
        if len(pts) < 2:
            return
        wgt = self._stroke(self.rules.rule_pt if weight is None else weight, color) if stroke else 0.0
        half = wgt / 2                               # round joins: nothing reaches past a corner
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        self._mark(min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half, "shape")
        if fill is not None:
            self.c.setFillColorRGB(*fill)
        p = self.c.beginPath()
        p.moveTo(*pts[0])
        for pt in pts[1:]:
            p.lineTo(*pt)
        if close:
            p.close()
        self.c.setLineJoin(1)
        self.c.drawPath(p, stroke=1 if stroke else 0, fill=1 if (fill is not None and close) else 0)
        self.c.setLineJoin(0)

    def circle(self, x: float, y: float, r: float, *, weight: float | None = None, color=INK, fill=None,
               stroke: bool = True) -> None:
        wgt = self._stroke(self.rules.light_pt if weight is None else weight, color) if stroke else 0.0
        half = wgt / 2
        self._mark(x - r - half, y - r - half, x + r + half, y + r + half, "circle")
        if fill is not None:
            self.c.setFillColorRGB(*fill)
        self.c.circle(x, y, r, stroke=1 if stroke else 0, fill=1 if fill is not None else 0)

    def ellipse(self, x0: float, y0: float, x1: float, y1: float, *, weight: float | None = None, color=INK,
                fill=None, stroke: bool = True) -> None:
        wgt = self._stroke(self.rules.light_pt if weight is None else weight, color) if stroke else 0.0
        half = wgt / 2
        self._mark(min(x0, x1) - half, min(y0, y1) - half, max(x0, x1) + half, max(y0, y1) + half, "ellipse")
        if fill is not None:
            self.c.setFillColorRGB(*fill)
        self.c.ellipse(x0, y0, x1, y1, stroke=1 if stroke else 0, fill=1 if fill is not None else 0)

    def curve(self, pts: list[tuple[float, float]], *, weight: float | None = None, color=INK) -> None:
        """A smooth open path through points (Catmull-Rom as cubic Beziers)."""
        if len(pts) < 2:
            return
        wgt = self._stroke(self.rules.light_pt if weight is None else weight, color)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        pad = wgt / 2 + 0.5
        p = self.c.beginPath()
        p.moveTo(*pts[0])
        ext = [pts[0], *pts, pts[-1]]
        lo_x, lo_y, hi_x, hi_y = min(xs), min(ys), max(xs), max(ys)
        for i in range(1, len(ext) - 2):
            p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            lo_x, hi_x = min(lo_x, c1[0], c2[0]), max(hi_x, c1[0], c2[0])
            lo_y, hi_y = min(lo_y, c1[1], c2[1]), max(hi_y, c1[1], c2[1])
            p.curveTo(*c1, *c2, *p2)
        self._mark(lo_x - pad, lo_y - pad, hi_x + pad, hi_y + pad, "curve")
        self.c.setLineCap(1)
        self.c.drawPath(p, stroke=1, fill=0)
        self.c.setLineCap(0)

    def image(self, path: str, box: Box) -> None:
        self._mark(box.x, box.y, box.right, box.top, "image")
        self.c.drawImage(path, box.x, box.y, box.w, box.h, preserveAspectRatio=True, anchor="c", mask="auto")

    # --- writing helpers ----------------------------------------------------------------------

    def writing_lines(self, box: Box, *, gap: float | None = None, first_at: float | None = None,
                      color=LIGHT) -> int:
        """Horizontal writing lines `gap` apart (at least the line gap), the first one `first_at`
        below the top (default: one gap). Returns how many were drawn."""
        gap = max(self.rules.line_gap_pt if gap is None else gap, self.rules.min_gap_pt)
        first = gap if first_at is None else first_at
        y = box.top - first
        n = 0
        while y >= box.y - 0.01:
            self.line(box.x, y, box.right, y, color=color)
            y -= gap
            n += 1
        if n:
            self.gap(min(gap, first))       # the space above the first line is writing space too
        return n

    def checkbox(self, x: float, y: float, size: float, *, round_: bool = False, weight: float | None = None) -> None:
        """A checkbox with its bottom-left at (x, y)."""
        if round_:
            self.circle(x + size / 2, y + size / 2, size / 2, weight=weight or self.rules.light_pt + 0.2)
        else:
            self.rect(Box(x, y, size, size), weight=weight or self.rules.light_pt + 0.2, radius=1.0)


def deg(a: float) -> float:
    return math.radians(a)
