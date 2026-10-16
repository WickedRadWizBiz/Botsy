"""service.status: status.txt for people (CRLF, plain words) and status.json for the search list."""
from __future__ import annotations

import datetime as dt
import json
import os
import stat
import sys

import pytest

from service import status

NOW = dt.datetime(2026, 10, 16, 6, 30, tzinfo=dt.timezone.utc)


def record(started: dt.datetime, *, accepted=1, live=False, forced=False, errors=()) -> dict:
    rec = status.new_record(date=started.date().isoformat(), forced=forced, live=live,
                            started=started.isoformat(timespec="seconds"))
    rec["finished"] = (started + dt.timedelta(minutes=3)).isoformat(timespec="seconds")
    rec["ingest"]["accepted"] = [f"everbee-x-{i}.csv" for i in range(accepted)]
    rec["ingest"]["duplicates"] = ["again.csv"]
    rec["ingest"]["rejected"] = [{"name": "bank.csv", "reason": "it does not look like an EverBee or eRank export"}]
    rec["research"].update(ran=True, brief_files=["data/briefs/stickers-2026-10-16.json"], briefs=5,
                           notes=["coloring_pages: no brief file yet"])
    rec["run"].update(ran=True, items=4, generated=2, drafts=2, awaiting_review=1,
                      failed=["2026-10-16-stickers-bad-dddd: qa: pdf_blank_pages"])
    rec["errors"] = list(errors)
    return rec


def test_render_says_what_happened_in_plain_words():
    rec = record(NOW - dt.timedelta(hours=1), errors=["research[stickers]: RuntimeError: boom"])
    doc = {"updated": NOW.isoformat(), "last_checked": rec["finished"], "live": False, "ticks": [rec],
           "week": status.week_totals([rec], now=NOW)}
    text = status.render(doc)
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")   # CRLF only, for Notepad
    body = text.replace("\r\n", "\n")
    assert "Mode: TEST (dry run)" in body
    assert "Exports received: 1 new, 1 already received before, 1 not used" in body
    assert "Not used: bank.csv: it does not look like an EverBee or eRank export" in body
    assert "Research: 1 brief file written (5 design briefs)" in body
    assert "Items made: 2" in body and "Practice drafts (test mode, nothing sent to Etsy): 2" in body
    assert "Waiting for your review: 1" in body and "pdf_blank_pages" in body
    assert "Problems: 1" in body and "RuntimeError: boom" in body
    assert "Last 7 days (1 run):" in body and "1 export received" in body


def test_render_live_mode_and_empty_state():
    assert "Mode: LIVE" in status.render({"live": True, "ticks": []})
    text = status.render({"ticks": [], "last_checked": None})
    assert "Nothing has happened yet" in text and "Last checked for uploads: never" in text


def test_long_lists_are_capped():
    rec = record(NOW, errors=[f"error {i} " + "x" * 1000 for i in range(40)])
    body = status.render({"ticks": [rec]})
    assert "... and 25 more" in body
    assert max(len(line) for line in body.split("\r\n")) < 400


def test_update_keeps_active_ticks_and_counts_the_week(tmp_path):
    old = record(NOW - dt.timedelta(days=10), accepted=5)
    status.update(tmp_path, old, now=NOW - dt.timedelta(days=10))
    week_ago = record(NOW - dt.timedelta(days=2), accepted=2, live=True)
    status.update(tmp_path, week_ago, now=NOW - dt.timedelta(days=2))
    idle = status.new_record(date="2026-10-16", forced=False, live=False)
    idle["finished"] = NOW.isoformat(timespec="seconds")
    doc = status.update(tmp_path, idle, now=NOW, last_research={"date": "2026-10-14", "inputs": "abc"})

    assert len(doc["ticks"]) == 2                     # the idle check is not a record ...
    assert doc["last_checked"] == idle["finished"]    # ... it only moves last_checked
    week = doc["week"]
    assert week["ticks"] == 1 and week["exports"] == 2 and week["briefs"] == 1 and week["design_briefs"] == 5
    assert week["items"] == 2 and week["drafts"] == 2 and week["practice_drafts"] == 0
    assert week["since"] == "2026-10-09"
    assert doc["last_research"] == {"date": "2026-10-14", "inputs": "abc"}

    saved = json.loads((tmp_path / status.JSON_NAME).read_text(encoding="utf-8"))
    assert saved["schema"] == status.SCHEMA and saved["week"] == week
    assert status.load(tmp_path)["last_research"]["inputs"] == "abc"


def test_old_ticks_are_pruned(tmp_path):
    status.update(tmp_path, record(NOW - dt.timedelta(days=60)), now=NOW - dt.timedelta(days=60))
    doc = status.update(tmp_path, record(NOW), now=NOW, keep_days=35)
    assert len(doc["ticks"]) == 1


def test_a_brief_file_rewritten_the_same_day_counts_once():
    """Research rewrites <niche>-<date>.json when it runs again that day (a second upload)."""
    first = record(NOW - dt.timedelta(hours=2))
    second = record(NOW - dt.timedelta(hours=1))
    other = record(NOW)
    other["research"].update(brief_files=["data/briefs/coloring_pages-2026-10-16.json"], briefs=3)
    week = status.week_totals([first, second, other], now=NOW)
    assert week["ticks"] == 3 and week["briefs"] == 2 and week["design_briefs"] == 5 + 3


def test_dry_run_drafts_are_practice_drafts():
    week = status.week_totals([record(NOW)], now=NOW)
    assert week["drafts"] == 0 and week["practice_drafts"] == 2


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_files_are_readable_by_the_upload_group(tmp_path):
    status.update(tmp_path, record(NOW), now=NOW)
    for name in (status.TEXT_NAME, status.JSON_NAME):
        assert stat.S_IMODE(os.stat(tmp_path / name).st_mode) == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == [status.JSON_NAME, status.TEXT_NAME]


def test_load_handles_missing_and_broken_files(tmp_path):
    assert status.load(tmp_path) is None
    (tmp_path / status.JSON_NAME).write_text("{oops", encoding="utf-8")
    assert status.load(tmp_path) is None
    (tmp_path / status.JSON_NAME).write_text('{"ticks": "nope"}', encoding="utf-8")
    assert status.load(tmp_path) is None


def test_refresh_rebuilds_text_from_json(tmp_path):
    path = status.refresh(tmp_path, live=False, now=NOW)
    assert "Nothing has happened yet" in path.read_text(encoding="utf-8")
    status.update(tmp_path, record(NOW), now=NOW)
    (tmp_path / status.TEXT_NAME).unlink()
    text = status.refresh(tmp_path, live=True, now=NOW).read_text(encoding="utf-8")
    assert "Mode: LIVE" in text and "Items made: 2" in text


def test_secret_values_never_reach_the_status_files(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0123456789abcdef")
    monkeypatch.setenv("ETSY_SHARED_SECRET", "etsy-shared-secret-value")
    monkeypatch.setenv("ETSY_TOKEN_FILE", "/opt/botsy/.etsy_tokens.json")    # a path, not a secret
    rec = record(NOW - dt.timedelta(hours=1),
                 errors=["listing: AuthError: bad key sk-ant-test-0123456789abcdef",
                         "publish: 401 for etsy-shared-secret-value at /opt/botsy/.etsy_tokens.json"])
    status.update(tmp_path, rec, now=NOW)
    for name in (status.JSON_NAME, status.TEXT_NAME):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert "sk-ant-test" not in text and "etsy-shared-secret-value" not in text
        assert "[secret]" in text and "/opt/botsy/.etsy_tokens.json" in text


def test_redact_ignores_short_and_non_secret_values():
    env = {"API_KEY": "short", "SHOP_NAME": "SuperLongShopName123", "MY_TOKEN": "tok-1234567890"}
    assert status.redact("short SuperLongShopName123 tok-1234567890", env) == \
        "short SuperLongShopName123 [secret]"


def test_the_discord_webhook_link_is_a_secret(tmp_path, monkeypatch):
    link = "https://discord.com/api/webhooks/123/very-secret-token"
    assert status.redact(f"post to {link} failed", {"DISCORD_WEBHOOK_URL": link}) == "post to [secret] failed"
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", link)
    status.update(tmp_path, record(NOW, errors=[f"notify: could not reach {link}"]), now=NOW)
    for name in (status.JSON_NAME, status.TEXT_NAME):
        assert "very-secret-token" not in (tmp_path / name).read_text(encoding="utf-8")


def test_render_shows_the_research_stage_and_cleanup():
    rec = record(NOW)
    rec["research"]["stage"] = "refined"
    rec["cleanup"] = {"rejected_files": 2, "old_exports": 1}
    body = status.render({"ticks": [rec]}).replace("\r\n", "\n")
    assert "Research: 1 brief file written (5 design briefs, refined stage)" in body
    assert "Old files tidied away: rejected files 2, old exports 1" in body


def test_any_discord_webhook_link_is_replaced():
    from service.status import redact

    text = ("ConnectionError: HTTPSConnectionPool(host='discord.com', port=443): Max retries exceeded "
            "with url: /api/webhooks/123456/AbC-token_xyz (Caused by x); full https://discordapp.com/api/v10/"
            "webhooks/1/tok?wait=true")
    out = redact(text, environ={})
    assert "AbC-token" not in out and "tok?wait" not in out and out.count("[secret]") == 2
    assert redact("starts with https://discord.com/api/webhooks/ (copy it)", environ={}) == \
        "starts with https://discord.com/api/webhooks/ (copy it)"
