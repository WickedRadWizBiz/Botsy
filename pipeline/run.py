"""Run the stages in order: research -> generate -> QA -> listing -> Etsy draft.

Each stage is called only through its public entry point (docs/CONTRACTS.md), and a
failure on one item is recorded and does not stop the others. The run summary goes
to <out_root>/runs/run-<date>.json so the GitHub Action can upload it with the items.
"""
from __future__ import annotations

import logging
import re
import traceback
from pathlib import Path

from core import items
from core.config import get_shop, load_settings, resolve, section, today

log = logging.getLogger("pipeline")

STAGES = ("research", "generate", "qa", "listing", "publish")
DEFAULTS = {
    "max_items_per_run": 10,       # caps image/API spend per run
    "dry_run_output_dir": "output/dry-run",
}
BRIEF_FILE_RE = re.compile(r"^(?P<niche>[a-z_]+)-(?P<date>\d{4}-\d{2}-\d{2})\.json$")


def latest_briefs(briefs_dir: Path, niches: list[str]) -> list[Path]:
    """Newest brief file per niche (by the date in the file name)."""
    newest: dict[str, tuple[str, Path]] = {}
    for p in sorted(briefs_dir.glob("*.json")):
        m = BRIEF_FILE_RE.match(p.name)
        if m and m["niche"] in niches and m["date"] >= newest.get(m["niche"], ("", p))[0]:
            newest[m["niche"]] = (m["date"], p)
    return [p for _, p in sorted(newest.values(), key=lambda t: t[1].name)]


def _record(summary: dict, item_id: str, stage: str, ok: bool, detail: str = "") -> None:
    summary["items"].setdefault(item_id, {})[stage] = {"ok": ok, "detail": detail}


def run(
    *,
    stages=STAGES,
    niche: str | None = None,
    shop_id: str | None = None,
    dry_run: bool = False,
    inputs_dir: str | Path | None = None,
    briefs_dir: str | Path | None = None,
    brief_files: list[str | Path] | None = None,
    out_root: str | Path | None = None,
    max_items: int | None = None,
    date: str | None = None,
    settings: dict | None = None,
) -> dict:
    settings = settings or load_settings()
    cfg = section(settings, "pipeline", DEFAULTS)
    shop = get_shop(settings, shop_id)
    date = date or today()
    niches = [niche] if niche else list(shop["niches"])
    briefs_dir = Path(briefs_dir) if briefs_dir else resolve(settings["research"]["briefs_dir"])
    if out_root:
        out_root = Path(out_root)
    else:
        out_root = resolve(cfg["dry_run_output_dir"]) if dry_run else items.output_root(settings)
    review_dir = None  # each stage derives it per item via core.items.review_dir_for()
    max_items = max_items if max_items is not None else cfg["max_items_per_run"]

    summary = {"date": date, "shop_id": shop["id"], "dry_run": dry_run, "stages": list(stages),
               "briefs": [], "items": {}, "errors": []}

    # 1. research: CSV exports -> brief files. The exports are licensed and stay local,
    # so in CI this stage usually finds nothing and the committed briefs are used.
    if "research" in stages:
        import research

        for n in niches:
            try:
                research.run(niche=n, shop_id=shop["id"], inputs_dir=inputs_dir,
                             briefs_dir=briefs_dir, date=date)
            except SystemExit as e:  # research exits cleanly when there are no inputs
                summary["errors"].append(f"research[{n}]: {e}")
            except Exception as e:
                summary["errors"].append(f"research[{n}]: {type(e).__name__}: {e}")
                log.exception("research failed for %s", n)

    # 2. generate: newest brief per niche -> items (capped by max_items)
    new_dirs: list[Path] = []
    if "generate" in stages:
        import generators

        files = [Path(p) for p in brief_files] if brief_files else latest_briefs(briefs_dir, niches)
        queues = []
        for bf in files:
            summary["briefs"].append(items.repo_relative(bf))
            queues.append([(bf, b) for b in items.read_json(bf).get("briefs", [])])
        # Round-robin across brief files (one per niche) so a small max_items still
        # covers every niche instead of filling up from the first file.
        order = [q[i] for i in range(max(map(len, queues), default=0)) for q in queues if i < len(q)]
        for n, (bf, brief) in enumerate(order):
            if len(new_dirs) >= max_items:
                summary["errors"].append(f"max_items={max_items} reached; {len(order) - n} brief(s) skipped")
                break
            try:
                new_dirs += generators.generate(
                    brief, brief_file=items.repo_relative(bf), shop_id=shop["id"],
                    out_root=out_root, date=date, dry_run=dry_run)
            except Exception as e:
                summary["errors"].append(f"generate[{brief.get('brief_id')}]: {type(e).__name__}: {e}")
                log.debug(traceback.format_exc())

    # Later stages work on this run's items, or on everything in out_root when
    # generation was skipped (e.g. `--stages qa,listing` to redo one step).
    targets = new_dirs if "generate" in stages else list(items.iter_item_dirs(out_root))

    for d in targets:
        item_id = d.name
        status = items.read_manifest(d).get("status")
        _record(summary, item_id, "generate", True, status or "")

        if "qa" in stages and status in ("generated", "qa_failed", "qa_passed"):
            import qa

            try:
                report = qa.run(d, settings=settings, review_dir=review_dir)
                failed = [c["name"] for c in report["checks"] if not c["passed"] and c.get("severity") == "error"]
                _record(summary, item_id, "qa", report["passed"], ", ".join(failed))
            except Exception as e:
                _record(summary, item_id, "qa", False, f"{type(e).__name__}: {e}")
            status = items.read_manifest(d).get("status")

        if "listing" in stages and status == "qa_passed":
            import listing

            try:
                lst = listing.write_listing(d, dry_run=dry_run, settings=settings, review_dir=review_dir)
                _record(summary, item_id, "listing", True, lst.get("title", ""))
            except Exception as e:
                _record(summary, item_id, "listing", False, f"{type(e).__name__}: {e}")
            status = items.read_manifest(d).get("status")

        if "publish" in stages and status == "listed":
            import publisher

            try:
                rec = publisher.publish_draft(d, dry_run=dry_run, settings=settings, review_dir=review_dir)
                _record(summary, item_id, "publish", True, f"draft {rec.get('listing_id')}")
            except Exception as e:
                # Gate refusals (e.g. waiting for review approval) land here too.
                _record(summary, item_id, "publish", False, f"{type(e).__name__}: {e}")

    summary["path"] = str(items.write_json(out_root / "runs" / f"run-{date}.json", summary))
    return summary


def format_summary(summary: dict) -> str:
    lines = [f"run {summary['date']} shop={summary['shop_id']} dry_run={summary['dry_run']}",
             f"briefs: {', '.join(summary['briefs']) or '(none)'}"]
    for item_id, stages in summary["items"].items():
        cells = []
        for st in ("qa", "listing", "publish"):
            if st in stages:
                cells.append(f"{st}={'ok' if stages[st]['ok'] else 'FAIL'}")
        lines.append(f"  {item_id}: {' '.join(cells) or 'generated'}")
        lines += [f"      {st}: {v['detail']}" for st, v in stages.items() if not v["ok"] and v["detail"]]
    lines += [f"! {e}" for e in summary["errors"]]
    lines.append(f"summary: {summary['path']}")
    return "\n".join(lines)


def status_table(out_root: str | Path | None = None, review_dir: str | Path | None = None) -> str:
    out_root = Path(out_root) if out_root else items.output_root()
    review_dir = Path(review_dir) if review_dir else (out_root / "review")
    rows = []
    for d in items.iter_item_dirs(out_root):
        m = items.read_manifest(d)
        review = review_dir / f"{d.name}.json"
        rstatus = items.read_json(review).get("status", "") if review.exists() else ""
        pub = d / "publish.json"
        draft = items.read_json(pub).get("listing_id", "") if pub.exists() else ""
        rows.append(f"{d.name:<60} {m.get('status', ''):<10} review={rstatus or '-':<9} draft={draft or '-'}")
    return "\n".join(rows) or f"no items in {out_root}"
