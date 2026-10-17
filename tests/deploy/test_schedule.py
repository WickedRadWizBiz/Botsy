"""core.config.tz / now_local and the schedule settings (docs/SERVER.md section 6)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml

from core import config
from core.config import ROOT, SCHEDULE_DEFAULTS, now_local, tz

NY = ZoneInfo("America/New_York")


def test_tz_defaults_to_new_york_and_reads_schedule_timezone():
    assert tz(None) == NY and tz({}) == NY and tz({"schedule": {}}) == NY
    assert tz({"schedule": {"timezone": "Europe/London"}}) == ZoneInfo("Europe/London")
    assert tz({"schedule": {"timezone": ""}}) == NY
    assert tz(config.load_settings()) == NY


def test_an_unknown_zone_name_falls_back_with_one_warning(caplog):
    config._logged.discard("schedule.timezone:Mars/Olympus")
    with caplog.at_level("WARNING", logger="core.config"):
        assert tz({"schedule": {"timezone": "Mars/Olympus"}}) == NY
        assert tz({"schedule": {"timezone": "Mars/Olympus"}}) == NY
    assert sum("Mars/Olympus" in r.getMessage() for r in caplog.records) == 1


def test_now_local_through_the_change_of_clocks():
    # 2026-11-01: 05:30 UTC is 01:30 EDT, one hour later 06:30 UTC is 01:30 EST again.
    first = now_local({}, datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))
    second = now_local({}, datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))
    assert (first.hour, first.minute, first.utcoffset()) == (1, 30, timedelta(hours=-4))
    assert (second.hour, second.minute, second.utcoffset()) == (1, 30, timedelta(hours=-5))
    # 2027-03-14: 06:59 UTC is 01:59 EST; 07:00 UTC is 03:00 EDT (02:xx never happens).
    before = now_local({}, datetime(2027, 3, 14, 6, 59, tzinfo=timezone.utc))
    after = now_local({}, datetime(2027, 3, 14, 7, 0, tzinfo=timezone.utc))
    assert (before.hour, before.minute) == (1, 59) and (after.hour, after.minute) == (3, 0)
    assert now_local({}, datetime(2027, 3, 14, 7, 0)).hour == 3        # naive counts as UTC
    assert now_local({}).tzinfo == NY and abs(now_local({}) - datetime.now(NY)) < timedelta(seconds=5)


def test_schedule_defaults_are_the_sunday_plan_as_planned():
    assert SCHEDULE_DEFAULTS == {
        "timezone": "America/New_York", "plan_post": "Sun 20:00", "reminders": ["Mon 08:00", "Mon 19:00"],
        "deadline": "Tue 20:00", "auto_start": "Mon 08:00", "revision_min_hours": 2, "max_revisions": 3,
        "approvals_before_auto": 4, "auto_start_max_cost_share": 0.40, "stale_skip_hours": 24,
        "upload_cutoff": "Sun 19:00"}
    raw = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    if "schedule" in raw:     # once the integrator merges the block, it must say the same
        assert config.section(raw, "schedule", SCHEDULE_DEFAULTS) == SCHEDULE_DEFAULTS
