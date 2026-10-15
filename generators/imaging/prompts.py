"""Image prompts, built only from our own brief fields (theme, keywords, audience, style_notes).

No competitor title, description, image or URL ever reaches a prompt: briefs hold only
aggregated patterns (CLAUDE.md), subjects come from our built-in table (subjects.py), and
every prompt is checked against the trademark blocklist before any API call. A blocked
term raises; a review-flagged term is reported so the item gets `needs_review`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.compliance import Blocklist, ComplianceResult, find_urls
from generators.imaging.subjects import subject_pool

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

KID_WORDS = ("kid", "child", "toddler", "preschool", "boy", "girl", "ages", "age ")
# Checked first: "adults ages 18+" or "teens and adults" must not become "for kids".
GROWN_UP_WORDS = ("adult", "teen", "grown", "senior")


class BlockedPromptError(ValueError):
    """A prompt contains a blocklisted term or a URL; nothing was sent to the model."""


@dataclass(frozen=True)
class PlannedImage:
    subject: str
    prompt: str


def as_list(value) -> list[str]:
    """Brief list fields as a list of strings; a lone string is one entry, not its letters."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    return [str(value)]


def clean_text(text, max_len: int = 200) -> str:
    """One line of plain text: no control characters or runs of whitespace, bounded length."""
    if not isinstance(text, str):
        text = ", ".join(as_list(text))
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = " ".join(text.split())
    return text[:max_len].rsplit(" ", 1)[0] if len(text) > max_len else text


def clean_style_notes(notes: str | None) -> str:
    clauses = [c.strip() for c in re.split(r"[,;]", clean_text(notes, 400))]
    kept = [c for c in clauses if c and not any(w in c.lower() for w in CONFLICTING)]
    return ", ".join(kept)


def _who(audience: str) -> str:
    a = audience.lower()
    if any(w in a for w in GROWN_UP_WORDS):
        return audience
    return "kids" if not a or any(w in a for w in KID_WORDS) else audience


def _context(brief: dict) -> str:
    theme = clean_text(brief.get("theme"), 80)
    audience = clean_text(brief.get("audience"), 80)
    style = clean_style_notes(brief.get("style_notes"))
    bits = [f"Theme: {theme}."]
    if audience:
        bits.append(f"Audience: {audience}.")
    if style:
        bits.append(f"Style: {style}.")
    return " ".join(bits)


def coloring_prompt(brief: dict, subject: str) -> str:
    base = COLORING_BASE.format(who=_who(clean_text(brief.get("audience"), 80)))
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
