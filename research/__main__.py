"""CLI: python -m research [--niche N] [--shop ID] [--inputs DIR] [--out DIR] [--date D]
[--dry-run] [--demo]."""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile

from core.config import ROOT
from core.items import read_json
from research.guard import BriefComplianceError
from research.pipeline import NoInputsError, run


def _show(path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research",
                                 description="Build design briefs from research CSV exports.")
    ap.add_argument("--niche", help="only this niche (default: every niche of the shop)")
    ap.add_argument("--shop", default="main", help="shop id from config/settings.yaml")
    ap.add_argument("--inputs", help="folder with the CSV exports (default: research.inputs_dir)")
    ap.add_argument("--out", help="folder for brief JSON files (default: research.briefs_dir)")
    ap.add_argument("--date", help="date in output names, YYYY-MM-DD (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="build and check briefs, write nothing")
    ap.add_argument("--demo", action="store_true",
                    help="use built-in synthetic exports instead of --inputs (CI and dry runs); "
                         "needs --out or --dry-run")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.demo and not (args.out or args.dry_run):
        # The weekly workflow drafts listings from data/briefs; synthetic briefs must not
        # land there (or overwrite today's real <niche>-<date>.json) by default.
        print("research: --demo writes synthetic briefs; pass --out DIR (a scratch folder) "
              "or --dry-run.", file=sys.stderr)
        return 2
    if args.demo and args.inputs:
        print("research: --demo ignores --inputs; use one or the other.", file=sys.stderr)
        return 2

    try:
        if args.demo:
            from research.demo import write_demo_inputs

            with tempfile.TemporaryDirectory(prefix="research-demo-") as tmp:
                write_demo_inputs(tmp)
                paths = run(args.niche, args.shop, tmp, args.out, args.date, dry_run=args.dry_run)
        else:
            paths = run(args.niche, args.shop, args.inputs, args.out, args.date, dry_run=args.dry_run)
    except NoInputsError as e:
        print(f"research: {e}", file=sys.stderr)
        return 2
    except BriefComplianceError as e:
        print(f"research: nothing written. {e}", file=sys.stderr)
        return 3
    except (ValueError, KeyError) as e:
        print(f"research: {e}", file=sys.stderr)
        return 2

    if not paths:
        print("research: no counted rows for any niche (see warnings above); no briefs written.")
    for p in paths:
        if args.dry_run:
            print(f"would write {_show(p)}")
            continue
        doc = read_json(p)
        review = sum(1 for b in doc["briefs"] if b["needs_review"])
        print(f"wrote {_show(p)}: {len(doc['briefs'])} briefs ({review} need review), "
              f"{doc['listing_count']} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
