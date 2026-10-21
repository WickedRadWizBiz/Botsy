"""service/plan.py: the Sunday plan (docs/OVERSEER.md Part B) with a fixed clock.

Everything is in tmp_path: briefs, the plan state, orders, the control state, the journal
and the ledger. Discord posts are captured, so nothing reaches the network. No codes (user,
2026-10-21): plans that wait for you are approved with a plain `plan approve`. Times are UTC;
the comments give New York time."""
from __future__ import annotations

import json
import os
import re
import shutil
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import events, items, spend
from core.config import ROOT
from service import capacity, orders, plan
from tests.service._support import make_settings

Usage = namedtuple("Usage", "total used free")
UTC = timezone.utc
SUN_8PM = datetime(2026, 10, 26, 0, 0, tzinfo=UTC)        # Sun 25 Oct 2026, 20:00 EDT: plan for 2026-W44
MON_8AM = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)       # Mon 26 Oct, 08:00 EDT
MON_7PM = datetime(2026, 10, 26, 23, 0, tzinfo=UTC)       # Mon 26 Oct, 19:00 EDT
TUE_8PM = datetime(2026, 10, 28, 0, 0, tzinfo=UTC)        # Tue 27 Oct, 20:00 EDT
WEEK = "2026-W44"
DAY = "2026-10-24"                                         # the briefs' date (Saturday)
M = timedelta(minutes=1)


@pytest.fixture(autouse=True)
def big_disk(monkeypatch):
    monkeypatch.setattr(capacity.shutil, "disk_usage", lambda p: Usage(10**12, 0, 10**12))


class Posts(list):
    """The captured posts as (kind, text); .urgent says, for each, whether it was sent urgent."""

    def __init__(self):
        super().__init__()
        self.urgent: list[bool] = []


@pytest.fixture
def posts(monkeypatch):
    """Every plan message, as (kind, text): service.notify.send is replaced."""
    got = Posts()

    def fake_send(text, *, settings=None, kinds=(), now=None, urgent=False, **kw):
        got.append(((list(kinds) or ["?"])[0], text))
        got.urgent.append(bool(urgent))
        return True

    from service import notify

    monkeypatch.setattr(notify, "send", fake_send)
    return got


def ctx(tmp_path: Path, limits_doc: str | None = None, **sections) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    lim = tmp_path / "limits.yaml"
    if limits_doc is None:
        shutil.copy(ROOT / "config" / "limits.example.yaml", lim)
    elif limits_doc != "MISSING":
        lim.write_text(limits_doc, encoding="utf-8")
    if lim.exists():
        os.chmod(lim, 0o644)
    control = {"orders_dir": str(tmp_path / "data" / "orders"),
               "state_file": str(tmp_path / "data" / "control" / "state.json"), "limits_path": str(lim),
               "limits_require_root": False, "winners_file": str(tmp_path / "data" / "performance" / "winners.json")}
    plan_cfg = {"dir": str(tmp_path / "data" / "plans"), **sections.pop("plan", {})}
    s, _ = make_settings(tmp_path, control=control, plan=plan_cfg, **sections)
    return s


def write_briefs(s: dict, niche: str, themes, *, day: str = DAY, sizes=(5, 20), opps=None) -> Path:
    briefs = [{"brief_id": f"{niche}-{day}-{i:02d}", "niche": niche, "theme": t, "keywords": [t],
               "pack_sizes": list(sizes), "page_count": max(sizes),
               "evidence": {"listing_count": 12, "shop_count": 4, "score": 0.5,
                            "opportunity": opps[i - 1] if opps else round(0.9 - i * 0.1, 2)},
               "compliance": {"blocked": [], "flagged": []}, "needs_review": False}
              for i, t in enumerate(themes, 1)]
    doc = {"schema": "brief/v1", "niche": niche, "created": day, "shop_id": "main",
           "sources": [{"tool": "erank", "file": "k.csv", "rows": 50}], "listing_count": 50,
           "min_listings_per_pattern": 10, "patterns": {"themes": [{"name": t, "listing_count": 12} for t in themes]},
           "briefs": briefs}
    return items.write_json(Path(s["research"]["briefs_dir"]) / f"{niche}-{day}.json", doc)


def three_niches(s: dict) -> None:
    write_briefs(s, "coloring_pages", ["axolotl garden", "cozy mushrooms"])
    write_briefs(s, "stationery", ["weekly reset", "habit grid", "focus blocks"])
    write_briefs(s, "ttrpg", ["hex crawl journal"])


class Checks:
    """Stands in for the plan's first check (a forced tick under the main lock)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, settings):
        self.calls += 1
        return {"exit_code": 0}


def journal(s: dict, kind: str | None = None) -> list[dict]:
    p = Path(s["service"]["events_file"])
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
    return [r for r in rows if kind is None or r["kind"] == kind]


def entry(s: dict, week: str = WEEK) -> dict:
    return plan.load_state(s)["plans"][week]


def set_approved(s: dict, n: int, mode=None) -> None:
    st = plan.load_state(s)
    st["approved_count"], st["mode"] = n, mode
    plan._save_state(s, st)


def make_item(s: dict, brief_id: str, niche: str, theme: str, size: int, *, created="2026-10-26") -> Path:
    root = Path(s["pipeline"]["dry_run_output_dir"])
    d = items.item_dir(items.new_item_id(created, niche, theme, salt=f"{brief_id}{size}"), root)
    items.write_manifest(d, {"item_id": d.name, "niche": niche, "shop_id": "main", "brief_id": brief_id,
                             "theme": theme, "created": created, "status": "generated",
                             "generator": {"name": niche, "dry_run": True}, "pack": {"size": size, "source": "master"}})
    return d


# ---------------------------------------------------------------- the clock

@pytest.mark.parametrize("week, post, auto, deadline", [
    # summer time: Sun 20:00 EDT = Mon 00:00 UTC
    ("2026-W44", datetime(2026, 10, 26, 0, 0, tzinfo=UTC), datetime(2026, 10, 26, 12, 0, tzinfo=UTC),
     datetime(2026, 10, 28, 0, 0, tzinfo=UTC)),
    # the clocks change on Sun 1 Nov 2026: Sun 20:00 EST = Mon 01:00 UTC, Mon 08:00 EST = 13:00 UTC
    ("2026-W45", datetime(2026, 11, 2, 1, 0, tzinfo=UTC), datetime(2026, 11, 2, 13, 0, tzinfo=UTC),
     datetime(2026, 11, 4, 1, 0, tzinfo=UTC)),
    # the week before the spring change: still EST
    ("2027-W10", datetime(2027, 3, 8, 1, 0, tzinfo=UTC), datetime(2027, 3, 8, 13, 0, tzinfo=UTC),
     datetime(2027, 3, 10, 1, 0, tzinfo=UTC)),
    # the clocks change on Sun 14 Mar 2027 at 02:00: Sun 20:00 is EDT again
    ("2027-W11", datetime(2027, 3, 15, 0, 0, tzinfo=UTC), datetime(2027, 3, 15, 12, 0, tzinfo=UTC),
     datetime(2027, 3, 17, 0, 0, tzinfo=UTC)),
])
def test_plan_times_follow_new_york_time_through_the_clock_changes(tmp_path, week, post, auto, deadline):
    s = ctx(tmp_path)
    t = plan.schedule_for(week, s)
    assert t["post"] == post and t["auto_start"] == auto and t["deadline"] == deadline
    assert t["no_plan_at"] == post + timedelta(hours=1)
    assert t["reminders"] == [auto, auto + timedelta(hours=11)]                  # Mon 08:00 and 19:00
    assert t["upload_cutoff"] == post - timedelta(hours=1)                       # Sun 19:00
    assert plan.scheduled_post(s, post) == (week, post)
    assert plan.scheduled_post(s, post - M)[0] != week                           # 19:59 is last week's run
    assert plan.scheduled_post(s, post + timedelta(days=3))[0] == week


def test_the_week_of_the_november_change_in_local_time(tmp_path):
    s = ctx(tmp_path)
    t = plan.schedule_for("2026-W45", s)
    from core.config import now_local

    assert now_local(s, t["post"]).strftime("%a %H:%M %Z") == "Sun 20:00 EST"
    assert now_local(s, t["auto_start"]).strftime("%a %H:%M %Z") == "Mon 08:00 EST"
    assert now_local(s, t["deadline"]).strftime("%a %H:%M %Z") == "Tue 20:00 EST"


# ---------------------------------------------------------------- prepare

def test_prepare_writes_facts_a_draft_and_a_signed_plan_due(tmp_path, posts, monkeypatch):
    monkeypatch.setenv("BOTSY_EVENT_KEY", "k" * 40)
    s = ctx(tmp_path)
    three_niches(s)
    res = plan.prepare(s, now=SUN_8PM + M)
    assert res["ok"] and res["week"] == WEEK and res["status"] == "waiting"
    d = tmp_path / "data" / "plans" / WEEK
    facts = json.loads((d / "facts.json").read_text())
    draft = json.loads((d / "plan.json").read_text())
    due = json.loads((d / "plan_due.json").read_text())
    assert facts["schema"] == "plan-facts/v1" and facts["money"]["cap"] == 25
    assert {b["brief_id"] for b in facts["briefs"]} >= {"coloring_pages-2026-10-24-01", "ttrpg-2026-10-24-01"}
    assert facts["scale_ups"] == {"allowed": False, "reason": "no_winners_yet"}
    assert facts["kdp"]["built"] is False and facts["approvals"]["approvals_before_auto"] == 4
    # Each niche's best brief first, then the rest by opportunity; every new pack size.
    assert [ln["brief_id"] for ln in draft["lines"][:3]] == ["coloring_pages-2026-10-24-01",
                                                             "stationery-2026-10-24-01", "ttrpg-2026-10-24-01"]
    assert all(ln["sizes"] == [5, 20] for ln in draft["lines"]) and draft["packs"] == 12
    assert draft["cost_usd"] <= draft["budget_usd"] == 10.0      # 1 plan week left in October: min($25, 40% of $25)
    o = orders.load(res["order_id"], s)
    assert o["kind"] == "weekly_plan" and o["created_by"] == "botsy" and o["status"] in plan.WAITING
    assert plan._ok_reason(o) == "first_plans" and o["payload"]["kdp"] == []
    assert events.verify(due) and due["kind"] == "plan_due" and due["draft_order_id"] == o["id"]
    assert set(due) <= {"schema", "kind", "ts", "plan_week", "draft_order_id", "draft_status", "needs_approval",
                        "approval_reason", "packs", "cost_usd", "money", "capacity_reasons", "kdp_slots_left",
                        "briefs", "facts_digest", "deadline", "auto_start", "max_revisions", "reason", "sig"}
    assert due["needs_approval"] is True and due["approval_reason"] == "first_plans"
    assert [k for k, _ in posts] == ["plan_posted"] and posts.urgent == [False]      # quiet hours apply
    text = posts[0][1]
    assert "Plan 1 of the first 4: it waits for your approval" in text and "Tue 27 Oct 20:00 New York time" in text
    assert "Approve: botsy service plan approve (or tell Hermes)" in text
    assert "code" not in text.lower()
    assert "'axolotl garden' in 5 and 20 pages" in posts[0][1]
    assert journal(s, "plan_due") and journal(s, "plan_posted")
    e = entry(s)
    assert e["needs_ok"] and e["ok_reason"] == "first_plans" and "needs_code" not in e
    assert e["themes"]["ttrpg-2026-10-24-01"] == "hex crawl journal"


def test_prepare_twice_makes_one_plan(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    first = plan.prepare(s, now=SUN_8PM + M)
    again = plan.prepare(s, now=SUN_8PM + 5 * M)
    assert again["skipped"] == "already_prepared" and again["order_id"] == first["order_id"]
    assert len(orders.list_orders(s, week=WEEK)) == 1 and len(posts) == 1


def test_the_draft_leaves_out_made_packs_cooldown_old_briefs_and_what_does_not_fit(tmp_path, posts):
    s = ctx(tmp_path, plan={"max_packs": 3})
    write_briefs(s, "stationery", ["weekly reset", "habit grid", "focus blocks", "brain dump"])
    write_briefs(s, "coloring_pages", ["old theme"], day="2026-10-10")          # 16 days old on Monday
    make_item(s, "stationery-2026-10-24-01", "stationery", "weekly reset", 20)   # that pack is made already
    make_item(s, "stationery-2026-09-30-07", "stationery", "Habit Grid", 5, created="2026-10-01")  # cooldown, 5 only
    res = plan.prepare(s, now=SUN_8PM + M)
    draft = json.loads((tmp_path / "data" / "plans" / WEEK / "plan.json").read_text())
    lines = {ln["brief_id"]: ln["sizes"] for ln in draft["lines"]}
    assert lines["stationery-2026-10-24-01"] == [5]          # 20 already made
    assert lines["stationery-2026-10-24-02"] == [20]         # 5 blocked by the theme cooldown
    assert lines["stationery-2026-10-24-03"] == [5]          # one slot left: the line's smallest size
    assert sum(len(v) for v in lines.values()) == 3 == res["packs"] == draft["packs"]
    skipped = {x["brief_id"]: x["reason"] for x in draft["skipped"]}
    assert skipped["coloring_pages-2026-10-10-01"] == "brief_too_old"
    assert skipped["stationery-2026-10-24-04"] == "too_many_packs"


def test_the_draft_fits_this_weeks_share_of_the_money(tmp_path, posts):
    s = ctx(tmp_path)
    write_briefs(s, "coloring_pages", ["axolotl garden", "cozy mushrooms", "sea turtles", "tiny houses"])
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=int(20 * 250_000),
                 settings=s, now=SUN_8PM)                                      # $20 of $25 spent
    plan.prepare(s, now=SUN_8PM + M)
    draft = json.loads((tmp_path / "data" / "plans" / WEEK / "plan.json").read_text())
    assert draft["budget_usd"] == pytest.approx(5.0) and draft["cost_usd"] <= 5.0
    assert "over_budget" in {x["reason"] for x in draft["skipped"]}


def test_a_plan_starting_next_month_uses_next_months_weeks(tmp_path, posts):
    s = ctx(tmp_path)
    write_briefs(s, "stationery", ["weekly reset"], day="2026-10-31")
    sunday = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)                         # Sun 1 Nov 20:00 EST
    res = plan.prepare(s, now=sunday + M)
    facts = json.loads((tmp_path / "data" / "plans" / "2026-W45" / "facts.json").read_text())
    assert res["week"] == "2026-W45" and facts["money"]["month"] == "2026-11"
    assert facts["money"]["weeks_left"] == 5 and facts["money"]["budget_usd"] == pytest.approx(5.0)


def test_a_late_run_after_the_server_was_off_is_skipped(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    res = plan.prepare(s, now=SUN_8PM + timedelta(hours=24, minutes=30))
    assert res["status"] == "missed" and orders.list_orders(s) == []
    assert posts[0][0] == "plan_missed" and "This week's plan was missed" in posts[0][1]
    assert plan.for_tick(s, now=SUN_8PM + timedelta(hours=26))["mode"] == "normal"
    # Still inside the day of the "No plan this week" notice, but the missed message said it already.
    assert plan.check(s, now=SUN_8PM + timedelta(hours=24, minutes=35))["actions"] == [] and len(posts) == 1
    # 23 hours late still runs.
    s2 = ctx(tmp_path / "b")
    three_niches(s2)
    assert plan.prepare(s2, now=SUN_8PM + timedelta(hours=23))["status"] == "waiting"


def test_a_late_plan_by_hand_gets_a_fair_deadline(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    late = SUN_8PM + timedelta(days=2)                                        # Tue 20:00
    res = plan.prepare(s, now=late, force=True)
    assert res["status"] == "waiting"
    t = entry(s)["times"]
    assert datetime.fromisoformat(t["deadline"]) >= late + timedelta(hours=48)
    assert t["reminders"] == []
    assert plan.check(s, now=late + timedelta(hours=1))["actions"] == []        # not expired at once


def test_no_briefs_means_no_plan_and_a_notice_at_nine(tmp_path, posts):
    s = ctx(tmp_path)
    res = plan.prepare(s, now=SUN_8PM + M)
    assert res["status"] == "none" and res["reason"] == "no_briefs"
    assert "could not make a plan" in posts[-1][1]
    assert plan.check(s, now=SUN_8PM + 30 * M)["actions"] == []                # 20:30: not yet
    assert plan.check(s, now=SUN_8PM + 65 * M)["actions"] == [f"{WEEK}: no plan"]
    assert posts[-1] == ("no_plan", "No plan this week. Botsy keeps running on its normal rules.")
    assert plan.check(s, now=SUN_8PM + 125 * M)["actions"] == []               # once
    assert plan.for_tick(s, now=SUN_8PM + 3 * 60 * M)["mode"] == "normal"


def test_without_the_limits_file_there_is_no_plan(tmp_path, posts):
    s = ctx(tmp_path, limits_doc="MISSING")
    three_niches(s)
    res = plan.prepare(s, now=SUN_8PM + M)
    assert res["status"] == "none" and res["reason"] == "limits_unavailable"
    assert plan.for_tick(s, now=MON_8AM)["mode"] == "normal"


def test_plans_switched_off(tmp_path, posts):
    s = ctx(tmp_path, plan={"enabled": False})
    three_niches(s)
    assert plan.prepare(s, now=SUN_8PM + M)["skipped"] == "plans_disabled"
    assert plan.check(s, now=MON_8AM + 5 * M)["skipped"] == "plans_disabled"
    tp = plan.for_tick(s, now=MON_8AM)
    assert tp["mode"] == "normal" and "switched off" in tp["note"] and posts == []


# ---------------------------------------------------------------- the first month

def test_first_month_reminders_plain_approval_and_count(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    plan.prepare(s, now=SUN_8PM + M)
    assert plan.check(s, now=MON_8AM - 55 * M)["actions"] == []               # Mon 07:05
    assert plan.check(s, now=MON_8AM + 5 * M)["actions"] == [f"{WEEK}: reminder 1"]
    assert plan.check(s, now=MON_8AM + 65 * M)["actions"] == []
    assert plan.check(s, now=MON_7PM + 5 * M)["actions"] == [f"{WEEK}: reminder 2"]
    assert "expires Tue 27 Oct 20:00" in posts[-1][1]
    tp = plan.for_tick(s, now=MON_7PM + 6 * M)
    assert tp["mode"] == "wait" and tp["lines"] == [] and "waits for your approval" in tp["note"]

    checks = Checks()
    done = plan.approve(s, now=MON_7PM + 12 * M, run_check=checks)
    assert done["ok"] and done["step"] == "started" and checks.calls == 1        # work begins that night
    st = plan.load_state(s)
    assert st["approved_count"] == 1 and st["plans"][WEEK]["status"] == "running"
    # A former code action: posted at once (urgent), with who asked and how to undo it.
    assert posts[-1][0] == "plan_approved" and posts.urgent[-1] is True
    assert "asked by: user" in posts[-1][1] and "Undo: botsy service plan hold" in posts[-1][1]
    j = journal(s, "plan_approved")[-1]
    assert j["by"] == "user" and j["plan_week"] == WEEK and j["reason"] == "first_plans"
    assert journal(s, "plan_started")[-1]["how"] == "approved"
    assert not any(re.search(r"\b\d{6}\b", t) for _, t in posts)                # no code anywhere
    assert not any(re.search(r"\b\d{6}\b", json.dumps(r)) for r in journal(s, "plan_approved"))
    tp = plan.for_tick(s, now=MON_7PM + 13 * M)
    assert tp["mode"] == "plan" and len(tp["lines"]) == 6
    assert all(ln["origin"] == "plan" and ln["order_id"] == done["order_id"] for ln in tp["lines"])
    # The hourly check after that neither reminds nor starts anything again.
    assert plan.check(s, now=MON_7PM + 65 * M, run_check=checks)["actions"] == []
    assert checks.calls == 1 and plan.check(s, now=TUE_8PM + 5 * M)["actions"] == []    # no expiry once running


def test_an_unapproved_plan_expires_tuesday_at_eight(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    plan.prepare(s, now=SUN_8PM + M)
    # No check ran on Monday (the server was off): the latest reminder still goes out, once.
    assert plan.check(s, now=TUE_8PM - 55 * M)["actions"] == [f"{WEEK}: reminder 2"]
    assert plan.check(s, now=TUE_8PM - 54 * M)["actions"] == []
    assert plan.check(s, now=TUE_8PM + 5 * M)["actions"] == [f"{WEEK}: expired"]
    e = entry(s)
    assert e["status"] == "expired" and orders.current_plan(s, WEEK) is None
    assert posts[-1][0] == "plan_expired" and "normal rules" in posts[-1][1]
    assert plan.load_state(s)["approved_count"] == 0                             # expired plans do not count
    tp = plan.for_tick(s, now=TUE_8PM + 6 * M)
    assert tp["mode"] == "normal" and "expired" in tp["note"]
    assert plan.approve(s, now=TUE_8PM + 7 * M)["reason"] == "no_plan"
    assert plan.check(s, now=TUE_8PM + 65 * M)["actions"] == []                  # no "no plan" notice on top


def test_hold_in_the_first_month_keeps_the_deadline_and_approve_still_works(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    plan.prepare(s, now=SUN_8PM + M)
    held = plan.hold(s, now=SUN_8PM + 10 * M)
    assert held["ok"] and entry(s)["status"] == "held"
    assert plan.hold(s, now=SUN_8PM + 11 * M)["reason"] == "already_held"
    assert plan.check(s, now=MON_8AM + 5 * M)["actions"] == [f"{WEEK}: reminder 1"]
    assert "It is on hold." in posts[-1][1]
    assert plan.approve(s, now=MON_8AM + 11 * M, run_check=Checks())["ok"]
    o = orders.current_plan(s, WEEK)
    assert o["status"] == "applied" and "held" not in o and entry(s)["status"] == "running"


def test_the_hold_window_does_not_save_an_expired_plan(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    plan.prepare(s, now=SUN_8PM + M)
    plan.hold(s, now=SUN_8PM + 10 * M)
    assert plan.check(s, now=TUE_8PM + 5 * M)["actions"] == [f"{WEEK}: expired"]


def test_an_approval_after_the_deadline_never_starts_the_plan(tmp_path, posts):
    """Fail closed: the hourly check expires the plan at :05, but an approval in between
    (or while the server was off at the deadline) does not start it either."""
    s = ctx(tmp_path)
    three_niches(s)
    plan.prepare(s, now=SUN_8PM + M)
    checks = Checks()
    late = plan.approve(s, now=TUE_8PM + 2 * M, run_check=checks)                     # Tue 20:02
    assert not late["ok"] and late["reason"] == "plan_expired" and checks.calls == 0
    assert orders.current_plan(s, WEEK) is None and entry(s)["status"] == "expired"
    assert posts[-1][0] == "plan_expired" and plan.load_state(s)["approved_count"] == 0
    assert plan.deadline_for(WEEK, s) == TUE_8PM
    assert plan.check(s, now=TUE_8PM + 5 * M)["actions"] == []                         # said once


def test_a_first_check_that_fails_is_not_retried_every_hour(tmp_path, posts):
    from service.lock import LockTimeout

    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 4)
    plan.prepare(s, now=SUN_8PM + M)
    calls = []

    def broken(settings):
        calls.append(1)
        raise RuntimeError("tick broke")

    got = plan.check(s, now=MON_8AM + 5 * M, run_check=broken)
    assert got["first_check"] == {"ran": False, "reason": "failed"} and entry(s)["first_check"] == "gave_up"
    assert "first_check" not in plan.check(s, now=MON_8AM + 65 * M, run_check=broken) and len(calls) == 1

    # A busy main lock: the next hourly checks try again, 3 times in all.
    s2 = ctx(tmp_path / "busy")
    three_niches(s2)
    set_approved(s2, 4)
    plan.prepare(s2, now=SUN_8PM + M)

    def busy(settings):
        raise LockTimeout("busy")

    tries = [plan.check(s2, now=MON_8AM + (5 + 60 * i) * M, run_check=busy).get("first_check") for i in range(4)]
    assert tries[:3] == [{"ran": False, "reason": "busy"}] * 3 and tries[3] is None
    assert entry(s2)["first_check"] == "gave_up"


def test_plan_due_goes_only_to_a_local_address_without_redirects(tmp_path, posts):
    sent = []

    class Session:
        def post(self, url, *, json=None, timeout=None, allow_redirects=True):
            sent.append((url, allow_redirects))
            return type("R", (), {"status_code": 204})()

    for url, ok in [("http://127.0.0.1:8644/webhooks/botsy", True), ("http://[::1]:8644/x", True),
                    ("http://localhost:8644/x", False), ("http://127.0.0.1/x", False),
                    ("http://u:p@127.0.0.1:8644/x", False), ("https://example.com:443/x", False),
                    ("file:///etc/passwd", False)]:
        s = ctx(tmp_path / str(len(sent)) / "x", plan={"hermes_events_url": url})
        assert plan._deliver(s, {"kind": "plan_due"}, session=Session()) is ok, url
    assert sent == [("http://127.0.0.1:8644/webhooks/botsy", False), ("http://[::1]:8644/x", False)]


def test_four_approvals_then_plans_start_by_themselves(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 3)
    plan.prepare(s, now=SUN_8PM + M)
    assert "Plan 4 of the first 4" in posts[-1][1]
    plan.approve(s, now=SUN_8PM + 6 * M, run_check=Checks())
    assert plan.load_state(s)["approved_count"] == 4
    # Next week's plan does not wait for you.
    write_briefs(s, "stationery", ["meal planner"], day="2026-10-31")
    res = plan.prepare(s, now=datetime(2026, 11, 2, 1, 1, tzinfo=UTC))       # Sun 1 Nov 20:01 EST
    assert res["week"] == "2026-W45" and res["status"] == "waiting" and not entry(s, "2026-W45")["needs_ok"]
    assert "It starts by itself on Mon 02 Nov 08:00 New York time" in posts[-1][1]


# ---------------------------------------------------------------- from plan 5

def test_auto_start_monday_at_eight(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 4)
    res = plan.prepare(s, now=SUN_8PM + M)
    assert orders.load(res["order_id"], s)["status"] == "accepted"
    checks = Checks()
    assert plan.check(s, now=MON_8AM - 55 * M, run_check=checks)["actions"] == []      # 07:05: not yet, no reminder
    tp = plan.for_tick(s, now=MON_8AM - 50 * M)
    assert tp["mode"] == "wait" and "starts later" in tp["note"]
    got = plan.check(s, now=MON_8AM + 5 * M, run_check=checks)
    assert got["actions"] == [f"{WEEK}: started"] and got["first_check"]["ran"] and checks.calls == 1
    assert posts[-1][0] == "plan_started" and "by itself" in posts[-1][1]
    assert entry(s)["status"] == "running" and plan.load_state(s)["approved_count"] == 5
    assert plan.check(s, now=MON_8AM + 65 * M, run_check=checks)["actions"] == [] and checks.calls == 1


def test_a_held_plan_waits_and_approving_it_is_level_2(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 6)
    plan.prepare(s, now=SUN_8PM + M)
    plan.hold(s, now=SUN_8PM + 30 * M)
    assert plan.check(s, now=MON_8AM + 5 * M)["actions"] == []
    assert entry(s)["status"] == "held" and plan.check(s, now=TUE_8PM + 5 * M)["actions"] == []   # no expiry
    checks = Checks()
    res = plan.approve(s, now=TUE_8PM + 10 * M, run_check=checks)
    assert res["ok"] and res["step"] == "started" and checks.calls == 1
    assert entry(s)["status"] == "running"
    assert posts[-1][0] == "plan_started" and posts.urgent[-1] is False      # not a Level 3 approval
    assert orders.counts(s, now=TUE_8PM + 10 * M)["level3_last_24h"] == 0


def test_start_now_starts_at_once_and_approves_a_waiting_plan(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 5)
    plan.prepare(s, now=SUN_8PM + M)
    checks = Checks()
    res = plan.start_now(s, now=SUN_8PM + 60 * M, run_check=checks)
    assert res["ok"] and res["step"] == "started" and checks.calls == 1
    assert plan.for_tick(s, now=SUN_8PM + 61 * M)["mode"] == "plan"           # Sunday night, next week's plan runs
    # First month: start-now on a plan that waits for you is an approval, with its guards.
    s2 = ctx(tmp_path / "b")
    three_niches(s2)
    plan.prepare(s2, now=SUN_8PM + M)
    res = plan.start_now(s2, now=SUN_8PM + 60 * M, run_check=Checks())
    assert res["ok"] and res["step"] == "started" and posts[-1][0] == "plan_approved" and posts.urgent[-1]
    assert orders.counts(s2, now=SUN_8PM + 60 * M)["level3_last_24h"] == 0    # D1: your plan approval: posted, not counted


def test_approve_on_a_plan_that_starts_by_itself_says_so(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 5)
    plan.prepare(s, now=SUN_8PM + M)
    res = plan.approve(s, now=SUN_8PM + 5 * M)
    assert res["ok"] and res["reason"] == "starts_by_itself"
    assert datetime.fromisoformat(res["start_at"]) == MON_8AM


def test_a_plan_over_ten_dollars_still_waits_for_approval(tmp_path, posts):
    s = ctx(tmp_path)
    write_briefs(s, "coloring_pages", ["axolotl garden", "cozy mushrooms", "sea turtles", "tiny houses"])
    set_approved(s, 9)
    hermes = {"schema": "order/v1", "kind": "weekly_plan", "created_by": "hermes",
              "payload": {"plan_week": WEEK, "lines": [
                  {"niche": "coloring_pages", "brief_id": f"coloring_pages-{DAY}-0{i}", "sizes": [5, 20]}
                  for i in range(1, 5)]}}
    res = plan.submit_order(hermes, s, now=SUN_8PM + 2 * M)                   # 4 x $3.15 = $12.60
    assert res["ok"] and plan._needs_ok(res) and plan._ok_reason(res["order"]) == "plan_over_auto_share"
    assert "more than the share of the cap" in posts[-1][1]
    assert plan.check(s, now=MON_8AM + 5 * M)["actions"] == [f"{WEEK}: reminder 1"]   # it waits: reminders
    assert plan.check(s, now=MON_8AM + 65 * M)["actions"] == []                         # never starts by itself
    assert entry(s)["status"] == "waiting"
    res = plan.approve(s, by="hermes", now=MON_8AM + 66 * M, run_check=Checks())        # Hermes, for you
    assert res["ok"] and posts[-1][0] == "plan_approved" and "asked by: hermes" in posts[-1][1]
    assert "it costs more than the share of the cap" in posts[-1][1]
    assert journal(s, "plan_approved")[-1]["by"] == "hermes"


def test_hermes_plan_replaces_botsys_draft_as_a_revision(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 5)
    draft = plan.prepare(s, now=SUN_8PM + M)
    hermes = {"schema": "order/v1", "kind": "weekly_plan", "created_by": "hermes",
              "payload": {"plan_week": WEEK, "lines": [{"niche": "ttrpg", "brief_id": f"ttrpg-{DAY}-01",
                                                        "sizes": [20]}]}}
    res = plan.submit_order(hermes, s, now=SUN_8PM + 5 * M)
    assert res["ok"] and res["converted"] and res["order"]["kind"] == "plan_revision"
    assert res["order"]["payload"]["replaces"] == draft["order_id"]
    assert entry(s)["order_id"] == res["order_id"] and orders.current_plan(s, WEEK)["created_by"] == "hermes"
    assert posts[-1][1].startswith(f"Plan {WEEK} checked: 1 pack, about $0.20, fits.")


def test_a_revision_starts_no_sooner_than_two_hours_after_it(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 5)
    plan.prepare(s, now=SUN_8PM + M)
    rev = {"kind": "weekly_plan", "created_by": "hermes",
           "payload": {"plan_week": WEEK, "lines": [{"niche": "stationery", "brief_id": f"stationery-{DAY}-02",
                                                     "sizes": [5]}]}}
    res = plan.submit_order(rev, s, now=MON_8AM - 10 * M)                     # Mon 07:50
    assert res["ok"] and not plan._needs_ok(res)
    assert plan.check(s, now=MON_8AM + 5 * M, run_check=Checks())["actions"] == []
    assert plan.check(s, now=MON_8AM + 105 * M, run_check=Checks())["actions"] == []     # 09:45
    assert plan.check(s, now=MON_8AM + 125 * M, run_check=Checks())["actions"] == [f"{WEEK}: started"]


def test_at_most_three_revisions(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 5)
    plan.prepare(s, now=SUN_8PM + M)
    for i in range(3):
        cur = orders.current_plan(s, WEEK)
        rev = {"kind": "plan_revision", "created_by": "hermes",
               "payload": {"plan_week": WEEK, "replaces": cur["id"],
                           "lines": [{"niche": "stationery", "brief_id": f"stationery-{DAY}-0{i + 1}", "sizes": [5]}]}}
        assert plan.submit_order(rev, s, now=SUN_8PM + (10 + i) * M)["ok"]
    cur = orders.current_plan(s, WEEK)
    fourth = plan.submit_order({"kind": "plan_revision", "created_by": "hermes",
                                "payload": {"plan_week": WEEK, "replaces": cur["id"],
                                            "lines": [{"niche": "ttrpg", "brief_id": f"ttrpg-{DAY}-01",
                                                       "sizes": [5]}]}}, s, now=SUN_8PM + 20 * M)
    assert not fourth["ok"] and "too_many_revisions" in fourth["reasons"]
    assert "was not accepted" in posts[-1][1]


@pytest.mark.parametrize("line, reason", [
    ({"niche": "stationery", "brief_id": "stationery-2026-10-24-09", "sizes": [5]}, "brief_unknown"),
    ({"niche": "stationery", "brief_id": "stationery-2026-10-24-01", "sizes": [12]}, "size_not_in_brief"),
    ({"niche": "coloring_pages", "brief_id": "coloring_pages-2026-10-10-01", "sizes": [5]}, "brief_too_old"),
])
def test_botsy_checks_the_briefs_of_hermes_plans(tmp_path, posts, line, reason):
    s = ctx(tmp_path)
    write_briefs(s, "stationery", ["weekly reset"])
    write_briefs(s, "coloring_pages", ["old theme"], day="2026-10-10")
    res = plan.submit_order({"kind": "weekly_plan", "created_by": "hermes",
                             "payload": {"plan_week": WEEK, "lines": [line]}}, s, now=SUN_8PM + M)
    assert not res["ok"] and res["stage"] == "content" and reason in res["reasons"]


def test_kdp_lines_are_refused_until_kdp_is_built(tmp_path, posts):
    s = ctx(tmp_path)
    write_briefs(s, "stationery", ["weekly reset"])
    res = plan.submit_order({"kind": "weekly_plan", "created_by": "hermes",
                             "payload": {"plan_week": WEEK,
                                         "lines": [{"niche": "stationery", "brief_id": f"stationery-{DAY}-01",
                                                    "sizes": [5]}],
                                         "kdp": [{"brief_id": f"stationery-{DAY}-01", "template": "planner"}]}},
                            s, now=SUN_8PM + M)
    assert not res["ok"] and res["reasons"] == ["kdp_not_built"]


# ---------------------------------------------------------------- again / auto

def test_again_makes_a_plan_not_started_yet_wait_for_approval(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 6)
    plan.prepare(s, now=SUN_8PM + M)
    res = plan.again(s, now=SUN_8PM + 20 * M)
    assert res["ok"] and res["mode"] == "approve" and res["plan"] == "revised"
    o = orders.current_plan(s, WEEK)
    assert o["status"] in plan.WAITING and plan._ok_reason(o) == "approve_each"
    assert plan.check(s, now=MON_8AM + 5 * M)["actions"] == [f"{WEEK}: reminder 1"]   # never starts by itself
    assert plan.effective_mode(plan.load_state(s), s) == "approve"
    assert journal(s, "plan_mode_changed")[-1]["mode_to"] == "approve"


def test_auto_needs_no_code_but_is_posted_at_once_and_journaled(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    ok = plan.auto(s, now=SUN_8PM - 58 * M)
    assert ok["ok"] and ok["step"] == "switched" and plan.load_state(s)["mode"] == "auto"
    assert posts[-1][0] == "plan_mode_changed" and posts.urgent[-1] is True
    assert "asked by: user" in posts[-1][1] and "0 of the first 4 plans had been approved" in posts[-1][1]
    assert "Undo: botsy service plan again" in posts[-1][1]
    j = journal(s, "plan_mode_changed")[-1]
    assert (j["mode_from"], j["mode_to"], j["by"], j["approved_plans"]) == ("approve", "auto", "user", 0)
    assert plan.auto(s, now=SUN_8PM - 57 * M)["reason"] == "already_auto"
    res = plan.prepare(s, now=SUN_8PM + M)                                   # 0 approvals, but automatic now
    assert not entry(s)["needs_ok"] and orders.load(res["order_id"], s)["status"] == "accepted"
    # After "again", going back to automatic is the same plain step (and counted again).
    plan.again(s, now=SUN_8PM + 10 * M)
    assert plan.auto(s, by="hermes", now=SUN_8PM + 11 * M)["step"] == "switched"
    assert orders.counts(s, now=SUN_8PM + 11 * M)["level3_last_24h"] == 1       # one shared counter
    rows = json.loads((tmp_path / "data" / "orders" / "counters.json").read_text())["events"]
    assert [r["by"] for r in rows if r["counter"] == "level3"] == ["hermes"]     # D1: only Hermes's counts


def test_level_3_approvals_have_a_daily_limit(tmp_path, posts):
    s = ctx(tmp_path, limits_doc="level3_per_day: 1\n")
    three_niches(s)
    plan.prepare(s, now=SUN_8PM + M)
    assert plan.auto(s, by="hermes", now=SUN_8PM - 23 * 60 * M)["ok"]         # 24 hours minus a bit before
    plan.again(s, now=SUN_8PM - 22 * 60 * M)
    res = plan.approve(s, by="hermes", now=SUN_8PM + 2 * M, run_check=Checks())
    assert not res["ok"] and res["reason"] == "level3_daily_limit" and entry(s)["status"] == "waiting"
    assert plan.auto(s, by="hermes", now=SUN_8PM + 3 * M)["reason"] == "level3_daily_limit"
    assert plan.approve(s, by="hermes", now=SUN_8PM + 2 * 60 * M, run_check=Checks())["ok"]   # 24 h later: room


def test_your_plan_commands_are_not_limited_by_the_approval_count(tmp_path, posts):
    """D1 (user, 2026-10-21): only approvals that lead to posting on Etsy or KDP count when you
    give them; your plan approve and plan auto are still posted at once and journaled."""
    s = ctx(tmp_path, limits_doc="level3_per_day: 1\n")
    three_niches(s)
    plan.prepare(s, now=SUN_8PM + M)
    assert orders.level3_take(s, "review_approve", "user", now=SUN_8PM + M)           # the one approval used
    assert not orders.level3_room(s, now=SUN_8PM + 2 * M)
    res = plan.approve(s, now=SUN_8PM + 2 * M, run_check=Checks())
    assert res["ok"] and res["step"] == "started" and posts[-1][0] == "plan_approved" and posts.urgent[-1]
    assert journal(s, "plan_approved")[-1]["by"] == "user"
    assert plan.again(s, now=SUN_8PM + 3 * M)["ok"]
    assert plan.auto(s, now=SUN_8PM + 4 * M)["step"] == "switched"
    assert orders.counts(s, now=SUN_8PM + 5 * M)["level3_last_24h"] == 1


def test_only_the_user_or_hermes_may_ask(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    plan.prepare(s, now=SUN_8PM + M)
    for fn in (plan.approve, plan.auto, plan.hold, plan.start_now, plan.again):
        with pytest.raises(ValueError):
            fn(s, by="botsy", now=SUN_8PM + 2 * M)
    assert entry(s)["status"] == "waiting" and orders.counts(s, now=SUN_8PM + 2 * M)["level3_last_24h"] == 0


def test_urgent_plan_messages_go_out_in_quiet_hours_and_with_notify_off(tmp_path, monkeypatch):
    from service import notify

    sent = []
    monkeypatch.setattr(notify, "send", lambda text, **kw: sent.append(kw) or True)
    s = ctx(tmp_path, plan={"notify": False})
    three_niches(s)
    plan.prepare(s, now=SUN_8PM + M)
    assert sent == []                                                        # plan.notify false: not posted
    plan.approve(s, now=SUN_8PM + 2 * 60 * M + 30 * M, run_check=Checks())   # Sun 22:30 New York: quiet hours
    assert [kw["kinds"] for kw in sent] == [["plan_approved"]] and sent[0]["urgent"] is True


# ---------------------------------------------------------------- what the tick makes

def test_finish_last_weeks_lines_while_this_weeks_plan_waits(tmp_path, posts):
    s = ctx(tmp_path, plan={"brief_max_age_days": 14})          # with the default 7, Monday's lines would be dropped
    three_niches(s)
    set_approved(s, 5)
    plan.prepare(s, now=SUN_8PM + M)
    plan.start_now(s, now=SUN_8PM + 2 * M, run_check=Checks())               # W44 runs
    st = plan.load_state(s)
    st["mode"] = "approve"                                                    # W45 will wait for approval
    plan._save_state(s, st)
    write_briefs(s, "stationery", ["meal planner"], day="2026-10-31")
    nxt = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)                             # Sun 1 Nov 20:00 EST
    assert plan.prepare(s, now=nxt + M)["status"] == "waiting"
    monday = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)                         # Mon 2 Nov 10:00 EST
    tp = plan.for_tick(s, now=monday)
    assert tp["mode"] == "finish" and tp["week"] == WEEK and tp["lines"]
    assert "finishing last week's plan" in tp["note"]
    # More than 8 days after last week's plan was posted: nothing new while this week's waits.
    tp = plan.for_tick(s, now=SUN_8PM + timedelta(days=8, hours=1))
    assert tp["mode"] == "wait" and "more than 8 days old" in tp["note"]


def test_old_briefs_are_dropped_once_and_made_packs_still_go_on(tmp_path, posts):
    s = ctx(tmp_path)
    write_briefs(s, "stationery", ["weekly reset", "habit grid"], day="2026-10-19")   # 7 days old on Monday
    set_approved(s, 5)
    plan.prepare(s, now=SUN_8PM + M)
    plan.start_now(s, now=SUN_8PM + 2 * M, run_check=Checks())
    make_item(s, "stationery-2026-10-19-01", "stationery", "weekly reset", 20)
    tuesday = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)                       # 8 days old
    tp = plan.for_tick(s, now=tuesday)
    assert {d["brief_id"] for d in tp["dropped"]} == {"stationery-2026-10-19-01", "stationery-2026-10-19-02"}
    assert [(ln["brief_id"], ln["sizes"]) for ln in tp["lines"]] == [("stationery-2026-10-19-01", [20])]
    new = plan.record_run(s, tp, [], now=tuesday)
    assert len(new) == 2 and len(journal(s, "plan_line_dropped")) == 2
    again = plan.for_tick(s, now=tuesday + M)
    assert again["dropped"] == [] and plan.record_run(s, again, [], now=tuesday + M) == []
    assert entry(s)["dropped"]["stationery-2026-10-19-02"] == "brief_too_old"


def test_pipeline_drops_are_recorded_and_a_finished_plan_is_done(tmp_path, posts):
    s = ctx(tmp_path)
    write_briefs(s, "stationery", ["weekly reset", "habit grid"])
    set_approved(s, 5)
    plan.prepare(s, now=SUN_8PM + M)
    plan.start_now(s, now=SUN_8PM + 2 * M, run_check=Checks())
    tp = plan.for_tick(s, now=MON_8AM)
    order_id = tp["order_id"]
    for size in (5, 20):
        make_item(s, f"stationery-{DAY}-01", "stationery", "weekly reset", size)
    summary = {"plan": {"lines": [{"order_id": order_id, "brief_id": f"stationery-{DAY}-02",
                                   "dropped": "brief_changed"}]}}
    new = plan.record_run(s, tp, [summary], now=MON_8AM)
    assert new == [{"week": WEEK, "brief_id": f"stationery-{DAY}-02", "reason": "brief_changed"}]
    assert entry(s)["status"] == "done" and journal(s, "plan_done")
    p = plan.summary(s, now=MON_8AM)["progress"]
    assert p["packs_made"] == 2 and p["lines_dropped"] == 1 and p["lines_done"] == 2


def test_hold_on_a_running_plan_stops_new_lines(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 5)
    plan.prepare(s, now=SUN_8PM + M)
    plan.check(s, now=MON_8AM + 5 * M, run_check=Checks())
    plan.hold(s, now=MON_8AM + 30 * M)
    tp = plan.for_tick(s, now=MON_8AM + 31 * M)
    assert tp["mode"] == "wait" and tp["lines"] == [] and "on hold" in tp["note"]
    res = plan.approve(s, now=MON_8AM + 40 * M, run_check=Checks())
    assert res["ok"] and res["step"] == "started"
    assert plan.for_tick(s, now=MON_8AM + 41 * M)["mode"] == "plan"


def test_hermes_cancelling_the_plan_means_normal_rules(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 5)
    res = plan.prepare(s, now=SUN_8PM + M)
    orders.submit({"kind": "order_cancel", "created_by": "hermes", "payload": {"order_id": res["order_id"]}}, s,
                  now=SUN_8PM + 5 * M)
    assert plan.for_tick(s, now=MON_8AM)["mode"] == "normal"
    plan.check(s, now=MON_8AM + 5 * M)
    assert entry(s)["status"] == "cancelled"


# ---------------------------------------------------------------- status, daily log, CLI

def test_summary_status_txt_and_daily_log_show_the_plan(tmp_path, posts):
    from service import digest, status

    s = ctx(tmp_path)
    three_niches(s)
    plan.prepare(s, now=SUN_8PM + M)
    summ = plan.summary(s, now=SUN_8PM + 4 * M)
    assert summ["status"] == "waiting" and summ["needs_approval"] and summ["approval_reason"] == "first_plans"
    assert "code" not in summ
    text = "\n".join(summ["lines"])
    assert "waiting for your approval" in text and "Deadline: Tue 27 Oct 20:00 New York time" in text
    assert "code" not in text.lower()
    # Sunday night, the plan for the coming week waits: the briefs wait for it (no normal rules).
    assert "This check: plan 2026-W44 waits for your approval; nothing new is made until it starts" in text
    outbox = tmp_path / "inbox" / "outbox"
    status.set_plan(outbox, summ)
    txt = (outbox / "status.txt").read_bytes().decode("utf-8")
    assert "\r\nWeekly plan\r\n" in txt and "Plan 2026-W44: waiting for your approval" in txt
    real = datetime.now(UTC)                       # the journal stamps events with the real clock
    log = digest.build(real - timedelta(hours=1), real + M, s)
    assert "\r\nWeekly plan\r\n" in log and f"{WEEK}: posted" in log and "  Weekly plan: Sun" in log
    assert "Plan 2026-W44: waiting for your approval" in log


def test_the_cli(tmp_path, posts, capsys, monkeypatch):
    import yaml

    from service import __main__ as cli

    s = ctx(tmp_path)
    three_niches(s)
    cfg = tmp_path / "plan-settings.yaml"
    cfg.write_text(yaml.safe_dump(s), encoding="utf-8")
    assert cli.main(["plan", "status", "--settings", str(cfg)]) == 0
    assert "Weekly plan" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:                                      # no --code any more
        cli.main(["plan", "approve", "--code", "123456", "--settings", str(cfg)])
    assert e.value.code == 2
    assert cli.main(["plan", "hold", "--force", "--settings", str(cfg)]) == 2
    assert cli.main(["plan", "approve", "--settings", str(cfg)]) == 1        # no plan yet
    assert "Not done: There is no plan in force." in capsys.readouterr().out
    assert "plan" in cli.NO_LOCK                                              # never waits for a running check


def test_approval_goes_through_orders_approve(tmp_path, posts, monkeypatch):
    """The order/v1 call without codes: orders.approve(order, settings, by=, now=). The plan
    service passes who asked; orders counts the approval and checks the daily limit."""
    s = ctx(tmp_path)
    three_niches(s)
    res = plan.prepare(s, now=SUN_8PM + M)
    calls = []

    def fake_approve(order_id, settings, *, by, now):
        calls.append((order_id, by))
        return {"ok": False, "stage": "limits", "reasons": ["level3_daily_limit"], "order_id": order_id}

    monkeypatch.setattr(orders, "approve", fake_approve)
    got = plan.approve(s, by="hermes", now=SUN_8PM + 2 * M, run_check=Checks())
    assert calls == [(res["order_id"], "hermes")]
    assert not got["ok"] and got["reason"] == "level3_daily_limit" and entry(s)["status"] == "waiting"
    assert not journal(s, "plan_approved")


def test_approve_binds_to_the_named_order_and_can_leave_the_first_check_to_the_hourly_check(tmp_path, posts):
    """botsy-control calls plan.approve(settings, by="hermes", order_id=..., first_check=False):
    another order id is refused untouched, and no check runs in the act server's process."""
    s = ctx(tmp_path)
    three_niches(s)
    res = plan.prepare(s, now=SUN_8PM + M)
    wrong = plan.approve(s, by="hermes", now=SUN_8PM + 2 * M, order_id="pl-20261001-0001", first_check=False)
    assert not wrong["ok"] and wrong["reason"] == "not_waiting" and entry(s)["status"] == "waiting"
    checks = Checks()
    got = plan.approve(s, by="hermes", now=SUN_8PM + 3 * M, order_id=res["order_id"], first_check=False,
                       run_check=checks)
    assert got["ok"] and got["step"] == "started" and checks.calls == 0
    assert got["first_check"] == {"ran": False, "reason": "next_hourly_check"}
    assert entry(s)["first_check"] == "pending" and posts[-1][0] == "plan_approved" and posts.urgent[-1]
    later = Checks()
    plan.check(s, now=SUN_8PM + 65 * M, run_check=later)                     # the hourly :05 check runs it
    assert later.calls == 1


# ---------------------------------------------------------------- D3: your Hold sticks (user, 2026-10-21)

def test_your_hold_survives_monday_and_hermes(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 5)
    res = plan.prepare(s, now=SUN_8PM + M)
    assert plan.hold(s, now=SUN_8PM + 10 * M)["ok"]
    e = entry(s)
    assert e["status"] == "held" and e["held_by"] == "user"
    # The hourly check at Monday 08:05 (and later) never starts it.
    checks = Checks()
    for hour in range(0, 4):
        plan.check(s, now=MON_8AM + 5 * M + hour * 60 * M, run_check=checks)
    assert entry(s)["status"] == "held" and checks.calls == 0
    assert orders.current_plan(s, WEEK)["status"] == "accepted"
    # Hermes's Start now, approve and revision are all refused.
    assert plan.start_now(s, by="hermes", now=MON_8AM + 5 * 60 * M)["reason"] == "held_by_user"
    assert plan.approve(s, by="hermes", now=MON_8AM + 5 * 60 * M)["reason"] == "held_by_user"
    o = orders.current_plan(s, WEEK)
    rev = {"schema": "order/v1", "kind": "weekly_plan", "created_by": "hermes", "payload": o["payload"]}
    got = plan.submit_order(rev, s, now=MON_8AM + 5 * 60 * M)
    assert not got.get("ok") and "held_by_user" in (got.get("reasons") or [got.get("reason")])
    assert entry(s)["status"] == "held" and orders.current_plan(s, WEEK)["id"] == res["order_id"]
    # You lift it.
    out = plan.approve(s, now=MON_8AM + 6 * 60 * M, run_check=Checks())
    assert out["ok"] and out["step"] == "started" and entry(s)["status"] == "running"


def test_hermes_hold_can_be_lifted_by_hermes(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 5)
    plan.prepare(s, now=SUN_8PM + M)
    assert plan.hold(s, by="hermes", now=SUN_8PM + 10 * M)["ok"] and entry(s)["held_by"] == "hermes"
    res = plan.start_now(s, by="hermes", now=SUN_8PM + 20 * M, run_check=Checks())
    assert res["ok"] and res["step"] == "started"


def test_hermes_cannot_lift_your_hold_on_a_running_plan(tmp_path, posts):
    s = ctx(tmp_path)
    three_niches(s)
    set_approved(s, 5)
    plan.prepare(s, now=SUN_8PM + M)
    plan.check(s, now=MON_8AM + 5 * M, run_check=Checks())
    assert entry(s)["status"] == "running"
    plan.hold(s, now=MON_8AM + 30 * M)
    assert plan.approve(s, by="hermes", now=MON_8AM + 40 * M)["reason"] == "held_by_user"
    assert plan.start_now(s, by="hermes", now=MON_8AM + 40 * M)["reason"] == "held_by_user"
    assert plan.for_tick(s, now=MON_8AM + 41 * M)["mode"] == "wait"
    assert plan.approve(s, now=MON_8AM + 50 * M, run_check=Checks())["ok"]
    assert plan.for_tick(s, now=MON_8AM + 51 * M)["mode"] == "plan"
