"""Etsy listing rules: normalize, validate and deterministically repair a draft.

A "draft" is the copy we generate: {title, tags, hook, about, suggested_uses, alt_texts}.
Every draft (model or template) goes through normalize() -> validate() and, if anything
is still wrong after the model's retries, repair(). The listing is only written when
validate() returns no errors.

Etsy rules, verified 2026-10-15 against the Open API v3 spec that developer.etsy.com
renders (www.etsy.com/openapi/generated/oas/3.0.0.json, createDraftListing and
uploadListingImage):
- title: "valid title strings contain only letters, numbers, punctuation marks,
  mathematical symbols, whitespace characters, ™, ©, and ®.
  (regex: /[^\\p{L}\\p{Nd}\\p{P}\\p{Sm}\\p{Zs}™©®]/u) You can only use the %, :, & and +
  characters once each."
- tags: "valid tag strings contain only letters, numbers, whitespace characters, -, ',
  ™, ©, and ®. (regex: /[^\\p{L}\\p{Nd}\\p{Zs}\\-'™©®]/u)"
- image alt_text: "Max length 500 characters."
The 140-character title, 13 tags and 20 characters per tag come from the Etsy Help
Center (settings `listing:`); the API spec does not state them. The character rules and
limits are shared with the publisher's gates (core/etsy.py).

Our own stricter rules on top: no ™ © ® at all (they imply brand claims), titles start
with a letter or digit, tags are lowercase, no copy claims hand drawing, "no AI" or an
official product, and no medical or therapeutic claims (CLAUDE.md; docs/PACKS.md section 4:
treat, cure, heal, diagnose, therapy, therapeutic, clinically, medical, prescription,
symptom relief and their word forms; "ADHD-friendly" describes a layout and is fine). A
ttrpg listing may name its game system, but never in alt text (`alt_names`), and the
system's trademark owner ("Wizards of the Coast") only in the description, where the
disclaimer names it: never in the title or tags (`owner_names`), which would read as an
official product.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from functools import lru_cache

from core.compliance import URL_RE, Blocklist, find_urls, normalize, term_pattern
from core import health
from core.etsy import MARKS, TITLE_ONCE_ONLY, tag_char_ok, title_char_ok  # noqa: F401 (re-exported)

# text -> the names it holds (a game system's names, its trademark owner); see validate().
Finder = Callable[[str], list[str]]

ONCE_ONLY = TITLE_ONCE_ONLY  # Etsy: each may appear at most once in a title
ONCE_ONLY_REPLACEMENTS = {"&": " and ", "+": " and ", ":": ",", "%": " percent"}
ETSY_MARKS = MARKS
TEXT_FIELDS = ("hook", "about")
DANGLING = {"a", "an", "and", "for", "in", "of", "on", "or", "the", "to", "with"}
# Words a shopper rarely needs inside a tag; dropped first when a phrase is too long
# ("fruit digital stickers" -> "fruit stickers", not "fruit digital").
FILLER = {"pages", "page", "sheets", "printable", "digital", "pdf", "png", "instant", "download", "set", "pack"}


class DraftShapeError(ValueError):
    """The model's JSON does not have the fields and types we asked for."""


# ------------------------------------------------------------------ characters

def _starts_ok(text: str) -> bool:
    cat = unicodedata.category(text[0]) if text else ""
    return cat[:1] == "L" or cat == "Nd"


def squash(text) -> str:
    return " ".join(str(text or "").split())


def tag_key(tag: str) -> str:
    """Comparison key: 'Dot-to-Dot' and 'dot to dot' are the same tag to a shopper."""
    return re.sub(r"[\s\-']+", " ", normalize(tag)).strip()


def scrub_urls(text: str) -> str:
    return squash(URL_RE.sub(" ", text))


def truncate_words(text: str, limit: int) -> str:
    """Cut at a word boundary at or before `limit` characters."""
    text = squash(text)
    if len(text) <= limit:
        return text
    cut = text[: limit + 1]
    space = cut.rfind(" ")
    out = cut[:space] if space > limit // 2 else text[:limit]
    return out.rstrip(" ,;:-|/&+")


@lru_cache(maxsize=8)
def _phrase_checker(phrases: tuple[str, ...]) -> Blocklist:
    # Reuse the blocklist matcher: whole words, case/accent-insensitive, "hand-drawn"
    # also matches "hand drawn" and "handdrawn".
    return Blocklist(phrases)


def misleading(text: str, cfg: dict) -> list[str]:
    """Claims our copy must not make: how the product was made (Etsy Creativity
    Standards; AI-assisted designs are not "hand-drawn") or rights beyond personal use."""
    phrases = tuple(cfg.get("misleading_phrases") or ())
    return _phrase_checker(phrases).check(text).blocked if phrases and text else []


def medical(text: str, cfg: dict) -> list[str]:
    """Medical or therapeutic wording our copy must not use (CLAUDE.md compliance rules):
    the claim words in `medical_terms`, the medical topics in `health_terms` (listing_config
    reads them from config/health_terms.txt) and the health claims core.health finds
    ("reduces anxiety", "clinically proven"; docs/HEALTH_TERMS.md "Decided 2026-10-17"). Phrases in
    `medical_ok_phrases` ("trick or treat", "sweet treats") are not claims and are taken out
    before the word check. A claim phrase that already holds a found word ("treat anxiety"
    with "treat") is not listed twice."""
    terms = tuple(dict.fromkeys([*(cfg.get("medical_terms") or ()), *(cfg.get("health_terms") or ())]))
    if not text:
        return []
    norm = normalize(text)
    for ok in cfg.get("medical_ok_phrases") or ():
        rx = _phrase_checker((str(ok),)).hard[0][1]
        norm = rx.sub(" | ", norm)
    found = list(_phrase_checker(terms).check(norm).blocked) if terms else []
    for claim in health.claim_problems(norm):  # a listed word inside the claim already names it
        if not any(term_pattern(t).search(claim) for t in found):
            found.append(claim)
    # Keep the shortest spelling of a hit: "clinically", not also "clinically proven".
    inner = {t for t in found for u in found
             if u != t and term_pattern(u).search(normalize(t)) and not term_pattern(t).search(normalize(u))}
    return sorted(t for t in found if t not in inner)


def banned(text: str, cfg: dict) -> bool:
    """Misleading claims or medical wording: a part with either is never kept in a repair."""
    return bool(misleading(text, cfg) or medical(text, cfg))


# ------------------------------------------------------------------ normalize

def normalize_draft(raw) -> dict:
    """Coerce a parsed model/template draft into clean field types. Raises
    DraftShapeError when a field is missing or has the wrong type."""
    if not isinstance(raw, dict):
        raise DraftShapeError("the response must be a JSON object")
    problems = []
    for key in ("title", "hook", "about"):
        if not isinstance(raw.get(key), str):
            problems.append(f"'{key}' must be a string")
    for key in ("tags", "suggested_uses", "alt_texts"):
        value = raw.get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            problems.append(f"'{key}' must be a list of strings")
    if problems:
        raise DraftShapeError("; ".join(problems))
    return {
        "title": squash(raw["title"]),
        "tags": [clean_tag_case(t) for t in raw["tags"]],
        "hook": squash(raw["hook"]),
        "about": squash(raw["about"]),
        "suggested_uses": [squash(u) for u in raw["suggested_uses"] if squash(u)],
        "alt_texts": [squash(a) for a in raw["alt_texts"]],
    }


def clean_tag_case(tag: str) -> str:
    """Always-safe tag cleanup: lowercase, straight apostrophes, single spaces."""
    return squash(tag.replace("’", "'").lower()).strip("-' ")


# ------------------------------------------------------------------ validate

def title_errors(title: str, cfg: dict) -> list[str]:
    errs = []
    limit = int(cfg["title_max_chars"])
    if not title:
        return ["title is empty"]
    if len(title) > limit:
        errs.append(f"title is {len(title)} characters; the limit is {limit}")
    if not _starts_ok(title):
        errs.append("title must start with a letter or a number")
    bad = sorted({ch for ch in title if not title_char_ok(ch)})
    if bad:
        errs.append(f"title has characters Etsy does not allow in titles: {' '.join(bad)}")
    marks = sorted({ch for ch in title if ch in ETSY_MARKS})
    if marks:
        errs.append(f"title must not use trademark or copyright symbols: {' '.join(marks)}")
    for ch in ONCE_ONLY:
        if title.count(ch) > 1:
            errs.append(f"title uses '{ch}' {title.count(ch)} times; Etsy allows it once")
    return errs


def tag_errors(tag: str, cfg: dict) -> list[str]:
    limit = int(cfg["tag_max_chars"])
    if not tag:
        return ["a tag is empty"]
    errs = []
    if len(tag) > limit:
        errs.append(f"tag '{tag}' is {len(tag)} characters; the limit is {limit}")
    bad = sorted({ch for ch in tag if not tag_char_ok(ch) or ch in ETSY_MARKS})
    if bad:
        errs.append(f"tag '{tag}' has characters not allowed in tags: {' '.join(bad)} "
                    "(only letters, numbers, spaces, hyphens and apostrophes)")
    if tag != tag.lower():
        errs.append(f"tag '{tag}' must be lowercase")
    return errs


def alt_errors(i: int, alt: str, cfg: dict) -> list[str]:
    limit = int(cfg["alt_text_max_chars"])
    if not alt:
        return [f"alt text {i} is empty"]
    if len(alt) > limit:
        return [f"alt text {i} is {len(alt)} characters; the limit is {limit}"]
    return []


def section_errors(draft: dict, cfg: dict) -> list[str]:
    errs = []
    for key in TEXT_FIELDS:
        limit = int(cfg[f"{key}_max_chars"])
        if not draft[key]:
            errs.append(f"'{key}' is empty")
        elif len(draft[key]) > limit:
            errs.append(f"'{key}' is {len(draft[key])} characters; the limit is {limit}")
    uses = cfg["suggested_uses"]
    n = len(draft["suggested_uses"])
    if not uses["min"] <= n <= uses["max"]:
        errs.append(f"suggested_uses has {n} items; give {uses['min']} to {uses['max']}")
    for u in draft["suggested_uses"]:
        if len(u) > uses["max_chars"]:
            errs.append(f"suggested use '{u[:40]}...' is {len(u)} characters; the limit is {uses['max_chars']}")
    return errs


def validate(draft: dict, n_images: int, cfg: dict, *, alt_names: Finder | None = None,
             owner_names: Finder | None = None, absent_types: Finder | None = None) -> list[str]:
    """Every rule the final listing must meet, as messages the model can act on.
    `alt_names(text)` lists the game-system names in a text (a ttrpg item made for a
    system): those may be in the title, tags and description but never in alt text.
    `owner_names(text)` lists the system's trademark owner in a text: never in the title
    or tags (only the description's disclaimer names the owner). `absent_types(text)` lists
    the page types a text names that the pack does not include (stationery, ttrpg): never
    in the title, which would promise a page the buyer does not get."""
    errs = title_errors(draft["title"], cfg)
    missing = absent_types(draft["title"]) if absent_types else []
    if missing:
        errs.append(f"the title names page types this pack does not include ({', '.join(missing)}); name only "
                    "page types listed in page_types")
    if owner_names:
        for where, text in (("the title", draft["title"]), *((f"tag '{t}'", t) for t in draft["tags"])):
            names = owner_names(text)
            if names:
                errs.append(f"{where} names the trademark owner ({', '.join(names)}); name only the game system "
                            "there, the owner is named in the disclaimer")

    tags, count = draft["tags"], int(cfg["tags_count"])
    if len(tags) != count:
        errs.append(f"{len(tags)} tags given; exactly {count} are required")
    seen: set[str] = set()
    for t in tags:
        errs += tag_errors(t, cfg)
        k = tag_key(t)
        if k in seen:
            errs.append(f"tag '{t}' is repeated; every tag must be different")
        seen.add(k)
        if k and k == tag_key(draft["title"]):
            errs.append(f"tag '{t}' repeats the whole title; use a different phrase")

    alts = draft["alt_texts"]
    if len(alts) != n_images:
        errs.append(f"{len(alts)} alt texts given; exactly {n_images} are required (one per image, same order)")
    for i, a in enumerate(alts, 1):
        errs += alt_errors(i, a, cfg)

    errs += section_errors(draft, cfg)
    for field, text in iter_fields(draft):
        for url in find_urls(text):
            errs.append(f"{field} contains a URL or web address ({url}); remove it")
        for phrase in misleading(text, cfg):
            errs.append(f"{field} says '{phrase}'; we never claim that (how it was made, an official product "
                        "or usage rights), remove it")
        for word in medical(text, cfg):
            errs.append(f"{field} uses '{word}'; no medical or therapeutic claims or wording, describe the "
                        "layout instead")
    if alt_names:
        for i, a in enumerate(alts, 1):
            names = alt_names(a)
            if names:
                errs.append(f"alt text {i} names the game system ({', '.join(names)}); alt text describes the "
                            "photo only and never names a game system")
    return errs


def iter_fields(draft: dict):
    """(field name, text) for every string in a draft, for URL/phrase/blocklist checks."""
    yield "title", draft["title"]
    for t in draft["tags"]:
        yield "tags", t
    for key in TEXT_FIELDS:
        yield key, draft[key]
    for u in draft["suggested_uses"]:
        yield "suggested_uses", u
    for i, a in enumerate(draft["alt_texts"], 1):
        yield f"alt text {i}", a


def texts(draft: dict) -> list[str]:
    return [t for _, t in iter_fields(draft)]


# ------------------------------------------------------------------ repair

def clean_title(title: str, cfg: dict) -> str:
    """Make a title meet Etsy's character rules and length, keeping as much as possible."""
    t = scrub_urls(title)
    t = "".join(ch if title_char_ok(ch) and ch not in ETSY_MARKS else " " for ch in t)
    for ch, repl in ONCE_ONLY_REPLACEMENTS.items():
        first = t.find(ch)
        if first >= 0:
            t = t[: first + 1] + t[first + 1:].replace(ch, repl)
    t = squash(t)
    t = re.sub(r"\s+([,.;!?])", r"\1", t)
    t = re.sub(r"([,;|])(\s*\1)+", r"\1", t)
    while t and not _starts_ok(t):
        t = t[1:].lstrip()
    return truncate_words(t, int(cfg["title_max_chars"]))


def clean_tag(tag: str, cfg: dict, shorten: bool = True) -> str | None:
    """Etsy-safe version of a tag; None if nothing usable. Too-long phrases lose filler
    words (rightmost first), then words from the end, unless shorten=False."""
    t = clean_tag_case(tag)
    t = "".join(ch if tag_char_ok(ch) and ch not in ETSY_MARKS else " " for ch in t)
    words = squash(t).strip("-' ").split()
    limit = int(cfg["tag_max_chars"])
    too_long = lambda: len(" ".join(words)) > limit
    if too_long() and not shorten:
        return None
    for i in range(len(words) - 1, -1, -1):
        if too_long() and words[i] in FILLER and len(words) > 1:
            del words[i]
    while words and (too_long() or words[-1] in DANGLING):
        words.pop()
    out = " ".join(words)
    return out if len(out) >= 2 else None


def pick_tags(sources: list[str], title: str, cfg: dict, reject: Finder | None = None) -> list[str]:
    """The first `tags_count` valid, distinct tags from `sources`, in order. A tag for
    which `reject(tag)` finds something (a trademark owner's name) is skipped."""
    out, seen = [], {tag_key(title)}
    for raw in sources:
        t = clean_tag(raw, cfg)
        if not t or tag_errors(t, cfg) or banned(t, cfg) or find_urls(t) or (reject and reject(t)):
            continue
        k = tag_key(t)
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
        if len(out) == int(cfg["tags_count"]):
            break
    return out


def fit(text: str, limit: int, cfg: dict, cut: str = "sentence") -> str | None:
    """`text` without URLs, shortened to `limit` if needed; None if unusable.
    cut="sentence" ends at the last full sentence that fits, "words" falls back to a
    word boundary, "none" refuses anything too long."""
    t = scrub_urls(text)
    if not t or banned(t, cfg):
        return None
    if len(t) <= limit:
        return t
    if cut == "none":
        return None
    end = max(t.rfind(p, 0, limit) for p in (". ", "! ", "? "))
    if end > 0:
        return t[: end + 1]
    return truncate_words(t, limit) if cut == "words" else None


def repair(draft: dict, fallback: dict, tag_candidates: list[str], n_images: int, cfg: dict, *,
           alt_names: Finder | None = None, owner_names: Finder | None = None,
           absent_types: Finder | None = None) -> dict:
    """Deterministic fix-up: keep every valid part of `draft`, replace or trim the rest
    with the template `fallback` and tags from our own manifest keywords. A part with a
    misleading claim or medical wording is dropped, never edited (`banned`), and so is a
    title or tag naming the game system's trademark owner (`owner_names`) and a title
    naming a page type the pack does not include (`absent_types`)."""
    title = clean_title(draft["title"], cfg)
    if (title_errors(title, cfg) or banned(title, cfg) or (owner_names and owner_names(title))
            or (absent_types and absent_types(title))):
        title = clean_title(fallback["title"], cfg)

    out = {"title": title, "tags": pick_tags([*draft["tags"], *tag_candidates], title, cfg, reject=owner_names)}
    for key in TEXT_FIELDS:
        limit = int(cfg[f"{key}_max_chars"])
        out[key] = fit(draft[key], limit, cfg) or fit(fallback[key], limit, cfg, "words") or ""

    uses_cfg = cfg["suggested_uses"]
    uses, seen = [], set()
    for source, cap in ((draft["suggested_uses"], uses_cfg["max"]), (fallback["suggested_uses"], uses_cfg["min"])):
        for u in source:
            u = fit(u, int(uses_cfg["max_chars"]), cfg, "none")
            if len(uses) < int(cap) and u and u.lower() not in seen:
                seen.add(u.lower())
                uses.append(u)
    out["suggested_uses"] = uses

    limit = int(cfg["alt_text_max_chars"])
    alts = []
    clean_alt = (lambda t: t if t and not alt_names(t) else None) if alt_names else (lambda t: t)
    for i in range(n_images):
        mine = draft["alt_texts"][i] if i < len(draft["alt_texts"]) else ""
        alts.append(clean_alt(fit(mine, limit, cfg, "words"))
                    or clean_alt(fit(fallback["alt_texts"][i], limit, cfg, "words")) or "")
    out["alt_texts"] = alts
    return out
