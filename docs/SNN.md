# SNN role, revised: Scout for adjacent ideas, plus Watcher

> **Status: design, written 2026-10-17 after the user's answers of 2026-10-16; not built yet.** Open choices and build order: [PROPOSAL_2026-10-16.md](PROPOSAL_2026-10-16.md) section "Round 2".

Status: proposed 2026-10-16, not built, skeptic-checked. It replaces the "Roles" and "Testing and promotion" parts of docs/SNN.md. It answers your point 6: "find adjacent things to create for stores just based on trending sales ... be doing something while also gathering history and watching".

## What changes
- **Before:** an optional shadow experiment (snnTorch) that started only after 8 weeks of records.
- **Now:** it runs from the first week the server runs research. It has three jobs:
  1. **Scout (weekly):** suggests adjacent things to make:
     - the same theme in another niche (axolotl coloring pages, then axolotl stickers);
     - themes next to our own best sellers (for Hermes's "more of this");
     - learned links: themes that tend to rise in the same week as, or 1, 2 or 4 weeks after, themes rising now.
  2. **Watcher (hourly):** watches Botsy's own events and spending, and flags anything unusual. Advisory only; it never acts.
  3. **History keeper:**
     - starts a daily record of our own shop's numbers. Today `own_shop_stats.csv` is overwritten every day, so this history is being lost right now;
     - keeps a compact weekly record of rises. The market history itself is already kept in the history snapshots, which cleanup never deletes.
- **Cost:**
  - $0 in API calls, no new library, no bigger server;
  - but each search line it adds is a manual export for you, and uses some of a free plan's lookups (eRank's free plan allows 5 keyword lookups a day). So its lines replace weaker lines instead of adding new ones.
- **Limits:**
  - it never makes a product, a brief or a decision;
  - its ideas only become "exploratory" lines in the weekly search list and suggestions in Hermes's weekly plan;
  - every idea passes the blocklist, and becomes a product only through the normal floor (10+ listings from 3+ shops; the refined stage's stricter bar once that is on).

## Honest expectations
- **What "trending sales" can mean here:**
  - With the safe inputs (section 2), the SNN never sees market sales numbers.
  - On EverBee's free Hobby plan, exports have no sales anyway: Hobby product analytics shows "views, reviews, favorites"; sales and revenue start with Growth at $24.99 a month.
  - So, on the market side, a "rise" means a theme newly showed up, or newly passed the floor, in your weekly exports.
  - Real sales trends come only from our own shop, once listings are live and selling.
- **First weeks:** most useful output comes from fixed rules, not learning:
  - the same theme in another niche;
  - word neighbours ("mushroom" in "cozy mushrooms" and "mushroom fairy");
  - our winners' neighbours.
- **Learning needs repeats:** a theme has to rise several times before a link means anything.
- **Your searches shape the data** (section 3). Fixing that leaves less to learn from, so learning will be slow.
- **Prototype (made-up data, scratch folder only, not Botsy code):**
  - **When a leading theme rose about 5-10 times a year:** both the SNN and the simple counter found the planted "A, then B" links, at about 3-10 times better than random picks.
  - **Head to head:** the counter was as good or better in 11 of my 12 reruns.
  - **When the leader rose about once a year:** neither beat random. My rerun gave 0.03 for the SNN, 0.03 for the counter and 0.02 for random.
  - **Not tested:** the made-up data had no search-list effects.
- **So:** I expect the simple counter to match or beat the SNN in year one. The SNN runs anyway, because you want it included. The counter runs beside it, and a scoreboard shows the truth every week.

## 1. What data Botsy has over time

### Market side: history snapshots (research/history.py, schema history/v1)
- **Where:** `data/history/<YYYY-MM-DD>/<niche>.json`, one per niche per research run. Research runs daily at 06:17 UTC and after uploads.
  - Gitignored, never sent to a model.
  - Cleanup never deletes them; only raw exports go, after 180 days.
- **Per theme (at most 200 per niche per snapshot):**
  - `name` (our own n-gram, already through the brief guard and the near-miss brand-word check);
  - `listing_rows`, `keyword_rows`, `shop_count`;
  - `fresh_rows` (rows from exports received in that ISO week);
  - `demand` (a 0-1 rank);
  - `qualified` (met that run's stage bar);
  - `needs_review`.
- **Per snapshot:** `stage`, `thresholds`, and `sources` (tool, shape and row count only; own-shop and trend files are left out).
- **`timeline()`:** a week counts as "seen" when fresh_rows is 3 or more. It also gives weeks qualified, first and last seen, last fresh, and the listing-row trend.
- **Evidence window:** 35 days (research/window.py).
- **Refined stage** (research/stages.py): 15 listings, 3 shops, seen in 3 ISO weeks, demand not falling.
- **Briefs** (committed to git) already hold demand, share, median price and shop counts per theme, and keep the top 25 qualified themes.

### The weekly search list (service/searchlist.py)
- **Lines:**
  - seed searches, 2 per niche, the same every week;
  - near misses, which need 2+ shops or keyword rows;
  - rechecks.
- **Limit:** at most 6 lines per niche.
- **No machine copy today.** It is written only as a text file for you. The SNN needs a machine copy (section 3).

### Our own shop (Etsy Open API v3, our own listings only)
- **Daily today:** `publisher own-stats` writes title, views, favorites (`num_favorers`), price, quantity, tags, state and age_days.
  - It writes to `data/research_inputs/own_shop_stats.csv`.
  - It overwrites the file, so no history is kept.
- **`views`:** "tabulated once per day and only for active listings" (Etsy spec).
- **Sales:**
  - They need the `transactions_r` permission. Botsy asks today only for listings_r, listings_w, listings_d and shops_r.
  - `getShopReceiptTransactionsByShop` returns listing_id, quantity, price and created_timestamp per sale. It also returns buyer_user_id and receipt details, which we drop.
  - It takes only limit and offset (no date filter, no sort).

### Botsy's own work
- **Event journal** `data/events.jsonl`: it rotates at 5 MB × 5 files, so old lines disappear.
  - There is no "tick" event.
  - Tick records (start time, results) are kept in `outbox/status.json` for 35 days.
- **Spending ledger** `data/spend/ledger.jsonl`: append-only, kept.
- **Also:**
  - review approvals and rejections;
  - theme cooldown (in pipeline/run.py and generators/packs, which the packs build is editing right now).

### Off-Etsy
- Google Trends files you download by hand (`trends-*.csv`).

## 2. Compliance: what the SNN may read

### Each tool's terms, read 2026-10-16 (not legal advice)
- **EverBee** ("Last updated: May 13, 2026"):
  - section 4.1: "the software is for your limited use only";
  - nothing about exports, derived data, models or competing products.
  - **Verdict:** not forbidden as written, not clearly allowed.
- **eRank** ("Updated August 13, 2026"):
  - section 3: use "only for your internal business purposes";
  - section 5: no screen scraping, and no use "to compete with the Services";
  - section 26: its "Intellectual Property" includes "information, data, insights", and "You agree to not copy, modify, create derivative works of, publicly display, publicly perform, share, sell, re-sell, license, or re-publish any of our Intellectual Property."
  - **Verdict:** unclear. A model trained on eRank's numbers reads like a "derivative work". But, honestly, so do our briefs. The strict-reading question is the same for research itself.
- **BookBeam** ("Last Updated: May 15, 2026"):
  - "You may not access or use the Service to build, train, benchmark, improve, support, or assist" any competing product, dataset or model;
  - its outputs stay BookBeam's property.
  - **Verdict:** treat as forbidden. Nothing from BookBeam, not even yes/no facts.
- **Etsy (our own shop):**
  - CLAUDE.md allows "our own stats".
  - The API Terms page returned 403 to my fetch too. I did not try to get around the block.
  - The policy file records "no automated systems ... analysing ... Etsy data without Etsy's written authorization".
  - A web-search summary of an archived copy (etsy.com/legal/api-archived, not opened) mentions display limits: listing content not shown more than 6 hours older than the site. Whether that touches a private history of our own stats is unverified (decision 7).
- **Google Trends:**
  - "You can use any information from Google Trends, subject to the Google Terms of Service";
  - "If you reuse Trends data, attribute the information to Google with a citation".
  - Files downloaded by hand only; Botsy never fetches Trends.

### Where the line is, and why
- Keeping export numbers out of the SNN is a **cautious extra choice for a learning model**. It is not a line the terms draw: briefs already contain numbers.
- It costs little. The SNN mainly needs "did it rise", not "by how much".

### What the SNN uses, exactly
**(a) Our own data, full numbers:**
- per our listing per day: views, favorites, state;
- sales (date, listing, quantity, price) once transactions_r is granted;
- our events, spending, drafts and review decisions.

**(b) Botsy's own yes/no facts per (niche, theme) per ISO week**, from EverBee and eRank snapshots only:
- `seen` (fresh_rows of 3 or more);
- `qualified`;
- `lead` (2+ shops or keyword rows: the search list's own rule);
- first time seen;
- streak;
- `asked`: our own search list asked for it (section 3).
- Plus theme names (already checked by research) and word overlap between them.

**(c) Google Trends weekly interest:** only from files with the Google Trends timeline header. Other trend files could come from a licensed tool, so they are ignored.

**Never in the SNN:**
- export demand, sales, revenue, favorites, reviews, search volume, competition, prices, listing_rows, keyword_rows or shop_count as numbers;
- anything from BookBeam or KDP snapshots;
- competitor titles, shop names, URLs or images.

**How this is enforced:**
- `snn/encode.py` is the only door. It reads history through `research.history.load()` and keeps only name, date and the yes/no facts.
- A test changes every number in a snapshot but keeps each yes/no fact the same. The SNN's saved state must come out byte-for-byte identical.
- A snapshot is used only if every tool in its `sources` is on the allowed list (`[everbee, erank]`).
  - One unknown file in the 35-day window blinds that niche for up to 5 weeks.
  - The daily log says when that happens.
- **"Numbers" mode:** not built. Only if EverBee and eRank say yes in writing (decision 2).

## 3. Three traps in this data, and the fixes

### Trap 1: your searches shape what Botsy sees
- A theme is "seen" when your exports that week held 3+ rows of it. Your exports follow the search list.
  - If the SNN puts "axolotl stickers" on Sunday's list and you search it, the theme is "seen" next week. The SNN would "learn" that its own guess came true.
  - Themes from one search show up together, so "rose together" would mostly learn "came from the same search".
- **Fix:**
  - the search list saves a machine copy of each week's lines: `data/search_lists/<date>.json`, with phrase, kind and theme;
  - a theme that shares a main word with a non-seed line from the last 2 lists, in the same niche, is **asked** that week;
  - asked appearances can act as a cause, but never as a teacher, and never count as a hit in the offline test;
  - two themes asked by the same line never strengthen a "rose together" link.
- **The clean signal:** the seed searches, the same 10 phrases every week. What newly shows up in those broad searches is the closest thing to "what is selling now" that we may use.

### Trap 2: flicker
- A theme seen most weeks drops out when you export less, then comes back.
- On made-up data, guessing such returns made both methods look about 3-4 times better, without learning anything real.
- **Fix:** only **rises** teach and score:
  - first time ever seen;
  - newly qualified (not qualified in the 4 weeks before);
  - seen again after 4+ weeks away.
- "Still seen" is input only.

### Trap 3: empty weeks
- If you skip a week of exports, every theme goes silent, and every learned lead would "fail".
- **Fix:** a niche-week with no fresh export rows, or fewer than 2 export files, is not learned.

## 4. Scout: how the spiking network finds adjacent ideas

### Neurons
- **M, market theme neurons: 1,024 slots.** One per (niche, theme), shared across the 5 Etsy niches.
  - Only themes that were a lead or qualified at least once get a slot.
  - A slot is freed after 26 silent weeks (least active first).
- **O, own-shop neurons: 128.** One per theme of our own listings (title and tags mapped to theme n-grams).
- **T, trend neurons: 64.** One per Google Trends term (optional).
- **P, readout neurons: 1,024.** Leaky integrate-and-fire neurons, one per M slot: membrane time constant 8 steps; threshold starts at 0.5 and adapts.
- **Watcher:** 16 neurons (section 6).
- **Total:** about 2,250 neurons.
- **Synapses:** 4 delay banks (0, 1, 2 and 4 weeks) from 1,216 inputs to 1,024 P neurons, about 5 million weights.
  - 20 MB as float32, plus 10 MB of evidence counters;
  - 2-5 MB compressed on disk.

### Time and encoding
- **One week = 40 steps.** Input spikes fall in steps 0-9; the rest lets traces fade.
- **Which weeks:** only closed ISO weeks are learned, each once. The open week is used only for the readout.
- **M input, latency code (earlier = stronger sign):**
  - newly qualified: step 0
  - qualified again: step 2
  - seen 3+ weeks in a row: step 4
  - seen 2 weeks in a row: step 6
  - first seen, or back after a gap: step 8
  - otherwise silent
- **Teacher spikes** (what should have been predicted): unasked rises only. Newly qualified at step 0, back after 4+ weeks at step 6, first ever at step 8.
- **O neurons, count code, our own numbers:**
  - count = min(10, 2 × sales this week + favorites gained ÷ 2 + 1 if views growth is in our top quarter);
  - before transactions_r: favorites and views only.
- **T neurons:** this week against the mean of the 4 weeks before.
  - +50%: step 0; +25%: step 3; +10%: step 6; otherwise silent.

### Learning rule (STDP with delay banks, soft bounds 0-1)
- **Delay banks:**
  - bank d = 1, 2 or 4 learns "i was active, then j rose d weeks later";
  - bank 0 learns "rose in the same week".
- **Strengthen** when a pre spike from week w−d meets j's teacher spike in week w:
  - w += 0.05 · e^(−|Δt|/8) · (1 − w);
  - bank 0 uses a time constant of 5, and i ≠ j.
- **Weaken** only when a lead did not come true (pre spike arrived, no teacher spike): w −= 0.01 · w.
- **Why not the classic "post before pre weakens":**
  - inside one week, spike time means "how strong", not "which came first"; the delay bank already says which came first;
  - the classic rule would weaken "a faint sign of A, then B took off", the pattern we most want;
  - on made-up data the change made no measurable difference, so this is a logic fix, not an accuracy claim.
- **Forgetting:** weights × 0.995 per week (half-life about 138 weeks).
- **Evidence counter:** +1 per strengthening pair. A learned link is used only with 2 or more, and its reason shows the counts, so thin evidence is visible ("2 of 2 times").
- **Prior wiring at slot creation, kept apart from learned weights:**
  - word overlap of theme names (Jaccard, without niche nouns and stop words) × 0.15, in banks 0 and 1;
  - same theme in another niche: 0.3, in bank 0;
  - priors fade as 1 / (1 + weeks learned ÷ 8).
- **Our own winners:** O neurons feed bank 0 like M neurons, so "while our X was selling, Y rose" is learned too.

### Reading out ideas (Sunday)
1. **"Next":**
   - feed the delayed spikes that land in the coming week (this week via bank 1, last week via bank 2, 3 weeks ago via bank 4);
   - run 40 LIF steps: V ← V·e^(−1/8) + input;
   - an earlier first spike means more confidence; ties go by peak V ÷ threshold;
   - themes qualified this week are skipped;
   - **adaptive threshold:** +0.05 after a false alarm, −0.02 after a miss.
2. **"Rose together":** bank-0 input from this week's active themes.
3. **"Winner neighbours":**
   - for each of our winners, the top 3 themes by bank-0 weight plus prior, in any Etsy niche;
   - a winner is in our top quarter by sales over 28 days, with at least 2 sales;
   - before sales exist: top quarter by favorites and views growth, with at least 5 new favorites, labelled "early signal".
   - KDP as a target is for later, once KDP research runs.
- **Reasons:** each idea names its top 2 links, with counts from Botsy's own weekly facts. Example: "rose 1 week after 'sea animals' (coloring pages) 3 of the 4 times; we sell 'axolotl garden'".

### Filters before anything leaves the SNN
- **Names:**
  - ideas only use theme names research already checked (blocklist, brief guard, near-miss brand-word check), plus Botsy's niche nouns;
  - the SNN cannot re-run the shop-name and title checks itself, because it never reads exports. So it adds no new words.
- **Lead rule:** only themes that were qualified or a lead (2+ shops or keyword rows) at least once.
- **Blocklist recheck:** blocked ideas are dropped; flagged ones get `needs_review` and "(check: trademark?)", and Hermes can't push them.
- **Game systems:** game-system names are dropped (`research.briefs.names_a_system`).
- **Medical:**
  - uses one shared list: `research.medical_terms` plus the medical log-book list from the medical-vs-lifestyle design (answer 7);
  - medical log books are dropped;
  - lifestyle journals pass (meditation, stress, exercise);
  - ADHD planners pass (layout only, never claims);
  - unclear ones (food diary, mood tracker, sleep log) are held for review.
- **URLs:** no URLs.
- **Path:** each idea gets one of three labels:
  - `brief_ready`: the theme is in the niche's newest valid brief and not under cooldown;
  - `near_miss`: it is in history but below the floor;
  - `new`.
- **Caps:** at most 10 ideas a week.

### Cold start
- **First run:** replays every existing history snapshot. It starts empty if there are none.
- **Week 1:**
  - cross-niche and word-neighbour ideas (labelled "prior");
  - the watcher starts logging;
  - our own shop history starts.
- **Weeks 2-6:** first learned links, once they have 2 matching rises each.
- **Week 5 on:** ideas start being scored (4-week horizon).
- **About week 21:** first promotion check (16 scored weeks).
- **Own-sales links:** only once our listings sell.

## 5. The simple counter (baseline), side by side
- **Same record:** it reads the same weekly spike record, with the same asked-masking and the same rise-only targets.
- **Lagged counts:** C_d[i, j] = weeks where i was active at w−d and j rose at w, for d = 1, 2, 4.
  - score_j = the highest, over themes i active at the right time, of (C_d + 0.05) ÷ (weeks i was active + 0.5).
- **"Rose together":** co-rise lift.
- **Same rest:** the same priors, filters and caps.
- **Size:** about 80 lines of NumPy.
- **What the SNN could add:** it uses how strong each sign was (timing), and adds up several weak cues instead of taking the single best one.

## 6. Watcher (reflex)
- **When:** runs right after the hourly record job from build step 3, in under 1 second.
- **What it reads:**
  - that job's hourly record (so it keeps no second copy);
  - the newest tick record in `outbox/status.json`, as the heartbeat.
- **12 input channels**, one step = 1 hour, count code up to 10:
  - error, qa_failed, held_for_review, item_generated, draft_created;
  - export_received, export_rejected;
  - spend (1 spike per $0.10);
  - spend_warning/budget_reached, token_expiring, notification_held;
  - heartbeat.
- **12 surprise neurons:** each threshold learns that hour-of-week's normal level, moving 5% a week. That takes about 14 weeks to get halfway, so early alerts are noisy. There is a floor, so one error always fires.
- **1 stall neuron:** fires at 30 hours with no tick. The daily tick runs every 24 hours.
- **3 pattern neurons with fixed wiring:**
  - "spending without output";
  - "error burst";
  - "exports bouncing".
- **Output:** `data/snn/watch.jsonl`, read by the daily log. No Discord until promoted. It never acts.
- **Honest note:** this is a spiking version of "count above its usual level". It overlaps the rule-based watchdog and is scored against it.

## 7. Scoring
- **Log:** every idea, from the SNN and from the counter, goes to `data/snn/suggestions.jsonl`: week, niche, theme, source, kind, path, reason, score. Outcomes are added later.
- **Main fair test (offline, needs no searches):** each week, of each method's top 10 "next" themes, how many had an **unasked** rise the following week.
- **Searched idea hit:** qualified within 4 weeks.
  - In plain words: enough sellers make this to pass the floor.
  - It does not prove demand, because a direct search almost always finds 10 listings from 3 shops when a product exists.
  - Base rate: the normal near-miss lines.
- **Strong outcome (slow):** we made it, and it sold within 8 weeks.
- **Also tracked:**
  - lead time;
  - agreement between the two top-10 lists. Near 100% means the SNN is only copying the counter.

## 8. Library
**Recommended:** a small NumPy LIF + STDP engine inside Botsy (`snn/lif.py`, about 450 lines with the scout, counter and watcher). NumPy is already in requirements.txt.

| Option | License | Size and memory | STDP | Verdict |
|---|---|---|---|---|
| NumPy in Botsy | ours (NumPy: BSD-3) | 0 MB extra. Prototype rerun: 52 weeks in 1.0 s, 83 MB peak, 25 MB state | we write the exact rule | **use this** |
| snnTorch 1.0.0 (2026-06-29) | MIT | 0.13 MB, but needs PyTorch. torch 2.14.1: default Linux wheel 554.6 MB (pulls CUDA 13 packages), CPU-only wheel 196 MB. Designer measured about 900 MB installed and 300 MB to run (not rechecked) | yes: stdp, mstdp and mstdpet helpers (docs/SNN.md was wrong to say it has no reward rule) | too heavy for no gain |
| Brian2 2.10.1 (2025-12-05) | CeCILL-2.1 (copyleft; obligations only on distribution) | needs Python 3.12+, Cython, sympy | yes | optional one-time cross-check, not on the server |
| Norse 1.1.0 (2024-03-18) | LGPL-3.0 | needs torch and torchvision | yes | stale and heavy |
| Lava 0.10.0 (2024-08-08) | BSD-3 + LGPL-2.1+ | needs Python below 3.11 and NumPy below 2; the server and CI use 3.12 | yes | can't install |
| SpikingJelly | PyPI says "Other/Proprietary"; the newest stable release is 0.0.0.0.14 (2023) | needs torch | yes | license unsettled |

**Risk:** bugs in our own engine. The fix is hand-worked unit tests, plus a one-time comparison with Brian2 on your PC or in a scratch folder.

## 9. Schedule and outputs
- **Weekly scout:** `botsy-snn.service`, a one-shot job.
  - `botsy-weekly.service` gets `Wants=` and `After=botsy-snn.service`, so the scout finishes before the Saturday 09:07 (New York time) search list.
  - If the scout fails, the list still runs.
  - It runs after Sunday's 06:17 research.
  - The server uses UTC (deploy README). If you change its timezone, these times move.
- **Hermes weekly plan:** Sunday 8 pm US Eastern (answer 5; "EST" read as Eastern time with daylight saving).
  - That is Monday 00:00 UTC until 1 November, then 01:00 UTC (first on Monday 2 November).
  - It is set as Hermes's own scheduled job. If a systemd timer is used instead, `Sun *-*-* 20:00:00 America/New_York` works on systemd 255 (checked).
  - The scout's ideas are always ready hours before the plan.
- **Watcher:** right after the hourly record job from step 3.
- **Files (all gitignored, server only):**
  - `data/search_lists/<date>.json`: the machine copy of each week's list (written by the search list, not the SNN);
  - `data/snn/spikes/<YYYY>.jsonl`: one line per closed week, yes/no facts and rises only;
  - `data/snn/slots.json`;
  - `data/snn/state/<YYYY-Www>.npz`: keep the last 8 plus one per month;
  - `data/snn/ideas-<date>.json` (schema snn-ideas/v1);
  - `data/snn/suggestions.jsonl` and `data/snn/scoreboard.json`;
  - `data/snn/watch.jsonl`;
  - `data/snn/last_run.json`;
  - `data/own_history/<date>.json`: our own daily listing stats, written by publisher own-stats; research never reads this folder.
- **Weekly search list:** "Ideas to explore (exploratory)", at most 3 lines (2 from the SNN, 1 from the counter).
  - Each replaces the weakest stalled near miss in its niche, so your number of searches doesn't grow. If a niche has no stalled near miss, the idea waits.
  - The search list rechecks the blocklist and the lead rule.
  - Hermes's research co-pilot keeps its own approved "up to 5" (OVERSEER.md).
  - Example line: "axolotl digital stickers (idea: rose after 'sea animals')".
- **Daily log line, example:** "SNN scout: week 43 learned (212 themes, 9 unasked rises, 31 new links). Ideas: 3 searches, 2 brief-ready neighbours. Offline test: SNN 2 of 10, counter 3 of 10. Searched ideas that qualified: SNN 3 of 11, counter 2 of 6, near misses 9 of 52. Watcher: 0 alerts in 24 h."
- **Events:** the SNN job writes only `data/snn/`. The weekly service then emits `snn_ideas_written` or `snn_error` from `last_run.json`, to the journal and the daily log, not Discord.

## 10. How Hermes uses it
- **Read tools:** `botsy-control` gets two:
  - `snn_ideas`: the newest ideas plus the scoreboard, names only, already checked;
  - `snn_watch`: the last 7 days of alerts.
- **In the Sunday plan:**
  - **Brief-ready ideas:** Hermes may plan them; Botsy rechecks the brief, blocklist and cooldown.
  - **Near-miss and new ideas:** Hermes can only point to the search lines already added.
  - **"More of this" (answer 4):** for a winner, Hermes may plan more pack sizes, the winner theme in another niche with a valid brief, and brief-ready winner neighbours.
- **When there is room:** that rule belongs to the Hermes scale-up design, and the SNN follows it.
  - **Fallback if none is set:** spending left after the plan is at least 15% of the cap, and the review queue holds 10 items or fewer.
  - **To add later:** a weekly item limit and a no-open-watchdog-alarm check. Neither exists yet: settings has only `max_items_per_run: 10`, and the watchdog is build step 3.
- **Marking:** Hermes marks which idea ids it used, so outcomes can be traced back.
- **Hermes may not:**
  - make briefs from ideas;
  - change SNN settings or search slots;
  - use an idea that failed a check.
- **Cost:** a few hundred tokens a week. My estimate is under $0.05 a month on Haiku 5.5.

## 11. Server resources
- **Measured** in a container (4 processors, Python 3.13, NumPy 2.5), not on Lightsail. The 4 GB Lightsail plan has 2 processors.
  - Full size (1,024 slots, 4 banks): 52 weeks replayed in about 1 s;
  - 83 MB peak memory;
  - 25 MB of state, about 2 MB compressed.
- **Estimated for the real job** (with research imports and pandas): 150-250 MB peak, under 5 s a week. The watcher: under 1 s and 100 MB.
- **Limits:**
  - systemd `MemoryMax=512M`, `Nice=19`, `CPUWeight=10`, `IOSchedulingClass=idle`;
  - the job stops itself above 400 MB, so it ends cleanly before systemd would kill it.
- **Disk:** under 150 MB a year.
- **Plan size:** it fits the 4 GB plan and is not a reason for the 8 GB one. Build step 7 still applies: run the 52-week replay on the real server once before switching it on.

## 12. Safety rules
- **Inputs:** only those in section 2.
- **Sandbox** (systemd, running as botsy; a separate Linux user isn't needed for about 450 lines of our own code):
  - `PrivateNetwork=yes`;
  - `.env` and the Etsy token file are unreachable. The SNN code never imports `publisher` or `service.notify`, because both call `load_env()`, which would fail on the hidden `.env`. A test checks this;
  - read-only: code, `data/history`, `data/briefs`, `data/search_lists`, `data/own_history`, `outbox/status.json`, the cooldown state, and `data/research_inputs` (only Google Trends-shaped `trends-*` files are opened there);
  - writable: `data/snn` only.
- **Advisory only:** ideas pass the name, lead, blocklist, game-system, medical and cooldown checks, then enter only the search list and Hermes's plan. Products still need a brief through the research floor.
- **No commits:** nothing goes to git.

## 13. Promotion: when its ideas get more weight
- **Start:** 2 search slots, labelled "experimental" for Hermes.
- **Promote** (3 slots, SNN ideas listed first in Hermes's plan, SNN-first ranking of winner neighbours) only when all of these hold:
  1. at least 16 scored weeks (about week 21) and at least 30 scored, searched SNN ideas (about 15 weeks at 2 a week);
  2. in the offline test, the SNN beats the counter in at least 12 of the last 16 weeks. Weeks with a tie don't count, and there is no decision with fewer than 10 untied weeks;
  3. its searched ideas qualify at least as often as the counter's, and at least 1.5 times the near-miss rate;
  4. no crash and no memory stop in the last 8 weeks.
- **Never:** promotion never lets an idea skip the floor, the blocklist or the cooldown, or create a brief.
- **No win after 26 scored weeks (about week 31):**
  - it keeps running (your wish) at 1 slot, and the counter gets 2;
  - the daily log says so plainly;
  - you can switch it off in settings.
- **Watcher promotion:** after at least 8 weeks, its alerts may post to Discord as "advisory", and later wake Hermes, if:
  - it caught at least 2 incidents you confirmed, earlier than the watchdog or that the watchdog missed;
  - it raised at most 1 false alarm a week.
  - Even then it never acts.

## 14. Tests
- **Engine:**
  - LIF fading, threshold and reset against hand-worked numbers;
  - STDP: strengthening, symmetric window, weakening on a failed lead, 0-1 bounds;
  - delay alignment, forgetting, evidence counts, adaptive thresholds.
- **Determinism:** the same inputs give the same state, and each closed week is learned once.
- **Planted patterns:** with 30 synthetic weeks and A → B one week later, B is in the top 5 for both the SNN and the counter, and random picks are not.
- **Traps:**
  - an asked theme never strengthens a link and never scores;
  - two themes from one asked line never link;
  - frequent themes that skip a week are not rises;
  - an empty week causes no weakening.
- **Compliance:**
  - changing every number while keeping the yes/no facts leaves the state byte-identical;
  - BookBeam, KDP and unknown-tool snapshots are skipped;
  - one-shop themes never become ideas;
  - no forbidden fields or URLs in ideas files;
  - the `snn` package imports neither `publisher` nor `service.notify`.
- **Filters:**
  - blocked ideas dropped, flagged ones held for review;
  - game-system and medical names dropped; lifestyle journals pass; unclear ones held;
  - cooldown sets the path.
- **Search list:**
  - ideas replace only stalled near misses;
  - the cap and labels hold;
  - the machine copy is written;
  - a missing or old ideas file means no section and no failure.
- **Watcher:**
  - the stall neuron fires after 30 hours without a heartbeat;
  - an error burst fires;
  - normal levels go quiet.
- **Isolation and size:**
  - an SNN failure never stops the search list, and it is reported as `snn_error`;
  - the memory guard stops the job at 400 MB;
  - a 52-week replay takes under 5 s and 300 MB.

## 15. Effort and place in the build order
- **Effort:** about 5-7 build days (estimate).
  - engine: 0.5 day;
  - encoder, traps and compliance tests: 1-1.5 days;
  - scout: 1 day;
  - counter and scoring: 0.5-1 day;
  - filters, search list, digest and Hermes hook: 1 day;
  - watcher: 0.5 day;
  - own history and sales: 0.5 day;
  - docs and settings: 0.5 day.
- **Order:**
  - **Build step 2 (minutes of work each):** start the daily own-stats copy and the search list's machine copy. Their history can't be recovered later, and the masking needs the list copies from week 1.
  - **Right after step 3:** build the scout and watcher. They need step 3's hourly record. This replaces step 10.
  - **After the packs build lands:** make the changes to `service/searchlist.py`, `research/history.py` and `config/settings.yaml`, because the packs build is editing those files right now.
  - **Before building:** this is a heavy phase, so per CLAUDE.md, check `/usage` first.
- **First live output:** the first Sunday after it is built. It joins Hermes's plan once Hermes is up (steps 8-9).

## 16. Not verified
- Etsy's current API Terms: 403, not read. In particular, any limit on keeping our own stats.
- Whether `getShopReceiptTransactionsByShop` returns newest first. The spec has only limit, offset and legacy.
- How many unasked rises real seed-search exports give each week. Learning may stay very thin.
- EverBee free-plan export limits: the pricing page lists none.
- Memory and time on the real Lightsail server.
- Whether EverBee and eRank would allow "numbers" mode.
- How many themes per week real exports produce. The 200-per-snapshot cap is known.
- Brian2 and snnTorch install sizes: the designer's measurements, not rechecked.
