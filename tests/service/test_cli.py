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
