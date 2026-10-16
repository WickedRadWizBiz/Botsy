"""service.digest: the overnight log from synthetic events and spend lines (all in tmp_path)."""
from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import spend
from service import digest

from ._support import make_settings

UNTIL = datetime(2026, 10, 16, 7, 7, tzinfo=timezone.utc)
SINCE = UNTIL - timedelta(hours=24)


def at(hours_before: float) -> str:
    return (UNTIL - timedelta(hours=hours_before)).isoformat(timespec="seconds")


def write_events(settings: dict, rows: list[dict]) -> None:
    path = Path(settings["service"]["events_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


EVENTS = [
    {"ts": at(30), "kind": "draft_created", "item_id": "old-item", "listing_id": 1, "title": "Too Old Title"},
    {"ts": at(20), "kind": "export_received", "tool": "everbee", "shape": "listing", "rows": 240},
    {"ts": at(20), "kind": "export_received", "tool": "erank", "shape": "keyword", "rows": 60},
    {"ts": at(20), "kind": "export_rejected", "name": "bank.csv",
     "reason": "it does not look like an EverBee or eRank export"},
    {"ts": at(19), "kind": "briefs_written", "niche": "stickers", "stage": "discovery", "count": 3},
    {"ts": at(19), "kind": "briefs_written", "niche": "coloring_pages", "stage": "discovery", "count": 0},
    {"ts": at(18), "kind": "item_generated", "item_id": "2026-10-15-stickers-frogs-aaaa", "niche": "stickers",
     "theme": "frogs"},
    {"ts": at(18), "kind": "item_generated", "item_id": "2026-10-15-stickers-cats-bbbb", "niche": "stickers",
     "theme": "cats"},
    {"ts": at(18), "kind": "qa_failed", "item_id": "2026-10-15-stickers-bad-dddd", "checks": ["pdf_blank_pages"]},
    {"ts": at(17), "kind": "listing_written", "item_id": "2026-10-15-stickers-frogs-aaaa",
     "title": "Frog Stickers Pack"},
    {"ts": at(17), "kind": "draft_created", "item_id": "2026-10-15-stickers-frogs-aaaa", "listing_id": 4321,
     "title": "Frog Stickers Pack", "dry_run": False},
    {"ts": at(17), "kind": "held_for_review", "item_id": "2026-10-15-stickers-cats-bbbb",
     "reasons": "needs review (flagged term)", "dry_run": False},
    {"ts": at(10), "kind": "spend_warning", "month_to_date": 20.5, "cap": 25.0, "pct": 0.82},
    {"ts": at(9), "kind": "error", "where": "research[stickers]", "message": "RuntimeError: boom"},
    {"ts": at(8), "kind": "error", "where": "research[stickers]", "message": "RuntimeError: boom"},
    {"ts": at(7), "kind": "notification_held", "kinds": ["draft_created", "held_for_review"],
     "text": "Botsy:\n1 new draft is waiting in Etsy: Frog Stickers Pack\n1 item is waiting for your review. "
             "On the server type:\n  botsy qa review approve x"},
    {"ts": at(-1), "kind": "error", "where": "future", "message": "after the window"},
]


@pytest.fixture
def settings(tmp_path):
    s, _ = make_settings(tmp_path, service={"live": True}, spend={"monthly_cap_usd": 25})
    write_events(s, EVENTS)
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", item_id="x", input_tokens=1_000_000,
                 settings=s, now=UNTIL - timedelta(hours=2))           # $4.00 in the window
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=500_000,
                 settings=s, now=datetime(2026, 10, 2, tzinfo=timezone.utc))   # $2.00 earlier this month
    return s


def body(text: str) -> str:
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")       # CRLF only
    return text.replace("\r\n", "\n")


def test_digest_lists_every_section(settings, monkeypatch):
    monkeypatch.setattr(spend, "month_of", lambda now=None: "2026-10")
    text = body(digest.build(SINCE, UNTIL, settings))
    assert text.startswith("Botsy daily log\n")
    assert "Mode: LIVE" in text
    assert "Received: 2 (1 from EverBee, 1 from eRank; 300 rows)" in text
    assert "Not used: 1" in text and "bank.csv: it does not look like an EverBee or eRank export" in text
    assert "Stage: discovery" in text and "Design briefs: stickers 3, coloring pages 0" in text
    assert "Made: 2" in text and "Listings written: 1" in text
    assert "Failed a quality check: 1" in text and "2026-10-15-stickers-bad-dddd: pdf_blank_pages" in text
    assert "New Etsy drafts: 1" in text and "Frog Stickers Pack" in text
    assert "Too Old Title" not in text and "after the window" not in text       # outside the window
    assert "Waiting for your review" in text
    assert "Approve: botsy qa review approve 2026-10-15-stickers-cats-bbbb\n" in text
    assert "botsy qa review list\n" in text
    assert "Last 24 hours: $4.00" in text
    assert "This month (2026-10): $6.00 of $25.00 cap (24%)" in text
    assert "Spending warning: $20.50 of $25.00 used this month (82%)." in text
    assert "research[stickers]: RuntimeError: boom (2 times)" in text
    held = text.split("held during quiet hours")[1].split("What runs next")[0]
    assert "  - 1 new draft is waiting in Etsy: Frog Stickers Pack\n" in held     # not the "Botsy:" line
    assert "  - 1 item is waiting for your review.\n" in held and "Botsy:" not in held
    assert "approve x" not in held                                               # indented details left out
    assert "What runs next" in text and "Daily check:" in text and "Weekly search list: Sun" in text
    assert max(len(line) for line in text.splitlines()) < 260


def test_test_mode_review_commands_say_dry_run(tmp_path):
    s, _ = make_settings(tmp_path)
    write_events(s, [{"ts": at(1), "kind": "held_for_review", "item_id": "2026-10-15-stickers-cats-bbbb",
                      "reasons": ["flagged term"], "dry_run": True},
                     {"ts": at(1), "kind": "draft_created", "item_id": "a", "listing_id": 1,
                      "title": "Practice Title", "dry_run": True}])
    text = body(digest.build(SINCE, UNTIL, s))
    assert "Mode: TEST" in text
    assert "Approve: botsy qa review approve 2026-10-15-stickers-cats-bbbb --dry-run" in text
    assert "botsy qa review list --dry-run" in text
    assert "Practice drafts (test mode, nothing sent to Etsy): 1" in text and "New Etsy drafts" not in text
    assert "(no monthly cap set)" in text


def test_quiet_day(tmp_path):
    s, _ = make_settings(tmp_path)
    text = body(digest.build(SINCE, UNTIL, s))
    assert "None received." in text and "Research did not run" in text and "No new drafts." in text
    assert "Problems and warnings\n  None." in text and "held during quiet hours" not in text


def test_stage_change_and_cap_reached(tmp_path, monkeypatch):
    s, _ = make_settings(tmp_path, live=True, spend={"monthly_cap_usd": 1})
    write_events(s, [{"ts": at(3), "kind": "stage_changed", "from": "discovery", "to": "refined", "drafts": 20},
                     {"ts": at(3), "kind": "briefs_written", "niche": "stickers", "stage": "refined", "count": 0},
                     {"ts": at(2), "kind": "budget_reached", "month_to_date": 1.2, "cap": 1.0}])
    monkeypatch.setattr(spend, "month_of", lambda now=None: "2026-10")
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=300_000,
                 settings=s, now=UNTIL - timedelta(hours=1))
    text = body(digest.build(SINCE, UNTIL, s))
    assert "Research stage changed from discovery to refined (after 20 Etsy drafts)." in text
    assert "Stage: refined" in text
    assert "Monthly spending cap reached: $1.20 of $1.00." in text
    assert "The cap is reached" in text and "Paid steps stay paused" in text


def test_secrets_are_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-test-secret-0123456789")
    s, _ = make_settings(tmp_path)
    write_events(s, [{"ts": at(1), "kind": "error", "where": "generate", "message": "bad AIza-test-secret-0123456789"}])
    text = digest.build(SINCE, UNTIL, s)
    assert "AIza-test" not in text and "[secret]" in text


def test_bad_cap_setting_does_not_stop_the_log(tmp_path):
    s, _ = make_settings(tmp_path, spend={"monthly_cap_usd": "lots"})
    assert "Could not be read: ValueError" in digest.build(SINCE, UNTIL, s)


def test_next_runs_follow_the_timers():
    """digest.SCHEDULE matches the OnCalendar lines of the systemd timers."""
    from core.config import ROOT

    units = ROOT / "deploy" / "lightsail" / "systemd"
    cal = {name: next(ln.split("=", 1)[1].strip() for ln in (units / name).read_text().splitlines()
                      if ln.startswith("OnCalendar="))
           for name in ("botsy-daily.timer", "botsy-digest.timer", "botsy-weekly.timer")}
    h, m = digest.SCHEDULE["daily_check"]
    assert cal["botsy-daily.timer"] == f"*-*-* {h:02d}:{m:02d}:00"
    h, m = digest.SCHEDULE["daily_log"]
    assert cal["botsy-digest.timer"] == f"*-*-* {h:02d}:{m:02d}:00"
    wd, h, m = digest.SCHEDULE["search_list"]
    assert wd == 6 and cal["botsy-weekly.timer"] == f"Sun *-*-* {h:02d}:{m:02d}:00"


def test_next_run_dates():
    after = datetime(2026, 10, 16, 6, 0).astimezone()          # a Friday, local time
    assert digest._next_run(after, 6, 17).hour == 6 and digest._next_run(after, 6, 17).day == 16
    assert digest._next_run(after.replace(hour=7), 6, 17).day == 17
    sunday = digest._next_run(after, 18, 7, weekday=6)
    assert sunday.weekday() == 6 and sunday.day == 18


def test_write_saves_latest_and_dated_copies(tmp_path):
    out = tmp_path / "outbox"
    latest = digest.write(out, "Botsy daily log\r\n", "2026-10-16")
    assert latest == out / "daily-log.txt"
    assert latest.read_bytes() == (out / "daily-log-2026-10-16.txt").read_bytes() == b"Botsy daily log\r\n"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(latest).st_mode) == 0o640
    for i in range(40):
        digest.write(out, "x\r\n", (datetime(2026, 1, 1) + timedelta(days=i)).date().isoformat())
    dated = sorted(p.name for p in out.glob("daily-log-*.txt"))
    assert len(dated) == digest.KEEP_DATED and dated[-1] == "daily-log-2026-10-16.txt"
    with pytest.raises(ValueError):
        digest.write(out, "x", "16/10/2026")


def test_review_lists_items_still_waiting_and_drops_drafted_ones(tmp_path):
    """Every run records the items still waiting, so the log lists them all; an item that
    became a draft later in the window is no longer waiting."""
    s, _ = make_settings(tmp_path)
    write_events(s, [
        {"ts": at(20), "kind": "held_for_review", "item_id": "2026-10-10-stickers-cats-bbbb",
         "reasons": "needs review (review status: pending)", "dry_run": True},
        {"ts": at(20), "kind": "held_for_review", "item_id": "2026-10-10-stickers-owls-eeee",
         "reasons": "needs review (review status: pending)", "dry_run": True},
        {"ts": at(5), "kind": "draft_created", "item_id": "2026-10-10-stickers-owls-eeee", "listing_id": 1,
         "title": "Owl Stickers", "dry_run": True},
        {"ts": at(1), "kind": "held_for_review", "item_id": "2026-10-10-stickers-cats-bbbb",
         "reasons": "needs review (review status: pending)", "dry_run": True},
    ])
    text = body(digest.build(SINCE, UNTIL, s))
    review = text.split("Waiting for your review")[1].split("Spending")[0]
    assert "1 item." in review and "approve 2026-10-10-stickers-cats-bbbb --dry-run" in review
    assert "owls" not in review


def test_held_message_without_a_header_line(tmp_path):
    s, _ = make_settings(tmp_path)
    write_events(s, [{"ts": at(2), "kind": "notification_held", "kinds": ["search_list_written"],
                      "text": "This week's search list is ready (attached).", "file": "search-list.txt"},
                     {"ts": at(1), "kind": "notification_held", "kinds": [], "text": "Botsy:"}])
    text = body(digest.build(SINCE, UNTIL, s))
    assert "  - This week's search list is ready (attached).\n" in text and "  - (empty)\n" in text


def test_next_run_keeps_the_wall_clock_time_across_a_summer_time_change():
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/London"
    time.tzset()
    try:
        before_change = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)    # BST; clocks go back on the 25th
        run = digest._next_run(before_change, 6, 17, weekday=0)                 # Monday the 26th, GMT
        assert (run.day, run.hour, run.minute) == (26, 6, 17) and run.utcoffset() == timedelta(0)
        assert digest._show(run).endswith("06:17")
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()
