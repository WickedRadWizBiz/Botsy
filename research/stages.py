"""Research stages: discovery first, then refined (docs/SERVER.md section 8).

- `discovery` uses today's rules: research.min_listings_per_pattern (10) listings from
  research.min_shops_per_pattern (3) shops.
- `refined` starts once the shop has research.stages.refined.after_drafts (20) real Etsy
  drafts (research.stage: auto, the default), or when research.stage says so. It needs
  15 listings from 3 shops for brief support, patterns and tags, and for a BRIEF also:
  the theme seen in snapshots from 3 different ISO weeks (this run included), demand not
  falling against the newest earlier snapshot, and a stronger weight on our own sales.

The floors never move: no stage goes below CLAUDE.md's 10 listings, below 3 shops (the
user's rule), or below the discovery values in settings.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from core.config import section
from research.defaults import DEFAULTS as RESEARCH_DEFAULTS
from research.defaults import SHOP_FLOOR
from research.guard import HARD_FLOOR
from research import history
from research.history import fresh_rows
from research.window import iso_week

log = logging.getLogger(__name__)

STAGES = ("discovery", "refined")
# Read with core.config.section(settings, "research", DEFAULTS); settings win.
DEFAULTS: dict = {
    "stage": "auto",                     # auto | discovery | refined
    "stages": {
        "refined": {
            "after_drafts": 20,          # auto: refined once the shop has this many real Etsy drafts
            "min_listings": 15,
            "min_shops": 3,
            "min_weeks_seen": 3,         # distinct ISO weeks with fresh rows, this run included
            "demand_not_falling": True,
            "falling_tolerance": 0.10,   # listing rows or demand rank may drop this much
            "own_shop_boost": 0.25,      # discovery uses research.own_shop_boost (0.1)
        },
    },
}


def _cfg(settings: dict) -> dict:
    return section(settings or {}, "research", DEFAULTS)


def _refined(settings: dict) -> dict:
    """research.stages.refined over the defaults; an empty value means the default."""
    base = DEFAULTS["stages"]["refined"]
    stages = _cfg(settings).get("stages")
    given = (stages or {}).get("refined") if isinstance(stages, dict) else None
    given = given if isinstance(given, dict) else {}
    return {k: (base[k] if given.get(k) is None else given[k]) for k in base}


def _int(value, default: int) -> int:
    return int(default if value is None else value)


def _fraction(value, default: float) -> float:
    return min(1.0, max(0.0, float(default if value is None else value)))


def configured_stage(settings: dict) -> str:
    """research.stage as written: auto, discovery or refined (empty means auto)."""
    value = _cfg(settings).get("stage")
    value = "auto" if value is None or str(value).strip() == "" else str(value).strip().lower()
    if value not in ("auto", *STAGES):
        raise ValueError(f"research.stage is {value!r}; use auto, discovery or refined")
    return value


def after_drafts(settings: dict) -> int:
    return max(0, _int(_refined(settings)["after_drafts"], DEFAULTS["stages"]["refined"]["after_drafts"]))


def resolve_stage(settings: dict, *, out_root=None) -> str:
    """"discovery" or "refined" for settings research.stage.

    auto: refined once core.items.count_drafts(out_root, settings) (real Etsy drafts
    under the configured output dir, or `out_root`) reaches
    research.stages.refined.after_drafts (20).
    """
    stage = configured_stage(settings)
    if stage != "auto":
        return stage
    from core import items  # lazy: the count reads the output folder only when needed

    drafts = items.count_drafts(out_root, settings)
    return "refined" if drafts >= after_drafts(settings) else "discovery"


def check_stage(stage) -> str:
    if stage not in STAGES:
        raise ValueError(f"research stage must be discovery or refined, got {stage!r}")
    return stage


def stage_thresholds(settings: dict, stage: str) -> dict:
    """{min_listings, min_shops, min_weeks_seen, demand_not_falling, falling_tolerance,
    own_shop_boost} for `stage` ("discovery" or "refined").

    discovery: research.min_listings_per_pattern / min_shops_per_pattern / own_shop_boost
    (no history rules). refined: research.stages.refined. Both keep the floors: at least
    10 listings and 3 shops, and refined never below the discovery values.
    """
    check_stage(stage)
    cfg = section(settings or {}, "research", RESEARCH_DEFAULTS)
    listings = max(HARD_FLOOR, _int(cfg.get("min_listings_per_pattern"),
                                    RESEARCH_DEFAULTS["min_listings_per_pattern"]))
    shops = max(SHOP_FLOOR, _int(cfg.get("min_shops_per_pattern"),
                                 RESEARCH_DEFAULTS["min_shops_per_pattern"]))
    boost = _fraction(cfg.get("own_shop_boost"), RESEARCH_DEFAULTS["own_shop_boost"])
    if stage == "discovery":
        return {"min_listings": listings, "min_shops": shops, "min_weeks_seen": 0,
                "demand_not_falling": False, "falling_tolerance": 0.0, "own_shop_boost": boost}
    r = _refined(settings)
    base = DEFAULTS["stages"]["refined"]
    return {
        "min_listings": max(listings, _int(r["min_listings"], base["min_listings"])),
        "min_shops": max(shops, _int(r["min_shops"], base["min_shops"])),
        "min_weeks_seen": max(1, _int(r["min_weeks_seen"], base["min_weeks_seen"])),
        "demand_not_falling": r["demand_not_falling"] is not False,
        "falling_tolerance": _fraction(r["falling_tolerance"], base["falling_tolerance"]),
        "own_shop_boost": _fraction(r["own_shop_boost"], base["own_shop_boost"]),
    }


def apply_thresholds(cfg: dict, th: dict) -> dict:
    """The research config (research/defaults.load_config) with a stage's numbers in it:
    every support rule (themes, patterns, tags) and the demand score then use them."""
    return {**cfg, "min_listings_per_pattern": th["min_listings"],
            "min_shops_per_pattern": th["min_shops"], "own_shop_boost": th["own_shop_boost"]}


# ---------------------------------------------------------------- refined brief gate

def _number(value, default: float = 0.0) -> float:
    """A snapshot number; anything else (a hand-edited or damaged file) reads as `default`."""
    if isinstance(value, bool):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return out if math.isfinite(out) else default


def _demands(entries) -> dict[str, float]:
    return {e["name"]: _number(e.get("demand")) for e in entries or []
            if isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"]}


def _ranks(demands: dict[str, float]) -> dict[str, float]:
    """Demand rank per theme name as the share of themes with higher demand (0 = best)."""
    n = len(demands)
    return {name: sum(1 for d in demands.values() if d > mine) / n
            for name, mine in demands.items()} if n else {}


@dataclass
class HistoryGate:
    """The refined history rules for briefs: weeks seen and demand not falling.

    Built from the snapshots dated before the run (`earlier`, oldest first) and this run's
    own snapshot (`current`), so "seen in 3 weeks" counts the current run.
    """
    thresholds: dict
    min_rows: int
    week: tuple[int, int]
    weeks: dict[str, set] = field(default_factory=dict)
    previous: dict[str, dict] = field(default_factory=dict)
    previous_ranks: dict[str, float] = field(default_factory=dict)
    current_ranks: dict[str, float] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, earlier: list[dict], current: dict, thresholds: dict, min_rows: int) -> "HistoryGate":
        gate = cls(thresholds, max(1, int(min_rows)), iso_week(current["date"]))
        gate.weeks = history.weeks_by_theme(earlier, gate.min_rows)
        if earlier:
            gate.previous = {e["name"]: e for e in earlier[-1]["themes"]
                             if isinstance(e, dict) and isinstance(e.get("name"), str)}
            # Ranks compare the themes both snapshots hold: a new theme found above this
            # one (or an old one that left the window) is not this theme's demand falling.
            before, now = _demands(earlier[-1]["themes"]), _demands(current.get("themes"))
            common = before.keys() & now.keys()
            gate.previous_ranks = _ranks({k: before[k] for k in common})
            gate.current_ranks = _ranks({k: now[k] for k in common})
        return gate

    def weeks_seen(self, theme) -> int:
        weeks = set(self.weeks.get(theme.name, ()))
        if fresh_rows(theme, self.week) >= self.min_rows:
            weeks.add(self.week)
        return len(weeks)

    def trend(self, theme) -> int | None:
        """Change in listing rows since the newest earlier snapshot (None: not in it)."""
        prev = self.previous.get(theme.name)
        if prev is None:
            return None
        return theme.listing_rows - int(_number(prev.get("listing_rows")))

    def falling(self, theme) -> str | None:
        """Why the theme's demand counts as falling against the newest earlier snapshot.

        Listing rows are compared (the spec's rule), and keyword-tool rows too, but each
        kind only when it was big enough to count (min_listings) in either snapshot: a
        keyword-backed theme whose few listing rows went from 4 to 3 is not falling, nor
        is a theme that gained 20 listings while a handful of keyword rows left the window.
        """
        prev = self.previous.get(theme.name)
        if prev is None:
            return None
        tol = float(self.thresholds["falling_tolerance"])
        floor = int(self.thresholds["min_listings"])
        for kind, key, now in (("listing rows", "listing_rows", theme.listing_rows),
                               ("keyword rows", "keyword_rows", theme.keyword_rows)):
            before = int(_number(prev.get(key)))
            if max(before, now) >= floor and before > now * (1 + tol):
                return f"{kind} fell from {before} to {now}"
        a, b = self.previous_ranks.get(theme.name), self.current_ranks.get(theme.name)
        if a is not None and b is not None and b - a > tol:
            return f"demand rank fell ({a:.0%} -> {b:.0%} of themes ahead of it)"
        return None

    def check(self, theme) -> str | None:
        """Why a refined brief may not use `theme` (None = it may)."""
        need = int(self.thresholds["min_weeks_seen"])
        seen = self.weeks_seen(theme)
        if seen < need:
            reason = f"seen in {seen} of {need} weeks"
        elif self.thresholds.get("demand_not_falling"):
            reason = self.falling(theme)
        else:
            reason = None
        if reason:
            self.refused[theme.name] = reason
        return reason

    def evidence(self, theme) -> dict:
        return {"weeks_seen": self.weeks_seen(theme), "trend": self.trend(theme)}


def gate_for(niche: str, current: dict, history_dir: Path, date: str, thresholds: dict,
             min_rows: int) -> HistoryGate:
    """The refined gate for one niche: snapshots in `history_dir` dated before `date`."""
    earlier = [s for s in history.load(history_dir, niche) if s["date"] < date]
    return HistoryGate.build(earlier, current, thresholds, min_rows)
