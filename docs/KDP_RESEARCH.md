# KDP research: learn what kinds of books sell, then make our own

> **Status: proposed 2026-10-16, not built yet.** Decisions still open are listed in [PROPOSAL_2026-10-16.md](PROPOSAL_2026-10-16.md); build order is there too.

In short: you download book lists from BookBeam by hand. Botsy counts patterns across many books, never just one, and turns them into a plan for an original book.

### What "copy the kind, not the book" means
- Botsy looks at what many top-selling books in a niche have in common:
  - size, page count and price;
  - the features sellers name in their titles and subtitles, such as "undated", "habit tracker", "large print" or "answer key".
- A pattern only counts when at least 10 books from at least 3 different authors share it. This applies to every single phrase, including the "more common among top sellers" ones, so wording from one author's subtitle can never get through.
- Botsy then makes that kind of book from its own page designs. It never sees or copies another book's inside pages, cover or wording.
- One limit: Botsy only sees the words in titles and subtitles. Features hidden inside books are invisible to it.

### How it works, step by step
1. **Search list.** Each week Botsy gives you 10-20 Amazon searches, for example "undated planner", "fishing log book", "password book large print" and "rpg campaign journal". Brand names and game-system names are left out.
2. **You export.** You run each search on Amazon with the BookBeam extension and export to CSV. The Windows helper uploads the files once it learns to recognize BookBeam files. That is a small change; today it knows only EverBee and eRank.
3. **Botsy reads the files.** Plain code, no AI.
   - Keeps paperback rows only and removes duplicates.
   - Turns authors into anonymous counts in memory.
   - Titles, ASINs (Amazon's book IDs), author names and links never go into briefs, history, logs, Discord or any AI prompt.
   - The raw files themselves stay on the server, outside git, for up to 90 days and are then deleted.
4. **Patterns for each niche:**
   - demand: sales of the top 10 books (BookBeam estimates);
   - competition: reviews on the top 10, how many have fewer than 500 reviews, and whether a few authors dominate;
   - newcomers: the share of top sellers less than a year old;
   - the usual price, page count and trim size.
5. **Features.**
   - "Table stakes": phrases almost every book has.
   - "Lift": phrases much more common among the best sellers.
   - A lift phrase must appear in 10 or more books from 3 or more authors overall, and among top sellers from at least 3 authors.
6. **Score and season.**
   - A 0-1 opportunity score combines demand, low competition, how well new books do, features we can build, and profit per copy.
   - A season calendar gives each book a "must be live by" date.
7. **Book plan.** Botsy picks the trim, page count, sections and price from the patterns and our own page designs. It checks that the book is clearly different from our earlier books.
8. **Build, check, queue.**
   - Botsy makes the interior and cover and runs its checks.
   - Title check: at kit time, plain code compares our title and subtitle with the titles in raw files still inside the 90-day window, blocks close matches, and stores only pass or fail.
   - Botsy prepares a copy-paste kit for KDP and fills KDP's 2 weekly paperback slots. You upload and submit in KDP.

### What you do
- **Now:** send one real BookBeam export of each kind you plan to use:
  - an Amazon search page;
  - a category best-seller page;
  - Keyword Research or Niche Finder, if those can export.

  BookBeam doesn't publish its column names, so the parser waits on these samples. The free CE Lite tier can export to CSV; whether it includes sales estimates is unknown until we see a file.
- **Each week:** run the searches and export them (my guess: 20-40 minutes).
- **Each book:** upload the files, paste the fields in KDP, and order one printed proof for each new kind of book.
- **Each month:** download your KDP sales reports.

### What Botsy does
Everything else: reading, counting, scoring, planning, making and checking the files, filling the kit, and tracking slots and dates. It never opens BookBeam, Amazon or KDP.

### What a KDP brief looks like (made-up numbers)
"fishing log book:
- 34 books from 21 authors.
- Top 10: median 140 reviews; 6 of 10 have fewer than 500.
- 40% of top sellers are less than a year old.
- Most common price $7.99; 100-120 pages; 6 x 9 (12 titles, 5 authors).
- Table stakes: 'log book' (95%).
- More common among top sellers: 'tide and moon' (1.8x; 14 books, 6 authors), 'large print' (1.5x; 11 books, 5 authors).
- Opportunity 0.64. Season: none."

### Book-plan examples (math rechecked against KDP's pages, 2026-10-16)
- 6 x 9 black ink: $2.30 flat for 24-110 pages, otherwise $1.00 + $0.012 a page.
- 8.5 x 11: $2.84 flat, otherwise $1.00 + $0.017 a page.
- Both tables include 110 pages ($2.30 flat or $2.32 by formula), so Botsy uses the higher number at that point.
- Royalty: 60% of the price at $9.99 and up, 50% below, minus print cost.

**1) Undated 90-day planner (6 x 9, 120 pages, low-content)**
- 4 front pages, 3 monthly overviews and 3 habit trackers, 90 daily pages (top 3, time blocks, brain-dump box), 13 weekly reset pages, 7 notes pages.
- Print $2.44. You earn $3.55 at $9.99, or $2.05 at $8.99.
- Title idea: "Undated 90-Day Planner: Daily Top 3, Time Blocks, Brain Dump and Habit Tracker". It describes the layout and makes no health claim. I dropped "Focus Planner" because it is close to the brand "Full Focus Planner"; check before use.
- "Notes under 10%" is our own rule, not KDP's.

**2) Fishing log book (6 x 9, 110 pages, low-content, new "field log" page type)**
- Entries: date, place, weather, water temperature, bait or lure, catch table, notes. Two entries per page.
- 4 front pages, a 2-page spots index, 100 log pages, 4 notes pages.
- Print $2.30 (or $2.32 if KDP applies the formula). You earn $3.69 at $9.99, or $1.70 at $7.99. Botsy shows both prices; you choose.

**3) Tabletop RPG campaign journal (8.5 x 11, 120 pages, low-content, no game system named)**
- 30 two-page session notes, 16 NPC pages, 10 quest-log pages, 10 hex-map and 10 grid-map pages, 4 front pages, 10 notes pages.
- Print $3.04. You earn $2.95 at $9.99, or $4.75 at $12.99.

### Staying within the rules
**BookBeam's terms** (Last Updated May 15, 2026):
- use is "solely for your internal personal or business use";
- "any automated use of the Service, including the use of bots, scripts, scrapers, crawlers" is barred;
- the Service and its outputs "remain the exclusive property of BookBeam";
- no using it to "build, train, benchmark, improve, support, or assist in the development of any competing product".

So Botsy:
- reads only the files you download;
- keeps private counts only;
- deletes raw files after 90 days;
- never puts KDP briefs on GitHub. The repo is public (checked 2026-10-16), so they get their own .gitignore line;
- gets a BookBeam entry in config/platform_policies.yaml before anything is built.

**Amazon:**
- Botsy never touches Amazon or KDP.
- But the BookBeam extension runs on Amazon pages in your browser, and Amazon's Conditions of Use exclude "data mining, robots, or similar data gathering and extraction tools".
- That risk falls on your Amazon account, which is probably also your KDP login. Many KDP sellers use such tools, and I found no source on enforcement either way.
- It is your decision. Whatever you choose is recorded in the policy file.

**No book is copied:**
- every pattern and every phrase needs 10 or more books from 3 or more authors;
- no titles, ASINs, author names or covers go into briefs or any AI model;
- our titles are checked against the raw files in plain code only.

**KDP's content rules:**
- KDP rejects content "not significantly different" from another book and content "excessively reused" across books. That page is written for eBooks, so treat it as a likely print rule. Each of our books differs from our others in sections and design.
- No repeated generic keywords in titles; title plus subtitle under 200 characters.
- Planners and logs must tick the low-content box or be rejected. Low-content books get no series, no free ISBN and no Expanded Distribution.

**Other rules:**
- The blocklist checks every brief and title. Planner brands become review words after a USPTO check.
- No medical claims; health log books wait for your decision.
- KDP's AI questions are answered conservatively.
- No Look Inside for low-content books without an ISBN, so Botsy makes 3-5 A+ Content images of our own interior, with no prices, "new" or holiday words.

### Timing
- Limits: 2 paperback creations and 2 paperback submissions a week, reset Sunday 00:00 UTC. Hardcover would be a separate format.
- Go-live: up to 72 hours on Amazon.com, or up to 6 days for an account with no published book. KDP currently says low-content reviews "may take 3-4 weeks" because they are "taking longer than usual right now".
- Print books can't be pre-ordered; a release can be scheduled up to 90 days ahead.
- We will likely miss this holiday season (NRF: Nov 1 to Dec 31; at least 2 in 5 shoppers start before November) and most of the New Year window.
- Realistic targets: late-January "fresh start" titles; Valentine's, Easter and Mother's Day gift journals; back-to-school 2027, live by mid-June (NRF: 62% had started by early July 2026).
- Planner peak months are claimed only by vendor blogs (unverified). Botsy will measure them.

### Weak spots
- BookBeam's export columns are unknown until your sample arrives, including whether the free tier exports sales estimates.
- The market is crowded. Narrow, well-made trackers are the target, not generic notebooks.
- Pen names mean "3 authors" may really be fewer sellers.
- Feedback is slow: no KDP API, monthly reports.
- Sales are BookBeam estimates; KDP says sales rank "is not an accurate way to track your book's activity". Use them only to compare niches.
- No credible public source gives sales numbers for planners or log books.

### Effort
- Small: policy entry, .gitignore line, Windows-helper sniff, search list, score, season calendar.
- Medium: parser, patterns, the phrase floor, field-log page type, queue.
- Large: the data-driven book-plan builder. It needs embedded fonts and the KDP specs first.
