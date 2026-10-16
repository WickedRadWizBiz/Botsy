"""CLI: python -m service <command> [--settings FILE] [--inbox DIR] [--date YYYY-MM-DD]

Runs on the server (docs/SERVER.md section 5); on the server, `botsy service <command>`
runs it as the botsy user.

    ingest       take finished uploads out of the inbox (nothing else)
    research     research every shop niche, with history snapshots
    run          generate -> qa -> listing -> draft for every shop (dry run unless service.live)
    searchlist   write outbox/search-list.txt for this week
    tick         ingest, then research + run when an export was accepted, then status
                 (--force: research + run even without a new upload; --own-stats: refresh
                 our own shop's Etsy stats first, live mode only)
    status       rewrite outbox/status.txt from status.json and show it

Every command takes the service lock first, so two never run at once.
Exit codes: 0 ok, 1 something failed (see outbox/status.txt), 2 setup problem, 3 another
service command held the lock longer than service.lock_timeout_s.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import sys

import yaml

from core.config import check_date, load_settings, today
from service import inbox, status, tick
from service.lock import EXIT_LOCKED, LockTimeout, service_lock
from service.paths import ServicePaths, is_live, load_config, service_paths

COMMANDS = {
    "ingest": "take finished uploads out of the inbox",
    "research": "research every shop niche (with history snapshots)",
    "run": "generate -> qa -> listing -> draft for every shop",
    "searchlist": "write outbox/search-list.txt",
    "tick": "ingest, then research + run when something new arrived, then status",
    "status": "rewrite outbox/status.txt and show it",
}


def _common(sub: bool) -> argparse.ArgumentParser:
    # The same options work before and after the command. On the subcommand they default
    # to SUPPRESS, so an option given before the command is not reset by the subparser.
    kw = {"default": argparse.SUPPRESS} if sub else {}
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--settings", help="settings YAML (default: config/settings.yaml)", **kw)
    p.add_argument("--inbox", help="inbox folder (default: settings service.inbox_dir, /srv/botsy-inbox)", **kw)
    p.add_argument("--date", help="date for file names, YYYY-MM-DD (default: today)", **kw)
    p.add_argument("-v", "--verbose", action="store_true", help="more log output", **kw)
    return p


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m service", parents=[_common(False)],
                                 description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    parsers = {name: sub.add_parser(name, parents=[_common(True)], help=text)
               for name, text in COMMANDS.items()}
    parsers["tick"].add_argument("--force", action="store_true",
                                 help="research + run even when no new export arrived (the daily timer)")
    parsers["tick"].add_argument("--own-stats", action="store_true",
                                 help="first refresh our own shop's Etsy stats (live mode only)")
    return ap


# ---------------------------------------------------------------- commands

def cmd_ingest(a, settings: dict, paths: ServicePaths, day: str) -> int:
    result = inbox.ingest(paths.incoming, paths.inputs, paths.rejected, settings=settings, date=day)
    for p in result.accepted:
        print(f"accepted: {p.name}")
    for name in result.duplicates:
        print(f"duplicate (already received): {name}")
    for name, reason in result.rejected:
        print(f"not used: {name}: {reason}")
    if result.empty():
        print(f"no finished uploads in {paths.incoming}")
    return 0


def cmd_research(a, settings: dict, paths: ServicePaths, day: str) -> int:
    out = tick.research_all(settings, paths, day)
    for f in out["brief_files"]:
        print(f"wrote {f}")
    print(f"{len(out['brief_files'])} brief file(s), {out['briefs']} design brief(s); "
          f"history snapshots in {paths.history}")
    for note in out["notes"]:
        print(f"- {note}")
    for err in out["errors"]:
        print(f"! {err}", file=sys.stderr)
    return 1 if out["errors"] else 0


def cmd_run(a, settings: dict, paths: ServicePaths, day: str) -> int:
    from pipeline import run as prun

    live = is_live(settings)
    print("live run: real API calls and Etsy drafts" if live else "dry run (service.live is false)")
    out = tick.run_all(settings, paths, day, live=live)
    code = 0
    for summary in out["summaries"]:
        print(prun.format_summary(summary))
        code = max(code, prun.exit_code(summary))
    for note in out["notes"]:
        print(f"- {note}")
    for err in out["errors"]:
        print(f"! {err}", file=sys.stderr)
    return max(code, 1) if out["errors"] else code


def cmd_searchlist(a, settings: dict, paths: ServicePaths, day: str) -> int:
    searchlist = importlib.import_module("service.searchlist")  # looked up at call time (tests swap it)
    week = (status.load(paths.outbox) or {}).get("week")
    text = searchlist.build(settings, paths.history, date=day, status=week)
    latest = searchlist.write(paths.outbox, text, day)   # mode 640: the upload account reads it
    print(f"wrote {latest}")
    return 0


def cmd_tick(a, settings: dict, paths: ServicePaths, day: str) -> int:
    rec = tick.tick(settings, inbox_dir=paths.inbox, force=a.force, own_stats=a.own_stats, date=day)
    ing = rec["ingest"]
    print(f"tick {rec['date']}: {len(ing['accepted'])} accepted, {len(ing['duplicates'])} duplicate, "
          f"{len(ing['rejected'])} rejected; research {'ran' if rec['research']['ran'] else 'not run'} "
          f"({len(rec['research']['brief_files'])} brief files); run {'ran' if rec['run']['ran'] else 'not run'} "
          f"({rec['run']['generated']} items, {rec['run']['drafts']} drafts"
          f"{', dry run' if rec['run']['dry_run'] else ''})")
    for err in rec["errors"]:
        print(f"! {err}", file=sys.stderr)
    print(f"status: {paths.outbox / status.TEXT_NAME}")
    return int(rec["exit_code"])


def cmd_status(a, settings: dict, paths: ServicePaths, day: str) -> int:
    txt = status.refresh(paths.outbox, live=is_live(settings))
    sys.stdout.write(txt.read_text(encoding="utf-8").replace("\r\n", "\n"))
    return 0


HANDLERS = {"ingest": cmd_ingest, "research": cmd_research, "run": cmd_run,
            "searchlist": cmd_searchlist, "tick": cmd_tick, "status": cmd_status}


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        day = check_date(a.date) if a.date else today()
    except ValueError as e:
        ap.error(f"--date: {e}" if a.date else str(e))
    try:
        settings = load_settings(a.settings) if a.settings else load_settings()
        if not isinstance(settings, dict):
            raise ValueError("the file is not a YAML mapping")
        paths = service_paths(settings, a.inbox)
        timeout = float(load_config(settings)["lock_timeout_s"])
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as e:
        print(f"service: cannot read the settings: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    try:
        with service_lock(paths.lock, timeout):
            return HANDLERS[a.cmd](a, settings, paths, day)
    except LockTimeout as e:
        print(f"service: {e}", file=sys.stderr)
        return EXIT_LOCKED
    except (OSError, ValueError) as e:  # e.g. the inbox is missing or a settings mistake
        print(f"service {a.cmd}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
