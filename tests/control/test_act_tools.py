"""botsy-act: every action goes through service.orders (validate / submit / approve / undo; plans
through service.plan), created_by is always "hermes", there are no codes (decided 2026-10-21),
and bad input is refused before anything runs, without its value reaching an event or the log."""
from __future__ import annotations

import json
import logging
from collections import namedtuple

import pytest

from control import tools
from qa import review
from service import capacity, orders
from tests.control._support import (
    BRIEF_DAY,
    ITEM_A,
    ITEM_B,
    NEXT_WEEK,
    NOW,
    brief_file,
    ctx,
    item,
    journal,
    mcp_call,
    work_root,
)

Usage = namedtuple("Usage", "total used free")


@pytest.fixture(autouse=True)
def big_disk(monkeypatch):
    monkeypatch.setattr(capacity.shutil, "disk_usage", lambda p: Usage(10**12, 0, 10**12))


@pytest.fixture
def s(tmp_path):
    return ctx(tmp_path)


@pytest.fixture(autouse=True)
def no_discord(monkeypatch):
    """No webhook in the environment: every Discord post fails closed offline."""
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)


@pytest.fixture
def spy(monkeypatch):
    """Wraps service.orders.validate: every call is recorded with the order it got."""
    calls = []
    real = orders.validate

    def wrapped(order, *a, **kw):
        calls.append(json.loads(json.dumps(order)))
        return real(order, *a, **kw)

    monkeypatch.setattr(orders, "validate", wrapped)
    return calls


def act(s, name, args=None, **kw):
    data, _ = mcp_call(s, "act", name, args, **kw)
    return data


def entry(s, item_id):
    return review.read_entry(item_id, work_root(s) / "review")


# ---------------------------------------------------------------- routing through service.orders

@pytest.mark.parametrize("tool,args,kind", [
    ("pause", {}, "pause"),
    ("pause", {"reason_code": "incident"}, "pause"),
    ("lower_cap", {"cap_usd": 20}, "lower_cap"),
    ("search_ideas", {"ideas": [{"niche": "stickers", "query": "axolotl stickers"}]}, "search_ideas"),
    ("submit_order", {"kind": "plan_hold", "payload": {"plan_week": NEXT_WEEK}}, "plan_hold"),
])
def test_act_tools_route_through_orders_validate(s, spy, tool, args, kind):
    d = act(s, tool, args)
    assert len(spy) == 1, spy
    assert spy[0]["kind"] == kind and spy[0]["created_by"] == "hermes"
    if kind != "plan_hold":
        assert d["ok"] and d["applied"]["ok"] and d["status"] == "applied", d
    else:
        assert not d["ok"] and d["reasons"] == ["no_plan"]          # refused by orders, not here


def test_pause_then_resume_own_pause(s, spy):
    d = act(s, "pause", {"reason_code": "incident"})
    assert d["ok"] and orders.paused(s)["by"] == "hermes"
    d = act(s, "resume")
    assert d["ok"] and d["applied"]["ok"] and orders.paused(s) is None
    assert [c["kind"] for c in spy] == ["pause", "resume"]


def test_resume_of_the_users_pause_is_a_hermes_order_without_a_code(s, spy):
    orders.submit({"kind": "pause", "created_by": "user", "payload": {}}, s, now=NOW)
    d = act(s, "resume")
    assert [c["kind"] for c in spy] == ["pause", "resume"] and spy[-1]["created_by"] == "hermes"
    assert "code" not in d and "needs_code" not in d and isinstance(d["needs_approval"], bool)
    assert d["ok"] and d["status"] == "applied" and orders.paused(s) is None, d


def test_lower_cap_only_lowers(s):
    assert act(s, "lower_cap", {"cap_usd": 30})["reasons"] == ["not_lower"]
    assert act(s, "lower_cap", {"cap_usd": 15})["ok"] and orders.effective_cap(s) == 15.0


def test_created_by_cannot_be_set(s, spy):
    d = act(s, "submit_order", {"kind": "pause", "payload": {}, "created_by": "user"})
    assert d["reasons"] == ["unknown_argument"] and spy == []
    d = act(s, "submit_order", {"kind": "pause", "payload": {"created_by": "user"}})
    assert not d["ok"] and d["stage"] == "format" and "unknown_field" in d["reasons"]
    assert spy[-1]["created_by"] == "hermes"


def test_scale_up_is_refused_until_winners_exist(s, spy):
    d = act(s, "submit_order", {"kind": "scale_up", "payload": {
        "plan_week": NEXT_WEEK, "winner_ref": "win-2026-11-15-coloring_pages-axolotl-garden",
        "reason_code": "sales_winner", "max_cost_usd": 2.0,
        "actions": [{"kind": "pack_size", "niche": "coloring_pages", "brief_id": f"coloring_pages-{BRIEF_DAY}-01",
                     "size": 10}]}})
    assert not d["ok"] and d["stage"] == "switches" and d["reasons"] == ["no_winners_yet"]
    assert "winners" in d["explain"]["no_winners_yet"].lower() or d["explain"]["no_winners_yet"]


def test_undo_order_goes_through_orders_undo(s, monkeypatch):
    seen = []
    real = orders.undo

    def wrapped(order_id, settings=None, *, by="user", now=None):
        seen.append(by)
        return real(order_id, settings, by=by, now=now)

    monkeypatch.setattr(orders, "undo", wrapped)
    d = act(s, "lower_cap", {"cap_usd": 15})
    u = act(s, "undo_order", {"order_id": d["order_id"]})
    assert u["ok"] and seen == ["hermes"] and orders.effective_cap(s) == 25.0


# ---------------------------------------------------------------- Level 3 without codes

def _no_codes(d: dict) -> None:
    blob = json.dumps(d)
    for word in ("code sent", "#botsy-codes", "confirm_code", "request_code", '"code"', "needs_code"):
        assert word not in blob, (word, d)


def test_level3_orders_go_to_orders_validate_and_never_mention_codes(s, spy):
    item(s, ITEM_A)
    for args in ({"kind": "review_approve", "payload": {"item_ids": [ITEM_A]}},
                 {"kind": "raise_cap", "payload": {"cap_usd": 30}},
                 {"kind": "resume_after_budget_stop", "payload": {}}):
        d = act(s, "submit_order", args)
        _no_codes(d)
        assert spy[-1]["kind"] == args["kind"] and spy[-1]["created_by"] == "hermes"
    assert len(spy) == 3


def test_a_hard_blocked_item_is_never_approved(s, spy):
    item(s, ITEM_A, passed=False)
    d = act(s, "submit_order", {"kind": "review_approve", "payload": {"item_ids": [ITEM_A]}})
    assert not d["ok"] and "item_failed_qa" in d["reasons"], d
    assert entry(s, ITEM_A)["status"] == "pending"


def test_review_approval_from_hermes_applies_at_once(s, spy):
    item(s, ITEM_A)
    item(s, ITEM_B, theme="frogs")
    d = act(s, "submit_order", {"kind": "review_approve", "payload": {"item_ids": [ITEM_A, ITEM_B],
                                                                      "batch": "frogs-and-axolotls"}})
    assert d["ok"] and d["status"] == "applied" and d["needs_approval"] is False, d
    assert entry(s, ITEM_A)["status"] == "approved" and entry(s, ITEM_B)["status"] == "approved"
    _no_codes(d)


def test_raise_cap_never_goes_above_the_ceiling(s):
    lim = orders._limits(s, None)
    ceiling = lim.get("cap_ceiling_usd")
    d = act(s, "submit_order", {"kind": "raise_cap", "payload": {"cap_usd": float(ceiling) + 1}})
    assert not d["ok"] and d["reasons"] == ["above_ceiling"], d


WAITING_ID = "pl-20261021-0001"


def _stored(monkeypatch, **fields):
    o = {"id": WAITING_ID, "kind": "weekly_plan", "status": "waiting_approval", "created_by": "botsy",
         "payload": {"plan_week": NEXT_WEEK}, **fields}
    monkeypatch.setattr(orders, "load", lambda oid, settings=None: dict(o) if oid == o["id"] else None)
    return o


def test_approve_order_binds_the_plan_service_to_the_stored_plan(s, monkeypatch):
    from service import plan

    calls = []

    def fake_approve(settings=None, *, by="user", now=None, run_check=None, order_id=None, first_check=True):
        calls.append({"by": by, "order_id": order_id, "first_check": first_check, "now": now})
        return {"ok": True, "step": "started", "reason": None, "week": NEXT_WEEK, "order_id": order_id,
                "status": "running"}

    monkeypatch.setattr(plan, "approve", fake_approve)
    _stored(monkeypatch)
    d = act(s, "approve_order", {"order_id": WAITING_ID})
    assert d["ok"] and d["order_id"] == WAITING_ID and d["step"] == "started" and d["week"] == NEXT_WEEK, d
    assert calls == [{"by": "hermes", "order_id": WAITING_ID, "first_check": False, "now": NOW}]
    assert "hourly" in d["next"]
    _no_codes(d)


def test_approve_order_fails_closed_without_a_bindable_plan_approve(s, monkeypatch):
    """A plan service whose approve() cannot be told which plan (or would run a check in this
    process) is never called: a refusal, not a guess."""
    from service import plan

    calls = []

    def old_approve(settings=None, *, by="user", now=None, run_check=None, **_ignored):
        calls.append(by)
        return {"ok": True}

    monkeypatch.setattr(plan, "approve", old_approve)
    _stored(monkeypatch)
    d = act(s, "approve_order", {"order_id": WAITING_ID})
    assert not d["ok"] and d["reasons"] == ["approval_unavailable"] and calls == []
    monkeypatch.setattr(tools, "_plan_module", lambda: None)
    assert act(s, "approve_order", {"order_id": WAITING_ID})["reasons"] == ["plan_service_missing"]


def test_approve_order_refuses_what_does_not_wait(s, monkeypatch):
    from service import plan

    monkeypatch.setattr(plan, "approve", lambda *a, **k: pytest.fail("never called"))
    assert act(s, "approve_order", {"order_id": "pl-20261021-0099"})["reasons"] == ["not_found"]
    for fields in ({"status": "applied"}, {"status": "accepted"}, {"status": "waiting_code"},
                   {"status": "waiting_approval", "superseded_by": "pr-20261021-0002"}):
        _stored(monkeypatch, **fields)
        d = act(s, "approve_order", {"order_id": WAITING_ID})
        assert not d["ok"] and d["reasons"] == ["not_waiting"], (fields, d)


def test_approve_order_for_other_kinds_uses_orders_approve(s, monkeypatch):
    seen = []

    def fake(order_id, settings=None, *, by="user", now=None):
        seen.append((order_id, by))
        return {"ok": True, "order_id": order_id, "status": "applied", "stage": None, "reasons": [],
                "undo": {"kind": "review_hold", "item_ids": [ITEM_A]}}

    _stored(monkeypatch, kind="review_approve", payload={"item_ids": [ITEM_A]})
    monkeypatch.delattr(orders, "approve", raising=False)
    assert act(s, "approve_order", {"order_id": WAITING_ID})["reasons"] == ["approval_unavailable"]
    monkeypatch.setattr(orders, "approve", fake, raising=False)
    d = act(s, "approve_order", {"order_id": WAITING_ID})
    assert d["ok"] and d["status"] == "applied" and seen == [(WAITING_ID, "hermes")]
    assert d["undo"] == {"kind": "review_hold", "item_ids": [ITEM_A]}


def test_a_waiting_plan_says_to_ask_the_user(s, monkeypatch):
    def fake_submit(order, settings=None, *, now=None):
        return {"ok": True, "order_id": WAITING_ID, "status": "waiting_approval", "needs_approval": True,
                "stage": None, "reasons": [], "order": {"kind": "weekly_plan", "level": 2, "week": NEXT_WEEK,
                                                        "plan_number": 1}}

    from service import plan

    monkeypatch.setattr(plan, "submit_order", fake_submit)
    d = act(s, "submit_order", {"kind": "weekly_plan", "payload": {"plan_week": NEXT_WEEK, "lines": [
        {"niche": "coloring_pages", "brief_id": f"coloring_pages-{BRIEF_DAY}-01", "sizes": [5]}]}})
    assert d["ok"] and d["needs_approval"] is True and d["plan_number"] == 1
    assert "approve_order" in d["next"] and "own words" in d["next"]
    _no_codes(d)


def test_plans_are_refused_without_the_plan_service(s, monkeypatch, spy):
    monkeypatch.setattr(tools, "_plan_module", lambda: None)
    brief_file(s)
    d = act(s, "submit_order", {"kind": "weekly_plan", "payload": {"plan_week": NEXT_WEEK, "lines": [
        {"niche": "coloring_pages", "brief_id": f"coloring_pages-{BRIEF_DAY}-01", "sizes": [5]}]}})
    assert not d["ok"] and d["reasons"] == ["plan_service_missing"] and spy == []
    assert act(s, "pause")["ok"]                         # other orders still work


def test_plan_content_is_checked_by_the_plan_service(s):
    brief_file(s)
    brief_file(s, "stickers", "2026-10-10", briefs=[{"brief_id": "stickers-2026-10-10-01", "theme": "frogs",
                                                     "pack_sizes": [20]}])
    line = {"niche": "coloring_pages", "brief_id": f"coloring_pages-{BRIEF_DAY}-01", "sizes": [5, 20]}
    for bad in ({"niche": "coloring_pages", "brief_id": "coloring_pages-2026-10-19-09", "sizes": [5]},
                {**line, "sizes": [7]}):
        d = act(s, "submit_order", {"kind": "weekly_plan", "payload": {"plan_week": NEXT_WEEK, "lines": [bad]}})
        assert not d["ok"] and d["stage"] == "content" and d["reasons"], (bad, d)
    assert act(s, "submit_order", {"kind": "weekly_plan", "payload": {"plan_week": NEXT_WEEK,
                                                                      "lines": [line]}})["ok"]


def test_hermes_plans_go_to_the_plan_service(s, monkeypatch, spy):
    """With service.plan installed, a Hermes plan goes to plan.submit_order (which runs
    orders.submit / validate with the plan service's own checks)."""
    from service import plan

    seen = []
    real = plan.submit_order

    def wrapped(order, settings=None, *, now=None):
        seen.append(order)
        return real(order, settings, now=now)

    monkeypatch.setattr(plan, "submit_order", wrapped)
    brief_file(s)
    d = act(s, "submit_order", {"kind": "weekly_plan", "payload": {"plan_week": NEXT_WEEK, "lines": [
        {"niche": "coloring_pages", "brief_id": f"coloring_pages-{BRIEF_DAY}-01", "sizes": [5]}]}})
    assert d["ok"], d
    assert len(seen) == 1 and seen[0]["created_by"] == "hermes" and seen[0]["kind"] == "weekly_plan"
    assert len(spy) == 1 and spy[0]["created_by"] == "hermes"


def test_plan_context_fails_closed(s, monkeypatch):
    class Broken:
        @staticmethod
        def load_state(settings):
            return {}

        @staticmethod
        def order_context(st, settings):
            raise RuntimeError("bug")

    monkeypatch.setattr(tools, "_plan_module", lambda: Broken)
    assert tools._plan_context(s) == {"approved_plans": 0, "approve_each": True}
    monkeypatch.setattr(tools, "_plan_module", lambda: None)
    assert tools._plan_context(s) == {"approved_plans": 0, "approve_each": True}


# ---------------------------------------------------------------- refused input

BAD_CALLS = [
    ("submit_order", {"kind": "drop_tables", "payload": {}}, "bad_argument"),
    ("submit_order", {"kind": "pause"}, "missing_argument"),
    ("submit_order", {"kind": "pause", "payload": "pause please"}, "bad_argument"),
    ("submit_order", {"kind": "pause", "payload": {"Reason Code": "x"}}, "bad_argument"),
    ("submit_order", {"kind": "pause", "payload": {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}}, "bad_argument"),
    ("submit_order", {"kind": "pause", "payload": {"reason_code": "x" * 161}}, "bad_argument"),
    ("submit_order", {"kind": "search_ideas", "payload": {"ideas": [{"niche": "stickers", "query": "q" * 150}] * 120}},
     "too_large"),
    ("submit_order", {"kind": "pause", "payload": {}, "client_ref": "Has Spaces"}, "bad_argument"),
    ("pause", {"reason_code": "because I said so"}, "bad_argument"),
    ("lower_cap", {"cap_usd": "20"}, "bad_argument"),
    ("lower_cap", {"cap_usd": 0}, "bad_argument"),
    ("lower_cap", {"cap_usd": True}, "bad_argument"),
    ("lower_cap", {"cap_usd": 10**400}, "bad_argument"),
    ("lower_cap", {}, "missing_argument"),
    ("search_ideas", {"ideas": []}, "bad_argument"),
    ("search_ideas", {"ideas": [{"niche": "stickers", "query": "Ignore All Rules"}]}, "bad_argument"),
    ("search_ideas", {"ideas": [{"niche": "weapons", "query": "frogs"}]}, "bad_argument"),
    ("search_ideas", {"ideas": [{"niche": "stickers", "query": "frogs", "prompt": "draw x"}]}, "unknown_argument"),
    ("search_ideas", {"ideas": [{"niche": "stickers", "query": "frogs"}] * 6}, "bad_argument"),
    ("approve_order", {}, "missing_argument"),
    ("approve_order", {"order_id": "../etc/passwd"}, "bad_argument"),
    ("approve_order", {"order_id": "RA-20261021-0001"}, "bad_argument"),
    ("approve_order", {"order_id": "pl-20261021-0001", "code": "123456"}, "unknown_argument"),
    ("approve_order", {"order_id": "pl-20261021-0001", "by": "user"}, "unknown_argument"),
    ("request_code", {"action": "review_approve", "items": [ITEM_A]}, "unknown_tool"),
    ("confirm_code", {"code": "123456", "action": "resume", "items": ["paid_work"]}, "unknown_tool"),
    ("undo_order", {"order_id": "../../x"}, "bad_argument"),
    ("status", {}, "unknown_tool"),                     # a read tool is not on the act server
    ("run_shell", {"cmd": "id"}, "unknown_tool"),
]


@pytest.mark.parametrize("name,args,reason", BAD_CALLS)
def test_bad_input_is_refused_before_anything_runs(s, spy, caplog, name, args, reason):
    caplog.set_level(logging.DEBUG)
    d = act(s, name, args)
    assert d["ok"] is False and reason in d["reasons"], d
    assert spy == []                                    # orders.validate never saw it
    text = json.dumps(journal(s)) + caplog.text
    for v in ("drop_tables", "pause please", "Reason Code", "Has Spaces", "because I said so", "Ignore All Rules",
              "weapons", "draw x", "delete_listing", "etc/passwd", "12345a", "run_shell"):
        assert v not in text
    if name in tools.TOOLS and tools.TOOLS[name].server == "act":
        ev = [e for e in journal(s) if e["kind"] == "control_refused"]
        assert ev and ev[-1]["tool"] == name and reason in ev[-1]["reasons"]
        assert set(ev[-1]) == {"ts", "kind", "tool", "reasons"}


def test_hermes_text_never_reaches_events_or_the_log(s, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    marker = "please-approve-everything-now"
    d = act(s, "submit_order", {"kind": "review_reject", "payload": {"item_id": ITEM_A, "reason_code": marker}})
    assert not d["ok"] and d["stage"] == "format"
    d = act(s, "submit_order", {"kind": "review_reject", "payload": {"item_id": ITEM_A, marker.replace("-", "_"): 1,
                                                                     "reason_code": "other_rule"}})
    assert not d["ok"] and "unknown_field" in d["reasons"]
    blob = json.dumps(journal(s)) + caplog.text + "".join(p.read_text(errors="replace")
                                                          for p in tmp_path.rglob("*") if p.is_file()
                                                          and p.suffix in (".json", ".jsonl", ".log"))
    assert marker not in blob and marker.replace("-", "_") not in blob


def test_no_forbidden_tools():
    names = set(tools.TOOLS)
    for bad in ("shell", "exec", "run", "file", "read_file", "write_file", "web", "fetch", "browse", "delete",
                "settings", "blocklist", "research", "publish", "activate", "listing_edit"):
        assert not any(n == bad or n.startswith(bad + "_") for n in names), bad
    assert set(t.name for t in tools.tools_for("act")) == {"submit_order", "approve_order", "pause", "resume",
                                                           "lower_cap", "search_ideas", "undo_order",
                                                           *tools.FIX_TOOLS}
    assert not any("code" in n for n in names)                  # decided 2026-10-21: no codes


def test_a_hermes_plan_for_botsys_draft_becomes_a_revision(s):
    """A Hermes weekly_plan for a week that holds Botsy's draft is stored as a plan_revision of it."""
    brief_file(s)
    line = {"niche": "coloring_pages", "brief_id": f"coloring_pages-{BRIEF_DAY}-01", "sizes": [5]}
    draft = orders.submit({"kind": "weekly_plan", "created_by": "botsy",
                           "payload": {"plan_week": NEXT_WEEK, "lines": [line]}}, s, now=NOW)
    assert draft["ok"]
    d = act(s, "submit_order", {"kind": "weekly_plan", "payload": {"plan_week": NEXT_WEEK,
                                                                   "lines": [{**line, "sizes": [5, 20]}]}})
    assert d["ok"] and d["converted_to_revision"] and d["kind"] == "plan_revision", d
    # The draft it replaced can never be approved through Hermes.
    assert act(s, "approve_order", {"order_id": draft["order_id"]})["reasons"] == ["not_waiting"]


def test_a_refusal_stays_a_refusal_when_the_journal_fails(s, monkeypatch, spy):
    """A journal that cannot be written never turns a refusal into internal_error (or an order)."""
    from core import events as events_mod

    def broken(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(events_mod, "emit", broken)
    d = act(s, "pause", {"reason_code": "not-a-code"})
    assert not d["ok"] and d["reasons"] == ["bad_argument"] and d["fields"] == ["reason_code"]
    assert spy == []
