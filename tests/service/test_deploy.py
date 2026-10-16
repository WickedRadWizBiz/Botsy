"""deploy/lightsail: static checks that keep the server kit's safety properties.

The full setup was tested by hand on Ubuntu's real OpenSSH and systemd in a private mount
namespace; these tests only guard the rules that are easy to break in a later edit:
- root installs files only from its own copy (never from /opt/botsy, which the botsy
  account owns) and never follows a link inside /opt/botsy;
- the SFTP account stays locked down;
- every service keeps its hardening and the paths from docs/SERVER.md section 6.
The `--upload-pubkey` fast path is also run for real, with shims that keep it in tmp_path.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from core.config import ROOT

DEPLOY = ROOT / "deploy" / "lightsail"
SETUP = DEPLOY / "setup.sh"
SERVICES = ("botsy-tick.service", "botsy-daily.service", "botsy-weekly.service")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _unit(name: str) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for line in _text(DEPLOY / "systemd" / name).splitlines():
        if "=" in line and not line.lstrip().startswith(("#", "[")):
            k, v = line.split("=", 1)
            keys.setdefault(k.strip(), []).append(v.strip())
    return keys


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_setup_script_parses():
    subprocess.run(["bash", "-n", str(SETUP)], check=True)


def test_root_installs_only_from_its_own_copy():
    """`install ... -o root` copies a file into a root-owned place (systemd, sshd, bin):
    its source must be root's copy ($SRC_DIR), a temp file of the script, or /dev/null."""
    text = _text(SETUP)
    assert 'TRUSTED=/usr/local/lib/botsy/src' in text and 'SRC_DIR=$TRUSTED/deploy/lightsail' in text
    for line in text.splitlines():
        m = re.match(r'\s*install -m \S+ -o root -g root (\S+) ', line)
        if m:
            assert m.group(1).startswith(('"$SRC_DIR/', '"$tmp"', "/dev/null")), line
    assert "$APP_DIR/deploy" not in text and "dirname \"$0\"" not in text


def test_root_never_follows_links_in_the_app_folder():
    text = _text(SETUP)
    # root only changes the owner of .env with -h; mode, copy and file edits run as botsy
    assert 'chown -h "$APP_USER:$APP_USER" "$env_file"' in text
    assert 'as_app chmod 600 "$env_file"' in text
    assert 'as_app install -m 600 "$APP_DIR/.env.example" "$env_file"' in text
    assert 'as_app tee -a "$exclude"' in text
    for line in text.splitlines():
        if re.search(r"\b(chmod|chown|cp|install|tee|printf .*>>)\b.*\$APP_DIR/", line) and "as_app" not in line:
            assert line.strip().startswith(("install -d -m 750 -o", "#", "[[")), line


def test_setup_restarts_from_root_copy_before_doing_anything():
    text = _text(SETUP)
    main = text[text.index("main() {"):]
    assert main.index("update_trusted_copy") < main.index("install_packages")
    assert 'BOTSY_SETUP_STAGE=run exec bash "$SRC_DIR/setup.sh"' in main
    assert text.rstrip().endswith('main "$@"; exit')      # the whole file is read before it runs


def test_sftp_account_is_locked_down():
    conf = _text(DEPLOY / "sshd_botsy_upload.conf")
    lines = [ln.strip() for ln in conf.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines[0] == "Match User botsy-upload"
    assert "ChrootDirectory /srv/botsy-inbox" in lines
    force = next(ln for ln in lines if ln.startswith("ForceCommand"))
    assert force.startswith("ForceCommand internal-sftp -u 0027 -P ")
    denied = set(force.split("-P ", 1)[1].split()[0].split(","))
    assert {"symlink", "hardlink", "mkdir", "setstat", "fsetstat", "lsetstat"} <= denied
    assert not denied & {"open", "write", "close", "rename", "posix-rename", "read", "opendir", "readdir"}
    for rule in ("PasswordAuthentication no", "AllowTcpForwarding no", "X11Forwarding no",
                 "PermitTTY no", "DisableForwarding yes", "AuthenticationMethods publickey"):
        assert rule in lines


@pytest.mark.parametrize("name", SERVICES)
def test_services_keep_their_hardening(name):
    keys = _unit(name)
    assert keys["User"] == ["botsy"] and keys["WorkingDirectory"] == ["/opt/botsy"]
    assert keys["ExecStart"][0].startswith("/opt/botsy/.venv/bin/python -m service ")
    for k, v in {"NoNewPrivileges": "yes", "ProtectSystem": "full", "PrivateTmp": "yes",
                 "ProtectHome": "yes", "PrivateDevices": "yes", "RestrictSUIDSGID": "yes"}.items():
        assert keys.get(k) == [v], k
    assert keys.get("CapabilityBoundingSet") == [""]
    rw = keys["ReadWritePaths"][0].split()
    assert "/opt/botsy" in rw and "-/srv/botsy-inbox/outbox" in rw


def test_timer_runs_retry_only_a_lock_timeout():
    for name in ("botsy-daily.service", "botsy-weekly.service"):
        keys = _unit(name)
        assert keys["Restart"] == ["on-failure"]
        assert set(keys["RestartPreventExitStatus"][0].split()) >= {"1", "2"}   # 3 = lock timeout
    assert "Restart" not in _unit("botsy-tick.service")   # the path unit starts it again itself
    assert _unit("botsy-daily.service")["ExecStart"] == [
        "/opt/botsy/.venv/bin/python -m service tick --force --own-stats"]
    assert _unit("botsy-weekly.service")["ExecStart"] == ["/opt/botsy/.venv/bin/python -m service searchlist"]


# ---------------------------------------------------------------- --upload-pubkey fast path

# setup.sh without its last line (`main "$@"; exit`), with shims so it runs as a normal
# user in tmp_path: id -u says root, install drops -o/-g, chown does nothing, and the
# full setup is replaced by a marker so a test can see it was (not) reached.
HARNESS = r'''
source "$SCRIPT"
KEYS_DIR=$T/keys; KEYS_FILE=$KEYS_DIR/authorized_keys
SSHD_DROPIN=$T/botsy-upload.conf; UNIT_DIR=$T/units; CONF_FILE=$T/setup.conf
UPLOAD_USER=$(command id -un)
id() { if [[ $# -eq 1 && $1 == -u ]]; then echo 0; else command id "$@"; fi; }
install() {
  local args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac
  done
  command install "${args[@]}"
}
chown() { :; }
update_trusted_copy() { echo "FULL SETUP"; exit 9; }
main "$@"
'''


def _ssh_key(tmp_path: Path) -> str:
    key = tmp_path / "pc_key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "botsy-upload",
                    "-f", str(key)], check=True)
    return key.with_suffix(".pub").read_text(encoding="utf-8").strip()


def _setup(tmp_path: Path, *args: str, installed: bool = True):
    script = tmp_path / "setup-defs.sh"
    lines = _text(SETUP).rstrip().splitlines()
    assert lines[-1] == 'main "$@"; exit'
    script.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    (tmp_path / "setup.conf").write_text("REPO_URL=https://github.com/example/botsy.git\nBRANCH=main\n",
                                         encoding="utf-8")
    if installed:   # what a finished setup leaves behind
        (tmp_path / "keys").mkdir(exist_ok=True)
        (tmp_path / "units").mkdir(exist_ok=True)
        (tmp_path / "units" / "botsy-inbox.path").write_text("", encoding="utf-8")
        (tmp_path / "botsy-upload.conf").write_text("", encoding="utf-8")
    env = {"PATH": os.environ["PATH"], "SCRIPT": str(script), "T": str(tmp_path),
           "TMPDIR": str(tmp_path), "HOME": str(tmp_path)}
    return subprocess.run(["bash", "-c", HARNESS, "setup", *args], env=env, text=True,
                          capture_output=True, timeout=60)


needs_tools = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("ssh-keygen")),
                                 reason="needs bash and ssh-keygen")


@needs_tools
def test_upload_key_alone_is_added_without_a_full_setup(tmp_path):
    key = _ssh_key(tmp_path)
    first = _setup(tmp_path, "--upload-pubkey", key)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "Upload key added" in first.stdout and "FULL SETUP" not in first.stdout
    again = _setup(tmp_path, "--upload-pubkey", key)
    assert again.returncode == 0 and "That upload key was already allowed" in again.stdout
    keys = (tmp_path / "keys" / "authorized_keys").read_text(encoding="utf-8").splitlines()
    assert keys == [f"restrict {key}"]
    assert stat.S_IMODE((tmp_path / "keys" / "authorized_keys").stat().st_mode) == 0o644


@needs_tools
@pytest.mark.parametrize("extra, installed", [((), False), (("--branch", "main"), True)])
def test_upload_key_runs_the_full_setup_when_needed(tmp_path, extra, installed):
    """A server that is not set up yet, or a run that names a branch, gets the full setup."""
    res = _setup(tmp_path, "--upload-pubkey", _ssh_key(tmp_path), *extra, installed=installed)
    assert res.returncode == 9 and "FULL SETUP" in res.stdout
    assert not (tmp_path / "keys" / "authorized_keys").exists()


@needs_tools
def test_a_wrong_upload_key_is_refused(tmp_path):
    res = _setup(tmp_path, "--upload-pubkey", "ssh-ed25519 not-a-key")
    assert res.returncode == 1 and "is not a public SSH key" in res.stderr
    assert not (tmp_path / "keys" / "authorized_keys").exists()
