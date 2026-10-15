"""CLI: python -m pipeline run [--dry-run] ... | python -m pipeline status"""
from __future__ import annotations

import argparse
import logging
import sys

from pipeline.run import STAGES, format_summary, run, status_table


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="research -> generate -> qa -> listing -> draft")
    r.add_argument("--stages", default=",".join(STAGES),
                   help=f"comma list, subset of {','.join(STAGES)} (default: all)")
    r.add_argument("--niche", help="limit to one niche")
    r.add_argument("--shop", help="shop id from settings.yaml (default: first shop)")
    r.add_argument("--dry-run", action="store_true",
                   help="no API calls or secrets: fake image client, template listings, fake Etsy client")
    r.add_argument("--inputs", help="research inputs dir (default: data/research_inputs)")
    r.add_argument("--briefs-dir", help="brief files dir (default: data/briefs)")
    r.add_argument("--brief", action="append", help="use this brief file (repeatable) instead of the newest per niche")
    r.add_argument("--out", help="items dir (default: output/, or output/dry-run/ with --dry-run)")
    r.add_argument("--max-items", type=int, help="cap on items generated this run")
    r.add_argument("--date", help="override the run date (YYYY-MM-DD)")
    r.add_argument("-v", "--verbose", action="store_true")

    s = sub.add_parser("status", help="list items and where each one is")
    s.add_argument("--out", help="items dir (default: output/)")

    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(status_table(a.out))
        return 0

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stages = tuple(x.strip() for x in a.stages.split(",") if x.strip())
    unknown = set(stages) - set(STAGES)
    if unknown:
        ap.error(f"unknown stages: {', '.join(sorted(unknown))}")
    summary = run(stages=stages, niche=a.niche, shop_id=a.shop, dry_run=a.dry_run, inputs_dir=a.inputs,
                  briefs_dir=a.briefs_dir, brief_files=a.brief, out_root=a.out, max_items=a.max_items, date=a.date)
    print(format_summary(summary))
    failed = any(not v["ok"] for st in summary["items"].values() for k, v in st.items() if k in ("qa", "listing"))
    return 1 if (failed or not summary["items"]) and "generate" in stages else 0


if __name__ == "__main__":
    sys.exit(main())
