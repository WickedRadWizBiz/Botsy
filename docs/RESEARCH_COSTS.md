# Low-cost research data: KDP first, then Etsy

> **Status: design, written 2026-10-17 after the user's answers of 2026-10-16; not built yet.** Open choices and build order: [PROPOSAL_2026-10-16.md](PROPOSAL_2026-10-16.md) section "Round 2".

Written 2026-10-16. It answers your point 3: "minimize any upfront costs".
- Prices and terms were checked on the web on 2026-10-16.
- Minutes and other guesses are marked as estimates.
- Anything I could not confirm is marked unverified.

## 1. Short answer

- **Upfront cost: $0 on both platforms.**
- **Etsy, now ($0):**
  - Etsy's own Marketplace Insights (in Shop Manager).
  - You type the numbers into a Botsy template.
  - Botsy can already read that template.
- **Etsy, first paid step:** eRank Basic, $5.99 a month, monthly billing.
  - Buy it when Botsy is live on the server and making real drafts.
  - It replaces most of the typing. It also gives about 100 lookups a day with an Export button.
- **KDP, now: nothing to collect yet.**
  - Botsy has no KDP reader yet. It comes with KDP build step 3.
  - When that starts, use the free manual template (you copy a few fields by hand).
- **BookBeam's free tier (CE Lite) needs a new yes from you first.**
  - Its terms say the extension may fetch Amazon pages in the background, over your internet connection.
  - CLAUDE.md allows Amazon only through official APIs or your own clicks. So this needs your OK and a written exception.
  - The same clause covers paid BookBeam plans.
- **Paid KDP tools cost more than first thought.**
  - BookBeam's cheapest plan is now $47 a month (or $348 a year).
  - Pay only once KDP royalties cover it (section 8).
- **Amazon's official API does not fit** (section 3.3).

## 2. Ranked options

### 2.1 KDP (Amazon books)

| # | Option | Cost | What it shows | Export a file by hand? | Limits | Runs on Amazon pages? | Fit |
|---|---|---|---|---|---|---|---|
| 1 | Botsy manual template | $0 | What you type: title, author, price, ratings count, BSR, pages, date, size | Yes, our own CSV | Your time: about 30-40 min per niche (estimate) | No extension. Only your own browsing | **Recommended start**, once the KDP reader exists |
| 2 | BookBeam CE Lite | $0, no card | "Average Best Sellers Rank, Reviews, Price, Competition". Sales, pages, dates and size are not stated | Yes, CSV ("one click"). Columns not published | None published | Yes. It may also fetch Amazon pages in the background | Only if you say yes to background fetching (3.1) |
| 3 | Our own KDP reports | $0 | Our orders and royalties | Yes. File type unverified, probably Excel | None | No, our own account | Once books are live |
| 4 | BookBeam Basic (paid) | $47/mo, $141 a quarter, or $348/yr | Paid extension: "sales, royalties and other critical metrics". 250 history views | CSV per plan not stated | 7-day money-back; no free trial | Yes, same background clause | Only after royalties cover it |
| 5 | Titans Pro | $99 one-time (standalone extension), or Hobbyist $20/mo / $180/yr | On screen: BSR, type, page count, size, publish date | "Export to Excel" is listed for keywords. Book rows: unverified | 300 searches/mo (sales page) | Yes | Possible one-time option. Ask the vendor first (3.4) |
| 6 | DS Amazon Quick View | Free. Extended: one-time fee, amount not shown | Free: BSR on search pages. Extended: CSV export, pages, publisher | Extended only | Not stated | Yes. "No data are transmitted to anywhere, ever" | Optional typing aid. How it gets BSR is unverified |
| 7 | Google Trends CSV | $0 | Interest 0-100 over time | Yes | A few terms per chart | No | Small effect. Deferred (3.5) |
| 8 | Amazon Creators API | $0 | Sales rank, price | API | 10 affiliate sales in 30 days | API | **Does not fit** (3.3) |

Also checked, not recommended: Keepa (one product at a time; sales rank needs Pro, about €29/mo per a secondary source), Publisher Rocket ($199 one-time, eBook focus, export not mentioned), Book Bolt (about $9.99/mo per secondary sources; its site blocked us), KDSpy (about $79, affiliate pages only), AMZScout's free Sales Estimator (book coverage unverified), KDP Rank Fuel (vendor blog only), and BookBeam's free Book Sales Calculator (BSR to sales, 5 free estimates; good for a hand spot-check).

### 2.2 Etsy

| # | Option | Cost | What it shows | Export a file by hand? | Limits | Runs on etsy.com pages? | Fit |
|---|---|---|---|---|---|---|---|
| 1 | Etsy Marketplace Insights + Botsy template | $0 | Real Etsy "Searches" and "Search Results" for a keyword and for similar keywords (last 30 days) | No export. You type into our CSV | 15 searches a week (Etsy Plus: unlimited, about $10/mo per a third party) | Etsy's own Shop Manager | **Recommended start**. Works with today's reader |
| 2 | eRank Basic | $5.99/mo or $65.99/yr | Keyword Ideas (searches, clicks, CTR, Etsy Competition). Top Listings: top 100 with shop name, age, views, lifetime est. sales and revenue, price, hearts, tags | Yes, Export (Basic and up). File type and columns unverified | 100 lookups a day | No, a website | **First paid step** |
| 3 | Our own shop stats | $0 | Our views, favorites, sales | Already built (Etsy API, own shop) | None | It is the API | Already built |
| 4 | eRank Free | $0 | 5 lookups a day; one row of Keyword Ideas | No | 5 a day | No | Only for extra typing |
| 5 | EverBee Growth | About $39/mo on monthly billing, or $299/yr ($24.99/mo) | Sales, revenue, conversion rate, shop names | Yes, "up to 3,000 listings at once" | Unlimited searches | Yes, extension. Also connects your Etsy account | Later, if at all |
| 6 | EverBee Hobby | $0 | Views, reviews, favorites | No (exports are paid) | 10 keywords | Yes | Not usable for files |
| 7 | Google Trends | $0 | Interest over time | CSV | A few terms per chart | No | Deferred |

## 3. Details, KDP

### 3.1 BookBeam free tier (CE Lite)

**What it is:**
- "The free tier of the BookBeam Chrome Extension". No credit card.
- It works on Amazon search, category, book and author pages.
- It shows "Average Best Sellers Rank, Reviews, Price, Competition". You choose the columns.
- "Export your research in one click to CSV". The column names are not published.
- Not stated: sales estimates, page count, publication date, trim size, limits. A secondary review (Aug 2025) says sales and search volume are paid only. Unverified.

**Terms (Last Updated May 15, 2026):**
- Use is "solely for your internal personal or business use".
- No "bots, scripts, scrapers, crawlers, browser automation tools".
- No building a "competing product, service, dataset".
- Outputs stay BookBeam's property.
- Nothing about exporting or how long you may keep files.
- Botsy's use fits: you export by hand, and Botsy keeps private counts.

**The background-fetch clause ("Data Synchronization"):**
- The extension, "including the free version", "may periodically retrieve publicly available Amazon marketplace data, such as search result pages, book detail pages, category listings ... using your device's standard network connection".
- It "does not access your personal Amazon account data, passwords, payment information, or private account credentials".
- It "may be required as a condition for accessing certain free features".
- You can opt out only by uninstalling.
- It covers paid plans as well.

**Why this needs a new decision:**
- Your earlier yes covered the extension running on Amazon pages you open.
- This clause is different. It is automated fetching from your computer, on BookBeam's schedule.
- CLAUDE.md says Amazon is used "only through official APIs or the user's own clicks", and asks you before any rule changes.
- **If you say yes:**
  - Botsy adds a named exception to CLAUDE.md, like the D&D one.
  - Use the extension only in a separate Chrome profile that is never logged in to Amazon or KDP.
  - Close that profile when you are done.
  - Note: a separate profile does not change your internet address. Amazon sees fetches from the same connection you use for KDP. What Amazon does about that is unverified.
- **If you say no:** use the manual template only.

**About "people use it, so it must be allowed":**
- About 30,000 people have installed the extension (Chrome Web Store).
- Amazon's Conditions of Use still exclude "data mining, robots, or similar data gathering and extraction tools".
- I found no source on Amazon acting against users of such extensions, either way.

### 3.2 Fully manual (Botsy template)

**What Amazon's terms say:**
- Browsing Amazon by hand is ordinary use.
- Amazon's licence (Conditions of Use dated Aug. 14, 2026, read from a third-party copy because amazon.com returned HTTP 503 to us) is for "personal and non-commercial use".
- It does not include "any collection and use of any product listings, descriptions, or prices".

**My assessment:**
- Copying a few fields by hand for business research is not clearly inside that licence.
- The same clause applies to every tool in this document.
- The manual way avoids the "robots / extraction tools" clause and any background fetching.
- It matches CLAUDE.md: your own clicks.
- I found no source on enforcement.
- This is your risk choice (section 12, decision 2).

**Fields, books per search and time:** see section 5.

### 3.3 Amazon's official API: does not fit

- **PA-API 5.0 is retired.**
  - Amazon: it "has been deprecated and is being replaced by the Creators API".
  - Old calls "receive an HTTP 403 Forbidden response".
- **To get access:** Associates enrollment plus "at least 10 qualifying sales within the past 30 days". We have no affiliate site.
- **Licence:** Associates Program Policies, updated April 14, 2026, item (e). It bars using the Creators API, PA API or Data Feeds "for the purpose of aggregating, analyzing, extracting, or repurposing any Product Advertising Content or in connection with any software or other application intended for use by persons or entities that offer products on an Amazon Site", without written approval.
- **Caching:** item (h) allows keeping non-image content for up to 24 hours, then it must be refreshed.
- **Verdict:**
  - The licence is for advertising Amazon products.
  - Research for a KDP seller is exactly what it bars.
  - It does not fit.

### 3.4 Other KDP tools (short)

- **Titans Pro:**
  - On screen it shows "BSR, type, page count, size, publish date".
  - "One-click export to Excel" is listed under its keyword features. Whether book rows export is unverified.
  - Its terms (March 2026) allow "personal and commercial use".
  - An older Use Policy (19 April 2022) says "personal, non-commercial purposes".
  - Ask the vendor in writing before paying.
- **DS Amazon Quick View:** Extended adds CSV export, page count and publisher, for a one-time fee (amount not shown). It could cut typing. How it fetches BSR is unverified.
- **Keepa, Publisher Rocket, Book Bolt, KDSpy, AMZScout, KDP Rank Fuel:** none is needed to start (see 2.1).

### 3.5 Google Trends (deferred)

- **Manual CSV downloads are free.**
  - Google: "You can use any information from Google Trends, subject to the Google Terms of Service". Cite it if you reuse it.
  - robots.txt disallows `/explore?`, so Botsy never fetches it.
- **What Botsy does with it today:**
  - It takes the average of a chart's last 4 points as a "right now" boost.
  - The boost is small: trend_boost 0.1 closes at most 10% of the gap.
  - It does not look ahead to a coming season.
  - A file counts for 35 days.
  - The Windows helper does not upload Trends files today.
- **Advice:** skip it until the rest runs. Seasonal look-ahead would be new work.
- **Trends API:** alpha only. Google "can't open the API for everyone yet". Not needed.

### 3.6 Our own KDP reports

- Free: Dashboard, Orders, KENP, Prior Months' Royalties. All can be downloaded.
- The file type is unverified, probably Excel. Botsy reads CSV only, so use "Save As CSV" for now.

## 4. Details, Etsy

**Etsy Marketplace Insights (Shop Manager > Stats > Marketplace Insights):**
- Per a third-party summary (eRank, Oct 2025): it shows "Searches" and "Search Results" for a keyword, and for similar keywords too.
- "Standard Etsy sellers get 15 keyword searches per week."
- Search engine snippets of Etsy's help page say results stay available for 7 days and cover the last 30 days.
- No export is described.
- These are real Etsy numbers, not estimates.
- Etsy's own help page returned HTTP 403 to us. Its exact wording, and whether your shop has the tool yet, are unverified.

**eRank:**
- **Free:** 5 lookups a day, one row of Keyword Ideas, no Export.
- **Basic, $5.99 a month:**
  - 100 lookups a day.
  - Keyword Ideas, with Etsy Competition (the number of listings).
  - Export, but the help page states it only for Keyword Ideas and keyword lists. Top Listings export is unverified.
- **Top Listings:**
  - The "Listing" column shows the title "as well as the name of the shop".
  - Est. Sales and Est. Revenue are "over the life of each listing", not monthly. Botsy must convert them (section 10).
- **Terms (Aug 13, 2026):**
  - No "screen scraping".
  - "You agree to not copy, modify, create derivative works of ... share ... or re-publish any of our Intellectual Property", which includes "information, data, insights".
  - Keep everything private. Ask eRank in writing (decision 6).

**EverBee:**
- Hobby is free, but sales, revenue and exports are paid.
- **Growth:** $299 a year ($24.99 a month), or about $39 a month on monthly billing ($468 a year shown crossed out; check at checkout).
- **Acceptable Use (May 13, 2026):** no scraping; no reselling exports "as a competing product or dataset". Private use is fine.
- **Extension:** it runs on etsy.com, and you connect your Etsy account. Etsy's API Terms bar extensions that analyse Etsy data without Etsy's approval. You have not decided on this risk.
- **MCP integration (Growth and up):** we cannot use it, because it would pass competitor listings to an AI model (CLAUDE.md).

## 5. The manual KDP template (when the KDP build starts)

**File:** `kdp-manual-template.csv`, blank, opens in Excel.

**First line:** `# botsy-template: kdp_manual v1`. Then one row per book:

```
search_term,page_type,position,title,author,price,ratings_count,bsr,print_length,publication_date,dimensions
```

**From the search or best-seller page (every book):**
- `title`: paste the title and subtitle as shown.
- `author`: the "by" name.
- `price`.
- `ratings_count`: the number next to the stars.
- `position` on the page.
- Paperbacks only. Skip "Sponsored" results.

**From the book page (top 10 per page only):**
- `bsr`: "#12,345 in Books".
- `print_length`: pages.
- `publication_date`.
- `dimensions`: for example "6 x 0.25 x 9 inches". Botsy reads this as 6 x 9.
- These are the labels Amazon book pages usually show. I did not re-check them.

**Books per niche:**
- Every pattern needs 10 books from 3 authors.
- Record the top 20 paperbacks on 2 pages:
  - an Amazon search;
  - the category's Best Sellers or New Releases page.
- That gives about 30-40 books after duplicates (estimate).
- Size and page-count patterns come only from the top-10 rows, so about 15-20 books. They may miss the floor. Then Botsy uses its default sizes and says so.
- Botsy says when a niche is short ("8 of 10 books, 2 of 3 authors").

**Search terms:**
- They come from the weekly list.
- They are checked against the trademark blocklist.
- Medical log books (blood sugar, blood pressure, medication, symptom trackers) are left out.
- Lifestyle logs (meditation, stress, exercise) are fine.

**Minutes (estimates):**
- About 25 seconds per book on a list page: about 17 minutes for 2 pages.
- About 50 seconds per book page, for 15-20 pages: about 13-17 minutes.
- **About 30-40 minutes per niche.** The first three KDP books would take about 1.5-2 hours, once.
- CE Lite (if approved) might cut this to about 15 minutes per niche. That depends on its columns.

**Saving the file:**
- Open the template, fill it in.
- Then File > Save As > "CSV UTF-8" > your Downloads folder, with a new name (for example `kdp-coloring-oct.csv`).
- The helper only watches Downloads.

**Privacy:**
- Titles and author names are used in memory only: for patterns, the 3-author count and the title check.
- Never in briefs, history, logs, Discord, Hermes or any AI prompt.
- Raw KDP files are deleted after 90 days.

## 6. The Etsy Marketplace Insights template (usable today)

**File:** `etsy-insights-template.csv`, blank. First line: `# botsy-template: etsy_insights v1`. Then:

```
keyword,searches,competition,checked_on
```

**How to fill it in:**
- One row for your search term, and one for each similar keyword Etsy shows.
- `searches` = Etsy's "Searches" number.
- `competition` = Etsy's "Search Results" number. Do not name the column "Search Results" yet (section 10, item 1).
- Save As CSV into Downloads, as in section 5.

**It works today:**
- Tested read-only: the current reader maps keyword, search_volume and competition from these headers.
- The Windows helper recognizes them.

**How Botsy counts these rows (current rule):**
- A theme needs 10 different keyword rows, each with at least 10 competing listings.
- Keyword rows have no shop names. The 3-shop floor applies only to listing rows.
- This is how Botsy already treats eRank keyword files.
- In the refined stage (after 20 drafts) a theme needs 15 keyword rows, seen in 3 different weeks. Re-checking terms uses up the 15 searches a week. So start eRank Basic before then.

**Time (estimate):**
- About 2-3 minutes per term.
- 15 terms is about 30-45 minutes a week.

## 7. What each option gives Botsy, and what is lost

**Etsy reader (`research/columns.py`):**
- **Insights template:** keyword, search volume, competition. Nothing at listing level.
- **eRank Keyword Ideas export:** keyword, searches, clicks, CTR, competition.
- **eRank Top Listings export:**
  - title, price, lifetime est. sales and revenue, hearts, views, age, tags.
  - Shop names only if the export carries them (unverified).
  - No reviews or category.
- **EverBee Growth:** probably title, shop, price, monthly sales, revenue, reviews, favorites, age. Unverified until we see a file.

**Lost on the $0 Etsy path:**
- No listing rows. So:
  - no price bands;
  - no "pack size top sellers use" (only 5 and 20 pages);
  - no competitor tag patterns;
  - no sales numbers.
- Briefs are labelled "keyword-only" and use your default prices. You still need to set those in settings.
- Demand = Etsy's real search counts. Botsy's demand score already uses whatever metrics exist.

**KDP reader (to be built):**
- **Needs:** title, author, price, ratings count, BSR or a sales estimate, pages, date, trim size, and an ASIN or URL for removing duplicates.
- **Manual template:** all of these except a sales estimate and an ASIN. Duplicates are found by title + author.
- **CE Lite:** BSR, reviews, price and competition are confirmed. The rest is unknown.

**Lost on the $0 KDP path:**
- No sales estimates. Demand comes from BSR rank and ratings count, as an estimate for comparing niches only.
- KDP itself says sales rank "is not an accurate way to track your book's activity".

## 8. Recommended path

### Etsy

- **Now ($0):**
  - Marketplace Insights template, 15 terms a week.
  - Plus our own shop stats.
- **When Botsy is live on the server and drafting:**
  - eRank Basic, $5.99, monthly billing.
  - Export Keyword Ideas and Top Listings for each term on the list.
  - The first export settles the open questions (shop names, file type).
- **Before 20 drafts (refined stage):** keep eRank Basic. The free path gets too thin there.
- **EverBee:** only if all of these hold:
  - eRank's export has no shop names;
  - listing patterns matter;
  - Etsy profit covers about $39 a month;
  - you accept its etsy.com and account-connection risk.

### KDP

- **Now:** nothing to collect.
- **When KDP build step 3 starts:**
  - Fill one manual template for the first book's niche. That file also tests the reader.
  - Then about 30-40 minutes per new niche.
- **CE Lite:** only after your yes on the background clause, and the CLAUDE.md exception.
- **Paying for a KDP tool:**
  - Only after KDP royalties were above the tool's monthly price for 2 months in a row.
  - Monthly billing first. A yearly plan only after 3 useful paid months.
  - Cheapest options today: BookBeam Basic $47/mo, or Titans Pro $99 one-time (after the vendor answers the "non-commercial" question).

### Budget

- **Upfront: $0.**
- **Likely next:** $5.99 a month (eRank Basic).
- **A KDP tool:** only when royalties pay for it.
- Tool subscriptions are separate from Botsy's $25 API cap. core/spend.py counts API calls only.

## 9. Proposed config/platform_policies.yaml

**What changes:**
- New entries: `bookbeam` and `google_trends`.
- Updated entries: `amazon`, `everbee` and `erank`.
- One new source on `etsy`.
- One new optional key: `user_decision`. No code reads this file, so the new key is safe.

**What stays:** pinterest and fiverr, unchanged.

**Checked:** it parses as YAML. A copy is at `/tmp/claude-0/-home-user-Botsy/16642d68-6549-5012-b01b-89a5f5499bc7/scratchpad/platform_policies_proposed_v2.yaml`.

```yaml
# Each platform's stance on scraping / automated access (CLAUDE.md compliance rule).
# Botsy accesses a site automatically only if it is listed here with scraping: allowed.
# allowed       = terms do not forbid it AND robots.txt allows the paths we would use
# forbidden     = terms (or robots.txt) forbid it: official API or the user's own clicks only
# unknown       = not checked yet: no automated access
# user_decision = (optional) a risk the user chose to accept, in their words, with the date.
#                 It never lets Botsy itself access the site; it records what the user runs.
# Re-check entries older than 180 days before relying on them.

platforms:
  etsy:
    scraping: forbidden
    official_api: Etsy Open API v3 (own shop only; never other sellers' listings or shops)
    sources:
      - url: https://www.etsy.com/legal/api
        note: "API Terms of Use: no automated systems or browser extensions accessing, analysing or scraping Etsy data without Etsy's written authorization."
      - url: https://www.etsy.com/legal/terms-of-use
        note: "General Terms of Use also restrict automated access (secondary summary; re-read the live page)."
      - url: https://help.erank.com/blog/etsy-marketplace-insights-tool/
        note: "Third-party summary (22 Oct 2025) of Etsy's own Marketplace Insights (Shop Manager > Stats): 'Searches' and 'Search Results' for a keyword and for similar keywords; 'Standard Etsy sellers get 15 keyword searches per week'; no export described. Etsy's own help page (help.etsy.com/hc/en-us/articles/35122361353239) returned HTTP 403 on 2026-10-16, so Etsy's wording is unverified. The user reads the numbers by hand and types them into Botsy's etsy-insights template; Botsy never opens Etsy."
    checked: "2026-10-16"
  amazon:   # includes KDP
    scraping: forbidden
    official_api: none for research or KDP publishing (the user uploads by hand). The Creators API (successor of PA-API 5.0) does not fit; see sources.
    sources:
      - url: https://www.amazon.com/gp/help/customer/display.html?nodeId=508088
        note: "Live Conditions of Use returned HTTP 503 to our fetch on 2026-10-16; text below is from a third-party copy."
      - url: https://conductatlas.com/platform/amazon/amazon-conditions-of-use/license-and-access-restrictions/
        note: "Third-party copy of the Conditions of Use dated Aug. 14, 2026 (captured Sept. 14, 2026). Licence 'to access and make personal and non-commercial use'; it does not include 'any resale or commercial use', 'any collection and use of any product listings, descriptions, or prices', 'any derivative use', or 'any use of data mining, robots, or similar data gathering and extraction tools'. So hand-copying listings or prices for business research is not clearly inside the licence either."
      - url: https://affiliate-program.amazon.com/help/operating/policies
        note: "Associates Program Policies, updated April 14, 2026. IP License (e): no use of Creators API, PA API or Data Feeds 'for the purpose of aggregating, analyzing, extracting, or repurposing any Product Advertising Content or in connection with any software or other application intended for use by persons or entities that offer products on an Amazon Site' without express prior written approval. (h): non-image content may be cached for up to 24 hours, then must be refreshed."
      - url: https://affiliate-program.amazon.com/creatorsapi/docs/
        note: "Creators API prerequisites: Associates enrollment and 'at least 10 qualifying sales within the past 30 days'."
      - url: https://affiliate-program.amazon.com/creatorsapi/docs/en-us/paapiv5-deprecation
        note: "PA-API 5 'has been deprecated and is being replaced by the Creators API'; calls 'receive an HTTP 403 Forbidden response'."
    user_decision: "2026-10-16: the user accepts the Amazon-account risk of running the BookBeam extension on Amazon pages ('Yeah I guess, people use bookbeam so it must be allowed'). See bookbeam.user_decision for what is still open."
    checked: "2026-10-16"
  bookbeam:
    scraping: forbidden
    official_api: none. Botsy reads only CSV files the user exports by hand from the BookBeam extension. Botsy never opens BookBeam or Amazon.
    sources:
      - url: https://bookbeam.io/terms-of-service/
        note: "Last Updated May 15, 2026. Licence 'solely for your internal personal or business use'. 'Any automated use of the Service, including the use of bots, scripts, scrapers, crawlers, browser automation tools, or similar tools, is strictly forbidden.' No use 'to build, train, benchmark, improve, support, or assist in the development of any competing product, service, dataset, software, model, tool, feature, or business'. The Service and its outputs (other than User Content) stay BookBeam's property. Nothing on exporting or how long exported files may be kept."
      - url: https://bookbeam.io/terms-of-service/
        note: "DATA SYNCHRONIZATION: 'including the free version of the Service, the BookBeam Chrome Extension may periodically retrieve publicly available Amazon marketplace data, such as search result pages, book detail pages, category listings ... using your device's standard network connection'. It 'does not access your personal Amazon account data, passwords, payment information, or private account credentials'. It 'may be required as a condition for accessing certain free features or versions of the Service'. Opt out only by uninstalling. This applies to paid plans too (the clause covers the extension, 'including' the free version)."
      - url: https://bookbeam.io/ce-lite/
        note: "CE Lite is 'The free tier of the BookBeam Chrome Extension'; 'No Credit Card Required'. Shows 'Average Best Sellers Rank, Reviews, Price, Competition' on search, category, book and author pages; 'Export your research in one click to CSV'. No column list and no limits published."
      - url: https://bookbeam.io/pricing/
        note: "Basic $47/mo, $141 a quarter or $348 a year; Publisher $69/mo or $576 a year; Publisher Pro $119/mo or $984 a year. 'We do not offer a free trial'; 7-day money-back guarantee. (prod.bookbeam.io/pricing still shows an older list with a $24 Starter plan; treat bookbeam.io as current and check at checkout.)"
      - url: https://chromewebstore.google.com/detail/mbchdidopjakdkjkekaogajhcdekciop
        note: "'BookBeam: Extension' v1.4.6, updated October 9, 2026, 30,000 users. Store privacy panel: handles 'Personally identifiable information', 'Authentication information', 'Website content'."
    user_decision: "2026-10-16: the user accepts the Amazon-account risk of the BookBeam extension. NOT yet decided: the Data Synchronization clause (the extension fetching Amazon pages in the background over the user's connection). CLAUDE.md allows Amazon only through official APIs or the user's own clicks, so CE Lite is not used until the user says yes and that exception is written into CLAUDE.md."
    checked: "2026-10-16"
  everbee:
    scraping: forbidden
    official_api: none for research. EverBee's MCP integration ('Etsy data in your AI assistant', Growth plan and up) is not used, because it would pass competitor listings to an AI model (CLAUDE.md). Botsy reads only CSV files the user exports by hand (exports need a paid plan).
    sources:
      - url: https://everbee.io/terms-of-service
        note: "Last updated May 13, 2026. Section 4.1: 'the software is for your limited use only'. Nothing on derived datasets or how long exports may be kept."
      - url: https://everbee.io/legal/acceptable-use
        note: "Last updated May 13, 2026. Bars 'Scraping, crawling, or using any automated method to collect data from the Services without our prior written consent' and bars users to 'Resell, sublicense, or redistribute EverBee data, reports, or exports as a competing product or dataset'."
      - url: https://www.everbee.io/
        note: "'Paid plans add unlimited searches, Keyword Score, Hot Filters and exports'. 'Save or download up to 3,000 listings at once to a spreadsheet.' Sign-up means installing the extension and connecting your Etsy account."
      - url: https://everbee.io/pricing
        note: "Hobby $0 (10 keywords; product analytics: views, reviews, favorites). Growth $24.99 a month billed yearly at $299 (the page shows $468 a year crossed out, about $39 a month on monthly billing; check at checkout); adds sales, revenue, conversion rate, MCP access. Business $69 a month billed yearly at $828. No free trial."
      - url: https://www.etsy.com/legal/api
        note: "The EverBee extension runs on etsy.com pages, and Etsy's API Terms bar browser extensions accessing or analysing Etsy data without Etsy's written authorization. The user has not decided on this Etsy-account risk."
    checked: "2026-10-16"
  erank:
    scraping: forbidden
    official_api: none. Botsy reads only files the user exports by hand from eRank's website (Export needs Basic or higher). The website is used, not the eRank extension, so nothing runs on etsy.com.
    sources:
      - url: https://erank.com/legal/terms-of-service
        note: "Updated August 13, 2026. Bars users to 'Use automated systems, bots or software to extract data from the Sites (\"screen scraping\")'. Its Intellectual Property includes 'information, data, insights', and 'You agree to not copy, modify, create derivative works of, publicly display, publicly perform, share, sell, re-sell, license, or re-publish any of our Intellectual Property.' Botsy keeps exports and counts private (repo private, briefs never published). Ask eRank in writing whether private aggregation of exports is fine."
      - url: https://erank.com/pricing
        note: "Free $0: 5 keyword lookups a day. Basic $5.99/mo or $65.99/yr: 100 a day plus keyword ideas. Pro $9.99/mo or $94.99/yr: 200 a day. Expert $29.99/mo: 500 a day."
      - url: https://hc.erank.com/en/articles/8420556-keyword-tool
        note: "Keyword Ideas: Keywords, Avg. Searches, Avg. Clicks, Avg. CTR, Etsy Competition and more; Free members see one row. Top Listings (top 100 Etsy results): Rank, Listing (title, thumbnail 'as well as the name of the shop'), Age (Days), Views, Daily Views, Est. Sales and Est. Revenue (both 'over the life of each listing'), Price, Hearts, Tags. 'Export' is for Basic, Pro and Expert members; the file type and the exported columns are not stated."
    checked: "2026-10-16"
  google_trends:
    scraping: forbidden
    official_api: Google Trends API (alpha, by application only; not applied for). Botsy reads only CSV files the user downloads by hand from trends.google.com.
    sources:
      - url: https://trends.google.com/robots.txt
        note: "'Disallow: /explore?' and 'Disallow: /trends/explore?' for all user agents."
      - url: https://policies.google.com/terms
        note: "Effective July 30, 2026. No 'using automated means to access content from any of our services in violation of the machine-readable instructions on our web pages (for example, robots.txt files ...)'."
      - url: https://support.google.com/trends/answer/4365538
        note: "'You can use any information from Google Trends, subject to the Google Terms of Service.' Reused data needs a citation ('Data source: Google Trends'). Charts can be downloaded as CSV."
      - url: https://developers.google.com/search/apis/trends
        note: "Alpha: 'We're now accepting applications for alpha testers'; Google 'can't open the API for everyone yet'."
    checked: "2026-10-16"
  # pinterest and fiverr: unchanged from the current file
```

- Add a `titans_pro` or `ds_quick_view` entry only if you choose that tool later.
- After your answer on the background clause, update `bookbeam.user_decision`.

## 10. What Botsy must change

Wait until the running packs build lands. It is editing research/pipeline.py, research/aggregate.py, service/searchlist.py and config/settings.yaml right now.

**A. Small and useful now (Etsy side, plus a safeguard):**

1. **Alias fix (urgent):**
   - Add "search results", "search result count" and "results count" to the competition aliases.
   - Today a file with Keyword + Search Results reads Search Results as search volume (tested read-only). That would count competing listings as search demand.
2. **Stop KDP files landing in Etsy research:**
   - Today the helper uploads any CSV with, for example, Title + Reviews.
   - The inbox then accepts any CSV with a title or keyword column.
   - Book headers like Title, Price, Reviews, Est. Sales tested as an "erank" listing file. Book titles would then be sorted into the coloring or planner niches.
   - **Fix in service/inbox.py:** move KDP-looking files to `data/research_inputs/kdp/`. Signs: BSR, Best Sellers Rank, ASIN, Author, Print Length, amazon.com links, a bookbeam file name, or the `kdp_manual` template line.
   - Research already skips subfolders.
   - **Second safeguard in research/pipeline.py:** skip files whose detected tool is a KDP tool.
3. **Templates:**
   - Add `etsy-insights-template.csv`, blank and fixed.
   - The helper delivers it to Documents\Botsy by adding it to OUTBOX_FILES.
   - Fixed content means the hourly fetch never replaces your copy.
4. **Search list wording:**
   - Instructions per source instead of "export from EverBee": Marketplace Insights (15 terms a week, similar keywords included); later, eRank Basic exports.
   - The time line in the header changes from "about 10 minutes" to the real estimate.
   - Messages say "research export", not "EverBee or eRank".
5. **eRank lifetime sales:**
   - When the tool is eRank and Age (Days) is present, Est. Sales and Est. Revenue become per-month estimates: value x 30.4 / max(age, 30).
   - Check this against the first real export.
   - If the export puts the shop name inside the Listing cell, read it from there (after a sample).

**B. With KDP build step 3:**

6. **`research/kdp_columns.py`:**
   - KDP alias table and value readers. They read "#12,345 in Books", "6 x 0.25 x 9 inches" and dates, including the way Excel rewrites dates.
   - Authors are hashed for the 3-author count and never written out.
   - Demand comes from the BSR rank percentile plus the ratings count, labelled an estimate.
7. **`kdp-manual-template.csv`:** blank and fixed, with the `# botsy-template: kdp_manual v1` first line.
8. **Windows helper:**
   - Recognize KDP headers and the `# botsy-template:` first line.
   - Upload KDP files with a `kdp-` name so the inbox routes them.
   - CSV only: for Excel files, use "Save As CSV".
9. **Cleanup:** delete files in `data/research_inputs/kdp/` after 90 days. Today cleanup reads only files directly in research_inputs and keeps them 180 days.
10. **Settings:** a `kdp.research` block:
    - sources [manual, bookbeam];
    - min_books 10, min_authors 3;
    - books_per_page 20; book-page fields for the top 10;
    - demand_proxy bsr_rank;
    - keep_raw_days 90.
11. **KDP search list:**
    - Terms checked against the blocklist.
    - Medical log books left out.

**C. Deferred:**

- Google Trends upload in the helper and look-ahead seasons.
- Pre-filled weekly templates.
- Letting the helper watch Documents\Botsy for filled templates, if Save As to Downloads proves confusing.

**Docs and state:**

- KDP_RESEARCH.md: the $0 path, the background clause, the template, current BookBeam prices, and why the Creators API does not fit.
- SERVER.md: new sources and KDP routing.
- task_state.md: your decisions.

**Tests:**

- The Search Results alias.
- KDP headers not detected as erank, and routed to kdp/.
- Template recognition.
- eRank lifetime-to-monthly conversion.
- KDP value readers.

## 11. Not verified

- **BookBeam:**
  - CE Lite export columns and limits;
  - whether it has sales estimates, pages, dates or trim size;
  - whether CE Lite is the same Chrome extension as "BookBeam: Extension";
  - whether background fetches carry Amazon cookies, and how Amazon treats them;
  - which page (bookbeam.io or prod.bookbeam.io) checkout uses.
- **Amazon:** the live Conditions of Use page (we read a third-party copy); any enforcement against research-extension users.
- **eRank:**
  - the export file type;
  - whether Top Listings can be exported;
  - whether shop names come out in their own column.
- **EverBee:** the exact monthly price; export headers.
- **Etsy:**
  - Marketplace Insights wording;
  - whether your shop has it;
  - how many similar keywords it shows;
  - its terms on reusing the numbers;
  - the Etsy Plus price.
- **Other tools:**
  - Titans Pro book-row export;
  - DS Quick View Extended price and how it gets BSR;
  - Keepa, Book Bolt, KDSpy, AMZScout and KDP Rank Fuel details (secondary sources only).
- **File formats:** KDP report file type; the 2026 Google Trends CSV layout; what Excel's "Save As" writes for the template's first line.
- **Time:** all minute figures are estimates.

## 12. Decisions for you

See the user_decisions list. The recommended defaults are marked there.
