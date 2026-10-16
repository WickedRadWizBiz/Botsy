# Expansion research: overseer agent, Fiverr-Botsy, other income avenues
_Researched 2026-10-16 (web sources read that day; see links). Recommendations, not decisions: decisions are in task_state.md._

## 1. Central overseer ("Hermes") and control UI

THE PLAN IN ONE PARAGRAPH
One 'boss' assistant, Hermes Agent, runs on the same Lightsail server as Botsy. You talk to it in a private Discord chat. It does not make products itself. It can only press a short list of safe buttons, which I call a 'safe menu', that each worker bot offers. Today the only worker is Etsy-Botsy; Fiverr-Botsy comes later. Each worker keeps running by itself, the way Botsy does today.

WHY HERMES AGENT AND NOT STARNET
- Hermes Agent is free and open source (MIT). It is built to run 24/7 on a small server. It talks over Discord, and only people on an allowlist can talk to it.
  Sources: https://github.com/NousResearch/hermes-agent and https://hermes-agent.nousresearch.com/docs/user-guide/messaging/, read 2026-10-16.
- StarNet is a Windows/Mac desktop app, and Linux is 'not a supported public release target'. Its main server file alone is 26,140 lines. Its name and art are not licensed to copies, so a copy would need a full rebrand. It ships shell, browser and computer-control tools, which clash with Botsy's no-browser-automation rule.
  Source: the local clone's README.md, LICENSE and sidecar/, read 2026-10-16.
- Copying (forking) StarNet would be a second full-time job. Skip it.

PHASE 0: THE SAFE MENU (build first; no AI involved)
- A small locked-down command on the server called 'botsy-control'. It knows only these actions: status, spend, list items waiting for review, approve one item, reject one item, set cap, pause, resume, run now, daily log.
- Botsy already has most of these. Only 'set cap' and 'pause/resume' are new.
- 'Set cap' changes the monthly cap, $25 today, in the server-only settings file. It refuses any number above a ceiling kept in a file only you (the server's admin account) can edit, for example $100.
- 'Pause' stops the paid steps until you say 'resume'.
- Every change is logged and posted to Discord.
- Next, wrap the menu as a small 'Botsy tool server'. This is the standard plug (called MCP) that AI assistants use to call tools.
- Result: you can change the cap with one short command instead of editing a file over SSH.

PHASE 1: THE FIRST VERSION, A BOSS IN DISCORD
- Install Hermes Agent under its own server account called 'hermes'. It cannot read Botsy's files or keys.
- Freeze it on one version (for example v0.22.2) and update only on purpose. Three versions came out on Oct 13-15, 2026, and one 'was built from the wrong commit' (https://github.com/NousResearch/hermes-agent/releases, read 2026-10-16).
- Settings:
  - Only your Discord ID can talk to it.
  - Only the Botsy tools are turned on. Terminal, file, browser and code tools are off. The docs show how to set tools per chat app but not how to allow only one set, so we test that.
  - No add-on installs.
  - It cannot schedule its own jobs.
  - Model: Claude Haiku 5.5 (cheap).
- A daily summary runs as a plain script, so it costs nothing in AI.
- What you see:
  - You DM 'How is Botsy doing?' It replies: '$11.40 of $25 spent this month. 3 items wait for review. Last run 06:00, OK.'
  - You DM 'Set the cap to $40.' It replies: 'Raise from $25 to $40? Reply with code 4821.' You reply with the code, and Botsy's channel posts 'Cap changed to $40.'
  - 'Pause Botsy' stops the paid steps until you say 'resume'.

PHASE 2: A CONTROL PAGE
- A 'Botsy' tab inside Hermes's own web dashboard. It shows a spend bar, a cap number box with a Save button, a Pause switch, and the review queue with Approve and Reject buttons. Hermes supports tabs like this (https://hermes-agent.nousresearch.com/docs/user-guide/features/extending-the-dashboard, read 2026-10-16).
- The page lives only inside the server (127.0.0.1:9119) and is never opened to the internet.
- You open it from Windows through Hermes Desktop's SSH option, or with one SSH tunnel command through a new tunnel-only account. The docs say this works; it is untested on Windows.
- Caution: on the server itself, the page has no login, and it can read and write Hermes's key file (https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard). So the ceiling and the confirm step stay in botsy-control, not in the page.

PHASE 3: MORE WORKERS
- Fiverr-Botsy, and any later workers, follow the same pattern: their own server account, their own safe menu, their own tool server and their own small cap.
- The boss gets new buttons, such as 'Fiverr orders due today'.
- The boss never gets a 'do anything' tool.

APPROVAL GATES
- Etsy: Botsy still creates drafts only. No tool can make a listing live. You approve in Etsy.
- Approving a review item from chat needs a confirm code.
- Raising the cap needs a confirm code or a Save click, and can never go above the ceiling file. Lowering the cap or pausing happens at once.
- No tool hands research-export rows or other sellers' data to the AI.
- No add-on installs, and the boss cannot schedule its own jobs.
- Hermes's own security page says its command blocklist is 'Defense-in-depth, not a hard boundary', that the local terminal has no isolation, and that the gateway must never run as root. It does not say whether its approval prompts cover tool-server calls. That is why the Botsy menu must be safe on its own (https://hermes-agent.nousresearch.com/docs/user-guide/security, read 2026-10-16).

MONTHLY COSTS
- Server: the 4 GB Lightsail plan is $24 today. The 8 GB plan is $44 if Hermes plus Botsy's image cleanup don't fit (https://aws.amazon.com/lightsail/pricing/, read 2026-10-16). Memory use is unmeasured; check it after install.
- Hermes: free (MIT).
- The boss's AI use: my estimate is about $1-3 a month on Haiku 5.5 at about 10 chats a day. That comes from list prices of $0.10 per million input tokens and $0.50 per million output tokens, assuming 20-30K tokens per chat. It is arithmetic, not a measurement. Measure the real size with `hermes prompt-size`.
- Give the boss its own Anthropic workspace key with its own monthly limit, for example $5. Per-workspace limits exist per Anthropic's docs (https://platform.claude.com/docs/en/api/cli/beta/organization/spend_limits/set.md).
- Discord is free.
- Botsy's own $25 cap stays separate and unchanged.
- The boss earns nothing directly. Its value is less hands-on time for you and safer control.

FALLBACK IF HERMES PROVES FLAKY
- A small private web page on the server plus our own short Claude loop, using the same safe menu. It means more code of our own, but no fast-moving outside project to depend on.
- StarNet's official Windows app could still be an optional desktop viewer, but only while your PC is on. Its '$25' limit is per DAY for its own AI use, not Botsy's $25 per month.

## 2. Fiverr-Botsy

IN SHORT
Fiverr-Botsy is a back office. It never touches fiverr.com, and you do every click on Fiverr. It is not passive income.

FIVERR RULES THAT SHAPE IT (all read 2026-10-16 through Fiverr's public Help Center)
- Banned: getting in 'through unauthorized methods', manipulating rankings, scraping, and using 'software, scripts, bots' to send mass messages.
- All payments stay on Fiverr. No emails, phone numbers or chat apps shared to move business off Fiverr.
  Source for both: https://help.fiverr.com/hc/en-us/articles/32242973123985-Our-Community-Standards (edited 2026-07-28).
- AI is allowed in every category, but each delivery must be 'meaningfully refined and customized' for that buyer. 'Generic, unmodified, or reused AI output' fails. You 'must also honor a client's explicit request for non-AI work'.
  Source: https://help.fiverr.com/hc/en-us/articles/37333301560593 (edited 2026-07-09).
- No public seller API for orders, messages or gigs was found. That is absence of evidence, not a confirmed fact.
- Fiverr's full Terms of Service could not be read, because fiverr.com blocks automated reading. You should read it before launch: https://www.fiverr.com/legal-portal/legal-terms/terms-of-service

WHAT BOTSY DOES (automated, on our server only)
1. You paste the buyer's answers into an order folder: a requirements.txt file plus a short order sheet with the package, due time, revisions left, and 'AI OK? yes/no'.
2. Claude turns this into a page plan. Checks then run: the trademark blocklist, no medical claims, no photos of real people or children. Orders marked 'no AI' go only to the puzzle and planner generators, which use no AI images.
3. The existing generators build the pages. The existing QA runs, plus two new checks: every request is covered, and no page repeats an Etsy pack or another buyer's order.
4. Botsy packs the delivery: a ZIP with Letter and A4 PDFs, PNGs and a rights note; watermarked previews; and a draft reply. A guard blocks emails, phone numbers, links and 'pay outside Fiverr' wording.
5. A one-time gig kit for you to paste into Fiverr: title, 3 packages, FAQs, buyer questions, 3 gallery images at 1280x769 made from our own samples, plus auto-reply and quick-reply texts.
6. Discord alerts such as 'order ready to check' and 'due in 12 hours'. Buyer files are deleted 30 days after the order completes.

WHAT YOU DO (by hand, on fiverr.com)
- Log in, read messages, and answer new buyers within 24 hours. Your response rate counts this (https://help.fiverr.com/hc/en-us/articles/360011451678).
- Paste the buyer's requirements into the order folder, check the files, press Deliver, and handle revisions.
- Set up the gigs by pasting in the kit.
- Fiverr's own auto-reply and quick responses are fine to use (https://help.fiverr.com/hc/en-us/articles/10092069817617).
- Never use Fiverr browser bridges (such as 'fiverr-mcp'), scrapers or order-export extensions.

GIGS TO OFFER FIRST (new sellers can have up to 4 gigs: https://help.fiverr.com/hc/en-us/articles/360010560118)
1. Personalized kids activity packs: name tracing, themed word searches, mazes, dot-to-dot, and classroom word lists.
   - The best fit: almost no AI cost, unique to each buyer by design, and no AI images needed.
   - Suggested packages: $15 for 5 pages, $30 for 15 pages with answer keys, $55 for 30 pages or a class set. These are my guesses, anchored on other gigs' prices seen in search results; the demand is not verified.
2. Custom planner pages and KDP planner interiors.
   - Describe the layout only. Fiverr bans services that claim to treat conditions, including emotional therapy (https://help.fiverr.com/hc/en-us/articles/49174165765137).
3. Custom sheets for homebrew tabletop RPGs, built from the buyer's own stats and fields. This avoids trademarks entirely.

LATER, MAYBE
- KDP puzzle-book interiors. Crowded, with gigs listed 'from $5'.

SKIP FOR NOW
- Photo-to-coloring pages: consent issues, photos of children, and free tools that already do it.
- Sticker sets: entry prices of $5-15.

WORK AND EARNINGS, HONESTLY
- Level 1 needs 5 orders, 3 buyers, $400 earned and a 4.4 rating (https://help.fiverr.com/hc/en-us/articles/360010560118).
- Sellers reportedly keep 80% after a 14-day hold (https://help.fiverr.com/hc/en-us/articles/34069565843985; researcher-read, not re-checked by me).
- Fiverr's active buyers fell 21.9% to 2.7M. The CEO says AI 'absorbs high-volume, low-value, transactional tasks', which is the cheap end where new sellers start (Q2 2026 release, 2026-07-29: https://www.stocktitan.net/news/FVRR/fiverr-announces-second-quarter-2026-o0quisjjfetp.html).
- What new sellers earn: unknown. I found no credible source.
- Pure arithmetic, not a forecast: 10 orders a month at $30 would net about $240.

MAIN RISKS
- Account suspension if any part automates fiverr.com.
- A refund plus suspension if AI use is hidden or a delivery looks generic or reused.
- Children's names are personal data. Use only Gemini's paid tier for anything from buyers, because Google uses unpaid-tier content to improve its products (https://ai.google.dev/gemini-api/terms). Delete buyer files on schedule.
- Deadlines, 3-day buyer review windows, and gigs that reportedly pause after 30 days of inactivity.
- Copying another seller's gig text is banned. Never copy it, and never feed it to an AI.

## 3. Ranked passive-income avenues

| # | Score | Avenue | Summary | Sources |
|---|---|---|---|---|
| 1 | 8/10 | Hyperlinked digital planners (tablet PDFs for apps like GoodNotes) through the current Etsy pipeline | Fit: very high. It reuses the stationery and TTRPG pages, QA and the Etsy draft flow, and Botsy's PDF library already supports clickable links. Automation: full, up to an Etsy draft, as today. Risk: probably a crowded category (unverified; check your EverBee/eRank exports); GoodNotes links need read-only mode on Android/Windows; no ADHD treatment claims. Effort: low. Earnings: unknown, no credible public data. | https://support.goodnotes.com/hc/en-us/articles/13623343439631 (search snippet, 2026-10-16)<br>/home/user/Botsy/generators/shared.py and the reportlab canvas link functions in the Botsy venv (checked by researcher 2026-10-16) |
| 2 | 7/10 | Pinterest Pins for our own Etsy listings through Pinterest's official API | Fit: high. It reuses the mockups and listing text, and brings traffic rather than new products. Automation: allowed, but 'the end user must choose each Pin to be published', so you approve every Pin. Trial Pins are visible only to you until Standard access, which needs a video of the login flow. Risk: spam rules on near-duplicate Pins, and AI images get an 'AI modified' label. Effort: medium. Earnings: indirect (traffic), unknown. | https://policy.pinterest.com/en/developer-guidelines (read 2026-10-16)<br>https://developers.pinterest.com/docs/key-concepts/access-tiers/ (read 2026-10-16)<br>https://community.pinterest.biz/t/introducing-gen-ai-labels-on-pinterest/30967 (researcher-read 2026-10-16) |
| 3 | 6/10 | Amazon KDP paperbacks (activity, coloring, planner and TTRPG log books) | Fit: high. Botsy's generators can make 8.5x11 interiors. Automation: Botsy makes the files and you upload by hand (no public KDP upload API found). The limit is 2 new titles per format per week, and AI-generated images must be disclosed. Risk: saturated market, near-duplicate books, account rules; planners and journals get no free ISBN. Effort: medium. Earnings per sale only: a 100-page black-ink 8.5x11 book costs $2.84 to print, which leaves about $3.15 per copy at $9.99 (60% royalty) or about $1.16 at $7.99 (50%). Sales volume: unknown. | https://kdp.amazon.com/en_US/help/topic/G202172740 (read 2026-10-16)<br>https://kdp.amazon.com/en_US/help/topic/G200672390 (read 2026-10-16)<br>https://kdp.amazon.com/en_US/help/topic/G201834330 (read 2026-10-16) |
| 4 | 6/10 | Fiverr-Botsy: custom printables made to order (kids activity packs, planner pages, homebrew TTRPG sheets) | Fit: high code reuse. Automation: only the file-making happens off Fiverr; every Fiverr click is yours, and bots and scraping are banned. Risk: not passive (24-hour replies, deadlines), suspension if anything is automated, a shrinking low-end market (active buyers down 21.9%). Effort: medium. Earnings: unknown. | https://help.fiverr.com/hc/en-us/articles/32242973123985-Our-Community-Standards (read 2026-10-16)<br>https://help.fiverr.com/hc/en-us/articles/37333301560593 (read 2026-10-16)<br>https://help.fiverr.com/hc/en-us/articles/360010560118 (read 2026-10-16) |
| 5 | 5/10 | Our own storefront mirror (Payhip, Gumroad or Lemon Squeezy) | Fit: the same files and text as Etsy. Automation: weak. Lemon Squeezy's API can only list and read products, no Payhip API was found, and Gumroad product creation is unverified, so Botsy makes an upload kit and you upload. Risk: no built-in buyers, and taxes, support and refunds are ours; selling the same files on TPT Exclusive would conflict. Effort: low. Fees (researcher-read): Gumroad 10% + $0.50, Payhip 5% on the free plan, Lemon Squeezy 5% + 50 cents. Earnings: unknown. | https://docs.lemonsqueezy.com/api/products (read 2026-10-16)<br>https://gumroad.com/pricing (researcher-read 2026-10-16)<br>https://payhip.com/pricing (researcher-read 2026-10-16) |
| 6 | 4/10 | Print-on-demand stickers and prints through Printify in our own Etsy shop | Fit: medium-high, since it reuses the sticker PNGs. Automation: Printify's API can create products and publish them to the shop, but whether Etsy receives a draft or a live listing is unverified. Botsy must never make a listing live, so test one product first or leave the publish click to you. Printful's API will 'never support' creating products on outside platforms. Risk: physical goods and returns, and Printify must be named as a production partner. Effort: medium. Earnings: unknown. | https://developers.printify.com/ (researcher-read 2026-10-16)<br>https://developers.printful.com/docs/ (read 2026-10-16)<br>https://podvector.ai/articles/printify/integrations/printify-etsy-integration-setup-guide-for-pod-sellers (third-party, searched 2026-10-16) |
| 7 | 4/10 | Google Sheets trackers (habit, budget, TTRPG campaign) sold as Etsy digital items | Fit: medium; the spreadsheet builder is new code. Automation: the official Google Sheets API plus the Etsy drafts we already make. Risk: copy links can be passed around, and formula bugs lead to refunds. Effort: medium. Earnings: unknown. I did not check the Sheets API page myself. | https://developers.google.com/sheets/api (researcher-cited, not re-checked) |
| 8 | 3/10 | Adobe Stock illustrations (master sets only); not Shutterstock | Fit: medium. Automation: web or SFTP upload with the 'generative AI' box ticked, under weekly upload limits; Shutterstock refuses AI content. Risk: saturated categories, low payouts (about $0.99 per license in Adobe's photo-plan example, from search snippets), and penalties for near-duplicates. Effort: medium. Earnings: unknown. | https://submit.shutterstock.com/help/en/articles/10594622-content-policy-updates-ai-generated-content (read 2026-10-16)<br>https://helpx.adobe.com/stock/contributor/submit-your-content/submit-generative-ai-content/generative-ai-content-guidelines.html (search snippet, 2026-10-16)<br>https://community.adobe.com/announcements-37/new-submission-limits-for-adobe-stock-contributors-315961 (researcher-read 2026-10-16) |
| 9 | 3/10 | Design marketplaces (Creative Market; Creative Fabrica and Design Bundles unverified) | Fit: medium for the content, but the license wording needs rework. Automation: manual uploads with a yes/no AI label on each one. Risk: repetitive or derived packs are discouraged; the other sites' AI rules could not be read. Effort: medium. Earnings: unknown. | https://support.creativemarket.com/hc/en-us/articles/26926388691099-Navigating-Our-New-AI-Label (search snippet, 2026-10-16)<br>https://creativemarket.com/blog/destination-design-vision (researcher-read 2026-10-16) |
| 10 | 1/10 | AVOID FOR NOW: TPT, Canva Creators, Redbubble/TeePublic/Society6/Merch on Demand, faceless YouTube, mass AI niche sites | TPT is going 'AI-free' (written policy still pending). Canva Creators has not allowed AI-generated content since 2026-01-14. The POD marketplaces have no official upload API, so uploads would be manual, and they come with steep fees or are invite-only. YouTube demonetizes templated mass AI videos. Google treats mass AI pages as spam. Fit and earnings: poor, or a risk to the account. | https://www.chalkbeat.org/2026/10/15/teachers-pay-teachers-to-ban-ai-generated-teaching-resources/ (read 2026-10-16)<br>https://www.canva.com/help/updates-to-contributor-agreement-jan-2026/ (search snippet, 2026-10-16)<br>https://help.redbubble.com/hc/en-us/articles/50959494599060-How-does-the-platform-fee-get-calculated (researcher, search snippet) |

## 4. Claims spot-checked by the synthesizer

- CONFIRMED: Fiverr bans 'access the platform through unauthorized methods', ranking manipulation, scraping, and using 'artificial tools and/or automated processes (through software, scripts, bots, or any other means)' to send mass messages. All payments must go through Fiverr. Checked by reading the article JSON from Fiverr's public Help Center API (edited 2026-07-28). https://help.fiverr.com/hc/en-us/articles/32242973123985-Our-Community-Standards, read 2026-10-16.
- CONFIRMED, WITH A CAUTION: Fiverr 'permits the use of AI across all service categories'. The work must be 'meaningfully refined and customized to the client's specific requirements'. 'Generic, unmodified, or reused AI output' fails the quality standard. Sellers 'must also honor a client's explicit request for non-AI work'. Article edited 2026-07-09. The researcher read 'freelancers do not necessarily disclose all the tools they use' as meaning disclosure is optional. That sentence describes what sellers do; it does not grant permission. I recommend disclosing AI use anyway. https://help.fiverr.com/hc/en-us/articles/37333301560593, read 2026-10-16.
- CONFIRMED: Fiverr Level 1 needs a Success Score of 5+, a rating of 4.4+, an 80% response rate, 5 orders, 3 unique clients and $400 earned (edited 2025-12-17). https://help.fiverr.com/hc/en-us/articles/360010560118, read 2026-10-16. Also CONFIRMED: Fiverr's Q2 2026 release (2026-07-29) reports 2.7M active buyers, down 21.9%, and revenue of $97.8M, down 10.0%. It quotes the CEO: 'AI absorbs high-volume, low-value, transactional tasks.' I read this on a site that republishes the release: https://www.stocktitan.net/news/FVRR/fiverr-announces-second-quarter-2026-o0quisjjfetp.html, read 2026-10-16. NOT RE-CHECKED by me: the 80% payout with a 14-day hold, and gigs pausing after 30 days of inactivity (researcher-read only).
- CONFIRMED: Hermes Agent is MIT-licensed. Its README says 'Run it on a $5 VPS...' and 'talk to it from Telegram while it works on a cloud VM.' Releases: v0.22.0 on 2026-10-13, v0.22.1 on 2026-10-14, v0.22.2 on 2026-10-15. The v0.22.2 notes say 'v0.22.1 was built from the wrong commit.' Sources: https://raw.githubusercontent.com/NousResearch/hermes-agent/main/README.md and https://github.com/NousResearch/hermes-agent/releases, read 2026-10-16.
- CONFIRMED: Hermes security page. The local terminal backend has isolation 'None — runs on host'. The checklist says 'never run the gateway as root'. The command denylist is 'Defense-in-depth, not a hard boundary'. Approval modes are smart, manual and off. GAP (confirmed): the page does not say whether approvals cover MCP tool calls, so Botsy's own tools must ask for confirmation themselves. https://hermes-agent.nousresearch.com/docs/user-guide/security, read 2026-10-16.
- CONFIRMED, WITH A SMALL CORRECTION: the Hermes gateway is 'a single background process that connects to all your configured platforms'. 'By default, the gateway denies all users who are not in an allowlist or paired via DM' (DISCORD_ALLOWED_USERS). GATEWAY_ALLOW_ALL_USERS=true is 'NOT recommended for bots with terminal access'. Correction: for a VPS the docs suggest the system service (`sudo hermes gateway install --system`) so it 'come[s] back at boot', or a user service with lingering enabled. https://hermes-agent.nousresearch.com/docs/user-guide/messaging/, read 2026-10-16.
- CONFIRMED: MCP tool servers go under mcp_servers in ~/.hermes/config.yaml, with a per-server tools include/exclude list. Only the env vars you list plus 'a safe baseline' reach the server. PARTLY VERIFIED: tools can be limited per platform (a `toolsets` list, hermes-<platform> presets, disabled_toolsets, and mcp-<server> toolsets usable in 'platform configs'). The docs do NOT show a tested 'Discord gets only the Botsy tools' recipe, so this must be tested. Sources: https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp, https://hermes-agent.nousresearch.com/docs/user-guide/features/tools and https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference, read 2026-10-16.
- CONFIRMED: the Hermes dashboard binds to 127.0.0.1:9119 with no login on loopback. Any other address forces an auth provider. The dashboard 'reads and writes your .env file'. Plugin tabs add routes under /api/plugins/<name>/ that 'sit behind the dashboard's normal auth gate' and 'run inside the dashboard process'. I found no budget or cap feature. SKEPTIC NOTE: with no login on loopback, any program on the server can reach the dashboard. So the cap ceiling and the confirmation step must live in Botsy's own control command, not in the dashboard. Sources: https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard and https://hermes-agent.nousresearch.com/docs/user-guide/features/extending-the-dashboard, read 2026-10-16.
- CONFIRMED, by reading the local StarNet clone only (no code run): code is MIT, Copyright (c) 2026 Andrew Sims. 'The MIT License covers the code only': the name, logo and artwork are not licensed (README.md lines 165-173). 'Linux packages are internal build artifacts only and are not a supported public release target' (README.md line 79). sidecar/index.js is 26,140 lines and calls server.listen(PORT, '127.0.0.1') at line 11571. budgetcaps.js sets DEFAULT_PER_DAY_USD = 25, which is per DAY and for StarNet's own AI spend. Built-in tools include shell, terminal, browser, computer and desktop. NOTICE.md says Hermes is 'the harness StarNet's backend was in part ported from'. Read 2026-10-16.
- CONFIRMED: Pinterest's developer guidelines list Pin schedulers as acceptable, but say 'the end user must choose each Pin to be published'. They also ban scraping and using Pinterest materials to train AI. Pins made with Trial access are 'only visible to their creator as Sandbox entities'. Standard access needs a video of the OAuth flow, 'even if you are the only user'. Sources: https://policy.pinterest.com/en/developer-guidelines and https://developers.pinterest.com/docs/key-concepts/access-tiers/, read 2026-10-16.
- CONFIRMED: KDP allows 'Up to 2 titles per book format each week' to be created, and separately 'Up to 2 new titles per book format each week' to be submitted. Both reset Sunday 00:00 UTC. AI-generated text, images or translations must be disclosed; AI-assisted content need not be. US paperback royalty is 60% at $9.99 and up and 50% at $9.98 and below. Printing a large-trim black-ink book of 24-110 pages costs a fixed $2.84. UNVERIFIED: that the weekly limit started 2026-09-21 (secondary source only). Sources: https://kdp.amazon.com/en_US/help/topic/G202172740, https://kdp.amazon.com/en_US/help/topic/G200672390, https://kdp.amazon.com/en_US/help/topic/G201834330 and https://kdp.amazon.com/en_US/help/topic/G201834340, read 2026-10-16.
- CONFIRMED, WITH A CORRECTION: TPT plans to ban AI-generated resources ('unique, expertly crafted, and AI-free'), and the written policy is 'forthcoming'. Correction: Chalkbeat (2026-10-15) dates the town hall only as 'last week'. The exact date of 2026-10-08 and the scope (covers, thumbnails, descriptions) come from seller blogs I did not verify. https://www.chalkbeat.org/2026/10/15/teachers-pay-teachers-to-ban-ai-generated-teaching-resources/, read 2026-10-16.
- CONFIRMED: Printful's Products API 'is not intended and will never support creating and managing products in external platforms such as Shopify' (https://developers.printful.com/docs/). Lemon Squeezy's products API has only 'List all products' and 'Retrieve a product', with no create endpoint (https://docs.lemonsqueezy.com/api/products). Both read 2026-10-16. STILL UNVERIFIED: whether a Printify publish lands in Etsy as a draft or a live listing. Third-party guides disagree; one mentions a 'Hide in store' toggle that sends it as a draft. I found no official Printify page.
- CONFIRMED: Shutterstock 'does not accept AI-generated content from our contributors' (page dated 2025-07-16; https://submit.shutterstock.com/help/en/articles/10594622-content-policy-updates-ai-generated-content, read 2026-10-16). Canva Creators does not permit AI-generated content as of 2026-01-14. That comes from search results of Canva's own help page; I did not fetch it directly (https://www.canva.com/help/updates-to-contributor-agreement-jan-2026/, searched 2026-10-16).
- PARTLY VERIFIED: Anthropic supports monthly spend limits per workspace (official spend-limits API docs, where setting limits through the API is in early access, plus Anthropic's workspaces announcement). The exact Console clicks come from a third-party guide only. Sources: https://platform.claude.com/docs/en/api/cli/beta/organization/spend_limits/set.md and https://anthropic.com/news/workspaces, searched 2026-10-16.
- CONFIRMED: Lightsail Linux plans with public IPv4: 2 GB for $12/mo, 4 GB for $24/mo, 8 GB for $44/mo (https://aws.amazon.com/lightsail/pricing/, read 2026-10-16). CONFIRMED in the Botsy repo: there is no manual pause or set-cap command today. service/__main__.py COMMANDS = ingest, research, run, searchlist, tick, status, digest, spend, notify-test, cleanup; qa has review list/approve/reject. The cap is spend.monthly_cap_usd: 25 (config/settings.yaml line 222). Botsy already pauses paid steps by itself when the cap is reached (service/tick.py). The Haiku 5.5 price of $0.10 in / $0.50 out per million tokens matches config/settings.yaml line 243 (checked 2026-10-16).

## 5. Decisions for the user

- Confirm that 'Hermes' means Nous Research's Hermes Agent, the self-hosted assistant. It is not the Hermes 4 AI model, and not StarNet's pixel-art look.
- Approve Phase 0: a locked 'botsy-control' command that adds 'set cap' and 'pause/resume'. Pick a hard ceiling that only SSH can change (suggested: $100 a month).
- Boss AI spending: give Hermes its own Anthropic workspace key with its own monthly limit (suggested: $5), or count it against Botsy's $25 cap?
- Is it OK to run Hermes on the current 4 GB server ($24), knowing it may need the 8 GB plan ($44) if memory runs short? Memory use is unmeasured.
- Should a confirm code be required in chat for cap raises and for approving review items? Recommended: yes.
- Add a rule to CLAUDE.md: no scraping or browser automation on fiverr.com (and other marketplaces), same as etsy.com. CLAUDE.md says you must approve rule changes.
- Fiverr: are you willing to do the on-Fiverr work yourself (replies within 24 hours, delivering on time, revisions)? Please read Fiverr's Terms of Service yourself; the site blocked automated reading.
- Fiverr first gig: personalized kids activity packs at $15/$30/$55? These prices are unverified guesses.
- Fiverr budget: share Botsy's $25 a month, or give Fiverr its own cap?
- Fiverr TTRPG gigs: does your 2026-10-16 exception for naming a game system (allowed in Etsy listing text) also cover Fiverr gig text? Default: homebrew and system-neutral only.
- Disclose AI use up front on every Fiverr gig, and offer no-AI versions of the activity, planner and TTRPG gigs? Recommended: yes.
- Is the Gemini key on the paid tier? Buyer names and files must not go to the unpaid tier.
- Next earning step: build hyperlinked digital planners first? Then Pinterest, where you approve every Pin before it posts?
- Printify: willing to test one product to see whether it lands in Etsy as a draft, before connecting it for real?

## Sources

- https://help.fiverr.com/hc/en-us/articles/32242973123985-Our-Community-Standards (read 2026-10-16 via help.fiverr.com/api/v2/help_center/en-us/articles/32242973123985.json; edited 2026-07-28)
- https://help.fiverr.com/hc/en-us/articles/37333301560593 (read 2026-10-16 via Help Center API; edited 2026-07-09)
- https://help.fiverr.com/hc/en-us/articles/360010560118 (read 2026-10-16 via Help Center API; edited 2025-12-17)
- https://help.fiverr.com/hc/en-us/articles/360011451678 (researcher-read 2026-10-16)
- https://help.fiverr.com/hc/en-us/articles/10092069817617 (researcher-read 2026-10-16)
- https://help.fiverr.com/hc/en-us/articles/49174165765137 (researcher-read 2026-10-16)
- https://help.fiverr.com/hc/en-us/articles/34069565843985 (researcher-read 2026-10-16)
- https://www.fiverr.com/legal-portal/legal-terms/terms-of-service (could not be read: HTTP 403, 2026-10-16)
- https://www.stocktitan.net/news/FVRR/fiverr-announces-second-quarter-2026-o0quisjjfetp.html (read 2026-10-16; republishes Fiverr's 2026-07-29 release)
- https://ai.google.dev/gemini-api/terms (researcher-read 2026-10-16)
- https://github.com/NousResearch/hermes-agent (README via raw.githubusercontent.com, read 2026-10-16)
- https://github.com/NousResearch/hermes-agent/releases (read 2026-10-16)
- https://hermes-agent.nousresearch.com/docs/user-guide/security (read 2026-10-16)
- https://hermes-agent.nousresearch.com/docs/user-guide/messaging/ (read 2026-10-16)
- https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp (read 2026-10-16)
- https://hermes-agent.nousresearch.com/docs/user-guide/features/tools (read 2026-10-16)
- https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference (read 2026-10-16)
- https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard (read 2026-10-16)
- https://hermes-agent.nousresearch.com/docs/user-guide/features/extending-the-dashboard (read 2026-10-16)
- https://hermes-agent.nousresearch.com/docs/user-guide/multi-connection-desktop (researcher-read 2026-10-16; not tested on Windows)
- /home/user/androoagi/starnet LICENSE, README.md, INSTALL.md, NOTICE.md, sidecar/index.js, sidecar/budgetcaps.js, sidecar/tools/builtin (read only, 2026-10-16)
- /home/user/Botsy service/__main__.py, qa/__main__.py, config/settings.yaml, service/tick.py, docs/SERVER.md (read 2026-10-16)
- https://aws.amazon.com/lightsail/pricing/ (read 2026-10-16)
- https://platform.claude.com/docs/en/api/cli/beta/organization/spend_limits/set.md (search result, 2026-10-16)
- https://anthropic.com/news/workspaces (search result, 2026-10-16)
- https://policy.pinterest.com/en/developer-guidelines (read 2026-10-16)
- https://developers.pinterest.com/docs/key-concepts/access-tiers/ (read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G202172740 (read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G200672390 (read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G201834330 (read 2026-10-16)
- https://kdp.amazon.com/en_US/help/topic/G201834340 (read 2026-10-16)
- https://www.chalkbeat.org/2026/10/15/teachers-pay-teachers-to-ban-ai-generated-teaching-resources/ (read 2026-10-16)
- https://developers.printful.com/docs/ (read 2026-10-16)
- https://docs.lemonsqueezy.com/api/products (read 2026-10-16)
- https://podvector.ai/articles/printify/integrations/printify-etsy-integration-setup-guide-for-pod-sellers (third-party, search 2026-10-16)
- https://community.make.com/t/etsy-create-a-product-draft/19926 (third-party, search 2026-10-16)
- https://submit.shutterstock.com/help/en/articles/10594622-content-policy-updates-ai-generated-content (read 2026-10-16)
- https://www.canva.com/help/updates-to-contributor-agreement-jan-2026/ (search snippet, 2026-10-16)
- https://developers.printify.com/ (researcher-read 2026-10-16)
- https://support.goodnotes.com/hc/en-us/articles/13623343439631 (researcher, search snippet 2026-10-16)
