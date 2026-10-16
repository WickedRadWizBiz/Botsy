"""research.run(): research exports -> data/briefs/<niche>-<date>.json.

Deterministic Python only; no model ever sees competitor text (CLAUDE.md).
Inputs in the inputs dir (default data/research_inputs/):
- market exports: any *.csv from eRank, EverBee or a similar licensed tool
  ("<niche>__name.csv" puts every row in that niche);
- own_shop*.csv: our own listings (title + views/favorites/sales), demand boost only;
- trends*.csv: off-Etsy trend signals (keyword + interest 0-100, or a Google
  Trends timeline), demand boost only.
Own-shop and trend rows never count toward min_listings_per_pattern.

Stages (research/stages.py, docs/SERVER.md section 8): the run uses the thresholds of the
discovery or refined stage. Evidence window (research/window.py, section 12): only market
exports (and trend files) received within research.window_days of the run date are used.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from core.compliance import Blocklist, normalize
from core.config import get_shop, load_settings, resolve, today
from core.items import check_date, write_json
from research import columns, history, stages, window
from research.aggregate import Row, Signal
from research.briefs import build_doc
from research.defaults import load_config
from research.guard import Guard, extend_blocklist, validate_brief_doc
from research.niches import assign_niche, compile_rules, niche_from_filename
from research.text import TextRules, activity_types, formats, pack_count, phrase

log = logging.getLogger(__name__)

_LISTING_URL_RE = re.compile(r"/listing/(\d+)")
_TAG_SPLIT_RE = re.compile(r"[,;|\n]")
TREND_INTEREST = frozenset({"interest", "score", "value", "trend", "trendscore", "index",
                            "popularity", "searchinterest", "interestovertime"})
TREND_TIME = frozenset({"week", "day", "month", "date", "time", "year"})
# A malformed or non-CSV file is skipped with a warning instead of stopping the run.
READ_ERRORS = (pd.errors.ParserError, pd.errors.EmptyDataError, csv.Error, UnicodeError)


class NoInputsError(RuntimeError):
    """No research exports to read. The CLI prints this message instead of a traceback."""


@dataclass
class InputFiles:
    market: list[Path] = field(default_factory=list)
    own_shop: list[Path] = field(default_factory=list)
    trends: list[Path] = field(default_factory=list)


@dataclass
class MarketFile:
    name: str
    tool: str
    shape: str
    frame: pd.DataFrame          # canonical columns minus identifying ones, plus _niche/_key/_counts
    has_competition: bool
    shop_names: set[str]
    titles: set[str]
    received: str = ""           # YYYY-MM-DD the export arrived (research/window.py)


def discover_inputs(inputs_dir: Path, known_niches) -> InputFiles:
    found = InputFiles()
    if not inputs_dir.is_dir():
        return found
    for p in sorted(inputs_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() != ".csv" or p.name.startswith("."):
            continue
        base = p.name.split("__", 1)[1] if niche_from_filename(p.name, known_niches) else p.name
        role = re.sub(r"[\s-]+", "_", base.lower())
        if role.startswith(("own_shop", "ownshop")):
            found.own_shop.append(p)
        elif role.startswith("trends"):
            found.trends.append(p)
        else:
            found.market.append(p)
    return found


def _key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _row_key(rec: dict, shape: str) -> str:
    """Opaque identity for dedupe across files: listing id > URL > shop+title > title+price."""
    if shape == "keyword":
        return _key("kw", phrase(rec.get("keyword", "")))
    lid = re.sub(r"\D", "", str(rec.get("listing_id", "")))
    url = str(rec.get("url", ""))
    m = _LISTING_URL_RE.search(url)
    if lid:
        return _key("id", lid)
    if m:
        return _key("id", m.group(1))
    if url.strip():
        return _key("url", url.strip().lower())
    title = phrase(rec.get("title", ""))
    if str(rec.get("shop_name", "")).strip():
        return _key("shop", normalize(str(rec["shop_name"])).strip(), title)
    return _key("title", title, rec.get("price"))


def load_market_file(path: Path, cfg: dict, niche_rules, known_niches) -> MarketFile | None:
    export = columns.load_export(path, cfg["column_overrides"], cfg["source_tool"])
    if export.shape is None:
        log.warning("%s: no title or keyword column found (headers: %s); skipped",
                    path.name, ", ".join(map(str, export.mapping.values())) or "none")
        return None
    df = export.frame.copy()
    for col in ("title", "keyword", "tags", "category", "shop_name", "url", "listing_id"):
        if col not in df:
            df[col] = ""
    file_niche = niche_from_filename(path.name, known_niches)

    def niche_of(rec) -> str | None:
        if file_niche:
            return file_niche
        main = " , ".join([rec["title"], rec["keyword"], rec["category"]])
        return assign_niche(main, niche_rules) or assign_niche(rec["tags"], niche_rules)

    records = df.to_dict("records")
    df["_niche"] = [niche_of(r) for r in records]
    df["_key"] = [_row_key(r, export.shape) for r in records]
    # Shop diversity is counted on an opaque hash; the name itself is dropped below.
    df["_shop"] = [_key("shop", normalize(str(r["shop_name"])).strip()) if str(r["shop_name"]).strip()
                   else "" for r in records]
    shop_names = {s for s in df["shop_name"].astype(str).str.strip() if s} | export.shop_names
    titles = {t for t in df["title"].astype(str).str.strip() if t}
    # Identifying columns end here: nothing below can see a shop, URL, id or image.
    df = df.drop(columns=[c for c in columns.IDENTIFYING if c in df])
    has_competition = "competition" in export.mapping
    if export.shape == "listing" and not df["_shop"].any() and cfg["min_shops_per_pattern"] > 1:
        log.warning("%s: listing export has no shop names, so its rows cannot show the %d "
                    "different shops a pattern needs. Export it with the shop name column.",
                    path.name, cfg["min_shops_per_pattern"])
    if export.shape == "keyword" and not has_competition:
        log.warning("%s: keyword export has no competition (matching listings) column, so no "
                    "row can show >= %d listings; none of its rows count. Export it with the "
                    "competition column, or add a listing-level export.",
                    path.name, cfg["min_listings_per_pattern"])
    return MarketFile(path.name, export.tool, export.shape, df, has_competition,
                      shop_names, titles)


def _num(rec: dict, col: str) -> float | None:
    v = rec.get(col)
    return None if v is None or pd.isna(v) else float(v)


def rows_for_niche(files: list[MarketFile], niche: str, threshold: int,
                   rules: TextRules, min_shops: int = 1) -> tuple[list[Row], list[dict]]:
    """Counted rows for one niche (deduped across files, first file wins) and sources."""
    rows: dict[str, Row] = {}
    sources = []
    for f in files:
        sub = f.frame[f.frame["_niche"] == niche]
        if sub.empty:
            continue
        counted = 0
        for rec in sub.to_dict("records"):
            if f.shape == "keyword":  # a search term stands for listings only via competition
                comp = _num(rec, "competition")  # None when the export has no such column
                if comp is None or comp < threshold:
                    continue
            counted += 1
            if rec["_key"] in rows:
                # The first file keeps the row's data; a newer export of the same listing
                # still makes the row fresh evidence.
                row = rows[rec["_key"]]
                if f.received and (row.received or "") < f.received:
                    row.received = f.received
                continue
            listing = f.shape == "listing"
            text = rec["title"] if listing else rec["keyword"]
            g = rules.grams(text)
            tag_texts = _TAG_SPLIT_RE.split(rec["tags"]) + ([rec["keyword"]] if listing else [])
            tags = tuple(dict.fromkeys(t for t in map(phrase, tag_texts) if t))
            rows[rec["_key"]] = Row(
                key=rec["_key"], kind=f.shape, grams=g.keys, blocked=g.blocked, flagged=g.flagged,
                formats=frozenset(formats(text)), count=pack_count(text),
                activities=frozenset(activity_types(text)),
                # Only keyword-level rows are keyword-tool phrases; a listing file's
                # keyword column is per-listing data and goes through the tag threshold.
                phrase=None if listing else (phrase(rec["keyword"]) or None), tags=tags,
                price=_num(rec, "price"), sales=_num(rec, "sales"),
                favorites=_num(rec, "favorites"), reviews=_num(rec, "reviews"),
                search_volume=_num(rec, "search_volume"), shop=rec["_shop"],
                received=f.received or None,
            )
        sources.append(_source_entry(f, len(sub), counted, threshold, min_shops))
    return list(rows.values()), sources


def _source_entry(f: MarketFile, rows: int, counted: int, threshold: int, min_shops: int = 1) -> dict:
    entry = {"tool": f.tool, "file": f.name, "shape": f.shape, "rows": rows, "counted_rows": counted}
    if f.received:
        entry["received"] = f.received
    if f.shape == "keyword":
        entry["rule"] = (
            f"keyword-level: a pattern needs >= {threshold} distinct keyword rows, each with "
            f"competition >= {threshold} listings"
            + ("" if f.has_competition else " (no competition column: no row counted)"))
    else:
        entry["rule"] = (f"listing-level: a pattern needs >= {threshold} distinct listing rows"
                         + (f" from >= {min_shops} shops" if min_shops > 1 else ""))
    return entry


def _percentile_strengths(frame: pd.DataFrame, cols) -> list[float]:
    cols = [c for c in cols if c in frame and frame[c].notna().any()]
    if not cols or frame.empty:
        return [0.5] * len(frame)
    ranks = frame[cols].fillna(0).rank(method="average", pct=True)
    return ranks.mean(axis=1).round(4).tolist()


def load_own_shop(paths: list[Path], cfg: dict, rules: TextRules, niche_rules,
                  known_niches) -> list[Signal]:
    """Our own listings: n-grams + percentile of views/favorites/sales (boost only)."""
    signals = []
    for p in paths:
        try:
            export = columns.load_export(p, cfg["column_overrides"], "own_shop")
        except READ_ERRORS as e:
            log.warning("%s: could not read (%s); skipped", p.name, e)
            continue
        if "title" not in export.frame:
            log.warning("%s: own-shop file has no title column; skipped", p.name)
            continue
        frame = export.frame
        file_niche = niche_from_filename(p.name, known_niches)
        strengths = _percentile_strengths(frame, ("views", "favorites", "sales"))
        for title, strength in zip(frame["title"].tolist(), strengths):
            niche = file_niche or assign_niche(title, niche_rules)
            signals.append(Signal(rules.grams(title).keys, float(strength), niche))
    return signals


def load_trends(paths: list[Path], cfg: dict, rules: TextRules, known_niches) -> list[Signal]:
    """keyword + interest (0-100) rows, or a Google Trends timeline (mean of last 4 points)."""
    signals = []
    for p in paths:
        try:
            raw = columns.read_raw(p, TREND_INTEREST | TREND_TIME).frame
        except READ_ERRORS as e:
            log.warning("%s: could not read (%s); skipped", p.name, e)
            continue
        file_niche = niche_from_filename(p.name, known_niches)
        headers = list(raw.columns)
        mapping = columns.map_columns(headers, cfg["column_overrides"])
        interest = next((h for h in headers if columns.norm_header(h) in TREND_INTEREST), None)
        pairs: list[tuple[str, float | None]] = []
        if "keyword" in mapping and interest:
            pairs = [(k, columns.parse_number(v))
                     for k, v in zip(raw[mapping["keyword"]], raw[interest])]
        elif headers and columns.norm_header(headers[0]) in TREND_TIME:
            for h in headers[1:]:
                vals = [columns.parse_number(v) for v in raw[h].tolist()]
                vals = [v for v in vals if v is not None][-4:]
                pairs.append((h.split(":")[0], sum(vals) / len(vals) if vals else None))
        else:
            log.warning("%s: trend file needs keyword + interest columns; skipped", p.name)
        for kw, value in pairs:
            if value is None or not str(kw).strip():
                continue
            strength = min(max(value, 0.0), 100.0) / 100.0
            signals.append(Signal(rules.grams(str(kw)).keys, strength, file_niche))
    return signals


def _read_market(path: Path, cfg: dict, niche_rules, known) -> MarketFile | None:
    try:
        return load_market_file(path, cfg, niche_rules, known)
    except READ_ERRORS as e:
        log.warning("%s: could not read (%s); skipped", path.name, e)
        return None


def _count_rows(path: Path) -> int:
    """Data rows in a boost-only file, for its sources entry (0 if unreadable)."""
    try:
        return len(columns.read_raw(path, TREND_INTEREST | TREND_TIME).frame)
    except READ_ERRORS:
        return 0


def _safe_source_names(sources: list[dict], guard: Guard) -> None:
    """A downloaded file name could carry a shop name or URL; replace it if so."""
    for s in sources:
        if not guard.ok(s["file"].rsplit(".", 1)[0]):
            s["file"] = f"input-{_key(s['file'])[:8]}.csv"


def _received(path: Path, ledger: dict[str, str]) -> str:
    try:
        return window.received_date(path, ledger).isoformat()
    except OSError:  # the file went away while we read; it counts as received today
        return today()


def _with_received(market: list[MarketFile], paths: dict[str, Path], ledger: dict[str, str]) -> None:
    for m in market:
        m.received = _received(paths[m.name], ledger)


def _in_window(market: list[MarketFile], date: str, days: int) -> list[MarketFile]:
    kept = [m for m in market if window.in_window(_dt.date.fromisoformat(m.received), date, days)]
    old = len(market) - len(kept)
    if old:
        log.warning("%d of %d research exports were received more than %d days before %s, so "
                    "they are not used (research.window_days). Fresh exports keep the evidence "
                    "current.", old, len(market), days, date)
    return kept


def _trends_in_window(paths: list[Path], ledger: dict[str, str], date: str, days: int) -> list[Path]:
    """Trend files are uploaded exports too, so an old one stops boosting demand once it
    leaves the window. Own-shop stats are not windowed: they are our own numbers, which
    the server refreshes, not market evidence that goes stale."""
    kept = [p for p in paths
            if window.in_window(_dt.date.fromisoformat(_received(p, ledger)), date, days)]
    if len(kept) < len(paths):
        log.info("%d trend file(s) were received more than %d days before %s and are not used",
                 len(paths) - len(kept), days, date)
    return kept


def run(niche: str | None = None, shop_id: str = "main", inputs_dir=None, briefs_dir=None,
        date: str | None = None, *, settings: dict | None = None,
        blocklist: Blocklist | None = None, dry_run: bool = False,
        history_dir=None, stage: str | None = None) -> list[Path]:
    """Write data/briefs/<niche>-<date>.json for each niche of the shop that has rows.

    Every document passes validate_brief_doc before anything is written, so a
    compliance failure in one niche writes nothing. dry_run builds and validates
    but writes nothing; it returns the paths that would be written.

    stage: "discovery" or "refined" (docs/SERVER.md section 8); None or "auto" resolves
    it with research.stages.resolve_stage (refined after 20 real Etsy drafts). The stage's
    thresholds apply to themes, patterns and tags; a refined brief also needs the theme
    seen in research.stages.refined.min_weeks_seen ISO weeks (this run included) and
    demand not falling, read from the history in history_dir (else settings
    research.history_dir). Only market exports received within research.window_days of
    the run date are counted (research/window.py).

    history_dir (the service passes settings research.history_dir): also write
    <history_dir>/<date>/<niche>.json for each of those niches (research/history.py,
    docs/SERVER.md section 3). Snapshots are checked like briefs before anything is
    written and are never written in a dry run. The return value is unchanged.
    """
    settings = settings if settings is not None else load_settings()
    cfg = load_config(settings)
    if stage not in (None, "auto"):
        stages.check_stage(stage)
    shop = get_shop(settings, shop_id)
    known = list(settings.get("niches") or cfg["niche_rules"])
    targets = list(shop.get("niches") or known)
    if niche:
        if niche not in known:
            raise ValueError(f"unknown niche {niche!r}; known: {', '.join(known)}")
        targets = [niche]
    date = check_date(date or today())  # goes into the file name
    inputs = Path(inputs_dir) if inputs_dir else resolve(cfg["inputs_dir"])
    out_dir = Path(briefs_dir) if briefs_dir else resolve(cfg["briefs_dir"])
    hist_dir = Path(history_dir) if history_dir else None

    found = discover_inputs(inputs, known)
    if not found.market:
        raise NoInputsError(
            f"No research exports found in {inputs}. Download a CSV export from eRank or "
            f"EverBee (keyword or listing results) into that folder, then run again.")
    if stage in (None, "auto"):
        stage = stages.resolve_stage(settings)
    th = stages.stage_thresholds(settings, stage)
    cfg = stages.apply_thresholds(cfg, th)
    threshold = cfg["min_listings_per_pattern"]
    refined = stage == "refined"
    hist_cfg = history.history_config(settings)
    read_dir = hist_dir if hist_dir is not None else resolve(hist_cfg["history_dir"])

    if blocklist is None:
        blocklist = Blocklist.load((settings.get("compliance") or {}).get("trademark_blocklist"))
    blocklist = extend_blocklist(blocklist, cfg["extra_blocked_terms"], cfg["extra_review_terms"])
    rules = TextRules(cfg["extra_stopwords"], cfg["excluded_terms"], blocklist)
    niche_rules = compile_rules({n: r for n, r in cfg["niche_rules"].items() if n in known})
    market = [m for m in (_read_market(p, cfg, niche_rules, known) for p in found.market) if m]
    if not market:
        raise NoInputsError(
            f"None of the CSVs in {inputs} could be read with a title or keyword column. Check "
            f"the export, or map its headers in settings research.column_overrides.")
    # Every export's shop names and titles guard the output, including exports too old to count.
    shop_names = set().union(*(m.shop_names for m in market)) if market else set()
    titles = set().union(*(m.titles for m in market)) if market else set()
    guard = Guard(shop_names, titles, blocklist, cfg["verbatim_title_min_words"])
    ledger = window.load_ledger(inputs)
    days = window.window_config(settings)["window_days"]
    _with_received(market, {p.name: p for p in found.market}, ledger)
    market = _in_window(market, date, days)
    trend_files = _trends_in_window(found.trends, ledger, date, days)
    own = load_own_shop(found.own_shop, cfg, rules, niche_rules, known)
    trends = load_trends(trend_files, cfg, rules, known)
    boost_rows = {p: _count_rows(p) for p in found.own_shop + trend_files}

    docs: dict[str, dict] = {}
    snapshots: dict[str, dict] = {}
    for n in targets:
        rows, sources = rows_for_niche(market, n, threshold, rules, cfg["min_shops_per_pattern"])
        if not rows:
            log.info("%s: no rows in the research exports; no brief file", n)
            continue
        sources += [{"tool": tool, "file": p.name, "rows": boost_rows[p],
                     "role": "demand boost only (not counted)"}
                    for tool, paths in (("own_shop", found.own_shop), ("trends", trend_files))
                    for p in paths]
        _safe_source_names(sources, guard)
        snap = gate = None
        if hist_dir is not None or refined:
            snap = history.build_snapshot(n, rows, sources, settings=settings, cfg=cfg, date=date,
                                          guard=guard, rules=rules, own=own, trends=trends,
                                          shop_names=shop_names, stage=stage,
                                          min_weeks_seen=th["min_weeks_seen"] if refined else 0)
            history.validate_snapshot(snap, guard)  # raises before any write, like briefs
        if refined:
            gate = stages.gate_for(n, snap, read_dir, date, th, hist_cfg["min_rows"])
        doc = build_doc(n, rows, sources, settings=settings, cfg=cfg, shop=shop, shop_id=shop_id,
                        date=date, guard=guard, rules=rules, own=own, trends=trends,
                        stage=stage, thresholds=th, gate=gate)
        validate_brief_doc(doc, shop_names, titles, guard=guard)  # raises before any write
        docs[n] = doc
        if refined:
            _log_refined(n, doc, gate, th)
        if hist_dir is not None:
            snapshots[n] = snap

    paths = []
    for n, doc in docs.items():
        path = out_dir / f"{n}-{date}.json"
        if not dry_run:
            write_json(path, doc)
        paths.append(path)
    if hist_dir is not None and not dry_run:
        for snap in snapshots.values():
            history.write_snapshot(hist_dir, snap)
    return paths


def _log_refined(niche: str, doc: dict, gate, th: dict) -> None:
    """One clear line when the refined history rules leave a niche without briefs."""
    if doc["briefs"]:
        if gate.refused:
            log.info("%s: refined stage: %d brief(s); %d theme(s) held back (%s)", niche,
                     len(doc["briefs"]), len(gate.refused),
                     "; ".join(f"{k}: {v}" for k, v in sorted(gate.refused.items())[:5]))
        return
    if not doc["patterns"]["themes"]:
        log.info("%s: refined stage: no theme has %d listings from %d shops in the recent "
                 "exports yet; no briefs this time", niche, th["min_listings"], th["min_shops"])
        return
    log.info("%s: refined stage: no brief yet. Themes need fresh exports in %d different weeks "
             "and steady demand (%s). The search list asks for those exports.", niche,
             th["min_weeks_seen"],
             "; ".join(f"{k}: {v}" for k, v in sorted(gate.refused.items())[:5]) or "none ready")
