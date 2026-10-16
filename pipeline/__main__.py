"""CLI: python -m pipeline run [--dry-run] ... | python -m pipeline status

Exit status of `run`: 0 = every stage that ran succeeded (items waiting for review
approval count as success, and so does a run stopped by the monthly spending cap:
spend.monthly_cap_usd is your own limit, and the summary says what waits), 1 = an item,
brief or stage failed, 2 = setup error (missing secret, no Etsy tokens, safety layer, a
settings file that cannot be read, a spending log that cannot be written).
On GitHub Actions the summary also goes to the job's step summary, and setup errors
become ::error:: annotations.

Settings: config/settings.yaml with config/settings.local.yaml (optional, server-only)
merged over it; --settings FILE reads only that file.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

import yaml

from core.config import check_date, get_shop, load_settings, today
from pipeline.run import STAGES, exit_code, format_summary, run, status_table

SETTINGS_HELP = ("settings YAML, read on its own (default: config/settings.yaml with "
                 "config/settings.local.yaml merged over it when that file exists)")


def _github_report(summary: dict, text: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for e in summary.get("setup_errors", []):
            print(f"::error title=pipeline setup::{e}".replace("\n", " "))
        for e in summary.get("errors", []):
            print(f"::error title=pipeline::{e}".replace("\n", " "))
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as f:
                f.write(f"### Pipeline run {summary['date']}\n\n```\n{text}\n```\n")
        except OSError as e:
            print(f"pipeline: could not write GITHUB_STEP_SUMMARY: {e}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="research -> generate -> qa -> listing -> draft")
    r.add_argument("--stages", default=",".join(STAGES),
                   help=f"comma list, subset of {','.join(STAGES)} (default: all)")
    r.add_argument("--niche", help="limit to one of the shop's niches")
    r.add_argument("--shop", help="shop id from settings.yaml (default: first shop)")
    r.add_argument("--dry-run", action="store_true",
                   help="no API calls or secrets: fake image client, template listings, fake Etsy client")
    r.add_argument("--inputs", help="research inputs dir (default: data/research_inputs)")
    r.add_argument("--briefs-dir", help="brief files dir (default: data/briefs; a dry run that includes "
                                        "research writes and reads <out>/briefs instead)")
    r.add_argument("--brief", action="append", help="use this brief file (repeatable) instead of the newest per niche")
    r.add_argument("--out", help="items dir (default: output/, or output/dry-run/ with --dry-run)")
    r.add_argument("--max-items", type=int, help="cap on briefs turned into items this run, reused items "
                                                 "included (without the generate stage: on items listed)")
    r.add_argument("--regenerate", action="store_true",
                   help="rebuild items that already exist for a brief (new images, new API spend) and "
                        "lift the theme cooldown (pipeline.theme_cooldown_days); items with an Etsy draft "
                        "or rejected in review are never rebuilt, and a theme with an Etsy draft is never "
                        "made again")
    r.add_argument("--date", help="override the run date (YYYY-MM-DD)")
    r.add_argument("--settings", help=SETTINGS_HELP)
    r.add_argument("-v", "--verbose", action="store_true")

    s = sub.add_parser("status", help="list items and where each one is")
    s.add_argument("--out", help="items dir (default: output/)")
    s.add_argument("--settings", help=SETTINGS_HELP)

    a = ap.parse_args(argv)
    # Before the settings are read, so the line naming a merged settings.local.yaml shows.
    logging.basicConfig(level=logging.DEBUG if getattr(a, "verbose", False) else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    try:  # a broken settings file (e.g. config/settings.local.yaml) is a setup problem: exit 2
        settings = load_settings(a.settings) if a.settings else load_settings()
        if not isinstance(settings, dict):
            raise ValueError("the file is not a YAML mapping")
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"pipeline: cannot read the settings: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if a.cmd == "status":
        print(status_table(a.out, settings=settings))
        return 0

    stages = tuple(x.strip() for x in a.stages.split(",") if x.strip())
    unknown = set(stages) - set(STAGES)
    if unknown or not stages:
        ap.error(f"unknown stages: {', '.join(sorted(unknown))}" if unknown else "--stages is empty")
    try:
        date = check_date(a.date) if a.date else today()
    except ValueError as e:
        ap.error(f"--date: {e}" if a.date else str(e))
    try:
        shop = get_shop(settings, a.shop)
    except KeyError as e:
        ap.error(f"--shop: {e.args[0] if e.args else e}")
    if a.niche and a.niche not in shop["niches"]:
        ap.error(f"--niche must be one of shop {shop['id']!r}'s niches: {', '.join(shop['niches'])}")
    if a.max_items is not None and a.max_items < 0:
        ap.error("--max-items must be 0 or more")

    summary = run(stages=stages, niche=a.niche, shop_id=a.shop, dry_run=a.dry_run, inputs_dir=a.inputs,
                  briefs_dir=a.briefs_dir, brief_files=a.brief, out_root=a.out, max_items=a.max_items,
                  date=date, settings=settings, regenerate=a.regenerate)
    text = format_summary(summary)
    print(text)
    _github_report(summary, text)
    return exit_code(summary)


if __name__ == "__main__":
    sys.exit(main())
