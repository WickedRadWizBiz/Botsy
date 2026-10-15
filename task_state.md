# Task State
_Last updated: 2026-10-15 (Phases 0–6 built and pushed; cross-stage audit fixes integrated, not yet committed)_

## Current objective
Commit and push the integrated cross-stage audit fixes, then hand the open decisions below to the user.

## Next immediate action
User: review the working-tree diff, then commit and push (883 tests pass, `pytest -q`; dry-run e2e exits 0 for all three niches and is idempotent on rerun). Then answer "Open decisions".

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
- Hard-blocked common words (Cars, Frozen, Stitch, Woody, Goofy, Trolls, Peanuts, Creeper, Highlights, Inside Out) reject generic themes like "race cars" or "cross stitch". Move some to `?review`? (Loosening needs the user's OK.)
- Research uses competitor tags as brief keywords when 10+ distinct listings share them; those keywords reach the listing model. OK, or keyword-tool phrases only?
- Add `min_shops_per_pattern` (10 listings from one shop currently satisfy the rule)?
- Etsy refresh tokens may rotate, so publishing from CI may work for one run only. The weekly job now has a write-back step: create the `SECRETS_WRITE_TOKEN` fine-grained token (this repo, Secrets + Variables read/write) and set the repo variable `ETSY_REFRESH_TOKEN_ISSUED` (YYYY-MM-DD) for it, or keep publishing locally.
- Keep the repo variable `PIPELINE_PUBLISH` off until the item above is set up.
- `pipeline.max_brief_age_days: 6` now gates live runs: a niche's newest brief file older than 6 days fails the run (files named with `--brief` are exempt). Keep 6, change, or `null` to switch off?
- Optional: a pinned constraints file for the weekly job's `pip install` (reproducible installs vs. picking up fixes)?
- Research no longer counts rows of a keyword-level export without a competition (matching listings) column, so such files alone produce no briefs (a warning is logged). To keep them usable, approve an explicit opt-in setting first; their rows would then count as keyword evidence, not `listing_count`.
- Etsy limits now live once in `core/etsy.py`. The duplicate settings keys (`qa.etsy_max_files`, `qa.etsy_max_file_bytes`, `publisher.max_files`, `publisher.max_file_bytes`) still work and default to it. Remove them from `settings.yaml`?
- Optional: research could sign brief files (HMAC with a secret from `.env`) so generators and the pipeline refuse hand-edited briefs; without the licensed exports they cannot detect competitor shop names or verbatim titles in a committed brief. Worth adding?
- Publisher dedupe across CI checkouts matches our own drafts/active listings by exact title (case/space-insensitive). A rerun whose listing copy got a different title is not caught. Enough, or also keep a brief_id → listing_id record outside the runner (e.g. the previous run's artifact)?
- `listings_d` scope (lets `python -m publisher discard` delete our own failed drafts): keep or drop?
- Primary listing photo: fanned-pages hero (current) or single-page close-up first?
- Tracing font: Helvetica (current) or embed a school-style font (e.g. Andika, OFL)?

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
- [x] Cross-stage audit fixes integrated (2026-10-15; awaiting commit)

## Completed
- 2026-10-15: Phase 0 skeleton, config files, smoke tests; switched image model to gemini-nano-banana-2.1; vectorize toolchain.
- 2026-10-15: Pushed to GitHub. Added `core/` + `docs/CONTRACTS.md`.
- 2026-10-15: Built Phases 1–5 with parallel agents + adversarial review per stage (~720 tests). Built Phase 6 orchestrator + Actions. Dry-run e2e passes for all three niches.
- 2026-10-15: Integrated the audit fixes: `core/briefs.py` (brief check shared by research, generators, pipeline), `core/etsy.py` (Etsy limits), `core.items.content_count` / `stage_out_root` / `clear_downstream` (drops dry-run publish.json, reopens approvals on regeneration); publisher: `PublishGateError.problems`, `ReviewPending`, `review_wait()`, content-digest approval check, own-shop title dedupe, title/tag limits from settings, refresh-token age warning (`refresh_issued`); listing: `needs_listing()` re-lists stale copy (old shop name / placeholder); stage CLIs default `--dry-run` to `output/dry-run`; new `tests/test_pipeline.py`; CONTRACTS.md and README rewritten to match.

## Deferred until reset
(none)
