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
- `origin` (`plan` | `scale_up`) and `order_id` (optional): set on packs made for a weekly plan line (service/plan.py, order/v1). Packs made on the normal rules have neither.
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
- `plan` (only when the run was given `plan_lines`, i.e. a weekly plan steers it): `{"lines": [{"order_id", "brief_id", "niche", "sizes", "made": {"<size>": item_id}, "skipped": {"<size>": reason}, "dropped": null | "bad_line" | "not_this_shop" | "duplicate_line" | "brief_missing" | "brief_changed" | "size_not_in_brief"}]}`. Only the line's sizes are made; packs already made take no `max_items` slot.
- The listing step lists what `listing.needs_listing()` selects; the publish step asks `publisher.review_wait()` first and records waiting items under `awaiting_review`.
- Live runs skip a niche's newest brief file when it is older than `pipeline.max_brief_age_days` (6) days; files named with `--brief` are exempt.
- `pipeline.exit_code(summary)`: 2 with any `setup_errors` (including a spending log that cannot be written, and unreadable settings in the CLI), 1 with any `errors` or any `ok: false` record except `budget: true` ones, else 0. A run stopped only by the spending cap exits 0.

## 7. Own-shop performance: `data/performance/` (gitignored)

Written by `python -m publisher stats` (`publisher.__main__.run_stats(settings, dry_run=, performance_dir=, csv_path=)`), which also refreshes the id-free `own_shop_stats.csv` from the same pass. Our own shop only (ETSY_SHOP_ID, pinned by the safety layer); never other sellers. Every `day` and file name is Botsy's local day (`schedule.timezone`, America/New_York); rerunning the same day replaces that day's files, so the command is idempotent. Dry runs write the same tree under `<pipeline.dry_run_output_dir>/performance/` and never under `data/`. Settings: `publisher.performance_dir` (`data/performance`), `publisher.sales_keep_days` (90), `publisher.sales_max_pages` (50). The service's daily check (`service tick --force --own-stats`) calls `run_stats`: live with the real client, in test mode with `dry_run=True`. `service/cleanup.py` removes `etsy/<day>.json` and `reviews/<day>.json` older than `service.performance_keep_days` (400) days and `sales/<day>.json` older than `service.sales_files_keep_days` (90), in both trees; `etsy_quota.json` is never removed.

### performance/v1: `etsy/<YYYY-MM-DD>.json` (daily per-listing snapshot)

Written by `publisher.own_stats.write_snapshot(client, performance_dir, day=, settings=)` / `write_all(...)` from getListingsByShop (active, then `state=draft`; scope `listings_r`).

```json
{"schema": "performance/v1", "day": "2026-10-17", "taken": "2026-10-17T10:17:02+00:00", "etsy_shop_id": 12345678,
 "active": 2, "drafts": 1,
 "listings": [{"listing_id": 900000001, "state": "active", "title": "Farm Animal Coloring Pages ...",
               "tags": ["farm coloring", "..."], "views": 120, "num_favorers": 7, "created": "2026-09-17",
               "price": 4.0, "currency": "USD", "quantity": 999}]}
```
- `views` and `num_favorers` are Etsy's running totals ("views in the last 30 days" = today's count minus the count 30 days ago). Etsy tabulates views once a day and only for active listings, so a 0 after a non-zero number means "not counted yet", not a drop; drafts have no views. `created` is the local day of `original_creation_timestamp`; `null` fields were missing from Etsy's answer.
- `drafts` (and the `state: "draft"` rows) feed the waiting-drafts check; `active` counts the live listings. Each listing appears once.
- Winners, Hermes and the SNN read these files; the research stage keeps reading only the id-free CSV.

### sales/v1: `sales/<YYYY-MM-DD>.json` (our own sales, rolling 90 days)

Written by `publisher.sales.write_sales(client, performance_dir, keep_days=90, max_pages=50, settings=)` from getShopReceiptTransactionsByShop (scope `transactions_r`, user-approved 2026-10-17; limit 100, offset paging, at most 50 pages; the endpoint has no date filter, so paging stops at the first page that lies entirely before the window and runs newest first; a page in any other order never stops paging early, so only the cap does). The window is `keep_days` local days including today (90: today and the 89 days before it).

```json
{"schema": "sales/v1", "day": "2026-10-17", "etsy_shop_id": 12345678, "window_days": 90, "since": "2026-07-20",
 "complete": true, "pages": 1, "currency": "USD",
 "sales": [{"listing_id": 900000001, "day": "2026-10-14", "quantity": 1, "price": 4.0}]}
```
- Per sale exactly `listing_id`, `day` (local day of `created_timestamp`), `quantity` and `price` (unit price; `price × quantity` is our revenue). Never stored: `buyer_user_id`, `receipt_id`, `transaction_id`, names, addresses, coupon fields, notes, SKUs or file names. The receipt endpoints are never called (the safety layer refuses every `/receipts` path), so refunds are not visible.
- `complete: false` means the page cap stopped paging before the window was exhausted (the oldest days may be missing). Rows are sorted by day, then listing id.
- A token without the scope (issued before `transactions_r` was added to `publisher.scopes`) or a 403 skips the file with one log line and no error (`write_sales` returns `(None, None)`); `python -m publisher auth` once more grants the scope.

### reviews/v1: `reviews/<YYYY-MM-DD>.json` (star ratings of our own listings)

Written by `publisher.reviews.write_reviews(client, performance_dir, settings=)` from getReviewsByShop (app key only; limit 100, at most 50 pages, every review kept).

```json
{"schema": "reviews/v1", "day": "2026-10-17", "etsy_shop_id": 12345678, "count": 1,
 "reviews": [{"listing_id": 900000001, "rating": 5, "day": "2026-10-16"}]}
```
- Per review exactly `listing_id`, `rating` and `day` (local day of `created_timestamp`). Never stored: review text, language, photo links or anything about the buyer. A 403 skips the file with one log line.

### quota/v1: `etsy_quota.json`

Kept by `publisher.client.EtsyClient` (`quota_path`; the default is `<publisher.performance_dir>/etsy_quota.json` for a client on the real network, while a client with an injected session (tests, fakes) keeps it in memory only as `client.quota`; a dry run's `stats` names `<dry-run root>/performance/etsy_quota.json`), updated after every Etsy answer and never allowed to fail a request. Etsy's quota belongs to the app key, so all clients (every shop, the service and manual runs) share the one file and re-read it before each update.

```json
{"schema": "quota/v1", "etsy_shop_id": 12345678, "day": "2026-10-17", "requests_today": 123,
 "rate_limited_today": 0, "rate_limited_total": 2, "limit_per_day": 10000, "remaining_today": 9877,
 "last_rate_limited": "2026-10-03T14:02:11+00:00", "updated": "2026-10-17T10:17:03+00:00"}
```
- `limit_per_day` and `remaining_today` are Etsy's `x-limit-per-day` and `x-remaining-today` headers from the latest answer that carried them (null until one did); `requests_today` and `rate_limited_today` count all clients' answers and 429s and reset when the UTC `day` changes; `rate_limited_total` never resets. A later capacity check reads this before planning a batch.

## 8. Orders and capacity: `data/orders/`, `data/control/` (gitignored)

The shared core of the weekly plan and of Hermes (docs/OVERSEER.md "Four permission levels" and Part B). No codes (decided 2026-10-21, user: "I don't want the codes"). It works without Hermes: Botsy writes its own plan orders, and you approve from the server CLI (`botsy service plan approve`, `botsy service control ...`). Hermes, once installed, only calls botsy-control, which calls these functions with `created_by: "hermes"`. Settings `control:` (paths; tests point every one into tmp_path, and a test run refuses files inside the repo with a RuntimeError):
`limits_path` (`/etc/botsy/limits.yaml`), `limits_require_root` (true), `orders_dir` (`data/orders`), `state_file` (`data/control/state.json`), `winners_file` (`data/performance/winners.json`).

### limits/v1: `/etc/botsy/limits.yaml` (admin-only; `service/limits.py`)

- Owned by root, mode 0644; `setup.sh` installs `config/limits.example.yaml` there only when no file exists. Botsy never writes it (the module has no writer) and nothing takes a limit from an order, the settings or the environment.
- `load(settings=None, *, path=None) -> Limits(ok, values, path, reason, detail, unknown)`; `.level2_allowed`, `.scale_up_allowed`, `.get("scale_up.monthly_usd")`. Never raises.
- Keys and defaults: `changes_per_hour` 20, `page_redos_per_week` 10, `run_now_per_day` 3, `cap_ceiling_usd` 100, `level3_per_day` 10 (Level 3 approvals in any 24 hours, whoever asks), `approve_batch_max` 20 (items in one batch approval), `search_ideas_per_week` 5, `plan.max_cost_share_auto` 0.40, `scale_up` {`enabled` false, `packs_per_week` 3, `max_share` 0.30, `min_new_share` 0.50, `monthly_usd` 6, `per_theme_open` 1, `per_theme_max_listings` 8, `auto_stop_failures` 2, `auto_stop_days` 60}, `capacity` {`max_review_queue` 30, `max_waiting_drafts` 40, `min_etsy_quota_share` 0.30, `min_disk_free_mb` 2000}. Keys left out take the default; unknown keys are ignored (listed in `unknown`); one value of the wrong type or out of range breaks the whole file.
- Fail closed, with a fixed `reason`: `limits_missing`, `limits_unreadable`, `limits_invalid`, `limits_not_admin_only` (world-writable; with `limits_require_root`, also not root's, group-writable, a link not root's, or in a folder others can write). The open file itself is checked again (fstat) before it is read, so a file swapped in after the path check is refused too. Then Level 2 changes, scale-ups and cap raises are off; pause, order_cancel, plan_hold, lower_cap and review_hold still pass, and so do Level 1 search ideas, Level 3 approvals other than raise_cap (with the shipped `level3_per_day`), and all reads (`values` are then the shipped defaults, not in force).

### order/v1: `data/orders/<week>/<id>.json` (`service/orders.py`)

What a caller sends (no other field is accepted, and there is no free-text field anywhere):

```json
{"schema": "order/v1", "kind": "review_approve", "created_by": "hermes", "client_ref": "h-20261021-1",
 "payload": {"item_ids": ["2026-10-20-coloring_pages-axolotl-garden-1a2b"], "batch": "week-43"}}
```

What Botsy stores once it is accepted (refused orders are journaled, never stored):

```json
{"schema": "order/v1", "id": "ra-20261021-0001", "kind": "review_approve", "level": 3, "created_by": "hermes",
 "created_at": "2026-10-21T15:00:00+00:00", "week": "2026-W43", "client_ref": "h-20261021-1",
 "payload": {"item_ids": ["..."], "batch": "week-43"}, "needs_approval": true, "status": "applied",
 "checks": {"limits_ok": true, "items": {"2026-10-20-coloring_pages-axolotl-garden-1a2b": "<content_digest>"}},
 "history": [{"ts": "...", "status": "waiting_approval", "by": "hermes"}, {"ts": "...", "status": "applied", "by": "hermes"}],
 "approved": {"by": "hermes", "at": "..."}, "applied_at": "...", "result": {"approved": ["..."]},
 "undo": {"kind": "hold_items", "item_ids": ["..."], "reason_code": "undo_approval"}}
```
- `id`: `<prefix>-<YYYYMMDD>-<NNNN>` (Botsy's local day), prefix per kind (`pl` weekly_plan, `pr` plan_revision, `ph` plan_hold, `ps` plan_start_now, `su` scale_up, `oc` order_cancel, `pa` pause, `re` resume, `lc` lower_cap, `rc` raise_cap, `rj` review_reject, `rh` review_hold, `ra` review_approve, `rb` resume_after_budget_stop, `si` search_ideas). `week`: the plan's `plan_week` for plan kinds, else the ISO week on `schedule.timezone`. `created_by`: `hermes` (botsy-control always sets it), `user` (CLI, dashboard), `botsy` (its own plan). The same `client_ref` from the same sender gives back the stored order (`duplicate: true`); with another payload it is refused (`client_ref_reused`).
- `status`: `waiting_approval` → (`approve`) → `applied`; `accepted` → `applied`; then `undone`, `cancelled` (order_cancel) or `failed` (the effect raised, or a direct approval was refused). `needs_approval` is true for Level 3 orders and plans that wait for you; `checks.approval_reason` says why for plans. `approved` records who approved (`user` | `hermes`). Plan orders also get `revision` (plan_revision), `plan_number` (when the caller gives `approved_plans`) and `superseded_by` (a newer revision).
- Kinds, levels and payloads (each value has a fixed type: ids, niches from `settings.niches`, sizes 1-500, money, ISO weeks `YYYY-Www`, reason codes from fixed lists):

| kind | level | payload | effect |
|---|---|---|---|
| weekly_plan | 2 (waits for your approval for the first 4 plans and above the auto share) | `plan_week`, `lines` [1-40 × {`niche`, `brief_id`, `sizes` [1-8]}], `kdp` [0-2 × {`brief_id`, `template`}] | recorded; the plan service reads it (or its registered handler runs) |
| plan_revision | as weekly_plan | the same + `replaces` (order id) | as weekly_plan; marks the old one `superseded_by` |
| plan_hold / plan_start_now | 2 | `plan_week` | marks the week's plan `held` / `start_now` (each lifts the other), then the handler; `submit` of plan_start_now also starts the plan at once (result `plan`) |
| scale_up | 2 | `plan_week`, `winner_ref`, `reason_code` (sales_winner, interest_winner, kdp_winner), `actions` [1-3 × {`kind`, `niche`, `brief_id`, `size`/`sizes`, `adjacency_ref`, `set_id`, `kdp_template`}], `max_cost_usd` | always refused for now: `no_winners_yet` (while `scale_up.enabled` is false or no winners file exists), else `scale_up_not_built` |
| order_cancel | 2 | `order_id` | the target becomes `cancelled` (plans, search ideas, orders still waiting for approval) |
| pause | 2 | `reason_code` (user_request, incident, spend, quality, other) | control state `paused` |
| resume | 2; 3 after Botsy's own stop, or when Hermes lifts your pause | — | clears `paused` (refused with `control_state_unreadable` while the control file cannot be read) |
| lower_cap | 2 | `cap_usd` (1-10000; 0 would mean "no cap" and is refused) | control state `cap_override_usd` |
| raise_cap | 3 | `cap_usd` | `cap_override_usd`, never above `cap_ceiling_usd`; also ends a budget stop |
| resume_after_budget_stop | 3 | — | clears `budget_stop` (and a pause by Botsy) |
| review_reject | 2 | `item_id`, `reason_code` (print_defect, off_theme, low_quality, lookalike, trademark_risk, medical_wording, duplicate, wrong_size, user_dislike, other_rule) | `qa.review.reject` |
| review_hold | 2 | `item_id`, `reason_code` (needs_a_look, possible_lookalike, possible_trademark, possible_medical, print_check, user_request) | adds `hold: <reason> (order <id>)`; an approved item goes back to `pending` |
| review_approve | 3 (your approval; one approval may cover a named `batch`) | `item_ids` [1-200, at most `approve_batch_max`], `batch` | `qa.review.approve` for each, all or none, only if each item still has the `content_digest` it was checked with; `decision_note` names who approved and the order |
| search_ideas | 1 (stored only) | `ideas` [1-20 × {`niche`, `query`: lowercase letters, digits, spaces, `'&-`, at most 60}] | none; blocklist-flagged ideas are listed in `result.flagged` |

- `validate(order, settings=None, limits=None, capacity=None, *, store=True, now=None, cost_usd=None, context=None, content_check=None)` runs the stages in order and stops at the first that fails, returning `{"ok": false, "stage", "reasons": [codes], "fields": [schema paths]}`; with `store=True` the checks and the write happen under `data/orders/.lock`. Stages and their reason codes:
  - `format`: `not_an_object`, `bad_schema`, `unknown_kind`, `bad_created_by`, `unknown_field` (the sender's field name is never echoed), `missing_field`, `bad_field`, `client_ref_reused`;
  - `content`: the caller's `content_check(order) -> [codes]` (the plan service checks its briefs, e.g. `no_current_brief`);
  - `switches`: `limits_unavailable`, `paused` (start-now while paused; restarting after a budget stop while you paused), `already_paused`, `not_paused`, `no_budget_stop`, `control_state_unreadable` (resume or resume_after_budget_stop while data/control/state.json cannot be read: resuming would forget what it held), `wrong_week` (a plan is for this or next week), `plan_exists` (one plan per week; later ones are revisions), `no_plan`, `no_plan_to_revise`, `too_many_revisions` (`schedule.max_revisions` revisions; every stored revision counts, undone or cancelled too, and a new weekly_plan after a cancel counts as a version: at most 1 + max_revisions plan orders a week), `plan_needs_approval`, `plan_already_started`, `plan_held` and `before_start_time` (apply only, below), `brief_niche_mismatch`, `no_winners_yet`, `scale_up_not_built`, `no_such_order`, `not_cancellable`, `item_unknown`, `item_not_pending`, `item_failed_qa` (no `qa_report.json` with `passed: true`), `item_not_ready` (no `content_digest`), `already_rejected`, `already_drafted` (a real `publish.json`), `blocklisted_term`, `health_claim`;
  - `limits`: `change_limit_reached` (Hermes's Level 2 orders and undos in 60 minutes; stop-only kinds and your own orders are not counted), `search_ideas_limit` (Hermes, per ISO week), `batch_too_large`, `level3_daily_limit` (a direct Level 3 approval when `level3_per_day` approvals happened in the last 24 hours; a stamp later than now counts too);
  - `capacity` (plans, start-now, scale-ups): capacity/v1 reasons; a plan stops on `review_queue_full`, `drafts_waiting`, `disk_low`, `money_unknown`, while `money_below_warn_line` and `etsy_quota_low` stop only scale-ups;
  - `cost`: `over_cap` (spent this month + the plan's cost above the cap in force), `money_unknown`, `above_ceiling`, `not_a_raise`, `not_lower`.
- Plan money: always Botsy's own read (`capacity.money`: the ledger and the cap in force) for the month the plan starts in (its week's `schedule.auto_start`, so a plan posted on the last Sunday of a month is checked against the next month). A `capacity` result the caller hands in can only make it stricter (a higher `spent`, a lower `cap`, same month), never looser; its queue and disk reasons still apply.
- Plan cost: the caller's `cost_usd` (Botsy's own estimate), else `estimate_cost(payload)`, the fixed table of docs/OVERSEER.md A3 (largest size fresh: coloring $0.15 a page, stickers $0.07 a sticker, procedural niches $0.20 a pack; each smaller size cut from it $0.15; each KDP book $3.00). `plan_needs_approval(cost_usd, settings, limits, *, approved_plans, approve_each=False, cap=None) -> (bool, reason)`: `first_plans` (fewer than `schedule.approvals_before_auto` approved), `approve_each`, `limits_unavailable`, `plan_over_auto_share` (above the smaller of `schedule.auto_start_max_cost_share` and limits `plan.max_cost_share_auto` of the cap in force), `money_unknown`; `context` without `approved_plans` counts as 0, so the plan waits for you.
- `submit(order, settings=None, *, ..., apply_now=None)`: validate and store, then act. A Level 3 order from `user` or `hermes` (review_approve, raise_cap, resume_after_budget_stop, a resume that is Level 3) is your approval: it is applied at once through `approve()` (result `applied`; if that refuses, the order becomes `failed`). From `botsy` it waits (`waiting_approval`). The small kinds (pause, resume, lower_cap, review_reject, review_hold, order_cancel, plan_hold, plan_start_now, search_ideas) are applied at once. Plans wait: the plan service applies them at their start time, or you approve one that waits for you.
- `apply(order_id, settings=None, *, now=None)` (an `accepted` order) and `approve(order_id, settings=None, *, by, now=None)` (a `waiting_approval` order; `by` is `user` or `hermes`, else `not_an_approver`): under `data/orders/.lock` from the first check to the effect, so nothing changes in between. They check the switches (and the cap rules) again first (`superseded` for a replaced plan, `not_applicable` for one already applied or cancelled, `approval_required` when apply meets a waiting order, `not_waiting` when approve meets one that does not wait, `state_changed` for an accepted resume whose pause is now yours or Botsy's), then `approve` checks `level3_per_day` (`level3_daily_limit`), then the effect runs and `undo` is recorded. A failing effect sets `failed` (`apply_failed`). Review approvals are all or none: if any item is no longer pending or was regenerated since the check, none is approved (`state_changed`). After an approval: the `level3` counter, `order_approved` in the journal and, except for plan kinds (service/plan.py posts `plan_approved` itself), one urgent post in the main Discord channel (`approval_text`: what changed, who asked, how to undo it; fixed words, ids and money only); the result has `announced`.
  - Plans: every plan is checked against the money again (`over_cap`, `money_unknown`; it keeps waiting). A plan that waits for you cannot be approved after its deadline (`plan_expired`). A plan that starts by itself starts only at `plan_start_at(order, settings)`: its week's `schedule.auto_start` (Mon 08:00 New York time) and at least `schedule.revision_min_hours` (2) after it was made, so neither a revision nor a new plan skips your Hold window (`before_start_time`), unless a plan_start_now marked it; never while a plan_hold holds it (`plan_held`); and when it would now need your approval (the cap was lowered, the limits file broke) it becomes `waiting_approval` and is refused with `plan_needs_approval`. A plan you approve starts at once.
- `undo(order_id, settings=None, *, by="user")`: Hermes (`by="hermes"`) may undo only orders it created, except undos that only stop work or make things stricter (pause again, put approved items back on hold, cancel a plan, lower a raised cap, stop paid work again); otherwise `not_hermes_order`, so Hermes cannot lift your pause, raise the cap you lowered, take back your rejection or hold, or bring back what you cancelled (resume and raise_cap are Level 3 approvals instead: counted and posted at once). It performs the recorded undo: resume / pause again, restore the cap that was in force (never above the ceiling), put approved items back on hold (an undo never approves), restore a rejected entry as pending, remove a hold reason, give a cancelled order back its earlier status (unless another plan took its place), or tell the plan handler. Refused with `state_changed` when the same thing changed since, `nothing_to_undo` for an order not applied or already undone.
- `register_handler(kind, apply=None, undo=None)`: the plan service performs plan kinds (`apply(order, settings, now) -> dict`).
- Rate counters: `data/orders/counters.json` `{"schema": "order-counters/v1", "events": [{"ts", "counter": "changes" | "search_ideas" | "level3", "by", "n"}]}`, kept 8 days; `counts(settings)` shows the current use (`level3_last_24h` counts every approver). `level3_room(settings, *, now=None)` and `level3_take(settings, action, by, *, now=None)` (service/plan.py `plan auto` counts on the same counter).
- Journal (core/events.py), fixed fields only: `order_accepted` (order_id, order_kind, level, created_by, week, needs_approval, status), `order_refused` (order_kind, created_by, stage, reasons, fields), `order_applied` (order_id, order_kind, created_by, week, approved_by?), `order_approved` (order_id, order_kind, level, by, count, announced), `order_undone` (order_id, order_kind, by), `order_failed` (order_id, order_kind, stage, reasons), `control_refused` (tool, reasons: botsy-control refused an act call before it became an order). No `client_ref`, no payload text.
- Control state `data/control/state.json`, written only by service/orders.py:
  `{"schema": "control/v1", "paused": {"by", "since", "order_id", "reason_code"} | null, "cap_override_usd": 40.0 | null, "cap_changed": {...} | null, "budget_stop": {"month": "2026-10", "since", "cap_usd", "reason_code"} | null}`.
  - `effective_cap(settings)`: the override when set, else `spend.monthly_cap_usd`; never above `cap_ceiling_usd`, whichever it comes from (a cap typed into settings.local.yaml cannot pass the admin's ceiling). With the limits file missing or broken, an override can only lower the setting and the shipped ceiling ($100) applies. A null cap with no override stays no cap. It reads the limits file on every call. `check_budget` and `budget_state` in core.spend use it (wired at integration).
  - `paid_work_block(settings, *, now=None) -> "paused" | "budget_stop" | None`: the tick skips paid stages while it is set (wired at integration). `note_budget_stop(settings, *, month=None)`: the tick records the stop when it records `budget_reached`; it ends by itself when the UTC month changes or when the cap in force rises above the stored `cap_usd` (the admin raised `spend.monthly_cap_usd`), otherwise only through raise_cap or resume_after_budget_stop (Level 3 approvals).
  - An unreadable or damaged file (or a cap override that is not a positive number) counts as paused by Botsy (`control_state_unreadable`), so a lost pause or lower cap never means "go". Stricter orders still work (lower_cap, the budget stop); the first write keeps a copy of the damaged file as `state.json.unreadable-<UTC time>` and writes the pause on. Resuming is refused while the file itself cannot be read.

### capacity/v1: the capacity check (`service/capacity.py`, nothing stored)

`check(settings=None, paths=None, *, limits=None, planned_usd=0.0, now=None, disk_usage=None) -> dict`, pure reads (no paid calls, nothing written):

```json
{"schema": "capacity/v1", "ok": false, "reasons": ["review_queue_full"], "checked_at": "2026-10-21T15:00:00+00:00",
 "facts": {"month": "2026-10", "spent": 9.1, "cap": 25.0, "money_left": 15.9, "planned": 6.4, "projected": 15.5,
           "warn_line": 20.0, "review_queue": 31, "waiting_drafts": 3, "waiting_drafts_source": "snapshot",
           "etsy_quota": {"day": "2026-10-21", "limit_per_day": 10000, "remaining_today": 9877, "share_left": 0.9877,
                          "rate_limited_today": 0, "today": true},
           "kdp_slots_left": null, "disk_free_mb": 48211.5},
 "thresholds": {"max_review_queue": 30, "max_waiting_drafts": 40, "min_etsy_quota_share": 0.3, "min_disk_free_mb": 2000}}
```
- Reasons: `money_below_warn_line` (spent + `planned_usd` at or over `spend.warn_at` × the cap in force), `money_unknown` (the cap setting or ledger cannot be read), `review_queue_full` (pending entries above `capacity.max_review_queue`), `drafts_waiting` (Etsy drafts above `capacity.max_waiting_drafts`: the newest `performance/v1` snapshot's `drafts`, else the real publish records, an upper bound), `etsy_quota_low` (today's `share_left` below `capacity.min_etsy_quota_share`, or an HTTP 429 today, from quota/v1), `disk_low` (free space for data/ and the output folder below `capacity.min_disk_free_mb`).
- It reads the output Botsy works on: `paths.output_dir` and `publisher.performance_dir` when `service.live`, else `pipeline.dry_run_output_dir` and its `performance/`. `paths` may override `review_dir`, `performance_dir`, `kdp_queue` (`data/kdp_queue.json`: `slots_left`, read when the KDP queue exists, else null) and `disk` (a list of folders). Thresholds come from the limits file; with it missing or broken, the shipped defaults.
- `money(settings, *, planned_usd=0.0, now=None)` gives the money facts alone (the plan cost stage reads them).

## 9. Weekly plan files: `data/plans/` (gitignored; `service/plan.py`)

docs/OVERSEER.md Part B. Settings `plan:` (`dir`, `max_packs`, `brief_max_age_days`, `stale_days`, ...) and `schedule:` (New York times). No codes (decided 2026-10-21): a plan that waits for you is approved with a plain `botsy service plan approve` (or Hermes for you), counted against limits `level3_per_day`, posted at once (urgent, `plan_approved`) with who asked and how to undo it.

- `data/plans/state.json` (`plan-state/v1`): `approved_count`, `mode` (`approve` | `auto` | null = the default), `counted_weeks`, and `plans` `{"<week>": {status (waiting | running | held | expired | done | cancelled | none | missed), order_id, created_by, needs_ok, ok_reason (first_plans | approve_each | plan_over_auto_share | limits_unavailable | money_unknown), cost_usd, packs, times {post, no_plan_at, reminders, auto_start, deadline}, reminders_sent, notices, themes {brief_id: theme}, dropped {brief_id: reason}, started_at, counted, first_check (pending | done | gave_up), progress}}`. The order files in `data/orders/` stay the truth; the state is synced from them on every run.
- `data/plans/<week>/facts.json` (`plan-facts/v1`): the facts Botsy plans from: money (month, spent, cap, money left, the week's share), capacity reasons, current briefs per niche (our own aggregated briefs only: id, niche, theme, sizes, opportunity, date), KDP slots, scale-ups (`no_winners_yet`), last week's results.
- `data/plans/<week>/plan.json` (`plan-draft/v1`): Botsy's own deterministic draft: `lines` [{niche, brief_id, theme, sizes, opportunity, brief_date}], `skipped` [{brief_id, reason: brief_too_old | already_made | theme_cooldown | duplicate_theme | too_many_packs | over_budget | size_not_in_brief}], `cost_usd`, `order` {order_id, status, stage, reasons}.
- `data/plans/<week>/plan_due.json` (`plan-event/v1`, signed with `core.events.sign`, `BOTSY_EVENT_KEY`): kind `plan_due`, ts, plan_week, draft_order_id, draft_status, needs_approval, approval_reason, packs, cost_usd, money {month, spent, cap, money_left}, capacity_reasons, kdp_slots_left, briefs, facts_digest, deadline, auto_start, max_revisions, reason, sig. POSTed to `plan.hermes_events_url` only when that is a 127.0.0.1 / ::1 address.
- Journal: `plan_due`, `plan_posted`, `plan_reminder`, `plan_expired`, `plan_started`, `plan_approved` (by, urgent), `no_plan`, `plan_missed`, `plan_line_dropped`, `plan_mode_changed` (to auto: urgent), `plan_held`, `plan_done` (core/events.py KINDS lists their fields).
