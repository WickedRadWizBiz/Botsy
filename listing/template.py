"""Deterministic listing copy built from the product facts alone (no API call).

Used for dry runs, as the fallback when the model's draft cannot be repaired field by
field, and for the fixed description sections (what's included, other packs, how it works,
terms, the game-system disclaimer) that we never let a model write, because they state
facts and terms buyers rely on. Every generic phrase here was checked against the
trademark blocklist and the medical-claims rule (see tests).

Packs (docs/PACKS.md section 5): titles carry the pack (", 20 Pages", ", Set of 12" for
stickers, " for D&D 5e" for a ttrpg item made for a system), and the description says
plainly which pages are also in the theme's other packs, so nobody buys a page twice.
"""
from __future__ import annotations

import re

from listing import rules
from listing.facts import ACTIVITY_LABELS, absent_types, named_types, niche_labels, plural_label
from listing.rules import pick_tags, squash

GENERIC_TAGS = {
    "coloring_pages": [
        "coloring pages", "printable coloring", "kids coloring pages", "coloring sheets", "coloring book pdf",
        "coloring for kids", "printable pages", "instant download", "digital download", "printable pdf",
        "kids activity", "rainy day activity", "quiet time activity", "party activity", "homeschool printable",
    ],
    "activity_sheets": [
        "activity sheets", "kids activity pages", "printable activity", "worksheets for kids", "activity book pdf",
        "busy book printable", "homeschool printable", "preschool activity", "instant download",
        "digital download", "printable pdf", "rainy day activity", "quiet time activity", "travel activity",
        "learning printable",
    ],
    "stickers": [
        "digital stickers", "planner stickers", "png stickers", "transparent png", "sticker sheet",
        "printable stickers", "journal stickers", "scrapbook stickers", "digital planner", "sticker bundle",
        "cute stickers", "clipart png", "instant download", "digital download", "kids stickers",
    ],
    "stationery": [
        "printable planner", "planner pages", "planner printable", "productivity planner", "undated planner",
        "daily planner", "weekly planner", "planner inserts", "to do list", "habit tracker", "printable pdf",
        "instant download", "digital download", "planner bundle", "letter planner",
    ],
    "ttrpg": [
        "ttrpg printable", "rpg printable", "tabletop rpg", "rpg character sheet", "character sheet",
        "campaign tracker", "session notes", "game master tools", "rpg journal", "npc tracker", "printable pdf",
        "instant download", "digital download", "rpg gift", "fantasy rpg",
    ],
}
GENERIC_TAGS_OTHER = ["digital download", "instant download", "printable pdf", "printable pages",
                      "kids activity", "printable art", "rainy day activity", "quiet time activity",
                      "homeschool printable", "party activity", "travel activity", "learning printable",
                      "printable gift"]

ACTIVITY_TAGS = {
    "maze": ["mazes for kids", "printable mazes"],
    "word_search": ["word search", "word search for kids"],
    "tracing": ["tracing worksheets", "handwriting practice"],
    "dot_to_dot": ["dot to dot", "connect the dots"],
}

SUGGESTED_USES = {
    "coloring_pages": ["A quiet-time or rainy-day activity at home", "A screen-free activity for trips and waiting rooms",
                       "A table activity for birthday parties and family gatherings",
                       "A relaxing coloring break for the whole family"],
    "activity_sheets": ["Screen-free practice at home or on the go", "Homeschool and after-school activities",
                        "Quiet time and travel activity", "A table activity for parties and family gatherings"],
    "stickers": ["Decorate digital planners and journals", "Brighten up notes, to-do lists and digital scrapbooks",
                 "Print on sticker paper for crafts, cards and gift tags", "Make reward charts and fun notes for kids"],
    "stationery": ["Plan the day, the week or the month at a glance", "Break a big task into small, doable steps",
                   "Keep track of habits, routines and focus sessions",
                   "Print a fresh page whenever you need one, at home or at work"],
    "ttrpg": ["Keep characters, quests and loot organized at the table",
              "Prepare sessions and encounters as a game master",
              "Keep notes for a long-running campaign in one binder",
              "Print fresh sheets for every new adventure"],
}
SUGGESTED_USES_OTHER = ["A screen-free activity at home", "Quiet time and travel activity",
                        "A table activity for parties and family gatherings"]


SMALL_WORDS = {"a", "an", "and", "at", "for", "in", "of", "on", "or", "the", "to", "with"}
ACRONYMS = {"pdf": "PDF", "png": "PNG", "svg": "SVG", "zip": "ZIP", "diy": "DIY", "abc": "ABC", "3d": "3D",
            "adhd": "ADHD", "wfh": "WFH", "rpg": "RPG", "ttrpg": "TTRPG", "npc": "NPC", "gm": "GM"}
ARTICLES = ("a ", "an ", "the ")


def title_case(text: str) -> str:
    """'fruit png for teens and adults' -> 'Fruit PNG for Teens and Adults'."""
    out = []
    for i, w in enumerate(squash(text).split()):
        low = w.lower()
        if low in ACRONYMS:
            out.append(ACRONYMS[low])
        elif i and low in SMALL_WORDS:
            out.append(low)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def strip_article(text: str) -> str:
    low = text.lower()
    for a in ARTICLES:
        if low.startswith(a):
            return text[len(a):]
    return text


def join_words(items: list[str]) -> str:
    """['a', 'b', 'c'] -> 'a, b and c'."""
    items = [i for i in items if i]
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def cap(text: str) -> str:
    """Capitalize the first letter only ('us letter' stays as written after it)."""
    return text[:1].upper() + text[1:]


def size_names(facts: dict) -> list[str]:
    """'US Letter (8.5 x 11 in)' -> 'US Letter'."""
    return [s.split(" (")[0] for s in facts["page_sizes"]]


# Abbreviations written in capitals when a theme is used in a sentence ("ADHD friendly").
THEME_ACRONYMS = {"adhd": "ADHD", "wfh": "WFH", "rpg": "RPG", "ttrpg": "TTRPG", "npc": "NPC"}


def pretty(text: str) -> str:
    """'adhd friendly' -> 'ADHD friendly'; other words stay as written."""
    return re.sub(r"[A-Za-z]+", lambda m: THEME_ACRONYMS.get(m.group().lower(), m.group()), text or "")


def count_phrase(facts: dict) -> str:
    """'20 farm animals coloring pages', or without a count when it is unknown."""
    labels = niche_labels(facts["niche"])
    n, theme = facts["content_count"], pretty(facts["theme"])
    unit = labels["one"] if n == 1 else labels["many"]
    return squash(f"{n or ''} {theme} {unit}")


def unit_words(facts: dict) -> tuple[str, str]:
    """('page', 'pages'), or ('sticker', 'stickers') for stickers: the unit a pack counts."""
    return ("sticker", "stickers") if facts.get("niche") == "stickers" else ("page", "pages")


def pack_count(facts: dict) -> int | None:
    """The pack's size for a pack item: what the buyer gets (content_count), else the
    planned size. None for an item made before packs, whose title keeps its old count."""
    if not facts.get("pack_size"):
        return None
    return facts.get("content_count") or facts["pack_size"]


def ordered_types(facts: dict) -> list[tuple[str, int]]:
    """(label, count) of the pack's page types, most pages first, then by name (any case)."""
    types = facts.get("page_types") or {}
    return sorted(types.items(), key=lambda kv: (-kv[1], kv[0].lower(), kv[0]))


def type_mix(facts: dict) -> str:
    """'4 daily planners, 3 brain dump pages and 2 habit trackers' (stationery, ttrpg)."""
    return join_words([f"{n} {label}" for label, n in ordered_types(facts)])


# The about text names the page mix itself up to this many kinds; a longer mix is
# summarized there and listed in full under "What's included".
ABOUT_MAX_TYPES = 5


def type_summary(facts: dict) -> str:
    """The page mix for the about text: in full when short, else its leading kinds."""
    types = ordered_types(facts)
    if len(types) <= ABOUT_MAX_TYPES:
        return f"The pack mixes {type_mix(facts)}."
    lead = join_words([plural_label(label) for label, _ in types[:3]] + ["more"])
    return f"The pack mixes {len(types)} kinds of pages, among them {lead} (full list below)."


def activity_phrase(facts: dict) -> str:
    parts = []
    for kind, n in facts["activities"].items():
        one, many = ACTIVITY_LABELS[kind]
        parts.append(f"{n} {one if n == 1 else many}" if n else many)
    return join_words(parts)


# ------------------------------------------------------------------ the draft

def draft(facts: dict, cfg: dict) -> dict:
    """Template copy in the same shape the model returns. It still goes through the same
    rules.validate() / repair() as model copy before it is used."""
    head = title(facts, cfg)
    return {
        "title": head,
        "tags": pick_tags(tag_candidates(facts, cfg), head, cfg),
        "hook": hook(facts),
        "about": about(facts, cfg),
        "suggested_uses": list(SUGGESTED_USES.get(facts["niche"], SUGGESTED_USES_OTHER)),
        "alt_texts": alt_texts(facts),
    }


def theme_unit(theme: str, unit: str) -> str:
    """'fantasy rpg' + 'RPG sheets' -> 'fantasy rpg sheets': the unit's words the theme
    already ends with are not repeated ("Fantasy RPG RPG Sheets")."""
    words, last = unit.split(), (theme.split() or [""])[-1].lower()
    if len(words) > 1 and words[0].lower() == last:
        words = words[1:]
    return squash(f"{theme} {' '.join(words)}")


def minor_type(keyword: str, facts: dict) -> bool:
    """True when `keyword` names a page type that is less than half of a mixed pack
    ("dark fantasy character sheet" on a 20-page pack with 2 character sheets)."""
    total = sum(int(n or 0) for n in (facts.get("page_types") or {}).values())
    return any(n * 2 < total for _, n in named_types(keyword, facts))


def without_theme(keyword: str, facts: dict) -> str:
    """'dark fantasy character sheet' -> 'character sheet' (theme words dropped)."""
    theme = _words(facts.get("theme") or "")
    rest = " ".join(w for w in keyword.split() if not _words(w) <= theme)
    return rest or keyword


def title_keywords(facts: dict) -> tuple[str | None, list[str]]:
    """(lead keyword or None, the other keywords) for the title of a mixed pack
    (stationery, ttrpg): a keyword naming a page type the pack lacks is left out, and one
    naming a type that is under half the pack is not the lead ("Dark Fantasy Character
    Sheet, 20 Pages" would promise 20 character sheets), only its type is named after the
    count. Other niches keep their keywords as they are."""
    keywords = [k for k in facts["keywords"] if not absent_types(k, facts)]
    if not keywords:
        return None, []
    if minor_type(keywords[0], facts):
        return None, [without_theme(keywords[0], facts), *keywords[1:]]
    return keywords[0], keywords[1:]


def title(facts: dict, cfg: dict) -> str:
    labels = niche_labels(facts["niche"])
    first, rest = title_keywords(facts)
    lead = title_case(first) if first else title_case(theme_unit(facts["theme"], labels["many"]))
    system = facts.get("game_system")
    if system and system.lower() not in lead.lower():
        lead += f" for {system}"  # compatibility reference only ("... for D&D 5e")
    elif facts["audience"] and " for " not in f" {lead.lower()} ":
        lead += f" for {title_case(facts['audience'])}"
    n = facts["content_count"]
    packed = pack_count(facts)
    if packed and facts["niche"] == "stickers":
        count = f"Set of {packed}"
    elif packed:
        count = f"{packed} Pages"
    elif facts["niche"] == "stickers":
        count = f"{n} Transparent PNG Stickers" if n else "Transparent PNG Stickers"
    else:
        count = f"{n} Printable Pages" if n else "Printable Pages"
    # Brief keywords are variations of one phrase ("farm animal coloring pages",
    # "farm animal coloring pages pdf"); a variation that mostly repeats words the title
    # already has is skipped, so the title stays readable instead of keyword-stuffed.
    optional = [title_case(k) for k in rest]
    tail = ["Transparent PNG"] if packed and facts["niche"] == "stickers" else []
    if facts["page_sizes"]:
        tail.append(f"{join_words(size_names(facts))} PDF")
    tail.append("Instant Download")

    limit = int(cfg["title_max_chars"])
    out = lead
    if len(out) + 2 + len(count) <= limit:
        out += f", {count}"
    added = 0
    for p in optional:
        if added < 2 and len(out) + 2 + len(p) <= limit and _mostly_new(p, out):
            out += f", {p}"
            added += 1
    for p in tail:
        if len(out) + 2 + len(p) <= limit and _mostly_new(p, out):
            out += f", {p}"
    return out


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _mostly_new(part: str, so_far: str) -> bool:
    """True when more than half of `part`'s words are not in `so_far` yet."""
    words = _words(part)
    return len(words - _words(so_far)) * 2 > len(words)


def tag_candidates(facts: dict, cfg: dict) -> list[str]:
    """Tag sources in priority order: our brief keywords, the theme, then niche phrases."""
    labels = niche_labels(facts["niche"])
    theme = facts["theme"].lower()
    unit = labels["tag"]  # "coloring", "activity", "stickers", "planner", "rpg"
    # A keyword naming a page type the pack lacks ("rpg campaign tracker") describes another pack.
    out = [*(k for k in facts["keywords"] if not absent_types(k, facts)), theme, f"{theme} {unit}"]
    types = [label for label, _ in ordered_types(facts)]
    out += system_tags(facts, types, cfg)
    for kind in facts["activities"]:
        out += ACTIVITY_TAGS.get(kind, [])
    for label in types[:4]:  # "habit trackers" -> "habit tracker"
        one = label[:-1] if label.endswith("s") and not label.endswith("ss") else label
        out += [t for t in (f"{one} printable", one) if rules.clean_tag(t, cfg, shorten=False)][:1]
    for subject in facts["subjects"][:4]:  # whole phrases only: a cut subject reads badly
        subject = strip_article(subject).lower()
        out += [t for t in (f"{subject} {unit}", subject) if rules.clean_tag(t, cfg, shorten=False)][:1]
    out += GENERIC_TAGS.get(facts["niche"], GENERIC_TAGS_OTHER)
    # Generic niche tags too: a tag naming a page type this pack lacks would describe another pack.
    return [t for t in out if t and not absent_types(t, facts)]


SYSTEM_TAGS_MAX = 3  # tags naming the game system; the rest describe the pack


def system_tags(facts: dict, types: list[str], cfg: dict) -> list[str]:
    """Up to SYSTEM_TAGS_MAX tags naming the item's game system, from its tag-safe names
    ("dnd 5e", "5e character sheet", "dnd 5e printable"). A name that is only an edition
    ("5e") never stands alone as a tag. The sheet kind is "character sheet" when the pack
    has one, else its main page type. Empty unless the listing may name a system (ttrpg)."""
    if not facts.get("game_system"):
        return []
    singular = [t[:-1] if t.endswith("s") and not t.endswith("ss") else t for t in types]
    kind = "character sheet" if "character sheet" in singular or not singular else singular[0]
    names = []
    for name in facts.get("game_system_names") or []:
        tag = rules.clean_tag_case(name)
        if tag and all(rules.tag_char_ok(ch) for ch in tag) and tag not in names:
            names.append(tag)  # "d&d" is not a valid tag; "dnd 5e" is
    full = [n for n in names if re.search(r"[a-z]{2}", n)]   # "dnd 5e", not the bare edition "5e"
    candidates = [f"{n} {kind}" for n in names[:3]] + [f"{n} printable" for n in full[:2]] + full[:2]
    out = []
    for t in candidates:
        if rules.clean_tag(t, cfg, shorten=False) and t not in out:
            out.append(t)
    return out[:SYSTEM_TAGS_MAX]


def hook(facts: dict) -> str:
    audience = f" for {facts['audience']}" if facts["audience"] else ""
    if facts["niche"] == "stickers":
        return squash(f"{cap(count_phrase(facts))}{audience}, each one a separate PNG with a "
                      "transparent background, plus a printable sticker sheet.")
    return squash(f"{cap(count_phrase(facts))}{audience}, ready to download and print at home in minutes.")


def about(facts: dict, cfg: dict | None = None) -> str:
    theme = pretty(facts["theme"]) or "fun"
    if facts["niche"] == "coloring_pages":
        text = f"Every page is a clean black-and-white line drawing themed around {theme}, one design per page."
    elif facts["niche"] == "activity_sheets":
        mix = activity_phrase(facts) or "puzzles and activities"
        text = f"The pack mixes {mix}, all themed around {theme}."
        if facts["answer_key_pages"]:
            text += " Answer keys are included."
    elif facts["niche"] == "stickers":
        text = f"Each {theme} sticker is cut out cleanly, so it sits on any page or background."
    elif facts["niche"] == "stationery":
        text = (f"Every page is a clean, original {theme} layout with clear sections and room to write. "
                "The pages are undated, so you can start on any day.")
        if type_mix(facts):
            text += f" {type_summary(facts)}"
    elif facts["niche"] == "ttrpg":
        text = (f"Every sheet is an original {theme} layout of our own, made to be filled in by hand at the "
                "table, with clear boxes and plenty of writing space.")
        if type_mix(facts):
            text += f" {type_summary(facts)}"
    else:
        text = f"A printable {theme} pack, made by us."
    # A subject with wording our copy may not use ("a medical kit") is left out, not reworded.
    subjects = [x for x in facts["subjects"] if not (cfg and rules.banned(x, cfg))]
    if subjects:
        text += f" Designs include {join_words(subjects[:6])}."
    return text


def alt_texts(facts: dict) -> list[str]:
    product = count_phrase(facts)
    if facts["audience"]:
        product += f" for {facts['audience']}"
    out = []
    for img in facts["images"]:
        out.append(f"{cap(img['shows'])}. Product: {product}.")
    return out


# ------------------------------------------------------------------ description

def whats_included(facts: dict) -> list[str]:
    lines = [count_phrase(facts)]
    if facts["niche"] == "activity_sheets" and facts["activities"]:
        lines[0] += f": {activity_phrase(facts)}"
    elif type_mix(facts):
        lines[0] += f": {type_mix(facts)}"
    covers = facts.get("title_pages") or 0
    if covers:  # so the PDF's page count adds up for the buyer
        lines.append("A cover page" if covers == 1 else f"{covers} cover pages")
    if facts["answer_key_pages"]:
        n = facts["answer_key_pages"]
        lines.append(f"Answer keys ({n} {'page' if n == 1 else 'pages'})")
    if facts["png_in_zip"]:
        n_zips = facts.get("zip_files") or 1
        if n_zips == 1:
            lines.append(f"ZIP file with {facts['png_in_zip']} transparent PNG files")
        else:  # the generator splits a big pack to stay under Etsy's file size limit
            lines.append(f"{n_zips} ZIP files with {facts['png_in_zip']} transparent PNG files in total")
    if facts["page_sizes"]:
        pdf = "Printable sticker sheet PDF" if facts["niche"] == "stickers" else "Printable PDF"
        pages = ""
        if facts["pdf_pages"]:
            n = facts["pdf_pages"]
            pages = f", {n} page{'' if n == 1 else 's'}" + (" each" if len(facts["page_sizes"]) > 1 else "")
        lines.append(f"{pdf} in {join_words(facts['page_sizes'])}{pages}")
    others = [t for t in facts["file_types"] if t not in ("PDF", "ZIP")]
    if others:
        lines.append(f"Also included: {join_words(others)} files")
    if facts["dpi"]:
        lines.append(f"High resolution: {facts['dpi']} DPI")
    return lines


def how_it_works(facts: dict, disclosure_included: bool) -> list[str]:
    lines = ["Instant digital download: your files are ready in your Etsy account as soon as your payment is confirmed"]
    if facts["niche"] == "stickers":
        lines.append("Use the PNG stickers in your digital planner or note app, or print the sticker sheet "
                     "on sticker paper")
    elif facts["page_sizes"]:
        lines.append(f"Print at home or at a local print shop on {join_words(size_names(facts))} paper")
    else:
        lines.append("Print at home or at a local print shop")
    if not disclosure_included:  # the AI disclosure text already says this
        lines.append("This is a digital download. No physical item will be shipped.")
    return lines


def other_packs(facts: dict) -> list[str]:
    """What this pack shares with the theme's other packs, stated plainly (docs/PACKS.md
    section 5; user-approved 2026-10-16): a derived pack names the bigger pack that holds
    its pages, the master pack names the smaller packs it contains, a fresh pack says its
    pages are all new. Then the other sizes, without links. [] for an item without pack data."""
    size, source = facts.get("pack_size"), facts.get("pack_source")
    if not size:
        return []
    one, many = unit_words(facts)
    theme = pretty(facts["theme"]) or "same theme"
    related = facts.get("related_packs") or []
    same = [r for r in related if r.get("same_set")]
    lines = []
    if source == "derived":
        masters = sorted({r["size"] for r in same if r.get("source") == "master"}) \
            or sorted({r["size"] for r in same if r["size"] > size})[-1:]
        where = f"our {masters[-1]}-{one} {theme} pack" if masters else f"our larger {theme} pack"
        lines.append(f"These {size} {many} are also included in {where}, so there is no need to buy both.")
        siblings = sorted({r["size"] for r in same if r["size"] not in masters and r["size"] != size})
        if siblings:
            lines.append(f"Some of them may also appear in our {_sizes(siblings, one)} {theme} "
                         f"pack{'s' if len(siblings) > 1 else ''}.")
    elif source == "master":
        smaller = sorted({r["size"] for r in same if r["size"] < size})
        if smaller:
            lines.append(f"This pack contains every {one} of our {_sizes(smaller, one)} {theme} "
                         f"pack{'s' if len(smaller) > 1 else ''}, so there is no need to buy "
                         f"{'those' if len(smaller) > 1 else 'that one'} as well.")
    elif source == "fresh":
        lines.append(f"All-new {many}, not included in our other packs.")
    others = sorted({r["size"] for r in related if r["size"] != size})
    if others:
        lines.append(f"This theme also comes in packs of {join_words([str(n) for n in others])} {many}.")
    return lines


def _sizes(sizes: list[int], one: str) -> str:
    """[5, 10] -> '5-page and 10-page'."""
    return join_words([f"{n}-{one}" for n in sizes])


def compatibility(facts: dict) -> str:
    """The compatibility line of a ttrpg listing made for a game system."""
    system = facts.get("game_system")
    return f"Made for use with {system}. This is an unofficial product of our own design." if system else ""


def description(draft: dict, facts: dict, cfg: dict, disclosure: str | None, disclaimer: str | None = None) -> str:
    """Our fixed structure: hook, about, what's included, other packs, how it works,
    suggested uses, terms, the game-system disclaimer for a ttrpg listing that names its
    system (CLAUDE.md), then the AI disclosure verbatim (CLAUDE.md: every AI-assisted
    listing has it)."""
    bullets = lambda lines: "\n".join(f"- {line}" for line in lines)
    packs = other_packs(facts)
    blocks = [
        draft["hook"],
        draft["about"],
        "What's included:\n" + bullets(whats_included(facts)),
        ("Other packs in this theme:\n" + bullets(packs)) if packs else "",
        "How it works:\n" + bullets(how_it_works(facts, disclosure is not None)),
        "Suggested uses:\n" + bullets(draft["suggested_uses"]),
        "Terms of use:\n" + squash(cfg["terms"]),
    ]
    if disclaimer:
        blocks.append("\n".join(x for x in (compatibility(facts), squash(disclaimer)) if x))
    if disclosure:
        blocks.append(disclosure)
    return "\n\n".join(b for b in blocks if b.strip())
