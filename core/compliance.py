"""Compliance helpers: trademark blocklist, health topics, URL scrubbing, AI disclosure.

Blocklist.load reads two files with one format: config/trademark_blocklist.txt and
config/health_terms.txt (medical topics as hard terms, borderline ones as `?` review terms;
docs/HEALTH_TERMS.md). Blocklist.health holds the health terms so a message can say
"health topic" (Blocklist.label). Result claims ("reduces anxiety") are core.health.

Blocklist format (config/trademark_blocklist.txt): one term per line, `#` comments,
`?term` = flag for human review instead of hard block, `!term` = a game-system name
(docs/PACKS.md section 4). Matching is case- and accent-insensitive, on whole words, and
tolerant of joined or hyphenated forms ("Paw Patrol" also matches "pawpatrol" and
"paw-patrol"), of a full stop after a word ("Dr. Seuss"), of a plural or possessive "s"
("Minion's", "Barbies"), and of the Unicode tricks normalize() undoes (™ signs,
zero-width characters, look-alike letters). A term written with "&" also matches "and"
there ("Dungeons and Dragons"), punctuation the term itself uses between words is allowed
there ("Magic: The Gathering"), and two one-letter words need their joiner or a dash
between them ("D&D" matches "d & d", "d-d" and "d_d", never "dd" or "d d").

Game-system (`!`) terms are blocked like hard terms everywhere, unless the caller allows
their system: `check(*texts, allow_systems=("dnd5e",))` treats that system's names as clean
(reported in ComplianceResult.allowed). Only a ttrpg listing's title, tags and description
for an item with that system set may do so; briefs, prompts, printed pages, file names,
mockups, alt text and other niches never pass allow_systems. A `!` term belongs to every
system in config/ttrpg_systems.yaml (core.systems, read lazily) whose names or owner it
appears in; a term that belongs to no system can never be allowed. A system term written
together with an edition ("dnd5e") is not matched here, because registry ids such as
"dnd5e" are stored in briefs and manifests; the listing, QA and publisher stages also run
core.compliance.system_mentions() over buyer-facing text (every spelling the registry
lists, with these word rules), which catches those spellings.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from core.config import CONFIG_DIR, resolve

log = logging.getLogger("core.compliance")

# Scheme or www. URLs in any case; bare domains only with a lowercase TLD, so a
# missing space after a sentence ("for kids.Shop now") is not read as a domain.
URL_RE = re.compile(
    r"(?i:https?://|www\.)\S+"
    r"|\b[\w-]{2,}\.(?:com|net|org|co|io|me|ly|gl|uk|de|ca|au|us|link|app|site|online|shop|store)\b(?:/\S*)?"
)


# Trademark signs become a space: NFKD would spell ™ / ℠ out and glue them to the
# word ("Pokémon™" -> "pokemontm"), which then no longer matches "Pokemon".
_MARKS = str.maketrans({c: " " for c in "™℠©®"})
_APOSTROPHES = str.maketrans({c: "'" for c in "‘’ʼ′`"})
# Latin look-alikes from Cyrillic and Greek, so "Pаw Patrol" with a Cyrillic "а" still
# matches. Applied before lowercasing: Greek "Η" reads as "H" but its lowercase "η" as "n".
_CONFUSABLES = str.maketrans(dict(zip(
    "АВЕКМНОРСТУХІЈЅ" "авекмнорстухіјѕԁӏɡ"
    "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧ" "αβεηικμνορτυχ",
    "ABEKMHOPCTYXIJS" "abekmhopctyxijsdlg"
    "ABEZHIKMNOPTYX" "abenikmvoptux",
    strict=True)))
_HSPACE_RE = re.compile(r"[^\S\n]+")  # runs of whitespace other than newlines


def normalize(text: str) -> str:
    """Lowercase, strip accents (Pokémon -> pokemon), unify apostrophes and dashes.

    Also: ™ ℠ © ® become a space, invisible format characters (zero-width spaces and
    joiners, soft hyphens, BOM) are dropped, every Unicode dash and the minus sign become
    "-", Cyrillic/Greek look-alike letters become Latin, and runs of spaces or tabs
    become one space. Each step only lets more spellings match the blocklist.
    """
    text = unicodedata.normalize("NFKD", text.translate(_MARKS))
    out = []
    for c in text:
        if unicodedata.combining(c):
            continue
        cat = unicodedata.category(c)
        if cat == "Cf":
            continue
        out.append("-" if cat == "Pd" or c == "−" else c)
    text = "".join(out).translate(_APOSTROPHES).translate(_CONFUSABLES).lower()
    return _HSPACE_RE.sub(" ", text)


_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Between words: an optional full stop right after the word ("Dr. Seuss", "Hello.Kitty"),
# then only spaces, hyphens, underscores, apostrophes or "&", never commas or newlines, so
# "wooden toy, story time" is not "Toy Story".
_GAP_CHARS = r" \t\-_'&"
_SUFFIX = r"(?:'?s|es)?"


def _gap_pattern(gap: str, left: str, right: str) -> str:
    """The regex between two words of a term. `gap` is what the term itself has there."""
    extra = "".join(sorted({c for c in gap if not c.isspace() and c not in "-_'&."}))
    chars = _GAP_CHARS + "".join(re.escape(c) for c in extra)
    joiners = sorted({c for c in gap if not c.isspace()})
    if len(left) == 1 and len(right) == 1 and joiners:
        # Two one-letter words keep a joiner: "D&D" is "d&d", "d & d", "d-d", "d_d" (a file
        # name), "d.&d." or "d and d", never "dd" or the "d d d d" of a letter-tracing row.
        out = rf"\.?[ \t]{{0,2}}[{''.join(re.escape(c) for c in sorted({*joiners, '-', '_'}))}][ \t]{{0,2}}"
    else:
        least = 1 if len(left) == 1 and len(right) == 1 else 0
        out = rf"\.?[{chars}]{{{least},3}}"
    if "&" in gap:                                            # "Dungeons and Dragons"
        # Longer words may also be joined ("DungeonsAndDragons"); two one-letter words keep
        # a separator, so "dandd" is never "D&D".
        sep = r"[ \t\-_]{1,2}" if len(left) == 1 and len(right) == 1 else r"[ \t\-_]{0,2}"
        out = rf"(?:{out}|{sep}and{sep})"
    return out


def _term_pattern(term: str) -> re.Pattern:
    norm = normalize(term)
    words = list(_TOKEN_RE.finditer(norm))
    if not words:
        raise ValueError(f"blocklist term has no letters or digits: {term!r}")
    body = re.escape(words[0].group())
    for prev, cur in zip(words, words[1:]):
        body += _gap_pattern(norm[prev.end():cur.start()], prev.group(), cur.group()) + re.escape(cur.group())
    return re.compile(rf"(?<![a-z0-9]){body}{_SUFFIX}(?![a-z0-9])")


def term_pattern(term: str) -> re.Pattern:
    """The compiled pattern a blocklist term matches with (on normalize()d text): whole words,
    a plural "s"/"es", and the joiners _gap_pattern allows between words. Public for other
    matchers of the same names (core.systems)."""
    return _term_pattern(term)


@dataclass
class ComplianceResult:
    blocked: list[str] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)
    # Game-system (`!`) terms found that the check was told to allow (allow_systems).
    allowed: list[str] = field(default_factory=list)

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
            allowed=sorted(set(self.allowed) | set(other.allowed)),
        )

    def to_dict(self) -> dict:
        out = {"blocked": list(self.blocked), "flagged": list(self.flagged)}
        if self.allowed:  # only ttrpg listings that name their game system have any
            out["allowed"] = list(self.allowed)
        return out


_catalog_warned = False


def system_catalog() -> dict:
    """config/ttrpg_systems.yaml through core.systems.load_systems(), or {} when it cannot
    be read (then no `!` term can be allowed: the stricter outcome)."""
    global _catalog_warned
    try:
        from core import systems

        catalog = systems.load_systems()
    except Exception as e:  # missing module or file, bad YAML: fail closed
        if not _catalog_warned:
            log.warning("game systems unavailable (%s: %s); every game-system name stays blocked",
                        type(e).__name__, e)
            _catalog_warned = True
        return {}
    return catalog if isinstance(catalog, Mapping) else {}


def _id_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    return tuple(str(v) for v in value if v)


def system_ids_for(terms: Iterable[str], catalog: Mapping | None = None) -> dict[str, tuple[str, ...]]:
    """{term: ids of the systems it names}: a term belongs to every system whose names,
    owner or owner_names it appears in ("5e" in "D&D 5e", "Paizo" in owner "Paizo Inc.")."""
    catalog = system_catalog() if catalog is None else catalog
    entries = []
    for sid, entry in (catalog or {}).items():
        if not isinstance(entry, Mapping):
            continue
        names = []
        for key in ("names", "owner", "owner_names"):
            raw = entry.get(key)
            names += [raw] if isinstance(raw, str) else list(raw or [])
        texts = [normalize(n) for n in names if isinstance(n, str) and n.strip()]
        entries.append((str(entry.get("id") or sid), texts))
    out = {}
    for term in terms:
        rx = _term_pattern(term)
        out[term] = tuple(dict.fromkeys(sid for sid, texts in entries if any(rx.search(t) for t in texts)))
    return out


def system_mentions(texts: Iterable[str], catalog: Mapping | None = None) -> list[str]:
    """Ids of the game systems whose names, owner or owner_names (config/ttrpg_systems.yaml,
    `catalog` default core.systems.load_systems()) appear in `texts`, with the blocklist's
    word rules: also spellings the `!` terms do not cover ("DnD 5e" as "dnd5e", "PF2e").
    Each text is checked on its own. [] when the registry cannot be read."""
    catalog = system_catalog() if catalog is None else catalog
    terms: dict[str, list[str]] = {}
    for sid, entry in (catalog or {}).items():
        if not isinstance(entry, Mapping):
            continue
        for key in ("names", "owner", "owner_names"):
            raw = entry.get(key)
            for name in [raw] if isinstance(raw, str) else list(raw or []):
                if isinstance(name, str) and _TOKEN_RE.search(normalize(name)):
                    terms.setdefault(name, []).append(str(entry.get("id") or sid))
    if not terms:
        return []
    found = Blocklist([], [], terms).check(*[t for t in texts if isinstance(t, str)]).blocked
    order = [str(e.get("id") or k) for k, e in catalog.items() if isinstance(e, Mapping)]
    hit = {sid for t in found for sid in terms[t]}
    return [sid for sid in order if sid in hit]


HEALTH_LABEL = "health topic"


def read_terms(path: str | Path) -> tuple[list[str], list[str], list[str]]:
    """(hard, review, systems) from a term file: one term per line, `#` comments, `?term`
    review, `!term` game system. A UTF-8 byte-order mark (a file saved by Windows Notepad)
    is ignored, so the first line is not read as a term."""
    hard, review, systems = [], [], []
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("?"):
            review.append(line[1:].strip())
        elif line.startswith("!"):
            systems.append(line[1:].strip())
        else:
            hard.append(line)
    return hard, review, systems


class Blocklist:
    """Hard, review and game-system terms. `systems` is a list of `!` terms (their ids are
    looked up in `catalog`, default core.systems.load_systems(), when first needed) or a
    {term: id or ids} mapping that fixes them. `health` names the hard and review terms
    that are health topics (config/health_terms.txt: medical = hard, borderline = review)
    rather than trademarks, so messages can say so (label())."""

    def __init__(self, hard: Iterable[str], review: Iterable[str] = (), systems: Iterable[str] = (), *,
                 catalog: Mapping | None = None, health: Iterable[str] = ()):
        self.hard = [(t, _term_pattern(t)) for t in hard]
        self.review = [(t, _term_pattern(t)) for t in review]
        self.health = frozenset(health)
        given = dict(systems) if isinstance(systems, Mapping) else dict.fromkeys(systems)
        self.system_rx = [(t, _term_pattern(t)) for t in given]
        self._system_catalog = catalog
        self._system_map: dict[str, tuple[str, ...]] | None = (
            {t: _id_tuple(v) for t, v in given.items()} if isinstance(systems, Mapping) else None)

    @classmethod
    def load(cls, path: str | Path | None = None, *, catalog: Mapping | None = None,
             health_path: str | Path | None | bool = None) -> "Blocklist":
        """The trademark blocklist (`path`, default config/trademark_blocklist.txt) plus the
        health topics (`health_path`, settings compliance.health_terms, default
        config/health_terms.txt; False reads none). A health term already in the trademark
        file keeps that file's kind. The health file may not hold `!` lines."""
        p = resolve(path) if path else CONFIG_DIR / "trademark_blocklist.txt"
        hard, review, systems = read_terms(p)
        health: list[str] = []
        if health_path is not False:
            hp = resolve(health_path) if health_path else CONFIG_DIR / "health_terms.txt"
            if health_path or hp.is_file():
                h_hard, h_review, h_systems = read_terms(hp)
                if h_systems:
                    raise ValueError(f"{hp}: health terms cannot be game-system (!) terms: {h_systems}")
                known = {normalize(t) for t in [*hard, *review, *systems]}
                h_hard = [t for t in dict.fromkeys(h_hard) if normalize(t) not in known]
                h_review = [t for t in dict.fromkeys(h_review) if normalize(t) not in known]
                hard += h_hard
                review += h_review
                health = h_hard + h_review
        return cls(hard, review, systems, catalog=catalog, health=health)

    @property
    def health_terms(self) -> frozenset[str]:
        """Alias of `health` (service/searchlist.py reads either name)."""
        return self.health

    def is_health(self, term: str) -> bool:
        """True for a term that came from the health file (a medical or borderline topic)."""
        return term in self.health

    def label(self, term: str) -> str:
        """The term for a message: "blood pressure (health topic)" or "Disney"."""
        return f"{term} ({HEALTH_LABEL})" if term in self.health else term

    def labels(self, terms: Iterable[str]) -> list[str]:
        return [self.label(t) for t in terms]

    def _ids(self) -> dict[str, tuple[str, ...]]:
        if self._system_map is None:
            self._system_map = system_ids_for([t for t, _ in self.system_rx], self._system_catalog)
        return self._system_map

    @property
    def system_terms(self) -> dict[str, str | None]:
        """{`!` term: the first system id it belongs to, or None}. Research cuts these from
        themes and keywords; a row naming one is not licensed property."""
        ids = self._ids() if self.system_rx else {}
        return {t: (ids.get(t) or (None,))[0] for t, _ in self.system_rx}

    def system_names(self, system_id: str | None) -> list[str]:
        """The `!` terms that belong to `system_id` (empty for "generic" or an unknown id)."""
        if not system_id or not self.system_rx:
            return []
        ids = self._ids()
        return [t for t, _ in self.system_rx if str(system_id) in ids.get(t, ())]

    def check(self, *texts: str, allow_systems: Iterable[str] | str = ()) -> ComplianceResult:
        """Check each string on its own. Returns the original blocklist spellings found.
        Game-system terms are blocked unless they belong to a system in `allow_systems`."""
        if isinstance(allow_systems, str):
            allow_systems = (allow_systems,)
        allow = {str(s) for s in allow_systems or () if s}
        ids = self._ids() if allow and self.system_rx else {}
        blocked, flagged, allowed = set(), set(), set()
        for text in texts:
            if not text:
                continue
            norm = normalize(text)
            blocked.update(t for t, rx in self.hard if rx.search(norm))
            flagged.update(t for t, rx in self.review if rx.search(norm))
            for t, rx in self.system_rx:
                if rx.search(norm):
                    (allowed if allow.intersection(ids.get(t, ())) else blocked).add(t)
        return ComplianceResult(sorted(blocked), sorted(flagged), sorted(allowed))

    def check_obj(self, obj, *, allow_systems: Iterable[str] | str = ()) -> ComplianceResult:
        """Check every string inside a nested dict/list structure (keys included)."""
        return self.check(*iter_strings(obj), allow_systems=allow_systems)


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
