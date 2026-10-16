# Task State
_Last updated: 2026-10-16 (phase 3 packs build + expansion research running)_

## Current objective
Run Botsy 24/7 on Lightsail. Done and pushed: server service, weekly search list, Windows helper, deploy kit (phase 1); refined research stage, evidence window, spend tracker, events + Discord + daily log, theme cooldown, settings.local.yaml, disk cleanup (phase 2, 1451 tests). Next: phase 3 themed packs + stationery + TTRPG niches + low-competition scoring (docs/PACKS.md).

## Next immediate action
- POD + KDP research running: run `wf_e468ebde-ec0` (script botsy-pod-kdp-research-wf_e468ebde-ec0.js). Then write docs/POD.md and docs/KDP.md specs and confirm priorities with the user (overseer vs POD vs KDP vs digital planners).
Two workflows running (restarted after a container restart on 2026-10-16):
- packs build, run `wf_bbcab72a-afa`, script `botsy-packs-build-wf_54bb3bbf-a07.js` (spec docs/PACKS.md). Uncommitted partial work from the interrupted run is in research/ and generators/packs/; the new run picks it up.
- expansion research, run `wf_5db68d9c-22f` (resumed; Hermes + Fiverr results cached, passive-income + synthesis rerunning).
If the container restarts again: resume with Workflow(scriptPath=..., resumeFromRunId=...). When done: verify + commit packs build; report research + architecture recommendation to the user.

## Decisions made
- Image API: Google Gemini `gemini-nano-banana-2.1` via `generate_content` (Gemini API route; Vertex optional).
- Shops: one shop (`main`) covering stickers, coloring pages, activity sheets.
- Repo: WickedRadWizBiz/Botsy, branch `claude/zen-newton-w7xzq9`.
- Text model: `claude-opus-5-5`, max_tokens 16000, effort low, structured output.
- Research CSVs: one parser recognizing eRank and EverBee columns via aliases (`research/columns.py`; override with `research.column_overrides`). Research is deterministic, no model calls.
- Shared code in `core/`; stage data formats in `docs/CONTRACTS.md`.
- Vectorizing: potrace (coloring), rembg `isnet-general-use` or floodfill (sticker backgrounds), OpenCV cleanup. Recraft stays disabled.
- Licensed research exports stay local; briefs (aggregated, compliant) are committed and the weekly Action starts from them.
- Weekly Action is dry-run unless repo variable `PIPELINE_LIVE=true`; drafts only when `PIPELINE_PUBLISH=true`.
- Flagged (`?term`) items and QA warnings wait in `output/review/` for `python -m qa review approve <id>` before drafting; clean items go straight to draft (user approves every draft in Etsy).
- Listings always include the AI disclosure (`listing.always_disclose_ai: true`), since the copy itself is AI-written.
- Blocklist: added ~36 franchise terms, 5 review terms, 11 app/platform brands as review terms (2026-10-15).

## Open decisions (ask the user)
- Server build, still to check for real: `setup.sh` was tested by hand on Ubuntu's OpenSSH and systemd in a mount namespace, but not yet on a real Lightsail instance. Its `--upload-pubkey` fast path (added at integration) is tested only with shims. The Windows helper and `install_helper.ps1` have not run on a real Windows PC yet (tests use Linux, a fake sftp and OpenSSH's `sftp -D`).
- `deploy/lightsail/README.md` names the branch `claude/zen-newton-w7xzq9`; change it there if the server should follow another branch (for example `main`).
- The weekly GitHub Action and the server can both create drafts from the same briefs. Once the server is live, keep `PIPELINE_PUBLISH` off (or drop the weekly Action) so only one of them drafts.
- `listings_d` scope (lets `python -m publisher discard` delete our own failed drafts): keep or drop?
- Primary listing photo: fanned-pages hero (current) or single-page close-up first?
- Tracing font: Helvetica (current) or embed a school-style font (e.g. Andika, OFL)?
- Optional: research could sign brief files (HMAC) so hand-edited briefs are refused.
- Research skips keyword-level exports with no competition column (stricter); an opt-in setting could count them as keyword evidence.

## Lightsail plan (proposed 2026-10-16; built 2026-10-16, see Completed)
- The user wants the pipeline on a Lightsail server running 24/7, doing market research continuously, with a stricter research bar after the first batch.
- On a server the Etsy token file persists, so refresh-token rotation stops being a problem; the weekly GitHub Action becomes optional (CI tests stay).
- Limit: competitor data can only come from licensed exports the user downloads (no official eRank/EverBee research API found 2026-10-16; scraping Etsy or using the Etsy API on other shops is forbidden). The server automates everything else: watch an inbox for new exports, pull our own shop stats daily (Etsy API, allowed), optional trend data, keep research history, re-score, gate candidates, generate, list, draft.
- Two-stage research: "discovery" (current rules) for the first batch, then "refined" (e.g. 25+ listings, 3+ shops, seen in 3+ weekly snapshots, demand not falling, our own sales of similar items considered) before a candidate is generated.

## Decisions made 2026-10-16
- (latest) POD provider: PRINTFUL (user already has an account). Flow to build: Botsy creates the Etsy physical DRAFT itself via the Etsy API (production partner Printful, shipping profile) + print files at Printful print-area sizes + Printful Mockup Generator API; Printful imports Etsy drafts (daily); variants linked to Printful products/print files via Printful API if it supports Etsy-store sync variants, else the user links them in Printful. Verify in the POD build with one sticker. Printify dropped.
- (latest) User agrees with all other POD/KDP defaults; questions "no kids' physical products": kept off until Printful confirms in writing it provides a Children's Product Certificate and CPSIA tracking labels for its decorated kids' items (draft support question given to user). Digital kids' products and KDP kids' books unaffected.
- (latest) KDP research tool: BookBeam (user will send a sample export; instructions given). Later: extend research column aliases + Windows helper header sniff to BookBeam exports.
- (latest) Pinterest: kept in the backlog, not the main focus. "Copy them" meant exactly what Botsy already does (original designs from selling themes). Scraping rule now per platform: check terms + robots.txt, record in config/platform_policies.yaml; Etsy/Amazon/Pinterest/Fiverr forbid it.
- (latest) Build order (user: "after is fine per your suggestion"): 1) finish packs build, 2) embedded fonts + WATCHDOG (deterministic 24/7 health checks/auto-retry/alerts, added to answer "can Hermes oversee if last?"), 3) KDP coloring_50 then activity_100 then undated planner, 4) Etsy POD after a one-sticker Printify test, 5) Hermes overseer + safe menu (plugs into the watchdog).
- (latest) Recommended defaults adopted pending user objection: Printful fallback if Printify publishes live; US-only POD; opt out of Offsite Ads under $10k/yr; free shipping built into price; no kids' physical products or phone cases; KDP RPG books system-neutral; disclose AI on KDP conservatively; one Etsy design set may seed one KDP book; one printed proof per new KDP template; Liberation Sans embedded font. KDP research tool: BookBeam recommended, user to choose.
- Overseer (user approved the recommended plan, docs/EXPANSION.md section 1): Nous Research Hermes Agent on the Lightsail server, own `hermes` Linux user, private Discord chat (allowlisted to the user), pinned version; it can only call a safe menu (`botsy-control`: status, spend, review list/approve/reject, set cap, pause/resume, run now, daily log) through a Botsy MCP server; confirm codes for cap raises and approvals; $100 cap ceiling only root can change; Hermes on its own $5/month Anthropic workspace limit; later a "Botsy" tab in the Hermes dashboard (cap box + Save, pause switch, review queue). Spec: docs/OVERSEER.md.
- Fiverr-Botsy: skipped (user).
- After the pack build (user): hyperlinked digital planners, then Pinterest pins via the official API (user approves each pin), then KDP interiors (manual upload).
- UPDATE (user, 2026-10-16, later): focus on Etsy and KDP. Pinterest dropped. New: Etsy print-on-demand physical products made by a third-party printer. User asked to "look for items selling now, essentially copy them and augment slightly"; answered that Botsy follows selling THEMES/product types across many shops and makes original designs (copying a specific item breaks copyright and CLAUDE.md). Overseer priority vs POD/KDP to confirm with the user. POD + KDP research workflow started.
- CLAUDE.md rule extended: no scraping or browser automation on ANY marketplace/platform (user).
- Spending cap: $25/month default in settings.yaml (user). User wants to adjust it easily "in the UI" -> part of the control-center work.
- Pack ratio confirmed via my recommendation (master fresh; 2 derived : 1 fresh for 5-packs; 5 derived : 1 fresh for 20-packs), plus listings state page overlap between packs.
- Next big asks (user): a StarNet-like control center with a central "Hermes" AI overseeing specialist agents (Botsy = one specialist); a Fiverr digital-download version of Botsy; a ranked list of other bot/agent passive-income avenues. Research workflow running.
- Themed packs (user): every theme -> 5-page pack, 20-page pack, plus the top sellers' pack size; each pack its own listing. Biggest pack = fresh master set; smaller packs are curated subsets, except every 3rd 5-pack and every 6th 20-pack get fresh pages (interpretation of "one fresh pack for every 2 five-page and 5 twenty-page packs"; confirm). Spec: docs/PACKS.md.
- New niches (user): stationery (ADHD/productivity planners etc.) and tabletop RPG papers, in shop `main` for now. Prefer niche, low-competition themes (opportunity score).
- RPG listings may name the game system ("for D&D 5e") as a compatibility reference with an unofficial/not-affiliated line (user). CLAUDE.md exception added; `!term` blocklist marker. No medical claims for ADHD stationery.
- Refined research stage (user): starts automatically after 20 real Etsy drafts. Needs 15+ listings from 3+ shops, theme seen in 3+ weekly exports, demand not falling, our own sales weighted in. Lightsail 4 GB plan OK.
- Next build (user approved): Discord notifications (webhook), spending tracker with monthly cap, overnight log (daily digest to outbox + Discord). Ideas taken from androoAGI/starnet (MIT); StarNet itself is a general agent desktop app, not an Etsy pipeline.
- Hard-blocked common words stay hard-blocked (user: fine as is).
- Competitor tags shared by 10+ listings may be used as brief keywords (user: OK).
- Duplicate Etsy-limit settings keys removed; limits live only in `core/etsy.py`.
- Exports cannot be automated within the rules (EverBee runs on etsy.com pages; no official EverBee/eRank API). User approved instead (2026-10-16): a weekly search list the server writes, and a Windows helper (user is on Windows) that uploads EverBee/eRank CSVs from Downloads automatically. Transport: SFTP to an upload-only chroot account (Windows has OpenSSH built in). Spec: `docs/SERVER.md`.
- `research.min_shops_per_pattern: 3` (user): listing-backed themes, tags and patterns need 3+ different shops (hashed in memory, only the count is written). Applies from the first batch.

## Waiting on the user
- [ ] Shop display name in `config/settings.yaml` (`YOUR_SHOP_NAME`; real drafts are blocked until set) and default prices.
- [ ] Register an Etsy developer app (personal use, own shop) with redirect `https://localhost:3003/callback`; run `python -m publisher auth`.
- [ ] Taxonomy ids: `python -m publisher taxonomy --search sticker` (and coloring, activity) → `listing.taxonomy_ids` in settings.
- [ ] One real eRank and one real EverBee export in `data/research_inputs/` to confirm column aliases.
- [ ] One BookBeam export (coloring, activity, planner searches) to map its columns.
- [ ] Ask Printful support about CPSIA documents for kids' items.
- [ ] One live smoke test with `GOOGLE_API_KEY` (confirm the model accepts `generate_content` + `image_size: 4K`).
- [ ] GitHub secrets for live runs: ANTHROPIC_API_KEY, GOOGLE_API_KEY, ETSY_API_KEYSTRING, ETSY_SHARED_SECRET, ETSY_SHOP_ID, ETSY_REFRESH_TOKEN.

## Phases
- [x] Phase 0 — Setup (pushed 2026-10-15)
- [x] Phase 1 — Research: `research/` (CSV aliases, niche rules, n-gram themes, 10-listing guard, briefs)
- [x] Phase 2 — Generators: `generators/activity_sheets`, `generators/coloring_pages`, `generators/stickers`, shared `generators/imaging`
- [x] Phase 3 — QA + mockups + review queue: `qa/`
- [x] Phase 4 — Listing agent: `listing/`
- [x] Phase 5 — Publisher (drafts only): `publisher/`
- [x] Phase 6 — `pipeline/` orchestrator + `.github/workflows/weekly.yml` and `ci.yml`
- [x] Cross-stage audit fixes integrated and pushed (2026-10-15, commit a07efb1)

## Completed
- 2026-10-15: Phase 0 skeleton, config files, smoke tests; switched image model to gemini-nano-banana-2.1; vectorize toolchain.
- 2026-10-15: Pushed to GitHub. Added `core/` + `docs/CONTRACTS.md`.
- 2026-10-15: Built Phases 1–5 with parallel agents + adversarial review per stage (~720 tests). Built Phase 6 orchestrator + Actions. Dry-run e2e passes for all three niches.
- 2026-10-15: Integrated the audit fixes: `core/briefs.py` (brief check shared by research, generators, pipeline), `core/etsy.py` (Etsy limits), `core.items.content_count` / `stage_out_root` / `clear_downstream` (drops dry-run publish.json, reopens approvals on regeneration); publisher: `PublishGateError.problems`, `ReviewPending`, `review_wait()`, content-digest approval check, own-shop title dedupe, title/tag limits from settings, refresh-token age warning (`refresh_issued`); listing: `needs_listing()` re-lists stale copy (old shop name / placeholder); stage CLIs default `--dry-run` to `output/dry-run`; new `tests/test_pipeline.py`; CONTRACTS.md and README rewritten to match.

- 2026-10-16: Server build (docs/SERVER.md): `service/` (`python -m service` ingest/research/run/searchlist/tick/status, lock, settle wait, rounds, status.txt/status.json, outbox scrubbing and secret redaction), `research/history.py` (history snapshots, `--history`), weekly search list, `deploy/lightsail/` (`setup.sh` installed as root-owned `botsy-setup`, root's own copy in /usr/local/lib/botsy/src, chroot SFTP account, hardened systemd units), `windows/` (stdlib helper + installer). Integration: settings blocks (`research.history*`, `service:`), `botsy-setup` command everywhere, `--upload-pubkey` fast path, distinct brief-file counts in the weekly totals, `tests/service/test_end_to_end.py` (uploads -> tick -> helper -> tick -> search list -> PC), README "Run it 24/7 on Lightsail", docs/SERVER.md updated to match the build.

## Deferred until reset
(none)
