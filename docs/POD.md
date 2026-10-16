# Etsy print-on-demand (physical products) plan

> **Decision 2026-10-16 (user): the most automated provider.** Printify first: Botsy creates complete products through the Printify API. After one supervised sticker test proves that API publishing reaches Etsy as a DRAFT, Botsy publishes through the API; otherwise Gelato's API (template based, documented to send Etsy drafts), then Printful's dashboard as the last resort. Kids' physical products go only through Printful (user, 2026-10-16: Printful's kids' items carry CPSIA tracking labels; Printify's do not), limited to an allowlist of Printful kids' blanks with their Children's Product Certificates on file; everything else goes through Printify.
_Researched 2026-10-16 from primary sources (links inline). Plan, not yet built. Decisions in task_state.md._

ETSY PRINT-ON-DEMAND (POD) PLAN FOR BOTSY. All sources were read on 2026-10-16.

0. SCOPE (applies to every step)
- Botsy studies which product types and themes are selling across many shops, using the same floors as today (10 or more listings and 3 or more shops per pattern). It then prints its own original art on blank products.
- It never reproduces or 'slightly changes' one specific listing. Etsy allows POD only for the seller's own original design (https://help.etsy.com/hc/en-us/articles/23948763872151), and CLAUDE.md forbids copying.
- No CLAUDE.md compliance rule changes.

1. PROVIDER: PRINTIFY FOR VERSION 1, PRINTFUL AS FALLBACK, GELATO LATER
Why Printify:
- Its API can build the whole product: upload art (POST /v1/uploads/images.json), then create the product (POST /v1/shops/{shop_id}/products.json) with blank, printer, variants and prices, print positions, title, description, tags and EU safety text (https://developers.printify.com/).
- Botsy does almost all the work, and the user clicks once in Printify.
- If the Etsy shop has a production partner named exactly 'Printify', Printify assigns it on publish (https://help.printify.com/hc/en-us/articles/4578896595345).
- It has the widest catalog, including kiss-cut stickers.

The catch (unverified):
- Printify does not document whether publishing to Etsy creates a draft or a live listing.
- Its publish call has only on/off flags for which fields to send, with no draft option, and 'visible' is read-only (https://developers.printify.com/).
- Third-party posts say the app's 'Hide in store' option sends the product to Etsy Drafts (https://community.etsy.com/t5/Technical-Issues/Transfer-draft-listing-from-Printify-to-Etsy/m-p/141388722).
- One automation user saw active listings instead (https://community.make.com/t/etsy-create-a-product-draft/19926).
- So Botsy NEVER calls Printify's publish, and POD stays switched off until the one-listing test in step 8 passes.

Why Printful is not first:
- It officially sends products to Etsy as drafts (https://help.printful.com/hc/en-us/articles/50262520118801), but its API cannot create products in an Etsy store (https://developers.printful.com/docs/).
- So either the user builds each product in Printful's dashboard, or Botsy creates the Etsy draft and the user links each variant to a Printful product and design by hand (https://help.printful.com/hc/en-us/articles/50263352901905).
- That linking can only start after Printful's once-a-day import of drafts (https://help.printful.com/hc/en-us/articles/50262491807889).
- Printful does not add the production partner (https://help.printful.com/hc/en-us/articles/50262492439953).
- Result: more clicks per listing. Keep it as the fallback.

Gelato:
- It officially adds new products to Etsy as drafts (https://support.gelato.com/en/articles/8996451).
- Its API behaviour with Etsy is unverified (https://support.gelato.com/en/articles/8996064) and its base prices need a sign-up.
- Deferred.

2. FLOW
research -> POD brief -> print files -> QA -> listing copy -> Printify product (unpublished) -> review queue -> USER publishes in Printify with 'Hide in store' -> Etsy draft -> Botsy checks the draft (read-only) -> USER does the final check and publishes in Etsy (pays $0.20).

Botsy automates:
- reading exports;
- scoring product types and themes;
- making art and print files at each print area's exact pixel size;
- QA;
- title, 13 tags, description, alt text and AI disclosure;
- pricing;
- creating the Printify product with everything filled in;
- downloading Printify's mockups for review;
- the review entry;
- checking the Etsy draft with read-only calls;
- alerts on Discord and in the event journal.

User clicks, one-time setup:
- create a Printify account and connect Etsy;
- in Shop Manager > Settings > Partners you work with, add a partner named exactly 'Printify' (https://help.etsy.com/hc/en-us/articles/360000336547);
- choose a fixed-price shipping profile, or Printify's 'Automatically assign', which works only for shops selling in USD (https://help.printify.com/hc/en-us/articles/23524753555985);
- set a return policy and a processing profile;
- create a Printify personal access token with the minimal scopes (section 4);
- decide on Offsite Ads (section 5);
- add a Printify payment method (production is billed to the seller; the exact billing steps were not checked).

User clicks, per product:
1. Open the product in Printify and check the mockups.
2. Turn on 'Hide in store' and click Publish.
3. Open the Etsy draft and check: 'Who made it? Another company or person', Printify as the partner, the AI disclosure, the price.
4. Publish in Etsy.
- Never edit the title or description on the Etsy side. That can change the SKU and break Printify's link to the order (https://help.printify.com/hc/en-us/articles/4483635595793).

3. PRODUCTS TO START WITH (adults and general audiences only)
Version 1, single- or few-variant blanks:
- Kiss-cut stickers, 3 in and 4 in. Uses existing sticker art as transparent PNGs. Printify's spacing minimums: 110 px at 3 in, 150 px at 4 in, about 75 px on sheets (https://help.printify.com/hc/en-us/articles/4483637274257).
- 11oz white mug, using a sticker motif.
- Matte vertical poster, 8x10 to 18x24, using coloring line art. It is vector art, so it rasterizes sharp at any size.
- Spiral notebook or hardcover journal cover, using stationery and genre art. Never game-system names or system art on the product.

Phase 2:
- tote;
- adult crewneck sweatshirt and tee, at most 2 options and few colours;
- 500 or 1000-piece puzzle.

Excluded:
- Phone cases: too many device variants for Etsy's limits of 2 options, 70 choices each and 400 combinations, and Printify's 100-variant limit (https://help.printify.com/hc/en-us/articles/4483617102097).
- Anything made mainly for children aged 12 or under. Those need third-party testing, a Children's Product Certificate and tracking labels (https://www.cpsc.gov/Business--Manufacturing/Business-Education/Childrens-Products).

Base costs:
- Approximate snapshots: stickers about $1.21-1.42, mug $5.34, poster from $4.96, notebook $8.26-8.77.
- Treat them as unverified. Botsy reads live cost, shipping and print-area pixel sizes from the Printify catalog API (GET /v1/catalog/blueprints/{id}/print_providers/{pp}/variants.json and GET /v2/catalog/blueprints/{id}/print_providers/{pp}/shipping.json, https://developers.printify.com/).
- Cache them weekly in data/pod_catalog/.

4. SAFETY LAYER
- pod/safety.py: an allowlist checked before every Printify request.
  - Allowed: GET /v1/shops.json; GET /v1/catalog/*; GET /v2/catalog/*/shipping.json; POST /v1/uploads/images.json; POST /v1/shops/{id}/products.json; GET and PUT /v1/shops/{id}/products/{pid}.json; GET .../gpsr.json.
  - Anything else raises ForbiddenEndpoint before it is sent, including publish.json, publishing_succeeded, publishing_failed, unpublish, every orders and webhooks path, and DELETE of the shop connection.
- Token: create it with only shops.read, catalog.read, print_providers.read, products.read, products.write, uploads.read and uploads.write.
  - Keep it in .env as PRINTIFY_API_TOKEN, with PRINTIFY_SHOP_ID, and add both to .env.example.
  - Tokens expire after one year (https://developers.printify.com/). Warn 30 days before.
- pod.printify.api_publish is hard-coded to False. Changing it means asking the user under CLAUDE.md.
- Etsy side: publisher/pod_watch.py uses only the existing own-shop guard, with getListingsByShop(state=draft) plus the default active list.
  - It never PATCHes. The guard forbids updateListing and only touches listings Botsy created (publisher/safety.py).
  - It matches the new draft by title and price, then checks: listing_type physical, who_made someone_else, when_made made_to_order, shipping_profile_id set, and the description containing the text from config/ai_disclosure.txt.
  - The API does not return a listing's production partner (ShopListing schema, https://www.etsy.com/openapi/generated/oas/3.0.0.json). The partner check stays on the user's checklist. The setup check only confirms through GET production-partners that a 'Printify' partner exists.
  - If the matching listing shows up ACTIVE before the user approved it, Botsy records pod_went_live_unexpectedly and alerts the user. It does not change the listing.
- pod.enabled stays false until setup and the step 8 test are done.

5. PRICING (pod/pricing.py)
Inputs:
- C = provider base cost, Sh = provider shipping (both live from the catalog API).
- Etsy fees (https://help.etsy.com/hc/en-us/articles/115014483627): $0.20 listing fee (renewed on each sale), 6.5% transaction fee on item plus shipping, payment processing of 3% + $0.25 (https://help.etsy.com/hc/en-us/articles/115015628847).
- Offsite Ads at 15% under $10k a year (opt-out allowed) or 12%, capped at $100 per order (https://help.etsy.com/hc/en-us/articles/360000338367).
- a = share of orders that come from Offsite Ads.
- m = target margin.

Calculation:
- Order revenue R = item price + buyer shipping. With free shipping (the default), buyer shipping is 0.
- R = (C + Sh + 0.45) / (1 - 0.095 - 0.15a - m).
- For a safety margin on sales tax, multiply the 0.03 by (1 + t), with t defaulting to 0.10.
- Round up to .99.

Checks:
- Break-even: price at a=1 and m=0 must be at or below the chosen price, unless the shop has opted out of Offsite Ads.
- Refuse the product if R is above the theme's p75 price for that product type; flag it if R is below p25.
- Every variant must be at least $0.20, and the title must have no quotes or trailing spaces (https://help.printify.com/hc/en-us/articles/4483617102097).

Example (shipping is an assumed $6.00, not verified): mug with C=5.34, Sh=6.00 and m=0.30 gives $19.49 with a=0 (listed at $19.99) and $25.91 with a=1.

Earnings and sales volumes are unknown; I found no credible source.

Etsy charges nothing until the user publishes, so core.spend records no POD costs.

6. COMPLIANCE
- Etsy category and disclosure: 'Designed by a seller'. AI-prompted art must say AI was used, and the production partner must be disclosed (https://help.etsy.com/hc/en-us/articles/360024112614). Every POD description carries config/ai_disclosure.txt and the line 'Printed and shipped by our production partner'.
- No brand names in copy, art, tags or file names. That includes the blanks' manufacturers (for example Gildan, Bella+Canvas) and the provider's name, per the CLAUDE.md brand rule. Describe the blank generically ('unisex heavy blend crewneck') and overwrite Printify's default description.
- The existing blocklist and !term rules apply. TTRPG system names are never allowed on printed products.
- No medical claims.
- QA blocks kid-targeted words (kids, toddler, baby, boys, girls, nursery, classroom, 'for children') and youth/kids/baby blanks.
- No AI-generated photoreal people in mockups. Printify cites New York law S8420-A (secondary source, https://help.printify.com/hc/en-us/articles/47658708553617).
- EU sales need GPSR details and a responsible person (https://help.printify.com/hc/en-us/articles/30680548875025). Version 1 ships to the US only.

7. QA (qa/pod.py)
- The file covers each print area and its effective resolution is at least 1.0 of the placeholder pixels (0.5 for posters above 18x24).
- Transparent background (alpha) for stickers.
- Stray specks removed.
- Sticker spacing at or above Printify's minimums.
- Art inside a 5% safe area.
- File under 100 MB. Above 5 MB, upload by URL, not base64 (https://help.printify.com/hc/en-us/articles/4483617936657).
- Variant limits: at most 100 variants, 2 options and 70 choices per option.
- Price floor checks.
- Blocklist, kids and brand checks.
- Review queue as today.

8. BUILD ORDER
- P1: pod/safety.py, pod/providers/printify.py and a fake client with tests.
- P2: catalog cache and pod/pricing.py with tests.
- P3: pod/compose.py (print files from data/pod_catalog placeholder sizes, centred with x=0.5, y=0.5, scale=1).
- P4: qa/pod.py.
- P5: POD listing template.
- P6: review entry and the read-only publisher/pod_watch.py.
- P7: research/product_types.py (category and title words mapped to product types; briefs get niche 'pod', product_class and price_stats; still no titles, shops, URLs or images).
- P8: one-listing test by the user. Botsy creates one sticker product; the user publishes it in Printify with 'Hide in store'. If it lands as an Etsy DRAFT, enable POD. If it lands ACTIVE, or there is no 'Hide in store' option, stop and ask the user (decision 3).

Fallback route (only if chosen):
- The publisher gets a separate 'physical' path that creates the Etsy draft itself: type=physical, who_made=someone_else, when_made=made_to_order, production_partner_ids, shipping_profile_id and readiness_state_id (https://www.etsy.com/openapi/generated/oas/3.0.0.json).
- The user then links the draft in Printful.
- Products with variants need PUT updateListingInventory, which today's guard does not allow. That guard change needs the user's yes.

## Decisions (recommended defaults)

- 1. Copying: your request was to 'essentially copy' items selling now. Botsy will follow themes and product types that sell across many shops and make its own designs, for both Etsy POD and KDP. Copying one listing or book breaks Etsy's POD rule, KDP's content rule, copyright and CLAUDE.md. Recommended: yes, original designs only.
- 2. POD provider: Printify for version 1 (most automated), Printful as fallback, Gelato later. Recommended: Printify, enabled only after a one-sticker test.
- 3. If the Printify test publishes ACTIVE instead of as a draft (or 'Hide in store' doesn't exist): either (a) treat your Publish click in Printify as the approval, which changes CLAUDE.md's 'the user approves in Etsy', or (b) switch to Printful, where Botsy creates the Etsy draft and you link it in Printful. Recommended: (b).
- 4. First POD products: stickers, 11oz mug, poster, notebook cover. Phase 2: tote, adult sweatshirt and tee, puzzle. Never kids' products or phone cases. Recommended: yes.
- 5. Shipping area: US only at first. EU sales need GPSR safety details and an EU responsible person, and the GPSR text may require naming the blank's maker, which conflicts with the no-brands rule. Recommended: US only.
- 6. Offsite Ads: opt out while sales are under $10k a year (Etsy allows it) so prices don't carry the 15% fee. Recommended: opt out.
- 7. POD shipping: free shipping with the cost built into the price, or buyer-paid. Recommended: free shipping, with prices checked against the theme's price range.
- 8. POD shop settings (you set these in Etsy): no returns or exchanges, but replacement for damaged or misprinted items; made-to-order processing at the provider's stated time (for example 3-5 business days). Recommended: yes.
- 9. POD art: reuse existing sticker art for stickers and mugs, and existing coloring line art for posters and totes. Make new high-resolution art only when a print area needs it, within the $25/month cap. Recommended: reuse first.
- 10. KDP research tool: BookBeam or Helium 10, whose terms allow internal business use. Use Publisher Rocket only after its support confirms business use is allowed. Please share one sample export so the columns can be confirmed. Recommended: BookBeam or Helium 10.
- 11. KDP author and ISBN: choose a pen name. Use the free KDP ISBN for coloring and puzzle books and no ISBN for low-content planners and logs. Recommended: as stated.
- 12. KDP start: paperback only, Amazon.com, no bleed, matte. First books: coloring_50, then activity_100, then the undated planner. Recommended: yes.
- 13. TTRPG books on KDP: system-neutral ('Fantasy RPG Campaign Log'). The CLAUDE.md game-system exception covers Etsy listings only, and KDP forbids unauthorized trademark references. Recommended: system-neutral.
- 14. Embedded font for every PDF: Liberation Sans, with tracing glyph points re-derived; Andika for kids' tracing can come later. Recommended: Liberation Sans now.
- 15. KDP AI disclosure: disclose any Gemini art (covers included) as AI-generated images and any Claude-written word lists as AI-generated text, with 'minimal or no editing'. Recommended: disclose conservatively.
- 16. Reuse between platforms: may one Etsy design set seed one KDP book, with no page shared between two KDP books? Recommended: yes.
- 17. Printed proofs: order one proof per new KDP template before the queue submits books built from it. Recommended: yes.
- 18. Priority: (1) finish the packs build, (2) embedded fonts, (3) KDP coloring_50 while you do the Printify, Etsy partner and shipping setup, (4) POD version 1 after the test, (5) the overseer. Recommended: this order; please confirm.

## Checked claims

- Etsy createDraftListing (Etsy Open API v3 spec, https://www.etsy.com/openapi/generated/oas/3.0.0.json, read 2026-10-16). CONFIRMED: required fields are quantity, title, description, price, who_made, when_made and taxonomy_id. production_partner_ids is an optional array of ids. shipping_profile_id is 'Required when listing type is physical'. readiness_state_id can be sent. The create call has no `state` field.
- Etsy updateListing (same spec, read 2026-10-16). CONFIRMED: `state` takes active or inactive, and 'Setting a draft listing to active will also publish the listing on etsy.com'. updateListingInventory (PUT) requires price, quantity, is_enabled and readiness_state_id on every offering.
- Etsy production partners (same spec, plus https://help.etsy.com/hc/en-us/articles/360000336547, updated 2026-10-15, read 2026-10-16). CONFIRMED: the API has only GET /shops/{shop_id}/production-partners (scope shops_r). Partners are created only in Shop Manager.
- CORRECTION to the POD design: the ShopListing object that getListingsByShop and getListing return has no production-partner field (I checked its properties in the spec, 2026-10-16), so Botsy cannot check a listing's partner through the API. It can read who_made, when_made, listing_type, shipping_profile_id, description and state.
- CORRECTION to the POD design: Botsy's own guard (/home/user/Botsy/publisher/safety.py lines 1-60) never sends PUT or PATCH (updateListing) and only touches listing ids Botsy created. So the proposed watcher that PATCHes Printify-created drafts would break an existing safety rule. In the plan below the watcher only reads.
- Printify publish (https://developers.printify.com/ and https://developers.printify.com/openapi.json, read 2026-10-16). CONFIRMED: publishRequest holds only booleans (title, description, images, variants, tags, keyFeatures, shipping_template), with no draft or visibility option. 'visible' is READ-ONLY ('Visibility in sales channel... defaults to true'). The docs describe publishing only for custom-integration shops (lock, product:publish:started, then publishing_succeeded) and say nothing about Etsy draft or active status.
- Printify 'Hide in store' for Etsy: NOT VERIFIED. Printify's help center has no article about it (Zendesk search, 2026-10-16). The only official 'hide' article covers unpublishing an already published product (https://help.printify.com/hc/en-us/articles/4483637295121, updated 2026-08-27). The draft claim comes only from a closed Etsy forum thread and third-party guides.
- Printify production partner (https://help.printify.com/hc/en-us/articles/4578896595345, updated 2026-10-14, read 2026-10-16). CONFIRMED: Printify cannot create the partner. If a partner named 'Printify' already exists, Printify assigns it on publish. Otherwise the listing defaults to 'Made by seller' and the user must fix it.
- Printify Migration Tool (https://help.printify.com/hc/en-us/articles/4483636659985, updated 2026-10-05, read 2026-10-16). CONFIRMED: 'Draft products won't be possible to migrate'.
- Printful + Etsy (https://help.printful.com/hc/en-us/articles/50262520118801 and 50262382828433, read 2026-10-16). CONFIRMED: products pushed from the Printful dashboard land as Etsy drafts. Listings created on Etsy can be synced by hand afterwards. Drafts are imported once a day (https://help.printful.com/hc/en-us/articles/50262491807889). Printful does not add the production partner (https://help.printful.com/hc/en-us/articles/50262492439953). Manual sync means picking a Printful product and adding the design for each variant in the Printful dashboard (https://help.printful.com/hc/en-us/articles/50263352901905, updated 2026-10-14).
- Printful API (https://developers.printful.com/docs/, read 2026-10-16). CONFIRMED: 'it's not possible to create new sync products directly through the API'. The docs never mention Etsy. A Mockup Generator API exists.
- Gelato (https://support.gelato.com/en/articles/8996451, read 2026-10-16). CONFIRMED: 'newly created products will be added as drafts to prevent Etsy from charging you'. NOT FOUND on that page: the 'Show product to store visitors' wording. NOT VERIFIED: the API article (https://support.gelato.com/en/articles/8996064) names no supported stores and no draft or visibility field. 'isVisibleInTheOnlineStore' appears only in a third-party README.
- Etsy POD policy (https://help.etsy.com/hc/en-us/articles/360024112614, updated 2026-10-16, and https://help.etsy.com/hc/en-us/articles/23948763872151, updated 2026-10-16, read 2026-10-16). CONFIRMED: 'Designed by a seller' covers designs printed by a third party and seller-prompted AI creations, which 'must disclose the use of AI'. Production partners must be disclosed. POD is allowed only for 'the seller's original design (or the buyer's customization)'.
- Etsy fees (https://help.etsy.com/hc/en-us/articles/115014483627, updated 2026-10-15, and https://help.etsy.com/hc/en-us/articles/360000338367, read 2026-10-16). CONFIRMED: $0.20 listing fee for 4 months; 6.5% transaction fee including shipping; Offsite Ads 15% (under $10k in 365 days, opt-out allowed) or 12%, never more than $100 per order. NOT re-checked: payment processing of 3% + $0.25 (researcher's source https://help.etsy.com/hc/en-us/articles/115015628847).
- Pricing formula (my algebra, 2026-10-16). R = (C + Sh + 0.45) / (1 - 0.095 - 0.15a - m) follows from setting profit equal to m x R. Mug example: 11.79 / 0.605 = $19.49 with a=0, and 11.79 / 0.455 = $25.91 with a=1. Both are correct. The $6.00 shipping cost is an assumption, not a verified number.
- KDP cover (https://kdp.amazon.com/en_US/help/topic/G201953020, read 2026-10-16). CONFIRMED: spine = pages x 0.002252 in (white or standard color), x 0.0025 (cream), x 0.002347 (premium color). Cover width = bleed + back + spine + front + bleed; height = bleed + trim + bleed; bleed 0.125 in. 300 DPI minimum, CMYK, 650 MB limit (40 MB recommended). 0.0625 in spine-text clearance plus 0.0625 in fold variance. Spine text: '79 pages' in one place and 'more than 79' in another (ambiguous), 80 for Cover Creator, so Botsy uses 80.
- KDP barcode and low-content rules (https://kdp.amazon.com/en_US/help/topic/GGE5T76TWKA85DJM, read 2026-10-16). CONFIRMED: a 2 x 1.2 in white box in the lower right corner of the back cover. Coloring and puzzle books are 'Not Generally Low-Content'. Low-content books get no free ISBN, no Expanded Distribution and no series. The low-content box must be ticked, 'or your book will be rejected'.
- KDP AI disclosure (https://kdp.amazon.com/en_US/help/topic/G200672390, read 2026-10-16). CONFIRMED: AI-generated text, images or translations must be disclosed on a new book and on any republish. Content counts as AI-generated even after substantial edits. 'AI-generated images include cover and interior images and artwork.' AI-assisted content does not need disclosure. NOT VERIFIED on KDP Help: the exact dropdown wording of the disclosure form (third-party source only).
- KDP weekly limits (https://kdp.amazon.com/en_US/help/topic/G202172740, read 2026-10-16). CONFIRMED: up to 2 title creations and 2 new submissions per book format each week, reset every Sunday 12:00 AM UTC.
- KDP printing cost and royalty (https://kdp.amazon.com/en_US/help/topic/G201834340 and G201834330, read 2026-10-16). CONFIRMED: black ink, 24-110 pages costs a flat $2.30 (regular trim) or $2.84 (large trim); 110-828 pages costs $1.00 + $0.012 or $0.017 per page. Large trim = more than 6.12 in wide or 9 in tall. Royalty is 60% at $9.99 or more and 50% at $9.98 or less; Expanded Distribution 40%. Worked examples: 104-page 8.5x11 at $9.99 earns $3.15; 6x9 150-page at $9.99 earns $3.19.
- Repo blocker (grep of /home/user/Botsy/generators, 2026-10-16). CONFIRMED: generators/activity_sheets/layout.py:14-15, generators/stationery/styles.py:24-26, generators/imaging/pdf.py:93 and the tracing.py glyph metrics all use reportlab's built-in Helvetica. reportlab does not embed built-in fonts (from background knowledge), and KDP requires all fonts embedded. print.page_sizes.us_letter (8.5x11) and margin_in 0.5 in config/settings.yaml fit KDP.
- Skeptic note: the CLAUDE.md rule 'No trademarked ... brands in designs, titles, tags or descriptions' also covers the brands of blank products (for example Gildan, Bella+Canvas) and the provider's name, which Printify's default descriptions and GPSR examples include. POD copy must describe the blank generically, with no brand names.

## Sources

- https://www.etsy.com/openapi/generated/oas/3.0.0.json (Etsy Open API v3 spec, read 2026-10-16)
- https://help.etsy.com/hc/en-us/articles/360024112614 (What Can I Sell on Etsy, updated 2026-10-16, read 2026-10-16)
- https://help.etsy.com/hc/en-us/articles/23948763872151 (Drop shipping / production partners, updated 2026-10-16, read 2026-10-16)
- https://help.etsy.com/hc/en-us/articles/360000336547 (Working with Production Partners, updated 2026-10-15, read 2026-10-16)
- https://help.etsy.com/hc/en-us/articles/115014483627 (Etsy fees, updated 2026-10-15, read 2026-10-16)
- https://help.etsy.com/hc/en-us/articles/360000338367 (Offsite Ads, updated 2026-10-16, read 2026-10-16)
- https://help.etsy.com/hc/en-us/articles/115015628847 (Etsy Payments fees, researcher-read 2026-10-16, not re-checked)
- https://developers.printify.com/ and https://developers.printify.com/openapi.json (Printify API, read 2026-10-16)
- https://help.printify.com/hc/en-us/articles/4578896595345 (Printify and Etsy Creativity Standards, updated 2026-10-14, read 2026-10-16)
- https://help.printify.com/hc/en-us/articles/4483636659985 (Printify Migration Tool, updated 2026-10-05, read 2026-10-16)
- https://help.printify.com/hc/en-us/articles/4483637295121 (Printify unpublish/hide, updated 2026-08-27, read 2026-10-16)
- https://help.printify.com/hc/en-us/articles/4483617102097 (Printify Etsy publish errors, researcher-read 2026-10-16)
- https://help.printify.com/hc/en-us/articles/4483635595793 (Printify order linking/SKU, researcher-read 2026-10-16)
- https://help.printify.com/hc/en-us/articles/4483637274257 (Printify sticker spacing, researcher-read 2026-10-16)
- https://help.printify.com/hc/en-us/articles/4483617936657 (Printify image upload, researcher-read 2026-10-16)
- https://help.printify.com/hc/en-us/articles/23524753555985 (Printify Etsy shipping, researcher-read 2026-10-16)
- https://help.printify.com/hc/en-us/articles/30680548875025 (Printify GPSR, researcher-read 2026-10-16)
- https://help.printify.com/hc/en-us/articles/47658708553617 (Printify on NY S8420-A, secondary, researcher-read 2026-10-16)
- https://community.etsy.com/t5/Technical-Issues/Transfer-draft-listing-from-Printify-to-Etsy/m-p/141388722 (forum, unverified 'Hide in store' claim, read 2026-10-16)
- https://community.make.com/t/etsy-create-a-product-draft/19926 (forum, report of active listings, read 2026-10-16)
- https://developers.printful.com/docs/ (Printful API, read 2026-10-16)
- https://help.printful.com/hc/en-us/articles/50262520118801 (Printful Etsy drafts, updated 2026-10-09, read 2026-10-16)
- https://help.printful.com/hc/en-us/articles/50262382828433 (Printful adding products to Etsy, updated 2026-10-06, read 2026-10-16)
- https://help.printful.com/hc/en-us/articles/50262491807889 (Printful Etsy sync by status, updated 2026-10-09, read 2026-10-16)
- https://help.printful.com/hc/en-us/articles/50262492439953 (Printful production partner, updated 2026-10-09, read 2026-10-16)
- https://help.printful.com/hc/en-us/articles/50263352901905 (Printful manual sync, updated 2026-10-14, read 2026-10-16)
- https://support.gelato.com/en/articles/8996451 (Gelato Etsy connection, dated 2025-10-30, read 2026-10-16)
- https://support.gelato.com/en/articles/8996064-creating-products-via-api-from-template (Gelato API from template, dated 2025-10-30, read 2026-10-16)
- https://www.cpsc.gov/Business--Manufacturing/Business-Education/Childrens-Products (CPSC children's products, researcher via snippets 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G201953020 (KDP paperback cover, read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/GGE5T76TWKA85DJM (KDP low-content books and barcode box, read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G200672390 (KDP content guidelines and AI disclosure, read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G202172740 (KDP weekly title limits, read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G201834340 (KDP printing cost, read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G201834330 (KDP paperback royalty, read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/GVBQ3CMEQW3W2VL6 (KDP trim, bleed, margins, researcher-read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G201857950 (KDP paperback submission guidelines, researcher-read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G201834260 (KDP formatting issues, researcher-read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G201113520 (KDP Cover Creator, researcher-read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G201097560 (KDP metadata guidelines, researcher-read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G201298500 (KDP keywords, researcher-read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G200952510 (Kindle content quality, researcher-read 2026-10-16)
- https://kdp.amazon.com/terms-and-conditions (KDP Terms, researcher-read 2026-10-16)
- https://affiliate-program.amazon.com/help/operating/policies (Amazon Associates policies, Creators API restriction, researcher-read 2026-10-16)
- https://bookbeam.io/terms-of-service/ (BookBeam terms, researcher-read 2026-10-16)
- https://www.helium10.com/terms-and-conditions/ (Helium 10 terms, researcher-read 2026-10-16)
- https://publisherrocket.com/terms-and-conditions/ (Publisher Rocket terms, researcher-read 2026-10-16)
- /home/user/Botsy/publisher/safety.py, /home/user/Botsy/publisher/publish.py, /home/user/Botsy/generators (font grep), /home/user/Botsy/config/settings.yaml (repo, checked 2026-10-16)
