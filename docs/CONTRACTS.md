# Stage contracts

Every stage reads the previous stage's files and writes its own. Stages never call each other's internals, only the entry points below, so each one can be rerun on its own.

```
data/research_inputs/*.csv ──research──▶ data/briefs/<niche>-<date>.json
   brief ──generators──▶ output/<item_id>/manifest.json + files/ previews/ assets/
   item  ──qa──────────▶ output/<item_id>/qa_report.json + mockups/ ; output/review/<item_id>.json
   item  ──listing─────▶ output/<item_id>/listing.json
   item  ──publisher───▶ output/<item_id>/publish.json   (Etsy DRAFT only)
   run   ──pipeline────▶ output/runs/run-<date>-<HHMMSS>.json
```
Dry runs use `output/dry-run/` instead of `output/` unless `--out` is given (settings `pipeline.dry_run_output_dir`; every stage CLI and the pipeline follow it).

Shared helpers live in `core/`. Use them instead of local copies:
- `core.config`: `load_settings()`, `section(settings, name, DEFAULTS)`, `deep_merge()`, `resolve(path)` (repo-relative → absolute), `get_shop(settings, shop_id)` (KeyError for an unknown id), `load_env()`, `require_env(name)` (raises `MissingSecretError`), `check_date(value)` (a real `YYYY-MM-DD`, nothing else; `DATE_RE`), `today()` (`PIPELINE_DATE` or today, checked the same way), `is_placeholder_shop_name(shop)` / `PLACEHOLDER_SHOP_NAMES`.
- `core.compliance`: `Blocklist.load().check(*texts, allow_systems=())` / `.check_obj(obj, allow_systems=())` → `ComplianceResult(blocked, flagged, allowed)` (each string is checked on its own, so a term never spans two fields; `!` game-system terms count as blocked unless they belong to a system id in `allow_systems`, and then appear in `allowed`), `Blocklist(hard, review, systems=, catalog=)`, `.system_terms` (`{! term: system id or None}`), `.system_names(id)`, `system_mentions(texts)` (ids of the systems whose registry names, owners or owner names appear, also in spellings no `!` line covers such as "DnD5e" or "PF2e"; never run it over a `system` id field), `term_pattern(term)`, `normalize(text)` (the text form the blocklist matches on: lowercase, no accents, ™/℠/©/® as spaces, no zero-width or soft-hyphen characters, every Unicode dash as `-`, Cyrillic/Greek look-alikes as Latin, single spaces), `iter_strings(obj)`, `find_urls(obj)`, `load_ai_disclosure(shop_name)`, `has_ai_disclosure(text, shop_name)`.
- `core.systems` (docs/PACKS.md section 1): the game-system registry `config/ttrpg_systems.yaml` (settings `ttrpg.systems_file`): `load_systems()`, `get_system(id)`, `names_for(id)`, `label_for(id)`, `owner_for(id)`, `disclaimer_for(id)`, `sheet_fields_for(id)`, `detect_systems(text)` (cached).
- `core.briefs`: the brief-document check research runs before writing and the generators and pipeline rerun on every brief file they read: `HARD_FLOOR` (10), `Guard(shop_names, titles, blocklist)`, `doc_problems(doc, guard)`, `validate_brief_doc(...)` (raises `BriefComplianceError` with `.problems`). `research.guard` re-exports it.
- `core.etsy`: Etsy's limits, shared by every stage: `MAX_FILES` 5, `MAX_FILE_BYTES` 20,000,000, `MAX_FILE_NAME_CHARS` 70, `FILE_NAME_RE`, `TITLE_MAX_CHARS` 140, `TAGS_MAX` 13, `TAG_MAX_CHARS` 20, `ALT_TEXT_MAX_CHARS` 500, `title_char_problems()`, `tag_char_problems()`. The settings keys that repeat them (`qa.etsy_*`, `publisher.max_file*`, `listing.*_max_chars`) default to these.
- `core.spend` (docs/SERVER.md section 10): `check_budget(kind, settings)` before every paid call (raises `BudgetExceeded`, or `LedgerUnavailable` when a cap is set and the ledger cannot be written), `record(...)` after it, `budget_state()`, `month_to_date()`, `entries()`, `summarize()`, `table()`. Dry runs and fake clients never record.
- `core.events` (docs/SERVER.md section 9): `emit(kind, *, settings=None, **fields)` (never raises) and `read(since, until=None, kinds=None, *, settings=None, path=None)`, the event journal the service's daily log is built from.
- `core.items`:
  - ids and dates: `slugify(text)`, `check_date()` (from `core.config`), `new_item_id(date, niche, theme, salt)` (`<date>-<niche>-<theme-slug>-<4 hex>`; generators pass the `brief_id` as salt), `ITEM_ID_RE` / `valid_item_id(id)`.
  - folders: `output_root(settings)`, `dry_run_root(settings)`, `stage_out_root(out, settings, dry_run=)` (`--out`, else the dry-run folder for a dry run, else `output_root`), `review_root(settings)`, `item_dir(item_id, root)`, `iter_item_dirs(root)` (folders with a `manifest.json`, sorted), `review_dir_for_root(out_root, settings)` (settings `paths.review_dir` for the configured output dir, else `<out_root>/review`), `review_dir_for(item_dir, settings)` (the same rule for one item), `review_entry_path(item_id, review_dir)` (refuses ids that could leave the folder), `repo_relative(path)` (POSIX path relative to the repo, else absolute).
  - JSON: `read_json()`, `write_json()` (atomic: temp file `.<name>.*.tmp` + rename), `read_manifest()`, `write_manifest()`, `set_status()`.
  - `content_count(manifest, fallback=None)`: how many designs the buyer gets (pack `activity_pages` / `pages` / `stickers`; for stickers without `pack.stickers`, the transparent PNG assets, else the ZIP `count`s summed over all parts; else the first PDF's pages). QA's photos and the listing copy both use it.
  - `count_drafts(out_root=None, settings=None)`: real Etsy drafts (item folders under the configured output dir whose `publish.json` has a `listing_id` and is not a dry run); the research stage uses it.
  - `clear_downstream(item_dir)`: after an item is regenerated, removes `qa_report.json`, `listing.json`, `mockups/` and a dry-run `publish.json`, and sets an `approved` review entry back to `pending` (`previous_status`, `reopened`, `content_digest: null`); a rejection stays. Refuses (RuntimeError) once a real `publish.json` exists (an unreadable one counts as real).

Rules that apply to all stages:
- Every entry point takes optional path overrides (`out_root`, `inputs_dir`, ...) so tests can run in `tmp_path`. Defaults come from `config/settings.yaml`.
- Network clients (Anthropic, Gemini, Etsy) are injected (`client=` argument) so tests use fakes. Every stage has an offline `dry_run` path that needs no secrets.
- Each module keeps its defaults in a `DEFAULTS` dict merged with its settings section via `core.config.section()`.
- Paths stored inside JSON files are relative to the item directory, except `brief_file`, which is relative to the repo root (absolute when outside it).
- `publish.json` with `dry_run: true` is a simulated draft. No stage treats it as a real draft: it never blocks a regeneration or a real draft.

## 1. Brief: `data/briefs/<niche>-<YYYY-MM-DD>.json`

Written by `research.run(...)`. Built only from aggregated patterns. Contains no shop names, listing URLs, listing ids, images or verbatim competitor titles.

```json
{
  "schema": "brief/v1",
  "niche": "coloring_pages",
  "created": "2026-10-15",
  "shop_id": "main",
  "stage": "discovery",
  "sources": [{"tool": "erank", "file": "erank-2026-10-14-1a2b3c4d.csv", "rows": 250, "received": "2026-10-14"}],
  "listing_count": 250,
  "min_listings_per_pattern": 10,
  "min_shops_per_pattern": 3,
  "patterns": {
    "themes":           [{"name": "farm animals", "listing_count": 23, "listing_rows": 23, "keyword_rows": 0, "shop_count": 7, "share": 0.092, "median_price_usd": 3.99, "demand": 0.81, "competition": 0.3, "opportunity": 0.77}],
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
      "pack_sizes": [5, 12, 20],
      "page_count": 20,
      "price_usd": 4.0,
      "page_sizes": ["us_letter", "a4"],
      "style_notes": "bold thick outlines, large simple shapes, no shading",
      "activity_types": [],
      "evidence": {"listing_count": 23, "listing_rows": 23, "keyword_rows": 0, "shop_count": 7, "score": 0.81,
                   "opportunity": 0.77, "competition": 0.3, "top_seller_sizes": [12]},
      "compliance": {"blocked": [], "flagged": []},
      "needs_review": false
    }
  ]
}
```
- Every `patterns.*` entry and every brief has `listing_count >= min_listings_per_pattern` (from settings, at least 10).
- Support from listing rows must also span `min_shops_per_pattern` different shops (settings `research.min_shops_per_pattern`, at least 3; lower values are raised to 3, and `core.briefs` refuses a file that declares fewer). Shops are counted through an opaque hash in memory; only the count (`shop_count`) is written. Support from keyword-tool rows (competition >= 10 listings each) carries no shop count.
- `stage` is the research stage that wrote the file, `discovery` or `refined` (docs/SERVER.md section 8). A refined file uses that stage's thresholds (`min_listings_per_pattern` 15) and also records `min_weeks_seen` (3); each refined brief's `evidence` gains `weeks_seen` (ISO weeks with fresh rows, this run included) and `trend` (listing-row change against the newest earlier history snapshot, or null).
- `sources[].received` is the day the export arrived (docs/SERVER.md section 12); only exports received within `research.window_days` (35) of `created` count.
- A brief with any blocked term is dropped. A brief with a flagged term is kept with `needs_review: true`.
- `activity_types` is used only for `activity_sheets` and holds a subset of `[maze, word_search, tracing, dot_to_dot]`.
- Packs (docs/PACKS.md section 2): `pack_sizes` is sorted and unique, `research.base_pack_sizes` (5, 20) plus the top sellers' sizes (`evidence.top_seller_sizes`), each within `packs.min_size` / `packs.max_size.<niche>`; `page_count` is `max(pack_sizes)`. Briefs are ordered by `evidence.opportunity`, best first.
- A `ttrpg` brief has `system`: a `config/ttrpg_systems.yaml` id (`dnd5e`, `pf2e`, `coc7e`) or `"generic"`, never a name. No other brief text names a game system in any spelling (`core.briefs` runs `core.compliance.system_mentions` over every string except `system`); system names, owners, editions, planner and VTT brands, rulebook titles and medical-claim words are cut from themes and keywords in every niche.
- A keyword-level export without a competition (matching listings) column counts no listings, so such a file alone produces no briefs (research logs a warning).

## 2. Item manifest: `output/<item_id>/manifest.json`

Written by the generators: `generators.generate(brief, brief_file=..., shop_id=..., out_root=..., date=..., dry_run=..., client=..., sizes=None) -> list[Path]` returns one item dir per pack made (docs/PACKS.md section 3: master first; `sizes` limits the run to some of the brief's pack sizes). A pack that fails stops the brief; the exception carries the folders made before it as `pack_dirs`. `generators.ImageCapExceeded` (the run's image budget is spent), `generators.ImageGenerationError`, and from the Gemini client `core.spend.BudgetExceeded` (the monthly spending cap) and `core.spend.LedgerUnavailable` are the errors a caller may handle. `core.render` has `page_size_points()`, `pdf_info()` and `render_pdf_pages()` for PDF work.

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
  "review_reasons": [],
  "generator": {"name": "coloring_pages", "model": "gemini-nano-banana-2.1", "dry_run": false},
  "prompts": ["Simple coloring page of a friendly cow ..."],
  "files":    [{"path": "files/farm-animals-coloring-pages-us_letter.pdf", "kind": "pdf", "page_size": "us_letter", "pages": 21, "dpi": 300}],
  "previews": [{"path": "previews/page-01.png", "page": 1}],
  "assets":   [{"path": "assets/page-01.png", "kind": "png", "page": 1}, {"path": "assets/page-01.svg", "kind": "svg", "page": 1}],
  "pack": {"size": 20, "source": "master", "n": 0, "set_id": "2026-10-15-coloring_pages-farm-animals-9c1e",
           "theme": "farm animals", "page_ids": ["2026-10-15-coloring_pages-farm-animals-9c1e-p01", "..."],
           "page_types": ["coloring_page", "..."], "type_counts": {"coloring_page": 20},
           "related": [{"size": 5, "source": "derived", "item_id": "2026-10-15-coloring_pages-farm-animals-4d2a", "same_set": true}],
           "requested_pages": 20, "pages": 20, "contents": [{"page": 1, "subject": "a friendly cow", "...": "..."}]},
  "status": "generated"
}
```
- `files` are the digital files the buyer downloads (uploaded to Etsy). `previews` are RGB PNG renders used by QA and mockups. `assets` are intermediates kept for QA (`png` cleaned line art and `svg` traces for coloring pages with `page`; `png_transparent` stickers with `sticker`; none for activity sheets).
- `kind` is one of: `pdf`, `png`, `png_transparent`, `zip`, `svg`.
- `ai_assisted` is `false` for procedural activity sheets and `true` for anything that used an image model. `generator` has `name` and `dry_run` for every niche, plus `model` for the image niches (`fake-image` in a dry run). `prompts` is empty for activity sheets.
- `status` moves `generated → qa_passed | qa_failed → listed → drafted`.
- `files[]` entries may also carry: `count` (ZIP: number of PNGs inside, read by QA and the listing stage), `part` / `parts` (a ZIP split below Etsy's 20 MB limit), and optionally `print_size_in: [w, h]` or `width_in` / `height_in` (intended print size; QA's DPI check uses it before `page_size`). No generator writes the print-size fields; they are for hand-made manifests.
- `review_reasons` (every niche): reasons the generator wants a person to look (brief review terms, a brief marked `needs_review`, a bad brief price, review terms in printed page text, brief style notes outside the prompt allowlist, too much ink, refused images, a sticker in several pieces, oversize files). `needs_review` is true when there is any. QA reports them as the `generator_needs_review` warning, so they reach the review entry.
- `pack` describes the contents; QA and the listing stage read it. Pack keys (every niche, docs/PACKS.md section 3): `size`, `source` (`master` | `derived` | `fresh`), `n` (the pack's number among the shop's packs of this size and niche; 0 for a master), `set_id` (its design set, `<out>/sets/<set_id>/set.json`), `theme`, `page_ids` (unique; prefixed with the set id), `page_types`, `type_counts`, `related` (`[{size, source, item_id, same_set}]`: the theme's other packs when the item was made; the set's `items[]` is the current list). File names carry the pack size (`farm-animals-coloring-pages-20-pages-us_letter.pdf`). Common key: `contents` (one entry per page or sticker). Per niche:
  - `activity_sheets`: `activity_pages`, `title_pages`, `answer_key_pages`, `difficulty`, `activity_types`, `contents[] {page, type, title}`.
  - `coloring_pages`: `requested_pages`, `pages`, `dry_run_capped` (always false: dry runs make the whole pack with small fake images), `aspect_ratio`, `image_size`, `tracers`, `min_line_weight_pt`, `contents[] {page, subject, size_px, min_line_px, ink_fraction}`, `skipped`.
  - `stickers`: `requested_stickers`, `stickers`, `dry_run_capped` (always false), `aspect_ratio`, `image_size`, `background_removal`, `rembg_model`, `sheet_grid`, `zip_parts`, `contents[] {sticker, subject, size_px, pieces}`, `skipped`.
  - `stationery`: `pages`, `title_pages` (0), `style`, `seasons`, `undated`, `ai_accents`, `hero_page`, `layout_reused_pages`, `min_font_pt`, `min_line_gap_mm`, `print_checks {page_size: {...}}`, `contents[] {page, type, variant, title}`.
  - `ttrpg`: `pages`, `title_pages` (0), `genre`, `focus`, `kit`, `system_fields`, `undated`, `ai_accents`, `type_labels`, `type_counts`, `min_font_pt`, `min_line_gap_mm`, `print_checks`, `contents[] {page, type, variant, title, subtitle}`. The manifest's own `system` holds the game-system id (or `generic`); nothing in the files, file names or previews names the system.

Generator rules:
- Before anything is planned or paid for, each brief goes through the brief-document check (`core.briefs`, with the header of the file it came from): evidence and every pattern from at least 10 listings, a file floor of at least 10, no forbidden identifying fields, ids, image names, URLs or blocked terms. Brief free text (`style_notes`, `audience`) never reaches an image prompt as written: only `style_notes` clauses in `generators/imaging/prompts.py` `STYLE_CLAUSES` are used, and any other clause marks the item for review.
- An item with a real Etsy draft is left untouched. A dry run refuses to replace a real item. A rebuilt item goes through `core.items.clear_downstream` (see above).
- Every printed string (the plugin's `printed_text()`, else the page specs) is checked before a pack is kept: hard-blocked and `!` terms, and any game-system spelling `core.compliance.system_mentions` finds, refuse the pack; review terms mark it for review.
- Design sets and pack counters: `<out>/sets/<set_id>/` (docs/PACKS.md section 3); counters in `packs.state_file` (`data/packs_state.json`) for the configured output folder, else `<out>/sets/packs_state.json`. Reruns reuse a set instead of paying for it again.

## 3. QA report: `output/<item_id>/qa_report.json` and review queue

Written by `qa.run(item_dir) -> dict` and `qa.run_all(out_root)` (every item not yet `listed` or `drafted`). `python -m qa` exits 1 when any item fails.

```json
{
  "schema": "qa/v1",
  "item_id": "...",
  "checked": "2026-10-15",
  "passed": true,
  "needs_review": false,
  "reasons": [],
  "checks": [{"name": "pdf_page_size", "passed": true, "severity": "error", "detail": "us_letter 612x792pt"}],
  "mockups": ["mockups/mockup-01.jpg"],
  "content_digest": "d495...a859"
}
```
- `severity` is `error` (fails the item) or `warning` (passes, and the item is marked `needs_review`). `reasons` has one line per failed warning check (`<check>: <detail>`); the generator's `review_reasons` arrive through the `generator_needs_review` warning.
- Checks include the manifest schema (the niche must be in `settings.niches`), safe and existing paths, Etsy's file limits (`etsy_file_count`, `etsy_file_size`, `etsy_file_name`: basename at most 70 characters of `[A-Za-z0-9._-]`), PDF page size, count, blank pages and image DPI, PNG resolution and transparency, line weight and regions (coloring), ZIP contents, the blocklist and URLs over the manifest (the manifest's `system` id is not text), the text printed in the files and the mockup text.
- Pack checks (docs/PACKS.md section 6): `pack_page_count` (PDF pages = `pack.size` plus declared `title_pages`, `answer_key_pages`, `extra_pages`), `pack_page_ids`, `pack_duplicates` / `pack_near_duplicates` (image niches, perceptual hash), `pack_fresh_pages` (a fresh pack shares no page id with another item), and for stationery and ttrpg `print_font_size` (7 pt) and `print_line_spacing` (6 mm). A game-system name in any spelling fails the item when it is printed, in a file name or in the mockup text.
- `content_digest`: sha256 over the paths and bytes of `files`, `previews`, `assets` and the mockups; `null` for a failed item.
- QA creates the listing photos in `output/<item_id>/mockups/` (JPEG, at least 2000 px on the long side, five for a full set, six for stationery and ttrpg: a desk scene). The cover photo carries the pack's size badge ("20 PAGES", stickers "SET OF 5") and leads with `pack.hero_page` when the pack has one. A failed item keeps none.
- Each passing item gets a review-queue entry at `output/review/<item_id>.json` (`<out>/review/` for another output root):
  `{"item_id", "niche", "status": "pending" | "approved" | "rejected", "needs_review", "reasons": [...], "qa_reasons": [...], "mockups": [...], "content_digest", "queued"}`.
  - `qa_reasons` are the reasons QA owns and replaces on a rerun; other stages' reasons (the listing stage's review terms) stay.
  - `decided` and `decision_note` are set by `python -m qa review approve|reject` (a rejection needs `--reason`).
  - `previous_status` / `reopened`: an approval reset to `pending` because a rerun found a new reason, the content digest changed or is missing, the listing copy has new review terms, or the item was regenerated.
  - A rejection always stays; the pipeline neither lists nor rebuilds a rejected item. A pending entry is removed when the item stops passing QA.
- Items with `needs_review: true` (trademark flag, QA warning, listing review term) must be `approved` before the publisher drafts them, and the approval must carry the report's `content_digest`. Clean items go straight to draft, because the user approves every draft in Etsy anyway.

## 4. Listing: `output/<item_id>/listing.json`

Written by `listing.write_listing(item_dir, client=None, dry_run=False) -> dict` and `listing.run_all(out_root)`.

```json
{
  "schema": "listing/v1",
  "item_id": "...",
  "shop_id": "main",
  "title": "Farm Animal Coloring Pages for Kids, 20 Printable ...",
  "tags": ["farm coloring pages", "... 13 total, each <= 20 chars"],
  "description": "... ends with the AI disclosure text ...",
  "alt_texts": ["one per mockup, same order"],
  "price_usd": 4.0,
  "quantity": 999,
  "who_made": "i_did",
  "when_made": "made_to_order",
  "taxonomy_id": null,
  "type": "download",
  "image_paths": ["mockups/mockup-01.jpg"],
  "file_paths": ["files/farm-animals-coloring-pages-us_letter.pdf"],
  "ai_disclosure_included": true,
  "compliance": {"blocked": [], "flagged": []},
  "needs_review": false,
  "review_reasons": [],
  "warnings": [],
  "model": "claude-opus-5-5",
  "dry_run": false,
  "writer": {"kind": "model", "dry_run": false, "attempts": 1, "repaired": []},
  "created": "2026-10-15"
}
```
- Title is at most 140 chars, exactly 13 unique tags, each at most 20 chars (`core.etsy`, settings `listing:`). Pack listings name the size in the title ("..., 20 Pages", "Set of 5 ...") and state the overlap with the theme's other packs (docs/PACKS.md section 6).
- A ttrpg item made for a game system (manifest `system` other than `generic`) may name that system in the title, tags and description only, always with "Made for use with <label>. This is an unofficial product of our own design." and the registry's disclaimer; `compliance.allowed` lists the `!` terms it used. Any other system, or any system in alt text, other niches or file names, is refused. Medical-claim words are refused in every listing ("ADHD-friendly" describes a layout and is allowed). A listing with a blocked term or a URL is not written (the stage raises `ListingComplianceError`; an earlier `listing.json` is moved to `listing.withdrawn.json` and the item goes back to `qa_passed`). A flagged term sets `needs_review` and is added to the review entry.
- The AI disclosure (`config/ai_disclosure.txt` with the shop name) ends every description when the item is AI-assisted and, by default, on every listing (`listing.always_disclose_ai: true`), because the copy is AI-written.
- `review_reasons`: listing review terms plus the reasons the item already had (manifest, QA). `warnings`: placeholder shop name, missing taxonomy id. `model`: the text model, or `null` for template copy.
- `dry_run` and `writer.dry_run`: the copy was written in dry-run mode (template text). `writer.kind` is `model` or `template` (`template` also when the model gave no usable draft), `attempts` counts model calls, `repaired` lists rule problems fixed in code. The publisher refuses a real draft when `dry_run` or `writer.dry_run` is true.
- `write_listing(..., dry_run=True)` on a real item (manifest `generator.dry_run` false) only previews: it returns the listing with `preview: true` and writes nothing (no `listing.json`, no status change, no review entry).
- `listing.needs_listing(item_dir, *, dry_run=False, settings=None) -> bool` says which items a run (re)lists: every `qa_passed` item and, in a real run, a `listed` real item whose copy the publisher would refuse as stale: dry-run template text, the AI disclosure missing for the current shop name while one is required, or the placeholder shop name in any text (only once the shop name is set). `run_all` and the pipeline use it.
- `python -m listing` exits 1 when an item could not be listed and 2 on a setup error (bad item id, or `ANTHROPIC_API_KEY` missing in a real run).

## 5. Publish record: `output/<item_id>/publish.json`

Written by `publisher.publish_draft(item_dir, client=None, dry_run=False) -> dict` and `publisher.run_all(out_root)`.

```json
{"schema": "publish/v1", "item_id": "...", "shop_id": "main", "etsy_shop_id": 12345678, "listing_id": 123456,
 "state": "draft", "image_ids": [1, 2], "file_ids": [3], "created": "2026-10-15", "updated": "2026-10-15",
 "dry_run": false, "complete": true, "listing_type": "download", "plan_digest": "c34b...", "warnings": []}
```
- The publisher only ever creates drafts and never sends `state=active` or PUT/PATCH (the safety layer refuses them, and any request outside our own shop and the listings this tool created).
- Idempotent: if a real `publish.json` exists it never creates a second draft. Before `createDraftListing` it also reads our own shop's drafts and active listings and refuses when one has the same title (case- and space-insensitive), so a run without the earlier `publish.json` (a fresh CI checkout) does not draft the same listing twice.
- Resume fields: `etsy_shop_id`, `listing_type`, `plan_digest` (hash of the fields, photos and files; a changed item is refused), `complete` (false while uploads are pending; a rerun resumes the same draft), `warnings`, `updated`.
- Gates (`publisher.check_ready`; every reason is listed in `PublishGateError.problems`): status `listed`, QA passed, `listing.json` not written in dry-run mode, blocklist-clean (title, tags, description, alt texts, photo and file names), no placeholder shop name in the copy or settings, the AI disclosure for the current shop name whenever `manifest.ai_assisted`, `listing.ai_disclosure_included`, `writer.kind == "model"`, `listing.model` or `listing.always_disclose_ai` (default true) applies, the review gate, `taxonomy_id` set, photos and files inside the item folder and within Etsy's limits, title and tags within the `listing:` limits (capped at Etsy's) and Etsy's character rules, price and quantity in Etsy's range, the client is for the item's shop.
- Review gate: the entry is found with `core.items.review_dir_for` (resolved item dir). A `rejected` entry always blocks. An item needing review waits until it is `approved` and the entry's `content_digest` matches `qa_report.json`'s. When that wait is the only problem, the error is `publisher.ReviewPending`. `publisher.review_wait(item_dir) -> str | None` answers the same question without building an Etsy client (the pipeline uses it).
- Refusal messages name the fix: `python -m listing --item <id> [--out DIR]` for stale copy, `python -m qa review approve <id> [--out DIR | --review-dir DIR]` for a review wait.
- Dry runs use `FakeEtsyClient`. On items generated in dry-run mode they write `publish.json` with `dry_run: true` and set `drafted`. On real items a dry run only previews and writes nothing.

## 6. Pipeline run summary: `<out_root>/runs/run-<date>-<HHMMSS>.json`

Written by `pipeline.run(...)` (`python -m pipeline run`) after every run; earlier summaries are kept. The weekly Action uploads it with the items and shows `format_summary()` in the job summary.

```json
{"date": "2026-10-15", "shop_id": "main", "dry_run": true, "stages": ["generate", "qa", "listing", "publish"],
 "started": "2026-10-15T13:17:02+00:00", "briefs": ["data/briefs/stickers-2026-10-15.json"],
 "items": {"2026-10-15-stickers-frogs-2f05": {"generate": {"ok": true, "detail": "generated"},
                                              "qa": {"ok": true, "detail": ""},
                                              "listing": {"ok": true, "detail": "<title>"},
                                              "publish": {"ok": true, "detail": "needs review ...", "skipped": true}}},
 "errors": [], "setup_errors": [], "notes": ["max_items=10: 5 brief(s) not used this run"],
 "awaiting_review": {"2026-10-15-stickers-frogs-2f05": "needs review (review status: pending); approve with ..."},
 "budget_reached": false,
 "path": "output/runs/run-2026-10-15-131702.json"}
```
- Packs (docs/PACKS.md section 3): `max_items` counts packs, reused ones included, tier by tier (the first brief of every niche gets one pack, its master when that is new, then those briefs' other packs, then the next briefs). `packs` lists every pack the run made or reused (`{item_id, size, source, generate: "generated" | "reused"}`), each generate record carries `size` and `source`, and `format_summary` labels items `[pack of N, source]`. Notes say how many briefs and packs `max_items` left for a later run.
- `items.<id>.<stage>` has `ok`, `detail` and, for an item the stage left alone (reused, rejected, waiting for review, a listing preview on a real item in a dry run), `skipped: true`. A broken item folder gets a `manifest` record.
- Theme cooldown (docs/SERVER.md section 13, keyed per pack size): a pack skipped because the shop already has an item of the same theme and size gets a `generate` record under the id it would have made, with `skipped: true`, `cooldown: <blocking item id>` and the detail `theme cooldown: <item id>` (`format_summary` shows `generate: skip (theme cooldown: <item id>)`). It uses no `max_items` slot.
- Spending cap (docs/SERVER.md section 10): `budget_reached` is always present. When a stage raises `core.spend.BudgetExceeded`, that item's record has `ok: false` and `budget: true` (`format_summary` shows `wait`), `budget_reached` becomes true and no new paid work (images, model listing copy) starts for the rest of the run; QA and drafts of finished items go on. `core.spend.LedgerUnavailable` is a setup error (that stage stops).
- `errors` are brief or stage failures, `setup_errors` are missing secrets, Etsy auth or safety-layer refusals (the stage stopped), and `notes` are informational.
- The listing step lists what `listing.needs_listing()` selects; the publish step asks `publisher.review_wait()` first and records waiting items under `awaiting_review`.
- Live runs skip a niche's newest brief file when it is older than `pipeline.max_brief_age_days` (6) days; files named with `--brief` are exempt.
- `pipeline.exit_code(summary)`: 2 with any `setup_errors` (including a spending log that cannot be written, and unreadable settings in the CLI), 1 with any `errors` or any `ok: false` record except `budget: true` ones, else 0. A run stopped only by the spending cap exits 0.
