"""core/: blocklist matching, URL detection, AI disclosure, item helpers."""
import pytest

from core import compliance, config, items
from core.compliance import Blocklist


@pytest.fixture(scope="module")
def bl():
    return Blocklist.load()


def test_blocklist_loads_hard_and_review_terms(bl):
    hard = {t for t, _ in bl.hard}
    review = {t for t, _ in bl.review}
    assert "Disney" in hard and "Paw Patrol" in hard
    assert "Winnie the Pooh" in review and "Winnie the Pooh" not in hard


@pytest.mark.parametrize("text, term", [
    ("PAW PATROL coloring book", "Paw Patrol"),
    ("pawpatrol stickers", "Paw Patrol"),
    ("paw-patrol party", "Paw Patrol"),
    ("spider man mask", "Spider-Man"),
    ("Pokémon cards", "Pokemon"),
    ("the minions' goggles", "Minions"),
    ("barbies dream house", "Barbie"),
    ("wheres waldo puzzle", "Where's Waldo"),
])
def test_blocklist_matches_variants(bl, text, term):
    assert term in bl.check(text).blocked


@pytest.mark.parametrize("text, term", [
    ("Pokémon™", "Pokemon"),                 # NFKD spells ™ as "TM" and glues it on
    ("Disney™ Princess", "Disney Princess"),
    ("Disney℠", "Disney"),
    ("Bluey™ coloring", "Bluey"),
    ("Hello Kitty™", "Hello Kitty"),
    ("Minecraft™", "Minecraft"),
    ("LEGO™ bricks", "LEGO"),
    ("LEGO® bricks", "LEGO"),
    ("Dr. Seuss", "Dr Seuss"),               # full stop after a word
    ("Hello.Kitty", "Hello Kitty"),
    ("spider‐man", "Spider-Man"),       # hyphen
    ("Spider‑Man", "Spider-Man"),       # non-breaking hyphen
    ("Spider–Man", "Spider-Man"),       # en dash
    ("Spider—Man", "Spider-Man"),       # em dash
    ("Spider−Man", "Spider-Man"),       # minus sign
    ("Paw​Patrol", "Paw Patrol"),       # zero-width space
    ("Paw‍Patrol", "Paw Patrol"),       # zero-width joiner
    ("Paw⁠Patrol", "Paw Patrol"),       # word joiner
    ("﻿Paw Patrol", "Paw Patrol"),      # BOM
    ("Mine­craft", "Minecraft"),        # soft hyphen
    ("Paw    Patrol", "Paw Patrol"),         # whitespace run
    ("Paw    Patrol", "Paw Patrol"),
    ("Pаw Patrol", "Paw Patrol"),       # Cyrillic а
    ("Вluey", "Bluey"),                 # Cyrillic В
    ("Ηello Kitty", "Hello Kitty"),          # Greek Η
    ("Where‘s Waldo", "Where's Waldo"),
    ("K-pop Demon Hunters", "K-Pop Demon Hunters"),
    ("kpop demon hunters", "KPop Demon Hunters"),
    ("Coco Melon", "Coco Melon"),
    ("Seuss", "Seuss"),
    ("Disneyland", "Disneyland"),
    ("disneyworld", "Disney World"),
    ("Grinchmas", "Grinchmas"),
    ("Mc Donald's", "Mc Donald's"),
])
def test_blocklist_matches_unicode_and_punctuation_tricks(bl, text, term):
    assert term in bl.check(text).blocked, (text, compliance.normalize(text))


def test_normalize_steps():
    assert compliance.normalize("Pokémon™ Cards") == "pokemon cards"
    assert compliance.normalize("a​­b") == "ab"
    assert compliance.normalize("a–b−c") == "a-b-c"
    assert compliance.normalize("a \t  b\nc") == "a b\nc"  # newlines stay
    assert items.slugify("Pokémon™ Party") == "pokemon-party"


@pytest.mark.parametrize("text", [
    "farm animal coloring pages",
    "dinosaur maze for kids",
    "Elsewhere in the forest",   # 'Elsa' must not match inside a longer word
    "barbecue party stickers",  # 'Barbie' must not match 'barbecue'
    "Dinosaurs™ and dragons",    # a ™ sign alone is not a term
    "Old McDonald had a farm",  # nursery rhyme, not "McDonald's"
    "spider web, man in the moon",
])
def test_blocklist_no_false_positive_inside_words(bl, text):
    assert bl.check(text).ok


def test_review_terms_flag_not_block(bl):
    r = bl.check("Peter Rabbit garden coloring page")
    assert r.ok and r.needs_review and r.flagged == ["Peter Rabbit"]


def test_check_obj_walks_nested(bl):
    r = bl.check_obj({"briefs": [{"keywords": ["cute", "Hello Kitty bow"]}]})
    assert r.blocked == ["Hello Kitty"]


def test_find_urls():
    assert compliance.find_urls({"a": ["see https://x.example/y", "etsy.com/listing/1"]})
    assert not compliance.find_urls({"a": "farm animals, 20 pages, e.g. cows"})


def test_ai_disclosure_roundtrip():
    text = compliance.load_ai_disclosure("Sunny Prints")
    assert "Sunny Prints" in text and "{shop_name}" not in text
    desc = "Twenty pages.\n\n" + text.replace(" ", "  ")
    assert compliance.has_ai_disclosure(desc, "Sunny Prints")
    assert not compliance.has_ai_disclosure("Twenty pages.", "Sunny Prints")


def test_item_ids_deterministic_and_slugged():
    a = items.new_item_id("2026-10-15", "stickers", "Cute Frogs & Toads!")
    assert a == items.new_item_id("2026-10-15", "stickers", "Cute Frogs & Toads!")
    assert a.startswith("2026-10-15-stickers-cute-frogs-toads-")
    assert a != items.new_item_id("2026-10-15", "stickers", "Cute Frogs & Toads!", salt="2")


def test_manifest_roundtrip_and_status(tmp_path):
    d = tmp_path / "item1"
    items.write_manifest(d, {"item_id": "item1", "status": "generated"})
    assert [p.name for p in items.iter_item_dirs(tmp_path)] == ["item1"]
    assert items.set_status(d, "qa_passed")["status"] == "qa_passed"
    with pytest.raises(ValueError):
        items.set_status(d, "active")


def test_section_merges_defaults():
    s = {"qa": {"min_dpi": 300, "nested": {"b": 2}}}
    merged = config.section(s, "qa", {"min_dpi": 150, "max": 5, "nested": {"a": 1}})
    assert merged == {"min_dpi": 300, "max": 5, "nested": {"a": 1, "b": 2}}
    assert config.section(s, "missing", {"x": 1}) == {"x": 1}


def test_require_env_missing(monkeypatch):
    monkeypatch.delenv("ETSY_AGENTS_TEST_UNSET", raising=False)
    with pytest.raises(config.MissingSecretError):
        config.require_env("ETSY_AGENTS_TEST_UNSET")


def test_blocklist_does_not_match_across_strings_or_list_items(bl):
    assert bl.check("wooden toy", "story time").ok
    assert bl.check("wooden toy, story time").ok
    assert bl.check_obj({"tags": ["hello", "kitty cat"]}).ok
    assert "Toy Story" in bl.check("toy - story party").blocked


def test_find_urls_edge_cases():
    assert compliance.find_urls("see etsy.me/abc")
    assert compliance.find_urls("shop at example.co.uk")
    assert compliance.find_urls("HTTPS://EXAMPLE.ORG")
    assert not compliance.find_urls("Fun for kids.Shop now")


def test_write_json_temp_file_not_globbed(tmp_path, monkeypatch):
    seen = []
    real_replace = items.os.replace

    def spy(src, dst):
        seen.append(sorted(p.name for p in tmp_path.glob("*.json")))
        real_replace(src, dst)

    monkeypatch.setattr(items.os, "replace", spy)
    items.write_json(tmp_path / "a.json", {"x": 1})
    assert seen == [[]]


@pytest.mark.parametrize("bad", ["../x", "2026-13-01", "20261015", "a/b", ""])
def test_item_id_rejects_bad_dates(bad):
    with pytest.raises(ValueError):
        items.new_item_id(bad, "stickers", "frogs")


def test_today_validates_override(monkeypatch):
    monkeypatch.setenv("PIPELINE_DATE", "../../etc")
    with pytest.raises(ValueError):
        config.today()
    monkeypatch.setenv("PIPELINE_DATE", "2026-10-15")
    assert config.today() == "2026-10-15"


def test_review_dir_for(tmp_path):
    s = config.load_settings()
    assert items.review_dir_for(items.output_root(s) / "x", s) == items.review_root(s)
    assert items.review_dir_for(tmp_path / "x", s) == tmp_path / "review"


def test_clear_downstream(tmp_path):
    d = tmp_path / "item"
    (d / "mockups").mkdir(parents=True)
    for name in ("qa_report.json", "listing.json", "manifest.json"):
        (d / name).write_text("{}")
    items.clear_downstream(d)
    assert sorted(p.name for p in d.iterdir()) == ["manifest.json"]
    (d / "publish.json").write_text("{}")
    with pytest.raises(RuntimeError):
        items.clear_downstream(d)
    (d / "publish.json").write_text("not json")  # unreadable counts as a real draft
    with pytest.raises(RuntimeError):
        items.clear_downstream(d)


def test_clear_downstream_drops_dry_run_record_and_reopens_approval(tmp_path):
    out = tmp_path / "out"
    d = out / "2026-10-15-stickers-frogs-1a2b"
    d.mkdir(parents=True)
    items.write_json(d / "publish.json", {"listing_id": 1, "dry_run": True})
    entry = items.review_entry_path(d.name, out / "review")
    items.write_json(entry, {"item_id": d.name, "status": "approved", "content_digest": "abc",
                             "needs_review": True, "reasons": ["x"]})
    items.clear_downstream(d)
    assert not (d / "publish.json").exists()
    e = items.read_json(entry)
    assert e["status"] == "pending" and e["previous_status"] == "approved" and e["reopened"]
    assert e["content_digest"] is None and e["reasons"] == ["x"]
    items.write_json(entry, {"item_id": d.name, "status": "rejected", "decision_note": "no"})
    items.clear_downstream(d)
    assert items.read_json(entry)["status"] == "rejected"  # a rejection always stays


def test_content_count_rules():
    cc = items.content_count
    assert cc({"pack": {"activity_pages": 12}, "files": []}) == 12
    assert cc({"pack": {"pages": True}, "files": [{"kind": "pdf", "pages": 21}]}) == 21  # bools are not counts
    assert cc({"niche": "stickers", "pack": {"stickers": 24}, "assets": [{"kind": "png_transparent"}]}) == 24
    zips = [{"kind": "zip", "count": 12, "part": k, "parts": 2} for k in (1, 2)]
    assert cc({"niche": "stickers", "files": zips}) == 24  # every ZIP part counts
    assets = [{"kind": "png_transparent"}] * 5
    assert cc({"niche": "stickers", "assets": assets, "files": zips}) == 5
    assert cc({"niche": "stickers", "files": []}, 7) == 7 and cc({"niche": "stickers"}) is None


def test_check_date_and_today_reject_other_iso_forms(monkeypatch):
    for bad in ("2026-W42-4", "20261015", "2026-10-15\n", "2026-02-30", None):
        with pytest.raises(ValueError):
            config.check_date(bad)
    assert items.check_date("2026-10-15") == "2026-10-15"
    monkeypatch.setenv("PIPELINE_DATE", "2026-W42-4")
    with pytest.raises(ValueError):
        config.today()


def test_review_dir_for_root_and_entry_path(tmp_path):
    s = config.load_settings()
    assert items.review_dir_for_root(items.output_root(s), s) == items.review_root(s)
    assert items.review_dir_for_root(tmp_path, s) == tmp_path.resolve() / "review"
    assert items.review_entry_path("2026-10-15-x-1a2b", tmp_path) == tmp_path / "2026-10-15-x-1a2b.json"
    for bad in ("../x", "a/b", "", ".hidden", "a..b"):
        with pytest.raises(ValueError):
            items.review_entry_path(bad, tmp_path)


def test_placeholder_shop_name():
    assert config.is_placeholder_shop_name({"name": " YOUR_SHOP_NAME "})
    assert config.is_placeholder_shop_name({})
    assert not config.is_placeholder_shop_name({"name": "Sunny Prints"})


# ------------------------------------------------------------------ pipeline orchestrator
# The stage entry points are replaced by small fakes, so these tests check only how
# pipeline.run strings the stages together (reuse, gates, exit codes) and stay fast.

DATE = "2026-10-15"


def _brief_doc(niche, date, themes, listing_count=12, **brief_extra):
    briefs = [{"brief_id": f"{niche}-{date}-{i:02d}", "niche": niche, "theme": t, "keywords": [t],
               "evidence": {"listing_count": listing_count, "score": 0.5},
               "compliance": {"blocked": [], "flagged": []}, "needs_review": False, **brief_extra}
              for i, t in enumerate(themes, 1)]
    return {"schema": "brief/v1", "niche": niche, "created": date, "shop_id": "main",
            "sources": [{"tool": "erank", "file": "keywords.csv", "rows": 50}], "listing_count": 50,
            "min_listings_per_pattern": 10,
            "patterns": {"themes": [{"name": t, "listing_count": listing_count} for t in themes]},
            "briefs": briefs}


@pytest.fixture
def pipe(tmp_path, monkeypatch):
    """Fake stages plus tmp dirs. pipe.calls records every stage call."""
    import generators
    import listing
    import publisher
    import qa
    import research
    from pipeline import run as prun

    class P:
        out = tmp_path / "out"
        briefs = tmp_path / "briefs"
        calls = {"research": [], "generate": [], "qa": [], "listing": [], "publish": []}
        publish_error = None
        generate_error = None
        settings = config.load_settings()

        def write_brief(self, niche, themes, date=DATE, **kw):
            return items.write_json(self.briefs / f"{niche}-{date}.json", _brief_doc(niche, date, themes, **kw))

        def run(self, **kw):
            kw.setdefault("stages", ("generate", "qa", "listing", "publish"))
            kw.setdefault("dry_run", True)
            kw.setdefault("date", DATE)
            summary = prun.run(out_root=self.out, briefs_dir=self.briefs, settings=self.settings, **kw)
            return summary, prun.exit_code(summary)

    p = P()

    def fake_research(**kw):
        p.calls["research"].append(kw)
        return []

    def fake_generate(brief, *, brief_file=None, shop_id="main", out_root=None, date=None, dry_run=False,
                      settings=None, client=None):
        p.calls["generate"].append((brief["brief_id"], date, settings))
        if p.generate_error:
            raise p.generate_error
        d = items.item_dir(items.new_item_id(date, brief["niche"], brief["theme"], salt=brief["brief_id"]), out_root)
        if (d / "publish.json").exists():
            return [d]
        items.write_manifest(d, {"item_id": d.name, "niche": brief["niche"], "shop_id": shop_id,
                                 "brief_id": brief["brief_id"], "theme": brief["theme"], "created": date,
                                 "status": "generated", "needs_review": bool(brief.get("needs_review"))})
        return [d]

    def fake_qa(d, *, settings=None, review_dir=None, mockups=True):
        p.calls["qa"].append(d.name)
        try:
            m = items.read_manifest(d)
        except ValueError:
            return {"item_id": d.name, "passed": False,
                    "checks": [{"name": "manifest_readable", "passed": False, "severity": "error"}]}
        report = {"item_id": d.name, "passed": True, "needs_review": m.get("needs_review", False), "checks": []}
        items.write_json(d / "qa_report.json", report)
        items.set_status(d, "qa_passed")
        entry = items.review_entry_path(d.name, items.review_dir_for(d, settings))
        old = items.read_json(entry) if entry.exists() else {}
        items.write_json(entry, {"item_id": d.name, "status": old.get("status", "pending"),
                                 "needs_review": report["needs_review"]})
        return report

    def fake_listing(d, *, client=None, dry_run=False, settings=None, review_dir=None):
        p.calls["listing"].append(d.name)
        items.write_json(d / "listing.json", {"title": "T", "needs_review": False})
        items.set_status(d, "listed")
        return {"title": "T"}

    def fake_publish(d, *, client=None, dry_run=False, settings=None, review_dir=None):
        p.calls["publish"].append(d.name)
        if p.publish_error:
            raise p.publish_error
        items.write_json(d / "publish.json", {"listing_id": 7, "dry_run": dry_run, "complete": True})
        items.set_status(d, "drafted")
        return {"listing_id": 7, "dry_run": dry_run}

    monkeypatch.setattr(research, "run", fake_research)
    monkeypatch.setattr(generators, "generate", fake_generate)
    monkeypatch.setattr(qa, "run", fake_qa)
    monkeypatch.setattr(listing, "write_listing", fake_listing)
    monkeypatch.setattr(publisher, "publish_draft", fake_publish)
    return p


def test_pipeline_happy_path_passes_settings_and_exits_0(pipe):
    pipe.write_brief("stickers", ["frogs", "cats"])
    summary, code = pipe.run()
    assert code == 0 and len(pipe.calls["publish"]) == 2
    assert all(s is pipe.settings for _, _, s in pipe.calls["generate"])
    assert summary["path"].endswith(".json") and "/runs/run-2026-10-15-" in summary["path"]


@pytest.mark.parametrize("error, code", [(RuntimeError("Etsy 500"), 1), ("auth", 2), ("safety", 2), ("secret", 2)])
def test_pipeline_publish_failures_set_exit_code(pipe, error, code):
    import publisher

    pipe.write_brief("stickers", ["frogs", "cats"])
    pipe.publish_error = {"auth": publisher.AuthRequired("no tokens"),
                          "safety": publisher.SafetyViolation("state=active"),
                          "secret": config.MissingSecretError("ETSY_API_KEYSTRING is not set")}.get(error, error)
    summary, got = pipe.run()
    assert got == code
    # A setup error stops the stage instead of being retried once per item.
    assert len(pipe.calls["publish"]) == (1 if code == 2 else 2)


def test_pipeline_generate_errors_set_exit_code(pipe):
    pipe.write_brief("stickers", ["frogs", "cats"])
    pipe.generate_error = config.MissingSecretError("GOOGLE_API_KEY is not set")
    summary, code = pipe.run()
    assert code == 2 and len(pipe.calls["generate"]) == 1
    pipe.generate_error = ValueError("bad brief")
    summary, code = pipe.run()
    assert code == 1 and summary["errors"]


def test_pipeline_exit_code_counts_stages_without_generate(pipe):
    pipe.write_brief("stickers", ["frogs"])
    pipe.run(stages=("generate",))
    (d,) = items.iter_item_dirs(pipe.out)
    (d / "manifest.json").write_text("{trunc", encoding="utf-8")
    summary, code = pipe.run(stages=("qa", "listing"))
    assert code == 1 and summary["items"][d.name]["qa"]["ok"] is False


def test_pipeline_rerun_reuses_items_and_regenerate_rebuilds_in_place(pipe):
    pipe.write_brief("stickers", ["frogs"])
    pipe.run(stages=("generate", "qa", "listing"))
    pipe.publish_error = RuntimeError("Etsy 503")
    pipe.run(stages=("publish",))
    pipe.publish_error = None
    # A later rerun (other date) retries publishing without generating again.
    summary, code = pipe.run(date="2026-10-16")
    assert code == 0 and len(pipe.calls["generate"]) == 1 and len(pipe.calls["publish"]) == 2
    (d,) = items.iter_item_dirs(pipe.out)
    assert "reused" in summary["items"][d.name]["generate"]["detail"]
    # Items with a real Etsy draft are never rebuilt, even with --regenerate.
    rec = items.read_json(d / "publish.json")
    items.write_json(d / "publish.json", {**rec, "dry_run": False})
    pipe.run(date="2026-10-16", regenerate=True)
    assert len(pipe.calls["generate"]) == 1
    (d / "publish.json").unlink()
    items.set_status(d, "listed")
    pipe.run(date="2026-10-16", regenerate=True, stages=("generate",))
    assert pipe.calls["generate"][-1][1] == DATE  # rebuilt in its own folder, not a new one
    assert [x.name for x in items.iter_item_dirs(pipe.out)] == [d.name]


def test_pipeline_max_items_counts_reused_items(pipe):
    pipe.write_brief("stickers", ["frogs", "cats", "owls"])
    pipe.run(max_items=2)
    summary, _ = pipe.run(max_items=2)
    assert len(pipe.calls["generate"]) == 2
    assert any("1 brief(s) not used" in n for n in summary["notes"])


def test_pipeline_skips_rejected_items_and_waits_for_review(pipe):
    pipe.write_brief("stickers", ["frogs"], needs_review=True)
    pipe.write_brief("coloring_pages", ["owls"])
    summary, code = pipe.run()
    flagged = next(d for d in items.iter_item_dirs(pipe.out) if "stickers" in d.name)
    assert code == 0 and flagged.name in summary["awaiting_review"]
    assert flagged.name not in pipe.calls["publish"] and len(pipe.calls["publish"]) == 1
    assert summary["items"][flagged.name]["publish"]["skipped"]
    # A rejected item is neither rebuilt nor listed again.
    entry = items.review_entry_path(flagged.name, pipe.out / "review")
    items.write_json(entry, {**items.read_json(entry), "status": "rejected"})
    items.set_status(flagged, "qa_passed")
    pipe.calls["listing"].clear()
    summary, code = pipe.run(regenerate=True, stages=("generate", "listing"))
    assert code == 0 and flagged.name not in pipe.calls["listing"]
    assert all(bid.startswith("coloring") for bid, _, _ in pipe.calls["generate"][2:])
    summary, code = pipe.run(stages=("listing",))
    assert flagged.name not in pipe.calls["listing"]
    assert summary["items"][flagged.name]["listing"]["skipped"]


def test_pipeline_refuses_brief_files_that_break_the_brief_rules(pipe):
    pipe.write_brief("stickers", ["frogs"], listing_count=2)
    doc = _brief_doc("coloring_pages", DATE, ["owls"])
    doc["briefs"][0]["reference_image"] = "il_fullxfull.4412345678_abcd.jpg"
    items.write_json(pipe.briefs / f"coloring_pages-{DATE}.json", doc)
    (pipe.briefs / f"activity_sheets-{DATE}.json").write_text("{", encoding="utf-8")
    summary, code = pipe.run()
    assert code == 1 and pipe.calls["generate"] == []
    assert len(summary["errors"]) == 3


def test_pipeline_no_briefs_says_why(pipe):
    pipe.briefs.mkdir(parents=True)
    summary, code = pipe.run()
    assert code == 1 and "python -m research" in summary["errors"][0]


def test_pipeline_live_runs_skip_stale_brief_files(pipe):
    pipe.write_brief("stickers", ["frogs"], date="2026-10-01")
    summary, code = pipe.run(dry_run=False, stages=("generate",))
    assert code == 1 and pipe.calls["generate"] == [] and "days old" in summary["errors"][0]
    summary, code = pipe.run(dry_run=True, stages=("generate",))   # dry runs may reuse old briefs
    assert code == 0 and len(pipe.calls["generate"]) == 1
    summary, code = pipe.run(dry_run=False, stages=("generate",),     # named files are exempt
                             brief_files=[pipe.briefs / "stickers-2026-10-01.json"])
    assert code == 0


def test_pipeline_unreadable_manifest_does_not_stop_the_run(pipe):
    from pipeline.run import status_table

    pipe.write_brief("stickers", ["frogs"])
    pipe.run(stages=("generate", "qa"))
    broken = pipe.out / "2026-10-01-stickers-broken-0000"
    broken.mkdir()
    (broken / "manifest.json").write_text("", encoding="utf-8")
    summary, code = pipe.run(stages=("listing", "publish"))
    assert code == 1 and len(pipe.calls["publish"]) == 1
    assert summary["items"][broken.name]["manifest"]["ok"] is False
    assert "unreadable" in status_table(pipe.out, settings=pipe.settings)


def test_pipeline_filters_niche_and_caps_listing_without_generate(pipe):
    pipe.write_brief("stickers", ["frogs", "cats"])
    pipe.write_brief("coloring_pages", ["owls"])
    pipe.run(stages=("generate", "qa"))
    summary, _ = pipe.run(stages=("listing",), niche="stickers", max_items=1)
    assert len(pipe.calls["listing"]) == 1 and "stickers" in pipe.calls["listing"][0]


def test_pipeline_research_stage(pipe):
    import research

    pipe.run(stages=("research",))
    assert pipe.calls["research"][0]["briefs_dir"] == pipe.briefs
    assert all(c["settings"] is pipe.settings for c in pipe.calls["research"])
    # A dry run without --briefs-dir never writes into data/briefs.
    from pipeline import run as prun
    prun.run(stages=("research",), dry_run=True, out_root=pipe.out, date=DATE, settings=pipe.settings)
    assert pipe.calls["research"][-1]["briefs_dir"] == pipe.out / "briefs"

    def no_inputs(**kw):
        raise research.NoInputsError("No research exports found")

    research.run = no_inputs  # undone by the fixture's monkeypatch
    summary, code = pipe.run(stages=("research",))
    assert code == 0 and summary["notes"] == ["research: No research exports found"]


def test_pipeline_summary_files_are_not_overwritten(pipe):
    pipe.briefs.mkdir(parents=True)
    a, _ = pipe.run(stages=("qa",))
    b, _ = pipe.run(stages=("qa",))
    assert a["path"] != b["path"] and len(list((pipe.out / "runs").glob("*.json"))) == 2


def test_pipeline_status_uses_configured_review_dir(tmp_path):
    from pipeline.run import status_table

    s = config.load_settings()
    s["paths"] = {"output_dir": str(tmp_path / "out"), "review_dir": str(tmp_path / "queue")}
    items.write_manifest(tmp_path / "out" / "item-1", {"status": "qa_passed"})
    items.write_json(tmp_path / "queue" / "item-1.json", {"status": "approved"})
    assert "review=approved" in status_table(tmp_path / "out", settings=s)


@pytest.mark.parametrize("args", [["--date", "x/../../escaped"], ["--date", "2026-02-30"], ["--shop", "nope"],
                                  ["--niche", "foo"], ["--max-items", "-1"], ["--stages", "draft"]])
def test_pipeline_cli_validates_arguments(tmp_path, args, capsys):
    from pipeline.__main__ import main

    argv = ["run", "--dry-run", "--out", str(tmp_path)] + ([] if "--stages" in args else ["--stages", "research"])
    with pytest.raises(SystemExit) as e:
        main(argv + args)
    err = capsys.readouterr().err
    assert e.value.code == 2 and "pipeline: error: " in err and "Traceback" not in err
    assert not (tmp_path / "runs").exists()


def test_pipeline_cli_exit_code_and_step_summary(pipe, monkeypatch, tmp_path):
    from pipeline.__main__ import main

    import publisher

    pipe.write_brief("stickers", ["frogs"])
    step = tmp_path / "step.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step))
    pipe.publish_error = publisher.AuthRequired("run python -m publisher auth")
    code = main(["run", "--dry-run", "--stages", "generate,qa,listing,publish", "--date", DATE,
                 "--out", str(pipe.out), "--briefs-dir", str(pipe.briefs)])
    assert code == 2 and "publish=FAIL" in step.read_text()


def test_pipeline_publish_stage_requires_draft_only_mode(pipe):
    pipe.write_brief("stickers", ["frogs"])
    pipe.settings = {**pipe.settings, "compliance": {**pipe.settings["compliance"], "publisher_mode": "active"}}
    summary, code = pipe.run()
    assert code == 2 and pipe.calls["publish"] == [] and "draft_only" in summary["setup_errors"][0]
