"""service.tick: ingest -> research -> pipeline -> status, with fake research and pipeline."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import pipeline.run as prun
import research
from service import inbox, status, tick
from service.paths import service_paths

from ._support import (
    DATE,
    FakePipeline,
    FakeResearch,
    fake_summary,
    everbee_csv,
    make_settings,
    upload,
)

NICHES = ["stickers", "coloring_pages", "activity_sheets"]


@pytest.fixture
def fakes(monkeypatch):
    r, p = FakeResearch(), FakePipeline()
    monkeypatch.setattr(research, "run", r)
    monkeypatch.setattr(prun, "run", p)
    return r, p


def setup(tmp_path, **service):
    settings, _ = make_settings(tmp_path, service=service)
    return settings, service_paths(settings)


def read_status(paths) -> tuple[dict, str]:
    doc = json.loads((paths.outbox / status.JSON_NAME).read_text(encoding="utf-8"))
    return doc, (paths.outbox / status.TEXT_NAME).read_bytes().decode("utf-8")


def test_no_upload_means_no_research_and_no_run(tmp_path, fakes):
    r, p = fakes
    settings, paths = setup(tmp_path)
    rec = tick.tick(settings, date=DATE)
    assert r.calls == [] and p.calls == []
    assert rec["exit_code"] == 0 and not rec["research"]["ran"] and not rec["run"]["ran"]
    doc, text = read_status(paths)
    assert doc["ticks"] == [] and doc["last_checked"]        # an idle check only moves last_checked
    assert "Nothing has happened yet" in text


def test_accepted_upload_runs_research_with_history_and_a_dry_run(tmp_path, fakes):
    r, p = fakes
    settings, paths = setup(tmp_path)
    upload(paths.incoming, "everbee export.csv", everbee_csv())
    rec = tick.tick(settings, date=DATE)

    assert r.niches() == NICHES                               # every shop niche, once each
    for call in r.calls:
        assert call["history_dir"] == paths.history and call["inputs_dir"] == paths.inputs
        assert call["briefs_dir"] == paths.briefs and call["date"] == DATE
        assert call["settings"] is settings
    assert len(p.calls) == 1
    assert p.calls[0]["dry_run"] is True                      # service.live defaults to false
    assert p.calls[0]["stages"] == ("generate", "qa", "listing", "publish")
    assert p.calls[0]["shop_id"] == "main" and p.calls[0]["date"] == DATE

    assert len(rec["ingest"]["accepted"]) == 1
    assert rec["research"]["brief_files"] and rec["research"]["briefs"] == 2
    run = rec["run"]
    assert (run["items"], run["generated"], run["drafts"], run["awaiting_review"]) == (4, 3, 1, 1)
    assert run["failed"] == [f"{DATE}-stickers-bad-dddd: qa: pdf_blank_pages"]
    assert rec["exit_code"] == 1                              # a failed item, like the pipeline CLI

    doc, text = read_status(paths)
    assert doc["ticks"][-1]["ingest"]["accepted"] == rec["ingest"]["accepted"]
    assert doc["week"]["exports"] == 1 and doc["week"]["items"] == 3
    assert doc["week"]["drafts"] == 0 and doc["week"]["practice_drafts"] == 1
    assert "Exports received: 1 new" in text and "Items made: 3" in text
    assert "Mode: TEST" in text and "Practice drafts" in text and "pdf_blank_pages" in text


def test_live_setting_turns_the_dry_run_off(tmp_path, fakes):
    _, p = fakes
    settings, paths = setup(tmp_path, live=True)
    upload(paths.incoming, "export.csv", everbee_csv())
    rec = tick.tick(settings, date=DATE)
    assert p.calls[0]["dry_run"] is False
    assert rec["live"] is True and rec["run"]["dry_run"] is False
    doc, text = read_status(paths)
    assert doc["week"]["drafts"] == 1 and "Mode: LIVE" in text and "Etsy drafts created: 1" in text


@pytest.mark.parametrize("value", ["true", "yes", 1, None])
def test_only_a_real_true_is_live(tmp_path, fakes, value):
    _, p = fakes
    settings, paths = setup(tmp_path, live=value)
    upload(paths.incoming, "export.csv", everbee_csv())
    tick.tick(settings, date=DATE)
    assert p.calls[0]["dry_run"] is True


def test_force_runs_without_upload_but_does_not_rebuild_unchanged_briefs(tmp_path, fakes):
    r, p = fakes
    settings, paths = setup(tmp_path)
    first = tick.tick(settings, force=True, date=DATE)
    assert r.niches() == NICHES and len(p.calls) == 1 and first["research"]["ran"]

    second = tick.tick(settings, force=True, date="2026-10-17")
    assert len(r.calls) == 3                                  # research skipped: same inputs
    assert len(p.calls) == 2                                  # the pipeline still runs (reviews, retries)
    assert not second["research"]["ran"]
    assert any("research skipped" in n for n in second["research"]["notes"])

    # A file dropped straight into the inputs folder counts as changed inputs.
    paths.inputs.mkdir(parents=True, exist_ok=True)
    (paths.inputs / "erank-manual.csv").write_bytes(everbee_csv(seed=5))
    tick.tick(settings, force=True, date="2026-10-18")
    assert len(r.calls) == 6


def test_force_with_no_brief_files_skips_the_pipeline(tmp_path, monkeypatch):
    r, p = FakeResearch(writes=()), FakePipeline()
    monkeypatch.setattr(research, "run", r)
    monkeypatch.setattr(prun, "run", p)
    settings, _ = setup(tmp_path)
    rec = tick.tick(settings, force=True, date=DATE)
    assert len(r.calls) == 3 and p.calls == []
    assert rec["exit_code"] == 0 and any("no brief files yet" in n for n in rec["run"]["notes"])
    assert any("no brief file yet" in n for n in rec["research"]["notes"])


def test_no_inputs_is_a_note_said_once(tmp_path, monkeypatch):
    r = FakeResearch(no_inputs=True)
    monkeypatch.setattr(research, "run", r)
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, _ = setup(tmp_path)
    rec = tick.tick(settings, force=True, date=DATE)
    assert len(r.calls) == 1 and rec["errors"] == []
    assert rec["research"]["notes"] == ["no research exports yet; waiting for the first upload"]


def test_research_error_is_recorded_and_retried_next_time(tmp_path, monkeypatch):
    r = FakeResearch(fail={"coloring_pages"})
    monkeypatch.setattr(research, "run", r)
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, paths = setup(tmp_path)
    rec = tick.tick(settings, force=True, date=DATE)
    assert r.niches() == NICHES                               # the other niches still ran
    assert rec["errors"] == ["research[coloring_pages]: RuntimeError: boom in coloring_pages"]
    assert rec["exit_code"] == 1
    _, text = read_status(paths)
    assert "Problems: 1" in text and "boom in coloring_pages" in text
    tick.tick(settings, force=True, date=DATE)
    assert len(r.calls) == 6                                  # inputs unchanged, but last research failed


def test_pipeline_setup_error_gives_exit_2(tmp_path, monkeypatch):
    def summary(day, shop_id, dry_run):
        return {"date": day, "shop_id": shop_id, "dry_run": dry_run, "items": {}, "errors": [],
                "setup_errors": ["listing: MissingSecretError: ANTHROPIC_API_KEY is not set"],
                "notes": [], "awaiting_review": {}, "path": "x"}

    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline(summary=summary))
    settings, _ = setup(tmp_path, live=True)
    rec = tick.tick(settings, force=True, date=DATE)
    assert rec["exit_code"] == 2 and rec["errors"][0].startswith("setup: listing")


def test_pipeline_exception_is_recorded(tmp_path, monkeypatch):
    def broken(**kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", broken)
    settings, _ = setup(tmp_path)
    rec = tick.tick(settings, force=True, date=DATE)
    assert rec["errors"] == ["run[main]: RuntimeError: disk on fire"] and rec["exit_code"] == 1


def test_unexpected_ingest_failure_still_writes_status(tmp_path, fakes, monkeypatch):
    def broken(*a, **kw):
        raise PermissionError("cannot list incoming")

    monkeypatch.setattr(inbox, "ingest", broken)
    settings, paths = setup(tmp_path)
    rec = tick.tick(settings, date=DATE)
    assert rec["errors"] == ["ingest: PermissionError: cannot list incoming"]
    doc, text = read_status(paths)
    assert doc["ticks"][-1]["errors"] == rec["errors"] and "cannot list incoming" in text


def test_upload_during_the_run_gets_another_round(tmp_path, monkeypatch):
    settings, paths = setup(tmp_path)

    def late_upload(n):
        if n == 1:
            upload(paths.incoming, "second.csv", everbee_csv(seed=9))

    r, p = FakeResearch(), FakePipeline(during=late_upload)
    monkeypatch.setattr(research, "run", r)
    monkeypatch.setattr(prun, "run", p)
    upload(paths.incoming, "first.csv", everbee_csv(seed=1))
    rec = tick.tick(settings, date=DATE)
    assert len(rec["ingest"]["accepted"]) == 2 and len(p.calls) == 2 and len(r.calls) == 6
    assert list(paths.incoming.iterdir()) == []


def test_max_rounds_leaves_late_uploads_for_the_next_tick(tmp_path, monkeypatch):
    settings, paths = setup(tmp_path, max_rounds=1)

    def late_upload(n):
        upload(paths.incoming, f"late{n}.csv", everbee_csv(seed=20 + n))

    monkeypatch.setattr(research, "run", FakeResearch())
    p = FakePipeline(during=late_upload)
    monkeypatch.setattr(prun, "run", p)
    upload(paths.incoming, "first.csv", everbee_csv(seed=1))
    tick.tick(settings, date=DATE)
    assert len(p.calls) == 1 and [x.name for x in paths.incoming.iterdir()] == ["late1.csv"]


def test_settle_waits_for_the_rest_of_a_batch(tmp_path, fakes):
    r, p = fakes
    settings, paths = setup(tmp_path, settle_s=0.3, settle_max_s=5)
    slept = []

    def sleep(s):
        slept.append(s)
        if len(slept) == 1:  # the user's next export arrives while the server waits
            upload(paths.incoming, "second.csv", everbee_csv(seed=2))
        time.sleep(s)

    upload(paths.incoming, "first.csv", everbee_csv(seed=1))
    rec = tick.tick(settings, date=DATE, sleep=sleep)
    assert len(rec["ingest"]["accepted"]) == 2
    assert len(r.calls) == 3 and len(p.calls) == 1            # one research run for the whole batch
    assert len(slept) >= 2


def test_own_stats_only_in_live_mode(tmp_path, fakes, monkeypatch):
    calls = []
    monkeypatch.setattr(tick, "fetch_own_stats", lambda settings: calls.append(1) or 7)
    settings, _ = setup(tmp_path)
    rec = tick.tick(settings, force=True, own_stats=True, date=DATE)
    assert calls == [] and "test mode" in rec["own_stats"]["detail"]

    live, _ = setup(tmp_path, live=True)
    rec = tick.tick(live, force=True, own_stats=True, date=DATE)
    assert calls == [1] and rec["own_stats"] == {"ran": True, "detail": "7 of our own listings read from Etsy"}


def test_own_stats_failure_does_not_stop_the_tick(tmp_path, fakes, monkeypatch):
    _, p = fakes

    def fail(settings):
        raise RuntimeError("ETSY_API_KEYSTRING is not set")

    monkeypatch.setattr(tick, "fetch_own_stats", fail)
    settings, _ = setup(tmp_path, live=True)
    rec = tick.tick(settings, force=True, own_stats=True, date=DATE)
    assert rec["errors"][0].startswith("own-stats: RuntimeError") and len(p.calls) == 1


def test_shared_niche_is_researched_once_and_each_shop_runs(tmp_path, fakes):
    r, p = fakes
    settings, paths = setup(tmp_path)
    settings["shops"].append({"id": "second", "name": "Second Shop", "niches": ["stickers"],
                              "default_price_usd": {"stickers": 3.5}})
    upload(paths.incoming, "export.csv", everbee_csv())
    tick.tick(settings, date=DATE)
    assert r.niches() == NICHES
    assert [c["shop_id"] for c in p.calls] == ["main", "second"]


def test_inputs_fingerprint_ignores_own_shop_and_trend_files(tmp_path):
    settings, paths = setup(tmp_path)
    paths.inputs.mkdir(parents=True)
    (paths.inputs / "erank-1.csv").write_bytes(b"a")
    before = tick.inputs_fingerprint(paths.inputs, settings)
    (paths.inputs / "own_shop_stats.csv").write_bytes(b"b")
    (paths.inputs / "trends-x.csv").write_bytes(b"c")
    (paths.inputs / ".ingested.json").write_bytes(b"{}")
    assert tick.inputs_fingerprint(paths.inputs, settings) == before
    (paths.inputs / "stickers__everbee-2.csv").write_bytes(b"d")
    assert tick.inputs_fingerprint(paths.inputs, settings) != before
    assert tick.inputs_fingerprint(Path(tmp_path / "missing"), settings)   # no folder: still a value


def test_brief_compliance_error_keeps_competitor_text_out_of_the_outbox(tmp_path, monkeypatch):
    """research quotes the shop name, link or title it found; status.txt/json (copied to the
    PC by the helper) must only say what kind of problem it was."""
    def bad(niche=None, **kw):
        raise research.BriefComplianceError([
            "shop name 'RivalShopCo' in 'Frog stickers by RivalShopCo'",
            "url 'https://www.etsy.com/listing/123456/frog-stickers'",
            'verbatim listing title in "Cute Frog Stickers Mega Pack"'])

    monkeypatch.setattr(research, "run", bad)
    monkeypatch.setattr(prun, "run", FakePipeline())
    settings, paths = setup(tmp_path)
    rec = tick.tick(settings, force=True, date=DATE)
    assert rec["errors"] and all(e.startswith("research[") for e in rec["errors"])
    assert "shop name '...' in '...'" in rec["errors"][0] and "verbatim listing title" in rec["errors"][0]
    for f in (status.JSON_NAME, status.TEXT_NAME):
        text = (paths.outbox / f).read_text(encoding="utf-8")
        for secret in ("RivalShopCo", "123456", "etsy.com", "Cute Frog Stickers"):
            assert secret not in text


def test_bad_brief_file_error_from_the_pipeline_is_scrubbed(tmp_path, monkeypatch):
    def summary(day, shop_id, dry_run):
        s = fake_summary(day, shop_id, dry_run)
        s["errors"] = ["brief file data/briefs/x.json breaks the brief rules; not used: brief failed "
                       "compliance checks: - shop name 'RivalShopCo' in 'by RivalShopCo'",
                       "generate: something 'ours' failed"]
        return s

    monkeypatch.setattr(research, "run", FakeResearch())
    monkeypatch.setattr(prun, "run", FakePipeline(summary=summary))
    settings, paths = setup(tmp_path)
    rec = tick.tick(settings, force=True, date=DATE)
    assert not any("RivalShopCo" in e for e in rec["errors"])
    assert "generate: something 'ours' failed" in rec["errors"]       # other errors keep their detail
    assert "RivalShopCo" not in (paths.outbox / status.JSON_NAME).read_text(encoding="utf-8")


def test_quiet_error_scrubs_quotes_and_links():
    msg = tick.quiet_error("research[x]", ValueError("read 'a b.csv' from etsy.com/shop/Foo and https://x.y/z"))
    assert msg == "research[x]: ValueError: read '...' from [link] and [link]"


def test_accepted_files_still_count_when_the_second_ingest_fails(tmp_path, fakes, monkeypatch):
    """Accepted exports have left the inbox; if the settle re-check fails they must still be
    researched now (no upload event would bring them back before the daily check)."""
    r, p = fakes
    settings, paths = setup(tmp_path, settle_s=1, settle_max_s=1)
    upload(paths.incoming, "export.csv", everbee_csv())
    real, calls = inbox.ingest, []

    def flaky(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(*a, **kw)

    monkeypatch.setattr(inbox, "ingest", flaky)
    monkeypatch.setattr(inbox, "wait_for_quiet", lambda *a, **kw: 0.0)
    rec = tick.tick(settings, date=DATE)
    assert len(rec["ingest"]["accepted"]) == 1 and rec["research"]["ran"] and p.calls
    assert rec["errors"] == ["ingest: OSError: disk full"]
