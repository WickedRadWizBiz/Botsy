"""CLI for the image generators (python -m generators.coloring_pages / .stickers).

    python -m generators.coloring_pages --brief data/briefs/coloring_pages-2026-10-15.json --all --dry-run
    python -m generators.stickers --brief <file> --index 1 --out /tmp/out

The options are in generators/cli.py, shared with the activity-sheet CLI. Without --dry-run
the Gemini image API is called and GOOGLE_API_KEY (or the Vertex variables) must be set in
.env; --dry-run writes to output/dry-run unless --out is given. A brief that fails (blocked
term, refused images) is reported and skipped; hitting the run's image cap stops the run.
"""
from __future__ import annotations

from collections.abc import Callable

from core.config import MissingSecretError
from generators import cli
from generators.imaging.client import ImageCapExceeded, ImageGenerationError


def run_cli(niche: str, generate: Callable, argv: list[str] | None = None, description: str = "") -> int:
    return cli.run_cli(niche, generate, argv, description=description,
                       stop_errors=(ImageCapExceeded, MissingSecretError),
                       skip_errors=(ValueError, ImageGenerationError))
