"""Word search grids: placement, filler, drawing and answer key.

Placement rules: a word may cross another only on an identical letter, must add at
least one new cell (so it is never hidden inside another word), and no listed word is
a substring of another listed word (or of its reverse, when words can run backwards).
After filling, every word must occur exactly once in the allowed directions, counting a
palindrome read both ways on the same cells as one occurrence. The filler is re-rolled
while that fails; if the placed words themselves spell a second copy, the words are
placed again. As a last resort an ambiguous word is left out of the word bank.
"""
from __future__ import annotations

import logging
import math
import random
import string
from dataclasses import dataclass

from reportlab.pdfbase.pdfmetrics import stringWidth

from generators.activity_sheets.layout import CAP_HEIGHT, FONT, FONT_BOLD, INK, Box, Style, capsule, set_line

log = logging.getLogger(__name__)

# (d_row, d_col); row 0 is the top row.
DIRECTIONS: dict[str, tuple[int, int]] = {
    "right": (0, 1), "down": (1, 0), "down_right": (1, 1), "up_right": (-1, 1),
    "left": (0, -1), "up": (-1, 0), "up_left": (-1, -1), "down_left": (1, -1),
}

PLACE_TRIES = 6        # placement attempts per grid size before growing the grid
FILL_TRIES = 12        # filler re-rolls per placement


@dataclass(frozen=True)
class Placement:
    word: str
    row: int
    col: int
    direction: str

    def cells(self) -> list[tuple[int, int]]:
        dr, dc = DIRECTIONS[self.direction]
        return [(self.row + k * dr, self.col + k * dc) for k in range(len(self.word))]


@dataclass(frozen=True)
class WordSearch:
    size: int
    grid: tuple[str, ...]              # `size` rows of `size` uppercase letters
    placements: tuple[Placement, ...]
    directions: tuple[str, ...]

    @property
    def words(self) -> list[str]:
        """Word bank, alphabetical."""
        return sorted(p.word for p in self.placements)


def reversible(directions) -> bool:
    """True if some allowed direction has its opposite allowed too (words can read backwards)."""
    vecs = {DIRECTIONS[d] for d in directions}
    return any((-dr, -dc) in vecs for dr, dc in vecs)


def clean_word_list(words: list[str], max_len: int, backwards: bool = False) -> list[str]:
    """Uppercase, letters only, 3..max_len, no duplicates, no word inside another.

    With `backwards`, a word is also dropped when its reverse overlaps another word
    ("STAR" / "RATS"): either one would always be found twice.
    """
    out: list[str] = []
    for w in words:
        w = w.strip().upper()
        if not (w.isalpha() and w.isascii() and 3 <= len(w) <= max_len):
            continue
        if any(w in o or o in w for o in out):
            continue
        if backwards and any(w[::-1] in o or o in w[::-1] for o in out):
            continue
        out.append(w)
    return out


def make_word_search(words: list[str], directions: list[str], max_size: int,
                     rng: random.Random, min_size: int = 8) -> WordSearch:
    """Place as many of `words` as fit (longest first) in the smallest square grid that works."""
    for d in directions:
        if d not in DIRECTIONS:
            raise ValueError(f"unknown word search direction {d!r}; known: {sorted(DIRECTIONS)}")
    if not directions:
        raise ValueError("a word search needs at least one direction")
    words = clean_word_list(words, max_size, reversible(directions))
    if not words:
        raise ValueError("no usable words for the word search")
    letters = sum(len(w) for w in words)
    size = min(max(min_size, max(len(w) for w in words), math.ceil(math.sqrt(letters / 0.5))), max_size)
    order = sorted(words, key=lambda w: (-len(w), w))

    for n in range(size, max_size + 1):
        for _ in range(PLACE_TRIES):
            placed = _try_place(order, n, directions, rng, allow_skip=False)
            if placed is None:
                continue
            grid, ambiguous = _fill(placed, n, directions, rng)
            if not ambiguous:
                return WordSearch(n, grid, tuple(placed), tuple(directions))

    # Last resort: the biggest grid, skipping words that do not fit and leaving any word that
    # still reads twice out of the word bank (its letters stay in the grid as filler).
    placed = _try_place(order, max_size, directions, rng, allow_skip=True) or []
    grid, ambiguous = _fill(placed, max_size, directions, rng)
    kept = [p for p in placed if p.word not in ambiguous]
    dropped = sorted(set(order) - {p.word for p in kept})
    log.warning("word search: left %s out of the %dx%d grid (no room or found twice)", dropped,
                max_size, max_size)
    if not kept:
        raise ValueError("could not build a word search from these words")
    return WordSearch(max_size, grid, tuple(kept), tuple(directions))


def _try_place(order: list[str], size: int, directions: list[str], rng: random.Random,
               allow_skip: bool) -> list[Placement] | None:
    cells = [[""] * size for _ in range(size)]
    placed: list[Placement] = []
    for word in order:
        options = _valid_placements(word, cells, size, directions)
        if not options:
            if allow_skip:
                continue
            return None
        p = rng.choice(options)
        for (r, c), ch in zip(p.cells(), word):
            cells[r][c] = ch
        placed.append(p)
    return placed


def _valid_placements(word: str, cells: list[list[str]], size: int, directions: list[str]) -> list[Placement]:
    out = []
    for d in directions:
        dr, dc = DIRECTIONS[d]
        for r in range(size):
            for c in range(size):
                p = Placement(word, r, c, d)
                rr, cc = r + dr * (len(word) - 1), c + dc * (len(word) - 1)
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                spots = p.cells()
                if any(cells[y][x] not in ("", ch) for (y, x), ch in zip(spots, word)):
                    continue                       # collision with a different letter
                if all(cells[y][x] for y, x in spots):
                    continue                       # fully hidden inside earlier words
                out.append(p)
    return out


def _fill(placed: list[Placement], size: int, directions: list[str], rng: random.Random,
          attempts: int = FILL_TRIES) -> tuple[tuple[str, ...], list[str]]:
    """Fill the empty cells with random letters. Returns (grid, words found more than once);
    the list is empty on success. Keeps the grid with the fewest ambiguous words."""
    cells = [[""] * size for _ in range(size)]
    for p in placed:
        for (r, c), ch in zip(p.cells(), p.word):
            cells[r][c] = ch
    best: tuple[tuple[str, ...], list[str]] | None = None
    for _ in range(attempts):
        grid = tuple("".join(ch or rng.choice(string.ascii_uppercase) for ch in row) for row in cells)
        found = occurrences(grid, [p.word for p in placed], directions)
        ambiguous = sorted(w for w, spots in found.items() if len(spots) != 1)
        if best is None or len(ambiguous) < len(best[1]):
            best = (grid, ambiguous)
        if not ambiguous:
            break
    return best if best else ((), [])


def _lines(grid, directions):
    """Every maximal straight line through the grid per direction: (direction, cells, text)."""
    size = len(grid)
    for d in directions:
        dr, dc = DIRECTIONS[d]
        for r in range(size):
            for c in range(size):
                if 0 <= r - dr < size and 0 <= c - dc < size:
                    continue                       # not the first cell of a line
                cells = []
                y, x = r, c
                while 0 <= y < size and 0 <= x < size:
                    cells.append((y, x))
                    y, x = y + dr, x + dc
                yield d, cells, "".join(grid[y][x] for y, x in cells)


def _scan(grid, words, directions) -> dict[str, list[Placement]]:
    found: dict[str, list[Placement]] = {w: [] for w in words}
    for d, cells, text in _lines(grid, directions):
        for w in found:
            i = text.find(w)
            while i != -1:
                found[w].append(Placement(w, cells[i][0], cells[i][1], d))
                i = text.find(w, i + 1)
    return found


def find_all(grid: tuple[str, ...] | list[str], word: str, directions) -> list[Placement]:
    """Every occurrence of `word` in `grid` along `directions`, ordered by direction then
    start cell. A palindrome read both ways on the same cells appears twice here."""
    order = {d: i for i, d in enumerate(directions)}
    hits = _scan(grid, [word], directions)[word]
    return sorted(hits, key=lambda p: (order[p.direction], p.row, p.col))


def occurrences(grid, words, directions) -> dict[str, set[frozenset]]:
    """For each word, the distinct cell sets where it can be read. A solver sees one
    occurrence per cell set, so a palindrome on the same cells counts once."""
    return {w: {frozenset(p.cells()) for p in hits} for w, hits in _scan(grid, words, directions).items()}


def draw_word_search(c, box: Box, ws: WordSearch, style: Style, solution: bool = False,
                     word_bank: bool = True) -> dict:
    """Grid (plus word bank below it) fitted to `box`. The answer key circles every word."""
    grid_box = box
    bank_layout = None
    if word_bank:
        bank_layout = _bank_layout(ws.words, box.w)
        bank_h = bank_layout["height"]
        bank_box, grid_box = box.split_bottom(bank_h)
        _, grid_box = grid_box.split_bottom(14)
    pad_cells = 0.35
    cell = min(grid_box.w, grid_box.h) / (ws.size + 2 * pad_cells)
    gw = cell * ws.size
    ox = grid_box.cx - gw / 2
    top = grid_box.cy + gw / 2

    def center(r: int, col: int) -> tuple[float, float]:
        return ox + (col + 0.5) * cell, top - (r + 0.5) * cell

    pad = cell * pad_cells
    set_line(c, style.lw(2.0), INK)
    c.roundRect(ox - pad, top - gw - pad, gw + 2 * pad, gw + 2 * pad, cell * 0.5, stroke=1, fill=0)

    if solution:
        radius = cell * 0.4
        c.setFillGray(0.86)
        set_line(c, style.lw(1.5), 0.25)
        for p in ws.placements:
            cells = p.cells()
            capsule(c, center(*cells[0]), center(*cells[-1]), radius, stroke=False, fill=True)
        for p in ws.placements:
            cells = p.cells()
            capsule(c, center(*cells[0]), center(*cells[-1]), radius, stroke=True, fill=False)

    size = cell * 0.58
    c.setFillGray(INK)
    c.setFont(FONT_BOLD if solution else FONT, size)
    for r, row in enumerate(ws.grid):
        for col, ch in enumerate(row):
            x, y = center(r, col)
            c.drawCentredString(x, y - size * CAP_HEIGHT / 2, ch)

    if bank_layout:
        _draw_bank(c, bank_box, ws.words, bank_layout, style)
    return {"cell": cell, "origin": (ox, top)}


def _bank_layout(words: list[str], width: float, size: float = 14.0) -> dict:
    widest = max(stringWidth(w, FONT_BOLD, size) for w in words)
    col_w = widest + 28
    cols = max(1, min(4, int((width - 24) // col_w)))
    rows = math.ceil(len(words) / cols)
    line_h = size * 1.6
    return {"size": size, "cols": cols, "rows": rows, "line_h": line_h, "col_w": col_w,
            "height": rows * line_h + 26}


def _draw_bank(c, box: Box, words: list[str], lay: dict, style: Style) -> None:
    set_line(c, style.lw(1.5), 0.45)
    c.roundRect(box.x, box.y, box.w, box.h, 10, stroke=1, fill=0)
    size, cols, rows, line_h = lay["size"], lay["cols"], lay["rows"], lay["line_h"]
    col_w = box.w / cols
    c.setFillGray(INK)
    c.setFont(FONT_BOLD, size)
    top = box.top - 13 - line_h / 2
    for k, w in enumerate(words):
        col, row = divmod(k, rows)     # fill column by column so the list reads downward
        x = box.x + col * col_w + col_w / 2
        y = top - row * line_h - size * CAP_HEIGHT / 2
        c.drawCentredString(x, y, w)
