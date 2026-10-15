"""research.run(): research exports -> data/briefs/<niche>-<date>.json.

Deterministic Python only; no model ever sees competitor text (CLAUDE.md).
Inputs in the inputs dir (default data/research_inputs/):
- market exports: any *.csv from eRank, EverBee or a similar licensed tool
  ("<niche>__name.csv" puts every row in that niche);
- own_shop*.csv: our own listings (title + views/favorites/sales), demand boost only;
- trends*.csv: off-Etsy trend signals (keyword + interest 0-100, or a Google
  Trends timeline), demand boost only.
Own-shop and trend rows never count toward min_listings_per_pattern.
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from core.compliance import Blocklist, normalize
from core.config import get_shop, load_settings, resolve, today
from core.items import write_json
from research import columns
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
    shop_names = {s for s in df["shop_name"].astype(str).str.strip() if s} | export.shop_names
    titles = {t for t in df["title"].astype(str).str.strip() if t}
    # Identifying columns end here: nothing below can see a shop, URL, id or image.
    df = df.drop(columns=[c for c in columns.IDENTIFYING if c in df])
    return MarketFile(path.name, export.tool, export.shape, df, "competition" in export.mapping,
                      shop_names, titles)


def _num(rec: dict, col: str) -> float | None:
    v = rec.get(col)
    return None if v is None or pd.isna(v) else float(v)


def rows_for_niche(files: list[MarketFile], niche: str, threshold: int,
                   rules: TextRules) -> tuple[list[Row], list[dict]]:
    """Counted rows for one niche (deduped across files, first file wins) and sources."""
    rows: dict[str, Row] = {}
    sources = []
    for f in files:
        sub = f.frame[f.frame["_niche"] == niche]
        if sub.empty:
            continue
        counted = 0
        for rec in sub.to_dict("records"):
            if f.shape == "keyword" and f.has_competition:
                comp = _num(rec, "competition")
                if comp is None or comp < threshold:
                    continue
            counted += 1
            if rec["_key"] in rows:
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
                search_volume=_num(rec, "search_volume"),
            )
        sources.append(_source_entry(f, len(sub), counted, threshold))
    return list(rows.values()), sources


def _source_entry(f: MarketFile, rows: int, counted: int, threshold: int) -> dict:
    entry = {"tool": f.tool, "file": f.name, "shape": f.shape, "rows": rows, "counted_rows": counted}
    if f.shape == "keyword":
        entry["rule"] = (
            f"keyword-level: a pattern needs >= {threshold} distinct keyword rows"
            + (f", each with competition >= {threshold} listings" if f.has_competition
               else " (no competition column)"))
    else:
        entry["rule"] = f"listing-level: a pattern needs >= {threshold} distinct listing rows"
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


def run(niche: str | None = None, shop_id: str = "main", inputs_dir=None, briefs_dir=None,
        date: str | None = None, *, settings: dict | None = None,
        blocklist: Blocklist | None = None, dry_run: bool = False) -> list[Path]:
    """Write data/briefs/<niche>-<date>.json for each niche of the shop that has rows.

    Every document passes validate_brief_doc before anything is written, so a
    compliance failure in one niche writes nothing. dry_run builds and validates
    but writes nothing; it returns the paths that would be written.
    """
    settings = settings if settings is not None else load_settings()
    cfg = load_config(settings)
    threshold = cfg["min_listings_per_pattern"]
    shop = get_shop(settings, shop_id)
    known = list(settings.get("niches") or cfg["niche_rules"])
    targets = list(shop.get("niches") or known)
    if niche:
        if niche not in known:
            raise ValueError(f"unknown niche {niche!r}; known: {', '.join(known)}")
        targets = [niche]
    date = date or today()
    # Python 3.11+ fromisoformat also takes "20261015" and "2026-W42-4"; names need YYYY-MM-DD.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date)):
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
    datetime.date.fromisoformat(date)  # ValueError on impossible dates (2026-02-30)
    inputs = Path(inputs_dir) if inputs_dir else resolve(cfg["inputs_dir"])
    out_dir = Path(briefs_dir) if briefs_dir else resolve(cfg["briefs_dir"])

    found = discover_inputs(inputs, known)
    if not found.market:
        raise NoInputsError(
            f"No research exports found in {inputs}. Download a CSV export from eRank or "
            f"EverBee (keyword or listing results) into that folder, then run again.")

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
    shop_names = set().union(*(m.shop_names for m in market)) if market else set()
    titles = set().union(*(m.titles for m in market)) if market else set()
    guard = Guard(shop_names, titles, blocklist, cfg["verbatim_title_min_words"])
    own = load_own_shop(found.own_shop, cfg, rules, niche_rules, known)
    trends = load_trends(found.trends, cfg, rules, known)
    boost_rows = {p: _count_rows(p) for p in found.own_shop + found.trends}

    docs: dict[str, dict] = {}
    for n in targets:
        rows, sources = rows_for_niche(market, n, threshold, rules)
        if not rows:
            log.info("%s: no rows in the research exports; no brief file", n)
            continue
        sources += [{"tool": tool, "file": p.name, "rows": boost_rows[p],
                     "role": "demand boost only (not counted)"}
                    for tool, paths in (("own_shop", found.own_shop), ("trends", found.trends))
                    for p in paths]
        _safe_source_names(sources, guard)
        doc = build_doc(n, rows, sources, settings=settings, cfg=cfg, shop=shop, shop_id=shop_id,
                        date=date, guard=guard, rules=rules, own=own, trends=trends)
        validate_brief_doc(doc, shop_names, titles, guard=guard)  # raises before any write
        docs[n] = doc

    paths = []
    for n, doc in docs.items():
        path = out_dir / f"{n}-{date}.json"
        if not dry_run:
            write_json(path, doc)
        paths.append(path)
    return paths
