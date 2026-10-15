"""Deterministic listing copy built from the product facts alone (no API call).

Used for dry runs, as the fallback when the model's draft cannot be repaired field by
field, and for the fixed description sections (what's included, how it works, terms)
that we never let a model write, because they state facts and terms buyers rely on.
Every generic phrase here was checked against the trademark blocklist (see tests).
"""
from __future__ import annotations

import re

from listing import rules
from listing.facts import ACTIVITY_LABELS, niche_labels
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
}
SUGGESTED_USES_OTHER = ["A screen-free activity at home", "Quiet time and travel activity",
                        "A table activity for parties and family gatherings"]


SMALL_WORDS = {"a", "an", "and", "at", "for", "in", "of", "on", "or", "the", "to", "with"}
ACRONYMS = {"pdf": "PDF", "png": "PNG", "svg": "SVG", "zip": "ZIP", "diy": "DIY", "abc": "ABC", "3d": "3D"}
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


def count_phrase(facts: dict) -> str:
    """'20 farm animals coloring pages', or without a count when it is unknown."""
    labels = niche_labels(facts["niche"])
    n, theme = facts["content_count"], facts["theme"]
    unit = labels["one"] if n == 1 else labels["many"]
    return squash(f"{n or ''} {theme} {unit}")


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
        "about": about(facts),
        "suggested_uses": list(SUGGESTED_USES.get(facts["niche"], SUGGESTED_USES_OTHER)),
        "alt_texts": alt_texts(facts),
    }


def title(facts: dict, cfg: dict) -> str:
    labels = niche_labels(facts["niche"])
    keywords = facts["keywords"]
    lead = title_case(keywords[0]) if keywords else title_case(f"{facts['theme']} {labels['many']}")
    if facts["audience"] and " for " not in f" {lead.lower()} ":
        lead += f" for {title_case(facts['audience'])}"
    n = facts["content_count"]
    if facts["niche"] == "stickers":
        count = f"{n} Transparent PNG Stickers" if n else "Transparent PNG Stickers"
    else:
        count = f"{n} Printable Pages" if n else "Printable Pages"
    # Brief keywords are variations of one phrase ("farm animal coloring pages",
    # "farm animal coloring pages pdf"); a variation that mostly repeats words the title
    # already has is skipped, so the title stays readable instead of keyword-stuffed.
    optional = [title_case(k) for k in keywords[1:]]
    tail = [f"{join_words(size_names(facts))} PDF"] if facts["page_sizes"] else []
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
    unit = labels["tag"]  # "coloring", "activity", "stickers"
    out = [*facts["keywords"], theme, f"{theme} {unit}"]
    for kind in facts["activities"]:
        out += ACTIVITY_TAGS.get(kind, [])
    for subject in facts["subjects"][:4]:  # whole phrases only: a cut subject reads badly
        subject = strip_article(subject).lower()
        out += [t for t in (f"{subject} {unit}", subject) if rules.clean_tag(t, cfg, shorten=False)][:1]
    out += GENERIC_TAGS.get(facts["niche"], GENERIC_TAGS_OTHER)
    return [t for t in out if t]


def hook(facts: dict) -> str:
    audience = f" for {facts['audience']}" if facts["audience"] else ""
    if facts["niche"] == "stickers":
        return squash(f"{cap(count_phrase(facts))}{audience}, each one a separate PNG with a "
                      "transparent background, plus a printable sticker sheet.")
    return squash(f"{cap(count_phrase(facts))}{audience}, ready to download and print at home in minutes.")


def about(facts: dict) -> str:
    theme = facts["theme"] or "fun"
    if facts["niche"] == "coloring_pages":
        text = f"Every page is a clean black-and-white line drawing themed around {theme}, one design per page."
    elif facts["niche"] == "activity_sheets":
        mix = activity_phrase(facts) or "puzzles and activities"
        text = f"The pack mixes {mix}, all themed around {theme}."
        if facts["answer_key_pages"]:
            text += " Answer keys are included."
    elif facts["niche"] == "stickers":
        text = f"Each {theme} sticker is cut out cleanly, so it sits on any page or background."
    else:
        text = f"A printable {theme} pack, made by us."
    if facts["subjects"]:
        text += f" Designs include {join_words(facts['subjects'][:6])}."
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
    if facts["answer_key_pages"]:
        lines.append(f"Answer keys ({facts['answer_key_pages']} pages)")
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


def description(draft: dict, facts: dict, cfg: dict, disclosure: str | None) -> str:
    """Our fixed structure: hook, about, what's included, how it works, suggested uses,
    terms, then the AI disclosure verbatim (CLAUDE.md: every AI-assisted listing has it)."""
    bullets = lambda lines: "\n".join(f"- {line}" for line in lines)
    blocks = [
        draft["hook"],
        draft["about"],
        "What's included:\n" + bullets(whats_included(facts)),
        "How it works:\n" + bullets(how_it_works(facts, disclosure is not None)),
        "Suggested uses:\n" + bullets(draft["suggested_uses"]),
        "Terms of use:\n" + squash(cfg["terms"]),
    ]
    if disclosure:
        blocks.append(disclosure)
    return "\n\n".join(b for b in blocks if b.strip())
