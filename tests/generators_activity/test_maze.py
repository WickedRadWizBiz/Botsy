"""Mazes are perfect (spanning trees), solvable, and reproducible from their seed."""
import random
from collections import deque

import pytest

from generators.activity_sheets.maze import make_maze, solve, wall_segments


def _reachable(maze, start):
    seen, queue = {start}, deque([start])
    while queue:
        for n in maze.neighbors(queue.popleft()):
            if n not in seen:
                seen.add(n)
                queue.append(n)
    return seen


def _count_paths(maze, cell, goal, seen):
    if cell == goal:
        return 1
    return sum(_count_paths(maze, n, goal, seen | {n}) for n in maze.neighbors(cell) if n not in seen)


@pytest.mark.parametrize("cols, rows, seed", [(8, 10, 1), (12, 15, 2), (16, 20, 3), (2, 2, 4), (5, 3, 5)])
def test_maze_is_perfect(cols, rows, seed):
    maze = make_maze(cols, rows, random.Random(seed))
    cells = cols * rows
    assert len(_reachable(maze, maze.start)) == cells          # every cell reachable
    assert len(maze.passages) == cells - 1                     # connected + n-1 edges = tree: no loops
    for a, b in (tuple(p) for p in maze.passages):              # passages join grid neighbours only
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def test_exactly_one_path_start_to_finish():
    maze = make_maze(5, 5, random.Random(7))
    assert _count_paths(maze, maze.start, maze.finish, {maze.start}) == 1


def test_solution_walks_open_passages_from_start_to_finish():
    maze = make_maze(12, 15, random.Random(11))
    path = solve(maze)
    assert path[0] == maze.start == (0, 0)
    assert path[-1] == maze.finish == (11, 14)
    assert len(set(path)) == len(path)
    assert all(maze.is_open(a, b) for a, b in zip(path, path[1:]))


def test_walls_leave_only_start_and_finish_openings():
    maze = make_maze(6, 7, random.Random(3))
    horiz, vert = set(), set()          # unit wall pieces: (col, line) and (line, row)
    for (x0, y0), (x1, y1) in wall_segments(maze):
        if y0 == y1:
            horiz |= {(i, y0) for i in range(x0, x1)}
        else:
            vert |= {(x0, j) for j in range(y0, y1)}
    assert (0, 0) not in horiz                                   # START gap in the top edge
    assert (5, 7) not in horiz                                   # FINISH gap in the bottom edge
    assert all((i, 0) in horiz for i in range(1, 6))
    assert all((i, 7) in horiz for i in range(0, 5))
    assert all((0, j) in vert and (6, j) in vert for j in range(7))
    # every interior wall is exactly a closed neighbour pair
    for i in range(6):
        for j in range(1, 7):
            assert ((i, j) in horiz) == (not maze.is_open((i, j - 1), (i, j)))


def test_same_seed_same_maze():
    a = make_maze(10, 12, random.Random(42))
    b = make_maze(10, 12, random.Random(42))
    c = make_maze(10, 12, random.Random(43))
    assert a == b and a != c


def test_rejects_degenerate_grid():
    with pytest.raises(ValueError):
        make_maze(1, 5, random.Random(0))
