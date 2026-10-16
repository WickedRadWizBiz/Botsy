"""Shared builders for the service tests: settings and an inbox in tmp_path, invented CSVs.

Every path in the settings points into tmp_path (inbox, research folders, output, the
event journal, the service state file and the spend ledger), so no test touches the
repo's data/, output/ or /srv. Discord is switched off. All rows are invented (fake
shops, fake listing links).
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import yaml

from core.config import deep_merge, load_settings

DATE = "2026-10-16"
EVERBEE_HEADERS = ["Product Name", "Shop Name", "Price", "Mo. Sales", "Reviews", "Favorites",
                   "Listing Age", "Tags", "Link"]
ERANK_KW_HEADERS = ["Keywords", "Avg. Searches", "Avg. Clicks", "Avg. CTR", "Etsy Competition",
                    "Google Searches"]


def make_settings(tmp_path: Path, service: dict | None = None, **sections) -> tuple[dict, Path]:
    """Real settings with every folder in tmp_path; returns (settings, path of a YAML copy)."""
    s = load_settings()
    deep_merge(s, {
        "research": {"inputs_dir": str(tmp_path / "inputs"), "briefs_dir": str(tmp_path / "briefs"),
                     "history_dir": str(tmp_path / "history")},
        "service": {"inbox_dir": str(tmp_path / "inbox"), "rejected_dir": str(tmp_path / "rejected"),
                    "lock_file": str(tmp_path / "data" / ".service.lock"), "settle_s": 0,
                    "lock_timeout_s": 5, "events_file": str(tmp_path / "data" / "events.jsonl"),
                    "state_file": str(tmp_path / "data" / ".service_state.json"), **(service or {})},
        "paths": {"output_dir": str(tmp_path / "output"), "review_dir": str(tmp_path / "output" / "review")},
        "pipeline": {"dry_run_output_dir": str(tmp_path / "dry")},
        "spend": {"ledger_file": str(tmp_path / "data" / "spend" / "ledger.jsonl")},
        # Discord stays off unless a test switches it on (and then it passes a fake session).
        "notify": {"discord": {"enabled": False}},
    })
    deep_merge(s, sections)
    (tmp_path / "inbox" / "incoming").mkdir(parents=True, exist_ok=True)
    (tmp_path / "inbox" / "outbox").mkdir(parents=True, exist_ok=True)
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(s, sort_keys=False), encoding="utf-8")
    return s, path


def csv_bytes(headers, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(headers)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def everbee_csv(n: int = 12, theme: str = "Frog", seed: int = 0) -> bytes:
    """An EverBee-style listing export: n rows over three invented shops."""
    rows = [[f"{theme} Stickers Pack {seed}-{i}, Cute Digital Clipart PNG", f"TestShop{i % 3}", "$3.99",
             str(10 + i), str(i), str(5 * i), "120 days", "frog,sticker,clipart",
             f"https://www.etsy.com/listing/{9_000_000 + seed * 100 + i}/test"] for i in range(n)]
    return csv_bytes(EVERBEE_HEADERS, rows)


def erank_keyword_csv() -> bytes:
    rows = [["frog stickers", "1200", "300", "25%", "4500", "800"],
            ["mushroom stickers", "900", "200", "22%", "3000", "500"]]
    return csv_bytes(ERANK_KW_HEADERS, rows)


def upload(incoming: Path, name: str, data: bytes) -> Path:
    """Upload like the Windows helper: write <name>.part, then rename it."""
    part = incoming / f"{name}.part"
    part.write_bytes(data)
    final = incoming / name
    part.replace(final)
    return final


class FakeResearch:
    """Stands in for research.run: records each call; writes a small brief file for the
    niches in `writes` (so the pipeline step finds brief files), [] for the others."""

    def __init__(self, writes=("stickers",), fail=(), no_inputs=False):
        self.calls: list[dict] = []
        self.writes, self.fail, self.no_inputs = set(writes), set(fail), no_inputs

    def __call__(self, niche=None, shop_id="main", inputs_dir=None, briefs_dir=None, date=None, **kw):
        import json

        import research

        self.calls.append({"niche": niche, "shop_id": shop_id, "inputs_dir": inputs_dir,
                           "briefs_dir": briefs_dir, "date": date, **kw})
        if self.no_inputs:
            raise research.NoInputsError("No research exports found")
        if niche in self.fail:
            raise RuntimeError(f"boom in {niche}")
        if niche not in self.writes:
            return []
        p = Path(briefs_dir) / f"{niche}-{date}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": "brief/v1", "niche": niche, "briefs": [{}, {}]}), encoding="utf-8")
        return [p]

    def niches(self) -> list[str]:
        return [c["niche"] for c in self.calls]


def fake_summary(day: str, shop_id: str, dry_run: bool) -> dict:
    """A pipeline run summary (docs/CONTRACTS.md section 6) with every kind of outcome."""
    ok = {"ok": True, "detail": ""}
    return {
        "date": day, "shop_id": shop_id, "dry_run": dry_run,
        "stages": ["generate", "qa", "listing", "publish"], "briefs": [],
        "items": {
            f"{day}-stickers-frogs-aaaa": {"generate": {"ok": True, "detail": "generated"}, "qa": ok,
                                           "listing": ok, "publish": {"ok": True, "detail": "draft 1 (dry run)"}},
            f"{day}-stickers-cats-bbbb": {"generate": {"ok": True, "detail": "generated"}, "qa": ok,
                                          "listing": ok,
                                          "publish": {"ok": True, "detail": "needs review", "skipped": True}},
            f"{day}-stickers-old-cccc": {"generate": {"ok": True, "detail": "drafted (reused)", "skipped": True}},
            f"{day}-stickers-bad-dddd": {"generate": {"ok": True, "detail": "generated"},
                                         "qa": {"ok": False, "detail": "pdf_blank_pages"}},
        },
        "errors": [], "setup_errors": [], "notes": ["max_items=10: 1 brief(s) not used this run"],
        "awaiting_review": {f"{day}-stickers-cats-bbbb": "needs review (review status: pending)"},
        "path": f"output/dry-run/runs/run-{day}-061700.json",
    }


class FakePipeline:
    """Stands in for pipeline.run.run: records kwargs, returns fake_summary(); `during`
    runs inside the call (e.g. an upload that lands while the pipeline works)."""

    def __init__(self, during=None, summary=fake_summary):
        self.calls: list[dict] = []
        self.during, self.summary = during, summary

    def __call__(self, **kw):
        self.calls.append(kw)
        if self.during:
            self.during(len(self.calls))
        return self.summary(kw["date"], kw["shop_id"], kw["dry_run"])
