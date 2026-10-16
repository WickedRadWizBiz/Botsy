# Amazon KDP books plan
_Researched 2026-10-16 from primary sources (links inline). Plan, not yet built. Decisions in task_state.md._

AMAZON KDP PLAN FOR BOTSY. All sources were read on 2026-10-16.

0. SCOPE
- Botsy follows themes, formats, page counts and price bands aggregated from exports the user downloads, with at least 10 books and 3 authors per pattern. Every book gets fresh original pages.
- It never builds on one specific book. KDP rejects content 'not significantly different from content in another book' (https://kdp.amazon.com/en_US/help/topic/G200952510). Its terms require you to hold all rights, and section 4.3 bars letting a third party use your account (https://kdp.amazon.com/terms-and-conditions).
- I found no public KDP publishing API, so Botsy builds files plus a metadata kit and the user uploads them by hand. No login, no browser extensions, no scraping Amazon.
- Amazon's Creators API cannot feed research. Using it to aggregate or analyze product content needs Amazon's prior written approval (https://affiliate-program.amazon.com/help/operating/policies).

1. BLOCKER FIRST: EMBEDDED FONTS
- KDP requires every font to be embedded (https://kdp.amazon.com/en_US/help/topic/G201857950). Botsy's PDFs use reportlab's built-in Helvetica, which is never embedded:
  - generators/activity_sheets/layout.py:14-15;
  - generators/stationery/styles.py:24-26;
  - generators/imaging/pdf.py:93;
  - the tracing.py glyph metrics.
- Fix: add core/fonts.py to register TrueType fonts and send every font choice through it. Default font: Liberation Sans, which has nearly the same metrics as Helvetica; check its license version.
- Re-derive the tracing start points for the new font.
- The Etsy PDFs benefit too.

2. BOOK TYPES TO START WITH (in this order)
a) coloring_50
- 8.5x11, black ink on white, no bleed, matte. NOT low-content, so it gets the free ISBN.
- Pages: title, copyright ('(c) 2026 <author>. All rights reserved.', no URLs), 'This book belongs to', a color-test page, then 50 single-sided designs, each followed by a blank back. Total 104 pages.
- Cost and royalty: printing $2.84 (flat for 24-110 pages, large trim); at $9.99 the royalty is $3.15 (https://kdp.amazon.com/en_US/help/topic/G201834340 and G201834330).
b) activity_100
- 8.5x11, black on white. Puzzle books are not low-content (https://kdp.amazon.com/en_US/help/topic/GGE5T76TWKA85DJM).
- About 90 maze, word search, dot-to-dot and tracing pages, a 6-10 page answer key and front matter, about 104 pages.
c) planner_undated
- 6x9, black on white or cream, 120-160 pages.
- LOW-CONTENT: no free ISBN, no Expanded Distribution, no series, and the low-content box must be ticked or the book is rejected (GGE5T76TWKA85DJM).
- 150 pages costs $2.80 to print and earns $3.19 at $9.99.
- The no-medical-claims rule applies.
d) ttrpg_campaign_log
- 6x9 or 8.5x11, about 120 pages, low-content, system-neutral by default (decision 13).

Hardcover, bleed and non-US stores come later.

Earnings and sales volumes are unknown; I found no credible source.

3. SPECS TO ENCODE
Keep them as data in config/kdp_specs.yaml. Each block stores its source URL and read date, and QA warns once a block is more than 180 days old. Math lives in core/kdp.py, with unit tests on the worked examples.
- Trims and page ranges (https://kdp.amazon.com/en_US/help/topic/GVBQ3CMEQW3W2VL6):
  - 8.5x11: black/white 24-590, cream 24-550, standard color 72-600, premium color 24-590.
  - 6x9 and 8x10: black/white 24-828, cream 24-776, standard color 72-600, premium color 24-828.
- Large trim: more than 6.12 in wide or more than 9 in tall (G201834340).
- Bleed: +0.125 in width and +0.25 in height. If any page bleeds, the whole interior must be set up with bleed (GVBQ3CMEQW3W2VL6).
- Gutter (inside margin) by page count: 0.375 in (24-150), 0.5 (151-300), 0.625 (301-500), 0.75 (501-700), 0.875 (701-828). Outside margins at least 0.25 in without bleed, 0.375 in with bleed (GVBQ3CMEQW3W2VL6).
- Interior files (G201857950 and https://kdp.amazon.com/en_US/help/topic/G201834260):
  - fonts embedded, transparency flattened, images at least 300 DPI;
  - single pages, no crop marks, annotations or encryption;
  - at most 650 MB;
  - at most 4 blank pages in a row at the start or middle, 10 at the end;
  - text at least 7 pt;
  - page count even.
- Cover (https://kdp.amazon.com/en_US/help/topic/G201953020):
  - spine = pages x 0.002252 in (white or standard color), x 0.0025 (cream), x 0.002347 (premium color);
  - width = 0.125 + back + spine + front + 0.125; height = trim + 0.25;
  - 300 DPI, CMYK, flattened, fonts embedded;
  - under 650 MB (aim for 40 MB);
  - spine text needs 0.0625 in clearance plus 0.0625 in fold variance each side;
  - spine text only from 80 pages (the page says 79 in one place and 'more than 79' in another, so use the safer number).
- Barcode: a 2 x 1.2 in white box in the lower right of the back cover, left empty (GGE5T76TWKA85DJM).
- US printing costs, black ink: 24-110 pages flat $2.30 regular or $2.84 large; 110-828 pages $1.00 + $0.012 or $0.017 per page. Groundwood, standard color and premium color tables from the same page (G201834340).
- US royalty: 60% at $9.99 or more, 50% at $9.98 or less, 40% for Expanded Distribution, then minus printing (G201834330).
- Title limits: 2 creations and 2 submissions per format per week, reset Sunday 00:00 UTC (https://kdp.amazon.com/en_US/help/topic/G202172740).
- Line weight of at least 0.75 pt: an advisory warning only. It is not on KDP Help.

4. INTERIOR (kdp/interior.py)
- Add page-size keys kdp_8.5x11, kdp_6x9 and kdp_8x10 so the existing renderers draw straight at the trim size.
- Use symmetric margin = max(0.5, required gutter). Up to 300 pages that is 0.5 in, which already meets KDP's minimums.
- Drop the Etsy 'personal use' footer.
- Flatten any image with transparency onto white.
- Black-ink books must be grayscale only; QA flags any color pixel.
- Merge the sections (pypdfium2, or add pypdf, which is BSD-licensed) with no outlines, annotations or extra metadata.

5. COVER (kdp/cover.py)
- One reportlab canvas sized by the formula in section 3. Example: 104 pages on white paper gives a 0.234 in spine and a 17.484 x 11.25 in cover (5246 x 3375 px at 300 DPI).
- Front: the exact title, subtitle and author, and art. The default art is a vector collage of the book's own pages, with no resolution limit.
- An optional AI color cover must reach 300 DPI at full size (2588 x 3375 px for 8.5x11), and is then disclosed as an AI-generated image.
- Back: a short blurb with no URLs or prices, 2-4 interior thumbnails, and the barcode box left empty and white.
- Spine: text only when the rule allows it; otherwise a plain colored spine.
- CMYK fills, no transparency.
- Also write a cover_creator/ folder (front JPG at 300 DPI plus the text fields) as a fallback for KDP Cover Creator (https://kdp.amazon.com/en_US/help/topic/G201113520).

6. METADATA KIT (kdp/metadata.py)
Uses claude-opus-5-5 with structured output and is tracked in core.spend. It writes kdp_kit.json and kdp_kit.md with:
- Title exactly as on the cover; title plus subtitle under 200 characters. No 'bestselling' or 'free', no repeated generic words, no trademarks (https://kdp.amazon.com/en_US/help/topic/G201097560).
- Description in allowed HTML only: no URLs, contact details, reviews, prices or time-sensitive words. No medical claims.
- 7 keywords: no words already in the title, no 'book', no program names, no brands, no quotes, no plural variants (https://kdp.amazon.com/en_US/help/topic/G201298500).
- Up to 3 category suggestions, which the user picks in KDP.
- Low-content flag and ISBN choice.
- Print options: trim, ink, paper, bleed, finish, page count.
- Price with the cost and royalty math; Expanded Distribution off by default.
- AI disclosure answers taken from the manifest (https://kdp.amazon.com/en_US/help/topic/G200672390):
  - Gemini art, covers included, is AI-generated images.
  - Claude-written word lists or prompts printed in the book are AI-generated text.
  - Mazes, grids and planners drawn by deterministic code are not AI.
  - Each answer gives its reason.
- File SHA-256s and an upload checklist in KDP's field order.

7. QA (qa/kdp_checks.py; run with `python -m qa kdp <kit_id>`)
- Interior: every page box matches the trim; page count even, within range and matching the kit; fonts embedded (so built-in fonts fail); no encryption, annotations or transparency; images at 300-600 DPI; no ink in the margins; text at least 7 pt; blank runs within limits; grayscale for black-ink books; file size within limits.
- Cover: exact size, barcode box white, spine rule followed, title matches.
- Metadata: blocklist and the field rules above.
- Overlap: a perceptual-hash check between KDP books, with default overlap 0. KDP disallows content 'excessively reused ... across books' (G200952510).
- Failures go to output/review/.

8. WEEKLY UPLOAD QUEUE (kdp/queue.py, data/kdp_queue.json)
- Each kit moves through ready, created, submitted, then live, rejected or withdrawn.
- Weeks start Sunday 00:00 UTC, with 2 creation and 2 submission slots per format.
- `python -m kdp queue` shows the top 2 ready kits by opportunity score and the slots left.
- `python -m kdp mark <kit_id> created|submitted|live|rejected [--asin X]` records what the user did. Botsy cannot see KDP.
- Advise creating a title only in the week it will be submitted, because drafts use up creation slots.
- Generation pauses once there are more than 4 weeks x 2 ready kits, which also protects the $25/month spend cap.
- The digest and Discord show free slots. The Windows helper syncs output/kdp/<kit_id>/ down for upload.

9. RESEARCH INPUT
- research/columns.py gets aliases for KDP exports: asin, bsr, est_sales, price, reviews, pages, category, pub_date, keyword, search_volume.
- Exports go in data/research_inputs/kdp/, from BookBeam or Helium 10, whose terms allow internal business use (https://bookbeam.io/terms-of-service/, https://www.helium10.com/terms-and-conditions/).
- Publisher Rocket's terms 5.2(e) and 5.3 are unclear (https://publisherrocket.com/terms-and-conditions/).
- Briefs never store ASINs, titles, authors or covers.
- Botsy only reads files the user exported by hand.

10. BUILD ORDER
- K0: embedded fonts.
- K1: kdp_specs.yaml and core/kdp.py with tests.
- K2: kdp/plan.py with the 4 templates.
- K3: interior.
- K4: cover.
- K5: qa/kdp_checks.py.
- K6: metadata kit.
- K7: queue, digest and pipeline channel switch (etsy|kdp; the KDP branch never calls the Etsy publisher).
- K8: research aliases.
- Then the user orders one printed proof of each new template before the queue starts submitting books built from it.

See docs/POD.md for the shared decision list and checked claims.
