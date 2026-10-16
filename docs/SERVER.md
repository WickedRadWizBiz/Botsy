# Server (Lightsail) and Windows helper: contracts

The pipeline runs 24/7 on an AWS Lightsail Ubuntu 24.04 instance (4 GB RAM plan). Competitor data still comes only from
exports the user makes in EverBee or eRank (no scraping, no browser automation on etsy.com, no
third-party research API). Everything after the export is automatic:

```
Windows PC                                   Lightsail server (/opt/botsy, user "botsy")
----------                                   --------------------------------------------
EverBee/eRank "Export CSV" -> Downloads
windows/botsy_helper.py  --sftp-->  /srv/botsy-inbox/incoming/   (upload-only account "botsy-upload")
                                         | systemd path unit
                                         v
                                    python -m service tick
                                      1. ingest   incoming/ -> data/research_inputs/
                                      2. research briefs + data/history/<date>/<niche>.json
                                      3. pipeline generate -> qa -> listing -> Etsy drafts
                                      4. status   -> /srv/botsy-inbox/outbox/status.txt
windows/botsy_helper.py  <--sftp--  /srv/botsy-inbox/outbox/search-list.txt   (weekly timer)
```

User guides: `deploy/lightsail/README.md` (server) and `windows/README.md` (PC).
`tests/service/test_end_to_end.py` runs this whole loop offline.

## 1. Inbox layout and upload protocol

- `/srv/botsy-inbox/` is the SFTP chroot of `botsy-upload` (owned by root, mode 755, as OpenSSH requires).
  The session starts at the chroot root (no `internal-sftp -d`), so the helper's relative paths `incoming/...` and `outbox/...` work.
- `/srv/botsy-inbox/incoming/`: owner `botsy-upload`, group `botsy`, mode 2770. The helper writes here.
- `/srv/botsy-inbox/outbox/`: owner `botsy`, group `botsy`, mode 2750 (the upload account is in group `botsy` and can only read). The server writes here.
  Every outbox file is written to a temp file, set to mode 640, then renamed, so the helper never downloads half a file and can always read it.
- Upload is atomic: the helper writes `incoming/<name>.part`, then renames it to `incoming/<name>`. The server ignores `*.part` files and dotfiles.
  An abandoned `<name>.part` untouched for `service.part_max_age_h` (48 hours; 0 = never) is deleted.
- The server accepts only `.csv` files up to `service.max_upload_bytes` (25 MB).
- sshd (`deploy/lightsail/sshd_botsy_upload.conf`): `ForceCommand internal-sftp -u 0027 -P symlink,hardlink,copy-data,mkdir,setstat,fsetstat,lsetstat`.
  The helper only uses `put` (no `-p`), `rename` (posix-rename), `-get`, `cd` and `pwd`.
  Upload keys live in the root-owned `/etc/ssh/botsy-upload/authorized_keys` (`AuthorizedKeysFile`), one `restrict <key>` line per PC.

## 2. Ingest: `service.inbox.ingest(incoming, inputs_dir, rejected_dir, *, settings, date=None) -> IngestResult`

- For every finished file in `incoming/`, the server:
  - checks the extension and size;
  - reads it with `research.columns.load_export` and requires a recognized shape (`listing` or `keyword`) with at least one data row;
  - computes its sha256.
- **Accepted:** moved to `data/research_inputs/[<niche>__][own_shop-|trends-]<tool>-<YYYY-MM-DD>-<sha8>.csv`.
  - A `<niche>__` prefix on the original name is kept in front, so the filename niche override still works.
  - An own-shop or trends file keeps its role word, because research tells those files apart by name.
  - What is stored is the server's own copy of the exact bytes it checked.
  - The hash is recorded in `data/research_inputs/.ingested.json` as `{sha256: {"file", "received", "original_name"}}`.
- **Duplicate:** a file whose sha256 was already recorded is deleted and counted as `duplicate`.
- **Rejected:** moved to `data/inbox_rejected/<YYYY-MM-DD>-<name>`, with `<same>.reason.txt` next to it in plain words.
  Links, special files and files over the size limit are deleted instead (only the reason file is written).
- `IngestResult` has `accepted: list[Path]`, `duplicates: list[str]` and `rejected: list[tuple[str, str]]` (name, reason).
- Ingest never reads anything outside `incoming/`. It never follows symlinks (it rejects them), and it rejects hard links.
- Uploaded names are made printable before they reach logs, the ledger or `status.txt`.

## 3. Research history: `data/history/<YYYY-MM-DD>/<niche>.json` (gitignored)

- `research.run(..., history_dir=None)` writes one snapshot per niche when `history_dir` is given.
  A snapshot is written for each niche that has rows in the exports: the same niches that get a brief file, even a file with 0 briefs, so near misses are recorded from the first export.
  It is checked like a brief before anything is written, and it is never written in a dry run.
- The CLI flag is `--history DIR`. The service passes settings `research.history_dir` (`data/history`).
- Snapshots are local working state, never briefs. They are never committed and never passed to a model.

```json
{
  "schema": "history/v1",
  "niche": "coloring_pages",
  "date": "2026-10-19",
  "thresholds": {"listings": 10, "shops": 3},
  "sources": [{"tool": "everbee", "shape": "listing", "rows": 240}],
  "themes": [
    {"name": "farm animals", "listing_rows": 23, "keyword_rows": 0, "shop_count": 7,
     "demand": 0.81, "qualified": true},
    {"name": "axolotl", "listing_rows": 7, "keyword_rows": 0, "shop_count": 2,
     "demand": 0.40, "qualified": false}
  ]
}
```
- `themes` holds the qualifying themes plus near misses. A near miss has at least `research.history.min_rows` (default 3) clean supporting rows of one kind (listing rows, or counted keyword rows).
  The list is capped at `research.history.max_themes` (default 200), best demand first; qualified themes are never the ones cut.
- Same rules as briefs:
  - no shop names, URLs, listing ids, image names or verbatim titles (run the same guard, plus a stricter check on near misses for one seller's brand words);
  - themes with a hard-blocked term are dropped;
  - flagged themes keep `"needs_review": true`.
- `research.history.load(history_dir, niche) -> list[dict]` returns the snapshots oldest first.
- `research.history.timeline(history_dir, niche) -> dict[name, dict]` gives, per theme:
  - `weeks_seen`: distinct ISO weeks the theme appears in;
  - `weeks_qualified`;
  - `first_seen` and `last_seen`;
  - `last` (the newest entry);
  - `trend`: change in `listing_rows` between the newest two snapshots, or null.

## 4. Weekly search list: `service.searchlist.build(settings, history_dir=None, *, date, status=None) -> str` and `write(outbox, text, date)`

- Plain text (UTF-8, CRLF line endings so Notepad shows it cleanly). One section per shop niche, at most `service.search_list.max_per_niche` lines each (default 6).
- `history_dir` defaults to settings `research.history_dir`. Missing history is fine: the list then holds the seed searches only, with a note that it gets smarter after the first exports.
- Each section is made of:
  1. **Seed searches** from `service.search_list.seeds.<niche>`, marked "(every week)". A niche's seeds may be one phrase or a list.
  2. **Near misses**: themes not yet qualified, best demand first, with progress, for example `axolotl coloring pages  (7 of 10 listings, 2 of 3 shops so far)`.
     A theme seen only in keyword exports says `(no listings yet, seen in 3 keyword searches)`.
  3. **Recheck**: qualified themes not seen for `service.search_list.recheck_weeks` weeks (default 2), stalest first, to keep evidence fresh.
     When there are rechecks, one slot is kept for them, so near misses never crowd them out.
- Each search phrase is the theme plus the niche's noun from `service.search_list.nouns` (coloring_pages: "coloring pages", stickers: "stickers", activity_sheets: "activity sheets").
- Every phrase passes the blocklist used by research (blocked terms dropped; flagged ones marked "(check: trademark?)"). Phrases holding a URL are dropped.
- A short header explains what to do: search each phrase in EverBee Product Analytics, click Export CSV, and the helper uploads it. It also says the notes in brackets need not be typed.
- A short footer gives last week's numbers. `status` is the `week` mapping of `outbox/status.json` (section 5): `exports`, `briefs`, `items`, `drafts`, and `practice_drafts` in test mode.
- `write` saves `outbox/search-list-<date>.txt` and `outbox/search-list.txt` (latest), each as a temp file set to mode 640 and then renamed.

## 5. Service CLI: `python -m service <cmd>` (runs on the server)

On the server, `botsy service <cmd>` runs it as the `botsy` user from `/opt/botsy`.
Common options, before or after the command: `--settings FILE`, `--inbox DIR` (overrides `service.inbox_dir`), `--date YYYY-MM-DD`, `-v`.

- `ingest`: section 2 only.
- `research`: `research.run(niche=..., shop_id=..., history_dir=<research.history_dir>)` for every shop niche (each niche once).
- `run`: `pipeline.run.run(stages=("generate","qa","listing","publish"), dry_run=not service.live, ...)` for every shop that has brief files.
- `searchlist`: section 4, with `status=` the `week` totals from `outbox/status.json`.
- `tick`: ingest, then (if any file was accepted, or `--force`) research and run, then status. Used by the systemd path unit and the daily timer. Built as follows (deviations from the first spec):
  - `--own-stats` first refreshes our own shop's stats (`publisher own-stats`), in live mode only. The daily timer passes it.
  - **Settle wait:** after an accepted upload, the tick waits until `incoming/` has been quiet for `service.settle_s` (300 s, at most `service.settle_max_s`, 3600 s), then ingests again. A batch of exports from one search-list session gets one research run.
  - **Rounds:** after research and run, the tick ingests again and goes round once more for uploads that finished meanwhile, up to `service.max_rounds` (3). systemd does not queue path events that arrive while the tick runs.
  - **Forced re-research rule:** a `--force` tick re-researches only when the market exports changed since the last research (a fingerprint of names, sizes and times, kept in `status.json` `last_research`). Brief ids carry the date, so rebuilding unchanged briefs every day would make new items, and image spend, for the same themes. The pipeline still runs, so failed items are retried.
  - Each step records its problems and the tick carries on, so `status.txt` always says what happened.
- `status`: rewrites `outbox/status.txt` from `outbox/status.json` and prints it.
  - `status.txt` shows: the mode (test or live), the last check, the latest tick that did something (exports received, rejected files with reasons, brief files and design briefs, items made, drafts or practice drafts, items waiting for review, failures, problems) and the last 7 days.
  - `status.json` (`status/v1`) keeps tick records for `service.status_keep_days` (35), plus `week` and `last_research`.
    `week` holds `ticks`, `exports`, `duplicates`, `rejected`, `briefs` (distinct brief files), `design_briefs`, `items`, `drafts` (real Etsy drafts), `practice_drafts` (dry run), `errors` and `since`.
  - Errors written to either file have quoted text and links replaced (a brief compliance error lists only the kinds of problem), and the value of any secret environment variable is replaced by `[secret]`. The server log keeps the detail.
- All commands take a single lock (`fcntl.flock` on `service.lock_file`, `data/.service.lock`), so a path-triggered tick and a timer tick never overlap.
  The second one waits up to `service.lock_timeout_s` (1800 s), then exits with code 3.
- Exit codes: 0 ok, 1 something failed (see `status.txt`), 2 setup problem (settings, missing inbox), 3 lock timeout.
- `service.live: false` (default) means dry runs: no API calls and no Etsy drafts until the user sets keys and flips it. Only a real YAML `true` turns it on.

## 6. Server setup and systemd units (deploy/lightsail/)

- `setup.sh` runs as root. The first time it runs as `sudo bash setup.sh --repo URL --branch NAME`; it then installs itself as `/usr/local/sbin/botsy-setup` (on sudo's secure_path).
  - Updates: `sudo botsy-setup` (repository and branch remembered in `/etc/botsy/setup.conf`).
  - Root keeps its own copy of the code in `/usr/local/lib/botsy/src`, downloaded from GitHub by root. The units, the sshd drop-in and the launcher are installed only from that copy, never from `/opt/botsy` (which the `botsy` account owns). Root never follows a link inside `/opt/botsy`.
  - `/opt/botsy/.env` may not be a link; it is created from `.env.example` with mode 600 when missing.
  - `sudo botsy-setup --upload-pubkey "<key>"` authorizes a PC.
    On a server that is already set up, with no other option given, it only checks the key, adds `restrict <key>` to `/etc/ssh/botsy-upload/authorized_keys` and stops. It prints `Upload key added` or `That upload key was already allowed`.
    Otherwise the key is added during the full setup. The Windows installer's fallback line is `echo "restrict <key>" | sudo tee -a /etc/ssh/botsy-upload/authorized_keys`.
- `botsy-inbox.path`: `PathChanged=/srv/botsy-inbox/incoming`, plus `PathExistsGlob=/srv/botsy-inbox/incoming/*.csv` (and `*.CSV`), triggers `botsy-tick.service`. The glob also catches files left over from a tick that ended early.
- `botsy-tick.service`: oneshot, `User=botsy`, `WorkingDirectory=/opt/botsy`, runs `.venv/bin/python -m service tick`.
- `botsy-daily.timer` (06:17 local, `Persistent=true`) runs `botsy-daily.service`: `service tick --force --own-stats` (the tick itself skips own-stats unless live).
- `botsy-weekly.timer` (Sunday 18:07 local, `Persistent=true`) runs `botsy-weekly.service`: `service searchlist`.
- The daily and weekly services retry a lock timeout (exit 3) after 20 minutes (`Restart=on-failure`, `RestartPreventExitStatus=1 2`). The tick service is not restarted: the path unit starts it again.
- Every service runs hardened (`NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome`, `PrivateTmp`, no capabilities) and may write only `/opt/botsy` and the inbox folders.

## 7. Windows helper (windows/)

- `botsy_helper.py` uses the standard library only (Python 3.11+ on Windows). It runs at logon with `pythonw.exe`, from a Scheduled Task "Botsy helper", or from a Startup-folder shortcut when Windows does not allow the task.
- Config lives in `%APPDATA%\Botsy\helper.json`. The installer writes `{"host", "user": "botsy-upload", "key": "%USERPROFILE%\\.ssh\\botsy_upload", "watch_dir": "%USERPROFILE%\\Downloads", "poll_seconds": 30, "max_age_days": 14}`.
  Every other key has a default in `DEFAULTS` in `botsy_helper.py`: `port`, `outbox_dir` (`%USERPROFILE%\Documents\Botsy`), `outbox_every_seconds` (3600), `fetch_after_upload_seconds` (900), `remote_incoming`, `remote_outbox`, `sftp`, `timeout_seconds`, `max_upload_bytes` (25000000, matches `service.max_upload_bytes`), `state_file`, `outbox_state_file` (`%APPDATA%\Botsy\outbox.json`), `log_file` and `lock_file`.
- Every poll:
  - Find `*.csv` files in `watch_dir` modified within `max_age_days`, finished (unchanged for a few seconds and since the last poll) and not yet in the state file (`%APPDATA%\Botsy\state.json`, keyed by sha256).
  - Upload only files whose header row looks like an EverBee or eRank export: at least 2 known research headers within the first 20 lines. Any other CSV, such as a bank statement, is never uploaded.
  - Upload a snapshot copy of each with the Windows OpenSSH client: `sftp -b <batch> -i <key> -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes ... user@host`.
    The batch is `put "<name>" "incoming/<name>.part"`, then `rename "incoming/<name>.part" "incoming/<name>"`.
    `<name>` is the original name folded to `[A-Za-z0-9._-]` plus `-<sha8>.csv`, so a leading `<niche>__` survives and two different files with the same name stay apart.
  - Once an hour (and 15 minutes after an upload), `-get outbox/search-list.txt` and `outbox/status.txt` into `%USERPROFILE%\Documents\Botsy\`.
    A file is replaced only when the server has content this PC has not fetched before (recorded in `outbox.json`), so notes the user wrote in the list are kept. When the search list changes, it is opened with `os.startfile`.
- The helper never deletes, moves or edits the user's files. Logs go to `%APPDATA%\Botsy\helper.log` (rotating, about 1 MB). `--check` tests the connection; `--once` does one pass.
- `install_helper.ps1`:
  - checks that `python` and `sftp` exist;
  - asks for the server address;
  - creates an ed25519 key (`ssh-keygen`) if it is missing;
  - prints the public key with the exact server command that authorizes it, `sudo botsy-setup --upload-pubkey "<key>"`, plus the fallback line, and saves both in `%APPDATA%\Botsy\server-command.txt`;
  - writes the config;
  - registers the Scheduled Task "Botsy helper" (at logon, hidden), or the Startup shortcut;
  - offers a connection test.
- `-Uninstall` removes the task (or shortcut).

## 8. Research stages: discovery, then refined (user, 2026-10-16)

- `research.stage`: `auto` (default), `discovery` or `refined`. With `auto`, the stage is `refined` once the shop has `research.stages.refined.after_drafts` (20) real Etsy drafts. A real draft is an item dir under the configured output dir whose `publish.json` has a `listing_id` and is not `dry_run`. The count comes from `core.items.count_drafts(out_root=None, settings=None) -> int`.
- Thresholds:
  - `discovery`: `min_listings: 10`, `min_shops: 3`. These are today's `research.min_listings_per_pattern` and `min_shops_per_pattern`. Those keys stay valid and act as the discovery values and as the floor.
  - `refined`:
    - `min_listings: 15` and `min_shops: 3`. They apply to brief support, patterns and tags.
    - `min_weeks_seen: 3`: from `research.history.timeline`, the theme must appear in snapshots from at least 3 distinct ISO weeks, counting the current run.
    - `demand_not_falling: true`: refused when the theme's `listing_rows` in the newest earlier snapshot exceeds the current count by more than `falling_tolerance` (0.10), or when its demand rank dropped by more than that tolerance.
    - `own_shop_boost: 0.25` (discovery uses research's existing 0.1).
  - The floor never drops below CLAUDE.md's 10 listings, and below 3 shops per the user's rule.
- The refined history rules (weeks seen, falling) apply only to briefs. Patterns still use the listing and shop thresholds.
- Each brief document records `"stage": "discovery" | "refined"` and the thresholds it used: `min_listings_per_pattern`, `min_shops_per_pattern`, plus for refined `min_weeks_seen`. Refined briefs carry `evidence.weeks_seen` and `evidence.trend`.
- `research.run(..., stage=None)` resolves the stage when it is None. The service passes the resolved stage. The CLI flag is `--stage auto|discovery|refined`.
- A refined run with history too short to qualify any theme writes briefs with an empty `briefs` list and a clear log line. The search list then tells the user which themes need more weeks.
- The search list uses the active stage's thresholds in its progress text. In the refined stage it also shows weeks: "(seen 2 of 3 weeks)".

## 9. Event journal: `core/events.py`

- `emit(kind: str, **fields)` appends one JSON line to `data/events.jsonl`: `{"ts": ISO8601 UTC, "kind": ..., ...fields}`.
  - The file rotates at 5 MB to `events-<n>.jsonl`, keeping 5.
  - Writes are best effort: an emit failure is logged and never raises.
- `read(since: datetime, until: datetime | None = None, kinds=None) -> list[dict]`.
- Kinds the service emits:
  - from ingest: `export_received` (tool, shape, rows) and `export_rejected` (name, reason);
  - from research: `briefs_written` (niche, stage, count) and `stage_changed` (from, to, drafts);
  - from the pipeline summary: `item_generated` (item_id, niche, theme), `qa_failed` (item_id, checks), `listing_written` (item_id, title), `draft_created` (item_id, listing_id, title) and `held_for_review` (item_id, reasons);
  - `spend_warning` (month_to_date, cap, pct), `budget_reached` (month_to_date, cap) and `token_expiring` (days_left);
  - `error` (where, message) and `search_list_written` (path).
- Events hold only our own data: theme names, titles, ids, counts. No competitor identifiers and no secrets.

## 10. Spending tracker: `core/spend.py`

- The ledger is `data/spend/ledger.jsonl`, one line per paid call:
  `{"ts", "month": "YYYY-MM", "provider": "anthropic" | "google", "model", "kind": "text" | "image", "stage", "item_id", "input_tokens", "output_tokens", "images", "cost_usd", "estimated": true}`.
- `record(provider, model, kind, *, stage, item_id=None, input_tokens=0, output_tokens=0, images=0, settings=None) -> float` computes the cost from `spend.prices` (settings, with the date checked) and appends a line.
- `month_to_date(settings=None, month=None) -> dict` returns the total plus breakdowns by provider, stage and item.
- `check_budget(kind, settings=None)` raises `BudgetExceeded` when month-to-date spend is at or over `spend.monthly_cap_usd`.
  - Stages call it BEFORE a paid call: the listing model call and each Gemini image.
  - A cap of null or 0 means no cap.
  - The pipeline turns `BudgetExceeded` into a clean item error. The service emits `budget_reached` once per month and skips paid stages until the month changes or the cap rises.
- `spend.warn_at: 0.8` emits `spend_warning` once per month when crossed.
- Prices go in settings with a comment saying where and when they were checked:
  - Anthropic per million input and output tokens for `claude-opus-5-5` (verify with the claude-api skill);
  - Gemini per image for `gemini-nano-banana-2.1` at each `image_size` (verify on Google's pricing page; if not verifiable, leave a clearly marked placeholder and say so).
  - Costs are estimates (`"estimated": true`); the user's provider bills are authoritative.
- Hooks:
  - `listing/model.py` records `response.usage` input and output tokens after each real call.
  - `generators/imaging/client.py` records one image per successful real `generate()`, plus token usage when the response has `usage_metadata`.
  - Dry runs and fake clients never record.
- CLI: `python -m service spend [--month YYYY-MM]` prints a plain table.

## 11. Notifications (Discord) and overnight log

- `service/notify.py`: `send(text, *, file=None, settings=None) -> bool` posts to the Discord webhook in env `DISCORD_WEBHOOK_URL` (a secret, in `.env`).
  - Limits: 2000 characters of content (longer text is attached as a `.txt` file), timeout 10 s, one retry on 429 honoring `retry_after`.
  - It never raises and returns False on any failure. It never logs the webhook URL.
  - Disabled when the env var is unset or `notify.discord.enabled` is false.
- `notify.discord.events` picks which event kinds post right away. Defaults:
  - `draft_created`, summarized per tick as "3 new drafts are waiting in Etsy: <titles>";
  - `held_for_review`, `export_rejected`, `budget_reached`, `spend_warning`, `token_expiring`, `stage_changed`, `error`;
  - the weekly search list, posted with `search-list.txt` attached.
- `notify.quiet_hours: ["22:00", "07:00"]` (server local time): non-urgent posts during quiet hours are held for the morning digest. Urgent kinds (`budget_reached`, `error`) always post.
- `service/digest.py`, the overnight log:
  - `build(since, until, settings) -> str` builds a plain-text summary of the window from the event journal plus the spend ledger.
  - Sections:
    - exports received and rejected;
    - research (stage, briefs per niche);
    - items made, QA failures, listings written;
    - drafts created, with titles;
    - waiting for your review, with the exact approve command;
    - spend for the window and month to date versus the cap;
    - errors and warnings;
    - what runs next.
  - Plain words, CRLF line endings.
- `python -m service digest [--hours 24]` writes `outbox/daily-log.txt` and `outbox/daily-log-<date>.txt`, then posts the log to Discord (attached when long).
- The systemd timer `botsy-digest.timer` runs it daily at 07:07 local time.
- The Windows helper also fetches `outbox/daily-log.txt` into `Documents\Botsy\`. It does not auto-open it; only the search list opens.
- `.env.example` gains `DISCORD_WEBHOOK_URL=` with a comment: Discord channel > Edit Channel > Integrations > Webhooks > New Webhook > Copy URL.

## 12. Evidence window and weekly evidence (fixes "every theme is seen every week")

- Each accepted export has a received date. It comes from the ingest name `<tool>-<YYYY-MM-DD>-<sha8>.csv`, else from the `.ingested.json` ledger, else from the file's mtime.
- Briefs use only exports received within `research.window_days` (default 35) of the run date. Older exports stay on disk until `research.keep_days` (180), then the service deletes them.
- Each history snapshot theme gains `fresh_rows`: its supporting rows from exports received in the snapshot's ISO week.
  - `timeline().weeks_seen` counts the ISO weeks where `fresh_rows >= research.history.min_rows`. That is what "seen in 3+ weekly exports" means.
  - `weeks_qualified` counts the weeks where the theme met the stage thresholds within that week's window.
- The search list:
  - lists a near miss only if it has rows from at least 2 shops, or counted keyword rows;
  - moves a near miss down when its listing rows and shop count have not grown for 3 snapshots ("stalled");
  - rechecks qualified themes with no fresh rows for `recheck_weeks`.

## 13. Theme cooldown (no duplicate products)

- Before generating, the pipeline skips a brief when the same shop and niche already has an item, under the configured output dir, with the same normalized theme.
  - The theme is matched with `core.compliance.normalize` plus slug.
  - The existing item must have been created within `pipeline.theme_cooldown_days` (default 60), or carry a real Etsy draft at any age.
- `--regenerate` and a rejected review entry keep their current meaning.
- Skips are reported in the run summary as `generate: skip (theme cooldown: <item_id>)`.

## 14. Server-only settings and disk caps

- `core.config.load_settings()` deep-merges `config/settings.local.yaml` (gitignored, optional) over `config/settings.yaml`. Server-only switches live there, for example `service.live: true`, so going live never needs a commit.
- `setup.sh` never overwrites `settings.local.yaml`. The deploy README tells the user to edit it with nano.
- Disk caps:
  - `data/inbox_rejected/` keeps at most `service.rejected_max_mb` (200 MB) and `service.rejected_keep_days` (30), deleting the oldest first;
  - accepted exports older than `research.keep_days` are deleted;
  - `output/dry-run/` items older than `service.dry_run_keep_days` (14) are deleted.
- Cleanup runs at the end of every tick and logs what it removed.
