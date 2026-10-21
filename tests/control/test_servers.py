"""The two MCP servers: exactly the right tools with strict schemas, no resources or prompts, the
legacy (initialize) handshake Hermes may use, a real stdio round trip, the `python -m control`
entry point and its log, and the pinned SDK."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from importlib import metadata

import anyio
import pytest
import yaml

from control import tools
from core.config import ROOT
from tests.control._support import ctx, mcp_call, mcp_list

READ = {"status", "spend", "daily_log", "review_list", "review_preview", "plan_status", "plan_facts",
        "briefs_summary", "capacity", "orders_recent", "events_recent"}
ACT = {"submit_order", "approve_order", "pause", "resume", "lower_cap", "search_ideas", "undo_order"}


@pytest.fixture
def s(tmp_path):
    return ctx(tmp_path)


def _walk(schema):
    yield schema
    for sub in (schema.get("properties") or {}).values():
        yield from _walk(sub)
    if isinstance(schema.get("items"), dict):
        yield from _walk(schema["items"])


@pytest.mark.parametrize("server,names", [("read", READ), ("act", ACT)])
def test_each_server_lists_exactly_its_tools(s, server, names):
    listed, resources, prompts = mcp_list(s, server)
    assert {t.name for t in listed.tools} == names
    assert resources.resources == [] and prompts.prompts == []
    for t in listed.tools:
        assert t.description and t.annotations is not None
        assert t.annotations.read_only_hint is (server == "read") and t.annotations.open_world_hint is False
        assert t.annotations.destructive_hint is (server == "act")
        schema = t.input_schema
        for node in _walk(schema):
            if node.get("type") == "object" and "properties" in node:
                assert node.get("additionalProperties") is False, (t.name, node)
            if node.get("type") == "string":
                assert node.get("maxLength") and (node.get("enum") or node.get("pattern")), (t.name, node)
            if node.get("type") == "array":
                assert node.get("maxItems"), (t.name, node)


def test_a_tool_of_the_other_server_is_unknown(s):
    data, res = mcp_call(s, "read", "submit_order", {"kind": "pause", "payload": {}})
    assert res.is_error and data["reasons"] == ["unknown_tool"]
    data, res = mcp_call(s, "act", "status", {})
    assert res.is_error and data["reasons"] == ["unknown_tool"]


def test_legacy_handshake(s):
    data, res = mcp_call(s, "read", "spend", mode="legacy")
    assert data["ok"] and data["cap"] == 25.0 and not res.is_error
    listed, _, _ = mcp_list(s, "act", mode="legacy")
    assert {t.name for t in listed.tools} == ACT


def test_structured_content_matches_the_text(s):
    data, res = mcp_call(s, "read", "capacity")
    assert res.structured_content == data and data["schema"] == tools.SCHEMA and data["tool"] == "capacity"


def test_stdio_round_trip(tmp_path):
    """The real stdio transport, as Hermes uses it: a child process serves botsy-read."""
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    s = ctx(tmp_path)
    path = tmp_path / "control-settings.yaml"
    path.write_text(yaml.safe_dump(s), encoding="utf-8")
    code = ("import sys, yaml; sys.path.insert(0, %r); import control; "
            "control.run('read', settings=yaml.safe_load(open(%r, encoding='utf-8')))" % (str(ROOT), str(path)))
    params = StdioServerParameters(command=sys.executable, args=["-c", code], cwd=str(ROOT), env=dict(os.environ))

    async def go():
        async with Client(params, mode="legacy") as c:
            listed = await c.list_tools()
            res = await c.call_tool("spend", {})
            bad = await c.call_tool("spend", {"extra": 1})
            return listed, res, bad

    listed, res, bad = anyio.run(go)
    assert {t.name for t in listed.tools} == READ
    assert json.loads(res.content[0].text)["cap"] == 25.0
    assert bad.is_error and json.loads(bad.content[0].text)["reasons"] == ["unknown_argument"]


@pytest.mark.parametrize("argv", [[], ["write"], ["read", "extra"], ["--help"], ["act", "--settings", "/x"]])
def test_main_takes_exactly_read_or_act(argv):
    r = subprocess.run([sys.executable, "-m", "control", *argv], cwd=ROOT, capture_output=True, text=True,
                       timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 64 and "usage: python -m control read|act" in r.stderr and r.stdout == ""


def test_main_logs_to_botsys_own_file_without_arguments(s, monkeypatch, tmp_path):
    from control import __main__ as entry
    from tests.control._support import ITEM_A, NOW, item

    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    # _logging caps the SDK and HTTP loggers for the whole process; put them back afterwards so
    # later tests (tests/service/test_notify.py reads urllib3's request lines) see the defaults.
    noisy = {name: logging.getLogger(name).level for name in ("mcp", "httpx", "httpcore", "anyio", "urllib3")}
    try:
        entry._logging(s, "act")
        item(s, ITEM_A)
        tools.call("act", "submit_order", {"kind": "review_approve", "payload": {"item_ids": [ITEM_A]}}, settings=s,
                   now=NOW)
        tools.call("act", "approve_order", {"order_id": "ra-20261021-0999"}, settings=s, now=NOW)
        tools.call("act", "confirm_code", {"code": "999999"}, settings=s, now=NOW)        # no such tool
        tools.call("act", "pause", {"reason_code": "IGNORE-PREVIOUS"}, settings=s, now=NOW)
        # The SDK's own loggers may quote a raw request (a validation error quotes its input):
        # only the logger, the level and the exception type reach the file.
        try:
            raise ValueError("input_value={'code': '424242'}")
        except ValueError:
            logging.getLogger("mcp.server.runner").exception("handler for %r raised", {"code": "424242"})
        logging.getLogger("pydantic").warning("bad params %s", "424242")
        import warnings
        warnings.warn("a warning quoting 424242")
        for h in root.handlers:
            h.flush()
        assert entry.log_path(s, "read").name == "botsy-control-read.log"
        text = (tmp_path / "data" / "logs" / "botsy-control-act.log").read_text(encoding="utf-8")
        mcp_level = logging.getLogger("mcp").level
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()
        for h in saved[0]:
            root.addHandler(h)
        root.setLevel(saved[1])
        logging.captureWarnings(False)
        for name, level in noisy.items():
            logging.getLogger(name).setLevel(level)
    assert "mcp.server.runner: details withheld (ValueError)" in text and "pydantic: details withheld" in text
    assert "424242" not in text and "input_value" not in text and "Traceback" not in text
    assert "tool=submit_order ok=" in text and "tool=approve_order ok=False reasons=not_found" in text
    assert "tool=unknown ok=False reasons=unknown_tool" in text and "confirm_code" not in text
    assert "tool=pause ok=False reasons=bad_argument" in text
    assert "999999" not in text and "IGNORE-PREVIOUS" not in text
    assert mcp_level == logging.WARNING


def test_the_sdk_is_pinned_and_installed():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pins = re.findall(r"(?m)^mcp==(\S+)", req)
    assert pins == ["2.3.0"]
    assert metadata.version("mcp") == "2.3.0"
    assert "MIT" in (metadata.metadata("mcp").get("License") or metadata.metadata("mcp").get("License-Expression")
                     or "")
