"""plan(brief, settings, state) -> list[PackSpec] (docs/PACKS.md section 3).

One PackSpec per size in the brief's pack sizes (or the sizes asked for), master first:
- the largest size is the theme's master set: new pages, always (its counter never moves);
- a smaller size is derived from a master set (this plan's, or one of the same theme made
  within the theme cooldown) unless the fresh policy makes pack number counter + 1 fresh
  (packs.fresh_every); with no master set to cut from, it is fresh too;
- a brief whose pack of a size was made before keeps that assignment (same source, number
  and set), so a rerun rebuilds the same pack instead of counting a new one.
"""
from __future__ import annotations

import importlib

from core.config import check_date, today
from generators.packs import sets as sets_mod
from generators.packs.model import (
    PackSpec, forced_fresh, max_size, pack_sizes, set_id_for,
)
from generators.packs.state import PackState


def niche_plugin(niche: str):
    """The niche's generator module (generators.NICHE_MODULES), or None."""
    import generators

    name = generators.NICHE_MODULES.get(niche)
    return importlib.import_module(name) if name else None


def size_limits(niche: str, settings: dict, plugin=None) -> tuple[int, int | None]:
    """(default pack size, largest allowed) for a niche: the plugin's default_pack_size() /
    max_pack_size() when it has them, else niches.<niche>.pages_per_pack[0] (or 20) and
    packs.max_size.<niche>."""
    plugin = plugin if plugin is not None else niche_plugin(niche)
    default = None
    if plugin is not None and hasattr(plugin, "default_pack_size"):
        default = plugin.default_pack_size(settings)
    if default is None:
        options = ((settings.get("niches") or {}).get(niche) or {}).get("pages_per_pack") or [20]
        default = int(options[0])
    own = plugin.max_pack_size(settings) if plugin is not None and hasattr(plugin, "max_pack_size") else None
    return int(default), max_size(settings, niche, own)


def brief_pack_sizes(brief: dict, settings: dict, plugin=None) -> list[int]:
    """The brief's pack sizes, smallest first (ValueError for a bad or oversize one)."""
    default, cap = size_limits(str(brief.get("niche") or ""), settings, plugin)
    return pack_sizes(brief, default=default, cap=cap)


def brief_system(brief: dict) -> str:
    """The game system id a ttrpg brief names, else "generic" (other niches: "generic")."""
    if brief.get("niche") != "ttrpg":
        return "generic"
    system = brief.get("system")
    return system.strip() if isinstance(system, str) and system.strip() else "generic"


def plan(brief: dict, settings: dict, state: PackState | None = None, *, sizes=None,
         plugin=None) -> list[PackSpec]:
    """PackSpecs for the brief's pack sizes (only `sizes` when given), master first, then
    the other sizes smallest first. Deterministic for the same brief, settings and state."""
    niche = str(brief.get("niche") or "")
    theme = str(brief.get("theme") or "").strip()
    if not theme:
        raise ValueError("brief has no theme")
    brief_id = str(brief.get("brief_id") or "")
    state = state if state is not None else PackState(date=today())
    date = check_date(state.date or today())
    all_sizes = brief_pack_sizes(brief, settings, plugin)
    master_size = all_sizes[-1]
    if sizes is None:
        wanted = list(all_sizes)
    else:
        asked = {int(s) for s in sizes}
        unknown = sorted(asked - set(all_sizes))
        if unknown:
            raise ValueError(f"pack size(s) {unknown} are not in the brief's pack sizes {all_sizes}")
        wanted = [s for s in all_sizes if s in asked]
    system = brief_system(brief)

    specs: list[PackSpec] = []
    building_master = master_size in wanted
    if building_master:
        specs.append(PackSpec(master_size, "master", set_id_for(date, niche, theme, brief_id, master_size, "master", 0),
                              0, True, master_size))
    for size in wanted:
        if size == master_size:
            continue
        known = state.assignment(niche, brief_id, size)
        if known and known.get("source") in ("fresh", "derived") and isinstance(known.get("n"), int):
            this_master = building_master and known.get("set_id") == specs[0].set_id     # built first, below
            if known["source"] == "fresh" or this_master or _usable_master(state, known.get("set_id"), size):
                specs.append(PackSpec(size, known["source"], str(known["set_id"]), known["n"],
                                      known["source"] == "fresh", master_size, str(known.get("reason") or "")))
                continue
        n = state.counter(niche, size) + 1
        if forced_fresh(settings, size, n):
            specs.append(PackSpec(size, "fresh", set_id_for(date, niche, theme, brief_id, size, "fresh", n), n,
                                  True, master_size, "fresh_every"))
            continue
        if building_master:
            source_set = specs[0].set_id
        else:
            doc = state.find_master(niche, theme, min_pages=size + 1, brief_id=brief_id or None, system=system)
            source_set = doc["set_id"] if doc else None
        if source_set is None:
            specs.append(PackSpec(size, "fresh", set_id_for(date, niche, theme, brief_id, size, "fresh", n), n,
                                  True, master_size, "no master set"))
        else:
            specs.append(PackSpec(size, "derived", source_set, n, False, master_size))
    return specs


def _usable_master(state: PackState, set_id, size: int) -> bool:
    if not set_id or state.out_root is None:
        return False
    doc = sets_mod.load_set(state.out_root, str(set_id))
    return bool(doc) and doc.get("kind") == "master" and len(doc.get("pages") or []) > size \
        and sets_mod.is_complete(state.out_root, doc)
