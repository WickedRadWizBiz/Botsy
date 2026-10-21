"""CLI for the QA stage.

    python -m qa                         # QA every item waiting for it in output/
    python -m qa --item ID [--out DIR]   # QA one item (any status)
    python -m qa review list [--status pending]
    python -m qa review approve ID [ID ...] [--batch NAME]
    python -m qa review reject ID --reason TEXT

QA never calls a network service, so --dry-run is accepted but changes nothing: it runs the
same in CI without secrets. --no-mockups skips the listing photos for a quick recheck.
Exits 1 when any checked item fails QA, so CI shows it.

`review approve` is an order (user, 2026-10-21, D2): it goes through service/orders.py as a
review_approve with created_by "user", for the queue this command names (--out, --review-dir,
--dry-run), so it is checked like every approval (an item that failed QA, is not pending, is
already a draft or was regenerated since QA is refused), posted at once to the main Discord
channel with how to undo it, journaled, and counted against limits level3_per_day (it lets
the items become Etsy drafts). Exit 0 approved, 1 refused, 2 a bad id or a setup problem.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import qa
from core.config import load_settings
from core.items import review_dir_for_root, stage_out_root
from qa import review


def _review_dir(args, settings: dict) -> Path:
    """The folder `qa` queues into (core.items.review_dir_for_root), so `review` commands
    find its entries: settings paths.review_dir for the configured output dir, else <out>/review."""
    if getattr(args, "review_dir", None):
        return Path(args.review_dir)
    return review_dir_for_root(_out(args, settings), settings)


def _out(args, settings: dict) -> Path:
    """--out, else pipeline.dry_run_output_dir with --dry-run, else settings paths.output_dir."""
    return stage_out_root(getattr(args, "out", None), settings, dry_run=bool(getattr(args, "dry_run", False)))


def _item_dir(ref: str, out: Path) -> Path:
    """An item id inside --out, or a path to an item folder."""
    as_path = Path(ref)
    if (as_path / "manifest.json").is_file():
        return as_path
    review.entry_path(ref, out)  # validates the id: no path tricks
    return out / ref


def _print_report(r: dict) -> None:
    failed = [c["name"] for c in r["checks"] if not c["passed"] and c["severity"] == "error"]
    warned = [c["name"] for c in r["checks"] if not c["passed"] and c["severity"] == "warning"]
    verdict = "PASS" if r["passed"] else "FAIL"
    line = f"{verdict}  {r['item_id']}  mockups={len(r['mockups'])}"
    if r["needs_review"]:
        line += "  needs_review"
    print(line)
    if failed:
        print(f"      errors: {', '.join(failed)}")
    if warned:
        print(f"      warnings: {', '.join(warned)}")


def cmd_run(args, settings: dict) -> int:
    out = _out(args, settings)
    review_dir = Path(args.review_dir) if args.review_dir else None
    if args.item:
        d = _item_dir(args.item, out)
        if not (d / "manifest.json").is_file():
            print(f"no item {args.item!r} in {out}", file=sys.stderr)
            return 2
        reports = [qa.run(d, settings=settings, review_dir=review_dir, mockups=not args.no_mockups)]
    else:
        reports = qa.run_all(out, review_dir, settings=settings, mockups=not args.no_mockups)
    for r in reports:
        _print_report(r)
    if not reports:
        print(f"no items waiting for QA in {out}")
    else:
        passed = sum(r["passed"] for r in reports)
        print(f"{passed}/{len(reports)} passed")
    return 0 if all(r["passed"] for r in reports) else 1


def cmd_review(args, settings: dict) -> int:
    rdir = _review_dir(args, settings)
    try:
        if args.action == "list":
            entries = review.list_entries(rdir, args.status)
            if not entries:
                print(f"no review entries in {rdir}" + (f" with status {args.status}" if args.status else ""))
            for e in entries:
                flag = "needs_review" if e.get("needs_review") else "clean"
                print(f"{e.get('status', '?'):<9} {flag:<12} {e.get('item_id')}")
                for reason in e.get("reasons", []):
                    print(f"          - {reason}")
            return 0
        if args.action == "approve":
            return _approve(args, settings, rdir)
        e = review.reject(args.id, rdir, args.reason)
    except (FileNotFoundError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(f"{e['item_id']}: {e['status']}")
    return 0


def _approve(args, settings: dict, rdir: Path) -> int:
    """An approval through service/orders.py (D2): checked, posted at once, journaled, counted."""
    from service import orders

    ids = list(dict.fromkeys(args.ids))
    for item_id in ids:
        review.entry_path(item_id, rdir)          # a bad id is a usage error (exit 2), as before
    if getattr(args, "note", None):
        print("note: approvals keep no free text (orders hold none); the order id is recorded instead")
    res = review.request_approval(ids, settings, out=_out(args, settings), review_dir=rdir,
                                  batch=getattr(args, "batch", None))
    applied = res.get("applied") if isinstance(res.get("applied"), dict) else {}
    if res.get("ok") and applied.get("ok"):
        for item_id in (applied.get("result") or {}).get("approved") or ids:
            print(f"{item_id}: approved")
        print(f"order {res.get('order_id')}" + ("; posted in the main Discord channel (with how to undo it)"
                                                if applied.get("announced") else
                                                "; not posted to Discord (see the server log), journaled"))
        print(f"Undo: botsy service control undo {res.get('order_id')}")
        return 0
    reasons = list(res.get("reasons") or []) + list(applied.get("reasons") or [])
    print("not approved:", file=sys.stderr)
    for r in dict.fromkeys(reasons):
        print(f"  {orders.describe(r)} ({r})", file=sys.stderr)
    setup = ("limits_unavailable", "control_state_unreadable")
    return 2 if res.get("stage") == "format" or any(r in setup for r in reasons) else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m qa", description="QA checks, mockups and the review queue.")
    p.add_argument("--item", help="item id (or item folder) to check; default: every item waiting for QA")
    p.add_argument("--out", help="output root holding the item folders (default: settings paths.output_dir, "
                                 "or pipeline.dry_run_output_dir with --dry-run)")
    p.add_argument("--review-dir", help="review queue folder (default: settings paths.review_dir, or <out>/review)")
    p.add_argument("--settings", help="settings YAML (default: config/settings.yaml)")
    p.add_argument("--no-mockups", action="store_true", help="skip building listing photos")
    p.add_argument("--dry-run", action="store_true",
                   help="QA never uses the network; like the other stages, the default --out becomes "
                        "pipeline.dry_run_output_dir")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command")

    # Options repeated on the review commands; SUPPRESS keeps a value given before
    # `review` from being reset by the subparser default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", default=argparse.SUPPRESS)
    common.add_argument("--review-dir", default=argparse.SUPPRESS)
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="default --out: pipeline.dry_run_output_dir (where dry runs queue)")
    rv = sub.add_parser("review", help="manage the human review queue")
    actions = rv.add_subparsers(dest="action", required=True)
    ls = actions.add_parser("list", parents=[common], help="show queue entries")
    ls.add_argument("--status", choices=review.STATUSES)
    ap = actions.add_parser("approve", parents=[common],
                            help="approve items for drafting (an order: checked, posted to Discord, journaled)")
    ap.add_argument("ids", nargs="+", metavar="id")
    ap.add_argument("--batch", default=None, help="a short name for the batch (lowercase letters, digits, -)")
    ap.add_argument("--note", default="", help=argparse.SUPPRESS)   # accepted for old scripts; not kept
    rj = actions.add_parser("reject", parents=[common], help="reject an item")
    rj.add_argument("id")
    rj.add_argument("--reason", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings(args.settings)
    if args.command == "review":
        return cmd_review(args, settings)
    return cmd_run(args, settings)


if __name__ == "__main__":
    sys.exit(main())
