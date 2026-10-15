"""Page geometry and drawing helpers shared by every activity page.

All sizes are PDF points. Each page is laid out from its own page size and margins,
so US Letter and A4 each get a layout that fills the page instead of a scaled copy.
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass

from reportlab.pdfbase.pdfmetrics import stringWidth

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
# Helvetica AFM metrics as a fraction of the font size. Used to place text on guide lines.
CAP_HEIGHT = 0.718
X_HEIGHT = 0.523
DESCENT = 0.207

INK = 0.0          # gray levels: 0 = black, 1 = white
SOFT_INK = 0.35


@dataclass(frozen=True)
class Box:
    """Axis-aligned rectangle, origin bottom-left (PDF coordinates)."""

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

    def inset(self, dx: float, dy: float | None = None) -> "Box":
        dy = dx if dy is None else dy
        return Box(self.x + dx, self.y + dy, max(self.w - 2 * dx, 0.0), max(self.h - 2 * dy, 0.0))

    def split_top(self, h: float) -> tuple["Box", "Box"]:
        """(strip of height h at the top, the rest below it)."""
        h = min(h, self.h)
        return Box(self.x, self.top - h, self.w, h), Box(self.x, self.y, self.w, self.h - h)

    def split_bottom(self, h: float) -> tuple["Box", "Box"]:
        """(strip of height h at the bottom, the rest above it)."""
        h = min(h, self.h)
        return Box(self.x, self.y, self.w, h), Box(self.x, self.y + h, self.w, self.h - h)

    def contains(self, x: float, y: float, tol: float = 1e-6) -> bool:
        return self.x - tol <= x <= self.right + tol and self.y - tol <= y <= self.top + tol

    def grid(self, cols: int, rows: int, gap: float = 0.0) -> list["Box"]:
        """Cells of a cols x rows grid, row by row from the top-left."""
        cw = (self.w - gap * (cols - 1)) / cols
        ch = (self.h - gap * (rows - 1)) / rows
        return [Box(self.x + c * (cw + gap), self.top - (r + 1) * ch - r * gap, cw, ch)
                for r in range(rows) for c in range(cols)]


@dataclass(frozen=True)
class Style:
    """Print rules every drawing function follows."""

    min_line_pt: float = 1.5
    footer_text: str = ""
    trace_style: str = "gray"          # "gray" (light filled letters) or "dotted" (dotted outlines)

    def lw(self, pt: float) -> float:
        """A line width that never drops below the print minimum."""
        return max(pt, self.min_line_pt)


@dataclass(frozen=True)
class PageGeometry:
    name: str
    width: float
    height: float
    margin: float
    footer_h: float = 20.0

    @property
    def content(self) -> Box:
        """Printable area inside the margins, above the footer band."""
        m = self.margin
        return Box(m, m + self.footer_h, self.width - 2 * m, self.height - 2 * m - self.footer_h)

    @property
    def page_box(self) -> Box:
        return Box(0.0, 0.0, self.width, self.height)


def fit_font_size(text: str, font: str, max_size: float, max_width: float, min_size: float = 6.0) -> float:
    """Largest size <= max_size at which `text` fits in max_width (never below min_size)."""
    width_at_1 = stringWidth(text, font, 1.0) or 1.0
    return max(min(max_size, max_width / width_at_1), min_size)


# Letters with no Unicode decomposition to a base letter.
_FOLD = {"Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "ı": "i", "Ħ": "H", "ħ": "h", "Ŧ": "T", "ŧ": "t"}


def printable(text: str) -> str:
    """`text` reduced to characters the built-in Helvetica can print.

    The standard-14 fonts only cover WinAnsi (cp1252); anything else prints as a black
    box. Letters outside it fold to their base letter ("Łódź Ő" -> "Lódz O"), other
    symbols (emoji, CJK) are dropped.
    """
    out = []
    for ch in text:
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            ch = _FOLD.get(ch, ch)
            out.extend(c for c in unicodedata.normalize("NFKD", ch) if c.isascii())
    return " ".join("".join(out).split())


def wrap_text(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy word wrap. A single word wider than `width` gets a line of its own."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}" if cur else word
        if cur and stringWidth(trial, font, size) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


def fit_lines(text: str, font: str, max_size: float, width: float, max_lines: int = 2,
              one_line_min: float = 0.0, min_size: float = 6.0) -> tuple[float, list[str]]:
    """(size, lines) for a title: one line if it fits at >= one_line_min, else the largest
    size at which it wraps into at most `max_lines` lines that each fit `width`.

    Long themes ("outer space adventure for little astronauts ...") would otherwise run off
    the page or shrink to an unreadable single line.
    """
    one = fit_font_size(text, font, max_size, width, min_size)
    if stringWidth(text, font, one) <= width + 1e-6 and (one >= one_line_min or max_lines <= 1):
        return one, [text]
    size = max_size
    while size >= min_size:
        lines = wrap_text(text, font, size, width)
        if len(lines) <= max_lines and all(stringWidth(s, font, size) <= width + 1e-6 for s in lines):
            return size, lines
        size -= 0.5
    # Nothing fits in max_lines: keep the minimum size and let the block grow taller instead
    # of overflowing sideways. Overlong single words are shrunk to fit.
    lines = wrap_text(text, font, min_size, width)
    widest = max(stringWidth(s, font, 1.0) for s in lines) or 1.0
    return min(min_size, width / widest), lines


def draw_lines_centered(c, cx: float, strip: "Box", lines: list[str], font: str, size: float,
                        leading: float = 1.15) -> None:
    """Draw `lines` centered horizontally on cx and vertically (by cap height) in `strip`."""
    lead = size * leading
    block = size * CAP_HEIGHT + (len(lines) - 1) * lead
    y = strip.y + (strip.h - block) / 2 + (len(lines) - 1) * lead
    c.setFont(font, size)
    for line in lines:
        c.drawCentredString(cx, y, line)
        y -= lead


def lines_height(n_lines: int, size: float, leading: float = 1.15) -> float:
    """Height of a block of n centered lines drawn by draw_lines_centered."""
    return size * CAP_HEIGHT + (n_lines - 1) * size * leading


def title_case(text: str) -> str:
    """Capitalize each word without lowercasing the rest (str.title breaks "kid's")."""
    return " ".join(w[:1].upper() + w[1:] for w in text.split())


def set_line(c, width: float, gray: float = INK, dash: list[float] | None = None) -> None:
    c.setLineWidth(width)
    c.setStrokeGray(gray)
    c.setLineCap(1)    # round caps and joins read as friendlier, kid-safe lines
    c.setLineJoin(1)
    c.setDash(dash or [], 0)


def polyline(c, points, closed: bool = False, stroke: bool = True, fill: bool = False) -> None:
    if len(points) < 2:
        return
    p = c.beginPath()
    p.moveTo(*points[0])
    for pt in points[1:]:
        p.lineTo(*pt)
    if closed:
        p.close()
    c.drawPath(p, stroke=int(stroke), fill=int(fill))


def capsule(c, p, q, radius: float, stroke: bool = True, fill: bool = False) -> None:
    """Rounded "pill" around the segment p-q: how an answer key circles a word."""
    (x1, y1), (x2, y2) = p, q
    dx, dy = x2 - x1, y2 - y1
    if math.hypot(dx, dy) < 1e-9:
        c.circle(x1, y1, radius, stroke=int(stroke), fill=int(fill))
        return
    ang = math.degrees(math.atan2(dy, dx))
    nx, ny = -math.sin(math.radians(ang)) * radius, math.cos(math.radians(ang)) * radius
    path = c.beginPath()
    path.moveTo(x1 + nx, y1 + ny)
    path.lineTo(x2 + nx, y2 + ny)
    path.arcTo(x2 - radius, y2 - radius, x2 + radius, y2 + radius, ang + 90, -180)
    path.lineTo(x1 - nx, y1 - ny)
    path.arcTo(x1 - radius, y1 - radius, x1 + radius, y1 + radius, ang - 90, -180)
    path.close()
    c.drawPath(path, stroke=int(stroke), fill=int(fill))


def draw_header(c, box: Box, title: str, instruction: str, style: Style, name_line: bool = True) -> Box:
    """Name line, page title and one-line instruction. Returns the box left below them."""
    scale = min(1.0, box.w / 520.0)
    rest = box
    if name_line:
        strip, rest = rest.split_top(22 * scale)
        size = 11 * scale
        c.setFillGray(SOFT_INK)
        c.setFont(FONT, size)
        c.drawString(strip.x, strip.y + 4, "Name:")
        x0 = strip.x + stringWidth("Name:", FONT, size) + 6
        set_line(c, style.lw(1.5), SOFT_INK)
        c.line(x0, strip.y + 3, min(strip.x + strip.w * 0.55, x0 + 220), strip.y + 3)
    size, lines = fit_lines(title, FONT_BOLD, 28 * scale, box.w, max_lines=2, one_line_min=18 * scale)
    strip, rest = rest.split_top(max(40 * scale, lines_height(len(lines), size) + 16 * scale))
    c.setFillGray(INK)
    draw_lines_centered(c, box.cx, strip, lines, FONT_BOLD, size)
    if instruction:
        strip, rest = rest.split_top(22 * scale)
        size = fit_font_size(instruction, FONT, 13 * scale, box.w)
        c.setFillGray(SOFT_INK)
        c.setFont(FONT, size)
        c.drawCentredString(box.cx, strip.y + (strip.h - size * CAP_HEIGHT) / 2, instruction)
    _, rest = rest.split_top(14 * scale)
    c.setFillGray(INK)
    return rest


def draw_footer(c, geo: PageGeometry, style: Style, page_number: int | None) -> None:
    """Small print (personal-use note) on the left, page number on the right, inside the margins."""
    y = geo.margin + 4
    c.setFillGray(SOFT_INK)
    if style.footer_text:
        width = geo.width - 2 * geo.margin - 60
        size = fit_font_size(style.footer_text, FONT, 7.5, width, 5.0)
        c.setFont(FONT, size)
        c.drawString(geo.margin, y, style.footer_text)
    if page_number is not None:
        c.setFont(FONT, 9)
        c.drawRightString(geo.width - geo.margin, y, str(page_number))
    c.setFillGray(INK)
