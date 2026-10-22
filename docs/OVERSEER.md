# Hermes: active director and helper for Botsy

> **Status: proposed 2026-10-16. Built 2026-10-21: the foundation (orders, limits, capacity, botsy-control, the Hermes install files) and the Sunday plan (Part B); Hermes itself is not installed yet. No codes (decided 2026-10-21, below).** Decisions still open are listed in [PROPOSAL_2026-10-16.md](PROPOSAL_2026-10-16.md); build order is there too.

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
  - your approval for every review approval (no codes since 2026-10-21: a plain approve, posted at once). One approval may cover a batch of named items.
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
> **Decided 2026-10-21: no codes** (user: "I don't want the codes"). There are no 6-digit codes, no #botsy-codes channel and no codes webhook. The actions below that needed a code are accepted straight from you: through Hermes (your allowlisted Discord user only; each act call also waits for your Allow click on Hermes's approval button) or the server CLI (`botsy service plan approve`, `botsy service control ...`). Guards that stay: hard-blocked items are never approved; raise_cap never goes above `cap_ceiling_usd` in the root-only limits file; at most limits `level3_per_day` (10) such approvals in 24 hours (`approve_batch_max` 20 items in one batch); each one is posted at once to the main Discord channel (urgent, quiet hours never hold it) with what changed, who asked (hermes | user) and how to undo it; all are journaled with fixed fields. The first 4 plans and plans over 40% of the cap still wait for your plain approval.

- **Level 0, read:** status, spending, briefs (aggregated numbers and terms only), our own previews, our own shop's stats.
- **Level 1, suggest:** notes, recommendations, search ideas, preferences. Nothing changes until Botsy's normal rules act on them.
- **Level 2, small changes:**
  - covers the weekly plan, page redos, listing edits on not-yet-approved items, and a few safe settings;
  - Botsy checks every value and enforces limits from an admin-only file, for example 20 changes an hour, 10 page redos a week and 3 "run now" a day;
  - Hermes may ask you with buttons first, but that is a convenience, not a safeguard. Botsy cannot tell whether you pressed anything, so every Level 2 change is capped and can be undone even if Hermes acts without asking.
- **Level 3, your approval (no codes):**
  - approving any held item (a named batch is fine);
  - raising the cap (never above the admin's ceiling);
  - restarting paid work after a budget stop, or after Botsy's own stop;
  - approving a weekly plan that waits for you, or switching plans to automatic.

  How approvals work (2026-10-21): you ask Hermes in plain words, or type the CLI command on the server. Botsy checks the order in full again, applies it, counts it (at most 10 in 24 hours), and posts at once to the main channel what changed, who asked and how to undo it.

### Lock-down: three layers (must do before any role)
> **Decided 2026-10-21: no codes.** There is no #botsy-codes channel to hide from the bot; your Discord account is now the key, so keep two-factor login on and read the approval posts.

**1. Hermes settings.** Verified defaults (2026-10-16):
- Discord chats get every tool, including the terminal, files, web, browser, subagents, scheduled jobs, code running and discord_admin (bans, role changes, channel management).
- Scheduled jobs get the same set.

So (opened up on 2026-10-21, "too cautious; I want Hermes pulling its weight"):
- each place gets an exact list: Discord chat gets the Botsy tools, memory, vision, cronjob, session_search, todo, kanban, skills and the non-admin discord tool; the command line gets the Botsy tools, memory, vision, session_search, todo and skills (no Discord, task board or job tools); scheduled jobs get the same without skills, and `/bg` is off, because their tool calls are not shown in Discord; in Discord every tool call is shown (pinned), so each skill change is posted to you;
- turn off terminal, file, web, search, browser, delegation, code_execution, computer_use, image_gen, video, video_gen, tts and discord_admin (applied last, whatever else is on);
- vision sees only our own images: every web host is on Hermes's website blocklist (vision checks it before any download), the firewall allows only Anthropic and Discord, and systemd hides Botsy's folders and other programs' data;
- turn off sampling and elicitation for botsy-control (both are on by default);
- set memory.write_approval: true and skills.write_approval: true (every skill change waits for `/skills approve`); background review and curator stay off, because v0.21.7 does not post every change they make;
- compress chat history at about 60,000 tokens, because Haiku 5.5 charges 5x for requests over 100,000 tokens and cache reads count toward that;
- Hermes may create its own scheduled jobs; they run on Haiku (Hermes cannot pick a model for a job), cannot act (approvals are declined in jobs), and an hourly check enforces at most 10, none more often than hourly, no scripts or web monitors. Sonnet runs only in the two weekly jobs you pin yourself;
- the task board starts no worker agents (dispatcher off, no profile claimable).

`check_lock.py --probe` asks the pinned Hermes itself what each place gets; checked against a real v0.21.7 install on 2026-10-21.

**2. Operating system:**
- Hermes runs as its own "hermes" user with no sudo except one fixed rule that runs botsy-control;
- that user has no read access to Botsy's .env, data/, output/ or research files;
- required since 2026-10-21: a firewall rule so the hermes user can reach only Anthropic and Discord. Scheduled jobs (URL monitors), the task board (attach from a URL) and vision can each fetch a web address in v0.21.7, so this rule, not a Hermes setting, is what keeps "no web" true.

**3. Discord:**
- invite the Hermes bot with Discord's minimum permissions only: view channels, send messages, send messages in threads, create public threads, embed links, attach files, read message history, add reactions;
- Hermes's discord tool may only read recent messages and start threads (`discord.server_actions`); pins, deletes, roles and member search are discord_admin or left out;
- never Administrator, Manage Channels, Manage Roles or Manage Webhooks;
- botsy-act is `trust: untrusted` in Hermes's config, so every Botsy action waits for your Allow click on Hermes's Discord approval button; scheduled Hermes jobs can never act;
- allow only your Discord user ID (DISCORD_ALLOWED_USERS), never a role;
- turn on two-factor login. With no codes, a stolen Discord account could approve; the daily limit, the ceiling and the at-once posts are what limit the damage.

**Dashboard:**
- the Botsy tab is read-only, plus Pause and Lower cap;
- no Save-to-raise or Approve buttons, because the dashboard has no login on the server itself;
- reach it through an SSH tunnel;
- a Pause from the tab is recorded as Hermes's (`created_by: hermes`), so you or Hermes can lift it. For a Hold only you can lift, use the server CLI (`botsy service control pause`); asking Hermes in Discord also records `hermes`. See "Botsy dashboard tab" below.

**Version:**
- pin, at install time, the newest release that is at least about 7 days old with no hotfix after it. v0.22.2 shipped Oct 15, one day ago, after releases on Oct 13 and 14, and has a known session-deletion issue;
- rerun the lock tests after every update.

### Hermes can never
- run commands, read or edit files, run code, browse, or search the web with its own tools (trend search only through a Botsy tool that strips marketplace listings);
- download an image or look at a competitor's image, title or description (vision sees only our own previews from botsy-read and what you attach);
- make an Etsy listing live, publish on Printify, or log in to KDP;
- change compliance rules, the blocklist, the AI disclosure, the research minimums, the ceiling or the limits file;
- read research exports, research history or raw logs, or see other sellers' data;
- write image prompts or free-text instructions for Botsy's models (fixed options and approved style phrases only);
- save a skill or a memory without your approval;
- pick a model: chat and its own jobs run on Haiku; Sonnet only in the two weekly jobs you made;
- run a script from a scheduled job (the scripts folder is root's and empty); keep a job that watches a web address, runs more often than hourly or uses tools beyond the job list (Hermes v0.21.7 lets it save one; the hourly check then stops Hermes, and until then the firewall limits a URL monitor to Anthropic, Discord and loopback, never the dashboard); or start other agents (no delegation, no task-board workers);
- install add-ons, or change Discord channels, roles, pins or members;
- act without your Allow click: botsy-act is `trust: untrusted`, so scheduled Hermes jobs can never act;
- lift or bypass a Hold you set (Botsy enforces that on its side).

### Roles
**First wave**

**1. Foundation**
- botsy-control "read" and "act" tool servers, order files Botsy checks again, signed fixed-field events, the admin-only limits file and ceiling, the daily approval limit with at-once posts (no codes), the three-layer lock, and a one-line log of every Hermes action in Botsy's own channel.
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
- Every approval is yours: you say so in plain words (Hermes asks "yes?" first) and Botsy posts it at once with how to undo it. Batches are fine.
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
- Added 2026-10-21: the two weekly Sonnet jobs (plan, creative director) at roughly 30,000 tokens in and 3,000 out each, about $0.10 a run, so about $1 a month plus any "run now"; Hermes's own Haiku check-ins at most hourly, about $1-2 a month at that rate; our preview images on Haiku, about 1,000-1,600 tokens each. New estimate: about $7-10.
- Hermes's own workspace limit is now **$10** a month (raised from $5 on 2026-10-21). It is the hard stop: when it is reached Hermes stops answering until next month; Botsy is not affected. Measure the first month before raising it again.
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
- That Hermes's Allow button really blocks botsy-act calls until you click it, and that scheduled jobs are declined.
- Whether Hermes's Discord buttons work for our tools (only a convenience now).
- How well Haiku 5.5 spots print defects and look-alikes.
- Memory use on the 4 GB server.


---

## Scale-ups ("more of this") and the Sunday plan

> **Status: Part B (the Sunday plan) built 2026-10-21 (service/plan.py, Botsy's own draft plan, botsy-plan.timer and botsy-plan-check.timer); Part A (scale-ups) not active until winners exist: only the `scale_up` order type and its refusal (`no_winners_yet`) are built.** **Decided 2026-10-21: no codes:** wherever this part says "code", read "your plain approval" (CLI or Hermes), counted against limits `level3_per_day`, posted at once with who asked and how to undo it. Open choices and build order: [PROPOSAL_2026-10-16.md](PROPOSAL_2026-10-16.md) section "Round 2".

> **First design note, 2026-10-16.** It answers your points 4 and 5 and builds on [OVERSEER.md](OVERSEER.md). All limits and winner rules are starting guesses, not measurements.

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
  - Your first 4 plans wait for your OK (a plain approve; no codes since 2026-10-21).
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
2. **Hermes's $10 limit** (raised from $5 on 2026-10-21, D4 item 2).
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
- In the first month, the plan and its scale-ups wait for your approval anyway (B3).

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
- Resuming after Botsy's automatic stop needs your approval (Level 3), like restarting paid work after a budget stop.

**Judging results and the auto-stop:**
- Botsy judges each order 30 days after its items went live.
- **For sales winners:** it compares the scaled items' combined orders per live day with the parent's rate in the parent's own first 30 live days.
- **For interest winners:** it compares views per live day the same way.
- **Results:**
  - below 25% of the parent: **failed.** The theme gets no scale-ups for 90 days;
  - 25-75%: **neutral.** One more scale-up of the theme is allowed;
  - above 75%: **good.**
- If nothing from the order is live after 45 days (for example, you didn't publish the drafts), the order closes as **no result.** It counts neither way.
- **2 failed orders within 60 days stop all scale-ups.** Botsy posts that, and resuming needs your approval.
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
> **Decided 2026-10-21: no codes.** You approve with a plain `botsy service plan approve` on the server, or by telling Hermes (it calls botsy-control `approve_order`; you also click Allow on its button). Botsy posts the approval at once (urgent) with who asked and the undo (`plan hold`), and counts it against limits `level3_per_day`. The steps with a code below are replaced by that.

- **"First month" means the first 4 plans you approve,** not calendar weeks.
  - Expired or held plans don't count.
  - Each plan says "plan 2 of 4 that need your OK".
  - The count is kept in `data/plans/state.json`.
- **Approval (Level 3, no code).** You say "approve" (Hermes) or type `botsy service plan approve` (server). Botsy checks the plan again (deadline, hold, money, the daily limit), starts it, and posts at once in the main channel what was approved, who asked and how to undo it. That post is urgent, so quiet hours never hold it.
- **When you approve,** Botsy starts one check right away, so work begins that night. It does not count toward "run now".
- **If you don't answer:**
  - Mon 8:00 am: reminder 1.
  - Mon 7:00 pm: reminder 2, "expires Tuesday 8 pm".
  - Tue 8:00 pm: the plan expires. Botsy says so, then runs on its normal rules for the rest of the week (decision 4):
    - the newest briefs;
    - at most 10 items a run;
    - no scale-ups;
    - no KDP slots assigned by a plan.
  - You can then ask Hermes for a new plan. It goes through the same checks and waits for your approval the same way. It counts toward the 4 if you approve it.
- **While a plan waits,** Botsy only finishes last week's plan lines (B5).
- **Hold** is always allowed. The plan keeps waiting, and the Tuesday deadline still applies.
- **Change:** Hermes revises the plan, and Botsy checks it again.
  - The revision still waits for your approval.
  - The deadline stays Tuesday 8 pm.
  - Any change after you approved waits for a new approval.

#### B4. From plan 5: automatic, with Hold
> **Decided 2026-10-21: no codes.** Approving a big plan and going automatic early are plain approvals (`plan approve`, `plan auto`), each posted at once and counted against `level3_per_day`.

- The plan posts Sunday at 8 pm with [Hold] [Change] [Start now].
- It starts by itself Monday at 8:00 am New York time. Botsy's 8:05 check starts it and runs one check.
- **Level 2, no approval post:** Hold, Start now, or approving a held plan.
- **A held plan** waits until you approve or change it, or until next Sunday's plan replaces it. Botsy finishes last week's lines meanwhile.
- **A revised plan** starts at least 2 hours after the revision is posted, and never before Monday 8:00 am. So a revision can't skip your Hold window.
- **Hermes may revise a plan up to 3 times.** If no version passes, there is no plan that week.
- **Big plans still wait for you:** a plan over 40% of the cap ($10 of $25) waits for your approval, as in the first month (decision 10).
- **Going back:**
  - "approve plans again" is allowed any time, because it is stricter;
  - switching back to automatic, or going automatic before 4 approved plans, is a Level 3 approval (`plan auto`: posted at once, undone with `plan again`).
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
- **Too many approvals in 24 hours (`level3_daily_limit`):** wait, or approve once the 24 hours have passed; the deadline still applies. **The approval post fails:** the approval stands, the journal and the daily log record it (`announced: false`).
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
- **First 4 approved plans:** each needs your OK (a plain approve since 2026-10-21; no codes). The plan says "plan N of 4".
  - Reminders: Monday 8 am and 7 pm.
  - If it is not approved by Tuesday 8 pm, it expires, and Botsy runs on its normal rules that week.
  - While a plan waits, Botsy only finishes last week's plan lines.
- **From plan 5:** the plan starts by itself Monday at 8 am (12 hours after posting) unless you press Hold.
  - Hold, Start now and approving a held plan need no approval post.
  - A plan over $10 still waits for your approval.
  - A revised plan starts no sooner than 2 hours after the revision, and never before Monday 8 am.
  - You can say "approve plans again" any time. Going back to automatic is a Level 3 approval (posted at once).
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
  - Two failures in 60 days stop all scale-ups, until you approve resuming them.
~~~

**Other edits to OVERSEER.md:**
- **Level 2 list:** add "scale-up orders, with the weekly plan only (at most 3 packs a week, inside the limits file)".
- **Level 3 list:** add four approval-only actions (no codes since 2026-10-21):
  - approving one of the first 4 weekly plans;
  - approving a plan over $10 after that;
  - switching plans to automatic early, or back to automatic;
  - resuming scale-ups after Botsy's auto-stop.
- **Production director:** change "Every Monday it proposes" to "Every Sunday at 8 pm New York time it proposes". Add "More of what sells" as a plan line.
- **Performance analyst (Later):** note that the daily own-listing snapshot starts with the plan build, so history exists when the analyst and scale-ups arrive.

---

## botsy-control reference

**Decided 2026-10-21: no codes.** There are no 6-digit codes, no #botsy-codes channel, no `BOTSY_CODES_WEBHOOK_URL` and no `request_code` / `confirm_code` tools. The actions that needed a code (review approvals, also in a named batch; raising the cap; restarting paid work after a budget stop or the user's pause; approving one of the first 4 weekly plans or a plan over the cost share) are taken directly from you: through Hermes (Discord, your allowlisted user ID only) or the server CLI. Botsy's guards stay in `service/orders.py` and `service/plan.py`: a hard-blocked item is never approvable, a cap raise never goes above `limits.cap_ceiling_usd` (root-only file), at most `level3_per_day` (default 10) such actions a day and `approve_batch_max` (20) items a batch, and each one is posted at once to the main Discord channel (urgent, ignores quiet hours) with what changed, who asked (hermes, user or botsy) and how to undo it. Botsy still never activates a listing: you approve in Etsy.

> **Status: built 2026-10-21** (`control/`, tests in `tests/control/`; the D4 tools `view_item_images`, `trend_search` and the five `fix_*` tools added the same day). Not yet run against a real Hermes install; Hermes itself is not installed yet. Everything here also works without Hermes: the same orders go through the server CLI (`python -m service plan approve`, `python -m qa approve ...`).

botsy-control is the only way Hermes reaches Botsy. It is two MCP servers over stdio, made with the official MCP Python SDK (`mcp==2.3.0`, MIT, released 2026-10-02; in mcp 2.x FastMCP is called `MCPServer`):

| Server | Wrapper (runs as botsy) | Runs | Level |
|---|---|---|---|
| botsy-read | `/usr/local/bin/botsy-control-read` | `python -m control read` | 0, read only |
| botsy-act | `/usr/local/bin/botsy-control-act` | `python -m control act` | 1-3, orders |

How Hermes reaches the wrappers (deploy/hermes, the Hermes install): by default `botsy-control-connect read|act` links Hermes's stdio to a systemd socket (`/run/botsy-control/read.sock`, `act.sock`, root:hermes 0660), and systemd starts a fresh wrapper as botsy for each connection, so the hermes user needs no sudo. With the sudo bridge, Hermes runs `sudo -n -u botsy /usr/local/bin/botsy-control-read` (or `-act`) under the sudoers rule below.

**How it is locked:**
- The servers are plain glue. Each tool hands its raw JSON arguments to `control/tools.py`, which checks every argument (enums, patterns, lengths, ranges; unknown arguments refused) before anything runs. There is no free-text argument at all. Hermes's values never reach an event, a log line, a model prompt or a file name; a refusal names the argument, never its value.
- Every act tool is an order and goes through `service/orders.py` (`submit`, `validate`, `approve`, `undo`), which checks it again in full. Weekly plans go through `service/plan.py` `submit_order` (Botsy's brief checks, cost estimate and approval count), then `orders.submit`; approving a waiting plan goes through `plan.approve(by="hermes", order_id=..., first_check=False)` (its deadline, daily limit, hold and Discord post). Without the plan service, plans are refused (`plan_service_missing`); a plan service whose `approve` cannot be bound to one order id, or would run a check inside the act server, is never called (`approval_unavailable`). botsy-control never changes anything itself, and `created_by` / `by` is always `hermes`.
- Level 3 is guarded by Botsy, not by Hermes's manners: Hermes is told to act only when you asked in your own words, but Botsy cannot tell whether you did, so the daily limit, the batch size, the ceiling, the hard blocks and the instant main-channel post (with undo) are what protect you.
- The servers offer tools only: no resources and no prompts. They never ask Hermes for sampling or elicitation.
- Every string in a result passes a last scrub: secret values from the environment and `.env`, Discord webhook links, any URL, paths and data-file names are replaced. Only real time-zone names (`America/New_York`) and a few fixed words (`image/jpeg`, `and/or`) keep a slash; any other `word/word` is treated as a path.
- Logs go to Botsy's own files next to `control.log_file`, one per server (`data/logs/botsy-control-read.log`, `botsy-control-act.log`; rotated at 1 MB, 3 kept), never to stdout or to Hermes. Each call logs one line: the tool name, ok, and reason codes, never an argument. Lines from the MCP SDK and other libraries keep only their logger name, level and exception type (their messages can quote a raw request); Python warnings go to the same file, and a crash prints one line with the error type, never a traceback (under the sudo bridge, stderr reaches Hermes).
- Act calls run one at a time in a server process; across processes and against the hourly check, `service/orders.py` and `service/plan.py` hold their own file locks. A call refused before it became an order is journaled as `control_refused` with the tool and its reason codes only. If the journal cannot be written, the refusal still goes back unchanged.
- Preview images (`review_preview`, `view_item_images`): only our own files inside a Botsy item's folder (a real folder directly in the output root whose manifest names that item, never a link), only from its `previews/` or `mockups/` folder, no link followed at the last step and nothing that resolves outside the folder; at most 8 MB and 40 megapixels per source (larger is "no preview", never decoded), re-encoded as JPEG without metadata. Never research files or anyone else's images (D4.1).
- `trend_search` (D4.8) is the only tool that reaches the web, and Hermes's own web and search toolsets stay off. It calls TinyFish Search (`GET https://api.search.tinyfish.ai`, free) with `TINYFISH_API_KEY` from `.env` (only in the request header; never logged or returned), and Botsy filters every result before Hermes sees it, so no competitor listing's title, description or image reaches the model. The rule is recorded in `config/platform_policies.yaml` (tinyfish, `search_result_filter`): the query must pass the trademark blocklist and health terms (hard, game-system and review terms all refused) and may not name a marketplace; a result is dropped when its host is a marketplace or listing site (any host label, or hyphen-joined part of one, etsy, amazon/amzn, goodreads, abebooks, audible, pinterest, ebay, redbubble, teepublic, zazzle, creativemarket, gumroad, society6, spreadshirt, walmart, temu, aliexpress, shein, and digital-product shops such as teacherspayteachers, creativefabrica, designbundles, payhip or myshopify; so also m.media-amazon.com; pin.it, a.co, TikTok Shop, Google Shopping, Facebook Marketplace; archive, cache and translation proxies such as web.archive.org or translate.goog; plus `trend_search.blocked_domains`), when a path segment names a marketplace (an archived `.../https:/www.etsy.com/listing/...` page), when its title, snippet or site name names a marketplace (a blog quoting an Etsy price; checked after NFKC normalization and with zero-width characters removed, so fullwidth or split spellings do not slip through), when it reads like a product listing (a price with "add to cart", "sold", "in stock", "free shipping" and similar, or two such cues), when its text or page path hits the blocklist, or when its link is not plain http(s). What is kept: site, page (domain + path only, @handles removed), title (120 characters), snippet (280), date. Every result set is labelled "trend reading only, never a brief by itself". At most `limits trend_searches_per_day` (default 30) in any 24 hours, counted before the request in `data/control/trend_search.json` (also a per-day TinyFish usage counter; no query text). A refused, rate-limited or failed search is not retried.
- OS layer (`deploy/lightsail/sudoers.d/botsy-control`, root:root 0440, sudo bridge only): the hermes user may run exactly the two wrapper commands, as botsy, with no arguments (`""`), no password (`NOPASSWD`), no terminal needed (`!requiretty`), a reset environment it cannot add to (`env_reset`, `NOSETENV`, `!setenv`) and no I/O logging. It passes `visudo -cf`. The wrappers (root-owned 0755 in `/usr/local/bin`) refuse any argument (exit 64) and any user but botsy (exit 77). They `cd /opt/botsy` and start Python with an empty environment (`env -i`) and `-E -s -B`.

**Read tools (botsy-read, Level 0).** They only read, and write nothing (except `trend_search`, which writes only its own usage counter).

| Tool | Arguments | Returns |
|---|---|---|
| `status` | none | `outbox/status.json` as fixed fields: the week's totals (ticks, exports, briefs, items, drafts, errors), the last check's counts, the pause and budget stop, the cap override, whether the limits file is in force, and Hermes's own limits and their use (changes this hour, search ideas this week, `approve_batch_max`, `level3_per_day`). No file names and no error text. |
| `spend` | none | month (UTC), spent, cap in force, money left, warning line, share used, cap reached, where the cap comes from (settings or an order), budget stop. US dollars, estimates. |
| `daily_log` | none | The newest daily log, scrubbed again: export file lists and held messages become counts, review reasons are dropped (see `review_list`), error lines keep only where the problem was ("A problem in research[coloring_pages]"). At most 8,000 characters. |
| `review_list` | `status` (pending, approved, rejected, all), `limit` (1-50) | Per item: id, niche, theme, status, needs_review, reasons as fixed codes (never the text, which can hold the flagged term), queued date, thumbnail (its place inside the item), number of previews, qa_passed, drafted, and `approvable` (a `review_approve` order could pass now). |
| `review_preview` | `item_id`, `index` (0-9) | One of our own preview images as an image, at most 768 px, re-encoded as JPEG (no metadata). |
| `plan_status` | none | For this week and next (ISO weeks on New York time): the plan in force (order id, kind, status, who, lines, packs, cost, `needs_approval` and `approval_reason`, plan number, revision, held, start now, `start_at` in UTC and `start_at_local` on New York time with its DST offset, e.g. `2026-11-02T08:00:00-05:00`, applied), the number of versions, Botsy's own draft, and the plan service's state. |
| `plan_facts` | `week` (YYYY-Www, default next week) | Money, the capacity check, the newest briefs per niche, approvals so far, and the plan service's facts file for that week (aggregated fields only). |
| `briefs_summary` | `niche`, `limit` (1-20) | The newest brief file per niche: date, age in days, stage, and per brief: id, niche, theme, pack sizes, opportunity, needs_review. Never evidence rows, keywords, sources or file names. |
| `capacity` | none | The capacity check (capacity/v1): ok, reason codes, numeric facts and thresholds. |
| `orders_recent` | `days` (1-60), `kind`, `status` (waiting_approval, accepted, applied, undone, cancelled, failed), `limit` (1-100) | Stored orders, newest first: id, kind, level, who, created, week, status, needs_approval, applied, and the checked payload fields. |
| `view_item_images` | `item_id`, `kind` (pages, mockups), `start` (0-199), `max` (1-6) | Up to 6 of our own images of one Botsy item as images for Hermes's vision: `pages` = the generator's page renders (manifest `previews`, else `previews/`), `mockups` = QA's listing photos (review entry, else `mockups/`). At most 1024 px, JPEG without metadata, about 3 MB per call (`truncated`), plus index, page, bytes, `available`, `more`. Refuses `not_found` (not a Botsy item), `no_preview`. |
| `trend_search` | `query` (lowercase, 80 characters, letters, digits, spaces, `' & -`), `recency` (week, month, year), `max` (1-10) | Filtered TinyFish results (rule above): `label`, `results` [{site, page, title, snippet, date}], `received`, `dropped` per reason (marketplace, names_marketplace, listing_like, blocklist, bad_url, duplicate), `searches_24h`, `limit`, `left`. Refuses `not_configured` (no key), `trend_search_off`, `trend_search_limit`, `query_blocked`, `query_needs_review`, `query_marketplace`, `search_failed`, `search_rate_limited`, `search_auth_failed`, `search_allowance_used`, `usage_unreadable`. |
| `events_recent` | `hours` (1-168), `kinds` (a list; "plan" = the plan service's kinds), `limit` (1-200) | Journal events as fixed fields only, each signed with `BOTSY_EVENT_KEY` (`"sig": "v1=<hex>"`) over exactly the fields returned; `signed: false` until the key is set. Never error text, titles, listing ids, file names or export rows. |

**Act tools (botsy-act).**

| Tool | Level | Arguments | What happens |
|---|---|---|---|
| `submit_order` | 1-3, by kind | `kind`, `payload`, `client_ref` | Any order/v1 kind (docs/CONTRACTS.md order/v1). Returns ok, the kind Botsy stored (a `weekly_plan` for a week that holds Botsy's own draft becomes a `plan_revision` of it, `converted_to_revision`), order id, status, `needs_approval`, stage and reason codes with plain words (`explain`), and `applied` or `plan` (with `undo` where Botsy saved one). A plan that waits for you carries `next`: ask, then `approve_order`. `review_approve`, `raise_cap` and `resume_after_budget_stop` are Level 3 (guards above). `scale_up` is refused with `no_winners_yet` until Botsy has winners. |
| `approve_order` | 3 | `order_id` | Approves exactly that stored order when it waits for you (`waiting_approval`, not replaced by a revision): a plan through the plan service (no check runs in the act server; the next hourly plan check makes the first items), anything else through `orders.approve(by="hermes")`. Refuses `not_found`, `not_waiting`, `approval_unavailable`, `plan_service_missing`, plus the plan's and orders' own reasons (`plan_expired`, `level3_daily_limit`, ...). |
| `pause` | 2 | `reason_code` | A `pause` order; always allowed. |
| `resume` | 2 or 3 | none | A `resume` order. Lifting Hermes's own pause is Level 2; lifting your pause or Botsy's own stop is Level 3. |
| `lower_cap` | 2 | `cap_usd` | A `lower_cap` order (only lower). |
| `search_ideas` | 1 | `ideas`: 1-5 of {niche, query} | A `search_ideas` order (5 a week; blocklist and health checks; marked exploratory). |
| `undo_order` | 2 | `order_id` | `orders.undo(..., by="hermes")`. |
| `fix_retry_run` | 2 | `tick_started` (optional ISO time) | A `fix_retry_run` order (service/fixes.py `retry_last_failed_run`): one retry of the last check when it failed only for a passing reason; counts toward `run_now_per_day`; the next hourly plan check runs it. |
| `fix_clear_stale_lock` | 2 | `lock` (service, plans) | A `fix_clear_stale_lock` order: empties the PID note of a lock whose process is dead, older than the longest run, held by nobody. |
| `fix_rerun_qa` | 2 | `item_id` | A `fix_rerun_qa` order: QA again for an item whose QA crashed (never one that failed real checks, a drafted or rejected item). |
| `fix_resend_daily_log` | 2 | none | A `fix_resend_daily_log` order: posts the newest daily log again (at most 36 hours old, once). |
| `fix_retry_notify` | 2 | none | A `fix_retry_notify` order: re-posts approval and fix notices Discord did not take. |

The `fix_*` tools (D4.7) map 1:1 to the order kinds of the same name and call `orders.submit` with `created_by: "hermes"`; Botsy checks each recipe's preconditions itself (and again when it runs), allows `limits fixes_per_day` (6) in 24 hours, and posts and journals each fix. Hermes never names a file, a process, a command or a time beyond the optional `tick_started`. They are also `submit_order` kinds. Because botsy-act is `trust: untrusted`, each fix call also waits for your Allow click in Discord, and fixes are declined inside scheduled Hermes jobs (`approvals.cron_mode: deny`).

Fix preconditions (service/fixes.py; checked when the order is checked and again when it runs; a refusal is journaled as `order_refused` with the precondition facts, never posted):
- `fix_retry_run`: the last check (outbox/status.json) failed (`no_failed_run`) and every error in it names only passing exception classes (network timeout, dropped connection, busy lock, API overload). If any class named in an error is not on that list (for example `ConnectionError ... BudgetExceeded`) the retry is refused (`not_retryable`). Also refused: a newer check already ran (`newer_run_ok`), paid work is paused or budget-stopped (`paid_work_stopped`), a retry is already queued (`retry_pending`), `run_now_per_day` used up (`run_now_limit`). With `tick_started`, it must still be the last check (`run_changed`).
- `fix_clear_stale_lock`: the lock file exists (`lock_missing`) and names a PID (`lock_no_pid`), that process is dead (`lock_process_alive`), the lock is older than max(`service.lock_timeout_s`, `fixes.longest_run_s` = 4 h) (`lock_too_young`), and a non-blocking flock succeeds (`lock_held`). Only the stale PID note is cleared.
- `fix_rerun_qa`: the item exists (`item_unknown`), is not drafted (`already_drafted`) or rejected (`already_rejected`), its QA crashed rather than failed (`qa_not_crashed`), and the main lock is free (`service_busy`).
- `fix_resend_daily_log`: `outbox/daily-log.txt` exists (`no_daily_log`), is at most 36 hours old (`daily_log_stale`), was not resent already (`already_resent`), Discord is set up (`discord_off`).
- `fix_retry_notify`: there are unsent approval or fix notices from the last 48 hours (`nothing_unsent`); at most 5 a run.
- Every fix: at most `limits fixes_per_day` (6) from Hermes in 24 hours (`fixes_limit`); a missing or broken limits file refuses them (`limits_unavailable`). Applied fixes are posted and journaled as `fix_applied` (and a queued retry's run as `fix_run`), which `events_recent` shows with the facts.

**Your Hold sticks (D3, user 2026-10-21).** A plan Hold records who set it (`held_by`: user, hermes or botsy; `plan_status` shows it). On a week you hold, Hermes's `plan_start_now`, `approve_order` on that plan, `plan_revision` and `weekly_plan`, and Hermes undoing your `plan_hold`, are all refused with `held_by_user`. The hourly check and the Monday auto-start never lift your Hold, and a Hold Hermes sets never replaces yours. A Hold Hermes set can be lifted by either of you.

**Which approvals count toward the daily limit (D1).** `level3_per_day` counts every approval-type order from Hermes, and your own only when it leads to posting on Etsy or KDP (`review_approve`, batches included, and `kdp_kit_approve`). Your pause, resume, cap and plan commands are not counted, but are still posted and journaled. `python -m qa review approve` is a `review_approve` order from `user` (D2): posted at once, journaled and counted.

**`kdp_kit_approve`** {"kit_id"} (Level 3) is on the menu for the KDP build and is always refused with `kdp_not_built` until it exists.

**Not on the menu:** a shell, files, fetching web pages (only the filtered `trend_search`), raw research data, the compliance lists, settings writes beyond orders, live listings, listing edits (second wave), deleting anything on Etsy, `plan auto` and `plan again` (server CLI only).

**Refusal codes of this layer:** `unknown_tool`, `not_an_object`, `unknown_argument`, `missing_argument`, `bad_argument`, `too_large`, `settings_unreadable`, `internal_error`, `not_found`, `no_preview`, `money_unknown`, `not_waiting`, `approval_unavailable`, `plan_service_missing`, and for `trend_search` `not_configured`, `trend_search_off`, `trend_search_limit`, `query_blocked`, `query_needs_review`, `query_marketplace`, `search_failed`, `search_rate_limited`, `search_auth_failed`, `search_allowance_used`, `usage_unreadable`. Orders, plans and capacity add their own (docs/CONTRACTS.md).

**Installing (root, after Hermes's user exists),** always from root's own copy of the code, never from /opt/botsy:
```
SRC=/usr/local/lib/botsy/src
install -m 0755 -o root -g root "$SRC/deploy/lightsail/botsy-control-read" "$SRC/deploy/lightsail/botsy-control-act" /usr/local/bin/
# only with the sudo bridge:
visudo -cf "$SRC/deploy/lightsail/sudoers.d/botsy-control" && \
  install -m 0440 -o root -g root "$SRC/deploy/lightsail/sudoers.d/botsy-control" /etc/sudoers.d/botsy-control
sudo -l -U hermes        # shows exactly the two commands, (botsy) NOPASSWD
```

**Tested (offline):** an in-process MCP client calls every tool against a temp Botsy data dir, over the 2026 discovery mode and the legacy initialize handshake; a real stdio round trip in a child process; that read tools never return research-input paths, export names, shop names, listing links, review terms or secrets from the environment or `.env`; that read tools write nothing; that act tools reach `orders.validate` with `created_by: hermes` (a spy) and that bad input never does; that no result mentions a code; that `approve_order` binds the plan service to the named order with `first_check=False` and refuses a plan service that cannot; that a hard-blocked item is refused; `view_item_images` (downscaling, the 6-image and byte caps, refusing non-items, links out of the item and paths outside its folders, writing nothing); `trend_search` with a fake TinyFish (the filter on tricky results: etsy.com subdomains, etsy.me, amazon.co.uk, smile/aws.amazon.com, pin.it, TikTok Shop, a configured domain, a blog quoting an Etsy price, listing-like snippets, a blocklisted result; archived and proxied Etsy pages, m.media-amazon.com, Teachers Pay Teachers, Creative Fabrica, Shopify stores, Google Shopping, Facebook Marketplace, fullwidth and zero-width spellings, a trademark in the page path; URL stripping; the query blocklist; the daily limit; no key; HTTP errors not retried; the key never returned); each `fix_*` tool reaching the real `orders.submit` with `created_by: hermes` (refused on failing preconditions, applied and posted on passing ones) and bad input never doing so; `kdp_kit_approve` refused with `kdp_not_built`; `visudo -c` and `cvtsudoers` on the sudoers rule. **Not tested yet:** a real TinyFish Search call (offline tests only), Hermes's own MCP client, the socket and sudo bridges on the Lightsail server, and whether Hermes passes tool images on to the model.

## Hermes setup

**Decided 2026-10-21: no codes.** Hermes has no code tools, there is no `#botsy-codes` channel
and no codes channel ID in its secrets; `discord.ignored_channels` is empty. Approvals come from
you directly: Hermes asks "yes?" first (`SOUL.md`), and then Hermes itself holds every
`botsy-act` call until you click Allow on its Discord approval button (`botsy-act` is
`trust: untrusted`; scheduled jobs are always declined). Botsy's own guards (blocked items, the cap
ceiling, the daily limit, the instant main-channel post with undo) stay on Botsy's side. An old
`DISCORD_CODES_CHANNEL_ID` line in `/etc/hermes/hermes.env` is reported by the lock check as
"no longer used; delete the line".

Files: `deploy/hermes/` (step by step for you in `deploy/hermes/README.md`). Tests:
`tests/deploy/test_hermes_lock.py` (offline). Nothing here is installed yet; Botsy works without
Hermes (the server CLI, for example `python -m service plan approve` and
`python -m qa review approve ID`, covers everything Hermes would do).

**The pin.** Hermes Agent **v0.21.7**, published 2026-10-12, tag commit
`71d34bad1045610252eda02ee79549436896e784`,
https://github.com/NousResearch/hermes-agent/releases/tag/v0.21.7 (MIT). Rule: the newest
release at least 7 days old with no hotfix after it, checked 2026-10-21. v0.22.3 (2026-10-21)
is too new; v0.22.2 (2026-10-15) is a 6-day-old hotfix for v0.22.1, which was "built from the
wrong commit"; v0.22.0 (2026-10-14) was followed within two days by both. v0.22.4 (tag seen later on 2026-10-21) is too new as well. The pin lives in
`deploy/hermes/hermes-pin.env`; the tag's commit was rechecked with `git ls-remote` on
2026-10-21 (twice). Install method: git tag (commit checked) into `/opt/hermes/src`, then Hermes's own
pinned `uv` and Python 3.14 from its `pm/lock.json` (sha256 checked) and `uv sync --frozen` with
only the `anthropic`, `discord` and `mcp` extras into `/opt/hermes/venv`, all root-owned. The
install stamp says `updateMechanism: external`, so `hermes update` refuses to replace the code.

**Where the settings live.** Hermes reads `~/.hermes/config.yaml` (here
`/home/hermes/.hermes/config.yaml`) and, on top of it, a "managed scope" in `/etc/hermes/`
(`config.yaml` and `.env`) that wins key by key over the user's config, its `.env` and the
environment. Both files are rendered from `deploy/hermes/config.yaml.template` by
`check_lock.py render`, root-owned, read-only to the `hermes` user, so Hermes cannot change its
own locks. Secrets are only in `/etc/hermes/hermes.env` (root:hermes 0640): a key from a
separate Anthropic workspace with a $10 monthly limit (raised from $5 on 2026-10-21), the bot
token and your user ID.

**Opened 2026-10-21 (D4 items 1-6, "too cautious; I want Hermes pulling its weight").** How each
was mapped onto v0.21.7, read in its code:
1. *Vision, our own images only.* `vision_analyze` (toolset `vision`) reads a `data:` URL, an
   http(s) URL or any file path the process can see (`tools/image_source.py`). Images from
   botsy-read's `review_preview` arrive as MCP image content and are attached natively
   (`agent.image_input_mode: native`; verified that Haiku on anthropic takes images in tool
   results). The web is closed three ways: `security.website_blocklist` with `*.*` (checked
   before any download; verified that etsy.com, etsystatic.com, discordapp.com and example.com
   are refused), `is_safe_url` for private addresses, and the required firewall. Files: the
   service already hides Botsy's folders; `/var/lib`, `/var/log`, `/srv`, `/mnt`, `/media` and
   similar are now hidden too, so what is left is the read-only system and `/home/hermes` (its
   cache of botsy-read images and your attachments). No export folder is needed.
2. *Models.* v0.21.7 has no per-task model routing in a chat, and its cronjob tool deliberately
   does not forward `model`, `provider` or `base_url` from the model ("models don't pick
   models", `tools/cronjob_tools.py`), so: chat and every job Hermes makes run on Haiku
   (`model.default`, `cron.model` with `cron.model_provider: anthropic`; `cron.provider` is the
   scheduler and stays empty). Sonnet runs only in two jobs you create on the server with
   `hermes cron create ... --model claude-sonnet-5-5`, named `botsy-weekly-plan` and
   `botsy-creative-director` (commands in `deploy/hermes/README.md` "Sonnet jobs"). `/model`
   stays refused: v0.21.7 has no enforced model allowlist behind it.
3. *Its own jobs.* `cronjob` is on for Discord chat. In jobs, Hermes's scheduler always denies
   `cronjob`, `messaging` and `clarify` and keeps `agent.disabled_toolsets` on top of a job's own
   `enabled_toolsets` (`cron/scheduler.py`); `cron.allow_agent_scheduling: false` keeps a running
   job from making jobs; `approvals.cron_mode: deny` declines every botsy-act call in a job.
   v0.21.7 has no job-count or interval setting, so `check_lock.py jobs` (hourly,
   `botsy-hermes-jobs.timer`, and inside every `check`) reads `~/.hermes/cron/jobs.json` and
   refuses: more than 10 jobs on, any job more often than hourly, a script, a script-only job, a
   monitor script or URL, a base URL, a work folder, another provider or model, toolsets beyond
   the job list, Sonnet on any other job or more often than weekly, two Sonnet jobs with one name.
   On a problem it stops Hermes. `~/.hermes/scripts` is root-owned and empty, so no script exists
   to schedule.
4. *session_search, todo, kanban.* `session_search` uses no extra model call in v0.21.7. The task
   board would normally spawn `hermes -p <profile>` worker processes for cards (effectively
   sub-agents) and run a Triage decomposer: `kanban.dispatch_in_gateway: false`,
   `dispatch_profiles: []` (claims nothing), `auto_decompose: false`, `review_dispatch: false`,
   `max_in_progress: 1`. `kanban_attach_url` can fetch a URL (it refuses private addresses);
   the firewall stops the rest.
5. *Skills.* `skills` on, `skills.write_approval: true`: every `skill_manage` change, foreground
   or background, is only staged in `~/.hermes/pending/skills/` (`tools/write_approval.py`)
   until you run `/skills approve <id>` (`/skills pending`, `/skills diff <id>`); `/skills` is
   now an allowed slash command. `guard_agent_created: true` scans each one. Background review
   and the curator stay off: the review summary leaves out staged skill writes
   (`agent/background_review.py`), and the curator archives skills without the gate, so neither
   meets "every change is posted to you". Posted: in Discord every tool call is shown in the chat
   with its argument (the skill's name for `skill_manage`); `display.platforms.discord.tool_progress:
   all` and `display.tool_progress_command: false` pin that, and the lock check and the probe
   (`gateway/display_config.py resolve_tool_progress`) refuse anything else. Where tool calls are
   not shown, skills cannot be written: scheduled jobs have no `skills` toolset (a job's own
   `skills` field still preloads a skill's text, `cron/scheduler_prompt.py`) and `/bg` stays off
   (a background run posts only its answer, `gateway/run_turn.py _run_background_task_inner`).
6. *Discord.* The `discord` toolset is the core tool (fetch messages, search members, create
   threads); pins, unpins, deletes and roles are `discord_admin` in v0.21.7, which stays off.
   `discord.server_actions: [fetch_messages, create_thread]` (member search left out: other
   people's details). Reactions are the adapter's own status reactions (`discord.reactions`).
   Slash commands: Hermes gates them per user and you are the only allowed user, so `/loop` and
   `/goal` (both run as turns in the same chat) are now yours; `/bg` stays off (see 5);
   `/goal gate add` (a shell command) needs an admin and stays refused; `/cron` is command-line
   only in v0.21.7.
7. *Job URL monitors.* The cronjob tool accepts a `monitor` URL, and v0.21.7 fetches it with a
   plain `urllib` GET (`cron/monitor.py _fetch_monitor_url`): no website blocklist and no
   private-address check, and a manual "run now" fetches at once. `check_lock.py jobs` stops
   Hermes within the hour when a job has one; until then the firewall limits the fetch to
   Anthropic, Discord and loopback, and refuses the hermes user new connections to the
   dashboard's port 9119 (its page carries a session token).

**The keys used (config schema 50).** `model.provider: anthropic`, `model.default:
claude-haiku-5-5`, no `fallback_providers`; `platform_toolsets.discord` = `[botsy-read,
botsy-act, memory, vision, cronjob, session_search, todo, kanban, skills, discord]`, `cli` =
`[botsy-read, botsy-act, memory, vision, session_search, todo, skills]`, `cron` = the same
without `skills`, `webhook` and
`api_server` = `[botsy-read, botsy-act, memory]`, `toolsets` = the Discord list;
`agent.disabled_toolsets` (applied last; single toolsets only, since a bundle such as `safe`
would also strip vision) turns off terminal, file, web, search, browser, delegation,
code_execution, discord_admin, computer_use, video, image_gen, video_gen, tts, x_search,
connections, catalog, project, desktop_ui, start_chat, setup, bot_room, context_engine,
yuanbao, feishu_doc, feishu_drive; `agent.image_input_mode: native`, `vision.max_calls_per_image:
3`, `security.website_blocklist` `{enabled: true, domains: ["*.*"]}`; `cron.model:
claude-haiku-5-5`, `cron.model_provider: anthropic`, `cron.provider: ""`,
`cron.max_parallel_jobs: 1`; `kanban.dispatch_in_gateway: false`, `dispatch_profiles: []`,
`auto_decompose: false`, `review_dispatch: false`; `discord.server_actions: [fetch_messages,
create_thread]`;
`mcp_servers` has only `botsy-read` and `botsy-act`, each with `env: {}`, `tools.resources` and
`tools.prompts` false, `sampling.enabled: false` and `elicitation.enabled: false` (Hermes turns
both on when the key is missing), `botsy-read` `trust: full` and `botsy-act` `trust: untrusted`; `memory.write_approval: true`, `skills.write_approval: true`,
`skills.guard_agent_created: true`, no memory provider; `cron.allow_agent_scheduling: false`
(a running job makes no jobs) and `approvals.cron_mode: deny` (a job never acts); `discord.allow_from: [your ID]`, `allow_all_users: false`, no
`allowed_roles`, `ignored_channels: []`, `require_mention: true`;
`gateway.unauthorized_dm_behavior: ignore`; slash commands gated with
`discord.allow_admin_from` and `group_allow_admin_from` = `["0"]` (no real account, so nobody
is admin) and `user_allowed_commands` / `group_user_allowed_commands` = `new, retry, status,
stop, compress, memory, skills, loop, goal` (plus `/help` and `/whoami`, always allowed);
`display.platforms.discord.tool_progress: all`; plugins, hooks, quick commands, lazy installs,
update checks, telemetry, the background review and the curator off; chat history compressed at
60,000 tokens (Haiku 5.5 costs 5x above 100,000). The managed `.env` also pins
`GATEWAY_ALLOW_ALL_USERS=false`, `DISCORD_ALLOWED_USERS` and an empty `DISCORD_IGNORED_CHANNELS`.

**How Hermes reaches botsy-control (the sudo question).** `NoNewPrivileges=yes` blocks sudo,
so the default bridge needs no sudo at all: systemd owns two sockets,
`/run/botsy-control/read.sock` and `act.sock` (root:hermes 0660, `Accept=yes`), and starts a
fresh `/usr/local/bin/botsy-control-read` or `-act` as the botsy user in its own sandbox for
each connection. Hermes's MCP command is `/usr/local/bin/botsy-control-connect read|act`, a
root-owned stdio-to-socket copier with no other logic. So the Hermes service keeps
`NoNewPrivileges=yes`, no capabilities, `ProtectSystem=strict`, a seccomp filter and Botsy's
folders made inaccessible. The fallback `--bridge sudo` uses exactly the approved command
`sudo -n -u botsy /usr/local/bin/botsy-control-read` (and `-act`) with the sudoers rule from
`deploy/lightsail/sudoers.d/botsy-control`, and a drop-in that relaxes only what sudo needs
(`NoNewPrivileges=no`, the setuid capabilities, no seccomp filter); it is weaker and not the
default.

**The lock check.** `check_lock.py check` (exit 1 with one `PROBLEM:` line each) runs at the end
of every install, before every Hermes start (`--quick`) and daily at 5:41 am New York time
(`botsy-hermes-lock.timer`, `--probe --stop hermes-gateway.service`). It refuses: a toolset
outside a place's list, a forbidden toolset not in `disabled_toolsets`, an MCP server other than
the two (or one started differently), sampling or elicitation on, `botsy-act` not
`trust: untrusted`, memory or skill approval off, the skill scan off, an empty or open allowlist
or a role allowlist, a running job allowed to make jobs, another model or provider for chat or
jobs, an outside scheduler, task-board workers or the decomposer, the website blocklist not
blocking every host, a Discord action beyond reading and threads, background review or curator
on, a scheduled job outside the limits above, anything in `~/.hermes/scripts`, the firewall off,
a rendered file that differs from the template, any
config, secret, unit, code file or folder the hermes user can write or replace, a foreign
systemd drop-in, a version or commit other than the pin, sudo beyond the botsy-control rule,
Botsy's folders open to hermes, and a missing firewall table. `--probe` also asks the installed
Hermes itself, as the hermes user, which toolsets and built-in tools each place gets (an exact
list per place), what its website policy does with Etsy, Discord and other addresses, which
Discord actions it allows, and what its merged settings and environment say.
`check_lock.py jobs` (hourly) runs only the job limits.

**Firewall (required since 2026-10-21; `--no-firewall` is refused).** One nftables table, `inet botsy_hermes`, for the hermes
user's traffic only: loopback (except new connections to the dashboard's 127.0.0.1:9119), HTTPS to Anthropic's published ranges (160.79.104.0/23,
2607:6bc0::/48) and to the addresses of a fixed list of Discord and Anthropic host names,
refreshed every 10 minutes; everything else is refused and logged. The botsy user is also refused new connections to the dashboard's port; otherwise other users are not affected.

**Verified** (on 2026-10-21, against the pinned v0.21.7 source and a throwaway install of it in
the scratchpad, never on this machine's system): every key name above in its code
(`hermes_cli/config_defaults.py`, `tools_config.py`, `managed_scope.py`,
`tools/mcp_tool_server_run.py`, `plugins/platforms/discord/adapter.py`,
`gateway/config_loader.py`, `cron/scheduler.py`); that the managed scope in `/etc/hermes` wins;
the default of sampling and elicitation (on when missing); the `pm/lock.json` and
`scripts/write_install_stamp.py` the installer uses; and the probe run against that install with
the rendered config: every place gets only `botsy-read`, `botsy-act` and `memory`, the only
built-in tool is `memory`, both MCP servers have sampling and elicitation off, the allowlist is
your ID, and turning on `terminal` and `web` in the managed config is reported as
`terminal, web` and the tools `process_manage, terminal, web_extract`. Rechecked 2026-10-21 after
opening D4 items 1-6, against a fresh throwaway v0.21.7 install (`uv sync --frozen`, commit
71d34bad) with the rendered template: the probe reports exactly the lists above (Discord:
`cronjob_manage`, `discord`, 15 `kanban_*` tools, `memory`, `session_search`, `skill_manage`,
`skill_view`, `skills_list`, `todo_list`, `vision_analyze`; CLI: the same without Discord, task
board and job tools; jobs: also without the skill tools), slash commands gated with nothing risky
open (`/bg` included), Discord tool progress `all`, the website policy refusing every test
address, Discord actions `fetch_messages, create_thread`, and no problems (rerun after the review
fixes below). The recorded output is the probe fixture in the tests. The test suite runs
`install_hermes.sh` in its test mode (`--root DIR --test-owner`) and checks good and broken
layouts and configs.

**Not verified yet (test on the server before relying on it):** the full install on Ubuntu 24.04
(download, `uv sync`, systemd units, socket activation, the nftables table); Hermes's Discord
gateway with a real bot, including that `require_mention` and the allowlist behave as the code
reads; Hermes's MCP client against botsy-control over the socket bridge; that
memory approval prompts and the `botsy-act` Allow/Deny button work in Discord; RAM use on the 4 GB Lightsail server (measure, add a
2 GB swapfile first); whether the Anthropic workspace limit stops calls at exactly $10; that
Hermes runs with the extra hidden paths (`/var/lib` and the rest); `/skills approve` and the
`/loop`, `/goal` commands in a real Discord chat, and that each `skill_manage` call shows in the
chat; the nftables rule for port 9119; Hermes-made jobs posting back to the
chat; and the two Sonnet jobs created with `hermes cron create --model`.

**Review fixes (2026-10-21).** Found by reading the pinned source and checked against the
throwaway install with the probe:
- *Slash commands were open.* Without an admin list, v0.21.7 (`gateway/slash_access.py`) lets
  every allowed user run every slash command: `/model` (session-scoped, no file write needed),
  `/yolo`, `/cron`, `/loop`, `/heartbeat`, `/goal`, `/bg`, `/moa`, `/tools`, `/skills`,
  `/import` and more. The template now gates them as above; the managed scope carries the gate
  into the gateway config (verified: with the gate only in `/etc/hermes/config.yaml` the user's
  `/model` and `/yolo` are refused). `check_lock.py` refuses another admin list or any command
  outside the safe set, and the probe now asks Hermes's own slash-access policy for DM, group and
  unknown chats and reports every risky command still open. This also answers "should `/model`
  be blocked": it is. The workspace limit ($10 since 2026-10-21) stays the money guard behind it.
  (2026-10-21: `/skills`, `/loop` and `/goal` were then opened for you, see above; `/bg` was
  opened too, then closed again in review because its tool calls are not shown.)
- *Profiles.* Hermes's scheduled-job ticker visits every profile under `~/.hermes/profiles/`
  whatever the config says, so `profiles/` is now root-owned and empty, like `hooks/` and
  `plugins/`, and the check refuses anything in it.
- *Fail closed.* A lock check that crashes is now a problem (exit 1), and
  `botsy-hermes-lock.service` stops Hermes in `ExecStopPost` whenever the check did not end in
  success (crash, kill, timeout), not only when it printed problems.
- *What systemd really loads.* On the server the check also reads `systemctl show
  hermes-gateway.service`: the unit must come from `/etc/systemd/system`, run as `hermes`, keep
  `NoNewPrivileges=yes` (socket bridge) and have no drop-in but the ones the installer writes
  (this also catches `service.d/` and `hermes-.service.d/` drop-ins and `/run` copies).
- The Discord invite now also ticks Embed Links and Attach Files, as layer 3 above says.
- *No codes (2026-10-21).* Removed the codes channel ID from the secrets, the template and the
  managed `.env`; `SOUL.md` now tells Hermes to send an approval only when you ask plainly and
  after a clear "yes". Rechecked with the probe against the throwaway v0.21.7 install: with
  `ignored_channels: []` every place still gets only `botsy-read`, `botsy-act` and `memory`,
  slash commands stay gated, and the managed environment reads back as written.
- *A check outside the model (2026-10-21, reviewer).* With no codes, only the model's own
  "yes?" stood between a misread message or a tool result and an approval. `botsy-act` is now
  `trust: untrusted`: v0.21.7 (`tools/mcp_tool_handlers.py` `_trust_gate_check`) then asks for
  an Allow click on every tool without `readOnlyHint=true` (all act tools; botsy-control marks
  only read tools read-only) before it even connects, routes the button to Discord where only
  allowlisted users may click, and declines outright in scheduled jobs and other sessions
  nobody can answer (fail-closed). The button names the tool, not its arguments, so Hermes's
  one-line summary still matters. `check_lock.py` and the probe (which asks Hermes's own trust
  parser) refuse any other value; rechecked against the throwaway v0.21.7 install: no problems.
- *botsy-control sandboxes (reviewer).* `botsy-control-read@` and `-act@` now use
  `ProtectSystem=strict` with write access only to `/opt/botsy` (and, for act, the status
  outbox) instead of `ProtectSystem=full`, which had left `/opt`, `/srv` and `/var` writable.
- *botsy-control-read network (integrated 2026-10-22, D4.8).* `trend_search` needs HTTPS to
  TinyFish Search, which publishes no fixed address ranges, so `botsy-control-read@` now allows
  IPv4/IPv6 sockets with `IPAddressDeny=localhost link-local multicast` plus the private ranges
  (10/8, 172.16/12, 192.168/16, 100.64/10, 0/8, fc00::/7; the cloud metadata address
  169.254.169.254 is link-local) and only `IPAddressAllow=127.0.0.53/32` (the DNS stub). It
  takes no host from Hermes: the URL is fixed in `control/trend.py`. `botsy-control-act@` is
  unchanged.

## Botsy dashboard tab

A Botsy tab in Hermes's own web dashboard (pinned v0.21.7). It shows what botsy-read shows
Hermes, and it can do two things: **Pause** and **Lower cap** (user, 2026-10-21). No approve,
resume, raise-cap, plan, review or settings buttons. Files: `deploy/hermes/dashboard/`
(`manifest.json`, `plugin_api.py`, `botsy_tab.py`, `dist/index.js`, `dist/style.css`,
`hermes-dashboard.service`); tests: `tests/deploy/test_dashboard_tab.py`.

**How Hermes v0.21.7 runs plugins** (read in its source and docs, `website/docs/user-guide/features/extending-the-dashboard.md`,
`hermes_cli/web_server_dashboard.py`; then tried on a throwaway v0.21.7 install, 2026-10-21):
- A dashboard plugin is a folder `~/.hermes/plugins/<name>/dashboard/` with `manifest.json`
  (tab path, icon, entry script, CSS, `api` file), a pre-built JavaScript file that calls
  `window.__HERMES_PLUGINS__.register(name, Component)` with React from
  `window.__HERMES_PLUGIN_SDK__`, and an optional Python file whose FastAPI `router` is mounted at
  `/api/plugins/<name>/`.
- A plugin in `~/.hermes/plugins/` (a "user" plugin) loads only when its name is in
  `plugins.enabled`: its JavaScript is not served and its Python is not imported otherwise. The
  folder has no `plugin.yaml`, so the gateway's own plugin loader finds nothing to run in it.
- The routes sit behind the dashboard's checks: the Host header must match the bound address, and
  every `/api/` request needs the session token the dashboard makes at each start and writes into
  its page (`SDK.fetchJSON` adds it).
- The Python file runs in the dashboard process. It cannot call Hermes's MCP tools, so the tab
  speaks MCP itself to the same two sockets Hermes uses, `/run/botsy-control/read.sock` and
  `act.sock`. systemd starts a fresh botsy-control as the botsy user for each connection, and
  that botsy-control checks everything again.
- Login: on 127.0.0.1 the dashboard has **no login of its own**. On any other address
  (`--host 0.0.0.0`, or a `dashboard.public_url`) it requires a password or OAuth provider and
  refuses to start without one.

**Reaching it.** `hermes-dashboard.service` runs `hermes dashboard --host 127.0.0.1 --port 9119
--no-open --skip-build` as the hermes user, in the same sandbox as the gateway, with
`IPAddressDeny=any` / `IPAddressAllow=localhost`: nobody off the server can reach it, and it
reaches nothing on the internet itself. Its own Chat page cannot answer; chat in Discord.
From your computer:

    ssh -N -L 9119:127.0.0.1:9119 <you>@<server>      then open http://127.0.0.1:9119/botsy

Never bind it elsewhere or put a reverse proxy in front of it. The tab checks this twice:
- `plugin_api.py` refuses to load when the command line has any `--host` (or an abbreviation
  argparse accepts, `--ho` / `--hos`) that is not a loopback IP or `localhost`;
  Hermes then logs "Failed to load plugin botsy API routes" (tried: `--host 0.0.0.0` with a
  password provider).
- Every route answers 403 unless the dashboard reports a loopback bind, no login gate, no
  public URL, and the request came from the server itself.

The two POST routes also need an `X-Botsy-Tab: 1` header, which a form on another website
cannot send.

**What it shows** (botsy-read only: `status`, `spend`, `plan_status`, `review_list`,
`orders_recent`, `capacity`, `daily_log`, `view_item_images`), refreshed every minute:
- Botsy status: running, paused (by whom, since when, why) or budget stop; the last check;
  practice or live; the approvals counted toward `level3_per_day` in the last 24 hours (every
  one Hermes makes, and yours only when they lead to posting on Etsy or KDP; D1) and Hermes's
  fixes in the last 24 hours;
- money: spent, cap and warn line for the month (UTC), money left;
- this week's and next week's plan: lines made / total, packs, cost, waiting for you, and held
  (by user or hermes, from the plan service's state);
- the review queue: item ids, niches, themes, reason codes, approvable. **Show** loads the item's
  first mockup (else its first page) through `view_item_images`, our own image only, as a small
  JPEG;
- recent orders and, separately, fixes (`fix_*` orders) from the last 14 days, with who asked
  and the status;
- KDP slots left and the KDP kits in the plans;
- the newest daily log, scrubbed as botsy-read scrubs it.

**The two actions** go through botsy-act, so through `service/orders.py` like any other order:
- `pause` takes a reason code only (default `user_request`);
- `lower_cap` first reads the cap in force, so a value that is not lower is refused before
  botsy-act is called. Botsy checks it again.

Both ask for a confirmation in the page and show Botsy's answer or reason codes.

**Who it is recorded as: created_by "hermes".** botsy-act records every order it takes as
`created_by "hermes"` and has no way to tell the dashboard from Hermes. The dashboard cannot
prove it is you either: on 127.0.0.1 it has no login, only the per-start token it writes into its
page for anyone who can open 127.0.0.1:9119 on the server. So:
- a Pause from the tab is a Hermes pause: you or Hermes may lift it (D3);
- pause and lower_cap are stop-only orders (`service/orders.py` STOP_ONLY): always allowed, never
  limited, and they use none of Hermes's change limit or of `level3_per_day`;
- for a pause only you can lift, use `botsy service control pause` on the server. Asking Hermes
  in Discord does not help: botsy-act records that as a Hermes pause too;
- lowering the cap is safe whoever asks.

The dashboard's Allow button for botsy-act (`trust: untrusted`) is Hermes's MCP client and is
not involved here; the page's confirmation and the SSH tunnel stand in for it.

**The rest of the dashboard** is Hermes's own admin pages (config, keys, sessions, cron, skills,
MCP). They run as the hermes user in the same sandbox, the root-owned managed config in
`/etc/hermes` still wins, and the hourly lock check still applies. Treat the tunnel like your SSH
login.

Its Chat page starts `hermes --tui` inside the same sandbox, which cannot reach Anthropic, so it
cannot answer. Anything on the server that can open 127.0.0.1:9119 gets what the tunnel gets
(the page hands out its session token). The hermes user is refused new connections to that port
by the `inet botsy_hermes` firewall table (so a scheduled job cannot fetch the page), and so is
the botsy user (`meta skuid "botsy" oifname "lo" tcp dport 9119 ct state new reject`, integrated
2026-10-22), so a Botsy job that handles downloaded files can never drive Hermes's admin pages.
Your own login (the user sshd forwards the tunnel as) and root stay allowed. The unit has no
`EnvironmentFile`: the dashboard holds no Anthropic key or Discord token, so Hermes's Keys page
has none to show.

**Install steps** (built into install_hermes.sh and check_lock.py, 2026-10-22; `--no-dashboard`
leaves it all out and is remembered):
1. copy the tab's five files (`manifest.json`, `plugin_api.py`, `botsy_tab.py`, `dist/index.js`,
   `dist/style.css`; never the unit or `__pycache__`) root-owned 755/644 to
   `/home/hermes/.hermes/plugins/botsy/dashboard/`, and the same files plus the unit to the
   reference copy in `/usr/local/lib/botsy-hermes/dashboard/`; anything else found in `plugins/`
   is moved aside to `/root/hermes-backup-*`;
2. `plugins.enabled: [botsy]`, `plugins.isolation: in_process`, `dashboard.public_url: ""` in the
   managed config (the gateway's own plugin discovery loads nothing from the tab's folder: checked
   against v0.21.7, which found only its bundled plugins);
3. the `web` extra (fastapi, uvicorn) in `hermes-pin.env`;
4. the web UI built once into `/opt/hermes/web_dist` (root-owned): Node.js 22.23.3 from
   nodejs.org, sha256 pinned in `hermes-pin.env`; a throwaway copy of the pinned checkout;
   `npm ci --workspace web --include-workspace-root=false --ignore-scripts` (integrity hashes from
   `package-lock.json`, no install scripts) and `npm run build --workspace web`, both as the user
   `nobody` (`setpriv`), never as root or hermes. Tried on 2026-10-22 in the scratchpad with the
   pinned checkout: about 30 seconds, it writes `index.html` and the assets;
5. `hermes-dashboard.service` plus the firewall drop-in, started only after the lock check passes.

The lock check accepts exactly that plugin (each file's sha256 equal to the reference copy,
root-owned, no `plugin.yaml`, `plugin.json`, `__init__.py`, `__pycache__` or link) or an empty
`plugins/`; refuses `plugins.enabled` other than `[]` or `[botsy]`, `auto_apply`, another
`plugins.isolation` and a non-empty `dashboard.public_url` in any scope; and, on the server, asks
systemd what it loaded for `hermes-dashboard.service`: `/etc/systemd/system`, user hermes,
`NoNewPrivileges=yes`, `IPAddressDeny=any`, `IPAddressAllow=localhost`, no drop-in but the
firewall one, exactly one `--host` (abbreviations `--ho`/`--hos` included) and it is 127.0.0.1,
port 9119, and a root-owned `/opt/hermes/web_dist/index.html`.
