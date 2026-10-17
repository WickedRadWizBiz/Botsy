"""service.tick: events, the one Discord message per tick, the spending cap, the research
stage and cleanup (docs/SERVER.md sections 8-14). Fake research and pipeline, all in tmp_path."""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import pipeline.run as prun
import research
import research.stages
from core import events, spend
from service import notify, status, tick
from service.paths import service_paths

from ._support import (DATE, SHOP_NICHES, FakePipeline, FakeResearch, everbee_csv, fake_summary,
                       make_settings, upload)

N = len(SHOP_NICHES)            # research runs once per shop niche

FROGS = f"{DATE}-stickers-frogs-aaaa"
CATS = f"{DATE}-stickers-cats-bbbb"
BAD = f"{DATE}-stickers-bad-dddd"


@pytest.fixture
def sent(monkeypatch):
    """notify.send replaced: records (text, kwargs) and says it was sent."""
    calls = []

    def fake_send(text, **kw):
        calls.append((text, kw))
        return True

    monkeypatch.setattr(notify, "send", fake_send)
    return calls


@pytest.fixture
def no_cleanup(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "service.cleanup",
                        types.SimpleNamespace(run=lambda settings, now=None: calls.append(settings) or {}))
    return calls


@pytest.fixture
def stage(monkeypatch):
    """research.stages.resolve_stage replaced; set stage.value to change it."""
    holder = types.SimpleNamespace(value="discovery", calls=0)

    def resolve(settings, *, out_root=None):
        holder.calls += 1
        if isinstance(holder.value, Exception):
            raise holder.value
        return holder.value

    monkeypatch.setattr(research.stages, "resolve_stage", resolve)
    return holder


def setup(tmp_path, live=False, **sections):
    settings, _ = make_settings(tmp_path, service={"live": live}, **sections)
    return settings, service_paths(settings)


def all_events(settings) -> list[dict]:
    return events.read(datetime.now(timezone.utc) - timedelta(days=1), settings=settings)


def kinds(settings) -> list[str]:
    return [e["kind"] for e in all_events(settings)]


def item_files(settings, dry=True):
    """Manifest, listing and publish records for the fake summary's items."""
    root = Path(settings["pipeline"]["dry_run_output_dir"] if dry else settings["paths"]["output_dir"])
    for item_id, theme in ((FROGS, "frogs"), (CATS, "cats"), (BAD, "bad")):
        d = root / item_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps({"item_id": item_id, "niche": "stickers", "theme": theme}))
    (root / FROGS / "listing.json").write_text(json.dumps({"title": "Frog Stickers Pack"}))
    (root / FROGS / "publish.json").write_text(json.dumps({"listing_id": 4321, "dry_run": dry}))


def test_tick_records_events_and_sends_one_message(tmp_path, monkeypatch, sent, no_cleanup, stage):
    r, p = FakeResearch(), FakePipeline()
    monkeypatch.setattr(research, "run", r)
    monkeypatch.setattr(prun, "run", p)
    settings, paths = setup(tmp_path)
    item_files(settings)
    upload(paths.incoming, "everbee export.csv", everbee_csv())
    upload(paths.incoming, "bank.csv", b"Date,Amount\n1,2\n")
    rec = tick.tick(settings, date=DATE)

    assert all(c["stage"] == "discovery" for c in r.calls)          # the resolved stage is passed on
    assert rec["research"]["stage"] == "discovery"
    got = all_events(settings)
    by = {}
    for e in got:
        by.setdefault(e["kind"], []).append(e)
    (received,) = by["export_received"]
    assert (received["tool"], received["shape"], received["rows"]) == ("everbee", "listing", 12)
    assert by["export_rejected"][0]["name"] == "bank.csv" and by["export_rejected"][0]["reason"]
    assert [(e["niche"], e["stage"], e["count"]) for e in by["briefs_written"]] == [("stickers", "discovery", 2)]
    assert {e["item_id"]: e["theme"] for e in by["item_generated"]} == {FROGS: "frogs", CATS: "cats", BAD: "bad"}
    assert by["qa_failed"] == [{**by["qa_failed"][0], "item_id": BAD, "checks": ["pdf_blank_pages"]}]
    assert {e["item_id"] for e in by["listing_written"]} == {FROGS, CATS}
    (draft,) = by["draft_created"]
    assert (draft["item_id"], draft["listing_id"], draft["title"], draft["dry_run"]) == \
        (FROGS, 4321, "Frog Stickers Pack", True)
    (held,) = by["held_for_review"]
    assert held["item_id"] == CATS and held["dry_run"] is True and "needs review" in held["reasons"]
    assert "stage_changed" not in by                                 # the first stage is not a change
    assert no_cleanup == [settings]                                  # cleanup ran at the end

    # One combined Discord message, only with the kinds in notify.discord.events.
    (text, kw), = sent
    assert "1 practice draft made (test mode, nothing sent to Etsy): Frog Stickers Pack" in text
    assert "1 item is waiting for your review" in text
    assert f"botsy qa review approve {CATS} --dry-run" in text
    assert "1 upload was not used: bank.csv" in text
    assert "item_generated" not in kw["kinds"] and {"draft_created", "held_for_review", "export_rejected"} <= \
        set(kw["kinds"])
    assert kw["settings"] is settings

    # A second run of the same items: the review hold is recorded again (the daily log lists
    # every item still waiting) but not announced again.
    sent.clear()
    tick.tick(settings, force=True, date=DATE)
    assert [e["item_id"] for e in all_events(settings) if e["kind"] == "held_for_review"] == [CATS, CATS]
    assert sent and "waiting for your review" not in sent[0][0]
    state = json.loads((tmp_path / "data" / ".service_state.json").read_text())
    assert state["stage"] == "discovery" and f"dry:{CATS}" in state["held"]


def test_live_drafts_message_lists_titles(tmp_path, monkeypatch, sent, no_cleanup, stage):
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, paths = setup(tmp_path, live=True)
    item_files(settings, dry=False)
    tick.tick(settings, force=True, date=DATE)
    text = sent[0][0]
    assert "1 new draft is waiting in Etsy: Frog Stickers Pack" in text
    assert f"botsy qa review approve {CATS}\n" in text + "\n" and "--dry-run" not in text


def test_nothing_new_means_no_message(tmp_path, monkeypatch, sent, no_cleanup, stage):
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, _ = setup(tmp_path)
    rec = tick.tick(settings, date=DATE)
    assert sent == [] and kinds(settings) == [] and rec["exit_code"] == 0
    assert stage.calls == 0                                          # an idle check does not even look


def test_errors_become_events_and_an_urgent_message(tmp_path, monkeypatch, sent, no_cleanup, stage):
    monkeypatch.setattr(research, "run", FakeResearch(fail={"coloring_pages"}))
    monkeypatch.setattr(prun, "run", FakePipeline(summary=lambda d, s, dry: {
        **fake_summary(d, s, dry), "items": {}, "awaiting_review": {}}))
    settings, _ = setup(tmp_path)
    tick.tick(settings, force=True, date=DATE)
    errors = [e for e in all_events(settings) if e["kind"] == "error"]
    assert [(e["where"], e["message"]) for e in errors] == \
        [("research[coloring_pages]", "RuntimeError: boom in coloring_pages")]
    (text, kw), = sent
    assert "Problems: 1" in text and "research[coloring_pages]: RuntimeError: boom" in text
    assert "error" in kw["kinds"]


def test_budget_reached_skips_paid_stages_but_still_researches(tmp_path, monkeypatch, sent, no_cleanup, stage):
    r, p = FakeResearch(), FakePipeline()
    monkeypatch.setattr(research, "run", r)
    monkeypatch.setattr(prun, "run", p)
    settings, paths = setup(tmp_path, live=True, spend={"monthly_cap_usd": 1})
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=300_000, settings=settings)
    upload(paths.incoming, "export.csv", everbee_csv())
    rec = tick.tick(settings, date=DATE)

    assert len(rec["ingest"]["accepted"]) == 1 and r.calls                # ingest + research still run
    assert p.calls[0]["stages"] == ("qa", "publish")                      # no generate, no listing
    assert any("paid steps skipped" in n for n in rec["run"]["notes"])
    reached = [e for e in all_events(settings) if e["kind"] == "budget_reached"]
    assert len(reached) == 1 and reached[0]["cap"] == 1.0 and reached[0]["month_to_date"] == 1.2
    assert "spend_warning" not in kinds(settings)                         # the cap itself was reached
    assert "Monthly spending cap reached: $1.20 of $1.00" in sent[0][0]
    assert "budget_reached" in sent[0][1]["kinds"]
    assert "paid steps skipped" in (paths.outbox / status.TEXT_NAME).read_text(encoding="utf-8")

    tick.tick(settings, force=True, date="2026-10-17")
    assert kinds(settings).count("budget_reached") == 1                   # once per month
    assert p.calls[1]["stages"] == ("qa", "publish")

    settings["spend"]["monthly_cap_usd"] = 50                             # the user raised the cap
    tick.tick(settings, force=True, date="2026-10-18")
    assert p.calls[2]["stages"] == tick.RUN_STAGES


def test_test_mode_is_not_paused_by_the_cap(tmp_path, monkeypatch, sent, no_cleanup, stage):
    monkeypatch.setattr(research, "run", FakeResearch())
    p = FakePipeline()
    monkeypatch.setattr(prun, "run", p)
    settings, _ = setup(tmp_path, spend={"monthly_cap_usd": 1})
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=300_000, settings=settings)
    tick.tick(settings, force=True, date=DATE)
    assert p.calls[0]["stages"] == tick.RUN_STAGES                        # dry runs cost nothing


def test_spend_warning_once_per_month(tmp_path, monkeypatch, sent, no_cleanup, stage):
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, _ = setup(tmp_path, live=True, spend={"monthly_cap_usd": 10, "warn_at": 0.8})
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=2_100_000, settings=settings)
    tick.tick(settings, force=True, date=DATE)
    tick.tick(settings, force=True, date=DATE)
    warnings = [e for e in all_events(settings) if e["kind"] == "spend_warning"]
    assert len(warnings) == 1 and warnings[0]["pct"] == 0.84 and warnings[0]["cap"] == 10.0
    assert "Spending warning: $8.40 of $10.00 used this month (84%)." in sent[0][0]


def test_a_cap_that_is_not_a_number_pauses_paid_steps(tmp_path, monkeypatch, sent, no_cleanup, stage):
    monkeypatch.setattr(research, "run", FakeResearch())
    p = FakePipeline()
    monkeypatch.setattr(prun, "run", p)
    settings, _ = setup(tmp_path, live=True, spend={"monthly_cap_usd": "lots"})
    rec = tick.tick(settings, force=True, date=DATE)
    assert p.calls[0]["stages"] == ("qa", "publish")
    assert any(e.startswith("spend: ValueError") for e in rec["errors"])


def test_budget_reached_during_the_run_is_reported(tmp_path, monkeypatch, sent, no_cleanup, stage):
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline(summary=lambda d, s, dry: {
        **fake_summary(d, s, dry), "budget_reached": True}))
    settings, _ = setup(tmp_path, live=True, spend={"monthly_cap_usd": 100})
    tick.tick(settings, force=True, date=DATE)
    assert kinds(settings).count("budget_reached") == 1
    assert "Monthly spending cap reached" in sent[0][0]


def test_stage_changed_once_and_re_research_on_change(tmp_path, monkeypatch, sent, no_cleanup, stage):
    r = FakeResearch()
    monkeypatch.setattr(research, "run", r)
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, _ = setup(tmp_path)
    tick.tick(settings, force=True, date=DATE)
    assert len(r.calls) == N and "stage_changed" not in kinds(settings)

    stage.value = "refined"                     # 20 real drafts reached since the last tick
    rec = tick.tick(settings, force=True, date="2026-10-17")
    assert len(r.calls) == 2 * N and all(c["stage"] == "refined" for c in r.calls[N:])   # inputs unchanged
    assert rec["research"]["ran"] and rec["research"]["stage"] == "refined"
    changed = [e for e in all_events(settings) if e["kind"] == "stage_changed"]
    assert [(e["from"], e["to"]) for e in changed] == [("discovery", "refined")]
    assert "drafts" in changed[0]
    assert any("refined stage" in t for t, _ in sent)

    tick.tick(settings, force=True, date="2026-10-18")
    assert len(r.calls) == 2 * N                                     # unchanged inputs, same stage
    assert kinds(settings).count("stage_changed") == 1
    doc = status.load(service_paths(settings).outbox)
    assert doc["last_research"]["stage"] == "refined"


def test_stage_that_cannot_be_worked_out_skips_research(tmp_path, monkeypatch, sent, no_cleanup, stage):
    r, p = FakeResearch(), FakePipeline()
    monkeypatch.setattr(research, "run", r)
    monkeypatch.setattr(prun, "run", p)
    settings, paths = setup(tmp_path)
    paths.briefs.mkdir(parents=True)
    (paths.briefs / f"stickers-{DATE}.json").write_text("{}", encoding="utf-8")     # from an earlier day
    stage.value = ValueError("research stage must be discovery or refined, got 'refind'")
    rec = tick.tick(settings, force=True, date=DATE)
    assert r.calls == [] and len(p.calls) == 1                       # the pipeline still runs
    assert rec["errors"][0].startswith("research stage: ValueError")
    assert any("stage could not be worked out" in n for n in rec["research"]["notes"])


def test_research_command_resolves_the_stage(tmp_path, monkeypatch, stage):
    r = FakeResearch()
    monkeypatch.setattr(research, "run", r)
    settings, paths = setup(tmp_path)
    stage.value = "refined"
    out = tick.research_all(settings, paths, DATE)
    assert out["stage"] == "refined" and {c["stage"] for c in r.calls} == {"refined"}
    assert out["written"] == [{"niche": "stickers", "file": out["brief_files"][0], "count": 2}]


def test_cleanup_results_errors_and_a_missing_module(tmp_path, monkeypatch, sent, stage):
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, paths = setup(tmp_path)
    class Result(dict):
        problems = ["practice items: not cleaned (output/dry-run is a link)"]

    monkeypatch.setitem(sys.modules, "service.cleanup", types.SimpleNamespace(
        run=lambda s, now=None: Result(rejected=[Path("a"), Path("b")], dry_run_items=3, exports=[])))
    rec = tick.tick(settings, force=True, date=DATE)
    assert rec["cleanup"] == {"rejected": 2, "dry_run_items": 3}
    assert rec["cleanup_problems"] == Result.problems and rec["exit_code"] == 1   # 1: the fake's QA failure
    text = (paths.outbox / status.TEXT_NAME).read_text(encoding="utf-8")
    assert "Old files tidied away: rejected 2, dry run items 3" in text
    assert "Could not tidy: practice items: not cleaned (output/dry-run is a link)" in text

    def broken(s, now=None):
        raise OSError("disk trouble")

    monkeypatch.setitem(sys.modules, "service.cleanup", types.SimpleNamespace(run=broken))
    rec = tick.tick(settings, date=DATE)
    assert rec["errors"] == ["cleanup: OSError: disk trouble"]

    monkeypatch.setitem(sys.modules, "service.cleanup", None)        # not installed: import fails
    monkeypatch.setattr(tick.importlib, "import_module", _missing("service.cleanup"))
    rec = tick.tick(settings, date=DATE)
    assert rec["errors"] == [] and "cleanup" not in rec


def _missing(name):
    import importlib

    real = importlib.import_module

    def fake(mod, *a):
        if mod == name:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real(mod, *a)

    return fake


def test_notification_text_is_plain_and_short():
    items = [{"kind": "draft_created", "item_id": f"i{n}", "title": f"Title {n}", "dry_run": False}
             for n in range(8)]
    items += [{"kind": "item_generated", "item_id": "x"},
              {"kind": "token_expiring", "days_left": 9},
              {"kind": "stage_changed", "from": "discovery", "to": "refined", "drafts": 20}]
    text, kinds_ = tick.notification(items, {"notify": {}})
    assert text.startswith("Botsy:\n")
    assert "8 new drafts are waiting in Etsy: Title 0; Title 1; Title 2; Title 3; Title 4 and 3 more" in text
    assert "The Etsy connection runs out in 9 days" in text and "refined stage" in text
    assert kinds_ == ["draft_created", "stage_changed", "token_expiring"]
    assert len(text) < 2000
    only_errors = {"notify": {"discord": {"events": ["error"]}}}
    assert tick.notification(items, only_errors) == ("", [])


def test_notify_failure_never_breaks_the_tick(tmp_path, monkeypatch, no_cleanup, stage):
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline())

    def boom(*a, **kw):
        raise RuntimeError("bug in the message")

    monkeypatch.setattr(tick, "notification", boom)
    settings, paths = setup(tmp_path)
    rec = tick.tick(settings, force=True, date=DATE)
    assert rec["finished"] and (paths.outbox / status.TEXT_NAME).exists()


def test_real_send_is_used_with_discord_switched_on(tmp_path, monkeypatch, no_cleanup, stage):
    """With Discord on and a webhook set, the tick goes through notify.send (fake session)."""
    posts = []

    class Session:
        def post(self, url, **kw):
            posts.append(kw)
            return types.SimpleNamespace(status_code=204, headers={}, json=lambda: {})

    real_send = notify.send
    monkeypatch.setattr(notify, "send", lambda text, **kw: real_send(text, session=Session(),
                                                                       now=datetime(2026, 1, 1, 12), **kw))
    monkeypatch.setattr(notify, "load_env", lambda: None)
    monkeypatch.setenv(notify.ENV_VAR, "https://discord.com/api/webhooks/1/abc")
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, _ = setup(tmp_path, notify={"discord": {"enabled": True}})
    tick.tick(settings, force=True, date=DATE)
    assert len(posts) == 1 and "practice draft" in posts[0]["json"]["content"]


def test_items_stopped_by_the_cap_are_not_problems(tmp_path, monkeypatch, sent, no_cleanup, stage):
    """An item record the pipeline marks budget: true is the cap, not a fault: budget_reached says it."""
    def summary(d, s, dry):
        out = fake_summary(d, s, dry)
        out["items"] = {FROGS: {"generate": {"ok": False, "detail": "monthly spending cap reached",
                                             "budget": True}},
                        BAD: {"generate": {"ok": True, "detail": "generated"},
                              "listing": {"ok": False, "detail": "ValueError: title too long"}}}
        return {**out, "awaiting_review": {}, "budget_reached": True}

    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline(summary=summary))
    settings, _ = setup(tmp_path, live=True, spend={"monthly_cap_usd": 100})
    tick.tick(settings, force=True, date=DATE)
    errors = [e for e in all_events(settings) if e["kind"] == "error"]
    assert [e["where"] for e in errors] == [f"listing[{BAD}]"]
    assert kinds(settings).count("budget_reached") == 1


def test_the_same_problem_is_posted_once_a_day(tmp_path, monkeypatch, sent, no_cleanup, stage):
    monkeypatch.setattr(research, "run", FakeResearch(fail={"coloring_pages"}))
    monkeypatch.setattr(prun, "run", FakePipeline(summary=lambda d, s, dry: {
        **fake_summary(d, s, dry), "items": {}, "awaiting_review": {}}))
    settings, _ = setup(tmp_path)
    for day in (DATE, DATE, "2026-10-17"):
        tick.tick(settings, force=True, date=day)
    # Every tick records it (the daily log counts them) ...
    assert kinds(settings).count("error") == 3
    # ... but Discord hears about it once a day.
    posted = [t for t, _ in sent if "boom in coloring_pages" in t]
    assert len(posted) == 2
    state = json.loads((tmp_path / "data" / ".service_state.json").read_text())
    assert list(state["errors_sent"].values()) == ["2026-10-17"]


def test_rejected_upload_reasons_lose_quoted_text_and_links(tmp_path, monkeypatch, sent, no_cleanup, stage):
    from service import inbox

    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, paths = setup(tmp_path)
    result = inbox.IngestResult()
    result.rejected.append(("www.etsy.com_shop_Rival.csv",
                            "it could not be read as a CSV table (ValueError: bad value 'RivalShop Owl Pack')"))
    monkeypatch.setattr(inbox, "ingest", lambda *a, **kw: result)
    tick.tick(settings, date=DATE)
    (e,) = [e for e in all_events(settings) if e["kind"] == "export_rejected"]
    assert "Rival" not in json.dumps(e) and "'...'" in e["reason"] and e["name"] == "[link]"
    assert "Rival" not in sent[0][0]


def test_cap_reached_in_one_shop_skips_paid_stages_for_the_next(tmp_path, monkeypatch):
    calls = []

    def run(**kw):
        calls.append(kw)
        return {**fake_summary(kw["date"], kw["shop_id"], kw["dry_run"]), "budget_reached": True}

    monkeypatch.setattr(prun, "run", run)
    monkeypatch.setattr(prun, "latest_briefs", lambda briefs, niches: ["x"])
    settings, paths = setup(tmp_path, live=True)
    settings["shops"] = [{"id": "main", "niches": ["stickers"]}, {"id": "second", "niches": ["coloring_pages"]}]
    tick.run_all(settings, paths, DATE, live=True)
    assert [c["stages"] for c in calls] == [tick.RUN_STAGES, tick.UNPAID_STAGES]
    calls.clear()
    tick.run_all(settings, paths, DATE, live=False)                       # dry runs cost nothing
    assert [c["stages"] for c in calls] == [tick.RUN_STAGES, tick.RUN_STAGES]


def test_a_damaged_state_file_does_not_stop_the_tick(tmp_path, monkeypatch, sent, no_cleanup, stage):
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, _ = setup(tmp_path, live=True, spend={"monthly_cap_usd": 10, "warn_at": 0.8})
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=2_100_000, settings=settings)
    state_file = tmp_path / "data" / ".service_state.json"
    state_file.write_text(json.dumps({"spend": "x", "held": [1], "errors_sent": None, "stage": 5}))
    rec = tick.tick(settings, force=True, date=DATE)
    assert not any(e.startswith("tick:") for e in rec["errors"])
    assert kinds(settings).count("spend_warning") == 1
    state = json.loads(state_file.read_text())
    assert isinstance(state["spend"], dict) and isinstance(state["held"], dict)


def test_an_unreadable_ledger_pauses_paid_steps_with_a_plain_reason(tmp_path, monkeypatch):
    def broken(settings=None, **kw):
        raise PermissionError("ledger")

    monkeypatch.setattr(spend, "budget_state", broken)
    settings, _ = setup(tmp_path, live=True)
    rec = status.new_record(date=DATE, forced=True, live=True)
    why = tick.budget_step(settings, {}, tick.Journal(settings), rec)
    assert why and "could not be read" in why and "not a number" not in why
    assert rec["errors"] == ["spend: PermissionError: ledger"]


# ---------------------------------------------------------------- integration additions

def test_token_expiring_is_sent_once_a_day_from_the_daily_live_tick(tmp_path, monkeypatch, sent, no_cleanup,
                                                                    stage):
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline())
    monkeypatch.setattr(tick, "fetch_own_stats", lambda settings: 3)
    monkeypatch.setattr(tick, "token_days_left", lambda settings: 6)
    settings, _ = setup(tmp_path, live=True, notify={"discord": {"enabled": True}, "quiet_hours": None})
    tick.tick(settings, force=True, own_stats=True, date=DATE)
    tick.tick(settings, force=True, own_stats=True, date=DATE)
    assert [e["days_left"] for e in all_events(settings) if e["kind"] == "token_expiring"] == [6]
    assert any("The Etsy connection runs out in 6 days" in text for text, _ in sent)
    monkeypatch.setattr(tick, "token_days_left", lambda settings: 30)    # far off: nothing
    tick.tick(settings, force=True, own_stats=True, date="2026-10-17")
    assert kinds(settings).count("token_expiring") == 1


def test_cooldown_skips_and_cap_waits_are_not_failures_in_status(tmp_path, monkeypatch, no_cleanup, stage):
    summary = fake_summary(DATE, "main", True)
    summary["items"] = {
        FROGS: {"generate": {"ok": True, "detail": "generated"}, "qa": {"ok": True, "detail": ""}},
        CATS: {"generate": {"ok": True, "detail": f"theme cooldown: {FROGS}", "skipped": True, "cooldown": FROGS}},
        BAD: {"generate": {"ok": False, "detail": prun.BUDGET_GENERATE, "budget": True}},
    }
    summary["budget_reached"] = True
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline(summary=lambda day, shop_id, dry_run: summary))
    settings, paths = setup(tmp_path)
    rec = tick.tick(settings, force=True, date=DATE)
    run = rec["run"]
    assert (run["items"], run["cooldown"], run["budget_wait"], run["failed"]) == (2, 1, 1, [])
    text = (paths.outbox / status.TEXT_NAME).read_text(encoding="utf-8")
    assert "Skipped: 1 (same theme made recently)" in text
    assert "Waiting for next month (spending cap reached): 1" in text
    assert "error" not in kinds(settings)                    # the planned cap stop is not a problem


def test_forced_tick_researches_again_when_an_export_leaves_the_window(tmp_path, monkeypatch, no_cleanup, stage):
    r = FakeResearch()
    monkeypatch.setattr(research, "run", r)
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, paths = setup(tmp_path, research={"window_days": 35})
    paths.inputs.mkdir(parents=True, exist_ok=True)
    (paths.inputs / "everbee-2026-09-14-0123abcd.csv").write_bytes(everbee_csv())
    tick.tick(settings, force=True, date="2026-10-16")              # 32 days old: inside the window
    first = len(r.calls)
    tick.tick(settings, force=True, date="2026-10-17")              # nothing changed
    assert len(r.calls) == first
    tick.tick(settings, force=True, date="2026-10-20")              # 36 days old: it left the window
    assert len(r.calls) > first
    a = tick.inputs_fingerprint(paths.inputs, settings, "2026-10-16", "discovery")
    assert a != tick.inputs_fingerprint(paths.inputs, settings, "2026-10-16", "refined")


def test_all_exports_older_than_the_window_get_a_plain_note(tmp_path, monkeypatch, no_cleanup, stage):
    monkeypatch.setattr(research, "run", lambda **kw: [])
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, paths = setup(tmp_path)
    paths.inputs.mkdir(parents=True, exist_ok=True)
    (paths.inputs / "everbee-2026-08-01-0123abcd.csv").write_bytes(everbee_csv())
    rec = tick.tick(settings, force=True, date=DATE)
    notes = rec["research"]["notes"]
    assert any("all exports are older than 35 days; new exports are needed" in n for n in notes)
    assert not any("not enough matching listings" in n for n in notes)


def test_a_ledger_that_cannot_be_written_pauses_paid_stages_once_per_tick(tmp_path, monkeypatch, no_cleanup,
                                                                          stage):
    p = FakePipeline()
    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", p)

    def unavailable(kind, settings=None, **kw):
        raise spend.LedgerUnavailable("Paid AI calls are paused: the spending log cannot be written")

    monkeypatch.setattr(spend, "check_budget", unavailable)
    settings, paths = setup(tmp_path, live=True, spend={"monthly_cap_usd": 25})
    rec = tick.tick(settings, force=True, date=DATE)
    assert p.calls[0]["stages"] == tick.UNPAID_STAGES
    assert sum(e.startswith("spend: LedgerUnavailable") for e in rec["errors"]) == 1
    assert any("spending log cannot be written" in n for n in rec["run"]["notes"])
