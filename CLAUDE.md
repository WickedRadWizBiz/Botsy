# Etsy Agents — Project Guide

## Purpose
A pipeline that studies demand in digital-download niches, generates original products, writes SEO listings, and saves them as Etsy drafts for human approval.
Niches: digital stickers, coloring pages, children's activity sheets, stationery (planners, ADHD/productivity pages), tabletop RPG papers. Products are themed packs (5 pages, 20 pages, plus the size top sellers use), aimed at niche, low-competition markets. Supports more than one shop via config.

## Pipeline
research → brief → generate → QA → listing → draft → human approval

1. `research/` reads exports the user downloads into `data/research_inputs/` (CSV from eRank, EverBee or a similar licensed tool), optional off-Etsy trend signals, and our own shop's stats. Outputs aggregated design briefs.
2. `generators/` turns briefs into print-ready files.
   - Activity sheets: procedural Python (mazes, word searches, tracing, dot-to-dot) with reportlab. No image model needed.
   - Coloring pages: image API → threshold and vectorize cleanup → PDF.
   - Stickers: image API → transparent PNGs + sticker sheet.
3. `qa/` runs automated checks, then queues items for human review.
4. `listing/` writes title, 13 tags, description, alt text and AI disclosure.
5. `publisher/` uses Etsy Open API v3 for our own shop and creates DRAFTS only.

## Compliance rules (never break; ask me before changing any)
- Scraping and browser automation follow each platform's own policies (user, 2026-10-16). Before any automated access, check the platform's terms of service / acceptable-use policy and its robots.txt, and record the result with sources and date in `config/platform_policies.yaml`. Allowed only where the terms do not forbid it and robots.txt allows the paths; if the terms say nothing about scraping or automated access, robots.txt decides. A platform with no entry is not accessed automatically. Never log in, bypass blocks or paywalls, or collect personal data. Recorded so far: Etsy, Amazon (incl. KDP), Pinterest and Fiverr forbid it, so they are used only through official APIs or the user's own clicks.
  - Exception (user, 2026-10-17): the user may run the BookBeam browser extension on Amazon pages, including its background fetching of Amazon pages over the user's own connection (BookBeam terms, "Data Synchronization"), in a separate browser profile that is never logged in to Amazon or KDP. Botsy itself still never accesses Amazon or BookBeam; it reads only the CSV files the user exports by hand. The user also accepts that copying a few public fields from Amazon pages by hand into Botsy's template is their own risk. Both recorded in `config/platform_policies.yaml`.
  - Exception (user, 2026-10-17, "I want to use TinyFish and see how that goes"): Botsy may read PUBLIC Amazon book search, category and product pages through the TinyFish service (Fetch or Agent API) for aggregated KDP market research, although Amazon's Conditions of Use exclude data-gathering tools; the user accepts that risk. Limits: no login or account pages, no reviews text or reviewer names, no personal data, a small fixed weekly page budget set in `config/settings.yaml`, stop (do not retry or escalate) when a page comes back blocked or with a CAPTCHA, raw pages kept at most 90 days outside git, and the 10-book / 3-author floor applies to everything derived. Apify or other services may be used only on platforms whose entry in `config/platform_policies.yaml` says allowed, or by the same kind of dated exception.
- Never use the Etsy API to collect or analyze other sellers' listings or shops (Etsy API Terms, section 5, items 24–25; requires Etsy's written authorization).
- Etsy API use is limited to our own shop: drafts, images, digital files, our own stats.
- Never copy a specific listing. Never pass a competitor's image, file, title or description to any model.
- Briefs are built from patterns aggregated across at least 10 listings and contain no shop names, listing URLs or images.
- No trademarked or licensed characters, brands or franchises in designs, titles, tags or descriptions. Check every brief and listing against `config/trademark_blocklist.txt`, and flag anything uncertain for review.
  - Exception (user, 2026-10-16): tabletop RPG listings may name the game system the sheets are made for, as a compatibility reference only ("Character Sheet for D&D 5e"). Allowed in that listing's title, tags and description; never in designs, image prompts, printed pages, file names or mockups; never logos, official art, settings or product-identity creatures; never implying the product is official. Every such listing carries an "unofficial, not affiliated with <owner>" line. These names are `!term` entries in the blocklist.
- No medical or therapeutic claims in listings (e.g. stationery for ADHD may describe the layout, never claim to treat, cure or diagnose).
  - Health topics (user, 2026-10-17): no medical products on Etsy or KDP (medical log books such as blood pressure, blood sugar, medication or symptom trackers; disease- or medicine-themed stickers). Lifestyle journals and logs are fine (meditation, stress, exercise, sleep, mood, gratitude, food diaries, anxiety and mental-health journals) and are described by their layout; "stress relief" may be used as a descriptive term. Still never: claims to treat, cure, diagnose or manage a condition. Lists live in `config/health_terms.txt`.
- Every AI-assisted listing includes the text in `config/ai_disclosure.txt` and follows Etsy's Creativity Standards.
- The publisher never activates a listing. The user approves in Etsy.
- Secrets live only in `.env` (never committed). Keep `.env.example` current.

## Tech
Python 3.11+ in a venv. Anthropic API for text tasks; image provider set in `config/settings.yaml`.
reportlab (PDFs), Pillow (images), potrace or equivalent (vectorizing line art), pytest.
GitHub Actions for scheduled batch runs; large outputs go to workflow artifacts, not git.

## Operating rules (token budget)
- State lives in `task_state.md`, not the conversation. Read it at session start. After every completed step, update: objective, completed, next action.
- When a sub-task finishes, update `task_state.md`, then tell me to run `/clear`.
- If I say I'm stepping away, tell me to run `/compact`.
- Before a heavy phase (big refactor, batch generation), ask me to check `/usage`. If usage is around 80% or higher, finish the core work first and log deferrable items (tests, polish) under "Deferred until reset" in `task_state.md`.
- Quiet commands: `pip -q`, `pytest -q`, and pipe long output through `tail -n 40` or `grep`.
- Locate code with `grep` / `sed -n` ranges instead of reading whole large files. Don't open files the current step doesn't need.
- Prefer scripts that run on GitHub Actions for long batch jobs over running them in-session.
- If a rule blocks good work, ask me before ignoring it.
