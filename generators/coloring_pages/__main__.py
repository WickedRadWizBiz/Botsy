"""CLI: python -m generators.coloring_pages --brief FILE [--index N | --all] [--dry-run] [--out DIR]

See generators/imaging/cli.py for every option.
"""
from __future__ import annotations

import sys

from generators.coloring_pages import NICHE, generate
from generators.imaging.cli import run_cli


def main(argv: list[str] | None = None) -> int:
    return run_cli(NICHE, generate, argv, description="Generate AI-assisted coloring-page packs from a brief file.")


if __name__ == "__main__":
    sys.exit(main())
