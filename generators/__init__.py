"""Turn a design brief into print-ready items. See docs/CONTRACTS.md section 2.

Each niche package exposes the same entry point:
    generate(brief, *, brief_file=None, shop_id="main", out_root=None, date=None,
             dry_run=False, client=None) -> list[Path]

`brief_file` is the repo-relative path of the brief JSON, recorded in each manifest.

Errors callers may want to handle: ImageCapExceeded (the run's image budget is spent; stop
generating), ImageGenerationError (an image niche could not produce a usable pack).
"""
from __future__ import annotations

import importlib
from pathlib import Path

from generators.imaging.client import ImageCapExceeded, ImageGenerationError

NICHE_MODULES = {
    "activity_sheets": "generators.activity_sheets",
    "coloring_pages": "generators.coloring_pages",
    "stickers": "generators.stickers",
}


__all__ = ["NICHE_MODULES", "ImageCapExceeded", "ImageGenerationError", "generate"]


def generate(brief: dict, **kwargs) -> list[Path]:
    """Dispatch one brief (an entry of a brief file's "briefs" list) to its niche generator."""
    niche = brief["niche"]
    if niche not in NICHE_MODULES:
        raise ValueError(f"no generator for niche {niche!r}")
    module = importlib.import_module(NICHE_MODULES[niche])
    return module.generate(brief, **kwargs)
