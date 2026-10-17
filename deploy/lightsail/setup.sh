#!/usr/bin/env bash
# Botsy server setup for an AWS Lightsail Ubuntu 24.04 instance (docs/SERVER.md).
#
# Run it as root (with sudo). It is safe to run again: it adds what is missing, updates
# the code, and leaves your .env and settings alone. The first time:
#   sudo bash setup.sh --repo git@github.com:OWNER/REPO.git --branch BRANCH
# After that it is installed as the command botsy-setup, which remembers the repository,
# branch and time zone (in /etc/botsy/setup.conf):
#   sudo botsy-setup                                   (update code, packages, services)
#   sudo botsy-setup --upload-pubkey "ssh-ed25519 AAAA... name"
#                                                      (let your PC's helper upload; on a
#                                                      server that is set up, only this)
#   sudo botsy-setup --timezone Europe/London          (the server's own clock; default
#                                                      America/New_York. The timers name
#                                                      their zone in the unit files and
#                                                      quiet hours follow schedule.timezone,
#                                                      so this only changes what `date` shows)
# Options: --repo URL  --branch NAME  --timezone ZONE  --upload-pubkey "KEY"  --help
# It never prints a secret. deploy/lightsail/README.md walks through every step.
#
# Why root keeps its own copy of the code: /opt/botsy belongs to the botsy account, which
# runs Botsy, so it handles uploaded files and downloaded Python packages. If root ran or
# installed anything from there, a problem in the botsy account could become a problem
# for the whole server. So everything root uses (this script, the SSH rules, the systemd
# units) comes from root's own copy in /usr/local/lib/botsy/src, downloaded from GitHub
# by root. The first thing this script does is update that copy and restart from it.
# Root also never follows a link inside /opt/botsy: file work there runs as botsy.
set -euo pipefail
umask 022

APP_USER=botsy
UPLOAD_USER=botsy-upload
APP_DIR=/opt/botsy
TRUSTED=/usr/local/lib/botsy/src          # root's own copy of the code (root-owned)
SRC_DIR=$TRUSTED/deploy/lightsail         # root installs files only from here
INBOX=/srv/botsy-inbox
CONF_DIR=/etc/botsy
CONF_FILE=$CONF_DIR/setup.conf
DEPLOY_KEY=$CONF_DIR/deploy_key
APP_KNOWN_HOSTS=$CONF_DIR/known_hosts      # the botsy account's (it fetches /opt/botsy)
ROOT_KNOWN_HOSTS=$CONF_DIR/known_hosts.root
SSHD_DROPIN=/etc/ssh/sshd_config.d/botsy-upload.conf
KEYS_DIR=/etc/ssh/botsy-upload
KEYS_FILE=$KEYS_DIR/authorized_keys
UNIT_DIR=/etc/systemd/system
WRAPPER=/usr/local/bin/botsy
LAUNCHER=/usr/local/sbin/botsy-setup
UNITS=(botsy-inbox.path botsy-tick.service botsy-daily.service botsy-daily.timer
       botsy-weekly.service botsy-weekly.timer botsy-digest.service botsy-digest.timer)
ENABLE=(botsy-inbox.path botsy-daily.timer botsy-weekly.timer botsy-digest.timer)

DEFAULT_TIMEZONE=America/New_York   # the same as schedule.timezone in config/settings.yaml
ZONEINFO=/usr/share/zoneinfo         # root-owned (package tzdata); zone names are checked here

REPO_URL=""
BRANCH=""
TIMEZONE=""
UPLOAD_PUBKEY=""
KEY_ONLY=1        # 0 once --repo or --branch is given: then it is a full setup run
APP_GIT_SSH=""
ROOT_GIT_SSH=""
WORK=$(mktemp -d)
trap 'rm -rf -- "$WORK"' EXIT

say()  { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,/^# It never prints/p' "$0" | sed 's/^# \{0,1\}//'; }

# Run a command as the botsy user, from /, with HOME set (git reads its config there).
as_app() {
  local envs=("HOME=$APP_DIR")
  [[ -n $APP_GIT_SSH ]] && envs+=("GIT_SSH_COMMAND=$APP_GIT_SSH")
  (cd / && runuser -u "$APP_USER" -- env "${envs[@]}" "$@")
}

# Run git as root, for root's own copy only (never inside /opt/botsy).
root_git() {
  local envs=()
  [[ -n $ROOT_GIT_SSH ]] && envs+=("GIT_SSH_COMMAND=$ROOT_GIT_SSH")
  (cd / && env "${envs[@]}" git "$@")
}

apt_install() {
  # No questions on screen; on a new server, wait for the automatic updates to finish first.
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1
  apt-get -q -o DPkg::Lock::Timeout=900 update > /dev/null
  apt-get -q -o DPkg::Lock::Timeout=900 install -y "$@" > /dev/null
}

valid_pubkey() {
  local key=$1 f=$WORK/upload_key.pub
  [[ $key =~ ^(ssh-ed25519|sk-ssh-ed25519@openssh\.com|ecdsa-sha2-nistp(256|384|521)|sk-ecdsa-sha2-nistp256@openssh\.com|ssh-rsa)\ [A-Za-z0-9+/]+={0,3}(\ [^[:cntrl:]]*)?$ ]] \
    || return 1
  printf '%s\n' "$key" > "$f"
  ssh-keygen -l -f "$f" > /dev/null 2>&1
}

# A time zone name as tzdata spells it (Area/City, at most three parts, no '..'), and the
# zone file exists in root's /usr/share/zoneinfo. "UTC" and "Etc/UTC" are fine too.
valid_timezone() {
  local zone=$1
  [[ $zone =~ ^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}$ && $zone != *..* ]] || return 1
  [[ -f $ZONEINFO/$zone ]]
}

# ---------------------------------------------------------------- 0. options

read_options() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo)          [[ $# -ge 2 ]] || die "--repo needs a value";          REPO_URL=$2; KEY_ONLY=0; shift 2 ;;
      --branch)        [[ $# -ge 2 ]] || die "--branch needs a value";        BRANCH=$2; KEY_ONLY=0; shift 2 ;;
      --timezone)      [[ $# -ge 2 ]] || die "--timezone needs a value";      TIMEZONE=$2; KEY_ONLY=0; shift 2 ;;
      --upload-pubkey) [[ $# -ge 2 ]] || die "--upload-pubkey needs a value"; UPLOAD_PUBKEY=$2; shift 2 ;;
      -h|--help)       usage; exit 0 ;;
      *)               die "unknown option: $1 (run with --help to see the options)" ;;
    esac
  done

  [[ $(id -u) -eq 0 ]] || die "run this with sudo, for example: sudo botsy-setup --help"

  # Saved answers from an earlier run (only REPO_URL, BRANCH and TIMEZONE; read as text,
  # never run; the file is root's, under /etc/botsy).
  if [[ -f $CONF_FILE ]]; then
    local key value
    while IFS='=' read -r key value; do
      case "$key" in
        REPO_URL) [[ -n $REPO_URL ]] || REPO_URL=$value ;;
        BRANCH)   [[ -n $BRANCH ]] || BRANCH=$value ;;
        TIMEZONE) [[ -n $TIMEZONE ]] || TIMEZONE=$value ;;
      esac
    done < "$CONF_FILE"
  fi
  [[ -n $TIMEZONE ]] || TIMEZONE=$DEFAULT_TIMEZONE
  [[ -n $REPO_URL ]] || die "say which repository to install, for example:
    sudo bash $0 --repo git@github.com:OWNER/REPO.git --branch main"
  [[ -n $BRANCH ]] || BRANCH=main
  [[ $REPO_URL =~ ^(https://|git@|ssh://)[^[:space:]]+$ ]] \
    || die "--repo must look like git@github.com:OWNER/REPO.git or https://github.com/OWNER/REPO.git"
  [[ $BRANCH =~ ^[A-Za-z0-9._/][A-Za-z0-9._/-]*$ && $BRANCH != *..* ]] \
    || die "--branch '$BRANCH' is not a valid branch name"
  valid_timezone "$TIMEZONE" \
    || die "--timezone '$TIMEZONE' is not a time zone name. Use one from  timedatectl list-timezones
    (for example America/New_York, America/Chicago, America/Los_Angeles, Europe/London)."
  if [[ -n $UPLOAD_PUBKEY ]] && ! valid_pubkey "$UPLOAD_PUBKEY"; then
    die "the --upload-pubkey value is not a public SSH key. Copy the whole line the Botsy
    helper printed on your PC (it starts with ssh-ed25519) and put it in double quotes."
  fi
  if [[ $REPO_URL == git@* || $REPO_URL == ssh://* ]]; then
    ROOT_GIT_SSH="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$ROOT_KNOWN_HOSTS -o StrictHostKeyChecking=accept-new"
    APP_GIT_SSH="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$APP_KNOWN_HOSTS -o StrictHostKeyChecking=accept-new"
  fi
  GIT_HELP="could not download branch '$BRANCH' from $REPO_URL (see the message above).
    Check the repository address and branch name. For a private repository, check that
    the deploy key is added on GitHub (README, step 4)."
}

# ---------------------------------------------------------------- 1. root's own copy

update_trusted_copy() {
  say "Downloading the setup files from GitHub (branch $BRANCH)"
  # shellcheck disable=SC1091
  os_id=$(. /etc/os-release && printf '%s' "${ID:-}")
  # shellcheck disable=SC1091
  os_version=$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")
  if [[ $os_id != ubuntu || $os_version != 24.04 ]]; then
    note "Warning: this script is made for Ubuntu 24.04; this server is ${os_id:-unknown} ${os_version:-}."
  fi
  command -v git > /dev/null || apt_install git

  install -d -m 750 -o root -g "$(getent group "$APP_USER" > /dev/null && echo "$APP_USER" || echo root)" "$CONF_DIR"
  # Remembered for later runs (command-line options always win over it).
  printf 'REPO_URL=%s\nBRANCH=%s\nTIMEZONE=%s\n' "$REPO_URL" "$BRANCH" "$TIMEZONE" > "$CONF_FILE.new"
  chmod 644 "$CONF_FILE.new" && mv -f "$CONF_FILE.new" "$CONF_FILE"

  if [[ -n $ROOT_GIT_SSH ]]; then
    if [[ ! -f $DEPLOY_KEY ]]; then
      ssh-keygen -q -t ed25519 -N "" -C "botsy-server-deploy" -f "$DEPLOY_KEY"
      chmod 600 "$DEPLOY_KEY"
      say "One more step: let this server read your GitHub repository"
      note "On GitHub open the repository, then Settings > Deploy keys > Add deploy key."
      note "Title: Lightsail server. Key: paste the line below. Leave 'Allow write access' unticked."
      printf '\n%s\n\n' "$(cat "$DEPLOY_KEY.pub")"
      note "Then run the same setup command again."
      exit 0
    fi
    [[ -f $ROOT_KNOWN_HOSTS ]] || install -m 644 -o root -g root /dev/null "$ROOT_KNOWN_HOSTS"
  fi

  install -d -m 755 -o root -g root "$(dirname "$TRUSTED")"
  if [[ -d $TRUSTED/.git && $(stat -c %U "$TRUSTED") == root ]]; then
    root_git -C "$TRUSTED" remote set-url origin "$REPO_URL"
    root_git -C "$TRUSTED" fetch --quiet --depth 1 origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" \
      || die "$GIT_HELP"
    root_git -C "$TRUSTED" checkout --quiet --force -B "$BRANCH" "refs/remotes/origin/$BRANCH"
    root_git -C "$TRUSTED" clean --quiet -fdx
  else
    rm -rf -- "$TRUSTED"
    root_git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$TRUSTED" || die "$GIT_HELP"
  fi
  [[ -f $SRC_DIR/setup.sh ]] || die "branch '$BRANCH' has no deploy/lightsail/setup.sh"
  note "Done (commit $(root_git -C "$TRUSTED" rev-parse --short HEAD))."

  local tmp=$WORK/botsy-setup
  cat > "$tmp" <<EOF
#!/bin/sh
# Botsy server setup (installed by deploy/lightsail/setup.sh). It runs root's own copy.
exec bash $SRC_DIR/setup.sh "\$@"
EOF
  install -d -m 755 "$(dirname "$LAUNCHER")"
  install -m 755 -o root -g root "$tmp" "$LAUNCHER"
}

# ---------------------------------------------------------------- 2. packages

install_packages() {
  say "Installing system packages (python3-venv, python3-pip, git, potrace, openssh-server)"
  apt_install python3-venv python3-pip git potrace openssh-server
  note "Done."
}

# ---------------------------------------------------------------- 2b. server clock

# Botsy's timers name their zone (OnCalendar=... America/New_York) and Botsy reads quiet
# hours and the daily log's date in schedule.timezone, so they do not depend on this. The
# server's own zone is set too, so `date`, journalctl and systemctl list-timers show the
# same clock you read in Botsy's messages.
set_timezone() {
  say "Setting the server's clock to $TIMEZONE"
  local current=""
  if command -v timedatectl > /dev/null; then
    current=$(timedatectl show -p Timezone --value 2> /dev/null || true)
  fi
  [[ -n $current ]] || current=$(cat /etc/timezone 2> /dev/null || true)
  if [[ $current == "$TIMEZONE" ]]; then
    note "Already $TIMEZONE."
  elif command -v timedatectl > /dev/null; then
    timedatectl set-timezone "$TIMEZONE" \
      || die "could not set the time zone to $TIMEZONE (see the message above). To keep the
    server's clock as it is, run again with  --timezone ${current:-UTC}"
    note "Done (was ${current:-unknown}). Botsy's timers keep their own zone; see below."
  else
    note "Warning: timedatectl is not available, so the server's clock was left at ${current:-unknown}."
    note "Botsy's timers and quiet hours use $DEFAULT_TIMEZONE / schedule.timezone anyway."
  fi
}

# ---------------------------------------------------------------- 3. accounts

create_accounts() {
  say "Creating the accounts '$APP_USER' (runs Botsy) and '$UPLOAD_USER' (uploads only)"
  getent group "$APP_USER" > /dev/null || groupadd --system "$APP_USER"
  if ! id -u "$APP_USER" > /dev/null 2>&1; then
    useradd --system --gid "$APP_USER" --home-dir "$APP_DIR" --no-create-home \
            --shell /usr/sbin/nologin --comment "Botsy service" "$APP_USER"
  fi
  if ! id -u "$UPLOAD_USER" > /dev/null 2>&1; then
    useradd --system --gid "$APP_USER" --home-dir / --no-create-home \
            --shell /usr/sbin/nologin --comment "Botsy uploads (SFTP only)" "$UPLOAD_USER"
  fi
  # Group botsy, no shell, and no password at all ("*": key login only, never a password).
  usermod --gid "$APP_USER" --shell /usr/sbin/nologin --password '*' "$UPLOAD_USER"
  chown root:"$APP_USER" "$CONF_DIR" && chmod 750 "$CONF_DIR"
  if [[ -f $DEPLOY_KEY ]]; then
    # The botsy account fetches /opt/botsy with the (read-only) deploy key.
    chown "$APP_USER:$APP_USER" "$DEPLOY_KEY" && chmod 600 "$DEPLOY_KEY"
    [[ -f $DEPLOY_KEY.pub ]] && chown "$APP_USER:$APP_USER" "$DEPLOY_KEY.pub"
  fi
  if [[ -n $APP_GIT_SSH && ! -f $APP_KNOWN_HOSTS ]]; then
    install -m 644 -o "$APP_USER" -g "$APP_USER" /dev/null "$APP_KNOWN_HOSTS"
  fi
  note "Done."
}

# ---------------------------------------------------------------- 4. inbox

create_inbox() {
  say "Creating the upload inbox $INBOX"
  install -d -m 755 -o root -g root "$INBOX"
  install -d -m 2770 -o "$UPLOAD_USER" -g "$APP_USER" "$INBOX/incoming"
  install -d -m 2750 -o "$APP_USER" -g "$APP_USER" "$INBOX/outbox"
  # Set them again in case the folders existed with other owners or modes.
  chown root:root "$INBOX" && chmod 755 "$INBOX"
  chown "$UPLOAD_USER:$APP_USER" "$INBOX/incoming" && chmod 2770 "$INBOX/incoming"
  chown "$APP_USER:$APP_USER" "$INBOX/outbox" && chmod 2750 "$INBOX/outbox"
  # OpenSSH refuses a chroot when a folder on its path is not root's or others can write it.
  local d perm
  for d in / /srv "$INBOX"; do
    [[ $(stat -c %U "$d") == root ]] || die "$d must belong to root for the SFTP chroot"
    perm=$(stat -c %a "$d")
    (( (8#$perm & 8#022) == 0 )) || die "$d must not be writable by group or others (it is $perm)"
  done
  note "incoming/ (uploads, $UPLOAD_USER:$APP_USER 2770) and outbox/ (server notes, $APP_USER:$APP_USER 2750) are ready."
}

# ---------------------------------------------------------------- 5. SFTP account

# The key file sshd reads for botsy-upload (AuthorizedKeysFile in the drop-in). sshd wants
# it and its folder owned by root and not writable by anyone else. The Windows installer
# and windows/README.md tell the user to look for the two phrases below: keep them.
add_upload_key() {
  install -d -m 755 -o root -g root "$KEYS_DIR"
  [[ -f $KEYS_FILE ]] || install -m 644 -o root -g root /dev/null "$KEYS_FILE"
  chown root:root "$KEYS_DIR" "$KEYS_FILE" && chmod 755 "$KEYS_DIR" && chmod 644 "$KEYS_FILE"
  [[ -n $UPLOAD_PUBKEY ]] || return 0
  local key_type key_body
  read -r key_type key_body _ <<< "$UPLOAD_PUBKEY"
  if grep -qF -- "$key_type $key_body" "$KEYS_FILE"; then
    note "That upload key was already allowed."
  else
    # "restrict": no shell, forwarding or terminal for this key, whatever sshd allows.
    printf 'restrict %s\n' "$UPLOAD_PUBKEY" >> "$KEYS_FILE"
    note "Upload key added: your PC's Botsy helper can now upload."
  fi
}

# Only --upload-pubkey, on a server that is already set up: add the key and stop. A full
# run takes minutes (downloads, packages, restarts) and can stop for reasons that have
# nothing to do with the key (a busy package manager, a file changed in /opt/botsy),
# which would look as if the key had failed.
key_only_run() {
  [[ -n $UPLOAD_PUBKEY && $KEY_ONLY -eq 1 && ${BOTSY_SETUP_STAGE:-} != run ]] || return 1
  [[ -f $SSHD_DROPIN && -f $UNIT_DIR/botsy-inbox.path && -d $KEYS_DIR ]] || return 1
  id -u "$UPLOAD_USER" > /dev/null 2>&1
}

setup_sftp() {
  say "Setting up SFTP for '$UPLOAD_USER' (upload only, locked into $INBOX)"
  add_upload_key

  local backup=""
  if [[ -f $SSHD_DROPIN ]]; then
    backup=$WORK/botsy-upload.conf.before
    cp -p "$SSHD_DROPIN" "$backup"
  fi
  install -m 644 -o root -g root "$SRC_DIR/sshd_botsy_upload.conf" "$SSHD_DROPIN"
  install -d -m 755 /run/sshd
  if ! sshd -t; then
    if [[ -n $backup ]]; then cp -p "$backup" "$SSHD_DROPIN"; else rm -f "$SSHD_DROPIN"; fi
    die "the SSH settings did not pass 'sshd -t' (see the message above); nothing was changed"
  fi
  local effective
  effective=$(sshd -T -C "user=$UPLOAD_USER,host=localhost,addr=127.0.0.1" 2> /dev/null || true)
  if ! grep -qi "^chrootdirectory $INBOX\$" <<< "$effective"; then
    if [[ -n $backup ]]; then cp -p "$backup" "$SSHD_DROPIN"; else rm -f "$SSHD_DROPIN"; fi
    die "SSH does not read $SSHD_DROPIN. Add this line at the top of /etc/ssh/sshd_config,
    then run this script again:  Include /etc/ssh/sshd_config.d/*.conf"
  fi
  systemctl try-reload-or-restart ssh.service
  note "Done. Other logins (yours, Lightsail's browser SSH) are not affected."
}

# ---------------------------------------------------------------- 6. code

update_app_code() {
  local want
  want=$(root_git -C "$TRUSTED" rev-parse HEAD)
  say "Getting the Botsy code into $APP_DIR (branch $BRANCH)"
  install -d -m 750 -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
  if [[ -d $APP_DIR/.git ]]; then
    local changed
    changed=$(as_app git -C "$APP_DIR" status --porcelain --untracked-files=no)
    if [[ -n $changed ]]; then
      die "these files were changed on the server, so the update stopped (nothing was lost):
$changed
    Make settings changes on GitHub instead (README, 'Change a setting'). To drop the
    server's change to a file:  sudo -u $APP_USER git -C $APP_DIR checkout -- FILE
    then run sudo botsy-setup again."
    fi
    as_app git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    as_app git -C "$APP_DIR" fetch --quiet origin "$BRANCH" || die "$GIT_HELP"
    if ! as_app git -C "$APP_DIR" checkout --quiet "$BRANCH" 2> /dev/null; then
      as_app git -C "$APP_DIR" checkout --quiet -b "$BRANCH" --track "origin/$BRANCH"
    fi
  else
    [[ -z $(ls -A "$APP_DIR") ]] || die "$APP_DIR is not empty and is not a git checkout; move it away first"
    as_app git clone --quiet --no-checkout --branch "$BRANCH" "$REPO_URL" "$APP_DIR" || die "$GIT_HELP"
    as_app git -C "$APP_DIR" checkout --quiet -B "$BRANCH" "$want" \
      || die "could not check out commit $want in $APP_DIR"
  fi
  # The same commit as root's copy, so the code matches the services and SSH rules.
  if ! as_app git -C "$APP_DIR" merge --ff-only --quiet "$want" \
     || [[ $(as_app git -C "$APP_DIR" rev-parse HEAD) != "$want" ]]; then
    die "the server's copy of $BRANCH has commits GitHub does not have, or GitHub's
    branch was rewritten; ask for help"
  fi
  if [[ -n $APP_GIT_SSH ]]; then
    as_app git -C "$APP_DIR" config core.sshCommand "$APP_GIT_SSH"   # so a later "git pull" works too
  fi
  # The botsy home is the checkout: keep tool caches out of "git status".
  local exclude=$APP_DIR/.git/info/exclude pattern
  for pattern in .cache/ .config/ .local/ .rembg/ .u2net/ .ssh/ .python_history .bash_history; do
    as_app grep -qxF -- "$pattern" "$exclude" 2> /dev/null \
      || printf '%s\n' "$pattern" | as_app tee -a "$exclude" > /dev/null
  done
  note "Code is at commit $(as_app git -C "$APP_DIR" rev-parse --short HEAD)."
}

# ---------------------------------------------------------------- 7. Python

install_python_packages() {
  say "Installing Python packages (the first time this takes several minutes)"
  [[ -x $APP_DIR/.venv/bin/python ]] || as_app python3 -m venv "$APP_DIR/.venv"
  as_app "$APP_DIR/.venv/bin/pip" install -q --disable-pip-version-check --no-cache-dir \
         -r "$APP_DIR/requirements.txt"
  note "Done."
}

# ---------------------------------------------------------------- 8. secrets file

check_env_file() {
  say "Checking the secrets file $APP_DIR/.env"
  local env_file=$APP_DIR/.env
  [[ ! -L $env_file ]] || die "$env_file is a link to another file, which is not allowed.
    Look at it with:  sudo ls -l $env_file"
  if [[ ! -e $env_file ]]; then
    as_app install -m 600 "$APP_DIR/.env.example" "$env_file"
    note "Created it from .env.example (empty values). Fill it in with:  sudo -u $APP_USER nano $env_file"
  else
    note "It already exists and was left as it is."
  fi
  chown -h "$APP_USER:$APP_USER" "$env_file"   # -h: changes a link itself, never a file it points to
  as_app chmod 600 "$env_file"
}

# ---------------------------------------------------------------- 8b. server-only settings

# config/settings.local.yaml holds switches that belong to this server only (going live,
# the spending cap). Botsy reads it over config/settings.yaml (docs/SERVER.md section 14).
# It is created once, as botsy, and never overwritten: an update leaves it as it is.
check_local_settings() {
  say "Checking the server settings file $APP_DIR/config/settings.local.yaml"
  local local_file=$APP_DIR/config/settings.local.yaml
  if as_app test -e "$local_file" || as_app test -L "$local_file"; then
    note "It already exists and was left as it is."
    return 0
  fi
  # set -C: never replaces a file (or follows a link) that appeared in the meantime.
  as_app sh -c 'umask 027 && set -C && cat > "$1"' sh "$local_file" <<'LOCAL'
# Botsy settings for this server only. They win over config/settings.yaml, and updates
# (sudo botsy-setup) never change this file. Edit it with:
#   sudo -u botsy nano /opt/botsy/config/settings.local.yaml
# Save with Ctrl+O then Enter, leave with Ctrl+X. The next check uses the new values.

service:
  # false = test mode (no API calls, no Etsy drafts). true = live: real images, listing
  # text and Etsy drafts (you still approve every draft in Etsy).
  live: false

# Monthly spending cap in US dollars for the APIs (estimates). When it is reached, new
# images and listing text wait until next month. Keep the two spaces in front of
# monthly_cap_usd, so it stays under spend:.
spend:
  monthly_cap_usd: null   # change null to your monthly limit in dollars, e.g. 25
LOCAL
  note "Created it (test mode). See README, 'Go live'."
}

# ---------------------------------------------------------------- 8c. working folders

# Folders Botsy keeps its own records in, made as botsy so botsy owns them. With a
# spending cap set, paid calls pause while the spending log cannot be written.
check_data_dirs() {
  say "Checking Botsy's working folders"
  local dir
  for dir in "$APP_DIR/data/spend"; do
    as_app mkdir -p -m 750 "$dir"
    if ! as_app test -w "$dir"; then
      note "WARNING: $dir cannot be written by $APP_USER, so a spending cap would pause paid steps."
      note "Fix it with:  sudo chown -R $APP_USER:$APP_USER $dir"
      return 0
    fi
  done
  note "Done."
}

# ---------------------------------------------------------------- 9. services

# Every OnCalendar line in root's copy of the timers must be one systemd accepts (they
# carry a zone name, which needs systemd 235 or newer; Ubuntu 24.04 has 255). Checked
# before anything is installed, so a typo never leaves a timer that cannot start.
check_calendars() {
  command -v systemd-analyze > /dev/null || return 0
  local unit spec found
  for unit in "${UNITS[@]}"; do
    [[ $unit == *.timer ]] || continue
    found=0
    while IFS= read -r spec; do
      found=1
      systemd-analyze calendar -- "$spec" > /dev/null 2>&1 \
        || die "systemd does not accept the time in $unit: '$spec' (systemd-analyze calendar).
    The timers name a time zone, which needs systemd 235 or newer:  systemctl --version"
    done < <(sed -n 's/^[[:space:]]*OnCalendar[[:space:]]*=[[:space:]]*//p' "$SRC_DIR/systemd/$unit")
    [[ $found -eq 1 ]] || die "$unit has no OnCalendar line"
  done
}

install_services() {
  say "Installing the Botsy services and timers"
  check_calendars
  local unit
  for unit in "${UNITS[@]}"; do
    install -m 644 -o root -g root "$SRC_DIR/systemd/$unit" "$UNIT_DIR/$unit"
  done
  systemctl daemon-reload
  systemctl reset-failed 'botsy-*' 2> /dev/null || true   # clears a stopped watcher (README, troubleshooting)
  systemctl enable --quiet "${ENABLE[@]}"
  systemctl restart "${ENABLE[@]}"
  note "Watching $INBOX/incoming; daily check at 06:17, daily log at 07:07, weekly search list"
  note "on Saturday 09:07, all New York time (America/New_York), whatever the server's clock."
  note "Check with:  systemctl list-timers 'botsy*'"
  # TODO (Sunday plan, docs/OVERSEER.md "Scale-ups ... and the Sunday plan"): botsy-plan.timer
  # (Sun *-*-* 20:00:00 America/New_York) and botsy-plan.service are not installed yet,
  # because `python -m service plan` does not exist. Add them to UNITS and ENABLE when it does.
}

# ---------------------------------------------------------------- 10. helper command

install_wrapper() {
  local tmp=$WORK/botsy
  cat > "$tmp" <<'EOF'
#!/bin/sh
# Run a Botsy command as the botsy user from /opt/botsy (installed by setup.sh). Examples:
#   botsy service status     botsy publisher auth --paste     botsy qa review list
if [ $# -eq 0 ]; then
  echo "usage: botsy <part> [options]   for example: botsy service status" >&2
  exit 2
fi
cd / || exit 1
if [ "$(id -un)" = botsy ]; then
  cd /opt/botsy && exec /opt/botsy/.venv/bin/python -m "$@"
fi
exec sudo -u botsy -H sh -c 'cd /opt/botsy && exec /opt/botsy/.venv/bin/python -m "$@"' botsy "$@"
EOF
  install -d -m 755 "$(dirname "$WRAPPER")"
  install -m 755 -o root -g root "$tmp" "$WRAPPER"
  as_app sh -c "cd '$APP_DIR' && exec .venv/bin/python -m service status" > /dev/null \
    || note "Warning: could not write the first status file (see the message above)."
}

finish() {
  say "Botsy is installed."
  note "Status file:   $INBOX/outbox/status.txt   (or type: botsy service status)"
  note "Test mode is on until you set service.live to true (README, 'Go live')."
  note "To update later, type:  sudo botsy-setup"
  if [[ ! -s $KEYS_FILE ]]; then
    note "No upload key yet. When the Botsy helper on your PC prints its key, run:"
    note "  sudo botsy-setup --upload-pubkey \"ssh-ed25519 AAAA... botsy-upload\""
  fi
  if ! as_app grep -qE '^ANTHROPIC_API_KEY=.+' "$APP_DIR/.env"; then
    note "Next: fill in the secrets with  sudo -u $APP_USER nano $APP_DIR/.env"
  fi
  if ! as_app grep -qE '^DISCORD_WEBHOOK_URL=.+' "$APP_DIR/.env"; then
    note "Optional: Discord messages. Put DISCORD_WEBHOOK_URL in $APP_DIR/.env, then type:"
    note "  botsy service notify-test      (README, 'Discord messages')"
  fi
}

# ---------------------------------------------------------------- run

main() {
  read_options "$@"
  if key_only_run; then
    say "Adding the upload key for your PC's Botsy helper"
    add_upload_key
    note "Nothing else was changed. To update the rest of the server, type:  sudo botsy-setup"
    exit 0
  fi
  local self
  self=$(readlink -f -- "${BASH_SOURCE[0]:-$0}")
  if [[ ${BOTSY_SETUP_STAGE:-} != run || $self != "$SRC_DIR/setup.sh" \
        || $(stat -c %U "$TRUSTED" 2> /dev/null) != root ]]; then
    # Whichever copy was started (root's own, a fresh download, or /opt/botsy's), update
    # root's copy from GitHub and carry on with that one.
    update_trusted_copy
    local args=(--repo "$REPO_URL" --branch "$BRANCH" --timezone "$TIMEZONE")
    [[ -z $UPLOAD_PUBKEY ]] || args+=(--upload-pubkey "$UPLOAD_PUBKEY")
    rm -rf -- "$WORK"   # exec skips the EXIT trap
    BOTSY_SETUP_STAGE=run exec bash "$SRC_DIR/setup.sh" "${args[@]}"
  fi
  install_packages
  set_timezone
  create_accounts
  create_inbox
  setup_sftp
  update_app_code
  install_python_packages
  check_env_file
  check_local_settings
  check_data_dirs
  install_services
  install_wrapper
  finish
}

# Everything above is only definitions, so bash has read the whole file before anything
# runs (git may replace this file while it runs).
main "$@"; exit
