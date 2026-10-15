# Task State
_Last updated: 2026-10-15 (Phases 0–6 built and pushed; cross-stage audit in progress)_

## Current objective
Finish the cross-stage audit (compliance, real-API wiring, CI, duplication), commit its fixes, then hand the open decisions below to the user.

## Next immediate action
Merge the audit workflow's fixes, run `pytest -q` and the dry-run e2e, commit, push. Then wait for the user's answers under "Open decisions".

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
- Etsy refresh tokens may rotate, so publishing from CI may work for one run only. Publish locally, or add a secret-update step?
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
- [ ] Cross-stage audit fixes merged

## Completed
- 2026-10-15: Phase 0 skeleton, config files, smoke tests; switched image model to gemini-nano-banana-2.1; vectorize toolchain.
- 2026-10-15: Pushed to GitHub. Added `core/` + `docs/CONTRACTS.md`.
- 2026-10-15: Built Phases 1–5 with parallel agents + adversarial review per stage (~720 tests). Built Phase 6 orchestrator + Actions. Dry-run e2e passes for all three niches.

## Deferred until reset
(none)
