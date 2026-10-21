"""Botsy's clock: the systemd timers run on New York time whatever the server's zone, and
setup.sh sets the server clock from --timezone (docs/SERVER.md section 6)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from core.config import DEFAULT_TIMEZONE, ROOT

DEPLOY = ROOT / "deploy" / "lightsail"
SETUP = DEPLOY / "setup.sh"
UNITS_DIR = DEPLOY / "systemd"
TIMERS = {"botsy-daily.timer": "*-*-* 06:17:00 America/New_York",
          "botsy-digest.timer": "*-*-* 07:07:00 America/New_York",
          "botsy-weekly.timer": "Sat *-*-* 09:07:00 America/New_York",
          "botsy-plan.timer": "Sun *-*-* 20:00:00 America/New_York",
          "botsy-plan-check.timer": "*-*-* *:05:00 America/New_York"}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _unit(name: str) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for line in _text(UNITS_DIR / name).splitlines():
        if "=" in line and not line.lstrip().startswith(("#", "[")):
            k, v = line.split("=", 1)
            keys.setdefault(k.strip(), []).append(v.strip())
    return keys


def _setup_lists() -> tuple[list[str], list[str]]:
    text = _text(SETUP)
    units = re.search(r"UNITS=\(([^)]*)\)", text).group(1).split()
    enable = re.search(r"ENABLE=\(([^)]*)\)", text).group(1).split()
    return units, enable


@pytest.mark.parametrize("name, spec", sorted(TIMERS.items()))
def test_timers_name_new_york_time(name, spec):
    timer = _unit(name)
    assert timer["OnCalendar"] == [spec]
    assert timer["Persistent"] == ["true"]
    assert timer["Unit"] == [name.replace(".timer", ".service")]
    assert "New York" in timer["Description"][0]
    assert spec.endswith(" " + DEFAULT_TIMEZONE)


def test_every_installed_timer_is_one_of_these_and_the_plan_timers_are_installed():
    units, enable = _setup_lists()
    assert {u for u in units if u.endswith(".timer")} == set(TIMERS)
    assert set(TIMERS) <= set(enable)
    for name in ("botsy-plan.service", "botsy-plan-check.service"):
        assert name in units and name not in enable              # the timers start them
        assert (UNITS_DIR / name).is_file()
    assert "TODO (Sunday plan" not in _text(SETUP)


def test_the_plan_services_run_the_plan_commands_hardened():
    prepare, check = _unit("botsy-plan.service"), _unit("botsy-plan-check.service")
    assert prepare["ExecStart"] == ["/opt/botsy/.venv/bin/python -m service plan prepare"]
    assert check["ExecStart"] == ["/opt/botsy/.venv/bin/python -m service plan check"]
    for keys in (prepare, check):
        assert keys["User"] == ["botsy"] and keys["WorkingDirectory"] == ["/opt/botsy"]
        assert keys["Type"] == ["oneshot"] and "Restart" not in keys
        for k, v in {"NoNewPrivileges": "yes", "ProtectSystem": "full", "PrivateTmp": "yes", "ProtectHome": "yes",
                     "PrivateDevices": "yes", "RestrictSUIDSGID": "yes", "ProtectKernelTunables": "yes",
                     "LockPersonality": "yes", "RestrictNamespaces": "yes"}.items():
            assert keys.get(k) == [v], k
        assert keys.get("CapabilityBoundingSet") == [""]
        rw = keys["ReadWritePaths"][0].split()
        assert "/opt/botsy" in rw and "-/srv/botsy-inbox/outbox" in rw
    # The check can run a started plan's first check (a forced tick that ingests uploads too).
    assert "-/srv/botsy-inbox/incoming" in check["ReadWritePaths"][0].split()
    assert "-/srv/botsy-inbox/incoming" not in prepare["ReadWritePaths"][0].split()


def test_the_plan_schedule_matches_the_settings_and_the_daily_log():
    from core.config import SCHEDULE_DEFAULTS
    from service import digest

    assert SCHEDULE_DEFAULTS["plan_post"] == "Sun 20:00" and TIMERS["botsy-plan.timer"].startswith("Sun *-*-* 20:00:00")
    assert digest.SCHEDULE["weekly_plan"] == (6, 20, 0)
    assert "prepare|check|status|approve|hold|start-now|again|auto" in _text(ROOT / "service" / "plan.py")


def _base_time_supported() -> bool:
    if not shutil.which("systemd-analyze"):
        return False
    res = subprocess.run(["systemd-analyze", "calendar", "--base-time=2026-10-24 12:00:00 UTC", "--", "*-*-* 06:17:00"],
                         text=True, capture_output=True, timeout=30, env={**os.environ, "TZ": "UTC"})
    return res.returncode == 0


@pytest.mark.skipif(not _base_time_supported(), reason="needs systemd-analyze with --base-time (systemd 254+)")
@pytest.mark.parametrize("base, want", [
    # The Sunday plan through both changes of clocks (Nov 1, 2026 and Mar 14, 2027).
    ("2026-10-24 12:00:00 UTC", ["Mon 2026-10-26 00:00:00", "Mon 2026-11-02 01:00:00", "Mon 2026-11-09 01:00:00"]),
    ("2027-03-07 12:00:00 UTC", ["Mon 2027-03-08 01:00:00", "Mon 2027-03-15 00:00:00", "Mon 2027-03-22 00:00:00"]),
])
def test_the_sunday_plan_timer_through_the_clock_changes(base, want):
    res = subprocess.run(["systemd-analyze", "calendar", "--iterations=3", f"--base-time={base}", "--",
                          TIMERS["botsy-plan.timer"]], text=True, capture_output=True, timeout=30,
                         env={**os.environ, "TZ": "UTC"})
    assert res.returncode == 0, res.stderr
    got = re.findall(r"(?:Next elapse|Iteration #\d): (\w+ \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC", res.stdout)
    assert got == want


@pytest.mark.skipif(not _base_time_supported(), reason="needs systemd-analyze with --base-time (systemd 254+)")
def test_the_hourly_plan_check_runs_at_five_past_through_the_clock_change():
    res = subprocess.run(["systemd-analyze", "calendar", "--iterations=4", "--base-time=2026-11-01 04:30:00 UTC",
                          "--", TIMERS["botsy-plan-check.timer"]], text=True, capture_output=True, timeout=30,
                         env={**os.environ, "TZ": "UTC"})
    assert res.returncode == 0, res.stderr
    got = re.findall(r"(?:Next elapse|Iteration #\d): \w+ \d{4}-\d{2}-\d{2} (\d{2}:\d{2}):\d{2} UTC", res.stdout)
    assert got and all(t.endswith(":05") for t in got)
    # Mon 08:05 New York time, the check that starts a plan by itself, in both seasons:
    for base, hour in (("2026-10-26 11:30:00 UTC", "12"), ("2026-11-02 12:30:00 UTC", "13")):
        res = subprocess.run(["systemd-analyze", "calendar", f"--base-time={base}", "--",
                              "Mon *-*-* 08:05:00 America/New_York"], text=True, capture_output=True, timeout=30,
                             env={**os.environ, "TZ": "UTC"})
        assert f" {hour}:05:00 UTC" in res.stdout


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="needs systemd-analyze")
@pytest.mark.parametrize("name, spec", sorted(TIMERS.items()))
def test_systemd_accepts_the_calendar_lines(name, spec):
    env = {**os.environ, "TZ": "UTC"}      # the "Next elapse" lines are in the caller's zone
    res = subprocess.run(["systemd-analyze", "calendar", "--iterations=2", "--", spec],
                         text=True, capture_output=True, timeout=30, env=env)
    assert res.returncode == 0, res.stderr
    assert f"Normalized form: {spec}" in res.stdout
    # The next runs are given in UTC and differ from New York's wall time by 4 or 5 hours.
    hours = [int(h) for h in re.findall(r"Next elapse: \w+ \d{4}-\d{2}-\d{2} (\d{2}):\d{2}:\d{2} UTC", res.stdout)]
    if "*:" in spec:                       # the hourly plan check: every hour, the zone only fixes the minute
        assert hours
        return
    wall = int(re.search(r"(\d{2}):\d{2}:\d{2} America", spec).group(1))
    assert hours and all((h - wall) % 24 in (4, 5) for h in hours)


# --- setup.sh -----------------------------------------------------------------------------
# The definitions of setup.sh (without its last line) with shims, so read_options and
# set_timezone run as a normal user in tmp_path: id -u says root, timedatectl is a fake
# that records what it was asked, and the zone files are a small fake tzdata tree.
HARNESS = r'''
source "$SCRIPT"
CONF_FILE=$T/setup.conf; ZONEINFO=$T/zoneinfo
id() { if [[ $# -eq 1 && $1 == -u ]]; then echo 0; else command id "$@"; fi; }
timedatectl() {
  case "$1" in
    show) cat "$T/current_zone" 2> /dev/null ;;
    set-timezone) printf '%s\n' "$2" > "$T/set_zone"; printf '%s\n' "$2" > "$T/current_zone" ;;
    *) return 1 ;;
  esac
}
read_options "$@"
echo "TIMEZONE=$TIMEZONE KEY_ONLY=$KEY_ONLY"
set_timezone
'''

CALENDAR_HARNESS = r'''
source "$SCRIPT"
SRC_DIR=$T/src
check_calendars
echo "CALENDARS OK"
'''


def _defs(tmp_path: Path) -> Path:
    script = tmp_path / "setup-defs.sh"
    lines = _text(SETUP).rstrip().splitlines()
    assert lines[-1] == 'main "$@"; exit'
    script.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    return script


def _run(tmp_path: Path, harness: str, *args: str, conf: str | None = None, current: str = "Etc/UTC"):
    script = _defs(tmp_path)
    for zone in ("America/New_York", "Europe/London", "Etc/UTC", "UTC"):
        (tmp_path / "zoneinfo" / zone).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "zoneinfo" / zone).write_text("TZif", encoding="utf-8")
    if conf is not None:
        (tmp_path / "setup.conf").write_text(conf, encoding="utf-8")
    (tmp_path / "current_zone").write_text(current + "\n", encoding="utf-8")
    env = {"PATH": os.environ["PATH"], "SCRIPT": str(script), "T": str(tmp_path),
           "TMPDIR": str(tmp_path), "HOME": str(tmp_path)}
    return subprocess.run(["bash", "-c", harness, "setup", *args], env=env, text=True,
                          capture_output=True, timeout=60)


needs_bash = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
CONF = "REPO_URL=https://github.com/example/botsy.git\nBRANCH=main\n"


@needs_bash
def test_timezone_defaults_to_new_york_and_is_applied(tmp_path):
    res = _run(tmp_path, HARNESS, conf=CONF)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "TIMEZONE=America/New_York KEY_ONLY=1" in res.stdout
    assert (tmp_path / "set_zone").read_text(encoding="utf-8").strip() == "America/New_York"
    assert "was Etc/UTC" in res.stdout


@needs_bash
def test_timezone_option_wins_over_the_remembered_one_and_means_a_full_run(tmp_path):
    res = _run(tmp_path, HARNESS, "--timezone", "Europe/London", conf=CONF + "TIMEZONE=America/New_York\n")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "TIMEZONE=Europe/London KEY_ONLY=0" in res.stdout
    assert (tmp_path / "set_zone").read_text(encoding="utf-8").strip() == "Europe/London"


@needs_bash
def test_remembered_timezone_is_used_and_an_unchanged_clock_is_left_alone(tmp_path):
    res = _run(tmp_path, HARNESS, conf=CONF + "TIMEZONE=Europe/London\n", current="Europe/London")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "TIMEZONE=Europe/London" in res.stdout and "Already Europe/London" in res.stdout
    assert not (tmp_path / "set_zone").exists()


@needs_bash
@pytest.mark.parametrize("bad", ["Mars/Olympus", "../../etc/passwd", "America/New York", "Europe/London/../Paris",
                                 "$(id)", "a" * 10 + "/b/c/d"])
def test_a_wrong_timezone_is_refused_before_anything_runs(tmp_path, bad):
    res = _run(tmp_path, HARNESS, "--timezone", bad, conf=CONF)
    assert res.returncode == 1 and "is not a time zone name" in res.stderr
    assert not (tmp_path / "set_zone").exists()


@needs_bash
def test_setup_remembers_the_timezone_in_its_conf_file_and_passes_it_on():
    text = _text(SETUP)
    assert "printf 'REPO_URL=%s\\nBRANCH=%s\\nTIMEZONE=%s\\n'" in text
    assert "TIMEZONE) [[ -n $TIMEZONE ]] || TIMEZONE=$value ;;" in text
    assert '--timezone "$TIMEZONE"' in text                     # the re-exec from root's copy
    assert "ZONEINFO=/usr/share/zoneinfo" in text               # zone names checked against root's tzdata
    main = text[text.index("\nmain() {"):]
    assert main.index("set_timezone") < main.index("install_services")


@needs_bash
@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="needs systemd-analyze")
def test_setup_checks_the_calendar_lines_with_systemd_analyze(tmp_path):
    src = tmp_path / "src" / "systemd"
    src.mkdir(parents=True)
    for unit in UNITS_DIR.iterdir():
        shutil.copy(unit, src / unit.name)
    ok = _run(tmp_path, CALENDAR_HARNESS)
    assert ok.returncode == 0 and "CALENDARS OK" in ok.stdout, ok.stdout + ok.stderr
    broken = src / "botsy-daily.timer"
    broken.write_text(_text(broken).replace("America/New_York", "Mars/Olympus"), encoding="utf-8")
    bad = _run(tmp_path, CALENDAR_HARNESS)
    assert bad.returncode == 1 and "systemd does not accept the time in botsy-daily.timer" in bad.stderr
    # A second OnCalendar line is checked too, not only the first.
    weekly = src / "botsy-weekly.timer"
    shutil.copy(UNITS_DIR / "botsy-daily.timer", broken)
    weekly.write_text(_text(weekly).replace("Persistent=true", "OnCalendar=Sat *-*-* 25:07:00\nPersistent=true"),
                      encoding="utf-8")
    second = _run(tmp_path, CALENDAR_HARNESS)
    assert second.returncode == 1 and "botsy-weekly.timer: 'Sat *-*-* 25:07:00'" in second.stderr
    weekly.write_text("[Timer]\nPersistent=true\n", encoding="utf-8")
    none = _run(tmp_path, CALENDAR_HARNESS)
    assert none.returncode == 1 and "botsy-weekly.timer has no OnCalendar line" in none.stderr
