"""Shared builders for the botsy-control tests: a whole Botsy data dir in tmp_path, an in-process
MCP client. Nothing touches the network, the repo's data/ or /etc."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import anyio

from core.config import ROOT
from tests.service._support import make_settings

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)     # Wednesday of 2026-W43, 11:00 New York
THIS_WEEK, NEXT_WEEK = "2026-W43", "2026-W44"
ITEM_A = "2026-10-20-coloring_pages-axolotl-garden-1a2b"
ITEM_B = "2026-10-20-stickers-frogs-3c4d"
BRIEF_DAY = "2026-10-20"
MAIN_URL = "https://discord.com/api/webhooks/222222/main-token-BBBBBBBBBBBB"


def ctx(tmp_path: Path, *, live: bool = False, limits_doc: str | None = None, **sections) -> dict:
    """Settings with every folder in tmp_path (orders, control state, plans, the log)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    lim = tmp_path / "limits.yaml"
    if limits_doc is None:
        shutil.copy(ROOT / "config" / "limits.example.yaml", lim)
    elif limits_doc != "MISSING":
        lim.write_text(limits_doc, encoding="utf-8")
    if lim.exists():
        os.chmod(lim, 0o644)
    control = {"orders_dir": str(tmp_path / "data" / "orders"),
               "state_file": str(tmp_path / "data" / "control" / "state.json"), "limits_path": str(lim),
               "limits_require_root": False, "winners_file": str(tmp_path / "data" / "performance" / "winners.json"),
               "log_file": str(tmp_path / "data" / "logs" / "botsy-control.log"), **sections.pop("control", {})}
    plan = {"dir": str(tmp_path / "data" / "plans"), **sections.pop("plan", {})}
    s, _ = make_settings(tmp_path, service={"live": live}, control=control, plan=plan, **sections)
    return s


def work_root(s: dict) -> Path:
    return Path(s["paths"]["output_dir"]) if s["service"]["live"] else Path(s["pipeline"]["dry_run_output_dir"])


def outbox(s: dict) -> Path:
    return Path(s["service"]["inbox_dir"]) / "outbox"


def item(s: dict, item_id: str, *, status="pending", digest="d1", passed=True, drafted=False, reasons=None,
         mockups=None, theme="axolotl garden", image: bytes | None = None) -> dict:
    d = work_root(s) / item_id
    (d / "mockups").mkdir(parents=True, exist_ok=True)
    (d / "mockups" / "mockup-01.jpg").write_bytes(image if image is not None else b"\xff\xd8 fake")
    (d / "qa_report.json").write_text(json.dumps({"schema": "qa/v1", "item_id": item_id, "passed": passed}))
    (d / "manifest.json").write_text(json.dumps({"item_id": item_id, "niche": item_id.split("-")[3],
                                                 "theme": theme}))
    if drafted:
        (d / "publish.json").write_text(json.dumps({"listing_id": 900000001}))
    rdir = work_root(s) / "review"
    rdir.mkdir(parents=True, exist_ok=True)
    entry = {"item_id": item_id, "niche": item_id.split("-")[3], "status": status, "needs_review": True,
             "reasons": reasons if reasons is not None else ["trademark: Winnie the Pooh"], "qa_reasons": [],
             "mockups": mockups if mockups is not None else ["mockups/mockup-01.jpg"], "content_digest": digest,
             "queued": "2026-10-20"}
    (rdir / f"{item_id}.json").write_text(json.dumps(entry))
    return entry


def brief_file(s: dict, niche: str = "coloring_pages", day: str = BRIEF_DAY, briefs=None) -> Path:
    d = Path(s["research"]["briefs_dir"])
    d.mkdir(parents=True, exist_ok=True)
    rows = briefs if briefs is not None else [
        {"brief_id": f"{niche}-{day}-01", "niche": niche, "theme": "axolotl garden",
         "keywords": ["axolotl coloring pages", "SecretShopAlpha favourites"], "pack_sizes": [5, 12, 20],
         "style_notes": "bold outlines", "evidence": {"listing_count": 23, "shop_count": 7, "opportunity": 0.77,
                                                      "top_seller_sizes": [12]}},
        {"brief_id": f"{niche}-{day}-02", "niche": niche, "theme": "cozy mushrooms", "pack_sizes": [5, 20],
         "evidence": {"listing_count": 15, "shop_count": 4, "opportunity": 0.61}}]
    doc = {"schema": "brief/v1", "niche": niche, "created": day, "shop_id": "main", "stage": "discovery",
           "listing_count": 250, "min_listings_per_pattern": 10, "min_shops_per_pattern": 3,
           "sources": [{"tool": "everbee", "file": "everbee-2026-10-19-deadbeef.csv", "rows": 250}],
           "patterns": {"themes": [{"name": "axolotl garden", "listing_count": 23, "listing_rows": 23, "shop_count": 7}]},
           "briefs": rows}
    p = d / f"{niche}-{day}.json"
    p.write_text(json.dumps(doc))
    return p


def journal(s: dict) -> list[dict]:
    p = Path(s["service"]["events_file"])
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def fixed_clock():
    return NOW


def mcp_call(s: dict, server: str, name: str, args: dict | None = None, *, mode: str = "auto",
             clock=fixed_clock):
    """One tool call through a real in-process MCP client (clock None = the real time).
    Returns (data, result)."""
    from mcp import Client

    from control import build

    async def go():
        async with Client(build(server, settings=s, clock=clock), mode=mode) as c:
            return await c.call_tool(name, args or {})

    res = anyio.run(go)
    data = json.loads(res.content[0].text)
    return data, res


def mcp_list(s: dict, server: str, *, mode: str = "auto"):
    from mcp import Client

    from control import build

    async def go():
        async with Client(build(server, settings=s, clock=lambda: NOW), mode=mode) as c:
            return await c.list_tools(), await c.list_resources(), await c.list_prompts()

    return anyio.run(go)
