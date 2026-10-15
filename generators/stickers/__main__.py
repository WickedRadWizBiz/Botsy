"""CLI: python -m generators.stickers --brief FILE [--index N | --all] [--dry-run] [--out DIR]

See generators/imaging/cli.py for every option.
"""
from __future__ import annotations

import sys

from generators.imaging.cli import run_cli
from generators.stickers import NICHE, generate


def main(argv: list[str] | None = None) -> int:
    return run_cli(NICHE, generate, argv, description="Generate AI-assisted sticker packs from a brief file.")


if __name__ == "__main__":
    sys.exit(main())
