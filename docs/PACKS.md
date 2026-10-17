# Themed packs, new niches, low-competition research (user, 2026-10-16)

Products are themed packs, not single images: "Summer Coloring Pages, 20 Pages", "Animal Stickers, Set of 5", "ADHD Daily Planner Pages, 20 Pages", "Fantasy RPG Campaign Tracker for D&D 5e". Every theme yields several packs, each its own Etsy listing.

This file describes what is built. File formats are in `docs/CONTRACTS.md`; the theme cooldown is in `docs/SERVER.md` section 13.

## 1. Niches

| niche | generator | what a page is |
|---|---|---|
| `coloring_pages` | image model, cleanup, vector | one coloring page (one subject) |
| `stickers` | image model, background removal | one sticker (one subject) |
| `activity_sheets` | procedural (reportlab) | one maze, word search, tracing or dot-to-dot page |
| `stationery` | procedural (reportlab), optional AI line-art accents | one planner or productivity page |
| `ttrpg` | procedural (reportlab), optional AI line-art accents | one tabletop RPG sheet |

- All five are in `settings.niches` and in shop `main` (`config/settings.yaml`; default price 4.50 for the two new niches, to confirm). QA's `manifest_schema` check refuses an item whose niche `settings.niches` does not list.
- Each generator is a plugin of the pack engine (`generators/<niche>/__init__.py`): `PAGE_TYPES`, `plan_pages(ctx, n, *, seed, settings)`, `render_pages(pages, out_pdf, *, page_size, settings, ctx)`, `preview(...)`, and `generate(brief, **kw)` = `generators.packs.generate_packs(brief, plugin=<module>, **kw)`. Optional hooks are listed in `generators/packs/engine.py`. Every generator also has a CLI: `python -m generators.<niche> --brief FILE [--index N | --all] [--dry-run] [--out DIR]`.
- The AI accents (`stationery.ai_accents`, `ttrpg.ai_accents`) are off by default. They build their own prompts (never from brief style notes), run only in real runs and use the image client and its spending checks.

### Stationery (`generators/stationery`, settings `stationery:`)
- **Page types:** daily planner, weekly planner, monthly planner, time-blocked day, brain dump, task breakdown (one big task into small steps), top-3 priorities, priority matrix (urgent/important), habit tracker, routine checklist (morning/evening), focus session tracker, mood and energy tracker, to-do list, meal planner, budget tracker, reading log, gratitude page, notes (lined, dot, grid).
- **Styles** come from the theme: ADHD-friendly (big boxes, clear zones, few lines per box, checkboxes, visual time), minimalist, cozy/cottagecore, botanical, seasonal, student, teacher, work-from-home. A theme that matches no style gets a plain style and a review reason, because nothing on the pages would show that theme.
- Pages are undated. `stationery.week_start: auto` starts weeks on Sunday for US Letter and Monday for A4. At least 80% of a pack's pages are different page types (`unique_type_share`).
- The printed timed-work page is a "focus session" tracker; pages and copy never say "Pomodoro" (a review term).
- Manifest `pack` adds `style`, `seasons`, `undated`, `ai_accents`, `contents[] {page, type, variant, title}`, `hero_page` (the page the style ranks highest; QA's cover photo leads with it), `layout_reused_pages`, `min_font_pt`, `min_line_gap_mm`, `print_checks`.

### Tabletop RPG (`generators/ttrpg`, settings `ttrpg:`)
- **Page types:** character sheet, spell or ability tracker, inventory and loot, campaign tracker, session notes, NPC tracker, quest log, initiative tracker, encounter planner, world-building worksheets (town, faction, deity, region), hex map paper, square grid map paper, dungeon grid, dice and roll log.
- **Focus** comes from the brief's theme only, never from keywords: campaign (default), character, maps, gm, worldbuilding or journal. A genre theme ("dark fantasy") gets the campaign mix, whose 5-page pack is the "campaign starter" (`pack.kit`).
- **Genre and tone** (classic fantasy, dark fantasy, sci-fi, cosmic horror, cyberpunk, pirate, western, cozy) set our own borders and corner art (`ttrpg.default_genre: classic_fantasy`).
- **Game systems** come from `config/ttrpg_systems.yaml`, read through `core/systems.py` (`load_systems`, `names_for`, `label_for`, `disclaimer_for`, `sheet_fields_for`, `detect_systems`, cached). Each entry has `id` (stored in briefs and manifests, never trademark text), `label` (how a listing names it, "D&D 5e"), `names` (spellings in listing text, label first), `owner`, `owner_names`, `disclaimer` and `sheet_fields`: OUR OWN field list from the game mechanics, in groups `core`, `details`, `spells`, `combat`, `currency`, `spell_card` (a missing group is taken from the default system). `default: generic` is the system-neutral entry. Shipped ids: `generic`, `dnd5e`, `pf2e`, `coc7e`.
- Layouts, art and wording are always our own. Never an official sheet's layout or trade dress, logos, official art, setting names or product-identity creatures.
- Manifest: `system` (the id, or `generic`); `pack` adds `genre`, `focus`, `kit`, `system_fields`, `undated`, `ai_accents`, `type_labels`, `type_counts`, `contents[] {page, type, variant, title, subtitle}`, `min_font_pt`, `min_line_gap_mm`, `print_checks`.

## 2. Research: low competition and top-seller pack sizes

- **Opportunity score**, ranked per niche, which combines demand with low competition:
  - `demand`: the existing percentile score;
  - `competition`: percentile of competing listings. From keyword exports' `competition`, otherwise the theme's share of the niche's listing rows weighted by review counts, since many established reviews mean a crowded theme;
  - `sales_per_listing`: percentile of median monthly sales per listing;
  - formula: `opportunity = w_d*demand + w_s*sales_per_listing + w_c*(1 - competition)`. Defaults: `research.opportunity_weights: {demand: 0.4, sales_per_listing: 0.3, low_competition: 0.3}`, renormalized over the metrics the data has.
- Briefs are chosen by opportunity, best first, still behind every support rule (10+/15+ listings, 3+ shops, stage rules). Each brief records `evidence.opportunity`, `evidence.competition` and `evidence.top_seller_sizes`; `patterns.themes[]` gain `competition` and `opportunity`.
- **Top-seller pack sizes** (`research.top_sellers: {quantile: 0.75, min_rows: 5, second_share: 0.3}`): for each theme, take the listing rows with a parsed pack count. The top sellers are the top quartile by sales, or by favorites when sales are missing, with at least 5 rows. Their most common count is used, plus the second most common when it has at least 30% share. These sizes are aggregated counts (no listing data) and need at least 3 shops among the top sellers, or they are ignored.
- The brief gains `pack_sizes`: `research.base_pack_sizes` (`[5, 20]`) plus the top-seller sizes, sorted, unique, each moved inside `packs.min_size` (5) / `packs.max_size.<niche>` (stickers 60, coloring 50, activity 50, stationery 60, ttrpg 40). `page_count` is `max(pack_sizes)` so older readers keep working.
- Niche rules, stopwords, audiences and style notes for `stationery` and `ttrpg` live in `research/niches.py` and `research/defaults.py` (deep-merged per niche; settings list only the first three niches' rules).
- **ttrpg system:** a ttrpg brief has `system`: the registry id named by at least `research.system_share` (0.5) of the theme's clean supporting rows (listing and keyword rows), else `"generic"`. The brief stores only the id, never the name. An id the registry does not list, or one the brief guard refuses, becomes `"generic"` with a warning.
- **What research cuts from themes and keywords, in every niche:** game-system names, owners and editions (`!` blocklist terms and every registry spelling), Wizards of the Coast rulebook titles, planner and VTT brands, and medical-claim words (outside the listing's ok phrases such as "trick or treat"; `research.medical_terms` can only add, `listing.medical_ok_phrases` can only shrink). A row naming a `!` term is NOT licensed property: it still counts and triggers no blocked co-occurrence drop.
- **Weekly search list** seeds and nouns (`service.search_list`): stationery "adhd planner printable", "productivity planner pages" (noun "planner pages"); ttrpg "ttrpg character sheet", "hex map paper printable" (noun "rpg sheets").
- **Demo data:** `research.demo.write_demo_inputs(dir, niches=DEMO_NICHES)` writes synthetic rows for all five niches (`python -m research --demo` and `data/samples/` use them; tests that count brief files keep the first three by default). Most "classic fantasy" ttrpg rows say "for D&D 5e", so that brief gets `system: dnd5e`; the other ttrpg briefs stay generic.

## 3. Pack engine: design sets, fresh and derived packs (`generators/packs`)

- A **design set** is the pages generated for one theme: `<out_root>/sets/<set_id>/set.json` plus `pages/` (image assets only; procedural pages are rendered from their spec).
  - `set_id = <date>-<niche>-<theme-slug>-<4hex>` (the same shape as an item id).
  - `set.json`: `{schema: "set/v1", set_id, niche, theme, theme_key, shop_id, brief_id, created, kind: master|fresh, size, system, dry_run, model, ai_assisted, pages: [{page_id, type, subject, spec, asset, quality, fit?, used_in: [item_id]}], items: [{item_id, size, source, created}]}`.
  - Page ids are prefixed with the set id, so they are unique across sets.
  - A set is built in a scratch folder and swapped in when complete. Reruns reuse it, so a rerun pays for nothing twice; a dry run never replaces a real (paid) set.
- **Plan** (`generators.packs.plan(brief, settings, state, *, sizes=None) -> list[PackSpec]`, `generators.packs.preview(...)` for the item ids): one `PackSpec{size, source, set_id, n, ...}` per size in `brief.pack_sizes` (or `[page_count]` for an older brief), master first, then the other sizes smallest first.
  - The largest size is `master`: a new design set of that many pages, the theme's master set. Its counter never moves.
  - A smaller size is `derived` (a curated subset of the master set) unless the fresh policy makes it `fresh`: new pages for this pack only, never reused by another pack.
  - **Fresh policy** (user): `packs.fresh_every: {5: 3, 20: 6}`. Out of every 3 five-page packs, 2 are derived and the 3rd is fresh; out of every 6 twenty-page packs (that are not masters), 5 are derived and the 6th is fresh. Other sizes use `packs.fresh_every_default: 0` (never forced fresh).
  - When the brief's master size is cooled down (section 4 below), its other sizes are cut from the theme's master set made within the cooldown, or are fresh when there is none (`reason: "no master set"`).
  - A brief whose pack of a size was made before keeps that assignment (same source, number and set), so a rerun rebuilds the same pack instead of counting a new one.
- **State** (`generators/packs/state.py`): counters per shop, niche and size, plus each brief's assignments. They advance only when the pack's item is generated successfully. The file is `packs.state_file` (`data/packs_state.json`, gitignored) for the configured output folder (`paths.output_dir`); any other output root (dry runs, `--out`, tests) keeps `<out>/sets/packs_state.json`, so a dry run never moves the real counters.
- **Curation** of a derived subset of k pages (`generators/packs/curate.py`), greedy and deterministic. Each pick takes the best remaining page by: quality at least `packs.min_quality` (0.5); not used by another derived pack of the same size; the page type picked least so far (round-robin); theme fit (optional page `fit`, default 1.0); not used by a derived pack of another size; a new subject, then higher quality; a stable hash to break ties. The chosen pages keep their set order.
- **Subject planning** (image niches): `generators.packs.subjects(theme, niche, n, *, settings, client=None, dry_run=False)` / `plan_subjects(..., minimum=)` return distinct subjects for the theme ("summer": a beach ball, a sandcastle, an ice cream cone, a sunflower, a sailboat, ...).
  - It uses the text model (`claude-opus-5-5`, structured output, `packs.subjects.effort: low`, refusal fallback, spend-tracked). The request holds only our theme name, the niche, a fixed audience phrase and the rules (no brands or characters, kid-friendly for kids).
  - Results are blocklist-checked (`!` names count as blocked), deduplicated and topped up from the built-in tables (`generators/imaging/subjects.py`). Dry runs, `use_model: false`, a missing key or a failed request use only the tables. A theme that names one season uses that season's table, and subjects that clash with it are left out (no snowmen in a summer pack).
  - Procedural niches plan page types and params instead: a 20-page ADHD planner pack mixes daily, brain-dump, task-breakdown, habit and focus pages.
- **Items:** every pack is one item, `output/<item_id>/`, with `item_id = new_item_id(date, niche, theme, salt=f"{brief_id}:{size}-{source}-{n}")`.
  - Manifest `pack`: `size`, `source`, `n`, `set_id`, `theme`, `page_ids`, `page_types`, `type_counts`, `related: [{size, source, item_id, same_set}]` (a snapshot of the theme's other packs when the item was made; the current list is the set's `items[]`), plus the niche's own fields. File names carry the pack size (`classic-fantasy-rpg-20-pages-us_letter.pdf`).
  - Every printed string is checked before the PDF is kept: the plugin's `printed_text(pages, ctx[, settings=])` (called with the run's settings when it takes them) against the blocklist, with `!` names and every registry spelling (`core.compliance.system_mentions`) counted as blocked.
  - Dry runs never cap a pack below its size; their fake images are small instead (`packs.dry_run_fake_long_side_px: 640`).
- `generators.generate(brief, ...)` keeps its signature (plus `sizes=`) and returns one item dir per pack made. A pack that fails stops the brief; the packs made before it stay and the exception carries their folders as `pack_dirs`.
- **Pipeline** (`pipeline/run.py`): `max_items` counts packs, reused ones included, tier by tier: the first brief of every niche gets one pack (its master when that is new), then those briefs' other packs, then the second brief of every niche, and so on. Packs left out are made by a later run, cut from the same master set. The run summary lists every pack (`summary["packs"]`) and puts `size` / `source` on each generate record (`[pack of N, source]` in `format_summary`).
- **Cleanup** (`service/cleanup.py`): a practice design set (`set.json` `dry_run: true`) under `pipeline.dry_run_output_dir/sets/` is removed once it is older than `service.dry_run_keep_days` (14) and none of its items is left. Real sets are never removed.

## 4. Theme cooldown per pack size

- The theme cooldown (`docs/SERVER.md` section 13) keys on (shop, niche, theme, pack size). A theme made last week at 5 and 20 pages is skipped at those sizes for 60 days, but a new top-seller size of it may be made (cut from the master set).
- An existing item whose size is unknown (made before packs) blocks every size of its theme.

## 5. Compliance

- **Blocklist markers** (`config/trademark_blocklist.txt`): plain = hard block, `?term` = review, `!term` = game-system name.
  - A `!term` is allowed only in a `ttrpg` listing's title, tags and description, and only for the system set on the item (`Blocklist.check(*texts, allow_systems=(id,))`; `ComplianceResult.allowed` lists what was let through).
  - It is blocked everywhere else: briefs, prompts, designs, printed text, file names, mockups, alt text and other niches' listings.
  - `!` lines never spell a registry id (`dnd5e`, `pf2e`, `coc7e`), because briefs and manifests store ids and are checked with the blocklist. Spellings that look like ids ("PF2e", "CoC 7e") and joined ones ("DnD5e", "DungeonsAndDragons") are caught by `core.compliance.system_mentions`, which the brief guard (`core.briefs`), the pack engine, listing, QA and the publisher all run (never over a `system` id field).
  - `!` entries: Dungeons & Dragons, D&D, DnD, 5e, Fifth Edition, Pathfinder, Starfinder, Call of Cthulhu, CoC 7th Edition, Wizards of the Coast, WotC, Paizo, Chaosium, D'n'D (belongs to no system, so never allowed).
- Hard blocks (add, never remove): official TTRPG settings, places, characters and product-identity creatures (Beholder, Mind Flayer, Illithid, Displacer Beast, Githyanki, Forgotten Realms, Baldur's Gate, Waterdeep, Ravenloft, Eberron, Strahd, Vecna and more), other tabletop franchises (Critical Role, Exandria, Golarion, Warhammer, Magic: The Gathering, Shadowrun, World of Darkness, Gloomhaven and more), and Wizards of the Coast rulebook titles (Player's Handbook, Dungeon Master's Guide, Monster Manual, Adventurers League). Review: "Dungeon Master" (our copy and sheets say "game master").
- Review terms for brands and methods: Happy Planner, Erin Condren, Hobonichi, Passion Planner, Panda Planner, Clever Fox, Bullet Journal, BuJo, Pomodoro, Pomodoro Technique, Getting Things Done, GTD.
- **ttrpg listings that name a system** say "Made for use with <label>. This is an unofficial product of our own design." and append that system's disclaimer, for example: "Unofficial fan-made product, not affiliated with or endorsed by Wizards of the Coast. Dungeons & Dragons and D&D are trademarks of Wizards of the Coast." The publisher refuses a draft whose copy names a system without its disclaimer.
- **Medical claims:** listing copy may not use: treat, cure, heal, diagnose, therapy, therapeutic, clinically, medical, prescription, symptom relief (and more in `listing.DEFAULTS["medical_terms"]`). "ADHD-friendly" and "for ADHD brains" are allowed as layout descriptions. Settings can add terms (`listing.medical_terms`, `listing.misleading_phrases`) and drop ok phrases (`listing.medical_ok_phrases`), never the other way round; research reads the same keys the same way.

## 6. Listing, QA, mockups

- **Overlap is stated plainly** (user-approved, 2026-10-16), from `pack.related` and the set's `items[]`:
  - a derived pack: "These N pages are also included in our <size>-page <theme> pack, so there is no need to buy both.";
  - a master cut into smaller packs: "This pack contains every page of our <size>-page <theme> pack, so there is no need to buy that one as well.";
  - a fresh pack: "All-new pages, not included in our other packs.";
  - every pack: "This theme also comes in packs of <sizes> pages." (no links).
- **Titles** carry the pack ("…, 20 Pages", "Set of 5 …") and, for ttrpg, the system when set ("… for D&D 5e"). A mixed pack's title names only page types it includes. The description lists what is in the pack (page types and counts) and the cover page when there is one.
- **QA checks** (`qa/packs.py`): `pack_page_count` (PDF pages = `pack.size` plus declared `title_pages`, `answer_key_pages`, `extra_pages`), `pack_page_ids` (unique), `pack_duplicates` and `pack_near_duplicates` (image niches, perceptual hash: `qa.packs.duplicate_distance` 3 fails, `near_distance` 6 goes to review), `pack_fresh_pages` (a fresh pack shares no page id with any other item), and for stationery and ttrpg `print_font_size` (7 pt) and `print_line_spacing` (light writing lines at least 6 mm apart, `qa.print_rules`). `!` names and registry spellings are never allowed in printed text, file names or mockup text.
- **Mockups:** the cover photo fans the pack's pages with a size badge ("20 PAGES"; stickers "SET OF 5"), leading with `pack.hero_page` when the pack has one. The grid shows the pack's actual pages. Stationery and ttrpg get a sixth photo, printed pages on a desk (`qa.mockups.desk_niches`).

## 7. Open decisions

- Per-size pricing: every pack of a brief has the brief's theme price, so a Set of 5 costs the same as a 20-page pack. The generator can set `price_usd` per pack once a rule is chosen.
- Prices for the new niches (4.50) and their Etsy taxonomy ids (`listing.taxonomy_ids.stationery` / `.ttrpg`, needed before a real draft).
