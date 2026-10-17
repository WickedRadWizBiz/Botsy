# Hermes: active director and helper for Botsy

> **Status: proposed 2026-10-16, not built yet.** Decisions still open are listed in [PROPOSAL_2026-10-16.md](PROPOSAL_2026-10-16.md); build order is there too.

In short: Hermes plans the week, looks at our own products, and walks you through chores in Discord. It still can't run anything itself. It writes "orders", and Botsy's own code checks each order before anything happens.

### What changes from the plan you approved
- **Before:** a short safe menu (status, spend, approve, reject, cap, pause, run now).
- **Now:** Hermes can also write checked orders:
  - a weekly plan;
  - page redos;
  - listing edits;
  - search ideas;
  - review recommendations;
  - your stated likes and dislikes.
- **These stay the same:**
  - drafts only;
  - a spending ceiling only the server admin can change;
  - no shell, no web, no research files;
  - a code for every review approval, as you approved. New: one code may approve a batch of named items.
- **If Hermes is down,** or its plan is more than 8 days old, Botsy runs exactly as it does today.

### How Botsy and Hermes talk
- **Botsy to Hermes:**
  - short signed event messages on the server, through Hermes's webhook route, bound to 127.0.0.1;
  - each event contains only fixed fields: item ids, counts, money, and reason codes from a fixed list;
  - never raw log text, export filenames, CSV headers or any row from a research file.
- **Hermes to Botsy:**
  - one locked tool menu (botsy-control), split into "read" and "act" tools;
  - the tool servers are plain glue: all checks happen inside botsy-control, which runs as Botsy's user and takes its input as JSON.
- Tools can hand Hermes images (verified), so it can look at our own previews without file access.

### Four permission levels
- **Level 0, read:** status, spending, briefs (aggregated numbers and terms only), our own previews, our own shop's stats.
- **Level 1, suggest:** notes, recommendations, search ideas, preferences. Nothing changes until Botsy's normal rules act on them.
- **Level 2, small changes:**
  - covers the weekly plan, page redos, listing edits on not-yet-approved items, and a few safe settings;
  - Botsy checks every value and enforces limits from an admin-only file, for example 20 changes an hour, 10 page redos a week and 3 "run now" a day;
  - Hermes may ask you with buttons first, but that is a convenience, not a safeguard. Botsy cannot tell whether you pressed anything, so every Level 2 change is capped and can be undone even if Hermes acts without asking.
- **Level 3, code required:**
  - approving any held item (a batch code is fine);
  - raising the cap;
  - restarting paid work after a budget stop.

  How codes work:
  - Botsy, not Hermes, posts a 6-digit code to a private #botsy-codes channel, with Botsy's own facts: the rule that fired, the term, the item and a thumbnail;
  - you read it there and type the code to Hermes;
  - a code works once, only for that exact action and those exact items, and expires after 10 minutes.

### Lock-down: three layers (must do before any role)
**1. Hermes settings.** Verified defaults (2026-10-16):
- Discord chats get every tool, including the terminal, files, web, browser, subagents, scheduled jobs, code running and discord_admin (bans, role changes, channel management).
- Scheduled jobs get the same set.

So:
- give the chat and every scheduled job only the Botsy tools;
- turn off terminal, file, web, browser, delegation, cronjob, code_execution and discord_admin;
- turn off sampling and elicitation for botsy-control (both are on by default);
- set memory.write_approval: true, and require approval for skill changes;
- compress chat history at about 60,000 tokens, because Haiku 5.5 charges 5x for requests over 100,000 tokens and cache reads count toward that;
- only you create scheduled jobs, each fixed to a model.

The exact setting name for the tool lock is not confirmed, so test that it holds.

**2. Operating system:**
- Hermes runs as its own "hermes" user with no sudo except one fixed rule that runs botsy-control;
- that user has no read access to Botsy's .env, data/, output/ or research files;
- optional, recommended: a firewall rule so the hermes user can reach only Anthropic and Discord. Then "no web" holds even if the Hermes setting fails.

**3. Discord:**
- invite the Hermes bot with Discord's minimum permissions only: view channels, send messages, embed links, attach files, read message history; optionally threads and reactions;
- never Administrator, Manage Channels, Manage Roles or Manage Webhooks;
- deny the bot View Channel on #botsy-codes, and list that channel in DISCORD_IGNORED_CHANNELS;
- allow only your Discord user ID (DISCORD_ALLOWED_USERS), never a role;
- turn on two-factor login. Codes don't protect you if your Discord account is stolen.

**Dashboard:**
- the Botsy tab is read-only, plus Pause and Lower cap;
- no Save-to-raise or Approve buttons, because the dashboard has no login on the server itself;
- reach it through an SSH tunnel.

**Version:**
- pin, at install time, the newest release that is at least about 7 days old with no hotfix after it. v0.22.2 shipped Oct 15, one day ago, after releases on Oct 13 and 14, and has a known session-deletion issue;
- rerun the lock tests after every update.

### Hermes can never
- run commands, edit files, browse or search the web;
- make an Etsy listing live, publish on Printify, or log in to KDP;
- change compliance rules, the blocklist, the AI disclosure, the research minimums, the ceiling or the limits file;
- read research exports, research history or raw logs, or see other sellers' data;
- write image prompts or free-text instructions for Botsy's models (fixed options and approved style phrases only);
- create scheduled jobs, install add-ons, or change Discord channels or roles.

### Roles
**First wave**

**1. Foundation**
- botsy-control "read" and "act" tool servers, order files Botsy checks again, signed fixed-field events, the admin-only limits file and ceiling, codes, the three-layer lock, and a one-line log of every Hermes action in Botsy's own channel.
- Cost about $0. Effort: high.

**2. Production director (with the season calendar, budget split and preference memory)**
- Every Monday it proposes the themes and pack sizes, the 2 KDP slots, print-on-demand products (once switched on) and the $25 split. You reply in plain words and it revises.
- Botsy checks:
  - each theme has a valid brief and passes the blocklist;
  - pack sizes are allowed;
  - totals fit the weekly limits and KDP's slots;
  - the cost fits the money left this month.
- Never: invent a theme without a brief, change research rules, pass the cap or KDP limits, or turn print-on-demand on.
- Preference memory lives in Botsy, not Hermes: approvals and rejections with reasons, plan edits, and what you say ("no pastel"). Hermes's own memory holds only about 3,600 characters.
- Example:

  "Week 43 plan:
  - Digital, 9 packs: coloring 'axolotl garden' in 20 and 5 pages; stationery 'weekly reset' in 5, 20 and 12 pages; TTRPG 'hex crawl journal' in 20 pages.
  - KDP: 1 coloring book from 'cozy mushrooms', slot 1 of 2.
  - $9.10 of $25 spent; this plan costs about $6.40.
  - Halloween is 15 days away: too late for KDP, fine for digital.
  - [Approve now] [Hold] [Change]"
- Cost: about $0.50 a month on Haiku 5.5, or $3-4 if the plan uses Sonnet 5.5. These are estimates, and Haiku's planning quality through Hermes is untested.

**3. Review-queue triage**
- Reads held items' reasons and previews, recommends approve, reject or redo, and sends a morning list.
- Rejecting and adding a hold happen at once.
- Every approval needs a code, with Botsy's facts in #botsy-codes. Batch codes are fine.
- Never approves alone, removes reasons, approves hard-blocked items, or treats its own look as legal clearance.
- Cost about $0.40 a month.

**4. KDP upload helper**
- A step-by-step checklist in its own Discord thread: copy blocks, categories, AI answers with reasons, trim, ink and paper, price with earnings, and which files to pick.
- Tracks progress and the 2-a-week limit, and finds the fix when you paste a rejection email.
- Never logs in to KDP or fills fields, and marks a book live only when you say it is.
- Cost $0.25-0.50 a month.

**5. Research co-pilot**
- Guides the weekly export session and explains rejected files using Botsy's reason codes only. It never sees file contents, headers or filenames.
- Suggests up to 5 searches a week. Botsy blocklist-checks them and labels them "exploratory".
- Never sees rows, titles or shop names, and never lets an idea skip the 10-listing / 3-shop rule.
- Cost about $0.50 a month.

**Second wave (one at a time, 1-2 weeks each)**

**6. Creative director**
- First, a vision test on about 20 packs we know are good or bad.
- Then it may ask for redos (up to 5 pages, reasons from a fixed list) and adjust style using approved phrases. A possible character look-alike is held for you.
- Redos come out of Botsy's $25 cap at about $0.05-0.11 a page (unverified). Suggested start: 10 pages a week.

**7. Incident helper**
- The watchdog performs every fix itself under fixed conditions:
  - a lock is cleared only if its process is dead and the lock is older than the longest allowed run;
  - cleanup follows Botsy's fixed retention rules;
  - retries count toward "run now" and the cap.
- Hermes reads structured health events, explains them in plain words, and may pause.
- Cost about $0.20 a month.

**8. Listing editor**
- Chat edits to copy. Botsy rechecks every rule and restores the locked lines.
- Only for items not yet drafted, or Etsy drafts. Before and after each update, Botsy re-reads the listing state, never sends a state field, and aborts with an alarm if the listing is not a draft.
- Edited items go back to review.

**9. Setup coach (move to the first wave if setup is still open)**
- Answers from Botsy's docs and changes a few safe settings after you confirm.
- Never sees or asks for secrets.

**Later**

**10. Print-on-demand helper, after the Printify test**
- Mockup checks, plus a read-only check that the Etsy listing is a DRAFT, with an alarm if it is live.

**11. Performance analyst, once there are sales**
- Uses our own shop's numbers only. Real sales need the transactions_r permission.
- Botsy keeps only listing, date, quantity and price, never buyer details.

### Monthly cost (estimates, not measured)
- Haiku 5.5 is $0.10/$0.50 per million tokens up to 100,000-token prompts, and $0.50/$2.50 above (verified 2026-10-16). Sonnet 5.5 is $2/$10.
- Roles: about $4-5 a month, plus $1-3 for chat. Total: about $5-8. Prompt caching should lower that.
- Keep the $5 Hermes limit and add roles one at a time. Raise it to $8-10 only if measured use needs it.
- Page redos come out of Botsy's $25 cap.
- Server: nobody has measured whether Hermes and Botsy fit in 4 GB. Add swap; the 8 GB plan is $44 instead of $24.

### Not a good fit
- Multi-bot teams (Bot Mode or Kanban).
- Hermes making designs or prompts.
- Any web or marketplace browsing.
- Editing live listings.
- Answering customer messages (no Etsy API found; unverified).

### Untested; test before relying on them
- The Botsy-only tool lock, including after `hermes update`.
- That the Hermes bot cannot read #botsy-codes and that codes cannot be reused.
- Whether Hermes's Discord buttons work for our tools (only a convenience now).
- How well Haiku 5.5 spots print defects and look-alikes.
- Memory use on the 4 GB server.


---

## Scale-ups ("more of this") and the Sunday plan

> **Status: design, written 2026-10-17 after the user's answers of 2026-10-16; not built yet.** Open choices and build order: [PROPOSAL_2026-10-16.md](PROPOSAL_2026-10-16.md) section "Round 2".

> **Status: design, 2026-10-16. Not built.** It answers your points 4 and 5 and builds on [OVERSEER.md](OVERSEER.md). All limits and winner rules are starting guesses, not measurements.

### In short
- **Botsy, not Hermes, decides what is selling.** Only our own numbers count.
- **Hermes may ask for "more of this"** with a Level 2 order called `scale_up`. It always comes with the weekly plan. Botsy checks it against:
  - the research rules;
  - the duplicate rules;
  - a capacity check;
  - an admin-only limits file.
- **Expect a slow start.** A listing must be live 21 days before it can count as selling, and the shop has no listings yet. The first scale-up is likely in week 5-8 at the earliest.
- **Under today's rules, "more of this" mostly means:**
  - the same theme in a related niche;
  - a related theme;
  - later, one KDP book from the winning pages.

  A "new pack size" mostly puts the winner first in line, because Botsy already makes every pack size the brief lists. A true "Volume 2" stays blocked unless you allow it.
- **Money is tight.** A plan of about $6.40 a week nearly fills the $25 cap.
  - Scale-ups get at most $6 a month, and only while the month stays under the 80% warning line.
  - Most will be cheap kinds: planner, RPG and activity packs, or packs cut from pages we already have.
- **The weekly plan posts every Sunday at 8 pm New York time.**
  - Your first 4 plans wait for your OK, given with a code.
  - From plan 5, a plan starts by itself Monday at 8 am unless you press Hold.
- **Three things need fixing first:**
  - The server runs on UTC, so quiet hours start at 6 pm your time.
  - The daily log arrives at about 3 am your time.
  - A Saturday upload would be spent on new items before Sunday's plan could choose.

  The fixes are in B5 and B7.
- **Suggested build order:**
  1. Now: the Sunday plan, and a daily record of our own listing numbers. The record costs nothing and builds history.
  2. Later, once about 20 listings have been live for 30 days: the winners list and scale-ups.

---

### Part A. "More of this" (scale-ups)

#### A1. What counts as selling (a "winner")

**Where the numbers come from.** Only our own data (checked 2026-10-16):
- **Etsy views and favorites** of our own listings (`views`, `num_favorers`).
  - Permission `listings_r`, which we already ask for. The daily check already reads these in live mode (own-stats).
  - Etsy says views are "tabulated once per day and only for active listings". A 0 can mean "not counted yet".
  - Etsy's API has no visits or conversion-rate numbers. Its spec has 105 operations and none of them is a stats endpoint.
- **Etsy sales**, from `getShopReceiptTransactionsByShop`.
  - It needs the `transactions_r` permission.
    - Etsy's docs list no separate approval for it. You just approve the app once more (re-run `publisher auth`).
    - You haven't connected the app yet. Add it before your first `publisher auth`, and you approve only once.
  - This endpoint has no date filter, and Etsy doesn't say what order the rows come in. So Botsy pages through all of them (at most 50 pages of 100) and keeps the last 90 days.
  - **What Botsy keeps:** listing id, day, quantity and price. Price x quantity gives our revenue.
  - **What it never stores:** buyer_user_id, receipt_id or coupon fields.
  - **What it never calls:** the receipt endpoints, which hold the buyer's name, email and address. The cost of that choice: refunds are not visible.
- **Star ratings of our own listings**, from `getReviewsByShop`. It needs only the app key.
  - Botsy keeps the listing, the rating and the date. It never keeps review text or photo links.
  - The endpoint answers for any shop, so Botsy's safety layer locks it to our own shop id.
- **KDP:** the "Prior Months' Royalties" report. You download it by hand, and the Windows helper uploads it.
  - KDP says this report is "updated near the 15th of every month".
  - A faster, optional source: KDP's dashboard shows print orders "within 24 hours after the order ships". The Orders report says print processing can take "up to a few days".
  - The file format is unknown until you send a sample.
- **Your review decisions** per theme: approvals and rejections in Botsy's queue.
  - A draft you delete in Etsy instead of publishing counts as a rejection, when Botsy can see that.

**How a result is tied back to what we made:**
- **Etsy:** the `listing_id` in `publish.json` leads to the item, then to `manifest.json`. The manifest gives:
  - niche;
  - theme (matched with `pipeline.run.theme_key`);
  - pack size;
  - source (master, derived or fresh);
  - set_id;
  - brief_id;
  - created date.
- **New manifest field `origin`:** `{kind: "plan" | "normal" | "scale_up", order_id, parent_item_id}`. It lets Botsy judge what each scale-up did.
- **KDP:** the ASIN (recorded with `kdp mark --asin`) leads to the kit. The kit gives the theme, the template and the Etsy set that seeded it.
- **Results are added up at three levels:**
  - each listing;
  - each theme in a niche (shop, niche, theme_key);
  - each design set.

  Derived packs share their master's set.

**Daily snapshot.** Build this first; it costs nothing.
- Own-stats also writes `data/performance/etsy/<date>.json` (gitignored). For each of our listings it holds listing_id, state, views and num_favorers.
- The research CSV stays as it is, with no ids.
- Etsy's numbers are running totals.
  - "Views in the last 30 days" = today's count minus the count from 30 days ago.
  - A 0 that comes after a non-zero number means "not counted yet", not a drop.
- Snapshots are kept for 400 days, then the normal cleanup removes them.

**Evidence rules.** These are defaults in the limits file, and they are starting guesses.
- **"Live days"** count from the first snapshot where the listing is active.
- **Sales winner** (needs transactions_r):
  - live at least 21 days;
  - at least 3 orders in the last 30 days, on at least 2 different days;
  - at least 1 order in the last 14 days.
- **Interest winner** (no sales data, or too few sales):
  - live at least 21 days;
  - at least 150 views and at least 8 new favorites in the last 30 days;
  - views per live day at least 2x the median of our other listings in that niche that have been live 21+ days. This comparison needs at least 5 such listings. With fewer, only the fixed numbers above apply.
- **KDP winner:**
  - live at least 30 days;
  - at least 5 paperbacks in the last full month, or at least 8 in the last 2 full months.
- **Quality gates.** Any one of these blocks the theme:
  - a 1-2 star rating on any item of the theme in the last 60 days;
  - any item of the theme rejected in review for a rule reason (trademark or look-alike flag, medical words);
  - an approval rate under 70% for the theme, once there are at least 3 decisions;
  - a seasonal theme whose season ends within 21 days (Etsy) or 60 days (KDP).
- **"Watching"** means half the bar. It shows in reports only. Hermes may only suggest searches for it.
- **Caution:** small numbers are noisy, and 3 sales can be luck. That is why scale-ups are small, go one at a time per theme, and are judged afterward (A5).
- **Winners file:** `data/performance/winners.json` (schema `winners/v1`).
  - It is rebuilt after each daily check, with no model calls.
  - Hermes reads it as a Level 0 tool.
  - Fixed fields only:
    - `ref`, `tier`, `shop_id`, `niche`, `theme`, `theme_key`;
    - its items: item_id, size, source, set_id, live_days, orders_30d, order_days_30d, views_30d, new_favorites_30d;
    - theme totals;
    - `allowed_kinds` and `blocked` reason codes.
  - No buyer data.

#### A2. What "more of this" may mean
These are the kinds of action, cheapest first.

1. **`pack_size`:** the same theme and niche, in a size that the current brief lists and we haven't made yet.
   - **Which sizes:** only those in the brief's `pack_sizes` (5, 20 and the top sellers' size). CLAUDE.md defines products that way, so sizes like 10 or 30 need your OK (decision 12).
   - **Honest note:** Botsy already makes every size the brief lists. So this kind mostly puts the winner first in line this week. It adds a new product only in two cases:
     - a newer brief lists a new top-seller size;
     - the 10-items-a-run limit left a size out.
   - **Smaller than the master set:** cut from it, about $0.05-0.15 (listing copy only; estimate). This works only while the master set is under 60 days old, which is today's `find_master` rule. See decision 7.
   - **Larger than the master set:** all-new pages. For example, a fresh 20-page coloring set is about $2.50-3.00 (estimate).
   - **Cooldown:** its key is (shop, niche, theme_key, size), so a size we never made is not blocked. An old item with no recorded size blocks every size.
2. **`sibling_niche`:** the same theme in a related niche.
   - Default map:
     - coloring pages, stickers and activity sheets are siblings of each other;
     - stationery goes to stickers (planner stickers);
     - ttrpg has no sibling.
   - It needs a current brief in that niche with the same theme_key.
3. **`adjacent_theme`:** a different theme that research links to the winner, through a shared keyword cluster or pattern.
   - Later, the SNN may suggest links too, as advice only.
   - The theme must be in a current brief.
   - It must not be a renamed copy of the winner (see the same-theme guard below).
4. **`kdp_seed`:** one KDP book made from the winner's design set.
   - **Existing decision:** one Etsy design set seeds at most one KDP book, and no page is shared between two KDP books.
   - **It needs:**
     - a KDP brief (10 books from 3 authors);
     - a free KDP slot;
     - an overlap of 0 with our other KDP books.
   - **Limits:**
     - sales winners and KDP winners only;
     - at most one book per winning theme from scale-ups; a second needs you;
     - available only after the KDP build.
5. **`pod_product`:** reserved. Refused until print-on-demand is switched on, after the Printify test.
6. **`fresh_volume`** ("Volume 2": same theme, niche and size, all-new pages): **refused.**
   - Today's code never makes a second pack of the same (shop, niche, theme, size) once one has a real Etsy draft, even with `--regenerate`.
   - This is the "no duplicate products" rule. Changing it is your call (decision 8).

**When a needed brief is missing.** This happens when research no longer supports the theme, or the sibling niche has no data yet.
- Botsy refuses the action with `no_current_brief`.
- Hermes may add a Level 1 search idea instead, for example "axolotl garden stickers (recheck)". It goes on your search list marked "exploratory".
- If your exports then show 10+ listings from 3+ shops, the next plan can use the theme.
- This search loop is the main way a winner grows.

**Rules no scale-up can skip:**
- **Current brief:**
  - the newest brief file for that niche;
  - no older than 6 days (`pipeline.max_brief_age_days`);
  - it passes `core.briefs`: 10 listings and 3 shops for listing-backed patterns. In the refined stage: 15 listings, 3 shops, seen in 3 weeks.
- **Checks on content:**
  - the blocklist;
  - the medical rules from your point 7 (no medical log books; lifestyle journals and logs are fine);
  - the ttrpg game-system name rules.
- **The theme always comes from the brief.** Hermes never types a theme or a prompt.
- **Cooldown:** it must be free for that pack.
- **Same-theme guard:**
  - drop the words vol, volume, part, set, pack, bundle, collection, edition, new and more, plus numbers and roman numerals;
  - if what is left equals the winner's theme_key, or shares 80% of its words, it is the same theme.

  So a renamed copy can't pass as a related theme.
- **Same shop only.** A scale-up never copies a theme into another of our shops. Etsy's House Rules forbid duplicate shops.
- **Per-theme cap:** at most 8 listings of one theme, across all niches and sizes.
- **Pages:**
  - Derived packs reuse pages only from their own master set, and the listing says so (existing rule).
  - Fresh scale-up packs share no page_id with any item. They also have no near-duplicate image (perceptual hash) among the theme's other items, in any niche.
- **Titles:** the publisher already refuses an exact duplicate of one of our titles.
- **Unchanged:** drafts only, the AI disclosure, the review queue, and every listing check.

**Etsy and KDP rules (checked 2026-10-16):**
- **Etsy House Rules** (Seller Policy, "Managing your Etsy Shop"): sellers must "Not create duplicate shops or take any other action (such as manipulating clicks, carts or sales) for the purpose of shilling, manipulating search or circumventing Etsy's policies."
  - Etsy's own page refused our fetch (HTTP 403), and we did not try to get around that.
  - The wording comes from search results and a third-party archive copy (marked "Last updated on Jan 14, 2026"). That copy has no sentence with "identical" or "same item".
  - So we found no official Etsy rule on near-identical listings within one shop (unverified).
  - Our guards make each listing a different product and say what it shares.
- **KDP:** its "Guide to Kindle Content Quality" lists as disappointing content:
  - "Content that is not significantly different from content in another book available in the Kindle Store";
  - "Content that is excessively reused, recycled, or repeated within or across books".

  That guide is written for eBooks. KDP's Content Guidelines "apply to book content, including title, cover art, and product description" and point to the guide.
  - Our guards:
    - one design set leads to one book;
    - overlap 0 between our KDP books;
    - one scale-up book per winning theme.

#### A3. "Botsy has room": the capacity check
Botsy runs this check when the plan arrives, and again before each pack is made. Every check must pass. Each failure has a fixed reason code.

1. **Money.** This is Botsy's $25 cap, counted per UTC calendar month.
   - Projected spend = spent so far + the estimated cost of planned items not made yet + the scale-ups.
   - Estimates use the median actual cost per pack (by niche and source) over 30 days, x1.5.
   - Before there is data, a fixed table: derived pack $0.15; procedural pack $0.20; 20 stickers $1.40; fresh 20-page coloring pack $3.00.
   - Scale-ups are allowed only while:
     - projected spend stays under the 80% warning line ($20 of $25);
     - scale-ups total at most $6 this month;
     - there has been no `spend_warning` or `budget_reached` this month.
   - A plan that starts in a new month is checked against the new month's money.
   - `check_budget` still guards every paid call, as today.
2. **Hermes's $5 limit.**
   - Botsy can't see it, because it lives in a separate Anthropic workspace.
   - When it is reached, Anthropic refuses Hermes's requests with HTTP 400 ("You have reached your specified workspace API usage limits").
   - So Botsy limits Hermes's extra work instead: scale-ups add at most 3 items a week for Hermes to triage.
   - If Hermes runs out, Botsy finishes the plan it has.
3. **Server load**, from Botsy's own records of each check:
   - the median check time over 7 days is under 2 hours;
   - no check was killed for time or memory in 7 days;
   - swap use is under 50%;
   - Botsy's CPU time is under about 5.8 hours a day.

   Why 5.8: the $24 plan sustains 20% per vCPU on 2 vCPUs, which is 9.6 CPU-hours a day. Above that it spends burst credits. 5.8 is 60% of 9.6. This is rough, because Botsy can't read AWS burst credits.
4. **Your queues**, so you aren't flooded:
   - at most 6 items in Botsy's review queue;
   - at most 20 Etsy drafts waiting for you to publish;
   - the oldest waiting draft is no more than 10 days old;
   - at most 4 KDP kits ready and not yet uploaded.
5. **Etsy API.**
   - Botsy records `x-limit-per-day` and `x-remaining-today` (a rolling 24-hour window).
   - Scale-ups are allowed only with:
     - at least 30% of the daily quota left;
     - no HTTP 429 in the last 24 hours;
     - fewer than 10 new drafts today.
   - This is unlikely to limit anything, because Botsy makes few calls.
6. **KDP.**
   - `kdp_seed` needs a free creation slot in the KDP week when the kit will be ready. KDP allows 2 per format per week, reset Sunday 00:00 UTC.
   - Fewer than 4 kits may be waiting.
7. **Disk:** at least 15% and at least 12 GB free after the order's expected size.
8. **Images:** no fresh-page scale-ups if Gemini returned an HTTP 429 in the last 24 hours.

#### A4. The admin-only limits file
Botsy reads `/etc/botsy/limits.yaml`. It is the same admin-only file OVERSEER.md describes, owned by root, mode 0644.
- Changing it needs sudo. Hermes can't touch it.
- If the file is missing or can't be read, scale-ups are off.
- `setup.sh` installs the shipped example only when no file exists.

```yaml
scale_up:
  enabled: true
  max_packs_per_week: 3
  max_share_of_week: 0.30            # at most max(1, floor(0.30 x the week's packs))
  min_new_theme_share: 0.50          # at least half the week's packs are themes new to the shop
  max_usd_per_month: 6.00
  max_projected_share_of_cap: 0.80   # stay under the existing 80% warning line
  allowed_kinds: [pack_size, sibling_niche, adjacent_theme, kdp_seed]   # pod_product and fresh_volume stay off
  interest_winner_kinds: [pack_size] # without sales data, only the cheap kind
  extra_sizes: false                 # true only if you allow sizes the brief doesn't list (decision 12)
  master_reuse_days: 60              # 180 if you agree (decision 7)
  per_theme:
    max_open_orders: 1               # the next order waits for this one's verdict
    max_listings_total: 8
    max_kdp_books: 1
  judge_after_live_days: 30
  no_result_after_days: 45           # nothing live by then: closed with no verdict
  failed_below_parent_share: 0.25
  good_above_parent_share: 0.75
  failed_theme_block_days: 90
  global_stop_after_failures: 2
  global_stop_window_days: 60
  sibling_niches:
    coloring_pages: [stickers, activity_sheets]
    stickers: [coloring_pages, stationery]
    activity_sheets: [coloring_pages, stickers]
    stationery: [stickers]
    ttrpg: []
  winners:
    min_live_days: 21
    window_days: 30
    sales: {min_orders: 3, min_order_days: 2, recent_days: 14}
    interest: {min_views: 150, min_new_favorites: 8, min_ratio_to_niche_median: 2.0, min_peers: 5}
    kdp: {min_live_days: 30, min_units_last_month: 5, min_units_two_months: 8}
    min_approval_rate: 0.70
    bad_rating_block_days: 60
    season_lead_days: {etsy: 21, kdp: 60}
  capacity:
    max_review_queue: 6
    max_waiting_drafts: 20
    max_oldest_waiting_draft_days: 10
    max_ready_kdp_kits: 4
    max_median_tick_hours: 2
    max_cpu_hours_per_day: 5.8       # 60% of the $24 plan's 9.6 sustained CPU-hours a day (AWS: 20% per vCPU x 2)
    max_swap_used_share: 0.50
    min_free_disk_pct: 15
    min_free_disk_gb: 12
    min_etsy_quota_share: 0.30
    max_new_drafts_per_day: 10
```

#### A5. The `scale_up` order (Level 2)
- Hermes sends it together with the weekly plan, or with a revision of the plan before the plan starts.
- Version 1 has no mid-week scale-ups. They can be added later if a week turns out too slow.
- In the first month, the plan and its scale-ups need your code anyway (B3).

Example:

```json
{
  "schema": "order/v1",
  "type": "scale_up",
  "client_ref": "h-20261115-1",
  "plan_week": "2026-W47",
  "winner_ref": "win-2026-11-15-coloring_pages-axolotl-garden",
  "reason_code": "sales_winner",
  "actions": [
    {"kind": "pack_size", "niche": "coloring_pages", "brief_id": "coloring_pages-2026-11-14-03", "size": 10},
    {"kind": "sibling_niche", "niche": "stickers", "brief_id": "stickers-2026-11-14-02", "sizes": [20]}
  ],
  "max_cost_usd": 2.00
}
```

In this example, the newer coloring brief lists 10 pages as the size top sellers use.

**Fields:**
- `client_ref`: up to 40 characters, using a-z, 0-9 and hyphens. Sending the same ref twice makes one order.
- `plan_week`: the ISO week of the plan's Monday. It must be the week of the plan it comes with.
- `winner_ref`: a record from Botsy's winners file. Hermes never supplies numbers.
- `reason_code`: one of sales_winner, interest_winner, kdp_winner.
- `actions`: 1 to 3 actions. Each has:
  - `kind`, from the list in A2;
  - `niche`, from the settings;
  - `brief_id`, required, because the theme comes from it;
  - `size` or `sizes`.

  Kind-specific fields:
  - `adjacent_theme` adds `adjacency_ref`, from Botsy's own adjacency list;
  - `kdp_seed` adds `set_id` and `kdp_template`, from a fixed list.
- `max_cost_usd`: Hermes's ceiling for the order.
- **No free-text field at all.** Unknown fields are refused.

**Botsy's checks, in order.** The first failure refuses that order, with reason codes.
1. **Format:** types and sizes are valid, and there are no unknown fields.
2. **Switches:**
   - `enabled` is on, and scale-ups are not auto-stopped;
   - the order comes with this week's plan, before the plan starts;
   - Hermes's change limit (20 an hour) is not used up.
3. **Winner:**
   - the record exists and was computed in the last 48 hours;
   - it still qualifies;
   - its tier allows each action kind.
4. **Each action:**
   - a current brief, `core.briefs`, the blocklist, the medical rules and the ttrpg rules all pass;
   - the theme matches the kind:
     - `pack_size` and `kdp_seed` use the winner's theme_key;
     - `sibling_niche` uses the same theme_key in a sibling niche;
     - `adjacent_theme` must be on the adjacency list and use a different key;
   - for `kdp_seed`: the set belongs to the winner's theme and hasn't seeded a book yet.
5. **Duplicates:** the cooldown, the same-theme guard, the same-shop rule, the per-theme cap.
6. **Limits:** packs per week, share of the week, share of new themes, the monthly scale-up dollars, one open order per theme.
7. **Capacity:** every check in A3.
8. **Cost:** Botsy's own estimate fits within `max_cost_usd` and the money rules.

**If the order passes:**
- Botsy gives it an id, for example `su-20261115-0001`, and saves it in `data/orders/`.
- Its status moves through accepted, running, done, cancelled, failed, stopped or no_result.
- Its packs join the week's plan, marked `origin: scale_up`.
- The plan shows it as one line (example below).

**If it is refused:** the reason codes go back to Hermes, and into Botsy's log. Hermes may revise the plan, up to 3 times (B4).

**Before each pack is made,** Botsy runs checks 4-8 again. A pack that fails is skipped with its reason, never forced.

**Undo:** `order_cancel {order_id}`. It is Level 2 and always allowed, because it only stops work.
- **Packs not made yet:** dropped, at no cost.
- **Made but not yet a draft:** Botsy doesn't draft them. They wait in the review queue, marked "scale-up cancelled", for you to approve or reject.
- **Already Etsy drafts:** Botsy lists them for you to delete in Etsy. A cancel never deletes anything on Etsy, even if Botsy keeps the `listings_d` permission.
- **Money already spent** can't be returned. Botsy reports how much it was.
- Cancelled orders don't count as failures.

**Pausing and resuming:**
- `scale_up_pause` (Level 2) stops all scale-ups.
- Resuming after a pause is Level 2.
- Resuming after Botsy's automatic stop needs a code (Level 3), like restarting paid work after a budget stop.

**Judging results and the auto-stop:**
- Botsy judges each order 30 days after its items went live.
- **For sales winners:** it compares the scaled items' combined orders per live day with the parent's rate in the parent's own first 30 live days.
- **For interest winners:** it compares views per live day the same way.
- **Results:**
  - below 25% of the parent: **failed.** The theme gets no scale-ups for 90 days;
  - 25-75%: **neutral.** One more scale-up of the theme is allowed;
  - above 75%: **good.**
- If nothing from the order is live after 45 days (for example, you didn't publish the drafts), the order closes as **no result.** It counts neither way.
- **2 failed orders within 60 days stop all scale-ups.** Botsy posts that, and resuming needs your code.
- If the parent stops selling (no order in 30 days), the order's packs not made yet are cancelled.
- If you reject a scaled item for a rule reason, its order is cancelled. The theme is then blocked until you say otherwise.

**Example line inside a plan:** "More of what sells (2 packs): 'axolotl garden' sold 4 times in 30 days, on 3 different days. Adding the 10-page coloring pack top sellers now use, cut from the same pages (about $0.10), and 20 stickers from this week's sticker brief (about $1.40). Undo: cancel su-20261115-0001."

---

### Part B. The Sunday 8 pm weekly plan

#### B1. What "8 pm EST" means here
- **Default:** 8 pm New York time all year. That is 8 pm EDT in summer and 8 pm EST in winter. You can change this (decision 1).
  - Sunday 8 pm EDT = Monday 00:00 UTC.
  - Sunday 8 pm EST = Monday 01:00 UTC.
- **If you literally meant EST all year (UTC-5),** the plan would arrive at 9 pm your time in summer. The line would then be `OnCalendar=Mon *-*-* 01:00:00 UTC`.
- **"Start" is read like this:**
  - the plan is posted at 8 pm;
  - in the first month, work starts when you approve;
  - from plan 5 on, work starts Monday at 8:00 am New York time. That is 12 hours after posting, the Hold window the proposal offered and you agreed to.
- **The alternative reading** (decision 2): post the plan at 8 am Sunday, so that work starts at 8 pm.

#### B2. The timer
Botsy's own systemd timer is the clock, not Hermes's scheduler. Two reasons:
- the lock-down says only you create Hermes jobs;
- Hermes's time zone handling is untested.

```
# deploy/lightsail/systemd/botsy-plan.timer
# Every Sunday at 8 pm New York time (EDT in summer, EST in winter), whatever the
# server's own time zone. systemd reads zone names since version 235; Ubuntu 24.04 has 255.
[Unit]
Description=Botsy: weekly plan facts for Hermes, Sunday 8 pm New York time

[Timer]
OnCalendar=Sun *-*-* 20:00:00 America/New_York
Persistent=true
Unit=botsy-plan.service

[Install]
WantedBy=timers.target
```

- **Checked 2026-10-16** with `systemd-analyze calendar` on Ubuntu 24.04.5 (systemd 255.4-1ubuntu8.17), in a test container, not on Lightsail. Next runs:
  - Mon 2026-10-19 00:00 UTC;
  - Mon 2026-10-26 00:00 UTC;
  - Mon 2026-11-02 01:00 UTC (after the clocks change on Nov 1);
  - back to 00:00 UTC from Mon 2027-03-15 (after the clocks change on Mar 14).
- **Check on the server:** `systemd-analyze calendar --iterations=3 "Sun *-*-* 20:00:00 America/New_York"`.
- **`botsy-plan.service`** runs `python -m service plan prepare`.
  - It only reads Botsy's records, like the daily log, so it never waits for a check that is running. The plan posts on time.
  - It writes `data/plans/<week>/facts.json`: money left, the capacity check, winners, current briefs, KDP slots and last week's results.
  - It sends a signed `plan_due` event to Hermes.

  Hermes then writes the plan (a Level 2 `weekly_plan` order with any `scale_up` orders) and posts it in Discord at about 8:00-8:05 pm. Botsy checks the plan and posts one line, for example: "Plan 2026-W47 checked: 9 packs, about $6.40, fits."
- **If the server was off,** `Persistent=true` runs the job at boot. A run more than 24 hours late is skipped, with the message "this week's plan was missed".
- **`botsy-plan-check.timer`** runs every hour at :05 (`OnCalendar=*-*-* *:05:00`).
  - It runs `python -m service plan check`: reminders, deadlines, auto-start and the no-plan notice.
  - It makes no paid calls.
  - It takes the main lock only to start a plan's first check.

#### B3. The first month: you approve each plan
- **"First month" means the first 4 plans you approve,** not calendar weeks.
  - Expired or held plans don't count.
  - Each plan says "plan 2 of 4 that need your OK".
  - The count is kept in `data/plans/state.json`.
- **Approval with a code (Level 3).** Botsy can't tell whether you pressed a Hermes button, so:
  1. you say "approve";
  2. Botsy posts a 6-digit code in #botsy-codes, with its own facts (packs, cost, KDP slots, scale-ups);
  3. you type the code to Hermes.

  That is 4 codes in total. Codes are urgent, so they ignore quiet hours.
- **When you approve,** Botsy starts one check right away, so work begins that night. It does not count toward "run now".
- **If you don't answer:**
  - Mon 8:00 am: reminder 1.
  - Mon 7:00 pm: reminder 2, "expires Tuesday 8 pm".
  - Tue 8:00 pm: the plan expires. Botsy says so, then runs on its normal rules for the rest of the week (decision 4):
    - the newest briefs;
    - at most 10 items a run;
    - no scale-ups;
    - no KDP slots assigned by a plan.
  - You can then ask Hermes for a new plan. It goes through the same checks and needs the same code. It counts toward the 4 if you approve it.
- **While a plan waits,** Botsy only finishes last week's plan lines (B5).
- **Hold** is always allowed, with no code. The plan keeps waiting, and the Tuesday deadline still applies.
- **Change:** Hermes revises the plan, and Botsy checks it again.
  - The revision still needs your code.
  - The deadline stays Tuesday 8 pm.
  - Any change after you approved needs a new code.

#### B4. From plan 5: automatic, with Hold
- The plan posts Sunday at 8 pm with [Hold] [Change] [Start now].
- It starts by itself Monday at 8:00 am New York time. Botsy's 8:05 check starts it and runs one check.
- **No code needed** for Hold, Start now, or approving a held plan (all Level 2).
- **A held plan** waits until you approve or change it, or until next Sunday's plan replaces it. Botsy finishes last week's lines meanwhile.
- **A revised plan** starts at least 2 hours after the revision is posted, and never before Monday 8:00 am. So a revision can't skip your Hold window.
- **Hermes may revise a plan up to 3 times.** If no version passes, there is no plan that week.
- **Big plans still wait for you:** a plan over 40% of the cap ($10 of $25) needs your code, as in the first month (decision 10).
- **Going back:**
  - "approve plans again" is allowed any time, because it is stricter;
  - switching back to automatic, or going automatic before 4 approved plans, needs a code.
- **Unchanged:** if Hermes is down, or a plan is more than 8 days old, Botsy runs exactly as it does today.
- **If no valid plan exists at 9:00 pm Sunday,** Botsy posts: "No plan this week. Botsy keeps running on its normal rules."

#### B5. What Botsy makes while plans are on
Today every check, daily or after an upload, researches and then makes items from the newest briefs. With plans:
- **A plan is running:** every check makes only the plan's lines not made yet. It still makes at most 10 a run, plus QA, listings and drafts of items already made or approved.
- **Uploads:** an upload still runs ingest and research right away. The new briefs wait for the next plan. Without this, a Saturday upload would be spent on new items before Sunday's plan could choose.
- **A plan is waiting for your OK (first month):** Botsy only finishes last week's plan lines. In week 1 there are none, so nothing new is made until you approve or the plan expires.
- **No plan, an expired plan, Hermes down, or a plan more than 8 days old:** today's normal rules.
- **A plan line whose brief turns 7 days old** before it is made is dropped, with the reason. Briefs get a new date only when new exports arrive. Most work happens Sunday night to Tuesday, so this should be rare.
- **The weekly GitHub Action** (Mondays 13:17 UTC) doesn't follow plans. Keep `PIPELINE_PUBLISH` off once the server makes drafts (already an open decision).

#### B6. A coherent week (all times New York time)

| When | What | UTC in summer / winter |
|---|---|---|
| Sat 9:07 am | Weekly search list. Moved from Sun 18:07 UTC (2:07 pm EDT) so you have the weekend to export. | 13:07 / 14:07 |
| Sat-Sun daytime | You export. Each upload runs ingest and research. Under a plan, it makes only plan items. | |
| Sat 8 pm EDT / 7 pm EST | KDP's weekly slots reset (Sunday 00:00 UTC). | 00:00 Sun |
| Sun by about 7:00 pm | Last upload that will surely be in tonight's plan. | |
| Sun 8:00 pm | Plan facts go to Hermes, and the plan is posted. | 00:00 / 01:00 Mon |
| Sun 9:00 pm | If there is no valid plan, Botsy posts "no plan this week". | |
| Mon 8:00 am | First month: reminder 1. From plan 5: the plan starts. | 12:00 / 13:00 |
| Mon 7:00 pm | First month: reminder 2. | |
| Tue 8:00 pm | First month: the plan expires if not approved. | |
| Daily 6:17 am | Daily check: own stats, snapshot, winners, research if exports changed, plan items, drafts. Today it runs at 2:17 am EDT. | 10:17 / 11:17 |
| Daily 7:07 am | Daily log, now with plan progress and scale-up lines. Today it arrives at 3:07 am EDT. | 11:07 / 12:07 |
| 10 pm-7 am | Quiet hours. Today they are in UTC, which is 6 pm-3 am EDT. | |
| Hourly at :05 | Plan check (no paid calls). | |

New timer lines (all checked with `systemd-analyze calendar`):
- `botsy-weekly.timer`: `OnCalendar=Sat *-*-* 09:07:00 America/New_York`
- `botsy-daily.timer`: `OnCalendar=*-*-* 06:17:00 America/New_York`
- `botsy-digest.timer`: `OnCalendar=*-*-* 07:07:00 America/New_York`

#### B7. Time zones
- **Every Botsy timer names the zone.** Botsy's quiet hours and plan times use one setting, `schedule.timezone: America/New_York`. So Botsy's Sunday-evening messages aren't held as "night" messages.
- **`setup.sh` also sets the server's zone** to the same value with `timedatectl` (already described in the README as optional). This keeps item dates, and the week each export counts in, on New York time.
  - Without it, an export uploaded Sunday after 8 pm EDT (7 pm EST) lands in the next ISO week for research's "seen in 3 weeks" rule.
  - The daily log warns if the server zone and `schedule.timezone` differ.
- **Some things stay in UTC on purpose:**
  - **Money months.** Botsy's cap and Anthropic's own spend cap reset at 00:00 UTC on the 1st. A plan posted on the last Sunday of a month can start in the next month. Example: Sun Jan 31, 2027, 8 pm EST is Mon Feb 1, 01:00 UTC. Botsy then checks it against the new month's money.
  - **KDP weeks.** They reset Sunday 00:00 UTC.
    - The plan's KDP slots belong to the KDP week that began about 24 hours before the plan. That week ends the coming Saturday at 8 pm EDT (7 pm EST).
    - So upload KDP books Monday to Saturday, before about 7 pm. A Saturday-night or Sunday upload uses the next week's slots.
    - The KDP helper counts by KDP's week, never by the plan week.

---

### Failure modes

**Scale-ups:**
- **Few sales, or noise:** limited by the thresholds, one open order per theme, judging at 30 days and the auto-stop.
- **No `transactions_r`:** only interest winners, and only `pack_size`, which mostly means "first in line".
- **Views not counted yet (0):** treated as missing, not as a drop.
- **You don't publish drafts:** there is no live data, so no winners. The queue check also stops scale-ups. Orders close as "no result" after 45 days.
- **Theme evidence expired:** refused with `no_current_brief`. Hermes may add a recheck search.
- **Seasonal winner:** stopped by the season lead-time gate.
- **The new pack takes sales from the parent:** judging compares the scaled items with the parent's own early rate, and the per-theme cap limits flooding.
- **Etsy treats packs as near-duplicates:** each listing differs (size, niche or all-new pages) and says what it shares. Etsy's exact rule is unverified.
- **Cost estimate too low:** `check_budget` still stops every paid call at the cap. Scale-up dollars are counted from the ledger by item, mapped to orders.
- **Limits file missing or broken:** scale-ups are off.
- **Refunds:** invisible, because receipts aren't read. A refunded sale still counts.
- **KDP report format unknown:** KDP winners wait for your sample.
- **Tight money:** if the base plan fills the month, scale-ups simply don't run. The plan says so.

**Weekly plan:**
- **Server off at 8 pm:** the plan runs at boot, or is skipped if more than 24 hours late.
- **Hermes down or out of budget:** the 9 pm no-plan notice, then normal rules.
- **Plan fails Botsy's checks:** up to 3 revisions, then no plan that week.
- **You are away:** the reminders, then the Tuesday 8 pm expiry, then normal rules.
- **Clock changes:** systemd handles them. Tested for Nov 1, 2026 and Mar 14, 2027.
- **Server zone left on UTC:** the timers and plan times are still right. Item dates and export weeks stay UTC, and the daily log warns.
- **A long check is running at 8 pm:** `plan prepare` doesn't wait. It uses the last finished briefs.
- **Upload arrives after about 7 pm Sunday:** it counts for next week's plan.
- **Brief turns 7 days old mid-week:** that plan line is dropped, with the reason.
- **A code expires (10 minutes) or #botsy-codes fails:** ask again. The failure is posted as an error, and the deadline still applies.
- **Hermes submits two plans in one week:** one plan per week. Later ones are revisions, with the Hold-window rules.

### Cost (estimates)
- **Botsy's new checks:** $0 in API money. They make no model calls.
- **Hermes:** scale-up lines ride inside the weekly plan, about $0.05 a month extra.
- **Scale-up production:** at most $6 a month, inside Botsy's $25 cap (default limit).
- **Etsy listing fees** (outside the $25 cap):
  - $0.20 for each listing you publish, and another $0.20 every 4 months while auto-renew is on. Drafts are free until you publish.
  - At 3 scale-up packs a week, that is about 13 listings a month, or about $2.60 a month if you publish them all (estimate).
  - Etsy also takes fees on each sale (not checked in this design).
  - Source: Etsy's fee policy as shown in search results; Etsy's page itself returned HTTP 403.

---

### Text to add to docs/OVERSEER.md

~~~markdown
#### Weekly plan schedule (user, 2026-10-16)
- **When:** every Sunday at 8 pm New York time (EDT in summer, EST in winter). Botsy's timer is the clock: `OnCalendar=Sun *-*-* 20:00:00 America/New_York` in `botsy-plan.timer`. Botsy sends Hermes the week's facts, and Hermes posts the plan. If there is no valid plan by 9 pm, Botsy says so and runs on its normal rules.
- **First 4 approved plans:** each needs your OK with a code, because Botsy can't tell whether you pressed a button. The plan says "plan N of 4".
  - Reminders: Monday 8 am and 7 pm.
  - If it is not approved by Tuesday 8 pm, it expires, and Botsy runs on its normal rules that week.
  - While a plan waits, Botsy only finishes last week's plan lines.
- **From plan 5:** the plan starts by itself Monday at 8 am (12 hours after posting) unless you press Hold.
  - Hold, Start now and approving a held plan need no code.
  - A plan over $10 still needs your code.
  - A revised plan starts no sooner than 2 hours after the revision, and never before Monday 8 am.
  - You can say "approve plans again" any time. Going back to automatic needs a code.
- **While plans are on:** every check makes only the plan's lines. Uploads still run research right away, and the new briefs wait for the next plan.
- **The week, New York time:** search list Saturday 9:07 am; exports over the weekend (by about 7 pm Sunday); plan Sunday 8 pm; daily check 6:17 am; daily log 7:07 am; quiet hours 10 pm-7 am.
- **Stays UTC:** money months (the cap resets on the 1st at 00:00 UTC) and KDP weeks. KDP's 2 slots reset Sunday 00:00 UTC, which is Saturday 8 pm EDT / 7 pm EST, so upload KDP books Monday to Saturday before about 7 pm.

#### Scale-ups: more of what sells (user, 2026-10-16)
- **Who decides what sells:** Botsy, from our own numbers only:
  - Etsy views and favorites of our listings;
  - sales, if you add the transactions_r permission (Botsy keeps listing, day, quantity and price, and never reads receipts or buyer details);
  - star ratings of our own listings (no review text);
  - the KDP royalty reports you download;
  - your review decisions.

  Hermes reads Botsy's winners list. It never supplies numbers.
- **A winner (defaults):**
  - live 21+ days, with 3+ orders in 30 days on 2+ different days and one in the last 14 days;
  - or, without sales data: 150+ views, 8+ new favorites, and twice the niche's usual views per day.

  Also: no bad ratings, no rule rejections, an approval rate of 70%+, and the theme's season is not about to end.
- **What "more of this" may be:**
  - the winner's missing pack sizes, put first in line (only sizes the brief lists);
  - the same theme in a related niche;
  - a related theme;
  - later, one KDP book from the winning design set.

  Each needs a current brief that passes the 10-listing / 3-shop floor and the blocklist. Hermes may add search ideas so that your exports can supply those briefs. "Volume 2" of the same theme, niche and size stays off unless you turn it on.
- **Botsy has room:**
  - the month stays under the 80% warning line, with at most $6 a month for scale-ups;
  - the server isn't busy;
  - your queues aren't full (6 review items, 20 waiting drafts, none older than 10 days);
  - Etsy's daily quota is 30%+ left;
  - KDP slots are free;
  - the disk has 15% / 12 GB free.
- **Limits** (admin-only file `/etc/botsy/limits.yaml`):
  - at most 3 scale-up packs a week, and 30% of the week's packs;
  - at least half of each week goes to themes new to the shop;
  - one open scale-up per theme;
  - at most 8 listings and 1 KDP book per theme.
- **It's a Level 2 order (`scale_up`)** that comes only with the weekly plan. It is capped, checked again before every pack, and undone with `order_cancel`:
  - packs not made yet are dropped;
  - made ones wait in review;
  - drafts are listed for you to delete. Botsy never deletes on a cancel.
- **Auto-stop:** each order is judged 30 days after its items go live.
  - Below a quarter of the parent's rate, it counts as failed, and the theme waits 90 days.
  - Two failures in 60 days stop all scale-ups, until you resume them with a code.
~~~

**Other edits to OVERSEER.md:**
- **Level 2 list:** add "scale-up orders, with the weekly plan only (at most 3 packs a week, inside the limits file)".
- **Level 3 list:** add four code-only actions:
  - approving one of the first 4 weekly plans;
  - approving a plan over $10 after that;
  - switching plans to automatic early, or back to automatic;
  - resuming scale-ups after Botsy's auto-stop.
- **Production director:** change "Every Monday it proposes" to "Every Sunday at 8 pm New York time it proposes". Add "More of what sells" as a plan line.
- **Performance analyst (Later):** note that the daily own-listing snapshot starts with the plan build, so history exists when the analyst and scale-ups arrive.
