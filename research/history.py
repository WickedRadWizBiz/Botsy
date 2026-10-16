"""Research history: one snapshot per niche per research run (docs/SERVER.md section 3).

`data/history/<YYYY-MM-DD>/<niche>.json` (schema history/v1) records which themes the
exports support today, including near misses that are not yet strong enough for a
brief. The weekly search list reads it to ask the user for the exports that would push
a near miss over the line, and a later, stricter research stage can ask "seen in 3+
weekly snapshots, demand not falling?" from `timeline()`.

Snapshots are local working state, never briefs: they are gitignored, never committed
and never passed to a model. They still follow the brief rules, because they are built
from licensed competitor exports:
- only aggregated counts and our own theme n-grams; no shop names, URLs, listing ids,
  image names or verbatim titles (the same `Guard` research runs on briefs, plus a
  stricter check on near misses for one seller's brand words, see `_brand_words`);
- themes naming a hard-blocked term are dropped; flagged themes keep needs_review.

Themes come from the same rows and the same aggregation as the brief (research/aggregate.py):
- qualified themes are exactly `find_themes()` with the brief thresholds;
- near misses are `find_themes()` run with the low bar research.history.min_rows (and
  one shop), minus every theme that already meets the brief thresholds.
Demand is then ranked once across both lists, so one snapshot has one demand scale and a
theme keeps that scale as it moves from near miss to qualified.
"""
from __future__ import annotations

import logging
import re
from datetime import date as Date
from pathlib import Path

from core.briefs import FORBIDDEN_KEYS, HARD_FLOOR, BriefComplianceError, Guard
from core.compliance import find_urls, normalize
from core.config import DATE_RE, check_date, section
from core.items import read_json, write_json
from research.aggregate import Row, Signal, Theme, find_themes, score_demand, supported
from research.text import TextRules

log = logging.getLogger(__name__)

SCHEMA = "history/v1"
# Read with core.config.section(settings, "research", DEFAULTS); settings win.
DEFAULTS: dict = {
    "history_dir": "data/history",   # the service passes this to research.run(history_dir=...)
    "history": {
        "min_rows": 3,               # a near miss needs this many clean supporting rows
        "max_themes": 200,           # per snapshot, best demand first
    },
}
_NICHE_RE = re.compile(r"[a-z0-9_]+")
_FRAGMENT_MIN_CHARS = 4             # shorter words ("cat", "ink") are too common to judge
_CHUNK_CHARS = 5                    # a shared run this long ties a word to a shop name


def history_config(settings: dict) -> dict:
    """{"history_dir", "min_rows", "max_themes"} from settings research: over DEFAULTS.

    A value left empty in settings.yaml ("min_rows:" with nothing after it) means the
    default; it must not stop research.
    """
    cfg = section(settings, "research", DEFAULTS)
    hist = cfg.get("history") or {}
    base = DEFAULTS["history"]

    def num(key: str) -> int:
        value = hist.get(key)
        return int(base[key] if value is None else value)

    return {
        "history_dir": cfg.get("history_dir") or DEFAULTS["history_dir"],
        "min_rows": max(1, num("min_rows")),
        "max_themes": max(0, num("max_themes")),
    }


# ---------------------------------------------------------------- building a snapshot

def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(text or ""))


def _brand_words(rows: list[Row], shop_names) -> set[str]:
    """Words tied to a competitor shop name that appear in rows from fewer than two shops
    ("sunnyday" when only SunnyDayPrints uses it).

    The brief guard matches whole shop names. Brief themes also need 3 shops, so a word
    only one seller uses (their brand) cannot become one. A near miss can come from a
    single shop, so for near misses these words are refused too.

    Sellers write their brand in titles in many joined forms ("ByMoonbeam", "CraftyFoxCo"
    for the shop TheCraftyFox), so a word is tied to a shop name when one holds the other
    or they share a run of `_CHUNK_CHARS` letters. This refuses some ordinary words a
    single seller uses ("handprint" next to a shop called "...Prints"); a near miss is
    only a hint for the search list, so refusing too much is the safe side.
    """
    bodies = {b for b in map(_letters, shop_names) if len(b) >= _FRAGMENT_MIN_CHARS}
    if not bodies:
        return set()
    n = _CHUNK_CHARS
    chunks = {b[i:i + n] for b in bodies for i in range(len(b) - n + 1)}

    def tied(w: str) -> bool:
        return (any(w in b or b in w for b in bodies)
                or any(w[i:i + n] in chunks for i in range(len(w) - n + 1)))

    shops: dict[str, set[str]] = {}
    for r in rows:
        for key, surface in r.grams.items():
            if len(key) == 1:
                shops.setdefault(_letters(surface), set()).update([r.shop] if r.shop else [])
    return {w for w, s in shops.items() if len(w) >= _FRAGMENT_MIN_CHARS and len(s) < 2 and tied(w)}


class _NearMissGuard(Guard):
    """The brief guard plus `_brand_words`, for the near-miss pass only.

    It is applied before themes are collapsed, so refusing "sunnyday turtle" leaves
    "turtle" as its own theme instead of losing it with the longer name.
    """

    def __init__(self, base: Guard, brand_words: set[str]):
        self.__dict__.update(base.__dict__)
        self.brand_words = brand_words

    def text_problems(self, text: str) -> list[str]:
        problems = super().text_problems(text)
        if any(_letters(w) in self.brand_words for w in normalize(text or "").split()):
            problems.append("part of a shop name")
        return problems


def snapshot_themes(rows: list[Row], cfg: dict, guard: Guard, rules: TextRules, *,
                    own: list[Signal] = (), trends: list[Signal] = (), min_rows: int = 3,
                    shop_names=()) -> list[tuple[Theme, bool]]:
    """[(theme, qualified)] for one niche, best demand first (not capped)."""
    threshold = cfg["min_listings_per_pattern"]
    min_shops = cfg["min_shops_per_pattern"]
    qualified = find_themes(rows, threshold, cfg, guard, rules)
    # Same aggregation with the low bar: every compliant theme with >= min_rows clean rows
    # of one kind (research never adds listing and keyword rows together).
    low_bar = {**cfg, "min_shops_per_pattern": 1}
    strict = _NearMissGuard(guard, _brand_words(rows, shop_names))
    candidates = find_themes(rows, max(1, int(min_rows)), low_bar, strict, rules)
    near = [t for t in candidates if not supported(t.rows, threshold, min_shops)]
    themes = qualified + near
    score_demand(themes, cfg, own, trends)  # one demand scale for the whole snapshot
    done = {id(t) for t in qualified}
    pairs = [(t, id(t) in done) for t in themes]
    return sorted(pairs, key=lambda p: (-p[0].demand, -len(p[0].rows), p[0].name))


def _entry(theme: Theme, qualified: bool, guard: Guard) -> dict | None:
    result = guard.blocklist.check(theme.name)
    if result.blocked or guard.text_problems(theme.name):
        return None  # find_themes already drops these; checked again before any write
    entry = {
        "name": theme.name,
        "listing_rows": theme.listing_rows,
        "keyword_rows": theme.keyword_rows,
        "shop_count": theme.shop_count,
        "demand": theme.demand,
        "qualified": qualified,
    }
    if theme.needs_review or result.flagged:
        entry["needs_review"] = True
    return entry


def build_snapshot(niche: str, rows: list[Row], sources: list[dict], *, settings: dict,
                   cfg: dict, date: str, guard: Guard, rules: TextRules,
                   own: list[Signal] = (), trends: list[Signal] = (), shop_names=()) -> dict:
    """The history/v1 document for one niche, from the rows research.run counted.

    `sources` are research's per-file entries; only tool, shape and row count are kept
    (a downloaded file name could carry a shop name, and history does not need it).
    """
    hist = history_config(settings)
    own = [s for s in own if s.niche in (None, niche)]
    trends = [s for s in trends if s.niche in (None, niche)]
    pairs = snapshot_themes(rows, cfg, guard, rules, own=own, trends=trends,
                            min_rows=hist["min_rows"], shop_names=shop_names)
    themes = _cap([e for e in (_entry(t, q, guard) for t, q in pairs) if e], hist["max_themes"])
    return {
        "schema": SCHEMA,
        "niche": niche,
        "date": check_date(date),
        "thresholds": {"listings": cfg["min_listings_per_pattern"],
                       "shops": cfg["min_shops_per_pattern"]},
        "sources": [{"tool": s["tool"], "shape": s["shape"], "rows": s["rows"]}
                    for s in sources if "shape" in s],
        "themes": themes,
    }


def _cap(entries: list[dict], limit: int) -> list[dict]:
    """At most `limit` entries, still best demand first, qualified themes kept first.

    A qualified theme cut by the cap would look "not seen" to timeline(), and the
    search list would then ask for a recheck of a theme research is using right now.
    """
    if len(entries) <= limit:
        return entries
    keep = sorted(range(len(entries)), key=lambda i: (not entries[i]["qualified"], i))[:limit]
    return [entries[i] for i in sorted(keep)]


# ---------------------------------------------------------------- compliance check

def _walk(obj, keys: list[str], values: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.append(str(k))
            _walk(v, keys, values)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk(v, keys, values)
    elif isinstance(obj, str):
        values.append(obj)


def snapshot_problems(doc: dict, guard: Guard) -> list[str]:
    """Reasons a snapshot may not be written (empty list = fine).

    The brief checks (core.briefs) on every string: URLs, shop names, verbatim titles,
    image names or listing ids, blocked terms; no identifying fields. `thresholds` is
    our own schema (its "shops" key is a number), so it is left out of the field check.
    Every theme marked qualified must really meet the thresholds.
    """
    problems: list[str] = []
    keys: list[str] = []
    values: list[str] = []
    _walk({k: v for k, v in doc.items() if k != "thresholds"}, keys, values)
    for k in sorted(set(keys) & FORBIDDEN_KEYS):
        problems.append(f"forbidden field {k!r}")
    for url in find_urls(doc):
        problems.append(f"url {url!r}")
    for s in values:
        for p in guard.text_problems(s):
            problems.append(f"{p} in {s[:60]!r}")
    th = doc.get("thresholds") or {}
    t, shops = th.get("listings"), th.get("shops")
    if not isinstance(t, int) or not isinstance(shops, int):
        problems.append(f"thresholds {th!r} must hold whole numbers")
        return problems
    if t < HARD_FLOOR or shops < 1:
        # "qualified" is only checked against these numbers, so they keep the brief floor.
        problems.append(f"thresholds {th!r} are below the floor ({HARD_FLOOR} listings, 1 shop)")
    for e in doc.get("themes") or []:
        if not e.get("qualified"):
            continue
        listing_ok = e.get("listing_rows", 0) >= t and e.get("shop_count", 0) >= shops
        if not (listing_ok or e.get("keyword_rows", 0) >= t):
            problems.append(f"theme {e.get('name')!r} is marked qualified below the thresholds")
    return problems


def validate_snapshot(doc: dict, guard: Guard) -> None:
    """Raise BriefComplianceError (nothing is written) if the snapshot breaks a rule."""
    problems = snapshot_problems(doc, guard)
    if problems:
        raise BriefComplianceError([f"history {doc.get('niche')}: {p}" for p in problems])


# ---------------------------------------------------------------- files

def _check_niche(niche: str) -> str:
    """Niche ids become file names; refuse anything that could leave the history folder."""
    if not isinstance(niche, str) or not _NICHE_RE.fullmatch(niche):
        raise ValueError(f"niche {niche!r} is not a plain id (a-z, 0-9, _)")
    return niche


def snapshot_path(history_dir: str | Path, date: str, niche: str) -> Path:
    return Path(history_dir) / check_date(date) / f"{_check_niche(niche)}.json"


def write_snapshot(history_dir: str | Path, doc: dict) -> Path:
    """Atomic write to <history_dir>/<date>/<niche>.json (a rerun the same day replaces it)."""
    return write_json(snapshot_path(history_dir, doc["date"], doc["niche"]), doc)


def load(history_dir: str | Path, niche: str) -> list[dict]:
    """Every readable history/v1 snapshot of `niche`, oldest first.

    Folders that are not real YYYY-MM-DD dates, unreadable files and documents whose
    schema, niche or date do not match their place are skipped with a warning. Symbolic
    links are skipped too: research only ever writes real files and folders here.
    """
    _check_niche(niche)
    root = Path(history_dir)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.is_symlink() or not d.is_dir() or not DATE_RE.fullmatch(d.name):
            continue
        try:
            check_date(d.name)
        except ValueError:
            continue
        p = d / f"{niche}.json"
        if p.is_symlink() or not p.is_file():
            continue
        try:
            doc = read_json(p)
        except (OSError, ValueError) as e:
            log.warning("history: %s could not be read (%s); skipped", p, e)
            continue
        if (not isinstance(doc, dict) or doc.get("schema") != SCHEMA
                or doc.get("niche") != niche or doc.get("date") != d.name
                or not isinstance(doc.get("themes"), list)):
            log.warning("history: %s is not a %s snapshot for %s on %s; skipped",
                        p, SCHEMA, niche, d.name)
            continue
        out.append(doc)
    return out


def _week(day: str) -> tuple[int, int]:
    iso = Date.fromisoformat(day).isocalendar()
    return iso[0], iso[1]


def timeline(history_dir: str | Path, niche: str) -> dict[str, dict]:
    """Per theme name: weeks_seen, weeks_qualified, first_seen, last_seen, last, trend.

    Weeks are ISO weeks (date.isocalendar), so two snapshots in one week count once.
    `trend` is the change in listing_rows between the niche's newest two snapshots when
    the theme is in both, else None.
    """
    snaps = load(history_dir, niche)
    out: dict[str, dict] = {}
    weeks: dict[str, set] = {}
    qualified: dict[str, set] = {}
    for snap in snaps:
        wk = _week(snap["date"])
        for e in snap["themes"]:
            name = e.get("name")
            if not isinstance(name, str) or not name:
                continue
            t = out.setdefault(name, {"first_seen": snap["date"]})
            weeks.setdefault(name, set()).add(wk)
            q = qualified.setdefault(name, set())
            if e.get("qualified"):
                q.add(wk)
            t["last_seen"] = snap["date"]
            t["last"] = dict(e)
    newest = {e.get("name"): e for e in snaps[-1]["themes"]} if snaps else {}
    previous = {e.get("name"): e for e in snaps[-2]["themes"]} if len(snaps) > 1 else {}
    for name, t in out.items():
        t["weeks_seen"] = len(weeks[name])
        t["weeks_qualified"] = len(qualified[name])
        a, b = previous.get(name), newest.get(name)
        t["trend"] = (int(b.get("listing_rows", 0)) - int(a.get("listing_rows", 0))
                      if a is not None and b is not None else None)
    return {name: {k: t[k] for k in ("weeks_seen", "weeks_qualified", "first_seen",
                                     "last_seen", "last", "trend")}
            for name, t in out.items()}
