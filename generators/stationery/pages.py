"""One layout per stationery page type and variant, plus the page header and footer.

Each layout draws into the area left under the header, sized from that area, so US Letter
and A4 each get their own row, line and column counts. All layouts are our own: zones,
tables and lines built from the pen in generators/stationery/layout.py, which enforces the
print rules. Pages are undated: dates are written in by hand.
"""
from __future__ import annotations

from dataclasses import dataclass

from generators.stationery.decor import draw_frame, draw_motif
from generators.stationery.layout import CAP_HEIGHT, INK, MM, SOFT, Box, Sheet, spread_gap

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
LETTERS = ("M", "T", "W", "T", "F", "S", "S")


@dataclass(frozen=True)
class RenderCtx:
    """Per-render options (from the stationery settings and the page size)."""

    page_size: str
    week_start: str = "monday"          # monday | sunday
    clock: str = "12h"
    day_start_hour: int = 7
    grid_pitch_pt: float = 6.35 * MM
    footer_text: str = ""
    accent_box_pt: float = 68.0

    def days(self, names=DAYS) -> list[str]:
        names = list(names)
        return names[6:] + names[:6] if self.week_start == "sunday" else names

    def hour(self, h: int) -> str:
        h %= 24
        if self.clock == "24h":
            return f"{h:02d}:00"
        return f"{(h - 1) % 12 + 1} {'am' if h < 12 else 'pm'}"


def week_start_for(setting: str, page_size: str) -> str:
    """"auto": US Letter weeks start on Sunday, A4 weeks on Monday."""
    if setting in ("monday", "sunday"):
        return setting
    return "sunday" if page_size == "us_letter" else "monday"


# --- header, footer ---------------------------------------------------------------------

def header(sh: Sheet, area: Box, spec: dict, rc: RenderCtx, accent: str | None = None) -> Box:
    """Title, subtitle, fill-in fields and the decoration slot. Returns the area below."""
    st = sh.style
    hdr, body = area.take_top(st.header_pt, gap=st.gap_pt)
    slot_w = min(rc.accent_box_pt, hdr.h)
    slot, text_area = hdr.take_right(slot_w, gap=10.0)
    title_y = text_area.top - st.title_pt * CAP_HEIGHT
    sh.text(text_area.x, title_y, spec.get("title") or "", size=st.title_pt, font=st.title_font,
            max_width=text_area.w)
    sub_y = title_y - st.subtitle_pt - 6
    if spec.get("subtitle"):
        sh.text(text_area.x, sub_y, spec["subtitle"], size=st.subtitle_pt, color=SOFT, max_width=text_area.w)
    # The fill-in line sits at least the line-gap rule above the first zone, so no two
    # horizontal lines across the page are closer than a writing line would be.
    field_y = max(hdr.y + 7, hdr.y - st.gap_pt + sh.rules.min_line_gap_pt + 3)
    x = text_area.x
    fields = list(spec.get("fields") or [])
    week = bool(spec.get("weekdays"))
    week_w = 7 * (st.label_pt * 1.9) if week else 0.0
    free = text_area.w - week_w - (12 if week else 0)
    widths = {"Date": 0.42, "Week of": 0.5, "Month": 0.45, "Year": 0.3, "Topic": 0.58}
    total = sum(widths.get(f, 0.4) for f in fields) or 1.0
    for f in fields:
        w = free * widths.get(f, 0.4) / max(total, 1.0) - 10
        sh.field(x, field_y, f, max(w, 40.0))
        x += max(w, 40.0) + 12
    if week:
        r = st.label_pt * 0.82
        step = st.label_pt * 1.9
        x0 = text_area.right - week_w + step / 2
        for i, letter in enumerate(rc.days(LETTERS)):
            cx = x0 + i * step
            sh.circle(cx, field_y + r * 0.35, r, kind="light")
            sh.text(cx, field_y, letter, size=st.label_pt * 0.9, font=st.label_font, color=SOFT, align="center")
    rule_y = hdr.y - st.gap_pt / 2
    sh.line(area.x, rule_y, area.x + min(64.0, area.w / 4), rule_y, kind="structural", color=st.accent)
    if accent:
        _accent_image(sh, slot, accent)
    else:
        draw_motif(sh, slot, spec.get("motif") or "geometric")
    return body


def _accent_image(sh: Sheet, slot: Box, path: str) -> None:
    from reportlab.lib.utils import ImageReader

    s = min(slot.w, slot.h)
    box = Box(slot.x + (slot.w - s) / 2, slot.y + (slot.h - s) / 2, s, s)
    sh._check(box, "accent image")
    sh.c.drawImage(ImageReader(path), box.x, box.y, box.w, box.h, preserveAspectRatio=True, anchor="c",
                   mask=[250, 255, 250, 255, 250, 255])


def footer(sh: Sheet, band: Box, rc: RenderCtx, spec: dict) -> None:
    if rc.footer_text:
        sh.text(band.cx, band.y + 3, rc.footer_text, size=sh.style.small_pt * 0.95, color=SOFT, align="center",
                max_width=band.w - 60)
    if sh.style.footer_motifs:
        s = min(band.h, 15.0)
        draw_motif(sh, Box(band.x, band.y, s, s), spec.get("motif") or "sprig")
        draw_motif(sh, Box(band.right - s, band.y, s, s), spec.get("motif") or "sprig")


def draw_page(sh: Sheet, page: dict, rc: RenderCtx, footer_h: float = 16.0) -> None:
    spec = page.get("spec") or {}
    band, content = sh.bounds.take_bottom(footer_h, gap=4.0)
    content = draw_frame(sh, content, sh.style.frame)
    accent = (page.get("asset_files") or {}).get("accent")
    area = header(sh, content, spec, rc, accent)
    layout = LAYOUTS.get((page.get("type"), spec.get("variant")))
    if layout is None:
        raise ValueError(f"no stationery layout for {page.get('type')!r} / {spec.get('variant')!r}")
    layout(sh, area, spec, rc)
    footer(sh, band, rc, spec)


# --- labels per style ---------------------------------------------------------------------

def _style_labels(spec: dict, table: dict) -> tuple:
    return table.get(spec.get("style"), table["default"])


BLOCK_NAMES = {"default": ("Focus block", "Quick tasks", "Out and about", "Wind down"),
               "wfh": ("Focused work", "Meetings and calls", "Admin and email", "Wrap up"),
               "student": ("Study block", "Classes", "Assignments", "Free time"),
               "teacher": ("Before class", "Teaching", "Prep and grading", "After class")}
DUMP_BOXES = {"default": ("Home", "Work or school", "People", "Errands", "Ideas", "Someday"),
              "student": ("Classes", "Assignments", "Exams", "Errands", "Ideas", "Someday"),
              "teacher": ("Lessons", "Grading", "Admin", "Families", "Ideas", "Someday"),
              "wfh": ("Projects", "Calls and email", "Admin", "Home", "Ideas", "Someday")}
ROUTINE_SECTIONS = {"default": ("Morning", "Daytime", "Evening"),
                    "student": ("Before school", "After school", "Evening"),
                    "teacher": ("Before school", "After school", "Evening"),
                    "wfh": ("Start of day", "Workday", "End of day")}
PERIOD_WORD = {"default": ("Block",), "teacher": ("Period",), "student": ("Class",)}


def _alt(spec: dict) -> int:
    return int(spec.get("alt") or 0)


# --- daily ------------------------------------------------------------------------------------

def daily_hourly(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    left, right = area.take_left(area.w * 0.56, gap=sh.gap)
    inner = sh.panel(left, "Schedule", tint=0)
    start = rc.day_start_hour - (_alt(spec) % 2)
    n = max(1, min(16, int(inner.h // sh.rules.min_line_gap_pt)))
    labels = [rc.hour(start + i) for i in range(n)]
    sh.labeled_rows(inner, labels, row_h=inner.h / n, label_w=34.0, bar_w=9.0 if sh.style.time_bars else 0.0)
    top3, rest = right.take_top(sh.panel_h(3), gap=sh.gap)
    todo, notes = rest.rows(2, sh.gap, [3, 2])
    sh.checklist(sh.panel(top3, "Top 3", tint=1), max_rows=3, numbered=True)
    sh.checklist(sh.panel(todo, "To do", tint=2))
    sh.writing_lines(sh.panel(notes, "Notes", tint=3))


def daily_zones(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    left, right = area.take_left(area.w * 0.64, gap=sh.gap)
    focus, zones = left.take_top(sh.panel_h(1, sh.line_gap), gap=sh.gap)
    sh.writing_lines(sh.panel(focus, "Main focus today", tint=0), max_lines=1)
    for i, (label, box) in enumerate(zip(("Morning", "Afternoon", "Evening"), zones.rows(3, sh.gap))):
        sh.checklist(sh.panel(box, label, tint=i + 1))
    energy, rest = right.take_bottom(sh.panel_h(1, sh.row_h), gap=sh.gap)
    remember, wins = rest.rows(2, sh.gap, [3, 2])
    sh.checklist(sh.panel(remember, "Don't forget", tint=4))
    sh.writing_lines(sh.panel(wins, "Wins today", tint=0))
    inner = sh.panel(energy, "Energy today", tint=1)
    sh.segments(Box(inner.x, inner.y + 2, inner.w, max(inner.h - 4, 6.0)), 5)


def daily_simple(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    main, rest = area.take_top(sh.panel_h(2, sh.line_gap), gap=sh.gap)
    sh.writing_lines(sh.panel(main, "Main thing today", tint=0), max_lines=2)
    notes, top = rest.take_bottom(rest.h * 0.3, gap=sh.gap)
    todo, sched = top.cols(2, sh.gap)
    sh.checklist(sh.panel(todo, "To do", tint=1))
    inner = sh.panel(sched, "Schedule", tint=2)
    sh.labeled_rows(inner, [None] * 20, label_w=40.0)
    sh.writing_lines(sh.panel(notes, "Notes", tint=3))


# --- weekly -----------------------------------------------------------------------------------

def weekly_columns(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    cells = area.grid(2, 4, sh.gap)
    for i, day in enumerate(rc.days()):
        sh.checklist(sh.panel(cells[i], day, tint=i))
    sh.writing_lines(sh.panel(cells[7], "Focus this week", tint=7))


def weekly_rows(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    left, right = area.take_left(area.w * 0.66, gap=sh.gap)
    for i, (day, box) in enumerate(zip(rc.days(), left.rows(7, sh.gap * 0.6))):
        sh.writing_lines(sh.panel(box, day, tint=i), max_lines=0)
    goals, todo, notes = right.rows(3, sh.gap, [1, 1.4, 1])
    sh.checklist(sh.panel(goals, "Goals", tint=0))
    sh.checklist(sh.panel(todo, "To do", tint=1))
    sh.writing_lines(sh.panel(notes, "Notes", tint=2))


def weekly_periods(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    word = _style_labels(spec, PERIOD_WORD)[0]
    day_w = 22 * MM
    n = max(4, min(6, int((area.w - day_w) // (28 * MM))))
    days = [d for d in rc.days(SHORT) if d not in ("Sat", "Sun")] if spec.get("style") in ("teacher", "student") \
        else rc.days(SHORT)
    bottom, top = area.take_bottom(area.h * 0.22, gap=sh.gap)
    sh.table(top, [("", day_w)] + [(f"{word} {i + 1}", (area.w - day_w) / n) for i in range(n)], rows=len(days),
             row_labels=days)
    prep, notes = bottom.cols(2, sh.gap)
    sh.checklist(sh.panel(prep, "To prepare", tint=1))
    sh.writing_lines(sh.panel(notes, "Notes", tint=2))


# --- monthly ----------------------------------------------------------------------------------

def monthly_calendar(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    bottom, cal = area.take_bottom(area.h * 0.24, gap=sh.gap)
    cells = sh.table(cal, [(d, 1.0) for d in rc.days(SHORT)], rows=6)
    s = min(13.0, max(sh.rules.min_font_pt + 3, cells[0][0].h * 0.2))
    for row in cells:
        for cell in row:
            sh.rect(Box(cell.x + 3, cell.top - 3 - s, s, s), kind="light", radius=1.5)
    goals, notes = bottom.cols(2, sh.gap)
    sh.checklist(sh.panel(goals, "Goals", tint=1))
    sh.writing_lines(sh.panel(notes, "Notes", tint=2))


def _fit_rows(sh: Sheet, box: Box, wanted: int, header_h: float | None = None) -> int:
    header_h = header_h if header_h is not None else sh.band_h
    return max(1, min(wanted, int((box.h - header_h) // sh.rules.min_line_gap_pt)))


def monthly_list(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    left, right = area.take_left(area.w * 0.62, gap=sh.gap)
    n = _fit_rows(sh, left, 31)
    cells = sh.table(left, [("Day", 0.6), ("Plan", 4.0), ("Done", 0.75)], rows=n,
                     row_labels=[str(i + 1) for i in range(n)])
    for row in cells:
        sh.cell_checkbox(row[2])
    goals, dates, notes = right.rows(3, sh.gap, [1.2, 1, 1])
    sh.checklist(sh.panel(goals, "Goals", tint=1))
    sh.writing_lines(sh.panel(dates, "Key dates", tint=2), max_lines=0)
    sh.writing_lines(sh.panel(notes, "Notes", tint=3), max_lines=0)


# --- time blocks -------------------------------------------------------------------------------

def time_half_hour(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    left, right = area.take_left(area.w * 0.66, gap=sh.gap)
    row = max(sh.rules.min_line_gap_pt, 7.0 * MM)
    n = max(2, min(30, int((left.h - sh.band_h) // row)))
    start = rc.day_start_hour - (_alt(spec) % 2)
    labels = [rc.hour(start + i // 2) if i % 2 == 0 else ":30" for i in range(n)]
    cells = sh.table(left, [("Time", 0.95), ("Bar", 0.4), ("Plan", 4.0), ("Done", 0.7)], rows=n)
    for label, row_cells in zip(labels, cells):
        t, bar, _, done = row_cells
        sh.small(t.x + 4, t.y + (t.h - sh.style.small_pt * CAP_HEIGHT) / 2, label, max_width=t.w - 6)
        sh.rect(bar.inset(3, 2.5), kind="medium", radius=1.5, color=SOFT)
        sh.cell_checkbox(done)
    top3, rest = right.take_top(sh.panel_h(3), gap=sh.gap)
    breaks, notes = rest.rows(2, sh.gap)
    sh.checklist(sh.panel(top3, "Top 3", tint=1), max_rows=3, numbered=True)
    sh.writing_lines(sh.panel(breaks, "Breaks", tint=2))
    sh.writing_lines(sh.panel(notes, "Notes", tint=3))


def time_blocks(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    left, right = area.take_left(area.w * 0.66, gap=sh.gap)
    names = _style_labels(spec, BLOCK_NAMES)
    for i, (name, box) in enumerate(zip(names, left.rows(4, sh.gap))):
        inner = sh.panel(box, name, tint=i)
        strip, rest = inner.take_top(sh.row_h, gap=4.0)
        fw = strip.w * 0.25
        sh.field(strip.x, strip.y + 4, "From", fw)
        sh.field(strip.x + fw + 8, strip.y + 4, "to", fw * 0.8)
        bar = Box(strip.x + fw * 1.8 + 22, strip.y + 2, strip.right - (strip.x + fw * 1.8 + 22), strip.h - 4)
        if bar.w > 30:
            sh.segments(bar, 8, tint=i if sh.style.time_bars else None)
        sh.checklist(rest)
    focus, step = right.rows(2, sh.gap)
    sh.writing_lines(sh.panel(focus, "Today's focus", tint=0))
    sh.writing_lines(sh.panel(step, "Next tiny step", tint=1))


# --- brain dump, tasks, priorities ---------------------------------------------------------------

def brain_open(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    bottom, top = area.take_bottom(area.h * 0.33, gap=sh.gap)
    sh.dot_grid(sh.panel(top, "Everything on my mind", tint=0), rc.grid_pitch_pt)
    for i, (label, box) in enumerate(zip(("Do soon", "Later", "Let it go"), bottom.cols(3, sh.gap))):
        sh.checklist(sh.panel(box, label, tint=i + 1))


def brain_sorted(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    first, rest = area.take_bottom(sh.panel_h(1, sh.line_gap), gap=sh.gap)
    for i, (label, box) in enumerate(zip(_style_labels(spec, DUMP_BOXES), rest.grid(2, 3, sh.gap))):
        sh.writing_lines(sh.panel(box, label, tint=i))
    sh.writing_lines(sh.panel(first, "First thing to do", tint=0), max_lines=1)


def task_steps(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    task, rest = area.take_top(sh.panel_h(2, sh.line_gap), gap=sh.gap)
    sh.writing_lines(sh.panel(task, "The big task", tint=0), max_lines=2)
    meta, rest = rest.take_top(sh.row_h, gap=sh.gap * 0.6)
    w = meta.w / 3
    sh.field(meta.x, meta.y + 5, "Due", w - 12)
    sh.field(meta.x + w, meta.y + 5, "Time needed", w - 12)
    sh.label(meta.x + 2 * w, meta.y + 5, "Done")
    sh.checkbox(meta.x + 2 * w + sh.text_width("Done", sh.style.label_pt, sh.style.label_font) + 6, meta.y + 3)
    reward, rest = rest.take_bottom(sh.panel_h(1, sh.line_gap), gap=sh.gap)
    cells = sh.table(rest, [("Step", 0.5), ("What to do", 4.2), ("Time", 1.0), ("Done", 0.75)],
                     max_rows=10 if sh.style.big_boxes else 14)
    for i, row in enumerate(cells):
        if i == 0:
            sh.rect(row[0].inset(1.5), stroke=False, fill=sh.tint(1), radius=0)
        sh.label(row[0].cx, row[0].y + (row[0].h - sh.style.label_pt * CAP_HEIGHT) / 2, str(i + 1), align="center")
        sh.cell_checkbox(row[3])
    sh.writing_lines(sh.panel(reward, "Reward when done", tint=2), max_lines=1)


def task_two(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    for i, box in enumerate(area.rows(2, sh.gap)):
        inner = sh.panel(box, f"Task {i + 1}", tint=i)
        name, rest = inner.take_top(sh.line_gap + 2, gap=4.0)
        sh.writing_lines(name, max_lines=1)
        meta, rest = rest.take_top(sh.row_h, gap=4.0)
        sh.field(meta.x, meta.y + 4, "Done by", meta.w * 0.45)
        sh.checklist(rest, numbered=True)


def top3_three(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    extra, top = area.take_bottom(area.h * 0.26, gap=sh.gap)
    for i, box in enumerate(top.rows(3, sh.gap)):
        inner = sh.panel(box, f"Priority {i + 1}", tint=i)
        step, lines = inner.take_bottom(sh.row_h, gap=4.0)
        sh.writing_lines(lines, max_lines=2)
        sh.field(step.x, step.y + 4, "First step", step.w * 0.72)
        lw = sh.text_width("Done", sh.style.label_pt, sh.style.label_font)
        sh.label(step.right - lw - sh.style.checkbox_pt - 6, step.y + 4, "Done")
        sh.checkbox(step.right - sh.style.checkbox_pt, step.y + 2)
    inner = sh.panel(extra, "If there is time", tint=3)
    for col in inner.cols(2, sh.gap):
        sh.checklist(col)


def top3_week(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    cells = sh.table(area, [("Day", 0.8), ("Priority 1", 2.0), ("Priority 2", 2.0), ("Priority 3", 2.0)], rows=7,
                     row_labels=rc.days(SHORT))
    size = sh.style.checkbox_pt
    for row in cells:
        for cell in row[1:]:
            sh.checkbox(cell.x + 4, cell.top - 4 - size, size)
            sh.writing_lines(Box(cell.x + size + 10, cell.y + 3, cell.w - size - 14, cell.h - 6), max_lines=2)


def _matrix(sh: Sheet, box: Box) -> None:
    lab = sh.style.label_pt + 6
    top_strip, rest = box.take_top(lab, gap=3.0)
    left_strip, grid_box = rest.take_left(lab, gap=3.0)
    for text, col in zip(("Urgent", "Not urgent"), grid_box.cols(2, sh.gap)):
        sh.label(col.cx, top_strip.y + 3, text, align="center")
    for text, row in zip(("Important", "Not important"), grid_box.rows(2, sh.gap)):
        sh.vtext(left_strip.cx, row.cy, text, size=sh.style.label_pt)
    names = ("Do first", "Plan a time", "Quick or hand off", "Drop or do later")
    for i, (name, q) in enumerate(zip(names, grid_box.grid(2, 2, sh.gap))):
        sh.checklist(sh.panel(q, name, tint=i))


def matrix(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    _matrix(sh, area)


def matrix_picks(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    picks, top = area.take_bottom(area.h * 0.28, gap=sh.gap)
    _matrix(sh, top)
    p, n = picks.cols(2, sh.gap)
    sh.checklist(sh.panel(p, "Today's picks", tint=4), max_rows=3, numbered=True)
    sh.writing_lines(sh.panel(n, "Notes", tint=1))


# --- trackers ------------------------------------------------------------------------------------

def habit_monthly(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    day_w = 12 * MM
    n = max(5, min(10, int((area.w - day_w) // (18 * MM))))
    header_h = max(2 * sh.rules.min_line_gap_pt, 30.0)
    rows = _fit_rows(sh, area, 31, header_h)
    sh.table(area, [("Day", day_w)] + [("", (area.w - day_w) / n)] * n, rows=rows, header_h=header_h,
             row_labels=[str(i + 1) for i in range(rows)])


def habit_weekly(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    note, top = area.take_bottom(sh.panel_h(2, sh.line_gap), gap=sh.gap)
    row = max(sh.row_h, 10 * MM if sh.style.big_boxes else 8 * MM)
    cells = sh.table(top, [("Habit", 3.0)] + [(d, 1.0) for d in rc.days(SHORT)], row_h=row, max_rows=12)
    for r in cells:
        for cell in r[1:]:
            sh.circle(cell.cx, cell.cy, min(cell.w, cell.h) * 0.3)
    sh.writing_lines(sh.panel(note, "Small win this week", tint=1), max_lines=2)


ROUTINE_MAX_STEPS = 12          # a routine is a short list, not a full page of rows


def routine_two(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    names = _style_labels(spec, ROUTINE_SECTIONS)
    steps = sh.style.list_rows + 2 if sh.style.list_rows else ROUTINE_MAX_STEPS
    base = sh.list_gap
    head_h = sh.style.small_pt + 6
    # The routines take the room their steps need; the notes box below gets the rest.
    want = sh.band_h + head_h + 2.0 + steps * max(base, 9.5 * MM) + 2 * sh.style.pad_pt + 2.0
    top, notes = area.take_top(min(want, area.h - sh.panel_h(2, sh.line_gap) - sh.gap), gap=sh.gap)
    for i, (label, box) in enumerate(zip((f"{names[0]} routine", f"{names[2]} routine"), top.cols(2, sh.gap))):
        inner = sh.panel(box, label, tint=i)
        head, body = inner.take_top(head_h, gap=2.0)
        n = max(1, min(steps, int(body.h // base)))
        gap = spread_gap(base, body.h, n + 0.15)
        cell = min(gap - 6, inner.w * 0.5 / 7, 17.0)
        days_x = inner.right - 7 * cell
        for k, letter in enumerate(rc.days(LETTERS)):
            sh.small(days_x + k * cell + cell / 2, head.y + 2, letter, align="center")
        if n > 1:
            sh.m.line_gaps.append(gap)
        for r in range(n):
            y = body.top - (r + 1) * gap + 3
            sh.line(body.x, y, days_x - 6, y)
            for k in range(7):
                sh.rect(Box(days_x + k * cell + 1, y, cell - 2, cell - 2), kind="medium", radius=1.5, color=INK)
    sh.writing_lines(sh.panel(notes, "Notes", tint=2), max_lines=0)


def routine_grid(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    sections = _style_labels(spec, ROUTINE_SECTIONS)
    cells = sh.table(area, [("Step", 3.0)] + [(d, 1.0) for d in rc.days(SHORT)],
                     row_h=max(sh.row_h, 8 * MM if sh.style.big_boxes else 7 * MM))
    n = len(cells)
    per = max(2, n // len(sections))
    starts = {i * per: name for i, name in enumerate(sections) if i * per < n}
    for i, row in enumerate(cells):
        if i in starts:
            color = sh.tint(list(starts).index(i))
            for cell in row:
                sh.rect(cell.inset(1.2), stroke=False, fill=color, radius=0)
            sh.label(row[0].x + 4, row[0].y + (row[0].h - sh.style.label_pt * CAP_HEIGHT) / 2, starts[i],
                     max_width=row[0].w - 8)
        else:
            for cell in row[1:]:
                sh.cell_checkbox(cell)


def focus_sessions(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    bottom, top = area.take_bottom(area.h * 0.27, gap=sh.gap)
    cells = sh.table(top, [("Round", 0.6), ("Task", 3.0), ("5-minute segments", 2.6), ("Break", 0.75),
                           ("Done", 0.75)], row_h=max(sh.row_h, 9 * MM), max_rows=9 if sh.style.big_boxes else 12)
    for i, row in enumerate(cells):
        sh.label(row[0].cx, row[0].y + (row[0].h - sh.style.label_pt * CAP_HEIGHT) / 2, str(i + 1), align="center")
        seg_h = min(row[2].h - 8, 14.0)
        sh.segments(Box(row[2].x + 5, row[2].cy - seg_h / 2, row[2].w - 10, seg_h), 6,
                    tint=None)
        sh.cell_checkbox(row[3])
        sh.cell_checkbox(row[4])
    ideas, parking = bottom.cols(2, sh.gap)
    sh.writing_lines(sh.panel(ideas, "Break ideas", tint=1))
    sh.writing_lines(sh.panel(parking, "Parking lot for stray thoughts", tint=2))


def focus_tally(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    focus, rest = area.take_top(sh.panel_h(1, sh.line_gap), gap=sh.gap)
    sh.writing_lines(sh.panel(focus, "Today I am focusing on", tint=0), max_lines=1)
    bottom, mid = rest.take_bottom(rest.h * 0.3, gap=sh.gap)
    cells = sh.table(mid, [("Task", 3.0), ("Rounds finished", 3.6), ("Total", 0.8)], row_h=max(sh.row_h, 9 * MM),
                     max_rows=12)
    for row in cells:
        sh.scale(row[1].inset(6, 2), 8)
    length, away = bottom.cols(2, sh.gap, [1, 1.4])
    inner = sh.panel(length, "Round length", tint=1)
    sh.fields_column(inner, ["Focus (min)", "Break (min)"])
    sh.writing_lines(sh.panel(away, "What pulled me away", tint=2))


def mood_monthly(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    n = _fit_rows(sh, area, 31)
    cells = sh.table(area, [("Day", 0.6), ("Mood 1-5", 2.0), ("Energy 1-5", 2.0), ("Note", 4.0)], rows=n,
                     row_labels=[str(i + 1) for i in range(n)])
    for row in cells:
        sh.scale(row[1].inset(4, 1), 5)
        h = min(row[2].h - 6, 10.0)
        sh.segments(Box(row[2].x + 6, row[2].cy - h / 2, row[2].w - 12, h), 5)


def mood_weekly(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    cells = area.grid(2, 4, sh.gap)
    for i, day in enumerate(rc.days()):
        inner = sh.panel(cells[i], day, tint=i)
        faces, rest = inner.take_top(min(inner.h * 0.38, 40.0), gap=4.0)
        r = min(faces.h * 0.42, faces.w / 12)
        step = faces.w / 5
        for k in range(5):
            sh.face(faces.x + step * (k + 0.5), faces.cy, r, k)
        energy, note = rest.take_top(min(16.0, rest.h / 2), gap=4.0)
        lw = sh.small(energy.x, energy.y + 3, "Energy")
        sh.segments(Box(energy.x + lw + 8, energy.y + 1, energy.w - lw - 8, energy.h - 2), 5)
        sh.writing_lines(note, max_lines=2)
    sh.writing_lines(sh.panel(cells[7], "Patterns I notice", tint=7))


# --- lists, meals, money, books, gratitude --------------------------------------------------------

def todo_checklist(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    must, rest = area.take_top(sh.panel_h(3), gap=sh.gap)
    sh.checklist(sh.panel(must, "Must do today", tint=0), max_rows=3)
    for col in rest.cols(2, sh.gap):
        sh.checklist(sh.panel(col, None), max_rows=0 if not sh.style.list_rows else sh.style.list_rows + 6)


def todo_sorted(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    left, right = area.take_left(area.w * 0.64, gap=sh.gap)
    for i, (label, box) in enumerate(zip(("Must do", "Should do", "Could do"), left.rows(3, sh.gap))):
        sh.checklist(sh.panel(box, label, tint=i))
    calls, errands = right.rows(2, sh.gap)
    sh.checklist(sh.panel(calls, "Calls and messages", tint=3))
    sh.checklist(sh.panel(errands, "Errands", tint=4))


def meal_weekly(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    groc, top = area.take_bottom(area.h * 0.3, gap=sh.gap)
    sh.table(top, [("", 0.8), ("Breakfast", 2.0), ("Lunch", 2.0), ("Dinner", 2.0), ("Snacks", 1.5)], rows=7,
             row_labels=rc.days(SHORT))
    inner = sh.panel(groc, "Shopping list", tint=1)
    for col in inner.cols(3, sh.gap):
        sh.checklist(col)


def meal_groceries(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    left, right = area.take_left(area.w * 0.48, gap=sh.gap)
    cells = sh.table(left, [("", 0.9), ("Meals", 4.0)], rows=7, row_labels=rc.days(SHORT))
    for row in cells:
        cell = row[1]
        sh.writing_lines(Box(cell.x + 4, cell.y + 2, cell.w - 8, cell.h - 4), max_lines=3)
    for i, (label, box) in enumerate(zip(("Produce", "Pantry", "Fridge and freezer", "Other"),
                                         right.rows(4, sh.gap))):
        sh.checklist(sh.panel(box, label, tint=i))


def budget_monthly(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    top, rest = area.take_top(area.h * 0.27, gap=sh.gap)
    income, summary = top.cols(2, sh.gap)
    sh.table(income, [("Income", 3.0), ("Amount", 1.5)], max_rows=5)
    inner = sh.panel(summary, "Summary", tint=1)
    sh.fields_column(inner, ["Income", "Bills", "Spending", "Left over"])
    bills, spend = rest.rows(2, sh.gap, [1.0, 1.2])
    cells = sh.table(bills, [("Bill", 3.0), ("Due", 1.2), ("Amount", 1.4), ("Paid", 0.8)], header_tint=2)
    for row in cells:
        sh.cell_checkbox(row[3])
    sh.table(spend, [("Date", 1.1), ("Spent on", 3.5), ("Amount", 1.4)], header_tint=3)


def budget_expenses(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    summary, top = area.take_bottom(sh.row_h * 1.5, gap=sh.gap)
    sh.table(top, [("Date", 1.1), ("Description", 3.4), ("Category", 1.6), ("Amount", 1.3)])
    for label, col in zip(("Total", "Budget", "Difference"), summary.cols(3, sh.gap)):
        sh.field(col.x, col.y + 6, label, col.w)


def reading_list(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    cells = sh.table(area, [("Title", 3.2), ("Author", 2.2), ("Started", 1.1), ("Finished", 1.1), ("Rating", 1.6)],
                     row_h=max(sh.row_h, 8 * MM))
    for row in cells:
        sh.stars(row[4].inset(4, 2), 5)


def book_notes(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    info, rest = area.take_top(sh.panel_h(3, sh.row_h + 2), gap=sh.gap)
    inner = sh.panel(info, "About the book", tint=0)
    left, right = inner.cols(2, sh.gap, [1.6, 1])
    sh.fields_column(left, ["Title", "Author", "Genre"])
    rows = right.rows(3)
    sh.field(rows[0].x, rows[0].y + 4, "Started", rows[0].w)
    sh.field(rows[1].x, rows[1].y + 4, "Finished", rows[1].w)
    lw = sh.label(rows[2].x, rows[2].y + 4, "Rating")
    sh.stars(Box(rows[2].x + lw + 6, rows[2].y, rows[2].w - lw - 6, rows[2].h), 5)
    rec, rest = rest.take_bottom(sh.panel_h(1), gap=sh.gap)
    fav, thoughts = rest.rows(2, sh.gap, [1, 1.4])
    sh.writing_lines(sh.panel(fav, "Favorite lines", tint=1), max_lines=0)
    sh.writing_lines(sh.panel(thoughts, "My thoughts", tint=2), max_lines=0)
    inner = sh.panel(rec, "Would I recommend it?", tint=3)
    for label, col in zip(("Yes", "Maybe", "No"), inner.cols(3)):
        size = sh.checkbox(col.x, col.y + 2)
        sh.label(col.x + size + 6, col.y + 4, label)


def gratitude_week(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    cells = area.grid(2, 4, sh.gap)
    for i, day in enumerate(rc.days()):
        sh.writing_lines(sh.panel(cells[i], day, tint=i), max_lines=3, numbered=True)
    sh.writing_lines(sh.panel(cells[7], "Best moment of the week", tint=7))


def gratitude_prompts(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    boxes = area.rows(5, sh.gap, [1.5, 1, 1, 1, 0.7])
    sh.writing_lines(sh.panel(boxes[0], "Three good things today", tint=0), max_lines=3, numbered=True)
    for i, label in enumerate(("Someone I appreciate", "A small win", "Something I am looking forward to"), 1):
        sh.writing_lines(sh.panel(boxes[i], label, tint=i))
    inner = sh.panel(boxes[4], "Today felt", tint=4)
    for word, col in zip(("calm", "busy", "happy", "tired", "proud"), inner.cols(5)):
        r = min(6.0, col.h / 2.5)
        sh.circle(col.x + r + 1, col.cy, r)
        sh.label(col.x + 2 * r + 6, col.cy - sh.style.label_pt * CAP_HEIGHT / 2, word, max_width=col.w - 2 * r - 8)


# --- notes ---------------------------------------------------------------------------------------

def notes_lined(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    margin_x = area.x + 16 * MM
    sh.line(margin_x, area.y, margin_x, area.top, kind="light", color=SOFT)
    sh.writing_lines(area, max_lines=0, first_offset=sh.line_gap * 0.8)


def notes_cue(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    summary, top = area.take_bottom(area.h * 0.2, gap=sh.gap)
    cue, notes = top.take_left(top.w * 0.3, gap=sh.gap)
    sh.writing_lines(sh.panel(cue, "Key points", tint=0), max_lines=0)
    sh.writing_lines(sh.panel(notes, "Notes", tint=1), max_lines=0)
    sh.writing_lines(sh.panel(summary, "Summary", tint=2), max_lines=0)


def notes_dots(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    sh.dot_grid(area, rc.grid_pitch_pt)


def notes_dots_framed(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    sh.dot_grid(sh.panel(area, "Ideas", tint=0), rc.grid_pitch_pt)


def notes_grid(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    sh.square_grid(area, rc.grid_pitch_pt)


def notes_grid_framed(sh: Sheet, area: Box, spec: dict, rc: RenderCtx) -> None:
    lines, top = area.take_bottom(area.h * 0.24, gap=sh.gap)
    sh.square_grid(sh.panel(top, "Plan", tint=0), rc.grid_pitch_pt)
    sh.writing_lines(sh.panel(lines, "Notes", tint=1), max_lines=0)


LAYOUTS = {
    ("daily_planner", "hourly"): daily_hourly,
    ("daily_planner", "zones"): daily_zones,
    ("daily_planner", "simple"): daily_simple,
    ("weekly_planner", "columns"): weekly_columns,
    ("weekly_planner", "rows"): weekly_rows,
    ("weekly_planner", "periods"): weekly_periods,
    ("monthly_planner", "calendar"): monthly_calendar,
    ("monthly_planner", "list"): monthly_list,
    ("time_blocked_day", "half_hour"): time_half_hour,
    ("time_blocked_day", "blocks"): time_blocks,
    ("brain_dump", "open"): brain_open,
    ("brain_dump", "sorted"): brain_sorted,
    ("task_breakdown", "steps"): task_steps,
    ("task_breakdown", "two_tasks"): task_two,
    ("top3_priorities", "three"): top3_three,
    ("top3_priorities", "week"): top3_week,
    ("priority_matrix", "matrix"): matrix,
    ("priority_matrix", "matrix_picks"): matrix_picks,
    ("habit_tracker", "monthly"): habit_monthly,
    ("habit_tracker", "weekly"): habit_weekly,
    ("routine_checklist", "morning_evening"): routine_two,
    ("routine_checklist", "weekly_grid"): routine_grid,
    ("focus_sessions", "sessions"): focus_sessions,
    ("focus_sessions", "tally"): focus_tally,
    ("mood_energy", "monthly"): mood_monthly,
    ("mood_energy", "weekly"): mood_weekly,
    ("todo_list", "checklist"): todo_checklist,
    ("todo_list", "sorted"): todo_sorted,
    ("meal_planner", "weekly"): meal_weekly,
    ("meal_planner", "groceries"): meal_groceries,
    ("budget_tracker", "monthly"): budget_monthly,
    ("budget_tracker", "expenses"): budget_expenses,
    ("reading_log", "list"): reading_list,
    ("reading_log", "book_notes"): book_notes,
    ("gratitude", "daily"): gratitude_week,
    ("gratitude", "prompts"): gratitude_prompts,
    ("notes_lined", "lined"): notes_lined,
    ("notes_lined", "cue_column"): notes_cue,
    ("notes_dot", "dots"): notes_dots,
    ("notes_dot", "framed"): notes_dots_framed,
    ("notes_grid", "grid"): notes_grid,
    ("notes_grid", "framed"): notes_grid_framed,
}
