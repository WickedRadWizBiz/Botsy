"""Audit regressions for activity packs: the brief is re-checked (10-listing evidence, no ids),
reruns drop stale QA/listing outputs, dry runs and real items never mix, file names fit
Etsy's 70 characters, and helpers are shared with the image generators."""
import copy
import datetime
import json

import pytest
import yaml

from core.items import read_manifest, write_json
from generators.activity_sheets import generate, page_sizes_for
from generators.activity_sheets.__main__ import main

DATE = "2026-10-15"


def _downstream(d):
    write_json(d / "qa_report.json", {"passed": True, "mockups": ["mockups/mockup-01.jpg"]})
    write_json(d / "listing.json", {"image_paths": ["mockups/mockup-01.jpg"]})
    (d / "mockups").mkdir()
    (d / "mockups" / "mockup-01.jpg").write_bytes(b"old pages")


@pytest.mark.parametrize("change,match", [
    ({"evidence": {"listing_count": 3}}, "evidence.listing_count 3"),
    ({"evidence": {}}, "evidence.listing_count None"),
    ({"keywords": ["farm maze", "listing 4012345678"]}, "listing id"),
    ({"style_notes": "like the photo farm-pack.webp"}, "image name"),
    ({"shop_names": ["x"]}, "forbidden field"),
])
def test_brief_is_checked_like_research_checks_it(tmp_path, brief, both_sizes, change, match):
    with pytest.raises(ValueError, match=match):
        generate(dict(brief, **change), out_root=tmp_path, date=DATE, settings=both_sizes)
    assert list(tmp_path.iterdir()) == []


def test_cli_refuses_a_brief_file_under_the_ten_listing_floor(tmp_path, brief, capsys):
    path = tmp_path / "activity_sheets-2026-10-15.json"
    path.write_text(json.dumps({"schema": "brief/v1", "shop_id": "main", "min_listings_per_pattern": 1,
                                "briefs": [dict(brief, page_count=1)]}))
    assert main(["--brief", str(path), "--out", str(tmp_path / "o"), "--date", DATE]) == 1
    assert "min_listings_per_pattern" in capsys.readouterr().err and not (tmp_path / "o").exists()


def test_regeneration_drops_stale_qa_mockups_and_listing(tmp_path, brief, both_sizes):
    b = dict(brief, page_count=1)
    [d] = generate(b, out_root=tmp_path, date=DATE, settings=both_sizes)
    _downstream(d)
    generate(b, out_root=tmp_path, date=DATE, settings=both_sizes)
    assert not any((d / n).exists() for n in ("qa_report.json", "listing.json", "mockups"))


def test_dry_run_draft_record_does_not_block_a_real_rebuild(tmp_path, brief, both_sizes):
    b = dict(brief, page_count=1)
    [d] = generate(b, out_root=tmp_path, date=DATE, dry_run=True, settings=both_sizes)
    _downstream(d)
    write_json(d / "publish.json", {"listing_id": 900000003, "dry_run": True})
    generate(b, out_root=tmp_path, date=DATE, dry_run=False, settings=both_sizes)
    assert read_manifest(d)["generator"]["dry_run"] is False
    assert not any((d / n).exists() for n in ("publish.json", "qa_report.json", "listing.json", "mockups"))
    write_json(d / "publish.json", {"listing_id": 123, "dry_run": False})       # a real draft: hands off
    (d / "files" / "keep.pdf").write_bytes(b"drafted")
    assert generate(b, out_root=tmp_path, date=DATE, settings=both_sizes) == [d]
    assert (d / "files" / "keep.pdf").exists()


def test_dry_run_never_overwrites_a_real_item(tmp_path, brief, both_sizes):
    b = dict(brief, page_count=1)
    [d] = generate(b, out_root=tmp_path, date=DATE, dry_run=False, settings=both_sizes)
    with pytest.raises(ValueError, match="real item"):
        generate(b, out_root=tmp_path, date=DATE, dry_run=True, settings=both_sizes)
    assert read_manifest(d)["generator"]["dry_run"] is False


def test_cli_dry_run_defaults_to_the_dry_run_dir(tmp_path, brief, settings, capsys):
    s = copy.deepcopy(settings)
    s["paths"]["output_dir"] = str(tmp_path / "real")
    s.setdefault("pipeline", {})["dry_run_output_dir"] = str(tmp_path / "dry")
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(yaml.safe_dump(s))
    path = tmp_path / "one.json"
    path.write_text(json.dumps(dict(brief, page_count=1)))
    assert main(["--brief", str(path), "--dry-run", "--date", DATE, "--settings", str(cfg)]) == 0
    [d] = capsys.readouterr().out.split()
    assert d.startswith(str(tmp_path / "dry")) and not (tmp_path / "real").exists()


def test_file_names_fit_etsy_limits(tmp_path, brief, both_sizes):
    s = copy.deepcopy(both_sizes)
    s["print"]["page_sizes"]["us_half_letter_landscape"] = {"width_in": 8.5, "height_in": 5.5}
    b = dict(brief, theme="friendly woodland forest animals and the", page_count=1,
             page_sizes=["us_letter", "us_half_letter_landscape"])
    [d] = generate(b, out_root=tmp_path, date=DATE, settings=s)
    for f in read_manifest(d)["files"]:
        name = f["path"].split("/", 1)[1]
        assert len(name) <= 70 and name.endswith("-activity-pack-1-pages-" + f["page_size"] + ".pdf"), name
        assert (d / f["path"]).is_file()


def test_review_reasons_and_price_rule(tmp_path, brief, both_sizes):
    s = copy.deepcopy(both_sizes)
    s["shops"][0]["default_price_usd"] = {"activity_sheets": 3.0}
    [d] = generate(dict(brief, page_count=1, price_usd=0, needs_review=True), out_root=tmp_path, date=DATE,
                   settings=s)
    m = read_manifest(d)
    assert m["price_usd"] == 3.0 and m["needs_review"]
    assert any("price_usd" in r for r in m["review_reasons"])
    assert "brief marked needs_review by research" in m["review_reasons"]
    [d] = generate(dict(brief, page_count=1), out_root=tmp_path / "b", date=DATE, settings=s)
    m = read_manifest(d)
    assert m["review_reasons"] == [] and not m["needs_review"] and m["price_usd"] == 4.0


def test_helpers_are_shared_with_the_image_generators(tmp_path, brief, both_sizes):
    from generators import coloring_pages, shared, stickers

    assert page_sizes_for is shared.page_sizes_for is coloring_pages.page_sizes_for is stickers.page_sizes_for
    known = list(both_sizes["print"]["page_sizes"])
    assert page_sizes_for({"page_sizes": 5}, both_sizes) == known          # junk -> every size, no crash
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        generate(brief, out_root=tmp_path, date=datetime.date(2026, 10, 15), settings=both_sizes)
    with pytest.raises(ValueError, match="unknown shop"):
        generate(brief, shop_id="nope", out_root=tmp_path, date=DATE, settings=both_sizes)
