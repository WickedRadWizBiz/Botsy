"""Image prompts, built from our own wording plus the brief's theme.

No competitor title, description, image or URL ever reaches a prompt: briefs hold only
aggregated patterns (CLAUDE.md) and are re-checked before generation (generators/shared.py),
subjects come from our built-in table (subjects.py), and every prompt is checked against
the trademark blocklist before any API call. A blocked term raises; a review-flagged term
is reported so the item gets `needs_review`.

Brief files can be edited by hand, so the brief's free text never reaches a prompt as
written: style_notes keep only clauses on STYLE_CLAUSES (our own wording; the rest are left
out and the item is marked for review), and the audience becomes one of a few fixed
phrases ("kids ages 4-8", "teens and adults", ...). Keywords only pick subjects.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.compliance import Blocklist, ComplianceResult, find_urls
from generators.imaging.subjects import subject_pool
from generators.shared import BRIEF_TEXT_FIELDS, as_list

COLORING_BASE = ("black and white line art coloring page for {who}, thick clean outlines, no shading, "
                 "no gray, no text, white background, single subject")
COLORING_TAIL = ("Large simple shapes with roomy spaces to color, the subject fills most of the "
                 "portrait page, no frame or border.")
STICKER_BASE = ("die-cut sticker illustration, bold outline, flat colors, centered, plain solid white "
                "background, no text")
STICKER_TAIL = ("One sticker only, the whole subject visible with empty white space around it, "
                "no cast shadow.")
ORIGINALITY = "original design, no logos, no brand or franchise characters"

# Style-note clauses an image model cannot honor or that fight the cleanup step: asking for
# a transparent background makes models paint a fake checkerboard, which breaks background
# removal. We make transparency and the die-cut border ourselves.
CONFLICTING = ("transparent", "checker", "die-cut border", "die cut border")

# The only style-note clauses a prompt may carry, in our own words: the research defaults
# (research/defaults.py style_notes) and common short forms. Compared after lowercasing and
# collapsing whitespace; the prompt gets this text, not the brief's.
STYLE_CLAUSES = frozenset({
    # research defaults: coloring pages, stickers, fallback
    "bold thick outlines", "large simple shapes with roomy spaces to color", "no shading or gray fills",
    "one main subject per page", "plain white background", "flat cheerful colors",
    "simple shapes that stay readable at small sizes", "one consistent style across the set",
    "clean bold line work", "simple shapes", "print-friendly white background",
    # short forms
    "bold outlines", "thick outlines", "clean outlines", "bold clean outlines", "thick clean outlines",
    "large simple shapes", "rounded shapes", "no shading", "no gray", "no gray fills", "white background",
    "flat colors", "bright colors", "bright cheerful colors", "pastel colors", "soft pastel colors",
    "cute kawaii style", "kawaii style", "cute style", "cartoon style", "simple cartoon style",
    "hand-drawn look", "minimal details", "detailed patterns", "intricate details",
})

KID_WORDS = ("kid", "child", "toddler", "preschool", "boy", "girl", "ages", "age ")
# Checked first: "adults ages 18+" or "teens and adults" must not become "for kids".
GROWN_UP_WORDS = ("adult", "teen", "grown", "senior")
AGE_RANGE_RE = re.compile(r"\b(\d{1,2})\s*(?:-|to)\s*(\d{1,2})\b")


class BlockedPromptError(ValueError):
    """A prompt contains a blocklisted term or a URL; nothing was sent to the model."""


@dataclass(frozen=True)
class PlannedImage:
    subject: str
    prompt: str


def clean_text(text, max_len: int = 200) -> str:
    """One line of plain text: no control characters or runs of whitespace, bounded length."""
    if not isinstance(text, str):
        text = ", ".join(as_list(text))
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = " ".join(text.split())
    return text[:max_len].rsplit(" ", 1)[0] if len(text) > max_len else text


def _style_clauses(notes) -> list[str]:
    """The style notes' clauses, lowercased with whitespace collapsed, minus the ones that
    fight the pipeline (CONFLICTING)."""
    clauses = (" ".join(c.lower().split()) for c in re.split(r"[,;.]", clean_text(notes, 400)))
    return [c for c in clauses if c and not any(w in c for w in CONFLICTING)]


def clean_style_notes(notes) -> str:
    """The style notes' clauses that are on STYLE_CLAUSES, joined; everything else is dropped."""
    return ", ".join(dict.fromkeys(c for c in _style_clauses(notes) if c in STYLE_CLAUSES))


def unlisted_style_clauses(notes) -> list[str]:
    """Clauses clean_style_notes() leaves out because they are not on STYLE_CLAUSES."""
    return [c for c in _style_clauses(notes) if c not in STYLE_CLAUSES]


def audience_phrase(audience) -> str:
    """The brief's audience as one of a few fixed phrases (brief text never reaches a prompt):
    "kids" or "kids ages 4-8", "teens", "adults", "teens and adults", or "all ages"."""
    a = clean_text(audience, 80).lower()
    if any(w in a for w in GROWN_UP_WORDS):
        teens = "teen" in a
        adults = any(w in a for w in ("adult", "grown", "senior"))
        return "teens and adults" if teens and adults else "teens" if teens else "adults"
    if not a or any(w in a for w in KID_WORDS):
        m = AGE_RANGE_RE.search(a)
        if m and int(m[1]) <= int(m[2]) <= 17:
            return f"kids ages {int(m[1])}-{int(m[2])}"
        return "kids"
    return "all ages"


def _context(brief: dict) -> str:
    theme = clean_text(brief.get("theme"), 80)
    style = clean_style_notes(brief.get("style_notes"))
    bits = [f"Theme: {theme}."]
    if clean_text(brief.get("audience"), 80):
        bits.append(f"Audience: {audience_phrase(brief.get('audience'))}.")
    if style:
        bits.append(f"Style: {style}.")
    return " ".join(bits)


def coloring_prompt(brief: dict, subject: str) -> str:
    base = COLORING_BASE.format(who=audience_phrase(brief.get("audience")))
    return f"{base}: {subject}. {_context(brief)} {COLORING_TAIL} {ORIGINALITY}."


def sticker_prompt(brief: dict, subject: str) -> str:
    return f"{STICKER_BASE}. Subject: {subject}. {_context(brief)} {STICKER_TAIL} {ORIGINALITY}."


BUILDERS = {"coloring": coloring_prompt, "sticker": sticker_prompt}


def guard(prompt: str, blocklist: Blocklist) -> ComplianceResult:
    """Refuse a prompt with a blocked term or a URL. Returns the result (flagged terms)."""
    result = blocklist.check(prompt)
    if result.blocked:
        raise BlockedPromptError(f"prompt contains blocked terms {result.blocked}; not sent to the model")
    urls = find_urls(prompt)
    if urls:
        raise BlockedPromptError(f"prompt contains URLs {urls}; not sent to the model")
    return result


def plan_images(brief: dict, kind: str, n: int, blocklist: Blocklist,
                spares: int = 0) -> tuple[list[PlannedImage], ComplianceResult]:
    """n + spares prompts with distinct subjects, all checked before anything is generated.

    Spares replace a subject the model refuses or returns blank, so one refusal does not
    sink a whole pack. Returns (planned, merged compliance result of every prompt).
    """
    if kind not in BUILDERS:
        raise ValueError(f"unknown image kind {kind!r}; choose from {sorted(BUILDERS)}")
    # Also the fields that never reach a prompt verbatim: a blocked term anywhere in them
    # means the brief is not ours to build from.
    fields = blocklist.check_obj({k: brief.get(k) for k in BRIEF_TEXT_FIELDS})
    if fields.blocked:
        raise BlockedPromptError(f"brief contains blocked terms {fields.blocked}; nothing sent to the model")
    theme = clean_text(brief.get("theme"), 80)
    if not theme:
        raise ValueError("brief has no theme")
    keywords = [clean_text(k, 80) for k in as_list(brief.get("keywords"))]
    pool = subject_pool(theme, keywords)
    if n > len(pool):
        raise ValueError(f"theme {theme!r} has only {len(pool)} distinct subjects; {n} were asked for")
    subjects = pool[:n + max(0, spares)]
    build = BUILDERS[kind]
    planned, result = [], ComplianceResult()
    for subject in subjects:
        prompt = build(brief, subject)
        result = result.merge(guard(prompt, blocklist))
        planned.append(PlannedImage(subject, prompt))
    return planned, result
