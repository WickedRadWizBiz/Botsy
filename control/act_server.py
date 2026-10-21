"""botsy-act: Hermes's action tools over MCP stdio (docs/OVERSEER.md "botsy-control reference").

    build(settings=None, clock=None) -> MCPServer ; main() -> int

Tools: submit_order, approve_order, pause, resume, lower_cap, search_ideas, undo_order
(orders, checked by service.orders.submit / validate / approve / undo; plans through
service.plan). No codes (decided 2026-10-21). The server applies nothing itself; created_by
is always "hermes". Started by
`python -m control act` (deploy/lightsail/botsy-control-act, run by the hermes user as botsy
through sudo).

The plan service (service/plan.py), when installed, is imported first, so plan handlers it
registers with service.orders.register_handler run when a plan is applied from here.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

SERVER = "act"


def build(settings: dict | None = None, clock: Callable[[], datetime] | None = None):
    from control import build as _build
    from control.tools import _plan_module

    _plan_module()                     # registers the plan service's order handlers, if it has any
    return _build(SERVER, settings=settings, clock=clock)


def main(settings: dict | None = None) -> int:
    from control import run
    from control.tools import _plan_module

    _plan_module()
    run(SERVER, settings=settings)
    return 0


__all__ = ["SERVER", "build", "main"]
