"""CLI: build activity packs from a brief file.

    python -m generators.activity_sheets --brief data/briefs/activity_sheets-2026-10-15.json --all
    python -m generators.activity_sheets --brief <file> --index 2 --out /tmp/out --date 2026-10-15

Prints one item directory per line. The options are in generators/cli.py, shared with the
image generators. --dry-run is accepted so the pipeline can pass it to every stage; this
generator never calls a network service, so the files are the same (items go to
output/dry-run unless --out is given, and the manifest records dry_run).
"""
from __future__ import annotations

import sys

from generators.activity_sheets import NICHE, generate
from generators.cli import run_cli


def main(argv: list[str] | None = None) -> int:
    return run_cli(NICHE, generate, argv,
                   description="Generate procedural activity-sheet packs from a brief file.",
                   dry_run_help="accepted for pipeline symmetry; the files are identical")


if __name__ == "__main__":
    sys.exit(main())
