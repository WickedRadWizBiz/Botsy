"""Compliance helpers: trademark blocklist, URL scrubbing, AI disclosure.

Blocklist format (config/trademark_blocklist.txt): one term per line, `#` comments,
`?term` = flag for human review instead of hard block. Matching is case- and
accent-insensitive, on whole words, and tolerant of joined or hyphenated forms
("Paw Patrol" also matches "pawpatrol" and "paw-patrol") and of a plural or
possessive "s" ("Minion's", "Barbies").
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from core.config import CONFIG_DIR, resolve

# Scheme or www. URLs in any case; bare domains only with a lowercase TLD, so a
# missing space after a sentence ("for kids.Shop now") is not read as a domain.
URL_RE = re.compile(
    r"(?i:https?://|www\.)\S+"
    r"|\b[\w-]{2,}\.(?:com|net|org|co|io|me|ly|gl|uk|de|ca|au|us|link|app|site|online|shop|store)\b(?:/\S*)?"
)


def normalize(text: str) -> str:
    """Lowercase, strip accents (Pokémon -> pokemon), unify apostrophes."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.replace("’", "'").lower()


def _term_pattern(term: str) -> re.Pattern:
    tokens = [t for t in re.split(r"[^a-z0-9]+", normalize(term)) if t]
    if not tokens:
        raise ValueError(f"blocklist term has no letters or digits: {term!r}")
    # Between words allow only spaces, hyphens, underscores, apostrophes or "&", never
    # commas, full stops or newlines, so "wooden toy, story time" is not "Toy Story".
    body = r"[ \t\-_'&]{0,3}".join(re.escape(t) for t in tokens)
    return re.compile(rf"(?<![a-z0-9]){body}(?:'?s|es)?(?![a-z0-9])")


@dataclass
class ComplianceResult:
    blocked: list[str] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """No hard-blocked term found."""
        return not self.blocked

    @property
    def needs_review(self) -> bool:
        return bool(self.flagged)

    def merge(self, other: "ComplianceResult") -> "ComplianceResult":
        return ComplianceResult(
            blocked=sorted(set(self.blocked) | set(other.blocked)),
            flagged=sorted(set(self.flagged) | set(other.flagged)),
        )

    def to_dict(self) -> dict:
        return {"blocked": list(self.blocked), "flagged": list(self.flagged)}


class Blocklist:
    def __init__(self, hard: Iterable[str], review: Iterable[str] = ()):
        self.hard = [(t, _term_pattern(t)) for t in hard]
        self.review = [(t, _term_pattern(t)) for t in review]

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Blocklist":
        p = resolve(path) if path else CONFIG_DIR / "trademark_blocklist.txt"
        hard, review = [], []
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("?"):
                review.append(line[1:].strip())
            else:
                hard.append(line)
        return cls(hard, review)

    def check(self, *texts: str) -> ComplianceResult:
        """Check each string on its own. Returns the original blocklist spellings found."""
        blocked, flagged = set(), set()
        for text in texts:
            if not text:
                continue
            norm = normalize(text)
            blocked.update(t for t, rx in self.hard if rx.search(norm))
            flagged.update(t for t, rx in self.review if rx.search(norm))
        return ComplianceResult(sorted(blocked), sorted(flagged))

    def check_obj(self, obj) -> ComplianceResult:
        """Check every string inside a nested dict/list structure (keys included)."""
        return self.check(*iter_strings(obj))


def iter_strings(obj) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from iter_strings(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from iter_strings(v)


def find_urls(obj) -> list[str]:
    """Every URL-like substring in a nested structure. Briefs must contain none."""
    return [m.group(0) for s in iter_strings(obj) for m in URL_RE.finditer(s)]


def load_ai_disclosure(shop_name: str, path: str | Path | None = None) -> str:
    p = resolve(path) if path else CONFIG_DIR / "ai_disclosure.txt"
    return p.read_text(encoding="utf-8").strip().replace("{shop_name}", shop_name)


def has_ai_disclosure(description: str, shop_name: str, path: str | Path | None = None) -> bool:
    """True if the full disclosure text appears in the description (whitespace-insensitive)."""
    squash = lambda s: " ".join(s.split())
    return squash(load_ai_disclosure(shop_name, path)) in squash(description)
