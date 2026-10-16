# Themed packs, new niches, low-competition research (user, 2026-10-16)

Products are themed packs, not single images: "Summer Coloring Pages, 20 Pages", "Animal Stickers, Set of 5", "ADHD Daily Planner Pages, 20 Pages", "Fantasy RPG Campaign Tracker for D&D 5e". Every theme yields several packs, each its own Etsy listing.

## 1. Niches

| niche | generator | what a page is |
|---|---|---|
| `coloring_pages` | image model, cleanup, vector | one coloring page (one subject) |
| `stickers` | image model, background removal | one sticker (one subject) |
| `activity_sheets` | procedural (reportlab) | one maze, word search, tracing or dot-to-dot page |
| `stationery` (new) | procedural (reportlab), optional AI line-art accents | one planner or productivity page |
| `ttrpg` (new) | procedural (reportlab), optional AI line-art accents | one tabletop RPG sheet |

- Both new niches are added to `settings.niches` and to shop `main`.
- **stationery page types:** daily planner, weekly planner, monthly planner, time-blocked day, brain dump, task breakdown (one big task into small steps), top-3 priorities, priority matrix (urgent/important), habit tracker, routine checklist (morning/evening), focus session (pomodoro) tracker, mood and energy tracker, to-do list, meal planner, budget tracker, reading log, gratitude page, notes (lined, dot, grid).
  - Stationery themes are styles and audiences: ADHD-friendly (big boxes, clear zones, few lines per box, checkboxes, visual time), minimalist, cozy/cottagecore, botanical, seasonal, student, teacher, work-from-home.
- **ttrpg page types:** character sheet, spell or ability tracker, inventory and loot, campaign tracker, session notes, NPC tracker, quest log, initiative tracker, encounter planner, world-building worksheets (town, faction, deity, region), hex map paper, square grid map paper, dungeon grid, dice and roll log.
  - Themes: genre and tone (classic fantasy, dark fantasy, sci-fi, cosmic horror, cyberpunk, pirate, western, cozy).
  - Game systems come from `config/ttrpg_systems.yaml`. Each entry has `id`, `names` (as it may appear in listing text), `owner`, `disclaimer`, `sheet_fields` (our own field list), and `default: generic` for system-neutral sheets.
  - Layouts are always our own. Never copy an official sheet's layout or trade dress.

## 2. Research: low competition and top-seller pack sizes

- **Opportunity score**, ranked per niche, which combines demand with low competition:
  - `demand`: the existing percentile score;
  - `competition`: percentile of competing listings. From keyword exports' `competition`, otherwise the theme's share of the niche's listing rows weighted by review counts, since many established reviews mean a crowded theme;
  - `sales_per_listing`: percentile of median monthly sales per listing;
  - formula: `opportunity = w_d*demand + w_s*sales_per_listing + w_c*(1 - competition)`. Defaults: `research.opportunity_weights: {demand: 0.4, sales_per_listing: 0.3, low_competition: 0.3}`, renormalized over the metrics the data has.
- Briefs are chosen by opportunity, best first, still behind every support rule (10+/15+ listings, 3+ shops, stage rules). Each brief records `evidence.opportunity` and `evidence.competition`.
- **Top-seller pack sizes:** for each theme, take the listing rows with a parsed pack count (`pack_count`). The top sellers are the top quartile by sales, or by favorites when sales are missing, with at least 5 rows. Their most common count is used, plus the second most common when it has at least 30% share.
  - These sizes are aggregated counts (no listing data), and need at least 3 shops among the top sellers, or they are ignored.
- The brief gains `pack_sizes: [5, 20, <top-seller sizes>]`: sorted, unique, each within `packs.min_size`/`packs.max_size.<niche>` (stickers 60, coloring 50, activity 50, stationery 60, ttrpg 40). `page_count` stays as `max(pack_sizes)` so older readers keep working.
- Niche rules, stopwords, audience and style templates are added for `stationery` and `ttrpg`. Weekly search-list seeds are added too:
  - stationery: "adhd planner printable", "productivity planner pages";
  - ttrpg: "ttrpg character sheet", "hex map paper printable".
- `!term` (game system) mentions in competitor rows do NOT mark the row as licensed-property, so they don't trigger the blocked co-occurrence drop. The term is still cut out of themes and keywords.

## 3. Pack engine: design sets, fresh and derived packs

- A **design set** is the pages generated for one theme: `<out_root>/sets/<set_id>/set.json` plus `pages/`.
  - `set_id = <date>-<niche>-<theme-slug>-<4hex>`.
  - `set.json` holds `{schema: "set/v1", set_id, niche, theme, brief_id, created, pages: [{page_id, type, subject, spec, asset, quality, used_in: [item_id]}]}`.
  - A page is either a procedural spec (type + params + seed, rendered at any page size) or an image asset (cleaned PNG/SVG), or both.
- **Plan** (`generators.packs.plan(brief, settings, state) -> list[PackSpec]`): one `PackSpec{size, source: "fresh"|"derived", set_id}` per size in `brief.pack_sizes`. Then:
  - The largest size always gets a fresh design set of that many pages for the theme. It is the theme's master set.
  - A smaller size is `derived` (curated subset of the master set) unless the fresh policy says the next pack of that size must be fresh. Fresh means new pages generated for this pack only, never reused by another pack.
  - **Fresh policy** (user): `packs.fresh_every: {5: 3, 20: 6}`. Out of every 3 five-page packs, 2 are derived and the 3rd is fresh. Out of every 6 twenty-page packs, 5 are derived and the 6th is fresh. Other sizes use `packs.fresh_every_default: 0` (never forced fresh).
    - Counters are per shop, niche and size in `data/packs_state.json` and advance only when the pack's item is generated successfully.
    - A size equal to the largest is always fresh (it is the master) and does not advance its counter.
- **Curation** of a derived subset of k pages from a set:
  - pick by `quality` (QA-style metrics such as ink balance, line weight and blank check, or 1.0 for procedural);
  - prefer variety: different subjects and page types, round-robin across types;
  - prefer pages not yet used by another derived pack of the same size;
  - deterministic for the same inputs.
- **Subject planning** (coherent themes): `generators.packs.subjects(theme, niche, n, *, settings, client=None, dry_run=False) -> list[str]` returns n distinct subjects for the theme. For example, "summer" gives beach ball, sandcastle, ice cream cone, sunflower, sailboat and so on.
  - It uses the text model (`claude-opus-5-5`, structured output, spend-tracked). The prompt holds only our theme name, the niche, the audience and the rules (no brands or characters, kid-friendly when the audience is kids).
  - Results are blocklist-checked, deduplicated, and topped up from built-in tables.
  - Dry runs use only the tables.
  - Procedural niches plan page types and params instead: a 20-page ADHD planner pack mixes daily, brain-dump, task-breakdown, habit and focus pages in sensible proportions.
- **Items:** every pack is one item: `output/<item_id>/`.
  - `item_id = new_item_id(date, niche, theme, salt=f"{size}-{source}-{n}")`.
  - The manifest gains `pack: {size, source, set_id, page_ids, theme, page_types}` (plus the existing pack fields).
  - Files: the pack's PDFs for both page sizes (stickers: ZIP + sheet PDFs), assembled from the chosen pages.
- `generators.generate(brief, ...)` keeps its signature and now returns one item dir per planned pack. The pipeline's `max_items` counts packs.
- **Theme cooldown** (SERVER.md section 13) keys on (shop, niche, theme, size). The master set for a theme is reused for 60 days for new derived packs, instead of generating a new set.

## 4. Compliance

- **Blocklist markers:** plain = hard block, `?term` = review, `!term` = game-system name.
  - A `!term` is allowed only in a `ttrpg` listing's title, tags and description, and only when a system from `config/ttrpg_systems.yaml` is set on the item.
  - It is blocked everywhere else: briefs, prompts, designs, printed text, file names, mockups, and other niches' listings.
  - API: `Blocklist.check(*texts, allow_systems=())` treats the `!` terms of the allowed systems as clean.
- Hard-block official D&D and other TTRPG settings and product-identity creatures. Add, never remove: Beholder, Mind Flayer, Illithid, Displacer Beast, Githyanki, Githzerai, Forgotten Realms, Baldur's Gate, Waterdeep, Drizzt, Critical Role, Vox Machina, Mighty Nein, Exandria, Golarion, Warhammer, Magic: The Gathering, Wizards of the Coast logo terms.
  - `!` entries: Dungeons & Dragons, D&D, DnD, 5e, Fifth Edition, Pathfinder, Starfinder, Call of Cthulhu, Wizards of the Coast, Paizo, Chaosium (owners are needed in disclaimers).
- **ttrpg listings that name a system** append that system's disclaimer, for example: "Unofficial fan-made product, not affiliated with or endorsed by Wizards of the Coast. Dungeons & Dragons and D&D are trademarks of Wizards of the Coast."
- **Medical claims:** listing copy may not use: treat, cure, heal, diagnose, therapy, therapeutic, clinically, medical, prescription, symptom relief. "ADHD-friendly" and "for ADHD brains" are allowed as layout descriptions. Flagged in the listing rules and enforced by the validator.
- Stationery planner brands are review terms: Happy Planner, Erin Condren, Hobonichi, Passion Planner, Panda Planner, Clever Fox, Bullet Journal, BuJo.

## 5. Listing, QA, mockups

- **Overlap is stated plainly** (user-approved recommendation, 2026-10-16):
  - a derived pack's description says "These N pages are also included in our <size>-page <theme> pack";
  - the master pack says which smaller packs it contains;
  - a fresh pack says "All-new pages, not included in our other packs".
  - Nobody should buy the same page twice by accident; it prevents bad reviews and refund requests.
- **Titles** carry the pack: "…, 20 Pages", "Set of 5 …", and for ttrpg the system when set ("… for D&D 5e"). The description says what is in the pack (page types and counts) and that bigger or smaller packs of the theme exist, without links.
- **QA checks:**
  - the pack page count equals `pack.size` (plus title/answer pages where the niche adds them);
  - no duplicate page within a pack (page_ids unique; image near-duplicate check by perceptual hash for image niches);
  - fresh packs share no page_id with any other item;
  - stationery and ttrpg: minimum printed font size 7 pt and writing lines at least 6 mm apart;
  - `!` terms never in printed text, file names or mockup text.
- **Mockups:** the hero shows the pack's pages fanned out with a size badge ("20 PAGES", "SET OF 5"). The grid shows the pack's actual pages. Stationery and ttrpg get a desk scene.
