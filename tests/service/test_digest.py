"""service.digest: the overnight log from synthetic events and spend lines (all in tmp_path)."""
from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core import spend
from service import digest

from ._support import make_settings

UNTIL = datetime(2026, 10, 16, 7, 7, tzinfo=timezone.utc)
SINCE = UNTIL - timedelta(hours=24)
NY = ZoneInfo("America/New_York")


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
    assert "What runs next" in text and "Daily check:" in text and "Weekly search list: Sat" in text
    # Times are on New York's clock (07:07 UTC on 16 Oct = 03:07 EDT), whatever the server's zone.
    assert "What happened from Thu 15 Oct 03:07 to Fri 16 Oct 03:07 (New York time)." in text
    assert "Daily check: Fri 16 Oct 06:17\n" in text and "Next daily log: Fri 16 Oct 07:07\n" in text
    assert "Weekly search list: Sat 17 Oct 09:07\n" in text
    assert max(len(line) for line in text.splitlines()) < 260


def test_test_mode_review_commands_say_dry_run(tmp_path):
    s, _ = make_settings(tmp_path, spend={"monthly_cap_usd": None})  # pinned: the shipped default is 25
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
    """digest.SCHEDULE matches the OnCalendar lines of the systemd timers, zone included."""
    from core.config import DEFAULT_TIMEZONE, ROOT

    units = ROOT / "deploy" / "lightsail" / "systemd"
    cal = {name: next(ln.split("=", 1)[1].strip() for ln in (units / name).read_text().splitlines()
                      if ln.startswith("OnCalendar="))
           for name in ("botsy-daily.timer", "botsy-digest.timer", "botsy-weekly.timer")}
    h, m = digest.SCHEDULE["daily_check"]
    assert cal["botsy-daily.timer"] == f"*-*-* {h:02d}:{m:02d}:00 {DEFAULT_TIMEZONE}"
    h, m = digest.SCHEDULE["daily_log"]
    assert cal["botsy-digest.timer"] == f"*-*-* {h:02d}:{m:02d}:00 {DEFAULT_TIMEZONE}"
    wd, h, m = digest.SCHEDULE["search_list"]
    assert wd == 5 and cal["botsy-weekly.timer"] == f"Sat *-*-* {h:02d}:{m:02d}:00 {DEFAULT_TIMEZONE}"
    assert digest.TIMER_ZONE == DEFAULT_TIMEZONE and all(c.endswith(" " + digest.TIMER_ZONE) for c in cal.values())
    assert DEFAULT_TIMEZONE == "America/New_York"


def test_next_run_dates():
    after = datetime(2026, 10, 16, 6, 0, tzinfo=NY)             # a Friday, New York time
    run = digest._next_run(after, 6, 17, zone=NY)
    assert (run.day, run.hour, run.tzinfo) == (16, 6, NY)
    assert digest._next_run(after.replace(hour=7), 6, 17, zone=NY).day == 17
    saturday = digest._next_run(after, 9, 7, weekday=5, zone=NY)
    assert saturday.weekday() == 5 and saturday.day == 17
    assert digest._next_run(after, 6, 17).tzinfo == NY            # the default zone is Botsy's


def test_log_date_is_the_date_on_new_york_clock():
    """07:07 UTC is still the day before in New York; a naive `now` counts as UTC."""
    assert digest.log_date({}, datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)) == "2026-10-16"
    assert digest.log_date({}, datetime(2026, 10, 17, 3, 0)) == "2026-10-16"
    assert digest.log_date({}, datetime(2026, 10, 17, 11, 7, tzinfo=timezone.utc)) == "2026-10-17"
    london = {"schedule": {"timezone": "Europe/London"}}
    assert digest.log_date(london, datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)) == "2026-10-17"


def test_log_date_with_no_time_given(monkeypatch):
    """No `now`: today on New York's clock, unless PIPELINE_DATE pins the date (like today())."""
    from core.config import now_local

    monkeypatch.delenv("PIPELINE_DATE", raising=False)
    assert digest.log_date({}) in {now_local({}).date().isoformat(),
                                   (now_local({}) - timedelta(seconds=5)).date().isoformat()}
    monkeypatch.setenv("PIPELINE_DATE", "2026-01-02")
    assert digest.log_date({}) == "2026-01-02"
    assert digest.log_date({}, datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)) == "2026-10-16"


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


@pytest.mark.parametrize("before, offset_after, day", [
    # New York's clocks go back on Sunday 2026-11-01 (02:00 EDT -> 01:00 EST) ...
    (datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc), timedelta(hours=-5), 1),
    # ... and forward on Sunday 2027-03-14 (02:00 EST -> 03:00 EDT).
    (datetime(2027, 3, 13, 12, 0, tzinfo=timezone.utc), timedelta(hours=-4), 14),
])
def test_next_run_keeps_the_wall_clock_time_across_a_change_of_clocks(before, offset_after, day):
    """The 06:17 check is still shown (and run by the timer) at 06:17 on the morning the
    clocks change, with that day's offset, not an hour off."""
    run = digest._next_run(before, 6, 17, zone=NY)
    assert (run.day, run.hour, run.minute) == (day, 6, 17) and run.utcoffset() == offset_after
    assert digest._show(run, NY).endswith("06:17")
    # Measured in real time the run is 25 or 23 hours after the same wall time the day before.
    previous = run - timedelta(days=1)
    assert (run.astimezone(timezone.utc) - previous.astimezone(timezone.utc)) \
        == timedelta(hours=24) - (offset_after - previous.utcoffset())


def test_log_times_do_not_follow_a_server_set_to_utc(tmp_path):
    """A server on UTC (or any zone) shows New York times in the log and names them."""
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    s, _ = make_settings(tmp_path)
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        until = datetime(2026, 11, 1, 12, 7, tzinfo=timezone.utc)        # 07:07 EST, the morning the clocks went back
        text = digest.build(until - timedelta(hours=24), until, s).replace("\r\n", "\n")
        assert "from Sat 31 Oct 08:07 to Sun 01 Nov 07:07 (New York time)." in text
        assert "Daily check: Mon 02 Nov 06:17\n" in text and "Weekly search list: Sat 07 Nov 09:07\n" in text
        s["schedule"] = {"timezone": "Europe/London"}
        text = digest.build(until - timedelta(hours=24), until, s).replace("\r\n", "\n")
        assert "from Sat 31 Oct 12:07 to Sun 01 Nov 12:07 (London time)." in text
        # The timers stay on New York time, so London shows when they really run.
        assert "Daily check: Mon 02 Nov 11:17\n" in text and "Weekly search list: Sat 07 Nov 14:07\n" in text
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()


def test_book_files_kept_for_the_kdp_reader_are_shown(tmp_path):
    s, _ = make_settings(tmp_path)
    write_events(s, [{"ts": at(5), "kind": "export_routed", "name": "bookbeam books.csv",
                      "file": "2026-10-15-bookbeam-books.csv", "reason": "kdp export, waits for the KDP reader"}])
    text = body(digest.build(SINCE, UNTIL, s))
    assert "Book (KDP) files kept for the KDP reader: 1" in text and "bookbeam books.csv" in text
    assert "None received." not in text
