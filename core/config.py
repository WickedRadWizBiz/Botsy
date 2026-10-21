"""Settings, paths and environment loading shared by every stage.

Server-only settings (docs/SERVER.md section 14): `load_settings()` with no path deep-merges
config/settings.local.yaml (gitignored, optional) over config/settings.yaml, so a switch
such as `service.live: true` never needs a commit. An explicit path loads just that file.
BOTSY_IGNORE_LOCAL_SETTINGS=1 turns the merge off (the test suite sets it, so a
developer's or the server's local file never changes test results).

A local file may never loosen a compliance rule (CLAUDE.md). After the merge, values it
set below a floor are raised to the floor with a warning: research needs at least 10
listings and 3 shops per pattern (also in research.stages.<stage>), and
compliance.publisher_mode stays draft_only. Its other `compliance:` values (blocklist and
AI disclosure files) and `listing.always_disclose_ai: false` are not used.

A section or list replaced by a single value (`service: true`) is a setup error
(ValueError), like a file that is not valid YAML. A `monthly_cap_usd` typed outside
`spend:` is used as the spending cap, with a warning, so a cap never silently does nothing.

Botsy's clock (user, 2026-10-17): `schedule.timezone` (America/New_York by default) is the
zone of every Botsy time: the systemd timers, Discord's quiet hours, the daily log's date
and times, and the Sunday plan's schedule (docs/OVERSEER.md; not built yet). `tz(settings)`
returns it as a ZoneInfo and `now_local(settings)` the current time in it, whatever the
server's own zone is. Money months and KDP weeks stay UTC.
"""
from __future__ import annotations

import copy
import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

log = logging.getLogger("core.config")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
SETTINGS_NAME = "settings.yaml"
LOCAL_SETTINGS_NAME = "settings.local.yaml"
IGNORE_LOCAL_ENV = "BOTSY_IGNORE_LOCAL_SETTINGS"
# Floors a local settings file may not go below (core.briefs.HARD_FLOOR: CLAUDE.md's 10
# listings; research.defaults.SHOP_FLOOR: the user's 3 shops). Kept here because
# core.briefs and research import this module.
LISTINGS_FLOOR = 10
SHOPS_FLOOR = 3
# Dates end up in folder and file names. Python 3.11+ fromisoformat also accepts
# "20261015" and the ISO week date "2026-W42-4", so the shape is checked first.
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# config/settings.yaml ships with this shop name; real drafts are refused until it is set.
PLACEHOLDER_SHOP_NAMES = ("", "YOUR_SHOP_NAME")
_logged: set[str] = set()   # log lines already written by this process (each is logged once)


# Settings section "schedule" (read with section(settings, "schedule", SCHEDULE_DEFAULTS)).
# Times are "Day HH:MM" on the wall clock of schedule.timezone. `timezone` sets the timers,
# quiet hours and the daily log; the other keys are the Sunday plan's schedule, used by
# service/plan.py.
DEFAULT_TIMEZONE = "America/New_York"
SCHEDULE_DEFAULTS: dict = {
    "timezone": DEFAULT_TIMEZONE,
    "plan_post": "Sun 20:00",            # Hermes posts the weekly plan
    "reminders": ["Mon 08:00", "Mon 19:00"],
    "deadline": "Tue 20:00",             # an unapproved plan expires
    "auto_start": "Mon 08:00",           # from plan 5: starts by itself unless held
    "revision_min_hours": 2,
    "max_revisions": 3,
    "approvals_before_auto": 0,          # user, 2026-10-21: plans start by themselves from week 1
    "auto_start_max_cost_share": 1.0,    # user, 2026-10-21: no cost gate (money left still limits plans)
    "stale_skip_hours": 24,              # a plan run more than this late is skipped
    "upload_cutoff": "Sun 19:00",        # shown in messages only: export by then for the plan
}


class MissingSecretError(RuntimeError):
    """A required secret is not set. Secrets live only in .env (see .env.example)."""


def load_settings(path: str | Path | None = None) -> dict:
    """Load config/settings.yaml (or another YAML file). Returns a fresh dict every call.

    With no `path`, config/settings.local.yaml is deep-merged over it when that file
    exists and BOTSY_IGNORE_LOCAL_SETTINGS is not set (see the module docstring). A local
    file that is not valid YAML, or not a mapping, raises ValueError: a broken server
    switch is a setup problem, never silently skipped."""
    if path:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    settings = yaml.safe_load((CONFIG_DIR / SETTINGS_NAME).read_text(encoding="utf-8"))
    local_path = local_settings_path()
    if local_path is None or not isinstance(settings, dict):
        return settings
    local = _read_local(local_path)
    base_compliance = copy.deepcopy(settings.get("compliance") or {})
    _merge_local(settings, local, local_path)
    _keep_floors(settings, local, local_path, base_compliance)
    _misplaced_cap(settings, local, local_path)
    _log_once(f"merged {local_path}", logging.INFO, "settings: using %s over %s", local_path, SETTINGS_NAME)
    return settings


def local_settings_path() -> Path | None:
    """config/settings.local.yaml when it exists and the merge is not switched off."""
    if os.environ.get(IGNORE_LOCAL_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        return None
    p = CONFIG_DIR / LOCAL_SETTINGS_NAME
    return p if p.is_file() else None


def _yaml_problem(e: yaml.YAMLError) -> str:
    """Where and what, without the quoted lines PyYAML adds (they would copy the file's
    text into logs and status messages)."""
    mark = getattr(e, "problem_mark", None)
    problem = getattr(e, "problem", None) or type(e).__name__
    return f"line {mark.line + 1}, column {mark.column + 1}: {problem}" if mark is not None else str(problem)


def _read_local(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"{path} is not valid YAML; fix it or remove it ({_yaml_problem(e)})") from None
    if data is None:  # an empty file, or only comments
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold settings as `name: value` lines (a YAML mapping)")
    return data


def _merge_local(base: dict, override: dict, source: Path | None = None, top: bool = True) -> dict:
    """deep_merge, except that an empty section (`service:` with nothing under it, a
    common slip when lines are commented out) keeps the section instead of wiping it, and
    a top-level section (`service:`, `compliance:`, `shops:` ...) replaced by a single
    value (`service: true`) is a setup error (ValueError), not a silently broken section.
    The value itself is not quoted in the message."""
    for k, v in override.items():
        old = base.get(k)
        if v is None and (isinstance(old, dict) or (top and isinstance(old, list))):
            continue
        if top and isinstance(old, dict) and not isinstance(v, dict):
            raise ValueError(f"{source or LOCAL_SETTINGS_NAME}: `{k}:` must have its settings on the "
                             "indented lines under it, not a value on the same line; fix that line or remove it")
        if top and isinstance(old, list) and not isinstance(v, list):
            raise ValueError(f"{source or LOCAL_SETTINGS_NAME}: `{k}:` must be a list (indented lines "
                             "starting with `-`); fix that line or remove it")
        if isinstance(v, dict) and isinstance(old, dict):
            _merge_local(old, v, source, top=False)
        else:
            base[k] = copy.deepcopy(v)
    return base


def _at_least(value, floor: int) -> bool:
    """True for a number (or number written as text) at or above `floor`. None (= the
    module default) is accepted: every default is at or above its floor."""
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    try:
        return float(value) >= floor
    except (TypeError, ValueError):
        return False


def _keep_floors(settings: dict, local: dict, source: Path, base_compliance: dict) -> None:
    """Undo what the local file did below a compliance floor (CLAUDE.md), with a warning."""
    def warn(key: str, value, kept) -> None:
        _log_once(f"floor {source} {key} {value!r}", logging.WARNING,
                  "settings: %s may not set %s to %r (compliance rule); using %r instead",
                  source.name, key, value, kept)

    def sub(d, key) -> dict:
        v = d.get(key) if isinstance(d, dict) else None
        return v if isinstance(v, dict) else {}

    research, research_local = sub(settings, "research"), sub(local, "research")
    for key, floor in (("min_listings_per_pattern", LISTINGS_FLOOR), ("min_shops_per_pattern", SHOPS_FLOOR)):
        if key in research_local and not _at_least(research.get(key), floor):
            warn(f"research.{key}", research.get(key), floor)
            research[key] = floor
    for stage, given in sub(research_local, "stages").items():
        if not isinstance(given, dict):
            continue
        merged = sub(sub(research, "stages"), stage)
        for key, floor in (("min_listings", LISTINGS_FLOOR), ("min_shops", SHOPS_FLOOR)):
            if key in given and not _at_least(merged.get(key), floor):
                warn(f"research.stages.{stage}.{key}", merged.get(key), floor)
                merged[key] = floor

    comp_local = sub(local, "compliance")
    if comp_local:
        comp = settings["compliance"] if isinstance(settings.get("compliance"), dict) else {}
        settings["compliance"] = comp
        for key in comp_local:
            kept = "draft_only" if key == "publisher_mode" else base_compliance.get(key)
            if comp.get(key) != kept:
                warn(f"compliance.{key}", comp.get(key), kept)
                if kept is None:
                    comp.pop(key, None)
                else:
                    comp[key] = kept
    if sub(local, "listing").get("always_disclose_ai", True) is not True:
        listing = sub(settings, "listing")
        if listing:
            warn("listing.always_disclose_ai", listing.get("always_disclose_ai"), True)
            listing["always_disclose_ai"] = True


def _misplaced_cap(settings: dict, local: dict, source: Path) -> None:
    """`monthly_cap_usd` belongs under `spend:`. The server file's template has `# spend:`
    commented out above the cap line, so un-commenting only the cap line puts it under
    `service:`, where nothing reads it. A cap the user typed must never silently do
    nothing: one found at the top level or in another section is used as
    spend.monthly_cap_usd (with a warning), unless the local file also sets that key."""
    found = [("monthly_cap_usd", local["monthly_cap_usd"])] if "monthly_cap_usd" in local else []
    found += [(f"{name}.monthly_cap_usd", sec["monthly_cap_usd"]) for name, sec in local.items()
              if name != "spend" and isinstance(sec, dict) and "monthly_cap_usd" in sec]
    found = [(key, value) for key, value in found if value is not None]
    if not found:
        return
    spend_local = local.get("spend")
    if isinstance(spend_local, dict) and spend_local.get("monthly_cap_usd") is not None:
        for key, value in found:
            _log_once(f"cap {source} {key}", logging.WARNING,
                      "settings: %s sets %s, which is not read; spend.monthly_cap_usd (%r) is the cap",
                      source.name, key, spend_local["monthly_cap_usd"])
        return
    key, value = found[0]
    if not isinstance(settings.get("spend"), dict):
        settings["spend"] = {}
    settings["spend"]["monthly_cap_usd"] = value
    _log_once(f"cap {source} {key} {value!r}", logging.WARNING,
              "settings: %s sets %s; it belongs under `spend:`, and is used as spend.monthly_cap_usd (%r)",
              source.name, key, value)


def _log_once(key: str, level: int, msg: str, *args) -> None:
    if key not in _logged:
        _logged.add(key)
        log.log(level, msg, *args)


def section(settings: dict, name: str, defaults: dict | None = None) -> dict:
    """Return settings[name] deep-merged over `defaults` (settings win)."""
    return deep_merge(copy.deepcopy(defaults or {}), settings.get(name) or {})


def deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def resolve(rel: str | Path) -> Path:
    """Resolve a settings path (relative to the repo root) to an absolute Path."""
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def get_shop(settings: dict, shop_id: str | None = None) -> dict:
    shops = settings["shops"]
    if shop_id is None:
        return shops[0]
    for shop in shops:
        if shop["id"] == shop_id:
            return shop
    raise KeyError(f"unknown shop id {shop_id!r}; known: {[s['id'] for s in shops]}")


def is_placeholder_shop_name(shop: dict) -> bool:
    """True while the shop's display name is still the shipped placeholder (or empty)."""
    return str(shop.get("name") or "").strip() in PLACEHOLDER_SHOP_NAMES


def load_env() -> None:
    """Load .env from the repo root into os.environ (existing variables win)."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)


def require_env(name: str) -> str:
    load_env()
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSecretError(f"{name} is not set. Add it to .env (see .env.example).")
    return value


def check_date(value) -> str:
    """Return `value` if it is a real date written YYYY-MM-DD, else raise ValueError."""
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"date {value!r} is not a real date: {e}") from None
    return value


def tz(settings: dict | None = None) -> ZoneInfo:
    """Botsy's time zone, schedule.timezone (default America/New_York), as a ZoneInfo.

    `settings` None means the defaults, not the settings file (so this never reads a file).
    A name tzdata does not know falls back to the default, with one warning."""
    name = DEFAULT_TIMEZONE
    if settings:
        raw = section(settings, "schedule", SCHEDULE_DEFAULTS).get("timezone")
        name = str(raw).strip() if raw not in (None, "") else DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        _log_once(f"schedule.timezone:{name}", logging.WARNING,
                  "schedule.timezone %r is not a time zone name (timedatectl list-timezones); using %s",
                  name, DEFAULT_TIMEZONE)
        return ZoneInfo(DEFAULT_TIMEZONE)


def now_local(settings: dict | None = None, now: datetime | None = None) -> datetime:
    """The current time (or `now`) in schedule.timezone, time-zone aware.

    A naive `now` counts as UTC (the event journal's time stamps are UTC)."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(tz(settings))


def today() -> str:
    """ISO date for output names. PIPELINE_DATE overrides it for reproducible runs."""
    value = os.environ.get("PIPELINE_DATE") or date.today().isoformat()
    try:
        return check_date(value)
    except ValueError as e:
        raise ValueError(f"PIPELINE_DATE: {e}") from None
