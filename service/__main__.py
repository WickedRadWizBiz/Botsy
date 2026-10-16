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
    digest       write outbox/daily-log.txt and post it to Discord (the last --hours; by default
                 24, or back to the last daily log when that is older, at most 7 days)
    spend        show this month's estimated API spending (--month YYYY-MM for another month)
    notify-test  send a test message to Discord and say whether it worked
    cleanup      remove old rejected uploads, old exports and old practice items now
                 (every tick already does this at the end)

Every command that changes Botsy's working files takes the service lock first, so two
never run at once. digest, spend and notify-test only read them (digest writes only the
daily log), so they never wait for a running check.
Exit codes: 0 ok, 1 something failed (see outbox/status.txt), 2 setup problem, 3 another
service command held the lock longer than service.lock_timeout_s.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import logging
import sys

import yaml

from core import events
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
    "digest": "write outbox/daily-log.txt and post it to Discord",
    "spend": "show this month's estimated API spending",
    "notify-test": "send a test message to Discord",
    "cleanup": "remove old rejected uploads, old exports and old practice items",
}
# Read-only commands: they never wait for the service lock (a long check may hold it).
NO_LOCK = {"digest", "spend", "notify-test"}
MAX_DIGEST_HOURS = 24 * 31
DIGEST_HOURS = 24.0
DIGEST_CATCH_UP_DAYS = 7      # without --hours, a log also covers back to the last one, this far


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
    parsers["digest"].add_argument("--hours", type=_hours, default=None,
                                   help="how many hours back the log goes (default 24, or back to "
                                        "the last daily log when that is older)")
    parsers["spend"].add_argument("--month", type=_month, default=None,
                                  help="month to show, YYYY-MM (default: this month)")
    return ap


def _hours(value: str) -> float:
    try:
        hours = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--hours must be a number of hours, got {value!r}") from None
    if not 0 < hours <= MAX_DIGEST_HOURS:
        raise argparse.ArgumentTypeError(f"--hours must be more than 0 and at most {MAX_DIGEST_HOURS}")
    return hours


def _month(value: str) -> str:
    from core.spend import MONTH_RE

    if not MONTH_RE.fullmatch(value or ""):
        raise argparse.ArgumentTypeError(f"--month must look like 2026-10, got {value!r}")
    return value


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
    print(f"{len(out['brief_files'])} brief file(s), {out['briefs']} design brief(s)"
          + (f", {out['stage']} stage" if out.get("stage") else "")
          + f"; history snapshots in {paths.history}")
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
    events.emit("search_list_written", settings=settings, path=str(latest))
    notify = importlib.import_module("service.notify")
    if notify.wants("search_list_written", settings) and notify.problem(settings) is None:
        sent = notify.send("This week's search list is ready (attached). The Botsy helper also puts it in "
                           "Documents\\Botsy on your PC. Search each line in EverBee and click Export CSV.",
                           file=latest, settings=settings, kinds=["search_list_written"])
        print("Discord: search list sent" if sent else "Discord: not sent now (see the log above)")
    return 0


def cmd_tick(a, settings: dict, paths: ServicePaths, day: str) -> int:
    rec = tick.tick(settings, inbox_dir=paths.inbox, force=a.force, own_stats=a.own_stats, date=day)
    ing = rec["ingest"]
    stage = f", {rec['research']['stage']} stage" if rec["research"].get("stage") else ""
    print(f"tick {rec['date']}: {len(ing['accepted'])} accepted, {len(ing['duplicates'])} duplicate, "
          f"{len(ing['rejected'])} rejected; research {'ran' if rec['research']['ran'] else 'not run'} "
          f"({len(rec['research']['brief_files'])} brief files{stage}); "
          f"run {'ran' if rec['run']['ran'] else 'not run'} "
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


def digest_since(outbox, until: _dt.datetime, hours: float | None, latest_name: str) -> _dt.datetime:
    """Start of the log's window: --hours back from `until`. Without --hours, 24 hours back,
    or back to when the last daily log was written if that is older (the server was off,
    or the timer ran late), at most DIGEST_CATCH_UP_DAYS, so nothing falls between two logs."""
    since = until - _dt.timedelta(hours=hours if hours is not None else DIGEST_HOURS)
    if hours is not None:
        return since
    try:
        last = _dt.datetime.fromtimestamp((outbox / latest_name).stat().st_mtime, _dt.timezone.utc)
    except (OSError, ValueError, OverflowError):
        return since
    if until - _dt.timedelta(days=DIGEST_CATCH_UP_DAYS) <= last < since:
        return last
    return since


def cmd_digest(a, settings: dict, paths: ServicePaths, day: str) -> int:
    digest = importlib.import_module("service.digest")
    notify = importlib.import_module("service.notify")
    until = _dt.datetime.now(_dt.timezone.utc)
    since = digest_since(paths.outbox, until, getattr(a, "hours", None), digest.LATEST_NAME)
    text = digest.build(since, until, settings)
    latest = digest.write(paths.outbox, text, day)
    print(f"wrote {latest}")
    if not (notify.config(settings).get("discord") or {}).get("digest", True):
        print("Discord: the daily log is not posted (notify.discord.digest is false)")
        return 0
    why = notify.problem(settings)
    if why:
        print(f"Discord: not sent. {why}")
        return 0
    if notify.send(text, settings=settings, urgent=True, filename=digest.LATEST_NAME):
        print("Discord: daily log sent")
        return 0
    print("Discord: the daily log could not be sent (the server log says why). It is still in "
          f"{latest} and on your PC.", file=sys.stderr)
    return 1


def cmd_spend(a, settings: dict, paths: ServicePaths, day: str) -> int:
    from core import spend

    sys.stdout.write(spend.table(settings, a.month))
    return 0


def cmd_notify_test(a, settings: dict, paths: ServicePaths, day: str) -> int:
    notify = importlib.import_module("service.notify")
    why = notify.problem(settings)
    if why:
        print(f"Not sent. {why}")
        return 2
    mode = "LIVE" if is_live(settings) else "TEST (dry run)"
    if notify.send(f"Botsy test message: Discord messages from your server work. Mode: {mode}.",
                   settings=settings, urgent=True):
        print("Sent a test message to Discord. Look in the channel you made the webhook for.")
        return 0
    print("Could not send the test message. Check that DISCORD_WEBHOOK_URL in /opt/botsy/.env is the "
          "whole webhook link (copy it again in Discord), then try again. The lines above say what "
          "went wrong.", file=sys.stderr)
    return 1


def cmd_cleanup(a, settings: dict, paths: ServicePaths, day: str) -> int:
    cleanup = importlib.import_module("service.cleanup")   # looked up at call time (tests swap it)
    result = cleanup.run(settings)
    print(cleanup.describe(result) or "nothing old enough to remove")
    problems = list(getattr(result, "problems", None) or [])
    for p in problems:
        print(f"! could not tidy: {tick.scrub(p)}", file=sys.stderr)
    return 1 if problems else 0


HANDLERS = {"ingest": cmd_ingest, "research": cmd_research, "run": cmd_run,
            "searchlist": cmd_searchlist, "tick": cmd_tick, "status": cmd_status,
            "digest": cmd_digest, "spend": cmd_spend, "notify-test": cmd_notify_test,
            "cleanup": cmd_cleanup}


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
        if a.cmd in NO_LOCK:
            return HANDLERS[a.cmd](a, settings, paths, day)
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
