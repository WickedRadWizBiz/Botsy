"""Spending tracker (docs/SERVER.md section 10): an estimate of what each paid API call cost.

    record(provider, model, kind, *, stage, item_id=None, input_tokens=0, output_tokens=0, images=0,
           image_size=None, settings=None, now=None) -> float
    month_to_date(settings=None, month=None) -> dict
    budget_state(settings=None, *, now=None) -> {"month", "mtd", "cap", "pct", "warn", "reached"}
    check_budget(kind, settings=None, *, now=None) -> None    (raises BudgetExceeded, LedgerUnavailable)
    entries(settings=None, *, month=None, since=None, until=None) -> list[dict]
    table(settings=None, month=None) -> str                   (plain text for `python -m service spend`)

The ledger (settings spend.ledger_file, data/spend/ledger.jsonl) is append-only JSONL, one line
per paid call. Each line goes out as a single write to a file opened for appending (no fsync),
so lines from parallel processes never interleave. A line that cannot be read (a crash mid-write,
a hand edit) is skipped with a warning (once per set of bad lines); it never stops the tracker.
With a cap set, a ledger that cannot be written pauses paid calls (LedgerUnavailable) instead
of letting spending go uncounted.

Months are calendar months in UTC. Costs come from spend.prices and are estimates (every line
says "estimated": true); the provider bills are what count. The stages call check_budget()
before each paid call and record() after it; FakeImageClient, dry runs and the template writer
never do. This module emits no events: the service turns budget_state() into spend_warning and
budget_reached.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

from core.config import ROOT, check_date, load_settings, resolve, section

log = logging.getLogger("core.spend")

KINDS = ("text", "image")
MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")

# Settings section "spend" (see the YAML block reported for config/settings.yaml).
DEFAULTS: dict = {
    "ledger_file": "data/spend/ledger.jsonl",
    "monthly_cap_usd": None,          # dollars per UTC calendar month; null or 0 = no cap
    "warn_at": 0.8,                   # share of the cap at which the service sends spend_warning
    "prices_max_age_days": 180,       # log a warning when prices.checked is older than this
    "prices": {
        "checked": "2026-10-16",      # when the prices below were last checked (YYYY-MM-DD)
        # USD per million tokens. https://platform.claude.com/docs/en/about-claude/pricing
        # (and the claude-api skill), checked 2026-10-16. usage.output_tokens includes thinking.
        # The listing stage sends no cache_control, so cache reads/writes never apply to it.
        # The other models are listed in case models.text.model changes; an unlisted model is
        # charged the highest rates here (Fable 5.1), which can only over-estimate.
        "anthropic": {
            "claude-opus-5-5": {"input_per_mtok": 4.00, "output_per_mtok": 20.00},
            "claude-opus-5": {"input_per_mtok": 5.00, "output_per_mtok": 25.00},    # possible refusal fallback
            "claude-opus-4-8": {"input_per_mtok": 5.00, "output_per_mtok": 25.00},  # possible refusal fallback
            "claude-fable-5-1": {"input_per_mtok": 10.00, "output_per_mtok": 50.00},
            "claude-sonnet-5-5": {"input_per_mtok": 2.00, "output_per_mtok": 10.00},
            "claude-haiku-5-5": {"input_per_mtok": 0.10, "output_per_mtok": 0.50},  # prompts up to 100K tokens
        },
        # https://ai.google.dev/gemini-api/docs/pricing (Gemini API paid tier, standard, not
        # batch), checked 2026-10-16. Image output is $30 per million tokens: 1,120 / 1,680 /
        # 3,780 tokens per 1K / 2K / 4K image. Text and thinking output is output_per_mtok.
        "google": {
            "gemini-nano-banana-2.1": {"input_per_mtok": 1.50, "output_per_mtok": 7.50,
                                       "per_image": {"1K": 0.0336, "2K": 0.0504, "4K": 0.1134}},
        },
    },
}


class BudgetExceeded(RuntimeError):
    """Month-to-date spend is at or over spend.monthly_cap_usd: no new paid call may start."""

    def __init__(self, message: str, *, kind: str | None = None, month: str | None = None,
                 mtd: float | None = None, cap: float | None = None):
        super().__init__(message)
        self.kind, self.month, self.mtd, self.cap = kind, month, mtd, cap


class LedgerUnavailable(RuntimeError):
    """A monthly cap is set but the ledger cannot be written, so spending could not be
    counted against it: no new paid call may start (the cap must never silently stop
    working). Not a BudgetExceeded: the cap itself is not reached."""


# --------------------------------------------------------------------------- record

def record(provider: str, model: str, kind: str, *, stage: str, item_id: str | None = None,
           input_tokens: int = 0, output_tokens: int = 0, images: int = 0, image_size: str | None = None,
           settings: dict | None = None, now: datetime | None = None) -> float:
    """Price one paid call from spend.prices, append it to the ledger and return its cost.

    `image_size` (1K / 2K / 4K) picks the per-image price; for images, output_tokens should
    hold only text and thinking tokens, since the per-image price covers the image itself.
    Raises ValueError on bad arguments and OSError when the ledger cannot be written."""
    _check_kind(kind)
    for name, value in (("provider", provider), ("model", model), ("stage", stage)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"spend.record: {name} must be a non-empty string, got {value!r}")
    counts = {"input_tokens": _count("input_tokens", input_tokens),
              "output_tokens": _count("output_tokens", output_tokens),
              "images": _count("images", images)}
    cfg = _cfg(settings)
    ts = _utc(now)
    _check_prices_date(cfg, ts)
    cost = _cost(cfg, provider, model, image_size=image_size, **counts)
    line = {"ts": ts.isoformat(timespec="seconds"), "month": ts.strftime("%Y-%m"), "provider": provider,
            "model": model, "kind": kind, "stage": stage, "item_id": None if item_id is None else str(item_id),
            **counts, "cost_usd": cost, "estimated": True}
    if image_size is not None:
        line["image_size"] = str(image_size)
    path = _ledger(cfg)
    try:
        _append(path, line)
    except OSError as e:          # with a cap set, check_budget now pauses paid calls (see LedgerUnavailable)
        _unwritable[str(path)] = f"{type(e).__name__}: {e.strerror or e}"
        raise
    _unwritable.pop(str(path), None)
    return cost


def estimate_cost(provider: str, model: str, *, input_tokens: int = 0, output_tokens: int = 0, images: int = 0,
                  image_size: str | None = None, settings: dict | None = None) -> float:
    """What record() would charge for these counts, without writing anything."""
    return _cost(_cfg(settings), provider, model, input_tokens=_count("input_tokens", input_tokens),
                 output_tokens=_count("output_tokens", output_tokens), images=_count("images", images),
                 image_size=image_size)


def _cost(cfg: dict, provider: str, model: str, *, input_tokens: int, output_tokens: int, images: int,
          image_size: str | None) -> float:
    rates = _rates(cfg, provider, model)
    cost = (input_tokens * _rate(rates, "input_per_mtok") + output_tokens * _rate(rates, "output_per_mtok")) / 1e6
    if images:
        cost += images * _image_price(rates, provider, model, image_size)
    return round(cost, 6)


def _rates(cfg: dict, provider: str, model: str) -> dict:
    """The price entry for provider/model. A dated snapshot of a listed model
    ("claude-opus-5-5-20261001", "...@20261001") uses that model's entry. Any other unknown
    model is priced at the highest rates listed for its provider, so a new or fallback model
    is never counted as free or cheap."""
    prices = cfg.get("prices")
    table = prices.get(provider) if isinstance(prices, dict) else None
    table = table if isinstance(table, dict) else {}
    entry = table.get(model)
    if not isinstance(entry, dict):
        entry = _snapshot_entry(table, model)
    if isinstance(entry, dict):
        if entry.get("placeholder"):
            _warn_once(("placeholder", provider, model), "spend.prices for %s %s is a placeholder, not a "
                       "checked price; costs for it are rough", provider, model)
        return entry
    known = [e for e in table.values() if isinstance(e, dict)]
    if not known:
        _warn_once(("no-prices", provider), "spend.prices has no prices for %r; its calls are counted as $0",
                   provider, level=logging.ERROR)
        return {}
    _warn_once(("unknown-model", provider, model), "spend.prices has no price for %s model %r; using the "
               "highest %s prices listed", provider, model, provider)
    sizes: dict = {}
    for e in known:
        per_image = e.get("per_image")
        if isinstance(per_image, dict):
            for size in per_image:
                sizes[size] = max(sizes.get(size, 0.0), _rate(per_image, size))
        elif per_image is not None:
            sizes["*"] = max(sizes.get("*", 0.0), _rate(e, "per_image"))
    return {"input_per_mtok": max(_rate(e, "input_per_mtok") for e in known),
            "output_per_mtok": max(_rate(e, "output_per_mtok") for e in known),
            "per_image": sizes}


SNAPSHOT_RE = re.compile(r"[-@]\d{8}")


def _snapshot_entry(table: dict, model: str) -> dict | None:
    """The entry for the listed model that `model` is a dated snapshot of (longest match), or
    None. Only a date suffix counts: "claude-opus-5-7" is not a snapshot of "claude-opus-5"."""
    if not isinstance(model, str):
        return None
    name = model.removeprefix("models/")              # google-genai may name a model "models/<id>"
    if isinstance(table.get(name), dict):
        return table[name]
    best = None
    for key, entry in table.items():
        if (isinstance(entry, dict) and isinstance(key, str) and name.startswith(key)
                and SNAPSHOT_RE.match(name, len(key)) and (best is None or len(key) > len(best[0]))):
            best = (key, entry)
    return best[1] if best else None


def _rate(entry: dict, key: str) -> float:
    value = entry.get(key)
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        _warn_once(("bad-rate", key, repr(value)), "spend.prices: %s must be a dollar amount, got %r; counted "
                   "as $0", key, value, level=logging.ERROR)
        return 0.0
    return float(value)


def _image_price(rates: dict, provider: str, model: str, image_size) -> float:
    per_image = rates.get("per_image")
    if per_image is None or per_image == {}:
        _warn_once(("no-image-price", provider, model), "spend.prices has no per_image price for %s %s; "
                   "images are counted as $0", provider, model, level=logging.ERROR)
        return 0.0
    if not isinstance(per_image, dict):          # one flat price for every size
        return _rate(rates, "per_image")
    if image_size is not None:
        for key in (str(image_size), str(image_size).strip().upper()):
            if key in per_image:
                return _rate(per_image, key)
    _warn_once(("unknown-size", provider, model, image_size), "spend.prices has no %s %s price for image "
               "size %r; using the highest listed", provider, model, image_size)
    return max(_rate(per_image, key) for key in per_image)


def _check_prices_date(cfg: dict, now: datetime) -> None:
    """Prices carry the date they were checked; old or missing dates are logged once."""
    prices = cfg.get("prices")
    checked = prices.get("checked") if isinstance(prices, dict) else None
    if isinstance(checked, datetime):
        checked = checked.date()
    if not isinstance(checked, date):            # YAML reads an unquoted 2026-10-16 as a date
        try:
            checked = date.fromisoformat(check_date(checked))
        except ValueError:
            _warn_once(("prices-date", repr(checked)), "spend.prices.checked should be the date the prices "
                       "were last checked (YYYY-MM-DD), got %r", checked)
            return
    max_age = cfg.get("prices_max_age_days")
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
        max_age = DEFAULTS["prices_max_age_days"]
    age = (now.date() - checked).days
    if age > max_age:
        _warn_once(("prices-stale", checked), "spend prices were last checked on %s (%d days ago); check the "
                   "pricing pages and update spend.prices", checked.isoformat(), age)


# --------------------------------------------------------------------------- read

def entries(settings: dict | None = None, *, month: str | None = None, since: datetime | None = None,
            until: datetime | None = None) -> list[dict]:
    """Readable ledger lines, oldest first, optionally limited to a "YYYY-MM" month and to
    since <= ts < until (naive datetimes count as UTC). Unreadable lines are skipped."""
    if month is not None:
        _check_month(month)
    return _read(_ledger(_cfg(settings)), month=month, since=since, until=until)


def month_to_date(settings: dict | None = None, month: str | None = None) -> dict:
    """Spend for one UTC month (default: this one): {"month", "total", "calls", "images",
    "input_tokens", "output_tokens", "by_provider", "by_stage", "by_model", "by_item"}.
    Breakdowns map a name to dollars; by_item leaves out calls made without an item id."""
    month = month or month_of()
    return {"month": month, **summarize(entries(settings, month=month))}


def summarize(rows: list[dict]) -> dict:
    """Totals and breakdowns for a list of entries() rows (any window)."""
    out = {"total": 0.0, "calls": 0, "images": 0, "input_tokens": 0, "output_tokens": 0,
           "by_provider": {}, "by_stage": {}, "by_model": {}, "by_item": {}}
    for e in rows:
        cost = e["cost_usd"]
        out["total"] += cost
        out["calls"] += 1
        for key in ("images", "input_tokens", "output_tokens"):
            value = e.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                out[key] += value
        for key, field in (("by_provider", "provider"), ("by_stage", "stage"), ("by_model", "model")):
            name = str(e.get(field) or "unknown")
            out[key][name] = out[key].get(name, 0.0) + cost
        if e.get("item_id"):
            item = str(e["item_id"])
            out["by_item"][item] = out["by_item"].get(item, 0.0) + cost
    out["total"] = round(out["total"], 6)
    for key in ("by_provider", "by_stage", "by_model", "by_item"):
        out[key] = {k: round(v, 6) for k, v in sorted(out[key].items(), key=lambda kv: (-kv[1], kv[0]))}
    return out


def _read(path: Path, *, month: str | None = None, since: datetime | None = None,
          until: datetime | None = None) -> list[dict]:
    if _off_limits(path):
        return []
    since = _utc(since) if since is not None else None
    until = _utc(until) if until is not None else None
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    rows, bad = [], []
    # The cap is checked before every paid call, so a month's read skips lines that name
    # another month (as record() writes it) without parsing them. Lines without a month
    # field are parsed, so a damaged line is still reported.
    mark, other = (f'"month": "{month}"', '"month": "') if month is not None else (None, None)
    with f:
        for n, raw in enumerate(f, 1):
            if not raw.strip() or (mark is not None and other in raw and mark not in raw):
                continue
            parsed = _parse(raw)
            if parsed is None:
                bad.append(n)
                continue
            ts, entry = parsed
            if month is not None and entry["month"] != month:
                continue
            if (since is not None and ts < since) or (until is not None and ts >= until):
                continue
            rows.append(entry)
    if bad:     # once per set of bad lines: the cap is checked before every paid call, all day long
        shown = ", ".join(str(n) for n in bad[:5]) + (", ..." if len(bad) > 5 else "")
        _warn_once(("unreadable", str(path), len(bad), bad[0]), "spend ledger %s: skipped %d unreadable "
                   "line(s) (line %s)", path, len(bad), shown)
    return rows


def _parse(raw: str) -> tuple[datetime, dict] | None:
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(entry, dict):
        return None
    cost = entry.get("cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
        return None
    try:
        ts = _utc(datetime.fromisoformat(entry["ts"]))
    except (KeyError, TypeError, ValueError):
        return None
    month = entry.get("month")
    if not (isinstance(month, str) and MONTH_RE.fullmatch(month)):
        month = ts.strftime("%Y-%m")
    return ts, {**entry, "month": month, "cost_usd": float(cost)}


# --------------------------------------------------------------------------- budget

def budget_state(settings: dict | None = None, *, now: datetime | None = None) -> dict:
    """{"month", "mtd", "cap", "pct", "warn", "reached"} for the current UTC month.
    cap is None when there is no cap (then pct is None and warn/reached are False). pct is
    the share of the cap spent (0.8 = 80%). warn is true from warn_at of the cap on, also
    once the cap is reached. Raises ValueError when spend.monthly_cap_usd is not a number."""
    return _state(_cfg(settings), now)


def check_budget(kind: str, settings: dict | None = None, *, now: datetime | None = None) -> None:
    """Call BEFORE a paid call. Raises BudgetExceeded when this month's spend is at or over
    spend.monthly_cap_usd. With no cap the ledger is not even read.

    With a cap it fails closed: LedgerUnavailable when the ledger cannot be written (checked
    once per process, and after any failed record()), OSError when it cannot be read, and
    ValueError when the cap is not a number. Each stops the paid call, never the cap."""
    _check_kind(kind)
    cfg = _cfg(settings)
    if monthly_cap(cfg) is None:
        return
    _check_writable(_ledger(cfg))
    state = _state(cfg, now)
    if state["reached"]:
        raise BudgetExceeded(
            f"Monthly spending cap reached: {_money(state['mtd'])} of {_money(state['cap'])} used in "
            f"{state['month']}. Paid {'image' if kind == 'image' else 'text'} calls are paused until next "
            "month, or until spend.monthly_cap_usd is raised.",
            kind=kind, month=state["month"], mtd=state["mtd"], cap=state["cap"])


def monthly_cap(cfg: dict) -> float | None:
    """spend.monthly_cap_usd as dollars, or None for no cap (null, 0 or empty). Text such as
    "$25" is accepted; anything else raises ValueError, so a typo never means "no cap"."""
    raw = cfg.get("monthly_cap_usd")
    value = raw
    if isinstance(raw, str):
        text = raw.strip().replace("$", "").replace(",", "")
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            value = None
    elif raw is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"spend.monthly_cap_usd must be a dollar amount such as 25, or null for no cap; got {raw!r}")
    return float(value) if value > 0 else None


def _state(cfg: dict, now: datetime | None) -> dict:
    month = month_of(now)
    cap = monthly_cap(cfg)
    mtd = summarize(_read(_ledger(cfg), month=month))["total"]
    if cap is None:
        return {"month": month, "mtd": mtd, "cap": None, "pct": None, "warn": False, "reached": False}
    return {"month": month, "mtd": mtd, "cap": cap, "pct": round(mtd / cap, 4),
            "warn": mtd >= _warn_at(cfg) * cap, "reached": mtd >= cap}


def _warn_at(cfg: dict) -> float:
    raw = cfg.get("warn_at", DEFAULTS["warn_at"])
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or not 0 < raw <= 1:
        _warn_once(("warn_at", repr(raw)), "spend.warn_at must be a share between 0 and 1 such as 0.8, got %r; "
                   "using 0.8", raw)
        return DEFAULTS["warn_at"]
    return float(raw)


# --------------------------------------------------------------------------- report

def table(settings: dict | None = None, month: str | None = None) -> str:
    """A plain-text spending table for one month, for `python -m service spend`."""
    cfg = _cfg(settings)
    s = month_to_date(settings, month)
    try:
        cap, cap_error = monthly_cap(cfg), None
    except ValueError:          # still show the spending; the cap line says what to fix
        cap, cap_error = None, cfg.get("monthly_cap_usd")
    lines = [f"Spending for {s['month']} (estimates; your provider bills are what count)"]
    if cap_error is not None:
        lines.append(f"Total: {_money(s['total'])}. The monthly cap setting is not a number "
                     f"(spend.monthly_cap_usd: {cap_error!r}), so paid steps are paused until it is fixed.")
    elif cap is None:
        lines.append(f"Total: {_money(s['total'])} (no monthly cap set)")
    else:
        lines.append(f"Total: {_money(s['total'])} of {_money(cap)} cap ({s['total'] / cap:.0%})")
    lines.append(f"Paid calls: {s['calls']} ({s['images']} image{'' if s['images'] == 1 else 's'})")
    for title, key, limit in (("By service", "by_provider", None), ("By stage", "by_stage", None),
                              ("By item (top 10)", "by_item", 10)):
        rows = list(s[key].items())[:limit]
        if rows:
            width = max(len(name) for name, _ in rows)
            lines += ["", title] + [f"  {name.ljust(width)}  {_money(cost)}" for name, cost in rows]
    return "\n".join(lines) + "\n"


def _money(x: float) -> str:
    return f"${x:,.2f}" if x == 0 or x >= 0.01 else f"${x:.4f}"


# --------------------------------------------------------------------------- helpers

_warned: set = set()


def _warn_once(key, msg: str, *args, level: int = logging.WARNING) -> None:
    if key not in _warned:
        _warned.add(key)
        log.log(level, msg, *args)


def _cfg(settings: dict | None) -> dict:
    return section(settings if settings is not None else load_settings(), "spend", DEFAULTS)


def _ledger(cfg: dict) -> Path:
    return resolve(cfg.get("ledger_file") or DEFAULTS["ledger_file"])


def _append(path: Path, line: dict) -> None:
    if _off_limits(path):
        _warn_once(("off-limits", str(path)), "test run: not writing the real spend ledger %s", path)
        return
    data = (json.dumps(line) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b", buffering=0) as f:       # O_APPEND: every write lands at the end
        end = f.seek(0, os.SEEK_END)
        if end:
            f.seek(end - 1)
            if f.read(1) != b"\n":                   # a crash left half a line: start a fresh one
                data = b"\n" + data
        written = f.write(data)                      # one write per line (never a second, which could interleave)
        if written != len(data):                     # disk full: the half line is skipped on read
            raise OSError(f"short write to the spend ledger {path} ({written} of {len(data)} bytes)")


_unwritable: dict[str, str] = {}     # ledger path -> why writing it failed (this process)
_writable: set[str] = set()          # ledger paths a probe could open for appending


def _check_writable(path: Path) -> None:
    """Raise LedgerUnavailable when the ledger cannot be appended to: a failed record() in
    this process, or (once per process) a probe that opens it for appending. The probe
    creates the folder and an empty ledger if they are missing; it writes nothing."""
    key = str(path)
    if key not in _unwritable and key not in _writable and not _off_limits(path):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "ab"):
                pass
            _writable.add(key)
        except OSError as e:
            _unwritable[key] = f"{type(e).__name__}: {e.strerror or e}"
    if key in _unwritable:
        raise LedgerUnavailable(
            f"Paid AI calls are paused: the spending log {path} cannot be written ({_unwritable[key]}), so "
            "spending cannot be counted against the monthly cap. Check the free disk space and that the "
            "botsy user owns that folder.")


def _off_limits(path: Path) -> bool:
    """While pytest runs, a ledger inside the repo (the user's real data/spend/) is neither
    read nor written: tests drive the real hooks with fake SDK clients, and those must never
    add to, or be capped by, the user's real spending. Tests point spend.ledger_file into tmp_path."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return False
    return True


def _utc(when: datetime | None = None) -> datetime:
    when = when or datetime.now(timezone.utc)
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)


def month_of(now: datetime | None = None) -> str:
    """The UTC calendar month ("YYYY-MM") of `now` (default: the current time)."""
    return _utc(now).strftime("%Y-%m")


def _check_month(month) -> None:
    if not isinstance(month, str) or not MONTH_RE.fullmatch(month):
        raise ValueError(f"month must be YYYY-MM, got {month!r}")


def _check_kind(kind) -> None:
    if kind not in KINDS:
        raise ValueError(f"spend kind must be one of {KINDS}, got {kind!r}")


def _count(name: str, value) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"spend.record: {name} must be a whole number >= 0, got {value!r}")
    return value


__all__ = ["DEFAULTS", "KINDS", "BudgetExceeded", "LedgerUnavailable", "budget_state", "check_budget", "entries",
           "estimate_cost", "month_of", "month_to_date", "monthly_cap", "record", "summarize", "table"]
