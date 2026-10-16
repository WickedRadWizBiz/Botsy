"""core.events: the event journal (docs/SERVER.md section 9). Everything in tmp_path."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import events
from core.config import ROOT


def settings_for(tmp_path: Path, **service) -> dict:
    return {"service": {"events_file": str(tmp_path / "data" / "events.jsonl"), **service}}


def lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_emit_appends_one_json_line_with_utc_time(tmp_path):
    s = settings_for(tmp_path)
    before = datetime.now(timezone.utc).replace(microsecond=0)
    events.emit("draft_created", settings=s, item_id="2026-10-16-stickers-frogs-aaaa",
                listing_id=123, title="Frog Stickers")
    events.emit("export_received", settings=s, tool="everbee", shape="listing", rows=240,
                path=Path("x.csv"))                        # odd values become text
    got = lines(tmp_path / "data" / "events.jsonl")
    assert [e["kind"] for e in got] == ["draft_created", "export_received"]
    assert got[0]["item_id"].endswith("frogs-aaaa") and got[0]["listing_id"] == 123
    ts = datetime.fromisoformat(got[0]["ts"])
    assert ts.utcoffset() == timedelta(0) and ts >= before
    assert got[1]["path"] == "x.csv"


def test_reserved_fields_cannot_be_overwritten(tmp_path):
    s = settings_for(tmp_path)
    events.emit("error", settings=s, ts="1999-01-01", where="tick", message="boom")
    (e,) = lines(tmp_path / "data" / "events.jsonl")
    assert e["ts"] != "1999-01-01" and e["where"] == "tick"


def test_emit_never_raises(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    s = {"service": {"events_file": str(blocker / "events.jsonl")}}   # parent is a file
    with caplog.at_level(logging.WARNING, logger="core.events"):
        events.emit("error", settings=s, where="x", message="y")
        events.emit("Bad Kind!", settings=s)
        events.emit(None, settings=s)                          # type: ignore[arg-type]
        events.emit("error", settings={"service": {"events_file": 12}})   # not a path
    assert "could not record event" in caplog.text and "not a plain name" in caplog.text


def test_rotation_keeps_five_files(tmp_path):
    s = settings_for(tmp_path, events_max_mb=0.001, events_keep=5)   # 1000 bytes
    for i in range(60):
        events.emit("item_generated", settings=s, item_id=f"item-{i:03d}", theme="x" * 40)
    data = tmp_path / "data"
    names = sorted(p.name for p in data.iterdir())
    assert names == ["events-1.jsonl", "events-2.jsonl", "events-3.jsonl", "events-4.jsonl",
                     "events-5.jsonl", "events.jsonl"]
    for p in data.iterdir():
        assert p.stat().st_size <= 1000
    # The newest events are in events.jsonl, older ones in events-1, events-2 ...
    newest = lines(data / "events.jsonl")[-1]["item_id"]
    assert newest == "item-059"
    assert lines(data / "events-1.jsonl")[-1]["item_id"] < lines(data / "events.jsonl")[0]["item_id"]
    assert lines(data / "events-5.jsonl")[-1]["item_id"] < lines(data / "events-4.jsonl")[0]["item_id"]
    # read() goes through every kept file, oldest first.
    got = events.read(datetime.now(timezone.utc) - timedelta(hours=1), settings=s)
    ids = [e["item_id"] for e in got]
    assert ids == sorted(ids) and ids[-1] == "item-059" and len(ids) > 20


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_read_window_and_kinds(tmp_path):
    s = settings_for(tmp_path)
    path = tmp_path / "data" / "events.jsonl"
    t0 = datetime(2026, 10, 15, 7, 7, tzinfo=timezone.utc)
    _write(path, [
        {"ts": (t0 - timedelta(hours=1)).isoformat(), "kind": "draft_created", "n": 0},
        {"ts": t0.isoformat(), "kind": "draft_created", "n": 1},
        {"ts": (t0 + timedelta(hours=5)).isoformat(), "kind": "error", "n": 2},
        {"ts": (t0 + timedelta(hours=24)).isoformat(), "kind": "draft_created", "n": 3},
    ])
    with open(path, "a", encoding="utf-8") as f:
        f.write("{broken\n\n[1, 2]\n" + json.dumps({"kind": "x"}) + "\n")
    got = events.read(t0, t0 + timedelta(hours=24), settings=s)
    assert [e["n"] for e in got] == [1, 2]                 # since included, until excluded
    assert [e["n"] for e in events.read(t0, settings=s, kinds=["draft_created"])] == [1, 3]
    assert [e["n"] for e in events.read(t0, settings=s, kinds="error")] == [2]
    # A naive datetime counts as UTC; path= reads a file directly.
    naive = datetime(2026, 10, 15, 12, 30)
    assert [e["n"] for e in events.read(naive, path=path)] == [3]


def test_read_skips_rotated_files_older_than_the_window(tmp_path):
    s = settings_for(tmp_path)
    path = tmp_path / "data" / "events.jsonl"
    old = events.rotated_path(path, 1)
    _write(old, [{"ts": "2020-01-01T00:00:00+00:00", "kind": "error"}])
    import os
    os.utime(old, (1577836800, 1577836800))                 # 2020-01-01
    _write(path, [{"ts": datetime.now(timezone.utc).isoformat(), "kind": "error"}])
    assert len(events.read(datetime(2026, 1, 1, tzinfo=timezone.utc), settings=s)) == 1
    assert len(events.read(datetime(2019, 1, 1, tzinfo=timezone.utc), settings=s)) == 2


def test_missing_journal_reads_empty(tmp_path):
    assert events.read(datetime(2026, 1, 1), settings=settings_for(tmp_path)) == []
    with pytest.raises(TypeError):
        events.read("2026-01-01", settings=settings_for(tmp_path))   # type: ignore[arg-type]


def test_the_real_journal_is_off_limits_in_tests(tmp_path):
    real = {"service": {"events_file": str(ROOT / "data" / "events-test-guard.jsonl")}}
    events.emit("error", settings=real, where="x", message="y")
    assert not (ROOT / "data" / "events-test-guard.jsonl").exists()
    assert events.events_path({}) == ROOT / "data" / "events.jsonl"


def test_a_line_cut_short_does_not_spoil_the_next_event(tmp_path):
    s = settings_for(tmp_path)
    path = tmp_path / "data" / "events.jsonl"
    events.emit("error", settings=s, where="a", message="first")
    with open(path, "ab") as f:
        f.write(b'{"ts": "2026-10-16T06:17:00+00:00", "kind": "err')      # a crash mid-write
    events.emit("error", settings=s, where="b", message="second")
    got = events.read(datetime.now(timezone.utc) - timedelta(hours=1), settings=s)
    assert [e["where"] for e in got] == ["a", "b"]


def test_rotation_removes_copies_beyond_a_smaller_keep(tmp_path):
    s = settings_for(tmp_path, events_max_mb=0.001, events_keep=5)
    for i in range(60):
        events.emit("item_generated", settings=s, item_id=f"item-{i:03d}", theme="x" * 40)
    data = tmp_path / "data"
    (data / "events-notes.jsonl").write_text("not ours\n", encoding="utf-8")
    s["service"]["events_keep"] = 2
    for i in range(10):
        events.emit("item_generated", settings=s, item_id=f"more-{i:03d}", theme="x" * 40)
    names = sorted(p.name for p in data.iterdir())
    assert names == ["events-1.jsonl", "events-2.jsonl", "events-notes.jsonl", "events.jsonl"]
