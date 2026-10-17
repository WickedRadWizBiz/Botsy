"""Page geometry and the drawing pen every stationery page uses.

All sizes are PDF points (origin bottom-left). Every page is laid out from its own page
size, margins and style, so US Letter and A4 each get their own row and line counts
instead of a scaled copy.

The pen (Sheet) enforces the print rules for everything it draws and records what it drew
(Metrics), so the generator, its tests and the manifest can state them:
- printed text is never smaller than Rules.min_font_pt (7 pt floor);
- writing lines, table rows, checklist rows and grid pitches are at least
  Rules.min_line_gap_pt apart (6 mm floor);
- light lines are at least 0.5 pt, structural lines at least print.min_line_weight_pt;
- nothing is drawn outside the print margins (out_of_bounds stays empty).
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field

from reportlab.pdfbase.pdfmetrics import stringWidth

MM = 72.0 / 25.4
CAP_HEIGHT = 0.718            # Helvetica cap height as a fraction of the font size
DESCENT = 0.207
INK = (0.08, 0.08, 0.08)      # text and structural lines
SOFT = (0.38, 0.38, 0.38)     # subtitles, small labels
LIGHT = (0.58, 0.58, 0.58)    # writing lines, grid lines
DOT = (0.5, 0.5, 0.5)

# Hard floors (docs/PACKS.md section 5): settings may raise them, never lower them.
FLOOR_FONT_PT = 7.0
FLOOR_LINE_GAP_MM = 6.0
FLOOR_LIGHT_PT = 0.5
# A box that holds only a few lines or rows (the ADHD-friendly style's box_lines and
# list_rows) spreads them over its height, up to this many times the usual spacing.
MAX_SPREAD = 1.6


def spread_gap(gap: float, height: float, slots: float) -> float:
    """The spacing for a capped number of lines in a box: `height / slots`, never tighter
    than `gap` and never wider than MAX_SPREAD times it."""
    return max(gap, min(gap * MAX_SPREAD, height / slots if slots > 0 else gap))


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

    def contains(self, other: Box, tol: float = 0.01) -> bool:
        return (other.x >= self.x - tol and other.y >= self.y - tol and other.right <= self.right + tol
                and other.top <= self.top + tol)


@dataclass(frozen=True)
class Rules:
    """The print rules every page follows (from settings print and stationery)."""

    min_font_pt: float = FLOOR_FONT_PT
    min_line_gap_pt: float = FLOOR_LINE_GAP_MM * MM
    light_pt: float = 0.6
    structural_pt: float = 1.5

    @classmethod
    def from_settings(cls, settings: dict, cfg: dict) -> Rules:
        pr = settings.get("print") or {}
        return cls(min_font_pt=max(FLOOR_FONT_PT, float(cfg.get("min_font_pt", FLOOR_FONT_PT))),
                   min_line_gap_pt=max(FLOOR_LINE_GAP_MM, float(cfg.get("min_line_gap_mm", FLOOR_LINE_GAP_MM))) * MM,
                   light_pt=max(FLOOR_LIGHT_PT, float(cfg.get("light_line_pt", 0.6))),
                   structural_pt=max(float(pr.get("min_line_weight_pt", 1.5)),
                                     float(cfg.get("structural_line_pt", 0.0))))


@dataclass
class Metrics:
    """What a render drew, for the print-rule checks and the manifest."""

    fonts: list[float] = field(default_factory=list)
    light_lines: list[float] = field(default_factory=list)
    structural_lines: list[float] = field(default_factory=list)
    line_gaps: list[float] = field(default_factory=list)      # points
    texts: list[str] = field(default_factory=list)
    out_of_bounds: list[str] = field(default_factory=list)

    @property
    def min_font_pt(self) -> float | None:
        return min(self.fonts) if self.fonts else None

    @property
    def min_line_gap_mm(self) -> float | None:
        return min(self.line_gaps) / MM if self.line_gaps else None

    @property
    def min_light_pt(self) -> float | None:
        return min(self.light_lines) if self.light_lines else None

    @property
    def min_structural_pt(self) -> float | None:
        return min(self.structural_lines) if self.structural_lines else None

    def summary(self) -> dict:
        def r(v, nd=2):
            return None if v is None else round(v, nd)
        return {"min_font_pt": r(self.min_font_pt), "min_line_gap_mm": r(self.min_line_gap_mm),
                "min_light_line_pt": r(self.min_light_pt), "min_structural_line_pt": r(self.min_structural_pt)}


_FOLD = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"}


def printable(text: str) -> str:
    """`text` with only characters the built-in fonts print (WinAnsi); accents are folded."""
    out = []
    for ch in str(text):
        ch = _FOLD.get(ch, ch)
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(base)
    return "".join(out)


class Sheet:
    """A pen over one reportlab canvas page that follows the Rules and fills Metrics.

    `bounds` is the printable area (inside the margins); anything drawn outside it is
    recorded in metrics.out_of_bounds.
    """

    def __init__(self, canvas, bounds: Box, style, rules: Rules, metrics: Metrics, *, tints: bool = True):
        self.c = canvas
        self.bounds = bounds
        self.style = style
        self.rules = rules
        self.m = metrics
        self.use_tints = tints
        self._tint_i = 0

    # --- rules -------------------------------------------------------------------------

    @property
    def gap(self) -> float:
        """Space between panels."""
        return self.style.gap_pt

    @property
    def line_gap(self) -> float:
        """Writing-line spacing of this style, never below the rule."""
        return max(self.style.line_gap_mm * MM, self.rules.min_line_gap_pt)

    @property
    def row_h(self) -> float:
        """Table and checklist row height of this style, never below the rule."""
        return max(self.style.row_mm * MM, self.rules.min_line_gap_pt)

    def font_pt(self, size: float) -> float:
        return max(float(size), self.rules.min_font_pt)

    def lw(self, kind: str) -> float:
        if kind == "structural":
            return self.rules.structural_pt * self.style.structural_scale
        if kind == "medium":
            return max(self.rules.light_pt * 1.5, 0.9)
        return self.rules.light_pt

    def tint(self, index: int | None = None) -> tuple | None:
        """A light, print-friendly zone colour (None when colour zones are off)."""
        tints = self.style.tints
        if not tints:
            return None
        if index is None:
            index = self._tint_i
            self._tint_i += 1
        rgb = tints[index % len(tints)]
        if not self.use_tints:
            g = 0.93
            return (g, g, g)
        return rgb

    def _check(self, box: Box, what: str) -> None:
        if not self.bounds.contains(box):
            self.m.out_of_bounds.append(f"{what} at ({box.x:.1f},{box.y:.1f},{box.w:.1f},{box.h:.1f})")

    def _stroke(self, kind: str, color=None) -> float:
        w = self.lw(kind)
        self.c.setLineWidth(w)
        self.c.setStrokeColorRGB(*(color or (INK if kind == "structural" else LIGHT)))
        (self.m.structural_lines if kind == "structural" else self.m.light_lines).append(w)
        return w

    # --- primitives ----------------------------------------------------------------------

    def text_width(self, s: str, size: float, font: str | None = None) -> float:
        return stringWidth(printable(s), font or self.style.body_font, self.font_pt(size))

    def text(self, x: float, y: float, s: str, *, size: float, font: str | None = None, color=None,
             align: str = "left", max_width: float | None = None) -> float:
        """Draw one line of text with its baseline at y. Shrinks to max_width (never below
        the minimum font size; text that still does not fit is shortened). Returns its width."""
        s = printable(s)
        if not s:
            return 0.0
        font = font or self.style.body_font
        size = self.font_pt(size)
        if max_width is not None and max_width > 0:
            width = stringWidth(s, font, size)
            if width > max_width:
                size = max(size * max_width / width, self.rules.min_font_pt)
                while stringWidth(s, font, size) > max_width and len(s) > 1:
                    s = s[:-2].rstrip() + "." if len(s) > 2 else s[:1]
        width = stringWidth(s, font, size)
        x0 = {"left": x, "center": x - width / 2, "right": x - width}[align]
        self._check(Box(x0, y - DESCENT * size, width, (CAP_HEIGHT + DESCENT) * size), f"text {s[:20]!r}")
        self.c.setFont(font, size)
        self.c.setFillColorRGB(*(color or INK))
        self.c.drawString(x0, y, s)
        self.m.fonts.append(size)
        self.m.texts.append(s)
        return width

    def vtext(self, x: float, y: float, s: str, *, size: float, font: str | None = None, color=None) -> None:
        """Text rotated 90 degrees (reading upwards), centred on (x, y)."""
        s = printable(s)
        font = font or self.style.label_font
        size = self.font_pt(size)
        width = stringWidth(s, font, size)
        self._check(Box(x - size * 0.5, y - width / 2, size, width), f"vtext {s[:20]!r}")
        c = self.c
        c.saveState()
        c.translate(x + CAP_HEIGHT * size / 2, y)
        c.rotate(90)
        c.setFont(font, size)
        c.setFillColorRGB(*(color or INK))
        c.drawString(-width / 2, 0, s)
        c.restoreState()
        self.m.fonts.append(size)
        self.m.texts.append(s)

    def line(self, x1: float, y1: float, x2: float, y2: float, *, kind: str = "light", color=None,
             dash: tuple | None = None) -> None:
        self._check(Box(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)), "line")
        self._stroke(kind, color)
        if dash:
            self.c.setDash(*dash)
        self.c.line(x1, y1, x2, y2)
        if dash:
            self.c.setDash()

    def rect(self, box: Box, *, kind: str = "structural", fill=None, stroke: bool = True, radius: float | None = None,
             color=None, dash: tuple | None = None) -> None:
        self._check(box, "rect")
        if fill is not None:
            self.c.setFillColorRGB(*fill)
        if stroke:
            self._stroke(kind, color)
        if dash:
            self.c.setDash(*dash)
        r = self.style.radius_pt if radius is None else radius
        r = min(r, box.w / 2, box.h / 2)
        if r > 0:
            self.c.roundRect(box.x, box.y, box.w, box.h, r, stroke=int(stroke), fill=int(fill is not None))
        else:
            self.c.rect(box.x, box.y, box.w, box.h, stroke=int(stroke), fill=int(fill is not None))
        if dash:
            self.c.setDash()

    def circle(self, x: float, y: float, r: float, *, kind: str = "medium", fill=None, stroke: bool = True,
               color=None) -> None:
        self._check(Box(x - r, y - r, 2 * r, 2 * r), "circle")
        if fill is not None:
            self.c.setFillColorRGB(*fill)
        if stroke:
            self._stroke(kind, color or SOFT)
        self.c.circle(x, y, r, stroke=int(stroke), fill=int(fill is not None))

    def checkbox(self, x: float, y: float, size: float | None = None) -> float:
        """A checkbox with its bottom-left corner at (x, y). Returns its size."""
        size = size or self.style.checkbox_pt
        self.rect(Box(x, y, size, size), kind="medium", radius=min(2.0, size / 5), color=INK)
        return size

    # --- composites ----------------------------------------------------------------------

    def label(self, x: float, y: float, s: str, *, size: float | None = None, color=None, max_width=None,
              align: str = "left") -> float:
        return self.text(x, y, s, size=size or self.style.label_pt, font=self.style.label_font, color=color,
                         max_width=max_width, align=align)

    def small(self, x: float, y: float, s: str, *, align: str = "left", max_width=None, color=None) -> float:
        return self.text(x, y, s, size=self.style.small_pt, color=color or SOFT, align=align, max_width=max_width)

    def panel(self, box: Box, label: str | None = None, *, tint: int | None = None, fill_band: bool = True) -> Box:
        """A bordered zone with an optional label band on top. Returns the inside below the
        band, padded."""
        st = self.style
        pad = st.pad_pt
        inner_top = box.top
        if label:
            band_h = st.label_pt + 2 * pad * 0.75
            band = Box(box.x, box.top - band_h, box.w, band_h)
            color = self.tint(tint) if fill_band else None
            if color is not None:
                self.rect(band, stroke=False, fill=color)
                if st.radius_pt > 0:            # square off the band's lower corners
                    lower = Box(band.x, band.y, band.w, min(st.radius_pt, band.h / 2))
                    self.rect(lower, stroke=False, fill=color, radius=0)
            self.label(box.x + pad, band.y + (band_h - st.label_pt * CAP_HEIGHT) / 2, label,
                       max_width=box.w - 2 * pad)
            self.line(box.x, band.y, box.right, band.y, kind="light", color=SOFT)
            inner_top = band.y
        self.rect(box, kind="structural")
        return Box(box.x + pad, box.y + pad, max(box.w - 2 * pad, 0.0), max(inner_top - box.y - 2 * pad, 0.0))

    def writing_lines(self, box: Box, *, gap: float | None = None, max_lines: int | None = None,
                      numbered: bool = False, first_offset: float | None = None) -> int:
        """Light writing lines across `box`, from the top down. Returns how many."""
        gap = max(gap or self.line_gap, self.rules.min_line_gap_pt)
        first = gap if first_offset is None else max(first_offset, 0.0)
        n = int((box.h - first) // gap) + 1 if box.h >= first else 0
        limit = max_lines if max_lines is not None else self.style.box_lines or None
        if limit and n > limit:
            n = limit
            if first_offset is None:        # a capped box: spread its few lines over it
                gap = first = spread_gap(gap, box.h, n + 0.35)
        if n <= 0:
            return 0
        if n > 1:
            self.m.line_gaps.append(gap)
        for i in range(n):
            y = box.top - first - i * gap
            x0 = box.x
            if numbered:
                self.label(box.x, y + 2, f"{i + 1}.", size=self.style.label_pt)
                x0 = box.x + self.text_width("10.", self.style.label_pt, self.style.label_font) + 3
            self.line(x0, y, box.right, y)
        return n

    def checklist(self, box: Box, *, gap: float | None = None, max_rows: int | None = None,
                  numbered: bool = False) -> int:
        """Checkbox rows with a writing line each. Returns how many rows."""
        gap = max(gap or self.list_gap, self.rules.min_line_gap_pt)
        n = int(box.h // gap)
        limit = max_rows if max_rows is not None else self.style.list_rows or None
        if limit and n > limit:
            n = limit
            gap = spread_gap(gap, box.h, n + 0.2)   # a capped box: spread its few rows over it
        if n <= 0:
            return 0
        if n > 1:
            self.m.line_gaps.append(gap)
        size = min(self.style.checkbox_pt, gap - 4)
        for i in range(n):
            y = box.top - (i + 1) * gap + 2
            x = box.x
            if numbered:
                self.label(x, y + 1, f"{i + 1}", size=self.style.label_pt)
                x += self.text_width("10", self.style.label_pt, self.style.label_font) + 3
            self.checkbox(x, y, size)
            self.line(x + size + 4, y, box.right, y)
        return n

    def table(self, box: Box, columns: list[tuple[str, float]], *, rows: int | None = None,
              row_h: float | None = None, header_h: float | None = None, row_labels: list[str] | None = None,
              header_tint: int | None = 0, max_rows: int | None = None) -> list[list[Box]]:
        """A ruled table: a header row with the column labels, then rows of at least the
        minimum row height. `rows` fixes the count (rows grow to fill the box), else as many
        as fit. Returns the body cells row by row."""
        st = self.style
        header_h = header_h if header_h is not None else st.label_pt + 2 * st.pad_pt * 0.75
        body = Box(box.x, box.y, box.w, max(box.h - header_h, 0.0))
        min_row = max(row_h or self.row_h, self.rules.min_line_gap_pt)
        if rows is None:
            rows = int(body.h // min_row)
            if max_rows:
                rows = min(rows, max_rows)
        rows = max(int(rows), 0)
        h = body.h / rows if rows else 0.0
        if rows and h < self.rules.min_line_gap_pt - 1e-6:
            raise ValueError(f"table rows of {h / MM:.1f} mm are below the "
                             f"{self.rules.min_line_gap_pt / MM:.1f} mm rule")
        used = Box(box.x, box.top - header_h - h * rows, box.w, header_h + h * rows)
        weights = [w for _, w in columns]
        cols = used.cols(len(columns), 0.0, weights)
        head = Box(used.x, used.top - header_h, used.w, header_h)
        color = self.tint(header_tint) if header_tint is not None else None
        if color is not None:
            self.rect(head, stroke=False, fill=color, radius=0)
        for (label, _), col in zip(columns, cols):
            if label:
                self.label(col.cx, head.y + (header_h - st.label_pt * CAP_HEIGHT) / 2, label,
                           size=st.label_pt * 0.92, align="center", max_width=col.w - 4)
        if rows > 1:
            self.m.line_gaps.append(h)
        for i in range(rows + 1):
            y = head.y - i * h
            self.line(used.x, y, used.right, y, kind="light", color=SOFT if i == 0 else LIGHT)
        for col in cols[1:]:
            self.line(col.x, used.y, col.x, used.top, kind="light")
        self.rect(used, kind="structural", radius=0)
        cells = []
        for i in range(rows):
            y = head.y - (i + 1) * h
            cells.append([Box(col.x, y, col.w, h) for col in cols])
        if row_labels:
            for i, text in enumerate(row_labels[:rows]):
                cell = cells[i][0]
                self.label(cell.x + 4, cell.y + (h - st.label_pt * CAP_HEIGHT) / 2, text, max_width=cell.w - 6)
        return cells

    def scale(self, box: Box, n: int = 5, *, numbers: bool = False) -> None:
        """n circles to colour in, centred in box (a 1..n rating)."""
        r = min(box.h * 0.32, box.w / (n * 2.6))
        step = box.w / n
        for i in range(n):
            x = box.x + step * (i + 0.5)
            self.circle(x, box.cy, r)
            if numbers and r * 1.2 >= self.rules.min_font_pt:
                self.text(x, box.cy - r * 0.35, str(i + 1), size=r * 1.1, color=SOFT, align="center")

    def segments(self, box: Box, n: int, *, tint: int | None = None) -> None:
        """A visual time or energy bar: n boxes in a row to shade in."""
        gap = min(2.0, box.w / (n * 6))
        cells = box.cols(n, gap)
        color = self.tint(tint) if tint is not None else None
        for cell in cells:
            self.rect(cell, kind="medium", fill=color, radius=min(1.5, cell.w / 4), color=SOFT)

    def stars(self, box: Box, n: int = 5) -> None:
        r = min(box.h * 0.4, box.w / (n * 2.4))
        step = box.w / n
        for i in range(n):
            self.star(box.x + step * (i + 0.5), box.cy, r)

    def star(self, x: float, y: float, r: float, *, fill=None, color=None) -> None:
        self._check(Box(x - r, y - r, 2 * r, 2 * r), "star")
        p = self.c.beginPath()
        for k in range(10):
            rad = r if k % 2 == 0 else r * 0.45
            a = math.pi / 2 + k * math.pi / 5
            px, py = x + rad * math.cos(a), y + rad * math.sin(a)
            (p.moveTo if k == 0 else p.lineTo)(px, py)
        p.close()
        self._stroke("medium", color or SOFT)
        if fill is not None:
            self.c.setFillColorRGB(*fill)
        self.c.drawPath(p, stroke=1, fill=int(fill is not None))

    def face(self, x: float, y: float, r: float, mood: int) -> None:
        """A simple face, mood 0 (low) to 4 (high), drawn with our own strokes."""
        self.circle(x, y, r)
        c = self.c
        c.setFillColorRGB(*SOFT)
        c.circle(x - r * 0.35, y + r * 0.25, r * 0.09, stroke=0, fill=1)
        c.circle(x + r * 0.35, y + r * 0.25, r * 0.09, stroke=0, fill=1)
        curve = (mood - 2) * 0.22 * r
        p = c.beginPath()
        p.moveTo(x - r * 0.42, y - r * 0.3)
        p.curveTo(x - r * 0.15, y - r * 0.3 - curve, x + r * 0.15, y - r * 0.3 - curve, x + r * 0.42, y - r * 0.3)
        self._stroke("medium", SOFT)
        c.drawPath(p, stroke=1, fill=0)

    def dot_grid(self, box: Box, pitch: float | None = None) -> int:
        """Light dots every `pitch` points (at least the line-gap rule). Returns the dot count."""
        pitch = max(pitch or self.rules.min_line_gap_pt, self.rules.min_line_gap_pt)
        cols = int(box.w // pitch)
        rows = int(box.h // pitch)
        if cols < 1 or rows < 1:
            return 0
        x0 = box.x + (box.w - cols * pitch) / 2
        y0 = box.y + (box.h - rows * pitch) / 2
        self._check(Box(x0, y0, cols * pitch, rows * pitch), "dot grid")
        self.m.line_gaps.append(pitch)
        # Each dot is a round-capped stroke of (almost) no length: one short path per dot
        # instead of four bezier curves keeps big dot-grid packs fast and small.
        c = self.c
        c.saveState()
        c.setLineCap(1)
        c.setLineWidth(2 * max(self.rules.light_pt, 0.7))
        c.setStrokeColorRGB(*DOT)
        path = c.beginPath()
        for i in range(cols + 1):
            x = x0 + i * pitch
            for j in range(rows + 1):
                y = y0 + j * pitch
                path.moveTo(x, y)
                path.lineTo(x + 0.01, y)
        c.drawPath(path, stroke=1, fill=0)
        c.restoreState()
        return (cols + 1) * (rows + 1)

    def square_grid(self, box: Box, pitch: float | None = None) -> tuple[int, int]:
        """Light squares of `pitch` points (at least the line-gap rule), centred in box."""
        pitch = max(pitch or self.rules.min_line_gap_pt, self.rules.min_line_gap_pt)
        cols = int(box.w // pitch)
        rows = int(box.h // pitch)
        if cols < 1 or rows < 1:
            return 0, 0
        g = Box(box.x + (box.w - cols * pitch) / 2, box.y + (box.h - rows * pitch) / 2, cols * pitch, rows * pitch)
        self.m.line_gaps.append(pitch)
        for i in range(cols + 1):
            self.line(g.x + i * pitch, g.y, g.x + i * pitch, g.top)
        for j in range(rows + 1):
            self.line(g.x, g.y + j * pitch, g.right, g.y + j * pitch)
        return cols, rows

    # --- sizing helpers --------------------------------------------------------------

    @property
    def band_h(self) -> float:
        """Height of a panel's label band."""
        return self.style.label_pt + 2 * self.style.pad_pt * 0.75

    @property
    def list_gap(self) -> float:
        """Row spacing of a checklist."""
        return max(self.row_h, self.style.checkbox_pt + 6, self.rules.min_line_gap_pt)

    def panel_h(self, rows: int, gap: float | None = None, label: bool = True) -> float:
        """Height of a labelled panel that holds `rows` rows of `gap` (default a checklist)."""
        return (self.band_h if label else 0.0) + rows * (gap or self.list_gap) + 2 * self.style.pad_pt + 1.0

    def cell_checkbox(self, cell: Box) -> None:
        """A checkbox centred in a table cell."""
        size = min(self.style.checkbox_pt, cell.h - 4, cell.w - 4)
        if size >= 4:
            self.checkbox(cell.cx - size / 2, cell.cy - size / 2, size)

    def labeled_rows(self, box: Box, labels: list[str | None], *, row_h: float | None = None,
                     label_w: float = 34.0, bar_w: float = 0.0, max_rows: int | None = None) -> int:
        """Rows with a small label (a time) at the left, an optional box to shade (a visual
        time bar) and a writing line. A None label leaves a short line to write a time on.
        Returns the row count."""
        row_h = max(row_h or self.row_h, self.rules.min_line_gap_pt)
        n = min(len(labels), int(box.h // row_h))
        if max_rows:
            n = min(n, max_rows)
        if n <= 0:
            return 0
        if n > 1:
            self.m.line_gaps.append(row_h)
        for i in range(n):
            y = box.top - (i + 1) * row_h
            text = labels[i]
            if text is None:
                self.line(box.x, y + 2, box.x + label_w - 6, y + 2)
            elif text:
                self.small(box.x, y + 4, text, max_width=label_w - 4)
            x = box.x + label_w
            if bar_w > 0:
                self.rect(Box(x, y + 2, bar_w, row_h - 4), kind="medium", radius=1.5, color=SOFT)
                x += bar_w + 5
            self.line(x, y, box.right, y)
        return n

    def fields_column(self, box: Box, labels: list[str], *, gap: float | None = None) -> int:
        """'Label ____' rows down a box, spaced at least the line-gap rule apart."""
        if not labels:
            return 0
        if gap is None:
            gap = min(box.h / len(labels), 2.2 * self.row_h)
        gap = max(gap, self.row_h if len(labels) > 1 else 0.0, self.rules.min_line_gap_pt)
        n = min(len(labels), int(box.h // gap) or 1)
        if n > 1:
            self.m.line_gaps.append(gap)
        top = box.top - max(box.h - n * gap, 0.0) / 2      # the fields sit in the middle of the box
        for i in range(n):
            self.field(box.x, top - (i + 1) * gap + 4, labels[i], box.w)
        return n

    def field(self, x: float, y: float, label: str, width: float) -> float:
        """'Label ________' with the writing line on the baseline. Returns the width used."""
        lw = self.label(x, y, label)
        self.line(x + lw + 4, y - 1.5, x + width, y - 1.5)
        return width
