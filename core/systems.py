"""Game systems for tabletop RPG sheets and listings: config/ttrpg_systems.yaml.

CLAUDE.md (user exception, 2026-10-16): a ttrpg listing may name the game system its sheets
are made for, as a compatibility reference only, in that listing's title, tags and
description, with the system's "unofficial, not affiliated with <owner>" disclaimer. The
name never goes on a printed page, a file name, a prompt or a mockup; a system only decides
which fields a sheet has. Briefs and manifests store the system's id, never its names.

    load_systems(settings=None, *, path=None) -> {id: entry}   validated (SystemsError)
    get_system(system_id, settings=None) -> entry               KeyError for an unknown id
    default_system(settings=None) -> "generic"
    is_known(system_id, settings=None) -> bool
    names_for(system_id, settings=None) -> [names]              [] for the default
    label_for / owner_for(system_id, settings=None) -> str
    disclaimer_for(system_id, settings=None) -> str             "" for the default
    sheet_fields_for(system_id, settings=None) -> dict          groups the system leaves out
                                                                come from the default
    detect_systems(text, settings=None) -> [ids]                systems whose names (or
                                                                owner) the text mentions
    system_terms(settings=None) -> {name: id}                   every name and owner spelling

An entry is {id, label, names, owner, owner_names, disclaimer, sheet_fields, default}.
Matching uses core.compliance.normalize (the blocklist's text form) and the blocklist's
word rules, so "5E", "d&d 5e", "DnD5e", "Dungeons & Dragons" and "D & D" all match, and a
name never matches inside a longer word. The file path is settings ttrpg.systems_file.
"""
from __future__ import annotations

import copy
import logging
import re
import threading
from pathlib import Path

import yaml

from core.config import ROOT, resolve, section

log = logging.getLogger("core.systems")

# Settings section "ttrpg" (shared with generators/ttrpg). Read with core.config.section().
DEFAULTS: dict = {"systems_file": "config/ttrpg_systems.yaml"}

SCHEMA = "ttrpg_systems/v1"
GENERIC = "generic"
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
GROUPS = ("core", "details", "spells")
COMBAT_KEYS = ("order", "defense", "health")
KINDS = ("lines", "scores", "boxes", "checklist", "track", "table", "slots", "notes", "prompts")
MAX_LABEL = 40
# Printed labels: letters, digits, spaces and a little punctuation, all in the PDF's
# standard (WinAnsi) encoding.
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,.'()/&:+-]*$")
DISCLAIMER_WORDS = ("unofficial", "not affiliated with")


class SystemsError(ValueError):
    """config/ttrpg_systems.yaml is missing, not valid YAML, or breaks the registry rules."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = list(problems or [])


# --- matching ----------------------------------------------------------------------------------

def _normalize(text: str) -> str:
    from core.compliance import normalize       # lazy: core.compliance may import this module

    return normalize(text)


_GAP_CHARS = r" \t\-_'&"


def _own_pattern(name: str) -> re.Pattern:
    """The blocklist's word rules, for when core.compliance does not offer its own: whole
    words; between two words an optional full stop, then up to three spaces, hyphens,
    underscores, apostrophes or "&" (at least one between two one-letter words, so "D&D"
    never matches "dd"); "&" also matches "and"; an optional plural or possessive "s"."""
    norm = _normalize(name)
    words = list(re.finditer(r"[a-z0-9]+", norm))
    if not words:
        raise ValueError(f"system name has no letters or digits: {name!r}")
    body = re.escape(words[0].group())
    for prev, cur in zip(words, words[1:]):
        gap = norm[prev.end():cur.start()]
        extra = "".join(re.escape(c) for c in sorted({c for c in gap if not c.isspace() and c not in "-_'&."}))
        least = 1 if len(prev.group()) == 1 and len(cur.group()) == 1 else 0
        sep = rf"\.?[{_GAP_CHARS}{extra}]{{{least},3}}"
        if "&" in gap:
            sep = rf"(?:{sep}|[ \t\-_]{{1,2}}and[ \t\-_]{{1,2}})"
        body += sep + re.escape(cur.group())
    return re.compile(rf"(?<![a-z0-9]){body}(?:'?s|es)?(?![a-z0-9])")


def name_pattern(name: str) -> re.Pattern:
    """The regex a system name is found with: the blocklist's own term pattern
    (core.compliance), so a name matches here exactly where the blocklist would match it
    ("5E", "d&d 5e", "DnD5e", "D & D", "Dungeons and Dragons"; never inside a longer word)."""
    try:
        from core.compliance import term_pattern
    except ImportError:
        return _own_pattern(name)
    try:
        return term_pattern(name)
    except ValueError:
        raise ValueError(f"system name has no letters or digits: {name!r}") from None


def _key(text: str) -> str:
    return " ".join(t for t in re.split(r"[^a-z0-9]+", _normalize(text)) if t)


# --- validation --------------------------------------------------------------------------------

def _label_problems(where: str, value, problems: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where}: must be a non-empty string")
        return
    if len(value) > MAX_LABEL:
        problems.append(f"{where}: {value!r} is longer than {MAX_LABEL} characters")
    if not LABEL_RE.match(value):
        problems.append(f"{where}: {value!r} uses characters a printed label may not have")


def _labels(where: str, value, problems: list[str], *, min_n: int = 1, max_n: int = 60) -> list[str]:
    if not isinstance(value, list) or not (min_n <= len(value) <= max_n):
        problems.append(f"{where}: must be a list of {min_n} to {max_n} labels")
        return []
    for i, v in enumerate(value):
        _label_problems(f"{where}[{i}]", v, problems)
    keys = [_key(v) for v in value if isinstance(v, str)]
    if len(set(keys)) != len(keys):
        problems.append(f"{where}: repeats a label")
    return [v for v in value if isinstance(v, str)]


def _int(where: str, value, lo: int, hi: int, problems: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        problems.append(f"{where}: must be a whole number from {lo} to {hi}")


def _section_problems(where: str, sec, problems: list[str]) -> list[str]:
    """Problems of one sheet section; returns its printed labels."""
    if not isinstance(sec, dict):
        problems.append(f"{where}: must be a mapping")
        return []
    kind = sec.get("kind")
    if kind not in KINDS:
        problems.append(f"{where}: kind must be one of {list(KINDS)}, not {kind!r}")
        return []
    labels: list[str] = []
    if sec.get("title") is not None:
        _label_problems(f"{where}.title", sec.get("title"), problems)
        labels.append(str(sec.get("title")))
    elif kind not in ("lines",):
        problems.append(f"{where}: a {kind} section needs a title")
    if sec.get("span") not in (None, "full", "column"):
        problems.append(f"{where}.span: must be full or column")
    _int(f"{where}.cols", sec.get("cols"), 1, 8, problems)
    _int(f"{where}.rows", sec.get("rows"), 1, 40, problems)
    _int(f"{where}.lines", sec.get("lines"), 1, 40, problems)
    if kind in ("lines", "scores", "boxes", "checklist", "prompts"):
        labels += _labels(f"{where}.fields", sec.get("fields"), problems)
    if kind == "scores":
        labels += _labels(f"{where}.sub", sec.get("sub", []), problems, min_n=0, max_n=2)
    if kind == "checklist":
        marks = sec.get("marks", [])
        labels += _labels(f"{where}.marks", marks, problems, min_n=0, max_n=4)
        if any(isinstance(m, str) and len(m) > 2 for m in marks or []):
            problems.append(f"{where}.marks: each mark is one or two letters")
        labels += _labels(f"{where}.values", sec.get("values", []), problems, min_n=0, max_n=3)
    if kind == "track":
        fields = sec.get("fields")
        if not isinstance(fields, list) or not 1 <= len(fields) <= 8:
            problems.append(f"{where}.fields: must be a list of 1 to 8 {{label, count}} entries")
        else:
            for i, f in enumerate(fields):
                if not isinstance(f, dict):
                    problems.append(f"{where}.fields[{i}]: must be {{label, count}}")
                    continue
                _label_problems(f"{where}.fields[{i}].label", f.get("label"), problems)
                _int(f"{where}.fields[{i}].count", f.get("count"), 1, 12, problems)
                if f.get("count") is None:
                    problems.append(f"{where}.fields[{i}].count: is required")
                labels.append(str(f.get("label")))
    if kind == "table":
        cols = _labels(f"{where}.columns", sec.get("columns"), problems, max_n=8)
        labels += cols
        weights = sec.get("weights")
        if weights is not None and (not isinstance(weights, list) or len(weights) != len(cols) or not all(
                isinstance(w, (int, float)) and not isinstance(w, bool) and w > 0 for w in weights)):
            problems.append(f"{where}.weights: one positive number per column")
    if kind == "slots":
        _int(f"{where}.levels", sec.get("levels"), 1, 12, problems)
        _int(f"{where}.boxes", sec.get("boxes"), 1, 8, problems)
        if sec.get("levels") is None:
            problems.append(f"{where}.levels: is required")
        _label_problems(f"{where}.label", sec.get("label", "Level"), problems)
        labels.append(str(sec.get("label", "Level")))
    return labels


def _sheet_problems(where: str, fields, problems: list[str], *, required: bool) -> list[str]:
    if not isinstance(fields, dict):
        problems.append(f"{where}: must be a mapping")
        return []
    labels: list[str] = []
    for group in GROUPS:
        if group not in fields:
            if required:
                problems.append(f"{where}.{group}: is required in the default system")
            continue
        secs = fields[group]
        if not isinstance(secs, list) or not secs:
            problems.append(f"{where}.{group}: must be a non-empty list of sections")
            continue
        for i, sec in enumerate(secs):
            labels += _section_problems(f"{where}.{group}[{i}]", sec, problems)
    if "combat" in fields:
        combat = fields["combat"]
        if not isinstance(combat, dict) or set(combat) != set(COMBAT_KEYS):
            problems.append(f"{where}.combat: must have exactly {list(COMBAT_KEYS)}")
        else:
            for k in COMBAT_KEYS:
                _label_problems(f"{where}.combat.{k}", combat[k], problems)
                labels.append(str(combat[k]))
    elif required:
        problems.append(f"{where}.combat: is required in the default system")
    for key, lo, hi in (("currency", 1, 6), ("spell_card", 2, 6)):
        if key in fields:
            labels += _labels(f"{where}.{key}", fields[key], problems, min_n=lo, max_n=hi)
        elif required:
            problems.append(f"{where}.{key}: is required in the default system")
    unknown = set(fields) - set(GROUPS) - {"combat", "currency", "spell_card"}
    if unknown:
        problems.append(f"{where}: unknown keys {sorted(unknown)}")
    return labels


def validate(doc, blocklist=None) -> list[str]:
    """Every problem with a registry document ([] when it is valid). With `blocklist`
    (core.compliance.Blocklist), printed sheet labels are also checked against it with no
    game system allowed."""
    problems: list[str] = []
    if not isinstance(doc, dict):
        return ["the file must hold a mapping with `default` and `systems`"]
    if doc.get("schema", SCHEMA) != SCHEMA:
        problems.append(f"schema must be {SCHEMA}, not {doc.get('schema')!r}")
    raw = doc.get("systems")
    if isinstance(raw, dict):
        raw = [dict(v, id=v.get("id", k)) if isinstance(v, dict) else v for k, v in raw.items()]
    if not isinstance(raw, list) or not raw:
        return problems + ["`systems` must be a non-empty list"]
    default = doc.get("default", GENERIC)
    ids: list[str] = []
    seen_names: dict[str, str] = {}
    printed: list[tuple[str, str]] = []
    for i, entry in enumerate(raw):
        where = f"systems[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        sid = entry.get("id")
        if not isinstance(sid, str) or not ID_RE.match(sid):
            problems.append(f"{where}.id: must match {ID_RE.pattern}, not {sid!r}")
            continue
        where = f"systems.{sid}"
        if sid in ids:
            problems.append(f"{where}: id is listed twice")
        ids.append(sid)
        if not isinstance(entry.get("label"), str) or not entry["label"].strip():
            problems.append(f"{where}.label: must be a non-empty string")
        names = entry.get("names", [])
        if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
            problems.append(f"{where}.names: must be a list of names")
            names = []
        owner = entry.get("owner")
        owner_names = entry.get("owner_names", [])
        if not isinstance(owner_names, list) or not all(isinstance(n, str) and n.strip() for n in owner_names):
            problems.append(f"{where}.owner_names: must be a list of names")
            owner_names = []
        disclaimer = entry.get("disclaimer") or ""
        if sid == default:
            if names or owner or owner_names or disclaimer:
                problems.append(f"{where}: the default (system-neutral) entry has no names, owner or disclaimer")
        else:
            if not names:
                problems.append(f"{where}.names: at least one name")
            elif _key(names[0]) != _key(str(entry.get("label") or "")):
                problems.append(f"{where}.names: the first name must be the label")
            if not isinstance(owner, str) or not owner.strip():
                problems.append(f"{where}.owner: the trademark owner is required")
            if not isinstance(disclaimer, str) or not disclaimer.strip():
                problems.append(f"{where}.disclaimer: required for a named system")
            else:
                norm = _normalize(disclaimer)
                for word in DISCLAIMER_WORDS:
                    if word not in norm:
                        problems.append(f"{where}.disclaimer: must say {word!r} (CLAUDE.md)")
                if isinstance(owner, str) and owner.strip() and _normalize(owner) not in norm:
                    problems.append(f"{where}.disclaimer: must name the owner {owner!r}")
        for n in [*names, *([owner] if isinstance(owner, str) and owner.strip() else []), *owner_names]:
            try:
                name_pattern(n)
            except ValueError as e:
                problems.append(f"{where}: {e}")
                continue
            k = _key(n)
            other = seen_names.get(k)
            if other is not None and other != sid:
                problems.append(f"{where}: name {n!r} is also a name of {other}")
            seen_names.setdefault(k, sid)
        fields = entry.get("sheet_fields")
        if fields is None and sid == default:
            problems.append(f"{where}.sheet_fields: required in the default system")
        elif fields is not None:
            printed += [(where, lbl) for lbl in _sheet_problems(f"{where}.sheet_fields", fields, problems,
                                                                 required=sid == default)]
    if default not in ids:
        problems.append(f"default {default!r} is not one of the systems {ids}")
    # Printed labels never carry any system's name or owner, nor a blocked term.
    patterns = [(n, rx) for n, rx in ((n, _safe_pattern(n)) for n in seen_names) if rx]
    for where, label in printed:
        norm = _normalize(label)
        hits = sorted({n for n, rx in patterns if rx.search(norm)})
        if hits:
            problems.append(f"{where}.sheet_fields: label {label!r} names a game system or owner {hits}")
    if blocklist is not None and printed:
        found = blocklist.check(*{label for _, label in printed})
        if found.blocked:
            problems.append(f"sheet_fields: labels contain blocked terms {found.blocked}")
    # A disclaimer names only its own system's trademarks.
    for entry in raw:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry.get("disclaimer"):
            norm = _normalize(str(entry["disclaimer"]))
            others = sorted({seen_names[k] for k in seen_names
                             if seen_names[k] != entry["id"] and (rx := _safe_pattern(k)) and rx.search(norm)})
            if others:
                problems.append(f"systems.{entry['id']}.disclaimer: names other systems {others}")
    return problems


def _safe_pattern(name: str) -> re.Pattern | None:
    try:
        return name_pattern(name)
    except ValueError:
        return None


# --- loading -----------------------------------------------------------------------------------

class _Registry:
    def __init__(self, systems: dict[str, dict], default: str):
        self.systems = systems
        self.default = default
        self.matchers: list[tuple[str, str, re.Pattern]] = []
        for sid, entry in systems.items():
            if sid == default:
                continue
            for term in [*entry["names"], *([entry["owner"]] if entry["owner"] else []), *entry["owner_names"]]:
                self.matchers.append((term, sid, name_pattern(term)))


_CACHE: dict[tuple, _Registry] = {}
_LOCK = threading.Lock()


_SETTINGS_PATH: list[Path] = []      # the path from config/settings.yaml, read once per process


def systems_path(settings: dict | None = None) -> Path:
    """The registry file: settings ttrpg.systems_file (repo-relative), else the default.
    With no settings, the configured settings are read once per process (callers such as
    research's per-row detect_systems(text) pass none)."""
    if settings is None:
        if _SETTINGS_PATH:
            return _SETTINGS_PATH[0]
        try:
            from core.config import load_settings

            loaded = load_settings()
        except Exception:  # noqa: BLE001 - unreadable settings: the shipped file still works
            loaded = {}
        p = systems_path(loaded if isinstance(loaded, dict) else {})
        _SETTINGS_PATH[:] = [p]
        return p
    cfg = section(settings if isinstance(settings, dict) else {}, "ttrpg", DEFAULTS)
    return Path(resolve(cfg.get("systems_file") or DEFAULTS["systems_file"]))


def _build(doc: dict) -> _Registry:
    raw = doc["systems"]
    if isinstance(raw, dict):
        raw = [dict(v, id=v.get("id", k)) for k, v in raw.items()]
    default = doc.get("default", GENERIC)
    base = next(e for e in raw if e["id"] == default)
    systems: dict[str, dict] = {}
    for e in raw:
        fields = copy.deepcopy(base.get("sheet_fields") or {})
        fields.update(copy.deepcopy(e.get("sheet_fields") or {}))
        systems[e["id"]] = {
            "id": e["id"],
            "label": str(e.get("label") or e["id"]).strip(),
            "names": [str(n).strip() for n in e.get("names") or []],
            "owner": (str(e["owner"]).strip() if e.get("owner") else None),
            "owner_names": [str(n).strip() for n in e.get("owner_names") or []],
            "disclaimer": str(e.get("disclaimer") or "").strip(),
            "sheet_fields": fields,
            "default": e["id"] == default,
        }
    return _Registry(systems, default)


def _registry(settings: dict | None = None, path: str | Path | None = None) -> _Registry:
    p = Path(resolve(path)) if path else systems_path(settings)
    try:
        st = p.stat()
    except OSError as e:
        raise SystemsError(f"{_shown(p)}: cannot read the game systems file ({e.strerror or e})") from None
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise SystemsError(f"{_shown(p)} is not valid YAML{where}") from None
    problems = validate(doc, _blocklist(settings))
    if problems:
        raise SystemsError(f"{_shown(p)}: {len(problems)} problem(s): " + "; ".join(problems[:5]), problems)
    reg = _build(doc)
    with _LOCK:
        for k in [k for k in _CACHE if k[0] == str(p)]:
            del _CACHE[k]
        _CACHE[key] = reg
    return reg


def _blocklist(settings: dict | None):
    """The trademark blocklist for checking printed labels, or None when it cannot be loaded
    (the generator checks every printed string again before a file is written)."""
    try:
        from core.compliance import Blocklist

        path = ((settings or {}).get("compliance") or {}).get("trademark_blocklist") if isinstance(settings, dict) \
            else None
        return Blocklist.load(path or None)
    except Exception as e:  # noqa: BLE001
        log.info("game systems: blocklist not loaded for the label check (%s)", e)
        return None


def _shown(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def load_systems(settings: dict | None = None, *, path: str | Path | None = None) -> dict[str, dict]:
    """{id: entry} for every system in the registry (a fresh copy each call). Raises
    SystemsError when the file is missing, not valid YAML or breaks a registry rule."""
    return copy.deepcopy(_registry(settings, path).systems)


def default_system(settings: dict | None = None, *, path: str | Path | None = None) -> str:
    return _registry(settings, path).default


def is_known(system_id, settings: dict | None = None, *, path: str | Path | None = None) -> bool:
    return isinstance(system_id, str) and system_id in _registry(settings, path).systems


def get_system(system_id: str, settings: dict | None = None, *, path: str | Path | None = None) -> dict:
    """One entry (a copy). KeyError for an id the registry does not list."""
    reg = _registry(settings, path)
    if not isinstance(system_id, str) or system_id not in reg.systems:
        raise KeyError(f"unknown game system id {system_id!r}; config/ttrpg_systems.yaml lists {sorted(reg.systems)}")
    return copy.deepcopy(reg.systems[system_id])


def names_for(system_id: str, settings: dict | None = None, *, path: str | Path | None = None) -> list[str]:
    """How the system may appear in listing text, its label first ([] for the default)."""
    return list(get_system(system_id, settings, path=path)["names"])


def label_for(system_id: str, settings: dict | None = None, *, path: str | Path | None = None) -> str:
    return get_system(system_id, settings, path=path)["label"]


def owner_for(system_id: str, settings: dict | None = None, *, path: str | Path | None = None) -> str | None:
    return get_system(system_id, settings, path=path)["owner"]


def disclaimer_for(system_id: str, settings: dict | None = None, *, path: str | Path | None = None) -> str:
    """The text every listing naming this system carries ("" for the default)."""
    return get_system(system_id, settings, path=path)["disclaimer"]


def sheet_fields_for(system_id: str, settings: dict | None = None, *, path: str | Path | None = None) -> dict:
    """The system's sheet fields (groups it does not list come from the default)."""
    return get_system(system_id, settings, path=path)["sheet_fields"]


def system_terms(settings: dict | None = None, *, path: str | Path | None = None) -> dict[str, str]:
    """{name or owner spelling: system id} for every named system."""
    return {term: sid for term, sid, _ in _registry(settings, path).matchers}


def detect_systems(text, settings: dict | None = None, *, path: str | Path | None = None) -> list[str]:
    """Ids of the systems whose names or owner `text` mentions, in registry order.

    Normalized like the blocklist (core.compliance.normalize): case, accents, look-alike
    letters, dashes and trademark signs do not matter, and spacing between words may be
    missing ("dnd5e") or a hyphen, underscore, apostrophe or "&"."""
    if not isinstance(text, str) or not text.strip():
        return []
    reg = _registry(settings, path)
    norm = _normalize(text)
    found = {sid for _, sid, rx in reg.matchers if rx.search(norm)}
    return [sid for sid in reg.systems if sid in found]
