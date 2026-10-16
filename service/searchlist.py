"""Weekly search list: which searches to export from EverBee next (docs/SERVER.md section 4).

Competitor data may only come from exports the user makes in a licensed tool (CLAUDE.md:
no scraping, no browser automation on etsy.com). So instead of fetching data, the
server tells the user, once a week, which searches would help most:
1. seed searches for each niche, repeated every week;
2. near misses from research history: themes close to the brief thresholds, with how
   far along they are ("7 of 10 listings, 2 of 3 shops so far"; in the refined research
   stage also "seen 2 of 3 weeks"). A near miss needs rows from at least 2 shops (or
   keyword-tool rows): one seller's catalog is not a lead. One that has not grown for 3
   weekly snapshots ("stalled") moves below the others;
3. rechecks: qualified themes with no fresh export rows for a while, so their evidence
   stays fresh (docs/SERVER.md section 12).
The progress text uses the active research stage's thresholds (research/stages.py).

The file is plain text with CRLF line endings, because the user opens it in Notepad on
Windows (the helper downloads it from the outbox). Every phrase passes the trademark
blocklist: blocked phrases are dropped, flagged ones are marked "(check: trademark?)".
The text is our own wording plus theme n-grams from history, which already passed the
brief guard; nothing here goes to a model.
"""
from __future__ import annotations

import copy
import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path

from core.compliance import Blocklist, find_urls, normalize
from core.config import check_date, resolve, section
from research import history, stages
from research.defaults import load_config
from research.window import iso_week
from research.guard import HARD_FLOOR, extend_blocklist

log = logging.getLogger(__name__)

# Read with core.config.section(settings, "service", DEFAULTS)["search_list"]; settings win
# (a seeds list in settings replaces that niche's default list).
DEFAULTS: dict = {
    "search_list": {
        "max_per_niche": 6,       # search lines per niche section
        "recheck_weeks": 2,       # a qualified theme not seen for this long is listed again
        # Our own generic searches. No brands: "goodnotes stickers" would be flagged
        # (?GoodNotes in config/trademark_blocklist.txt), so it is not a default.
        "seeds": {
            "coloring_pages": ["coloring pages for kids", "printable coloring pages"],
            "stickers": ["digital stickers", "digital planner stickers"],
            "activity_sheets": ["kids activity sheets", "printable mazes for kids"],
        },
        # Added after a theme name to make a search phrase ("axolotl" -> "axolotl coloring pages").
        "nouns": {
            "coloring_pages": "coloring pages",
            "stickers": "stickers",
            "activity_sheets": "activity sheets",
        },
    }
}

FLAG_NOTE = "(check: trademark?)"
LATEST_NAME = "search-list.txt"
CRLF = "\r\n"
# The outbox is group botsy, mode 2750, and the upload account (in group botsy) downloads
# from it. mkstemp creates files with mode 600, so the mode is set before the rename;
# otherwise the helper's hourly "get" can fail with "permission denied".
FILE_MODE = 0o640

HEADER = """\
What to do (about 10 minutes):
  1. Search for each line below in EverBee Product Analytics.
     Type only the words before the brackets.
  2. Click "Export CSV" and leave the file in your Downloads folder.
  3. That's it. The Botsy helper on your PC uploads it for you.
{stage_note}
What the notes in brackets mean:
  (every week)          a starter search. Export it every week.
  (7 of {t} listings...) an idea that is close. Botsy needs {t} listings from {s} different
{close}
  (recheck...)          an idea Botsy already uses. A fresh export keeps it up to date.
  (check: trademark?)   might be a brand or character name. If it is, skip it.
"""
CLOSE = {
    "discovery": "                        shops before it makes a design, and more exports get it there.",
    "refined": ("                        shops, seen in {w} different weeks, before it makes a design.\n"
                "                        More exports get it there."),
}
REFINED_NOTE = """
Botsy is in its stricter stage now: an idea must show up in exports from {w} different
weeks before it becomes a design, so repeat these searches every week.
"""
NO_HISTORY_NOTE = ("This list gets smarter after your first exports: Botsy then adds ideas\n"
                   "that are close to ready and reminds you to refresh older ones.")


@dataclass
class Line:
    phrase: str
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        return "  " + "  ".join([self.phrase, *self.notes])


def search_config(settings: dict) -> dict:
    """settings service.search_list over DEFAULTS, with max_per_niche at least 1.

    A setting left empty in settings.yaml ("max_per_niche:" with no value) means the
    default, not 1. A niche's seeds may be one phrase instead of a list.
    """
    base = DEFAULTS["search_list"]
    cfg = section(settings, "service", DEFAULTS).get("search_list")
    if not isinstance(cfg, dict):  # "search_list:" left empty
        cfg = copy.deepcopy(base)
    for key in ("max_per_niche", "recheck_weeks"):
        value = cfg.get(key)
        cfg[key] = max(1, int(base[key] if value is None else value))
    seeds = cfg.get("seeds")
    seeds = copy.deepcopy(base["seeds"]) if seeds is None else seeds
    cfg["seeds"] = {n: [v] if isinstance(v, str) else list(v or []) for n, v in seeds.items()}
    return cfg


def shop_niches(settings: dict) -> list[str]:
    """Every niche of every shop, in order (the search list is per niche, not per shop)."""
    out: list[str] = []
    for shop in settings.get("shops") or []:
        for n in shop.get("niches") or []:
            if n not in out:
                out.append(n)
    return out or list(settings.get("niches") or DEFAULTS["search_list"]["seeds"])


def load_blocklist(settings: dict) -> Blocklist:
    """The blocklist research uses: the trademark file plus research.extra_*_terms."""
    rcfg = load_config(settings)
    base = Blocklist.load((settings.get("compliance") or {}).get("trademark_blocklist"))
    return extend_blocklist(base, rcfg["extra_blocked_terms"], rcfg["extra_review_terms"])


def _title(niche: str) -> str:
    return niche.replace("_", " ").strip().upper()


def _num(entry: dict, key: str) -> int:
    value = entry.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _progress(entry: dict, thresholds: dict, weeks_seen: int | None = None) -> str:
    """"(7 of 10 listings, 2 of 3 shops so far)" for a near miss; in the refined stage
    "(7 of 15 listings, 2 of 3 shops, seen 2 of 3 weeks)".

    `thresholds` is research.stages.stage_thresholds (min_listings, min_shops,
    min_weeks_seen) or a snapshot's {"listings", "shops"}. A theme seen only in
    keyword-tool rows (eRank keyword exports) has no listings yet; "0 of 10 listings,
    0 of 3 shops" would read like an error, so it says where it was seen.
    """
    t = int(thresholds.get("min_listings") or thresholds.get("listings") or HARD_FLOOR)
    s = int(thresholds.get("min_shops") or thresholds.get("shops") or 1)
    w = int(thresholds.get("min_weeks_seen") or 0)
    rows, keyword, shops = _num(entry, "listing_rows"), _num(entry, "keyword_rows"), _num(entry, "shop_count")
    if w <= 0:
        if rows == 0 and keyword > 0:
            searches = "1 keyword search" if keyword == 1 else f"{keyword} keyword searches"
            return f"(no listings yet, seen in {searches})"
        listings = "enough listings" if rows >= t else f"{rows} of {t} listings"
        if s <= 1:
            return f"({listings} so far)"
        return f"({listings}, {min(shops, s)} of {s} shops so far)"
    weeks = f"seen {min(int(weeks_seen or 0), w)} of {w} weeks"
    if keyword >= t and not (rows >= t and shops >= s):
        return f"(enough keyword searches, {weeks})"
    if rows == 0 and keyword > 0:
        searches = "1 keyword search" if keyword == 1 else f"{keyword} keyword searches"
        return f"(no listings yet, in {searches}, {weeks})"
    if rows >= t and shops >= s:
        return f"(enough listings and shops, {weeks})"
    listings = "enough listings" if rows >= t else f"{rows} of {t} listings"
    return f"({listings}, {min(shops, s)} of {s} shops, {weeks})"


def _ready(entry: dict, thresholds: dict, weeks_seen: int) -> bool:
    """True when a theme meets the active stage's support and weeks rules (no near miss)."""
    t, s = int(thresholds["min_listings"]), int(thresholds["min_shops"])
    w = int(thresholds.get("min_weeks_seen") or 0)
    rows, keyword, shops = _num(entry, "listing_rows"), _num(entry, "keyword_rows"), _num(entry, "shop_count")
    support = (rows >= t and shops >= s) or keyword >= t
    return support and (w <= 0 or weeks_seen >= w)


def _is_lead(entry: dict) -> bool:
    """Rows from at least 2 shops, or counted keyword-tool rows (docs/SERVER.md section 12)."""
    return _num(entry, "shop_count") >= 2 or _num(entry, "keyword_rows") > 0


STALL_SNAPSHOTS = 3


def weekly(snapshots: list[dict]) -> list[dict]:
    """The newest snapshot of each ISO week, oldest first.

    Research writes a snapshot on every day it runs, including days with no new export
    (a forced re-research when an export leaves the window). "Not grown for 3 snapshots"
    is meant week to week, so a theme the user exported on Monday is not called stalled
    on Wednesday because nothing new arrived on Tuesday.
    """
    by_week: dict[tuple[int, int], dict] = {}
    for snap in snapshots:
        try:
            by_week[iso_week(snap["date"])] = snap
        except (KeyError, TypeError, ValueError):
            continue
    return [by_week[k] for k in sorted(by_week)]


def stalled(name: str, snapshots: list[dict], n: int = STALL_SNAPSHOTS) -> bool:
    """True when the theme is in each of the newest `n` of `snapshots` (niche_lines passes
    one per ISO week, see weekly()) and its listing rows, shop count and keyword rows did
    not grow from one to the next."""
    if len(snapshots) < n:
        return False
    series = []
    for snap in snapshots[-n:]:
        entry = next((e for e in snap.get("themes") or []
                      if isinstance(e, dict) and e.get("name") == name), None)
        if entry is None:
            return False
        series.append(tuple(_num(entry, k) for k in ("listing_rows", "shop_count", "keyword_rows")))
    return all(not any(b > a for a, b in zip(older, newer))
               for older, newer in zip(series, series[1:]))


def _snapshot_thresholds(snapshots: list[dict]) -> dict:
    """Discovery-style thresholds from the newest snapshot (niche_lines without a stage)."""
    th = (snapshots[-1].get("thresholds") if snapshots else None) or {}
    return {"min_listings": int(th.get("listings") or HARD_FLOOR),
            "min_shops": int(th.get("shops") or 1), "min_weeks_seen": int(th.get("weeks") or 0)}


def _weeks_ago(days: int) -> str:
    weeks = days // 7
    return "1 week ago" if weeks == 1 else f"{weeks} weeks ago"


class _Picker:
    """Builds phrases, checks each against the blocklist and drops repeats."""

    def __init__(self, blocklist: Blocklist):
        self.blocklist = blocklist
        self.seen: set[str] = set()

    def line(self, phrase: str, note: str, flagged: bool = False) -> Line | None:
        phrase = " ".join(str(phrase or "").split())
        key = normalize(phrase)
        if not phrase or key in self.seen or find_urls(phrase):
            return None
        result = self.blocklist.check(phrase)
        if result.blocked:
            return None  # a licensed name never goes on the list
        self.seen.add(key)
        notes = [note] + ([FLAG_NOTE] if flagged or result.flagged else [])
        return Line(phrase, notes)


def niche_lines(niche: str, snapshots: list[dict], tl: dict[str, dict], cfg: dict,
                blocklist: Blocklist, day: str, thresholds: dict | None = None) -> list[Line]:
    """Seeds, then near misses (best demand first, stalled ones last), then rechecks
    (stalest first).

    `thresholds` are the active stage's (research.stages.stage_thresholds); without them
    the newest snapshot's thresholds are used. A near miss is a theme in the newest
    snapshot that does not meet them yet (in the refined stage: or is seen in too few
    weeks) and has rows from 2+ shops or keyword-tool rows. When there are rechecks, one
    slot is kept for them so near misses never crowd them out for good.
    """
    pick = _Picker(blocklist)
    limit = cfg["max_per_niche"]
    th = thresholds or _snapshot_thresholds(snapshots)
    noun = (cfg.get("nouns") or {}).get(niche) or niche.replace("_", " ")
    seeds = [ln for ln in (pick.line(s, "(every week)") for s in (cfg.get("seeds") or {}).get(niche) or [])
             if ln][:limit]

    near: list[Line] = []
    if snapshots:
        newest = snapshots[-1]
        entries = [e for e in newest.get("themes") or [] if isinstance(e, dict)]
        entries.sort(key=lambda e: (-float(e.get("demand") or 0), -int(e.get("listing_rows") or 0),
                                    str(e.get("name"))))
        moving, stuck = [], []
        by_week = weekly(snapshots)
        for e in entries:
            name = e.get("name")
            if not isinstance(name, str) or not name.strip():
                continue  # a nameless entry would list the bare noun ("coloring pages")
            weeks = int((tl.get(name) or {}).get("weeks_seen") or 0)
            if _ready(e, th, weeks) or not _is_lead(e):
                continue
            ln = pick.line(f"{name} {noun}", _progress(e, th, weeks), bool(e.get("needs_review")))
            if ln:
                (stuck if stalled(name, by_week) else moving).append(ln)
        near = moving + stuck

    today = Date.fromisoformat(day)
    stale = []
    for name, t in tl.items():
        # "Seen" here means fresh export rows, not just a place in a snapshot: a theme stays
        # in snapshots for the whole evidence window after its last export.
        ref = t.get("last_fresh") or t["first_seen"]
        days = (today - Date.fromisoformat(ref)).days
        if t["weeks_qualified"] > 0 and days >= 7 * cfg["recheck_weeks"]:
            stale.append((ref, -float(t["last"].get("demand") or 0), name, days, t))
    recheck: list[Line] = []
    for _, _, name, days, t in sorted(stale):
        ln = pick.line(f"{name} {noun}", f"(recheck: last seen {_weeks_ago(days)})",
                       bool(t["last"].get("needs_review")))
        if ln:
            recheck.append(ln)

    room = limit - len(seeds)
    reserve = 1 if recheck and near and room > 1 else 0
    near = near[: max(0, room - reserve)]
    recheck = recheck[: max(0, room - len(near))]
    return seeds + near + recheck


def _footer(status: Mapping | None) -> str:
    """Last week's numbers, e.g. from the service status file."""
    def num(*keys):
        for k in keys:
            v = (status or {}).get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
        return None

    parts = [(num("exports", "exports_received"), "export", "received"),
             (num("briefs", "briefs_written"), "brief file", "written"),
             (num("items", "items_generated"), "item", "made"),
             (num("drafts", "drafts_created"), "Etsy draft", "created")]
    shown = [f"{n} {word}{'' if n == 1 else 's'} {verb}" for n, word, verb in parts if n is not None]
    if not shown:
        return "Last week: no numbers yet. They appear here after the server's first full week."
    practice = num("practice_drafts")
    if practice:
        # Dry-run mode (service.live false) makes practice drafts only; say so, or
        # "0 Etsy drafts created" looks like a fault.
        shown.append(f"{practice} practice draft{'' if practice == 1 else 's'} "
                     f"(not sent to Etsy while live mode is off)")
    return "Last week: " + ", ".join(shown) + "."


def active_thresholds(settings: dict, stage: str | None = None) -> tuple[str, dict]:
    """(stage, thresholds) for the progress text: `stage`, else the resolved
    research.stage. A bad research.stage setting falls back to discovery here (research
    itself reports the mistake), so the weekly list still goes out."""
    try:
        stage = stage if stage in stages.STAGES else stages.resolve_stage(settings)
    except Exception as e:  # noqa: BLE001 - a bad setting or an unreadable output folder
        log.warning("search list: research stage unknown (%s); using the discovery thresholds", e)
        stage = "discovery"
    return stage, stages.stage_thresholds(settings, stage)


def build(settings: dict, history_dir=None, *, date: str, status: Mapping | None = None,
          stage: str | None = None) -> str:
    """The search list text (CRLF line endings), one section per shop niche.

    history_dir defaults to settings research.history_dir. status holds last week's
    numbers for the footer: `exports`, `briefs`, `items`, `drafts` (whole numbers; the
    longer names `exports_received`, `briefs_written`, `items_generated`, `drafts_created`
    also work). Missing history is fine: the list then holds the seed searches only.
    stage ("discovery" or "refined") defaults to the resolved research.stage
    (research.stages.resolve_stage); its thresholds go into the progress notes.
    """
    day = check_date(date)
    cfg = search_config(settings)
    hist_cfg = history.history_config(settings)
    hist_dir = Path(history_dir) if history_dir else resolve(hist_cfg["history_dir"])
    blocklist = load_blocklist(settings)
    niches = shop_niches(settings)
    stage, th = active_thresholds(settings, stage)

    found = {n: history.load(hist_dir, n) for n in niches}
    any_history = any(found.values())
    d = Date.fromisoformat(day)
    pretty = f"{d:%A} {d.day} {d:%B %Y}"   # "Sunday 4 October 2026" (no leading zero)
    title = f"BOTSY WEEKLY SEARCH LIST - {pretty}"
    refined = stage == "refined"
    w = th["min_weeks_seen"]
    header = HEADER.format(t=th["min_listings"], s=th["min_shops"],
                           close=CLOSE["refined" if refined else "discovery"].format(w=w),
                           stage_note=REFINED_NOTE.format(w=w) if refined else "")
    out = [title, "=" * len(title), "", *header.splitlines()]
    if not any_history:
        out += ["", *NO_HISTORY_NOTE.splitlines()]
    for n in niches:
        tl = history.timeline(hist_dir, n, min_rows=hist_cfg["min_rows"]) if found[n] else {}
        lines = niche_lines(n, found[n], tl, cfg, blocklist, day, thresholds=th)
        out += ["", _title(n), "-" * len(_title(n))]
        out += [ln.render() for ln in lines] or ["  (Nothing to search this week.)"]
        if any_history and not found[n]:
            out.append("  (No exports for this niche yet, so only the starter searches are shown.)")
    out += ["", _footer(status)]
    return CRLF.join(out) + CRLF


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", CRLF)


def _atomic_write(path: Path, data: bytes) -> Path:
    # Temp file + rename, so the helper's hourly download never gets half a file.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, FILE_MODE)  # before the rename, so the file is never 600 in the outbox
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def write(outbox: str | Path, text: str, date: str) -> Path:
    """Save outbox/search-list-<date>.txt and outbox/search-list.txt (latest); return the latest.

    Line endings are made CRLF whatever `text` uses; the file is UTF-8.
    """
    day = check_date(date)
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    data = _crlf(text).encode("utf-8")
    _atomic_write(outbox / f"search-list-{day}.txt", data)
    return _atomic_write(outbox / LATEST_NAME, data)
