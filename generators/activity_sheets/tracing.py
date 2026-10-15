"""Handwriting practice: letters (upper and lower), numbers and theme words.

Each row has three guide lines: a solid top line at cap height, a dashed midline at
Helvetica's x-height (so lowercase letters touch it), and a solid baseline. The first
glyph of a row is the dark model; the rest are light gray (or dotted) forms to trace,
each with a dot where the first stroke starts.
"""
from __future__ import annotations

import string
from dataclasses import dataclass

from reportlab.pdfbase.pdfmetrics import stringWidth

from generators.activity_sheets.layout import CAP_HEIGHT, DESCENT, FONT, X_HEIGHT, Box, Style, set_line

# Approximate first-stroke start for each glyph in Helvetica:
# (fraction of the glyph's advance width, fraction of cap height above the baseline).
# Top of an x-height letter is 0.73 (523/718).
_TOP, _XH = 1.0, X_HEIGHT / CAP_HEIGHT
STROKE_START: dict[str, tuple[float, float]] = {
    "A": (0.5, _TOP), "B": (0.12, _TOP), "C": (0.85, 0.85), "D": (0.12, _TOP), "E": (0.12, _TOP),
    "F": (0.12, _TOP), "G": (0.85, 0.85), "H": (0.12, _TOP), "I": (0.5, _TOP), "J": (0.75, _TOP),
    "K": (0.12, _TOP), "L": (0.12, _TOP), "M": (0.12, _TOP), "N": (0.12, _TOP), "O": (0.5, _TOP),
    "P": (0.12, _TOP), "Q": (0.5, _TOP), "R": (0.12, _TOP), "S": (0.82, 0.85), "T": (0.5, _TOP),
    "U": (0.13, _TOP), "V": (0.05, _TOP), "W": (0.03, _TOP), "X": (0.08, _TOP), "Y": (0.06, _TOP),
    "Z": (0.08, _TOP),
    "a": (0.78, 0.62), "b": (0.12, _TOP), "c": (0.82, 0.62), "d": (0.85, _TOP), "e": (0.12, 0.4),
    "f": (0.75, 0.97), "g": (0.8, 0.65), "h": (0.12, _TOP), "i": (0.5, _XH), "j": (0.6, _XH),
    "k": (0.12, _TOP), "l": (0.5, _TOP), "m": (0.12, _XH), "n": (0.12, _XH), "o": (0.5, _XH),
    "p": (0.12, _XH), "q": (0.8, 0.65), "r": (0.15, _XH), "s": (0.78, 0.62), "t": (0.45, 0.93),
    "u": (0.12, _XH), "v": (0.05, _XH), "w": (0.03, _XH), "x": (0.08, _XH), "y": (0.08, _XH),
    "z": (0.1, _XH),
    "0": (0.5, _TOP), "1": (0.55, _TOP), "2": (0.15, 0.8), "3": (0.15, 0.85), "4": (0.65, _TOP),
    "5": (0.28, _TOP), "6": (0.75, 0.9), "7": (0.1, _TOP), "8": (0.75, 0.85), "9": (0.8, 0.7),
}

LESSON_CYCLE = ("letters", "words", "letters", "numbers")
NUMBERS = tuple(str(n) for n in range(1, 11))
MAX_TRACE_WORD = 8


@dataclass(frozen=True)
class TracingLesson:
    kind: str                 # letters | numbers | words
    rows: tuple[str, ...]     # one practice row each
    subject: tuple[str, ...]  # what the page teaches, for the title

    @property
    def title(self) -> str:
        if self.kind == "letters":
            return "Trace the Letters " + " and ".join(self.subject)
        if self.kind == "numbers":
            return f"Trace the Numbers {self.subject[0]} to {self.subject[-1]}"
        return "Trace the Words"

    @property
    def instruction(self) -> str:
        noun = {"letters": "letter", "numbers": "number"}.get(self.kind, "word")
        return f"Start at the dot and trace each {noun}."   # true for gray and dotted styles


def plan_lessons(count: int, words: list[str], letters_per_page: int = 2, numbers_per_page: int = 5,
                 words_per_page: int = 5) -> list[TracingLesson]:
    """`count` lessons rotating letters / words / letters / numbers, advancing through each."""
    letters_per_page, numbers_per_page, words_per_page = (max(1, int(n)) for n in
                                                          (letters_per_page, numbers_per_page, words_per_page))
    trace_words = [w for w in words if len(w) <= MAX_TRACE_WORD]
    cycle = [k for k in LESSON_CYCLE if k != "words" or trace_words]
    lessons, pos = [], {"letters": 0, "numbers": 0, "words": 0}
    letters = string.ascii_uppercase
    for i in range(count):
        kind = cycle[i % len(cycle)]
        if kind == "letters":
            subj = tuple(letters[(pos[kind] + j) % 26] for j in range(letters_per_page))
            rows = tuple(r for ch in subj for r in (ch, ch.lower()))
            pos[kind] += letters_per_page
        elif kind == "numbers":
            subj = tuple(NUMBERS[(pos[kind] + j) % len(NUMBERS)] for j in range(numbers_per_page))
            rows = subj
            pos[kind] += numbers_per_page
        else:
            take = min(words_per_page, len(trace_words))
            subj = tuple(trace_words[(pos[kind] + j) % len(trace_words)] for j in range(take))
            rows = subj
            pos[kind] += take
        lessons.append(TracingLesson(kind, rows, subj))
    return lessons


def draw_tracing(c, box: Box, lesson: TracingLesson, style: Style) -> dict:
    """Practice rows filling `box`. Row size adapts to the page; content does not."""
    n = len(lesson.rows)
    pitch = box.h / n
    guide = min(pitch * 0.56, 96.0)                   # top line to baseline
    side = 6.0
    if lesson.kind == "words":
        # every row must hold the model word plus at least one copy to trace
        widest = max(stringWidth(w, FONT, 1.0) for w in lesson.rows)
        guide = min(guide, (box.w - 2 * side) / (2 * widest + 0.8) * CAP_HEIGHT)
    size = guide / CAP_HEIGHT
    copies_per_row = []
    for k, text in enumerate(lesson.rows):
        slot_top = box.top - k * pitch
        top_line = slot_top - (pitch - guide - DESCENT * size) / 2
        base = top_line - guide
        _guides(c, box, top_line, base + X_HEIGHT * size, base, style)
        copies_per_row.append(_practice_row(c, box, side, base, text, size, style, lesson.kind == "words"))
    return {"font_size": size, "guide": guide, "copies": copies_per_row}


def _guides(c, box: Box, top: float, mid: float, base: float, style: Style) -> None:
    set_line(c, style.lw(1.5), 0.55)
    c.line(box.x, top, box.right, top)
    set_line(c, style.lw(1.5), 0.62, dash=[6, 5])
    c.line(box.x, mid, box.right, mid)
    set_line(c, style.lw(2.0), 0.3)
    c.line(box.x, base, box.right, base)
    c.setDash([], 0)


def _practice_row(c, box: Box, side: float, base: float, text: str, size: float, style: Style,
                  is_word: bool) -> int:
    width = stringWidth(text, FONT, size)
    gap = size * (0.8 if is_word else 0.45)
    usable = box.w - 2 * side
    count = max(2 if is_word else 1, min(12, int((usable + gap) // (width + gap))))
    x = box.x + side
    for i in range(count):
        if i == 0:
            c.setFillGray(0.12)
            c.setFont(FONT, size)
            c.drawString(x, base, text)
        else:
            _trace_text(c, x, base, text, size, style)
            _start_dot(c, x, base, text[0], size)
        x += width + gap
    c.setFillGray(0)
    return count


def _trace_text(c, x: float, base: float, text: str, size: float, style: Style) -> None:
    if style.trace_style == "dotted":
        set_line(c, style.lw(1.5), 0.35, dash=[0.1, 3.2])
        t = c.beginText(x, base)
        t.setFont(FONT, size)
        t.setTextRenderMode(1)           # stroke only: dotted outline letters
        t.textOut(text)
        t.setTextRenderMode(0)
        c.drawText(t)
        c.setDash([], 0)
    else:
        c.setFillGray(0.8)
        c.setFont(FONT, size)
        c.drawString(x, base, text)


def _start_dot(c, x: float, base: float, ch: str, size: float) -> None:
    fx, fy = STROKE_START.get(ch, (0.15, _TOP))
    adv = stringWidth(ch, FONT, size)
    c.setFillGray(0.15)
    c.circle(x + fx * adv, base + fy * CAP_HEIGHT * size, max(2.0, size * 0.04), stroke=0, fill=1)
