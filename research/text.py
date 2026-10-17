"""Turn titles and keywords into short theme n-grams, formats and pack counts.

Themes are n-grams of 1-3 content words left after removing words that describe
the niche, the file format, the buyer or the sales pitch ("coloring pages",
"printable", "pdf", "for kids", "bestseller", "300 dpi"). Removed words split the
text, so an n-gram never spans them: "Farm Animals Coloring Pages for Kids" gives
"farm", "animals", "farm animals" and nothing that reaches across "coloring pages".
Punctuation (",", "|", " - ") splits too. A few connector words ("and", "of",
"the", "to") may sit inside an n-gram but never start or end one, so seasonal
themes survive whole: "back to school", "day of the dead", "under the sea".
Generic heads ("day", "eve", "back") never stand alone as a theme, so holidays
stay apart instead of pooling into one broad "day".

Spans matching a hard-blocked trademark term are cut out before n-grams are
built, so a blocked name can never become a theme; the row remembers it had one
(see aggregate.py for how that marks themes that co-occur with licensed names).
Platform and app names (excluded_terms, e.g. "goodnotes") are cut the same way:
they describe compatibility, not a design, and are brands we keep out of briefs.

Game-system names (blocklist `!term` entries, Blocklist.system_terms, docs/PACKS.md
section 4) are cut too, but a row naming one is NOT a licensed-property row: "Cyberpunk
Character Sheet for <system>" is an ordinary listing that states compatibility, so it
keeps counting toward a theme and never marks the theme as co-occurring with a blocked
name. The name itself never reaches a theme, tag or keyword.

Each niche can add its own stopwords, keep words and connectors (research.niche_stopwords /
niche_keep_words / niche_connectors): planner page types ("habit tracker") are not
stationery themes, "teacher" is (a planner audience) while it stays a stopword elsewhere,
and "from" holds "work from home" together. Medical claim words (research.medical_terms)
are passed in as excluded terms, so no theme or keyword ever carries one, except inside the
ordinary phrases the listing stage allows too ("trick or treat", "sweet treats").
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from core.compliance import URL_RE, Blocklist, normalize

MAX_N = 3

# Multi-word phrases removed before splitting into words.
STOP_PHRASES = (
    "coloring pages", "coloring page", "coloring sheets", "coloring sheet", "coloring book",
    "coloring books", "colouring pages", "colouring page", "colouring book", "color by number",
    "colour by number", "activity sheets", "activity sheet", "activity pages", "activity page",
    "activity book", "activity books", "activity pack", "busy book", "word search",
    "word searches", "word find", "dot to dot", "dot to dots", "connect the dots",
    "digital download", "digital downloads", "instant download", "instant downloads",
    "digital stickers", "digital sticker", "planner stickers", "sticker sheet",
    "sticker sheets", "sticker pack", "sticker bundle", "clip art", "line art",
    "for kids", "for adults", "for toddlers", "for boys", "for girls", "for teens",
    "us letter", "letter size", "high resolution", "high res", "commercial use",
    "personal use", "transparent background", "print at home", "pre cropped",
    "ready to print", "print and play", "learning activities", "fine motor",
    # Sales pitch and format descriptions, not design themes.
    "black and white", "black white", "line drawing", "line drawings", "die cut", "kiss cut",
    "a4 and letter", "a4 and us letter", "letter and a4", "us letter and a4",
    "best seller", "best sellers", "gift idea", "gift ideas", "stocking stuffer",
    "stocking stuffers", "basket stuffer", "basket stuffers", "quiet time", "screen free",
    "busy bag", "busy bags", "bonus pages", "works with", "made for", "designed for",
)

STOP_WORDS = frozenset("""
a an and or the for with of in on to by from at as is are be it its this that my your our
coloring colouring color colour colors page pages sheet sheets book books sticker stickers
clipart cliparts maze mazes worksheet worksheets activity activities tracing trace printable
printables print printing printed digital download downloads instant pdf pdfs png pngs svg svgs
jpg jpeg jpgs zip file files bundle bundles set sets pack packs kit template templates
kids kid children childrens child toddler toddlers preschool preschooler preschoolers
kindergarten prek homeschool homeschooling classroom teacher teachers student students
adult adults boy boys girl girls teen teens age ages year years old grade
cute easy simple fun funny beautiful bold big large small mini little hand drawn new best
gift gifts high quality res hd use commercial personal letter size us edition collection
design designs image images illustration illustrations graphic graphics vol volume part bonus
free sale unique perfect ideal great premium ultimate mega super lot plus extra
learning educational education practice skills fine motor game games puzzle puzzles
transparent background cut cropped ready instantly sublimation party favor favors
item items listing listings shop store seller
pre pk tk dpi ppi inch inches cm mm px pixel pixels sizes format formats resolution
includes include included including contains containing featuring features
theme themed themes inspired style styled styles version versions series variety assorted
assortment various mixed mix combo value deluxe complete whole entire total
access immediate immediately now today deal deals discount off idea ideas bestseller
bestsellers bestselling popular trending top sellers selling
license licence licensed licensing resell resale plr mrr interior interiors editable
customizable custom customized personalized personalised
one two three four five six seven eight nine ten twelve fifteen twenty thirty forty fifty
hundred dozen per each only just more most all every any into about you we me also can get
make made decal decals planner planners journal journals journaling scrapbook scrapbooking
bujo notebook notebooks precropped weekly monthly daily functional grayscale greyscale
outline outlines lineart stuffer stuffers filler fillers booklet booklets printout printouts
handout handouts flashcard flashcards fits fit compatible compatibility works
""".split())
# Kept inside an n-gram ("back to school"), never at its start or end. "&" reads as "and".
CONNECTORS = frozenset({"and", "of", "the", "to", "n"})
MAX_CONNECTORS = 2  # in a row: "day of the dead"
# Generic heads that only mean something next to another word ("valentines day",
# "christmas eve", "back to school"). Alone they would pool every holiday into one
# broad theme ("day") that outranks, and overlaps, all the real ones.
# "trick" is half of "trick or treat" ("or" splits n-grams), never a theme of its own.
WEAK_WORDS = frozenset("""
day night eve time season week month world life land back first last happy dead st under
trick
""".split())

# Format vocabulary (our own fixed labels; counted, never copied).
FORMAT_RULES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(rx)) for name, rx in (
        ("pdf", r"\bpdfs?\b"),
        ("png", r"\bpngs?\b"),
        ("transparent png", r"\btransparent\b"),
        ("svg", r"\bsvgs?\b"),
        ("jpg", r"\bjpe?gs?\b"),
        ("printable", r"\bprintables?\b"),
        ("digital download", r"\b(digital|instant) downloads?\b"),
        ("bundle", r"\bbundles?\b"),
        ("zip", r"\bzip\b"),
        ("a4", r"\ba4\b"),
        ("us letter", r"\b(us letter|letter size|8 5 x 11)\b"),
        ("digital planner", r"\bdigital planners?\b"),
    )
)

# "20 pages", "100+ stickers", "set of 12", "30 pack".
_UNIT = (r"pages?|pgs?|sheets?|worksheets?|designs?|stickers?|pngs?|images?|illustrations?|"
         r"mazes?|puzzles?|cliparts?|elements?|pieces?|pcs|activities|cards?|"
         r"inserts?|templates?|trackers?|layouts?|maps?")
# "(?!st|nd|rd|th|e|ed|d)": "2nd grade worksheets" is not a count of 2, and neither is
# "5e character sheets" or "2e npc tracker" (a game edition) or "3d stickers". Group 2
# holds the words between the number and the unit.
_COUNT_RES = (
    re.compile(rf"\b(\d{{1,4}})(?!(?:st|nd|rd|th|e|ed|d)\b)\s*(?:x\s*)?((?:[a-z]+\s){{0,2}})(?:{_UNIT})\b"),
    re.compile(r"\b(?:set|pack|bundle|collection) of (\d{1,4})\b"),
    re.compile(r"\b(\d{1,4}) ?(?:pack|pk)\b"),
)
MAX_COUNT = 1000
# "ages 4-8 coloring pages", "grade 2 worksheets": an age or grade, not a count; "8.5 x 11
# planner pages": a page size.
_NOT_A_COUNT = re.compile(r"(\b(ages?|years?|yrs?|grades?|level|vol|volume|part|book|x)|\d) $")
# Between the number and the unit they make it a size or a time span, not a count:
# "1 inch hex map", "30 day habit tracker", "3 month budget tracker".
_MEASURE_WORDS = frozenset("""
in inch inches cm mm ft foot feet day days week weeks month months year years hour hours
minute minutes min mins
""".split())

ACTIVITY_RULES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(rx)) for name, rx in (
        ("maze", r"\b(mazes?|labyrinths?)\b"),
        ("word_search", r"\bword ?(search|find)(es)?\b"),
        ("tracing", r"\b(trac(e|es|ing)|handwriting)\b"),
        ("dot_to_dot", r"\b(dot ?to ?dots?|dot2dot|connect the dots)\b"),
    )
)

_SEGMENT_RE = re.compile(r"[,|/\\;:!?()\[\]{}\"+*#~•·–—]|\s-\s")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_BREAK = " , "


def stem(token: str) -> str:
    """Crude singular form, used only as a grouping key ("animals" ~ "animal")."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("xes", "ches", "shes", "sses")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def clean(text: str) -> str:
    """Lowercase, accents folded, apostrophes dropped, URLs cut, hyphens to spaces, & to and."""
    t = normalize(text or "")
    t = URL_RE.sub(_BREAK, t)
    t = t.replace("'", "").replace("_", " ").replace("&", " and ")
    t = re.sub(r"(?<=\w)-(?=\w)", " ", t)
    return t


def phrase(text: str) -> str:
    """Keyword/tag text as a plain lowercase phrase: "Mother's Day!" -> "mothers day"."""
    return " ".join(_TOKEN_RE.findall(clean(text)))


def _phrase_regex(phrases) -> re.Pattern | None:
    bodies = sorted({r"\s+".join(map(re.escape, p.split())) for p in phrases if p.split()},
                    key=lambda b: (-len(b), b))
    if not bodies:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(bodies) + r")(?![a-z0-9])")


def _cut(text: str, spans: list[tuple[int, int]]) -> str:
    out, pos = [], 0
    for start, end in sorted(spans):
        if start > pos:
            out.append(text[pos:start])
        out.append(_BREAK)
        pos = max(pos, end)
    out.append(text[pos:])
    return "".join(out)


@dataclass(frozen=True)
class Grams:
    keys: dict[tuple[str, ...], str]   # stemmed n-gram key -> surface form in this row
    blocked: bool                      # the text named a hard-blocked term (cut out of keys)
    flagged: bool                      # the text named a review-flagged term
    system: bool = False               # the text named a game-system term (cut, not blocked)


def system_terms(blocklist: Blocklist | None) -> dict[str, str | None]:
    """The blocklist's `!term` game-system names: {term: system id or None}.

    Blocklist.system_terms (core.compliance) holds them; an older Blocklist without the
    attribute has none. A leading "!" is dropped from the term.
    """
    terms = getattr(blocklist, "system_terms", None) if blocklist is not None else None
    if not isinstance(terms, Mapping):
        return {}
    out: dict[str, str | None] = {}
    for term, sid in terms.items():
        t = str(term).strip().lstrip("!").strip()
        if t:
            out[t] = str(sid) if sid else None
    return out


def _system_regex(term: str) -> re.Pattern | None:
    """A system name on clean() text: "D&D" also as "d and d", the words also joined
    ("dungeonsanddragons", "coc7e", "callofcthulhu"), plus a plural or an edition written
    onto the name ("pathfinder2e", "dnd5e")."""
    words = phrase(term).split()
    if not words:
        return None
    body = r"\s*".join(map(re.escape, words))
    return re.compile(rf"(?<![a-z0-9]){body}(?:s|es|\d{{1,2}}e)?(?![a-z0-9])")


# Edition markers left next to a cut system name ("d&d 5th edition", "2e"): they only point
# at a game system, so a tag or keyword phrase never keeps one.
_EDITION_RE = re.compile(
    r"(?<![a-z0-9])(?:(?:\d{1,2}(?:st|nd|rd|th)?|first|second|third|fourth|fifth|sixth|seventh|"
    r"eighth|ninth|tenth)\s+(?:edition|ed)s?|\d{1,2}e)(?![a-z0-9])")
# Words a cut can leave dangling at either end of a phrase ("character sheet for <system>",
# "<system> compatible character sheet"); trimmed only when something was cut.
_EDGE_WORDS = frozenset("""
a an and or the for with of to in on by from at as compatible compatibility edition editions
ed version versions rules system systems ready official unofficial
""".split())


def _trim_edges(text: str) -> str:
    words = text.split()
    while words and words[0] in _EDGE_WORDS:
        words.pop(0)
    while words and words[-1] in _EDGE_WORDS:
        words.pop()
    return " ".join(words)


class TextRules:
    """Stopwords, excluded platform terms and the blocklist, applied to row text.

    keep_words are content words even when they are stopwords (a niche's audiences, such
    as "teacher" for stationery). connectors are extra words kept inside an n-gram, like
    CONNECTORS ("from" for stationery's "work from home"). Game-system terms (blocklist
    `!term`) are cut like blocked names, but they do not mark the row as blocked.
    ok_phrases are ordinary phrases in which an excluded word is no claim ("trick or treat").
    """

    def __init__(self, extra_stopwords=(), excluded_terms=(), blocklist: Blocklist | None = None,
                 keep_words=(), connectors=(), system_names: Mapping[str, str | None] | None = None,
                 ok_phrases=()):
        extra = [phrase(w) for w in extra_stopwords if phrase(w)]
        keep = {k for w in keep_words for k in (phrase(w), stem(phrase(w))) if k and " " not in k}
        self.stop_words = (STOP_WORDS | {w for w in extra if " " not in w}) - keep
        self.connectors = CONNECTORS | {c for c in map(phrase, connectors) if c and " " not in c}
        self.stop_rx = _phrase_regex([*STOP_PHRASES, *(w for w in extra if " " in w)])
        self.excluded_rx = _phrase_regex([phrase(t) for t in excluded_terms if phrase(t)])
        # Ordinary phrases holding an excluded word ("trick or treat", "sweet treats"): the
        # word is not cut inside one, and a theme or keyword that keeps the whole phrase
        # stays. The listing stage allows the same phrases (listing medical_ok_phrases).
        self.ok_rx = _phrase_regex([phrase(p) for p in ok_phrases if phrase(p)])
        self.blocklist = blocklist
        # The blocklist's `!` terms, plus any other name config/ttrpg_systems.yaml lists for
        # a system (system_names, {name: id}), so a system name never becomes a theme even
        # when the blocklist does not list it yet.
        systems = {str(k).strip(): (str(v) if v else None)
                   for k, v in (system_names or {}).items() if str(k).strip()}
        for term, sid in system_terms(blocklist).items():
            systems[term] = sid or systems.get(term)
        self.systems: list[tuple[str, str | None, re.Pattern]] = [
            (t, sid, rx) for t, sid in sorted(systems.items()) if (rx := _system_regex(t))]
        # A Blocklist may also keep its `!` terms among the hard terms; here they are
        # system names (cut, row not blocked), never licensed-property names. Only the
        # blocklist's own `!` terms count: a system_names entry never softens a hard term.
        names = {normalize(t) for t in system_terms(blocklist)}
        self.hard = [(t, rx) for t, rx in (blocklist.hard if blocklist else [])
                     if normalize(str(t).lstrip("!").strip()) not in names]

    def is_stop(self, token: str) -> bool:
        return (len(token) < 2 or any(c.isdigit() for c in token)
                or token in self.stop_words or stem(token) in self.stop_words)

    def has_excluded(self, text: str) -> bool:
        """True when `text` holds an excluded term (a platform brand or a medical claim
        word) outside the ok phrases ("trick or treat" holds no claim)."""
        return bool(self._excluded_spans(clean(text)))

    def _ok_spans(self, cleaned: str) -> list[tuple[int, int]]:
        return [m.span() for m in self.ok_rx.finditer(cleaned)] if self.ok_rx else []

    def _excluded_spans(self, cleaned: str, ok: list[tuple[int, int]] | None = None
                        ) -> list[tuple[int, int]]:
        if not self.excluded_rx:
            return []
        spans = [m.span() for m in self.excluded_rx.finditer(cleaned)]
        ok = self._ok_spans(cleaned) if ok is None else ok
        if not ok or not spans:
            return spans
        return [(s, e) for s, e in spans if not any(a <= s and e <= b for a, b in ok)]

    def _spans(self, cleaned: str, terms) -> list[tuple[int, int]]:
        return [m.span() for _, rx in terms for m in rx.finditer(cleaned)]

    def _system_spans(self, cleaned: str) -> list[tuple[int, int]]:
        return [m.span() for _, _, rx in self.systems for m in rx.finditer(cleaned)]

    def cut_systems(self, text: str) -> str:
        """`text` as a plain phrase with every game-system name cut out, and with the
        edition markers and dangling words such a cut leaves ("dnd 5e character sheet" ->
        "character sheet", "character sheet for d&d 5th edition" -> "character sheet")."""
        t = clean(text)
        spans = self._system_spans(t)
        if not spans:
            return phrase(t)
        # An app named after the system ("d&d beyond") goes with it, not half of it.
        spans += [(s, e) for s, e in self._excluded_spans(t)
                  if any(s < b and a < e for a, b in spans)]
        spans += [m.span() for m in _EDITION_RE.finditer(t)]
        return _trim_edges(phrase(_cut(t, spans)))

    def names_system(self, text: str) -> bool:
        """True when `text` names a game system (also joined or with an edition: "dnd5e")."""
        return bool(self._system_spans(clean(text)))

    def system_ids(self, text: str) -> set[str]:
        """Ids of the game systems whose names (blocklist `!` terms and system_names) appear
        in `text`; research prefers core.systems.detect_systems and falls back to this."""
        t = clean(text)
        return {sid for _, sid, rx in self.systems if sid and rx.search(t)}

    def grams(self, text: str) -> Grams:
        """Theme n-grams for one row's text, plus whether it named a blocked/flagged term
        or a game-system name."""
        t = clean(text)
        blocked = self._spans(t, self.hard)
        flagged = bool(self.blocklist and self._spans(t, self.blocklist.review))
        systems = self._system_spans(t)
        ok = self._ok_spans(t)
        spans = blocked + systems + self._excluded_spans(t, ok)
        if spans:
            t = _cut(t, spans)
        if self.stop_rx:
            t = self.stop_rx.sub(_BREAK, t)
        out: dict[tuple[str, ...], str] = {}
        for segment in _SEGMENT_RE.split(t):
            run: list[str] = []      # content words, with connectors between them
            pending: list[str] = []  # connectors waiting for the next content word
            for tok in _TOKEN_RE.findall(segment) + [""]:
                if tok and not self.is_stop(tok):
                    run += pending + [tok]
                    pending = []
                elif tok in self.connectors and run and len(pending) < MAX_CONNECTORS:
                    pending.append(tok)
                else:
                    _emit(run, out, self.connectors)
                    run, pending = [], []
        if ok:
            # An excluded word kept for an ok phrase ("trick or treat") never stands in a
            # theme without that phrase around it ("treat" alone).
            out = {k: s for k, s in out.items() if not self.has_excluded(s)}
        return Grams(out, bool(blocked), flagged, bool(systems))


def _emit(run: list[str], out: dict[tuple[str, ...], str],
          connectors: frozenset[str] = CONNECTORS) -> None:
    """Every n-gram of `run` that starts and ends on a content word, <= MAX_N content words."""
    for i, first in enumerate(run):
        if first in connectors:
            continue
        content = 0
        for j in range(i, len(run)):
            if run[j] in connectors:
                continue
            content += 1
            if content > MAX_N:
                break
            words = run[i:j + 1]
            if len(words) == 1 and stem(first) in WEAK_WORDS:
                continue
            out.setdefault(tuple(w if w in connectors else stem(w) for w in words), " ".join(words))


def formats(text: str) -> set[str]:
    t = phrase(text)
    return {name for name, rx in FORMAT_RULES if rx.search(t)}


def pack_count(text: str) -> int | None:
    """First "<n> pages/stickers/..." style count in the text, if plausible."""
    t = phrase(text)
    for rx in _COUNT_RES:
        for m in rx.finditer(t):
            n = int(m.group(1))
            between = m.group(2).split() if rx.groups > 1 else []
            if (0 < n <= MAX_COUNT and not _NOT_A_COUNT.search(t[: m.start()])
                    and not _MEASURE_WORDS.intersection(between)):
                return n
    return None


def activity_types(text: str) -> set[str]:
    t = phrase(text)
    return {name for name, rx in ACTIVITY_RULES if rx.search(t)}


def head(key: tuple[str, ...]) -> tuple[str, ...]:
    """Grouping key for keyword clusters: the last word that can stand alone as a theme
    ("valentines day" -> "valentine"), else the whole key ("day of the dead")."""
    for w in reversed(key):
        if w not in CONNECTORS and w not in WEAK_WORDS:
            return (w,)
    return key


def contains(key: tuple[str, ...], sub: tuple[str, ...]) -> bool:
    """True if `sub` is a contiguous run inside `key`."""
    n = len(sub)
    return any(key[i:i + n] == sub for i in range(len(key) - n + 1))
