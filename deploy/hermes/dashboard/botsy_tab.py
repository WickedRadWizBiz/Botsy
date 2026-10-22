"""The Botsy tab of the Hermes dashboard: data shaping, the bind guard and the botsy-control link.

Pinned Hermes v0.21.7 (deploy/hermes/hermes-pin.env). Installed root-owned by install_hermes.sh as
/home/hermes/.hermes/plugins/botsy/dashboard/ next to manifest.json, plugin_api.py and dist/.
The dashboard (`hermes dashboard`, hermes-dashboard.service, the hermes user, 127.0.0.1:9119)
imports plugin_api.py, which loads this file. docs/OVERSEER.md "Botsy dashboard tab".

    overview(link=None) -> dict              every section of the tab, from botsy-read only
    thumbnail(item_id, link=None) -> dict    one of OUR OWN product images (view_item_images)
    act(name, body, link=None) -> dict       "pause" or "lower_cap" through botsy-act, nothing else
    bind_refusal(bound_host, auth_required, public_hosts, client_host) -> str | None
    argv_refusal(argv) -> str | None ; refuse_public_argv(argv)   (raises at import)

What the tab may do (user, 2026-10-21): VIEW everything Botsy shows Hermes, plus two actions,
Pause and Lower cap. There is no approve, resume, raise, plan, review or settings call here: the
only act tools this file can name are in ACTIONS, and act() refuses every other name before a
socket is opened.

Who it is: Botsy's act server records every order it takes as created_by "hermes"
(control/tools.py); it cannot tell the dashboard from Hermes, and the dashboard cannot prove it is
you either: bound to 127.0.0.1 it has no login of its own, only a per-start session token that it
writes into the page for anyone who can open 127.0.0.1:9119 on the server. So a Pause from this
tab is a Hermes pause (you or Hermes may lift it; D3). Pause and lower_cap are stop-only orders
(service/orders.py STOP_ONLY): always allowed, never limited, and they use none of Hermes's change
limit or the daily approval limit (D1). For a pause that only you can lift, use
`botsy service control pause` on the server (asking Hermes in Discord gives a Hermes pause too).
Lowering the cap is safe whoever asks.

Stdlib only (the dashboard's Python has no Botsy packages). Speaks MCP (JSON-RPC 2.0, one JSON
message per line, the initialize handshake) straight to Botsy's sockets in /run/botsy-control/,
the same ones botsy-control-connect links Hermes to; systemd starts a fresh botsy-control as the
botsy user for each connection, which checks everything again.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import math
import re
import socket
from collections import OrderedDict
from collections.abc import Callable, Iterable
from typing import Any

SOCKETS = {"read": "/run/botsy-control/read.sock", "act": "/run/botsy-control/act.sock"}
PROTOCOL = "2025-11-25"          # an initialize-handshake revision botsy-control's MCP SDK (2.3.0) accepts
CLIENT = {"name": "botsy-dashboard-tab", "version": "1.0.0"}
TIMEOUT_S = 30.0
MAX_LINE = 8_000_000             # view_item_images sends at most ~3 MB of JPEG, base64 inside JSON

# The read tools the tab uses (all botsy-read, Level 0) and the two act tools it may call.
READ_TOOLS = ("status", "spend", "plan_status", "review_list", "orders_recent", "capacity", "daily_log",
              "view_item_images")
ACTIONS = ("pause", "lower_cap")
PAUSE_REASONS = ("user_request", "incident", "spend", "quality", "other")
CAP_MIN, CAP_MAX = 1.0, 10000.0

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
ITEM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
# argparse accepts any unique prefix of a long option (allow_abbrev is on in Hermes v0.21.7):
# --ho and --hos are --host too ("--h" is ambiguous with --help and refused by argparse).
HOST_ARG_RE = re.compile(r"--ho(?:st?)?(?:=(.*))?$")
THUMB_CACHE = 64


class ControlError(Exception):
    """botsy-control could not be reached or answered something that is not MCP."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ------------------------------------------------------------------ the bind guard

def _loopback(host) -> bool:
    """A loopback IP literal (127.0.0.0/8, ::1, ::ffff:127.x) or the name "localhost". A host name
    such as "127.example.com" is not loopback: it can resolve anywhere."""
    if not isinstance(host, str):
        return False
    h = host.strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(ip.is_loopback or (mapped is not None and mapped.is_loopback))


def argv_refusal(argv: Iterable[str]) -> str | None:
    """Why the tab must not load for this `hermes dashboard` command line, or None. Hermes's own
    default is --host 127.0.0.1 (hermes_cli/subcommands/dashboard.py)."""
    args = [str(a) for a in argv]
    for i, a in enumerate(args):
        m = HOST_ARG_RE.match(a)
        if not m:
            continue
        host = m.group(1) if m.group(1) is not None else (args[i + 1] if i + 1 < len(args) else "")
        if not _loopback(host):
            return "public_bind"
    return None


def refuse_public_argv(argv: Iterable[str]) -> None:
    """Raise at import when the dashboard was started on a non-loopback address: Hermes then logs
    "Failed to load plugin botsy API routes" and mounts nothing of this tab."""
    if argv_refusal(argv):
        raise RuntimeError("the Botsy tab loads only on a dashboard bound to 127.0.0.1 (reach it through an SSH "
                           "tunnel; docs/OVERSEER.md 'Botsy dashboard tab')")


def bind_refusal(bound_host, auth_required, public_hosts, client_host) -> str | None:
    """Checked on every request from the dashboard's own state (hermes_cli/web_server.py:
    app.state.bound_host, auth_required, trusted_public_hosts) and the peer address."""
    if bound_host is None:
        return "bind_unknown"
    if not _loopback(bound_host):
        return "public_bind"
    if auth_required or (public_hosts and any(not _loopback(h) for h in public_hosts)):
        return "public_url"             # dashboard.public_url / a reverse proxy: reachable from outside
    if not _loopback(client_host):
        return "remote_client"
    return None


REFUSAL_TEXT = {
    "bind_unknown": "The dashboard did not say where it listens; the Botsy tab stays off.",
    "public_bind": "The dashboard listens on a public address. The Botsy tab works only on 127.0.0.1 through an "
                   "SSH tunnel: ssh -L 9119:127.0.0.1:9119 <server>.",
    "public_url": "The dashboard has a public URL or a login gate (reverse proxy). The Botsy tab works only on "
                  "127.0.0.1 through an SSH tunnel.",
    "remote_client": "This request did not come from the server itself. Use the SSH tunnel.",
    "control_unavailable": "botsy-control is not answering (botsy-control-read.socket / -act.socket).",
    "bad_answer": "botsy-control sent an answer the tab could not read.",
    "not_allowed": "The Botsy tab can only pause and lower the cap.",
    "bad_argument": "That value is not allowed.",
    "not_lower": "The new cap must be lower than the cap in force.",
    "missing_header": "Use the buttons on the Botsy tab.",
}


# ------------------------------------------------------------------ the MCP link

class Session:
    """One MCP session with botsy-read or botsy-act over a byte stream (a Unix socket by default).
    `connect` (tests) returns (reader, writer) binary file objects."""

    def __init__(self, server: str, *, sockets: dict | None = None, timeout: float = TIMEOUT_S,
                 connect: Callable[[], tuple[Any, Any]] | None = None):
        if server not in ("read", "act"):
            raise ValueError("server must be read or act")
        self.server = server
        self._sockets = sockets or SOCKETS
        self._timeout = timeout
        self._connect = connect
        self._sock = None
        self._r = self._w = None
        self._next = 0

    def __enter__(self) -> "Session":
        try:
            if self._connect is not None:
                self._r, self._w = self._connect()
            else:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._sock.settimeout(self._timeout)
                self._sock.connect(self._sockets[self.server])
                self._r = self._sock.makefile("rb")
                self._w = self._sock.makefile("wb")
            init = self._request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                "clientInfo": CLIENT})
            if not isinstance(init, dict) or not isinstance(init.get("protocolVersion"), str):
                raise ControlError("bad_answer")
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except ControlError:
            self.close()
            raise
        except (OSError, ValueError) as e:
            self.close()
            raise ControlError("control_unavailable") from e
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for f in (self._w, self._r, self._sock):
            try:
                if f is not None:
                    f.close()
            except OSError:
                pass
        self._sock = self._r = self._w = None

    def _send(self, msg: dict) -> None:
        self._w.write(json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n")
        self._w.flush()

    def _request(self, method: str, params: dict) -> Any:
        self._next += 1
        rid = self._next
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            line = self._r.readline(MAX_LINE + 1)
            if not line:
                raise ControlError("control_unavailable")
            if len(line) > MAX_LINE:
                raise ControlError("bad_answer")
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError as e:
                raise ControlError("bad_answer") from e
            if not isinstance(msg, dict):
                raise ControlError("bad_answer")
            if "method" in msg:                       # the server asks or tells us something
                if "id" in msg:                        # a request: ping gets {}, anything else "not here"
                    if msg.get("method") == "ping":
                        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                    else:
                        self._send({"jsonrpc": "2.0", "id": msg["id"],
                                    "error": {"code": -32601, "message": "not supported"}})
                continue
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                raise ControlError("bad_answer")
            return msg.get("result")

    def call(self, name: str, arguments: dict | None = None) -> tuple[bool, dict, list[tuple[str, bytes]]]:
        """(ok, data, images) of one tool call; a refusal is ok=False with Botsy's reason codes."""
        if self.server == "act" and name not in ACTIONS:
            raise ControlError("not_allowed")
        if self.server == "read" and name not in READ_TOOLS:
            raise ControlError("not_allowed")
        res = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        if not isinstance(res, dict):
            raise ControlError("bad_answer")
        data = res.get("structuredContent")
        images: list[tuple[str, bytes]] = []
        for c in res.get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and not isinstance(data, dict):
                try:
                    data = json.loads(c.get("text") or "")
                except ValueError:
                    data = None
            elif c.get("type") == "image" and c.get("mimeType") == "image/jpeg":
                try:
                    images.append(("image/jpeg", base64.b64decode(c.get("data") or "", validate=True)))
                except ValueError:
                    continue
        if not isinstance(data, dict):
            raise ControlError("bad_answer")
        return (not res.get("isError")) and data.get("ok") is not False, data, images


class Link:
    """Opens Sessions; tests pass their own (a fake, or pipes to a real botsy-control)."""

    def __init__(self, *, sockets: dict | None = None, connect: Callable[[str], tuple[Any, Any]] | None = None):
        self._sockets = sockets
        self._connect = connect

    def session(self, server: str) -> Session:
        conn = (lambda: self._connect(server)) if self._connect else None
        return Session(server, sockets=self._sockets, connect=conn)


# ------------------------------------------------------------------ shaping (pure)

def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _str(v, n: int = 80):
    return v[:n] if isinstance(v, str) else None


def _refused(data: dict | None) -> dict:
    data = data if isinstance(data, dict) else {}
    reasons = [r for r in data.get("reasons") or [] if isinstance(r, str)][:10]
    explain = data.get("explain") if isinstance(data.get("explain"), dict) else {}
    return {"ok": False, "reasons": reasons,
            "explain": [_str(explain.get(r), 300) for r in reasons if isinstance(explain.get(r), str)]}


def shape_status(status: dict, spend: dict | None = None) -> dict:
    """running | paused | budget_stop, who paused, the last check."""
    paused = status.get("paused") if isinstance(status.get("paused"), dict) else None
    stop = status.get("budget_stop") if isinstance(status.get("budget_stop"), dict) else None
    if stop is None and isinstance(spend, dict) and spend.get("budget_stop") is True:
        stop = {"month": spend.get("month"), "since": None, "reason_code": None}
    state = "budget_stop" if stop else "paused" if paused else "running"
    last = status.get("last_check") if isinstance(status.get("last_check"), dict) else None
    last_view = None
    if last:
        last_view = {k: last.get(k) for k in ("started", "finished", "exit_code", "errors", "failed", "items",
                                               "generated", "drafts", "awaiting_review", "dry_run", "research_ran")}
    week = status.get("week") if isinstance(status.get("week"), dict) else {}
    used = status.get("hermes_limits_used") if isinstance(status.get("hermes_limits_used"), dict) else {}
    lim = status.get("hermes_limits") if isinstance(status.get("hermes_limits"), dict) else {}
    return {"ok": True, "state": state, "live": bool(status.get("live")),
            "paused": {"by": _str(paused.get("by")), "since": _str(paused.get("since")),
                       "reason_code": _str(paused.get("reason_code")), "order_id": _str(paused.get("order_id"))}
            if paused else None,
            "budget_stop": {"month": _str(stop.get("month")), "since": _str(stop.get("since")),
                            "reason_code": _str(stop.get("reason_code"))} if stop else None,
            "last_checked": _str(status.get("last_checked")), "status_updated": _str(status.get("status_updated")),
            "last_check": last_view,
            "week": {k: _num(v) for k, v in week.items() if isinstance(k, str)},
            "approvals_today": {"used": _num(used.get("level3_last_24h")), "limit": _num(lim.get("level3_per_day"))},
            "changes_last_hour": {"used": _num(used.get("changes_last_hour")),
                                  "limit": _num(lim.get("changes_per_hour"))},
            "fixes_today": _num(used.get("fixes_today")),
            "limits_file_ok": status.get("limits_file_ok") is True,
            "control_state_ok": status.get("control_state_ok") is not False}


def shape_money(spend: dict) -> dict:
    spent, cap, warn = _num(spend.get("spent")), _num(spend.get("cap")), _num(spend.get("warn_line"))
    return {"ok": True, "month": _str(spend.get("month")), "spent": spent, "cap": cap, "warn_line": warn,
            "money_left": _num(spend.get("money_left")), "share_used": _num(spend.get("share_used")),
            "cap_source": _str(spend.get("cap_source")), "cap_reached": spend.get("cap_reached") is True,
            "warn_reached": spent is not None and warn is not None and spent >= warn,
            "budget_stop": spend.get("budget_stop") is True, "currency": _str(spend.get("currency")) or "USD"}


def _plan_week(w: dict, state: dict) -> dict:
    week = _str(w.get("plan_week"))
    plan = w.get("plan") if isinstance(w.get("plan"), dict) else None
    entry = ((state.get("plans") or {}).get(week) if isinstance(state.get("plans"), dict) else None) or {}
    entry = entry if isinstance(entry, dict) else {}
    prog = entry.get("progress") if isinstance(entry.get("progress"), dict) else {}
    lines = plan.get("lines") if plan and isinstance(plan.get("lines"), list) else []
    kdp = plan.get("kdp") if plan and isinstance(plan.get("kdp"), list) else []
    held = bool(plan and plan.get("held")) or entry.get("status") == "held"
    held_by = entry.get("held_by") if entry.get("held_by") in ("user", "hermes", "botsy") else None
    if plan and plan.get("held_by") in ("user", "hermes", "botsy"):
        held_by = plan["held_by"]
    lines_total = _num(prog.get("lines_total"))
    return {"plan_week": week, "has_plan": plan is not None,
            "order_id": _str(plan.get("order_id")) if plan else None,
            "status": _str(entry.get("status")) or (_str(plan.get("status")) if plan else "none"),
            "created_by": _str(plan.get("created_by")) if plan else None,
            "lines_total": lines_total if lines_total is not None else len(lines),
            "lines_done": _num(prog.get("lines_done")) or 0,
            "packs": _num(plan.get("packs")) if plan else 0, "packs_made": _num(prog.get("packs_made")),
            "kdp_kits": len(kdp), "niches": sorted({x.get("niche") for x in lines
                                                    if isinstance(x, dict) and isinstance(x.get("niche"), str)}),
            "cost_usd": _num(plan.get("cost_usd")) if plan else None,
            "needs_approval": bool(plan and plan.get("needs_approval")),
            "approval_reason": _str(plan.get("approval_reason")) if plan else None,
            "held": held, "held_by": held_by if held else None,
            "start_at_local": _str(plan.get("start_at_local")) if plan else None,
            "versions": _num(w.get("versions")) or 0}


def shape_plan(plan_status: dict) -> dict:
    state = plan_status.get("state") if isinstance(plan_status.get("state"), dict) else {}
    weeks = [_plan_week(w, state) for w in plan_status.get("weeks") or [] if isinstance(w, dict)]
    return {"ok": True, "this_week": _str(plan_status.get("this_week")),
            "next_week": _str(plan_status.get("next_week")), "weeks": weeks,
            "mode": _str(state.get("mode")), "approved_count": _num(state.get("approved_count"))}


def shape_review(review: dict) -> dict:
    items = []
    for it in review.get("items") or []:
        if not isinstance(it, dict) or not isinstance(it.get("item_id"), str) or not ITEM_RE.fullmatch(it["item_id"]):
            continue
        items.append({"item_id": it["item_id"], "niche": _str(it.get("niche")), "theme": _str(it.get("theme")),
                      "status": _str(it.get("status")), "queued": _str(it.get("queued")),
                      "reasons": [r for r in it.get("reasons") or [] if isinstance(r, str)][:10],
                      "previews": _num(it.get("previews")) or 0, "qa_passed": it.get("qa_passed") is True,
                      "drafted": it.get("drafted") is True, "approvable": it.get("approvable") is True})
    return {"ok": True, "count": _num(review.get("count")) or len(items), "more": _num(review.get("more")) or 0,
            "items": items}


def _order_summary(o: dict) -> str:
    p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
    kind = o.get("kind")
    if kind in ("lower_cap", "raise_cap") and _num(p.get("cap_usd")) is not None:
        return f"cap ${p['cap_usd']:.2f}"
    if kind == "pause" and isinstance(p.get("reason_code"), str):
        return p["reason_code"]
    if kind == "review_approve":
        n = len(p.get("item_ids") or [])
        return f"{n} item{'s' if n != 1 else ''}"
    if kind in ("review_reject", "review_hold", "fix_rerun_qa") and isinstance(p.get("item_id"), str):
        return p["item_id"]
    if isinstance(p.get("plan_week"), str):
        return p["plan_week"]
    return ""


def shape_orders(recent: dict) -> dict:
    orders, fixes = [], []
    for o in recent.get("orders") or []:
        if not isinstance(o, dict) or not isinstance(o.get("kind"), str):
            continue
        row = {"order_id": _str(o.get("order_id")), "kind": o["kind"][:40], "level": _num(o.get("level")),
               "created_by": _str(o.get("created_by")), "created_at": _str(o.get("created_at")),
               "status": _str(o.get("status")), "needs_approval": o.get("needs_approval") is True,
               "applied_at": _str(o.get("applied_at")), "summary": _order_summary(o)}
        (fixes if row["kind"].startswith("fix_") else orders).append(row)
    return {"ok": True, "orders": orders, "fixes": fixes, "more": _num(recent.get("more")) or 0}


def shape_kdp(capacity: dict, plan: dict | None) -> dict:
    cap = capacity.get("capacity") if isinstance(capacity.get("capacity"), dict) else {}
    facts = cap.get("facts") if isinstance(cap.get("facts"), dict) else {}
    kits = {w["plan_week"]: w["kdp_kits"] for w in (plan or {}).get("weeks") or [] if isinstance(w, dict)}
    return {"ok": True, "slots_left": _num(facts.get("kdp_slots_left")), "kits_planned": kits,
            "room": cap.get("ok") is True, "room_reasons": [r for r in cap.get("reasons") or []
                                                            if isinstance(r, str)][:10],
            "review_queue": _num(facts.get("review_queue")), "waiting_drafts": _num(facts.get("waiting_drafts")),
            "checked_at": _str(cap.get("checked_at"))}


def shape_daily_log(log: dict) -> dict:
    text = log.get("text") if isinstance(log.get("text"), str) else ""
    return {"ok": True, "date": _str(log.get("date")), "text": text[:60000], "cut": log.get("cut") is True}


_SHAPERS = {"review_list": shape_review, "orders_recent": shape_orders, "daily_log": shape_daily_log,
            "plan_status": shape_plan, "spend": shape_money}

# (section, tool, arguments) in the order the tab asks for them.
OVERVIEW_CALLS = (("status", "status", {}), ("money", "spend", {}), ("plan", "plan_status", {}),
                  ("review", "review_list", {"status": "pending", "limit": 50}),
                  ("orders", "orders_recent", {"days": 14, "limit": 40}), ("capacity", "capacity", {}),
                  ("daily_log", "daily_log", {}))


def overview(link: Link | None = None) -> dict:
    """Every section, from one botsy-read session. A refused tool gives that section ok=False."""
    link = link or Link()
    raw: dict[str, tuple[bool, dict]] = {}
    try:
        with link.session("read") as s:
            for section, tool, args in OVERVIEW_CALLS:
                ok, data, _ = s.call(tool, args)
                raw[section] = (ok, data)
    except ControlError as e:
        return {"ok": False, "error": e.code, "explain": REFUSAL_TEXT.get(e.code, "")}
    out: dict[str, Any] = {"ok": True}
    for section, tool, _ in OVERVIEW_CALLS:
        ok, data = raw[section]
        if section in ("status", "capacity"):
            continue
        out[section] = _SHAPERS[tool](data) if ok else _refused(data)
    st_ok, st = raw["status"]
    money_ok, money = raw["money"]
    out["status"] = shape_status(st, money if money_ok else None) if st_ok else _refused(st)
    cap_ok, cap = raw["capacity"]
    out["kdp"] = shape_kdp(cap, out["plan"] if out["plan"].get("ok") else None) if cap_ok else _refused(cap)
    out["actions"] = list(ACTIONS)
    out["pause_reasons"] = list(PAUSE_REASONS)
    return out


_thumbs: "OrderedDict[str, dict]" = OrderedDict()


def thumbnail(item_id, link: Link | None = None, *, cache: bool = True) -> dict:
    """The first mockup of one of our own items (else its first page) as a data: URL."""
    if not isinstance(item_id, str) or not ITEM_RE.fullmatch(item_id):
        return {"ok": False, "error": "bad_argument", "explain": REFUSAL_TEXT["bad_argument"]}
    if cache and item_id in _thumbs:
        _thumbs.move_to_end(item_id)
        return _thumbs[item_id]
    link = link or Link()
    try:
        with link.session("read") as s:
            for kind in ("mockups", "pages"):
                ok, data, images = s.call("view_item_images", {"item_id": item_id, "kind": kind, "max": 1})
                if ok and images:
                    mime, blob = images[0]
                    out = {"ok": True, "item_id": item_id, "kind": kind, "available": _num(data.get("available")),
                           "data_url": f"data:{mime};base64," + base64.b64encode(blob).decode("ascii")}
                    if cache:
                        _thumbs[item_id] = out
                        while len(_thumbs) > THUMB_CACHE:
                            _thumbs.popitem(last=False)
                    return out
            return {"ok": False, "item_id": item_id, **_refused(data)}
    except ControlError as e:
        return {"ok": False, "error": e.code, "explain": REFUSAL_TEXT.get(e.code, "")}


def _cap_value(body) -> float | None:
    v = body.get("cap_usd") if isinstance(body, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    v = round(float(v), 2)
    return v if CAP_MIN <= v <= CAP_MAX else None


def act(name, body=None, link: Link | None = None) -> dict:
    """Pause or Lower cap, nothing else. Botsy checks the order again in full (service/orders.py)
    and records it as created_by "hermes" (see the module text for why)."""
    if name not in ACTIONS:
        return {"ok": False, "error": "not_allowed", "explain": REFUSAL_TEXT["not_allowed"]}
    body = body if isinstance(body, dict) else {}
    if set(body) - ({"reason_code"} if name == "pause" else {"cap_usd"}):
        return {"ok": False, "error": "bad_argument", "explain": REFUSAL_TEXT["bad_argument"]}
    link = link or Link()
    try:
        if name == "pause":
            reason = body.get("reason_code", "user_request")
            if reason not in PAUSE_REASONS:
                return {"ok": False, "error": "bad_argument", "explain": REFUSAL_TEXT["bad_argument"]}
            args = {"reason_code": reason}
        else:
            cap = _cap_value(body)
            if cap is None:
                return {"ok": False, "error": "bad_argument", "explain": REFUSAL_TEXT["bad_argument"]}
            with link.session("read") as s:            # the cap in force, so a raise never leaves this tab
                ok, spend, _ = s.call("spend", {})
            now_cap = _num(spend.get("cap")) if ok else None
            if now_cap is None:
                return {"ok": False, **_refused(spend)}
            if cap >= now_cap:
                return {"ok": False, "error": "not_lower", "explain": REFUSAL_TEXT["not_lower"], "cap": now_cap}
            args = {"cap_usd": cap}
        with link.session("act") as s:
            ok, data, _ = s.call(name, args)
    except ControlError as e:
        return {"ok": False, "error": e.code, "explain": REFUSAL_TEXT.get(e.code, "")}
    if not ok:
        return {"action": name, **_refused(data)}
    return {"ok": True, "action": name, "order_id": _str(data.get("order_id")), "status": _str(data.get("status")),
            "created_by": "hermes"}


__all__ = ["ACTIONS", "ControlError", "Link", "OVERVIEW_CALLS", "PAUSE_REASONS", "READ_TOOLS", "REFUSAL_TEXT",
           "SOCKETS", "Session", "act", "argv_refusal", "bind_refusal", "overview", "refuse_public_argv",
           "shape_daily_log", "shape_kdp", "shape_money", "shape_orders", "shape_plan", "shape_review",
           "shape_status", "thumbnail"]
