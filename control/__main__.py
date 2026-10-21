"""python -m control read|act: start one botsy-control MCP server on stdio.

Exactly one argument, "read" or "act"; anything else exits with code 64 and starts nothing.
stdout carries only the MCP protocol. Logs go to Botsy's own file (settings
control.log_file, default data/logs/botsy-control.log, one per server: botsy-control-read.log and
botsy-control-act.log, rotated at 1 MB, 3 kept), never to
stdout and never to Hermes: one line per tool call with the tool name, ok and reason codes,
never an argument. If that file cannot be opened, logging is off and one
line says so on stderr.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys

USAGE = "usage: python -m control read|act"


def log_path(settings: dict | None, server: str):
    """Each server logs to its own file next to control.log_file (botsy-control-read.log,
    botsy-control-act.log): two processes rotating one file would rename it under each other."""
    from control.tools import LOG_DEFAULTS
    from core.config import resolve, section

    raw = section(settings or {}, "control", LOG_DEFAULTS).get("log_file") or LOG_DEFAULTS["log_file"]
    path = resolve(str(raw))
    return path.with_name(f"{path.stem}-{server}{path.suffix or '.log'}")


# Botsy's own loggers write fixed texts (never an argument, never a code). Every other logger
# (the MCP SDK, pydantic, anyio, httpx) may put the raw request in its message or traceback (a
# validation error quotes its input, which is Hermes's text), so for those only
# the logger name, the level and the exception's type are written.
OWN_LOGGERS = ("control", "service", "core", "qa", "pipeline", "listing", "publisher", "research", "generators")


class WithholdDetail(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        top = record.name.split(".", 1)[0]
        if top not in OWN_LOGGERS:
            exc = record.exc_info[1] if record.exc_info else None
            record.msg = "details withheld" + (f" ({type(exc).__name__})" if exc is not None else "")
            record.args = None
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def _logging(settings: dict | None, server: str = "read") -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    try:
        path = log_path(settings, server)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                                                         encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    except (OSError, ValueError, TypeError):
        handler = logging.NullHandler()
        print("botsy-control: the log file cannot be opened; logging is off", file=sys.stderr)
    handler.addFilter(WithholdDetail())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.captureWarnings(True)      # warnings go to the log file, never to stderr (Hermes may read it)
    for noisy in ("mcp", "httpx", "httpcore", "anyio", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)    # never DEBUG: it would print requests


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) != 1 or argv[0] not in ("read", "act"):
        print(USAGE, file=sys.stderr)
        return 64
    server = argv[0]
    os.umask(0o027)
    try:
        from core.config import load_settings

        settings = load_settings()
    except Exception as e:  # noqa: BLE001 - the tools report settings_unreadable per call
        settings = None
        print(f"botsy-control: settings not readable ({type(e).__name__})", file=sys.stderr)
    _logging(settings, server)
    logging.getLogger("control").info("botsy-control %s started (pid %d)", server, os.getpid())
    try:
        if server == "read":
            from control.read_server import main as run
        else:
            from control.act_server import main as run
        return run()                    # settings are read again on every call
    except (KeyboardInterrupt, BrokenPipeError):
        return 0
    except Exception as e:  # noqa: BLE001 - never a traceback on stderr: under sudo it reaches Hermes
        logging.getLogger("control").error("botsy-control %s ended: %s", server, type(e).__name__)
        print(f"botsy-control: stopped ({type(e).__name__})", file=sys.stderr)
        return 70
    finally:
        logging.getLogger("control").info("botsy-control %s stopped", server)


if __name__ == "__main__":
    sys.exit(main())
