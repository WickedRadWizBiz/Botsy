# Etsy Agents — Project Guide

## Purpose
A pipeline that studies demand in digital-download niches, generates original products, writes SEO listings, and saves them as Etsy drafts for human approval.
Niches: digital stickers, coloring pages, children's activity sheets. Supports more than one shop via config.

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
- No scraping and no browser automation on etsy.com.
- Never use the Etsy API to collect or analyze other sellers' listings or shops (Etsy API Terms, section 5, items 24–25; requires Etsy's written authorization).
- Etsy API use is limited to our own shop: drafts, images, digital files, our own stats.
- Never copy a specific listing. Never pass a competitor's image, file, title or description to any model.
- Briefs are built from patterns aggregated across at least 10 listings and contain no shop names, listing URLs or images.
- No trademarked or licensed characters, brands or franchises in designs, titles, tags or descriptions. Check every brief and listing against `config/trademark_blocklist.txt`, and flag anything uncertain for review.
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
