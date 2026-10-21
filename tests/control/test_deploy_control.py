"""deploy/lightsail/botsy-control-read, -act and sudoers.d/botsy-control: the hermes user may run
exactly these two commands as botsy, with no arguments, no password and a reset environment;
the wrappers take no arguments, run only as botsy and start Botsy with an empty environment."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from core.config import ROOT

DEPLOY = ROOT / "deploy" / "lightsail"
WRAPPERS = {"read": DEPLOY / "botsy-control-read", "act": DEPLOY / "botsy-control-act"}
SUDOERS = DEPLOY / "sudoers.d" / "botsy-control"


@pytest.mark.parametrize("server", ["read", "act"])
def test_wrapper_text(server):
    p = WRAPPERS[server]
    assert os.access(p, os.X_OK)
    text = p.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert subprocess.run(["sh", "-n", str(p)], capture_output=True).returncode == 0
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines[-2:] == ["exec /usr/bin/env -i PATH=/usr/bin:/bin HOME=/opt/botsy LANG=C.UTF-8 \\",
                          f"/opt/botsy/.venv/bin/python -E -s -B -m control {server}"]
    assert "cd /opt/botsy" in lines and "umask 027" in lines and "set -eu" in lines
    assert '"$@"' not in text and "$*" not in text            # nothing the caller passes reaches Botsy


@pytest.mark.parametrize("server", ["read", "act"])
def test_wrapper_refuses_arguments(server):
    r = subprocess.run(["sh", str(WRAPPERS[server]), "--settings", "/tmp/x"], capture_output=True, text=True)
    assert r.returncode == 64 and "takes no arguments" in r.stderr


@pytest.mark.parametrize("server", ["read", "act"])
def test_wrapper_runs_only_as_botsy(server):
    import pwd

    if pwd.getpwuid(os.geteuid()).pw_name == "botsy":
        pytest.skip("this test runs as botsy")
    r = subprocess.run(["sh", str(WRAPPERS[server])], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 77 and "runs only as the botsy user" in r.stderr


def _rule_lines() -> list[str]:
    return [ln.strip() for ln in SUDOERS.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def test_sudoers_text():
    assert _rule_lines() == [
        'Cmnd_Alias BOTSY_CONTROL = /usr/local/bin/botsy-control-read "", /usr/local/bin/botsy-control-act ""',
        'Defaults!BOTSY_CONTROL env_reset, !setenv, !requiretty, !log_input, !log_output, secure_path="/usr/bin:/bin"',
        "hermes ALL = (botsy) NOPASSWD: NOSETENV: BOTSY_CONTROL",
    ]
    assert SUDOERS.read_text(encoding="utf-8").endswith("\n")       # sudo ignores a last line without one


@pytest.mark.skipif(shutil.which("visudo") is None, reason="visudo is not installed")
def test_sudoers_passes_visudo(tmp_path):
    copy = tmp_path / "botsy-control"
    shutil.copy(SUDOERS, copy)
    os.chmod(copy, 0o440)
    r = subprocess.run(["visudo", "-c", "-f", str(copy)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "parsed OK" in r.stdout


@pytest.mark.skipif(shutil.which("cvtsudoers") is None, reason="cvtsudoers is not installed")
def test_sudoers_means_exactly_one_rule(tmp_path):
    r = subprocess.run(["cvtsudoers", "-f", "json", str(SUDOERS)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert doc["Command_Aliases"] == {"BOTSY_CONTROL": [{"command": '/usr/local/bin/botsy-control-read ""'},
                                                        {"command": '/usr/local/bin/botsy-control-act ""'}]}
    (spec,) = doc["User_Specs"]
    assert spec["User_List"] == [{"username": "hermes"}]
    (cmnd,) = spec["Cmnd_Specs"]
    assert cmnd["runasusers"] == [{"username": "botsy"}] and "runasgroups" not in cmnd
    assert {"authenticate": False} in cmnd["Options"] and {"setenv": False} in cmnd["Options"]
    assert cmnd["Commands"] == [{"cmndalias": "BOTSY_CONTROL"}]
    (defaults,) = doc["Defaults"]
    assert defaults["Binding"] == [{"cmndalias": "BOTSY_CONTROL"}]
    for opt in ({"env_reset": True}, {"setenv": False}, {"requiretty": False}, {"log_input": False},
                {"log_output": False}):
        assert opt in defaults["Options"]


def test_hermes_config_calls_exactly_these_wrappers():
    """When the Hermes install files exist, its two MCP servers reach exactly these wrappers: by
    default through botsy-control-connect and a systemd socket unit per server that runs the
    wrapper as botsy (no sudo), or with the sudo bridge through `sudo -n -u botsy <wrapper>`."""
    import yaml

    hermes = ROOT / "deploy" / "hermes"
    tpl = hermes / "config.yaml.template"
    if not tpl.exists():
        pytest.skip("deploy/hermes is not built yet")
    text = tpl.read_text(encoding="utf-8")
    assert "python -m control" not in text
    servers = (yaml.safe_load(text) or {}).get("mcp_servers") or {}
    assert set(servers) == {"botsy-read", "botsy-act"}
    for name, conf in servers.items():
        which = name.split("-")[1]
        cmd = [conf["command"], *conf.get("args", [])]
        assert cmd in (["/usr/local/bin/botsy-control-connect", which],
                       ["/usr/bin/sudo", "-n", "-u", "botsy", f"/usr/local/bin/botsy-control-{which}"],
                       ["sudo", "-n", "-u", "botsy", f"/usr/local/bin/botsy-control-{which}"]), (name, cmd)
        assert not conf.get("env")
        assert (conf.get("sampling") or {}).get("enabled") is False
        assert (conf.get("elicitation") or {}).get("enabled") is False
        unit = hermes / "systemd" / f"botsy-control-{which}@.service"
        if cmd[0].endswith("botsy-control-connect"):
            assert unit.exists(), unit
            lines = [ln.strip() for ln in unit.read_text(encoding="utf-8").splitlines()]
            assert f"ExecStart=/usr/local/bin/botsy-control-{which}" in lines
            assert "User=botsy" in lines and "StandardInput=socket" in lines and "StandardOutput=socket" in lines
            assert not any(ln.startswith("Environment") for ln in lines)
