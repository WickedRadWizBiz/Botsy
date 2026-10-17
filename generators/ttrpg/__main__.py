"""CLI: build tabletop RPG sheet packs from a brief file.

    python -m generators.ttrpg --brief data/briefs/ttrpg-2026-10-16.json --all --dry-run
    python -m generators.ttrpg --brief <file> --index 1 --out /tmp/out --date 2026-10-16

Prints one item directory per line, one per pack size of each brief (docs/PACKS.md). The
sheets are procedural (reportlab) and never print a game system's name; the brief's system
id only picks our own field list (config/ttrpg_systems.yaml). Only the optional AI line-art
accents (ttrpg.ai_accents, off by default) call the image API, and never in a dry run.
Options: generators/cli.py.
"""
from __future__ import annotations

import sys

from generators.imaging.cli import run_cli
from generators.ttrpg import NICHE, generate


def main(argv: list[str] | None = None) -> int:
    return run_cli(NICHE, generate, argv, description="Generate procedural tabletop RPG sheet packs from a brief file.")


if __name__ == "__main__":
    sys.exit(main())
