"""Pack data model: settings, pack sizes, the fresh policy, ids (docs/PACKS.md section 3).

A brief becomes one pack per size in brief["pack_sizes"] (or [page_count] for an older
brief). The largest size is the theme's master set and always gets new pages; a smaller
size is a curated subset of a master set ("derived"), except every fresh_every-th pack of
that size, which gets new pages of its own ("fresh"). Ids are deterministic, so a rerun of
the same brief on the same date lands on the same set and item folders.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from core.compliance import normalize
from core.config import section
from core.items import new_item_id

SOURCES = ("master", "fresh", "derived")

# Settings section "packs" (report: settings_yaml). Read with core.config.section().
DEFAULTS: dict = {
    # Out of every N packs of a size, the Nth gets fresh pages; the others are derived from
    # the theme's master set. The largest size of a brief is the master (always fresh).
    "fresh_every": {5: 3, 20: 6},
    "fresh_every_default": 0,              # other sizes: never forced fresh
    "max_size": {"stickers": 60, "coloring_pages": 50, "activity_sheets": 50, "stationery": 60, "ttrpg": 40},
    "state_file": "data/packs_state.json",  # counters for the configured output folder (others keep theirs in sets/)
    "min_quality": 0.5,                    # curation: pages below this are picked only when nothing else is left
    "subjects": {
        "use_model": True,                 # false: built-in subject tables only (dry runs always use the tables)
        "effort": "low",                   # Opus 5.5 always thinks; a short list needs little of it
        "refusal_fallback": True,          # server-side fallback model if the request is declined
        "extra": 6,                        # ask the model for this many more than needed (some get filtered)
        "max_chars": 80,                   # longer subjects are dropped
        "max_tokens": 8000,
    },
    "dry_run_fake_long_side_px": 640,      # dry runs: FakeImageClient size, so big packs stay fast
    "preview_pages": 8,                    # page previews per item (QA's mockup grid shows up to 8)
}


def config(settings: dict) -> dict:
    return section(settings, "packs", DEFAULTS)


@dataclass(frozen=True)
class PackSpec:
    """One planned pack: `size` pages from design set `set_id`.

    source: "master" (the largest size: new pages, the theme's master set), "fresh" (new
    pages for this pack only) or "derived" (a curated subset of master set `set_id`).
    n: this pack's number among the shop's packs of this size in this niche (counter + 1);
    0 for a master, which does not count. new_set: the pack creates `set_id`.
    """

    size: int
    source: str
    set_id: str
    n: int = 0
    new_set: bool = True
    master_size: int = 0
    reason: str = ""          # why a derived size became fresh ("fresh_every", "no master set")

    @property
    def is_master(self) -> bool:
        return self.source == "master"

    @property
    def is_fresh(self) -> bool:
        """New pages generated for this pack (master or fresh)."""
        return self.source in ("master", "fresh")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> PackSpec:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def theme_key(theme) -> str:
    """The form two themes are matched in (same as pipeline.run.theme_key)."""
    return "-".join(re.findall(r"[a-z0-9]+", normalize(str(theme or "")).replace("'", "")))


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if n == value or isinstance(value, str) else None


def max_size(settings: dict, niche: str, fallback: int | None = None) -> int | None:
    """packs.max_size.<niche>, else `fallback` (the niche's own cap)."""
    caps = config(settings).get("max_size") or {}
    cap = _int(caps.get(niche)) if isinstance(caps, dict) else None
    return cap if cap and cap > 0 else fallback


def pack_sizes(brief: dict, *, default: int, cap: int | None = None) -> list[int]:
    """The brief's pack sizes, smallest first: brief["pack_sizes"], else [page_count], else
    [default]. Raises ValueError for a size outside 1..cap or a pack_sizes that is not a
    list of whole numbers (a hand-edited brief must not become a 0- or 500-page pack)."""
    raw = brief.get("pack_sizes")
    if raw not in (None, [], ""):
        if not isinstance(raw, (list, tuple)):
            raise ValueError(f"brief pack_sizes {raw!r} is not a list of page counts")
        sizes = []
        for v in raw:
            n = _int(v)
            if n is None:
                raise ValueError(f"brief pack_sizes entry {v!r} is not a whole number")
            sizes.append(n)
    else:
        asked = brief.get("page_count")
        if asked in (None, ""):
            sizes = [int(default)]
        else:
            n = _int(asked)
            if n is None:
                raise ValueError(f"brief page_count {asked!r} is not a whole number")
            sizes = [n]
    sizes = sorted(set(sizes))
    for n in sizes:
        if n < 1 or (cap is not None and n > cap):
            raise ValueError(f"page_count {n} outside 1..{cap}" if cap else f"page_count {n} below 1")
    return sizes


def fresh_every(settings: dict, size: int) -> int:
    """How often a pack of `size` is forced fresh (0 = never). Keys may be ints or strings
    ("5" in a hand-written YAML); a settings key overrides the default for the same size."""
    cfg = config(settings)
    table = cfg.get("fresh_every") or {}
    value = None
    if isinstance(table, dict):
        for k, v in table.items():          # defaults first, then settings: the last match wins
            if _int(k) == size:
                value = v
    if value is None:
        value = cfg.get("fresh_every_default", 0)
    n = _int(value)
    return n if n and n > 0 else 0


def forced_fresh(settings: dict, size: int, n: int) -> bool:
    """True when pack number n (1-based) of this size must be fresh: every fresh_every-th."""
    every = fresh_every(settings, size)
    return bool(every) and n % every == 0


def set_id_for(date: str, niche: str, theme: str, brief_id: str, size: int, source: str, n: int) -> str:
    """<date>-<niche>-<theme-slug>-<4 hex>, deterministic for the pack it is made for."""
    return new_item_id(date, niche, theme, salt=f"set|{brief_id}|{size}-{source}-{n}")


def item_id_for(date: str, niche: str, theme: str, brief_id: str, spec: PackSpec) -> str:
    """The pack's item id: new_item_id(date, niche, theme, salt=<brief_id>:<size>-<source>-<n>)."""
    return new_item_id(date, niche, theme, salt=f"{brief_id}:{spec.size}-{spec.source}-{spec.n}")


def cooldown_days(settings: dict) -> int:
    """pipeline.theme_cooldown_days (docs/SERVER.md section 13): how long a master set is
    reused for new derived packs. null/0 = 0 (no reuse); not a number = the default 60."""
    value = (settings.get("pipeline") or {}).get("theme_cooldown_days", 60)
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return 60
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 60
