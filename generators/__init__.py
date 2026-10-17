"""Turn a design brief into print-ready items: one item per themed pack. See docs/CONTRACTS.md
section 2 and docs/PACKS.md section 3.

Each niche package exposes the same entry point:
    generate(brief, *, brief_file=None, shop_id="main", out_root=None, date=None,
             dry_run=False, client=None, settings=None, sizes=None) -> list[Path]
which is generators.packs.generate_packs(brief, plugin=<the niche module>, ...): one item dir
per size in brief["pack_sizes"] (or [page_count] for an older brief), master pack first.
`sizes` builds only some of them (the pipeline uses it for max_items and the theme cooldown).

`brief_file` is the repo-relative path of the brief JSON, recorded in each manifest.

Errors callers may want to handle: ImageCapExceeded (the run's image budget is spent; stop
generating), ImageGenerationError (an image niche could not produce a usable pack). A brief
that fails part way raises with `pack_dirs` set on the exception: the packs made before it.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from generators.imaging.client import ImageCapExceeded, ImageGenerationError

NICHE_MODULES = {
    "activity_sheets": "generators.activity_sheets",
    "coloring_pages": "generators.coloring_pages",
    "stickers": "generators.stickers",
    "stationery": "generators.stationery",
    "ttrpg": "generators.ttrpg",
}


__all__ = ["NICHE_MODULES", "ImageCapExceeded", "ImageGenerationError", "generate"]


def generate(brief: dict, **kwargs) -> list[Path]:
    """Dispatch one brief (an entry of a brief file's "briefs" list) to its niche generator."""
    niche = brief["niche"]
    if niche not in NICHE_MODULES:
        raise ValueError(f"no generator for niche {niche!r}")
    module = importlib.import_module(NICHE_MODULES[niche])
    return module.generate(brief, **kwargs)
