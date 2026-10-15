"""CLI for the listing stage.

    python -m listing                    # list every qa_passed item in output/
    python -m listing --item ID          # one item (an id inside --out, or an item folder)
    python -m listing --dry-run          # template copy, no API call, no secrets (default --out:
                                         # settings pipeline.dry_run_output_dir, output/dry-run)
    python -m listing --out DIR --review-dir DIR --settings FILE

A dry run on real (not dry-run generated) items only previews and writes nothing.
Exits 1 when any item could not be listed (blocked term, missing QA, ...), so CI shows it,
and 2 on a setup error (bad item id, ANTHROPIC_API_KEY missing) before any item is tried.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import listing
from core.config import MissingSecretError, load_settings
from core.items import stage_out_root, valid_item_id


def _item_dir(ref: str, out: Path) -> Path:
    as_path = Path(ref)
    if (as_path / "manifest.json").is_file():
        return as_path
    if not valid_item_id(ref):  # no path tricks
        raise ValueError(f"invalid item id {ref!r}")
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
    if lst.get("preview"):
        flags.append("preview (dry run on a real item; nothing written)")
    print(f"OK    {result['item_id']}  {' '.join(flags)}")
    print(f"      {lst['title']}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m listing", description="Write Etsy listing copy for QA-passed items.")
    p.add_argument("--item", help="item id (or item folder); default: every qa_passed item "
                                  "(and, in a real run, real items still holding dry-run copy)")
    p.add_argument("--out", help="output root holding the item folders (default: settings paths.output_dir, "
                                 "or pipeline.dry_run_output_dir with --dry-run)")
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
    out = stage_out_root(args.out, settings, dry_run=args.dry_run)
    # None: write_listing / run_all use core.items.review_dir_for per item, the same
    # queue QA and the publisher use (settings paths.review_dir for the configured output dir).
    review_dir = Path(args.review_dir) if args.review_dir else None
    d = None
    if args.item:
        try:
            d = _item_dir(args.item, out)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    try:
        results = _run(d, out, settings, review_dir, dry_run=args.dry_run)
    except MissingSecretError as e:  # setup error: stop before trying every item
        print(f"error: {e} Or use --dry-run for template copy.", file=sys.stderr)
        return 2
    for r in results:
        _print(r)
    if not results:
        print(f"no items to list in {out}")
    ok = sum(r["ok"] for r in results)
    if results:
        previews = sum(1 for r in results if r["ok"] and r["listing"].get("preview"))
        print(f"{ok}/{len(results)} listed" + (f" ({previews} preview only, nothing written)" if previews else ""))
    return 0 if ok == len(results) else 1


def _run(d: Path | None, out: Path, settings: dict, review_dir: Path | None, *, dry_run: bool) -> list[dict]:
    """One item (`d`) or every item in `out`, as run_all results. MissingSecretError passes."""
    if d is None:
        return listing.run_all(out, dry_run=dry_run, settings=settings, review_dir=review_dir)
    try:
        lst = listing.write_listing(d, dry_run=dry_run, settings=settings, review_dir=review_dir)
        return [{"item_id": d.name, "ok": True, "listing": lst, "error": None}]
    except MissingSecretError:
        raise
    except Exception as e:  # reported like run_all does, so CI output is the same either way
        if not isinstance(e, listing.ListingError):
            logging.getLogger("listing").exception("listing %s crashed", d.name)
        return [{"item_id": d.name, "ok": False, "listing": None, "error": f"{type(e).__name__}: {e}"}]


if __name__ == "__main__":
    sys.exit(main())
