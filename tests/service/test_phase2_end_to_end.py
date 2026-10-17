"""Phase 2 of the server, offline in tmp_path, with the real code at every step
(docs/SERVER.md sections 8-14):

1. Three weekly rounds, 7 days apart: demo exports (a new seed each week) land in the
   inbox and `python -m service tick --date <day>` ingests, researches in the discovery
   stage, writes history snapshots and runs the pipeline as a dry run.
   - History: every theme records fresh_rows, and the timeline counts 3 weeks seen.
   - Theme cooldown: weeks 2 and 3 skip the themes week 1 already made, so no niche gets
     two items of one theme.
   - The event journal holds only our own data (no demo shop names, links or titles).
2. `python -m service digest` writes the daily log (CRLF) and posts it to Discord
   (a fake webhook session records the posts).
3. `python -m service cleanup` removes an old rejected upload, an old export (its sha256
   stays in the ingest ledger, marked deleted) and an old practice item.
4. Spending over the monthly cap, then a live forced tick: only qa and publish run,
   budget_reached is recorded once, and Discord is told.
5. 20 real Etsy drafts (fake publish.json records): the next tick switches research to
   the refined stage (stage_changed), and the 3 weeks of history qualify refined briefs.

Everything is invented (demo exports, fake shops and links); no network, no root.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil

import pytest
import yaml

from core import events, spend
from research import history
from research.demo import write_demo_inputs
from service import __main__ as cli
from service import notify
from service.paths import service_paths

from ._support import make_settings

pytestmark = pytest.mark.slow

WEBHOOK = "https://discord.com/api/webhooks/123456789/fake-token-phase2"
NICHES = ("stickers", "coloring_pages", "activity_sheets")
# Identifiers from the demo exports that must never reach the journal or the daily log.
COMPETITOR_BITS = ("DemoShop", "etsy.com", "etsystatic", "9100000", "il_570xN")


class FakeSession:
    """Stands in for requests: records each webhook post and answers 204."""

    def __init__(self):
        self.posts: list[dict] = []

    def post(self, url, **kw):
        assert url == WEBHOOK
        self.posts.append(kw)
        return type("Response", (), {"status_code": 204})()

    def texts(self) -> list[str]:
        out = []
        for kw in self.posts:
            if "json" in kw:
                out.append(kw["json"]["content"])
            else:
                out.append(json.loads(kw["data"]["payload_json"])["content"])
        return out


@pytest.fixture
def world(tmp_path, monkeypatch):
    session = FakeSession()
    real_send = notify.send
    monkeypatch.setattr(notify, "send", lambda text, **kw: real_send(text, **{**kw, "session": session}))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK)
    settings, path = make_settings(
        tmp_path,
        pipeline={"max_items_per_run": 2},
        service={"dry_run_keep_days": 30},
        spend={"monthly_cap_usd": 1.0},
        notify={"discord": {"enabled": True}, "quiet_hours": []},
        vectorize={"stickers": {"background_removal": "floodfill"}},
    )
    return settings, path, service_paths(settings), session


def _cli(path, *argv) -> int:
    return cli.main([*argv, "--settings", str(path)])


def _save(settings, path) -> None:
    path.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")


def _upload(paths, tmp_path, seed: int, week: int) -> None:
    for p in write_demo_inputs(tmp_path / f"demo-{seed}", listings_per_theme=16, seed=seed):
        shutil.copyfile(p, paths.incoming / f"w{week}-{p.name}")


def _last_tick(paths) -> dict:
    return json.loads((paths.outbox / "status.json").read_text(encoding="utf-8"))["ticks"][-1]


def _events(settings) -> list[dict]:
    return events.read(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2), settings=settings)


def _briefs(paths, day) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(paths.briefs.glob(f"*-{day}.json"))]


def _items(root):
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if (d / "manifest.json").is_file():
            yield d, json.loads((d / "manifest.json").read_text(encoding="utf-8"))


def test_three_weeks_cap_cleanup_and_refined_stage(world, tmp_path):
    settings, path, paths, session = world
    dry = tmp_path / "dry"
    today = dt.date.today()
    weeks = [(today - dt.timedelta(days=d)).isoformat() for d in (14, 7, 0)]

    # 1. three weekly rounds in the discovery stage
    for n, day in enumerate(weeks, 1):
        _upload(paths, tmp_path, seed=100 + n, week=n)
        assert _cli(path, "tick", "--date", day) == 0, _last_tick(paths)["errors"]
        rec = _last_tick(paths)
        assert rec["research"]["ran"] and rec["research"]["stage"] == "discovery"
        docs = _briefs(paths, day)
        assert sorted(d["niche"] for d in docs) == sorted(NICHES)
        assert all(d["stage"] == "discovery" and d["min_shops_per_pattern"] >= 3 for d in docs)
        assert all(b["evidence"]["listing_count"] >= 10 for d in docs for b in d["briefs"])
        assert rec["run"]["generated"] == 2
        if n > 1:
            assert rec["run"]["cooldown"] >= 1                      # the themes made before were skipped
        # Theme cooldown is per pack size (docs/PACKS.md section 3): a later week may make
        # another size of a theme made before, never the same size again.
        themes = [(m["niche"], m["theme"], (m.get("pack") or {}).get("size")) for _, m in _items(dry)]
        assert len(themes) == 2 * n and len(set(themes)) == len(themes)

    for niche in NICHES:
        snaps = history.load(paths.history, niche)
        assert [s["date"] for s in snaps] == weeks
        assert all(t.get("fresh_rows", 0) >= 3 for s in snaps for t in s["themes"] if t["qualified"])
        timeline = history.timeline(paths.history, niche, settings=settings)
        assert max(v["weeks_seen"] for v in timeline.values()) == 3

    journal = (tmp_path / "data" / "events.jsonl").read_text(encoding="utf-8")
    kinds = {e["kind"] for e in _events(settings)}
    assert {"export_received", "briefs_written", "item_generated", "listing_written", "draft_created"} <= kinds
    for bit in COMPETITOR_BITS + ("fake-token-phase2",):
        assert bit not in journal, bit

    # 2. the daily log
    assert _cli(path, "digest", "--date", weeks[-1]) == 0
    log = (paths.outbox / "daily-log.txt").read_bytes().decode("utf-8")
    assert "\r\n" in log and "Botsy daily log" in log and "Stage: discovery" in log
    assert "Practice drafts (test mode, nothing sent to Etsy): 6" in log
    for bit in COMPETITOR_BITS:
        assert bit not in log, bit
    assert any("Botsy daily log" in t for t in session.texts())

    # 3. cleanup of old files
    old_rejected = paths.rejected / "2026-01-02-bank.csv"
    old_rejected.parent.mkdir(parents=True, exist_ok=True)
    old_rejected.write_text("x", encoding="utf-8")
    (paths.rejected / "2026-01-02-bank.csv.reason.txt").write_text("not an export", encoding="utf-8")
    old_export = paths.inputs / "everbee-2026-01-05-0badc0de.csv"
    old_export.write_text("Product Name,Shop Name\nx,y\n", encoding="utf-8")
    ledger_path = paths.inputs / ".ingested.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["0" * 64] = {"file": old_export.name, "received": "2026-01-05T08:00:00+00:00", "original_name": "old.csv"}
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    old_item = dry / "2026-01-05-stickers-old-theme-abcd"
    old_item.mkdir()
    (old_item / "manifest.json").write_text(json.dumps({
        "item_id": old_item.name, "created": "2026-01-05", "niche": "stickers", "theme": "old theme",
        "generator": {"name": "stickers", "dry_run": True}}), encoding="utf-8")
    assert _cli(path, "cleanup") == 0
    assert not old_rejected.exists() and not old_export.exists() and not old_item.exists()
    assert json.loads(ledger_path.read_text(encoding="utf-8"))["0" * 64]["deleted"]
    assert len(list(_items(dry))) == 6                              # this month's practice items stay

    # 4. over the spending cap, live: paid stages are skipped and Discord is told
    spend.record("google", "gemini-nano-banana-2.1", "image", stage="generate", item_id="fake-item", images=10,
                 image_size="4K", settings=settings)
    settings["service"]["live"] = True
    _save(settings, path)
    posted = len(session.posts)
    assert _cli(path, "tick", "--force", "--date", weeks[-1]) == 0
    rec = _last_tick(paths)
    assert any("paid steps skipped" in note for note in rec["run"]["notes"])
    runs = sorted((tmp_path / "output" / "runs").glob("run-*.json"))
    assert json.loads(runs[-1].read_text(encoding="utf-8"))["stages"] == ["qa", "publish"]
    assert any("Monthly spending cap reached" in t for t in session.texts()[posted:])
    assert _cli(path, "tick", "--force", "--date", weeks[-1]) == 0
    assert [e["kind"] for e in _events(settings)].count("budget_reached") == 1

    # 5. 20 real Etsy drafts: research switches to the refined stage
    out = tmp_path / "output"
    for n in range(20):
        d = out / f"{weeks[-1]}-stickers-fake-draft-{n:02d}-{n:04x}"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({
            "item_id": d.name, "niche": "stickers", "theme": f"fake {n}", "shop_id": "main",
            "status": "drafted", "created": weeks[-1]}), encoding="utf-8")
        (d / "publish.json").write_text(json.dumps({"listing_id": 1000 + n, "dry_run": False}), encoding="utf-8")
    posted = len(session.posts)
    assert _cli(path, "tick", "--force", "--date", weeks[-1]) == 0
    rec = _last_tick(paths)
    assert rec["research"]["ran"] and rec["research"]["stage"] == "refined"
    changed = [e for e in _events(settings) if e["kind"] == "stage_changed"]
    assert [(e["from"], e["to"], e["drafts"]) for e in changed] == [("discovery", "refined", 20)]
    docs = _briefs(paths, weeks[-1])
    assert docs and all(d["stage"] == "refined" and d["min_weeks_seen"] == 3 for d in docs)
    refined = [b for d in docs for b in d["briefs"]]
    assert refined, "three weeks of fresh exports should qualify refined briefs"
    assert all(b["evidence"]["weeks_seen"] >= 3 and b["evidence"]["listing_count"] >= 15 for b in refined)
    assert any("refined stage" in t for t in session.texts()[posted:])

    assert _cli(path, "digest", "--date", weeks[-1]) == 0
    log = (paths.outbox / "daily-log.txt").read_bytes().decode("utf-8")
    assert "changed from discovery to refined" in log and "The cap is reached" in log
