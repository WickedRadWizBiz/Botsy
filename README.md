# etsy-agents

Pipeline for our own Etsy digital-download shop: research → brief → generate → QA → listing → **draft** → human approval.
Niches: digital stickers, coloring pages, children's activity sheets.
`CLAUDE.md` holds the compliance rules (no scraping, own shop only, drafts only, no trademarks); `task_state.md` tracks progress and open decisions; `docs/CONTRACTS.md` describes the files each stage reads and writes.

## Setup
Python 3.11 or newer (CI uses 3.12).
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -q -r requirements.txt
cp .env.example .env   # then fill in keys (see the labels in the file)
pytest -q              # offline; about 2 minutes (the end-to-end test is marked slow: -m "not slow" skips it)
```
- **potrace** (coloring pages): Windows `choco install potrace`, macOS `brew install potrace`, Ubuntu `sudo apt install potrace`.
- **rembg** (sticker backgrounds, `isnet-general-use`): downloads a ~170 MB model on first use into `$REMBG_HOME/models` (default `~/.rembg`). To skip it, set `vectorize.stickers.background_removal: floodfill` in `config/settings.yaml`; that suits the plain white backgrounds the prompts ask for.
- Secrets live only in `.env` (gitignored). `.env.example` lists every variable.

## Quick start (offline)
A dry run needs no secrets and makes no API calls: fake images, template listings and a fake Etsy client, on the synthetic exports in `data/samples/`.
```bash
python -m pipeline run --dry-run --inputs data/samples --briefs-dir /tmp/briefs --out /tmp/out --max-items 6
python -m pipeline status --out /tmp/out
```
The same, one stage at a time:
```bash
python -m research --demo --out /tmp/stages/briefs
python -m generators.coloring_pages --brief /tmp/stages/briefs/coloring_pages-<date>.json --dry-run --out /tmp/stages/out
python -m qa --out /tmp/stages/out
python -m listing --dry-run --out /tmp/stages/out
python -m publisher publish --dry-run --out /tmp/stages/out
```

## Where outputs go
| Path | What |
|---|---|
| `data/research_inputs/` | your licensed CSV exports (gitignored, never committed) |
| `data/briefs/<niche>-<date>.json` | aggregated briefs (committed; the weekly Action starts from them) |
| `output/<item_id>/` | `manifest.json`, `files/` (what the buyer downloads), `previews/`, `assets/`, `mockups/` (listing photos), `qa_report.json`, `listing.json`, `publish.json` |
| `output/review/<item_id>.json` | review queue entries |
| `output/runs/run-<date>-<HHMMSS>.json` | pipeline run summaries |
| `output/dry-run/` | everything from a `--dry-run` without `--out` (setting `pipeline.dry_run_output_dir`), including its review queue, runs and research briefs |

`output/` is gitignored. In GitHub Actions it becomes a workflow artifact.

## Pipeline: `python -m pipeline`
```bash
python -m pipeline run [--stages research,generate,qa,listing,publish] [--niche stickers] [--shop main]
                       [--dry-run] [--inputs DIR] [--briefs-dir DIR] [--brief FILE ...] [--out DIR]
                       [--max-items N] [--regenerate] [--date YYYY-MM-DD] [--settings FILE]
python -m pipeline status [--out DIR]
```
- `generate` takes the newest `data/briefs/<niche>-<date>.json` per niche (or the files named with `--brief`), checks each file against the brief rules (patterns from at least 10 listings, no URLs, image names, listing ids or blocked terms) and round-robins across niches up to `--max-items` (default `pipeline.max_items_per_run`, 10).
- Reruns are cheap. An item that already exists for a brief (same shop and `brief_id`, any date) is reused at its current status, so a rerun only retries what failed; reused items count toward `--max-items`. `--regenerate` rebuilds them (new images, new API spend) and resets an earlier approval. Items with a real Etsy draft, or rejected in the review queue, are never rebuilt.
- Without `generate` in `--stages`, the later stages work on every item in `--out`, filtered by `--niche` / `--shop`; `--max-items` then caps the listings written. A real `--stages listing,publish` run also re-lists items whose copy the publisher would refuse as stale (dry-run template text, or written before the shop name was set).
- Live runs skip a niche's newest brief file when it is older than `pipeline.max_brief_age_days` (6 days), so the weekly Action drafts each committed brief file only once. Files named with `--brief` are exempt.
- Exit status: `0` every stage that ran succeeded (items waiting for review count as success), `1` an item, brief or stage failed, `2` setup error (missing secret, no Etsy tokens, safety-layer refusal). A setup error stops that stage instead of being retried once per item.

## Research inputs
Download CSV exports from eRank, EverBee or a similar licensed tool into `data/research_inputs/`. They are licensed data: they stay on your machine (gitignored) and are never committed or passed to a model.
- Keyword and listing exports are recognized by their column names (`research/columns.py`; override them in `research.column_overrides`). A keyword export needs a competition (matching listings) column to count toward a brief; without one it produces no briefs and research logs a warning.
- Name a file `<niche>__anything.csv` (for example `stickers__erank.csv`) to force its niche. Otherwise each row's niche comes from `research.niche_rules`.
- `own_shop*.csv` holds our own shop's stats (`python -m publisher own-stats` writes one). `trends*.csv` holds off-Etsy trend signals. Both only boost demand and are never counted as listings.

`python -m research [--niche N] [--inputs DIR] [--out DIR] [--date D] [--dry-run] [--demo]` writes `data/briefs/<niche>-<date>.json`. A brief pattern needs at least 10 listings from at least 3 different shops (`research.min_shops_per_pattern`), and briefs contain no shop names, listing URLs, images or verbatim titles. Commit the brief files.

## Stage commands
Each stage runs on its own. The generator, QA, listing and `publisher publish` commands take `--out DIR` (default `output/`; with `--dry-run`, `output/dry-run/`) and `--settings FILE`. For `research`, `--out` is the briefs folder.
| Command | What it does |
|---|---|
| `python -m research ...` | exports → briefs (see above) |
| `python -m generators.activity_sheets --brief FILE [--index N \| --all] [--dry-run]` | procedural mazes, word searches, tracing, dot-to-dot (no image model) |
| `python -m generators.coloring_pages --brief FILE [--index N \| --all] [--dry-run]` | image API → potrace → PDF (Letter and A4) |
| `python -m generators.stickers --brief FILE [--index N \| --all] [--dry-run]` | image API → transparent PNGs (ZIP) + sticker sheet PDFs |
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
3. **Taxonomy ids.** `python -m publisher taxonomy --search sticker` (and `coloring`, `activity`). Put the most specific ids under `listing.taxonomy_ids` in `config/settings.yaml`; Etsy needs one to create a draft.
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

## Layout
| Folder | Role |
|---|---|
| `research/` | Parse licensed research exports in `data/research_inputs/` → aggregated briefs in `data/briefs/` |
| `generators/` | Activity sheets (procedural), coloring pages and stickers (Gemini Nano Banana 2.1) |
| `qa/` | Automated checks and mockups, then the review queue in `output/review/` |
| `listing/` | Title, 13 tags, description, alt text, AI disclosure |
| `publisher/` | Etsy Open API v3, own shop, drafts only |
| `pipeline/` | Runs the stages in order; `python -m pipeline` |
| `core/` | Shared settings, compliance (blocklist, AI disclosure, brief checks), Etsy limits and item helpers |
| `config/` | `settings.yaml`, trademark blocklist, AI disclosure |
| `data/samples/` | synthetic exports for dry runs and CI |
