"""service.cleanup.run: disk caps for rejected uploads, old exports and practice items
(docs/SERVER.md section 14). Every folder is in tmp_path (see _support.make_settings)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time

import pytest

from core import items
from service import cleanup, inbox
from service.paths import service_paths

from ._support import everbee_csv, make_settings, upload

TODAY = dt.date(2026, 10, 16)
NOW = dt.datetime(2026, 10, 16, 6, 30)


def days_ago(n: int) -> str:
    return (TODAY - dt.timedelta(days=n)).isoformat()


def set_mtime(path, days: int) -> None:
    t = time.mktime((TODAY - dt.timedelta(days=days)).timetuple()) + 3600
    os.utime(path, (t, t), follow_symlinks=False)


@pytest.fixture
def env(tmp_path):
    settings, _ = make_settings(tmp_path)
    return settings, service_paths(settings)


def run(settings) -> dict:
    return cleanup.run(settings, now=NOW)


# ---------------------------------------------------------------- rejected uploads

def reject(folder, name: str, data: bytes = b"x" * 100, reason: bool = True):
    folder.mkdir(parents=True, exist_ok=True)
    if data is not None:
        (folder / name).write_bytes(data)
    if reason:
        (folder / f"{name}.reason.txt").write_text("Not used because ...\n", encoding="utf-8")


def test_rejected_uploads_older_than_keep_days_go_with_their_reason(env):
    settings, paths = env
    reject(paths.rejected, f"{days_ago(31)}-bank.csv")
    reject(paths.rejected, f"{days_ago(45)}-link.csv", data=None)        # a link: only the reason was kept
    reject(paths.rejected, f"{days_ago(30)}-notes.txt")
    reject(paths.rejected, f"{days_ago(2)}-photo.jpg")
    res = run(settings)
    assert res["rejected_uploads"] == [f"{days_ago(45)}-link.csv", f"{days_ago(31)}-bank.csv"]   # oldest first
    assert sorted(p.name for p in paths.rejected.iterdir()) == sorted([
        f"{days_ago(30)}-notes.txt", f"{days_ago(30)}-notes.txt.reason.txt",
        f"{days_ago(2)}-photo.jpg", f"{days_ago(2)}-photo.jpg.reason.txt"])
    assert res.freed_bytes > 100 and res.problems == []
    assert cleanup.describe(res).startswith("removed 2 rejected upload(s) (")


def test_files_not_named_by_ingest_are_never_removed_from_the_rejected_folder(tmp_path):
    """Only `<YYYY-MM-DD>-<name>` entries are Botsy's. A rejected_dir set by mistake to a
    folder with other files (here: a copy of config/) loses nothing."""
    settings, _ = make_settings(tmp_path, service={"rejected_dir": str(tmp_path / "config"),
                                                   "rejected_max_mb": 0.0001})       # 100 bytes
    folder = tmp_path / "config"
    reject(folder, "settings.yaml", data=b"x" * 5000, reason=False)
    reject(folder, "trademark_blocklist.txt", data=b"x" * 5000, reason=False)
    reject(folder, "2026-13-40-not-a-date.csv", reason=False)
    for name in ("settings.yaml", "trademark_blocklist.txt", "2026-13-40-not-a-date.csv"):
        set_mtime(folder / name, 400)
    reject(folder, f"{days_ago(90)}-old.csv")
    res = run(settings)
    assert res["rejected_uploads"] == [f"{days_ago(90)}-old.csv"]
    assert sorted(p.name for p in folder.iterdir()) == ["2026-13-40-not-a-date.csv", "settings.yaml",
                                                         "trademark_blocklist.txt"]


def test_rejected_folder_is_capped_oldest_first(tmp_path):
    settings, _ = make_settings(tmp_path, service={"rejected_max_mb": 0.001})   # 1000 bytes
    paths = service_paths(settings)
    for n, name in ((5, "e"), (3, "c"), (4, "d"), (1, "a")):
        reject(paths.rejected, f"{days_ago(n)}-{name}.csv", data=b"x" * 300, reason=False)
    res = run(settings)
    assert res["rejected_uploads"] == [f"{days_ago(5)}-e.csv"]            # 1200 -> 900 bytes
    assert sum(p.stat().st_size for p in paths.rejected.iterdir()) <= 1000


def test_zero_switches_the_rejected_rules_off(tmp_path):
    settings, _ = make_settings(tmp_path, service={"rejected_max_mb": 0, "rejected_keep_days": 0})
    paths = service_paths(settings)
    reject(paths.rejected, f"{days_ago(400)}-old.csv", data=b"x" * 5000)
    assert run(settings)["rejected_uploads"] == []
    assert len(list(paths.rejected.iterdir())) == 2


def test_links_in_the_rejected_folder_are_never_followed(env, tmp_path):
    settings, paths = env
    outside = tmp_path / "precious.csv"
    outside.write_bytes(b"keep me")
    set_mtime(outside, 400)
    paths.rejected.mkdir(parents=True)
    link = paths.rejected / f"{days_ago(90)}-link.csv"
    os.symlink(outside, link)
    set_mtime(link, 90)
    res = run(settings)
    assert res["rejected_uploads"] == [] and outside.read_bytes() == b"keep me" and os.path.islink(link)


def test_rejected_folder_that_is_a_link_is_not_cleaned(tmp_path):
    real = tmp_path / "elsewhere"
    reject(real, f"{days_ago(90)}-old.csv")
    settings, _ = make_settings(tmp_path, service={"rejected_dir": str(tmp_path / "rejected-link")})
    os.symlink(real, tmp_path / "rejected-link")
    res = run(settings)
    assert res["rejected_uploads"] == [] and len(list(real.iterdir())) == 2
    assert any("is a link" in p for p in res.problems)


def test_rejected_folder_sharing_a_folder_with_the_exports_is_not_cleaned(tmp_path):
    settings, _ = make_settings(tmp_path, service={"rejected_dir": str(tmp_path / "inputs")})
    paths = service_paths(settings)
    reject(paths.inputs, f"{days_ago(90)}-old.csv")
    res = run(settings)
    assert res["rejected_uploads"] == [] and len(list(paths.inputs.iterdir())) == 2
    assert any("shares a folder" in p for p in res.problems)


# ---------------------------------------------------------------- accepted exports

def store_export(paths, data: bytes, day: str, tool: str = "everbee", *, ledger: bool = True) -> str:
    """An export as ingest stores it, with its ledger entry."""
    sha = hashlib.sha256(data).hexdigest()
    name = f"{tool}-{day}-{sha[:8]}.csv"
    paths.inputs.mkdir(parents=True, exist_ok=True)
    (paths.inputs / name).write_bytes(data)
    if ledger:
        book = inbox.load_ledger(paths.inputs / inbox.LEDGER_NAME)
        book[sha] = {"file": name, "received": f"{day}T08:00:00+00:00", "original_name": "export.csv"}
        items.write_json(paths.inputs / inbox.LEDGER_NAME, book)
    return name


def ledger(paths) -> dict:
    return json.loads((paths.inputs / inbox.LEDGER_NAME).read_text(encoding="utf-8"))


def test_old_exports_are_removed_and_stay_in_the_ledger(env):
    settings, paths = env
    old_data, new_data = everbee_csv(seed=1), everbee_csv(seed=2)
    old = store_export(paths, old_data, days_ago(181))
    new = store_export(paths, new_data, days_ago(180))
    res = run(settings)
    assert res["old_exports"] == [old]
    assert not (paths.inputs / old).exists() and (paths.inputs / new).exists()
    book = ledger(paths)
    old_sha = hashlib.sha256(old_data).hexdigest()
    assert book[old_sha]["deleted"] == TODAY.isoformat() and book[old_sha]["file"] == old
    assert "deleted" not in book[hashlib.sha256(new_data).hexdigest()]
    assert "old export(s)" in cleanup.describe(res)

    # Uploading the removed file again is still recognised as a duplicate.
    upload(paths.incoming, "the same export again.csv", old_data)
    again = inbox.ingest(paths.incoming, paths.inputs, paths.rejected, settings=settings, date=TODAY.isoformat())
    assert again.accepted == [] and again.duplicates == ["the same export again.csv"]
    assert not (paths.inputs / old).exists()


def test_ledger_date_is_used_when_the_name_has_none(env):
    settings, paths = env
    data = everbee_csv(seed=3)
    sha = hashlib.sha256(data).hexdigest()
    paths.inputs.mkdir(parents=True)
    (paths.inputs / "coloring_pages__legacy.csv").write_bytes(data)
    items.write_json(paths.inputs / inbox.LEDGER_NAME,
                     {sha: {"file": "coloring_pages__legacy.csv", "received": f"{days_ago(200)}T08:00:00+00:00",
                            "original_name": "legacy.csv"}})
    assert run(settings)["old_exports"] == ["coloring_pages__legacy.csv"]
    assert ledger(paths)[sha]["deleted"] == TODAY.isoformat()


def test_export_missing_from_the_ledger_is_added_when_removed(env):
    settings, paths = env
    data = everbee_csv(seed=4)
    name = store_export(paths, data, days_ago(300), ledger=False)
    assert run(settings)["old_exports"] == [name]
    entry = ledger(paths)[hashlib.sha256(data).hexdigest()]
    assert entry["file"] == name and entry["deleted"] == TODAY.isoformat()


def test_files_ingest_did_not_store_are_left_alone(env):
    settings, paths = env
    paths.inputs.mkdir(parents=True)
    for name in ("own_shop_stats.csv", "my notes.csv", "erank-keywords.csv"):
        (paths.inputs / name).write_text("a,b\n1,2\n", encoding="utf-8")
        set_mtime(paths.inputs / name, 400)
    assert run(settings)["old_exports"] == []
    assert len(list(paths.inputs.glob("*.csv"))) == 3


def test_unreadable_ledger_keeps_every_export(env):
    settings, paths = env
    name = store_export(paths, everbee_csv(seed=5), days_ago(300))
    (paths.inputs / inbox.LEDGER_NAME).write_text("{broken", encoding="utf-8")
    res = run(settings)
    assert res["old_exports"] == [] and (paths.inputs / name).exists()
    assert any("ledger could not be read" in p for p in res.problems)


def test_exports_still_in_the_research_window_are_kept(tmp_path):
    settings, _ = make_settings(tmp_path, research={"keep_days": 10, "window_days": 35})
    paths = service_paths(settings)
    inside = store_export(paths, everbee_csv(seed=6), days_ago(30))
    outside = store_export(paths, everbee_csv(seed=7), days_ago(36))
    assert run(settings)["old_exports"] == [outside] and (paths.inputs / inside).exists()


def test_ledger_is_saved_before_an_export_is_removed(env, monkeypatch):
    """A crash between the two leaves a file marked deleted, never a removed file whose
    sha256 the ledger does not know (a re-upload would then be taken as new)."""
    settings, paths = env
    data = everbee_csv(seed=8)
    name = store_export(paths, data, days_ago(300), ledger=False)
    sha = hashlib.sha256(data).hexdigest()
    real_unlink = os.unlink

    def crash(path, *a, **kw):
        if str(path) == name:
            assert ledger(paths)[sha]["deleted"] == TODAY.isoformat()   # already saved
            raise KeyboardInterrupt("power cut")
        return real_unlink(path, *a, **kw)

    monkeypatch.setattr(cleanup.os, "unlink", crash)
    with pytest.raises(KeyboardInterrupt):
        run(settings)
    assert (paths.inputs / name).exists() and ledger(paths)[sha]["file"] == name
    monkeypatch.setattr(cleanup.os, "unlink", real_unlink)
    assert run(settings)["old_exports"] == [name] and not (paths.inputs / name).exists()


def test_export_that_could_not_be_removed_is_not_marked_deleted(env, monkeypatch):
    settings, paths = env
    data = everbee_csv(seed=9)
    name = store_export(paths, data, days_ago(300))
    sha = hashlib.sha256(data).hexdigest()
    before = ledger(paths)[sha]

    def refuse(path, *a, **kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cleanup.os, "unlink", refuse)
    res = run(settings)
    assert res["old_exports"] == [] and (paths.inputs / name).exists()
    assert ledger(paths)[sha] == before and any("could not remove" in p for p in res.problems)


def test_export_that_cannot_be_hashed_is_kept(env, monkeypatch):
    settings, paths = env
    name = store_export(paths, everbee_csv(seed=10), days_ago(300), ledger=False)
    monkeypatch.setattr(cleanup, "_sha256", lambda fd, n: None)
    res = run(settings)
    assert res["old_exports"] == [] and (paths.inputs / name).exists()
    assert any("could not be read" in p for p in res.problems)
    assert not (paths.inputs / inbox.LEDGER_NAME).exists()          # nothing was written


def test_one_export_without_a_date_does_not_stop_the_others(env, monkeypatch):
    settings, paths = env
    a = store_export(paths, everbee_csv(seed=11), days_ago(300))
    b = store_export(paths, everbee_csv(seed=12), days_ago(300))
    import research.window as window

    real = window.received_date

    def flaky(path, ledger=None):
        if path.name == a:
            raise OSError("no usable date")
        return real(path, ledger)

    monkeypatch.setattr(window, "received_date", flaky)
    assert run(settings)["old_exports"] == [b] and (paths.inputs / a).exists()


def test_linked_export_is_never_followed(env, tmp_path):
    settings, paths = env
    target = tmp_path / "elsewhere.csv"
    target.write_bytes(b"keep")
    paths.inputs.mkdir(parents=True)
    link = paths.inputs / f"everbee-{days_ago(300)}-0123abcd.csv"
    os.symlink(target, link)
    assert run(settings)["old_exports"] == [] and target.exists() and os.path.islink(link)


# ---------------------------------------------------------------- practice (dry-run) items

def practice_item(root, created: str, theme: str = "frogs", *, dry: bool = True, publish: dict | None = None):
    d = items.item_dir(items.new_item_id(created, "stickers", theme, salt=theme), root)
    items.write_manifest(d, {"item_id": d.name, "niche": "stickers", "shop_id": "main", "theme": theme,
                             "created": created, "status": "drafted",
                             "generator": {"name": "stickers", "dry_run": dry}})
    (d / "files").mkdir()
    (d / "files" / "pack.zip").write_bytes(b"z" * 2000)
    items.write_json(d / "publish.json", publish if publish is not None else {"listing_id": 1, "dry_run": True})
    items.write_json(root / "review" / f"{d.name}.json", {"item_id": d.name, "status": "pending"})
    return d


def test_old_practice_items_are_removed_with_their_review_entries(env, tmp_path):
    settings, _ = env
    dry = items.dry_run_root(settings)
    old = practice_item(dry, days_ago(15), "frogs")
    young = practice_item(dry, days_ago(14), "owls")
    res = run(settings)
    assert res["practice_items"] == [old.name]
    assert not old.exists() and young.exists()
    assert not (dry / "review" / f"{old.name}.json").exists() and (dry / "review" / f"{young.name}.json").exists()
    assert res.freed_bytes >= 2000 and "1 practice item(s)" in cleanup.describe(res)


def test_practice_cleanup_never_touches_real_drafts_or_real_items(env):
    settings, _ = env
    dry, real = items.dry_run_root(settings), items.output_root(settings)
    drafted = practice_item(dry, days_ago(90), "frogs", publish={"listing_id": 5, "dry_run": False})
    unknown = practice_item(dry, days_ago(90), "cats")
    (unknown / "publish.json").write_text("{broken", encoding="utf-8")         # may be a real record
    paid = practice_item(dry, days_ago(90), "owls", dry=False)                 # made without --dry-run
    real_item = practice_item(real, days_ago(90), "bees")                      # the real output folder
    assert run(settings)["practice_items"] == []
    assert drafted.exists() and unknown.exists() and paid.exists() and real_item.exists()


def test_links_in_the_practice_folder_are_never_followed(env, tmp_path):
    settings, _ = env
    dry = items.dry_run_root(settings)
    outside = practice_item(tmp_path / "elsewhere", days_ago(90), "frogs")
    dry.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, dry / outside.name)
    old = practice_item(dry, days_ago(90), "owls")
    secret = tmp_path / "secret.txt"
    secret.write_text("keep", encoding="utf-8")
    os.symlink(secret, old / "files" / "link.txt")                             # a link inside an old item
    res = run(settings)
    assert res["practice_items"] == [old.name] and not old.exists()
    assert outside.exists() and (outside / "manifest.json").exists() and os.path.islink(dry / outside.name)
    assert secret.read_text(encoding="utf-8") == "keep"


def test_old_practice_run_summaries_are_removed(env):
    settings, _ = env
    runs = items.dry_run_root(settings) / "runs"
    for name in (f"run-{days_ago(20)}-061700.json", f"run-{days_ago(20)}-061700-2.json",
                 f"run-{days_ago(3)}-061700.json", "notes.json"):
        items.write_json(runs / name, {})
    res = run(settings)
    assert sorted(res["practice_run_summaries"]) == sorted([f"run-{days_ago(20)}-061700.json",
                                                  f"run-{days_ago(20)}-061700-2.json"])
    assert sorted(p.name for p in runs.iterdir()) == sorted([f"run-{days_ago(3)}-061700.json", "notes.json"])


def test_dry_run_folder_that_is_the_real_output_folder_is_skipped(tmp_path):
    settings, _ = make_settings(tmp_path, pipeline={"dry_run_output_dir": str(tmp_path / "output")})
    item = practice_item(tmp_path / "output", days_ago(90))
    res = run(settings)
    assert res["practice_items"] == [] and item.exists()
    assert any("real output folder" in p for p in res.problems)


# ---------------------------------------------------------------- robustness

def test_a_settings_mistake_in_one_rule_does_not_stop_the_others(tmp_path):
    settings, _ = make_settings(tmp_path, service={"rejected_keep_days": "soon"})
    paths = service_paths(settings)
    reject(paths.rejected, f"{days_ago(90)}-old.csv")
    old = practice_item(items.dry_run_root(settings), days_ago(90))
    res = run(settings)
    assert res["rejected_uploads"] == [] and res["practice_items"] == [old.name]
    assert any(p.startswith("rejected uploads: not cleaned") for p in res.problems)


def test_nothing_to_do_on_a_fresh_server(env):
    settings, _ = env
    res = run(settings)
    assert res == {"rejected_uploads": [], "old_exports": [], "practice_items": [], "practice_run_summaries": []}
    assert res.freed_bytes == 0 and res.problems == [] and cleanup.describe(res) == ""


def test_result_holds_only_removals(tmp_path):
    """The tick counts every value for status.txt ("Old files tidied away"), so problems and
    bytes stay out of the dict."""
    settings, _ = make_settings(tmp_path, pipeline={"dry_run_output_dir": str(tmp_path / "output")})
    reject(service_paths(settings).rejected, f"{days_ago(90)}-old.csv")
    res = run(settings)
    assert tuple(res) == cleanup.CleanupResult.KEYS and all(isinstance(v, list) for v in res.values())
    assert res["rejected_uploads"] == [f"{days_ago(90)}-old.csv"] and len(res.problems) == 1


def test_defaults():
    assert cleanup.DEFAULTS == {"rejected_max_mb": 200, "rejected_keep_days": 30, "dry_run_keep_days": 14}
    assert cleanup.RESEARCH_DEFAULTS["keep_days"] == 180
