"""The stationery page catalog: page types, their layout variants and our own wording.

Every title, subtitle and label here is written for this shop. No brand, planner system or
trademark names (planner brands are review terms in config/trademark_blocklist.txt; the
timed-interval method is called "focus sessions" here, never by a trademarked name), and no
medical or therapeutic wording: ADHD-friendly pages describe the layout only (big boxes,
clear zones), never what it does for anyone's health. Pages are undated (fill-in dates),
so a pack never goes stale.
"""
from __future__ import annotations

from dataclasses import dataclass

PAGE_TYPES = (
    "daily_planner", "weekly_planner", "monthly_planner", "time_blocked_day", "brain_dump", "task_breakdown",
    "top3_priorities", "priority_matrix", "habit_tracker", "routine_checklist", "focus_sessions", "mood_energy",
    "todo_list", "meal_planner", "budget_tracker", "reading_log", "gratitude", "notes_lined", "notes_dot",
    "notes_grid",
)

# The order pages appear in a pack: planning from the month down to the day, then lists,
# trackers and notes.
PACK_ORDER = (
    "monthly_planner", "weekly_planner", "daily_planner", "time_blocked_day", "top3_priorities", "priority_matrix",
    "todo_list", "brain_dump", "task_breakdown", "focus_sessions", "routine_checklist", "habit_tracker",
    "mood_energy", "meal_planner", "budget_tracker", "reading_log", "gratitude", "notes_lined", "notes_dot",
    "notes_grid",
)


@dataclass(frozen=True)
class Variant:
    name: str
    subject: str                       # what the page is, in a few words (the page's "subject")
    titles: tuple[str, ...]
    subtitles: tuple[str, ...]
    fields: tuple[str, ...] = ("Date",)
    weekdays: bool = False             # a circle-the-day row in the header


V = Variant
CATALOG: dict[str, tuple[Variant, ...]] = {
    "daily_planner": (
        V("hourly", "daily planner with an hourly schedule", ("Daily Plan", "Plan for Today"),
          ("Map out the hours, then work through them one at a time.",
           "A clear schedule on the left, your priorities on the right."), weekdays=True),
        V("zones", "daily planner in morning, afternoon and evening zones", ("My Day", "Today in Three Parts"),
          ("Morning, afternoon and evening, each with its own box.",
           "Split the day into three parts and give each one a few tasks."), weekdays=True),
        V("simple", "simple daily page", ("Today", "Daily Page"),
          ("One main thing, a short list and room for notes.",
           "Keep today simple: what matters most, what comes next."), weekdays=True),
    ),
    "weekly_planner": (
        V("columns", "weekly planner with a box per day", ("Weekly Plan", "The Week Ahead"),
          ("Seven days at a glance, plus a box for this week's focus.",
           "Give each day a few tasks and keep the week's goals in view."), fields=("Week of",)),
        V("rows", "weekly overview in rows", ("This Week", "Weekly Overview"),
          ("One row per day, with goals and to-dos on the side.",
           "See the whole week in rows and keep your list close."), fields=("Week of",)),
        V("periods", "weekly schedule in blocks", ("Weekly Schedule", "Week by Blocks"),
          ("Plan each day in blocks across the week.", "A grid for the regular blocks of your week."),
          fields=("Week of",)),
    ),
    "monthly_planner": (
        V("calendar", "undated monthly calendar", ("Monthly Plan", "Month at a Glance"),
          ("Write in the dates and plan the month ahead.", "An undated calendar with room for goals and notes."),
          fields=("Month", "Year")),
        V("list", "month in a day-by-day list", ("Month List", "Monthly Overview"),
          ("One line for every day of the month.", "A day-by-day list with space for goals and key dates."),
          fields=("Month", "Year")),
    ),
    "time_blocked_day": (
        V("half_hour", "time-blocked day in half-hour blocks", ("Time Blocks", "Time-Blocked Day"),
          ("Give every half hour one job and shade the bar as you go.",
           "Plan in 30-minute blocks and keep breaks on the schedule."), weekdays=True),
        V("blocks", "day planned in a few big blocks", ("Block Schedule", "Day in Blocks"),
          ("A few big blocks instead of a crowded hourly plan.",
           "Group tasks into blocks and shade the bar as time passes."), weekdays=True),
    ),
    "brain_dump": (
        V("open", "open brain dump page with a sorting area", ("Brain Dump", "Clear Your Head"),
          ("Write down everything on your mind, then sort it below.",
           "Get it all out of your head first. Sort it after.")),
        V("sorted", "brain dump sorted into topic boxes", ("Sorted Brain Dump", "Brain Dump by Topic"),
          ("Drop each thought into the box where it belongs.", "Six boxes to catch every loose thought.")),
    ),
    "task_breakdown": (
        V("steps", "one big task broken into small steps", ("Task Breakdown", "Break It Down"),
          ("Turn one big task into small, doable steps.", "Start with the tiniest first step and tick off the rest.")),
        V("two_tasks", "two tasks broken into steps", ("Two Task Breakdown", "Small Steps"),
          ("Two big tasks, each split into small steps.", "Small steps make big tasks easier to start.")),
    ),
    "top3_priorities": (
        V("three", "top three priorities for the day", ("Top 3 Priorities", "Three Things Today"),
          ("Pick the three things that matter most today.", "If only three things get done, make it these."),
          weekdays=True),
        V("week", "top three priorities for each day of the week", ("Top 3 Every Day", "Weekly Top 3"),
          ("Three priorities for each day of the week.", "Choose three, do three, tick three."),
          fields=("Week of",)),
    ),
    "priority_matrix": (
        V("matrix", "urgent and important priority matrix", ("Priority Matrix", "Urgent or Important?"),
          ("Sort tasks by how urgent and how important they are.", "Four boxes to decide what to do first.")),
        V("matrix_picks", "priority matrix with today's picks", ("Priority Sort", "What Comes First"),
          ("Sort your list, then pick what to do today.", "Sort tasks into four boxes and choose today's picks.")),
    ),
    "habit_tracker": (
        V("monthly", "monthly habit tracker", ("Habit Tracker", "Monthly Habits"),
          ("Write your habits across the top and tick each day.", "One row per day, one column per habit."),
          fields=("Month",)),
        V("weekly", "weekly habit tracker with big boxes", ("Weekly Habits", "Habit Check"),
          ("Big circles for a week of small habits.", "Fill in a circle each day you show up for a habit."),
          fields=("Week of",)),
    ),
    "routine_checklist": (
        V("morning_evening", "morning and evening routine checklist", ("Daily Routines", "Morning and Evening"),
          ("List your routine steps and tick them off each day.", "Two short routines to bookend the day."),
          fields=("Week of",)),
        V("weekly_grid", "routine checklist for the week", ("Routine Checklist", "Weekly Routine"),
          ("Your routine steps down the side, the days across the top.",
           "Tick each step as you go through the week."), fields=("Week of",)),
    ),
    "focus_sessions": (
        V("sessions", "focus session planner with time bars", ("Focus Sessions", "Focus Planner"),
          ("Work in short rounds and shade a segment for every 5 minutes.",
           "Plan each round, take your break, tick it off.")),
        V("tally", "focus round tracker per task", ("Focus Rounds", "Session Tracker"),
          ("Fill a circle for each focus round you finish.",
           "Track focus rounds per task and note what pulled you away.")),
    ),
    "mood_energy": (
        V("monthly", "monthly mood and energy log", ("Mood and Energy", "Mood and Energy Log"),
          ("Rate your mood and energy each day and spot the patterns.",
           "A quick daily check-in: two ratings and a short note."), fields=("Month",)),
        V("weekly", "weekly mood and energy check-in", ("Weekly Check-In", "Mood and Energy Week"),
          ("Circle a face, shade the energy bar, add a note.", "Seven quick check-ins for the week."),
          fields=("Week of",)),
    ),
    "todo_list": (
        V("checklist", "to-do checklist", ("To-Do List", "Things to Do"),
          ("A simple checklist with a few must-dos at the top.", "Write it down, tick it off.")),
        V("sorted", "to-do list sorted by must, should and could", ("Sorted To-Do", "To-Do by Priority"),
          ("Split the list into must, should and could.", "Sort tasks by how much they matter.")),
    ),
    "meal_planner": (
        V("weekly", "weekly meal planner with a shopping list", ("Meal Planner", "Weekly Meals"),
          ("Plan the week's meals and build the shopping list.", "Every meal of the week on one page."),
          fields=("Week of",)),
        V("groceries", "weekly menu and shopping list by section", ("Meals and Groceries", "Menu and Shopping List"),
          ("A simple menu for the week and a shopping list by section.",
           "Plan meals on the left, shop from the list on the right."), fields=("Week of",)),
    ),
    "budget_tracker": (
        V("monthly", "monthly budget with income, bills and spending", ("Monthly Budget", "Budget Planner"),
          ("Income, bills and spending on one page.", "Plan the month's money and see what is left."),
          fields=("Month",)),
        V("expenses", "spending log", ("Spending Log", "Expense Tracker"),
          ("Write down what you spend and total it up.", "Every purchase, one line each."), fields=("Month",)),
    ),
    "reading_log": (
        V("list", "reading log with star ratings", ("Reading Log", "Books I Read"),
          ("Keep a list of the books you read and rate each one.", "Title, author, dates and a star rating."),
          fields=()),
        V("book_notes", "notes page for one book", ("Book Notes", "Book Review"),
          ("Favorite lines and your thoughts about one book.", "A page for each book you finish."), fields=()),
    ),
    "gratitude": (
        V("daily", "weekly gratitude page", ("Gratitude", "Good Things This Week"),
          ("Three good things for each day of the week.", "Notice the small good things, every day."),
          fields=("Week of",)),
        V("prompts", "gratitude page with short prompts", ("Good Things", "Gratitude Page"),
          ("A few short prompts to notice what went well.", "Look back at the day and write down what went right.")),
    ),
    "notes_lined": (
        V("lined", "lined notes page", ("Notes", "Lined Notes"),
          ("Lined paper with room for a topic and the date.", "Plain lines for notes, lists and ideas."),
          fields=("Date", "Topic")),
        V("cue_column", "notes page with a key points column and summary", ("Study Notes", "Notes and Key Points"),
          ("Key points on the left, notes on the right, a summary below.",
           "Take notes, pull out the key points, sum it up."), fields=("Date", "Topic")),
    ),
    "notes_dot": (
        V("dots", "dot grid notes page", ("Dot Grid", "Dot Grid Notes"),
          ("A light dot grid for notes, sketches and lists.", "Light dots that guide without getting in the way."),
          fields=("Date", "Topic")),
        V("framed", "framed dot grid ideas page", ("Ideas Page", "Dot Grid Ideas"),
          ("A framed dot grid for ideas and sketches.", "Room to think on a light dot grid."), fields=("Date",)),
    ),
    "notes_grid": (
        V("grid", "square grid notes page", ("Grid Paper", "Grid Notes"),
          ("Light squares for notes, charts and plans.", "A square grid for tidy lists and sketches."),
          fields=("Date", "Topic")),
        V("framed", "planning grid with notes lines", ("Planning Grid", "Grid and Notes"),
          ("A framed grid with lines for notes below.", "Sketch it on the grid, explain it underneath."),
          fields=("Date",)),
    ),
}

# Style-specific titles (our own words) that replace the catalog's for one variant.
TITLE_OVERRIDES: dict[str, dict[tuple[str, str], tuple[str, ...]]] = {
    "student": {
        ("focus_sessions", "sessions"): ("Study Sessions", "Study Planner"),
        ("focus_sessions", "tally"): ("Study Rounds", "Study Tracker"),
        ("task_breakdown", "steps"): ("Assignment Breakdown", "Project Steps"),
        ("weekly_planner", "periods"): ("Class Schedule", "Weekly Class Planner"),
        ("notes_lined", "cue_column"): ("Class Notes", "Lecture Notes"),
    },
    "teacher": {
        ("weekly_planner", "periods"): ("Weekly Lesson Plan", "Lesson Planner"),
        ("todo_list", "sorted"): ("Teaching To-Do", "School Day To-Do"),
        ("notes_lined", "cue_column"): ("Lesson Notes", "Meeting Notes"),
    },
    "wfh": {
        ("time_blocked_day", "half_hour"): ("Workday Time Blocks", "Workday Schedule"),
        ("focus_sessions", "sessions"): ("Focused Work Sessions", "Focus Blocks"),
        ("top3_priorities", "three"): ("Top 3 for Today", "Today's Big Three"),
    },
}

# Preferred variant order per style (others follow in catalog order).
VARIANT_PREFS: dict[str, dict[str, tuple[str, ...]]] = {
    "adhd_friendly": {"daily_planner": ("zones", "hourly"), "habit_tracker": ("weekly",),
                      "time_blocked_day": ("blocks",), "mood_energy": ("weekly",)},
    "student": {"weekly_planner": ("periods",), "notes_lined": ("cue_column",), "notes_grid": ("grid",)},
    "teacher": {"weekly_planner": ("periods",), "notes_lined": ("cue_column",)},
    "wfh": {"daily_planner": ("hourly",), "time_blocked_day": ("half_hour",)},
    "cozy": {"gratitude": ("prompts",), "reading_log": ("book_notes",)},
    "botanical": {"habit_tracker": ("monthly",), "mood_energy": ("monthly",)},
}

# Which page types a style's pack is built from, most important first, and which types
# repeat naturally in bigger packs (a 20-page planner has two daily layouts, not one).
_GENERAL_REPEATS = ("daily_planner", "weekly_planner", "todo_list", "notes_lined", "notes_dot", "gratitude",
                    "habit_tracker", "monthly_planner")
STYLE_MIX: dict[str, dict[str, tuple[str, ...]]] = {
    "adhd_friendly": {
        "core": ("daily_planner", "brain_dump", "task_breakdown", "habit_tracker", "focus_sessions", "top3_priorities",
                 "time_blocked_day", "routine_checklist", "mood_energy", "priority_matrix", "todo_list",
                 "weekly_planner", "monthly_planner", "meal_planner", "budget_tracker", "gratitude", "notes_dot",
                 "reading_log", "notes_lined", "notes_grid"),
        "repeats": ("daily_planner", "brain_dump", "task_breakdown", "time_blocked_day", "focus_sessions",
                    "top3_priorities", "todo_list", "habit_tracker", "weekly_planner", "routine_checklist"),
    },
    "minimalist": {
        "core": ("daily_planner", "weekly_planner", "monthly_planner", "todo_list", "habit_tracker", "notes_dot",
                 "top3_priorities", "time_blocked_day", "budget_tracker", "meal_planner", "reading_log", "gratitude",
                 "brain_dump", "priority_matrix", "mood_energy", "routine_checklist", "notes_lined", "notes_grid",
                 "task_breakdown", "focus_sessions"),
        "repeats": _GENERAL_REPEATS,
    },
    "cozy": {
        "core": ("daily_planner", "weekly_planner", "gratitude", "reading_log", "habit_tracker", "meal_planner",
                 "routine_checklist", "mood_energy", "monthly_planner", "todo_list", "budget_tracker",
                 "top3_priorities", "brain_dump", "notes_lined", "notes_dot", "time_blocked_day", "priority_matrix",
                 "task_breakdown", "focus_sessions", "notes_grid"),
        "repeats": ("daily_planner", "gratitude", "weekly_planner", "reading_log", "todo_list", "notes_lined",
                    "meal_planner", "habit_tracker"),
    },
    "botanical": {
        "core": ("weekly_planner", "daily_planner", "habit_tracker", "gratitude", "monthly_planner", "mood_energy",
                 "todo_list", "reading_log", "meal_planner", "routine_checklist", "budget_tracker", "notes_dot",
                 "top3_priorities", "brain_dump", "notes_lined", "time_blocked_day", "priority_matrix",
                 "task_breakdown", "focus_sessions", "notes_grid"),
        "repeats": _GENERAL_REPEATS,
    },
    "seasonal": {
        "core": ("monthly_planner", "weekly_planner", "daily_planner", "gratitude", "todo_list", "habit_tracker",
                 "meal_planner", "budget_tracker", "mood_energy", "reading_log", "routine_checklist",
                 "top3_priorities", "brain_dump", "notes_lined", "time_blocked_day", "priority_matrix", "notes_dot",
                 "task_breakdown", "focus_sessions", "notes_grid"),
        "repeats": ("weekly_planner", "daily_planner", "monthly_planner", "todo_list", "gratitude", "notes_lined",
                    "meal_planner", "habit_tracker"),
    },
    "student": {
        "core": ("weekly_planner", "daily_planner", "task_breakdown", "focus_sessions", "todo_list", "monthly_planner",
                 "priority_matrix", "notes_lined", "reading_log", "habit_tracker", "budget_tracker",
                 "time_blocked_day", "top3_priorities", "brain_dump", "notes_grid", "notes_dot", "routine_checklist",
                 "mood_energy", "meal_planner", "gratitude"),
        "repeats": ("notes_lined", "daily_planner", "weekly_planner", "task_breakdown", "focus_sessions",
                    "notes_grid", "todo_list", "monthly_planner"),
    },
    "teacher": {
        "core": ("weekly_planner", "monthly_planner", "todo_list", "daily_planner", "task_breakdown",
                 "priority_matrix", "notes_lined", "reading_log", "routine_checklist", "top3_priorities",
                 "brain_dump", "time_blocked_day", "habit_tracker", "focus_sessions", "budget_tracker", "notes_grid",
                 "notes_dot", "meal_planner", "mood_energy", "gratitude"),
        "repeats": ("weekly_planner", "daily_planner", "notes_lined", "todo_list", "monthly_planner",
                    "task_breakdown", "routine_checklist"),
    },
    "wfh": {
        "core": ("time_blocked_day", "top3_priorities", "priority_matrix", "focus_sessions", "todo_list",
                 "weekly_planner", "daily_planner", "brain_dump", "task_breakdown", "routine_checklist",
                 "monthly_planner", "habit_tracker", "notes_lined", "notes_grid", "budget_tracker", "meal_planner",
                 "mood_energy", "reading_log", "gratitude", "notes_dot"),
        "repeats": ("time_blocked_day", "daily_planner", "top3_priorities", "todo_list", "weekly_planner",
                    "focus_sessions", "brain_dump", "notes_lined"),
    },
}


def variants(page_type: str) -> tuple[Variant, ...]:
    return CATALOG[page_type]


def variant(page_type: str, name: str) -> Variant:
    for v in CATALOG[page_type]:
        if v.name == name:
            return v
    raise KeyError(f"{page_type} has no variant {name!r}")


def titles_for(style: str, page_type: str, v: Variant) -> tuple[str, ...]:
    return TITLE_OVERRIDES.get(style, {}).get((page_type, v.name)) or v.titles


def variant_order(style: str, page_type: str) -> list[str]:
    prefs = list(VARIANT_PREFS.get(style, {}).get(page_type, ()))
    return prefs + [v.name for v in CATALOG[page_type] if v.name not in prefs]
