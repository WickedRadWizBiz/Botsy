# Task State
_Last updated: 2026-10-15 (Phase 0 skeleton done)_

## Current objective
Phase 0 — finish by pushing the repo to GitHub. Then Phase 1 (research agent).

## Next immediate action
Push to GitHub (waiting on repo name + auth). Then start Phase 1 once a sample research CSV is in `data/research_inputs/`.

## Decisions made
- Image API: Google Gemini `gemini-nano-banana-2.1` (Nano Banana 2.1, stable; Imagen 4 deprecated). Gemini API route via `generate_content`.
- Shops: one shop (`main`) covering stickers, coloring pages, activity sheets. Shop name placeholder in settings.yaml.
- Repo hosted on GitHub.

## Open decisions (ask the user)
- Which research tool's exports will be used (eRank, EverBee, other). Need one sample CSV to build the parser.
- Shop display name for `config/settings.yaml` (`YOUR_SHOP_NAME`) and default prices (placeholders set).
- Text model id in settings.yaml (`claude-sonnet-4-5` placeholder) — confirm before Phase 1.

## Waiting on the user
- [ ] GitHub repo name + a way to push (cloud session's GitHub token is invalid).
- [ ] Register an Etsy developer app (personal use, own shop) and get it approved.
- [ ] Download a first research export into `data/research_inputs/`.
- [ ] Install potrace locally before Phase 2.

## Phases

### Phase 0 — Setup
- [x] Folders: research/, generators/, qa/, listing/, publisher/, config/, data/research_inputs/, data/briefs/, output/, tests/
- [x] `config/settings.yaml`: shops, niches, models, image provider, page sizes (US Letter + A4)
- [x] `config/trademark_blocklist.txt`: ~170 seed terms; `?term` = flag for review
- [x] `config/ai_disclosure.txt` (uses `{shop_name}` placeholder)
- [x] `.env.example`, `.gitignore`, `requirements.txt`, README.md
- [x] `tests/test_config.py` — 6 smoke tests, all passing
- [ ] Push to GitHub

### Phase 1 — Research agent
- [ ] Parser for research CSV exports → normalized table
- [ ] Aggregate into patterns: themes, formats, page counts, price bands, keyword clusters
- [ ] Output `data/briefs/<niche>-<date>.json`; enforce 10+ listing aggregation and blocklist check

### Phase 2 — Generators
- [ ] Activity sheets (procedural): maze, word search, tracing, dot-to-dot → PDF
- [ ] Coloring pages: Nano Banana 2.1 → cleanup → vector → PDF
- [ ] Stickers: Nano Banana 2.1 → transparent PNGs + sheet (transparency not documented; plan background removal)

### Phase 3 — QA and mockups
- [ ] Checks: 300 DPI, page size, line weight, file size within Etsy's digital-file limits (verify current limits)
- [ ] Listing photo mockups
- [ ] Review queue in `output/review/`

### Phase 4 — Listing agent
- [ ] Title (max 140 chars), 13 tags (max 20 chars each), description, alt text
- [ ] AI disclosure and blocklist check
- [ ] Write `output/<id>/listing.json`

### Phase 5 — Publisher
- [ ] OAuth for own shop; create draft; upload images and digital files
- [ ] Drafts only, never activate

### Phase 6 — Automation
- [ ] GitHub Actions weekly run: research → generate → listing → drafts
- [ ] Upload outputs as workflow artifacts for review

## Completed
- 2026-10-15: Phase 0 skeleton, config files, smoke tests (committed locally).
- 2026-10-15: Switched image model to gemini-nano-banana-2.1.

## Deferred until reset
(none)
