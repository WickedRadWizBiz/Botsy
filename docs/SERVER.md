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

## 1. Inbox layout and upload protocol

- `/srv/botsy-inbox/` is the SFTP chroot of `botsy-upload` (owned by root, mode 755, as OpenSSH requires).
- `/srv/botsy-inbox/incoming/`: owner `botsy-upload`, group `botsy`, mode 2770. The helper writes here.
- `/srv/botsy-inbox/outbox/`: owner `botsy`, group `botsy`, mode 2750 (the upload account is in group `botsy` and can only read). The server writes here.
- Upload is atomic: the helper writes `incoming/<name>.part`, then renames it to `incoming/<name>`. The server ignores `*.part` files and dotfiles.
- The server accepts only `.csv` files up to `service.max_upload_bytes` (25 MB).

## 2. Ingest: `service.inbox.ingest(incoming, inputs_dir, rejected_dir, *, settings) -> IngestResult`

- For every finished file in `incoming/`, the server:
  - checks the extension and size;
  - reads it with `research.columns.load_export` and requires a recognized shape (`listing` or `keyword`);
  - computes its sha256.
- **Accepted:** moved to `data/research_inputs/<tool>-<YYYY-MM-DD>-<sha8>.csv`. A `<niche>__` prefix on the original name is kept in front so the filename niche override still works. The hash is recorded in `data/research_inputs/.ingested.json` as `{sha256: {"file", "received", "original_name"}}`.
- **Duplicate:** a file whose sha256 was already recorded is deleted and counted as `duplicate`.
- **Rejected:** moved to `data/inbox_rejected/<YYYY-MM-DD>-<name>`, with `<same>.reason.txt` next to it.
- `IngestResult` has `accepted: list[Path]`, `duplicates: list[str]` and `rejected: list[tuple[str, str]]` (name, reason).
- Ingest never reads anything outside `incoming/` and never follows symlinks (it rejects them).

## 3. Research history: `data/history/<YYYY-MM-DD>/<niche>.json` (gitignored)

- `research.run(..., history_dir=None)` writes one snapshot per niche when `history_dir` is given.
- The CLI flag is `--history DIR`. The service passes `settings research.history_dir` (`data/history`).
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
- `themes` holds qualifying themes plus near misses: themes with at least `research.history.min_rows` (default 3) clean supporting rows, capped at `research.history.max_themes` (default 200), best demand first.
- Same rules as briefs:
  - no shop names, URLs, listing ids, image names or verbatim titles (run the same guard);
  - themes with a hard-blocked term are dropped;
  - flagged themes keep `"needs_review": true`.
- `research.history.load(history_dir, niche) -> list[dict]` returns the snapshots oldest first.
- `research.history.timeline(history_dir, niche) -> dict[name, dict]` gives, per theme:
  - `weeks_seen`: distinct ISO weeks the theme appears in;
  - `weeks_qualified`;
  - `first_seen` and `last_seen`;
  - `last` (the newest entry);
  - `trend`: change in `listing_rows` between the newest two snapshots, or null.

## 4. Weekly search list: `service.searchlist.build(settings, history_dir, *, date) -> str` and `write(outbox, text, date)`

- Plain text (UTF-8, CRLF line endings so Notepad shows it cleanly). One section per shop niche, at most `service.search_list.max_per_niche` lines each (default 6).
- Each section is made of:
  1. **Seed searches** from `service.search_list.seeds.<niche>`, marked "(every week)".
  2. **Near misses**: themes not yet qualified, best demand first, with progress, for example `axolotl coloring pages  (7 of 10 listings, 2 of 3 shops so far)`.
  3. **Recheck**: qualified themes not seen in the last 2 weeks, to keep evidence fresh.
- Each search phrase is the theme plus the niche's noun from `service.search_list.nouns` (coloring_pages: "coloring pages", stickers: "stickers", activity_sheets: "activity sheets").
- Every phrase passes the blocklist (blocked terms dropped; flagged ones marked "(check: trademark?)").
- A short header explains what to do: search each phrase in EverBee Product Analytics, click Export CSV, and the helper uploads it.
- A short footer gives last week's numbers from the status file: exports received, briefs, items, drafts.
- `write` saves `outbox/search-list.txt` (latest) and `outbox/search-list-<date>.txt`.

## 5. Service CLI: `python -m service <cmd>` (runs on the server)

- `ingest`: section 2 only.
- `research`: `research.run` for every shop niche with `history_dir`.
- `run`: `pipeline.run.run(stages=("generate","qa","listing","publish"), dry_run=not service.live, ...)`.
- `searchlist`: section 4.
- `tick`: ingest, then (if any file was accepted, or `--force`) research and run, then status. Used by the systemd path unit and the daily timer.
- `status`: writes `outbox/status.txt`: last tick time, ingest results (rejected files with reasons), briefs written, items generated, drafts created, and errors.
- All commands take a single lock (`fcntl.flock` on `data/.service.lock`), so a path-triggered tick and a timer tick never overlap. The second one waits up to 30 minutes, then exits with code 3.
- `service.live: false` (default) means dry runs: no API calls and no Etsy drafts until the user sets keys and flips it.

## 6. systemd units (deploy/lightsail/systemd/)

- `botsy-inbox.path`: `PathChanged=/srv/botsy-inbox/incoming` triggers `botsy-tick.service`.
- `botsy-tick.service`: oneshot, `User=botsy`, `WorkingDirectory=/opt/botsy`, runs `.venv/bin/python -m service tick`.
- `botsy-daily.timer` (06:17 local) runs `botsy-daily.service`: `publisher own-stats` (only when live), then `service tick --force`.
- `botsy-weekly.timer` (Sunday 18:07 local) runs `botsy-weekly.service`: `service searchlist`.

## 7. Windows helper (windows/)

- `botsy_helper.py` uses the standard library only (Python 3.11+ on Windows) and runs at logon from Task Scheduler with `pythonw.exe`.
- Config lives in `%APPDATA%\Botsy\helper.json`: `{"host", "user": "botsy-upload", "key": "%USERPROFILE%\\.ssh\\botsy_upload", "watch_dir": "%USERPROFILE%\\Downloads", "poll_seconds": 30, "max_age_days": 14}`.
- Every poll:
  - Find `*.csv` files in `watch_dir` modified within `max_age_days` that are not yet in the state file (`%APPDATA%\Botsy\state.json`, keyed by sha256).
  - Upload only files whose header row looks like an EverBee or eRank export: at least 2 known research headers within the first 20 lines. Any other CSV, such as a bank statement, is never uploaded.
  - Upload each with the Windows OpenSSH client: `sftp -b <batch> -i <key> -o BatchMode=yes -o StrictHostKeyChecking=accept-new user@host`, batch `put "<local>" "incoming/<name>.part"` then `rename "incoming/<name>.part" "incoming/<name>"`.
  - Once an hour, `get outbox/search-list.txt` and `outbox/status.txt` into `%USERPROFILE%\Documents\Botsy\`. When the search list's content changes, open it with `os.startfile`.
- The helper never deletes, moves or edits the user's files. Logs go to `%APPDATA%\Botsy\helper.log` (rotating, about 1 MB).
- `install_helper.ps1`:
  - checks that `python` and `sftp` exist;
  - asks for the server address;
  - creates an ed25519 key (`ssh-keygen`) if it is missing and prints the public key with the exact server command that authorizes it;
  - writes the config;
  - registers a Scheduled Task "Botsy helper" (at logon, hidden).
- `-Uninstall` removes the task.
