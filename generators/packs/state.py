"""Pack counters and assignments (docs/PACKS.md section 3): data/packs_state.json.

    {"schema": "packs_state/v1",
     "counters":    {"<shop>": {"<niche>": {"<size>": <packs made>}}},
     "assignments": {"<shop>|<niche>|<brief_id>|<size>": {"source", "n", "set_id", "item_id", "created",
                                                           "reason"}}}

`counters` count the packs of each size (masters excluded) made for a shop and niche; the
fresh policy reads them (pack number = counter + 1). `assignments` remember what each
brief's pack of a size became, so a rerun of the same brief rebuilds the same pack instead
of counting it again. Both change only after a pack's item was generated successfully.

Where the file lives: settings packs.state_file (data/packs_state.json) for the configured
output folder (paths.output_dir); any other output root (dry runs, --out, tests) keeps its
own <out_root>/sets/packs_state.json next to its design sets, so a dry run never moves the
real counters. While pytest runs, a state file inside the repo is neither read nor written.
"""
from __future__ import annotations

import logging
import os
from datetime import date as _date
from pathlib import Path

from core.config import ROOT, resolve
from core.items import output_root, read_json, write_json
from generators.packs import sets as sets_mod
from generators.packs.model import config, cooldown_days, theme_key

log = logging.getLogger(__name__)

SCHEMA = "packs_state/v1"
_warned: set[str] = set()


def state_path(settings: dict, out_root: str | Path) -> Path:
    """packs.state_file for the configured output folder, else <out_root>/sets/packs_state.json."""
    out = Path(out_root)
    try:
        configured = output_root(settings).resolve() == out.resolve()
    except (KeyError, OSError):
        configured = False
    if configured:
        return resolve(config(settings).get("state_file") or "data/packs_state.json")
    return sets_mod.sets_root(out) / "packs_state.json"


def _off_limits(path: Path) -> bool:
    """While pytest runs, the repo's own state file (the user's real counters) is off limits."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return False
    return True


class PackState:
    """Counters, assignments and the design sets of one output root, for one shop and date."""

    def __init__(self, data: dict | None = None, *, path: Path | None = None, out_root: str | Path | None = None,
                 shop_id: str = "main", date: str | None = None, cooldown_days: int = 60, dry_run: bool = False):
        data = data if isinstance(data, dict) else {}
        self.data = {"schema": SCHEMA,
                     "counters": data.get("counters") if isinstance(data.get("counters"), dict) else {},
                     "assignments": data.get("assignments") if isinstance(data.get("assignments"), dict) else {}}
        self.path = path
        self.out_root = Path(out_root) if out_root is not None else None
        self.shop_id = shop_id
        self.date = date
        self.cooldown_days = cooldown_days
        self.dry_run = dry_run
        self._sets: list[dict] | None = None

    @classmethod
    def load(cls, settings: dict, *, out_root: str | Path, shop_id: str = "main", date: str | None = None,
             dry_run: bool = False) -> PackState:
        path = state_path(settings, out_root)
        data = {}
        if _off_limits(path):
            if str(path) not in _warned:
                _warned.add(str(path))
                log.warning("test run: not reading or writing the real pack state %s", path)
            path = None
        elif path.is_file():
            try:
                data = read_json(path)
            except (OSError, ValueError) as e:
                # Unreadable counters must not stop production; the fresh policy restarts.
                log.warning("pack state %s is unreadable (%s); starting from empty counters", path, e)
                data = {}
        return cls(data, path=path, out_root=out_root, shop_id=shop_id, date=date,
                   cooldown_days=cooldown_days(settings), dry_run=dry_run)

    # --- counters and assignments ----------------------------------------------------------

    def counter(self, niche: str, size: int, shop_id: str | None = None) -> int:
        shop = self.data["counters"].get(shop_id or self.shop_id) or {}
        value = (shop.get(niche) or {}).get(str(size), 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    def advance(self, niche: str, size: int, shop_id: str | None = None) -> int:
        shop = self.data["counters"].setdefault(shop_id or self.shop_id, {})
        table = shop.setdefault(niche, {})
        table[str(size)] = self.counter(niche, size, shop_id) + 1
        return table[str(size)]

    @staticmethod
    def _key(shop_id: str, niche: str, brief_id, size: int) -> str:
        return f"{shop_id}|{niche}|{brief_id}|{size}"

    def assignment(self, niche: str, brief_id, size: int, shop_id: str | None = None) -> dict | None:
        if not brief_id:
            return None
        rec = self.data["assignments"].get(self._key(shop_id or self.shop_id, niche, brief_id, size))
        return rec if isinstance(rec, dict) else None

    def assign(self, niche: str, brief_id, size: int, rec: dict, shop_id: str | None = None) -> None:
        if brief_id:
            self.data["assignments"][self._key(shop_id or self.shop_id, niche, brief_id, size)] = dict(rec)

    def record_success(self, niche: str, brief_id, spec, item_id: str, created: str,
                       shop_id: str | None = None) -> None:
        """A pack's item was generated: advance its size's counter (once per assignment;
        never for a master) and remember the assignment."""
        known = self.assignment(niche, brief_id, spec.size, shop_id)
        same = known is not None and known.get("source") == spec.source and known.get("n") == spec.n
        if not spec.is_master and not same:
            self.advance(niche, spec.size, shop_id)
        self.assign(niche, brief_id, spec.size, {"source": spec.source, "n": spec.n, "set_id": spec.set_id,
                                                 "item_id": item_id, "created": created,
                                                 "reason": spec.reason}, shop_id)

    def save(self) -> None:
        if self.path is not None:
            write_json(self.path, self.data)

    # --- design sets -----------------------------------------------------------------------

    def sets(self, reload: bool = False) -> list[dict]:
        if self.out_root is None:
            return []
        if self._sets is None or reload:
            self._sets = sets_mod.iter_sets(self.out_root)
        return self._sets

    def find_master(self, niche: str, theme: str, *, min_pages: int, brief_id=None, system: str = "generic",
                    shop_id: str | None = None) -> dict | None:
        """The master set derived packs of this theme use: same shop, niche and theme (and
        game system), at least min_pages pages, complete, and from a run of the same kind (a
        real pack is never cut from dry-run art). Another brief's set must be made within the
        cooldown window (pipeline.theme_cooldown_days) before the plan date; the brief's own
        master (made when the run's max_items left no room for its smaller packs) always
        counts. The brief's own set first, then the newest."""
        if self.out_root is None:
            return None
        key = theme_key(theme)
        shop = shop_id or self.shop_id
        run_day = _day(self.date)
        found = []
        for doc in self.sets():
            if (doc.get("kind") != "master" or doc.get("niche") != niche or doc.get("shop_id") != shop
                    or (doc.get("theme_key") or theme_key(doc.get("theme"))) != key
                    or (doc.get("system") or "generic") != (system or "generic")
                    or len(doc.get("pages") or []) < min_pages):
                continue
            if bool(doc.get("dry_run")) != bool(self.dry_run) and doc.get("ai_assisted"):
                continue
            made = _day(doc.get("created"))
            own = brief_id is not None and doc.get("brief_id") == brief_id
            if run_day is not None and made is not None and made > run_day:
                continue                                    # made "after" the plan date: not ours to use
            if not own and (not self.cooldown_days or (run_day is not None and (
                    made is None or (run_day - made).days >= self.cooldown_days))):
                continue
            if not sets_mod.is_complete(self.out_root, doc):
                continue
            found.append(doc)
        if not found:
            return None
        found.sort(key=lambda d: (d.get("brief_id") == brief_id and brief_id is not None, str(d.get("created")),
                                  d["set_id"]), reverse=True)
        return found[0]


def _day(value) -> _date | None:
    try:
        return _date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None
