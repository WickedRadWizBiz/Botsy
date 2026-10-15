"""Shared argparse CLI for every generator (python -m generators.<niche>).

    python -m generators.coloring_pages --brief data/briefs/coloring_pages-2026-10-15.json --all --dry-run
    python -m generators.activity_sheets --brief <file> --index 2 --out /tmp/out --date 2026-10-15

Prints one item directory per line. With --dry-run and no --out, items go to settings
pipeline.dry_run_output_dir (output/dry-run), as with `python -m pipeline run --dry-run`, so
trying a dry run never touches real items. --brief takes a brief file, a JSON file holding
one brief, or a JSON list of briefs. A brief that fails (blocked term, failed compliance
check, refused images) is reported and skipped (exit code 1); a `stop_errors` exception
(image cap, missing secret) ends the run.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from core.config import MissingSecretError, load_settings, resolve
from core.items import read_json
from generators.shared import default_out_root, item_date


def _brief_path(arg: str) -> Path:
    """Absolute path, so the manifest's repo-relative brief_file is right from any working dir."""
    p = Path(arg)
    return (p if p.exists() else resolve(arg)).resolve()


def _date(value: str) -> str:
    try:
        return item_date(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def run_cli(niche: str, generate: Callable, argv: list[str] | None = None, *, description: str = "",
            dry_run_help: str = "use the offline fake image client (no API calls, no secrets)",
            stop_errors: tuple[type[BaseException], ...] = (MissingSecretError,),
            skip_errors: tuple[type[BaseException], ...] = (ValueError,)) -> int:
    ap = argparse.ArgumentParser(prog=f"python -m generators.{niche}", description=description)
    ap.add_argument("--brief", required=True,
                    help="brief file (data/briefs/<niche>-<date>.json), a JSON file holding one brief, "
                         "or a JSON list of briefs")
    pick = ap.add_mutually_exclusive_group()
    pick.add_argument("--index", type=int, help="0-based index into the file's briefs list (default 0)")
    pick.add_argument("--all", action="store_true", help=f"every {niche} brief in the file")
    ap.add_argument("--out", help="output root (default: settings paths.output_dir, or "
                                  "pipeline.dry_run_output_dir with --dry-run)")
    ap.add_argument("--date", type=_date, help="item date YYYY-MM-DD (default: PIPELINE_DATE or today)")
    ap.add_argument("--shop", help="shop id (default: the brief file's shop_id, else 'main')")
    ap.add_argument("--settings", help="alternate settings YAML (default: config/settings.yaml)")
    ap.add_argument("--dry-run", action="store_true", help=dry_run_help)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    path = _brief_path(args.brief)
    if not path.is_file():
        ap.error(f"brief file not found: {args.brief}")
    try:
        data = read_json(path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        ap.error(f"cannot read brief file {path}: {exc}")
    if isinstance(data, list):
        data = {"briefs": data}
    if not isinstance(data, dict):
        ap.error(f"{path} is not a brief file (expected a JSON object or list)")
    briefs = data["briefs"] if "briefs" in data else [data]
    if not isinstance(briefs, list) or not all(isinstance(b, dict) for b in briefs):
        ap.error(f"{path}: every brief must be a JSON object")
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
    out = default_out_root(args.out, settings, args.dry_run)
    failed = 0
    for brief in selected:
        try:
            dirs = generate(brief, brief_file=path, shop_id=shop, out_root=out, date=args.date,
                            dry_run=args.dry_run, settings=settings)
        except stop_errors as exc:
            print(f"stopped at brief {brief.get('brief_id')!r}: {exc}", file=sys.stderr)
            return 1
        except skip_errors as exc:
            failed += 1
            print(f"skipped brief {brief.get('brief_id')!r}: {exc}", file=sys.stderr)
            continue
        for d in dirs:
            print(d)
    return 1 if failed else 0
