"""`python -m service control ...`: your orders from the server (docs/OVERSEER.md "Four
permission levels"). Every command is an order with created_by "user" and goes through
service/orders.py like Hermes's orders do, so the same checks and the same journal apply.

No codes (decided 2026-10-21). A Level 3 command here (approve, raise-cap, resume-budget,
resume after Botsy's own stop) is your approval: it is applied at once, counted against limits
level3_per_day, posted at once to the main Discord channel with how to undo it, and journaled.

    pause [--reason R]          stop new paid work (always allowed)
    resume                      lift a pause
    lower-cap USD               lower the monthly cap in force (always allowed)
    raise-cap USD               raise it (never above the admin's ceiling in /etc/botsy/limits.yaml)
    resume-budget               restart paid work after this month's budget stop
    approve ITEM... [--batch N] approve review items (one approval for the whole batch)
    approve --order ID          approve an order that waits for you (a plan: use `plan approve`)
    reject ITEM --reason R      reject a review item       hold ITEM --reason R   hold one
    cancel ORDER                cancel an order            undo ORDER             undo an applied order
    orders [--week W]           list orders                limits / capacity      show them

These never take the service lock (orders have their own lock, data/orders/.lock, 30 s).
Exit codes: 0 done, 1 refused, 2 a setup problem the admin must fix (the limits file is
missing or broken, or the control state file cannot be read), 3 the orders lock was busy.
"""
from __future__ import annotations

import argparse
import json

from service import orders

ACTIONS = ("pause", "resume", "lower-cap", "raise-cap", "resume-budget", "approve", "reject", "hold", "cancel",
           "undo", "orders", "limits", "capacity")
SETUP_REASONS = ("limits_unavailable", "control_state_unreadable")


def add_arguments(p: argparse.ArgumentParser, parents=()) -> None:
    """The control subcommands (service/__main__.py adds them to its `control` parser, with its
    common options as `parents` so --settings works after the action too)."""
    sub = p.add_subparsers(dest="action", required=True, metavar="action")
    _add = sub.add_parser
    sub.add_parser = lambda name, **kw: _add(name, parents=list(parents), **kw)
    a = sub.add_parser("pause", help="stop new paid work")
    a.add_argument("--reason", choices=orders.PAUSE_REASONS, default="user_request")
    sub.add_parser("resume", help="lift a pause")
    for name, text in (("lower-cap", "lower the monthly cap in force"), ("raise-cap", "raise the monthly cap")):
        a = sub.add_parser(name, help=text)
        a.add_argument("usd", type=float, help="the new monthly cap in dollars")
    sub.add_parser("resume-budget", help="restart paid work after this month's budget stop")
    a = sub.add_parser("approve", help="approve review items, or an order that waits for you")
    a.add_argument("items", nargs="*", help="review item ids")
    a.add_argument("--batch", default=None, help="a short name for the batch (lowercase letters, digits, -)")
    a.add_argument("--order", default=None, help="approve this waiting order instead")
    for name, choices in (("reject", orders.REJECT_REASONS), ("hold", orders.HOLD_REASONS)):
        a = sub.add_parser(name, help=f"{name} a review item")
        a.add_argument("item", help="review item id")
        a.add_argument("--reason", required=True, choices=choices)
    for name in ("cancel", "undo"):
        a = sub.add_parser(name, help=f"{name} an order")
        a.add_argument("order_id", help="order id, e.g. pa-20261021-0001")
    a = sub.add_parser("orders", help="list orders")
    a.add_argument("--week", default=None, help="ISO week, e.g. 2026-W43")
    sub.add_parser("limits", help="show the admin-only limits file")
    sub.add_parser("capacity", help="show whether Botsy has room for more work")


def _order(kind: str, payload: dict) -> dict:
    return {"schema": orders.SCHEMA, "kind": kind, "created_by": "user", "payload": payload}


def _report(res: dict, what: str, out=print) -> int:
    """Print an orders result in plain words. Exit code 0 / 1 / 2."""
    applied = res.get("applied") if isinstance(res.get("applied"), dict) else None
    final = applied or res
    reasons = list(res.get("reasons") or []) + list((applied or {}).get("reasons") or [])
    plan_res = res.get("plan") if isinstance(res.get("plan"), dict) else None
    if res.get("ok") and final.get("ok", True):
        oid = res.get("order_id") or final.get("order_id")
        status = final.get("status") or res.get("status")
        out(f"{what}: done (order {oid}, {status}).")
        if status == "waiting_approval":
            out(f"  It waits for your approval: botsy service control approve --order {oid}")
        result = final.get("result")
        if isinstance(result, dict) and result:
            out("  " + json.dumps(result, sort_keys=True))
        if final.get("announced"):
            out("  Posted in the main Discord channel (with how to undo it).")
        if plan_res is not None and not plan_res.get("ok"):
            for r in plan_res.get("reasons") or []:
                out(f"  The plan did not start: {orders.describe(r)} ({r})")
        return 0
    out(f"{what}: not done.")
    for r in dict.fromkeys(reasons):
        out(f"  {orders.describe(r)} ({r})")
    return 2 if any(r in SETUP_REASONS for r in reasons) else 1


def run(a, settings: dict, out=print) -> int:
    act = a.action
    if act == "pause":
        return _report(orders.submit(_order("pause", {"reason_code": a.reason}), settings), "pause", out)
    if act == "resume":
        return _report(orders.submit(_order("resume", {}), settings), "resume", out)
    if act in ("lower-cap", "raise-cap"):
        kind = act.replace("-", "_")
        return _report(orders.submit(_order(kind, {"cap_usd": a.usd}), settings), act, out)
    if act == "resume-budget":
        return _report(orders.submit(_order("resume_after_budget_stop", {}), settings), act, out)
    if act == "approve":
        if a.order:
            if a.items or a.batch:
                out("approve: give either item ids or --order, not both.")
                return 2
            o = orders.load(a.order, settings)
            if o is not None and o.get("kind") in orders.PLAN_KINDS:
                out("approve: that is a weekly plan; use  botsy service plan approve")
                return 1
            return _report(orders.approve(a.order, settings, by="user"), "approve", out)
        if not a.items:
            out("approve: name at least one review item id (or --order ID).")
            return 2
        payload = {"item_ids": list(dict.fromkeys(a.items))}
        if a.batch:
            payload["batch"] = a.batch
        return _report(orders.submit(_order("review_approve", payload), settings), "approve", out)
    if act in ("reject", "hold"):
        kind = f"review_{act}"
        return _report(orders.submit(_order(kind, {"item_id": a.item, "reason_code": a.reason}), settings), act, out)
    if act == "cancel":
        return _report(orders.submit(_order("order_cancel", {"order_id": a.order_id}), settings), "cancel", out)
    if act == "undo":
        return _report(orders.undo(a.order_id, settings, by="user"), "undo", out)
    if act == "orders":
        rows = orders.list_orders(settings, week=a.week)
        if not rows:
            out("No orders" + (f" for {a.week}." if a.week else "."))
        for o in rows:
            who = (o.get("approved") or {}).get("by")
            out(f"{o.get('id')}  {o.get('week')}  {o.get('kind'):<24} by {o.get('created_by'):<6} "
                f"{o.get('status')}" + (f" (approved by {who})" if who else ""))
        return 0
    if act == "limits":
        from service import limits

        lim = limits.load(settings)
        if not lim.ok:
            out(f"The limits file is not in force ({lim.reason}): {lim.detail}")
        out(json.dumps(lim.to_dict(), indent=2, sort_keys=True, default=str))
        return 0 if lim.ok else 2
    if act == "capacity":
        from service import capacity

        out(json.dumps(capacity.check(settings), indent=2, sort_keys=True, default=str))
        return 0
    raise ValueError(f"unknown control action {act!r}")


__all__ = ["ACTIONS", "add_arguments", "run"]
