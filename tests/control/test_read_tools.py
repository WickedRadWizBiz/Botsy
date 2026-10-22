"""botsy-read (Level 0): every tool through a real in-process MCP client against a temp Botsy data
dir, and the leak test: no research path, export name, shop name, listing link, review term or
secret ever comes back."""
from __future__ import annotations

import base64
import io
import json
import os
from collections import namedtuple
from pathlib import Path

import pytest

from control import tools
from core import events
from service import capacity, orders
from tests.control._support import (
    BRIEF_DAY,
    ITEM_A,
    ITEM_B,
    NEXT_WEEK,
    NOW,
    THIS_WEEK,
    brief_file,
    ctx,
    item,
    mcp_call,
    outbox,
    work_root,
)

Usage = namedtuple("Usage", "total used free")
SHOP = "SecretShopAlpha"
LISTING_URL = "https://www.etsy.com/listing/9000001/frog-stickers"
EXPORT = "everbee-2026-10-19-deadbeef.csv"
API_SECRET = "sk-ant-test-SECRET-1234567890abcdef"
ENV_SECRET = "etsy-keystring-from-dotenv-0987654321"
EVENT_KEY = "k" * 40


@pytest.fixture(autouse=True)
def big_disk(monkeypatch):
    monkeypatch.setattr(capacity.shutil, "disk_usage", lambda p: Usage(10**12, 0, 10**12))


@pytest.fixture
def s(tmp_path):
    return ctx(tmp_path)


def call(s, name, args=None, **kw):
    data, _ = mcp_call(s, "read", name, args, **kw)
    return data


# ---------------------------------------------------------------- one test per tool

def test_status_fixed_fields_only(s):
    doc = {"schema": "status/v1", "updated": "2026-10-21T14:00:00+00:00", "last_checked": "2026-10-21T14:00:00+00:00",
           "live": False, "week": {"ticks": 3, "exports": 2, "items": 4, "drafts": 1, "errors": 1},
           "ticks": [{"started": "2026-10-21T13:00:00+00:00", "finished": "2026-10-21T13:05:00+00:00",
                      "forced": False, "live": False,
                      "ingest": {"accepted": [EXPORT], "duplicates": [], "rejected": [["x.csv", "bad header"]],
                                 "routed": []},
                      "research": {"ran": True, "stage": "discovery", "brief_files": ["/srv/x/briefs/a.json"],
                                   "briefs": 4, "notes": ["note from " + SHOP]},
                      "run": {"ran": True, "dry_run": True, "items": 2, "generated": 2, "drafts": 0,
                              "awaiting_review": 1, "cooldown": 0, "budget_wait": 0, "failed": ["generate[x]: boom"],
                              "exit_code": 0},
                      "errors": ["research: could not read " + EXPORT], "exit_code": 0}]}
    outbox(s).mkdir(parents=True, exist_ok=True)
    (outbox(s) / "status.json").write_text(json.dumps(doc))
    d = call(s, "status")
    assert d["ok"] and d["live"] is False
    assert d["week"]["exports"] == 2 and d["week"]["items"] == 4
    last = d["last_check"]
    assert last["exports_accepted"] == 1 and last["exports_rejected"] == 1
    assert last["research_stage"] == "discovery" and last["briefs"] == 4 and last["failed"] == 1 and last["errors"] == 1
    assert d["paused"] is None and d["limits_file_ok"] is True
    assert d["hermes_limits"]["changes_per_hour"] == 20
    text = json.dumps(d)
    assert EXPORT not in text and SHOP not in text and "x.csv" not in text and "boom" not in text


def test_spend(s):
    from core import spend

    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=250_000 * 3, settings=s,
                 now=NOW)
    d = call(s, "spend")
    assert d["ok"] and d["month"] == "2026-10" and d["cap"] == 25.0
    assert d["spent"] == pytest.approx(3.0, abs=0.01) and d["money_left"] == pytest.approx(22.0, abs=0.01)
    assert d["warn_line"] == 20.0 and d["cap_source"] == "settings" and d["cap_reached"] is False


DAILY_LOG = "\r\n".join([
    "Botsy daily log", "===============", "What happened from Tue 20 Oct 07:07 to Wed 21 Oct 07:07 (New York time).",
    "Mode: TEST (dry run). No API calls and no Etsy drafts.", "",
    "Exports", "  Received: 2 (2 from EverBee; 300 rows)", "  Not used: 1",
    f"    - {EXPORT}: missing column 'Shop Name'", "  Book (KDP) files kept for the KDP reader: 1",
    "    - bookbeam-export.xlsx", "",
    "Waiting for your review", "  1 item. On the server type the approve line to let one become a draft:",
    f"  - {ITEM_A}: trademark: Winnie the Pooh", f"    Approve: botsy qa review approve {ITEM_A} --dry-run", "",
    "Problems and warnings", "  ! Spending warning: $20.10 of $25.00 used this month (80%).",
    (f"  ! research[coloring_pages]: could not read /opt/botsy/data/research_inputs/{EXPORT} from {SHOP} "
     f"{LISTING_URL} key {API_SECRET}"),
    f"  ! listing[{ITEM_B}]: Anthropic said {ENV_SECRET}",
    "  More detail: status.txt, or on the server: sudo journalctl -u botsy-tick -n 100 --no-pager", "",
    "Discord messages held during quiet hours (not sent again)", f"  - Botsy: new export {EXPORT} from {SHOP}",
    "  - Botsy: 1 draft", "",
    "What runs next", "  Daily check: Thu 22 Oct 06:17", ""])


def test_daily_log_is_scrubbed(s, monkeypatch, tmp_path):
    env = tmp_path / "dotenv"
    env.write_text(f"ETSY_KEYSTRING={ENV_SECRET}\n")
    monkeypatch.setattr(tools, "ENV_FILE", env)
    from control import trend

    monkeypatch.setattr(trend, "ENV_FILE", env)          # no TINYFISH_API_KEY: trend_search is not configured
    monkeypatch.delenv(trend.KEY_ENV, raising=False)
    monkeypatch.setattr(trend, "_http_get", lambda *a, **kw: pytest.fail("no network in tests"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_SECRET)
    outbox(s).mkdir(parents=True, exist_ok=True)
    (outbox(s) / "daily-log.txt").write_text(DAILY_LOG)
    (outbox(s) / "daily-log-2026-10-21.txt").write_text(DAILY_LOG)
    d = call(s, "daily_log")
    assert d["ok"] and d["date"] == "2026-10-21"
    text = d["text"]
    assert "Received: 2 (2 from EverBee; 300 rows)" in text and "Not used: 1" in text
    assert f"  - {ITEM_A}" in text and "Winnie" not in text and "Pooh" not in text
    assert "Spending warning: $20.10 of $25.00" in text
    assert "A problem in research[coloring_pages]" in text and f"A problem in listing[{ITEM_B}]" in text
    assert "2 message(s)" in text and "What runs next" in text
    for bad in (EXPORT, "bookbeam-export", SHOP, "etsy.com", API_SECRET, ENV_SECRET, "research_inputs", "/opt/botsy"):
        assert bad not in text, bad


def test_daily_log_missing(s):
    d = call(s, "daily_log")
    assert d["ok"] is False and d["reasons"] == ["not_found"]


def test_review_list_codes_and_flags(s):
    item(s, ITEM_A)
    item(s, ITEM_B, passed=False, reasons=["listing copy has review terms: nurse",
                                           "hold: needs_a_look (order rh-20261021-0001)"])
    d = call(s, "review_list")
    assert d["ok"] and d["count"] == 2
    a, b = sorted(d["items"], key=lambda x: x["item_id"])
    assert a["item_id"] == ITEM_A and a["niche"] == "coloring_pages" and a["theme"] == "axolotl garden"
    assert a["reasons"] == ["trademark"] and a["thumbnail"] == "mockups/mockup-01.jpg"
    assert a["approvable"] is True and a["qa_passed"] is True and a["drafted"] is False
    assert b["reasons"] == ["listing_review_terms", "needs_a_look"] and b["approvable"] is False
    assert "Winnie" not in json.dumps(d) and "nurse" not in json.dumps(d)
    assert call(s, "review_list", {"status": "approved"})["count"] == 0
    assert call(s, "review_list", {"limit": 1})["more"] == 1


def _png(w=1200, h=900) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (200, 50, 50, 128)).save(buf, "PNG")
    return buf.getvalue()


def test_review_preview_returns_a_small_jpeg(s):
    item(s, ITEM_A, image=_png(), mockups=["mockups/mockup-01.jpg"])
    data, res = mcp_call(s, "read", "review_preview", {"item_id": ITEM_A})
    assert data["ok"] and data["mime"] == "image/jpeg"
    img = [c for c in res.content if c.type == "image"]
    assert len(img) == 1 and img[0].mime_type == "image/jpeg"
    from PIL import Image

    with Image.open(io.BytesIO(base64.b64decode(img[0].data))) as im:
        assert im.format == "JPEG" and max(im.size) <= tools.PREVIEW_PX


def test_review_preview_refuses_escapes_and_links(s, tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(_png(10, 10))
    item(s, ITEM_A, mockups=["../../secret.png", "mockups/link.png"])
    os.symlink(secret, work_root(s) / ITEM_A / "mockups" / "link.png")
    assert call(s, "review_preview", {"item_id": ITEM_A})["reasons"] == ["no_preview"]   # ".." never listed
    assert call(s, "review_preview", {"item_id": ITEM_A, "index": 1})["reasons"] == ["no_preview"]
    assert call(s, "review_preview", {"item_id": "../" + ITEM_A})["reasons"] == ["bad_argument"]
    assert call(s, "review_preview", {"item_id": "2026-10-20-nothing-here"})["reasons"] == ["not_found"]
    item(s, ITEM_B, image=b"not an image")
    assert call(s, "review_preview", {"item_id": ITEM_B})["reasons"] == ["no_preview"]


def test_review_preview_never_decodes_a_huge_image(s, monkeypatch):
    """A small file with huge dimensions (a decompression bomb) is "no preview": only its header is read."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("1", (8000, 6000)).save(buf, "PNG")            # 48 megapixels, a few kB on disk
    item(s, ITEM_A, image=buf.getvalue())
    loads = []
    monkeypatch.setattr(Image.Image, "thumbnail", lambda self, *a, **k: loads.append(self.size))
    assert call(s, "review_preview", {"item_id": ITEM_A})["reasons"] == ["no_preview"] and loads == []


def test_briefs_summary_aggregated_only(s):
    brief_file(s)
    brief_file(s, "coloring_pages", "2026-10-13", briefs=[{"brief_id": "coloring_pages-2026-10-13-01",
                                                          "theme": "old theme", "pack_sizes": [5]}])
    d = call(s, "briefs_summary")
    assert d["ok"] and len(d["niches"]) == 1
    n = d["niches"][0]
    assert n["niche"] == "coloring_pages" and n["date"] == BRIEF_DAY and n["age_days"] == 1 and n["stage"] == "discovery"
    assert [b["brief_id"] for b in n["briefs"]] == [f"coloring_pages-{BRIEF_DAY}-01", f"coloring_pages-{BRIEF_DAY}-02"]
    assert n["briefs"][0] == {"brief_id": f"coloring_pages-{BRIEF_DAY}-01", "niche": "coloring_pages",
                              "theme": "axolotl garden", "sizes": [5, 12, 20], "opportunity": 0.77,
                              "needs_review": False}
    text = json.dumps(d)
    assert EXPORT not in text and "keywords" not in text and "SecretShop" not in text and "evidence" not in text
    assert call(s, "briefs_summary", {"niche": "stickers"})["niches"] == []
    assert call(s, "briefs_summary", {"niche": "not_a_niche"})["reasons"] == ["bad_argument"]


def test_capacity(s):
    item(s, ITEM_A)
    d = call(s, "capacity")
    cap = d["capacity"]
    assert d["ok"] and cap["facts"]["review_queue"] == 1 and cap["facts"]["cap"] == 25.0
    assert cap["thresholds"]["max_review_queue"] == 30 and isinstance(cap["reasons"], list)


def test_plan_status_and_facts(s):
    brief_file(s)
    plan_dir = Path(s["plan"]["dir"])
    (plan_dir / NEXT_WEEK).mkdir(parents=True)
    (plan_dir / "state.json").write_text(json.dumps({"schema": "plan-state/v1", "approved_count": 2,
                                                     "mode": "approve", "status": "waiting", "file": EXPORT,
                                                     "note": f"from {SHOP}",
                                                     "deadline": "2026-10-27T20:00:00-04:00"}))
    (plan_dir / NEXT_WEEK / "facts.json").write_text(json.dumps({
        "plan_week": NEXT_WEEK, "money_left": 21.5, "kdp_slots_left": 2,
        "briefs": [{"brief_id": f"coloring_pages-{BRIEF_DAY}-01", "theme": "axolotl garden", "sizes": [5, 20],
                    "opportunity": 0.77, "keywords": ["x"], "evidence": {"shop_count": 7}}],
        "sources": [{"file": EXPORT}], "shop_names": [SHOP], "title": f"{SHOP} title"}))
    (plan_dir / NEXT_WEEK / "plan.json").write_text(json.dumps({
        "plan_week": NEXT_WEEK, "created_by": "botsy", "cost_usd": 3.4,
        "lines": [{"niche": "coloring_pages", "brief_id": f"coloring_pages-{BRIEF_DAY}-01", "sizes": [5, 20],
                   "theme": "axolotl garden"}]}))
    res = orders.submit({"kind": "weekly_plan", "created_by": "botsy", "payload": {
        "plan_week": NEXT_WEEK, "lines": [{"niche": "coloring_pages", "brief_id": f"coloring_pages-{BRIEF_DAY}-01",
                                           "sizes": [5, 20]}]}}, s, now=NOW)
    assert res["ok"], res
    d = call(s, "plan_status")
    assert d["ok"] and d["this_week"] == THIS_WEEK and d["next_week"] == NEXT_WEEK
    nxt = d["weeks"][1]
    assert nxt["plan"]["order_id"] == res["order_id"] and "needs_code" not in nxt["plan"]
    assert nxt["plan"]["needs_approval"] is True and nxt["plan"]["status"] == "waiting_approval"
    assert nxt["plan"]["packs"] == 2 and nxt["plan"]["lines"][0]["sizes"] == [5, 20] and nxt["versions"] == 1
    assert nxt["draft"]["lines"][0]["theme"] == "axolotl garden" and nxt["draft"]["cost_usd"] == 3.4
    assert d["state"] == {"approved_count": 2, "mode": "approve", "status": "waiting",
                          "deadline": "2026-10-27T20:00:00-04:00"}
    f = call(s, "plan_facts")
    assert f["ok"] and f["plan_week"] == NEXT_WEEK and f["money"]["cap"] == 25.0
    assert f["approvals"] == {"approved_plans": 2, "approve_each": True}
    assert f["facts"]["money_left"] == 21.5 and f["facts"]["briefs"][0]["theme"] == "axolotl garden"
    assert f["briefs"][0]["briefs"][0]["opportunity"] == 0.77
    text = json.dumps([d, f])
    for bad in (EXPORT, SHOP, "keywords", "evidence", "sources"):
        assert bad not in text, bad
    assert call(s, "plan_facts", {"week": "2026-W99"})["reasons"] == ["bad_argument"]


def test_orders_recent(s):
    orders.submit({"kind": "pause", "created_by": "user", "payload": {"reason_code": "incident"}}, s, now=NOW)
    d = call(s, "orders_recent")
    assert d["ok"] and d["count"] == 1
    o = d["orders"][0]
    assert o["kind"] == "pause" and o["created_by"] == "user" and o["status"] == "applied"
    assert o["payload"] == {"reason_code": "incident"} and o["order_id"].startswith("pa-")
    assert call(s, "orders_recent", {"kind": "lower_cap"})["count"] == 0
    assert call(s, "orders_recent", {"status": "bogus"})["reasons"] == ["bad_argument"]


def test_events_recent_signed_fixed_fields(s, monkeypatch):
    monkeypatch.setenv(events.KEY_ENV, EVENT_KEY)
    events.emit("export_received", settings=s, file=EXPORT, tool="everbee", rows=250, shape="listing")
    events.emit("export_rejected", settings=s, name=EXPORT, reason=f"bad header from {SHOP}")
    events.emit("error", settings=s, where=f"listing[{ITEM_A}]", message=f"{SHOP} {LISTING_URL} {API_SECRET}")
    events.emit("held_for_review", settings=s, item_id=ITEM_A, reasons="trademark: Winnie the Pooh", dry_run=True)
    events.emit("draft_created", settings=s, item_id=ITEM_A, listing_id=4400000001, title=f"{SHOP} title",
                dry_run=False)
    events.emit("plan_posted", settings=s, plan_week=NEXT_WEEK, packs=9, cost_usd=6.4, text="free text here")
    events.emit("something_else", settings=s, value=1)
    d = call(s, "events_recent", clock=None)          # the journal stamps the real time
    kinds = [e["kind"] for e in d["events"]]
    assert kinds == ["export_received", "export_rejected", "error", "held_for_review", "draft_created", "plan_posted"]
    assert d["signed"] is True and all(events.verify(e, key=EVENT_KEY) for e in d["events"])
    by = {e["kind"]: e for e in d["events"]}
    assert by["export_received"]["rows"] == 250 and "file" not in by["export_received"]
    assert set(by["export_rejected"]) == {"ts", "kind", "sig"}
    assert by["error"]["stage"] == "listing" and by["error"]["item_id"] == ITEM_A and "message" not in by["error"]
    assert by["held_for_review"]["reasons"] == ["trademark"]
    assert "listing_id" not in by["draft_created"] and "title" not in by["draft_created"]
    assert by["plan_posted"]["packs"] == 9 and "text" not in by["plan_posted"]
    text = json.dumps(d)
    for bad in (EXPORT, SHOP, "etsy.com", API_SECRET, "Winnie", "4400000001", "free text"):
        assert bad not in text, bad
    only = call(s, "events_recent", {"kinds": ["plan"]}, clock=None)
    assert [e["kind"] for e in only["events"]] == ["plan_posted"]


def test_events_recent_signatures_cover_exactly_what_hermes_gets(s, monkeypatch):
    """The last scrub runs before signing, so a scrubbed field never breaks a signature."""
    monkeypatch.setenv(events.KEY_ENV, EVENT_KEY)
    monkeypatch.setenv("BOTSY_TEST_SECRET_TOKEN", "abcdefgh1234")
    events.emit("plan_posted", settings=s, plan_week=NEXT_WEEK, ref="abcdefgh1234", packs=3)
    d = call(s, "events_recent", clock=None)
    (e,) = d["events"]
    assert "abcdefgh1234" not in json.dumps(d) and e["packs"] == 3
    assert events.verify(e, key=EVENT_KEY)


def test_plan_start_times_follow_new_york_dst(s):
    """Monday 8:00 am New York is 12:00 UTC before the DST change (2026-11-01) and 13:00 after."""
    made = "2026-10-21T15:00:00+00:00"
    before = tools._plan_view({"id": "wp-20261021-0001", "kind": "weekly_plan", "created_at": made,
                               "payload": {"plan_week": "2026-W44"}}, s)
    after = tools._plan_view({"id": "wp-20261021-0002", "kind": "weekly_plan", "created_at": made,
                              "payload": {"plan_week": "2026-W45"}}, s)
    assert (before["start_at"], before["start_at_local"]) == ("2026-10-26T12:00:00+00:00",
                                                              "2026-10-26T08:00:00-04:00")
    assert (after["start_at"], after["start_at_local"]) == ("2026-11-02T13:00:00+00:00",
                                                            "2026-11-02T08:00:00-05:00")


def test_events_recent_without_key_is_marked_unsigned(s, monkeypatch):
    monkeypatch.delenv(events.KEY_ENV, raising=False)
    events.emit("draft_created", settings=s, item_id=ITEM_A, dry_run=True)
    d = call(s, "events_recent", clock=None)
    assert d["ok"] and d["count"] == 1 and d["signed"] is False and "sig" not in d["events"][0]


def test_read_tools_write_nothing(s, tmp_path, monkeypatch):
    from control import trend

    monkeypatch.delenv(trend.KEY_ENV, raising=False)
    monkeypatch.setattr(trend, "ENV_FILE", tmp_path.parent / "no-dotenv")
    monkeypatch.setattr(trend, "_http_get", lambda *a, **kw: pytest.fail("no network in tests"))
    item(s, ITEM_A)
    brief_file(s)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    for t in tools.tools_for("read"):
        args = {"item_id": ITEM_A} if t.name in ("review_preview", "view_item_images") else {}
        if t.name == "trend_search":
            args = {"query": "cozy mushrooms"}           # no TINYFISH_API_KEY: not_configured, nothing counted
        call(s, t.name, args)
    after = sorted(str(p) for p in tmp_path.rglob("*"))
    assert after == before


# ---------------------------------------------------------------- the leak test

def test_read_tools_never_leak(s, monkeypatch, tmp_path):
    """Research inputs with shop names and listing links, a brief that names its export, a
    status file, daily log, plan files and events that carry them, and secrets in the environment
    and in .env: no read tool returns any of it."""
    env = tmp_path / "dotenv"
    env.write_text(f"ETSY_KEYSTRING={ENV_SECRET}\nANTHROPIC_API_KEY={API_SECRET}\n")
    monkeypatch.setattr(tools, "ENV_FILE", env)
    from control import trend

    monkeypatch.setattr(trend, "ENV_FILE", env)          # no TINYFISH_API_KEY: trend_search is not configured
    monkeypatch.delenv(trend.KEY_ENV, raising=False)
    monkeypatch.setattr(trend, "_http_get", lambda *a, **kw: pytest.fail("no network in tests"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_SECRET)
    monkeypatch.setenv(events.KEY_ENV, EVENT_KEY)
    inputs = Path(s["research"]["inputs_dir"])
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / EXPORT).write_text(f"Product Name,Shop Name,Link\nFrog Stickers,{SHOP},{LISTING_URL}\n")
    brief_file(s)
    item(s, ITEM_A, image=_png(50, 50), mockups=["mockups/mockup-01.jpg"])
    outbox(s).mkdir(parents=True, exist_ok=True)
    (outbox(s) / "daily-log.txt").write_text(DAILY_LOG)
    (outbox(s) / "status.json").write_text(json.dumps({
        "schema": "status/v1", "ticks": [{"ingest": {"accepted": [str(inputs / EXPORT)]},
                                          "research": {"brief_files": [str(inputs)], "notes": [SHOP]},
                                          "errors": [f"{SHOP} {LISTING_URL} {API_SECRET}"]}],
        "last_research": {"files": [EXPORT]}}))
    plan_dir = Path(s["plan"]["dir"])
    (plan_dir / NEXT_WEEK).mkdir(parents=True)
    (plan_dir / "state.json").write_text(json.dumps({"mode": "approve", "inputs": str(inputs), "shop": SHOP,
                                                     "last": EXPORT, "url": LISTING_URL, "top": SHOP}))
    (plan_dir / NEXT_WEEK / "facts.json").write_text(json.dumps({"x": str(inputs / EXPORT), "y": LISTING_URL,
                                                                 "z": SHOP + " " + API_SECRET}))
    for kind, f in (("export_received", {"file": EXPORT, "path": str(inputs / EXPORT), "tool": "everbee"}),
                    ("error", {"where": "research", "message": f"{SHOP} {inputs} {API_SECRET}"}),
                    ("listing_written", {"item_id": ITEM_A, "title": f"{SHOP} copy"}),
                    ("search_list_written", {"path": str(inputs)}),
                    ("notification_held", {"kinds": ["error"], "text": f"{SHOP} {LISTING_URL}"})):
        events.emit(kind, settings=s, **f)
    outputs = []
    for t in tools.tools_for("read"):
        variants = [{}]
        if t.name == "review_preview":
            variants = [{"item_id": ITEM_A}]
        if t.name == "view_item_images":
            variants = [{"item_id": ITEM_A}, {"item_id": ITEM_A, "kind": "mockups"}]
        if t.name == "review_list":
            variants = [{"status": "all"}]
        for args in variants:
            data, res = mcp_call(s, "read", t.name, args, clock=None)
            outputs.append(json.dumps(data))
            outputs += [c.text for c in res.content if c.type == "text"]
    blob = "\n".join(outputs)
    for bad in (SHOP, LISTING_URL, "etsy.com/listing", EXPORT, "deadbeef", str(inputs), "research_inputs",
                str(tmp_path), API_SECRET, ENV_SECRET, EVENT_KEY, "Winnie", "Pooh", "Shop Name"):
        assert bad not in blob, bad
    assert "https://" not in blob and ".csv" not in blob


def test_reason_codes_never_return_text():
    assert tools.reason_codes(["trademark: Mickey Mouse", "medical words: cure", "a long sentence about stuff"]) \
        == ["trademark", "health_review", "other"]
    assert tools.reason_codes("item data has review terms: nurse") == ["item_data_review_terms"]
    assert tools.reason_codes(None) == []


def test_scrubber_keeps_zone_names_and_own_files():
    sc = tools._Scrubber()
    assert sc.text("New York time (America/New_York); see status.txt") == \
        "New York time (America/New_York); see status.txt"
    # Only real zone names and a few fixed words keep their slash: folder-like pairs (an export
    # folder named after a shop, Botsy's own data folders) are scrubbed.
    assert sc.text(f"from Exports/{SHOP} and data/plans, image/jpeg, Europe/London, Foo/Bar") == \
        "from [path] and [path], image/jpeg, Europe/London, [path]"
    assert sc.text("read /opt/botsy/data/research_inputs/a.csv and data/x/y.json") == "read [path] and [path]"
    assert sc.text("file erank-2026.csv at https://x.example/a?b") == "file [file] at [link]"
