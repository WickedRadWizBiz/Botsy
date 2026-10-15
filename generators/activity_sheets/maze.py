"""Perfect mazes (recursive backtracker) and their drawing.

A perfect maze is a spanning tree of the cell grid: every cell is reachable and there
is exactly one path between any two cells, so the answer key is unambiguous.
Cells are (col, row) with row 0 at the top. START opens the top wall of the top-left
cell, FINISH opens the bottom wall of the bottom-right cell.
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

from generators.activity_sheets.layout import FONT_BOLD, CAP_HEIGHT, INK, Box, Style, polyline, set_line

Cell = tuple[int, int]


@dataclass(frozen=True)
class Maze:
    cols: int
    rows: int
    passages: frozenset[frozenset[Cell]]   # pairs of adjacent cells with no wall between them

    @property
    def start(self) -> Cell:
        return (0, 0)

    @property
    def finish(self) -> Cell:
        return (self.cols - 1, self.rows - 1)

    def is_open(self, a: Cell, b: Cell) -> bool:
        return frozenset((a, b)) in self.passages

    def neighbors(self, cell: Cell) -> list[Cell]:
        """Adjacent cells reachable from `cell` without crossing a wall."""
        return [n for n in _grid_neighbors(cell, self.cols, self.rows) if self.is_open(cell, n)]


def _grid_neighbors(cell: Cell, cols: int, rows: int) -> list[Cell]:
    c, r = cell
    out = [(c, r - 1), (c + 1, r), (c, r + 1), (c - 1, r)]
    return [(x, y) for x, y in out if 0 <= x < cols and 0 <= y < rows]


def make_maze(cols: int, rows: int, rng: random.Random) -> Maze:
    """Recursive backtracker (iterative). Long winding corridors suit young solvers."""
    if cols < 2 or rows < 2:
        raise ValueError("a maze needs at least 2x2 cells")
    start: Cell = (0, 0)
    visited = {start}
    stack = [start]
    passages: set[frozenset[Cell]] = set()
    while stack:
        cell = stack[-1]
        options = [n for n in _grid_neighbors(cell, cols, rows) if n not in visited]
        if not options:
            stack.pop()
            continue
        nxt = rng.choice(options)
        passages.add(frozenset((cell, nxt)))
        visited.add(nxt)
        stack.append(nxt)
    return Maze(cols, rows, frozenset(passages))


def solve(maze: Maze) -> list[Cell]:
    """Cells on the path from start to finish (BFS; unique in a perfect maze)."""
    prev: dict[Cell, Cell | None] = {maze.start: None}
    queue = deque([maze.start])
    while queue:
        cell = queue.popleft()
        if cell == maze.finish:
            break
        for n in maze.neighbors(cell):
            if n not in prev:
                prev[n] = cell
                queue.append(n)
    if maze.finish not in prev:
        raise ValueError("finish is not reachable")
    path, cur = [], maze.finish
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    return path[::-1]


def wall_segments(maze: Maze) -> list[tuple[Cell, Cell]]:
    """Walls as merged straight runs in grid-corner coordinates ((x0, y0), (x1, y1)).

    Corner (i, j) is the top-left corner of cell (i, j). Merging collinear walls keeps
    the PDF small and the strokes clean at the joints.
    """
    segs: list[tuple[Cell, Cell]] = []
    for j in range(maze.rows + 1):             # horizontal lines above row j
        xs = [i for i in range(maze.cols) if _has_h_wall(maze, i, j)]
        segs += [((a, j), (b + 1, j)) for a, b in _runs(xs)]
    for i in range(maze.cols + 1):             # vertical lines left of col i
        ys = [j for j in range(maze.rows) if _has_v_wall(maze, i, j)]
        segs += [((i, a), (i, b + 1)) for a, b in _runs(ys)]
    return segs


def _has_h_wall(maze: Maze, i: int, j: int) -> bool:
    if j == 0:
        return (i, 0) != maze.start              # START opening
    if j == maze.rows:
        return (i, maze.rows - 1) != maze.finish  # FINISH opening
    return not maze.is_open((i, j - 1), (i, j))


def _has_v_wall(maze: Maze, i: int, j: int) -> bool:
    if i in (0, maze.cols):
        return True
    return not maze.is_open((i - 1, j), (i, j))


def _runs(idx: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for k in idx:
        if runs and runs[-1][1] == k - 1:
            runs[-1] = (runs[-1][0], k)
        else:
            runs.append((k, k))
    return runs


def draw_maze(c, box: Box, maze: Maze, style: Style, solution: bool = False) -> dict:
    """Draw the maze centered in `box` with START/FINISH labels. Returns the geometry used."""
    band = min(26.0, box.h * 0.09)                    # label strip above and below the grid
    cell = min(box.w / maze.cols, (box.h - 2 * band) / maze.rows)
    gw, gh = cell * maze.cols, cell * maze.rows
    ox = box.cx - gw / 2
    top = box.cy + gh / 2
    wall = style.lw(min(8.0, cell * 0.14))

    def pt(i: float, j: float) -> tuple[float, float]:
        return ox + i * cell, top - j * cell

    def center(cl: Cell) -> tuple[float, float]:
        return pt(cl[0] + 0.5, cl[1] + 0.5)

    if solution:
        path = [center(cl) for cl in solve(maze)]
        sx, sy = path[0]
        fx, fy = path[-1]
        set_line(c, style.lw(cell * 0.32), 0.62)
        polyline(c, [(sx, top + band * 0.45)] + path + [(fx, top - gh - band * 0.45)])

    set_line(c, wall, INK)
    for (i0, j0), (i1, j1) in wall_segments(maze):
        c.line(*pt(i0, j0), *pt(i1, j1))

    size = band * 0.62
    c.setFillGray(INK)
    c.setFont(FONT_BOLD, size)
    sx, _ = center(maze.start)
    fx, _ = center(maze.finish)
    _arrow(c, sx, top + band * 0.15, cell, band)
    c.drawString(sx + min(cell, band) * 0.45, top + band * 0.5 - size * CAP_HEIGHT / 2, "START")
    _arrow(c, fx, top - gh - band * 0.95, cell, band)
    c.drawRightString(fx - min(cell, band) * 0.45, top - gh - band * 0.5 - size * CAP_HEIGHT / 2, "FINISH")
    return {"cell": cell, "wall": wall, "origin": (ox, top), "size": (gw, gh)}


def _arrow(c, x: float, y: float, cell: float, band: float) -> None:
    """Small solid down-pointing triangle with its tip at (x, y)."""
    half = min(cell, band) * 0.28
    h = band * 0.7
    p = c.beginPath()
    p.moveTo(x, y)
    p.lineTo(x - half, y + h)
    p.lineTo(x + half, y + h)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
