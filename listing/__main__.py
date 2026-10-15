"""CLI for the listing stage.

    python -m listing                    # list every qa_passed item in output/
    python -m listing --item ID          # one item (an id inside --out, or an item folder)
    python -m listing --dry-run          # template copy, no API call, no secrets
    python -m listing --out DIR --review-dir DIR --settings FILE

Exits 1 when any item could not be listed (blocked term, missing QA, ...), so CI shows it.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import listing
from core.config import load_settings
from core.items import output_root
from listing import review


def _item_dir(ref: str, out: Path) -> Path:
    as_path = Path(ref)
    if (as_path / "manifest.json").is_file():
        return as_path
    review.entry_path(ref, out)  # validates the id: no path tricks
    return out / ref


def _print(result: dict) -> None:
    if not result["ok"]:
        print(f"FAIL  {result['item_id']}  {result['error']}")
        return
    lst = result["listing"]
    flags = [f"writer={lst['writer']['kind']}"]
    if lst["writer"]["repaired"]:
        flags.append("repaired")
    if lst["needs_review"]:
        flags.append("needs_review")
    print(f"OK    {result['item_id']}  {' '.join(flags)}")
    print(f"      {lst['title']}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m listing", description="Write Etsy listing copy for QA-passed items.")
    p.add_argument("--item", help="item id (or item folder); default: every qa_passed item")
    p.add_argument("--out", help="output root holding the item folders (default: settings paths.output_dir)")
    p.add_argument("--review-dir", help="review queue folder (default: settings paths.review_dir, or <out>/review)")
    p.add_argument("--settings", help="settings YAML (default: config/settings.yaml)")
    p.add_argument("--dry-run", action="store_true", help="template copy, no API call and no secrets needed")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings(args.settings)
    out = Path(args.out) if args.out else output_root(settings)
    review_dir = Path(args.review_dir) if args.review_dir else (out / "review" if args.out else None)
    if args.item:
        try:
            d = _item_dir(args.item, out)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        try:
            lst = listing.write_listing(d, dry_run=args.dry_run, settings=settings, review_dir=review_dir)
            results = [{"item_id": d.name, "ok": True, "listing": lst, "error": None}]
        except Exception as e:  # reported like run_all does, so CI output is the same either way
            if not isinstance(e, listing.ListingError):
                logging.getLogger("listing").exception("listing %s crashed", d.name)
            results = [{"item_id": d.name, "ok": False, "listing": None, "error": f"{type(e).__name__}: {e}"}]
    else:
        results = listing.run_all(out, dry_run=args.dry_run, settings=settings, review_dir=review_dir)
    for r in results:
        _print(r)
    if not results:
        print(f"no qa_passed items in {out}")
    ok = sum(r["ok"] for r in results)
    if results:
        print(f"{ok}/{len(results)} listed")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
