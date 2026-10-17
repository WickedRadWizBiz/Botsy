"""Offline end-to-end run of the themed packs and the two new niches (docs/PACKS.md).

research.demo exports for all five niches -> research -> briefs with pack sizes (and a ttrpg
brief made for a game system) -> pipeline dry run of the ttrpg and stationery packs -> QA,
listing and dry-run drafts. Checks the fresh/derived accounting over two weeks, page counts,
overlap copy, the game-system name and disclaimer (listing only: never in a PDF, a file name
or a photo) and the mockups. Everything is in tmp_path; nothing touches output/ or data/.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from core import items
from core.compliance import Blocklist, iter_strings, system_mentions
from core.config import load_settings
from core.render import pdf_info
from core.systems import disclaimer_for, label_for
from pipeline import run as prun
from qa import mockups
from qa.file_text import pdf_text
from research.demo import DEMO_NICHES, write_demo_inputs

pytestmark = pytest.mark.slow

DATE = "2026-10-16"
WEEK_2 = "2026-10-23"
SYSTEM = "dnd5e"            # research.demo: most "classic fantasy" ttrpg rows say "for D&D 5e"


def _settings() -> dict:
    settings = copy.deepcopy(load_settings())
    settings["research"]["stage"] = "discovery"     # never the refined stage of a real checkout
    return settings


def _run(tmp_path: Path, date: str, *, stages, brief_files=None) -> dict:
    return prun.run(dry_run=True, inputs_dir=tmp_path / "inputs", briefs_dir=tmp_path / "briefs",
                    brief_files=brief_files, out_root=tmp_path / "out", max_items=40, date=date,
                    settings=_settings(), stages=stages)


def _trimmed(tmp_path: Path, niche: str, date: str, themes: list[str]) -> Path:
    """The niche's brief file with only the briefs of `themes` (the run's packs)."""
    doc = items.read_json(tmp_path / "briefs" / f"{niche}-{date}.json")
    doc["briefs"] = [b for b in doc["briefs"] if b["theme"] in themes]
    assert [b["theme"] for b in doc["briefs"]] == themes
    path = tmp_path / "picked" / f"{niche}-{date}.json"
    items.write_json(path, doc)
    return path


def _manifests(out: Path) -> dict[str, dict]:
    return {d.name: items.read_manifest(d) for d in items.iter_item_dirs(out)}


def test_five_niche_packs_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("ETSY_TOKEN_FILE", str(tmp_path / "etsy_tokens.json"))
    write_demo_inputs(tmp_path / "inputs", niches=DEMO_NICHES)
    out = tmp_path / "out"
    bl = Blocklist.load()

    # 1. Research: one brief file per niche; every brief has the 5- and 20-page packs (plus
    #    the top sellers' sizes) and page_count = the largest; only the ttrpg brief whose rows
    #    name a system carries its id, and no brief text names it.
    research = _run(tmp_path, DATE, stages=("research",))
    assert not research["errors"], research["errors"]
    docs = {n: items.read_json(tmp_path / "briefs" / f"{n}-{DATE}.json") for n in DEMO_NICHES}
    for niche, doc in docs.items():
        assert doc["briefs"], niche
        for b in doc["briefs"]:
            assert {5, 20} <= set(b["pack_sizes"]) and b["pack_sizes"] == sorted(set(b["pack_sizes"]))
            assert b["page_count"] == max(b["pack_sizes"])
        opp = [b["evidence"]["opportunity"] for b in doc["briefs"]]
        assert opp == sorted(opp, reverse=True), niche
        texts = [s for b in doc["briefs"] for k, v in b.items() if k != "system" for s in iter_strings(v)]
        assert not system_mentions(texts) and not bl.check(*texts).blocked, niche
    systems = {b["theme"]: b["system"] for b in docs["ttrpg"]["briefs"]}
    assert systems["classic fantasy"] == SYSTEM
    assert {s for t, s in systems.items() if t != "classic fantasy"} == {"generic"}
    assert all("system" not in b for n in DEMO_NICHES if n != "ttrpg" for b in docs[n]["briefs"])

    # 2. Generate, QA, list and draft (dry run) the packs of three ttrpg briefs and the ADHD
    #    stationery brief.
    ttrpg_themes = [b["theme"] for b in docs["ttrpg"]["briefs"]][:3]
    assert ttrpg_themes[0] == "classic fantasy"
    picked = [_trimmed(tmp_path, "ttrpg", DATE, ttrpg_themes), _trimmed(tmp_path, "stationery", DATE, ["adhd"])]
    summary = _run(tmp_path, DATE, stages=("generate", "qa", "listing", "publish"), brief_files=picked)
    assert prun.exit_code(summary) == 0, prun.format_summary(summary)
    manifests = _manifests(out)
    wanted = sum(len(b["pack_sizes"]) for b in docs["ttrpg"]["briefs"][:3]) + \
        len(next(b for b in docs["stationery"]["briefs"] if b["theme"] == "adhd")["pack_sizes"])
    assert len(manifests) == len(summary["packs"]) == wanted

    # Fresh / derived accounting: the largest size of each brief is its master; out of every
    # 3 five-page packs of a niche the 3rd is fresh (packs.fresh_every {5: 3}).
    five = sorted(((m["pack"]["n"], m["pack"]["source"]) for m in manifests.values()
                   if m["niche"] == "ttrpg" and m["pack"]["size"] == 5))
    assert five == [(1, "derived"), (2, "derived"), (3, "fresh")]
    state = items.read_json(out / "sets" / "packs_state.json")
    counters = state["counters"]["main"]
    assert counters["ttrpg"]["5"] == 3 and counters["stationery"] == {"5": 1}
    assert "20" not in counters["ttrpg"]                         # masters never count

    for item_id, m in manifests.items():
        d = out / item_id
        pack = m["pack"]
        rec = summary["items"][item_id]
        assert all(rec[s]["ok"] for s in ("generate", "qa", "listing", "publish")), rec
        report = items.read_json(d / "qa_report.json")
        assert report["passed"] and not report["needs_review"], (item_id, report["reasons"])
        names = {c["name"] for c in report["checks"]}
        assert {"pack_page_count", "pack_page_ids", "print_font_size", "print_line_spacing", "mockup_text"} <= names
        assert len(report["mockups"]) == 6                       # five photos plus the desk scene
        assert mockups.pack_badge(m, None) == f"{pack['size']} PAGES"

        # Page counts: every PDF holds exactly the pack's pages, each page once.
        assert len(pack["page_ids"]) == len(set(pack["page_ids"])) == pack["size"]
        pdfs = [f for f in m["files"] if f["kind"] == "pdf"]
        assert {f["page_size"] for f in pdfs} == {"us_letter", "a4"}
        for f in pdfs:
            assert pdf_info(d / f["path"])["pages"] == f["pages"] == pack["size"]

        # A game system's name is never printed (pages or PDF properties), in a file name or
        # on a photo.
        printed = [t for f in pdfs for t in pdf_text(d / f["path"])]
        file_names = [Path(f["path"]).name for f in m["files"]] + [Path(p).name for p in report["mockups"]]
        assert not bl.check(*printed, *file_names).blocked and not system_mentions(printed + file_names)

        # Listing: pack title, overlap copy, and the system named only where the item is for it.
        listing = items.read_json(d / "listing.json")
        title, desc = listing["title"], listing["description"]
        assert f"{pack['size']} Pages" in title and len(listing["tags"]) == 13
        if pack["source"] == "derived":
            assert f"also included in our {max(r['size'] for r in pack['related'])}-page" in desc
        elif pack["source"] == "fresh":
            assert "All-new pages, not included in our other packs" in desc
        elif any(r["source"] == "derived" for r in pack["related"]):     # a master cut into smaller packs
            assert "contains every page of our" in desc
        else:                                                           # its smaller pack is all-new
            assert "contains every page" not in desc and "This theme also comes in packs of" in desc
        copy_text = [title, desc, *listing["tags"]]
        if m.get("system") == SYSTEM:
            assert m["niche"] == "ttrpg" and m["theme"] == "classic fantasy"
            assert f"for {label_for(SYSTEM)}" in title
            assert disclaimer_for(SYSTEM) in desc and system_mentions(copy_text) == [SYSTEM]
            assert set(listing["compliance"]["allowed"]) >= {"D&D", "5e"}
        else:
            assert m.get("system") in (None, "generic")
            assert not system_mentions(copy_text) and "allowed" not in listing["compliance"]
        assert not system_mentions(listing["alt_texts"])
        if m["niche"] == "stationery":
            assert "ADHD" in title and not {"treat", "cure", "diagnose", "therapy"} & set(desc.lower().split())

        publish = items.read_json(d / "publish.json")
        assert publish["dry_run"] is True and publish["state"] == "draft"

    # The stationery hero photo leads with the pack's hero page when it has one.
    adhd = [m for m in manifests.values() if m["niche"] == "stationery"]
    assert all(isinstance(m["pack"].get("hero_page"), int) for m in adhd)

    # 3. A week later the same themes are cooled down at every size: nothing new is made and
    #    the counters stay where they were.
    research = _run(tmp_path, WEEK_2, stages=("research",))
    assert not research["errors"], research["errors"]
    picked = [_trimmed(tmp_path, "ttrpg", WEEK_2, [b["theme"] for b in
                                                   items.read_json(tmp_path / "briefs" / f"ttrpg-{WEEK_2}.json")["briefs"]
                                                   if b["theme"] in ttrpg_themes])]
    again = _run(tmp_path, WEEK_2, stages=("generate", "qa", "listing", "publish"), brief_files=picked)
    assert prun.exit_code(again) == 0, prun.format_summary(again)
    assert set(_manifests(out)) == set(manifests)
    skipped = [r["generate"] for r in again["items"].values() if "generate" in r]
    assert skipped and all(g.get("skipped") and g.get("cooldown") in manifests for g in skipped)
    assert items.read_json(out / "sets" / "packs_state.json")["counters"] == state["counters"]
    assert not (tmp_path / "etsy_tokens.json").exists()
