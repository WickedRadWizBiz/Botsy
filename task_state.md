# Task State
_Last updated: 2026-10-16 (server build integrated and tested, not committed yet; next: commit + push, deploy, then refined stage, Discord, spend tracker, overnight log)_

## Current objective
Run Botsy 24/7 on Lightsail: server service + weekly search list + Windows upload helper (built and integrated 2026-10-16), then refined research stage, Discord notifications, spend tracker, overnight log.

## Next immediate action
Review the uncommitted server build (`service/`, `deploy/lightsail/`, `windows/`, `research/history.py`, tests, settings, README, docs/SERVER.md), commit and push. Then the user sets up the server (`deploy/lightsail/README.md`) and the PC (`windows/README.md`). Then launch the second build (refined stage, Discord, spend, overnight log) from the spec drafted in the scratchpad.

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
