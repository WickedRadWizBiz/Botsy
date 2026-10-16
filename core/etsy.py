"""Etsy's own limits, in one place, for every stage that checks them.

Digital files (help.etsy.com, "How to Manage Your Digital Listings"): up to 5 files per
listing, 20 MB each (read as 20,000,000 bytes), file names of at most 70 letters, digits,
periods, underscores or hyphens, not editable after upload.
Listing text (Etsy Help Center): title up to 140 characters, up to 13 tags of at most 20
characters each. Open API v3 uploadListingImage alt_text is at most 500 characters.
Characters (Open API v3 createDraftListing, checked 2026-10-15): a title holds only letters,
numbers, punctuation, math symbols, spaces and ™©®, with %, :, & and + at most once each;
a tag holds only letters, numbers, spaces, -, ' and ™©®.

The file limits are set only here (settings.yaml no longer repeats them). Listing-text
keys in settings (listing.title_max_chars, ...) still work; these constants are their
defaults.
"""
from __future__ import annotations

import re
import unicodedata

MAX_FILES = 5
MAX_FILE_BYTES = 20_000_000
MAX_FILE_NAME_CHARS = 70
FILE_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")

TITLE_MAX_CHARS = 140
TAGS_MAX = 13
TAG_MAX_CHARS = 20
ALT_TEXT_MAX_CHARS = 500
MARKS = "™©®"
TITLE_ONCE_ONLY = "%:&+"


def valid_file_name(name: str) -> bool:
    """A digital-file name Etsy accepts: 1-70 characters of [A-Za-z0-9._-]."""
    return isinstance(name, str) and len(name) <= MAX_FILE_NAME_CHARS and bool(FILE_NAME_RE.fullmatch(name))


def title_char_ok(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] in "LP" or cat in ("Nd", "Sm", "Zs") or ch in MARKS


def tag_char_ok(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] == "L" or cat in ("Nd", "Zs") or ch in "-'" or ch in MARKS


def title_char_problems(title: str) -> list[str]:
    """Etsy's character rules for a title (not its length)."""
    bad = sorted({c for c in title if not title_char_ok(c)})
    out = [f"title has characters Etsy rejects: {''.join(bad)!r}"] if bad else []
    return out + [f"title uses {c!r} more than once" for c in TITLE_ONCE_ONLY if title.count(c) > 1]


def tag_char_problems(tag: str) -> list[str]:
    """Etsy's character rules for one tag (not its length), so no commas."""
    bad = sorted({c for c in tag if not tag_char_ok(c)})
    return [f"tag {tag!r} has characters Etsy rejects: {''.join(bad)!r}"] if bad else []
