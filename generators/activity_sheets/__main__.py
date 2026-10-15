"""CLI: build activity packs from a brief file.

    python -m generators.activity_sheets --brief data/briefs/activity_sheets-2026-10-15.json --all
    python -m generators.activity_sheets --brief <file> --index 2 --out /tmp/out --date 2026-10-15

Prints one item directory per line. --dry-run is accepted so the pipeline can pass it to
every stage; this generator never calls a network service, so the output is the same.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from core.config import load_settings, resolve
from core.items import read_json
from generators.activity_sheets import NICHE, generate


def _brief_path(arg: str) -> Path:
    """Absolute path, so the manifest's repo-relative brief_file is right from any working dir."""
    p = Path(arg)
    return (p if p.exists() else resolve(arg)).resolve()


def _iso_date(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError(f"{value!r} is not YYYY-MM-DD")
    return value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m generators.activity_sheets",
                                 description="Generate procedural activity-sheet packs from a brief file.")
    ap.add_argument("--brief", required=True,
                    help="brief file (data/briefs/<niche>-<date>.json) or a JSON file holding one brief")
    pick = ap.add_mutually_exclusive_group()
    pick.add_argument("--index", type=int, help="0-based index into the file's briefs list (default 0)")
    pick.add_argument("--all", action="store_true", help=f"every {NICHE} brief in the file")
    ap.add_argument("--out", help="output root (default: settings paths.output_dir)")
    ap.add_argument("--date", type=_iso_date, help="item date YYYY-MM-DD (default: PIPELINE_DATE or today)")
    ap.add_argument("--shop", help="shop id (default: the brief file's shop_id, else 'main')")
    ap.add_argument("--settings", help="alternate settings YAML (default: config/settings.yaml)")
    ap.add_argument("--dry-run", action="store_true", help="accepted for pipeline symmetry; output is identical")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    path = _brief_path(args.brief)
    if not path.exists():
        ap.error(f"brief file not found: {args.brief}")
    try:
        data = read_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        ap.error(f"cannot read brief file {path}: {exc}")
    if isinstance(data, list):
        data = {"briefs": data}
    if not isinstance(data, dict):
        ap.error(f"{path} is not a brief file (expected a JSON object)")
    briefs = data["briefs"] if "briefs" in data else [data]
    if not all(isinstance(b, dict) for b in briefs):
        ap.error(f"{path}: every brief must be a JSON object")
    if args.all:
        selected = [b for b in briefs if b.get("niche") == NICHE]
        if not selected:
            print(f"no {NICHE} briefs in {path}", file=sys.stderr)
            return 0
    else:
        idx = args.index or 0
        if not 0 <= idx < len(briefs):
            ap.error(f"--index {idx} out of range (file has {len(briefs)} briefs)")
        if briefs[idx].get("niche", NICHE) != NICHE:
            ap.error(f"brief {idx} is for niche {briefs[idx].get('niche')!r}, not {NICHE!r}")
        selected = [briefs[idx]]

    settings = load_settings(args.settings) if args.settings else load_settings()
    shop = args.shop or data.get("shop_id") or "main"
    failed = 0
    for brief in selected:
        try:
            dirs = generate(brief, brief_file=path, shop_id=shop, out_root=args.out, date=args.date,
                            dry_run=args.dry_run, settings=settings)
        except ValueError as exc:
            failed += 1
            print(f"skipped brief {brief.get('brief_id')!r}: {exc}", file=sys.stderr)
            continue
        for d in dirs:
            print(d)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
