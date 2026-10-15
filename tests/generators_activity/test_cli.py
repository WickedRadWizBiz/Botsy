"""CLI: python -m generators.activity_sheets --brief <file> [--index N | --all] [--out DIR] [--date D]."""
import json

import pytest

from core.items import read_manifest
from generators.activity_sheets.__main__ import main


def _brief_file(tmp_path, brief):
    other = dict(brief, brief_id="coloring_pages-2026-10-15-01", niche="coloring_pages")
    second = dict(brief, brief_id="activity_sheets-2026-10-15-02", theme="outer space", page_count=4)
    data = {"schema": "brief/v1", "niche": "activity_sheets", "created": "2026-10-15", "shop_id": "main",
            "briefs": [dict(brief, page_count=4), other, second]}
    path = tmp_path / "activity_sheets-2026-10-15.json"
    path.write_text(json.dumps(data))
    return path


def test_all_generates_every_activity_brief(tmp_path, brief, capsys):
    path = _brief_file(tmp_path, brief)
    out = tmp_path / "out"
    assert main(["--brief", str(path), "--all", "--out", str(out), "--date", "2026-10-15", "--dry-run"]) == 0
    printed = capsys.readouterr().out.split()
    assert len(printed) == 2
    themes = sorted(read_manifest(p)["theme"] for p in printed)
    assert themes == ["farm animals", "outer space"]
    assert all(read_manifest(p)["generator"]["dry_run"] for p in printed)


def test_index_picks_one_brief(tmp_path, brief, capsys):
    path = _brief_file(tmp_path, brief)
    assert main(["--brief", str(path), "--index", "2", "--out", str(tmp_path / "o"), "--date", "2026-10-15"]) == 0
    [d] = capsys.readouterr().out.split()
    assert read_manifest(d)["brief_id"] == "activity_sheets-2026-10-15-02"


def test_index_for_another_niche_or_out_of_range_is_rejected(tmp_path, brief):
    path = _brief_file(tmp_path, brief)
    for idx in ("1", "9"):
        with pytest.raises(SystemExit) as exc:
            main(["--brief", str(path), "--index", idx, "--out", str(tmp_path / "o")])
        assert exc.value.code == 2


def test_single_brief_json_and_blocked_brief(tmp_path, brief, capsys):
    good = tmp_path / "one.json"
    good.write_text(json.dumps(dict(brief, page_count=2)))
    assert main(["--brief", str(good), "--out", str(tmp_path / "o"), "--date", "2026-10-15"]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(dict(brief, theme="Minecraft adventure")))
    assert main(["--brief", str(bad), "--out", str(tmp_path / "o2"), "--date", "2026-10-15"]) == 1
    assert "blocked" in capsys.readouterr().err


def test_relative_brief_path_is_recorded_from_any_working_dir(tmp_path, brief, monkeypatch, capsys):
    """brief_file must not be a path relative to wherever the CLI happened to run."""
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "one.json").write_text(json.dumps(dict(brief, page_count=1)))
    monkeypatch.chdir(tmp_path)
    assert main(["--brief", "b/one.json", "--out", str(tmp_path / "o"), "--date", "2026-10-15"]) == 0
    [d] = capsys.readouterr().out.split()
    assert read_manifest(d)["brief_file"] == (tmp_path / "b" / "one.json").resolve().as_posix()


def test_list_of_briefs_is_accepted(tmp_path, brief, capsys):
    path = tmp_path / "list.json"
    path.write_text(json.dumps([dict(brief, page_count=1)]))
    assert main(["--brief", str(path), "--all", "--out", str(tmp_path / "o"), "--date", "2026-10-15"]) == 0
    assert len(capsys.readouterr().out.split()) == 1


@pytest.mark.parametrize("args", [["--date", "../up"], ["--date", "2026/10/15"]])
def test_bad_date_is_a_usage_error(tmp_path, brief, args):
    path = _brief_file(tmp_path, brief)
    with pytest.raises(SystemExit) as exc:
        main(["--brief", str(path), "--out", str(tmp_path / "o")] + args)
    assert exc.value.code == 2 and not (tmp_path / "o").exists()


def test_unreadable_brief_file_is_a_usage_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(SystemExit) as exc:
        main(["--brief", str(bad), "--out", str(tmp_path / "o")])
    assert exc.value.code == 2
