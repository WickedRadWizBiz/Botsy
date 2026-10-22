#!/usr/bin/env bash
# Locked-down Hermes Agent for Botsy's Lightsail server (Ubuntu 24.04). docs/OVERSEER.md
# "Lock-down: three layers" and "Hermes setup"; step by step in deploy/hermes/README.md.
#
# Run it as root, from root's own copy of the code (sudo botsy-setup keeps it up to date):
#   sudo bash /usr/local/lib/botsy/src/deploy/hermes/install_hermes.sh            (install or update)
#   sudo bash .../install_hermes.sh --firewall       (the default: limit the hermes user to Anthropic + Discord)
#   sudo bash .../install_hermes.sh --bridge sudo    (fallback: Hermes starts botsy-control with sudo)
#   sudo bash .../install_hermes.sh --bridge socket  (default: systemd starts it; no sudo for hermes)
#   sudo bash .../install_hermes.sh --check          (only run the lock check, with the Hermes probe)
#   sudo bash .../install_hermes.sh --no-dashboard   (leave the dashboard with the Botsy tab off)
# Options are remembered in /etc/hermes/install.conf. Safe to run again: it adds what is missing,
# rebuilds Hermes only when the pin in hermes-pin.env changes, and keeps /etc/hermes/hermes.env.
# It never prints a secret. Without a filled-in /etc/hermes/hermes.env it installs everything but
# does not start Hermes.
#
# What it does: a "hermes" system user (no shell, no sudo except the botsy-control rule in sudo
# mode, not in Botsy's groups); the pinned Hermes release (git tag, commit checked) in
# /opt/hermes/src with Hermes's own pinned uv and Python 3.14 (sha256 checked) and its locked
# dependencies (uv sync --frozen, hashes checked) in /opt/hermes/venv, all root-owned; the config
# rendered root-owned into /etc/hermes/config.yaml (Hermes's managed scope, which wins over
# everything) and /home/hermes/.hermes/config.yaml (read-only to hermes); root-owned SOUL.md,
# .env, hooks/ and plugins/ in a sticky home, so Hermes can add its own files but never replace
# these; a hardened systemd unit; Botsy's two tool sockets; a daily lock check and an hourly check
# of Hermes's own scheduled jobs; and an nftables table that lets only the hermes user's traffic to
# Anthropic and Discord out (required since 2026-10-21: Hermes's scheduled jobs, task board and
# vision can fetch web addresses, and only this table stops that); and the Hermes dashboard on
# 127.0.0.1:9119 with the Botsy tab (docs/OVERSEER.md "Botsy dashboard tab"; its web UI is built
# once here from Hermes's pinned lock file with a pinned, sha256-checked Node.js, as the
# unprivileged user nobody, never as hermes). No browser, computer-use or other optional tools are
# installed.
set -euo pipefail
umask 022

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
LIGHTSAIL=$(cd "$HERE/../lightsail" 2> /dev/null && pwd -P || true)

ROOT=""            # tests only (--root DIR --test-owner): lay out files under DIR, change nothing else
TEST_OWNER=0
SKIP_FETCH=0
CHECK_ONLY=0
BRIDGE=""
FIREWALL=""
DASHBOARD=""

say()  { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,/^# installed\.$/p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case $1 in
    --bridge)      [[ $# -ge 2 ]] || die "--bridge needs socket or sudo"; BRIDGE=$2; shift 2 ;;
    --firewall)    FIREWALL=1; shift ;;
    --no-firewall) die "the firewall is required since 2026-10-21 (Hermes's scheduled jobs, task board and vision can fetch web addresses); see deploy/hermes/README.md" ;;
    --check)       CHECK_ONLY=1; shift ;;
    --no-dashboard) DASHBOARD=0; shift ;;
    --dashboard)   DASHBOARD=1; shift ;;
    --skip-fetch)  SKIP_FETCH=1; shift ;;
    --root)        [[ $# -ge 2 ]] || die "--root needs a folder"; ROOT=${2%/}; shift 2 ;;
    --test-owner)  TEST_OWNER=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "unknown option '$1' (see --help)" ;;
  esac
done

R=$ROOT                               # every path below is "$R/..."
SYSTEM=1
if [[ -n $ROOT ]]; then
  [[ $TEST_OWNER == 1 ]] || die "--root is for tests only and needs --test-owner"
  SYSTEM=0; SKIP_FETCH=1
fi
if [[ $TEST_OWNER == 1 ]]; then
  [[ -n $ROOT ]] || die "--test-owner needs --root"
  OWNER=$(id -un); ROOTGROUP=$(id -gn); HGROUP=$(id -gn)
else
  OWNER=root; ROOTGROUP=root; HGROUP=hermes
fi

HUSER=hermes
ETC=$R/etc/hermes
SECRETS=$ETC/hermes.env
CONF=$ETC/install.conf
HOME_DIR=$R/home/hermes
HHOME=$HOME_DIR/.hermes
OPT=$R/opt/hermes
SRC=$OPT/src
VENV=$OPT/venv
LIB=$R/usr/local/lib/botsy-hermes
BIN=$R/usr/local/bin
UNITS=$R/etc/systemd/system
DROPIN=$UNITS/hermes-gateway.service.d
DASH_DROPIN=$UNITS/hermes-dashboard.service.d
PLUGIN=$HHOME/plugins/botsy/dashboard
WEB_DIST=$OPT/web_dist
SUDOERS=$R/etc/sudoers.d/botsy-control
WORK=$(mktemp -d)
trap 'rm -rf -- "$WORK"' EXIT

# ---------------------------------------------------------------- 0. preflight

[[ $SYSTEM == 0 || $(id -u) == 0 ]] || die "run it as root: sudo bash $0"

# The pin: KEY=value lines, read as data (never sourced).
pin() { sed -n "s/^$1=\\([^[:space:]]*\\)\$/\\1/p" "$HERE/hermes-pin.env" | head -n 1; }
H_VERSION=$(pin HERMES_VERSION); H_TAG=$(pin HERMES_TAG); H_COMMIT=$(pin HERMES_COMMIT)
H_REPO=$(pin HERMES_REPO); H_EXTRAS=$(pin HERMES_EXTRAS)
NODE_VERSION=$(pin NODE_VERSION)
[[ $H_COMMIT =~ ^[0-9a-f]{40}$ && -n $H_TAG && -n $H_VERSION && $H_REPO == https://github.com/* ]] \
  || die "$HERE/hermes-pin.env is incomplete"

if [[ $SYSTEM == 1 ]]; then
  # Root installs only from root's own copy: no folder above this script may be writable by
  # anyone but root (the same rule as deploy/lightsail/setup.sh).
  d=$HERE
  while :; do
    [[ $(stat -c %u "$d") == 0 ]] || die "$d is not owned by root. Run root's own copy: sudo bash /usr/local/lib/botsy/src/deploy/hermes/install_hermes.sh"
    perm=$(stat -c %a "$d")
    (( (8#$perm & 8#022) == 0 )) || die "$d is writable by group or others; run root's own copy in /usr/local/lib/botsy/src"
    [[ $d == / ]] && break
    d=$(dirname "$d")
  done
fi

# Remembered choices (command-line options win).
if [[ -f $CONF ]]; then
  old_bridge=$(sed -n 's/^BRIDGE=\(socket\|sudo\)$/\1/p' "$CONF" | head -n 1)
  old_fw=$(sed -n 's/^FIREWALL=\([01]\)$/\1/p' "$CONF" | head -n 1)
  old_dash=$(sed -n 's/^DASHBOARD=\([01]\)$/\1/p' "$CONF" | head -n 1)
  [[ -n $BRIDGE ]] || BRIDGE=${old_bridge:-}
  [[ -n $FIREWALL ]] || FIREWALL=${old_fw:-}
  [[ -n $DASHBOARD ]] || DASHBOARD=${old_dash:-}
fi
BRIDGE=${BRIDGE:-socket}
if [[ ${FIREWALL:-} == 0 ]]; then
  note "The firewall was off in $CONF; it is required now (2026-10-21), so it is turned on."
fi
FIREWALL=1
DASHBOARD=${DASHBOARD:-1}
[[ $BRIDGE == socket || $BRIDGE == sudo ]] || die "--bridge must be socket or sudo"

run_check() {   # $@: extra check_lock.py options
  if [[ $SYSTEM == 1 ]]; then
    python3 -I "$LIB/check_lock.py" check "$@"
  else
    return 0
  fi
}

if [[ $CHECK_ONLY == 1 ]]; then
  [[ -f $LIB/check_lock.py ]] || die "Hermes is not installed yet ($LIB/check_lock.py is missing)"
  run_check --probe
  exit $?
fi

# ---------------------------------------------------------------- 1. packages

install_packages() {
  [[ $SYSTEM == 1 ]] || return 0
  local pkgs=(git curl ca-certificates python3 python3-yaml sudo tar xz-utils util-linux)
  [[ $FIREWALL == 1 ]] && pkgs+=(nftables)
  say "Installing system packages (${pkgs[*]})"
  DEBIAN_FRONTEND=noninteractive apt-get -q -o DPkg::Lock::Timeout=900 install -y "${pkgs[@]}" > /dev/null \
    || die "apt-get install failed; run 'sudo apt-get update' and try again"
  note "Done."
}

# ---------------------------------------------------------------- 2. the hermes account

create_account() {
  [[ $SYSTEM == 1 ]] || return 0
  say "Creating the system user '$HUSER' (no shell, no password, no sudo)"
  getent group "$HUSER" > /dev/null || groupadd --system "$HUSER"
  if ! id -u "$HUSER" > /dev/null 2>&1; then
    useradd --system --gid "$HUSER" --home-dir /home/hermes --no-create-home \
            --shell /usr/sbin/nologin --comment "Hermes Agent (Botsy helper)" "$HUSER"
  fi
  usermod --gid "$HUSER" --shell /usr/sbin/nologin --home /home/hermes --password '*' "$HUSER"
  local g
  for g in sudo admin wheel adm botsy botsy-upload docker lxd disk systemd-journal shadow; do
    if getent group "$g" > /dev/null && id -nG "$HUSER" | tr ' ' '\n' | grep -qx "$g"; then
      gpasswd -d "$HUSER" "$g" > /dev/null && note "Removed $HUSER from the group $g."
    fi
  done
  id -u botsy > /dev/null 2>&1 || note "Warning: there is no botsy user yet. Run deploy/lightsail/setup.sh first."
  note "Done."
}

# ---------------------------------------------------------------- 3. folders and the secrets file

backup_dir=""
move_aside() {   # $1: path that must not stay as it is (never deleted: kept for you under /root)
  [[ -e $1 || -L $1 ]] || return 0
  if [[ -z $backup_dir ]]; then
    backup_dir=$R/root/hermes-backup-$(date +%Y%m%d-%H%M%S)
    install -d -m 700 -o "$OWNER" -g "$ROOTGROUP" "$backup_dir"
  fi
  mv -f -- "$1" "$backup_dir/" && note "Moved $1 to $backup_dir/ (Hermes's own copy is replaced by a locked one)."
}

DASH_FILES=(manifest.json plugin_api.py botsy_tab.py dist/index.js dist/style.css)
install_plugin() {   # $1: the folder that gets exactly the tab's five files (root-owned 755/644)
  local f
  install -d -m 755 -o "$OWNER" -g "$ROOTGROUP" "${1%/dashboard}" "$1" "$1/dist"
  for f in "${DASH_FILES[@]}"; do
    install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$HERE/dashboard/$f" "$1/$f"
  done
}

lay_out_folders() {
  say "Creating the folders (root-owned; the hermes home is sticky)"
  mkdir -p "$R/home" "$BIN" "$UNITS"          # system folders: created only if missing, never changed
  install -d -m 755 -o "$OWNER" -g "$ROOTGROUP" "$ETC" "$OPT" "$LIB" "$LIB/systemd"
  # /home/hermes and ~/.hermes: root:hermes 1770. Hermes may add its own files (sessions, memory,
  # logs), but the sticky bit stops it from renaming or deleting root's files in them.
  local d
  for d in "$HOME_DIR" "$HHOME"; do
    [[ -L $d ]] && die "$d is a symbolic link"
    install -d -m 1770 -o "$OWNER" -g "$HGROUP" "$d"
    chown "$OWNER:$HGROUP" "$d" && chmod 1770 "$d"
  done
  # hooks/ and plugins/: Hermes runs what it finds there at start; profiles/: its scheduled-job
  # ticker visits every profile in it, whatever this config says; scripts/: the only place a
  # scheduled job may run a script from. So they are root's and empty.
  # plugins/ may hold only our own copy of the Botsy dashboard tab (plugins/botsy, laid out again
  # below); anything else in it is moved aside.
  for d in "$HHOME/hooks" "$HHOME/plugins" "$HHOME/profiles" "$HHOME/scripts"; do
    if [[ -L $d ]] || [[ -e $d && ! -d $d ]]; then
      move_aside "$d"
    elif [[ -d $d && $(stat -c %U "$d") != "$OWNER" ]]; then
      move_aside "$d"
    elif [[ -d $d && $d == "$HHOME/plugins" ]]; then
      local entry
      for entry in "$d"/* "$d"/.[!.]* "$d"/..?*; do
        [[ -e $entry || -L $entry ]] || continue
        if [[ ${entry##*/} == botsy && -d $entry && ! -L $entry && $(stat -c %U "$entry") == "$OWNER" ]]; then
          rm -rf -- "$entry"          # our own copy (root's, in a root-only folder): laid out fresh
        else
          move_aside "$entry"
        fi
      done
    elif [[ -d $d && -n $(ls -A "$d") ]]; then
      move_aside "$d"
    fi
    install -d -m 755 -o "$OWNER" -g "$ROOTGROUP" "$d"
  done
  if [[ $DASHBOARD == 1 ]]; then
    install_plugin "$PLUGIN"
  fi
  if [[ -e $HHOME/.env && -s $HHOME/.env ]] || [[ -L $HHOME/.env ]]; then
    move_aside "$HHOME/.env"     # it would override the service environment
  fi
  install -m 644 -o "$OWNER" -g "$ROOTGROUP" /dev/null "$WORK/empty"
  install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$WORK/empty" "$HHOME/.env.new" && mv -f "$HHOME/.env.new" "$HHOME/.env"
  install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$HERE/SOUL.md" "$HHOME/SOUL.md.new" && mv -f "$HHOME/SOUL.md.new" "$HHOME/SOUL.md"
  # An empty working folder: Hermes reads no AGENTS.md or CLAUDE.md from it.
  install -d -m 755 -o "$OWNER" -g "$ROOTGROUP" "$OPT/work"
  if [[ ! -f $SECRETS ]]; then
    install -m 640 -o "$OWNER" -g "$HGROUP" "$HERE/hermes.env.example" "$SECRETS"
    note "Created $SECRETS from hermes.env.example. Fill it in (README step 5), then run this again."
  fi
  chown "$OWNER:$HGROUP" "$SECRETS" && chmod 640 "$SECRETS"
  printf 'BRIDGE=%s\nFIREWALL=%s\nDASHBOARD=%s\n' "$BRIDGE" "$FIREWALL" "$DASHBOARD" > "$WORK/install.conf"
  install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$WORK/install.conf" "$CONF"
  note "Done."
}

# ---------------------------------------------------------------- 4. Hermes itself (pinned)

fetch_and_build() {
  [[ $SKIP_FETCH == 0 ]] || return 0
  local head=""
  [[ -f $SRC/.git/HEAD ]] && head=$(tr -d '[:space:]' < "$SRC/.git/HEAD")
  if [[ $head == "$H_COMMIT" && -x $VENV/bin/hermes && $(cat "$VENV/.botsy-pin" 2> /dev/null) == "$H_COMMIT" ]]; then
    say "Hermes Agent v$H_VERSION is already installed (commit ${H_COMMIT:0:12})"
    return 0
  fi
  say "Installing Hermes Agent v$H_VERSION (tag $H_TAG, commit ${H_COMMIT:0:12})"
  if [[ $SYSTEM == 1 ]] && systemctl is-active --quiet hermes-gateway.service; then
    systemctl stop hermes-gateway.service
  fi
  if [[ $head != "$H_COMMIT" ]]; then
    rm -rf -- "$SRC.new"
    git -c core.hooksPath=/dev/null -c protocol.file.allow=never -c advice.detachedHead=false \
        clone --quiet --depth 1 --branch "$H_TAG" --single-branch "$H_REPO" "$SRC.new" \
      || die "could not download $H_REPO at $H_TAG"
    local got
    got=$(git -c safe.directory="$SRC.new" -C "$SRC.new" rev-parse HEAD)
    [[ $got == "$H_COMMIT" ]] || { rm -rf -- "$SRC.new"; die "tag $H_TAG is commit $got, not the pinned $H_COMMIT. Stopped."; }
    rm -rf -- "$SRC" && mv "$SRC.new" "$SRC"
  fi

  # Hermes's own pinned uv and Python (pm/lock.json in the pinned commit), sha256 checked.
  local arch target
  arch=$(uname -m)
  case $arch in
    x86_64)  target=linux-x64 ;;
    aarch64) target=linux-arm64 ;;
    *) die "unsupported CPU $arch" ;;
  esac
  local uv_line py_line
  uv_line=$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1]))["packages"][sys.argv[2]]; a=d["artifacts"][sys.argv[3]]; print(d["version"], a["url"], a["sha256"])' "$SRC/pm/lock.json" uv "$target")
  py_line=$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1]))["packages"][sys.argv[2]]; a=d["artifacts"][sys.argv[3]]; print(d["version"], a["url"], a["sha256"])' "$SRC/pm/lock.json" python "$target")
  local uv_ver uv_url uv_sha py_ver py_url py_sha
  read -r uv_ver uv_url uv_sha <<< "$uv_line"
  read -r py_ver py_url py_sha <<< "$py_line"
  [[ $uv_url == https://github.com/astral-sh/* && $py_url == https://github.com/astral-sh/* ]] \
    || die "pm/lock.json names a download outside github.com/astral-sh"
  fetch_tool() {   # $1 url  $2 sha256  $3 destination folder
    local name=${1##*/}
    if [[ -f $3/.sha256 && $(cat "$3/.sha256") == "$2" ]]; then return 0; fi
    curl -fsSL --proto '=https' --tlsv1.2 -o "$WORK/$name" "$1" || die "download failed: $1"
    [[ $(sha256sum "$WORK/$name" | cut -d' ' -f1) == "$2" ]] || die "sha256 mismatch for $name. Stopped."
    rm -rf -- "$3.new" && install -d -m 755 "$3.new"
    tar -xzf "$WORK/$name" -C "$3.new" --strip-components=1 --no-same-owner
    printf '%s\n' "$2" > "$3.new/.sha256"
    rm -rf -- "$3" && mv "$3.new" "$3"
  }
  install -d -m 755 "$OPT/tools" "$OPT/python"
  fetch_tool "$uv_url" "$uv_sha" "$OPT/tools/uv-$uv_ver"
  fetch_tool "$py_url" "$py_sha" "$OPT/python/$py_ver"
  local uv=$OPT/tools/uv-$uv_ver/uv py=$OPT/python/$py_ver/bin/python3
  [[ -x $uv && -x $py ]] || die "uv or Python is missing after the download"

  # The locked dependencies (uv.lock, hashes checked) and Hermes itself (editable, from the
  # root-owned checkout). Only the extras Botsy needs: no browser, voice or computer-use tools.
  rm -rf -- "$VENV"
  local extras=() e
  IFS=',' read -r -a parts <<< "$H_EXTRAS"
  for e in "${parts[@]}"; do extras+=(--extra "$e"); done
  ( cd "$SRC" && env -i PATH=/usr/bin:/bin HOME=/root LANG=C.UTF-8 \
      UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=never UV_CACHE_DIR="$OPT/cache" UV_PROJECT_ENVIRONMENT="$VENV" \
      UV_LINK_MODE=copy UV_NO_PROGRESS=1 \
      "$uv" sync --quiet --frozen --no-dev --compile-bytecode --python "$py" "${extras[@]}" ) \
    || die "installing Hermes's locked dependencies failed"
  # The install stamp says which release this is and that only this script updates it
  # (updateMechanism external), so `hermes --version` and the lock check agree.
  env -i PATH=/usr/bin:/bin HOME=/root "$VENV/bin/python" -I "$SRC/scripts/write_install_stamp.py" \
      -o "$SRC/install-stamp.json" --commit "$H_COMMIT" --base-version "$H_VERSION" --distance 0 \
      --source local --update-mechanism external > /dev/null || die "writing the install stamp failed"
  printf '%s\n' "$H_COMMIT" > "$VENV/.botsy-pin"
  rm -rf -- "$OPT/cache"
  chown -R root:root "$OPT"
  chmod -R u+rwX,go+rX,go-w "$OPT"
  note "Done: $("$VENV/bin/hermes" --version 2> /dev/null | head -n 1 || echo "v$H_VERSION")"
}

# ---------------------------------------------------------------- 4b. the dashboard web UI (pinned)

build_web_ui() {
  [[ $SKIP_FETCH == 0 && $DASHBOARD == 1 ]] || return 0
  local stamp="$H_COMMIT node-$NODE_VERSION"
  if [[ -f $WEB_DIST/index.html && $(cat "$WEB_DIST/.botsy-pin" 2> /dev/null) == "$stamp" ]]; then
    say "The dashboard web UI is already built for v$H_VERSION"
    return 0
  fi
  say "Building the dashboard web UI (Node.js $NODE_VERSION, npm ci from Hermes's pinned lock file)"
  local arch target sha
  arch=$(uname -m)
  case $arch in
    x86_64)  target=linux-x64;   sha=$(pin NODE_SHA256_LINUX_X64) ;;
    aarch64) target=linux-arm64; sha=$(pin NODE_SHA256_LINUX_ARM64) ;;
    *) die "unsupported CPU $arch" ;;
  esac
  [[ $NODE_VERSION =~ ^22\.[0-9]+\.[0-9]+$ && $sha =~ ^[0-9a-f]{64}$ ]] || die "$HERE/hermes-pin.env has no Node.js pin"
  local node=$OPT/tools/node-$NODE_VERSION name=node-v$NODE_VERSION-$target.tar.xz
  if [[ ! -x $node/bin/node || $(cat "$node/.sha256" 2> /dev/null) != "$sha" ]]; then
    curl -fsSL --proto '=https' --tlsv1.2 -o "$WORK/$name" "https://nodejs.org/dist/v$NODE_VERSION/$name" \
      || die "download failed: Node.js $NODE_VERSION"
    [[ $(sha256sum "$WORK/$name" | cut -d' ' -f1) == "$sha" ]] || die "sha256 mismatch for $name. Stopped."
    rm -rf -- "$node.new" && install -d -m 755 "$node.new"
    tar -xJf "$WORK/$name" -C "$node.new" --strip-components=1 --no-same-owner
    printf '%s\n' "$sha" > "$node.new/.sha256"
    rm -rf -- "$node" && mv "$node.new" "$node"
  fi
  # Build as nobody in a throwaway copy of the pinned checkout: npm ci checks every package's
  # integrity hash from package-lock.json and runs no install scripts (--ignore-scripts).
  local build
  build=$(mktemp -d /var/tmp/botsy-hermes-web.XXXXXX)
  git -c safe.directory="$SRC" -C "$SRC" archive --format=tar HEAD | tar -x -C "$build" --no-same-owner
  chown -R nobody:nogroup "$build"
  if ! ( cd "$build" && setpriv --reuid=nobody --regid=nogroup --clear-groups --no-new-privs \
           env -i PATH="$node/bin:/usr/bin:/bin" HOME="$build/.home" LANG=C.UTF-8 \
               npm_config_cache="$build/.npm" npm_config_update_notifier=false npm_config_fund=false \
               npm_config_audit=false HERMES_WEB_BUILD_LIGHT=1 \
           sh -c 'npm ci --workspace web --include-workspace-root=false --ignore-scripts --no-audit --no-fund > /dev/null &&
                  npm run build --workspace web > /dev/null' ); then
    rm -rf -- "$build"
    die "building the dashboard web UI failed (see the npm output above). Or run with --no-dashboard."
  fi
  [[ -f $build/hermes_cli/web_dist/index.html ]] || { rm -rf -- "$build"; die "the web UI build wrote no index.html"; }
  rm -rf -- "$WEB_DIST.new"
  cp -R --no-preserve=ownership,mode -- "$build/hermes_cli/web_dist" "$WEB_DIST.new"
  rm -rf -- "$build"
  find "$WEB_DIST.new" -type l -delete
  printf '%s\n' "$stamp" > "$WEB_DIST.new/.botsy-pin"
  chown -R root:root "$WEB_DIST.new"
  find "$WEB_DIST.new" -type d -exec chmod 755 {} + && find "$WEB_DIST.new" -type f -exec chmod 644 {} +
  rm -rf -- "$WEB_DIST" && mv "$WEB_DIST.new" "$WEB_DIST"
  note "Done: $WEB_DIST"
}

# ---------------------------------------------------------------- 5. Botsy's lock files

copy_lock_files() {
  say "Installing the lock files ($LIB) and Botsy's tool bridge"
  local f
  for f in check_lock.py config.yaml.template SOUL.md hermes-pin.env hermes.env.example \
           nftables-hermes.conf README.md; do
    install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$HERE/$f" "$LIB/$f"
  done
  install -m 755 -o "$OWNER" -g "$ROOTGROUP" "$HERE/botsy-hermes-resolve" "$LIB/botsy-hermes-resolve"
  install -m 755 -o "$OWNER" -g "$ROOTGROUP" "$HERE/botsy-control-connect" "$LIB/botsy-control-connect"
  for f in "$HERE"/systemd/*; do
    install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$f" "$LIB/systemd/${f##*/}"
  done
  # The dashboard tab's reference copy (check_lock.py compares the plugin folder and the unit with it).
  install_plugin "$LIB/dashboard"
  install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$HERE/dashboard/hermes-dashboard.service" "$LIB/dashboard/hermes-dashboard.service"
  # botsy-control's wrappers and sudo rule come from deploy/lightsail (docs/OVERSEER.md
  # "botsy-control reference"); copies sit next to check_lock.py so it can compare.
  [[ -n $LIGHTSAIL && -f $LIGHTSAIL/botsy-control-read ]] || die "deploy/lightsail/botsy-control-read is missing next to this script"
  for f in botsy-control-read botsy-control-act; do
    install -m 755 -o "$OWNER" -g "$ROOTGROUP" "$LIGHTSAIL/$f" "$LIB/$f"
    install -m 755 -o "$OWNER" -g "$ROOTGROUP" "$LIGHTSAIL/$f" "$BIN/$f"
  done
  install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$LIGHTSAIL/sudoers.d/botsy-control" "$LIB/sudoers.botsy-control"
  install -m 755 -o "$OWNER" -g "$ROOTGROUP" "$HERE/botsy-control-connect" "$BIN/botsy-control-connect"
  note "Done."
}

SECRETS_READY=0
render_configs() {
  say "Writing Hermes's locked config"
  if ! python3 -I "$LIB/check_lock.py" render --env "$SECRETS" --mode "$BRIDGE" --out "$WORK/config.yaml" 2> "$WORK/render.err"; then
    note "Not yet: $(cat "$WORK/render.err")"
    note "Fill in $SECRETS (README step 5), then run this script again."
    return 0
  fi
  python3 -I "$LIB/check_lock.py" render-env --env "$SECRETS" --out "$WORK/managed.env"
  install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$WORK/config.yaml" "$ETC/config.yaml.new" && mv -f "$ETC/config.yaml.new" "$ETC/config.yaml"
  install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$WORK/managed.env" "$ETC/.env.new" && mv -f "$ETC/.env.new" "$ETC/.env"
  install -m 640 -o "$OWNER" -g "$HGROUP" "$WORK/config.yaml" "$HHOME/config.yaml.new" && mv -f "$HHOME/config.yaml.new" "$HHOME/config.yaml"
  # Secrets: only the two keys are checked for being filled in here, never printed.
  if grep -qE '^ANTHROPIC_API_KEY=.+' "$SECRETS" && grep -qE '^DISCORD_BOT_TOKEN=.+' "$SECRETS"; then
    SECRETS_READY=1
  else
    note "ANTHROPIC_API_KEY or DISCORD_BOT_TOKEN in $SECRETS is still empty; Hermes will not start."
  fi
  note "Done: $ETC/config.yaml (managed scope) and $HHOME/config.yaml."
}

install_units() {
  say "Installing the systemd units ($BRIDGE bridge, firewall $([[ $FIREWALL == 1 ]] && echo on || echo off))"
  local u
  for u in hermes-gateway.service botsy-hermes-lock.service botsy-hermes-lock.timer \
           botsy-hermes-jobs.service botsy-hermes-jobs.timer; do
    install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$HERE/systemd/$u" "$UNITS/$u"
  done
  install -d -m 755 -o "$OWNER" -g "$ROOTGROUP" "$DROPIN"
  for u in "$DROPIN"/*; do       # only the drop-ins this script writes may exist
    [[ -e $u ]] || continue
    case ${u##*/} in sudo-bridge.conf|firewall.conf) ;; *) move_aside "$u" ;; esac
  done
  if [[ $BRIDGE == socket ]]; then
    for u in botsy-control-read.socket botsy-control-act.socket botsy-control-read@.service botsy-control-act@.service; do
      install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$HERE/systemd/$u" "$UNITS/$u"
    done
    rm -f -- "$DROPIN/sudo-bridge.conf"
    if [[ -f $SUDOERS ]]; then
      note "Note: $SUDOERS is not needed with the socket bridge. You may remove it: sudo rm $SUDOERS"
    fi
  else
    install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$HERE/systemd/sudo-bridge.conf" "$DROPIN/sudo-bridge.conf"
    if [[ $SYSTEM == 1 ]]; then
      visudo -cqf "$LIB/sudoers.botsy-control" || die "the botsy-control sudo rule does not pass visudo -c"
    fi
    install -d -m 755 -o "$OWNER" -g "$ROOTGROUP" "$R/etc/sudoers.d"
    install -m 440 -o "$OWNER" -g "$ROOTGROUP" "$LIB/sudoers.botsy-control" "$SUDOERS"
  fi
  if [[ $FIREWALL == 1 ]]; then
    for u in botsy-hermes-firewall.service botsy-hermes-resolve.service botsy-hermes-resolve.timer; do
      install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$HERE/systemd/$u" "$UNITS/$u"
    done
    install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$HERE/systemd/firewall.conf" "$DROPIN/firewall.conf"
  else
    rm -f -- "$DROPIN/firewall.conf"
  fi
  if [[ $DASHBOARD == 1 ]]; then
    install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$HERE/dashboard/hermes-dashboard.service" "$UNITS/hermes-dashboard.service"
    install -d -m 755 -o "$OWNER" -g "$ROOTGROUP" "$DASH_DROPIN"
    for u in "$DASH_DROPIN"/*; do
      [[ -e $u ]] || continue
      [[ ${u##*/} == firewall.conf ]] || move_aside "$u"
    done
    install -m 644 -o "$OWNER" -g "$ROOTGROUP" "$HERE/systemd/firewall.conf" "$DASH_DROPIN/firewall.conf"
  else
    if [[ $SYSTEM == 1 ]]; then
      systemctl disable --now hermes-dashboard.service > /dev/null 2>&1 || true
    fi
    rm -f -- "$UNITS/hermes-dashboard.service" "$DASH_DROPIN/firewall.conf"
  fi
  note "Done."
}

start_services() {
  [[ $SYSTEM == 1 ]] || return 0
  say "Starting the services"
  systemctl daemon-reload
  if [[ $BRIDGE == socket ]]; then
    systemctl enable --now botsy-control-read.socket botsy-control-act.socket > /dev/null
  else
    systemctl disable --now botsy-control-read.socket botsy-control-act.socket > /dev/null 2>&1 || true
    rm -f -- "$UNITS"/botsy-control-{read,act}.socket "$UNITS"/botsy-control-{read,act}@.service
    systemctl daemon-reload
  fi
  if [[ $FIREWALL == 1 ]]; then
    systemctl enable botsy-hermes-firewall.service botsy-hermes-resolve.timer > /dev/null
    systemctl restart botsy-hermes-firewall.service || die "the firewall rule did not load (see: journalctl -u botsy-hermes-firewall)"
    systemctl start botsy-hermes-resolve.timer
  elif [[ -f $UNITS/botsy-hermes-firewall.service ]]; then
    systemctl disable --now botsy-hermes-resolve.timer botsy-hermes-firewall.service > /dev/null 2>&1 || true
    rm -f -- "$UNITS"/botsy-hermes-firewall.service "$UNITS"/botsy-hermes-resolve.{service,timer}
    systemctl daemon-reload
  fi
  systemctl enable --now botsy-hermes-lock.timer botsy-hermes-jobs.timer > /dev/null
  if [[ $SECRETS_READY == 1 ]]; then
    systemctl enable hermes-gateway.service > /dev/null
  else
    systemctl disable --now hermes-gateway.service > /dev/null 2>&1 || true
  fi
  # The dashboard starts only after the lock check passes (final_check).
  [[ $DASHBOARD == 1 ]] && systemctl stop hermes-dashboard.service > /dev/null 2>&1 || true
  note "Done."
}

final_check() {
  [[ $SYSTEM == 1 ]] || return 0
  if [[ $SECRETS_READY == 0 ]]; then
    say "Lock check skipped until $SECRETS is filled in (Hermes is not started)"
    return 0
  fi
  say "Checking the lock (check_lock.py, including the Hermes probe)"
  if ! run_check --probe; then
    systemctl stop hermes-gateway.service hermes-dashboard.service > /dev/null 2>&1 || true
    die "the lock check failed (problems above), so Hermes is not running. Fix them and run this again."
  fi
  systemctl restart hermes-gateway.service
  note "Hermes is running: journalctl -u hermes-gateway -f"
  if [[ $DASHBOARD == 1 && -f $WEB_DIST/index.html ]]; then
    systemctl enable hermes-dashboard.service > /dev/null
    systemctl restart hermes-dashboard.service
    note "The dashboard runs on 127.0.0.1:9119: ssh -N -L 9119:127.0.0.1:9119 <you>@<server>, then http://127.0.0.1:9119/botsy"
  elif [[ $DASHBOARD == 1 ]]; then
    note "The dashboard web UI is not built ($WEB_DIST), so the dashboard was not started."
  fi
}

memory_note() {
  [[ $SYSTEM == 1 ]] || return 0
  say "Memory (README \"Memory\"): measure before you rely on it"
  free -m | sed 's/^/    /'
  if [[ -z $(swapon --noheadings --show 2> /dev/null) ]]; then
    note "Warning: this server has no swap. Add a 2 GB swapfile first (README \"Memory\")."
  fi
}

install_packages
create_account
lay_out_folders
fetch_and_build
build_web_ui
copy_lock_files
render_configs
install_units
start_services
final_check
memory_note
say "All done. Next: README \"After installing\" (test that the lock holds, from Discord)."
