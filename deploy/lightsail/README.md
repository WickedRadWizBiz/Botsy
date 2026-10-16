# Botsy on an AWS Lightsail server: step by step

This guide puts Botsy on a small Linux server in Amazon's cloud (AWS Lightsail) so it runs
day and night. You do everything from your Windows PC, in your web browser. You do not need
to know Linux: every command is written out, and you copy and paste it.

**What the server does once it is set up**

- When the Botsy helper on your PC uploads an EverBee or eRank export, the server takes it
  in, studies it (research), makes products, writes listings and saves them as **Etsy
  drafts**. Nothing ever goes live by itself: you approve every draft in Etsy.
- Every day at 06:17 (server time) it does a full check, and every Sunday at 18:07 it
  writes a new search list telling you what to export next.
- It writes a short report, `status.txt`, which the helper copies to `Documents\Botsy` on
  your PC.
- It starts in **test mode** (nothing is paid for, nothing is sent to Etsy). You switch
  on the real thing in step 9, when everything else works.

**What you need**

- An AWS account (sign up at <https://aws.amazon.com>; it asks for a card).
- Your GitHub account, with access to the Botsy repository.
- About an hour.

**How to copy and paste in the server window.** The server window (step 4) is a black
terminal in your browser. To paste, right-click inside it and choose Paste, or press
Ctrl+Shift+V. If that does not work, use the clipboard button at the bottom right of the
window: paste your text into the box there first. To copy text out of the window, select it
with the mouse and right-click > Copy. **Do not press Ctrl+C in the terminal to copy**:
there, Ctrl+C stops the running command.

---

## Step 1. Create the server

1. Go to <https://lightsail.aws.amazon.com> and sign in.
2. Click **Create instance**.
3. **Instance location**: pick the region closest to you (you can leave the zone as it is).
4. **Select a platform**: **Linux/Unix**.
5. **Select a blueprint**: click **OS Only**, then **Ubuntu 24.04 LTS**.
6. **SSH key pair**: leave the default. (The browser button in step 4 uses it for you.)
7. **Choose your instance plan**: choose **Dual-stack** (not "IPv6-only"), then the plan with
   **4 GB** memory. Smaller plans run out of memory when making images.
8. **Identify your instance**: type the name `botsy`.
9. Click **Create instance**. After a minute or two the instance says **Running**.

## Step 2. Give the server a fixed address (static IP)

Without this, the server's address can change when it restarts, and the helper on your PC
would lose it.

1. On the Lightsail home page, open the **Networking** tab.
2. Click **Create static IP**.
3. Under **Attach to an instance**, choose `botsy`. Name it `botsy-ip`. Click **Create**.
4. Write down the address it shows (four numbers with dots, for example `203.0.113.25`).
   This is **your server address**; the helper on your PC asks for it.

## Step 3. Close every door except SSH (firewall)

1. On the Lightsail home page, click the `botsy` instance, then its **Networking** tab.
2. Under **IPv4 Firewall** you see rules. Keep **SSH (TCP 22)**. Delete every other rule
   (a new instance usually also has **HTTP (TCP 80)**: click its trash icon).
3. If there is an **IPv6 Firewall** section, do the same there: keep only SSH.

Leave SSH open to "any IP address": the helper connects from your home, whose address can
change. SSH on this server only accepts keys, never passwords, and the upload account can
only drop files into one folder.

## Step 4. Open the server window and install Botsy

1. On the Lightsail home page, click the small terminal icon on the `botsy` card (or open
   the instance and click **Connect using SSH**). A black window opens. That is the server.
2. Paste each block below, press Enter, and wait until it finishes before the next one.

**4a. Make a key that lets the server read your private GitHub repository.**
(If your repository is public, skip to 4c and use the `https://` address there.)

```
sudo mkdir -p /etc/botsy
sudo ssh-keygen -t ed25519 -N "" -C botsy-server-deploy -f /etc/botsy/deploy_key
sudo cat /etc/botsy/deploy_key.pub
```

The last command shows one line starting with `ssh-ed25519`. Copy that whole line.

**4b. Add the key on GitHub.**

1. In your browser, open the Botsy repository on GitHub.
2. Click **Settings** > **Deploy keys** > **Add deploy key**.
3. Title: `Lightsail server`. Key: paste the line. Leave **Allow write access** unticked.
4. Click **Add key**.

**4c. Download the code and run the setup.** The commands below use the repository
`WickedRadWizBiz/Botsy` and the branch `claude/zen-newton-w7xzq9`. If yours are different,
change them in both lines before you press Enter.

```
sudo GIT_SSH_COMMAND="ssh -i /etc/botsy/deploy_key -o StrictHostKeyChecking=accept-new" git clone --branch claude/zen-newton-w7xzq9 git@github.com:WickedRadWizBiz/Botsy.git /root/botsy-setup
sudo bash /root/botsy-setup/deploy/lightsail/setup.sh --repo git@github.com:WickedRadWizBiz/Botsy.git --branch claude/zen-newton-w7xzq9
```

For a public repository use `https://github.com/WickedRadWizBiz/Botsy.git` in both places
and leave out the `GIT_SSH_COMMAND=...` part.

The setup takes 5 to 15 minutes (most of it is installing Python packages). It prints a
line starting with `==>` for each part and ends with **Botsy is installed.** If it stops
with **STOPPED:**, read the sentence after it: it says what to fix. Then run the same
command again; the setup is safe to run as often as you like.

What the setup did, in short:
- installed the programs Botsy needs (Python, git, potrace);
- created two accounts: `botsy`, which runs Botsy, and `botsy-upload`, which can only
  upload files into `/srv/botsy-inbox/incoming` and read `/srv/botsy-inbox/outbox`;
- put the code in `/opt/botsy`, with its own Python packages;
- created `/opt/botsy/.env` for your secrets (empty for now, readable only by `botsy`);
- switched on three automatic jobs (watch for uploads, daily check, weekly search list);
- added the command `botsy`, which runs Botsy's tools for you, for example
  `botsy service status`;
- added the command `botsy-setup`, which runs this setup again later (steps 7 and 10).
  It keeps its own copy of the setup files, so the temporary copy is no longer needed.

You can delete the temporary copy now: `sudo rm -rf /root/botsy-setup`

## Step 5. Fill in your secrets

Secrets live only in `/opt/botsy/.env` on the server. Open it in the simple editor nano
(as the `botsy` account, which owns the file):

```
sudo -u botsy nano /opt/botsy/.env
```

Use the arrow keys to move. Fill in the value after the `=` on these lines (no spaces, no
quotes):

- `ANTHROPIC_API_KEY=` your Anthropic API key (listing text);
- `GOOGLE_API_KEY=` your Google Gemini API key (coloring pages and stickers);
- `ETSY_API_KEYSTRING=` and `ETSY_SHARED_SECRET=` from your app at developer.etsy.com
  (see "Etsy setup" in the main README.md);
- leave `ETSY_REDIRECT_URI=https://localhost:3003/callback` as it is (it must match the
  callback URL of your Etsy app);
- `ETSY_SHOP_ID=` is filled in after step 6;
- `ETSY_REFRESH_TOKEN=` stays empty on the server (the server keeps its own token file).

Save with **Ctrl+O** then **Enter**. Leave nano with **Ctrl+X**.

Nothing secret ever goes into GitHub, the status file or the logs.

## Step 6. Connect your Etsy shop

```
botsy publisher auth --paste
```

1. It prints a long link starting with `https://www.etsy.com/oauth/connect`. Copy all of
   it (select with the mouse, right-click > Copy) and open it in a new browser tab on your PC.
2. Sign in to Etsy as the shop owner and click **Allow access**.
3. The browser then tries to open `https://localhost:3003/callback?...` and shows an
   error such as "This site can't be reached". **That is expected.**
4. Copy the whole address from the browser's address bar, paste it into the server
   window and press Enter.
5. It prints `Authorized Etsy user ... (shop_id 12345678)`. If it says
   `Set ETSY_SHOP_ID=...`, put that number into `/opt/botsy/.env` (step 5).

The tokens are saved in `/opt/botsy/.etsy_tokens.json`, readable only by `botsy`. On the
server they renew themselves; you only repeat this step if Etsy asks you to reconnect.

## Step 7. Connect your Windows PC

The Botsy helper on your PC sends your EverBee and eRank exports to the server. Install it
with `install_helper.ps1` from the `windows` folder of this project. When it asks for the
server address, give the static IP from step 2.

At the end, the helper's installer shows **one line to run on the server**. Paste it into
the server window and press Enter. It looks like this (with your own key):

```
sudo botsy-setup --upload-pubkey "ssh-ed25519 AAAA... botsy-upload"
```

It takes a few seconds and says **Upload key added**. That lets your PC upload. On a
server that is already set up, this only adds the key and changes nothing else. Running
it again with the same key says **That upload key was already allowed**.

(An older copy of the installer showed a line starting with
`sudo bash /opt/botsy/deploy/lightsail/setup.sh`. Type `sudo botsy-setup` in place of that
first part: it always uses root's own copy of the setup files.)

## Step 8. Check that it works

In the server window:

```
botsy service status
```

This shows the server's report (the same text as `status.txt`). Before your first export
it says "Nothing has happened yet". While a check is running, this command first waits for
it to finish (it says so, and waits up to 30 minutes). To read the last report straight
away instead: `sudo cat /srv/botsy-inbox/outbox/status.txt`

Now export one CSV in EverBee (Product Analytics > Export CSV). Within a minute the helper
uploads it. The server then waits about 5 minutes for more exports (so a batch is studied
together), and starts. After a few minutes more, `botsy service status` shows
"Exports received: 1 new" and what happened next. The helper copies the same report to
`Documents\Botsy\status.txt` on your PC every hour.

More ways to look (all safe, they change nothing):

| What | Command | Normal answer |
|---|---|---|
| Is the upload watcher on? | `systemctl status botsy-inbox.path` | `active (waiting)` |
| When do the timers run next? | `systemctl list-timers 'botsy*'` | two lines with dates |
| How did the last run go? | `systemctl status botsy-tick` | `inactive (dead)` between runs is fine; `failed` means the last run had a problem |
| The last run's messages | `sudo journalctl -u botsy-tick -n 50 --no-pager` | lines with INFO |
| The daily check's messages | `sudo journalctl -u botsy-daily -n 50 --no-pager` | lines with INFO |
| Run the daily check now | `sudo systemctl start botsy-daily.service` | takes minutes; then check the status |

Press **q** to leave a screen that ends with `(END)` or `lines 1-20`.

In test mode the items go to `/opt/botsy/output/dry-run/`. To see the review queue there:
`botsy qa review list --dry-run`.

## Step 9. Go live

Do this when steps 5 to 8 work and you are ready for real images, real listing text and
real Etsy drafts (they cost API money; drafts are only drafts until you publish them in Etsy).

1. Make sure `config/settings.yaml` has your shop name (not `YOUR_SHOP_NAME`), your prices
   and the Etsy taxonomy ids (see "Etsy setup" in the main README.md). Real drafts are
   refused until these are set.
2. In the same file, under `service:`, change `live: false` to `live: true`.
   Make the change on GitHub (see "Change a setting" below), or ask Claude to do it.
3. Update the server (step 10).
4. `botsy service status` now says **Mode: LIVE**.

To go back to test mode, change it to `live: false` the same way.

**Reviewing items.** Some items wait for your OK before they become drafts (for example a
word that might be a trademark). `status.txt` says how many. On the server:

```
botsy qa review list
botsy qa review approve ITEM_ID
botsy qa review reject ITEM_ID --reason "why"
```

An approved item becomes a draft at the next daily check (or run `sudo systemctl start botsy-daily.service`).

## Step 10. Update the code

When the code on GitHub changed (new features, a settings change), run on the server:

```
sudo botsy-setup
```

It remembers your repository and branch, downloads the new code, installs new Python
packages, reinstalls the automatic jobs and restarts them. Your `.env`, Etsy tokens,
research data and items stay as they are. A run that is busy finishes with the old code;
the next one uses the new code.

Always update this way (not with `git pull`): `botsy-setup` also updates root's own copy
of the setup files, the SSH rules and the automatic jobs, so they match the code.

## Change a setting

Settings are in `config/settings.yaml` in the repository. Change them on GitHub, so the
server and GitHub always agree:

1. Open the repository on GitHub and pick your branch in the branch menu (top left).
2. Open `config/settings.yaml` and click the pencil icon (**Edit this file**).
3. Make the change, then click **Commit changes...** and **Commit changes** again.
4. On the server, run the update from step 10.

If you edit the file on the server instead, the next update stops and lists the changed
file, so nothing is lost. To drop the server's change and use GitHub's version:
`sudo -u botsy git -C /opt/botsy checkout -- config/settings.yaml`, then update again.

After changing research settings, research again with `botsy service research` (the
automatic runs only research again when new exports arrive).

## Optional: use your own time zone

The server uses UTC, so "06:17" is 06:17 UTC. To use your time zone (for example London):

```
sudo timedatectl set-timezone Europe/London
```

`timedatectl list-timezones` lists the names (press q to leave). Then run the update from
step 10 once so the timers pick it up.

## What it costs

- **Lightsail** charges a fixed monthly price for the plan you picked, which includes a
  data-transfer allowance. Prices change and differ by region; see
  <https://aws.amazon.com/lightsail/pricing/>. That page also says when a static IP is
  free (attached to an instance) and when it is charged, so delete one you no longer use.
- **Snapshots** (backups, below) are charged per GB per month (same pricing page).
- **APIs**: Anthropic and Google bill you for what live mode uses. Test mode uses neither.
  `pipeline.max_items_per_run` in the settings caps how many items one run makes.
- **Etsy** charges its usual fees only when you publish a listing in Etsy.

To stop all Lightsail costs, delete the instance, its snapshots and the static IP.

## Backups

Lightsail can copy the whole server (a **snapshot**):

- **By hand**: open the `botsy` instance > **Snapshots** tab > **Create snapshot**.
  Do this before big changes.
- **Automatically**: on the same tab, switch on **Automatic snapshots** (one a day, the
  last seven are kept).

A snapshot contains everything, including `.env` and the Etsy tokens; it stays in your
AWS account. To restore: in the Snapshots tab, open the snapshot's menu > **Create new
instance**, then attach your static IP (step 2) to the new instance, so the helper on your
PC keeps working.

The most important files, if you ever copy things by hand: `/opt/botsy/.env`,
`/opt/botsy/.etsy_tokens.json`, `/opt/botsy/data/` (exports, briefs, history) and
`/opt/botsy/output/` (items).

## When something goes wrong

- **The status file lists a problem.** The text after `!` says what failed. Many fix
  themselves at the next run (Etsy or an API was down). Missing keys: step 5.
- **"another Botsy service command is still running"** (exit code 3): two runs met; the
  second waited 30 minutes and gave up. Nothing is lost: uploads stay in the inbox and the
  next run takes them. The daily check and the weekly search list try again 20 minutes
  later by themselves.
- **An export "Not used"**: the reason is in the status file, and a copy with a
  `.reason.txt` is in `/opt/botsy/data/inbox_rejected/`. The server only takes CSV exports
  from EverBee or eRank, up to 25 MB.
- **The helper on your PC cannot connect** ("Permission denied (publickey)"): run its
  server line from step 7 again. Check the Lightsail firewall still allows SSH (step 3) and
  that the static IP is attached (step 2).
- **Uploads arrive but nothing happens until the daily check**: the watcher stops itself
  when runs keep failing in a row (for example after a settings mistake; the status file
  shows the problem). Fix the problem, then switch it back on:
  `sudo systemctl reset-failed 'botsy-*' && sudo systemctl restart botsy-inbox.path`
  (running `sudo botsy-setup` does the same).
- **The setup says STOPPED**: read the sentence after it, fix that, and run it again.
- **The disk is full**: `df -h /` shows the free space. Old dry-run items can go:
  `sudo rm -rf /opt/botsy/output/dry-run`.
- **Restart everything**: `sudo reboot`, wait two minutes, open the server window again.

## Where things are on the server

| Path | What |
|---|---|
| `/opt/botsy` | the code Botsy runs, its Python packages (`.venv`), `.env` and the Etsy token file (all owned by `botsy`) |
| `/usr/local/lib/botsy/src` | root's own copy of the code; `botsy-setup` installs the SSH rules and automatic jobs from it |
| `/usr/local/sbin/botsy-setup` | the setup command (step 10) |
| `/etc/botsy/` | the remembered repository and branch, and the GitHub deploy key |
| `/opt/botsy/data/research_inputs/` | exports the server accepted (licensed data: stays on the server) |
| `/opt/botsy/data/inbox_rejected/` | uploads that were not used, with the reason |
| `/opt/botsy/data/history/` | research history (for the weekly search list) |
| `/opt/botsy/data/briefs/` | design briefs |
| `/opt/botsy/output/` | items (`output/dry-run/` in test mode) |
| `/srv/botsy-inbox/incoming/` | where the helper uploads |
| `/srv/botsy-inbox/outbox/` | `status.txt`, `status.json`, `search-list.txt` for the helper |
| `/etc/ssh/sshd_config.d/botsy-upload.conf` | the upload account's SSH rules |
| `/etc/ssh/botsy-upload/authorized_keys` | the PC keys that may upload |
| `/etc/systemd/system/botsy-*` | the automatic jobs |

**Safety rules the server keeps**: it never visits etsy.com pages or other sellers' shops;
competitor data only comes from the exports you make; only your own shop is used through
the Etsy API, and only for drafts and your own stats; it never publishes a listing.
