"""botsy-control: the only way Hermes reaches Botsy (docs/OVERSEER.md "botsy-control reference").

    call(server, name, arguments=None, *, settings=None, now=None) -> Outcome    (never raises)
    tools_for(server) -> list[Tool] ; input_schema(tool, settings=None) -> dict
    TOOLS, SERVERS, REASONS, describe(reason)

Two tool menus, each served by its own MCP server over stdio (control/read_server.py and
control/act_server.py); the servers are plain glue that hand the raw JSON arguments to call()
here, so every check happens in this file and in service/orders.py:

- "read" (Level 0): status, spend, daily_log, review_list, review_preview, view_item_images,
  plan_status, plan_facts, briefs_summary, capacity, orders_recent, events_recent,
  trend_search. They only read Botsy's records, and they return fixed fields: ids, counts,
  money, dates and reason codes from fixed lists, plus theme names from the briefs (aggregated
  terms), the scrubbed daily log and our own product images. Never a research row, an export
  file name or header, a path, a listing link, a shop name or a secret. trend_search (D4.8,
  2026-10-21) is the one read tool that reaches the web: TinyFish Search, with every
  marketplace or listing-like result dropped before Hermes sees it (control/trend.py); it
  writes only its own usage counter.
- "act": submit_order, approve_order, pause, resume, lower_cap, search_ideas, undo_order and
  the fixed watchdog fixes fix_retry_run, fix_clear_stale_lock, fix_rerun_qa,
  fix_resend_daily_log, fix_retry_notify (D4.7; service/fixes.py).
  Every one is an order: it goes through service.orders.submit / validate / approve / undo
  (weekly plans through service.plan.submit_order first), which check it again in full
  (levels, the admin-only limits file, capacity, the cap, the daily limit on the former code
  actions). This file never changes a thing itself. created_by is always "hermes"; Hermes
  cannot say otherwise.

Decided 2026-10-21: no codes. The actions that once needed a 6-digit code (review approvals,
raising the cap, restarting paid work after a budget stop, the first 4 weekly plans, a plan
over the cost share) are taken directly from the user, through Hermes (Discord, the one
allowlisted user ID) or the server CLI. Botsy's guards stay in service/orders.py: hard-blocked
items are never approvable, a raise never goes above limits.cap_ceiling_usd, a daily limit
(limits level3_per_day, approve_batch_max), and every such action is posted at once to the
main Discord channel with who asked and how to undo it.

There is no tool for a shell, files, fetching web pages, raw research data, the compliance
lists, settings, live listings or deleting anything on Etsy.

Input: every argument has a strict schema (enums, patterns, lengths, ranges), checked here
before anything runs; unknown arguments are refused. There is no free-text argument at all:
search queries and refs follow service.orders' fixed patterns. Hermes's values never reach an
event, a log line, a model prompt or a file name; refusals carry fixed reason codes and the
argument's name only, never its value.

Output: every string in a result passes a last scrub (secret values from the environment and
.env, Discord webhook links, any URL, paths and data-file names are replaced), on top of the
fixed-field projections.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.config import ROOT, load_settings, now_local, resolve, section

log = logging.getLogger("control.tools")

SCHEMA = "botsy-control/v1"
VERSION = "2026.10.21"
SERVERS = ("read", "act")
CREATED_BY = "hermes"              # every order from botsy-control; never taken from the caller

# Reason codes of this layer (service/orders.py, service/plan.py and service/capacity.py have theirs).
REASONS = ("unknown_tool", "not_an_object", "unknown_argument", "missing_argument", "bad_argument",
           "too_large", "settings_unreadable", "internal_error", "not_found", "no_preview", "money_unknown",
           "not_waiting", "approval_unavailable", "plan_service_missing", "not_configured", "trend_search_off",
           "trend_search_limit", "query_blocked", "query_needs_review", "query_marketplace", "search_failed",
           "search_rate_limited", "search_auth_failed", "search_allowance_used", "usage_unreadable")
DESCRIBE = {
    "plan_expired": "The plan expired at its deadline (Tuesday 8 pm); Botsy runs on its normal rules this week.",
    "unknown_tool": "There is no such tool on this server.",
    "not_an_object": "The arguments must be a JSON object.",
    "unknown_argument": "An argument this tool does not take was given.",
    "missing_argument": "A required argument is missing.",
    "bad_argument": "An argument has a value this tool does not accept (see the tool's schema).",
    "too_large": "The payload is too large.",
    "settings_unreadable": "Botsy could not read its settings (the server log says why).",
    "internal_error": "Something went wrong inside Botsy; nothing was changed (the server log says why).",
    "not_found": "Botsy has no such item.",
    "no_preview": "That item has no preview image Botsy can show.",
    "money_unknown": "Botsy could not read the spending ledger or the cap.",
    "not_waiting": "That order does not wait for the user's approval (see orders_recent or plan_status).",
    "approval_unavailable": "Botsy's order service has no approve step for this order yet; nothing was changed. "
                           "The user can approve it on the server.",
    "plan_service_missing": "Botsy's plan service (service/plan.py) is not installed or did not load, so plans "
                            "cannot be checked; nothing was changed.",
    "not_configured": "Trend search is not set up (no TINYFISH_API_KEY in Botsy's .env).",
    "trend_search_off": "Trend search is switched off in Botsy's settings (trend_search.enabled).",
    "trend_search_limit": "The daily trend search limit is used up (limits trend_searches_per_day, any 24 hours).",
    "query_blocked": "The query has a trademarked, licensed, game-system or medical term; it was not sent.",
    "query_needs_review": "The query has a term on the review list; it was not sent.",
    "query_marketplace": "The query names a marketplace; trend search reads off-marketplace sources only.",
    "search_failed": "TinyFish Search did not answer usefully; nothing was retried. Try later.",
    "search_rate_limited": "TinyFish Search said too many requests; nothing was retried. Try later.",
    "search_auth_failed": "TinyFish refused Botsy's API key; the user should check TINYFISH_API_KEY.",
    "search_allowance_used": "TinyFish's free daily Search allowance is used; Botsy does not pay for searches.",
    "usage_unreadable": "Botsy could not read its trend search counter, so no search was sent.",
}

# Size limits.
MAX_PAYLOAD_BYTES = 16_000
MAX_PAYLOAD_DEPTH = 5
MAX_PAYLOAD_KEYS = 30
MAX_PAYLOAD_LIST = 200
MAX_PAYLOAD_STR = 160
MAX_FILE_BYTES = 4_000_000          # status.json, briefs, plan files read here
MAX_LOG_CHARS = 8_000               # daily_log text returned
MAX_LOG_LINE = 300
PREVIEW_MAX_BYTES = 8_000_000       # source image
PREVIEW_PX = 768                    # longest side of the preview sent to Hermes
PREVIEW_MAX_PIXELS = 40_000_000     # a source image larger than this is "no preview" (memory)
VIEW_PX = 1024                      # view_item_images: longest side
VIEW_MAX_IMAGES = 6
VIEW_MAX_TOTAL_BYTES = 3_000_000    # all images of one view_item_images call together
VIEW_KINDS = {"pages": "previews", "mockups": "mockups"}     # kind -> the item's own folder

# Value patterns.
ITEM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")      # core.items.ITEM_ID_RE, capped (orders.ITEM_MAX)
# What the generic projection passes: lowercase codes and ids, ISO weeks and timestamps (never
# CamelCase words, which is how shop names usually look, and never a data-file name).
LOWER_STR_RE = re.compile(r"[a-z0-9][a-z0-9:+._-]{0,63}")
WEEK_STR_RE = re.compile(r"\d{4}-W(0[1-9]|[1-4]\d|5[0-3])")
THEME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 '&,.-]{0,79}")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})?)?")
MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
FIELD_PATH_RE = re.compile(r"[a-z_][a-z0-9_.\[\]]{0,80}")
KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,40}")
THUMB_RE = re.compile(r"(?:[A-Za-z0-9_-]{1,40}/){0,2}[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.(?:png|jpe?g|webp)", re.I)
BRIEF_FILE_RE = re.compile(r"(?P<niche>[a-z_]+)-(?P<date>\d{4}-\d{2}-\d{2})\.json")    # pipeline/run.py
WHERE_RE = re.compile(r"(?P<stage>[a-z][a-z_ ]{0,30})(?:\[(?P<target>[A-Za-z0-9][A-Za-z0-9._-]{0,159})\])?")

# Keys a generic projection never passes on (file names, paths, links, shops, copy, evidence).
DENY_KEY_PARTS = ("path", "file", "dir", "url", "link", "shop", "seller", "buyer", "title", "descr", "keyword",
                  "tag", "evidence", "source", "header", "rows", "text", "message", "detail", "note", "prompt",
                  "style", "audience", "image", "photo", "thumb", "email", "token", "secret", "password",
                  "listing_id", "review_text", "name", "sig", "salt", "hash")
THEME_KEYS = ("theme",)
STATUS_WEEK_KEYS = ("ticks", "exports", "duplicates", "rejected", "briefs", "design_briefs", "items", "drafts",
                    "practice_drafts", "errors")
STAGES = ("discovery", "refined")

PLAN_DEFAULTS = {"dir": "data/plans"}
LOG_DEFAULTS = {"log_file": "data/logs/botsy-control.log"}
ENV_FILE = ROOT / ".env"

_ACT_LOCK = threading.Lock()        # one act call at a time in a server process


# ---------------------------------------------------------------- results

@dataclass
class Outcome:
    """A tool's result: `data` (JSON) and `images` [(mime, bytes)] shown to Hermes as images."""
    data: dict
    images: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.data.get("ok"))


def _refusal(reasons, fields=(), **extra) -> Outcome:
    reasons = [r for r in dict.fromkeys(reasons) if isinstance(r, str) and KEY_RE.fullmatch(r)]
    data = {"ok": False, "reasons": reasons, "explain": {r: describe(r) for r in reasons}}
    fields = [f for f in dict.fromkeys(fields) if isinstance(f, str) and FIELD_PATH_RE.fullmatch(f)]
    if fields:
        data["fields"] = fields
    data.update(extra)
    return Outcome(data)


def describe(reason: str) -> str:
    """Plain words for a reason code (this layer, orders or capacity)."""
    if reason in DESCRIBE:
        return DESCRIBE[reason]
    try:
        from service import orders

        text = orders.describe(reason)
    except Exception:  # noqa: BLE001 - plain words are a convenience
        text = None
    return text if isinstance(text, str) and text and text != reason else str(reason).replace("_", " ")


# ---------------------------------------------------------------- argument specs

@dataclass(frozen=True)
class Arg:
    """One argument's strict schema. `type`: string, integer, number, boolean, array, object.
    `enum_from` "niches" takes the niche names from the settings; `payload` is an order payload
    (any JSON object within the size limits; service.orders checks its fields per kind)."""
    type: str
    required: bool = False
    description: str = ""
    enum: tuple = ()
    enum_from: str | None = None
    pattern: re.Pattern | None = None
    max_length: int = 0
    minimum: float | None = None
    maximum: float | None = None
    items: Arg | None = None
    min_items: int = 0
    max_items: int = 0
    unique: bool = False
    fields: tuple = ()
    payload: bool = False
    default: Any = None


def _enum(arg: Arg, settings: dict) -> tuple:
    if arg.enum_from == "niches":
        n = settings.get("niches")
        return tuple(n) if isinstance(n, dict) else ()
    return tuple(arg.enum)


def _num(x) -> bool:
    if isinstance(x, bool):
        return False
    if isinstance(x, int):
        return True                     # never math.isfinite on an int: a huge one overflows
    return isinstance(x, float) and math.isfinite(x)


def _check(arg: Arg, value, path: str, settings: dict, problems: list) -> Any:
    t = arg.type
    if t == "string":
        if isinstance(value, str) and len(value) <= (arg.max_length or 200):
            if arg.enum or arg.enum_from:
                if value in _enum(arg, settings):
                    return value
            elif arg.pattern is not None and arg.pattern.fullmatch(value):
                return value               # a string with neither a list nor a pattern is never accepted
    elif t == "integer":
        if isinstance(value, int) and not isinstance(value, bool) \
                and (arg.minimum is None or value >= arg.minimum) and (arg.maximum is None or value <= arg.maximum):
            return value
    elif t == "number":
        if _num(value) and (arg.minimum is None or value >= arg.minimum) \
                and (arg.maximum is None or value <= arg.maximum):
            return value
    elif t == "boolean":
        if isinstance(value, bool):
            return value
    elif t == "array":
        if isinstance(value, list) and arg.min_items <= len(value) <= (arg.max_items or 50):
            out = [_check(arg.items, v, f"{path}[{i}]", settings, problems) for i, v in enumerate(value)]
            if arg.unique and len({json.dumps(v, sort_keys=True) for v in out}) != len(out):
                problems.append(("bad_argument", path))
            return out
    elif t == "object" and arg.payload:
        if isinstance(value, dict):
            reason = _payload_problem(value)
            if reason is None:
                return value
            problems.append((reason, path))
            return None
    elif t == "object":
        if isinstance(value, dict):
            return _check_object(dict(arg.fields), value, path, settings, problems)
    problems.append(("bad_argument", path))
    return None


def _check_object(fields: dict, value: dict, path: str, settings: dict, problems: list) -> dict:
    out = {}
    for key in value:
        if key not in fields:
            problems.append(("unknown_argument", path or "arguments"))     # never the name: it is the caller's text
    for key, arg in fields.items():
        sub = f"{path}.{key}" if path else key
        if key not in value or value[key] is None:
            if arg.required:
                problems.append(("missing_argument", sub))
            elif arg.default is not None:
                out[key] = arg.default
            continue
        out[key] = _check(arg, value[key], sub, settings, problems)
    return out


def _payload_problem(value: dict) -> str | None:
    """None when `value` is a plain JSON object within the size limits (service.orders then
    checks every field of it per order kind)."""
    def ok(v, depth: int) -> bool:
        if depth > MAX_PAYLOAD_DEPTH:
            return False
        if v is None or isinstance(v, bool):
            return True
        if isinstance(v, (int, float)):
            return _num(v)
        if isinstance(v, str):
            return len(v) <= MAX_PAYLOAD_STR
        if isinstance(v, list):
            return len(v) <= MAX_PAYLOAD_LIST and all(ok(x, depth + 1) for x in v)
        if isinstance(v, dict):
            return len(v) <= MAX_PAYLOAD_KEYS and all(isinstance(k, str) and KEY_RE.fullmatch(k) and ok(x, depth + 1)
                                                      for k, x in v.items())
        return False

    if not ok(value, 0):
        return "bad_argument"
    try:
        size = len(json.dumps(value, separators=(",", ":")))
    except (TypeError, ValueError):
        return "bad_argument"
    return "too_large" if size > MAX_PAYLOAD_BYTES else None


def _schema(arg: Arg, settings: dict) -> dict:
    s: dict = {"type": arg.type}
    if arg.description:
        s["description"] = arg.description
    if arg.type == "string":
        choices = _enum(arg, settings)
        if choices:
            s["enum"] = list(choices)
        elif arg.pattern is not None:
            s["pattern"] = f"^{arg.pattern.pattern}$"
        s["maxLength"] = arg.max_length or 200
    elif arg.type in ("integer", "number"):
        if arg.minimum is not None:
            s["minimum"] = arg.minimum
        if arg.maximum is not None:
            s["maximum"] = arg.maximum
    elif arg.type == "array":
        s["items"] = _schema(arg.items, settings)
        s["minItems"] = arg.min_items
        s["maxItems"] = arg.max_items or 50
        if arg.unique:
            s["uniqueItems"] = True
    elif arg.type == "object" and arg.payload:
        s["maxProperties"] = MAX_PAYLOAD_KEYS
    elif arg.type == "object":
        s.update(_object_schema(dict(arg.fields), settings))
    if arg.default is not None:
        s["default"] = arg.default
    return s


def _object_schema(fields: dict, settings: dict) -> dict:
    return {"type": "object", "properties": {k: _schema(a, settings) for k, a in fields.items()},
            "required": [k for k, a in fields.items() if a.required], "additionalProperties": False}


# ---------------------------------------------------------------- tools

@dataclass(frozen=True)
class Tool:
    name: str
    server: str
    level: int
    title: str
    description: str
    args: tuple
    fn: Callable
    open_world: bool = False          # reaches outside Botsy (trend_search); MCP open_world_hint


@dataclass
class _Ctx:
    settings: dict
    args: dict
    now: datetime | None

    @property
    def clock(self) -> datetime:
        return self.now if self.now is not None else datetime.now(timezone.utc)


TOOLS: dict[str, Tool] = {}


def _tool(name: str, server: str, level: int, title: str, description: str, *, open_world: bool = False,
          **args: Arg):
    def deco(fn: Callable) -> Callable:
        TOOLS[name] = Tool(name, server, level, title, description, tuple(args.items()), fn, open_world)
        return fn
    return deco


def tools_for(server: str) -> list[Tool]:
    """The tools one server offers, in a fixed order."""
    return [t for t in TOOLS.values() if t.server == server]


def input_schema(tool: Tool, settings: dict | None = None) -> dict:
    """The tool's JSON Schema (additionalProperties false)."""
    settings = settings if settings is not None else _settings_or_empty()
    return _object_schema(dict(tool.args), settings)


def _settings_or_empty() -> dict:
    try:
        s = load_settings()
        return s if isinstance(s, dict) else {}
    except Exception:  # noqa: BLE001 - a schema is still listed
        return {}


def call(server: str, name: str, arguments=None, *, settings: dict | None = None,
         now: datetime | None = None) -> Outcome:
    """Run one tool. Checks the arguments, runs it, scrubs the result. Never raises."""
    try:
        return _dispatch(server, name, arguments, settings, now)
    except Exception as e:  # noqa: BLE001 - by contract: a fixed refusal, never a traceback to Hermes
        log.warning("botsy-control: call failed (%s)", type(e).__name__)
        return _refusal(["internal_error"])


def _dispatch(server: str, name: str, arguments, settings: dict | None, now: datetime | None) -> Outcome:
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None or tool.server != server:
        if server == "act":            # journaled without the name: it is the caller's text
            try:
                from core import events

                events.emit("control_refused", settings=settings if settings is not None else load_settings(),
                            tool="unknown", reasons=["unknown_tool"])
            except Exception as e:  # noqa: BLE001 - best effort
                log.warning("botsy-control: refusal not journaled (%s)", type(e).__name__)
        log.info("tool=unknown ok=False reasons=unknown_tool")
        return _refusal(["unknown_tool"])
    if server == "act":
        with _ACT_LOCK:
            out = _call(tool, arguments, settings, now)
    else:
        out = _call(tool, arguments, settings, now)
    log.info("tool=%s ok=%s reasons=%s", tool.name, out.ok, ",".join(out.data.get("reasons") or []) or "-")
    return out


def _call(tool: Tool, arguments, settings: dict | None, now: datetime | None) -> Outcome:
    try:
        settings = settings if settings is not None else load_settings()
        if not isinstance(settings, dict):
            raise ValueError("settings are not a mapping")
    except Exception as e:  # noqa: BLE001 - reported as a code
        log.warning("botsy-control: settings unreadable (%s)", type(e).__name__)
        return _refusal(["settings_unreadable"])
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _journal_refusal(tool, settings, _refusal(["not_an_object"]))
    problems: list = []
    args = _check_object(dict(tool.args), arguments, "", settings, problems)
    if problems:
        return _journal_refusal(tool, settings, _refusal([r for r, _ in problems], [f for _, f in problems]))
    try:
        out = tool.fn(_Ctx(settings, args, now))
    except Exception as e:  # noqa: BLE001 - never half-silent, never a traceback to Hermes
        log.warning("botsy-control %s failed: %s", tool.name, type(e).__name__)
        return _refusal(["internal_error"])
    if not isinstance(out, Outcome):
        out = Outcome(out if isinstance(out, dict) else {"ok": False, "reasons": ["internal_error"]})
    out.data = _scrub_tree(out.data, _Scrubber())
    out.data.setdefault("schema", SCHEMA)
    out.data.setdefault("tool", tool.name)
    return out


def _journal_refusal(tool: Tool, settings: dict, out: Outcome) -> Outcome:
    """An act call refused before it became an order: journaled with fixed fields only."""
    if tool.server == "act":
        try:
            from core import events

            events.emit("control_refused", settings=settings, tool=tool.name,
                        reasons=list(out.data.get("reasons") or []))
        except Exception as e:  # noqa: BLE001 - the refusal still goes back as it is
            log.warning("botsy-control: refusal not journaled (%s)", type(e).__name__)
    return out


# ---------------------------------------------------------------- scrubbing

_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s'\"<>]+|\b(?:[\w-]+\.)*etsy\.com/[^\s'\"<>]*")
_PATH_RE = re.compile(r"(?<![\w])(?:~|\.{1,2})?/?(?:[\w.@+-]+/)+[\w.@+-]*|[A-Za-z]:\\[^\s'\"<>]*")
# Not paths: real time-zone names (America/New_York) and a few fixed words with a slash. Any
# other "word/word" (an export folder, "Exports/SomeShop", "data/plans") is scrubbed.
_ZONE_RE = re.compile(r"[A-Z][A-Za-z_]+(?:/[A-Z][A-Za-z_+-]+)+")
_SLASH_WORDS = frozenset({"and/or", "image/jpeg", "image/png", "image/webp", "n/a", "w/o", "24/7"})


def _not_a_path(text: str) -> bool:
    if text.lower() in _SLASH_WORDS:
        return True
    if not _ZONE_RE.fullmatch(text):
        return False
    try:
        import zoneinfo

        return text in zoneinfo.available_timezones()
    except Exception:  # noqa: BLE001 - without the zone list, scrub it
        return False
_DATA_FILE_RE = re.compile(r"[\w.@()+-]*\.(?:csv|tsv|xlsx|xlsm|xls|ods|json|jsonl|part|zip|txt|yaml|yml|env|log)\b",
                           re.I)
_OWN_FILES = ("status.txt", "daily-log.txt", "search-list.txt")


class _Scrubber:
    """Replaces secret values (environment and .env), webhook links, URLs, paths and data-file
    names in text. Built once per call."""

    def __init__(self):
        from service.status import redact

        self._redact = redact
        values = set()
        try:
            from dotenv import dotenv_values

            if ENV_FILE.is_file():
                values = {str(v).strip() for v in dotenv_values(ENV_FILE).values() if v and len(str(v).strip()) >= 8}
        except Exception as e:  # noqa: BLE001 - the environment check below still runs
            log.warning("botsy-control: .env not read for scrubbing (%s)", type(e).__name__)
        self._values = sorted(values, key=len, reverse=True)

    def secrets(self, s: str) -> str:
        for v in self._values:
            s = s.replace(v, "[secret]")
        return s

    def text(self, s: str) -> str:
        s = self.secrets(s)
        s = self._redact(s)
        s = _URL_RE.sub("[link]", s)
        s = _PATH_RE.sub(lambda m: m.group(0) if _not_a_path(m.group(0)) else "[path]", s)
        return _DATA_FILE_RE.sub(lambda m: m.group(0) if m.group(0).lower() in _OWN_FILES else "[file]", s)


def _trend():
    from control import trend

    return trend


def _scrub_tree(obj, scrub: _Scrubber, key: str | None = None):
    if isinstance(obj, str):
        if key == "thumbnail" and THUMB_RE.fullmatch(obj) and ".." not in obj:
            return obj                  # checked against a strict pattern when it was read
        if key in ("site", "page") and len(obj) <= _trend().PAGE_MAX and _trend().PAGE_RE.fullmatch(obj) \
                and ".." not in obj:
            return scrub.secrets(obj)   # a trend result's domain + path (control/trend.py strip_url)
        return scrub.text(obj)
    if isinstance(obj, dict):
        return {k: _scrub_tree(v, scrub, k) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub_tree(v, scrub, key) for v in obj]
    return obj


# ---------------------------------------------------------------- safe values and files

def _int(v, lo: int = 0, hi: int = 10**9):
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def _money(v):
    return round(float(v), 4) if _num(v) and -1e7 < v < 1e7 else None


def _ts(v):
    return v if isinstance(v, str) and len(v) <= 40 and TS_RE.fullmatch(v) else None


def _code(v):
    return v if isinstance(v, str) and KEY_RE.fullmatch(v) else None


def _codes(v, limit: int = 30) -> list:
    return [c for c in (v if isinstance(v, list) else []) if _code(c)][:limit]


def _item(v):
    return v if isinstance(v, str) and ITEM_RE.fullmatch(v) and ".." not in v else None


def _niche(v, settings: dict):
    n = settings.get("niches")
    return v if isinstance(v, str) and isinstance(n, dict) and v in n else None


def _theme(v):
    if not isinstance(v, str):
        return None
    v = " ".join(v.split())
    return v if THEME_RE.fullmatch(v) else None


def _safe(obj, depth: int = 0, key: str | None = None):
    """A generic projection for Botsy's own state files (plan state, plan facts, handler
    results): plain numbers and booleans, short strings without spaces or slashes (ids, weeks,
    dates, codes), theme names under a "theme" key, and nothing under keys that could hold a
    file name, a path, a link, a shop or copy. Anything else is left out."""
    if depth > 6:
        return None
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return obj if abs(obj) < 10**12 else None
    if isinstance(obj, float):
        return round(obj, 6) if math.isfinite(obj) else None
    if isinstance(obj, str):
        if key in THEME_KEYS:
            return _theme(obj)
        if _DATA_FILE_RE.search(obj):
            return None
        if LOWER_STR_RE.fullmatch(obj) or WEEK_STR_RE.fullmatch(obj) or (len(obj) <= 40 and TS_RE.fullmatch(obj)):
            return obj
        return None
    if isinstance(obj, list):
        out = [_safe(v, depth + 1, key) for v in obj[:100]]
        return [v for v in out if v is not None]
    if isinstance(obj, dict):
        out = {}
        for k, v in list(obj.items())[:80]:
            if not isinstance(k, str) or not KEY_RE.fullmatch(k) or any(p in k for p in DENY_KEY_PARTS):
                continue
            sv = _safe(v, depth + 1, k)
            if sv is not None or v is None:
                out[k] = sv
        return out
    return None


def _read_bytes(path: Path, max_bytes: int = MAX_FILE_BYTES) -> bytes | None:
    """A regular file's bytes (no symlink at the last step, at most max_bytes), else None."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
            return None
        with os.fdopen(fd, "rb", closefd=True) as f:
            fd = -1
            data = f.read(max_bytes + 1)
        return data if len(data) <= max_bytes else None
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _read_json(path: Path, max_bytes: int = MAX_FILE_BYTES):
    data = _read_bytes(path, max_bytes)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _inside(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------- shared lookups

def _work_root(settings: dict) -> Path:
    from service import orders

    return orders._work_root(settings)


def _plan_dir(settings: dict) -> Path:
    mod = _plan_module()
    fn = getattr(mod, "plans_dir", None) if mod is not None else None
    if callable(fn):
        try:
            return Path(fn(settings))
        except Exception as e:  # noqa: BLE001 - the settings default below
            log.warning("botsy-control: plans_dir failed (%s)", type(e).__name__)
    cfg = section(settings, "plan", PLAN_DEFAULTS)
    raw = cfg.get("plans_dir") or cfg.get("dir") or PLAN_DEFAULTS["dir"]
    return resolve(raw) if isinstance(raw, str) else resolve(PLAN_DEFAULTS["dir"])


def _today(settings: dict, now: datetime) -> date:
    return now_local(settings, now).date()


def _newest_briefs(settings: dict) -> dict[str, tuple[str, Path]]:
    """niche -> (date, file) of the newest brief file per niche (by the date in its name)."""
    from service.paths import service_paths

    d = service_paths(settings).briefs
    niches = settings.get("niches") if isinstance(settings.get("niches"), dict) else {}
    newest: dict[str, tuple[str, Path]] = {}
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return {}
    for p in files:
        m = BRIEF_FILE_RE.fullmatch(p.name)
        if m and m["niche"] in niches and m["date"] >= newest.get(m["niche"], ("", p))[0]:
            newest[m["niche"]] = (m["date"], p)
    return newest


def _brief_rows(doc) -> list[dict]:
    rows = doc.get("briefs") if isinstance(doc, dict) else None
    return [b for b in rows if isinstance(b, dict)] if isinstance(rows, list) else []


def _brief_view(b: dict, settings: dict) -> dict | None:
    from service import orders

    bid = b.get("brief_id")
    if not (isinstance(bid, str) and orders.BRIEF_ID_RE.fullmatch(bid)):
        return None
    sizes = b.get("pack_sizes")
    sizes = sorted({s for s in sizes if _int(s, 1, 500)}) if isinstance(sizes, list) else []
    ev = b.get("evidence") if isinstance(b.get("evidence"), dict) else {}
    opp = ev.get("opportunity", b.get("opportunity"))
    return {"brief_id": bid, "niche": _niche(b.get("niche"), settings), "theme": _theme(b.get("theme")),
            "sizes": sizes[:8], "opportunity": round(float(opp), 3) if _num(opp) and 0 <= opp <= 10 else None,
            "needs_review": bool(b.get("needs_review"))}


_REVIEW_RULES = (
    (re.compile(r"^hold: ([a-z][a-z0-9_]{1,40})\b"), None),
    (re.compile(r"^([a-z][a-z0-9_]{1,40}): "), None),
    (re.compile(r"listing copy has review terms", re.I), "listing_review_terms"),
    (re.compile(r"item data has review terms", re.I), "item_data_review_terms"),
    (re.compile(r"style_notes", re.I), "style_notes_unlisted"),
    (re.compile(r"planner style|names a holiday", re.I), "stationery_style_check"),
    (re.compile(r"layout", re.I), "layout_reused"),
    (re.compile(r"margin|out of bounds|outside the", re.I), "print_check"),
    (re.compile(r"medical|health|diagnos|treat", re.I), "health_review"),
    (re.compile(r"look-?alike|character", re.I), "possible_lookalike"),
    (re.compile(r"trademark|review term|blocklist", re.I), "review_terms"),
    (re.compile(r"rejected in the review queue", re.I), "rejected"),
)


def reason_codes(reasons) -> list[str]:
    """Review reasons (free text from QA, listing and the generators) as fixed codes: the
    check name when the text starts with one ("trademark: ..."), else a code from a fixed
    list, else "other". Never the text itself (it can hold the flagged term)."""
    if isinstance(reasons, str):
        reasons = [reasons]
    out: list[str] = []
    for r in reasons if isinstance(reasons, list) else []:
        text = str(r or "")
        code = "other"
        for rx, fixed in _REVIEW_RULES:
            m = rx.search(text)
            if m:
                code = fixed or m.group(1)
                break
        if code not in out:
            out.append(code)
    return out[:12]


# ---------------------------------------------------------------- read tools (Level 0)

@_tool("status", "read", 0, "Botsy status",
       "What the server did lately, as fixed fields: the week's totals, the last check, the pause and budget "
       "stop, and how much of Hermes's own limits is used. No file names, no error text.")
def _status(c: _Ctx) -> dict:
    from service import limits as limits_mod
    from service import orders
    from service.paths import is_live, service_paths

    s = c.settings
    doc = _read_json(service_paths(s).outbox / "status.json")
    doc = doc if isinstance(doc, dict) else {}
    week = doc.get("week") if isinstance(doc.get("week"), dict) else {}
    ticks = [t for t in doc.get("ticks") or [] if isinstance(t, dict)] if isinstance(doc.get("ticks"), list) else []
    st = orders.control_state(s)
    lim = limits_mod.load(s)
    return {"ok": True, "live": is_live(s), "status_updated": _ts(doc.get("updated")),
            "last_checked": _ts(doc.get("last_checked")), "week": {k: _int(week.get(k)) for k in STATUS_WEEK_KEYS},
            "last_check": _tick_view(ticks[-1]) if ticks else None,
            "paused": _pause_view(st.get("paused")), "budget_stop": _stop_view(st.get("budget_stop")),
            "control_state_ok": not st.get("unreadable"), "cap_override_usd": _money(st.get("cap_override_usd")),
            "limits_file_ok": bool(lim.ok), "limits_reason": _code(lim.reason),
            "hermes_limits_used": {k: _int(v) for k, v in orders.counts(s, now=c.now).items()},
            "hermes_limits": {"changes_per_hour": _int(lim.get("changes_per_hour")),
                              "search_ideas_per_week": _int(lim.get("search_ideas_per_week")),
                              "approve_batch_max": _int(lim.get("approve_batch_max")),
                              "level3_per_day": _int(lim.get("level3_per_day"))}}


def _tick_view(t: dict) -> dict:
    ing = t.get("ingest") if isinstance(t.get("ingest"), dict) else {}
    res = t.get("research") if isinstance(t.get("research"), dict) else {}
    run = t.get("run") if isinstance(t.get("run"), dict) else {}

    def n(v):
        return len(v) if isinstance(v, list) else _int(v)

    return {"started": _ts(t.get("started")), "finished": _ts(t.get("finished")), "forced": bool(t.get("forced")),
            "live": bool(t.get("live")), "own_stats": bool(t.get("own_stats")),
            "exports_accepted": n(ing.get("accepted")), "exports_duplicates": n(ing.get("duplicates")),
            "exports_rejected": n(ing.get("rejected")), "exports_routed": n(ing.get("routed")),
            "research_ran": bool(res.get("ran")), "research_stage": res.get("stage") if res.get("stage") in STAGES
            else None, "briefs": _int(res.get("briefs")),
            "run_ran": bool(run.get("ran")), "dry_run": bool(run.get("dry_run", True)),
            **{k: _int(run.get(k)) for k in ("items", "generated", "drafts", "awaiting_review", "cooldown",
                                             "budget_wait")},
            "failed": n(run.get("failed")), "errors": n(t.get("errors")),
            "exit_code": _int(t.get("exit_code"), -255, 255)}


def _pause_view(p) -> dict | None:
    if not isinstance(p, dict):
        return None
    from service import orders

    return {"by": p.get("by") if p.get("by") in orders.CREATORS else None, "since": _ts(p.get("since")),
            "order_id": _order_id(p.get("order_id")), "reason_code": _code(p.get("reason_code"))}


def _stop_view(b) -> dict | None:
    if not isinstance(b, dict):
        return None
    m = b.get("month")
    return {"month": m if isinstance(m, str) and MONTH_RE.fullmatch(m) else None, "since": _ts(b.get("since")),
            "reason_code": _code(b.get("reason_code"))}


def _needs_approval(o: dict) -> bool:
    """Whether a stored order waits for the user's plain approval (order/v1 without codes)."""
    return o.get("status") == "waiting_approval" or (bool(o.get("needs_approval")) and o.get("status") not in
                                                     ("applied", "undone", "cancelled", "failed"))


def _order_id(v):
    from service import orders

    return v if isinstance(v, str) and orders.ORDER_ID_RE.fullmatch(v) else None


def _week(v):
    from service import orders

    return v if isinstance(v, str) and orders.WEEK_RE.fullmatch(v) else None


@_tool("spend", "read", 0, "Spending this month",
       "This month's estimated spend (UTC month), the cap in force, the money left and the warning line, in US "
       "dollars. Estimates from Botsy's ledger; the provider bills are what count.")
def _spend(c: _Ctx) -> dict | Outcome:
    from service import capacity, orders

    try:
        m = capacity.money(c.settings, now=c.now)
    except (ValueError, OSError, TypeError, KeyError):
        return _refusal(["money_unknown"])
    cap, spent = _money(m.get("cap")), _money(m.get("spent"))
    st = orders.control_state(c.settings)
    stop = _stop_view(st.get("budget_stop"))
    return {"ok": True, "month": m.get("month") if isinstance(m.get("month"), str) and MONTH_RE.fullmatch(m["month"])
            else None, "spent": spent, "cap": cap, "money_left": _money(m.get("money_left")),
            "warn_line": _money(m.get("warn_line")),
            "share_used": round(spent / cap, 4) if cap and spent is not None else None,
            "cap_reached": bool(cap is not None and spent is not None and spent >= cap),
            "cap_source": "order" if st.get("cap_override_usd") is not None else ("settings" if cap is not None
                                                                                   else "none"),
            "budget_stop": stop is not None and stop.get("month") == m.get("month"),
            "currency": "USD"}


@_tool("daily_log", "read", 0, "The last daily log",
       "The newest daily log (outbox/daily-log.txt) as plain text, scrubbed again: no file names, paths, links, "
       "secrets, review terms or error text. Lists of uploaded files and held messages become counts.")
def _daily_log(c: _Ctx) -> dict | Outcome:
    from service.paths import service_paths

    outbox = service_paths(c.settings).outbox
    data = _read_bytes(outbox / "daily-log.txt", 400_000)
    if data is None:
        return _refusal(["not_found"])
    log_date = None
    try:
        dated = sorted(p.name for p in outbox.glob("daily-log-????-??-??.txt"))
        if dated:
            log_date = dated[-1][len("daily-log-"):-4]
    except OSError:
        pass
    text = clean_daily_log(data.decode("utf-8", "replace"))
    return {"ok": True, "date": log_date if log_date and TS_RE.fullmatch(log_date) else None, "text": text,
            "cut": len(text) >= MAX_LOG_CHARS}


_FIXED_PROBLEMS = ("Monthly spending cap reached", "Spending warning", "The Etsy connection runs out")
_PROBLEM_WHERE = re.compile(r"  ! (?P<where>[a-z][a-z_ ]{0,30}(?:\[[A-Za-z0-9][A-Za-z0-9._-]{0,159}\])?): ")


def clean_daily_log(text: str) -> str:
    """The daily log for Hermes: section by section, lists of file names and held messages
    become counts, review reasons and error messages are left out (review_list and
    events_recent give their codes), and every line is scrubbed and capped."""
    scrub = _Scrubber()
    out: list[str] = []
    section: str | None = "head"
    held = 0
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            if section == "held" and held:
                out.append(f"  {held} message(s); the daily log on the server lists them.")
            held = 0
            section = None
            out.append("")
            continue
        if section is None:
            section = ("exports" if line == "Exports" else "review" if line.startswith("Waiting for your review")
                       else "problems" if line.startswith("Problems and warnings")
                       else "held" if line.startswith("Discord messages held") else "other")
            out.append(line)
            continue
        if section == "exports" and line.startswith("    "):
            continue                                    # names of uploaded or refused files
        if section == "held":
            held += 1 if line.startswith("  - ") else 0
            continue
        if section == "review" and line.startswith("  - "):
            line = line.split(":", 1)[0]               # the item id; its reasons are review_list's codes
        if section == "problems" and line.startswith("  ! "):
            body = line[4:]
            if not body.startswith(_FIXED_PROBLEMS):
                m = _PROBLEM_WHERE.match(line)
                line = (f"  ! A problem in {m['where']} (details: status.txt on the server)." if m
                        else "  ! A problem (details: status.txt on the server).")
        out.append(scrub.text(line)[:MAX_LOG_LINE])
    if section == "held" and held:
        out.append(f"  {held} message(s); the daily log on the server lists them.")
    joined = "\n".join(out).strip("\n") + "\n"
    return joined[:MAX_LOG_CHARS]


@_tool("review_list", "read", 0, "Review queue",
       "Items in Botsy's review queue: item id, niche, theme, status, the reasons as fixed codes and the "
       "thumbnail's place inside the item (see review_preview). `approvable` says whether a review_approve order "
       "could pass now.",
       status=Arg("string", enum=("pending", "approved", "rejected", "all"), default="pending",
                  description="Which entries."),
       limit=Arg("integer", minimum=1, maximum=50, default=20, description="Most entries returned."))
def _review_list(c: _Ctx) -> dict:
    from qa.review import list_entries
    from service import orders

    s = c.settings
    status = c.args.get("status", "pending")
    entries = list_entries(orders.review_dir(s), None if status == "all" else status)
    root = _work_root(s)
    items = []
    for e in entries:
        view = _entry_view(e, s, root)
        if view:
            items.append(view)
    limit = c.args.get("limit", 20)
    return {"ok": True, "status": status, "count": len(items), "items": items[:limit],
            "more": max(0, len(items) - limit)}


def _entry_view(e: dict, settings: dict, root: Path) -> dict | None:
    from service import orders

    item_id = _item(e.get("item_id"))
    if item_id is None:
        return None
    manifest = _read_json(root / item_id / "manifest.json", 1_000_000)
    manifest = manifest if isinstance(manifest, dict) else {}
    thumbs = [m for m in e.get("mockups") or [] if isinstance(m, str) and THUMB_RE.fullmatch(m) and ".." not in m] \
        if isinstance(e.get("mockups"), list) else []
    status = e.get("status") if e.get("status") in ("pending", "approved", "rejected") else None
    drafted = orders._drafted(settings, item_id)
    qa_ok = orders._qa_passed(settings, item_id)
    return {"item_id": item_id, "niche": _niche(e.get("niche"), settings) or _niche(manifest.get("niche"), settings),
            "theme": _theme(manifest.get("theme")), "status": status, "needs_review": bool(e.get("needs_review")),
            "reasons": reason_codes(e.get("reasons")), "queued": _ts(e.get("queued")),
            "thumbnail": thumbs[0] if thumbs else None, "previews": len(thumbs), "qa_passed": qa_ok,
            "drafted": drafted,
            "approvable": status == "pending" and qa_ok and not drafted and bool(e.get("content_digest"))}


@_tool("review_preview", "read", 0, "Preview image of a review item",
       "One of our own preview images (mockups) of an item in the review queue, at most 768 px, re-encoded as "
       "JPEG. Our own pages only; never a competitor's image.",
       item_id=Arg("string", required=True, pattern=ITEM_RE, max_length=160,
                   description="An item id from review_list."),
       index=Arg("integer", minimum=0, maximum=9, default=0, description="Which preview (0 = the first)."))
def _review_preview(c: _Ctx) -> Outcome:
    from qa.review import read_entry
    from service import orders

    s = c.settings
    item_id = _item(c.args["item_id"])
    if item_id is None:
        return _refusal(["bad_argument"], ["item_id"])
    try:
        e = read_entry(item_id, orders.review_dir(s))
    except (OSError, ValueError):
        e = None
    if not isinstance(e, dict):
        return _refusal(["not_found"])
    thumbs = [m for m in e.get("mockups") or [] if isinstance(m, str) and THUMB_RE.fullmatch(m) and ".." not in m] \
        if isinstance(e.get("mockups"), list) else []
    idx = c.args.get("index", 0)
    if idx >= len(thumbs):
        return _refusal(["no_preview"])
    root = _work_root(s)
    item_dir = root / item_id
    p = item_dir / thumbs[idx]
    if not _inside(item_dir, p) or not _inside(root, item_dir):
        return _refusal(["no_preview"])
    data = _read_bytes(p, PREVIEW_MAX_BYTES)
    img = _jpeg_preview(data) if data else None
    if img is None:
        return _refusal(["no_preview"])
    return Outcome({"ok": True, "item_id": item_id, "index": idx, "previews": len(thumbs), "mime": "image/jpeg",
                    "bytes": len(img)}, [("image/jpeg", img)])


def _jpeg_preview(data: bytes, px: int = PREVIEW_PX) -> bytes | None:
    """Our own preview, decoded and re-encoded (no metadata), at most `px` on its longest side."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP"):
                return None
            if im.width * im.height > PREVIEW_MAX_PIXELS:
                return None             # read only the header so far: never decode a huge image
            im.thumbnail((px, px))
            if im.mode in ("RGBA", "LA", "P"):
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                im2 = bg
            else:
                im2 = im.convert("RGB")
            buf = io.BytesIO()
            im2.save(buf, "JPEG", quality=82)
            return buf.getvalue()
    except Exception as e:  # noqa: BLE001 - a broken image is "no preview"
        log.info("botsy-control: preview not readable (%s)", type(e).__name__)
        return None


ITEM_FILE_RE = re.compile(r"(?P<dir>previews|mockups)/[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.(?:png|jpe?g|webp)", re.I)


def _botsy_item_dir(settings: dict, item_id: str) -> Path | None:
    """The item's folder when `item_id` names a Botsy item: a real folder (not a link) directly in
    Botsy's output root with a manifest that names the same item. Else None."""
    root = _work_root(settings)
    d = root / item_id
    try:
        if d.is_symlink() or not d.is_dir() or not _inside(root, d) or d.resolve().parent != root.resolve():
            return None
    except OSError:
        return None
    m = _read_json(d / "manifest.json", 1_000_000)
    if not isinstance(m, dict) or m.get("item_id") != item_id:
        return None
    return d


def _item_images(settings: dict, item_id: str, item_dir: Path, kind: str) -> list[tuple[str, int | None]]:
    """[(relative path, page or None)] of the item's own images of `kind`, in order: pages from the
    manifest's previews (else the previews/ folder), mockups from the review entry (else the
    mockups/ folder). Only names that match ITEM_FILE_RE in that kind's folder."""
    want = VIEW_KINDS[kind]
    out: list[tuple[str, int | None]] = []

    def ok(rel) -> bool:
        m = ITEM_FILE_RE.fullmatch(rel) if isinstance(rel, str) else None
        return bool(m) and m.group("dir") == want and ".." not in rel

    if kind == "pages":
        m = _read_json(item_dir / "manifest.json", 1_000_000)
        entries = m.get("previews") if isinstance(m, dict) and isinstance(m.get("previews"), list) else []
        rows = [(e.get("path"), _int(e.get("page"), 0, 10_000)) for e in entries if isinstance(e, dict)]
        out = [(rel, page) for rel, page in rows if ok(rel)]
        out.sort(key=lambda x: (x[1] is None, x[1] or 0))
    else:
        from qa.review import read_entry
        from service import orders

        try:
            e = read_entry(item_id, orders.review_dir(settings))
        except (OSError, ValueError):
            e = None
        listed = e.get("mockups") if isinstance(e, dict) and isinstance(e.get("mockups"), list) else []
        out = [(rel, None) for rel in listed if ok(rel)]
    if not out:
        folder = item_dir / want
        try:
            names = sorted(p.name for p in folder.iterdir()) if folder.is_dir() and not folder.is_symlink() else []
        except OSError:
            names = []
        out = [(f"{want}/{n}", None) for n in names if ok(f"{want}/{n}")][:200]
    return list(dict.fromkeys(out))


@_tool("view_item_images", "read", 0, "Look at our own product images",
       "Up to 6 of OUR OWN product images of one Botsy item, as images for your vision: `pages` are the page "
       "renders the generator made (previews), `mockups` are the listing photos QA made. Downscaled to at most "
       "1024 px and re-encoded as JPEG without metadata; about 3 MB in all per call (`truncated` says when fewer "
       "fit). Only items in Botsy's output folder; never research files or anyone else's images.",
       item_id=Arg("string", required=True, pattern=ITEM_RE, max_length=160,
                   description="A Botsy item id (review_list, events_recent, orders_recent)."),
       kind=Arg("string", enum=("pages", "mockups"), default="pages", max_length=10, description="Which images."),
       start=Arg("integer", minimum=0, maximum=199, default=0, description="First image (0 = the first)."),
       max=Arg("integer", minimum=1, maximum=VIEW_MAX_IMAGES, default=VIEW_MAX_IMAGES,
               description="Most images returned."))
def _view_item_images(c: _Ctx) -> Outcome:
    s = c.settings
    item_id = _item(c.args["item_id"])
    if item_id is None:
        return _refusal(["bad_argument"], ["item_id"])
    item_dir = _botsy_item_dir(s, item_id)
    if item_dir is None:
        return _refusal(["not_found"])
    kind = c.args.get("kind", "pages")
    start, most = c.args.get("start", 0), c.args.get("max", VIEW_MAX_IMAGES)
    found = _item_images(s, item_id, item_dir, kind)
    folder = item_dir / VIEW_KINDS[kind]
    images, shown, total, truncated = [], [], 0, False
    for i, (rel, page) in enumerate(found[start:start + most], start):
        p = item_dir / rel
        if not (_inside(folder, p) and _inside(item_dir, p)):
            continue
        data = _read_bytes(p, PREVIEW_MAX_BYTES)
        img = _jpeg_preview(data, VIEW_PX) if data else None
        if img is None:
            continue
        if total + len(img) > VIEW_MAX_TOTAL_BYTES:
            truncated = True
            break
        total += len(img)
        images.append(("image/jpeg", img))
        row = {"index": i, "bytes": len(img)}
        if page is not None:
            row["page"] = page
        shown.append(row)
    if not images:
        return _refusal(["no_preview"], item_id=item_id, kind=kind, available=len(found))
    last = shown[-1]["index"]
    return Outcome({"ok": True, "item_id": item_id, "kind": kind, "available": len(found), "start": start,
                    "shown": len(images), "images": shown, "bytes": total, "truncated": truncated,
                    "more": last + 1 < len(found), "mime": "image/jpeg",
                    "note": "our own product images, made by Botsy"}, images)


@_tool("plan_status", "read", 0, "Weekly plan status",
       "The weekly plan in force for this week and next week (Botsy's ISO weeks, New York time): its order id, "
       "status, lines, packs, cost, whether it waits for the user's approval and why, hold and start time; the plan service's state "
       "(approved plans so far, mode) and Botsy's own draft plan when there is one.")
def _plan_status(c: _Ctx) -> dict:
    from service import orders

    s = c.settings
    this, nxt = orders.plan_weeks(s, c.now)
    weeks = []
    for w in (this, nxt):
        plan = orders.current_plan(s, w)
        versions = [o for o in orders.list_orders(s, week=w) if o.get("kind") in orders.PLAN_KINDS]
        draft = _read_json(_plan_dir(s) / w / "plan.json", 1_000_000)
        weeks.append({"plan_week": w, "plan": _plan_view(plan, s) if plan else None, "versions": len(versions),
                      "draft": _safe(draft) if isinstance(draft, dict) else None})
    state = _read_json(_plan_dir(s) / "state.json", 1_000_000)
    return {"ok": True, "this_week": this, "next_week": nxt, "weeks": weeks,
            "state": _safe(state) if isinstance(state, dict) else None}


def _lines_view(p: dict, settings: dict) -> tuple[list, list]:
    from service import orders

    lines = []
    for line in p.get("lines") or [] if isinstance(p.get("lines"), list) else []:
        if not isinstance(line, dict):
            continue
        bid = line.get("brief_id")
        sizes = line.get("sizes") if isinstance(line.get("sizes"), list) else []
        lines.append({"niche": _niche(line.get("niche"), settings),
                      "brief_id": bid if isinstance(bid, str) and orders.BRIEF_ID_RE.fullmatch(bid) else None,
                      "sizes": [x for x in sizes if _int(x, 1, 500)][:8]})
    kdp = []
    for k in p.get("kdp") or [] if isinstance(p.get("kdp"), list) else []:
        if isinstance(k, dict):
            bid, tpl = k.get("brief_id"), k.get("template")
            kdp.append({"brief_id": bid if isinstance(bid, str) and orders.BRIEF_ID_RE.fullmatch(bid) else None,
                        "template": tpl if isinstance(tpl, str) and orders.SLUG_RE.fullmatch(tpl) else None})
    return lines, kdp


def _plan_view(o: dict, settings: dict) -> dict:
    from service import orders

    p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
    checks = o.get("checks") if isinstance(o.get("checks"), dict) else {}
    lines, kdp = _lines_view(p, settings)
    try:
        start = orders.plan_start_at(o, settings)
    except Exception:  # noqa: BLE001 - a display field
        start = None
    return {"order_id": _order_id(o.get("id")), "kind": o.get("kind") if o.get("kind") in orders.PLAN_KINDS else None,
            "status": _code(o.get("status")), "created_by": o.get("created_by") if o.get("created_by") in
            orders.CREATORS else None, "created_at": _ts(o.get("created_at")), "plan_week": _week(p.get("plan_week")),
            "lines": lines, "kdp": kdp, "packs": sum(len(x["sizes"]) for x in lines),
            "cost_usd": _money(checks.get("cost_usd")), "needs_approval": _needs_approval(o),
            "approval_reason": _code(checks.get("approval_reason")), "plan_number": _int(o.get("plan_number")),
            "revision": _int(o.get("revision")), "replaces": _order_id(p.get("replaces")),
            "superseded_by": _order_id(o.get("superseded_by")), "held": isinstance(o.get("held"), dict),
            "held_by": (lambda h: h.get("by") if h.get("by") in orders.CREATORS else None)(orders.hold_of(o) or {}),
            "start_now": isinstance(o.get("start_now"), dict),
            "start_at": start.astimezone(timezone.utc).isoformat(timespec="seconds") if isinstance(start, datetime)
            else None,
            # The same moment on Botsy's clock (schedule.timezone, New York; the offset follows DST).
            "start_at_local": now_local(settings, start).isoformat(timespec="seconds")
            if isinstance(start, datetime) else None, "applied_at": _ts(o.get("applied_at"))}


@_tool("plan_facts", "read", 0, "Facts for the weekly plan",
       "Aggregated facts for writing a weekly plan: money (spent, cap, left), the capacity check, the newest "
       "briefs per niche (niche, theme, sizes, opportunity; no evidence rows), KDP slots, and the facts file the "
       "plan service wrote for that week (aggregated fields only).",
       week=Arg("string", pattern=re.compile(r"\d{4}-W(0[1-9]|[1-4]\d|5[0-3])"), max_length=8,
                description="The plan week, YYYY-Www (default: next week)."))
def _plan_facts(c: _Ctx) -> dict:
    from service import capacity, orders

    s = c.settings
    this, nxt = orders.plan_weeks(s, c.now)
    week = c.args.get("week") or nxt
    cap = capacity.check(s, now=c.now)
    facts = _read_json(_plan_dir(s) / week / "facts.json")
    approved = _plan_context(s)
    view = _capacity_view(cap)
    money = {k: view["facts"].get(k) for k in ("month", "spent", "cap", "money_left", "projected", "warn_line")}
    return {"ok": True, "plan_week": week, "this_week": this, "next_week": nxt, "money": money, "capacity": view,
            "briefs": _briefs_view(s, None, 10, c.clock),
            "approvals": {"approved_plans": approved.get("approved_plans", 0),
                          "approve_each": approved.get("approve_each", True)},
            "facts": _safe(facts) if isinstance(facts, dict) else None}


@_tool("briefs_summary", "read", 0, "Current briefs",
       "The newest design briefs per niche: brief id, niche, theme, pack sizes and opportunity score. Aggregated "
       "only (each brief stands for at least 10 listings from 3 shops); never evidence rows, keywords or sources.",
       niche=Arg("string", enum_from="niches", max_length=40, description="One niche (default: all)."),
       limit=Arg("integer", minimum=1, maximum=20, default=10, description="Most briefs per niche."))
def _briefs_summary(c: _Ctx) -> dict:
    return {"ok": True, "niches": _briefs_view(c.settings, c.args.get("niche"), c.args.get("limit", 10), c.clock)}


def _briefs_view(settings: dict, niche: str | None, limit: int, now: datetime) -> list:
    today = _today(settings, now)
    out = []
    for n, (day, path) in sorted(_newest_briefs(settings).items()):
        if niche and n != niche:
            continue
        doc = _read_json(path)
        rows = [v for v in (_brief_view(b, settings) for b in _brief_rows(doc)) if v]
        try:
            age = (today - date.fromisoformat(day)).days
        except ValueError:
            age = None
        stage = doc.get("stage") if isinstance(doc, dict) and doc.get("stage") in STAGES else None
        out.append({"niche": n, "date": day, "age_days": age, "stage": stage, "count": len(rows),
                    "briefs": rows[:limit]})
    return out


@_tool("capacity", "read", 0, "Does Botsy have room",
       "The capacity check (capacity/v1): money against the warning line, the review queue, waiting drafts, "
       "Etsy's quota, KDP slots and disk space, with fixed reason codes.")
def _capacity(c: _Ctx) -> dict:
    from service import capacity

    return {"ok": True, "capacity": _capacity_view(capacity.check(c.settings, now=c.now))}


def _capacity_view(res: dict) -> dict:
    res = res if isinstance(res, dict) else {}
    facts = res.get("facts") if isinstance(res.get("facts"), dict) else {}
    th = res.get("thresholds") if isinstance(res.get("thresholds"), dict) else {}
    out_facts = {}
    for k, v in facts.items():
        if not isinstance(k, str) or not KEY_RE.fullmatch(k):
            continue
        if k == "month":
            out_facts[k] = v if isinstance(v, str) and MONTH_RE.fullmatch(v) else None
        elif k.endswith("_source"):
            out_facts[k] = _code(v)
        elif v is None or _num(v):
            out_facts[k] = v
    return {"ok": bool(res.get("ok")), "reasons": _codes(res.get("reasons")), "facts": out_facts,
            "thresholds": {k: v for k, v in th.items() if isinstance(k, str) and KEY_RE.fullmatch(k) and _num(v)},
            "checked_at": _ts(res.get("checked_at"))}


@_tool("orders_recent", "read", 0, "Recent orders",
       "Orders Botsy stored lately (from Hermes, you and Botsy itself), newest first: id, kind, level, who, "
       "status, week and the checked payload fields.",
       days=Arg("integer", minimum=1, maximum=60, default=14, description="How far back."),
       kind=Arg("string", enum=tuple(sorted(("weekly_plan", "plan_revision", "plan_hold", "plan_start_now",
                                             "scale_up", "order_cancel", "pause", "resume", "lower_cap",
                                             "review_reject", "review_hold", "review_approve", "raise_cap",
                                             "resume_after_budget_stop", "search_ideas", "fix_retry_run",
                                             "fix_clear_stale_lock", "fix_rerun_qa", "fix_resend_daily_log",
                                             "fix_retry_notify", "kdp_kit_approve"))),
                description="Only this kind."),
       status=Arg("string", enum=("waiting_approval", "accepted", "applied", "undone", "cancelled", "failed"),
                  description="Only this status."),
       limit=Arg("integer", minimum=1, maximum=100, default=20, description="Most orders returned."))
def _orders_recent(c: _Ctx) -> dict:
    from service import orders

    s = c.settings
    since = c.clock - timedelta(days=c.args.get("days", 14))
    rows = []
    for o in reversed(orders.list_orders(s, kind=c.args.get("kind"), status=c.args.get("status"))):
        t = orders._parse(o.get("created_at"))
        if t is None or t < since:
            continue
        rows.append(_order_view(o, s))
    limit = c.args.get("limit", 20)
    return {"ok": True, "count": len(rows), "orders": rows[:limit], "more": max(0, len(rows) - limit)}


def _order_view(o: dict, settings: dict) -> dict:
    from service import orders

    kind = o.get("kind") if o.get("kind") in orders.KINDS else None
    p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
    checks = o.get("checks") if isinstance(o.get("checks"), dict) else {}
    view = {"order_id": _order_id(o.get("id")), "kind": kind, "level": _int(o.get("level"), 0, 3),
            "created_by": o.get("created_by") if o.get("created_by") in orders.CREATORS else None,
            "created_at": _ts(o.get("created_at")), "week": _week(o.get("week")), "status": _code(o.get("status")),
            "needs_approval": _needs_approval(o), "applied_at": _ts(o.get("applied_at"))}
    if kind in orders.PLAN_KINDS:
        lines, kdp = _lines_view(p, settings)
        view["payload"] = {"plan_week": _week(p.get("plan_week")), "lines": lines, "kdp": kdp,
                           "replaces": _order_id(p.get("replaces"))}
        view["cost_usd"] = _money(checks.get("cost_usd"))
        view["plan_number"] = _int(o.get("plan_number"))
    elif kind in ("plan_hold", "plan_start_now"):
        view["payload"] = {"plan_week": _week(p.get("plan_week"))}
    elif kind == "order_cancel":
        view["payload"] = {"order_id": _order_id(p.get("order_id"))}
    elif kind == "pause":
        view["payload"] = {"reason_code": _code(p.get("reason_code"))}
    elif kind in ("lower_cap", "raise_cap"):
        view["payload"] = {"cap_usd": _money(p.get("cap_usd"))}
    elif kind in ("review_reject", "review_hold"):
        view["payload"] = {"item_id": _item(p.get("item_id")), "reason_code": _code(p.get("reason_code"))}
    elif kind == "review_approve":
        ids = p.get("item_ids") if isinstance(p.get("item_ids"), list) else []
        batch = p.get("batch")
        view["payload"] = {"item_ids": [i for i in (_item(x) for x in ids) if i],
                           "batch": batch if isinstance(batch, str) and orders.SLUG_RE.fullmatch(batch) else None}
    elif kind == "search_ideas":
        ideas = p.get("ideas") if isinstance(p.get("ideas"), list) else []
        view["payload"] = {"ideas": [{"niche": _niche(i.get("niche"), settings),
                                      "query": i.get("query") if isinstance(i.get("query"), str)
                                      and orders.QUERY_RE.fullmatch(i["query"]) else None}
                                     for i in ideas if isinstance(i, dict)]}
    else:
        view["payload"] = {}
    return view


# Per event kind, the fields passed on and how each is checked. Kinds not listed (and not
# "plan_*" / "no_plan" from the plan service, which get the generic projection) are left out.
_EVENT_FIELDS: dict[str, dict[str, str]] = {
    "export_received": {"tool": "code", "rows": "count", "shape": "code"},
    "export_rejected": {}, "export_routed": {}, "search_list_written": {},
    "briefs_written": {"niche": "niche", "stage": "stage", "count": "count"},
    "stage_changed": {"from": "stage", "to": "stage", "drafts": "count"},
    "item_generated": {"item_id": "item", "niche": "niche", "dry_run": "bool"},
    "qa_failed": {"item_id": "item", "checks": "codes"},
    "listing_written": {"item_id": "item", "dry_run": "bool"},
    "draft_created": {"item_id": "item", "dry_run": "bool"},
    "held_for_review": {"item_id": "item", "reasons": "review", "dry_run": "bool"},
    "spend_warning": {"month_to_date": "money", "cap": "money", "pct": "money"},
    "budget_reached": {"month_to_date": "money", "cap": "money"},
    "token_expiring": {"days_left": "count"},
    "error": {"where": "where"},
    "notification_held": {"kinds": "codes"},
    "order_accepted": {"order_id": "order", "order_kind": "code", "level": "count", "created_by": "code",
                       "week": "week", "needs_approval": "bool", "status": "code"},
    "order_refused": {"order_kind": "code", "created_by": "code", "stage": "code", "reasons": "codes",
                      "fields": "fields"},
    "order_applied": {"order_id": "order", "order_kind": "code", "created_by": "code", "week": "week",
                      "approved_by": "code"},
    "order_approved": {"order_id": "order", "order_kind": "code", "level": "count", "by": "code", "count": "count",
                       "announced": "bool"},
    "order_undone": {"order_id": "order", "order_kind": "code", "by": "code"},
    "order_failed": {"order_id": "order", "order_kind": "code", "stage": "code", "reasons": "codes"},
    "control_refused": {"tool": "code", "reasons": "codes"},
    # Watchdog fixes (service/fixes.py, D4.7): the precondition facts are fixed fields only.
    "fix_applied": {"order_id": "order", "fix": "code", "recipe": "code", "created_by": "code", "facts": "safe",
                    "result": "safe"},
    "fix_run": {"order_id": "order", "fix": "code", "ran": "bool", "exit_code": "count", "reasons": "codes",
                "facts": "safe"},
}
EVENT_KINDS = tuple(sorted(_EVENT_FIELDS))


def _event_kind_ok(kind) -> bool:
    return isinstance(kind, str) and KEY_RE.fullmatch(kind) is not None and \
        (kind in _EVENT_FIELDS or kind.startswith("plan_") or kind == "no_plan")


def _event_view(e: dict, settings: dict) -> dict | None:
    kind = e.get("kind")
    if not _event_kind_ok(kind):
        return None
    out: dict = {"ts": _ts(e.get("ts")), "kind": kind}
    spec = _EVENT_FIELDS.get(kind)
    if spec is None:                    # the plan service's kinds: the generic projection
        rest = _safe({k: v for k, v in e.items() if k not in ("ts", "kind")})
        out.update(rest if isinstance(rest, dict) else {})
        return out
    for key, how in spec.items():
        v = e.get(key)
        if how == "item":
            out[key] = _item(v)
        elif how == "niche":
            out[key] = _niche(v, settings)
        elif how == "bool":
            out[key] = v if isinstance(v, bool) else None
        elif how == "count":
            out[key] = _int(v)
        elif how == "money":
            out[key] = _money(v)
        elif how == "code":
            out[key] = _code(v)
        elif how == "codes":
            out[key] = _codes(v)
        elif how == "review":
            out[key] = reason_codes(v)
        elif how == "stage":
            out[key] = v if v in STAGES else None
        elif how == "order":
            out[key] = _order_id(v)
        elif how == "week":
            out[key] = _week(v)
        elif how == "ts":
            out[key] = _ts(v)
        elif how == "safe":
            v = _safe(v) if isinstance(v, dict) else None
            out[key] = v if isinstance(v, dict) else {}
        elif how == "fields":
            out[key] = [x for x in (v if isinstance(v, list) else []) if isinstance(x, str)
                        and FIELD_PATH_RE.fullmatch(x)][:30]
        elif how == "where":
            m = WHERE_RE.fullmatch(v) if isinstance(v, str) else None
            out["stage"] = m["stage"].strip().replace(" ", "_") if m else "other"
            out["item_id"] = _item(m["target"]) if m and m["target"] else None
    return out


@_tool("events_recent", "read", 0, "Recent events",
       "What Botsy did lately, from its event journal, as signed fixed-field events (HMAC-SHA256 with "
       "BOTSY_EVENT_KEY, \"sig\": \"v1=<hex>\"): item ids, counts, money and reason codes only, never text, file "
       "names or titles. Oldest first.",
       hours=Arg("integer", minimum=1, maximum=168, default=24, description="How far back."),
       kinds=Arg("array", items=Arg("string", enum=EVENT_KINDS + ("plan",), max_length=40), max_items=30,
                 unique=True, description='Only these kinds ("plan" = the plan service\'s plan_* kinds).'),
       limit=Arg("integer", minimum=1, maximum=200, default=50, description="Most events returned (the newest)."))
def _events_recent(c: _Ctx) -> dict:
    from core import events

    s = c.settings
    want = set(c.args.get("kinds") or [])
    rows = []
    for e in events.read(c.clock - timedelta(hours=c.args.get("hours", 24)), settings=s):
        kind = e.get("kind") if isinstance(e, dict) else None
        if want and kind not in want and not ("plan" in want and _event_kind_ok(kind) and kind not in _EVENT_FIELDS):
            continue
        view = _event_view(e, s)
        if view is not None:
            rows.append(view)
    limit = c.args.get("limit", 50)
    rows = rows[-limit:]
    # Scrubbed before signing (the last scrub then changes nothing), so a signature always
    # matches exactly the fields Hermes gets.
    scrub = _Scrubber()
    signed = [events.sign(_scrub_tree(r, scrub)) for r in rows]
    return {"ok": True, "signed": bool(signed) and all("sig" in r for r in signed), "count": len(signed),
            "events": signed}


TREND_QUERY_RE = re.compile(r"[a-z0-9](?:[a-z0-9 '&-]{0,78}[a-z0-9])?")


@_tool("trend_search", "read", 0, "Trend web search (filtered)",
       "Search the web for off-marketplace trend signals (blogs, news, magazines) through TinyFish Search. Botsy "
       "drops every result from a marketplace or listing site (Etsy, Amazon incl. Goodreads/AbeBooks/Audible, "
       "Pinterest, eBay, Redbubble, Teepublic, Zazzle, Creative Market, Gumroad, Society6, Spreadshirt, Walmart, "
       "Temu, AliExpress, Shein, TikTok Shop, digital-product shops such as Teachers Pay Teachers or Creative "
       "Fabrica, archived or proxied copies of them), every result that names one or reads like a product listing "
       "(price + add to cart / sold / in stock / free shipping), and every result with a blocklisted term; links "
       "come back as domain + path only and snippets are short. A trend reading only, never a brief by itself: "
       "briefs still need the user's exports (10 listings, 3 shops). The query is checked against the trademark "
       "blocklist and health terms first and may not name a marketplace. At most limits trend_searches_per_day "
       "(default 30) in any 24 hours.",
       open_world=True,
       query=Arg("string", required=True, pattern=TREND_QUERY_RE, max_length=80,
                 description="Short lowercase query: letters, digits, spaces, ' & -."),
       recency=Arg("string", enum=("week", "month", "year"), default="month", max_length=5,
                   description="How recent the pages are."),
       max=Arg("integer", minimum=1, maximum=10, default=10, description="Most results returned."))
def _trend_search(c: _Ctx) -> Outcome:
    from control import trend

    q = " ".join(c.args["query"].split())
    res = trend.search(q, c.args.get("recency", "month"), c.args.get("max", 10), c.settings, now=c.now)
    if not res.get("ok"):
        reasons = _codes(res.get("reasons")) or ["search_failed"]
        extra = {k: _int(res.get(k), 0, 10**6) for k in ("searches_24h", "limit") if k in res}
        return _refusal(reasons, **extra)
    results = []
    for r in res.get("results") or []:
        row = {"site": r.get("site"), "page": r.get("page"), "title": r.get("title"), "snippet": r.get("snippet")}
        if r.get("date"):
            row["date"] = r["date"]
        results.append(row)
    dropped = {k: _int(v, 0, 1000) for k, v in (res.get("dropped") or {}).items() if _code(k)}
    return Outcome({"ok": True, "label": trend.LABEL, "recency": res.get("recency"), "count": len(results),
                    "results": results, "received": _int(res.get("received"), 0, 1000), "dropped": dropped,
                    "searches_24h": _int(res.get("searches_24h"), 0, 10**6), "limit": _int(res.get("limit"), 0, 10**6),
                    "left": _int(res.get("left"), 0, 10**6)})


# ---------------------------------------------------------------- act tools (orders)

ORDER_KINDS = ("weekly_plan", "plan_revision", "plan_hold", "plan_start_now", "scale_up", "order_cancel", "pause",
               "resume", "lower_cap", "review_reject", "review_hold", "review_approve", "raise_cap",
               "resume_after_budget_stop", "search_ideas", "fix_retry_run", "fix_clear_stale_lock", "fix_rerun_qa",
               "fix_resend_daily_log", "fix_retry_notify", "kdp_kit_approve")
# The former code actions (decided 2026-10-21: no codes). Only when the user asked for it in
# their own words; Botsy caps them a day, never approves a hard-blocked item, never raises the
# cap above the admin ceiling, and posts each at once to the main channel with who asked.
LEVEL3_KINDS = ("review_approve", "raise_cap", "resume_after_budget_stop", "kdp_kit_approve")
LEVEL3_NOTE = ("only when the user asked for it in their own words; Botsy allows a few a day and posts each at "
               "once to the main channel, with how to undo it")
PAYLOAD_HELP = (
    'Payload per kind (no other fields; values from fixed lists): '
    'weekly_plan {"plan_week": "YYYY-Www", "lines": [{"niche", "brief_id", "sizes": [int]}] (1-40), '
    '"kdp": [{"brief_id", "template"}] (0-2)}; plan_revision: the same plus "replaces": "<plan order id>"; '
    'plan_hold / plan_start_now {"plan_week"}; scale_up: refused until Botsy has winners (no_winners_yet); '
    'order_cancel {"order_id"}; pause {"reason_code"?: user_request|incident|spend|quality|other}; resume {}; '
    'lower_cap {"cap_usd"}; raise_cap {"cap_usd"} (Level 3); resume_after_budget_stop {} (Level 3); '
    'review_reject {"item_id", "reason_code": print_defect|off_theme|low_quality|lookalike|trademark_risk|'
    'medical_wording|duplicate|wrong_size|user_dislike|other_rule}; review_hold {"item_id", "reason_code": '
    'needs_a_look|possible_lookalike|possible_trademark|possible_medical|print_check|user_request|undo_approval}; '
    'review_approve {"item_ids": [...], "batch"?: "short-name"} (Level 3); search_ideas {"ideas": '
    '[{"niche", "query"}]} (at most 5 a week); fix_retry_run {"tick_started"?: ISO time}, fix_clear_stale_lock '
    '{"lock": service|plans}, fix_rerun_qa {"item_id"}, fix_resend_daily_log {}, fix_retry_notify {} (Level 2, at '
    'most 6 a day; Botsy checks every precondition; the fix_* tools do the same); kdp_kit_approve {"kit_id"} '
    '(Level 3, refused with kdp_not_built until the KDP build exists). Level 3 orders: ' + LEVEL3_NOTE + '.')
_TERMINAL = ("applied", "undone", "cancelled", "failed")


def _plan_module():
    try:
        from service import plan as plan_mod
    except ImportError:
        return None
    except Exception as e:  # noqa: BLE001 - a broken plan service means plans are refused (fail closed)
        log.warning("botsy-control: the plan service did not load (%s)", type(e).__name__)
        return None
    return plan_mod


def _plan_context(settings: dict) -> dict:
    """{"approved_plans", "approve_each"}: the plan service's order_context(load_state(settings),
    settings), shown by plan_facts. Without the plan service (or when it fails): 0 approved
    plans and approve each."""
    mod = _plan_module()
    fn, load = (getattr(mod, "order_context", None), getattr(mod, "load_state", None)) if mod else (None, None)
    ctx = None
    if callable(fn) and callable(load):
        try:
            ctx = fn(load(settings), settings)
        except Exception as e:  # noqa: BLE001 - fail closed
            log.warning("botsy-control: plan order context failed (%s)", type(e).__name__)
    ctx = ctx if isinstance(ctx, dict) else {}
    approved = ctx.get("approved_plans")
    return {"approved_plans": approved if _int(approved, 0, 10**6) is not None else 0,
            "approve_each": ctx.get("approve_each") is not False}


def _submit(c: _Ctx, kind: str, payload: dict, client_ref: str | None = None) -> Outcome:
    from service import orders

    order = {"schema": orders.SCHEMA, "kind": kind, "created_by": CREATED_BY, "payload": payload}
    if client_ref:
        order["client_ref"] = client_ref
    if kind in orders.PLAN_KINDS:
        # The plan service checks the briefs (exists, at most 7 days old, has those sizes, theme not
        # blocked), estimates the cost and knows your approval count, then calls orders.submit.
        # Without it there is no plan mode to run a plan, so plans are refused (fail closed).
        mod = _plan_module()
        plan_submit = getattr(mod, "submit_order", None) if mod is not None else None
        if not callable(plan_submit):
            return _refusal(["plan_service_missing"], kind=kind)
        return _submit_view(plan_submit(order, c.settings, now=c.now), kind)
    return _submit_view(orders.submit(order, c.settings, now=c.now), kind)


def _submit_view(res: dict, kind: str) -> Outcome:
    from service import orders

    res = res if isinstance(res, dict) else {}
    o = res.get("order") if isinstance(res.get("order"), dict) else {}
    stored = o.get("kind") if o.get("kind") in orders.KINDS else None
    status = _code(res.get("status"))
    waiting = status == "waiting_approval" or (bool(res.get("needs_approval")) and status not in _TERMINAL)
    data = {"ok": bool(res.get("ok")), "kind": stored or kind, "order_id": _order_id(res.get("order_id")),
            "status": status, "needs_approval": waiting,
            "duplicate": bool(res.get("duplicate")), "stage": _code(res.get("stage")),
            "reasons": _codes(res.get("reasons")), "level": _int(o.get("level"), 0, 3),
            "week": _week(o.get("week")), "cost_usd": _money(res.get("cost_usd"))}
    if res.get("fields"):
        data["fields"] = [f for f in res["fields"] if isinstance(f, str) and FIELD_PATH_RE.fullmatch(f)][:30]
    if data["reasons"]:
        data["explain"] = {r: describe(r) for r in data["reasons"]}
    if _int(o.get("plan_number")):
        data["plan_number"] = o["plan_number"]
    if res.get("converted"):
        data["converted_to_revision"] = True        # the plan service made it a revision of Botsy's draft
    if data["ok"] and waiting and not data["duplicate"]:
        data["next"] = ("this order waits for the user's approval: ask them, and only when they say yes in their "
                        "own words call approve_order with this order id")
    for key in ("applied", "plan"):
        r = res.get(key)
        if isinstance(r, dict):
            data[key] = _apply_view(r)
    return Outcome(data)


def _apply_view(r: dict) -> dict:
    r = r if isinstance(r, dict) else {}
    reasons = _codes(r.get("reasons"))
    out = {"ok": bool(r.get("ok")), "order_id": _order_id(r.get("order_id")), "status": _code(r.get("status")),
           "stage": _code(r.get("stage")), "reasons": reasons}
    if reasons:
        out["explain"] = {x: describe(x) for x in reasons}
    if isinstance(r.get("result"), dict):
        out["result"] = _safe(r["result"])
    if isinstance(r.get("undo"), dict):
        out["undo"] = _safe(r["undo"])               # how to undo it (an order kind and ids)
    return out


@_tool("submit_order", "act", 2, "Submit an order",
       "Ask Botsy to change something. Botsy checks every order in full (format, state, the admin-only limits, "
       "capacity, the cap) and refuses with reason codes when it does not fit. Level 2 orders apply at once or "
       "at their start time and can be undone. Level 3 orders (review_approve, raise_cap, "
       "resume_after_budget_stop): " + LEVEL3_NOTE + "; Botsy never approves a hard-blocked item and never raises "
       "the cap above the admin ceiling. The first 4 weekly plans, and a plan over the cost share, wait for the "
       "user's approval (approve_order). " + PAYLOAD_HELP,
       kind=Arg("string", required=True, enum=ORDER_KINDS, max_length=40, description="The order kind."),
       payload=Arg("object", required=True, payload=True, description="The order's fields (see the tool description)."),
       client_ref=Arg("string", pattern=re.compile(r"[a-z0-9][a-z0-9-]{0,39}"), max_length=40,
                      description="Optional ref; the same ref twice makes one order."))
def _submit_order(c: _Ctx) -> Outcome:
    return _submit(c, c.args["kind"], c.args["payload"], c.args.get("client_ref"))


def _approve_plan(c: _Ctx, o: dict) -> Outcome:
    """A plan that waits for the user: the plan service's approve (its deadline, daily limit,
    hold and Discord post), for exactly this order, without running a check in this process
    (the next hourly plan check makes the first items)."""
    import inspect

    mod = _plan_module()
    fn = getattr(mod, "approve", None) if mod is not None else None
    if not callable(fn):
        return _refusal(["plan_service_missing"], order_id=_order_id(o.get("id")))
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    # Both are needed: the order id binds the approval to the plan Hermes named (never another
    # week's), and first_check=False keeps a whole check (paid work) out of this server.
    if not ("order_id" in params and "first_check" in params and "by" in params):
        return _refusal(["approval_unavailable"], order_id=_order_id(o.get("id")))
    res = fn(c.settings, by=CREATED_BY, now=c.now, order_id=o["id"], first_check=False)
    res = res if isinstance(res, dict) else {}
    reason = _code(res.get("reason"))
    reasons = [reason] if reason and not res.get("ok") else []
    data = {"ok": bool(res.get("ok")), "kind": o.get("kind"), "order_id": _order_id(res.get("order_id"))
            or _order_id(o.get("id")), "step": _code(res.get("step")), "status": _code(res.get("status")),
            "week": _week(res.get("week")), "reasons": reasons}
    if reason and res.get("ok"):
        data["note"] = reason                       # e.g. starts_by_itself, plan_already_started
    if reasons:
        data["explain"] = {r: describe(r) for r in reasons}
    if data["ok"] and data["step"] == "started":
        data["next"] = "the plan's first items are made at the next hourly plan check"
    return Outcome(data)


@_tool("approve_order", "act", 3, "Approve an order that waits for the user",
       "Approve a stored order that waits for the user's approval (the first 4 weekly plans, a plan over the "
       "cost share): " + LEVEL3_NOTE + ". Botsy checks it again in full (its deadline, its hold, the money, the "
       "daily limit) and approves exactly the stored order you name, never anything else.",
       order_id=Arg("string", required=True, pattern=re.compile(r"[a-z]{2}-\d{8}-\d{4}"), max_length=16,
                    description="The order id from plan_status or orders_recent."))
def _approve_order(c: _Ctx) -> Outcome:
    from service import orders

    o = orders.load(c.args["order_id"], c.settings)
    if not isinstance(o, dict) or o.get("kind") not in orders.KINDS:
        return _refusal(["not_found"])
    if not (o.get("status") == "waiting_approval" and not o.get("superseded_by")):
        return _refusal(["not_waiting"], order_id=_order_id(o.get("id")), status=_code(o.get("status")))
    if o.get("kind") in orders.PLAN_KINDS:
        return _approve_plan(c, o)
    fn = getattr(orders, "approve", None)
    if not callable(fn):
        return _refusal(["approval_unavailable"], order_id=_order_id(o.get("id")))
    view = _apply_view(fn(o["id"], c.settings, by=CREATED_BY, now=c.now))
    view["kind"] = o["kind"]
    return Outcome(view)


@_tool("pause", "act", 2, "Pause Botsy's paid work",
       "Stop new paid work (images and listing text) until resumed. Always allowed.",
       reason_code=Arg("string", enum=("user_request", "incident", "spend", "quality", "other"),
                       default="user_request", description="Why."))
def _pause(c: _Ctx) -> Outcome:
    return _submit(c, "pause", {"reason_code": c.args.get("reason_code", "user_request")})


@_tool("resume", "act", 2, "Resume after a pause",
       "Lift a pause. Lifting your own (Hermes's) pause applies at once. Lifting the user's pause or Botsy's own "
       "stop is Level 3: " + LEVEL3_NOTE + ". After a budget stop use submit_order resume_after_budget_stop.")
def _resume(c: _Ctx) -> Outcome:
    return _submit(c, "resume", {})


@_tool("lower_cap", "act", 2, "Lower the monthly cap",
       "Lower the monthly spending cap (US dollars). Only lower; raising is a raise_cap order (Level 3, never "
       "above the admin ceiling).",
       cap_usd=Arg("number", required=True, minimum=1, maximum=10000, description="The new, lower cap."))
def _lower_cap(c: _Ctx) -> Outcome:
    return _submit(c, "lower_cap", {"cap_usd": round(float(c.args["cap_usd"]), 2)})


@_tool("search_ideas", "act", 1, "Suggest searches",
       "Suggest searches for the user's weekly export session (at most 5 a week). Botsy checks each against the "
       "blocklist and the health rules and marks them exploratory; they never skip the 10-listing / 3-shop rule.",
       ideas=Arg("array", required=True, min_items=1, max_items=5,
                 items=Arg("object", fields=(("niche", Arg("string", required=True, enum_from="niches", max_length=40)),
                                             ("query", Arg("string", required=True, max_length=60,
                                                           pattern=re.compile(
                                                               r"[a-z0-9](?:[a-z0-9 '&-]{0,58}[a-z0-9])?"))))),
                 description="Search ideas: niche and a short lowercase query."))
def _search_ideas(c: _Ctx) -> Outcome:
    return _submit(c, "search_ideas", {"ideas": c.args["ideas"]})


@_tool("undo_order", "act", 2, "Undo an applied order",
       "Undo an applied order with the record Botsy saved. Hermes may undo its own orders, and any undo that only "
       "stops work or makes things stricter; never the user's pause, rejection, hold or lower cap.",
       order_id=Arg("string", required=True, pattern=re.compile(r"[a-z]{2}-\d{8}-\d{4}"), max_length=16,
                    description="The order to undo."))
def _undo_order(c: _Ctx) -> Outcome:
    from service import orders

    return Outcome(_apply_view(orders.undo(c.args["order_id"], c.settings, by=CREATED_BY, now=c.now)))


# ---------------------------------------------------------------- act tools (watchdog fixes, D4.7)

FIX_NOTE = ("A fixed Botsy recipe (service/fixes.py, Level 2): Botsy checks its own preconditions first and again "
            "when it runs, refuses with reason codes when they do not hold, allows a few a day (limits "
            "fixes_per_day), and posts each fix to Discord. You never name a file, a process or a command.")
ISO_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?")   # orders.ISO_RE


@_tool("fix_retry_run", "act", 2, "Retry the last failed check",
       "Queue one retry of the last hourly check when it failed only for a passing reason (a network timeout, a "
       "dropped connection, a busy lock, an API overload), no check ran since and paid work is not stopped. It "
       "counts toward run_now_per_day; the next hourly plan check runs it. " + FIX_NOTE,
       tick_started=Arg("string", pattern=ISO_TIME_RE, max_length=40,
                        description="Optional: the failed check's start time (status), so the retry is for that one."))
def _fix_retry_run(c: _Ctx) -> Outcome:
    payload = {"tick_started": c.args["tick_started"]} if c.args.get("tick_started") else {}
    return _submit(c, "fix_retry_run", payload)


@_tool("fix_clear_stale_lock", "act", 2, "Clear a stale lock note",
       "Clear the note of a lock whose process is dead, that is older than the longest allowed run and that nobody "
       "holds. A held lock is never touched. " + FIX_NOTE,
       lock=Arg("string", required=True, enum=("service", "plans"), max_length=10, description="Which lock."))
def _fix_clear_stale_lock(c: _Ctx) -> Outcome:
    return _submit(c, "fix_clear_stale_lock", {"lock": c.args["lock"]})


@_tool("fix_rerun_qa", "act", 2, "Run QA again for one item",
       "Run QA again for an item whose QA crashed (never one that failed real checks, a drafted or a rejected "
       "item). Local, no money. " + FIX_NOTE,
       item_id=Arg("string", required=True, pattern=ITEM_RE, max_length=160, description="The item id."))
def _fix_rerun_qa(c: _Ctx) -> Outcome:
    item_id = _item(c.args["item_id"])
    if item_id is None:
        return _journal_refusal(TOOLS["fix_rerun_qa"], c.settings, _refusal(["bad_argument"], ["item_id"]))
    return _submit(c, "fix_rerun_qa", {"item_id": item_id})


@_tool("fix_resend_daily_log", "act", 2, "Post the daily log again",
       "Post the newest daily log to Discord again when it is at most 36 hours old and was not resent already. "
       + FIX_NOTE)
def _fix_resend_daily_log(c: _Ctx) -> Outcome:
    return _submit(c, "fix_resend_daily_log", {})


@_tool("fix_retry_notify", "act", 2, "Retry Discord notices",
       "Post again the approval and fix notices Discord did not take (the last 48 hours, a few at a time). "
       + FIX_NOTE)
def _fix_retry_notify(c: _Ctx) -> Outcome:
    return _submit(c, "fix_retry_notify", {})


FIX_TOOLS = ("fix_retry_run", "fix_clear_stale_lock", "fix_rerun_qa", "fix_resend_daily_log", "fix_retry_notify")


__all__ = [
    "FIX_TOOLS",
    "LEVEL3_KINDS",
    "CREATED_BY",
    "EVENT_KINDS",
    "ORDER_KINDS",
    "REASONS",
    "SCHEMA",
    "SERVERS",
    "TOOLS",
    "VERSION",
    "Outcome",
    "Tool",
    "call",
    "clean_daily_log",
    "describe",
    "input_schema",
    "reason_codes",
    "tools_for",
]
