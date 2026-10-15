# Stage contracts

Every stage reads the previous stage's files and writes its own. Stages never call each other's internals, only the entry points below, so each one can be rerun on its own.

```
data/research_inputs/*.csv ──research──▶ data/briefs/<niche>-<date>.json
   brief ──generators──▶ output/<item_id>/manifest.json + files/ previews/ assets/
   item  ──qa──────────▶ output/<item_id>/qa_report.json + mockups/ ; output/review/<item_id>.json
   item  ──listing─────▶ output/<item_id>/listing.json
   item  ──publisher───▶ output/<item_id>/publish.json   (Etsy DRAFT only)
```

Shared helpers live in `core/`:
- `core.config`: `load_settings()`, `section(settings, name, DEFAULTS)`, `resolve(path)`, `get_shop()`, `require_env()`, `today()`.
- `core.compliance`: `Blocklist.load().check(*texts)` / `.check_obj(obj)` → `ComplianceResult(blocked, flagged)`, `find_urls(obj)`, `load_ai_disclosure(shop_name)`, `has_ai_disclosure(text, shop_name)`.
- `core.items`: `new_item_id()`, `item_dir()`, `iter_item_dirs()`, `read_json()`, `write_json()` (atomic), `read_manifest()`, `write_manifest()`, `set_status()`.

Rules that apply to all stages:
- Every entry point takes optional path overrides (`out_root`, `inputs_dir`, ...) so tests can run in `tmp_path`. Defaults come from `config/settings.yaml`.
- Network clients (Anthropic, Gemini, Etsy) are injected (`client=` argument) so tests use fakes. Every stage has an offline `dry_run` path that needs no secrets.
- Each module keeps its defaults in a `DEFAULTS` dict merged with its settings section via `core.config.section()`.
- Paths stored inside JSON files are relative to the item directory, except `brief_file`, which is relative to the repo root.

## 1. Brief: `data/briefs/<niche>-<YYYY-MM-DD>.json`

Written by `research.run(...)`. Built only from aggregated patterns. Contains no shop names, listing URLs, listing ids, images or verbatim competitor titles.

```json
{
  "schema": "brief/v1",
  "niche": "coloring_pages",
  "created": "2026-10-15",
  "shop_id": "main",
  "sources": [{"tool": "erank", "file": "erank-keywords-2026-10-14.csv", "rows": 250}],
  "listing_count": 250,
  "min_listings_per_pattern": 10,
  "patterns": {
    "themes":           [{"name": "farm animals", "listing_count": 23, "share": 0.092, "median_price_usd": 3.99, "demand": 0.81}],
    "formats":          [{"name": "pdf", "listing_count": 180}],
    "page_counts":      [{"bucket": "11-20", "listing_count": 40}],
    "price_bands":      [{"band": "3-5", "min_usd": 3.0, "max_usd": 5.0, "listing_count": 60}],
    "keyword_clusters": [{"label": "farm animals", "keywords": ["farm animal coloring pages", "barn animals"], "listing_count": 31, "search_volume": 5400}]
  },
  "briefs": [
    {
      "brief_id": "coloring_pages-2026-10-15-01",
      "niche": "coloring_pages",
      "theme": "farm animals",
      "audience": "kids 4-8",
      "keywords": ["farm animal coloring pages", "barn animals"],
      "page_count": 20,
      "price_usd": 4.0,
      "page_sizes": ["us_letter", "a4"],
      "style_notes": "bold thick outlines, large simple shapes, no shading",
      "activity_types": [],
      "evidence": {"listing_count": 23, "score": 0.81},
      "compliance": {"blocked": [], "flagged": []},
      "needs_review": false
    }
  ]
}
```
- Every `patterns.*` entry and every brief has `listing_count >= min_listings_per_pattern` (from settings, at least 10).
- A brief with any blocked term is dropped. A brief with a flagged term is kept with `needs_review: true`.
- `activity_types` is used only for `activity_sheets` and holds a subset of `[maze, word_search, tracing, dot_to_dot]`.

## 2. Item manifest: `output/<item_id>/manifest.json`

Written by the generators: `generators.generate(brief, ...) -> list[Path]` returns item dirs.

```json
{
  "schema": "manifest/v1",
  "item_id": "2026-10-15-coloring_pages-farm-animals-1a2b",
  "niche": "coloring_pages",
  "shop_id": "main",
  "brief_id": "coloring_pages-2026-10-15-01",
  "brief_file": "data/briefs/coloring_pages-2026-10-15.json",
  "theme": "farm animals",
  "keywords": ["farm animal coloring pages", "barn animals"],
  "audience": "kids 4-8",
  "price_usd": 4.0,
  "created": "2026-10-15",
  "ai_assisted": true,
  "needs_review": false,
  "generator": {"name": "coloring_pages", "model": "gemini-nano-banana-2.1", "dry_run": false},
  "prompts": ["Simple coloring page of a friendly cow ..."],
  "files":    [{"path": "files/farm-animals-us_letter.pdf", "kind": "pdf", "page_size": "us_letter", "pages": 20, "dpi": 300}],
  "previews": [{"path": "previews/page-01.png", "page": 1}],
  "assets":   [{"path": "assets/page-01.svg", "kind": "svg"}],
  "status": "generated"
}
```
- `files` are the digital files the buyer downloads (uploaded to Etsy). `previews` are RGB PNG renders used by QA and mockups. `assets` are intermediates kept for QA (transparent PNGs, SVGs).
- `kind` is one of: `pdf`, `png`, `png_transparent`, `zip`, `svg`.
- `ai_assisted` is `false` for procedural activity sheets and `true` for anything that used an image model.
- `status` moves `generated → qa_passed | qa_failed → listed → drafted`.

## 3. QA report: `output/<item_id>/qa_report.json` and review queue

Written by `qa.run(item_dir) -> dict` and `qa.run_all(out_root)`.

```json
{
  "schema": "qa/v1",
  "item_id": "...",
  "checked": "2026-10-15",
  "passed": true,
  "needs_review": false,
  "checks": [{"name": "pdf_page_size", "passed": true, "severity": "error", "detail": "us_letter 612x792pt"}],
  "mockups": ["mockups/mockup-01.jpg"]
}
```
- `severity` is `error` (fails the item) or `warning` (passes, and the item is marked `needs_review`).
- QA creates the listing photos in `output/<item_id>/mockups/` (JPEG, at least 2000 px on the long side).
- Each passing item gets a review-queue entry at `output/review/<item_id>.json`:
  `{"item_id", "niche", "status": "pending" | "approved" | "rejected", "needs_review", "reasons": [...], "mockups": [...]}`.
  Items with `needs_review: true` (trademark flag, QA warning) must be `approved` before the publisher drafts them. Clean items go straight to draft, because the user approves every draft in Etsy anyway.

## 4. Listing: `output/<item_id>/listing.json`

Written by `listing.write_listing(item_dir, client=None, dry_run=False) -> dict`.

```json
{
  "schema": "listing/v1",
  "item_id": "...",
  "shop_id": "main",
  "title": "Farm Animal Coloring Pages for Kids, 20 Printable ...",
  "tags": ["farm coloring pages", "... 13 total, each <= 20 chars"],
  "description": "... ends with the AI disclosure text when ai_assisted ...",
  "alt_texts": ["one per mockup, same order"],
  "price_usd": 4.0,
  "quantity": 999,
  "who_made": "i_did",
  "when_made": "made_to_order",
  "taxonomy_id": null,
  "type": "download",
  "image_paths": ["mockups/mockup-01.jpg"],
  "file_paths": ["files/farm-animals-us_letter.pdf"],
  "ai_disclosure_included": true,
  "compliance": {"blocked": [], "flagged": []},
  "needs_review": false,
  "model": "claude-opus-5-5",
  "created": "2026-10-15"
}
```
- Title is at most 140 chars, exactly 13 unique tags, each at most 20 chars. A listing with a blocked term is not written (the stage raises). A flagged term sets `needs_review`.

## 5. Publish record: `output/<item_id>/publish.json`

Written by `publisher.publish_draft(item_dir, client=None, dry_run=False) -> dict`.

```json
{"schema": "publish/v1", "item_id": "...", "shop_id": "main", "listing_id": 123456, "state": "draft",
 "image_ids": [1, 2], "file_ids": [3], "created": "2026-10-15", "dry_run": false}
```
- The publisher only ever creates drafts and never sends `state=active`. It is idempotent: if `publish.json` exists it does not create a second draft.
