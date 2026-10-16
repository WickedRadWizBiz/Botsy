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
