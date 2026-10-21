"""The Hermes Agent lock (deploy/hermes, docs/OVERSEER.md "Hermes setup").

Offline only: install_hermes.sh runs in its test mode (--root DIR --test-owner, which lays files
out under DIR and never fetches, starts or changes anything on this machine), and check_lock.py
judges good and broken configs and file layouts. The real Hermes probe is not run here (it needs
the pinned Hermes installed); its judgement is tested with recorded probe output.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from core.config import ROOT

HERMES = ROOT / "deploy" / "hermes"
LIGHTSAIL = ROOT / "deploy" / "lightsail"
INSTALL = HERMES / "install_hermes.sh"
USER_ID = "123456789012345678"
GOOD_ENV = (f"ANTHROPIC_API_KEY=sk-ant-test-not-a-real-key\nDISCORD_BOT_TOKEN=test-token\n"
            f"DISCORD_ALLOWED_USER_ID={USER_ID}\n")
FAKE_HERMES_UID = 64999
TODAY = dt.date(2026, 10, 21)

yaml = pytest.importorskip("yaml")


def _load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lock():
    return _load(HERMES / "check_lock.py", "botsy_hermes_check_lock")


@pytest.fixture(scope="module")
def values(lock):
    return {"ANTHROPIC_API_KEY": "sk-ant-x", "DISCORD_BOT_TOKEN": "t",
            "DISCORD_ALLOWED_USER_ID": USER_ID}


@pytest.fixture(scope="module")
def good_text(lock, values):
    return lock.render(lock.TEMPLATE.read_text(encoding="utf-8"), values, "socket")


@pytest.fixture()
def good_cfg(good_text):
    return yaml.safe_load(good_text)


# ---------------------------------------------------------------------------- the pin

def test_pin_is_complete_and_at_least_seven_days_old(lock):
    pin = lock.load_pin()
    assert pin["HERMES_TAG"] == "v" + pin["HERMES_VERSION"]
    assert lock.SHA.match(pin["HERMES_COMMIT"])
    published = dt.date.fromisoformat(pin["HERMES_PUBLISHED"])
    assert (TODAY - published).days >= 7
    assert pin["HERMES_RELEASE_URL"].endswith("/releases/tag/" + pin["HERMES_TAG"])
    assert pin["HERMES_REPO"] == "https://github.com/NousResearch/hermes-agent.git"
    extras = set(pin["HERMES_EXTRAS"].split(","))
    assert extras == {"anthropic", "discord", "mcp"}      # no browser, voice or computer-use extras


def test_pin_command_prints_the_pin(lock, capsys):
    assert lock.main(["pin"]) == 0
    out = capsys.readouterr().out
    pin = lock.load_pin()
    assert pin["HERMES_COMMIT"] in out and pin["HERMES_VERSION"] in out


# ---------------------------------------------------------------------------- the config itself

def test_template_renders_and_keeps_the_lock(lock, good_cfg, values):
    assert lock.check_config(good_cfg, values, "socket") == []
    assert good_cfg["model"] == {"provider": "anthropic", "default": "claude-haiku-5-5"}
    assert sorted(good_cfg["mcp_servers"]) == ["botsy-act", "botsy-read"]
    # No codes (2026-10-21): Hermes itself holds every action for your click; reads need none.
    assert good_cfg["mcp_servers"]["botsy-act"]["trust"] == "untrusted"
    assert good_cfg["mcp_servers"]["botsy-read"]["trust"] == "full"
    for forbidden in ("terminal", "file", "web", "browser", "delegation", "cronjob",
                      "code_execution", "discord_admin"):
        assert forbidden in good_cfg["agent"]["disabled_toolsets"]
    assert good_cfg["discord"]["allow_from"] == [USER_ID]
    assert good_cfg["discord"]["ignored_channels"] == []     # no codes channel (2026-10-21)


def test_no_codes_anywhere_in_the_hermes_files(lock):
    """Decided 2026-10-21: no codes. No codes channel ID, codes webhook or code tools remain."""
    assert "DISCORD_CODES_CHANNEL_ID" not in lock.ENV_KEYS and "DISCORD_CODES_CHANNEL_ID" not in lock.PLACEHOLDERS
    for path in [HERMES / "config.yaml.template", HERMES / "hermes.env.example", HERMES / "SOUL.md",
                 HERMES / "install_hermes.sh", *sorted((HERMES / "systemd").iterdir())]:
        text = path.read_text(encoding="utf-8")
        for gone in ("CODES_CHANNEL", "CODES_WEBHOOK", "confirm_code", "request_code", "6-digit"):
            assert gone not in text, (path.name, gone)


def test_sudo_mode_renders_the_fixed_sudo_command(lock, values):
    text = lock.render(lock.TEMPLATE.read_text(encoding="utf-8"), values, "sudo")
    cfg = yaml.safe_load(text)
    assert cfg["mcp_servers"]["botsy-read"]["command"] == "/usr/bin/sudo"
    assert cfg["mcp_servers"]["botsy-read"]["args"] == ["-n", "-u", "botsy", "/usr/local/bin/botsy-control-read"]
    assert cfg["mcp_servers"]["botsy-act"]["args"][-1] == "/usr/local/bin/botsy-control-act"
    assert lock.check_config(cfg, values, "sudo") == []
    assert lock.check_config(cfg, values, "socket")       # the wrong bridge for this file


@pytest.mark.parametrize("bad", [
    {"USER": ""}, {"USER": "12345"}, {"USER": "<@123456789012345678>"}, {"USER": "0"},
])
def test_render_refuses_bad_ids(lock, values, bad):
    v = dict(values)
    if "USER" in bad:
        v["DISCORD_ALLOWED_USER_ID"] = bad["USER"]
    with pytest.raises(ValueError):
        lock.render(lock.TEMPLATE.read_text(encoding="utf-8"), v, "socket")


def test_render_never_prints_a_secret(lock, tmp_path, capsys):
    env = tmp_path / "h.env"
    env.write_text(GOOD_ENV.replace(USER_ID, "1"), encoding="utf-8")
    assert lock.main(["render", "--env", str(env)]) == 1
    err = capsys.readouterr().err
    assert "sk-ant" not in err and "test-token" not in err
    env.write_text(GOOD_ENV, encoding="utf-8")
    assert lock.main(["render", "--env", str(env), "--out", str(tmp_path / "c.yaml")]) == 0
    text = (tmp_path / "c.yaml").read_text(encoding="utf-8")
    assert "sk-ant" not in text and "test-token" not in text
    assert lock.main(["render-env", "--env", str(env), "--out", str(tmp_path / "m.env")]) == 0
    managed = (tmp_path / "m.env").read_text(encoding="utf-8")
    assert "sk-ant" not in managed and "test-token" not in managed
    assert f"DISCORD_ALLOWED_USERS={USER_ID}" in managed and "GATEWAY_ALLOW_ALL_USERS=false" in managed


def _mutations():
    def on(name):
        def f(c):
            c["toolsets"] = c["toolsets"] + [name]
        return f

    def plat_on(platform, name):
        def f(c):
            c["platform_toolsets"][platform] = c["platform_toolsets"][platform] + [name]
        return f

    def undisable(name):
        def f(c):
            c["agent"]["disabled_toolsets"] = [t for t in c["agent"]["disabled_toolsets"] if t != name]
        return f

    def set_(path, value):
        def f(c):
            node = c
            *head, last = path.split(".")
            for k in head:
                node = node[k]
            node[last] = value
        return f

    def drop(path):
        def f(c):
            node = c
            *head, last = path.split(".")
            for k in head:
                node = node[k]
            del node[last]
        return f

    def extra_mcp(c):
        c["mcp_servers"]["filesystem"] = {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/"]}

    def http_mcp(c):
        c["mcp_servers"]["botsy-read"]["url"] = "https://example.com/mcp"

    return {
        "terminal toolset on": (on("terminal"), "toolsets turns on terminal"),
        "web on for discord": (plat_on("discord", "web"), "platform_toolsets.discord turns on web"),
        "browser on for cron": (plat_on("cron", "browser"), "platform_toolsets.cron turns on browser"),
        "terminal not disabled": (undisable("terminal"), "does not turn off terminal"),
        "delegation not disabled": (undisable("delegation"), "does not turn off delegation"),
        "discord_admin not disabled": (undisable("discord_admin"), "does not turn off discord_admin"),
        "code_execution not disabled": (undisable("code_execution"), "does not turn off code_execution"),
        "cronjob not disabled": (undisable("cronjob"), "does not turn off cronjob"),
        "extra MCP server": (extra_mcp, "MCP servers other than Botsy's: filesystem"),
        "Botsy MCP missing": (drop("mcp_servers.botsy-act"), "Botsy's MCP server missing: botsy-act"),
        "MCP over http": (http_mcp, "mcp_servers.botsy-read has url"),
        "MCP other command": (set_("mcp_servers.botsy-read.command", "/bin/sh"), "does not start"),
        "MCP env passed": (set_("mcp_servers.botsy-act.env", {"PATH": "/tmp"}), "passes environment"),
        "sampling on": (set_("mcp_servers.botsy-read.sampling.enabled", True), "botsy-read.sampling is not explicitly off"),
        "sampling default": (drop("mcp_servers.botsy-act.sampling"), "botsy-act.sampling is not explicitly off"),
        "elicitation on": (set_("mcp_servers.botsy-act.elicitation.enabled", True), "botsy-act.elicitation is not explicitly off"),
        "act tools without a click": (set_("mcp_servers.botsy-act.trust", "full"), "botsy-act.trust is not untrusted"),
        "act trust default": (drop("mcp_servers.botsy-act.trust"), "botsy-act.trust is not untrusted"),
        "act trust typo": (set_("mcp_servers.botsy-act.trust", "untrustd"), "botsy-act.trust is not untrusted"),
        "MCP prompts on": (set_("mcp_servers.botsy-read.tools.prompts", True), "tools.prompts is not off"),
        "memory approval off": (set_("memory.write_approval", False), "memory.write_approval is not on"),
        "memory approval missing": (drop("memory.write_approval"), "memory.write_approval is not on"),
        "outside memory": (set_("memory.provider", "honcho"), "memory.provider is set"),
        "skill approval off": (set_("skills.write_approval", False), "skills.write_approval is not on"),
        "allowlist empty": (set_("discord.allow_from", []), "allowlist is empty or open"),
        "allowlist two users": (set_("discord.allow_from", [USER_ID, "323456789012345678"]), "not exactly one"),
        "allowlist other user": (set_("discord.allow_from", ["323456789012345678"]), "is not DISCORD_ALLOWED_USER_ID"),
        "allow all users": (set_("discord.allow_all_users", True), "discord.allow_all_users is not off"),
        "gateway open": (set_("gateway.allow_all_users", True), "gateway.allow_all_users is not off"),
        "roles unlock": (set_("discord.allowed_roles", "999999999999999999"), "allowed_roles is set"),
        "agent makes cron jobs": (set_("cron.allow_agent_scheduling", True), "cron.allow_agent_scheduling is not off"),
        "cron on another model": (set_("cron.model", "claude-opus-5-5"), "scheduled jobs are not fixed"),
        "cron approvals open": (set_("approvals.cron_mode", "approve"), "approvals.cron_mode is not deny"),
        "other model": (set_("model.default", "claude-opus-5-5"), "model.default is not one of"),
        "other provider": (set_("model.provider", "openrouter"), "model.provider is not anthropic"),
        "base_url": (set_("model.base_url", "https://proxy.example"), "model.base_url is set"),
        "fallback": (set_("fallback_providers", [{"provider": "openrouter"}]), "fallback_providers is not empty"),
        "schema version": (set_("_config_version", 51), "_config_version is not"),
        "plugins": (set_("plugins.enabled", ["x"]), "plugins are enabled"),
        "hooks": (set_("hooks", {"on_start": ["sh"]}), "hooks are configured"),
        "quick commands": (set_("quick_commands", {"x": {"type": "exec", "command": "id"}}), "quick_commands"),
        "lazy installs": (set_("security.allow_lazy_installs", True), "allow_lazy_installs is not off"),
        "updates": (set_("updates.check", True), "updates.check is not off"),
        "background review": (set_("auxiliary.background_review.enabled", True), "background_review is on"),
        "no compression": (set_("compression.enabled", False), "chat history is not compressed"),
        "custom toolsets": (set_("custom_toolsets", {"x": ["terminal"]}), "custom_toolsets is set"),
        "slash commands ungated": (drop("discord.allow_admin_from"), "discord.allow_admin_from is not"),
        "group slash commands ungated": (drop("discord.group_allow_admin_from"), "group_allow_admin_from is not"),
        "you are slash admin": (set_("discord.allow_admin_from", [USER_ID]), "discord.allow_admin_from is not"),
        "/model opened": (set_("discord.user_allowed_commands", ["new", "model"]), "opens the slash commands model"),
        "/yolo opened in groups": (set_("discord.group_user_allowed_commands", ["/yolo"]), "opens the slash commands yolo"),
        "/cron opened": (set_("discord.user_allowed_commands", ["cron"]), "opens the slash commands cron"),
        "no mention needed": (set_("discord.require_mention", False), "require_mention is not on"),
    }


MUTATIONS = _mutations()


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_check_config_refuses_each_unlock(lock, good_cfg, values, name):
    mutate, expected = MUTATIONS[name]
    cfg = copy.deepcopy(good_cfg)
    mutate(cfg)
    problems = lock.check_config(cfg, values, "socket")
    assert any(expected in p for p in problems), problems


def test_check_config_refuses_garbage(lock, values):
    assert lock.check_config(None, values, "socket")
    assert lock.check_config([], values, "socket")
    problems = lock.check_config({}, values, "socket")
    assert len(problems) > 10


def test_template_that_unlocks_is_never_rendered(lock, values):
    text = lock.TEMPLATE.read_text(encoding="utf-8").replace("    - terminal\n", "", 1)
    with pytest.raises(ValueError, match="does not keep the lock"):
        lock.render(text, values, "socket")


# ---------------------------------------------------------------------------- install + full check

def _fake_hermes_code(root: Path, pin: dict, *, version: str | None = None) -> None:
    """What fetch_and_build would leave behind (the test mode never downloads)."""
    opt = root / "opt" / "hermes"
    (opt / "src" / ".git").mkdir(parents=True, exist_ok=True)
    (opt / "src" / ".git" / "HEAD").write_text(pin["HERMES_COMMIT"] + "\n", encoding="utf-8")
    (opt / "src" / "install-stamp.json").write_text(json.dumps(
        {"baseVersion": version or pin["HERMES_VERSION"], "commit": pin["HERMES_COMMIT"],
         "updateMechanism": "external"}), encoding="utf-8")
    (opt / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    hermes = opt / "venv" / "bin" / "hermes"
    hermes.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hermes.chmod(0o755)
    (opt / "venv" / ".botsy-pin").write_text(pin["HERMES_COMMIT"] + "\n", encoding="utf-8")
    for d in (opt, opt / "src", opt / "src" / ".git", opt / "venv", opt / "venv" / "bin"):
        d.chmod(0o755)


def _install(root: Path, *extra: str) -> subprocess.CompletedProcess:
    r = subprocess.run(["bash", str(INSTALL), "--root", str(root), "--test-owner", *extra],
                       capture_output=True, text=True, timeout=120)
    return r


@pytest.fixture()
def installed(tmp_path, lock):
    if shutil.which("bash") is None or shutil.which("install") is None:
        pytest.skip("bash and coreutils install are needed")
    root = tmp_path / "srv"
    root.mkdir()
    root.chmod(0o755)
    (root / "etc" / "hermes").mkdir(parents=True)
    (root / "etc" / "hermes").chmod(0o755)
    (root / "etc" / "hermes" / "hermes.env").write_text(GOOD_ENV, encoding="utf-8")
    r = _install(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sk-ant" not in r.stdout + r.stderr and "test-token" not in r.stdout + r.stderr
    _fake_hermes_code(root, lock.load_pin())
    # The installed copy of check_lock.py is the one the server runs: load that one.
    installed_lock = _load(root / "usr" / "local" / "lib" / "botsy-hermes" / "check_lock.py",
                           f"installed_check_lock_{abs(hash(str(root)))}")
    return root, installed_lock


def _ctx(mod, root: Path):
    gid = os.getgid()
    return mod.Ctx(root=root, hermes=mod.Who(FAKE_HERMES_UID, gid, frozenset({gid})),
                   admin_uid=os.getuid(), system=False)


def test_install_lays_out_a_locked_tree(installed):
    root, mod = installed
    assert mod.check(_ctx(mod, root)) == []
    managed = root / "etc" / "hermes" / "config.yaml"
    user = root / "home" / "hermes" / ".hermes" / "config.yaml"
    assert managed.read_text(encoding="utf-8") == user.read_text(encoding="utf-8")
    assert stat.S_IMODE(user.stat().st_mode) == 0o640
    assert stat.S_IMODE(managed.stat().st_mode) == 0o644
    assert stat.S_IMODE((root / "etc" / "hermes" / "hermes.env").stat().st_mode) == 0o640
    assert stat.S_IMODE((root / "home" / "hermes").stat().st_mode) == 0o1770
    assert (root / "etc" / "systemd" / "system" / "botsy-control-read.socket").exists()
    assert not (root / "etc" / "sudoers.d" / "botsy-control").exists()     # socket bridge: no sudo
    assert "BRIDGE=socket" in (root / "etc" / "hermes" / "install.conf").read_text(encoding="utf-8")


def test_install_is_idempotent(installed):
    root, mod = installed
    before = (root / "etc" / "hermes" / "config.yaml").read_text(encoding="utf-8")
    r = _install(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (root / "etc" / "hermes" / "config.yaml").read_text(encoding="utf-8") == before
    assert (root / "etc" / "hermes" / "hermes.env").read_text(encoding="utf-8") == GOOD_ENV
    assert mod.check(_ctx(mod, root)) == []
    assert not list(root.glob("root/hermes-backup-*"))     # nothing had to be moved aside


def test_install_without_secrets_writes_no_config(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    r = _install(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (root / "etc" / "hermes" / "hermes.env").exists()
    assert not (root / "etc" / "hermes" / "config.yaml").exists()
    assert "Fill in" in r.stdout


def test_install_refuses_root_mode_outside_tests(tmp_path):
    r = subprocess.run(["bash", str(INSTALL), "--root", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode != 0 and "--test-owner" in r.stderr


def test_sudo_bridge_install(installed):
    root, mod = installed
    r = _install(root, "--bridge", "sudo")
    assert r.returncode == 0, r.stdout + r.stderr
    sudoers = root / "etc" / "sudoers.d" / "botsy-control"
    assert stat.S_IMODE(sudoers.stat().st_mode) == 0o440
    assert sudoers.read_text(encoding="utf-8") == (LIGHTSAIL / "sudoers.d" / "botsy-control").read_text(encoding="utf-8")
    assert (root / "etc" / "systemd" / "system" / "hermes-gateway.service.d" / "sudo-bridge.conf").exists()
    cfg = yaml.safe_load((root / "etc" / "hermes" / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["mcp_servers"]["botsy-act"]["command"] == "/usr/bin/sudo"
    assert mod.check(_ctx(mod, root)) == []
    # and back to the socket bridge: the sudo drop-in goes away
    r = _install(root, "--bridge", "socket")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (root / "etc" / "systemd" / "system" / "hermes-gateway.service.d" / "sudo-bridge.conf").exists()


def test_firewall_flag_installs_its_units(installed):
    root, mod = installed
    r = _install(root, "--firewall")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (root / "etc" / "systemd" / "system" / "botsy-hermes-firewall.service").exists()
    assert (root / "etc" / "systemd" / "system" / "hermes-gateway.service.d" / "firewall.conf").exists()
    assert "FIREWALL=1" in (root / "etc" / "hermes" / "install.conf").read_text(encoding="utf-8")
    assert mod.check(_ctx(mod, root)) == []
    r = _install(root, "--no-firewall")
    assert r.returncode == 0
    assert not (root / "etc" / "systemd" / "system" / "hermes-gateway.service.d" / "firewall.conf").exists()


def _problems(mod, root):
    return mod.check(_ctx(mod, root))


def test_check_fails_when_config_is_writable_by_hermes(installed):
    root, mod = installed
    (root / "home" / "hermes" / ".hermes" / "config.yaml").chmod(0o660)
    problems = _problems(mod, root)
    assert any("config.yaml can be changed by the hermes user" in p for p in problems), problems


def test_check_fails_when_hermes_owns_its_config(installed):
    root, mod = installed
    if os.getuid() != 0:
        pytest.skip("needs root to chown")
    os.chown(root / "home" / "hermes" / ".hermes" / "config.yaml", FAKE_HERMES_UID, os.getgid())
    problems = _problems(mod, root)
    assert any("is not owned by root" in p for p in problems), problems
    assert any("can be changed by the hermes user" in p for p in problems), problems


def test_check_fails_when_the_config_folder_loses_its_sticky_bit(installed):
    root, mod = installed
    (root / "home" / "hermes" / ".hermes").chmod(0o770)
    problems = _problems(mod, root)
    assert any("has no sticky bit" in p for p in problems), problems
    assert any("can be replaced by the hermes user" in p for p in problems), problems


def test_check_fails_on_a_hand_edited_config(installed):
    root, mod = installed
    path = root / "etc" / "hermes" / "config.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("    - terminal\n", "", 1), encoding="utf-8")
    problems = _problems(mod, root)
    assert any("differs from the template" in p for p in problems), problems
    assert any("does not turn off terminal" in p for p in problems), problems


def test_check_fails_on_a_symlinked_config(installed, tmp_path):
    root, mod = installed
    path = root / "home" / "hermes" / ".hermes" / "config.yaml"
    other = tmp_path / "elsewhere.yaml"
    other.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.unlink()
    path.symlink_to(other)
    assert any("is a symbolic link" in p for p in _problems(mod, root))


def test_check_fails_when_the_version_differs_from_the_pin(installed, lock):
    root, mod = installed
    _fake_hermes_code(root, lock.load_pin(), version="0.22.3")
    problems = _problems(mod, root)
    assert any("is not the pinned" in p for p in problems), problems
    (root / "opt" / "hermes" / "src" / ".git" / "HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    assert any("not at the pinned commit" in p for p in _problems(mod, root))


def test_check_fails_when_hermes_could_update_itself(installed, lock):
    root, mod = installed
    stamp = root / "opt" / "hermes" / "src" / "install-stamp.json"
    data = json.loads(stamp.read_text(encoding="utf-8"))
    data["updateMechanism"] = "git"
    stamp.write_text(json.dumps(data), encoding="utf-8")
    assert any("updateMechanism" in p for p in _problems(mod, root))


def test_check_fails_when_hermes_can_write_its_code(installed):
    root, mod = installed
    (root / "opt" / "hermes" / "venv" / "bin" / "hermes").chmod(0o777)
    assert any("can be changed by the hermes user" in p for p in _problems(mod, root))


def test_check_fails_on_an_empty_allowlist_in_the_secrets(installed):
    root, mod = installed
    (root / "etc" / "hermes" / "hermes.env").write_text(GOOD_ENV.replace(USER_ID, ""), encoding="utf-8")
    problems = _problems(mod, root)
    assert any("DISCORD_ALLOWED_USER_ID" in p for p in problems), problems


def test_check_fails_on_extra_or_empty_secrets(installed):
    root, mod = installed
    env = root / "etc" / "hermes" / "hermes.env"
    env.write_text(GOOD_ENV + "GATEWAY_ALLOW_ALL_USERS=true\n", encoding="utf-8")
    assert any("not allowed: GATEWAY_ALLOW_ALL_USERS" in p for p in _problems(mod, root))
    env.write_text(GOOD_ENV.replace("test-token", ""), encoding="utf-8")
    assert any("DISCORD_BOT_TOKEN" in p and "empty" in p for p in _problems(mod, root))
    env.write_text(GOOD_ENV + "DISCORD_CODES_CHANNEL_ID=223456789012345678\n", encoding="utf-8")
    problems = _problems(mod, root)
    assert any("DISCORD_CODES_CHANNEL_ID" in p and "no codes" in p and "delete the line" in p for p in problems)
    assert not any("not allowed: DISCORD_CODES_CHANNEL_ID" in p for p in problems)
    env.write_text(GOOD_ENV, encoding="utf-8")
    env.chmod(0o644)
    assert any("hermes.env has mode 0644" in p for p in _problems(mod, root))


def test_check_fails_on_a_foreign_drop_in_or_a_changed_unit(installed):
    root, mod = installed
    dropin = root / "etc" / "systemd" / "system" / "hermes-gateway.service.d" / "zz-local.conf"
    dropin.write_text("[Service]\nNoNewPrivileges=no\n", encoding="utf-8")
    assert any("zz-local.conf changes hermes-gateway.service" in p for p in _problems(mod, root))
    dropin.unlink()
    unit = root / "etc" / "systemd" / "system" / "hermes-gateway.service"
    unit.write_text(unit.read_text(encoding="utf-8").replace("User=hermes", "User=root"), encoding="utf-8")
    assert any("hermes-gateway.service differs" in p for p in _problems(mod, root))


def test_check_fails_on_hooks_plugins_or_a_project_folder(installed):
    root, mod = installed
    (root / "home" / "hermes" / ".hermes" / "plugins" / "evil.py").write_text("x", encoding="utf-8")
    (root / "home" / "hermes" / ".hermes" / "profiles" / "other").mkdir()
    (root / "opt" / "hermes" / "work" / "AGENTS.md").write_text("x", encoding="utf-8")
    problems = _problems(mod, root)
    assert any("plugins is not empty" in p for p in problems), problems
    assert any("profiles is not empty" in p for p in problems), problems
    assert any("/opt/hermes/work is not empty" in p for p in problems), problems


def test_check_fails_when_hermes_can_open_botsy_files(installed):
    root, mod = installed
    botsy = root / "opt" / "botsy"
    botsy.mkdir(parents=True)
    botsy.chmod(0o755)
    assert any("can open /opt/botsy" in p for p in _problems(mod, root))
    botsy.chmod(0o750)               # the fake hermes user is in this folder's group here
    assert any("can open /opt/botsy" in p for p in _problems(mod, root))
    botsy.chmod(0o700)
    assert not any("/opt/botsy" in p for p in _problems(mod, root))


def test_check_cli_exits_one_and_lists_problems(installed, capsys):
    root, mod = installed
    (root / "home" / "hermes" / ".hermes" / "config.yaml").chmod(0o666)
    # The CLI looks the hermes user up; on a machine without one that is itself a problem.
    rc = mod.main(["check", "--root", str(root), "--quick"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "PROBLEM:" in out and "Hermes must not run like this" in out


def test_check_without_a_hermes_user_is_a_problem(installed):
    root, mod = installed
    ctx = mod.Ctx(root=root, hermes=None, admin_uid=os.getuid(), system=False)
    assert "there is no hermes user" in mod.check(ctx)


# ---------------------------------------------------------------------------- the probe's judgement

def _good_probe(lock):
    pin = lock.load_pin()
    plat = {"toolsets": ["botsy-act", "botsy-read", "memory"], "tools": ["memory"]}
    mcp = {n: {"command": c, "args": a, "env": {}, "url": None, "enabled": True,
               "sampling": False, "elicitation": False, "trust": lock.MCP_TRUST[n]}
           for n, (c, a) in lock.MCP_COMMANDS["socket"].items()}
    env = lock.expected_managed_env({"DISCORD_ALLOWED_USER_ID": USER_ID})
    env["DISCORD_ALLOW_ALL_USERS"] = "false"
    return {"platforms": {p: dict(plat) for p in lock.PLATFORMS}, "mcp": mcp,
            "memory_write_approval": True, "skills_write_approval": True, "memory_provider": "",
            "agent_scheduling": False, "model": "claude-haiku-5-5", "provider": "anthropic",
            "allow_from": [USER_ID], "env": env, "version": pin["HERMES_VERSION"], "commit": pin["HERMES_COMMIT"],
            "slash": {s: {"gated": True, "open": []} for s in ("dm", "group", "unknown")}}


def test_probe_judgement(lock, values):
    ctx = lock.Ctx()
    lock.judge_probe(ctx, _good_probe(lock), values, "socket")
    assert ctx.problems == []
    cases = [
        (lambda d: d["platforms"]["discord"].update(toolsets=["botsy-read", "botsy-act", "memory", "terminal"]), "toolsets terminal"),
        (lambda d: d["platforms"]["cron"].update(tools=["memory", "web_extract"]), "built-in tools web_extract"),
        (lambda d: d["mcp"].update(extra={"command": "npx"}), "MCP servers"),
        (lambda d: d["mcp"]["botsy-read"].update(sampling=True), "sampling or elicitation"),
        (lambda d: d["mcp"]["botsy-act"].update(trust="full"), "without your approval click"),
        (lambda d: d["mcp"]["botsy-act"].pop("trust"), "botsy-act as trust None"),
        (lambda d: d.update(memory_write_approval=False), "without your approval"),
        (lambda d: d.update(agent_scheduling=True), "its own scheduled jobs"),
        (lambda d: d.update(allow_from=[]), "allowlist"),
        (lambda d: d["env"].update(GATEWAY_ALLOW_ALL_USERS="true"), "GATEWAY_ALLOW_ALL_USERS"),
        (lambda d: d.update(version="0.22.3"), "not the pinned"),
        (lambda d: d["slash"]["dm"].update(gated=False), "not gated in Discord (dm chats)"),
        (lambda d: d["slash"]["group"].update(open=["model", "yolo"]), "/model, /yolo in Discord (group chats)"),
        (lambda d: d.pop("slash"), "slash-command gate could not be read"),
    ]
    for mutate, expected in cases:
        data = _good_probe(lock)
        mutate(data)
        ctx = lock.Ctx()
        lock.judge_probe(ctx, data, values, "socket")
        assert any(expected in p for p in ctx.problems), (expected, ctx.problems)


# ---------------------------------------------------------------------------- sudo -l judgement

SUDO_OK = """Matching Defaults entries for hermes on botsy:
    env_reset, !setenv

User hermes may run the following commands on botsy:
    (botsy) NOPASSWD: NOSETENV: /usr/local/bin/botsy-control-read "", /usr/local/bin/botsy-control-act ""
"""


def test_sudo_list_judgement(lock):
    assert lock.sudo_list_problems("User hermes is not allowed to run sudo on botsy.\n") == []
    assert lock.sudo_list_problems(SUDO_OK) == []
    assert lock.sudo_list_problems(SUDO_OK.replace("(botsy)", "(root)"))
    assert lock.sudo_list_problems(SUDO_OK.replace("(botsy)", "(ALL : ALL)"))
    assert lock.sudo_list_problems(SUDO_OK + "    (botsy) NOPASSWD: /bin/sh\n")
    assert lock.sudo_list_problems(SUDO_OK.replace(' ""', "", 1))        # arguments allowed
    assert lock.sudo_list_problems(SUDO_OK.replace("NOPASSWD: ", ""))


# ---------------------------------------------------------------------------- units, firewall, bridge

def _unit(name: str) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for line in (HERMES / "systemd" / name).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith(("#", "[")):
            k, v = line.split("=", 1)
            keys.setdefault(k.strip(), []).append(v.strip())
    return keys


def test_gateway_unit_is_hardened():
    u = _unit("hermes-gateway.service")
    assert u["User"] == ["hermes"] and u["Group"] == ["hermes"]
    assert u["NoNewPrivileges"] == ["yes"]           # the socket bridge needs no sudo
    assert u["CapabilityBoundingSet"] == [""]
    assert u["ProtectSystem"] == ["strict"]
    assert u["EnvironmentFile"] == ["/etc/hermes/hermes.env"]
    assert u["Environment"].count("HERMES_MANAGED_DIR=/etc/hermes") == 1
    assert u["ExecStart"] == ["/opt/hermes/venv/bin/hermes gateway run"]
    assert any("check_lock.py check --quick" in v for v in u["ExecStartPre"])
    inaccessible = " ".join(u["InaccessiblePaths"])
    for path in ("/opt/botsy", "/etc/botsy", "/home/botsy", "/srv/botsy-inbox", "/root"):
        assert path in inaccessible


def test_sudo_drop_in_only_loosens_what_sudo_needs():
    u = _unit("sudo-bridge.conf")
    assert u["NoNewPrivileges"] == ["no"]
    caps = u["CapabilityBoundingSet"][-1].split()
    assert "CAP_SYS_ADMIN" not in caps and "CAP_NET_ADMIN" not in caps
    assert u["ProtectSystem"] == ["full"]


def test_botsy_control_socket_services_run_as_botsy():
    for kind in ("read", "act"):
        sock = _unit(f"botsy-control-{kind}.socket")
        assert sock["SocketGroup"] == ["hermes"] and sock["SocketMode"] == ["0660"]
        assert sock["Accept"] == ["yes"]
        svc = _unit(f"botsy-control-{kind}@.service")
        assert svc["User"] == ["botsy"] and svc["NoNewPrivileges"] == ["yes"]
        assert svc["ExecStart"] == [f"/usr/local/bin/botsy-control-{kind}"]
    assert _unit("botsy-control-read@.service")["IPAddressDeny"] == ["any"]
    # Read-only file system except Botsy's own records; no home folders at all.
    for kind, rw in (("read", ["/opt/botsy"]), ("act", ["/opt/botsy", "-/srv/botsy-inbox/outbox"])):
        svc = _unit(f"botsy-control-{kind}@.service")
        assert svc["ProtectSystem"] == ["strict"] and svc["ProtectHome"] == ["yes"]
        assert svc["ReadWritePaths"][0].split() == rw
        assert svc["CapabilityBoundingSet"] == [""]


def test_lock_timer_is_daily_and_stops_hermes():
    assert _unit("botsy-hermes-lock.timer")["OnCalendar"] == ["*-*-* 05:41:00 America/New_York"]
    svc = _unit("botsy-hermes-lock.service")
    assert "check --probe --stop hermes-gateway.service" in svc["ExecStart"][0]
    # Fail closed: a check that cannot finish also stops Hermes. "$$" so systemd passes the
    # variable through to sh instead of expanding it itself.
    post = svc["ExecStopPost"][0]
    assert '"$$SERVICE_RESULT" = success' in post and "systemctl stop" in post and "hermes-gateway.service" in post


def test_check_that_crashes_fails_closed(installed, capsys, monkeypatch):
    root, mod = installed

    def boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(mod, "check", boom)
    rc = mod.main(["check", "--root", str(root), "--quick"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "PROBLEM: the lock check itself failed (RuntimeError)" in out


SHOW_OK = ("FragmentPath=/etc/systemd/system/hermes-gateway.service\nDropInPaths=\nUser=hermes\n"
           "NoNewPrivileges=yes\n")


def test_systemctl_show_judgement(lock):
    assert lock.unit_show_problems(SHOW_OK, "socket", False) == []
    fw = SHOW_OK.replace("DropInPaths=", "DropInPaths=/etc/systemd/system/hermes-gateway.service.d/firewall.conf")
    assert lock.unit_show_problems(fw, "socket", True) == []
    assert lock.unit_show_problems(fw, "socket", False)                     # firewall is off
    foreign = SHOW_OK.replace("DropInPaths=", "DropInPaths=/etc/systemd/system/service.d/10-all.conf")
    assert any("service.d/10-all.conf" in p for p in lock.unit_show_problems(foreign, "socket", False))
    moved = SHOW_OK.replace("/etc/systemd/system/hermes-gateway.service", "/run/systemd/system.control/hermes-gateway.service")
    assert any("loads hermes-gateway.service from" in p for p in lock.unit_show_problems(moved, "socket", False))
    assert any("runs as root" in p for p in lock.unit_show_problems(SHOW_OK.replace("User=hermes", "User="), "socket", False))
    nnp = SHOW_OK.replace("NoNewPrivileges=yes", "NoNewPrivileges=no")
    assert lock.unit_show_problems(nnp, "socket", False)
    sudo = nnp.replace("DropInPaths=", "DropInPaths=/etc/systemd/system/hermes-gateway.service.d/sudo-bridge.conf")
    assert lock.unit_show_problems(sudo, "sudo", False) == []


def test_nftables_limits_only_the_hermes_user():
    text = (HERMES / "nftables-hermes.conf").read_text(encoding="utf-8")
    body = text.split("chain output", 1)[1]
    rules = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert 'meta skuid != "hermes" accept' in rules
    accepts = [r for r in rules if r.endswith("accept") and "policy" not in r]
    for r in accepts:
        assert r.startswith(("meta skuid", "oifname \"lo\"", "ct state", "tcp dport 443")), r
    assert any(r.startswith("counter reject") for r in rules)
    assert "160.79.104.0/23" in text and "2607:6bc0::/48" in text
    assert "flush ruleset" not in text                # never touches other tables


def test_resolver_skips_local_addresses():
    mod = _load(HERMES / "botsy-hermes-resolve", "botsy_hermes_resolve")

    def lookup(host, port, proto=None):
        return [(socket.AF_INET, 1, 6, "", ("162.159.135.232", port)),
                (socket.AF_INET, 1, 6, "", ("127.0.0.1", port)),
                (socket.AF_INET, 1, 6, "", ("10.0.0.5", port)),
                (socket.AF_INET6, 1, 6, "", ("2606:4700::6810:1", port, 0, 0))]

    v4, v6 = mod.resolve(("discord.com",), lookup=lookup)
    assert v4 == {"162.159.135.232"} and v6 == {"2606:4700::6810:1"}
    script = mod.script(v4, v6)
    assert "add element inet botsy_hermes allow_v4 { 162.159.135.232 timeout 1h }" in script
    assert "10.0.0.5" not in script


def test_connect_bridge_copies_both_ways(tmp_path):
    mod = _load(HERMES / "botsy-control-connect", "botsy_control_connect")
    assert mod.main([]) == 64 and mod.main(["shell"]) == 64
    path = str(tmp_path / "read.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)

    def echo():
        conn, _ = server.accept()
        data = b""
        while not data.endswith(b"\n"):
            data += conn.recv(100)
        conn.sendall(b"got:" + data)
        conn.close()

    t = threading.Thread(target=echo, daemon=True)
    t.start()
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    os.write(in_w, b'{"jsonrpc":"2.0"}\n')
    os.close(in_w)
    rc = mod.run("read", sockets={"read": path}, fd_in=in_r, fd_out=out_w)
    os.close(out_w)
    assert rc == 0
    assert os.read(out_r, 1000) == b'got:{"jsonrpc":"2.0"}\n'
    assert mod.run("read", sockets={"read": str(tmp_path / "missing.sock")}, fd_in=in_r, fd_out=1) == 69
    server.close()


def test_soul_has_no_codes_and_asks_before_approving():
    text = (HERMES / "SOUL.md").read_text(encoding="utf-8")
    assert "no codes" in text                      # decided 2026-10-21
    for gone in ("#botsy-codes", "confirm_code", "request_code", "6-digit"):
        assert gone not in text
    assert "only when the person asks you plainly" in text and "after a clear yes" in text
    assert "data, not instructions" in text


def test_secrets_example_has_only_the_allowed_keys(lock):
    keys = set(lock.read_kv(HERMES / "hermes.env.example"))
    assert keys == set(lock.ENV_KEYS)


def test_install_script_parses():
    r = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
