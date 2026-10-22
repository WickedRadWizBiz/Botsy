"""The Botsy tab of the Hermes dashboard (deploy/hermes/dashboard/, docs/OVERSEER.md "Botsy dashboard tab").

Offline: botsy-control runs in-process (control.tools.call) or as a child process over pipes, the
same MCP stream the tab speaks to /run/botsy-control/*.sock. FastAPI is not in Botsy's venv, so
plugin_api.py is imported against a small stand-in that records its routes.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

from control import tools
from core.config import ROOT
from tests.control._support import ITEM_A, ITEM_B, NOW, ctx, item

DASH = ROOT / "deploy" / "hermes" / "dashboard"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


tab = _load("botsy_dashboard_tab_test", DASH / "botsy_tab.py")


@pytest.fixture
def s(tmp_path):
    return ctx(tmp_path)


def _jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (200, 120, 40)).save(buf, "JPEG")
    return buf.getvalue()


class _InProcess:
    """A Session stand-in that runs control.tools.call in-process and records every call."""

    def __init__(self, link, server):
        self.link, self.server = link, server

    def __enter__(self):
        self.link.opened.append(self.server)
        return self

    def __exit__(self, *exc):
        return None

    def call(self, name, arguments=None):
        allowed = tab.ACTIONS if self.server == "act" else tab.READ_TOOLS
        if name not in allowed:
            raise tab.ControlError("not_allowed")
        self.link.calls.append((self.server, name, dict(arguments or {})))
        out = tools.call(self.server, name, arguments or {}, settings=self.link.settings, now=NOW)
        return out.ok, out.data, list(out.images)


class FakeLink:
    def __init__(self, settings):
        self.settings, self.calls, self.opened = settings, [], []

    def session(self, server):
        return _InProcess(self, server)


class _Pipes:
    """A Link whose sessions talk MCP to a real botsy-control child process (like systemd's
    Accept=yes socket units start one per connection)."""

    def __init__(self, settings, tmp_path):
        path = tmp_path / "control-settings.yaml"
        path.write_text(yaml.safe_dump(settings), encoding="utf-8")
        self.path, self.procs = path, []

    def connect(self, server):
        code = ("import sys, yaml; sys.path.insert(0, %r); import control; "
                "control.run(%r, settings=yaml.safe_load(open(%r, encoding='utf-8')))"
                % (str(ROOT), server, str(self.path)))
        p = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=dict(os.environ))
        self.procs.append(p)
        return p.stdout, p.stdin

    def close(self):
        for p in self.procs:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()


# ------------------------------------------------------------------ only pause and lower_cap

def test_the_only_actions_are_pause_and_lower_cap():
    assert tab.ACTIONS == ("pause", "lower_cap")
    assert set(tab.READ_TOOLS) <= {t.name for t in tools.tools_for("read")}
    assert set(tab.ACTIONS) <= {t.name for t in tools.tools_for("act")}


@pytest.mark.parametrize("name", ["approve_order", "resume", "submit_order", "undo_order", "search_ideas",
                                  "raise_cap", "fix_retry_run", "status", "", None, "PAUSE"])
def test_act_refuses_every_other_name_before_connecting(s, name):
    link = FakeLink(s)
    out = tab.act(name, {}, link=link)
    assert out == {"ok": False, "error": "not_allowed", "explain": tab.REFUSAL_TEXT["not_allowed"]}
    assert link.opened == [] and link.calls == []


def test_a_session_refuses_tools_outside_its_list():
    sess = tab.Session("act", connect=lambda: (io.BytesIO(), io.BytesIO()))
    for name in ("approve_order", "resume", "submit_order", "status"):
        with pytest.raises(tab.ControlError) as e:
            sess.call(name, {})
        assert e.value.code == "not_allowed"
    read = tab.Session("read", connect=lambda: (io.BytesIO(), io.BytesIO()))
    with pytest.raises(tab.ControlError):
        read.call("trend_search", {"query": "frogs"})
    with pytest.raises(ValueError):
        tab.Session("write")


def test_overview_reads_only_botsy_read(s):
    item(s, ITEM_A)
    link = FakeLink(s)
    out = tab.overview(link=link)
    assert out["ok"] and link.opened == ["read"]
    assert {c[0] for c in link.calls} == {"read"}
    assert [c[1] for c in link.calls] == [t for _, t, _ in tab.OVERVIEW_CALLS]
    assert out["actions"] == ["pause", "lower_cap"]


# ------------------------------------------------------------------ data shaping

def test_overview_sections_from_real_tool_output(s):
    item(s, ITEM_A)
    item(s, ITEM_B, reasons=["qa: blank page"])
    out_dir = Path(s["service"]["inbox_dir"]) / "outbox"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "daily-log.txt").write_text("Botsy daily log\nAll good.\n", encoding="utf-8")
    tools.call("act", "lower_cap", {"cap_usd": 20}, settings=s, now=NOW)
    tools.call("act", "pause", {"reason_code": "quality"}, settings=s, now=NOW)

    out = tab.overview(link=FakeLink(s))
    st = out["status"]
    assert st["ok"] and st["state"] == "paused" and st["paused"]["by"] == "hermes"
    assert st["paused"]["reason_code"] == "quality" and st["budget_stop"] is None
    assert st["approvals_today"]["limit"] == 10 and st["limits_file_ok"] is True
    m = out["money"]
    assert m["cap"] == 20.0 and m["spent"] == 0.0 and m["warn_line"] is not None and m["month"] == "2026-10"
    assert m["warn_reached"] is False and m["cap_reached"] is False
    r = out["review"]
    assert r["count"] == 2 and {i["item_id"] for i in r["items"]} == {ITEM_A, ITEM_B}
    a = next(i for i in r["items"] if i["item_id"] == ITEM_A)
    assert a["niche"] == "coloring_pages" and a["reasons"] == ["trademark"] and a["approvable"] is True
    kinds = [o["kind"] for o in out["orders"]["orders"]]
    assert kinds == ["pause", "lower_cap"] and out["orders"]["fixes"] == []
    lc = out["orders"]["orders"][1]
    assert lc["summary"] == "cap $20.00" and lc["created_by"] == "hermes" and lc["status"] == "applied"
    assert out["daily_log"]["ok"] and "All good." in out["daily_log"]["text"]
    assert out["plan"]["ok"] and [w["plan_week"] for w in out["plan"]["weeks"]] == ["2026-W43", "2026-W44"]
    assert all(not w["has_plan"] and w["held_by"] is None for w in out["plan"]["weeks"])
    assert out["kdp"]["ok"] and "slots_left" in out["kdp"] and out["kdp"]["review_queue"] == 2
    json.dumps(out)


def test_a_refused_section_stays_a_refusal(s):
    out = tab.overview(link=FakeLink(s))           # no daily log written yet
    assert out["ok"] and out["daily_log"]["ok"] is False and out["daily_log"]["reasons"] == ["not_found"]
    assert out["daily_log"]["explain"]


def test_status_shapes_running_paused_and_budget_stop():
    base = {"ok": True, "live": True, "last_checked": "2026-10-21T14:00:00+00:00", "paused": None,
            "budget_stop": None, "last_check": {"finished": "2026-10-21T14:00:00+00:00", "items": 3, "drafts": 2,
                                                "errors": 0, "exit_code": 0, "secret": "x"},
            "hermes_limits_used": {"level3_last_24h": 2}, "hermes_limits": {"level3_per_day": 10}}
    run = tab.shape_status(base)
    assert run["state"] == "running" and run["live"] and run["last_check"]["items"] == 3
    assert "secret" not in run["last_check"] and run["approvals_today"] == {"used": 2, "limit": 10}
    paused = tab.shape_status({**base, "paused": {"by": "user", "since": "2026-10-21T10:00:00+00:00",
                                                  "reason_code": "user_request", "order_id": "pa-20261021-0001"}})
    assert paused["state"] == "paused" and paused["paused"]["by"] == "user"
    stop = tab.shape_status({**base, "paused": {"by": "user"}, "budget_stop": {"month": "2026-10"}})
    assert stop["state"] == "budget_stop" and stop["budget_stop"]["month"] == "2026-10"
    from_spend = tab.shape_status(base, {"budget_stop": True, "month": "2026-10"})
    assert from_spend["state"] == "budget_stop"


def test_money_warn_line():
    m = tab.shape_money({"month": "2026-10", "spent": 21.5, "cap": 25.0, "warn_line": 20.0, "money_left": 3.5,
                         "cap_reached": False, "spent_secret": 1})
    assert m["warn_reached"] is True and m["spent"] == 21.5 and m["money_left"] == 3.5
    assert tab.shape_money({"spent": float("nan"), "cap": True})["spent"] is None
    assert tab.shape_money({"cap": True})["cap"] is None


def test_plan_lines_made_and_who_holds_it():
    plan = {"order_id": "wp-20261018-0001", "status": "applied", "created_by": "botsy", "held": True,
            "lines": [{"niche": "coloring_pages", "sizes": [5, 20]}, {"niche": "stickers", "sizes": [5]},
                      {"niche": "stickers", "sizes": [12]}],
            "kdp": [{"brief_id": "x", "template": "y"}], "packs": 4, "cost_usd": 6.4, "needs_approval": False,
            "start_at_local": "2026-10-19T08:00:00-04:00"}
    ps = {"this_week": "2026-W43", "next_week": "2026-W44",
          "weeks": [{"plan_week": "2026-W43", "plan": plan, "versions": 2},
                    {"plan_week": "2026-W44", "plan": None, "versions": 0}],
          "state": {"mode": "approve", "approved_count": 3,
                    "plans": {"2026-W43": {"status": "held", "held_by": "user",
                                           "progress": {"lines_total": 3, "lines_done": 1, "packs_made": 2}}}}}
    out = tab.shape_plan(ps)
    w = out["weeks"][0]
    assert w["lines_done"] == 1 and w["lines_total"] == 3 and w["packs"] == 4 and w["packs_made"] == 2
    assert w["held"] and w["held_by"] == "user" and w["status"] == "held" and w["kdp_kits"] == 1
    assert w["niches"] == ["coloring_pages", "stickers"] and w["cost_usd"] == 6.4
    assert out["weeks"][1]["has_plan"] is False and out["weeks"][1]["status"] == "none"
    assert out["mode"] == "approve" and out["approved_count"] == 3
    # A Hermes hold, and a held_by on the plan view itself (when botsy-read adds one) wins.
    ps["state"]["plans"]["2026-W43"]["held_by"] = "hermes"
    assert tab.shape_plan(ps)["weeks"][0]["held_by"] == "hermes"
    plan["held_by"] = "user"
    assert tab.shape_plan(ps)["weeks"][0]["held_by"] == "user"
    # Not held: no holder shown; no progress yet: the plan's own line count.
    plan.update(held=False, held_by=None)
    ps["state"] = {}
    w = tab.shape_plan(ps)["weeks"][0]
    assert w["held"] is False and w["held_by"] is None and w["lines_total"] == 3 and w["lines_done"] == 0


def test_orders_and_fixes_are_split():
    rows = [{"order_id": "fx-20261021-0001", "kind": "fix_rerun_qa", "level": 2, "created_by": "hermes",
             "status": "applied", "payload": {"item_id": ITEM_A}},
            {"order_id": "ra-20261021-0001", "kind": "review_approve", "level": 3, "created_by": "user",
             "status": "applied", "payload": {"item_ids": [ITEM_A, ITEM_B]}},
            {"order_id": "wp-20261021-0001", "kind": "weekly_plan", "payload": {"plan_week": "2026-W44"}},
            {"kind": None}, "junk"]
    out = tab.shape_orders({"orders": rows, "more": 3})
    assert [o["kind"] for o in out["fixes"]] == ["fix_rerun_qa"] and out["fixes"][0]["summary"] == ITEM_A
    assert [o["summary"] for o in out["orders"]] == ["2 items", "2026-W44"] and out["more"] == 3


def test_review_drops_bad_ids():
    out = tab.shape_review({"count": 2, "items": [{"item_id": "../etc/passwd"}, {"item_id": ITEM_A, "reasons": ["qa"]}]})
    assert [i["item_id"] for i in out["items"]] == [ITEM_A]


def test_kdp_slots():
    cap = {"capacity": {"ok": False, "reasons": ["review_queue_full"],
                        "facts": {"kdp_slots_left": 2, "review_queue": 31, "waiting_drafts": 4}}}
    plan = {"weeks": [{"plan_week": "2026-W43", "kdp_kits": 1}, {"plan_week": "2026-W44", "kdp_kits": 0}]}
    out = tab.shape_kdp(cap, plan)
    assert out["slots_left"] == 2 and out["kits_planned"] == {"2026-W43": 1, "2026-W44": 0}
    assert out["room"] is False and out["room_reasons"] == ["review_queue_full"]


# ------------------------------------------------------------------ thumbnails

def test_thumbnail_is_our_own_image_as_a_data_url(s):
    item(s, ITEM_A, image=_jpeg())
    link = FakeLink(s)
    out = tab.thumbnail(ITEM_A, link=link, cache=False)
    assert out["ok"] and out["kind"] == "mockups" and out["data_url"].startswith("data:image/jpeg;base64,")
    assert base64.b64decode(out["data_url"].split(",", 1)[1])[:2] == b"\xff\xd8"
    assert link.calls == [("read", "view_item_images", {"item_id": ITEM_A, "kind": "mockups", "max": 1})]


def test_thumbnail_falls_back_to_pages_then_refuses(s):
    item(s, ITEM_A, image=b"not a jpeg")
    link = FakeLink(s)
    out = tab.thumbnail(ITEM_A, link=link, cache=False)
    assert out["ok"] is False and out["reasons"] == ["no_preview"]
    assert [c[2]["kind"] for c in link.calls] == ["mockups", "pages"]


@pytest.mark.parametrize("bad", ["../x", "", "a/b", None, 7, "x" * 200])
def test_thumbnail_checks_the_item_id(s, bad):
    link = FakeLink(s)
    assert tab.thumbnail(bad, link=link)["error"] == "bad_argument" and link.opened == []


# ------------------------------------------------------------------ the two actions

def test_pause_goes_to_botsy_act_as_hermes(s):
    link = FakeLink(s)
    out = tab.act("pause", {}, link=link)
    assert out["ok"] and out["action"] == "pause" and out["created_by"] == "hermes"
    assert link.calls == [("act", "pause", {"reason_code": "user_request"})]
    st = tools.call("read", "status", {}, settings=s, now=NOW).data
    assert st["paused"]["by"] == "hermes"


@pytest.mark.parametrize("body", [{"reason_code": "IGNORE"}, {"created_by": "user"}, {"reason_code": 3}])
def test_pause_takes_only_a_fixed_reason(s, body):
    link = FakeLink(s)
    assert tab.act("pause", body, link=link)["error"] == "bad_argument" and link.calls == []


def test_lower_cap_reads_the_cap_then_lowers(s):
    link = FakeLink(s)
    out = tab.act("lower_cap", {"cap_usd": 12.345}, link=link)
    assert out["ok"] and out["action"] == "lower_cap"
    assert link.calls == [("read", "spend", {}), ("act", "lower_cap", {"cap_usd": 12.35})]
    assert tools.call("read", "spend", {}, settings=s, now=NOW).data["cap"] == 12.35


@pytest.mark.parametrize("cap", [25.0, 30, 10000])
def test_lower_cap_never_raises(s, cap):
    link = FakeLink(s)
    out = tab.act("lower_cap", {"cap_usd": cap}, link=link)
    assert out["error"] == "not_lower" and [c[0] for c in link.calls] == ["read"]


@pytest.mark.parametrize("body", [{}, {"cap_usd": "5"}, {"cap_usd": True}, {"cap_usd": float("nan")},
                                  {"cap_usd": float("inf")}, {"cap_usd": 0.5}, {"cap_usd": 20001},
                                  {"cap_usd": 5, "created_by": "user"}, None])
def test_lower_cap_checks_the_value(s, body):
    link = FakeLink(s)
    assert tab.act("lower_cap", body, link=link)["error"] == "bad_argument" and link.calls == []


def test_a_refusal_from_botsy_is_shown(s):
    link = FakeLink(s)
    tools.call("act", "pause", {}, settings=s, now=NOW)
    # A second pause is refused or answered as a duplicate by Botsy; either way the tab reports it.
    out = tab.act("pause", {}, link=link)
    assert "ok" in out and out.get("action") == "pause"


# ------------------------------------------------------------------ the MCP link, for real

def test_round_trip_with_a_real_botsy_control(s, tmp_path):
    item(s, ITEM_A, image=_jpeg())
    pipes = _Pipes(s, tmp_path)
    try:
        link = tab.Link(connect=pipes.connect)
        out = tab.overview(link=link)
        assert out["ok"] and out["money"]["cap"] == 25.0 and out["review"]["items"][0]["item_id"] == ITEM_A
        thumb = tab.thumbnail(ITEM_A, link=link, cache=False)
        assert thumb["ok"] and thumb["data_url"].startswith("data:image/jpeg;base64,")
        assert tab.act("lower_cap", {"cap_usd": 15}, link=link)["ok"]
        res = tab.act("pause", {"reason_code": "spend"}, link=link)
        assert res["ok"] and res["order_id"].startswith("pa-")
    finally:
        pipes.close()
    st = tools.call("read", "status", {}, settings=s).data
    assert st["paused"]["by"] == "hermes" and tools.call("read", "spend", {}, settings=s).data["cap"] == 15.0


def test_no_socket_means_control_unavailable(tmp_path):
    link = tab.Link(sockets={"read": str(tmp_path / "missing.sock"), "act": str(tmp_path / "missing2.sock")})
    assert tab.overview(link=link)["error"] == "control_unavailable"
    assert tab.act("pause", {}, link=link)["error"] == "control_unavailable"


def test_garbage_from_the_server_is_bad_answer():
    def connect():
        return io.BytesIO(b"this is not json\n"), io.BytesIO()
    with pytest.raises(tab.ControlError) as e:
        tab.Session("read", connect=connect).__enter__()
    assert e.value.code == "bad_answer"


def test_server_requests_are_answered_and_skipped():
    init = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": tab.PROTOCOL, "capabilities": {}}}
    ping = {"jsonrpc": "2.0", "id": 99, "method": "ping"}
    sampling = {"jsonrpc": "2.0", "id": 100, "method": "sampling/createMessage", "params": {}}
    note = {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}
    res = {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "{}"}],
                                                 "structuredContent": {"ok": True, "cap": 25.0}}}
    reader = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in (init, ping, sampling, note, res)))
    writer = io.BytesIO()
    with tab.Session("read", connect=lambda: (reader, writer)) as sess:
        writer.close = lambda: None              # keep the bytes for the check below
        ok, data, images = sess.call("spend", {})
        sent = [json.loads(x) for x in writer.getvalue().splitlines()]
    assert ok and data["cap"] == 25.0 and images == []
    assert sent[0]["method"] == "initialize" and sent[1]["method"] == "notifications/initialized"
    assert {"jsonrpc": "2.0", "id": 99, "result": {}} in sent
    assert any(m.get("id") == 100 and m.get("error", {}).get("code") == -32601 for m in sent)
    calls = [m for m in sent if m.get("method") == "tools/call"]
    assert calls == [{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "spend", "arguments": {}}}]


# ------------------------------------------------------------------ the bind guard

@pytest.mark.parametrize("argv,refused", [
    (["hermes", "dashboard"], False),
    (["hermes", "dashboard", "--host", "127.0.0.1", "--port", "9119"], False),
    (["hermes", "dashboard", "--host=::1"], False),
    (["hermes", "dashboard", "--host", "0.0.0.0"], True),
    (["hermes", "dashboard", "--host=192.168.1.5"], True),
    (["hermes", "dashboard", "--host", "::"], True),
    (["hermes", "dashboard", "--host"], True),
    (["hermes", "serve", "--host", "10.0.0.2"], True),
    # argparse takes unique prefixes of --host (allow_abbrev is on in v0.21.7)
    (["hermes", "dashboard", "--hos", "0.0.0.0"], True),
    (["hermes", "dashboard", "--ho=0.0.0.0"], True),
    (["hermes", "dashboard", "--hos", "127.0.0.1"], False),
    # a host NAME that only looks like 127.x can resolve anywhere
    (["hermes", "dashboard", "--host", "127.example.com"], True),
    (["hermes", "dashboard", "--host", "127.0.0.2"], False),
    (["hermes", "dashboard", "--host", "::ffff:127.0.0.1"], False),
    (["hermes", "dashboard", "--host", "::ffff:10.0.0.1"], True),
    (["hermes", "dashboard", "--hostname-ish", "0.0.0.0"], False),
])
def test_argv_guard(argv, refused):
    assert (tab.argv_refusal(argv) == "public_bind") is refused
    if refused:
        with pytest.raises(RuntimeError):
            tab.refuse_public_argv(argv)
    else:
        tab.refuse_public_argv(argv)


@pytest.mark.parametrize("bound,auth,public,client,reason", [
    ("127.0.0.1", False, frozenset(), "127.0.0.1", None),
    ("localhost", False, frozenset(), "::1", None),
    (None, False, frozenset(), "127.0.0.1", "bind_unknown"),
    ("0.0.0.0", True, frozenset(), "127.0.0.1", "public_bind"),
    ("10.1.2.3", True, frozenset(), "127.0.0.1", "public_bind"),
    ("127.0.0.1", True, frozenset(), "127.0.0.1", "public_url"),
    ("127.0.0.1", False, frozenset({"botsy.example.com"}), "127.0.0.1", "public_url"),
    ("127.0.0.1", False, frozenset(), "203.0.113.9", "remote_client"),
    ("127.0.0.1", False, frozenset(), None, "remote_client"),
    ("127.evil.example", False, frozenset(), "127.0.0.1", "public_bind"),
    ("127.0.0.1", False, frozenset(), "127.evil.example", "remote_client"),
    ("127.0.0.1", False, frozenset({"localhost"}), "127.0.0.1", None),
])
def test_request_guard(bound, auth, public, client, reason):
    assert tab.bind_refusal(bound, auth, public, client) == reason


# ------------------------------------------------------------------ plugin_api.py and the files

class _Router:
    def __init__(self):
        self.routes = []

    def _add(self, method, path):
        def deco(fn):
            self.routes.append((method, path, fn))
            return fn
        return deco

    def get(self, path, **kw):
        return self._add("GET", path)

    def post(self, path, **kw):
        return self._add("POST", path)

    def __getattr__(self, name):              # put/patch/delete/api_route/... would be recorded too
        if name in ("put", "patch", "delete", "options", "head", "api_route", "websocket", "add_api_route"):
            return lambda path, *a, **kw: self._add(name.upper(), path)
        raise AttributeError(name)


class _JSONResponse:
    def __init__(self, content, status_code=200):
        self.content, self.status_code = content, status_code


@pytest.fixture
def fake_fastapi(monkeypatch):
    fa = types.ModuleType("fastapi")
    fa.APIRouter = _Router
    fa.Body = lambda *a, **kw: None
    fa.Request = object
    resp = types.ModuleType("fastapi.responses")
    resp.JSONResponse = _JSONResponse
    monkeypatch.setitem(sys.modules, "fastapi", fa)
    monkeypatch.setitem(sys.modules, "fastapi.responses", resp)
    monkeypatch.delitem(sys.modules, "botsy_dashboard_tab", raising=False)
    return fa


def _plugin_api(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    return _load("hermes_dashboard_plugin_botsy_test", DASH / "plugin_api.py")


class _Req:
    def __init__(self, bound="127.0.0.1", client="127.0.0.1", headers=None, auth=False):
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(bound_host=bound, auth_required=auth,
                                                                     trusted_public_hosts=frozenset()))
        self.client = types.SimpleNamespace(host=client) if client else None
        self.headers = headers or {}


def test_plugin_api_has_exactly_four_routes(fake_fastapi, monkeypatch):
    api = _plugin_api(monkeypatch, ["hermes", "dashboard", "--host", "127.0.0.1"])
    assert sorted((m, p) for m, p, _ in api.router.routes) == [
        ("GET", "/overview"), ("GET", "/thumb"), ("POST", "/lower_cap"), ("POST", "/pause")]


def test_plugin_api_refuses_to_load_on_a_public_bind(fake_fastapi, monkeypatch):
    with pytest.raises(RuntimeError):
        _plugin_api(monkeypatch, ["hermes", "dashboard", "--host", "0.0.0.0"])


def test_plugin_api_routes_check_bind_and_header(fake_fastapi, monkeypatch):
    api = _plugin_api(monkeypatch, ["hermes", "dashboard"])
    called = []
    monkeypatch.setattr(api.tab, "overview", lambda: called.append("overview") or {"ok": True})
    monkeypatch.setattr(api.tab, "act", lambda name, body: called.append(name) or {"ok": True})
    routes = {p: fn for _, p, fn in api.router.routes}
    assert routes["/overview"](_Req(bound="0.0.0.0")).status_code == 403
    assert routes["/overview"](_Req(client="198.51.100.4")).status_code == 403
    assert routes["/overview"](_Req(auth=True)).status_code == 403
    assert routes["/overview"](_Req()) == {"ok": True}
    assert routes["/pause"](_Req(), {}).status_code == 400                       # no X-Botsy-Tab
    assert routes["/lower_cap"](_Req(bound="0.0.0.0", headers={"x-botsy-tab": "1"}), {}).status_code == 403
    assert routes["/pause"](_Req(headers={"x-botsy-tab": "1"}), {}) == {"ok": True}
    assert routes["/lower_cap"](_Req(headers={"x-botsy-tab": "1"}), {"cap_usd": 5}) == {"ok": True}
    assert called == ["overview", "pause", "lower_cap"]


def test_the_page_calls_only_the_four_routes():
    js = (DASH / "dist" / "index.js").read_text(encoding="utf-8")
    assert set(re.findall(r'BASE \+ "(/[a-z_]+)"', js)) == {"/overview", "/thumb", "/pause", "/lower_cap"}
    assert re.findall(r'"/api/[^"]*"', js) == ['"/api/plugins/botsy"']
    assert "innerHTML" not in js and "dangerouslySetInnerHTML" not in js and "eval(" not in js
    for word in ("approve_order", "/resume", "raise_cap", "submit_order", "/api/config", "/api/env"):
        assert word not in js
    assert 'REG.register("botsy"' in js
    if subprocess.run(["which", "node"], capture_output=True).returncode == 0:
        assert subprocess.run(["node", "--check", str(DASH / "dist" / "index.js")], capture_output=True).returncode == 0


def test_manifest_matches_the_plugin():
    m = json.loads((DASH / "manifest.json").read_text(encoding="utf-8"))
    assert m["name"] == "botsy" and m["tab"]["path"] == "/botsy" and m["api"] == "plugin_api.py"
    for rel in (m["entry"], m["css"], m["api"]):
        assert (DASH / rel).is_file()
    assert m["icon"] in {"Activity", "BarChart3", "Clock", "Code", "Database", "Eye", "FileText", "Globe", "Heart",
                         "KeyRound", "MessageSquare", "Package", "Puzzle", "Settings", "Shield", "Sparkles", "Star",
                         "Terminal", "Wrench", "Zap"}
    # No plugin.yaml / plugin.json: the gateway's plugin loader finds nothing to run in this folder.
    assert not list(DASH.rglob("plugin.yaml")) and not list(DASH.rglob("plugin.json"))


def test_the_unit_binds_loopback_only():
    unit = (DASH / "hermes-dashboard.service").read_text(encoding="utf-8")
    exec_start = next(x for x in unit.splitlines() if x.startswith("ExecStart="))
    assert "--host 127.0.0.1" in exec_start and "--skip-build" in exec_start and "0.0.0.0" not in exec_start
    assert tab.argv_refusal(exec_start.split("=", 1)[1].split()) is None
    for line in ("User=hermes", "NoNewPrivileges=yes", "IPAddressDeny=any", "IPAddressAllow=localhost",
                 "Environment=HERMES_WEB_DIST=/opt/hermes/web_dist", "ProtectHome=tmpfs"):
        assert line in unit.splitlines()
    assert "/opt/botsy" in unit and "RemoveIPC" not in "\n".join(x for x in unit.splitlines() if not x.startswith("#"))


def test_overseer_documents_the_tab():
    doc = (ROOT / "docs" / "OVERSEER.md").read_text(encoding="utf-8")
    assert "## Botsy dashboard tab" in doc
    part = doc.split("## Botsy dashboard tab", 1)[1]
    assert "ssh -N -L 9119:127.0.0.1:9119" in part and 'created_by "hermes"' in part


def test_the_page_labels_the_approval_count_as_d1_counts_it():
    """level3_last_24h counts every Hermes approval and only the user's posting ones (D1): the page
    must not call it Hermes's own count."""
    js = (DASH / "dist" / "index.js").read_text(encoding="utf-8")
    assert "Hermes approvals in 24 h" not in js
    assert "yours that post to Etsy or KDP" in js


def test_docs_do_not_promise_a_user_pause_through_discord():
    part = (ROOT / "docs" / "OVERSEER.md").read_text(encoding="utf-8").split("## Botsy dashboard tab", 1)[1]
    assert "ask in\n  Discord yourself" not in part and "STOP_ONLY" in part
    assert "counts toward Hermes's own limits" not in (DASH / "botsy_tab.py").read_text(encoding="utf-8")


def test_both_actions_are_stop_only_orders():
    """The docs say the tab's two actions are never limited and use no Hermes change or approval
    budget; that holds only while Botsy treats them as stop-only."""
    from service import orders

    assert set(tab.ACTIONS) <= set(orders.STOP_ONLY)
    assert not any(orders.counts_toward_level3(a, "user") for a in tab.ACTIONS)
