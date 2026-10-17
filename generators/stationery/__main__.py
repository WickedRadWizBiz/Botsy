"""CLI: build planner and productivity page packs from a brief file.

    python -m generators.stationery --brief data/briefs/stationery-2026-10-16.json --all --dry-run
    python -m generators.stationery --brief <file> --index 1 --out /tmp/out --date 2026-10-16

Prints one item directory per line, one per pack size of each brief (docs/PACKS.md). The
pages are procedural (reportlab); only the optional AI line-art accents (stationery.ai_accents,
off by default) call the image API, and never in a dry run. Options: generators/cli.py.
"""
from __future__ import annotations

import sys

from generators.imaging.cli import run_cli
from generators.stationery import NICHE, generate


def main(argv: list[str] | None = None) -> int:
    return run_cli(NICHE, generate, argv, description="Generate procedural planner-page packs from a brief file.")


if __name__ == "__main__":
    sys.exit(main())
