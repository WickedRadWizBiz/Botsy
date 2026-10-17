# etsy-agents

Pipeline for our own Etsy digital-download shop: research → brief → generate → QA → listing → **draft** → human approval.
Niches: digital stickers, coloring pages, children's activity sheets, stationery (planner and productivity pages, including ADHD-friendly layouts) and tabletop RPG sheets. Every product is a themed pack (5 pages, 20 pages, plus the size top sellers use), each its own listing (`docs/PACKS.md`).
`CLAUDE.md` holds the compliance rules (no scraping, own shop only, drafts only, no trademarks); `task_state.md` tracks progress and open decisions; `docs/CONTRACTS.md` describes the files each stage reads and writes.

## Setup
Python 3.11 or newer (CI uses 3.12).
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -q -r requirements.txt
cp .env.example .env   # then fill in keys (see the labels in the file)
pytest -q              # offline; about 2 minutes (the end-to-end tests are marked slow: -m "not slow" skips them)
```
- **potrace** (coloring pages): Windows `choco install potrace`, macOS `brew install potrace`, Ubuntu `sudo apt install potrace`.
- **rembg** (sticker backgrounds, `isnet-general-use`): downloads a ~170 MB model on first use into `$REMBG_HOME/models` (default `~/.rembg`). To skip it, set `vectorize.stickers.background_removal: floodfill` in `config/settings.yaml`; that suits the plain white backgrounds the prompts ask for.
- Secrets live only in `.env` (gitignored). `.env.example` lists every variable, including the optional `DISCORD_WEBHOOK_URL` (server guide `deploy/lightsail/README.md`, steps 11-13).

## Quick start (offline)
A dry run needs no secrets and makes no API calls: fake images, template listings and a fake Etsy client, on the synthetic exports in `data/samples/`.
```bash
python -m pipeline run --dry-run --inputs data/samples --briefs-dir /tmp/briefs --out /tmp/out --max-items 10
python -m pipeline status --out /tmp/out
```
The same, one stage at a time:
```bash
python -m research --demo --out /tmp/stages/briefs
python -m generators.coloring_pages --brief /tmp/stages/briefs/coloring_pages-<date>.json --dry-run --out /tmp/stages/out
python -m generators.ttrpg --brief /tmp/stages/briefs/ttrpg-<date>.json --dry-run --out /tmp/stages/out
python -m qa --out /tmp/stages/out
python -m listing --dry-run --out /tmp/stages/out
python -m publisher publish --dry-run --out /tmp/stages/out
```

## Where outputs go
| Path | What |
|---|---|
| `data/research_inputs/` | your licensed CSV exports (gitignored, never committed) |
| `data/briefs/<niche>-<date>.json` | aggregated briefs (committed; the weekly Action starts from them) |
| `output/<item_id>/` | one pack: `manifest.json`, `files/` (what the buyer downloads), `previews/`, `assets/`, `mockups/` (listing photos), `qa_report.json`, `listing.json`, `publish.json` |
| `output/sets/<set_id>/` | design sets: the pages made for a theme, which the theme's packs are cut from |
| `data/packs_state.json` | pack counters (which pack of a size is fresh) for `output/` (gitignored; dry runs and `--out` keep theirs in `<out>/sets/`) |
| `output/review/<item_id>.json` | review queue entries |
| `output/runs/run-<date>-<HHMMSS>.json` | pipeline run summaries |
| `output/dry-run/` | everything from a `--dry-run` without `--out` (setting `pipeline.dry_run_output_dir`), including its review queue, runs and research briefs |
| `data/history/<date>/<niche>.json` | research history snapshots (server; gitignored, never sent to a model) |
| `data/inbox_rejected/` | uploads the server did not use, each with a `.reason.txt` (server; gitignored) |
| `data/spend/ledger.jsonl` | estimated cost of every paid API call (gitignored) |
| `data/events.jsonl` | what the server did, one line per event; the daily log is built from it (server; gitignored) |

`output/` is gitignored. In GitHub Actions it becomes a workflow artifact.

## Pipeline: `python -m pipeline`
```bash
python -m pipeline run [--stages research,generate,qa,listing,publish] [--niche stickers] [--shop main]
                       [--dry-run] [--inputs DIR] [--briefs-dir DIR] [--brief FILE ...] [--out DIR]
                       [--max-items N] [--regenerate] [--date YYYY-MM-DD] [--settings FILE]
python -m pipeline status [--out DIR]
```
- `generate` takes the newest `data/briefs/<niche>-<date>.json` per niche (or the files named with `--brief`), checks each file against the brief rules (patterns from at least 10 listings, no URLs, image names, listing ids, blocked terms or game-system names) and makes each brief's packs, round-robin across niches, up to `--max-items` packs (default `pipeline.max_items_per_run`, 10): first one pack (the master) of every niche's first brief, then their smaller packs, then the next briefs.
- Reruns are cheap. An item that already exists for a brief (same shop and `brief_id`, any date) is reused at its current status, so a rerun only retries what failed; reused items count toward `--max-items`. `--regenerate` rebuilds them (new images, new API spend) and resets an earlier approval. Items with a real Etsy draft, or rejected in the review queue, are never rebuilt.
- Without `generate` in `--stages`, the later stages work on every item in `--out`, filtered by `--niche` / `--shop`; `--max-items` then caps the listings written. A real `--stages listing,publish` run also re-lists items whose copy the publisher would refuse as stale (dry-run template text, or written before the shop name was set).
- Live runs skip a niche's newest brief file when it is older than `pipeline.max_brief_age_days` (6 days), so the weekly Action drafts each committed brief file only once. Files named with `--brief` are exempt.
- **No duplicates.** A pack is skipped when the shop already has an item of the same theme and pack size made in the last 60 days, or one with an Etsy draft at any age (`pipeline.theme_cooldown_days`). `--regenerate` lifts the 60 days, never a real draft.
- Exit status: `0` every stage that ran succeeded (items waiting for review count as success; so does a run stopped by the spending cap), `1` an item, brief or stage failed, `2` setup error (missing secret, no Etsy tokens, safety-layer refusal, a spending log that cannot be written). A setup error stops that stage instead of being retried once per item.

## Themed packs
A brief becomes several packs of one theme, each its own Etsy listing ("Summer Coloring Pages, 20 Pages", "Set of 5"). Details: `docs/PACKS.md`.
- **Sizes.** Research gives every brief 5 and 20 pages plus the size the theme's top sellers use (`pack_sizes`), within `packs.max_size` per niche.
- **Master, derived, fresh.** The largest size gets new pages: the theme's master set. Smaller packs are cut from it ("derived"), except every 3rd 5-page pack and every 6th 20-page pack of a niche, which get all-new pages ("fresh", `packs.fresh_every`). The listing says so plainly: "These 5 pages are also included in our 20-page ... pack", or "All-new pages, not included in our other packs".
- **Costs.** Derived packs cost no new images. Reruns reuse the design sets, so nothing is paid for twice.
- **Photos.** The cover photo shows the pack's pages with a "20 PAGES" / "SET OF 5" badge; planner and RPG packs also get a desk photo.
- **Prices.** Every pack of a theme has the same price for now (the theme's median, clamped around `shops[].default_price_usd`); a per-size rule is still to be decided.

## Tabletop RPG sheets and game-system names
RPG sheets are our own layouts, printed without any game-system name. A listing may name the system the sheets are made for as a compatibility reference only, for example "Character Sheet for D&D 5e" (CLAUDE.md exception, user, 2026-10-16):
- only in that ttrpg listing's title, tags and description, and only when research found the system in at least half of the theme's rows (the brief then carries the system's id, such as `dnd5e`, never its name);
- never in designs, image prompts, printed pages, file names, photos or alt text, and never in any other niche;
- always with "Made for use with D&D 5e. This is an unofficial product of our own design." and the owner's disclaimer from `config/ttrpg_systems.yaml` ("Unofficial fan-made product, not affiliated with or endorsed by Wizards of the Coast. ...");
- never logos, official art, settings or product-identity creatures (these are hard-blocked), and never anything that suggests the product is official.

The system names are `!term` lines in `config/trademark_blocklist.txt`; QA and the publisher refuse an item that breaks any of these rules. Stationery listings may say "ADHD-friendly" to describe a layout, never a medical claim (treat, cure, therapy, ...).

## Research inputs
Download CSV exports from eRank, EverBee or a similar licensed tool into `data/research_inputs/`. They are licensed data: they stay on your machine (gitignored) and are never committed or passed to a model.
- Keyword and listing exports are recognized by their column names (`research/columns.py`; override them in `research.column_overrides`). A keyword export needs a competition (matching listings) column to count toward a brief; without one it produces no briefs and research logs a warning.
- Name a file `<niche>__anything.csv` (for example `stickers__erank.csv`) to force its niche. Otherwise each row's niche comes from `research.niche_rules`.
- `own_shop*.csv` holds our own shop's stats (`python -m publisher own-stats` writes one). `trends*.csv` holds off-Etsy trend signals. Both only boost demand and are never counted as listings.

`python -m research [--niche N] [--inputs DIR] [--out DIR] [--date D] [--history DIR] [--stage auto|discovery|refined] [--dry-run] [--demo]` writes `data/briefs/<niche>-<date>.json` (and, with `--history`, a history snapshot per niche in `DIR/<date>/`). A brief pattern needs at least 10 listings from at least 3 different shops (`research.min_shops_per_pattern`), and briefs contain no shop names, listing URLs, images or verbatim titles. Commit the brief files.
- Only exports received in the last 35 days count (`research.window_days`). Older ones are ignored, and the server deletes them after 180 days (`research.keep_days`).
- **Low competition first.** Briefs are ranked by opportunity: demand, sales per listing and low competition (`research.opportunity_weights`). Each brief records its pack sizes (5, 20 and the top sellers' size).
- **Cut words.** Game-system names, planner and virtual-tabletop brands, rulebook titles and medical-claim words are cut from themes and keywords in every niche; a ttrpg brief names its system only by id.
- **Refined stage.** Once the shop has 20 real Etsy drafts, research gets stricter by itself (`research.stage: auto`): a theme then needs 15 listings from 3 shops, must show up in exports from 3 different weeks, and its demand may not be falling. Our own sales count for more. The first stage, "discovery", uses the rules above. Settings: `research.stage` and `research.stages.refined`.

## Stage commands
Each stage runs on its own. The generator, QA, listing and `publisher publish` commands take `--out DIR` (default `output/`; with `--dry-run`, `output/dry-run/`) and `--settings FILE`. For `research`, `--out` is the briefs folder.
| Command | What it does |
|---|---|
| `python -m research ...` | exports → briefs (see above) |
| `python -m generators.activity_sheets --brief FILE [--index N \| --all] [--dry-run]` | procedural mazes, word searches, tracing, dot-to-dot (no image model) |
| `python -m generators.coloring_pages --brief FILE [--index N \| --all] [--dry-run]` | image API → potrace → PDF (Letter and A4) |
| `python -m generators.stickers --brief FILE [--index N \| --all] [--dry-run]` | image API → transparent PNGs (ZIP) + sticker sheet PDFs |
| `python -m generators.stationery --brief FILE [--index N \| --all] [--dry-run]` | procedural planner and productivity pages (Letter and A4) |
| `python -m generators.ttrpg --brief FILE [--index N \| --all] [--dry-run]` | procedural tabletop RPG sheets (Letter and A4) |
| `python -m qa [--item ID] [--no-mockups]` | automated checks, listing photos, review queue entry; exits 1 when an item fails |
| `python -m listing [--item ID] [--dry-run]` | title, 13 tags, description, alt text, AI disclosure; exits 2 without `ANTHROPIC_API_KEY` in a real run |
| `python -m publisher publish [--item ID] [--dry-run]` | Etsy **drafts** for listed items (never activates); a dry run on real items (`--out output`) only previews |
| `python -m publisher discard --item ID` | delete this tool's draft for an item and start over |
| `python -m publisher taxonomy --search sticker [--json]` | find Etsy taxonomy ids |
| `python -m publisher own-stats [--dry-run]` | our own listings' stats as a research input |

`--item` takes an item id inside `--out` or a path to an item folder. `python -m listing --item ID` also rewrites a `listed` item (for example after you set the shop name).

## Review queue
QA queues every passing item in `output/review/<item_id>.json` (`<out>/review/` for another `--out`). Items flagged for review (a `?term` from `config/trademark_blocklist.txt`, a QA warning, a generator reason, a listing review term) are drafted only after you approve them. Clean items go straight to draft, because you approve every draft in Etsy anyway.
```bash
python -m qa review list [--status pending|approved|rejected] [--out DIR]
python -m qa review approve ID [--note "..."] [--out DIR | --review-dir DIR]
python -m qa review reject ID --reason "..." [--out DIR]
```
- An approval covers the reasons and the files and photos you saw. A QA rerun that finds a new reason or changed content, new listing review terms, or a regeneration puts the entry back to `pending`.
- A rejection is final: the pipeline never lists or rebuilds a rejected item.
- The publisher's refusal message prints the exact approve command (with `--out` when needed).

## Etsy setup
1. **App.** Register an app at [developer.etsy.com](https://developer.etsy.com) for personal use on our own shop. Add a callback URL exactly equal to `ETSY_REDIRECT_URI` (default `https://localhost:3003/callback`). Put the keystring and shared secret in `.env` (`ETSY_API_KEYSTRING`, `ETSY_SHARED_SECRET`).
2. **OAuth.** Run `python -m publisher auth`. It prints Etsy's connect URL for you to open (it never drives a browser). Etsy requires an https redirect, so either:
   - give the local listener a certificate: `openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 30 -subj /CN=localhost`, then `python -m publisher auth --cert cert.pem --key key.pem` (`*.pem` is gitignored); or
   - use `python -m publisher auth --paste` and paste the redirected URL from the address bar (the page itself may fail to load).

   Tokens go to `.etsy_tokens.json` (mode 600, gitignored), with the date the refresh token was issued; the publisher warns from day 80 (refresh tokens last 90 days). The command prints the shop id: set `ETSY_SHOP_ID` in `.env`.
3. **Taxonomy ids.** `python -m publisher taxonomy --search sticker` (and `coloring`, `activity`, `planner`, `role playing`). Put the most specific ids under `listing.taxonomy_ids` in `config/settings.yaml`; Etsy needs one to create a draft.
4. **Shop name and prices.** Set `shops[0].name` in `config/settings.yaml`. Real drafts are refused while it is still `YOUR_SHOP_NAME`, because the AI disclosure shows the shop name. Check `default_price_usd` too.

The publisher only creates drafts, only in our own shop, and refuses any other request (`publisher/safety.py`). Before creating a draft it checks our shop's drafts and active listings for the same title, so a lost `publish.json` does not lead to a second draft.

## GitHub Actions
`.github/workflows/ci.yml` runs the tests and an offline end-to-end dry run on every push and pull request.

`.github/workflows/weekly.yml` runs on Mondays at 13:17 UTC. Scheduled workflows run only from the repository's default branch.
- **Repo variables** (Settings → Secrets and variables → Actions → Variables):
  - `PIPELINE_LIVE`: `true` makes scheduled runs live (real API calls). Otherwise they are dry runs.
  - `PIPELINE_PUBLISH`: `true` lets live runs create Etsy drafts. Keep it off until the next section is set up.
  - `ETSY_REFRESH_TOKEN_ISSUED`: `YYYY-MM-DD` the stored refresh token was issued; the job warns from day 80.
- **Repo secrets:** `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `ETSY_API_KEYSTRING`, `ETSY_SHARED_SECRET`, `ETSY_SHOP_ID`, `ETSY_REFRESH_TOKEN`, and optionally `SECRETS_WRITE_TOKEN` (next section). Only the "Run pipeline" step and the token write-back step see them.
- **Manual runs** (Actions → weekly-batch → Run workflow) have the inputs `dry_run`, `publish`, `niche` and `max_items`.
- **Briefs.** The workflow uses the committed `data/briefs/*.json`. Run `python -m research` locally and commit new brief files before a live run; brief files older than 6 days fail the live run with a message saying so. A dry run with no committed briefs runs research on `data/samples/`.
- **Image route.** CI supports only `models.image.api: gemini` (`GOOGLE_API_KEY`); a live run stops early when the setting is `vertex`.
- **Outputs.** Items go to the artifact `batch-<run_id>` (14 days), intermediates (`assets/`) to `batch-<run_id>-assets` (3 days). The job page shows the run summary; setup errors appear as annotations. Nothing is committed.
- **Flagged items.** A CI run cannot see an approval you give locally. Download and unzip the artifact (say into `batch/`), then run `python -m qa review approve <id> --out batch` and `python -m publisher publish --out batch` on your machine with your local Etsy tokens.

## Etsy drafts from GitHub Actions
The live publish job has no token file. It seeds one from the `ETSY_REFRESH_TOKEN` secret and refreshes it on the first request.
1. Run `python -m publisher auth` locally. Copy `refresh_token` from `.etsy_tokens.json` into the `ETSY_REFRESH_TOKEN` secret and set `ETSY_REFRESH_TOKEN_ISSUED` to today's date.
2. **Rotation caveat.** Etsy's refresh response carries a new refresh token and may invalidate the old one, so a stored secret can be spent after one run. The "Save rotated Etsy refresh token" step writes the new token (and today's date) back after every live publish run, even a failed one. That needs `SECRETS_WRITE_TOKEN`: a fine-grained personal access token for this repository with **Secrets** and **Variables** read/write (`GITHUB_TOKEN` cannot write secrets). Without it, a run that received a new token fails with an error saying the stored secret is probably spent.
3. On `invalid_grant`, or after 90 days, repeat step 1.

Until this is set up, keep `PIPELINE_PUBLISH` off and create drafts locally from the downloaded artifact (`python -m publisher publish --out batch`).

## Run it 24/7 on Lightsail
Botsy can run on its own on a small AWS Lightsail server (Ubuntu 24.04, 4 GB plan). You still make the exports yourself in EverBee or eRank (the rules allow nothing else), but everything after that is automatic:

1. Every Sunday the server writes a **search list**. The Botsy helper on your Windows PC saves it as `Documents\Botsy\search-list.txt` and opens it. Search each line in EverBee (type only the words before the brackets) and click **Export CSV**.
2. The helper notices the new file in your Downloads folder and uploads it to the server. It only ever sends EverBee or eRank exports, never any other CSV.
3. The server checks the upload, researches, makes the items and creates Etsy **drafts** (practice drafts until you turn live mode on). You approve every draft in Etsy.
4. The helper also copies the server's report to `Documents\Botsy\status.txt` every hour, and the **daily log** (`daily-log.txt`: what happened overnight, what is waiting for you, what it cost) every morning.
5. If you want, the server also posts short messages to a **Discord** channel: new drafts, items waiting for your review, problems, the spending cap, the daily log and the weekly search list.

Set it up in this order. Both guides go step by step:
- **The server:** [`deploy/lightsail/README.md`](deploy/lightsail/README.md). It ends with `sudo botsy-setup`, the one command for updating the server later.
- **Your Windows PC:** [`windows/README.md`](windows/README.md). The installer prints one line to paste on the server: `sudo botsy-setup --upload-pubkey "..."`.

The server starts in **test mode** (`service.live: false`): no API calls and no Etsy drafts. The exact behaviour is in `docs/SERVER.md`.

**The server's own settings** live in `/opt/botsy/config/settings.local.yaml` on the server, not on GitHub. Updates never change that file, and it wins over `config/settings.yaml`. Edit it with `sudo -u botsy nano /opt/botsy/config/settings.local.yaml`:
- **Go live:** change `live: false` to `live: true` under `service:` (server guide, step 9).
- **Spending cap:** to cap AI spending, set `spend.monthly_cap_usd` (dollars per month) in config/settings.local.yaml: change `monthly_cap_usd: null` to, for example, `monthly_cap_usd: 25`. Keep the two spaces in front of it so it stays under `spend:` (step 12). At 80% you get a warning; at the cap, new images and listing text wait until next month. `botsy service spend` shows the estimate so far; your Anthropic and Google bills are what count.
- Values that would loosen a compliance rule (fewer than 10 listings or 3 shops per pattern, anything under `compliance:`, `listing.always_disclose_ai: false`) are ignored with a warning.

**Discord (optional):** make a webhook in Discord (channel settings, Integrations, Webhooks, New Webhook, Copy Webhook URL), put it in `/opt/botsy/.env` as `DISCORD_WEBHOOK_URL=...`, then type `botsy service notify-test` (step 11). The link is a secret: it lives only in `.env`. Between 22:00 and 07:00 only urgent messages (problems, the spending cap) are sent; the rest is in the morning's daily log (step 13).

### Server commands: `python -m service`
On the server, type `botsy service <command>`; that runs it as the `botsy` account. Options: `--settings FILE`, `--inbox DIR`, `--date YYYY-MM-DD`.
| Command | What it does |
|---|---|
| `ingest` | take finished uploads out of the inbox; anything that is not an export goes to `data/inbox_rejected/` with a reason |
| `research` | research every shop niche, with history snapshots in `data/history/` |
| `run` | generate, QA, listing and draft for every shop (a dry run unless `service.live` is `true`) |
| `searchlist` | write this week's `search-list.txt` for the helper to fetch |
| `tick` | ingest, then research and run when a new export arrived, then tidy old files, write the status files and send one Discord message (`--force`: run even without a new upload; `--own-stats`: first read our own shop's stats, live mode only) |
| `status` | rebuild `status.txt` and show it |
| `digest [--hours N]` | write `daily-log.txt` (the overnight log) and post it to Discord |
| `spend [--month YYYY-MM]` | this month's estimated API spending |
| `notify-test` | send a test message to Discord and say whether it worked |
| `cleanup` | remove old rejected uploads, old exports and old practice items now (every tick does this too) |

The server runs these by itself: `tick` when an upload arrives, `tick --force --own-stats` every day at 06:17, `digest` every day at 07:07, and `searchlist` every Sunday at 18:07. Exit codes: 0 ok, 1 something failed (see `status.txt`), 2 setup problem, 3 another command was still running after 30 minutes.

## Layout
| Folder | Role |
|---|---|
| `research/` | Parse licensed research exports in `data/research_inputs/` → aggregated briefs in `data/briefs/` |
| `generators/` | The pack engine (`packs/`: design sets, fresh and derived packs), activity sheets, stationery and RPG sheets (procedural), coloring pages and stickers (Gemini Nano Banana 2.1) |
| `qa/` | Automated checks and mockups, then the review queue in `output/review/` |
| `listing/` | Title, 13 tags, description, alt text, AI disclosure |
| `publisher/` | Etsy Open API v3, own shop, drafts only |
| `pipeline/` | Runs the stages in order; `python -m pipeline` |
| `service/` | The 24/7 server: inbox, ticks, status files, weekly search list, daily log, Discord, cleanup; `python -m service` |
| `deploy/lightsail/` | Server setup (`setup.sh`, installed as `botsy-setup`), SSH rules and systemd units |
| `windows/` | The Botsy helper for your PC (`botsy_helper.py`) and its installer |
| `core/` | Shared settings, compliance (blocklist, AI disclosure, brief checks), Etsy limits, item helpers, the spending tracker and the event journal |
| `config/` | `settings.yaml`, trademark blocklist, AI disclosure, game-system registry (`ttrpg_systems.yaml`) |
| `data/samples/` | synthetic exports for dry runs and CI (all five niches; written by `research.demo`) |
