"""Research's compliance guard. The brief-document check itself lives in core/briefs.py
(shared with the generators and the pipeline); this module re-exports it and adds the
research-only blocklist extension (settings research.extra_*_terms)."""
from __future__ import annotations

import copy
from collections.abc import Iterable

from core.briefs import (  # noqa: F401 (re-exported for research and its tests)
    FORBIDDEN_KEYS,
    HARD_FLOOR,
    MIN_SHOP_NAME_CHARS,
    BriefComplianceError,
    Guard,
    doc_problems,
    shop_tokens,
    tokens,
    validate_brief_doc,
)
from core.compliance import Blocklist, normalize


def extend_blocklist(blocklist: Blocklist, hard: Iterable[str] = (),
                     review: Iterable[str] = ()) -> Blocklist:
    """`blocklist` plus extra terms (stricter only: a term already listed keeps its kind,
    game-system `!term` entries included).

    The result is a copy of `blocklist` with the extra terms appended, so everything else
    it holds (its game-system terms) stays as loaded.
    """
    systems = getattr(blocklist, "system_terms", None) or {}
    have = {normalize(t) for t, _ in blocklist.hard + blocklist.review}
    have |= {normalize(str(t).lstrip("!").strip()) for t in systems}
    extra_hard = [t for t in hard if normalize(t) not in have]
    have |= {normalize(t) for t in extra_hard}
    extra_review = [t for t in review if normalize(t) not in have]
    if not extra_hard and not extra_review:
        return blocklist
    extra = Blocklist(extra_hard, extra_review)
    merged = copy.copy(blocklist)
    merged.hard = list(blocklist.hard) + extra.hard
    merged.review = list(blocklist.review) + extra.review
    return merged
