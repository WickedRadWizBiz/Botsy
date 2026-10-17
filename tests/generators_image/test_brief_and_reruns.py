"""Audit regressions: briefs are re-checked before generation, brief free text never reaches a
prompt, reruns drop stale QA/listing outputs, dry runs and real items never mix, and
single-species themes stay on theme."""
import copy
import hashlib
import json
import shutil

import pytest
import yaml

from core.items import read_manifest, write_json
from generators import coloring_pages, stickers
from generators.imaging import subjects
from generators.imaging.client import FakeImageClient
from generators.imaging.prompts import STYLE_CLAUSES, clean_style_notes, coloring_prompt, sticker_prompt

DATE = "2026-10-15"


class PaidFake(FakeImageClient):
    """Offline art standing in for a real (paid) client: not swapped out, recorded as non-dry."""
    model = "paid-model"


def _doc(*briefs, floor=10, **header):
    return {"schema": "brief/v1", "niche": briefs[0]["niche"], "created": DATE, "shop_id": "main",
            "listing_count": 250, "min_listings_per_pattern": floor, **header, "briefs": list(briefs)}


def _md5s(d):
    return {p.relative_to(d).as_posix(): hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file()}


# --- brief checks ------------------------------------------------------------------------------

def test_hand_written_competitor_brief_is_refused_before_any_call(tmp_path, settings, coloring_brief):
    """The audit's repro: 1-listing evidence, a listing id and an image name in the brief."""
    bad = {**coloring_brief, "evidence": {"listing_count": 1},
           "keywords": ["9100000001", "farm animals"],
           "style_notes": "copy exactly the listing Farm Animals, see image il_570xN.9100000001.jpg"}
    path = tmp_path / "coloring_pages-2026-10-15.json"
    path.write_text(json.dumps(_doc(bad, floor=1)))
    fake = FakeImageClient("coloring")
    with pytest.raises(ValueError, match="compliance") as exc:
        coloring_pages.generate(bad, brief_file=path, out_root=tmp_path / "out", date=DATE, dry_run=True,
                                client=fake, settings=settings)
    msg = str(exc.value)
    assert "min_listings_per_pattern" in msg and "evidence.listing_count" in msg and "listing id" in msg
    assert fake.calls == [] and not (tmp_path / "out").exists()


@pytest.mark.parametrize("change,match", [
    ({"evidence": {"listing_count": 9}}, "evidence.listing_count 9"),
    ({"evidence": None}, "evidence.listing_count None"),
    ({"evidence": "lots"}, "malformed"),
    ({"keywords": ["farm animals", "9100000001"]}, "listing id"),
    ({"theme": "farm animals il_570xN.jpg"}, "image name"),
    ({"listing_ids": [1, 2]}, "forbidden field 'listing_ids'"),
])
def test_brief_without_ten_listing_evidence_or_with_identifiers_is_refused(tmp_path, settings, sticker_brief,
                                                                           change, match):
    fake = FakeImageClient("sticker")
    with pytest.raises(ValueError, match=match):
        stickers.generate({**sticker_brief, **change}, out_root=tmp_path, date=DATE, dry_run=True, client=fake,
                          settings=settings)
    assert fake.calls == [] and list(tmp_path.iterdir()) == []


def test_brief_file_header_is_checked_too(tmp_path, settings, coloring_brief):
    fake = FakeImageClient("coloring")
    path = tmp_path / "b.json"
    patterns = {"themes": [{"name": "farm animals", "listing_count": 3}]}
    path.write_text(json.dumps(_doc(coloring_brief, patterns=patterns)))
    with pytest.raises(ValueError, match="patterns.themes"):
        coloring_pages.generate(coloring_brief, brief_file=path, out_root=tmp_path / "o", date=DATE, dry_run=True,
                                client=fake, settings=settings)
    path.write_text(json.dumps(_doc({**coloring_brief, "evidence": {"listing_count": 12}}, floor=15)))
    with pytest.raises(ValueError, match="< 15"):                  # the file's own floor applies
        coloring_pages.generate({**coloring_brief, "evidence": {"listing_count": 12}}, brief_file=path,
                                out_root=tmp_path / "o", date=DATE, dry_run=True, client=fake, settings=settings)
    path.write_text(json.dumps(_doc(coloring_brief)))
    (d,) = coloring_pages.generate({**coloring_brief, "page_count": 1}, brief_file=path, out_root=tmp_path / "o",
                                   date=DATE, dry_run=True, settings=settings)
    assert read_manifest(d)["brief_file"] == path.resolve().as_posix()
    assert fake.calls == []


def test_unlisted_style_notes_never_reach_prompts_and_mark_the_item(tmp_path, settings, coloring_brief):
    brief = {**coloring_brief, "page_count": 1,
             "style_notes": "bold thick outlines, in the manner of the bestselling farm pack, no shading"}
    (d,) = coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings)
    m = read_manifest(d)
    assert "Style: bold thick outlines, no shading." in m["prompts"][0]
    assert "bestselling" not in m["prompts"][0] and "manner" not in m["prompts"][0]
    assert m["needs_review"] and any("style_notes" in r for r in m["review_reasons"])
    assert not any("bestselling" in r for r in m["review_reasons"])    # the text itself is not copied


def test_research_default_style_notes_are_all_allowed():
    from research.defaults import DEFAULTS

    for niche in ("coloring_pages", "stickers"):
        notes = DEFAULTS["style_notes"][niche]
        kept = clean_style_notes(notes).split(", ")
        assert all(c in STYLE_CLAUSES for c in kept)
        assert len(kept) == len([c for c in notes.split(",") if "transparent" not in c and "die-cut" not in c])
    assert set(clean_style_notes(DEFAULTS["default_style_notes"]).split(", ")) <= STYLE_CLAUSES


def test_audience_free_text_never_reaches_prompts(sticker_brief, coloring_brief):
    sticker_brief["audience"] = "fans of the DemoShop35 bestseller, teens and adults"
    coloring_brief["audience"] = "kids 4-8 who loved the DemoShop35 pack"
    s, c = sticker_prompt(sticker_brief, "an apple"), coloring_prompt(coloring_brief, "a cow")
    assert "DemoShop35" not in s + c
    assert "Audience: teens and adults." in s and "coloring page for kids ages 4-8," in c


@pytest.mark.parametrize("price,expected", [(0, 3.0), (-2, 3.0), ("4.00", 3.0), (True, 3.0), (5.5, 5.5)])
def test_brief_price_must_be_a_positive_number(tmp_path, settings, coloring_brief, price, expected):
    settings["shops"][0]["default_price_usd"] = {"coloring_pages": 3.0}
    (d,) = coloring_pages.generate({**coloring_brief, "page_count": 1, "price_usd": price}, out_root=tmp_path,
                                   date=DATE, dry_run=True, settings=settings)
    m = read_manifest(d)
    assert m["price_usd"] == expected
    assert m["needs_review"] == (expected != price) == any("price_usd" in r for r in m["review_reasons"])


def test_relative_brief_file_is_repo_relative_from_any_cwd(tmp_path, settings, coloring_brief, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (d,) = coloring_pages.generate({**coloring_brief, "page_count": 1}, out_root=tmp_path / "o",
                                   brief_file="data/briefs/coloring_pages-2026-10-15.json", date=DATE,
                                   dry_run=True, settings=settings)
    assert read_manifest(d)["brief_file"] == "data/briefs/coloring_pages-2026-10-15.json"


# --- reruns ------------------------------------------------------------------------------------

def _downstream(d):
    write_json(d / "qa_report.json", {"passed": True, "mockups": ["mockups/mockup-01.jpg"]})
    write_json(d / "listing.json", {"image_paths": ["mockups/mockup-01.jpg"]})
    (d / "mockups").mkdir()
    (d / "mockups" / "mockup-01.jpg").write_bytes(b"old art")


@pytest.mark.parametrize("module,brief_name", [(coloring_pages, "coloring_brief"), (stickers, "sticker_brief")])
def test_regeneration_drops_stale_qa_mockups_and_listing(tmp_path, settings, request, module, brief_name):
    brief = {**request.getfixturevalue(brief_name), "page_count": 1}
    (d,) = module.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings)
    _downstream(d)
    module.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings)
    assert not any((d / n).exists() for n in ("qa_report.json", "listing.json", "mockups"))
    assert read_manifest(d)["status"] == "generated"


def test_failed_regeneration_keeps_downstream_outputs(tmp_path, settings, coloring_brief):
    """Only a rebuild that completes replaces the item, QA outputs included."""
    brief = {**coloring_brief, "page_count": 1}
    (d,) = coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings)
    _downstream(d)

    class Refuses(FakeImageClient):
        def generate(self, *a, **k):
            raise ConnectionError("network down")

    # A complete design set is reused (nothing is paid for twice), so the rerun needs new pages to fail.
    shutil.rmtree(tmp_path / "sets")
    with pytest.raises(ConnectionError):
        coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, client=Refuses("coloring"),
                                settings=settings)
    assert (d / "listing.json").exists() and (d / "mockups" / "mockup-01.jpg").exists()


def test_dry_run_draft_record_does_not_block_the_real_item(tmp_path, settings, coloring_brief):
    brief = {**coloring_brief, "page_count": 1}
    (d,) = coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings)
    _downstream(d)
    write_json(d / "publish.json", {"schema": "publish/v1", "listing_id": 900000003, "dry_run": True})
    paid = PaidFake("coloring", long_side_px=300)
    coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=False, client=paid, settings=settings)
    m = read_manifest(d)
    assert paid.calls and m["generator"] == {"name": "coloring_pages", "model": "paid-model", "dry_run": False}
    assert not any((d / n).exists() for n in ("publish.json", "qa_report.json", "listing.json", "mockups"))


@pytest.mark.parametrize("record", ['{"dry_run": false, "listing_id": 1}', "{}", "not json"])
def test_real_or_unreadable_draft_record_leaves_the_item_alone(tmp_path, settings, coloring_brief, record):
    brief = {**coloring_brief, "page_count": 1}
    (d,) = coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, settings=settings)
    (d / "publish.json").write_text(record)
    before = _md5s(d)
    paid = PaidFake("coloring", long_side_px=300)
    assert coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=False, client=paid,
                                   settings=settings) == [d]
    assert paid.calls == [] and _md5s(d) == before


def test_dry_run_never_overwrites_a_real_item(tmp_path, settings, coloring_brief):
    brief = {**coloring_brief, "page_count": 1}
    (d,) = coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=False,
                                   client=PaidFake("coloring", long_side_px=300), settings=settings)
    before = _md5s(d)
    fake = FakeImageClient("coloring")
    with pytest.raises(ValueError, match="real item"):
        coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=True, client=fake, settings=settings)
    assert fake.calls == [] and _md5s(d) == before
    coloring_pages.generate(brief, out_root=tmp_path, date=DATE, dry_run=False,              # real over real: ok
                            client=PaidFake("coloring", long_side_px=300), settings=settings)


# --- CLI ---------------------------------------------------------------------------------------

def _cli(niche):
    import importlib

    return importlib.import_module(f"generators.{niche}.__main__").main


def test_cli_dry_run_defaults_to_the_dry_run_dir(tmp_path, base_settings, coloring_brief, capsys):
    s = copy.deepcopy(base_settings)
    s.setdefault("imaging", {})["fake_long_side_px"] = 300
    s["paths"]["output_dir"] = str(tmp_path / "real")
    s.setdefault("pipeline", {})["dry_run_output_dir"] = str(tmp_path / "dry")
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(yaml.safe_dump(s))
    path = tmp_path / "b.json"
    path.write_text(json.dumps(_doc({**coloring_brief, "page_count": 1})))
    assert _cli("coloring_pages")(["--brief", str(path), "--dry-run", "--date", DATE, "--settings", str(cfg)]) == 0
    [d] = capsys.readouterr().out.split()
    assert d.startswith(str(tmp_path / "dry")) and not (tmp_path / "real").exists()


def test_cli_accepts_a_list_and_rejects_bad_json(tmp_path, coloring_brief, capsys):
    good = tmp_path / "list.json"
    good.write_text(json.dumps([{**coloring_brief, "page_count": 1}]))
    out = tmp_path / "o"
    assert _cli("stickers")(["--brief", str(good), "--all", "--dry-run", "--out", str(out)]) == 0   # no sticker brief
    assert _cli("coloring_pages")(["--brief", str(good), "--dry-run", "--out", str(out), "--date", DATE]) == 0
    assert len(capsys.readouterr().out.split()) == 1
    for name, text in (("bad.json", "{not json"), ("scalar.json", "7"), ("mixed.json", '[{"theme": "x"}, 3]')):
        (tmp_path / name).write_text(text)
        with pytest.raises(SystemExit) as exc:
            _cli("coloring_pages")(["--brief", str(tmp_path / name), "--dry-run", "--out", str(out)])
        assert exc.value.code == 2


def test_cli_records_an_absolute_brief_path_from_another_cwd(tmp_path, coloring_brief, monkeypatch, capsys):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "one.json").write_text(json.dumps({**coloring_brief, "page_count": 1}))
    monkeypatch.chdir(tmp_path)
    assert _cli("coloring_pages")(["--brief", "b/one.json", "--dry-run", "--out", "o", "--date", DATE]) == 0
    [d] = capsys.readouterr().out.split()
    assert read_manifest(tmp_path / d)["brief_file"] == (tmp_path / "b" / "one.json").resolve().as_posix()


def test_cli_bad_date_is_a_usage_error(tmp_path, coloring_brief):
    path = tmp_path / "one.json"
    path.write_text(json.dumps(coloring_brief))
    with pytest.raises(SystemExit) as exc:
        _cli("coloring_pages")(["--brief", str(path), "--dry-run", "--out", str(tmp_path / "o"), "--date", "2026-13-45"])
    assert exc.value.code == 2 and not (tmp_path / "o").exists()


# --- subjects ----------------------------------------------------------------------------------

@pytest.mark.parametrize("theme,words", [("cats", ("cat", "kitten")), ("kawaii kittens", ("cat", "kitten")),
                                         ("dogs", ("dog", "puppy")), ("cute puppies", ("dog", "puppy"))])
def test_single_species_themes_stay_on_species(theme, words):
    pool = subjects.subject_pool(theme)
    head = pool[:60]
    assert all(any(w in s for w in words) for s in head[:16])
    others = {"a hamster", "a goldfish in a bowl", "a parrot", "a guinea pig", "a turtle"}
    assert not others & set(head)


def test_species_in_a_mixed_table_comes_first_then_templates():
    pool = subjects.subject_pool("sharks")
    assert pool[0] == "a friendly shark" and "a shark parade" in pool[:10]
    assert "an octopus" not in pool[:50]                         # coloring_pages.max_pages
    assert subjects.focus_words("ocean animals") == set()
    assert subjects.subject_pool("ocean animals")[:3] == ["a smiling whale", "a dolphin jumping", "an octopus"]
    assert subjects.matched_keys("cats and dogs") == ["cats", "dogs"]
    assert subjects.matched_keys("pets") == ["pets"]
