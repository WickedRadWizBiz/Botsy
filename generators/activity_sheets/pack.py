"""Plan an activity pack from a brief and render it to one PDF per page size.

Page order in every PDF:
    1             title page (unnumbered)
    2 .. N+1      the N activity pages the brief asked for (brief["page_count"])
    N+2 ..        answer keys (mazes, word searches, dot-to-dots; several per page)
Only activity pages count toward page_count; the title page and answer keys are extra.

Puzzle content is planned once per pack and shared by every page size, so US Letter
and A4 buyers get the same puzzles; only the layout (cell sizes, row heights, label
positions) is recomputed for each page.
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.pdfgen import canvas

from core.compliance import Blocklist
from generators.activity_sheets import dot_to_dot, maze, tracing, vocab, word_search
from generators.activity_sheets.layout import (
    FONT, FONT_BOLD, INK, SOFT_INK, Box, PageGeometry, Style, draw_footer, draw_header, draw_lines_centered,
    fit_font_size, fit_lines, lines_height, polyline, printable, set_line, title_case,
)
from generators.activity_sheets.shapes import SHAPES_BY_NAME, Shape, rank_shapes
from generators.activity_sheets.word_search import clean_word_list
from generators.shared import as_list

log = logging.getLogger(__name__)

KINDS = ("maze", "word_search", "tracing", "dot_to_dot")
KIND_LABELS = {"maze": "Mazes", "word_search": "Word Searches", "tracing": "Tracing", "dot_to_dot": "Dot-to-Dot"}
WITH_ANSWERS = ("maze", "word_search", "dot_to_dot")


@dataclass
class ActivityPage:
    number: int            # printed page number (= PDF page number)
    kind: str
    title: str
    instruction: str
    content: object


@dataclass
class Pack:
    item_id: str
    theme: str
    audience: str
    difficulty: str
    kinds: list[str]
    pages: list[ActivityPage]
    cover: Shape
    words: list[str] = field(default_factory=list)

    @property
    def theme_title(self) -> str:
        return title_case(printable(self.theme))

    def answer_chunks(self, per_page: int) -> list[list[ActivityPage]]:
        keyed = [p for p in self.pages if p.kind in WITH_ANSWERS]
        return [keyed[i:i + per_page] for i in range(0, len(keyed), per_page)]

    def printed_text(self) -> list[str]:
        """Every theme-derived string that ends up on a page, for the final blocklist check."""
        out = [self.theme_title, self.audience] + [p.title for p in self.pages]
        for p in self.pages:
            if isinstance(p.content, word_search.WordSearch):
                out += p.content.words
            elif isinstance(p.content, tracing.TracingLesson):
                out += list(p.content.rows)
        return out


def rng_for(item_id: str, *parts) -> random.Random:
    """Independent, reproducible random stream per item and page."""
    key = "|".join([item_id, *map(str, parts)])
    return random.Random(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big"))


def resolve_kinds(brief: dict, settings: dict) -> list[str]:
    asked = (as_list(brief.get("activity_types"))
             or settings.get("niches", {}).get("activity_sheets", {}).get("types") or list(KINDS))
    kinds = [k for k in dict.fromkeys(asked) if k in KINDS]
    unknown = [k for k in asked if k not in KINDS]
    if unknown:
        log.warning("ignoring unknown activity types %s", unknown)
    if not kinds:
        raise ValueError(f"no known activity types in {asked!r}; choose from {list(KINDS)}")
    return kinds


def resolve_difficulty(brief: dict, cfg: dict) -> str:
    """brief["difficulty"] if valid, else settings, else "auto" from the audience's youngest age."""
    levels = cfg["levels"]
    for choice in (brief.get("difficulty"), cfg.get("difficulty")):
        if choice in levels:
            return choice
    audience = (brief.get("audience") or "").lower()
    ages = [int(a) for a in re.findall(r"\d+", audience)]
    if any(w in audience for w in ("toddler", "preschool", "pre-k", "prek")):
        return "easy" if "easy" in levels else next(iter(levels))
    if ages and min(ages) > int(cfg.get("auto_easy_max_age", 5)) and "medium" in levels:
        return "medium"
    return "easy" if "easy" in levels else next(iter(levels))


def plan_pack(brief: dict, item_id: str, cfg: dict, blocklist: Blocklist, settings: dict) -> Pack:
    """The whole pack for one brief (brief["page_count"] pages), seeded from item_id: the
    page specs of plan_specs() turned into puzzles."""
    n_pages = int(brief.get("page_count") or (settings.get("niches", {}).get("activity_sheets", {})
                                               .get("pages_per_pack") or [10])[0])
    ctx = brief_ctx(brief, item_id)
    specs = plan_specs(ctx, n_pages, seed=item_id, cfg=cfg, blocklist=blocklist, settings=settings)
    return build_pack(specs, ctx)


def brief_ctx(brief: dict, item_seed: str = "") -> dict:
    """The plugin ctx (docs/PACKS.md section 3) for a brief, outside the pack engine."""
    return {"theme": brief.get("theme"), "audience": str(brief.get("audience") or ""),
            "keywords": as_list(brief.get("keywords")), "activity_types": as_list(brief.get("activity_types")),
            "difficulty": brief.get("difficulty"), "item_seed": item_seed, "used_subjects": []}


def _subject(kind: str, content) -> str:
    """Short description of what a page holds (curation variety, fresh sets avoid repeats)."""
    if kind == "word_search":
        return "word search: " + ", ".join(w.lower() for w in content)
    if kind == "tracing":
        return "tracing: " + content.title.lower()
    if kind == "dot_to_dot":
        return f"dot-to-dot: {content.name}"
    return "maze"


def plan_specs(ctx: dict, n: int, *, seed: str, cfg: dict, blocklist: Blocklist, settings: dict) -> list[dict]:
    """n distinct page specs (the pack engine's plan_pages): types round-robin over the
    brief's activity types, puzzles seeded from `seed`. A spec holds everything needed to
    rebuild its puzzle at any page size (grid sizes and a seed, word list, lesson rows,
    picture name); nothing here is random outside rng_for(seed, ...), so the same
    (ctx, n, seed) always gives the same pages. Subjects already used by the theme's other
    design sets (ctx["used_subjects"]) come last, so a fresh pack brings new pictures,
    lessons and word lists."""
    theme = printable(ctx.get("theme") or "")
    if not any(ch.isalnum() for ch in theme):
        raise ValueError(f"theme {ctx.get('theme')!r} has no printable letters")
    keywords = [printable(k) for k in as_list(ctx.get("keywords"))]
    kinds = resolve_kinds({"activity_types": ctx.get("activity_types")}, settings)
    if not 1 <= n <= int(cfg["max_pages"]):
        raise ValueError(f"page_count {n} outside 1..{cfg['max_pages']}")
    difficulty = resolve_difficulty({"difficulty": ctx.get("difficulty"), "audience": ctx.get("audience")}, cfg)
    level = cfg["levels"][difficulty]
    order = [kinds[i % len(kinds)] for i in range(n)]
    used = {str(u) for u in ctx.get("used_subjects") or []}

    # A pool big enough for one full word search even after clean_word_list drops overlaps.
    words = vocab.theme_words(theme, keywords, blocklist,
                              min_pool=max(12, int(level["word_search"]["words"]) + 4))
    tcfg = cfg["tracing"]
    n_tracing = order.count("tracing")
    lessons = tracing.plan_lessons(n_tracing + len(used), words, tcfg["letters_per_page"],
                                   tcfg["numbers_per_page"], tcfg["words_per_page"])
    lessons = _new_first(lessons, lambda le: _subject("tracing", le), used)
    matched, others = rank_shapes(vocab.tokens(theme, *keywords), rng_for(seed, "shapes"))
    on_theme = {sh.name for sh in matched}
    shapes = _new_first(matched + others, lambda sh: _subject("dot_to_dot", sh), used)
    ws = level["word_search"]
    ws_words = clean_word_list(words, int(ws["max_size"]), word_search.reversible(ws["directions"]))
    ws_lists = word_lists(ws_words, int(ws["words"]), order.count("word_search"), rng_for(seed, "word_lists"),
                          used=[u for u in used if u.startswith("word search:")])
    title = title_case(theme)

    pages: list[dict] = []
    seen = {k: 0 for k in KINDS}
    for kind in order:
        k = seen[kind]
        seen[kind] += 1
        base = {"difficulty": difficulty, "seed": f"{seed}|{kind}|{k}"}
        if kind == "maze":
            m = level["maze"]
            spec = dict(base, cols=int(m["cols"]), rows=int(m["rows"]), title=f"{title} Maze",
                        instruction="Help find the way from START to FINISH!")
            subject = "maze"
        elif kind == "word_search":
            chosen = ws_lists[k]
            spec = dict(base, words=chosen, directions=list(ws["directions"]), max_size=int(ws["max_size"]),
                        title=f"{title} Word Search", instruction=_ws_instruction(ws["directions"]))
            subject = _subject(kind, chosen)
        elif kind == "tracing":
            lesson = lessons[k % len(lessons)]
            spec = dict(base, lesson=lesson.kind, rows=list(lesson.rows), lesson_subject=list(lesson.subject),
                        title=lesson.title, instruction=lesson.instruction)
            subject = _subject(kind, lesson)
        else:
            shape = shapes[k % len(shapes)]
            dots = int(level["dot_to_dot"]["dots"])
            pic = dot_to_dot.make_picture(shape, dots)
            tail = " and back to 1" if pic.closed else ""
            spec = dict(base, shape=shape.name, dots=dots, title="Connect the Dots",
                        instruction=f"Join the dots from 1 to {len(pic.dots)}{tail}. Then color it in!")
            subject = _subject(kind, shape)
        page = {"page_id": f"{kind}-{k + 1:02d}", "type": kind, "subject": subject, "spec": spec}
        if kind == "dot_to_dot":
            page["fit"] = 1.0 if spec["shape"] in on_theme else 0.6     # curation: on-theme pictures first
        pages.append(page)
    return pages


def _new_first(items: list, key, used: set[str]) -> list:
    """Items whose subject is not in `used` first (in order), then the used ones."""
    return [x for x in items if key(x) not in used] + [x for x in items if key(x) in used]


def build_pack(pages: list[dict], ctx: dict) -> Pack:
    """The puzzles of a pack's pages (specs from plan_specs, in pack order), numbered from 2
    (page 1 is the title page). Rebuilding from the spec gives the same puzzle every time."""
    theme = printable(ctx.get("theme") or "")
    keywords = [printable(k) for k in as_list(ctx.get("keywords"))]
    out: list[ActivityPage] = []
    words: list[str] = []
    for i, p in enumerate(pages):
        kind, spec = p["type"], p.get("spec") or {}
        if kind not in KINDS:
            raise ValueError(f"unknown activity page type {kind!r}")
        rng = rng_for(spec["seed"])
        if kind == "maze":
            content = maze.make_maze(int(spec["cols"]), int(spec["rows"]), rng)
        elif kind == "word_search":
            content = word_search.make_word_search(list(spec["words"]), list(spec["directions"]),
                                                   int(spec["max_size"]), rng)
            words += [w for w in spec["words"] if w not in words]
        elif kind == "tracing":
            content = tracing.TracingLesson(spec["lesson"], tuple(spec["rows"]), tuple(spec["lesson_subject"]))
        else:
            content = dot_to_dot.make_picture(SHAPES_BY_NAME[spec["shape"]], int(spec["dots"]))
        out.append(ActivityPage(i + 2, kind, spec["title"], spec["instruction"], content))
    kinds = [k for k in KINDS if any(p.kind == k for p in out)]
    difficulty = next((str((p.get("spec") or {}).get("difficulty")) for p in pages
                       if (p.get("spec") or {}).get("difficulty")), "easy")
    taken = {p.content.shape for p in out if p.kind == "dot_to_dot"}
    matched, others = rank_shapes(vocab.tokens(theme, *keywords), rng_for(ctx.get("item_seed") or theme, "shapes"))
    return Pack(str(ctx.get("item_seed") or ""), theme, printable(ctx.get("audience") or ""), difficulty, kinds, out,
                cover_shape(matched, others, taken), words)


def cover_shape(matched: list[Shape], others: list[Shape], taken: set[str]) -> Shape:
    """Title-page art that fits the theme without giving away a dot-to-dot answer.

    Falls back to a neutral shape (star, sun, ...) before repeating a puzzle picture.
    """
    neutral = [SHAPES_BY_NAME[n] for n in ("star", "sun", "balloon", "heart")]
    for s in matched + neutral + others:
        if s.name not in taken:
            return s
    return (matched + others)[0]


def word_lists(pool: list[str], n: int, count: int, rng: random.Random, used=()) -> list[list[str]]:
    """`count` word lists of n words for a pack's word searches, so no two puzzles of a pack
    (or of the theme's other design sets, `used` subjects "word search: a, b, ...") share a
    word list. While the pool lasts the lists are disjoint slices in pool order (theme and
    brief words first); after that each list takes the words used least so far, ties broken
    by `rng`, and is redrawn when it repeats an earlier list. A pool of n words or fewer can
    only give the same list again."""
    if not pool or count < 1:
        return [[] for _ in range(max(0, count))]
    n = min(n, len(pool))
    uses = {w: 0 for w in pool}
    by_key = {w.lower(): w for w in pool}           # subjects hold the words in lowercase
    seen: set[frozenset] = set()
    for u in used:
        earlier = [by_key.get(w.strip().lower(), w.strip().lower()) for w in str(u).split(":", 1)[-1].split(",")
                   if w.strip()]
        seen.add(frozenset(earlier))
        for w in earlier:
            if w in uses:
                uses[w] += 1
    out: list[list[str]] = []
    for k in range(count):
        chunk = pool[k * n:(k + 1) * n]
        if not used and len(chunk) == n:
            chosen = chunk
        else:
            chosen = []
            for _ in range(20):
                ties = {w: rng.random() for w in pool}
                pick = set(sorted(pool, key=lambda w: (uses[w], ties[w]))[:n])
                chosen = [w for w in pool if w in pick]
                if frozenset(chosen) not in seen:
                    break
        seen.add(frozenset(chosen))
        for w in chosen:
            uses[w] += 1
        out.append(chosen)
    return out


def _ws_instruction(directions) -> str:
    dirs = set(directions)
    if dirs <= {"right", "down"}:
        return "Find and circle each word. Words go across and down."
    if dirs <= {"right", "down", "down_right", "up_right"}:
        return "Find and circle each word. Words go across, down and diagonally."
    return "Find and circle each word. Words can go in any direction, even backwards!"


# --- rendering ---------------------------------------------------------------------------

def _draw_activity(c, box: Box, page: ActivityPage, style: Style, cfg: dict, solution: bool = False):
    if page.kind == "maze":
        return maze.draw_maze(c, box, page.content, style, solution=solution)
    if page.kind == "word_search":
        return word_search.draw_word_search(c, box, page.content, style, solution=solution,
                                            word_bank=not solution)
    if page.kind == "tracing":
        return tracing.draw_tracing(c, box, page.content, style)
    return dot_to_dot.draw_dot_to_dot(c, box, page.content, style, solution=solution,
                                      faint_preview=bool(cfg["dot_to_dot"].get("faint_preview")))


def render_pdf(pack: Pack, geo: PageGeometry, path: Path, style: Style, cfg: dict) -> Path:
    """Write the whole pack for one page size. `invariant` makes reruns byte-identical."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(geo.width, geo.height), invariant=1, pageCompression=1)
    c.setTitle(f"{pack.theme_title} Activity Pack")
    c.setSubject(", ".join(KIND_LABELS[k] for k in pack.kinds))
    c.setCreator("etsy-agents activity_sheets")

    _title_page(c, geo, pack, style)
    c.showPage()
    for page in pack.pages:
        box = draw_header(c, geo.content, page.title, page.instruction, style)
        _draw_activity(c, box, page, style, cfg)
        draw_footer(c, geo, style, page.number)
        c.showPage()

    cols, rows = (int(v) for v in cfg["answer_key_grid"])
    number = len(pack.pages) + 2
    for chunk in pack.answer_chunks(cols * rows):
        box = draw_header(c, geo.content, "Answer Key", "", style, name_line=False)
        for cell, page in zip(box.grid(cols, rows, gap=18), chunk):
            strip, rest = cell.split_top(18)
            c.setFillGray(SOFT_INK)
            c.setFont(FONT_BOLD, 11)
            c.drawCentredString(strip.cx, strip.y + 4, f"Page {page.number}: {page.title}")
            c.setFillGray(INK)
            _draw_activity(c, rest.inset(6), page, style, cfg, solution=True)
        draw_footer(c, geo, style, number)
        c.showPage()
        number += 1
    c.save()
    return path


def _title_page(c, geo: PageGeometry, pack: Pack, style: Style) -> None:
    box = geo.content
    set_line(c, style.lw(3.0), INK)
    c.roundRect(box.x, box.y, box.w, box.h, 22, stroke=1, fill=0)
    inner = box.inset(30)

    strip, rest = inner.split_top(34)
    c.setFillGray(SOFT_INK)
    c.setFont(FONT_BOLD, 16)
    c.drawCentredString(strip.cx, strip.y + 8, "ACTIVITY PACK")

    size, lines = fit_lines(pack.theme_title, FONT_BOLD, 54, inner.w, max_lines=3, one_line_min=30)
    strip, rest = rest.split_top(max(76, lines_height(len(lines), size) + 24))
    c.setFillGray(INK)
    draw_lines_centered(c, strip.cx, strip, lines, FONT_BOLD, size)

    strip, rest = rest.split_top(30)
    kinds = "  •  ".join(KIND_LABELS[k] for k in pack.kinds)
    size = fit_font_size(kinds, FONT, 15, inner.w)
    c.setFillGray(SOFT_INK)
    c.setFont(FONT, size)
    c.drawCentredString(strip.cx, strip.y + 8, kinds)

    bottom, rest = rest.split_bottom(64)
    c.setFillGray(INK)
    c.setFont(FONT_BOLD, 15)
    has_keys = any(p.kind in WITH_ANSWERS for p in pack.pages)
    pages = f"{len(pack.pages)} activity pages" + (" + answer keys" if has_keys else "")
    c.drawCentredString(bottom.cx, bottom.top - 22, pages)
    if pack.audience:
        line = f"For {pack.audience}"
        c.setFont(FONT, fit_font_size(line, FONT, 13, inner.w))
        c.drawCentredString(bottom.cx, bottom.top - 44, line)

    art = rest.inset(rest.w * 0.12, 24)
    shape = pack.cover
    tf = dot_to_dot.fit_transform(list(shape.outline) + [p for d in shape.details for p in d], art)
    set_line(c, style.lw(4.0), INK)
    polyline(c, [tf(p) for p in shape.outline], closed=shape.closed)
    set_line(c, style.lw(2.5), INK)
    for d in shape.details:
        polyline(c, [tf(p) for p in d])
    draw_footer(c, geo, style, None)
