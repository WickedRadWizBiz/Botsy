"""Shared argparse CLI for the image generators (python -m generators.coloring_pages / .stickers).

    python -m generators.coloring_pages --brief data/briefs/coloring_pages-2026-10-15.json --all --dry-run
    python -m generators.stickers --brief <file> --index 1 --out /tmp/out

Prints one item directory per line. Without --dry-run the Gemini image API is called and
GOOGLE_API_KEY (or the Vertex variables) must be set in .env. A brief that fails (blocked
term, refused images) is reported and skipped; hitting the run's image cap stops the run.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from core.config import MissingSecretError, load_settings, resolve
from core.items import read_json
from generators.imaging.client import ImageCapExceeded, ImageGenerationError


def _brief_path(arg: str) -> Path:
    p = Path(arg)
    return p if p.exists() else resolve(arg)


def run_cli(niche: str, generate: Callable, argv: list[str] | None = None, description: str = "") -> int:
    ap = argparse.ArgumentParser(prog=f"python -m generators.{niche}", description=description)
    ap.add_argument("--brief", required=True,
                    help="brief file (data/briefs/<niche>-<date>.json) or a JSON file holding one brief")
    pick = ap.add_mutually_exclusive_group()
    pick.add_argument("--index", type=int, help="0-based index into the file's briefs list (default 0)")
    pick.add_argument("--all", action="store_true", help=f"every {niche} brief in the file")
    ap.add_argument("--out", help="output root (default: settings paths.output_dir)")
    ap.add_argument("--date", help="item date YYYY-MM-DD (default: PIPELINE_DATE or today)")
    ap.add_argument("--shop", help="shop id (default: the brief file's shop_id, else 'main')")
    ap.add_argument("--settings", help="alternate settings YAML (default: config/settings.yaml)")
    ap.add_argument("--dry-run", action="store_true",
                    help="use the offline fake image client (no API calls, no secrets)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    path = _brief_path(args.brief)
    if not path.exists():
        ap.error(f"brief file not found: {args.brief}")
    data = read_json(path)
    briefs = data["briefs"] if "briefs" in data else [data]
    if args.all:
        selected = [b for b in briefs if b.get("niche") == niche]
        if not selected:
            print(f"no {niche} briefs in {path}", file=sys.stderr)
            return 0
    else:
        idx = args.index or 0
        if not 0 <= idx < len(briefs):
            ap.error(f"--index {idx} out of range (file has {len(briefs)} briefs)")
        if briefs[idx].get("niche", niche) != niche:
            ap.error(f"brief {idx} is for niche {briefs[idx].get('niche')!r}, not {niche!r}")
        selected = [briefs[idx]]

    settings = load_settings(args.settings) if args.settings else load_settings()
    shop = args.shop or data.get("shop_id") or "main"
    failed = 0
    for brief in selected:
        try:
            dirs = generate(brief, brief_file=path, shop_id=shop, out_root=args.out, date=args.date,
                            dry_run=args.dry_run, settings=settings)
        except (ImageCapExceeded, MissingSecretError) as exc:
            print(f"stopped at brief {brief.get('brief_id')!r}: {exc}", file=sys.stderr)
            return 1
        except (ValueError, ImageGenerationError) as exc:
            failed += 1
            print(f"skipped brief {brief.get('brief_id')!r}: {exc}", file=sys.stderr)
            continue
        for d in dirs:
            print(d)
    return 1 if failed else 0
