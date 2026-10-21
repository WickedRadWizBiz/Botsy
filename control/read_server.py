"""botsy-read: Hermes's Level 0 tools over MCP stdio (docs/OVERSEER.md "botsy-control reference").

    build(settings=None, clock=None) -> MCPServer ; main() -> int

Tools: status, spend, daily_log, review_list, review_preview, plan_status, plan_facts,
briefs_summary, capacity, orders_recent, events_recent (control/tools.py has what each
returns). They only read Botsy's own records and change nothing. Started by
`python -m control read` (deploy/lightsail/botsy-control-read, run by the hermes user as botsy
through sudo).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

SERVER = "read"


def build(settings: dict | None = None, clock: Callable[[], datetime] | None = None):
    from control import build as _build

    return _build(SERVER, settings=settings, clock=clock)


def main(settings: dict | None = None) -> int:
    from control import run

    run(SERVER, settings=settings)
    return 0


__all__ = ["SERVER", "build", "main"]
