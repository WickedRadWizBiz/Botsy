"""config/settings.local.yaml merge (docs/SERVER.md section 14) and the suite's isolation from it.

Every test points core.config.CONFIG_DIR at tmp_path, so the repo's config/ is never written.
"""
from __future__ import annotations

import logging
import os
import shutil

import pytest
import yaml

from core import config


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    """A config dir in tmp_path holding a copy of the real settings.yaml; the merge is on."""
    d = tmp_path / "config"
    d.mkdir()
    shutil.copy(config.ROOT / "config" / "settings.yaml", d / "settings.yaml")
    monkeypatch.setattr(config, "CONFIG_DIR", d)
    monkeypatch.setattr(config, "_logged", set())
    monkeypatch.delenv(config.IGNORE_LOCAL_ENV, raising=False)
    return d


def write_local(d, data) -> None:
    text = data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False)
    (d / "settings.local.yaml").write_text(text, encoding="utf-8")


def base(d) -> dict:
    return yaml.safe_load((d / "settings.yaml").read_text(encoding="utf-8"))


def test_local_file_is_deep_merged_over_settings_yaml(cfgdir):
    write_local(cfgdir, {"service": {"live": True, "settle_s": 60},
                         "spend": {"monthly_cap_usd": 25},
                         "shops": [{"id": "main", "name": "Sunny Prints", "niches": ["stickers"]}]})
    s = config.load_settings()
    b = base(cfgdir)
    assert s["service"]["live"] is True and s["service"]["settle_s"] == 60
    # Sibling keys of a merged section stay; untouched sections are unchanged.
    assert s["service"]["inbox_dir"] == b["service"]["inbox_dir"]
    assert s["service"]["search_list"] == b["service"]["search_list"]
    assert s["research"] == b["research"] and s["compliance"] == b["compliance"]
    assert s["spend"]["monthly_cap_usd"] == 25
    assert s["shops"] == [{"id": "main", "name": "Sunny Prints", "niches": ["stickers"]}]   # lists replace


def test_fresh_dict_every_call(cfgdir):
    write_local(cfgdir, {"service": {"live": True}})
    first = config.load_settings()
    first["service"]["live"] = "changed"
    first["research"]["min_listings_per_pattern"] = 1
    again = config.load_settings()
    assert again["service"]["live"] is True and again["research"]["min_listings_per_pattern"] == 10


def test_explicit_path_reads_only_that_file(cfgdir):
    write_local(cfgdir, {"service": {"live": True}})
    s = config.load_settings(cfgdir / "settings.yaml")
    assert s == base(cfgdir) and s["service"]["live"] is False


def test_ignore_env_turns_the_merge_off(cfgdir, monkeypatch):
    write_local(cfgdir, {"service": {"live": True}})
    for value in ("1", "true", "YES"):
        monkeypatch.setenv(config.IGNORE_LOCAL_ENV, value)
        assert config.load_settings() == base(cfgdir)
    monkeypatch.setenv(config.IGNORE_LOCAL_ENV, "0")
    assert config.load_settings()["service"]["live"] is True


def test_no_local_file_is_just_settings_yaml(cfgdir):
    assert config.local_settings_path() is None
    assert config.load_settings() == base(cfgdir)


def test_empty_file_and_empty_section_change_nothing(cfgdir):
    write_local(cfgdir, "# all lines commented out\n")
    assert config.load_settings() == base(cfgdir)
    # `service:` with every line under it commented out keeps the section.
    write_local(cfgdir, "service:\n  # live: true\nspend:\n  monthly_cap_usd: null\n")
    s = config.load_settings()
    assert s["service"] == base(cfgdir)["service"]
    assert s["spend"]["monthly_cap_usd"] is None   # a null leaf still means null


@pytest.mark.parametrize("text, words", [("service: [live\n", "not valid YAML"),
                                         ("- live\n- true\n", "YAML mapping")])
def test_broken_local_file_is_a_setup_error(cfgdir, text, words):
    write_local(cfgdir, text)
    with pytest.raises(ValueError, match=words):
        config.load_settings()


def test_yaml_error_does_not_quote_the_file(cfgdir):
    """PyYAML quotes the broken lines; those must not reach logs or status messages."""
    write_local(cfgdir, "service:\n  live: [true\n  note: private-text-123\n")
    with pytest.raises(ValueError) as info:
        config.load_settings()
    text = str(info.value)
    assert "line 3, column" in text and "private-text-123" not in text


@pytest.mark.parametrize("text, key", [("service: true\n", "service"),
                                       ("compliance: off\n", "compliance"),
                                       ("research: [10, 3]\n", "research"),
                                       ("shops: main\n", "shops")])
def test_a_section_replaced_by_one_value_is_a_setup_error(cfgdir, text, key):
    write_local(cfgdir, text)
    with pytest.raises(ValueError, match=f"`{key}:`") as info:
        config.load_settings()
    text = str(info.value).split(": ", 1)[1]                                 # after the file name
    assert "main" not in text and "10" not in text and "True" not in text  # the value is not quoted


def test_a_value_below_the_top_level_still_replaces(cfgdir):
    write_local(cfgdir, "research:\n  history: 5\nqa:\n  mockups:\n    fonts: null\n")
    s = config.load_settings()
    assert s["research"]["history"] == 5 and s["qa"]["mockups"]["fonts"] is None


def test_an_empty_list_line_keeps_the_list(cfgdir):
    write_local(cfgdir, "shops:\n  # - id: main\n")
    assert config.load_settings()["shops"] == base(cfgdir)["shops"]


@pytest.mark.parametrize("text", [
    "service:\n  live: false\n\n# spend:\n  monthly_cap_usd: 25\n",   # only the cap line un-commented
    "service:\n  live: false\n# spend:\nmonthly_cap_usd: 25\n",          # and moved to the left edge
])
def test_a_misplaced_spending_cap_still_caps(cfgdir, caplog, text):
    write_local(cfgdir, text)
    with caplog.at_level(logging.WARNING, logger="core.config"):
        s = config.load_settings()
    assert s["spend"]["monthly_cap_usd"] == 25
    assert any("belongs under `spend:`" in r.getMessage() for r in caplog.records)


def test_spend_cap_in_its_place_wins_over_a_misplaced_one(cfgdir):
    write_local(cfgdir, {"service": {"monthly_cap_usd": 50}, "spend": {"monthly_cap_usd": 20}})
    assert config.load_settings()["spend"]["monthly_cap_usd"] == 20


def test_pipeline_cli_reports_a_broken_local_file_as_a_settings_problem(cfgdir, capsys):
    from pipeline.__main__ import main

    write_local(cfgdir, "service: [live\n")
    assert main(["status"]) == 2
    assert "cannot read the settings" in capsys.readouterr().err


def test_service_cli_reports_a_broken_local_file_as_a_settings_problem(cfgdir, capsys):
    from service.__main__ import main

    write_local(cfgdir, "service: [live\n")
    assert main(["status"]) == 2
    assert "cannot read the settings" in capsys.readouterr().err


def test_merged_file_is_logged_once(cfgdir, caplog):
    write_local(cfgdir, {"service": {"live": True}})
    with caplog.at_level(logging.INFO, logger="core.config"):
        config.load_settings()
        config.load_settings()
    lines = [r.getMessage() for r in caplog.records if "settings.local.yaml" in r.getMessage()]
    assert len(lines) == 1 and str(cfgdir / "settings.local.yaml") in lines[0]


def test_local_file_cannot_lower_the_compliance_floors(cfgdir, caplog):
    write_local(cfgdir, {
        "research": {"min_listings_per_pattern": 5, "min_shops_per_pattern": 1,
                     "stages": {"refined": {"min_listings": 8, "min_shops": "2", "min_weeks_seen": 1},
                                "discovery": {"min_listings": "nine"}}},
        "compliance": {"publisher_mode": "active", "trademark_blocklist": "/tmp/empty.txt",
                       "ai_disclosure": "/tmp/none.txt"},
        "listing": {"always_disclose_ai": False, "quantity": 5},
    })
    b = base(cfgdir)
    with caplog.at_level(logging.WARNING, logger="core.config"):
        s = config.load_settings()
    r = s["research"]
    assert r["min_listings_per_pattern"] == 10 and r["min_shops_per_pattern"] == 3
    assert r["stages"]["refined"]["min_listings"] == 10 and r["stages"]["refined"]["min_shops"] == 3
    assert r["stages"]["refined"]["min_weeks_seen"] == 1          # not a compliance floor: allowed
    assert r["stages"]["discovery"]["min_listings"] == 10         # not a number: the floor
    assert s["compliance"] == b["compliance"] and s["compliance"]["publisher_mode"] == "draft_only"
    assert s["listing"]["always_disclose_ai"] is True and s["listing"]["quantity"] == 5
    warned = " ".join(rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING)
    for key in ("research.min_listings_per_pattern", "research.min_shops_per_pattern",
                "research.stages.refined.min_listings", "compliance.publisher_mode",
                "compliance.trademark_blocklist", "listing.always_disclose_ai"):
        assert key in warned, key


def test_stricter_local_values_are_kept(cfgdir):
    write_local(cfgdir, {"research": {"min_listings_per_pattern": 15, "min_shops_per_pattern": "4",
                                      "stages": {"refined": {"min_listings": 20}}},
                         "compliance": {"publisher_mode": "draft_only"}})
    s = config.load_settings()
    assert s["research"]["min_listings_per_pattern"] == 15 and s["research"]["min_shops_per_pattern"] == "4"
    assert s["research"]["stages"]["refined"]["min_listings"] == 20
    assert s["compliance"]["publisher_mode"] == "draft_only"


def test_floor_constants_match_the_rules():
    from core.briefs import HARD_FLOOR
    from research.defaults import SHOP_FLOOR

    assert config.LISTINGS_FLOOR == HARD_FLOOR == 10 and config.SHOPS_FLOOR == SHOP_FLOOR == 3


# ---------------------------------------------------------------- the suite's isolation (tests/conftest.py)

def test_suite_ignores_local_settings():
    assert os.environ.get(config.IGNORE_LOCAL_ENV) == "1"
    assert config.local_settings_path() is None


def test_suite_reads_only_settings_yaml_even_with_a_local_file(tmp_path, monkeypatch):
    d = tmp_path / "config"
    d.mkdir()
    shutil.copy(config.ROOT / "config" / "settings.yaml", d / "settings.yaml")
    write_local(d, {"service": {"live": True}, "research": {"min_listings_per_pattern": 50}})
    monkeypatch.setattr(config, "CONFIG_DIR", d)
    s = config.load_settings()          # the conftest's BOTSY_IGNORE_LOCAL_SETTINGS=1 applies
    assert s["service"]["live"] is False and s["research"]["min_listings_per_pattern"] == 10


def test_suite_settings_equal_the_committed_file():
    """Holds even on a machine whose config/ has a settings.local.yaml."""
    committed = yaml.safe_load((config.ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert config.load_settings() == committed
