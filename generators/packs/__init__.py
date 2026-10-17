"""Themed packs: design sets, fresh and derived packs (docs/PACKS.md section 3).

    plan(brief, settings, state) -> list[PackSpec]      one PackSpec per size in brief.pack_sizes
    generate_packs(brief, *, plugin, ...) -> list[Path] one item folder per planned pack
    subjects(theme, niche, n, *, settings, client=None, dry_run=False) -> list[str]
    curate(set_doc, k, ...) -> list[dict]               a derived pack's pages out of a master set
    load_state(settings, *, out_root, ...) -> PackState  counters, assignments and design sets
    preview(brief, *, settings, out_root, ...) -> list[PlannedPack]   what generate_packs would make

Modules: model.py (PackSpec, settings, ids), state.py (data/packs_state.json), sets.py
(<out_root>/sets/<set_id>/set.json + pages/), plan.py, curate.py, subjects.py, engine.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from generators.packs.curate import curate
from generators.packs.engine import PackPlanError, base_ctx, generate_packs
from generators.packs.model import DEFAULTS, SOURCES, PackSpec, config, item_id_for, set_id_for, theme_key
from generators.packs.plan import brief_pack_sizes, brief_system, niche_plugin, plan
from generators.packs.state import PackState
from generators.packs.subjects import SubjectPlan, plan_subjects, subjects

__all__ = ["DEFAULTS", "SOURCES", "PackPlanError", "PackSpec", "PackState", "PlannedPack", "SubjectPlan",
           "base_ctx", "brief_pack_sizes", "brief_system", "build_order", "config", "curate", "generate_packs",
           "item_id_for", "load_state", "niche_plugin", "plan", "plan_subjects", "preview", "set_id_for",
           "subjects", "theme_key"]


@dataclass(frozen=True)
class PlannedPack:
    """A pack generate_packs() would make now: its spec and the item id it gets."""

    spec: PackSpec
    item_id: str

    @property
    def size(self) -> int:
        return self.spec.size

    @property
    def source(self) -> str:
        return self.spec.source


def load_state(settings: dict, *, out_root: str | Path, shop_id: str = "main", date: str | None = None,
               dry_run: bool = False) -> PackState:
    """The pack state of one output root (see generators/packs/state.py)."""
    return PackState.load(settings, out_root=out_root, shop_id=shop_id, date=date, dry_run=dry_run)


def build_order(specs: list[PackSpec]) -> list[PackSpec]:
    """The order packs are built in: the master first (derived packs are cut from it), then
    derived packs (no paid work), then fresh packs (new pages), smallest first within each."""
    rank = {"master": 0, "derived": 1, "fresh": 2}
    return sorted(specs, key=lambda s: (rank.get(s.source, 3), s.size))


def preview(brief: dict, *, settings: dict, out_root: str | Path, shop_id: str = "main", date: str,
            dry_run: bool = False, state: PackState | None = None, sizes=None,
            plugin=None) -> list[PlannedPack]:
    """The packs generate_packs(brief, ...) would make on `date` (build order, master first)
    and their item ids, without building anything. Raises ValueError for a brief plan()
    refuses (no theme, a bad pack size) and ImportError for a niche without a generator.
    `plugin` defaults to the niche's module (generators.NICHE_MODULES)."""
    plugin = plugin if plugin is not None else niche_plugin(str(brief.get("niche") or ""))
    if plugin is None:
        raise ValueError(f"no generator for niche {brief.get('niche')!r}")
    if state is None:
        state = load_state(settings, out_root=out_root, shop_id=shop_id, date=date, dry_run=dry_run)
    elif state.date != date:
        state = PackState(state.data, path=state.path, out_root=state.out_root, shop_id=state.shop_id, date=date,
                          cooldown_days=state.cooldown_days, dry_run=state.dry_run)
    theme = str(brief.get("theme") or "").strip()
    niche = str(brief.get("niche") or "")
    brief_id = str(brief.get("brief_id") or "")
    specs = build_order(plan(brief, settings, state, sizes=sizes, plugin=plugin))
    return [PlannedPack(s, item_id_for(date, niche, theme, brief_id, s)) for s in specs]
