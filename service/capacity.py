""""Botsy has room": the capacity check (docs/OVERSEER.md Part A3; docs/CONTRACTS.md capacity/v1).

    check(settings=None, paths=None, *, limits=None, planned_usd=0.0, now=None, disk_usage=None) -> dict

Run when a plan or order arrives (service/orders.py) and before each plan line is made. Pure
reads: no paid calls, no network, nothing written. Every failure has a fixed reason code:

- money_below_warn_line  projected spend (spent this month + `planned_usd`) is at or over the
                         warning line (spend.warn_at x the cap in force, $20 of $25);
- money_unknown          the cap setting or the spend ledger cannot be read;
- review_queue_full      more pending items in Botsy's review queue than limits
                         capacity.max_review_queue (30);
- drafts_waiting         more Etsy drafts waiting for you to publish than
                         capacity.max_waiting_drafts (40);
- etsy_quota_low         less than capacity.min_etsy_quota_share (30%) of Etsy's daily request
                         quota left today, or an HTTP 429 today (data/performance/etsy_quota.json);
- disk_low               less than capacity.min_disk_free_mb (2000 MB) free for data/ or output/.

Which reasons stop which order is service/orders.py's decision (money_below_warn_line stops
only scale-ups; a weekly plan is checked against the cap itself).

The result (capacity/v1):
    {"schema": "capacity/v1", "ok": bool, "reasons": [codes], "checked_at": ISO UTC,
     "facts": {"month", "spent", "cap", "money_left", "projected", "planned", "warn_line",
               "review_queue", "waiting_drafts", "waiting_drafts_source", "etsy_quota",
               "kdp_slots_left", "disk_free_mb"},
     "thresholds": {...the capacity limits used...}}
Facts that cannot be known are null (no cap, no quota file yet, no KDP queue yet). The
thresholds come from the admin-only limits file (service/limits.py); with that file missing or
broken, the shipped defaults apply (the check never gets looser because of it).

`paths` may override where things are read, as a dict: review_dir, performance_dir, kdp_queue,
disk (a list of folders). Defaults: the review queue and the performance tree of the output
Botsy is working on (paths.output_dir when service.live, else pipeline.dry_run_output_dir),
data/kdp_queue.json, and the data and output folders.
"""
from __future__ import annotations

import logging
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

from core.config import load_settings, resolve, section

log = logging.getLogger("service.capacity")

SCHEMA = "capacity/v1"
REASONS = ("money_below_warn_line", "money_unknown", "review_queue_full", "drafts_waiting", "etsy_quota_low",
           "disk_low")
KDP_QUEUE_DEFAULT = "data/kdp_queue.json"
SNAPSHOT_GLOB = "etsy/????-??-??.json"


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def _work_root(settings: dict) -> Path:
    """The output tree Botsy works on: the real one when live, else the dry-run tree."""
    from core.items import dry_run_root, output_root
    from service.paths import is_live

    return output_root(settings) if is_live(settings) else dry_run_root(settings)


def _performance_dir(settings: dict) -> Path:
    from service.paths import is_live

    if is_live(settings):
        raw = (settings.get("publisher") or {}).get("performance_dir") or "data/performance"
        return resolve(raw)
    return _work_root(settings) / "performance"


def _thresholds(limits) -> dict:
    from service import limits as limits_mod

    if limits is None:
        return dict(limits_mod.DEFAULTS["capacity"])
    values = limits.values if hasattr(limits, "values") else limits
    return {**limits_mod.DEFAULTS["capacity"], **(values.get("capacity") or {})}


def _warn_at(settings: dict) -> float:
    raw = section(settings, "spend", {"warn_at": 0.8}).get("warn_at", 0.8)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or not 0 < raw <= 1:
        return 0.8
    return float(raw)


def money(settings: dict, *, planned_usd: float = 0.0, now: datetime | None = None) -> dict:
    """{"month", "spent", "cap", "money_left", "projected", "planned", "warn_line"}; cap is the cap in
    force (service/orders.py effective_cap: a raise or lower order, else spend.monthly_cap_usd).
    Raises ValueError when the cap or the ledger cannot be read."""
    from core import spend
    from service import orders

    state = spend.budget_state(settings, now=now)
    cap = orders.effective_cap(settings)
    spent = round(float(state["mtd"]), 4)
    planned = round(max(0.0, float(planned_usd or 0.0)), 4)
    out = {"month": state["month"], "spent": spent, "cap": cap, "planned": planned,
           "projected": round(spent + planned, 4), "money_left": None, "warn_line": None}
    if cap is not None:
        out["money_left"] = round(cap - spent, 4)
        out["warn_line"] = round(_warn_at(settings) * cap, 4)
    return out


def _review_queue(review_dir: Path) -> int:
    from qa.review import list_entries

    return len(list_entries(review_dir, "pending"))


def _newest_snapshot(perf: Path) -> dict | None:
    from core.items import read_json

    try:
        files = sorted(perf.glob(SNAPSHOT_GLOB))
    except OSError:
        return None
    for p in reversed(files):
        try:
            doc = read_json(p)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("schema") == "performance/v1":
            return doc
    return None


def _waiting_drafts(settings: dict, perf: Path) -> tuple[int, str]:
    """Etsy drafts waiting to be published: the newest own-stats snapshot's `drafts`, else the
    real publish records under the output folder (an upper bound: some may be live by now)."""
    snap = _newest_snapshot(perf)
    if snap is not None:
        n = snap.get("drafts")
        if isinstance(n, int) and not isinstance(n, bool) and n >= 0:
            return n, "snapshot"
        rows = snap.get("listings") or []
        return sum(1 for r in rows if isinstance(r, dict) and r.get("state") == "draft"), "snapshot"
    from core.items import count_drafts

    return count_drafts(_work_root(settings), settings), "publish_records"


def _quota(perf: Path, now: datetime) -> dict | None:
    from core.items import read_json

    p = perf / "etsy_quota.json"
    try:
        doc = read_json(p)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    limit, left = doc.get("limit_per_day"), doc.get("remaining_today")
    today = doc.get("day") == now.date().isoformat()
    share = None
    if today and isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit > 0 \
            and isinstance(left, (int, float)) and not isinstance(left, bool):
        share = round(max(0.0, float(left)) / float(limit), 4)
    limited = doc.get("rate_limited_today") if today else 0
    return {"day": doc.get("day"), "limit_per_day": limit, "remaining_today": left, "share_left": share,
            "rate_limited_today": limited if isinstance(limited, int) and not isinstance(limited, bool) else 0,
            "today": today}


def _kdp_slots(path: Path) -> int | None:
    from core.items import read_json

    try:
        doc = read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    for key in ("slots_left", "creation_slots_left"):
        v = doc.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            return v
    return None


def _existing(p: Path) -> Path:
    p = Path(p)
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def _disk_free_mb(folders, disk_usage) -> float | None:
    free = []
    for f in folders:
        try:
            free.append(disk_usage(str(_existing(Path(f)))).free / 1_000_000)
        except (OSError, AttributeError, TypeError, ValueError):
            continue
    return round(min(free), 1) if free else None


def check(settings: dict | None = None, paths=None, *, limits=None, planned_usd: float = 0.0,
          now: datetime | None = None, disk_usage=None) -> dict:
    """The capacity check (see the module docstring). Never raises for missing files.
    `disk_usage` replaces shutil.disk_usage (tests)."""
    from core.items import review_dir_for_root
    from service import limits as limits_mod

    settings = settings if settings is not None else load_settings()
    now = _utc(now)
    over = dict(paths or {}) if isinstance(paths, dict) else {}
    if limits is None:
        limits = limits_mod.load(settings)
    th = _thresholds(limits)
    reasons: list[str] = []
    facts: dict = {}

    try:
        facts.update(money(settings, planned_usd=planned_usd, now=now))
        if facts["cap"] is not None and facts["projected"] >= facts["warn_line"]:
            reasons.append("money_below_warn_line")
    except (ValueError, OSError, TypeError, KeyError) as e:
        log.warning("capacity: money unknown (%s)", type(e).__name__)
        facts.update({"month": None, "spent": None, "cap": None, "money_left": None, "projected": None,
                      "planned": planned_usd, "warn_line": None})
        reasons.append("money_unknown")

    root = _work_root(settings)
    review_dir = Path(over["review_dir"]) if over.get("review_dir") else review_dir_for_root(root, settings)
    try:
        facts["review_queue"] = _review_queue(review_dir)
    except (OSError, ValueError) as e:
        log.warning("capacity: review queue unreadable (%s)", type(e).__name__)
        facts["review_queue"] = None
    if facts["review_queue"] is not None and facts["review_queue"] > int(th["max_review_queue"]):
        reasons.append("review_queue_full")

    perf = Path(over["performance_dir"]) if over.get("performance_dir") else _performance_dir(settings)
    facts["waiting_drafts"], facts["waiting_drafts_source"] = _waiting_drafts(settings, perf)
    if facts["waiting_drafts"] > int(th["max_waiting_drafts"]):
        reasons.append("drafts_waiting")

    quota = _quota(perf, now)
    facts["etsy_quota"] = quota
    if quota and quota["today"] and ((quota["share_left"] is not None
                                      and quota["share_left"] < float(th["min_etsy_quota_share"]))
                                     or quota["rate_limited_today"] > 0):
        reasons.append("etsy_quota_low")

    kdp = Path(over["kdp_queue"]) if over.get("kdp_queue") else resolve(KDP_QUEUE_DEFAULT)
    facts["kdp_slots_left"] = _kdp_slots(kdp)

    folders = over.get("disk") or [resolve("data"), root]
    facts["disk_free_mb"] = _disk_free_mb(folders if isinstance(folders, (list, tuple)) else [folders],
                                          disk_usage or shutil.disk_usage)
    if facts["disk_free_mb"] is not None and facts["disk_free_mb"] < float(th["min_disk_free_mb"]):
        reasons.append("disk_low")

    return {"schema": SCHEMA, "ok": not reasons, "reasons": reasons, "facts": facts, "thresholds": th,
            "checked_at": now.isoformat(timespec="seconds")}


DESCRIBE = {
    "money_below_warn_line": "This month's spending, with what is planned, reaches the warning line.",
    "money_unknown": "Botsy cannot read the spending cap or the spending log.",
    "review_queue_full": "Too many items are waiting in Botsy's review queue.",
    "drafts_waiting": "Too many Etsy drafts are waiting for you to publish.",
    "etsy_quota_low": "Little of Etsy's daily request quota is left (or Etsy asked Botsy to slow down today).",
    "disk_low": "The server's disk is nearly full.",
}


__all__ = ["DESCRIBE", "REASONS", "SCHEMA", "check", "money"]
