"""service/limits.py: the admin-only limits file, read fail-closed. Everything in tmp_path."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
import yaml

from core.config import ROOT
from service import limits

EXAMPLE = ROOT / "config" / "limits.example.yaml"


def settings_for(path: Path, require_root: bool = False) -> dict:
    return {"control": {"limits_path": str(path), "limits_require_root": require_root}}


def write(tmp_path: Path, doc, name: str = "limits.yaml", mode: int = 0o644) -> Path:
    p = tmp_path / name
    p.write_text(doc if isinstance(doc, str) else yaml.safe_dump(doc), encoding="utf-8")
    os.chmod(p, mode)
    return p


def test_the_shipped_example_loads_and_matches_the_defaults(tmp_path):
    p = write(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    got = limits.load(settings_for(p))
    assert got.ok and got.reason is None and got.unknown == ()
    assert got.values == limits.DEFAULTS
    assert got.level2_allowed and not got.scale_up_allowed      # scale-ups ship switched off
    assert got.get("cap_ceiling_usd") == 100 and got.get("changes_per_hour") == 20
    assert got.get("page_redos_per_week") == 10 and got.get("run_now_per_day") == 3
    assert got.get("fixes_per_day") == 6                # watchdog fixes (user, 2026-10-21, D4.7)
    assert got.get("plan.max_cost_share_auto") == 1.0   # user, 2026-10-21: no cost gate
    assert got.get("scale_up.monthly_usd") == 6 and got.get("scale_up.per_theme_max_listings") == 8
    assert got.get("nope.nothing", "x") == "x"


def test_the_example_lists_every_key_the_code_reads():
    doc = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))

    def keys(d, prefix=""):
        out = set()
        for k, v in d.items():
            out.add(prefix + k)
            if isinstance(v, dict):
                out |= keys(v, prefix + k + ".")
        return out

    assert keys(doc) == keys(limits.DEFAULTS)


def test_missing_file_fails_closed_with_a_reason(tmp_path):
    got = limits.load(settings_for(tmp_path / "nope.yaml"))
    assert not got.ok and got.reason == "limits_missing"
    assert not got.level2_allowed and not got.scale_up_allowed
    assert got.values == limits.DEFAULTS                 # reads still work: the shipped values, not in force
    assert "missing" in got.detail and str(tmp_path / "nope.yaml") in got.detail
    assert got.to_dict()["level2_allowed"] is False


@pytest.mark.parametrize("text", ["changes_per_hour: [unclosed", "{{{", "a: b: c"])
def test_broken_yaml_fails_closed(tmp_path, text):
    got = limits.load(settings_for(write(tmp_path, text)))
    assert not got.ok and got.reason == "limits_invalid" and not got.level2_allowed


def test_a_file_that_is_not_a_mapping_fails_closed(tmp_path):
    got = limits.load(settings_for(write(tmp_path, "- 1\n- 2\n")))
    assert not got.ok and got.reason == "limits_invalid"


def test_binary_garbage_fails_closed(tmp_path):
    p = tmp_path / "limits.yaml"
    p.write_bytes(b"\xff\xfe\x00bad")
    os.chmod(p, 0o644)
    assert limits.load(settings_for(p)).reason == "limits_invalid"


@pytest.mark.parametrize("doc", [
    {"changes_per_hour": "twenty"},
    {"changes_per_hour": True},                  # a bool is not a number
    {"changes_per_hour": 20.5},                  # a whole number
    {"changes_per_hour": -1},
    {"cap_ceiling_usd": 0},
    {"cap_ceiling_usd": 1_000_000},
    {"scale_up": {"enabled": "yes"}},            # only a real true / false
    {"scale_up": 3},                             # a section replaced by a value
    {"plan": {"max_cost_share_auto": 1.5}},
    {"capacity": {"min_disk_free_mb": "lots"}},
    {"schema": "limits/v9"},
])
def test_one_bad_value_breaks_the_whole_file(tmp_path, doc):
    got = limits.load(settings_for(write(tmp_path, doc)))
    assert not got.ok and got.reason == "limits_invalid"
    assert not got.level2_allowed and not got.scale_up_allowed
    assert got.values == limits.DEFAULTS


def test_keys_left_out_take_the_defaults(tmp_path):
    got = limits.load(settings_for(write(tmp_path, {"changes_per_hour": 5, "scale_up": {"monthly_usd": 2}})))
    assert got.ok
    assert got.get("changes_per_hour") == 5 and got.get("scale_up.monthly_usd") == 2.0
    assert got.get("scale_up.packs_per_week") == 3 and got.get("cap_ceiling_usd") == 100
    assert limits.load(settings_for(write(tmp_path, "", name="empty.yaml"))).values == limits.DEFAULTS


def test_unknown_keys_are_ignored_and_never_loosen_anything(tmp_path):
    doc = copy.deepcopy(limits.DEFAULTS)
    doc["scale_up"]["allowed_kinds"] = ["pack_size", "fresh_volume"]
    doc["cap_ceiling_usd_for_hermes"] = 5000
    got = limits.load(settings_for(write(tmp_path, doc)))
    assert got.ok and got.get("cap_ceiling_usd") == 100
    assert got.unknown == ("cap_ceiling_usd_for_hermes", "scale_up.allowed_kinds")
    assert "cap_ceiling_usd_for_hermes" not in got.values


def test_scale_ups_need_enabled_true_in_a_good_file(tmp_path):
    got = limits.load(settings_for(write(tmp_path, {"scale_up": {"enabled": True}})))
    assert got.ok and got.scale_up_allowed


def test_a_world_writable_file_is_not_admin_only(tmp_path):
    p = write(tmp_path, EXAMPLE.read_text(encoding="utf-8"), mode=0o666)
    got = limits.load(settings_for(p))
    assert not got.ok and got.reason == "limits_not_admin_only" and not got.level2_allowed


def test_require_root_wants_root_ownership(tmp_path):
    p = write(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    if os.geteuid() == 0:                        # running as root: give the file to someone else
        os.chown(p, 1000, 1000)
    got = limits.load(settings_for(p, require_root=True))
    assert not got.ok and got.reason == "limits_not_admin_only"
    assert "root" in got.detail


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to make a root-owned file")
def test_require_root_accepts_a_root_owned_0644_file(tmp_path):
    d = tmp_path / "etc"
    d.mkdir()
    os.chmod(d, 0o755)
    p = write(d, EXAMPLE.read_text(encoding="utf-8"))
    assert limits.load(settings_for(p, require_root=True)).ok
    os.chmod(p, 0o664)                           # group-writable
    assert limits.load(settings_for(p, require_root=True)).reason == "limits_not_admin_only"
    os.chmod(p, 0o644)
    os.chmod(d, 0o775)                           # its folder group-writable: the file could be replaced
    assert limits.load(settings_for(p, require_root=True)).reason == "limits_not_admin_only"


def test_a_folder_is_not_a_limits_file(tmp_path):
    d = tmp_path / "limits.yaml"
    d.mkdir()
    got = limits.load(settings_for(d))
    assert not got.ok and got.reason in ("limits_not_admin_only", "limits_unreadable")


def test_a_huge_file_is_refused(tmp_path):
    p = write(tmp_path, "# x\n" * 20000)
    assert limits.load(settings_for(p)).reason == "limits_invalid"


def test_path_comes_from_settings_with_the_server_default():
    assert limits.limits_path({}) == Path("/etc/botsy/limits.yaml")
    assert limits.limits_path(None) == Path("/etc/botsy/limits.yaml")
    assert limits.limits_path({"control": {"limits_path": "/x/y.yaml"}}) == Path("/x/y.yaml")


def test_reading_never_changes_the_file_and_there_is_no_writer(tmp_path):
    p = write(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    before = (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
    for _ in range(3):
        limits.load(settings_for(p))
    assert (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode) == before
    assert not [n for n in dir(limits) if any(w in n.lower() for w in ("write", "save", "dump", "store"))]


def test_load_with_an_explicit_path_and_no_settings(tmp_path):
    p = write(tmp_path, {"changes_per_hour": 7})
    got = limits.load(None, path=p)
    # No settings means the server default: root ownership is required. Owned by root (tests run
    # as root) it loads; otherwise it fails closed. Never anything in between.
    if got.ok:
        assert got.get("changes_per_hour") == 7
    else:
        assert got.reason == "limits_not_admin_only" and got.get("changes_per_hour") == 20


def test_the_file_actually_read_is_checked_too(tmp_path, monkeypatch):
    """A file swapped in after the path check (a race) is caught by the check on the open file."""
    p = write(tmp_path, EXAMPLE.read_text(encoding="utf-8"), mode=0o666)
    monkeypatch.setattr(limits, "_admin_problem", lambda path, require_root: None)   # the path check "passed"
    got = limits.load(settings_for(p))
    assert not got.ok and got.reason == "limits_not_admin_only" and "world-writable" in got.detail


def test_fixes_per_day_is_checked_like_every_limit(tmp_path):
    assert limits.load(settings_for(write(tmp_path, "schema: limits/v1\nfixes_per_day: 2\n"))).get("fixes_per_day") == 2
    for bad in ("-1", "101", "true", "'6'"):
        got = limits.load(settings_for(write(tmp_path, f"schema: limits/v1\nfixes_per_day: {bad}\n")))
        assert not got.ok, bad
