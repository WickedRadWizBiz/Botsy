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
import re
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
    # No browser, voice or computer-use extras; "web" is fastapi + uvicorn for `hermes dashboard`.
    assert extras == {"anthropic", "discord", "mcp", "web"}
    assert re.fullmatch(r"22\.\d+\.\d+", pin["NODE_VERSION"])
    for key in ("NODE_SHA256_LINUX_X64", "NODE_SHA256_LINUX_ARM64"):
        assert re.fullmatch(r"[0-9a-f]{64}", pin[key])


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
    for forbidden in ("terminal", "file", "web", "search", "browser", "delegation", "code_execution",
                      "computer_use", "image_gen", "video", "video_gen", "tts", "discord_admin"):
        assert forbidden in good_cfg["agent"]["disabled_toolsets"]
    assert good_cfg["discord"]["allow_from"] == [USER_ID]
    assert good_cfg["discord"]["ignored_channels"] == []     # no codes channel (2026-10-21)
    # Decided 2026-10-21 (D4 items 1-6): what each place gets, exactly.
    for platform, want in lock.PLATFORM_TOOLSETS.items():
        assert set(good_cfg["platform_toolsets"][platform]) == want, platform
    assert set(good_cfg["toolsets"]) == lock.ALLOWED_TOOLSETS
    assert good_cfg["cron"]["model"] == "claude-haiku-5-5" and good_cfg["cron"]["model_provider"] == "anthropic"
    assert good_cfg["cron"]["provider"] == ""                  # the built-in scheduler
    assert good_cfg["skills"]["write_approval"] is True and good_cfg["memory"]["write_approval"] is True
    assert good_cfg["security"]["website_blocklist"] == {"enabled": True, "domains": ["*.*"], "shared_files": []}


# D4 (user, 2026-10-21): the toolsets Hermes now gets, and the ones that stay off.
D4_DISCORD = {"botsy-read", "botsy-act", "memory", "vision", "cronjob", "session_search", "todo", "kanban",
              "skills", "discord"}
STILL_OFF = ("terminal", "file", "code_execution", "browser", "computer_use", "image_gen", "video",
             "video_gen", "tts", "delegation", "web", "search", "discord_admin", "x_search", "connections",
             "catalog", "project", "desktop_ui", "yuanbao", "feishu_doc", "feishu_drive")
BUNDLES = ("safe", "coding", "debugging", "hermes-cli", "hermes-discord", "hermes-gateway")


def test_allowed_sets_match_the_decision(lock):
    assert lock.PLATFORM_TOOLSETS["discord"] == D4_DISCORD
    # Jobs get no skill tools: a job's tool calls are not shown in Discord, so a skill it changed
    # would not be posted to you (D4 item 5). The command line keeps them (you watch it run).
    assert lock.PLATFORM_TOOLSETS["cron"] == D4_DISCORD - {"cronjob", "kanban", "discord", "skills"}
    assert lock.PLATFORM_TOOLSETS["cli"] == D4_DISCORD - {"cronjob", "kanban", "discord"}
    assert lock.CRON_TOOLSETS == lock.PLATFORM_TOOLSETS["cron"]
    assert lock.ALLOWED_TOOLSETS == D4_DISCORD
    assert lock.ALLOWED_MODELS == {"claude-haiku-5-5", "claude-sonnet-5-5"}
    assert lock.DEFAULT_MODEL == "claude-haiku-5-5"
    assert set(STILL_OFF) <= set(lock.FORBIDDEN_TOOLSETS)
    assert not set(lock.FORBIDDEN_TOOLSETS) & lock.ALLOWED_TOOLSETS
    assert not set(BUNDLES) & set(lock.FORBIDDEN_TOOLSETS)    # a bundle there would strip vision/skills
    assert {"loop", "goal", "skills"} <= lock.SAFE_SLASH_COMMANDS
    # /bg: a background run posts only its answer, so a skill change in it would go unposted.
    assert {"model", "yolo", "tools", "heartbeat", "moa", "bg", "cron"} <= set(lock.RISKY_SLASH_COMMANDS)
    assert not lock.SAFE_SLASH_COMMANDS & set(lock.RISKY_SLASH_COMMANDS)


@pytest.mark.parametrize("name", STILL_OFF + BUNDLES)
def test_every_still_forbidden_toolset_is_rejected(lock, good_cfg, values, name):
    for where, mutate in (("toolsets", lambda c: c["toolsets"].append(name)),
                          ("platform_toolsets.discord", lambda c: c["platform_toolsets"]["discord"].append(name)),
                          ("platform_toolsets.cron", lambda c: c["platform_toolsets"]["cron"].append(name))):
        cfg = copy.deepcopy(good_cfg)
        mutate(cfg)
        problems = lock.check_config(cfg, values, "socket")
        assert any(f"{where} turns on {name}" in p for p in problems), (where, problems)
    if name in lock.FORBIDDEN_TOOLSETS:
        cfg = copy.deepcopy(good_cfg)
        cfg["agent"]["disabled_toolsets"].remove(name)
        assert any(f"does not turn off {name}" in p for p in lock.check_config(cfg, values, "socket"))


@pytest.mark.parametrize("platform,name", [("cron", "cronjob"), ("cron", "kanban"), ("cron", "discord"),
                                           ("cron", "skills"), ("cli", "kanban"), ("webhook", "vision"), ("api_server", "skills")])
def test_a_toolset_allowed_elsewhere_is_rejected_where_it_is_not(lock, good_cfg, values, platform, name):
    cfg = copy.deepcopy(good_cfg)
    cfg["platform_toolsets"][platform].append(name)
    problems = lock.check_config(cfg, values, "socket")
    assert any(f"platform_toolsets.{platform} turns on {name}" in p for p in problems), problems


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
        "computer_use not disabled": (undisable("computer_use"), "does not turn off computer_use"),
        "an unknown place": (set_("platform_toolsets.telegram", ["botsy-read", "botsy-act"]), "platform_toolsets.telegram is not one of"),
        "Botsy tools dropped": (set_("platform_toolsets.cron", ["memory"]), "platform_toolsets.cron does not name Botsy's two tool servers"),
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
        "cron on sonnet by default": (set_("cron.model", "claude-sonnet-5-5"), "scheduled jobs are not fixed"),
        "cron model provider": (set_("cron.model_provider", "openrouter"), "scheduled jobs are not fixed"),
        "cron outside scheduler": (set_("cron.provider", "chronos"), "cron.provider is set"),
        "chat on sonnet": (set_("model.default", "claude-sonnet-5-5"), "model.default is not one of"),
        "kanban workers": (set_("kanban.dispatch_in_gateway", True), "may start worker agents"),
        "kanban any profile": (drop("kanban.dispatch_profiles"), "may start worker agents"),
        "kanban decomposer": (set_("kanban.auto_decompose", True), "kanban.auto_decompose"),
        "kanban review workers": (set_("kanban.review_dispatch", True), "kanban.review_dispatch"),
        "vision on the web": (set_("security.website_blocklist.enabled", False), "website_blocklist does not block"),
        "blocklist emptied": (set_("security.website_blocklist.domains", ["etsy.com"]), "website_blocklist does not block"),
        "private urls": (set_("security.allow_private_urls", True), "allow_private_urls is not off"),
        "every discord action": (set_("discord.server_actions", ""), "discord.server_actions is not a list"),
        "discord pins": (set_("discord.server_actions", ["fetch_messages", "pin_message"]), "discord.server_actions"),
        "discord member search": (set_("discord.server_actions", ["search_members"]), "discord.server_actions"),
        "curator on": (set_("curator.enabled", True), "curator is on"),
        "skill scan off": (set_("skills.guard_agent_created", False), "guard_agent_created is not on"),
        "/heartbeat opened": (set_("discord.user_allowed_commands", ["heartbeat"]), "opens the slash commands heartbeat"),
        "/tools opened in groups": (set_("discord.group_user_allowed_commands", ["tools"]), "opens the slash commands tools"),
        "cron approvals open": (set_("approvals.cron_mode", "approve"), "approvals.cron_mode is not deny"),
        "other model": (set_("model.default", "claude-opus-5-5"), "model.default is not one of"),
        "other provider": (set_("model.provider", "openrouter"), "model.provider is not anthropic"),
        "base_url": (set_("model.base_url", "https://proxy.example"), "model.base_url is set"),
        "fallback": (set_("fallback_providers", [{"provider": "openrouter"}]), "fallback_providers is not empty"),
        "schema version": (set_("_config_version", 51), "_config_version is not"),
        "plugins": (set_("plugins.enabled", ["x"]), "plugins are enabled"),
        "botsy plus another plugin": (set_("plugins.enabled", ["botsy", "x"]), "plugins are enabled"),
        "plugins auto-applied": (set_("plugins.auto_apply", True), "plugins are enabled or auto-applied"),
        "plugins in a host process": (set_("plugins.isolation", "host"), "plugins.isolation is not in_process"),
        "dashboard public url": (set_("dashboard.public_url", "https://hermes.example"), "dashboard.public_url is set"),
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
        "/bg opened": (set_("discord.user_allowed_commands", ["new", "bg"]), "opens the slash commands bg"),
        "tool calls hidden in Discord": (set_("display.platforms.discord.tool_progress", "off"),
                                         "tool_progress is not pinned to all"),
        "tool calls only new": (set_("display.platforms.discord.tool_progress", "new"),
                                "tool_progress is not pinned to all"),
        "display pin dropped": (drop("display"), "tool_progress is not pinned to all"),
        "/verbose opened": (set_("display.tool_progress_command", True), "tool_progress is not pinned to all"),
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
    # Required since 2026-10-21: --no-firewall is refused, and an old FIREWALL=0 is turned on.
    r = _install(root, "--no-firewall")
    assert r.returncode != 0 and "firewall is required" in r.stdout + r.stderr
    conf = root / "etc" / "hermes" / "install.conf"
    conf.write_text("BRIDGE=socket\nFIREWALL=0\n", encoding="utf-8")
    assert any("the firewall is off" in p for p in mod.check(_ctx(mod, root)))
    r = _install(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FIREWALL=1" in conf.read_text(encoding="utf-8")
    assert mod.check(_ctx(mod, root)) == []


def test_default_install_has_the_firewall_and_the_jobs_timer(installed):
    root, mod = installed
    units = root / "etc" / "systemd" / "system"
    assert "FIREWALL=1" in (root / "etc" / "hermes" / "install.conf").read_text(encoding="utf-8")
    for name in ("botsy-hermes-firewall.service", "botsy-hermes-jobs.service", "botsy-hermes-jobs.timer"):
        assert (units / name).exists(), name
    scripts = root / "home" / "hermes" / ".hermes" / "scripts"
    assert scripts.is_dir() and not any(scripts.iterdir())


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
    assert any("plugins holds files that are not the Botsy dashboard tab (evil.py)" in p for p in problems), problems
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

# Recorded 2026-10-21 from the real pinned Hermes v0.21.7 (commit 71d34bad), installed in a
# throwaway venv and given this template: the toolsets and built-in tools it really gives each place.
_JOB_PLACE = {"toolsets": ["botsy-act", "botsy-read", "memory", "session_search", "todo", "vision"],
              "tools": ["memory", "session_search", "todo_list", "vision_analyze"]}
_CLI_PLACE = {"toolsets": ["botsy-act", "botsy-read", "memory", "session_search", "skills", "todo", "vision"],
              "tools": ["memory", "session_search", "skill_manage", "skill_view", "skills_list", "todo_list",
                        "vision_analyze"]}
RECORDED_PLATFORMS = {
    "discord": {"toolsets": ["botsy-act", "botsy-read", "cronjob", "discord", "kanban", "memory", "session_search",
                             "skills", "todo", "vision"],
                "tools": ["cronjob_manage", "discord", "kanban_attach", "kanban_attach_url", "kanban_attachments",
                          "kanban_block", "kanban_comment", "kanban_complete", "kanban_create", "kanban_heartbeat",
                          "kanban_link", "kanban_list", "kanban_request_changes", "kanban_request_review",
                          "kanban_schedule", "kanban_show", "kanban_unblock", "memory", "session_search",
                          "skill_manage", "skill_view", "skills_list", "todo_list", "vision_analyze"]},
    "cron": _JOB_PLACE, "cli": _CLI_PLACE,
    "webhook": {"toolsets": ["botsy-act", "botsy-read", "memory"], "tools": ["memory"]},
    "api_server": {"toolsets": ["botsy-act", "botsy-read", "memory"], "tools": ["memory"]},
}


def _good_probe(lock):
    pin = lock.load_pin()
    mcp = {n: {"command": c, "args": a, "env": {}, "url": None, "enabled": True,
               "sampling": False, "elicitation": False, "trust": lock.MCP_TRUST[n]}
           for n, (c, a) in lock.MCP_COMMANDS["socket"].items()}
    env = lock.expected_managed_env({"DISCORD_ALLOWED_USER_ID": USER_ID})
    env["DISCORD_ALLOW_ALL_USERS"] = "false"
    return {"platforms": copy.deepcopy(RECORDED_PLATFORMS), "mcp": mcp,
            "cron": {"model": "claude-haiku-5-5", "model_provider": "anthropic", "provider": ""},
            "kanban": {"auto_decompose": False, "dispatch_in_gateway": False, "dispatch_profiles": []},
            "background": {"curator": False, "review": False},
            "web_blocked": {"https://cdn.discordapp.com/x.png": True, "https://example.com/x.png": True,
                            "https://i.etsystatic.com/x.jpg": True, "https://www.etsy.com/x.jpg": True},
            "discord_actions": ["fetch_messages", "create_thread"], "discord_tool_progress": "all",
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
        (lambda d: d.update(agent_scheduling=True), "running scheduled job make more jobs"),
        (lambda d: d["platforms"]["cron"]["toolsets"].append("kanban"), "gives cron the toolsets kanban"),
        (lambda d: d["platforms"]["cron"]["tools"].append("cronjob_manage"), "built-in tools cronjob_manage"),
        (lambda d: d["platforms"]["discord"]["tools"].append("discord_admin"), "built-in tools discord_admin"),
        (lambda d: d["platforms"]["discord"]["tools"].append("web_search"), "built-in tools web_search"),
        (lambda d: d.update(model="claude-sonnet-5-5"), "not the pinned Anthropic model"),
        (lambda d: d["cron"].update(model="claude-sonnet-5-5"), "scheduled jobs are not fixed"),
        (lambda d: d["kanban"].update(dispatch_in_gateway=True), "worker agents"),
        (lambda d: d["background"].update(review=True), "background review or curator is on"),
        (lambda d: d["web_blocked"].update({"https://www.etsy.com/x.jpg": False}), "does not block every web address"),
        (lambda d: d.pop("web_blocked"), "does not block every web address"),
        (lambda d: d.update(discord_actions=None), "Discord tool allows actions beyond"),
        (lambda d: d.update(discord_actions=["fetch_messages", "add_role"]), "Discord tool allows actions beyond"),
        (lambda d: d.update(allow_from=[]), "allowlist"),
        (lambda d: d["platforms"]["cron"]["tools"].append("skill_manage"), "gives cron the built-in tools skill_manage"),
        (lambda d: d.update(discord_tool_progress="new"), "does not show every tool call in Discord"),
        (lambda d: d.pop("discord_tool_progress"), "does not show every tool call in Discord"),
        (lambda d: d["slash"]["dm"].update(open=["bg"]), "/bg in Discord (dm chats)"),
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
    # trend_search (D4.8) needs HTTPS to TinyFish; local and private ranges stay closed, only the
    # DNS stub is allowed on loopback. botsy-act keeps no network at all.
    read = _unit("botsy-control-read@.service")
    deny = read["IPAddressDeny"][0].split()
    assert "any" not in deny and {"localhost", "link-local", "10.0.0.0/8", "172.16.0.0/12",
                                  "192.168.0.0/16", "fc00::/7"} <= set(deny)
    assert read["IPAddressAllow"] == ["127.0.0.53/32"]
    assert read["RestrictAddressFamilies"] == ["AF_UNIX AF_NETLINK AF_INET AF_INET6"]
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
    # A job's URL monitor has no address check (v0.21.7 cron/monitor.py): the hermes user may not
    # open new connections to the dashboard on loopback (its page carries a session token).
    dash = rules.index('oifname "lo" tcp dport 9119 counter reject with tcp reset')
    assert rules.index('ct state established,related accept') < dash < rules.index('oifname "lo" accept')
    # The botsy user (downloaded files) never opens the dashboard either; root and sshd stay allowed.
    botsy = 'meta skuid "botsy" oifname "lo" tcp dport 9119 ct state new counter reject with tcp reset'
    assert rules.index(botsy) < rules.index('meta skuid != "hermes" accept')


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


# ---------------------------------------------------------------------------- Hermes's own scheduled jobs

def _job(**kw):
    """A job record as Hermes v0.21.7 stores it in ~/.hermes/cron/jobs.json (cron/jobs.py create_job)."""
    job = {"id": "a1b2c3", "name": "morning check-in", "prompt": "Read Botsy's status and tell me.",
           "skills": [], "skill": None, "model": None, "provider": None, "base_url": None, "script": None,
           "no_agent": False, "monitor_script": None, "monitor_url": None, "context_from": None,
           "schedule": {"kind": "cron", "expr": "17 8 * * *", "display": "17 8 * * *"},
           "enabled": True, "state": "scheduled", "deliver": "origin", "enabled_toolsets": None, "workdir": None}
    job.update(kw)
    return job


SONNET_PLAN = dict(name="botsy-weekly-plan", model="claude-sonnet-5-5", provider="anthropic",
                   schedule={"kind": "cron", "expr": "23 7 * * 0"})
SONNET_CD = dict(name="botsy-creative-director", model="claude-sonnet-5-5", provider="anthropic",
                 schedule={"kind": "cron", "expr": "23 10 * * wed"})


def test_good_jobs_pass(lock):
    jobs = [_job(), _job(name="hourly", schedule={"kind": "interval", "minutes": 60}),
            _job(name="once", schedule={"kind": "once", "run_at": "2026-10-22T09:00:00-04:00"}),
            _job(name="pinned haiku", model="claude-haiku-5-5", provider="anthropic"),
            _job(name="few tools", enabled_toolsets=["botsy-read", "memory"]),
            _job(name="no tools", enabled_toolsets=[]),
            _job(name="paused often", enabled=False, schedule={"kind": "interval", "minutes": 1}),
            _job(**SONNET_PLAN), _job(**SONNET_CD),
            _job(name="monthly sonnet", model=None, schedule={"kind": "cron", "expr": "5 6 1 * *"})]
    assert lock.job_problems(jobs) == []
    assert lock.job_problems([]) == []


JOB_CASES = {
    "script": (dict(script="x.sh"), "runs a script"),
    "script only": (dict(no_agent=True), "script-only job"),
    "monitor script": (dict(monitor_script="m.sh"), "runs a monitor script"),
    "url monitor": (dict(monitor_url="https://www.etsy.com/search?q=planner"), "fetches a web address"),
    "endpoint": (dict(base_url="https://proxy.example"), "sends to another endpoint"),
    "work folder": (dict(workdir="/home/hermes"), "has a work folder"),
    "other provider": (dict(provider="openrouter"), "uses the provider 'openrouter'"),
    "other model": (dict(model="claude-opus-5-5"), "uses the model 'claude-opus-5-5'"),
    "sonnet elsewhere": (dict(model="claude-sonnet-5-5", schedule={"kind": "cron", "expr": "1 1 * * 1"}),
                         "runs on Sonnet (only"),
    "terminal": (dict(enabled_toolsets=["botsy-read", "terminal"]), "turns on terminal"),
    "kanban in a job": (dict(enabled_toolsets=["kanban"]), "turns on kanban"),
    "cronjob in a job": (dict(enabled_toolsets=["cronjob"]), "turns on cronjob"),
    "skill writes in a job": (dict(enabled_toolsets=["botsy-read", "skills"]), "turns on skills"),
    "bundle in a job": (dict(enabled_toolsets=["hermes-cron"]), "turns on hermes-cron"),
    "every 30 minutes": (dict(schedule={"kind": "interval", "minutes": 30}), "runs every 30 minutes"),
    "every minute cron": (dict(schedule={"kind": "cron", "expr": "* * * * *"}), "more often than hourly"),
    "every 15 minutes cron": (dict(schedule={"kind": "cron", "expr": "*/15 * * * *"}), "more often than hourly"),
    "two minutes an hour": (dict(schedule={"kind": "cron", "expr": "0,30 * * * *"}), "more often than hourly"),
    "six fields": (dict(schedule={"kind": "cron", "expr": "0 0 * * * *"}), "not 5 plain fields"),
    "odd kind": (dict(schedule={"kind": "every"}), "unknown schedule kind"),
    "no schedule": (dict(schedule=None), "no readable schedule"),
    "sonnet daily": (dict(SONNET_PLAN, schedule={"kind": "cron", "expr": "23 7 * * *"}), "once a week on Sonnet"),
    "sonnet twice a week": (dict(SONNET_PLAN, schedule={"kind": "cron", "expr": "23 7 * * 0,3"}), "once a week on Sonnet"),
    "sonnet hourly": (dict(SONNET_PLAN, schedule={"kind": "cron", "expr": "23 * * * 0"}), "once a week on Sonnet"),
    "sonnet interval": (dict(SONNET_PLAN, schedule={"kind": "interval", "minutes": 1440}), "runs every 1440 minutes"),
}


@pytest.mark.parametrize("name", sorted(JOB_CASES))
def test_each_bad_job_is_refused(lock, name):
    kw, expected = JOB_CASES[name]
    problems = lock.job_problems([_job(**kw)])
    assert any(expected in p for p in problems), problems


def test_job_count_and_duplicate_sonnet_jobs(lock):
    jobs = [_job(name=f"job {i}") for i in range(lock.MAX_CRON_JOBS)]
    assert lock.job_problems(jobs) == []
    assert any("11 scheduled jobs are on" in p for p in lock.job_problems(jobs + [_job(name="one more")]))
    assert lock.job_problems(jobs + [_job(name="paused", enabled=False)]) == []
    assert any("2 scheduled jobs named 'botsy-weekly-plan'" in p
               for p in lock.job_problems([_job(**SONNET_PLAN), _job(**SONNET_PLAN)]))
    assert lock.job_problems("nope") and lock.job_problems([1])


def _write_jobs(root: Path, jobs) -> Path:
    path = root / "home" / "hermes" / ".hermes" / "cron" / "jobs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"jobs": jobs, "updated_at": "2026-10-21T09:00:00-04:00"}), encoding="utf-8")
    return path


def test_full_check_and_jobs_command_judge_the_jobs_file(installed, capsys):
    root, mod = installed
    path = _write_jobs(root, [_job(), _job(**SONNET_PLAN)])
    assert mod.check(_ctx(mod, root)) == []
    assert mod.main(["jobs", "--root", str(root)]) == 0
    assert "Hermes jobs: OK" in capsys.readouterr().out
    _write_jobs(root, [_job(monitor_url="https://www.amazon.com/s?k=planner")])
    assert any("fetches a web address" in p for p in mod.check(_ctx(mod, root)))
    assert mod.main(["jobs", "--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "PROBLEM: scheduled job 'morning check-in' fetches a web address" in out
    assert "amazon" not in out                            # the job's own text is never echoed
    path.write_text("{not json", encoding="utf-8")
    assert any("not readable JSON" in p for p in mod.check(_ctx(mod, root)))
    path.unlink()
    path.symlink_to(root / "etc" / "hermes" / "hermes.env")
    assert any("not a plain file" in p for p in mod.check(_ctx(mod, root)))


def test_check_fails_when_a_script_can_be_scheduled(installed):
    root, mod = installed
    (root / "home" / "hermes" / ".hermes" / "scripts" / "x.sh").write_text("id\n", encoding="utf-8")
    assert any("/home/hermes/.hermes/scripts is not empty" in p for p in mod.check(_ctx(mod, root)))


def test_jobs_timer_is_hourly_and_stops_hermes():
    assert _unit("botsy-hermes-jobs.timer")["OnCalendar"] == ["*-*-* *:17:00"]
    svc = _unit("botsy-hermes-jobs.service")
    assert "check_lock.py jobs --stop hermes-gateway.service" in svc["ExecStart"][0]
    post = svc["ExecStopPost"][0]
    assert '"$$SERVICE_RESULT" = success' in post and "hermes-gateway.service" in post


def test_gateway_hides_other_programs_data_from_vision():
    inaccessible = " ".join(_unit("hermes-gateway.service")["InaccessiblePaths"]).split()
    for path in ("-/var/lib", "-/var/log", "-/srv", "-/mnt", "-/media", "-/opt/botsy", "-/home/botsy"):
        assert path in inaccessible


def test_soul_names_the_new_limits():
    text = (HERMES / "SOUL.md").read_text(encoding="utf-8")
    for phrase in ("/skills approve", "botsy-weekly-plan", "our own", "hourly"):
        assert phrase in text, phrase


def test_secrets_example_and_readme_say_ten_dollars():
    for path in (HERMES / "hermes.env.example", HERMES / "README.md", HERMES / "config.yaml.template"):
        text = path.read_text(encoding="utf-8")
        assert "$10" in text, path.name
        assert "$5 monthly" not in text, path.name


# ---------------------------------------------------------------------------- the dashboard tab

DASH_FILES = ("manifest.json", "plugin_api.py", "botsy_tab.py", "dist/index.js", "dist/style.css")


def test_template_enables_only_the_botsy_tab(good_cfg, lock, values):
    assert good_cfg["plugins"] == {"enabled": ["botsy"], "auto_apply": False, "auto_update_check_hours": 0,
                                   "isolation": "in_process"}
    assert not (good_cfg.get("dashboard") or {}).get("public_url")
    cfg = copy.deepcopy(good_cfg)
    cfg["plugins"]["enabled"] = []
    assert lock.check_config(cfg, values, "socket") == []          # no tab is fine too


def test_install_lays_out_the_dashboard_tab(installed):
    root, mod = installed
    plugin = root / "home" / "hermes" / ".hermes" / "plugins" / "botsy" / "dashboard"
    ref = root / "usr" / "local" / "lib" / "botsy-hermes" / "dashboard"
    found = sorted(str(p.relative_to(plugin)) for p in plugin.rglob("*") if p.is_file())
    assert found == sorted(DASH_FILES)                               # never the .service or __pycache__
    for f in DASH_FILES:
        assert (plugin / f).read_bytes() == (HERMES / "dashboard" / f).read_bytes() == (ref / f).read_bytes()
        assert stat.S_IMODE((plugin / f).stat().st_mode) == 0o644
    units = root / "etc" / "systemd" / "system"
    assert (units / "hermes-dashboard.service").read_text(encoding="utf-8") == \
        (HERMES / "dashboard" / "hermes-dashboard.service").read_text(encoding="utf-8")
    assert (units / "hermes-dashboard.service.d" / "firewall.conf").exists()
    assert "DASHBOARD=1" in (root / "etc" / "hermes" / "install.conf").read_text(encoding="utf-8")
    assert mod.check(_ctx(mod, root)) == []


@pytest.mark.parametrize("change, expect", [
    ("edit", "botsy_tab.py differs from"),
    ("plugin.yaml", "not the Botsy dashboard tab (botsy/dashboard/plugin.yaml)"),
    ("pycache", "not the Botsy dashboard tab (botsy/dashboard/__pycache__"),
    ("link", "is a symbolic link"),
    ("mode", "botsy_tab.py has mode 0664"),
    ("missing", "dist/style.css is missing"),
])
def test_check_refuses_a_changed_dashboard_tab(installed, change, expect):
    root, mod = installed
    plugin = root / "home" / "hermes" / ".hermes" / "plugins" / "botsy" / "dashboard"
    if change == "edit":
        (plugin / "botsy_tab.py").write_text("print('x')\n", encoding="utf-8")
    elif change == "plugin.yaml":
        (plugin / "plugin.yaml").write_text("name: botsy\n", encoding="utf-8")
    elif change == "pycache":
        (plugin / "__pycache__").mkdir()
    elif change == "link":
        (plugin / "dist" / "index.js").unlink()
        (plugin / "dist" / "index.js").symlink_to(HERMES / "dashboard" / "dist" / "index.js")
    elif change == "mode":
        (plugin / "botsy_tab.py").chmod(0o664)
    elif change == "missing":
        (plugin / "dist" / "style.css").unlink()
    problems = _problems(mod, root)
    assert any(expect in p for p in problems), problems


def test_install_moves_foreign_plugins_aside_and_keeps_the_tab(installed):
    root, mod = installed
    plugins = root / "home" / "hermes" / ".hermes" / "plugins"
    (plugins / "evil").mkdir()
    (plugins / "evil" / "plugin.yaml").write_text("name: evil\n", encoding="utf-8")
    (plugins / "botsy" / "dashboard" / "extra.py").write_text("x", encoding="utf-8")
    r = _install(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.name for p in plugins.iterdir()) == ["botsy"]
    assert not (plugins / "botsy" / "dashboard" / "extra.py").exists()
    assert list(root.glob("root/hermes-backup-*/evil/plugin.yaml"))
    assert mod.check(_ctx(mod, root)) == []


def test_install_without_the_dashboard(installed):
    root, mod = installed
    r = _install(root, "--no-dashboard")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (root / "etc" / "systemd" / "system" / "hermes-dashboard.service").exists()
    assert not any((root / "home" / "hermes" / ".hermes" / "plugins").iterdir())
    assert "DASHBOARD=0" in (root / "etc" / "hermes" / "install.conf").read_text(encoding="utf-8")
    assert mod.check(_ctx(mod, root)) == []
    r = _install(root)                                   # remembered: still off
    assert r.returncode == 0 and not (root / "etc" / "systemd" / "system" / "hermes-dashboard.service").exists()


def test_check_refuses_a_foreign_dashboard_dropin(installed):
    root, mod = installed
    d = root / "etc" / "systemd" / "system" / "hermes-dashboard.service.d"
    (d / "open.conf").write_text("[Service]\nIPAddressAllow=any\n", encoding="utf-8")
    problems = _problems(mod, root)
    assert any("open.conf changes hermes-dashboard.service" in p for p in problems), problems


GOOD_DASH_SHOW = """FragmentPath=/etc/systemd/system/hermes-dashboard.service
DropInPaths=/etc/systemd/system/hermes-dashboard.service.d/firewall.conf
User=hermes
NoNewPrivileges=yes
IPAddressDeny=0.0.0.0/0 ::/0
IPAddressAllow=127.0.0.0/8 ::1/128
ExecStart={ path=/opt/hermes/venv/bin/hermes ; argv[]=/opt/hermes/venv/bin/hermes dashboard --host 127.0.0.1 --port 9119 --no-open --skip-build ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
"""


def test_dashboard_show_good(lock):
    assert lock.dashboard_show_problems(GOOD_DASH_SHOW, True) == []
    unit = (HERMES / "dashboard" / "hermes-dashboard.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/hermes/venv/bin/hermes dashboard --host 127.0.0.1 --port 9119 --no-open --skip-build" in unit
    assert "EnvironmentFile" not in unit.split("[Service]", 1)[1].split("#")[0]
    assert not re.search(r"^EnvironmentFile=", unit, re.M)          # the dashboard holds no secret


@pytest.mark.parametrize("old, new, expect", [
    ("--host 127.0.0.1", "--host 0.0.0.0", "127.0.0.1 only"),
    ("--host 127.0.0.1", "--host 127.0.0.1 --ho=0.0.0.0", "127.0.0.1 only"),
    ("--host 127.0.0.1", "--hos ::", "127.0.0.1 only"),
    ("--port 9119", "--port 9200", "port 9119"),
    ("User=hermes", "User=root", "runs as root"),
    ("NoNewPrivileges=yes", "NoNewPrivileges=no", "NoNewPrivileges"),
    ("IPAddressDeny=0.0.0.0/0 ::/0", "IPAddressDeny=", "IPAddressDeny=any"),
    ("IPAddressAllow=127.0.0.0/8 ::1/128", "IPAddressAllow=127.0.0.0/8 ::1/128 10.0.0.0/8", "more than loopback"),
    ("firewall.conf", "firewall.conf /etc/systemd/system/hermes-dashboard.service.d/zz.conf", "zz.conf"),
    ("FragmentPath=/etc/systemd/system/", "FragmentPath=/run/systemd/system/", "loads hermes-dashboard.service from"),
])
def test_dashboard_show_problems(lock, old, new, expect):
    out = GOOD_DASH_SHOW.replace(old, new, 1)
    problems = lock.dashboard_show_problems(out, True)
    assert any(expect in p for p in problems), problems


def test_installer_builds_the_web_ui_as_nobody_from_the_pinned_lock():
    text = INSTALL.read_text(encoding="utf-8")
    body = text.split("build_web_ui() {", 1)[1].split("\n}\n", 1)[0]
    assert "setpriv --reuid=nobody" in body and "--ignore-scripts" in body and "npm ci" in body
    assert 'sha256sum "$WORK/$name"' in body and "https://nodejs.org/dist/v$NODE_VERSION/" in body
    assert "HERMES_USER" not in body and "-u hermes" not in body
    # The dashboard starts only after the lock check passed.
    final = text.split("final_check() {", 1)[1].split("\n}\n", 1)[0]
    assert final.index("run_check --probe") < final.index("systemctl restart hermes-dashboard.service")
