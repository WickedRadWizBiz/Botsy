#!/usr/bin/python3 -I
"""check_lock.py: is Botsy's lock on Hermes Agent still in place? (docs/OVERSEER.md "Hermes setup")

    check_lock.py check [--quick] [--probe] [--stop UNIT] [--root DIR]
    check_lock.py jobs [--stop UNIT]
    check_lock.py render --env FILE [--mode socket|sudo] [--out FILE]
    check_lock.py render-env --env FILE [--out FILE]
    check_lock.py pin

check   exit 0 when locked, 1 with one "PROBLEM:" line per problem, 2 on wrong usage. It checks:
        the rendered configs (/etc/hermes/config.yaml, the managed scope, and
        /home/hermes/.hermes/config.yaml) equal the template and still lock everything (no forbidden
        toolset outside each place's list, only the two Botsy MCP servers, sampling and elicitation
        off, memory and skill approval on, your user ID as the only allowed user, no worker agents,
        vision kept off the web); Hermes's scheduled jobs (see jobs); that the
        hermes user cannot write any of them, its .env, SOUL.md, hooks/ or plugins/, or the code in
        /opt/hermes; the pinned version; the secrets file; the systemd units and that no foreign
        drop-in changes them; that the hermes user has no sudo beyond botsy-control and cannot read
        Botsy's folders; and the firewall (required since 2026-10-21).
        --quick skips walking every file of /opt/hermes (used before each start).
        --probe also asks the installed Hermes itself, as the hermes user, which toolsets and tools
        each place gets (Discord, scheduled jobs, CLI, webhooks, API server) and what its merged
        settings and environment say.
        --stop UNIT stops that systemd unit when there is a problem (the daily timer stops Hermes).
jobs    only Hermes's scheduled jobs (hourly timer): at most 10 on, none more often than hourly, no
        scripts, URL monitors, work folders, other providers or toolsets; Sonnet only on
        botsy-weekly-plan and botsy-creative-director, at most weekly.
render  writes the config from config.yaml.template with your Discord user ID from the secrets
        file. It refuses a missing or malformed ID. Nothing secret is ever printed.

Runs with the system Python (python3 and python3-yaml on Ubuntu 24.04) as root, from root's own
copy in /usr/local/lib/botsy-hermes/ (install_hermes.sh puts it there).
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "config.yaml.template"
SOUL = HERE / "SOUL.md"
PIN_FILE = HERE / "hermes-pin.env"
BRIDGE = HERE / "botsy-control-connect"
UNIT_DIR_SRC = HERE / "systemd"
SUDOERS_SRC = HERE / "sudoers.botsy-control"     # install_hermes.sh copies deploy/lightsail's rule here
# The Botsy dashboard tab (docs/OVERSEER.md "Botsy dashboard tab"): the reference copy sits in
# dashboard/ next to this file (the repo's deploy/hermes/dashboard, or install_hermes.sh's copy in
# /usr/local/lib/botsy-hermes/dashboard), and the plugin folder must hold exactly these files.
DASHBOARD_SRC = HERE / "dashboard"
DASHBOARD_FILES = ("manifest.json", "plugin_api.py", "botsy_tab.py", "dist/index.js", "dist/style.css")
DASHBOARD_DIRS = ("botsy", "botsy/dashboard", "botsy/dashboard/dist")
DASHBOARD_UNIT = "hermes-dashboard.service"
DASHBOARD_PORT = "9119"
PLUGINS_ALLOWED = ([], ["botsy"])

HERMES_USER = "hermes"
PLATFORMS = ("discord", "cron", "cli", "webhook", "api_server")
MCP_NAMES = ("botsy-read", "botsy-act")
# Hermes's own per-call gate (v0.21.7 tools/mcp_tool_handlers.py _trust_gate_check): on a
# "trust: untrusted" server every tool without readOnlyHint=true waits for your click on an
# approval button in Discord (only allowlisted users may click), and is declined outright in
# scheduled jobs and other sessions nobody can answer. Since there are no codes (decided
# 2026-10-21), this is the one check on Botsy's action tools that does not depend on the model.
MCP_TRUST = {"botsy-read": "full", "botsy-act": "untrusted"}
# What each place gets (decided 2026-10-21, "too cautious; I want Hermes pulling its weight"):
# Discord chat gets everything below; the command line gets no Discord tool, task board or job
# tools; scheduled jobs also get no skill tools (a skill a job changed would not be posted to you:
# a job's tool calls are not shown in Discord; a job can still preload a skill by name); webhooks
# and the API server (unused) keep the minimum. Exact allowlists.
_JOB_TOOLSETS = ("botsy-read", "botsy-act", "memory", "vision", "session_search", "todo")
PLATFORM_TOOLSETS = {
    "discord": frozenset(_JOB_TOOLSETS + ("skills", "cronjob", "kanban", "discord")),
    "cron": frozenset(_JOB_TOOLSETS),
    "cli": frozenset(_JOB_TOOLSETS + ("skills",)),
    "webhook": frozenset({"botsy-read", "botsy-act", "memory"}),
    "api_server": frozenset({"botsy-read", "botsy-act", "memory"}),
}
ALLOWED_TOOLSETS = frozenset().union(*PLATFORM_TOOLSETS.values())
CRON_TOOLSETS = PLATFORM_TOOLSETS["cron"]       # what a scheduled job may name in enabled_toolsets
# Built-in tool names the probe may see per place (v0.21.7 toolsets.py; MCP tools register at
# connect). A tool the pin does not list here is refused, so an update cannot add one quietly.
_JOB_TOOLS = frozenset({"memory", "vision_analyze", "session_search", "todo_list"})
SKILL_TOOLS = frozenset({"skills_list", "skill_view", "skill_manage"})
KANBAN_TOOLS = frozenset({"kanban_attach", "kanban_attach_url", "kanban_attachments", "kanban_block",
                          "kanban_comment", "kanban_complete", "kanban_create", "kanban_heartbeat",
                          "kanban_link", "kanban_list", "kanban_request_changes", "kanban_request_review",
                          "kanban_schedule", "kanban_show", "kanban_unblock"})
PLATFORM_PROBE_TOOLS = {
    "discord": _JOB_TOOLS | SKILL_TOOLS | KANBAN_TOOLS | {"cronjob_manage", "discord"},
    "cron": _JOB_TOOLS,
    "cli": _JOB_TOOLS | SKILL_TOOLS,
    "webhook": frozenset({"memory"}),
    "api_server": frozenset({"memory"}),
}
# Must be in agent.disabled_toolsets (Hermes subtracts these last, whatever else is on). Single
# toolsets only: a bundle ("safe", "coding", "debugging") there would also strip vision and skills.
FORBIDDEN_TOOLSETS = (
    "terminal", "file", "web", "search", "browser", "delegation", "code_execution", "discord_admin",
    "computer_use", "video", "image_gen", "video_gen", "tts", "x_search", "connections", "catalog",
    "project", "desktop_ui", "start_chat", "setup", "bot_room", "context_engine", "yuanbao",
    "feishu_doc", "feishu_drive")
DEFAULT_MODEL = "claude-haiku-5-5"            # chat and routine jobs
SONNET_MODEL = "claude-sonnet-5-5"            # only the two weekly jobs you pin yourself
ALLOWED_MODELS = frozenset({DEFAULT_MODEL, SONNET_MODEL})
SONNET_JOBS = ("botsy-weekly-plan", "botsy-creative-director")
MAX_CRON_JOBS = 10                            # v0.21.7 has no job-count setting
MIN_JOB_MINUTES = 60                          # no job more often than hourly
WEEK_MINUTES = 7 * 24 * 60
JOBS_FILE = "home/hermes/.hermes/cron/jobs.json"
MAX_JOBS_FILE_BYTES = 4_000_000
# The non-admin Discord tool: these actions only (discord.server_actions). Member search is left
# out (other people's details); pins, deletes and roles belong to discord_admin and stay off.
DISCORD_ACTIONS = frozenset({"fetch_messages", "create_thread"})
BLOCK_ALL_HOSTS = "*.*"                       # security.website_blocklist: vision never downloads
# Slash commands (gateway/slash_access.py): an admin list that names no real account turns the
# gate on with nobody as admin; you keep /help, /whoami and these. Hermes gates per user and you
# are the only allowed user, so /loop and /goal are yours alone ("/goal gate add" runs a shell
# command and needs an admin, so it stays refused). /bg stays off: a background run posts only its
# answer, not its tool calls, so a skill it changed would not be posted to you (D4 item 5). /cron
# is command-line only in v0.21.7.
NO_ADMIN = ("0",)
SAFE_SLASH_COMMANDS = frozenset({"new", "retry", "status", "stop", "compress", "memory", "skills",
                                 "loop", "goal", "help", "whoami"})
RISKY_SLASH_COMMANDS = ("model", "yolo", "cron", "bg", "heartbeat", "moa", "tools", "approvals",
                        "reasoning", "fast", "personality", "import", "snapshot", "learn", "refine",
                        "review", "kanban", "curator", "btw", "queue", "steer", "subgoal", "plan",
                        "init", "suggestions")
MAX_COMPRESSION_TOKENS = 80000      # Haiku 5.5 charges 5x above 100,000 prompt tokens
MODES = ("socket", "sudo")
MCP_COMMANDS = {
    "socket": {"botsy-read": ("/usr/local/bin/botsy-control-connect", ["read"]),
               "botsy-act": ("/usr/local/bin/botsy-control-connect", ["act"])},
    "sudo": {"botsy-read": ("/usr/bin/sudo", ["-n", "-u", "botsy", "/usr/local/bin/botsy-control-read"]),
             "botsy-act": ("/usr/bin/sudo", ["-n", "-u", "botsy", "/usr/local/bin/botsy-control-act"])},
}
SECRET_KEYS = ("ANTHROPIC_API_KEY", "DISCORD_BOT_TOKEN")
ID_KEYS = ("DISCORD_ALLOWED_USER_ID",)
ENV_KEYS = SECRET_KEYS + ID_KEYS
PLACEHOLDERS = {"DISCORD_ALLOWED_USER_ID": "__DISCORD_ALLOWED_USER_ID__"}
# Keys from the earlier design that must be deleted (decided 2026-10-21: no codes, so no
# #botsy-codes channel).
RETIRED_KEYS = {"DISCORD_CODES_CHANNEL_ID": "no longer used (decided 2026-10-21: no codes); delete the line"}
SNOWFLAKE = re.compile(r"^[1-9][0-9]{16,19}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
# Environment pins in the managed .env (/etc/hermes/.env): they win over every other source.
MANAGED_ENV_FIXED = (("GATEWAY_ALLOW_ALL_USERS", "false"), ("DISCORD_ALLOW_ALL_USERS", "false"),
                     ("GATEWAY_ALLOWED_USERS", ""), ("DISCORD_ALLOWED_ROLES", ""))
FORBIDDEN_GROUPS = frozenset({"sudo", "admin", "wheel", "adm", "botsy", "botsy-upload", "docker", "lxd",
                              "disk", "systemd-journal", "shadow", "root"})
UNITS = {  # installed name -> source file in systemd/ (socket mode)
    "hermes-gateway.service": "hermes-gateway.service",
    "botsy-hermes-lock.service": "botsy-hermes-lock.service",
    "botsy-hermes-lock.timer": "botsy-hermes-lock.timer",
    "botsy-hermes-jobs.service": "botsy-hermes-jobs.service",
    "botsy-hermes-jobs.timer": "botsy-hermes-jobs.timer",
}
SOCKET_UNITS = ("botsy-control-read.socket", "botsy-control-act.socket",
                "botsy-control-read@.service", "botsy-control-act@.service")
FIREWALL_UNITS = ("botsy-hermes-firewall.service", "botsy-hermes-resolve.service", "botsy-hermes-resolve.timer")
DROPIN_DIRS = ("etc/systemd/system/hermes-gateway.service.d", "etc/systemd/system.control/hermes-gateway.service.d",
               "run/systemd/system/hermes-gateway.service.d", "run/systemd/system.control/hermes-gateway.service.d",
               "run/systemd/transient/hermes-gateway.service.d")
BOTSY_PRIVATE = ("opt/botsy", "etc/botsy", "srv/botsy-inbox/incoming", "srv/botsy-inbox/outbox", "home/botsy")

PROBE = r"""
import json, os, sys
PLATFORMS = %(platforms)r
KEYS = %(keys)r
RISKY = %(risky)r
out = {}
from hermes_cli.config import load_config
from hermes_cli.tools_config import _get_platform_tools
from model_tools import _select_tool_names
cfg = load_config()
disabled = (cfg.get("agent") or {}).get("disabled_toolsets")
plats = {}
for p in PLATFORMS:
    ts = sorted(str(t) for t in _get_platform_tools(cfg, p))
    plats[p] = {"toolsets": ts, "tools": sorted(_select_tool_names(ts, disabled, True))}
out["platforms"] = plats
out["mcp"] = {}
for name, v in (cfg.get("mcp_servers") or {}).items():
    v = v if isinstance(v, dict) else {}
    out["mcp"][str(name)] = {"command": v.get("command"), "args": v.get("args"), "env": v.get("env"),
                             "url": v.get("url"), "enabled": v.get("enabled", True),
                             "sampling": (v.get("sampling") or {}).get("enabled", True),
                             "elicitation": (v.get("elicitation") or {}).get("enabled", True)}
    try:
        from tools.mcp_tool_registration import _normalize_server_trust
        out["mcp"][str(name)]["trust"] = _normalize_server_trust(v.get("trust"))
    except Exception as e:
        out["mcp"][str(name)]["trust_error"] = type(e).__name__
sec = lambda k: cfg.get(k) if isinstance(cfg.get(k), dict) else {}
out["memory_write_approval"] = sec("memory").get("write_approval")
out["memory_provider"] = sec("memory").get("provider")
out["skills_write_approval"] = sec("skills").get("write_approval")
out["agent_scheduling"] = sec("cron").get("allow_agent_scheduling")
out["cron"] = {k: sec("cron").get(k) for k in ("provider", "model", "model_provider")}
out["kanban"] = {k: sec("kanban").get(k) for k in ("dispatch_in_gateway", "dispatch_profiles", "auto_decompose")}
out["background"] = {"review": (sec("auxiliary").get("background_review") or {}).get("enabled"),
                     "curator": sec("curator").get("enabled")}
try:
    from tools.website_policy import check_website_access
    out["web_blocked"] = {u: bool(check_website_access(u)) for u in
                          ("https://www.etsy.com/x.jpg", "https://i.etsystatic.com/x.jpg",
                           "https://cdn.discordapp.com/x.png", "https://example.com/x.png")}
except Exception as e:
    out["web_blocked_error"] = type(e).__name__
try:
    from tools.discord_tool import _load_allowed_actions_config
    out["discord_actions"] = _load_allowed_actions_config()
except Exception as e:
    out["discord_actions_error"] = type(e).__name__
try:
    from gateway.display_config import resolve_tool_progress
    out["discord_tool_progress"] = resolve_tool_progress(cfg, "discord")[0]
except Exception as e:
    out["discord_tool_progress_error"] = type(e).__name__
out["model"] = sec("model").get("default")
out["provider"] = sec("model").get("provider")
out["allow_from"] = sec("discord").get("allow_from")
try:
    from hermes_cli.env_loader import load_hermes_dotenv
    load_hermes_dotenv()
    out["env"] = {k: os.environ.get(k) for k in KEYS}
except Exception as e:
    out["env_error"] = type(e).__name__
try:
    from types import SimpleNamespace
    from gateway.config import load_gateway_config, Platform
    from gateway.slash_access import policy_for_source
    gw = load_gateway_config()
    user = (out["allow_from"] or [""])[0] if isinstance(out["allow_from"], list) else ""
    slash = {}
    for chat_type in ("dm", "group", ""):
        pol = policy_for_source(gw, SimpleNamespace(platform=Platform.DISCORD, chat_type=chat_type))
        slash[chat_type or "unknown"] = {"gated": bool(pol.enabled),
                                         "open": sorted(c for c in RISKY if pol.can_run(str(user), c))}
    out["slash"] = slash
except Exception as e:
    out["slash_error"] = type(e).__name__
try:
    from hermes_cli.version_info import get_version_info
    vi = get_version_info()
    out["version"] = vi.base_version
    out["commit"] = vi.commit
except Exception as e:
    out["version_error"] = type(e).__name__
print("BOTSY-PROBE " + json.dumps(out, sort_keys=True))
"""


# ---------------------------------------------------------------------------- small helpers

def read_kv(path: Path) -> dict[str, str]:
    """KEY=value lines (systemd EnvironmentFile style); comments and blank lines skipped. Never sourced."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            out.setdefault("__malformed__", "")
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def load_pin(path: Path = PIN_FILE) -> dict[str, str]:
    return read_kv(path)


def validate_values(values: dict[str, str]) -> list[str]:
    """Problems with the Discord user ID (never echoes a value)."""
    problems = []
    for key in ID_KEYS:
        if not SNOWFLAKE.match(values.get(key, "")):
            problems.append(f"{key} in the secrets file is missing or is not a Discord ID (17-20 digits)")
    return problems


def render(template_text: str, values: dict[str, str], mode: str = "socket") -> str:
    """The config text for these IDs and this bridge mode. Raises ValueError on a bad ID or mode."""
    if mode not in MODES:
        raise ValueError(f"unknown bridge mode {mode!r}")
    bad = validate_values(values)
    if bad:
        raise ValueError("; ".join(bad))
    text = template_text
    for key, placeholder in PLACEHOLDERS.items():
        if placeholder not in text:
            raise ValueError(f"the template has no {placeholder}")
        text = text.replace(placeholder, values[key])
    if mode == "sudo":
        for name, (command, args) in MCP_COMMANDS["sudo"].items():
            s_cmd, s_args = MCP_COMMANDS["socket"][name]
            old = f"    command: {s_cmd}\n    args: {json.dumps(s_args)}\n"
            if old not in text:
                raise ValueError(f"the template's {name} command lines are not the expected socket form")
            text = text.replace(old, f"    command: {command}\n    args: {json.dumps(args)}\n", 1)
    yaml = _yaml()
    if yaml is not None:
        problems = check_config(yaml.safe_load(text), values, mode, where="rendered template")
        if problems:
            raise ValueError("the template does not keep the lock: " + "; ".join(problems))
    return text


def render_managed_env(values: dict[str, str]) -> str:
    bad = validate_values(values)
    if bad:
        raise ValueError("; ".join(bad))
    lines = ["# /etc/hermes/.env: Hermes's managed environment (written by install_hermes.sh; do not edit).",
             "# These values win over the hermes user's .env, the service environment and the shell.",
             "# No secrets here: this file is world-readable by design (Hermes managed scope)."]
    lines += [f"{k}={v}" for k, v in MANAGED_ENV_FIXED]
    lines.append(f"DISCORD_ALLOWED_USERS={values['DISCORD_ALLOWED_USER_ID']}")
    lines.append("DISCORD_IGNORED_CHANNELS=")
    return "\n".join(lines) + "\n"


def expected_managed_env(values: dict[str, str]) -> dict[str, str]:
    out = dict(MANAGED_ENV_FIXED)
    out["DISCORD_ALLOWED_USERS"] = values.get("DISCORD_ALLOWED_USER_ID", "")
    out["DISCORD_IGNORED_CHANNELS"] = ""
    return out


def _yaml():
    try:
        import yaml  # python3-yaml on Ubuntu
    except ImportError:  # pragma: no cover - depends on the server
        return None
    return yaml


# ---------------------------------------------------------------------------- config semantics

def check_config(cfg, values: dict[str, str], mode: str, *, where: str = "config") -> list[str]:
    """What this parsed config turns on, judged on its own (independent of the template text)."""
    p: list[str] = []
    if not isinstance(cfg, dict):
        return [f"{where}: not a YAML mapping"]
    pin = load_pin()

    def sec(key):
        v = cfg.get(key)
        return v if isinstance(v, dict) else {}

    if str(cfg.get("_config_version")) != pin.get("HERMES_CONFIG_VERSION"):
        p.append(f"{where}: _config_version is not {pin.get('HERMES_CONFIG_VERSION')} (the pinned schema)")
    text = json.dumps(cfg)
    if "__DISCORD_" in text:
        p.append(f"{where}: a placeholder was not filled in")
    model = sec("model")
    if model.get("provider") != "anthropic":
        p.append(f"{where}: model.provider is not anthropic")
    if model.get("default") != DEFAULT_MODEL:
        p.append(f"{where}: model.default is not one of [{DEFAULT_MODEL!r}] (Sonnet only in the jobs you pin)")
    for k in ("base_url", "api_key"):
        if model.get(k):
            p.append(f"{where}: model.{k} is set (Hermes must use the Anthropic API with the key from the secrets file)")
    if cfg.get("fallback_providers"):
        p.append(f"{where}: fallback_providers is not empty")
    if cfg.get("providers"):
        p.append(f"{where}: providers is not empty")
    if cfg.get("custom_toolsets"):
        p.append(f"{where}: custom_toolsets is set")
    for k in ("allow_all_users",):
        if cfg.get(k):
            p.append(f"{where}: {k} is on")

    def toolset_list(name, value, allowed):
        if not isinstance(value, list) or not value:
            p.append(f"{where}: {name} is not a list of Botsy toolsets")
            return
        extra = sorted(str(t) for t in value if str(t) not in allowed)
        if extra:
            p.append(f"{where}: {name} turns on {', '.join(extra)}")
        if not set(MCP_NAMES) <= {str(t) for t in value}:
            p.append(f"{where}: {name} does not name Botsy's two tool servers")

    toolset_list("toolsets", cfg.get("toolsets"), ALLOWED_TOOLSETS)
    plat = sec("platform_toolsets")
    for name in PLATFORMS:
        toolset_list(f"platform_toolsets.{name}", plat.get(name), PLATFORM_TOOLSETS[name])
    for name in plat:
        if name not in PLATFORMS:
            p.append(f"{where}: platform_toolsets.{name} is not one of Botsy's places ({', '.join(PLATFORMS)})")
    disabled = sec("agent").get("disabled_toolsets")
    disabled = {str(t) for t in disabled} if isinstance(disabled, list) else set()
    missing = [t for t in FORBIDDEN_TOOLSETS if t not in disabled]
    if missing:
        p.append(f"{where}: agent.disabled_toolsets does not turn off {', '.join(missing)}")

    mcp = sec("mcp_servers")
    names = sorted(str(n) for n in mcp)
    if names != sorted(MCP_NAMES):
        extra = sorted(set(names) - set(MCP_NAMES))
        lost = sorted(set(MCP_NAMES) - set(names))
        if extra:
            p.append(f"{where}: MCP servers other than Botsy's: {', '.join(extra)}")
        if lost:
            p.append(f"{where}: Botsy's MCP server missing: {', '.join(lost)}")
    for name in MCP_NAMES:
        server = mcp.get(name)
        if not isinstance(server, dict):
            continue
        command, args = MCP_COMMANDS[mode][name]
        if server.get("command") != command or server.get("args") != args:
            p.append(f"{where}: mcp_servers.{name} does not start {command} {' '.join(args)} ({mode} bridge)")
        if server.get("env") not in ({}, None):
            p.append(f"{where}: mcp_servers.{name} passes environment variables")
        for k in ("url", "headers", "auth", "client_cert"):
            if k in server:
                p.append(f"{where}: mcp_servers.{name} has {k}")
        if server.get("enabled", True) is not True:
            p.append(f"{where}: mcp_servers.{name} is not enabled")
        for k in ("sampling", "elicitation"):
            sub = server.get(k)
            if not isinstance(sub, dict) or sub.get("enabled") is not False:
                p.append(f"{where}: mcp_servers.{name}.{k} is not explicitly off (Hermes turns it on by default)")
        tools = server.get("tools") if isinstance(server.get("tools"), dict) else {}
        for k in ("resources", "prompts"):
            if tools.get(k) is not False:
                p.append(f"{where}: mcp_servers.{name}.tools.{k} is not off")
        if server.get("trust") != MCP_TRUST[name]:
            p.append(f"{where}: mcp_servers.{name}.trust is not {MCP_TRUST[name]}"
                     + (" (each Botsy action must wait for your approval click)" if name == "botsy-act" else ""))

    memory = sec("memory")
    if memory.get("write_approval") is not True:
        p.append(f"{where}: memory.write_approval is not on")
    if memory.get("provider"):
        p.append(f"{where}: memory.provider is set (an outside memory service)")
    skills = sec("skills")
    if skills.get("write_approval") is not True:
        p.append(f"{where}: skills.write_approval is not on")
    if skills.get("external_dirs") or skills.get("trusted_project_dirs") or skills.get("auto_load"):
        p.append(f"{where}: skills load from outside folders or auto-load")
    if skills.get("project_discovery") is not False or skills.get("inline_shell") is not False:
        p.append(f"{where}: skills.project_discovery or skills.inline_shell is not off")
    if skills.get("guard_agent_created") is not True:
        p.append(f"{where}: skills.guard_agent_created is not on (skills Hermes writes are not scanned)")
    cron = sec("cron")
    if cron.get("allow_agent_scheduling") is not False:
        p.append(f"{where}: cron.allow_agent_scheduling is not off (a running job could make more jobs)")
    # cron.provider is the SCHEDULER (v0.21.7: "" = the built-in ticker); the model's provider is
    # cron.model_provider.
    if cron.get("provider") not in ("", None):
        p.append(f"{where}: cron.provider is set (an outside scheduler)")
    if cron.get("model_provider") != "anthropic" or cron.get("model") != DEFAULT_MODEL:
        p.append(f"{where}: scheduled jobs are not fixed to {DEFAULT_MODEL} on anthropic")
    kanban = sec("kanban")
    if kanban.get("dispatch_in_gateway") is not False or kanban.get("dispatch_profiles") != []:
        p.append(f"{where}: the task board may start worker agents (kanban.dispatch_in_gateway "
                 f"must be false and kanban.dispatch_profiles [])")
    if kanban.get("auto_decompose") is not False or kanban.get("review_dispatch") is not False:
        p.append(f"{where}: kanban.auto_decompose or kanban.review_dispatch is not off")
    block = sec("security").get("website_blocklist")
    block = block if isinstance(block, dict) else {}
    domains = block.get("domains") if isinstance(block.get("domains"), list) else []
    if block.get("enabled") is not True or BLOCK_ALL_HOSTS not in domains:
        p.append(f"{where}: security.website_blocklist does not block every host "
                 f"(vision could download images from the web)")
    if sec("security").get("allow_private_urls") is not False:
        p.append(f"{where}: security.allow_private_urls is not off")
    approvals = sec("approvals")
    for k in ("cron_mode", "unattended_mode"):
        if approvals.get(k) != "deny":
            p.append(f"{where}: approvals.{k} is not deny")

    discord = sec("discord")
    allow = discord.get("allow_from")
    if not isinstance(allow, list) or len(allow) != 1 or not SNOWFLAKE.match(str(allow[0])):
        p.append(f"{where}: discord.allow_from is not exactly one Discord user ID (the allowlist is empty or open)")
    elif values.get("DISCORD_ALLOWED_USER_ID") and str(allow[0]) != values["DISCORD_ALLOWED_USER_ID"]:
        p.append(f"{where}: discord.allow_from is not DISCORD_ALLOWED_USER_ID from the secrets file")
    if discord.get("allow_all_users") is not False:
        p.append(f"{where}: discord.allow_all_users is not off")
    if discord.get("allowed_roles"):
        p.append(f"{where}: discord.allowed_roles is set (roles must never unlock Hermes)")
    if discord.get("require_mention") is not True:
        p.append(f"{where}: discord.require_mention is not on")
    actions = discord.get("server_actions")
    if not isinstance(actions, list) or not actions or not {str(a) for a in actions} <= DISCORD_ACTIONS:
        p.append(f"{where}: discord.server_actions is not a list within {sorted(DISCORD_ACTIONS)} "
                 f"(empty means every action)")
    for key in ("allow_admin_from", "group_allow_admin_from"):
        if discord.get(key) != list(NO_ADMIN):
            p.append(f"{where}: discord.{key} is not [\"0\"] (without it every slash command is open, "
                     f"including /model, /yolo and /cron)")
    for key in ("user_allowed_commands", "group_user_allowed_commands"):
        cmds = discord.get(key)
        if not isinstance(cmds, list):
            p.append(f"{where}: discord.{key} is not a list")
            continue
        extra = sorted(str(c).lstrip("/").lower() for c in cmds
                       if str(c).lstrip("/").lower() not in SAFE_SLASH_COMMANDS)
        if extra:
            p.append(f"{where}: discord.{key} opens the slash commands {', '.join(extra)}")
    display = sec("display")
    plats = display.get("platforms") if isinstance(display.get("platforms"), dict) else {}
    shown = plats.get("discord") if isinstance(plats.get("discord"), dict) else {}
    if shown.get("tool_progress") != "all" or display.get("tool_progress_command") is not False \
            or "tool_progress_overrides" in display or "tool_progress" in display:
        p.append(f"{where}: display.platforms.discord.tool_progress is not pinned to all (each skill change "
                 f"and each job Hermes makes must be shown to you in Discord)")
    if sec("gateway").get("allow_all_users") is not False:
        p.append(f"{where}: gateway.allow_all_users is not off")
    if sec("gateway").get("unauthorized_dm_behavior") != "ignore":
        p.append(f"{where}: gateway.unauthorized_dm_behavior is not ignore")

    plugins = sec("plugins")
    enabled = plugins.get("enabled")
    if (enabled is not None and enabled not in PLUGINS_ALLOWED) or plugins.get("auto_apply"):
        p.append(f"{where}: plugins are enabled or auto-applied (only the Botsy dashboard tab, plugins.enabled: "
                 f"[botsy], may be on)")
    if "isolation" in plugins and plugins.get("isolation") != "in_process":
        p.append(f"{where}: plugins.isolation is not in_process")
    if sec("dashboard").get("public_url"):
        p.append(f"{where}: dashboard.public_url is set (the dashboard is reached only through an SSH tunnel)")
    if cfg.get("hooks") or cfg.get("hooks_auto_accept"):
        p.append(f"{where}: hooks are configured or auto-accepted")
    if cfg.get("quick_commands"):
        p.append(f"{where}: quick_commands are configured (they can run shell commands)")
    if sec("security").get("allow_lazy_installs") is not False:
        p.append(f"{where}: security.allow_lazy_installs is not off")
    if sec("updates").get("check") is not False:
        p.append(f"{where}: updates.check is not off")
    shared = sec("telemetry").get("shared_metrics") if isinstance(sec("telemetry").get("shared_metrics"), dict) else {}
    if shared.get("send"):
        p.append(f"{where}: telemetry is sent")
    comp = sec("compression")
    tokens = comp.get("threshold_tokens")
    if comp.get("enabled") is not True or not isinstance(tokens, int) or tokens > MAX_COMPRESSION_TOKENS:
        p.append(f"{where}: chat history is not compressed by {MAX_COMPRESSION_TOKENS} tokens (Haiku costs 5x above 100,000)")
    aux = sec("auxiliary")
    review = aux.get("background_review") if isinstance(aux.get("background_review"), dict) else {}
    if review.get("enabled") is not False:
        p.append(f"{where}: auxiliary.background_review is on (it replays every chat to the model)")
    if sec("curator").get("enabled") is not False:
        p.append(f"{where}: curator is on")
    return p


# ---------------------------------------------------------------------------- files and owners

@dataclass
class Who:
    uid: int
    gid: int
    gids: frozenset = field(default_factory=frozenset)


@dataclass
class Ctx:
    root: Path = Path("/")
    hermes: Who | None = None
    admin_uid: int = 0
    system: bool = False           # the real server: run sudo -l, nft, groups, the probe as hermes
    problems: list = field(default_factory=list)

    def path(self, rel: str) -> Path:
        return self.root / rel

    def add(self, text: str) -> None:
        self.problems.append(text)


def hermes_identity(name: str = HERMES_USER) -> Who | None:
    try:
        pw = pwd.getpwnam(name)
    except KeyError:
        return None
    try:
        gids = frozenset(os.getgrouplist(name, pw.pw_gid))
    except OSError:  # pragma: no cover
        gids = frozenset({pw.pw_gid})
    return Who(pw.pw_uid, pw.pw_gid, gids)


def _writable_by(st: os.stat_result, who: Who) -> bool:
    if st.st_uid == who.uid:
        return True
    if st.st_mode & stat.S_IWOTH:
        return True
    return bool(st.st_mode & stat.S_IWGRP) and st.st_gid in who.gids


def _readable_dir_by(st: os.stat_result, who: Who) -> bool:
    if st.st_uid == who.uid:
        return True
    if st.st_mode & stat.S_IXOTH:
        return True
    return bool(st.st_mode & stat.S_IXGRP) and st.st_gid in who.gids


def locked_path(ctx: Ctx, rel: str, *, kind: str = "file", label: str | None = None,
                shared_sticky: bool = False) -> os.stat_result | None:
    """The path exists, is no symlink, is owned by root and cannot be changed or replaced by hermes.

    shared_sticky: a root-owned folder the hermes user may add files to (its home and HERMES_HOME):
    it must carry the sticky bit, so hermes can never rename or delete root's files in it."""
    path, label = ctx.path(rel), label or "/" + rel
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        ctx.add(f"{label} is missing")
        return None
    if stat.S_ISLNK(st.st_mode):
        ctx.add(f"{label} is a symbolic link")
        return None
    if kind == "file" and not stat.S_ISREG(st.st_mode):
        ctx.add(f"{label} is not a regular file")
        return None
    if kind == "dir" and not stat.S_ISDIR(st.st_mode):
        ctx.add(f"{label} is not a folder")
        return None
    if st.st_uid != ctx.admin_uid:
        ctx.add(f"{label} is not owned by root")
    if ctx.hermes is not None:
        if shared_sticky:
            if not st.st_mode & stat.S_ISVTX:
                ctx.add(f"{label} has no sticky bit (the hermes user could rename root's files in it)")
        elif _writable_by(st, ctx.hermes):
            ctx.add(f"{label} can be changed by the hermes user")
        # A parent folder hermes may write lets it rename or replace this path, unless the folder
        # has the sticky bit (then only the owner of an entry may rename it, and this one is root's).
        parent = path.parent
        while True:
            try:
                pst = os.lstat(parent)
            except FileNotFoundError:
                break
            if _writable_by(pst, ctx.hermes) and not (pst.st_mode & stat.S_ISVTX and st.st_uid != ctx.hermes.uid):
                ctx.add(f"{label} can be replaced by the hermes user (it can write {parent})")
                break
            if parent == ctx.root or parent == parent.parent:
                break
            st, parent = pst, parent.parent
    return os.lstat(path)


def same_text(ctx: Ctx, rel: str, expected: str, label: str) -> None:
    try:
        if ctx.path(rel).read_text(encoding="utf-8") != expected:
            ctx.add(f"/{rel} differs from {label} (edited by hand or out of date: run install_hermes.sh)")
    except (OSError, UnicodeDecodeError):
        pass  # reported by locked_path


def check_mode(ctx: Ctx, rel: str, st: os.stat_result | None, mode: int, *, group: str | None = None) -> None:
    if st is None:
        return
    if stat.S_IMODE(st.st_mode) != mode:
        ctx.add(f"/{rel} has mode {stat.S_IMODE(st.st_mode):04o}, expected {mode:04o}")
    if group == "hermes" and ctx.hermes is not None and st.st_gid != ctx.hermes.gid:
        ctx.add(f"/{rel} is not in the hermes group")
    if group == "root" and st.st_gid != 0 and ctx.admin_uid == 0:
        ctx.add(f"/{rel} is not in the root group")


def check_secrets(ctx: Ctx) -> dict[str, str]:
    rel = "etc/hermes/hermes.env"
    st = locked_path(ctx, rel)
    check_mode(ctx, rel, st, 0o640, group="hermes")
    if st is None:
        return {}
    try:
        values = read_kv(ctx.path(rel))
    except (OSError, UnicodeDecodeError):
        ctx.add(f"/{rel} cannot be read")
        return {}
    if "__malformed__" in values:
        ctx.add(f"/{rel} has a line that is not KEY=value")
    for k, why in RETIRED_KEYS.items():
        if k in values:
            ctx.add(f"{k} in /{rel} is {why}")
    extra = sorted(k for k in values if k not in ENV_KEYS and k not in RETIRED_KEYS and k != "__malformed__")
    if extra:
        ctx.add(f"/{rel} sets keys that are not allowed: {', '.join(extra)}")
    for key in SECRET_KEYS:
        if not values.get(key):
            ctx.add(f"{key} in /{rel} is empty")
    key = values.get("ANTHROPIC_API_KEY", "")
    if key and not key.startswith("sk-ant-"):
        ctx.add(f"ANTHROPIC_API_KEY in /{rel} does not look like an Anthropic key")
    for problem in validate_values(values):
        ctx.add(problem)
    return values


def check_configs(ctx: Ctx, values: dict[str, str], mode: str) -> None:
    yaml = _yaml()
    try:
        expected = render(TEMPLATE.read_text(encoding="utf-8"), values, mode)
    except ValueError:
        expected = None   # the ID problems are already listed
    for rel, perm, group, label in (("etc/hermes/config.yaml", 0o644, "root", "managed scope"),
                                    ("home/hermes/.hermes/config.yaml", 0o640, "hermes", "user config")):
        st = locked_path(ctx, rel)
        check_mode(ctx, rel, st, perm, group=group)
        if st is None:
            continue
        if expected is not None:
            same_text(ctx, rel, expected, "the template")
        if yaml is None:
            ctx.add("python3-yaml is not installed, so the config cannot be read (apt install python3-yaml)")
            continue
        try:
            cfg = yaml.safe_load(ctx.path(rel).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - any parse error is a problem
            ctx.add(f"/{rel} is not valid YAML")
            continue
        for problem in check_config(cfg, values, mode, where=f"{label} /{rel}"):
            ctx.add(problem)


def check_managed_env(ctx: Ctx, values: dict[str, str]) -> None:
    rel = "etc/hermes/.env"
    st = locked_path(ctx, rel)
    check_mode(ctx, rel, st, 0o644, group="root")
    if st is None:
        return
    try:
        got = read_kv(ctx.path(rel))
    except (OSError, UnicodeDecodeError):
        ctx.add(f"/{rel} cannot be read")
        return
    want = expected_managed_env(values)
    if got != want:
        wrong = sorted(set(got) ^ set(want)) + sorted(k for k in want if k in got and got[k] != want[k])
        ctx.add(f"/{rel} does not pin the expected values ({', '.join(sorted(set(wrong)))})")
    if any(k in got for k in SECRET_KEYS):
        ctx.add(f"/{rel} holds a secret (it is world-readable)")


def check_home(ctx: Ctx) -> None:
    for rel in ("home/hermes", "home/hermes/.hermes"):
        st = locked_path(ctx, rel, kind="dir", shared_sticky=True)
        check_mode(ctx, rel, st, 0o1770, group="hermes")
    for rel in ("home/hermes/.hermes/.env", "home/hermes/.hermes/SOUL.md"):
        st = locked_path(ctx, rel)
        check_mode(ctx, rel, st, 0o644, group="root")
    try:
        text = ctx.path("home/hermes/.hermes/.env").read_text(encoding="utf-8")
        if any(line.strip() and not line.strip().startswith("#") for line in text.splitlines()):
            ctx.add("/home/hermes/.hermes/.env is not empty (it would override the service environment)")
    except (OSError, UnicodeDecodeError):
        pass
    same_text(ctx, "home/hermes/.hermes/SOUL.md", SOUL.read_text(encoding="utf-8"), str(SOUL))
    for rel in ("home/hermes/.hermes/hooks", "home/hermes/.hermes/plugins", "home/hermes/.hermes/profiles",
                "home/hermes/.hermes/scripts"):
        st = locked_path(ctx, rel, kind="dir")
        check_mode(ctx, rel, st, 0o755, group="root")
        if rel.endswith("/plugins"):
            check_plugins(ctx, rel)
            continue
        try:
            if any(ctx.path(rel).iterdir()):
                ctx.add(f"/{rel} is not empty (Hermes would load what is in it at start)")
        except OSError:
            pass
    st = locked_path(ctx, "etc/hermes", kind="dir")
    check_mode(ctx, "etc/hermes", st, 0o755, group="root")
    if ctx.path("etc/hermes/install.conf").exists():
        st = locked_path(ctx, "etc/hermes/install.conf")
        check_mode(ctx, "etc/hermes/install.conf", st, 0o644, group="root")


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def check_plugins(ctx: Ctx, rel: str) -> None:
    """~/.hermes/plugins holds nothing, or exactly the Botsy dashboard tab: root-owned folders and
    files (755 / 644), each file identical to the reference copy, nothing else (no plugin.yaml,
    plugin.json, __init__.py, __pycache__ or link). Hermes loads what it finds there."""
    base = ctx.path(rel)
    try:
        found = sorted(str(p.relative_to(base)) for p in base.rglob("*"))
    except OSError:
        return
    if not found:
        return
    want = set(DASHBOARD_DIRS) | {f"botsy/dashboard/{f}" for f in DASHBOARD_FILES}
    extra = [f for f in found if f not in want]
    if extra:
        ctx.add(f"/{rel} holds files that are not the Botsy dashboard tab ({', '.join(extra[:5])}); "
                f"Hermes would load what is in it at start")
    for d in DASHBOARD_DIRS:
        st = locked_path(ctx, f"{rel}/{d}", kind="dir")
        check_mode(ctx, f"{rel}/{d}", st, 0o755, group="root")
    for f in DASHBOARD_FILES:
        frel = f"{rel}/botsy/dashboard/{f}"
        st = locked_path(ctx, frel)
        check_mode(ctx, frel, st, 0o644, group="root")
        if st is not None and _sha256(ctx.path(frel)) != _sha256(DASHBOARD_SRC / f):
            ctx.add(f"/{frel} differs from {DASHBOARD_SRC / f} (edited by hand or out of date: run install_hermes.sh)")


def check_code(ctx: Ctx, quick: bool) -> None:
    pin = load_pin()
    for rel in ("opt/hermes", "opt/hermes/src", "opt/hermes/venv", "opt/hermes/work", "opt/hermes/src/.git"):
        locked_path(ctx, rel, kind="dir")
    try:
        if any(ctx.path("opt/hermes/work").iterdir()):
            ctx.add("/opt/hermes/work is not empty (Hermes would read project files from it)")
    except OSError:
        pass
    locked_path(ctx, "opt/hermes/venv/bin/hermes")
    try:
        head = ctx.path("opt/hermes/src/.git/HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        head = ""
    if head != pin.get("HERMES_COMMIT"):
        ctx.add(f"/opt/hermes/src is not at the pinned commit of v{pin.get('HERMES_VERSION')} ({pin.get('HERMES_COMMIT', '')[:12]})")
    try:
        stamp = json.loads(ctx.path("opt/hermes/src/install-stamp.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stamp = None
    if not isinstance(stamp, dict):
        ctx.add("/opt/hermes/src/install-stamp.json is missing or unreadable")
    else:
        if stamp.get("baseVersion") != pin.get("HERMES_VERSION") or stamp.get("commit") != pin.get("HERMES_COMMIT"):
            ctx.add(f"the installed Hermes is not the pinned v{pin.get('HERMES_VERSION')}")
        if stamp.get("updateMechanism") != "external":
            ctx.add("the install stamp lets `hermes update` replace the code (updateMechanism is not external)")
    try:
        marker = ctx.path("opt/hermes/venv/.botsy-pin").read_text(encoding="utf-8").strip()
    except OSError:
        marker = ""
    if marker != pin.get("HERMES_COMMIT"):
        ctx.add("/opt/hermes/venv was not built for the pinned commit (run install_hermes.sh)")
    if quick or ctx.hermes is None:
        return
    seen = 0
    for top in ("opt/hermes",):
        for dirpath, dirnames, filenames in os.walk(ctx.path(top)):
            for name in dirnames + filenames:
                full = Path(dirpath) / name
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                seen += 1
                if st.st_uid == ctx.hermes.uid or (not stat.S_ISLNK(st.st_mode) and _writable_by(st, ctx.hermes)):
                    ctx.add(f"{full} can be changed by the hermes user")
                    return


def check_bridge_and_units(ctx: Ctx, mode: str, firewall: bool) -> None:
    units = "etc/systemd/system"
    for name, src in UNITS.items():
        rel = f"{units}/{name}"
        st = locked_path(ctx, rel)
        check_mode(ctx, rel, st, 0o644)
        if st is not None:
            same_text(ctx, rel, (UNIT_DIR_SRC / src).read_text(encoding="utf-8"), f"systemd/{src}")
    if mode == "socket":
        rel = "usr/local/bin/botsy-control-connect"
        st = locked_path(ctx, rel)
        check_mode(ctx, rel, st, 0o755)
        if st is not None:
            same_text(ctx, rel, BRIDGE.read_text(encoding="utf-8"), str(BRIDGE))
        for name in SOCKET_UNITS:
            rel = f"{units}/{name}"
            if locked_path(ctx, rel) is not None:
                same_text(ctx, rel, (UNIT_DIR_SRC / name).read_text(encoding="utf-8"), f"systemd/{name}")
    allowed = {}
    if mode == "sudo":
        allowed["sudo-bridge.conf"] = (UNIT_DIR_SRC / "sudo-bridge.conf").read_text(encoding="utf-8")
    if firewall:
        allowed["firewall.conf"] = (UNIT_DIR_SRC / "firewall.conf").read_text(encoding="utf-8")
        for name in FIREWALL_UNITS:
            rel = f"{units}/{name}"
            if locked_path(ctx, rel) is not None:
                same_text(ctx, rel, (UNIT_DIR_SRC / name).read_text(encoding="utf-8"), f"systemd/{name}")
    for d in DROPIN_DIRS:
        folder = ctx.path(d)
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            if d == DROPIN_DIRS[0] and entry.name in allowed:
                rel = f"{d}/{entry.name}"
                if locked_path(ctx, rel) is not None:
                    same_text(ctx, rel, allowed[entry.name], f"systemd/{entry.name}")
                continue
            ctx.add(f"/{d}/{entry.name} changes hermes-gateway.service (a drop-in install_hermes.sh did not write)")
    for name in allowed:
        if not ctx.path(f"{DROPIN_DIRS[0]}/{name}").exists():
            ctx.add(f"/{DROPIN_DIRS[0]}/{name} is missing")
    if ctx.system:
        r = subprocess.run(["systemctl", "show", "-p", "FragmentPath", "-p", "DropInPaths", "-p", "User",
                            "-p", "NoNewPrivileges", "hermes-gateway.service"], capture_output=True, text=True)
        if r.returncode != 0:
            ctx.add("systemctl show hermes-gateway.service failed")
        for problem in unit_show_problems(r.stdout or "", mode, firewall):
            ctx.add(problem)
    check_dashboard_unit(ctx, firewall)


DASHBOARD_DROPIN_DIRS = tuple(d.replace("hermes-gateway.service.d", f"{DASHBOARD_UNIT}.d") for d in DROPIN_DIRS)


def check_dashboard_unit(ctx: Ctx, firewall: bool) -> None:
    """hermes-dashboard.service (optional): the installer's copy, only the firewall drop-in, and,
    on the server, what systemd really loaded (dashboard_show_problems)."""
    rel = f"etc/systemd/system/{DASHBOARD_UNIT}"
    if not ctx.path(rel).exists() and not ctx.path(rel).is_symlink():
        return
    st = locked_path(ctx, rel)
    check_mode(ctx, rel, st, 0o644)
    if st is not None:
        same_text(ctx, rel, (DASHBOARD_SRC / DASHBOARD_UNIT).read_text(encoding="utf-8"), f"dashboard/{DASHBOARD_UNIT}")
    fw = (UNIT_DIR_SRC / "firewall.conf").read_text(encoding="utf-8")
    for d in DASHBOARD_DROPIN_DIRS:
        folder = ctx.path(d)
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            if d == DASHBOARD_DROPIN_DIRS[0] and entry.name == "firewall.conf" and firewall:
                if locked_path(ctx, f"{d}/{entry.name}") is not None:
                    same_text(ctx, f"{d}/{entry.name}", fw, "systemd/firewall.conf")
                continue
            ctx.add(f"/{d}/{entry.name} changes {DASHBOARD_UNIT} (a drop-in install_hermes.sh did not write)")
    if firewall and not ctx.path(f"{DASHBOARD_DROPIN_DIRS[0]}/firewall.conf").exists():
        ctx.add(f"/{DASHBOARD_DROPIN_DIRS[0]}/firewall.conf is missing")
    if ctx.system:
        # The web UI root built at install time (HERMES_WEB_DIST); the hermes user never builds it.
        locked_path(ctx, "opt/hermes/web_dist", kind="dir")
        locked_path(ctx, "opt/hermes/web_dist/index.html")
        r = subprocess.run(["systemctl", "show", "-p", "FragmentPath", "-p", "DropInPaths", "-p", "User",
                            "-p", "NoNewPrivileges", "-p", "IPAddressDeny", "-p", "IPAddressAllow",
                            "-p", "ExecStart", DASHBOARD_UNIT], capture_output=True, text=True)
        if r.returncode != 0:
            ctx.add(f"systemctl show {DASHBOARD_UNIT} failed")
        for problem in dashboard_show_problems(r.stdout or "", firewall):
            ctx.add(problem)


_HOST_ARG = re.compile(r"^--ho(?:s(?:t)?)?(?:=(.*))?$")


def dashboard_show_problems(output: str, firewall: bool) -> list[str]:
    """Judge `systemctl show hermes-dashboard.service`: loaded from /etc, as hermes, no new
    privileges, loopback only both ways, only the firewall drop-in, and one --host 127.0.0.1."""
    props = {}
    for line in output.splitlines():
        key, _, value = line.partition("=")
        props[key.strip()] = value.strip()
    u = DASHBOARD_UNIT
    problems = []
    if props.get("FragmentPath") != f"/etc/systemd/system/{u}":
        problems.append(f"systemd loads {u} from {props.get('FragmentPath') or 'nowhere'}, not /etc/systemd/system/{u}")
    allowed = {f"/{DASHBOARD_DROPIN_DIRS[0]}/firewall.conf"} if firewall else set()
    for d in (x for x in props.get("DropInPaths", "").split() if x):
        if d not in allowed:
            problems.append(f"systemd applies the drop-in {d} to {u} (install_hermes.sh did not write it)")
    if props.get("User") != HERMES_USER:
        problems.append(f"{u} runs as {props.get('User') or 'root'}, not {HERMES_USER}")
    if props.get("NoNewPrivileges") != "yes":
        problems.append(f"{u} does not have NoNewPrivileges=yes")
    if set(props.get("IPAddressDeny", "").split()) != {"0.0.0.0/0", "::/0"}:
        problems.append(f"{u} does not refuse every address (IPAddressDeny=any)")
    if set(props.get("IPAddressAllow", "").split()) != {"127.0.0.0/8", "::1/128"}:
        problems.append(f"{u} allows more than loopback (IPAddressAllow=localhost)")
    m = re.search(r"argv\[\]=([^;]*)", props.get("ExecStart", ""))
    argv = m.group(1).split() if m else []
    hosts, port = [], None
    for i, a in enumerate(argv):
        hm = _HOST_ARG.match(a)
        if hm:
            hosts.append(hm.group(1) if hm.group(1) is not None else (argv[i + 1] if i + 1 < len(argv) else ""))
        if a == "--port" and i + 1 < len(argv):
            port = argv[i + 1]
        elif a.startswith("--port="):
            port = a.split("=", 1)[1]
    if hosts != ["127.0.0.1"]:
        problems.append(f"{u} does not listen on 127.0.0.1 only (its --host must be 127.0.0.1, once)")
    if port != DASHBOARD_PORT:
        problems.append(f"{u} does not use port {DASHBOARD_PORT} (the firewall's dashboard rules name that port)")
    return problems


def unit_show_problems(output: str, mode: str, firewall: bool) -> list[str]:
    """Judge `systemctl show hermes-gateway.service`: what systemd really loaded, wherever the unit
    file or a drop-in sits (also service.d/ and hermes-.service.d/ drop-ins, /run, /usr/lib)."""
    props = {}
    for line in output.splitlines():
        key, _, value = line.partition("=")
        props[key.strip()] = value.strip()
    problems = []
    if props.get("FragmentPath") != "/etc/systemd/system/hermes-gateway.service":
        problems.append(f"systemd loads hermes-gateway.service from {props.get('FragmentPath') or 'nowhere'}, "
                        "not /etc/systemd/system/hermes-gateway.service")
    allowed = set()
    if mode == "sudo":
        allowed.add(f"/{DROPIN_DIRS[0]}/sudo-bridge.conf")
    if firewall:
        allowed.add(f"/{DROPIN_DIRS[0]}/firewall.conf")
    dropins = [d for d in props.get("DropInPaths", "").split() if d]
    for d in dropins:
        if d not in allowed:
            problems.append(f"systemd applies the drop-in {d} to hermes-gateway.service (install_hermes.sh did not write it)")
    if props.get("User") != HERMES_USER:
        problems.append(f"hermes-gateway.service runs as {props.get('User') or 'root'}, not {HERMES_USER}")
    if mode == "socket" and props.get("NoNewPrivileges") != "yes":
        problems.append("hermes-gateway.service does not have NoNewPrivileges=yes")
    return problems


def check_sudoers(ctx: Ctx, mode: str) -> None:
    rel = "etc/sudoers.d/botsy-control"
    present = ctx.path(rel).exists() or ctx.path(rel).is_symlink()
    if mode == "sudo" and not present:
        ctx.add(f"/{rel} is missing (the sudo bridge needs it)")
    if present:
        st = locked_path(ctx, rel)
        check_mode(ctx, rel, st, 0o440)
        if st is not None and SUDOERS_SRC.exists():
            same_text(ctx, rel, SUDOERS_SRC.read_text(encoding="utf-8"), "deploy/lightsail/sudoers.d/botsy-control")
    if not ctx.system:
        return
    r = subprocess.run(["sudo", "-n", "-l", "-U", HERMES_USER], capture_output=True, text=True)
    for problem in sudo_list_problems(r.stdout or ""):
        ctx.add(problem)


ALLOWED_SUDO_COMMANDS = frozenset({'/usr/local/bin/botsy-control-read ""', '/usr/local/bin/botsy-control-act ""',
                                   "BOTSY_CONTROL"})


def sudo_list_problems(output: str) -> list[str]:
    """Judge `sudo -l -U hermes`: nothing, or only the botsy-control rule (run as botsy, no password)."""
    lines = output.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if "may run the following commands" in ln) + 1
    except StopIteration:
        return []                       # "User hermes is not allowed to run sudo on ..."
    specs: list[str] = []
    for ln in lines[start:]:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("(") or not specs:
            specs.append(s)
        else:
            specs[-1] += " " + s        # a wrapped long line
    problems = []
    for spec in specs:
        m = re.match(r"^\((?P<runas>[^)]*)\)\s*(?P<rest>.*)$", spec)
        if not m or m.group("runas").strip() != "botsy":
            problems.append(f"the hermes user may run sudo as someone other than botsy: {spec[:120]}")
            continue
        rest = m.group("rest")
        while True:
            t = re.match(r"^(NOPASSWD|NOSETENV|NOEXEC|NOLOG_INPUT|NOLOG_OUTPUT|NOMAIL|NOINTERCEPT):\s*", rest)
            if not t:
                break
            rest = rest[t.end():]
        if "NOPASSWD:" not in spec:
            problems.append("the botsy-control sudo rule asks for a password (Hermes cannot answer it)")
        commands = [c.strip() for c in rest.split(",") if c.strip()]
        bad = [c for c in commands if c not in ALLOWED_SUDO_COMMANDS]
        if bad or not commands:
            problems.append(f"the hermes user may run more with sudo than botsy-control: {', '.join(bad)[:120]}")
    return problems


def check_groups_and_botsy(ctx: Ctx) -> None:
    if ctx.hermes is None:
        return
    if ctx.system:
        names = set()
        for gid in ctx.hermes.gids:
            try:
                names.add(grp.getgrgid(gid).gr_name)
            except KeyError:
                pass
        bad = sorted(names & FORBIDDEN_GROUPS)
        if bad:
            ctx.add(f"the hermes user is in the group(s) {', '.join(bad)}")
        try:
            if pwd.getpwnam(HERMES_USER).pw_shell not in ("/usr/sbin/nologin", "/bin/false", "/sbin/nologin"):
                ctx.add("the hermes user has a login shell")
        except KeyError:
            pass
    for rel in BOTSY_PRIVATE:
        try:
            st = os.stat(ctx.path(rel))
        except FileNotFoundError:
            continue
        if _readable_dir_by(st, ctx.hermes):
            ctx.add(f"the hermes user can open /{rel} (Botsy's own files)")


def check_firewall(ctx: Ctx, firewall: bool) -> None:
    if not firewall:
        ctx.add("the firewall is off (/etc/hermes/install.conf). It is required since 2026-10-21: scheduled "
                "jobs, the task board and vision can fetch web addresses (run install_hermes.sh --firewall)")
        return
    if not ctx.system:
        return
    r = subprocess.run(["nft", "list", "table", "inet", "botsy_hermes"], capture_output=True, text=True)
    if r.returncode != 0 or 'skuid != "hermes"' not in r.stdout.replace("meta skuid", "skuid"):
        ctx.add("the firewall is on in /etc/hermes/install.conf, but table inet botsy_hermes is not loaded")


# ---------------------------------------------------------------------------- scheduled jobs

_DOW = {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}


def _plain_int(field: str, lo: int, hi: int) -> bool:
    return field.isdigit() and lo <= int(field) <= hi


def _single_day(field: str) -> bool:
    return _plain_int(field, 0, 7) or field.lower() in _DOW


def job_minutes_problem(schedule, sonnet: bool) -> str | None:
    """Why this schedule runs too often (None when it is fine). Haiku jobs: at most hourly. Sonnet
    jobs: at most once a week. Read from the stored schedule (cron/jobs.py parse_schedule)."""
    if not isinstance(schedule, dict):
        return "has no readable schedule"
    kind = schedule.get("kind")
    if kind == "once":
        return None
    if kind == "interval":
        minutes = schedule.get("minutes")
        floor = WEEK_MINUTES if sonnet else MIN_JOB_MINUTES
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < floor:
            return f"runs every {minutes} minutes (at least every {floor} minutes is the limit)"
        return None
    if kind == "cron":
        fields = str(schedule.get("expr") or "").split()
        if len(fields) != 5:
            return "has a cron schedule that is not 5 plain fields"
        minute, hour, dom, _month, dow = fields
        if not _plain_int(minute, 0, 59):
            return f"runs more often than hourly ({' '.join(fields)})"
        if sonnet and not (_plain_int(hour, 0, 23) and ((dom == "*" and _single_day(dow))
                                                         or (_plain_int(dom, 1, 31) and dow == "*"))):
            return f"runs more often than once a week on Sonnet ({' '.join(fields)})"
        return None
    return f"has an unknown schedule kind {kind!r}"


def job_problems(jobs) -> list[str]:
    """What is wrong with Hermes's scheduled jobs (~/.hermes/cron/jobs.json). Hermes may make its
    own check-ins, but v0.21.7 has no setting for how many, how often, or what they may run, so
    these limits live here (checked hourly by botsy-hermes-jobs.timer and before every start)."""
    p: list[str] = []
    if not isinstance(jobs, list):
        return ["the scheduled jobs file has no job list"]
    active = [j for j in jobs if isinstance(j, dict) and j.get("enabled", True) is not False]
    if len(active) > MAX_CRON_JOBS:
        p.append(f"{len(active)} scheduled jobs are on (at most {MAX_CRON_JOBS})")
    seen: dict[str, int] = {}
    for job in jobs:
        if not isinstance(job, dict):
            p.append("a scheduled job is not a record")
            continue
        name = str(job.get("name") or job.get("id") or "?")[:60]
        label = f"scheduled job {name!r}"
        for key, why in (("script", "runs a script"), ("monitor_script", "runs a monitor script"),
                         ("monitor_url", "fetches a web address"), ("base_url", "sends to another endpoint"),
                         ("workdir", "has a work folder")):
            if job.get(key):
                p.append(f"{label} {why} ({key})")
        if job.get("no_agent"):
            p.append(f"{label} is a script-only job (no_agent)")
        if job.get("provider") not in (None, "", "anthropic"):
            p.append(f"{label} uses the provider {str(job.get('provider'))[:40]!r}")
        model = job.get("model") or None
        if model is not None and model not in ALLOWED_MODELS:
            p.append(f"{label} uses the model {str(model)[:40]!r}")
        sonnet = model == SONNET_MODEL
        if sonnet:
            if name not in SONNET_JOBS:
                p.append(f"{label} runs on Sonnet (only {', '.join(SONNET_JOBS)} may)")
            seen[name] = seen.get(name, 0) + 1
        toolsets = job.get("enabled_toolsets")
        if toolsets is not None:
            if not isinstance(toolsets, list):
                p.append(f"{label} has an unreadable toolset list")
            else:
                extra = sorted(str(t) for t in toolsets if str(t) not in CRON_TOOLSETS)
                if extra:
                    p.append(f"{label} turns on {', '.join(extra)}")
        if job.get("enabled", True) is not False:
            why = job_minutes_problem(job.get("schedule"), sonnet)
            if why:
                p.append(f"{label} {why}")
    for name, n in sorted(seen.items()):
        if n > 1:
            p.append(f"{n} scheduled jobs named {name!r} run on Sonnet (one each)")
    return p


def check_jobs(ctx: Ctx) -> None:
    path = ctx.path(JOBS_FILE)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return                                   # no jobs yet
    except OSError:
        ctx.add(f"/{JOBS_FILE} cannot be read")
        return
    if not stat.S_ISREG(st.st_mode):
        ctx.add(f"/{JOBS_FILE} is not a plain file")
        return
    if st.st_size > MAX_JOBS_FILE_BYTES:
        ctx.add(f"/{JOBS_FILE} is larger than {MAX_JOBS_FILE_BYTES} bytes")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        ctx.add(f"/{JOBS_FILE} is not readable JSON")
        return
    jobs = data.get("jobs") if isinstance(data, dict) else None
    for problem in job_problems(jobs):
        ctx.add(problem)


def install_conf(ctx: Ctx) -> tuple[str, bool]:
    try:
        conf = read_kv(ctx.path("etc/hermes/install.conf"))
    except (OSError, UnicodeDecodeError):
        conf = {}
    mode = conf.get("BRIDGE", "socket")
    if mode not in MODES:
        ctx.add(f"/etc/hermes/install.conf names an unknown bridge {mode!r}")
        mode = "socket"
    return mode, conf.get("FIREWALL", "0") == "1"


# ---------------------------------------------------------------------------- the probe

def run_probe(ctx: Ctx, values: dict[str, str], mode: str, *, python: str | None = None,
              as_user: str | None = None, timeout: int = 180) -> dict | None:
    """Ask the installed Hermes itself (as the hermes user on the server) what it turns on."""
    python = python or str(ctx.path("opt/hermes/venv/bin/python"))
    code = PROBE % {"platforms": PLATFORMS, "risky": RISKY_SLASH_COMMANDS, "keys": [k for k, _ in MANAGED_ENV_FIXED] +
                    ["DISCORD_ALLOWED_USERS", "DISCORD_IGNORED_CHANNELS", "DISCORD_ALLOW_ALL_USERS"]}
    env = {"PATH": "/usr/bin:/bin", "HOME": str(ctx.path("home/hermes")), "LANG": "C.UTF-8",
           "HERMES_HOME": str(ctx.path("home/hermes/.hermes")), "HERMES_MANAGED_DIR": str(ctx.path("etc/hermes"))}
    cmd = [python, "-I", "-c", code]
    if as_user:
        cmd = ["runuser", "-u", as_user, "--"] + ["env", "-i"] + [f"{k}={v}" for k, v in env.items()] + cmd
    cwd = tempfile.mkdtemp(prefix="hermes-probe-")
    try:
        os.chmod(cwd, 0o755)
        r = subprocess.run(cmd, env=None if as_user else env, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        ctx.add(f"the Hermes probe could not run ({type(e).__name__})")
        return None
    finally:
        try:
            os.rmdir(cwd)
        except OSError:
            pass
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("BOTSY-PROBE ")), None)
    if r.returncode != 0 or line is None:
        tail = (r.stderr or "").strip().splitlines()[-1:] or ["no output"]
        ctx.add(f"the Hermes probe failed (exit {r.returncode}): {tail[0][:200]}")
        return None
    data = json.loads(line[len("BOTSY-PROBE "):])
    judge_probe(ctx, data, values, mode)
    return data


def judge_probe(ctx: Ctx, data: dict, values: dict[str, str], mode: str) -> None:
    pin = load_pin()
    for platform in PLATFORMS:
        got = (data.get("platforms") or {}).get(platform) or {}
        extra = sorted(set(got.get("toolsets") or []) - PLATFORM_TOOLSETS[platform])
        if extra:
            ctx.add(f"Hermes gives {platform} the toolsets {', '.join(extra)}")
        tools = sorted(set(got.get("tools") or []) - PLATFORM_PROBE_TOOLS[platform])
        if tools:
            ctx.add(f"Hermes gives {platform} the built-in tools {', '.join(tools)}")
        if not set(MCP_NAMES) <= set(got.get("toolsets") or []):
            ctx.add(f"Hermes does not give {platform} Botsy's two tool servers")
    mcp = data.get("mcp") or {}
    if sorted(mcp) != sorted(MCP_NAMES):
        ctx.add(f"Hermes sees the MCP servers {', '.join(sorted(mcp)) or 'none'} (only Botsy's two are allowed)")
    for name in MCP_NAMES:
        server = mcp.get(name) or {}
        command, args = MCP_COMMANDS[mode][name]
        if server.get("command") != command or server.get("args") != args or server.get("url"):
            ctx.add(f"Hermes starts {name} differently from the {mode} bridge")
        if server.get("sampling") is not False or server.get("elicitation") is not False:
            ctx.add(f"Hermes has sampling or elicitation on for {name}")
        if server.get("env") not in ({}, None):
            ctx.add(f"Hermes passes environment variables to {name}")
        if server.get("trust") != MCP_TRUST[name]:
            ctx.add(f"Hermes treats {name} as trust {server.get('trust')!r}, not {MCP_TRUST[name]!r}"
                    + (" (Botsy actions would run without your approval click)" if name == "botsy-act" else ""))
    if data.get("memory_write_approval") is not True or data.get("skills_write_approval") is not True:
        ctx.add("Hermes saves memory or skills without your approval")
    if data.get("memory_provider"):
        ctx.add("Hermes uses an outside memory service")
    if data.get("agent_scheduling") is not False:
        ctx.add("Hermes lets a running scheduled job make more jobs")
    if data.get("provider") != "anthropic" or data.get("model") != DEFAULT_MODEL:
        ctx.add("Hermes's model is not the pinned Anthropic model")
    cron = data.get("cron") if isinstance(data.get("cron"), dict) else {}
    if cron.get("provider") not in ("", None) or cron.get("model") != DEFAULT_MODEL \
            or cron.get("model_provider") != "anthropic":
        ctx.add(f"Hermes's scheduled jobs are not fixed to {DEFAULT_MODEL} on anthropic")
    kanban = data.get("kanban") if isinstance(data.get("kanban"), dict) else {}
    if kanban.get("dispatch_in_gateway") is not False or kanban.get("dispatch_profiles") != [] \
            or kanban.get("auto_decompose") is not False:
        ctx.add("Hermes's task board may start worker agents or extra model calls")
    background = data.get("background") if isinstance(data.get("background"), dict) else {}
    if background.get("review") is not False or background.get("curator") is not False:
        ctx.add("Hermes's background review or curator is on")
    blocked = data.get("web_blocked")
    if not isinstance(blocked, dict) or not blocked or not all(v is True for v in blocked.values()):
        ctx.add("Hermes's website blocklist does not block every web address (vision could download)")
    actions = data.get("discord_actions")
    if not isinstance(actions, list) or not actions or not set(actions) <= DISCORD_ACTIONS:
        ctx.add(f"Hermes's Discord tool allows actions beyond {sorted(DISCORD_ACTIONS)}")
    if data.get("discord_tool_progress") != "all":
        ctx.add("Hermes does not show every tool call in Discord (a skill change could go unseen)")
    allow = data.get("allow_from")
    if allow != [values.get("DISCORD_ALLOWED_USER_ID")]:
        ctx.add("Hermes's Discord allowlist is not exactly your user ID")
    env = data.get("env")
    if not isinstance(env, dict):
        ctx.add("Hermes's environment could not be read in the probe")
    else:
        want = expected_managed_env(values)
        want["DISCORD_ALLOW_ALL_USERS"] = "false"
        wrong = sorted(k for k, v in want.items() if env.get(k) != v)
        if wrong:
            ctx.add(f"Hermes's environment does not hold the managed values for {', '.join(wrong)}")
    slash = data.get("slash")
    if not isinstance(slash, dict) or not slash:
        ctx.add("Hermes's slash-command gate could not be read in the probe")
    else:
        for scope, got in sorted(slash.items()):
            got = got if isinstance(got, dict) else {}
            if got.get("gated") is not True:
                ctx.add(f"Hermes's slash commands are not gated in Discord ({scope} chats)")
            if got.get("open"):
                ctx.add(f"Hermes lets you run /{', /'.join(got['open'])} in Discord ({scope} chats)")
    if data.get("version") != pin.get("HERMES_VERSION") or data.get("commit") != pin.get("HERMES_COMMIT"):
        ctx.add(f"Hermes reports version {data.get('version')}, not the pinned {pin.get('HERMES_VERSION')}")


# ---------------------------------------------------------------------------- entry points

def check(ctx: Ctx, *, quick: bool = False, probe: bool = False, probe_python: str | None = None) -> list[str]:
    if not PIN_FILE.exists() or not SHA.match(load_pin().get("HERMES_COMMIT", "")):
        ctx.add(f"{PIN_FILE} is missing or has no commit")
        return ctx.problems
    if ctx.hermes is None:
        ctx.add("there is no hermes user")
    mode, firewall = install_conf(ctx)
    values = check_secrets(ctx)
    check_configs(ctx, values, mode)
    check_managed_env(ctx, values)
    check_home(ctx)
    check_code(ctx, quick)
    check_bridge_and_units(ctx, mode, firewall)
    check_sudoers(ctx, mode)
    check_groups_and_botsy(ctx)
    check_firewall(ctx, firewall)
    check_jobs(ctx)
    if probe and not ctx.problems:
        run_probe(ctx, values, mode, python=probe_python, as_user=HERMES_USER if ctx.system else None)
    elif probe:
        ctx.add("the Hermes probe was skipped because of the problems above")
    return ctx.problems


def _cmd_check(args) -> int:
    root = Path(args.root)
    system = root == Path("/")
    if system and os.geteuid() != 0:
        print("check_lock.py check must run as root (sudo)", file=sys.stderr)
        return 2
    ctx = Ctx(root=root, hermes=hermes_identity(), admin_uid=0, system=system)
    try:
        problems = check(ctx, quick=args.quick, probe=args.probe)
    except Exception as e:  # noqa: BLE001 - fail closed: a check that crashes is a broken lock
        problems = ctx.problems + [f"the lock check itself failed ({type(e).__name__}), so the lock is not proven"]
    pin = load_pin()
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if problems:
        print(f"Hermes lock: {len(problems)} problem(s). Hermes must not run like this.")
        if args.stop and system:
            subprocess.run(["systemctl", "stop", args.stop], check=False)
            print(f"Stopped {args.stop}.")
        return 1
    mode, firewall = install_conf(ctx)
    print(f"Hermes lock: OK (v{pin.get('HERMES_VERSION')}, {mode} bridge, firewall {'on' if firewall else 'off'}"
          f"{', probe passed' if args.probe else ''})")
    return 0


def _cmd_jobs(args) -> int:
    """Only the scheduled-jobs limits (hourly, botsy-hermes-jobs.timer): cheap, no probe."""
    root = Path(args.root)
    system = root == Path("/")
    if system and os.geteuid() != 0:
        print("check_lock.py jobs must run as root (sudo)", file=sys.stderr)
        return 2
    ctx = Ctx(root=root, hermes=hermes_identity(), admin_uid=0, system=system)
    try:
        check_jobs(ctx)
        problems = ctx.problems
    except Exception as e:  # noqa: BLE001 - fail closed
        problems = ctx.problems + [f"the job check itself failed ({type(e).__name__})"]
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if problems:
        print(f"Hermes jobs: {len(problems)} problem(s). Remove or fix the job: sudo -u hermes ... hermes cron list "
              f"(deploy/hermes/README.md \"Scheduled jobs\").")
        if args.stop and system:
            subprocess.run(["systemctl", "stop", args.stop], check=False)
            print(f"Stopped {args.stop}.")
        return 1
    print("Hermes jobs: OK")
    return 0


def _values_from(path: str) -> dict[str, str]:
    return read_kv(Path(path))


def _write(text: str, out: str | None) -> None:
    if not out:
        sys.stdout.write(text)
        return
    tmp = Path(out + ".new")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out)


def _cmd_render(args) -> int:
    try:
        text = render(TEMPLATE.read_text(encoding="utf-8"), _values_from(args.env), args.mode)
    except (OSError, ValueError) as e:
        print(f"render: {e}", file=sys.stderr)
        return 1
    _write(text, args.out)
    return 0


def _cmd_render_env(args) -> int:
    try:
        text = render_managed_env(_values_from(args.env))
    except (OSError, ValueError) as e:
        print(f"render-env: {e}", file=sys.stderr)
        return 1
    _write(text, args.out)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="check_lock.py", description="Check Botsy's lock on Hermes Agent.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--quick", action="store_true")
    c.add_argument("--probe", action="store_true")
    c.add_argument("--stop", metavar="UNIT")
    c.add_argument("--root", default="/", help=argparse.SUPPRESS)
    j = sub.add_parser("jobs")
    j.add_argument("--stop", metavar="UNIT")
    j.add_argument("--root", default="/", help=argparse.SUPPRESS)
    r = sub.add_parser("render")
    r.add_argument("--env", required=True)
    r.add_argument("--mode", choices=MODES, default="socket")
    r.add_argument("--out")
    e = sub.add_parser("render-env")
    e.add_argument("--env", required=True)
    e.add_argument("--out")
    sub.add_parser("pin")
    try:
        args = parser.parse_args(argv)
    except SystemExit as ex:
        return 2 if ex.code else 0
    if args.cmd == "check":
        return _cmd_check(args)
    if args.cmd == "jobs":
        return _cmd_jobs(args)
    if args.cmd == "render":
        return _cmd_render(args)
    if args.cmd == "render-env":
        return _cmd_render_env(args)
    pin = load_pin()
    print(f"v{pin.get('HERMES_VERSION')} {pin.get('HERMES_COMMIT')} {pin.get('HERMES_PUBLISHED')} {pin.get('HERMES_RELEASE_URL')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
