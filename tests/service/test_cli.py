"""python -m service: argument parsing and each command, with fakes for the heavy stages."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

import pipeline.run as prun
import research
from service import __main__ as cli
from service import status, tick
from service.paths import service_paths

from ._support import (
    DATE,
    FakePipeline,
    FakeResearch,
    everbee_csv,
    make_settings,
    upload,
)


@pytest.fixture
def env(tmp_path):
    settings, path = make_settings(tmp_path)
    return settings, path, service_paths(settings)


@pytest.mark.parametrize("argv", [
    ["tick", "--force", "--own-stats", "--settings", "{s}", "--inbox", "{i}", "--date", DATE],
    ["--settings", "{s}", "--inbox", "{i}", "--date", DATE, "tick", "--force", "--own-stats"],
])
def test_options_work_before_and_after_the_command(env, monkeypatch, argv):
    _, path, paths = env
    seen = {}

    def fake_tick(settings, **kw):
        seen.update(kw, settings=settings)
        rec = status.new_record(date=kw["date"], forced=kw["force"], live=False)
        return rec

    monkeypatch.setattr(tick, "tick", fake_tick)
    other_inbox = paths.inbox.parent / "other-inbox"
    argv = [a.format(s=path, i=other_inbox) for a in argv]
    assert cli.main(argv) == 0
    assert seen["inbox_dir"] == other_inbox and seen["force"] is True and seen["own_stats"] is True
    assert seen["date"] == DATE and seen["settings"]["service"]["settle_s"] == 0


def test_parser_defaults():
    a = cli.build_parser().parse_args(["tick"])
    assert (a.cmd, a.force, a.own_stats, a.settings, a.inbox, a.date) == ("tick", False, False, None, None, None)
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["deploy"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["ingest", "--force"])   # --force belongs to tick


def test_bad_date_and_missing_settings(env, tmp_path, capsys):
    _, path, _ = env
    with pytest.raises(SystemExit) as e:
        cli.main(["status", "--settings", str(path), "--date", "16/10/2026"])
    assert e.value.code == 2
    assert cli.main(["status", "--settings", str(tmp_path / "nope.yaml")]) == 2
    assert "cannot read the settings" in capsys.readouterr().err


def test_ingest_command(env, capsys):
    _, path, paths = env
    upload(paths.incoming, "export.csv", everbee_csv())
    upload(paths.incoming, "photo.png", b"\x89PNG")
    assert cli.main(["ingest", "--settings", str(path), "--date", DATE]) == 0
    out = capsys.readouterr().out
    assert f"accepted: everbee-{DATE}-" in out and "not used: photo.png" in out
    assert cli.main(["ingest", "--settings", str(path)]) == 0
    assert "no finished uploads" in capsys.readouterr().out


def test_missing_inbox_is_exit_2(env, tmp_path, capsys):
    _, path, _ = env
    assert cli.main(["ingest", "--settings", str(path), "--inbox", str(tmp_path / "nowhere")]) == 2
    assert "setup.sh" in capsys.readouterr().err


def test_research_command_passes_history_dir(env, monkeypatch, capsys):
    _, path, paths = env
    fake = FakeResearch()
    monkeypatch.setattr(research, "run", fake)
    assert cli.main(["research", "--settings", str(path), "--date", DATE]) == 0
    assert [c["history_dir"] for c in fake.calls] == [paths.history] * 3
    assert "1 brief file(s), 2 design brief(s)" in capsys.readouterr().out
    monkeypatch.setattr(research, "run", FakeResearch(fail={"stickers"}))
    assert cli.main(["research", "--settings", str(path), "--date", DATE]) == 1


def test_run_command_is_a_dry_run_unless_live(env, tmp_path, monkeypatch, capsys):
    _, path, paths = env
    fake = FakePipeline()
    monkeypatch.setattr(prun, "run", fake)
    paths.briefs.mkdir(parents=True)
    (paths.briefs / f"stickers-{DATE}.json").write_text("{}", encoding="utf-8")
    assert cli.main(["run", "--settings", str(path), "--date", DATE]) == 1   # the fake has a failed item
    assert fake.calls[0]["dry_run"] is True
    out = capsys.readouterr().out
    assert "dry run (service.live is false)" in out and f"run {DATE} shop=main" in out

    _, live_path = make_settings(tmp_path, service={"live": True})
    cli.main(["run", "--settings", str(live_path), "--date", DATE])
    assert fake.calls[1]["dry_run"] is False


def test_searchlist_command_uses_the_other_module(env, monkeypatch, capsys):
    _, path, paths = env
    calls = {}

    def build(settings, history_dir, *, date, status=None):
        calls["build"] = (history_dir, date, status)
        return "SEARCH LIST\r\n"

    def write(outbox, text, date):
        p = Path(outbox) / "search-list.txt"
        p.write_text(text, encoding="utf-8")
        calls["write"] = (Path(outbox), text, date)
        return p

    monkeypatch.setitem(sys.modules, "service.searchlist",
                        types.SimpleNamespace(build=build, write=write))
    status.update(paths.outbox, _active_record())
    assert cli.main(["searchlist", "--settings", str(path), "--date", DATE]) == 0
    history_dir, day, week = calls["build"]
    assert history_dir == paths.history and day == DATE
    assert week["exports"] == 1 and {"briefs", "items", "drafts"} <= set(week)
    assert calls["write"] == (paths.outbox, "SEARCH LIST\r\n", DATE)
    assert "search-list.txt" in capsys.readouterr().out


def _active_record() -> dict:
    rec = status.new_record(date=DATE, forced=False, live=False)
    rec["ingest"]["accepted"] = ["everbee-x.csv"]
    rec["finished"] = rec["started"]
    return rec


def test_tick_command_end_to_end(env, monkeypatch, capsys):
    _, path, paths = env
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline())
    upload(paths.incoming, "export.csv", everbee_csv())
    assert cli.main(["tick", "--settings", str(path), "--date", DATE]) == 1   # one failed item
    out = capsys.readouterr().out
    assert "1 accepted" in out and "research ran" in out and "dry run" in out
    assert (paths.outbox / status.TEXT_NAME).exists()


def test_status_command_prints_the_status(env, capsys):
    _, path, paths = env
    assert cli.main(["status", "--settings", str(path)]) == 0
    assert "Nothing has happened yet" in capsys.readouterr().out
    assert (paths.outbox / status.TEXT_NAME).exists()


# ---------------------------------------------------------------- digest, spend, notify-test

WEBHOOK = "https://discord.com/api/webhooks/1/test-token-abcdef"


@pytest.fixture
def discord(monkeypatch, tmp_path):
    """Discord switched on with a webhook set; notify.send recorded instead of posting."""
    from service import notify

    calls = []
    monkeypatch.setattr(notify, "load_env", lambda: None)
    monkeypatch.setenv(notify.ENV_VAR, WEBHOOK)
    monkeypatch.setattr(notify, "send", lambda text, **kw: calls.append((text, kw)) or calls_ok[0])
    calls_ok = [True]
    settings, path = make_settings(tmp_path, notify={"discord": {"enabled": True}})
    return types.SimpleNamespace(calls=calls, ok=calls_ok, settings=settings, path=path,
                                 paths=service_paths(settings))


def test_digest_command_writes_the_log_and_posts_it(discord, capsys):
    from core import events

    events.emit("draft_created", settings=discord.settings, item_id="a", listing_id=1, title="Frog Stickers",
                dry_run=True)
    assert cli.main(["digest", "--settings", str(discord.path), "--date", DATE]) == 0
    out = capsys.readouterr().out
    latest = discord.paths.outbox / "daily-log.txt"
    assert f"wrote {latest}" in out and "Discord: daily log sent" in out
    assert (discord.paths.outbox / f"daily-log-{DATE}.txt").read_bytes() == latest.read_bytes()
    assert "Frog Stickers" in latest.read_text(encoding="utf-8")
    (text, kw), = discord.calls
    assert kw["urgent"] is True and kw["filename"] == "daily-log.txt" and "Botsy daily log" in text

    discord.ok[0] = False
    assert cli.main(["digest", "--settings", str(discord.path), "--hours", "6"]) == 1
    assert "could not be sent" in capsys.readouterr().err


def test_digest_without_discord_still_writes_the_log(env, capsys):
    _, path, paths = env
    assert cli.main(["digest", "--settings", str(path), "--date", DATE]) == 0
    out = capsys.readouterr().out
    assert "Discord: not sent." in out and "switched off" in out
    assert (paths.outbox / "daily-log.txt").exists()
    with pytest.raises(SystemExit):
        cli.main(["digest", "--settings", str(path), "--hours", "0"])
    with pytest.raises(SystemExit):
        cli.main(["digest", "--settings", str(path), "--hours", "lots"])


def test_spend_command_prints_a_table(env, capsys):
    from core import spend

    settings, path, _ = env
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", item_id="x",
                 input_tokens=1_000_000, settings=settings)
    assert cli.main(["spend", "--settings", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Total: $4.00" in out and "By stage" in out and "listing" in out
    assert cli.main(["spend", "--settings", str(path), "--month", "2020-01"]) == 0
    assert "Spending for 2020-01" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["spend", "--settings", str(path), "--month", "2026-13"])


def test_notify_test_command(discord, tmp_path, capsys):
    assert cli.main(["notify-test", "--settings", str(discord.path)]) == 0
    assert "Sent a test message to Discord" in capsys.readouterr().out
    (text, kw), = discord.calls
    assert "Botsy test message" in text and kw["urgent"] is True

    discord.ok[0] = False
    assert cli.main(["notify-test", "--settings", str(discord.path)]) == 1
    assert "Could not send the test message" in capsys.readouterr().err

    _, off_path = make_settings(tmp_path / "off")          # Discord switched off in these settings
    assert cli.main(["notify-test", "--settings", str(off_path)]) == 2
    assert "Not sent." in capsys.readouterr().out and len(discord.calls) == 2


def test_read_only_commands_do_not_wait_for_the_lock(env, capsys):
    from service.lock import service_lock

    _, path, paths = env
    with service_lock(paths.lock, 1):
        assert cli.main(["spend", "--settings", str(path)]) == 0
        assert cli.main(["digest", "--settings", str(path)]) == 0
        assert cli.main(["notify-test", "--settings", str(path)]) == 2      # not set up, but no wait


def test_searchlist_posts_the_list_with_the_file_attached(discord, monkeypatch, capsys):
    from core import events

    def build(settings, history_dir, *, date, status=None):
        return "SEARCH LIST\r\n"

    def write(outbox, text, date):
        p = Path(outbox) / "search-list.txt"
        p.write_text(text, encoding="utf-8")
        return p

    monkeypatch.setitem(sys.modules, "service.searchlist", types.SimpleNamespace(build=build, write=write))
    assert cli.main(["searchlist", "--settings", str(discord.path), "--date", DATE]) == 0
    assert "Discord: search list sent" in capsys.readouterr().out
    (text, kw), = discord.calls
    assert kw["file"] == discord.paths.outbox / "search-list.txt" and kw["kinds"] == ["search_list_written"]
    assert "search list is ready" in text
    import datetime as dt
    got = events.read(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1), settings=discord.settings)
    assert [e["kind"] for e in got] == ["search_list_written"]


def test_digest_window_reaches_back_to_the_last_log(tmp_path):
    import datetime as dt
    import os

    until = dt.datetime(2026, 10, 16, 7, 7, 30, tzinfo=dt.timezone.utc)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    day = dt.timedelta(days=1)
    # No earlier log: the last 24 hours.
    assert cli.digest_since(outbox, until, None, "daily-log.txt") == until - day
    log = outbox / "daily-log.txt"
    log.write_text("x", encoding="utf-8")
    for last, want in ((until - 3 * day, until - 3 * day),                 # two mornings missed
                       (until - day - dt.timedelta(seconds=20), until - day - dt.timedelta(seconds=20)),
                       (until - dt.timedelta(hours=8), until - day),       # a log made by hand today
                       (until - 9 * day, until - day)):                    # too long ago: 24 hours
        os.utime(log, (last.timestamp(), last.timestamp()))
        assert cli.digest_since(outbox, until, None, "daily-log.txt") == want
    os.utime(log, ((until - 3 * day).timestamp(),) * 2)
    assert cli.digest_since(outbox, until, 6, "daily-log.txt") == until - dt.timedelta(hours=6)   # --hours wins


def test_cleanup_command_prints_what_it_removed(env, capsys):
    _, path, paths = env
    old = paths.rejected / "2026-01-02-bank.csv"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("x", encoding="utf-8")
    (paths.rejected / "2026-01-02-bank.csv.reason.txt").write_text("not an export", encoding="utf-8")
    assert cli.main(["cleanup", "--settings", str(path)]) == 0
    out = capsys.readouterr().out
    assert "removed 1 rejected upload(s)" in out and not old.exists()
    assert cli.main(["cleanup", "--settings", str(path)]) == 0
    assert "nothing old enough to remove" in capsys.readouterr().out
