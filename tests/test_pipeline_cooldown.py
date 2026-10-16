"""pipeline.run: theme cooldown (docs/SERVER.md section 13) and the monthly spending cap
(core.spend.BudgetExceeded, section 10).

The stage entry points are small fakes, so these tests check only how pipeline.run decides
what to make, and stay fast. Everything is written in tmp_path.
"""
from __future__ import annotations

import sys

import pytest

from core import config, items
from core.spend import BudgetExceeded
from pipeline import run as prun

DATE = "2026-10-16"


def brief_doc(niche: str, date: str, themes, prefix: str = "") -> dict:
    briefs = [{"brief_id": f"{niche}-{date}-{prefix}{i:02d}", "niche": niche, "theme": t, "keywords": [t],
               "evidence": {"listing_count": 12, "score": 0.5},
               "compliance": {"blocked": [], "flagged": []}, "needs_review": False}
              for i, t in enumerate(themes, 1)]
    return {"schema": "brief/v1", "niche": niche, "created": date, "shop_id": "main",
            "sources": [{"tool": "erank", "file": "keywords.csv", "rows": 50}], "listing_count": 50,
            "min_listings_per_pattern": 10,
            "patterns": {"themes": [{"name": t, "listing_count": 12} for t in themes]},
            "briefs": briefs}


@pytest.fixture
def pipe(tmp_path, monkeypatch):
    import generators
    import listing
    import publisher
    import qa

    settings = config.load_settings()
    config.deep_merge(settings, {"paths": {"output_dir": str(tmp_path / "real"),
                                           "review_dir": str(tmp_path / "real" / "review")},
                                 "pipeline": {"dry_run_output_dir": str(tmp_path / "dry")}})

    class P:
        out = tmp_path / "out"
        briefs = tmp_path / "briefs"
        calls = {"generate": [], "qa": [], "listing": [], "publish": []}
        generate_errors: dict = {}      # theme -> exception raised by the fake generator
        listing_errors: dict = {}       # item id -> exception raised by the fake listing stage

        def __init__(self):
            self.settings = settings

        def write_brief(self, niche, themes, date=DATE, prefix=""):
            return items.write_json(self.briefs / f"{niche}-{date}.json", brief_doc(niche, date, themes, prefix))

        def make_item(self, niche, theme, created, *, shop_id="main", publish=None, status="drafted"):
            """An item from an earlier brief, as the generators would have left it."""
            bid = f"{niche}-{created}-old-{theme}"
            d = items.item_dir(items.new_item_id(created, niche, theme, salt=bid), self.out)
            items.write_manifest(d, {"item_id": d.name, "niche": niche, "shop_id": shop_id, "brief_id": bid,
                                     "theme": theme, "created": created, "status": status,
                                     "generator": {"name": niche, "dry_run": True}})
            if publish is not None:
                items.write_json(d / "publish.json", publish)
            return d

        def run(self, **kw):
            kw.setdefault("stages", ("generate", "qa", "listing", "publish"))
            kw.setdefault("dry_run", True)
            kw.setdefault("date", DATE)
            summary = prun.run(out_root=self.out, briefs_dir=self.briefs, settings=self.settings, **kw)
            return summary, prun.exit_code(summary)

        def generated_themes(self):
            return [t for _, t in self.calls["generate"]]

    p = P()

    def fake_generate(brief, *, brief_file=None, shop_id="main", out_root=None, date=None, dry_run=False,
                      settings=None, client=None):
        p.calls["generate"].append((brief["brief_id"], brief["theme"]))
        if brief["theme"] in p.generate_errors:
            raise p.generate_errors[brief["theme"]]
        d = items.item_dir(items.new_item_id(date, brief["niche"], brief["theme"], salt=brief["brief_id"]),
                           out_root)
        items.write_manifest(d, {"item_id": d.name, "niche": brief["niche"], "shop_id": shop_id,
                                 "brief_id": brief["brief_id"], "theme": brief["theme"], "created": date,
                                 "status": "generated", "generator": {"name": brief["niche"], "dry_run": dry_run}})
        return [d]

    def fake_qa(d, *, settings=None, review_dir=None, mockups=True):
        p.calls["qa"].append(d.name)
        items.set_status(d, "qa_passed")
        return {"item_id": d.name, "passed": True, "needs_review": False, "checks": []}

    def fake_listing(d, *, client=None, dry_run=False, settings=None, review_dir=None):
        p.calls["listing"].append(d.name)
        if d.name in p.listing_errors:
            raise p.listing_errors[d.name]
        items.set_status(d, "listed")
        return {"title": f"title {d.name}"}

    def fake_publish(d, *, client=None, dry_run=False, settings=None, review_dir=None):
        p.calls["publish"].append(d.name)
        items.write_json(d / "publish.json", {"listing_id": 7, "dry_run": dry_run, "complete": True})
        items.set_status(d, "drafted")
        return {"listing_id": 7, "dry_run": dry_run}

    monkeypatch.setattr(generators, "generate", fake_generate)
    monkeypatch.setattr(qa, "run", fake_qa)
    monkeypatch.setattr(listing, "write_listing", fake_listing)
    monkeypatch.setattr(listing, "needs_listing",
                        lambda d, *, dry_run=False, settings=None: items.read_manifest(d)["status"] == "qa_passed")
    monkeypatch.setattr(publisher, "publish_draft", fake_publish)
    monkeypatch.setattr(publisher, "review_wait", lambda d, *, settings=None: None)
    return p


def would_be(niche, theme, n=1, *, brief_date=DATE, prefix="", run_date=DATE):
    """The id a brief would make (the run date, salted with the brief id)."""
    return items.new_item_id(run_date, niche, theme, salt=f"{niche}-{brief_date}-{prefix}{n:02d}")


REAL_DRAFT = {"listing_id": 1234, "dry_run": False, "state": "draft", "complete": True}


# ---------------------------------------------------------------- theme cooldown

def test_recent_item_with_the_same_theme_skips_the_brief(pipe):
    old = pipe.make_item("stickers", "Farm Animals", "2026-10-01")
    pipe.write_brief("stickers", ["farm-animals!", "frogs"])       # same theme once normalized
    summary, code = pipe.run()
    assert code == 0 and pipe.generated_themes() == ["frogs"]
    rec = summary["items"][would_be("stickers", "farm-animals!")]["generate"]
    assert rec == {"ok": True, "detail": f"theme cooldown: {old.name}", "skipped": True, "cooldown": old.name}
    text = prun.format_summary(summary)
    assert f"generate: skip (theme cooldown: {old.name})" in text
    assert any("theme cooldown: 1 brief(s) skipped" in n and "60 days" in n for n in summary["notes"])
    assert not (pipe.out / would_be("stickers", "farm-animals!")).exists()
    # The old item is not a target of this run: no stage touched it.
    assert old.name not in pipe.calls["qa"] + pipe.calls["listing"] + pipe.calls["publish"]


def test_different_theme_other_niche_and_other_shop_are_allowed(pipe):
    pipe.make_item("stickers", "frogs", "2026-10-10")
    pipe.make_item("coloring_pages", "owls", "2026-10-10", shop_id="second")
    pipe.write_brief("stickers", ["toads"])
    pipe.write_brief("coloring_pages", ["frogs", "owls"])
    summary, code = pipe.run()
    assert code == 0 and sorted(pipe.generated_themes()) == ["frogs", "owls", "toads"]
    assert not any(st.get("generate", {}).get("cooldown") for st in summary["items"].values())


@pytest.mark.parametrize("age, made", [(59, False), (60, True), (400, True)])
def test_cooldown_lasts_theme_cooldown_days(pipe, age, made):
    import datetime as dt

    created = (dt.date.fromisoformat(DATE) - dt.timedelta(days=age)).isoformat()
    pipe.make_item("stickers", "frogs", created)
    pipe.write_brief("stickers", ["frogs"])
    pipe.run(stages=("generate",))
    assert pipe.generated_themes() == (["frogs"] if made else [])


def test_cooldown_days_come_from_settings(pipe):
    pipe.make_item("stickers", "frogs", "2026-10-01")
    pipe.settings["pipeline"]["theme_cooldown_days"] = 10
    pipe.write_brief("stickers", ["frogs"])
    pipe.run(stages=("generate",))
    assert pipe.generated_themes() == ["frogs"]


@pytest.mark.parametrize("value", ["sixty", True, [60]])
def test_a_cooldown_setting_typo_uses_the_default(pipe, value):
    """Neither stops the run (the server would fail every tick) nor lets duplicates through."""
    pipe.make_item("stickers", "frogs", "2026-10-01")
    pipe.settings["pipeline"]["theme_cooldown_days"] = value
    pipe.write_brief("stickers", ["frogs", "owls"])
    summary, code = pipe.run(stages=("generate",))
    assert code == 0 and pipe.generated_themes() == ["owls"]
    assert any("theme_cooldown_days is not a number" in n and "60 days" in n for n in summary["notes"])


def test_real_draft_blocks_at_any_age_even_with_regenerate(pipe):
    drafted = pipe.make_item("stickers", "frogs", "2024-01-01", publish=REAL_DRAFT)
    before = (drafted / "manifest.json").read_bytes()
    pipe.settings["pipeline"]["theme_cooldown_days"] = 0          # days off: the draft still blocks
    pipe.write_brief("stickers", ["frogs", "Frogs"])
    for regenerate in (False, True):
        summary, code = pipe.run(regenerate=regenerate)
        assert code == 0 and pipe.generated_themes() == []
        assert summary["items"][would_be("stickers", "frogs")]["generate"]["cooldown"] == drafted.name
        assert any("already has an Etsy draft" in n for n in summary["notes"])
    assert (drafted / "manifest.json").read_bytes() == before
    assert (drafted / "publish.json").exists() and items.read_json(drafted / "publish.json") == REAL_DRAFT


def test_unreadable_publish_record_counts_as_a_real_draft(pipe):
    d = pipe.make_item("stickers", "frogs", "2024-01-01")
    (d / "publish.json").write_text("{broken", encoding="utf-8")
    pipe.write_brief("stickers", ["frogs"])
    pipe.run(stages=("generate",))
    assert pipe.generated_themes() == []


def test_dry_run_draft_is_not_a_real_draft(pipe):
    pipe.make_item("stickers", "frogs", "2026-01-01", publish={"listing_id": 1, "dry_run": True})
    pipe.write_brief("stickers", ["frogs"])
    pipe.run(stages=("generate",))
    assert pipe.generated_themes() == ["frogs"]


def test_regenerate_lifts_the_days(pipe):
    pipe.make_item("stickers", "frogs", "2026-10-10")
    pipe.write_brief("stickers", ["frogs"])
    pipe.run(stages=("generate",))
    assert pipe.generated_themes() == []
    summary, code = pipe.run(stages=("generate",), regenerate=True)
    assert code == 0 and pipe.generated_themes() == ["frogs"]


def test_same_theme_twice_in_one_run_is_made_once(pipe):
    pipe.write_brief("stickers", ["frogs"])
    pipe.write_brief("stickers", ["Frogs"], date="2026-10-15", prefix="b")
    summary, code = pipe.run(stages=("generate",),
                             brief_files=[pipe.briefs / f"stickers-{DATE}.json",
                                          pipe.briefs / "stickers-2026-10-15.json"])
    assert code == 0 and pipe.generated_themes() == ["frogs"]
    first = would_be("stickers", "frogs")
    second = would_be("stickers", "Frogs", brief_date="2026-10-15", prefix="b")
    assert summary["items"][second]["generate"]["cooldown"] == first


def test_rerun_of_the_same_brief_reuses_its_item(pipe):
    pipe.write_brief("stickers", ["frogs"])
    pipe.run()
    summary, code = pipe.run()
    rec = summary["items"][would_be("stickers", "frogs")]["generate"]
    assert code == 0 and len(pipe.calls["generate"]) == 1
    assert rec["skipped"] and "reused" in rec["detail"] and "cooldown" not in rec


def test_cooldown_skip_does_not_use_a_max_items_slot(pipe):
    pipe.make_item("stickers", "frogs", "2026-10-10")
    pipe.write_brief("stickers", ["frogs", "owls", "cats"])
    summary, _ = pipe.run(stages=("generate",), max_items=1)
    assert pipe.generated_themes() == ["owls"]
    assert any("1 brief(s) not used" in n for n in summary["notes"])


def test_theme_key():
    assert prun.theme_key("Farm Animals!") == prun.theme_key("farm-animals") == "farm-animals"
    assert prun.theme_key("Café  Cats") == "cafe-cats"
    long_a, long_b = "a" * 45 + " one", "a" * 45 + " two"
    assert prun.theme_key(long_a) != prun.theme_key(long_b)       # not cut short like item ids
    assert prun.theme_key(None) == ""
    assert prun.theme_key("Cat’s Pajamas") == prun.theme_key("cats pajamas") == "cats-pajamas"


# ---------------------------------------------------------------- monthly spending cap

def cap_error() -> BudgetExceeded:
    return BudgetExceeded("Monthly spending cap reached ($25.00 of $25.00)", kind="image", month="2026-10",
                          mtd=25.0, cap=25.0)


def test_budget_exceeded_in_generate_stops_new_paid_work(pipe):
    pipe.write_brief("stickers", ["frogs", "owls", "cats"])
    pipe.generate_errors["frogs"] = cap_error()
    summary, code = pipe.run()
    assert pipe.generated_themes() == ["frogs"]                   # owls and cats were never started
    assert summary["budget_reached"] is True
    rec = summary["items"][would_be("stickers", "frogs")]["generate"]
    assert rec["ok"] is False and rec["budget"] is True and "monthly spending cap" in rec["detail"]
    assert any("2 more brief(s) not made" in n for n in summary["notes"])
    assert code == 0 and not summary["errors"]                    # a planned stop, not a failure
    text = prun.format_summary(summary)
    assert "monthly spending cap reached" in text and "generate=wait" in text and "FAIL" not in text
    assert items.read_json(prun.Path(summary["path"]))["budget_reached"] is True


def test_budget_exceeded_in_listing_stops_later_listings(pipe):
    pipe.write_brief("stickers", ["frogs", "owls", "cats"])
    first = would_be("stickers", "frogs")
    pipe.listing_errors[first] = cap_error()
    summary, code = pipe.run(dry_run=False)
    assert pipe.calls["listing"] == [first]                       # the other two were not tried
    assert len(pipe.calls["qa"]) == 3                             # QA costs nothing: it still ran
    assert pipe.calls["publish"] == []
    rec = summary["items"][first]["listing"]
    assert rec["ok"] is False and rec["budget"] and "not listed" in rec["detail"]
    assert summary["budget_reached"] is True and code == 0
    assert any("2 more item(s) not listed" in n for n in summary["notes"])
    for d in items.iter_item_dirs(pipe.out):                      # they wait as qa_passed for a later run
        assert items.read_manifest(d)["status"] == "qa_passed"


def test_budget_reached_in_generate_skips_paid_listing_but_publishes_listed_items(pipe):
    pipe.write_brief("stickers", ["frogs", "owls"])
    pipe.run(dry_run=False, stages=("generate", "qa"))            # both wait as qa_passed
    owls = would_be("stickers", "owls", 2)
    items.set_status(pipe.out / owls, "listed")                   # one is already listed
    pipe.write_brief("stickers", ["cats", "frogs", "owls"], date="2026-10-17", prefix="n")
    pipe.generate_errors["cats"] = cap_error()
    pipe.calls["listing"].clear()
    pipe.calls["generate"].clear()
    # cats (new: hits the cap) comes first; the earlier items are still reused after it.
    summary, code = pipe.run(dry_run=False, date="2026-10-17",
                             brief_files=[pipe.briefs / "stickers-2026-10-17.json",
                                          pipe.briefs / f"stickers-{DATE}.json"])
    assert summary["budget_reached"] is True and code == 0
    assert pipe.generated_themes() == ["cats"]
    assert pipe.calls["listing"] == []                            # a model call would be paid
    assert pipe.calls["publish"] == [owls]                        # a draft costs nothing
    assert any("1 more item(s) not listed" in n for n in summary["notes"])


def test_budget_stop_with_another_failure_still_exits_1(pipe):
    pipe.write_brief("stickers", ["frogs", "owls"])
    pipe.generate_errors["frogs"] = ValueError("bad brief")
    pipe.generate_errors["owls"] = cap_error()
    summary, code = pipe.run()
    assert summary["budget_reached"] is True and code == 1


def test_runs_without_a_cap_say_so(pipe):
    pipe.write_brief("stickers", ["frogs"])
    summary, code = pipe.run()
    assert summary["budget_reached"] is False and code == 0
    assert "spending cap" not in prun.format_summary(summary)


def test_unwritable_spend_log_stops_paid_work_as_a_setup_problem(pipe):
    """core.spend.LedgerUnavailable refuses every paid call of the process: the stage stops
    once (exit 2) instead of failing every brief the same way."""
    from core.spend import LedgerUnavailable

    pipe.write_brief("stickers", ["frogs", "owls", "cats"])
    pipe.generate_errors["frogs"] = LedgerUnavailable("Paid AI calls are paused: the spending log "
                                                      "cannot be written")
    summary, code = pipe.run()
    assert pipe.generated_themes() == ["frogs"] and code == 2
    assert len(summary["setup_errors"]) == 1 and "spending log" in summary["setup_errors"][0]


def test_unwritable_spend_log_in_listing_stops_the_listing_stage(pipe):
    from core.spend import LedgerUnavailable

    pipe.write_brief("stickers", ["frogs", "owls"])
    first = would_be("stickers", "frogs")
    pipe.listing_errors[first] = LedgerUnavailable("Paid AI calls are paused")
    summary, code = pipe.run(dry_run=False)
    assert pipe.calls["listing"] == [first] and code == 2
    assert summary["setup_errors"] == ["listing: Paid AI calls are paused"]


def test_missing_core_spend_falls_back(pipe, monkeypatch):
    monkeypatch.setitem(sys.modules, "core.spend", None)        # `from core.spend import ...` fails
    assert prun._budget_exceeded() is prun._NoBudgetCheck
    pipe.write_brief("stickers", ["frogs", "owls"])
    pipe.generate_errors["frogs"] = RuntimeError("boom")
    summary, code = pipe.run()
    assert code == 1 and summary["budget_reached"] is False
    assert pipe.generated_themes() == ["frogs", "owls"]           # an ordinary error stops nothing
