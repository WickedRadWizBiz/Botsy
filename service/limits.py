"""The admin-only limits file (docs/OVERSEER.md "Four permission levels", "Lock-down" and
Part A4): /etc/botsy/limits.yaml, owned by root, mode 0644.

    load(settings=None, *, path=None) -> Limits     (never raises)
    limits_path(settings=None) -> Path
    DEFAULTS, REASONS, SCHEMA

What it holds: Hermes's change limits (20 changes an hour, 10 page redos a week, 3 "run now"
a day, 6 watchdog fixes a day), the $100 cap ceiling only root can change, the plan's auto-start share of the cap,
the scale-up limits (off until there are winners), the capacity check's thresholds, the
batch size and the daily limit on Level 3 approvals (no codes: user, 2026-10-21). setup.sh
copies config/limits.example.yaml there only when no file exists, and changing it needs sudo.

Botsy never writes it and Hermes cannot reach it: this module has no write function, and
nothing in Botsy takes a limit from an order, the settings or the environment. The path
comes from settings control.limits_path (tests point it at a temp file).

FAIL CLOSED. A missing, unreadable or broken file, or one that someone other than root
could change, gives Limits(ok=False) with a fixed reason code and a plain-words detail:
- Level 2 changes are off (service/orders.py refuses them with `limits_unavailable`),
  except the ones that only stop work or make things stricter (pause, cancel, hold,
  lower the cap);
- scale-ups are off;
- raising the cap is off (the ceiling is unknown);
- reads still work, and the values are the shipped defaults, so a status page can show
  them (marked as not in force).
Keys left out of a good file take the defaults below. Keys this version does not know are
ignored (logged once), so a file written for a later version still loads; they can never
loosen a limit because nothing reads them. A key with a wrong type or a value out of range
makes the whole file broken: one bad limit is never replaced by a guess.

While pytest runs nothing here touches /etc: tests pass a path or settings
control.limits_path, and control.limits_require_root: false skips the root-owner check
(the not-writable-by-others check always runs).
"""
from __future__ import annotations

import copy
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.config import section

log = logging.getLogger("service.limits")

SCHEMA = "limits/v1"
DEFAULT_PATH = "/etc/botsy/limits.yaml"
MAX_BYTES = 64 * 1024

# Settings "control" keys this module reads (core.config.section(settings, "control", ...)).
SETTINGS_DEFAULTS: dict = {
    "limits_path": DEFAULT_PATH,
    "limits_require_root": True,     # the file and its folder must be root's and not writable by others
}

# The shipped values (config/limits.example.yaml repeats them). Used for keys a good file
# leaves out, and shown (not in force) when the file is missing or broken.
DEFAULTS: dict = {
    "schema": SCHEMA,
    "changes_per_hour": 20,          # Hermes's Level 2 changes in any 60 minutes
    "page_redos_per_week": 10,
    "run_now_per_day": 3,            # extra checks a day (a watchdog retry counts as one)
    "fixes_per_day": 6,              # Hermes's watchdog fixes (service/fixes.py) in any 24 hours
    "cap_ceiling_usd": 100,          # spend cap ceiling; only root changes it (raise_cap never goes above)
    "level3_per_day": 10,            # approvals in any 24 hours: every approval Hermes makes, and yours only
                                     # when they lead to posting on Etsy or KDP (review / KDP kit approvals; D1)
    "approve_batch_max": 20,         # items one batch approval may approve
    "search_ideas_per_week": 5,      # Hermes's search ideas per ISO week (Level 1)
    "plan": {
        "max_cost_share_auto": 1.0,    # user, 2026-10-21: no cost gate; a plan above this share of the cap waits for your approval
    },
    "scale_up": {
        "enabled": False,              # off until winners exist (about 20 listings live 30 days)
        "packs_per_week": 3,
        "max_share": 0.30,             # at most max(1, floor(0.30 x the week's packs))
        "min_new_share": 0.50,         # at least half the week's packs are themes new to the shop
        "monthly_usd": 6,
        "per_theme_open": 1,
        "per_theme_max_listings": 8,
        "auto_stop_failures": 2,
        "auto_stop_days": 60,
    },
    "capacity": {
        "max_review_queue": 30,        # pending items in Botsy's review queue
        "max_waiting_drafts": 40,      # Etsy drafts waiting for you to publish
        "min_etsy_quota_share": 0.30,  # share of Etsy's daily request quota left
        "min_disk_free_mb": 2000,
    },
}

# (kind, low, high): "int" and "number" are inclusive ranges; "bool" is a real YAML true/false.
SPEC: dict = {
    "changes_per_hour": ("int", 0, 1000),
    "page_redos_per_week": ("int", 0, 1000),
    "run_now_per_day": ("int", 0, 100),
    "fixes_per_day": ("int", 0, 100),
    "cap_ceiling_usd": ("number", 1, 10000),
    "level3_per_day": ("int", 0, 1000),
    "approve_batch_max": ("int", 1, 200),
    "search_ideas_per_week": ("int", 0, 50),
    "plan": {
        "max_cost_share_auto": ("number", 0, 1),
    },
    "scale_up": {
        "enabled": ("bool",),
        "packs_per_week": ("int", 0, 50),
        "max_share": ("number", 0, 1),
        "min_new_share": ("number", 0, 1),
        "monthly_usd": ("number", 0, 1000),
        "per_theme_open": ("int", 0, 10),
        "per_theme_max_listings": ("int", 0, 1000),
        "auto_stop_failures": ("int", 1, 50),
        "auto_stop_days": ("int", 1, 3650),
    },
    "capacity": {
        "max_review_queue": ("int", 0, 100000),
        "max_waiting_drafts": ("int", 0, 100000),
        "min_etsy_quota_share": ("number", 0, 1),
        "min_disk_free_mb": ("int", 0, 10_000_000),
    },
}

# Fixed reason codes (never free text). ok=True has reason None.
REASONS = ("limits_missing", "limits_unreadable", "limits_invalid", "limits_not_admin_only")

_warned: set[str] = set()


@dataclass(frozen=True)
class Limits:
    ok: bool                                  # the file was read and every value is valid
    values: dict                              # in force when ok; the shipped defaults otherwise
    path: str
    reason: str | None = None                 # one of REASONS when not ok
    detail: str = ""                          # plain words for status pages and the CLI
    unknown: tuple[str, ...] = field(default_factory=tuple)   # keys this version ignores

    @property
    def level2_allowed(self) -> bool:
        """Level 2 changes are possible only with a good limits file (fail closed)."""
        return self.ok

    @property
    def scale_up_allowed(self) -> bool:
        return self.ok and self.values.get("scale_up", {}).get("enabled") is True

    def get(self, dotted: str, default=None):
        """A value by dotted name, e.g. get("scale_up.monthly_usd")."""
        node = self.values
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "detail": self.detail, "path": self.path,
                "values": copy.deepcopy(self.values), "unknown": list(self.unknown),
                "level2_allowed": self.level2_allowed, "scale_up_allowed": self.scale_up_allowed}


def _cfg(settings: dict | None) -> dict:
    return section(settings or {}, "control", SETTINGS_DEFAULTS)


def limits_path(settings: dict | None = None) -> Path:
    """settings control.limits_path (default /etc/botsy/limits.yaml). Never relative to the repo
    on the server: a relative value is read relative to the repo root (tests, development)."""
    raw = _cfg(settings).get("limits_path") or DEFAULT_PATH
    p = Path(str(raw))
    if not p.is_absolute():
        from core.config import resolve

        p = resolve(p)
    return p


def _closed(path: Path, reason: str, detail: str) -> Limits:
    key = f"{path}:{reason}"
    if key not in _warned:
        _warned.add(key)
        log.warning("limits file %s: %s (Level 2 changes and scale-ups are off)", path, detail)
    return Limits(ok=False, values=copy.deepcopy(DEFAULTS), path=str(path), reason=reason, detail=detail)


def _writable_by_others(st: os.stat_result) -> bool:
    return bool(st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _file_problem(st: os.stat_result, require_root: bool) -> str | None:
    """Why this file (the stat of the open file itself) could be changed by someone other than
    the admin, or None."""
    if not stat.S_ISREG(st.st_mode):
        return "it is not a regular file"
    if st.st_mode & stat.S_IWOTH:
        return "anyone on the server can change it (world-writable); run: sudo chmod 0644 it"
    if not require_root:
        return None
    if st.st_uid != 0:
        return "it is not owned by root; run: sudo chown root:root it (mode 0644)"
    if _writable_by_others(st):
        return "its group can change it; run: sudo chmod 0644 it"
    return None


def _admin_problem(path: Path, require_root: bool) -> str | None:
    """Why the file could be changed by someone other than the admin, or None. load() checks
    the open file again with fstat, so a file swapped in between is caught too."""
    try:
        st = os.stat(path)
        parent = os.stat(path.parent)
        link = os.lstat(path)
    except OSError as e:
        return f"cannot check who owns it ({type(e).__name__})"
    problem = _file_problem(st, require_root)
    if problem:
        return problem
    if parent.st_mode & stat.S_IWOTH:
        return "anyone on the server can change its folder (world-writable); run: sudo chmod 0755 the folder"
    if not require_root:
        return None
    if link.st_uid != 0:
        return "it is a link not owned by root; replace it with the file itself (root:root, mode 0644)"
    if parent.st_uid != 0 or _writable_by_others(parent):
        return "its folder is not root's alone; run: sudo chown root:root and chmod 0755 the folder"
    return None


def _check(raw: dict, spec: dict, defaults: dict, prefix: str, problems: list[str], unknown: list[str]) -> dict:
    out = copy.deepcopy(defaults)
    for key, value in raw.items():
        name = f"{prefix}{key}"
        if not isinstance(key, str):
            problems.append(f"{name!r} is not a plain key")
            continue
        if prefix == "" and key == "schema":
            if value != SCHEMA:
                problems.append(f"schema must be {SCHEMA}")
            continue
        rule = spec.get(key)
        if rule is None:
            unknown.append(name)
            continue
        if isinstance(rule, dict):
            if value is None:
                continue                      # an empty section keeps the defaults
            if not isinstance(value, dict):
                problems.append(f"{name} must be a section (indented keys under it)")
                continue
            out[key] = _check(value, rule, defaults[key], f"{name}.", problems, unknown)
            continue
        kind = rule[0]
        if kind == "bool":
            if not isinstance(value, bool):
                problems.append(f"{name} must be true or false")
                continue
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(f"{name} must be a number")
                continue
            if kind == "int" and (isinstance(value, float) and not value.is_integer()):
                problems.append(f"{name} must be a whole number")
                continue
            low, high = rule[1], rule[2]
            if not (low <= value <= high):
                problems.append(f"{name} must be between {low} and {high}")
                continue
            value = int(value) if kind == "int" else float(value)
        out[key] = value
    return out


def load(settings: dict | None = None, *, path: str | Path | None = None) -> Limits:
    """Read the limits file. Never raises; a problem gives Limits(ok=False) (fail closed)."""
    p = Path(path) if path is not None else limits_path(settings)
    require_root = _cfg(settings).get("limits_require_root") is not False
    try:
        if not p.exists():
            return _closed(p, "limits_missing",
                           f"the admin-only limits file {p} is missing (setup.sh installs it; "
                           "or: sudo install -m 0644 -o root -g root config/limits.example.yaml " + str(p) + ")")
    except OSError as e:
        return _closed(p, "limits_unreadable", f"cannot look at {p} ({type(e).__name__})")
    problem = _admin_problem(p, require_root)
    if problem:
        return _closed(p, "limits_not_admin_only", f"{p} is not admin-only: {problem}")
    try:
        with open(p, "rb") as f:
            # The file actually read must pass too: nothing can swap it between the check and the read.
            problem = _file_problem(os.fstat(f.fileno()), require_root)
            data = b"" if problem else f.read(MAX_BYTES + 1)
    except OSError as e:
        return _closed(p, "limits_unreadable", f"cannot read {p} ({type(e).__name__})")
    if problem:
        return _closed(p, "limits_not_admin_only", f"{p} is not admin-only: {problem}")
    if len(data) > MAX_BYTES:
        return _closed(p, "limits_invalid", f"{p} is larger than {MAX_BYTES // 1024} KB")
    try:
        doc = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        return _closed(p, "limits_invalid", f"{p} is not valid YAML ({type(e).__name__})")
    if doc is None:
        doc = {}
    if not isinstance(doc, dict):
        return _closed(p, "limits_invalid", f"{p} must be a YAML mapping (key: value lines)")
    problems: list[str] = []
    unknown: list[str] = []
    values = _check(doc, SPEC, DEFAULTS, "", problems, unknown)
    if problems:
        return _closed(p, "limits_invalid", f"{p} has values Botsy cannot use: " + "; ".join(problems[:8]))
    values["schema"] = SCHEMA
    if unknown:
        key = f"{p}:unknown:{','.join(sorted(unknown))}"
        if key not in _warned:
            _warned.add(key)
            log.info("limits file %s: ignoring keys this version does not use: %s", p, ", ".join(sorted(unknown)))
    return Limits(ok=True, values=values, path=str(p), unknown=tuple(sorted(unknown)))


__all__ = ["DEFAULTS", "DEFAULT_PATH", "Limits", "REASONS", "SCHEMA", "SETTINGS_DEFAULTS", "SPEC", "limits_path",
           "load"]
