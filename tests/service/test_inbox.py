"""service.inbox.ingest: accepted, duplicate and rejected uploads (docs/SERVER.md section 2)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

import pytest

from service import inbox
from service.paths import service_paths

from ._support import DATE, erank_keyword_csv, everbee_csv, make_settings, upload


@pytest.fixture
def env(tmp_path):
    settings, _ = make_settings(tmp_path)
    paths = service_paths(settings)
    return settings, paths


def run_ingest(settings, paths):
    return inbox.ingest(paths.incoming, paths.inputs, paths.rejected, settings=settings, date=DATE)


def ledger(paths) -> dict:
    return json.loads((paths.inputs / inbox.LEDGER_NAME).read_text(encoding="utf-8"))


def test_accepted_export_is_stored_under_tool_date_hash(env):
    settings, paths = env
    data = everbee_csv()
    upload(paths.incoming, "Product Analytics export.csv", data)
    res = run_ingest(settings, paths)
    sha = hashlib.sha256(data).hexdigest()
    assert [p.name for p in res.accepted] == [f"everbee-{DATE}-{sha[:8]}.csv"]
    assert res.duplicates == [] and res.rejected == []
    stored = res.accepted[0]
    assert stored.parent == paths.inputs and stored.read_bytes() == data
    assert list(paths.incoming.iterdir()) == []          # moved out of the inbox
    entry = ledger(paths)[sha]
    assert entry["file"] == stored.name and entry["original_name"] == "Product Analytics export.csv"
    assert re.match(r"\d{4}-\d{2}-\d{2}T", entry["received"])
    # no temp files left behind (research skips dotfiles, but the folder should stay tidy)
    assert sorted(p.name for p in paths.inputs.iterdir()) == sorted([stored.name, inbox.LEDGER_NAME])


def test_keyword_export_is_accepted(env):
    settings, paths = env
    upload(paths.incoming, "erank keywords.csv", erank_keyword_csv())
    res = run_ingest(settings, paths)
    assert len(res.accepted) == 1 and res.accepted[0].name.startswith(f"erank-{DATE}-")


@pytest.mark.parametrize("original, prefix", [
    ("coloring_pages__farm export.csv", "coloring_pages__"),
    ("Coloring Pages__farm export.csv", "coloring_pages__"),   # same rule as research's filename override
    ("activity-sheets__mazes.csv", "activity_sheets__"),
    ("holiday__export.csv", ""),                              # not a known niche: no prefix
])
def test_niche_prefix_is_kept(env, original, prefix):
    settings, paths = env
    upload(paths.incoming, original, everbee_csv())
    res = run_ingest(settings, paths)
    name = res.accepted[0].name
    assert name.startswith(prefix) and re.fullmatch(rf"{re.escape(prefix)}everbee-{DATE}-[0-9a-f]{{8}}\.csv", name)


def test_own_shop_and_trend_files_keep_their_role_word(env):
    settings, paths = env
    upload(paths.incoming, "own_shop everbee.csv", everbee_csv(seed=1))
    upload(paths.incoming, "stickers__trends everbee.csv", everbee_csv(seed=2))
    names = sorted(p.name for p in run_ingest(settings, paths).accepted)
    assert names[0].startswith("own_shop-everbee-") and names[1].startswith("stickers__trends-everbee-")


def test_duplicate_is_deleted_and_counted(env):
    settings, paths = env
    data = everbee_csv()
    upload(paths.incoming, "first.csv", data)
    first = run_ingest(settings, paths)
    upload(paths.incoming, "again (1).csv", data)
    second = run_ingest(settings, paths)                  # a new call reads the ledger from disk
    assert second.accepted == [] and second.duplicates == ["again (1).csv"]
    assert list(paths.incoming.iterdir()) == []
    assert sorted(p.name for p in paths.inputs.glob("*.csv")) == [first.accepted[0].name]


def test_duplicates_in_one_batch(env):
    settings, paths = env
    data = everbee_csv()
    upload(paths.incoming, "a.csv", data)
    upload(paths.incoming, "b.csv", data)
    res = run_ingest(settings, paths)
    assert len(res.accepted) == 1 and res.duplicates == ["b.csv"]


def test_ledger_persists_and_grows(env):
    settings, paths = env
    upload(paths.incoming, "one.csv", everbee_csv(seed=1))
    run_ingest(settings, paths)
    upload(paths.incoming, "two.csv", everbee_csv(seed=2))
    run_ingest(settings, paths)
    entries = ledger(paths)
    assert len(entries) == 2
    assert {e["original_name"] for e in entries.values()} == {"one.csv", "two.csv"}
    assert all((paths.inputs / e["file"]).is_file() for e in entries.values())


def test_broken_ledger_is_set_aside(env):
    settings, paths = env
    paths.inputs.mkdir(parents=True)
    (paths.inputs / inbox.LEDGER_NAME).write_text("{not json", encoding="utf-8")
    upload(paths.incoming, "one.csv", everbee_csv())
    assert len(run_ingest(settings, paths).accepted) == 1
    assert len(ledger(paths)) == 1
    assert list(paths.inputs.glob(f"{inbox.LEDGER_NAME}.broken-*"))


def _reason(paths, kept_name: str) -> str:
    return (paths.rejected / f"{kept_name}.reason.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("name, data, words", [
    ("notes.txt", b"Product Name,Shop Name\nx,y\n", "only .csv files"),
    ("statement.xlsx", b"PK\x03\x04 not really a spreadsheet", "only .csv files"),
    ("bank.csv", b"Date,Description,Amount\n2026-01-01,Coffee,3.50\n", "does not look like"),
    ("headers only.csv", b"Product Name,Shop Name,Price\n", "no rows of data"),
    ("empty.csv", b"", "empty"),
    ("blank.csv", b"  \n\n", "empty"),
    ("garbage.csv", bytes(range(256)) * 20, "does not look like"),
])
def test_rejected_files_are_moved_with_a_reason(env, name, data, words):
    settings, paths = env
    upload(paths.incoming, name, data)
    res = run_ingest(settings, paths)
    assert res.accepted == [] and [n for n, _ in res.rejected] == [name]
    assert words in res.rejected[0][1]
    kept = f"{DATE}-{name}"
    assert (paths.rejected / kept).read_bytes() == data       # a copy is kept for the user
    text = _reason(paths, kept)
    assert words in text and f"File: {name}" in text and "copy is kept" in text
    assert list(paths.incoming.iterdir()) == []
    assert not list(paths.inputs.glob("*.csv"))


def test_rejected_name_collision_gets_a_number(env):
    settings, paths = env
    upload(paths.incoming, "bank.csv", b"Date,Amount\n1,2\n")
    run_ingest(settings, paths)
    upload(paths.incoming, "bank.csv", b"Date,Amount\n3,4\n")
    run_ingest(settings, paths)
    assert (paths.rejected / f"{DATE}-bank.csv").read_bytes() == b"Date,Amount\n1,2\n"
    assert (paths.rejected / f"{DATE}-bank-2.csv").read_bytes() == b"Date,Amount\n3,4\n"
    assert (paths.rejected / f"{DATE}-bank-2.csv.reason.txt").exists()


def test_odd_characters_in_names_are_made_safe(env):
    settings, paths = env
    upload(paths.incoming, "bad\nname;$(x).txt", b"hello")
    res = run_ingest(settings, paths)
    assert res.rejected[0][0] == "bad?name;$(x).txt"         # printable in status.txt and logs
    assert [p.name for p in paths.rejected.iterdir() if not p.name.endswith(".reason.txt")] \
        == [f"{DATE}-bad_name_x_.txt"]


@pytest.mark.skipif(sys.platform == "win32" or sys.platform == "darwin",
                    reason="needs a file system that stores names as raw bytes")
def test_names_that_are_not_utf8_do_not_block_the_inbox(env):
    """An SFTP client can send any bytes as a name. One such upload must not stop ingest
    (and with it every later upload), and the status files must still be written."""
    from service import status

    settings, paths = env
    base = os.fsencode(str(paths.incoming))
    with open(base + b"/bad\xff\x1b[31mname.txt", "wb") as f:      # rejected
        f.write(b"hello")
    with open(base + b"/caf\xe9 export.csv", "wb") as f:           # latin-1 name, a real export
        f.write(everbee_csv())
    res = run_ingest(settings, paths)
    assert [n for n, _ in res.rejected] == ["bad\\xff?[31mname.txt"]
    assert len(res.accepted) == 1
    assert list(paths.incoming.iterdir()) == []
    assert "caf\\xe9 export.csv" in {e["original_name"] for e in ledger(paths).values()}
    reason = next(paths.rejected.glob("*.reason.txt")).read_text(encoding="utf-8")
    assert "File: bad\\xff?[31mname.txt" in reason and "\x1b" not in reason
    # the same names inside a tick record still give valid status files
    rec = status.new_record(date=DATE, forced=False, live=False)
    rec["ingest"]["rejected"] = [{"name": "x\udcff.csv", "reason": "r"}]
    rec["errors"] = ["ingest: odd \udcff name"]
    status.update(paths.outbox, rec)
    assert status.load(paths.outbox)["ticks"][-1]["errors"] == ["ingest: odd \udcff name"]
    assert "odd" in (paths.outbox / status.TEXT_NAME).read_text(encoding="utf-8")


def test_part_files_and_dotfiles_are_left_alone(env):
    settings, paths = env
    (paths.incoming / "export.csv.part").write_bytes(everbee_csv())
    (paths.incoming / "EXPORT2.CSV.PART").write_bytes(everbee_csv(seed=3))
    (paths.incoming / ".hidden.csv").write_bytes(everbee_csv(seed=4))
    (paths.incoming / "a folder").mkdir()
    res = run_ingest(settings, paths)
    assert res.empty()
    assert sorted(p.name for p in paths.incoming.iterdir()) == \
        [".hidden.csv", "EXPORT2.CSV.PART", "a folder", "export.csv.part"]


def test_abandoned_part_files_are_removed_after_two_days(env):
    settings, paths = env
    old, fresh = paths.incoming / "old.csv.part", paths.incoming / "fresh.csv.part"
    old.write_bytes(b"half an upload")
    fresh.write_bytes(b"still uploading")
    hidden = paths.incoming / ".old-dotfile"
    hidden.write_bytes(b"x")
    three_days_ago = time.time() - 3 * 24 * 3600
    os.utime(old, (three_days_ago, three_days_ago))
    os.utime(hidden, (three_days_ago, three_days_ago))
    res = run_ingest(settings, paths)
    assert res.empty()
    assert sorted(p.name for p in paths.incoming.iterdir()) == [".old-dotfile", "fresh.csv.part"]


def test_part_cleanup_can_be_switched_off(tmp_path):
    settings, _ = make_settings(tmp_path, service={"part_max_age_h": 0})
    paths = service_paths(settings)
    old = paths.incoming / "old.csv.part"
    old.write_bytes(b"x")
    os.utime(old, (1_000_000, 1_000_000))
    run_ingest(settings, paths)
    assert old.exists()


def test_display_name():
    assert inbox.display_name("plain name.csv") == "plain name.csv"
    assert inbox.display_name("tab\there\u202e.csv") == "tab?here?.csv"   # control + direction mark
    assert inbox.display_name("Café.csv") == "Café.csv"


def test_uppercase_csv_extension_is_accepted(env):
    settings, paths = env
    upload(paths.incoming, "EXPORT.CSV", everbee_csv())
    assert len(run_ingest(settings, paths).accepted) == 1


def _symlink(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow creating symlinks")


def test_symlink_is_rejected_and_never_followed(env, tmp_path):
    settings, paths = env
    secret = tmp_path / "secret.env"
    secret.write_text("ANTHROPIC_API_KEY=sk-ant-test-SECRET\n", encoding="utf-8")
    _symlink(secret, paths.incoming / "looks-fine.csv")
    _symlink(tmp_path / "nowhere.csv", paths.incoming / "dangling.csv")
    res = run_ingest(settings, paths)
    assert res.accepted == [] and sorted(n for n, _ in res.rejected) == ["dangling.csv", "looks-fine.csv"]
    assert all("link" in r for _, r in res.rejected)
    assert list(paths.incoming.iterdir()) == []                # the links are removed ...
    assert secret.read_text(encoding="utf-8").startswith("ANTHROPIC")   # ... the target is untouched
    for p in paths.rejected.rglob("*"):                        # and its content was never copied
        assert not p.is_symlink()
        assert b"SECRET" not in p.read_bytes()
    assert "did not keep a copy" in _reason(paths, f"{DATE}-looks-fine.csv")


@pytest.mark.skipif(not hasattr(os, "link") or sys.platform == "win32", reason="POSIX hard links")
def test_hard_link_is_rejected(env, tmp_path):
    settings, paths = env
    other = tmp_path / "other.csv"
    other.write_bytes(everbee_csv())
    os.link(other, paths.incoming / "linked.csv")
    res = run_ingest(settings, paths)
    assert res.accepted == [] and "hard link" in res.rejected[0][1]
    assert other.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")
def test_fifo_is_rejected_without_blocking(env):
    settings, paths = env
    os.mkfifo(paths.incoming / "pipe.csv")
    res = run_ingest(settings, paths)
    assert res.rejected == [("pipe.csv", "it is not a normal file")]
    assert list(paths.incoming.iterdir()) == []


def test_oversize_file_is_rejected_and_not_kept(tmp_path):
    settings, _ = make_settings(tmp_path, service={"max_upload_bytes": 200})
    paths = service_paths(settings)
    data = everbee_csv()
    assert len(data) > 200
    upload(paths.incoming, "big.csv", data)
    upload(paths.incoming, "small.csv", b"Date,Amount\n1,2\n")
    res = run_ingest(settings, paths)
    reasons = dict(res.rejected)
    assert "larger than the" in reasons["big.csv"] and "MB limit" in reasons["big.csv"]
    assert not (paths.rejected / f"{DATE}-big.csv").exists()
    assert "did not keep a copy" in _reason(paths, f"{DATE}-big.csv")
    assert "small.csv" in reasons                                # the limit only stops the big one
    assert list(paths.incoming.iterdir()) == []


def test_settings_mistake_stops_before_touching_uploads(tmp_path):
    settings, _ = make_settings(tmp_path, research={"column_overrides": {"not_a_column": "X"}})
    paths = service_paths(settings)
    upload(paths.incoming, "export.csv", everbee_csv())
    with pytest.raises(ValueError, match="column_overrides"):
        run_ingest(settings, paths)
    assert [p.name for p in paths.incoming.iterdir()] == ["export.csv"]


def test_missing_inbox_is_a_plain_error(tmp_path):
    settings, _ = make_settings(tmp_path)
    paths = service_paths(settings, tmp_path / "no-such-inbox")
    with pytest.raises(FileNotFoundError, match="setup.sh"):
        run_ingest(settings, paths)


def test_accepted_name_rules():
    sha = "ab" * 32
    known = ["stickers", "coloring_pages"]
    assert inbox.accepted_name("x.csv", "eRank", sha, DATE, known) == f"erank-{DATE}-abababab.csv"
    assert inbox.accepted_name("stickers__x.csv", "", sha, DATE, known) == f"stickers__unknown-{DATE}-abababab.csv"
    with pytest.raises(ValueError):
        inbox.accepted_name("x.csv", "erank", sha, "16/10/2026", known)


def test_wait_for_quiet_resets_on_change(tmp_path):
    (tmp_path / "a.csv.part").write_bytes(b"1")
    clock = {"t": 0.0}
    calls = []

    def sleep(s):
        clock["t"] += s
        calls.append(clock["t"])
        if len(calls) == 2:  # a new upload shows up during the wait
            (tmp_path / "b.csv").write_bytes(b"2")

    waited = inbox.wait_for_quiet(tmp_path, 10, max_wait_s=100, poll_s=4,
                                  clock=lambda: clock["t"], sleep=sleep)
    assert 10 < waited < 25            # the change at t=8 restarted the 10 s quiet window
    capped = inbox.wait_for_quiet(tmp_path, 1000, max_wait_s=30, poll_s=4,
                                  clock=lambda: clock["t"], sleep=lambda s: clock.update(t=clock["t"] + s))
    assert capped == pytest.approx(30)
