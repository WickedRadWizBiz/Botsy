# etsy-agents

Pipeline for our own Etsy digital-download shop: research → brief → generate → QA → listing → **draft** → human approval.
See `CLAUDE.md` for the rules and `task_state.md` for current progress.

## Setup
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -q -r requirements.txt
cp .env.example .env   # then fill in keys
pytest -q
```
Also install `potrace` (system package) before Phase 2.

## Layout
| Folder | Role |
|---|---|
| `research/` | Parse licensed research exports in `data/research_inputs/` → aggregated briefs in `data/briefs/` |
| `generators/` | Activity sheets (procedural), coloring pages and stickers (Gemini Nano Banana 2.1) |
| `qa/` | Automated checks, then review queue in `output/review/` |
| `listing/` | Title, 13 tags, description, alt text, AI disclosure |
| `publisher/` | Etsy Open API v3, own shop, drafts only |
| `config/` | `settings.yaml`, trademark blocklist, AI disclosure |
