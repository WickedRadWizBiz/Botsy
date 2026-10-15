"""Brief-document compliance check (CLAUDE.md compliance rules), shared by research,
the generators and the pipeline.

A brief may hold only aggregated patterns: no shop names, URLs, listing ids,
images or verbatim competitor titles, nothing hard-blocked by the trademark
blocklist, and every pattern backed by at least `min_listings_per_pattern`
(never below 10) distinct rows. Research's builder filters candidates with
`Guard.text_problems`; `validate_brief_doc` re-checks the finished document and
is called before every write, so a builder bug cannot leak data to disk. The
generators and the pipeline rerun `doc_problems` / `validate_brief_doc` on the
brief files they read (without shop names or titles, which need the licensed
exports only research has).

Verbatim titles: a doc string fails if it contains a whole input title of at
least `min_title_words` words (default 4). Shorter "titles" are generic phrases
("Dinosaur Coloring") that keyword tools return too, so they are not treated as
copy. Shop names match as whole phrases, tolerant of CamelCase and joined forms
("LittleLearnersCo" also matches "little learners co").
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from core.compliance import Blocklist, find_urls, normalize

HARD_FLOOR = 10          # CLAUDE.md: briefs come from patterns across >= 10 listings
MIN_SHOP_NAME_CHARS = 3  # Etsy shop names have 4-20 chars; shorter cells are junk

FORBIDDEN_KEYS = {"shop", "shops", "shop_name", "shop_names", "seller", "url", "urls", "link",
                  "links", "listing_id", "listing_ids", "image", "images", "thumbnail",
                  "thumbnails", "photo", "photos"}
_IMAGE_RE = re.compile(r"\w\.(jpe?g|png|webp|gif|heic|avif)\b", re.I)
# Etsy listing ids are 9-10 digits today; 8-digit runs are left alone (YYYYMMDD dates).
_LONG_ID_RE = re.compile(r"(?<!\d)\d{9,}(?!\d)")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class BriefComplianceError(ValueError):
    """A brief document breaks a compliance rule; nothing is written."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("brief failed compliance checks:\n  - " + "\n  - ".join(problems))


def tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize(text or "").replace("'", ""))


def shop_tokens(name: str) -> list[str]:
    """'LittleLearnersCo' -> ['little', 'learners', 'co']; 'Shop2Kids' -> ['shop', '2', 'kids']."""
    split = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", name or "")
    return tokens(split)


class Guard:
    def __init__(self, shop_names: Iterable[str] = (), titles: Iterable[str] = (),
                 blocklist: Blocklist | None = None, min_title_words: int = 4):
        self.blocklist = blocklist or Blocklist.load()
        self.min_title_words = max(2, int(min_title_words))
        bodies = set()
        for name in shop_names:
            toks = shop_tokens(str(name))
            if len("".join(toks)) >= MIN_SHOP_NAME_CHARS:
                bodies.add(r"[\W_]*".join(map(re.escape, toks)))
        self.shop_rx = (re.compile(r"(?<![a-z0-9])(?:" + "|".join(sorted(bodies, key=lambda b: (-len(b), b)))
                                   + r")(?![a-z0-9])") if bodies else None)
        self.titles = {" ".join(t) for t in map(tokens, titles) if len(t) >= self.min_title_words}

    def shop_name_in(self, text: str) -> str | None:
        if not self.shop_rx:
            return None
        m = self.shop_rx.search(normalize(text or "").replace("'", ""))
        return m.group(0) if m else None

    def title_in(self, text: str) -> bool:
        """True if `text` contains a whole input title (of min_title_words or more)."""
        if not self.titles:
            return False
        toks = tokens(text)
        n_min = self.min_title_words
        return any(" ".join(toks[i:j]) in self.titles
                   for i in range(len(toks)) for j in range(i + n_min, len(toks) + 1))

    def text_problems(self, text: str) -> list[str]:
        """Reasons `text` may not appear in a brief (empty list = fine)."""
        problems = []
        if find_urls(text):
            problems.append("url")
        if self.shop_name_in(text):
            problems.append("shop name")
        if self.title_in(text):
            problems.append("verbatim title")
        if _IMAGE_RE.search(text) or _LONG_ID_RE.search(text):
            problems.append("image or listing id")
        if self.blocklist.check(text).blocked:
            problems.append("blocked term")
        return problems

    def ok(self, text: str) -> bool:
        return not self.text_problems(text)


def _values(obj) -> Iterator[str]:
    """String values only (dict keys are our schema, checked separately)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _values(v)


def _keys(obj) -> Iterator[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _keys(v)


def doc_problems(doc: dict, guard: Guard) -> list[str]:
    problems: list[str] = []
    floor = doc.get("min_listings_per_pattern")
    if not isinstance(floor, int) or floor < HARD_FLOOR:
        problems.append(f"min_listings_per_pattern is {floor!r}; it must be >= {HARD_FLOOR}")
        floor = HARD_FLOOR
    for kind, entries in (doc.get("patterns") or {}).items():
        for e in entries:
            if not isinstance(e.get("listing_count"), int) or e["listing_count"] < floor:
                problems.append(f"patterns.{kind} entry {_label(e)!r} has listing_count "
                                f"{e.get('listing_count')!r} < {floor}")
    for b in doc.get("briefs") or []:
        n = (b.get("evidence") or {}).get("listing_count")
        if not isinstance(n, int) or n < floor:
            problems.append(f"brief {b.get('brief_id')!r} has evidence.listing_count {n!r} < {floor}")
    for k in sorted(set(_keys(doc)) & FORBIDDEN_KEYS):
        problems.append(f"forbidden field {k!r}")
    for url in find_urls(doc):
        problems.append(f"url {url!r}")
    for s in _values(doc):
        name = guard.shop_name_in(s)
        if name:
            problems.append(f"shop name {name!r} in {s[:60]!r}")
        if guard.title_in(s):
            problems.append(f"verbatim listing title in {s[:60]!r}")
        if _IMAGE_RE.search(s) or _LONG_ID_RE.search(s):
            problems.append(f"image name or listing id in {s[:60]!r}")
    # Blocklist checks each string on its own, so a term never spans two fields.
    blocked = guard.blocklist.check_obj(doc).blocked
    if blocked:
        problems.append(f"blocked terms {blocked}")
    return problems


def _label(entry: dict) -> str:
    return str(entry.get("name") or entry.get("label") or entry.get("bucket") or entry.get("band"))


def validate_brief_doc(doc: dict, shop_names: Iterable[str], titles: Iterable[str], *,
                       blocklist: Blocklist | None = None, min_title_words: int = 4,
                       guard: Guard | None = None) -> None:
    """Raise BriefComplianceError if the brief document breaks any compliance rule.

    Checks: URLs (core.compliance.find_urls), competitor shop names as whole
    phrases, verbatim input titles, image names / listing ids and forbidden
    identifying fields, counts under the threshold (patterns and briefs), and
    hard-blocked trademark terms. Pass a prebuilt `guard` to skip recompiling.
    """
    guard = guard or Guard(shop_names, titles, blocklist, min_title_words)
    problems = doc_problems(doc, guard)
    if problems:
        raise BriefComplianceError(problems)
