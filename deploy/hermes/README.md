# Hermes on Botsy's server: install and lock-down

Hermes Agent (Nous Research, MIT licence) is the chat helper that talks to you on Discord and
uses Botsy's two tool servers, `botsy-read` and `botsy-act`. The design is in `docs/OVERSEER.md`
("How Botsy and Hermes talk", "Lock-down: three layers", "Hermes can never") and the reasons for
each setting are in "Hermes setup" there.

## What Hermes can do (decided 2026-10-21)

"Too cautious; I want Hermes pulling its weight." Besides Botsy's tools and its own memory,
Hermes now has, in Discord:

| Capability | What it means here | The limit |
|---|---|---|
| Vision | Looks at our own product previews (botsy-read's `review_preview`) and at images you attach. | It cannot download from the web (every host is on its blocklist, and the firewall lets it reach only Anthropic and Discord) and cannot see Botsy's folders or other programs' data. Never send it a competitor's image. |
| Models | Haiku 5.5 for chat and routine jobs; Sonnet 5.5 for the weekly plan and the creative-director review. | Sonnet runs only in the two jobs you pin to it ("Sonnet jobs" below). Hermes's own workspace limit is **$10** a month. |
| Its own check-ins | Hermes may schedule its own jobs (for example "every morning, tell me what waits for review"). | On Haiku, read-only for Botsy (a job can never approve), at most 10, none more often than hourly, no scripts or web monitors. Checked every hour; a job that breaks a limit stops Hermes until you remove it. |
| Past-chat search, a to-do list, a task board | `session_search`, `todo`, `kanban`. | The task board starts no other agents. |
| Skills | Hermes may write and improve its own how-to notes. | Every new or changed skill waits for you: `/skills pending`, `/skills diff <id>`, `/skills approve <id>` or `reject`. |
| Discord | Reads recent messages and starts threads; adds status reactions to your messages. | No pins, deletes, roles or member search (those are Discord administration, off). |

Still off, and why: terminal, files, code execution, browser and computer use (they reach the
machine); Hermes's own web and search tools (competitor listings must never reach a model, see
`CLAUDE.md`; trend research goes only through a Botsy tool that strips marketplace listings);
image, video and speech generation (paid media); sub-agents (`delegation`); Discord
administration; and any MCP server other than Botsy's two.

Botsy works without Hermes. Install Hermes only when Botsy itself runs on the server
(`deploy/lightsail/README.md`), and only after the memory check below.

Decided 2026-10-21: no codes. Older notes that mention 6-digit codes or `#botsy-codes` are
out of date.

Pinned release: **Hermes Agent v0.21.7** (2026-10-12, commit `71d34bad1045`),
https://github.com/NousResearch/hermes-agent/releases/tag/v0.21.7. The pin lives in
`hermes-pin.env`; the install refuses any other commit.

## What is in this folder

| File | What it is |
|---|---|
| `install_hermes.sh` | The installer. Run as root. Safe to run again. |
| `hermes-pin.env` | The pinned release (version, tag, commit, date, URL). |
| `config.yaml.template` | Hermes's locked settings. Rendered into `/etc/hermes/config.yaml` (Hermes's "managed scope", which wins over everything) and `/home/hermes/.hermes/config.yaml`. Both root-owned. |
| `SOUL.md` | Hermes's standing instructions (who it helps, how it asks before approving, what it never does). |
| `hermes.env.example` | The template for `/etc/hermes/hermes.env`, your secrets file. |
| `check_lock.py` | The lock check. Exit 1 with one `PROBLEM:` line per problem. |
| `botsy-control-connect` | The tiny stdio-to-socket link Hermes starts for each Botsy tool server. |
| `systemd/` | The Hermes service, Botsy's two tool sockets, the daily lock check, the hourly job check and the firewall. |
| `nftables-hermes.conf`, `botsy-hermes-resolve` | The egress limit (required since 2026-10-21). |

## Before you start (about 30 minutes, all in your browser)

### 1. Make a separate Anthropic workspace with a $10 limit

Hermes must never use Botsy's API key. A separate workspace gives it its own key and its own
spending limit, so a chat or a job that goes wrong can cost at most $10 a month (raised from $5
on 2026-10-21 for vision, its own check-ins and the two weekly Sonnet jobs).

1. Open https://console.anthropic.com and sign in.
2. Settings > Workspaces > Create workspace. Name it `hermes`.
3. In the `hermes` workspace: Limits > set the monthly spend limit to **$10**. (Already made
   with $5? Just raise it there.)
4. API keys > Create key, in the `hermes` workspace. Name it `hermes-server`. Copy it
   (it starts with `sk-ant-`). You paste it in step 4.

### 2. Create the Discord bot with the fewest permissions

1. Turn on two-factor authentication for your Discord account first (User Settings > My
   Account > Enable Two-Factor Auth). Without it, a stolen password is a stolen bot.
2. Open https://discord.com/developers/applications > New Application. Name it `Hermes`.
3. Bot tab:
   - Reset Token, copy the token (you paste it in step 4). Never share it, never paste it in
     a chat.
   - Public Bot: **off** (only you can add it to a server).
   - Privileged Gateway Intents: turn on **Message Content Intent** only. Leave Presence and
     Server Members off.
4. OAuth2 > URL Generator: scope `bot`. Bot permissions, tick only:
   - View Channels
   - Send Messages
   - Send Messages in Threads
   - Create Public Threads (Hermes may start a thread for a long topic)
   - Embed Links
   - Attach Files (so it can show you Botsy's own previews)
   - Read Message History
   - Add Reactions

   **Never** tick Administrator, Manage Server, Manage Channels, Manage Roles, Manage Webhooks,
   Manage Messages, Kick, Ban, Mention Everyone, or anything under Voice. Open the generated
   link and add the bot to your Botsy server.

### 3. Copy your user ID

1. Discord > User Settings > Advanced > **Developer Mode on**.
2. Right-click your own name > Copy User ID (17 to 20 digits). This is the only person Hermes
   answers. Never use a role.

No codes (decided 2026-10-21). There is no `#botsy-codes` channel, no codes webhook and no
channel ID to copy. When you ask Hermes to approve something (review items, a weekly plan, a
higher cap, restarting paid work), Hermes asks "yes?" and then sends it. Hermes itself then
holds the call until you click **Allow** on its approval button in Discord (`botsy-act` is
`trust: untrusted` in the config, checked by check_lock.py): this applies to every Botsy action,
pause and cap changes included, only your user ID can click, and a scheduled job can never send
one (Hermes declines it, as nobody is there to click). The button names the tool, not its details,
so read Hermes's one-line summary first. Botsy then checks it again
(blocked items can never be approved, the cap never goes above the root-only ceiling, and by
default at most 10 such actions a day and 20 items in one batch, set in the root-only limits
file) and posts it at once in the main channel with who asked and how to undo it. That makes your Discord account the key: keep two-factor
authentication on, and read those main-channel posts. The same actions also work from the
server CLI without Hermes (`python -m service plan approve`, `python -m qa review approve ID`).

## Install on the server

### Memory first (Lightsail, 4 GB)

Botsy, its image work and Hermes share 4 GB. Measure before you rely on it:

```
free -m
swapon --show
```

If there is no swap, add a 2 GB swapfile once:

```
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Hermes is capped at 1.5 GB (`MemoryMax=1536M` in the service). After a day of normal use, check
`systemctl status hermes-gateway` (the "Memory:" line) and `free -m`. If "available" stays
under about 500 MB while Botsy runs a batch, stop Hermes (`sudo systemctl stop hermes-gateway`)
and tell Claude; do not raise the cap without measuring.

### 4. Run the installer

Botsy's own setup keeps root's copy of the code in `/usr/local/lib/botsy/src`. Run the
installer from there (it refuses a copy that other users can write):

```
sudo botsy-setup                                 # update root's copy of the code first
sudo bash /usr/local/lib/botsy/src/deploy/hermes/install_hermes.sh
```

The first run installs everything but does not start Hermes, because the secrets are empty.
Fill them in:

```
sudo nano /etc/hermes/hermes.env
```

Paste the three values from steps 1 to 3 after the `=` signs (no quotes, no spaces). Save, then
run the installer again:

```
sudo bash /usr/local/lib/botsy/src/deploy/hermes/install_hermes.sh
```

It ends with `Hermes lock: OK (...)` and `Hermes is running`. If it says `STOPPED:` or lists
`PROBLEM:` lines, Hermes is not running; fix what it says and run it again.

The installer also limits the `hermes` user's network to Anthropic and Discord only (an
nftables table of its own). This is required since 2026-10-21: Hermes's scheduled jobs, task
board and vision can each fetch a web address, and this table is what stops that.
`--no-firewall` is refused, and an older install with the firewall off is switched on. Other
users on the server, Botsy included, are not affected.

### What the installer sets up

- A system user `hermes`: no password, no login shell, not in `sudo`, `adm`, `botsy` or any
  Botsy group, and no way into `/opt/botsy`, `/etc/botsy`, `/home/botsy` or the inbox.
- The pinned Hermes in `/opt/hermes` (root-owned): the git tag, checked against the pinned
  commit; Hermes's own pinned `uv` and Python, sha256 checked; its locked dependencies
  (`uv sync --frozen`) with only the `anthropic`, `discord` and `mcp` extras. No browser,
  voice or computer-use parts.
- `/etc/hermes/config.yaml` and `/home/hermes/.hermes/config.yaml`: the locked settings,
  root-owned, read-only to Hermes, so Hermes cannot change its own locks.
- `/etc/hermes/hermes.env`: your secrets, `root:hermes`, mode 0640.
- `hermes-gateway.service`: runs as `hermes` with `NoNewPrivileges=yes`, no capabilities,
  a read-only system, and Botsy's folders hidden.
- Botsy's tool sockets (`/run/botsy-control/read.sock` and `act.sock`): systemd starts a fresh
  `botsy-control-read` or `-act` as the `botsy` user for each connection. Hermes needs no sudo.
- `botsy-hermes-lock.timer`: every day at 5:41 am New York time, `check_lock.py` checks
  everything again (including asking the installed Hermes what it would turn on) and stops
  Hermes if anything is wrong.
- `botsy-hermes-jobs.timer`: every hour, `check_lock.py jobs` checks Hermes's scheduled jobs
  (the limits in "Scheduled jobs" below) and stops Hermes if one breaks them.
- An empty, root-owned `/home/hermes/.hermes/scripts`, so no scheduled job can run a script.

## After installing: test that the lock holds

In Discord, mention the bot in your Botsy channel and try:

1. "What is Botsy's status?" It should answer from `botsy-read`.
2. "Run `ls /` on the server." It must say it cannot run commands.
3. "Search the web for X." It must say it cannot. "Look at https://www.etsy.com/..." with
   an image link: vision must refuse it (blocked by website policy).
4. "Show me the preview of review item <id>." It should describe our own preview image.
5. "Approve the review batch." It should say in one line what that approves and ask "yes?";
   after your yes, Discord shows an approval button. Click **Deny** once: Hermes must report
   that nothing was sent. Ask again and click **Allow**: Botsy posts the approval in the main
   channel with how to undo it.
6. Type `/model` and `/yolo`. Both must be refused. `/skills pending` must answer.
7. "Every 5 minutes, check the queue." Within the hour Hermes is stopped and
   `journalctl -u botsy-hermes-jobs -n 20` names the job; remove it ("Scheduled jobs" below).
8. From a second Discord account (or ask a friend): message the bot. It must not answer.

Then on the server:

```
sudo bash /usr/local/lib/botsy/src/deploy/hermes/install_hermes.sh --check
journalctl -u hermes-gateway -n 50
```

## Slash commands in Discord

Hermes's own slash commands are locked too. Hermes checks them per user, and you are the only
allowed user, so these are yours alone: `/new` (fresh chat), `/retry`, `/status`, `/stop`,
`/compress`, `/memory`, `/skills` (review Hermes's skill changes), `/bg` (a background chat),
`/loop` (repeat a prompt), `/goal` (a standing goal), `/help` and `/whoami`. `/bg`, `/loop` and
`/goal` run on Haiku and count against the $10 limit; stop a loop with `/loop stop`.
Everything else is refused, including `/model` (a pricier model), `/yolo`, `/tools`,
`/heartbeat` and `/moa`, and `/goal gate add` (it runs a shell command). `/cron` is not a
Discord command in v0.21.7; ask Hermes in chat to make or change a check-in. The config does
this with an admin list that names no real account (`allow_admin_from: ["0"]`); the lock check
refuses any other admin list or any extra command.

Memory: Hermes asks before it saves anything to its memory. See what waits with
`/memory pending`, then `/memory approve <id>` or `/memory reject <id>`. Never let it save a
key, a token or anything personal. `/memory approval off` cannot work here: the config file is root's, and the locked
setting in /etc/hermes wins anyway.

Skills: when Hermes writes or changes a skill, nothing is saved yet. `/skills pending` lists
what waits, `/skills diff <id>` shows the change, then `/skills approve <id>` or
`/skills reject <id>`. `/skills approval off` cannot work either (same reason).

## Scheduled jobs

Hermes may make its own check-ins from chat ("every weekday at 8, tell me what waits"). Hermes
v0.21.7 has no setting for how many or how often, so `check_lock.py jobs` (hourly, and before
every start) enforces these and stops Hermes when one is broken:

- at most 10 jobs on; none more often than hourly;
- no scripts, no web-address monitors, no work folder, no other provider or endpoint;
- only the scheduled-job toolsets (Botsy's tools, memory, vision, past-chat search, to-do,
  skills); a job never gets Discord, the task board or the job tool, and every Botsy action in
  a job is declined (nobody is there to click Allow);
- Haiku, except the two Sonnet jobs below.

See and remove jobs from the server (Hermes may be stopped; this works anyway):

```
sudo -u hermes env HOME=/home/hermes HERMES_HOME=/home/hermes/.hermes HERMES_MANAGED_DIR=/etc/hermes \
  /opt/hermes/venv/bin/hermes cron list
sudo -u hermes env HOME=/home/hermes HERMES_HOME=/home/hermes/.hermes HERMES_MANAGED_DIR=/etc/hermes \
  /opt/hermes/venv/bin/hermes cron remove <job id>
sudo systemctl start hermes-gateway
```

### Sonnet jobs

Hermes cannot choose a model for a job (v0.21.7: "models don't pick models"), so you create the
two Sonnet jobs once, with exactly these names (the job check allows Sonnet on no other job, and
each at most once a week):

```
H="sudo -u hermes env HOME=/home/hermes HERMES_HOME=/home/hermes/.hermes HERMES_MANAGED_DIR=/etc/hermes /opt/hermes/venv/bin/hermes"
$H cron create "23 7 * * 0" "Prepare this week's plan with botsy-read (plan, briefs, spend, review queue). Post a short summary and ask me to approve it in chat; never approve it yourself." \
   --name botsy-weekly-plan --model claude-sonnet-5-5 --provider anthropic --deliver discord:<channel ID>
$H cron create "23 10 * * 3" "Creative-director review: look at this week's briefs and our own previews with botsy-read and say what to keep, drop or improve. Our own images only." \
   --name botsy-creative-director --model claude-sonnet-5-5 --provider anthropic --deliver discord:<channel ID>
```

`<channel ID>` is your Botsy channel (right-click it > Copy Channel ID, with Developer Mode on).
Jobs Hermes makes from chat post back to the chat they were made in.

When you ask in chat ("run the creative review now"), Hermes may run either job at once; the
$10 limit caps that.

## Everyday commands

| What | Command |
|---|---|
| Is Hermes running? | `systemctl status hermes-gateway` |
| Its log | `journalctl -u hermes-gateway -f` |
| Stop Hermes (Botsy keeps working) | `sudo systemctl stop hermes-gateway` |
| Start again | `sudo systemctl start hermes-gateway` |
| Check the lock now | `sudo bash /usr/local/lib/botsy/src/deploy/hermes/install_hermes.sh --check` |
| Change a secret | `sudo nano /etc/hermes/hermes.env`, then run the installer again |
| Hermes's scheduled jobs | "Scheduled jobs" above; `journalctl -u botsy-hermes-jobs -n 20` |

## Updating Hermes

Never run `hermes update` (it is disabled by the install stamp). To move to a newer release,
Claude checks it (at least 7 days old, no hotfix after it, settings still valid), changes
`hermes-pin.env` and the template if needed, and the lock tests must pass. Then
`sudo botsy-setup` and run the installer again; it rebuilds `/opt/hermes` and checks the lock
before starting.

## If something goes wrong

- `STOPPED: ... is not owned by root`: you ran a copy in your home folder. Use the
  `/usr/local/lib/botsy/src/...` path.
- `PROBLEM: ... differs from the template`: someone edited a rendered config. Run the installer
  again; never edit `/etc/hermes/config.yaml` by hand.
- `botsy-control read is not available`: `sudo systemctl status botsy-control-read.socket`.
- Hermes answers nobody: check `DISCORD_ALLOWED_USER_ID` is your user ID (not a role or a
  channel) and that Message Content Intent is on.
- The $10 limit was reached: Hermes stops answering until next month or until you raise the
  workspace limit in the Anthropic Console. Botsy is not affected.
- `PROBLEM: scheduled job ...`: Hermes made (or you changed) a job outside the limits. Remove or
  fix it ("Scheduled jobs"), then `sudo systemctl start hermes-gateway`.

Fallback bridge (only if the socket bridge does not work on your server): `--bridge sudo`
installs the one sudo rule from `deploy/lightsail/sudoers.d/botsy-control` and lets Hermes start
`sudo -n -u botsy /usr/local/bin/botsy-control-read` directly. That needs a weaker service
(sudo does not work under `NoNewPrivileges`), so it is not the default.
