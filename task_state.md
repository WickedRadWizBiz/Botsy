# Task State
_Last updated: 2026-10-15 (Phase 0 pushed; core + contracts done; Phases 1–6 in progress)_

## Current objective
Build Phases 1–6 on branch `claude/zen-newton-w7xzq9` (user chose full scope, 2026-10-15).

## Next immediate action
Parallel build of research/, generators/, qa/, listing/, publisher/ against `docs/CONTRACTS.md`; then pipeline CLI + GitHub Actions, review, commit, push.

## Decisions made
- Image API: Google Gemini `gemini-nano-banana-2.1` (Nano Banana 2.1, stable; Imagen 4 deprecated). Gemini API route via `generate_content`.
- Shops: one shop (`main`) covering stickers, coloring pages, activity sheets. Shop name placeholder in settings.yaml.
- Repo hosted on GitHub: WickedRadWizBiz/Botsy, branch `claude/zen-newton-w7xzq9`.
- Text model: `claude-opus-5-5`.
- Research CSVs: one parser that recognizes both eRank and EverBee columns via aliases.
- Shared code in `core/` (config, compliance blocklist checker, item helpers, PDF render via pypdfium2). Stage data formats in `docs/CONTRACTS.md`.
- Vectorizing: free local tools. potrace for coloring pages, vtracer for stickers, rembg for sticker backgrounds, OpenCV pre-cleanup. Settings under `vectorize:`.
- Recraft API: added to settings but `enabled: false`. Free plan forbids commercial use (Recraft owns + publicizes output); API is prepaid units (~$0.011/vectorize). Revisit if user buys a paid plan; confirm API ownership terms first.

## Open decisions (ask the user)
- A real sample CSV (eRank or EverBee) to confirm the column aliases.
- Shop display name for `config/settings.yaml` (`YOUR_SHOP_NAME`) and default prices (placeholders set).

## Waiting on the user
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
- [x] Push to GitHub (history imported into WickedRadWizBiz/Botsy)

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
- 2026-10-15: Added vtracer, rembg, OpenCV to requirements; vectorize + disabled Recraft config.
- 2026-10-15: Pushed to GitHub. Added `core/` + `docs/CONTRACTS.md` + 21 core tests.

## Deferred until reset
(none)
