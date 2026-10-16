"""Run the stages in order: research -> generate -> QA -> listing -> Etsy draft.

Each stage is called only through its public entry point (docs/CONTRACTS.md) and runs
over all of this run's items before the next stage starts. A failure on one item is
recorded and does not stop the others. A setup problem (a missing secret, no Etsy tokens,
a safety-layer refusal) stops that stage instead, because every item would fail the same
way and the publisher would retry the bad credentials once per item.

Reruns are cheap: an item that already exists for a brief (same shop and brief_id, any
run date) is reused at its current status instead of being generated and paid for again;
`regenerate=True` rebuilds it in its own folder. Items with an Etsy draft or rejected in
the review queue are never rebuilt.

Theme cooldown (docs/SERVER.md section 13): a brief with no item of its own is skipped when
the same shop and niche already has an item in this run's output folder with the same
theme (core.compliance.normalize plus slug) made within pipeline.theme_cooldown_days (60),
or one with a real Etsy draft at any age. `regenerate=True` lifts the 60 days but never a
real draft, so no second product of a theme already on Etsy is made. A skip is recorded
under the id the brief would have made: `generate: skip (theme cooldown: <item_id>)`.

Spending cap (docs/SERVER.md section 10): when the generators or the listing stage raise
core.spend.BudgetExceeded, that item gets a plain error, summary["budget_reached"] is
set, and no new paid work (images, listing copy) starts for the rest of the run. QA and
drafts of items that are already listed go on: they cost nothing. A spending log that
cannot be written (core.spend.LedgerUnavailable: every paid call is refused) is a setup
problem instead: that stage stops, like a missing secret.

The run summary goes to <out_root>/runs/run-<date>-<HHMMSS>.json so the GitHub Action can
upload it with the items. exit_code(summary) gives the CLI's exit status.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
import traceback
from pathlib import Path

from core import items
from core.briefs import BriefComplianceError, validate_brief_doc
from core.compliance import Blocklist, normalize
from core.config import MissingSecretError, check_date, get_shop, load_settings, resolve, section, today

log = logging.getLogger("pipeline")

STAGES = ("research", "generate", "qa", "listing", "publish")
DEFAULTS = {
    "max_items_per_run": 10,       # caps image/API spend per run
    "dry_run_output_dir": items.DRY_RUN_OUTPUT_DIR,
    # Live runs skip the newest brief file of a niche when it is older than this many days
    # (from the date in its name), so a weekly schedule on a fresh checkout never drafts
    # the same briefs twice. null turns the check off. Files named with --brief are exempt.
    "max_brief_age_days": 6,
    # A brief is skipped when the shop and niche already have an item with the same theme
    # made within this many days, or one with a real Etsy draft at any age (docs/SERVER.md
    # section 13). null or 0 = only real drafts block. --regenerate lifts the days only.
    "theme_cooldown_days": 60,
}
BRIEF_FILE_RE = re.compile(r"^(?P<niche>[a-z_]+)-(?P<date>\d{4}-\d{2}-\d{2})\.json$")
# Most advanced first: which existing item stands for a brief when several do.
_STATUS_RANK = {"drafted": 4, "listed": 3, "qa_passed": 2, "generated": 1, "qa_failed": 0}
# Item records stopped by the monthly spending cap carry "budget": true (see exit_code).
BUDGET_GENERATE = ("not made: the monthly spending cap is reached (settings spend.monthly_cap_usd); "
                   "it is made in a later run, next month or once the cap is raised")
BUDGET_LISTING = ("not listed: the monthly spending cap is reached (settings spend.monthly_cap_usd); "
                  "it is listed in a later run, next month or once the cap is raised")


class _NoBudgetCheck(RuntimeError):
    """Stands in for core.spend.BudgetExceeded / LedgerUnavailable when core.spend is
    missing; never raised."""


def _budget_exceeded() -> type[Exception]:
    """core.spend.BudgetExceeded, imported when a run starts (the class is defined there)."""
    try:
        from core.spend import BudgetExceeded
    except ImportError:
        return _NoBudgetCheck
    return BudgetExceeded


def _ledger_unavailable() -> type[Exception]:
    """core.spend.LedgerUnavailable: a cap is set but spending cannot be written down, so
    every paid call this process makes is refused. A setup problem: the stage stops."""
    try:
        from core.spend import LedgerUnavailable
    except ImportError:
        return _NoBudgetCheck
    return LedgerUnavailable


def theme_key(theme) -> str:
    """The form two themes are matched in for the cooldown: core.compliance.normalize,
    then a slug ("Farm Animals!" and "farm-animals" give "farm-animals"; apostrophes are
    dropped, so "Cat's Pajamas" matches "cats pajamas"). Not cut short, unlike item ids,
    so two long themes never match on a shared start."""
    return "-".join(re.findall(r"[a-z0-9]+", normalize(str(theme or "")).replace("'", "")))


def latest_briefs(briefs_dir: Path, niches: list[str]) -> list[Path]:
    """Newest brief file per niche (by the date in the file name)."""
    newest: dict[str, tuple[str, Path]] = {}
    for p in sorted(briefs_dir.glob("*.json")):
        m = BRIEF_FILE_RE.match(p.name)
        if m and m["niche"] in niches and m["date"] >= newest.get(m["niche"], ("", p))[0]:
            newest[m["niche"]] = (m["date"], p)
    return [p for _, p in sorted(newest.values(), key=lambda t: t[1].name)]


def _record(summary: dict, item_id: str, stage: str, ok: bool, detail: str = "", skipped: bool = False,
            **extra) -> None:
    rec = {"ok": ok, "detail": detail}
    if skipped:
        rec["skipped"] = True
    rec.update(extra)
    summary["items"].setdefault(item_id, {})[stage] = rec


def _budget_stop(summary: dict, e: Exception) -> None:
    """The monthly cap is reached: no new paid work for the rest of this run."""
    if not summary["budget_reached"]:
        log.warning("monthly spending cap reached (%s); no new paid work this run", e)
    summary["budget_reached"] = True


def _manifest(d: Path) -> dict | None:
    """The item's manifest, or None when it is missing or unreadable."""
    try:
        m = items.read_manifest(d)
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) else None


def _status(d: Path) -> str | None:
    return (_manifest(d) or {}).get("status")


def _read_optional(path: Path) -> dict:
    try:
        data = items.read_json(path)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _review_entry(d: Path, settings: dict) -> dict:
    try:
        path = items.review_entry_path(d.name, items.review_dir_for(d, settings))
    except ValueError:
        return {}
    return _read_optional(path) if path.exists() else {}


def _has_real_draft(d: Path) -> bool:
    """publish.json from a real (not dry-run) draft; an unreadable one counts as real."""
    p = d / "publish.json"
    if not p.exists():
        return False
    try:
        rec = items.read_json(p)
    except (OSError, ValueError):
        return True
    return not (isinstance(rec, dict) and rec.get("dry_run") is True)


def _brief_age_days(path: Path, date: str) -> int | None:
    m = BRIEF_FILE_RE.match(path.name)
    if not m:
        return None
    try:
        return (_dt.date.fromisoformat(date) - _dt.date.fromisoformat(m["date"])).days
    except ValueError:
        return None


def _summary_path(out_root: Path, date: str) -> Path:
    """runs/run-<date>-<HHMMSS>.json, never overwriting an earlier run's summary."""
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%H%M%S")
    base = out_root / "runs" / f"run-{date}-{stamp}"
    p, n = base.with_suffix(".json"), 2
    while p.exists():
        p, n = base.parent / f"{base.name}-{n}.json", n + 1
    return p


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
    regenerate: bool = False,
) -> dict:
    settings = settings or load_settings()
    cfg = section(settings, "pipeline", DEFAULTS)
    shop = get_shop(settings, shop_id)
    date = check_date(date) if date else today()
    if niche and niche not in shop["niches"]:
        raise ValueError(f"niche {niche!r} is not one of shop {shop['id']!r}'s niches: {shop['niches']}")
    niches = [niche] if niche else list(shop["niches"])
    out_root = items.stage_out_root(out_root, settings, dry_run=dry_run)
    if briefs_dir:
        briefs_dir = Path(briefs_dir)
    elif dry_run and "research" in stages:
        briefs_dir = out_root / "briefs"  # a simulated run never writes into data/briefs
    else:
        briefs_dir = resolve(settings["research"]["briefs_dir"])
    cap = max_items if max_items is not None else int(cfg["max_items_per_run"])

    summary = {"date": date, "shop_id": shop["id"], "dry_run": dry_run, "stages": list(stages),
               "started": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
               "briefs": [], "items": {}, "errors": [], "setup_errors": [], "notes": [],
               "awaiting_review": {}, "budget_reached": False}

    # 1. research: CSV exports -> brief files. The exports are licensed and stay local,
    # so in CI this stage is usually not run and the committed briefs are used.
    if "research" in stages:
        _research(summary, niches=niches, shop=shop, inputs_dir=inputs_dir, briefs_dir=briefs_dir,
                  date=date, settings=settings)

    # 2. generate: newest brief per niche -> items (at most max_items, reused ones included).
    # Later stages work on this run's items, or on everything in out_root when generation
    # was skipped (e.g. `--stages qa,listing` to redo one step).
    if "generate" in stages:
        targets = _generate(summary, brief_files=brief_files, briefs_dir=briefs_dir, niches=niches,
                            shop=shop, out_root=out_root, date=date, dry_run=dry_run, settings=settings,
                            cap=cap, max_age=cfg.get("max_brief_age_days"), regenerate=regenerate,
                            cooldown_days=_cooldown_days(cfg, summary))
    elif {"qa", "listing", "publish"} & set(stages):
        targets = _existing(summary, out_root, niche=niche, shop_id=shop["id"] if shop_id else None,
                            qa_runs="qa" in stages)
    else:
        targets = []

    if "qa" in stages:
        _qa(summary, targets, settings)
    if "listing" in stages:
        # Without generation, --max-items still caps the text-model calls of this run.
        budget = max_items if (max_items is not None and "generate" not in stages) else None
        _listing(summary, targets, settings, dry_run=dry_run, budget=budget)
    if "publish" in stages:
        _publish(summary, targets, settings, dry_run=dry_run)

    path = _summary_path(out_root, date)
    summary["path"] = str(path)
    items.write_json(path, summary)
    return summary


def _research(summary: dict, *, niches, shop, inputs_dir, briefs_dir, date, settings) -> None:
    import research

    for n in niches:
        try:
            written = research.run(niche=n, shop_id=shop["id"], inputs_dir=inputs_dir,
                                   briefs_dir=briefs_dir, date=date, settings=settings)
        except research.NoInputsError as e:
            # Same answer for every niche (it is about the inputs folder): say it once.
            summary["notes"].append(f"research: {e}")
            return
        except Exception as e:
            summary["errors"].append(f"research[{n}]: {type(e).__name__}: {e}")
            log.debug(traceback.format_exc())
            continue
        if not written:
            summary["notes"].append(f"research[{n}]: no rows for this niche; no brief file written")


def _pick_brief_files(summary, *, brief_files, briefs_dir: Path, niches, date, dry_run, max_age) -> list[Path]:
    if brief_files:
        return [Path(p) for p in brief_files]
    files = latest_briefs(briefs_dir, niches)
    if not files:
        summary["errors"].append(
            f"no brief files (<niche>-<YYYY-MM-DD>.json) for {', '.join(niches)} in "
            f"{items.repo_relative(briefs_dir)}; run `python -m research` locally and commit "
            "data/briefs/*.json, or pass --brief")
        return []
    found = {BRIEF_FILE_RE.match(p.name)["niche"] for p in files}
    missing = [n for n in niches if n not in found]
    if missing:
        summary["notes"].append(f"no brief file for {', '.join(missing)} in {items.repo_relative(briefs_dir)}")
    if dry_run or max_age is None:
        return files
    fresh = []
    for p in files:
        age = _brief_age_days(p, date)
        if age is not None and age > int(max_age):
            summary["errors"].append(
                f"{items.repo_relative(p)} is {age} days old (pipeline.max_brief_age_days={max_age}), so an "
                "earlier run has probably drafted it already; commit new briefs from `python -m research`, "
                "or name the file with --brief to use it anyway")
        else:
            fresh.append(p)
    return fresh


def _load_brief_queue(summary: dict, bf: Path, blocklist: Blocklist) -> list[tuple[Path, dict]]:
    """The file's briefs, or [] (with an error) when the file breaks the brief rules."""
    rel = items.repo_relative(bf)
    summary["briefs"].append(rel)
    try:
        doc = items.read_json(bf)
        if not isinstance(doc, dict):
            raise ValueError("not a JSON object")
        # Same rules research applies before writing: >= 10 listings per pattern and
        # brief, no URLs, image names, listing ids or blocked terms.
        validate_brief_doc(doc, [], [], blocklist=blocklist)
    except BriefComplianceError as e:
        summary["errors"].append(f"brief file {rel} breaks the brief rules; not used: {e}")
        return []
    except (OSError, ValueError) as e:
        summary["errors"].append(f"brief file {rel} is unreadable; not used: {type(e).__name__}: {e}")
        return []
    return [(bf, b) for b in doc.get("briefs") or [] if isinstance(b, dict)]


def _index_existing(existing: list[tuple[Path, dict]]) -> dict[tuple, tuple[Path, dict]]:
    """(shop_id, brief_id) -> the most advanced existing item made from that brief."""
    found: dict[tuple, tuple[Path, dict]] = {}
    for d, m in existing:
        if not m.get("brief_id"):
            continue
        key = (m.get("shop_id"), m["brief_id"])
        old = found.get(key)
        if old is None or _STATUS_RANK.get(m.get("status"), -1) >= _STATUS_RANK.get(old[1].get("status"), -1):
            found[key] = (d, m)
    return found


def _cooldown_days(cfg: dict, summary: dict | None = None) -> int:
    """pipeline.theme_cooldown_days; null or 0 = only real drafts block. A value that is
    not a number of days uses the default (60) with a note: a typo must neither stop the
    run nor let duplicates through."""
    value = cfg.get("theme_cooldown_days")
    if value is None or value == "":
        return 0
    try:
        if isinstance(value, bool):
            raise ValueError(value)
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        days = DEFAULTS["theme_cooldown_days"]
        if summary is not None:
            summary["notes"].append(f"settings pipeline.theme_cooldown_days is not a number of days; "
                                    f"used {days} days")
        return days


def _index_themes(existing: list[tuple[Path, dict]], shop_id: str) -> dict[tuple, list[tuple[Path, dict]]]:
    """(niche, theme_key) -> the shop's existing items with that theme."""
    found: dict[tuple, list[tuple[Path, dict]]] = {}
    for d, m in existing:
        _add_theme(found, d, m, shop_id)
    return found


def _add_theme(index: dict, d: Path, m: dict, shop_id: str) -> None:
    key = (m.get("niche"), theme_key(m.get("theme")))
    if m.get("shop_id") == shop_id and key[1]:
        index.setdefault(key, []).append((d, m))


def _created(d: Path, m: dict) -> _dt.date | None:
    """When an item was made: manifest `created`, else the date its id starts with, else
    the manifest's modification time."""
    for value in (m.get("created"), d.name[:10]):
        try:
            return _dt.date.fromisoformat(check_date(value))
        except ValueError:
            continue
    try:
        return _dt.date.fromtimestamp((d / "manifest.json").stat().st_mtime)
    except OSError:
        return None


def _cooldown_block(brief: dict, themes: dict, *, date: str, days: int, regenerate: bool) -> Path | None:
    """The existing item that keeps this brief from being made (docs/SERVER.md section
    13), or None. A real Etsy draft blocks at any age and even with --regenerate; any
    other item blocks while it is younger than `days` (an undated one counts as young)."""
    same = themes.get((brief.get("niche"), theme_key(brief.get("theme"))), [])
    for d, _ in same:
        if _has_real_draft(d):
            return d
    if regenerate or not days:
        return None
    run_day = _dt.date.fromisoformat(date)
    for d, m in sorted(same, key=lambda t: t[0].name, reverse=True):
        made = _created(d, m)
        if made is None or (run_day - made).days < days:
            return d
    return None


def _item_date(d: Path, manifest: dict, brief: dict, run_date: str) -> str:
    """The date that rebuilds `d` in place (its id is derived from it), else the run date."""
    created = manifest.get("created")
    try:
        theme = str(brief.get("theme") or "").strip()
        if items.new_item_id(created, brief.get("niche", ""), theme, salt=brief.get("brief_id") or "") == d.name:
            return created
    except (ValueError, TypeError):
        pass
    return run_date


def _generate(summary, *, brief_files, briefs_dir, niches, shop, out_root: Path, date, dry_run, settings,
              cap, max_age, regenerate, cooldown_days: int = 0) -> list[Path]:
    import generators

    budget_error, ledger_error = _budget_exceeded(), _ledger_unavailable()

    files = _pick_brief_files(summary, brief_files=brief_files, briefs_dir=briefs_dir, niches=niches,
                              date=date, dry_run=dry_run, max_age=max_age)
    blocklist = Blocklist.load((settings.get("compliance") or {}).get("trademark_blocklist"))
    queues = [q for q in (_load_brief_queue(summary, bf, blocklist) for bf in files) if q]
    # Round-robin across brief files (one per niche) so a small max_items still
    # covers every niche instead of filling up from the first file.
    order = [q[i] for i in range(max(map(len, queues), default=0)) for q in queues if i < len(q)]
    if files and not order and not summary["errors"]:
        summary["errors"].append("the brief files hold no briefs; nothing to generate")

    found = [(d, m) for d in items.iter_item_dirs(out_root) if (m := _manifest(d)) is not None]
    existing = _index_existing(found)
    themes = _index_themes(found, shop["id"])
    targets: list[Path] = []
    taken = not_taken = cooled = held = 0  # reused items count too, so a rerun retries instead of spending more
    for bf, brief in order:
        if taken >= cap:
            not_taken += 1
            continue
        bid = brief.get("brief_id")
        prior = existing.get((shop["id"], bid)) if bid else None
        d = None
        if prior is None:
            try:
                d = items.item_dir(items.new_item_id(date, str(brief.get("niche", "")),
                                                     str(brief.get("theme") or "").strip(), salt=bid or ""), out_root)
            except ValueError:
                d = None  # the generator reports the bad brief
            m = _manifest(d) if d else None
            prior = (d, m) if m else None
        if prior is None and d is not None:
            blocker = _cooldown_block(brief, themes, date=date, days=cooldown_days, regenerate=regenerate)
            if blocker is not None:
                _record(summary, d.name, "generate", True, f"theme cooldown: {blocker.name}", skipped=True,
                        cooldown=blocker.name)
                cooled += 1
                continue
        gen_date = date
        if prior is not None:
            d, m = prior
            if d in targets:
                continue
            if _review_entry(d, settings).get("status") == "rejected":
                summary["awaiting_review"][d.name] = "rejected in the review queue"
                _record(summary, d.name, "generate", True, "rejected in the review queue; not rebuilt", skipped=True)
                continue
            drafted = _has_real_draft(d)
            if not regenerate or drafted:
                why = "has an Etsy draft; not rebuilt" if regenerate else "reused; --regenerate rebuilds it"
                _record(summary, d.name, "generate", True, f"{m.get('status')} ({why})", skipped=True)
                targets.append(d)
                taken += 1
                continue
            gen_date = _item_date(d, m, brief, date)
        if summary["budget_reached"]:  # no new paid work; reused items above still go on
            held += 1
            continue
        try:
            dirs = generators.generate(brief, brief_file=items.repo_relative(bf), shop_id=shop["id"],
                                       out_root=out_root, date=gen_date, dry_run=dry_run, settings=settings)
        except (MissingSecretError, ledger_error) as e:
            summary["setup_errors"].append(f"generate: {e}; no further briefs were generated")
            break
        except budget_error as e:
            _budget_stop(summary, e)
            _record(summary, d.name if d is not None else str(bid or bf.name), "generate", False,
                    BUDGET_GENERATE, budget=True)
            continue
        except generators.ImageCapExceeded as e:
            summary["errors"].append(f"generate[{bid}]: {e}; the remaining briefs were not generated")
            break
        except Exception as e:
            summary["errors"].append(f"generate[{bid}]: {type(e).__name__}: {e}")
            log.debug(traceback.format_exc())
            continue
        taken += 1
        for d in dirs:
            if d not in targets:
                targets.append(d)
                _record(summary, d.name, "generate", True, "generated")
                m = _manifest(d)
                if m is not None:  # a later brief with the same theme waits too
                    _add_theme(themes, d, m, shop["id"])
    if not_taken:
        summary["notes"].append(f"max_items={cap}: {not_taken} brief(s) not used this run")
    if held:
        summary["notes"].append(f"monthly spending cap reached: {held} more brief(s) not made this run")
    if cooled:
        why = (f"made in the last {cooldown_days} days or already has an Etsy draft" if cooldown_days and
               not regenerate else "already has an Etsy draft")
        summary["notes"].append(f"theme cooldown: {cooled} brief(s) skipped because the same theme was {why}")
    return targets


def _existing(summary: dict, out_root: Path, *, niche, shop_id, qa_runs: bool) -> list[Path]:
    """Every item in out_root, filtered by --niche / --shop. Unreadable manifests are
    reported (QA writes a failing report for them when it runs) and do not stop the run."""
    targets = []
    for d in items.iter_item_dirs(out_root):
        m = _manifest(d)
        if m is None:
            if qa_runs:
                targets.append(d)
            else:
                _record(summary, d.name, "manifest", False, "manifest.json is unreadable")
            continue
        if (niche and m.get("niche") != niche) or (shop_id and m.get("shop_id") != shop_id):
            continue
        targets.append(d)
    if not targets and not summary["items"]:
        summary["notes"].append(f"no items in {out_root}")
    return targets


def _qa(summary: dict, targets: list[Path], settings: dict) -> None:
    import qa

    for d in targets:
        if _status(d) not in ("generated", "qa_failed", "qa_passed", None):
            continue
        try:
            report = qa.run(d, settings=settings)
            failed = [c["name"] for c in report["checks"] if not c["passed"] and c.get("severity") == "error"]
            _record(summary, d.name, "qa", bool(report.get("passed")), ", ".join(failed))
        except Exception as e:
            _record(summary, d.name, "qa", False, f"{type(e).__name__}: {e}")


def _listing(summary: dict, targets: list[Path], settings: dict, *, dry_run: bool, budget: int | None) -> None:
    import listing

    budget_error, ledger_error = _budget_exceeded(), _ledger_unavailable()
    capped = held = 0
    for d in targets:
        # qa_passed items, and in a real run 'listed' real items whose copy the publisher
        # would refuse as stale (dry-run template text, or an old shop name / disclosure).
        if not listing.needs_listing(d, dry_run=dry_run, settings=settings):
            continue
        if _review_entry(d, settings).get("status") == "rejected":  # no model call spent on it
            summary["awaiting_review"][d.name] = "rejected in the review queue"
            _record(summary, d.name, "listing", True, "rejected in the review queue; not listed", skipped=True)
            continue
        if summary["budget_reached"] and not dry_run:  # the model call is paid; template copy is not
            held += 1
            continue
        if budget is not None:
            if budget <= 0:
                capped += 1
                continue
            budget -= 1
        try:
            lst = listing.write_listing(d, dry_run=dry_run, settings=settings)
            if lst.get("preview"):  # dry run on a real item: nothing written, it stays qa_passed
                _record(summary, d.name, "listing", True, f"preview only (dry run on a real item): "
                        f"{lst.get('title', '')}", skipped=True)
            else:
                _record(summary, d.name, "listing", True, lst.get("title", ""))
        except (MissingSecretError, ledger_error) as e:
            summary["setup_errors"].append(f"listing: {e}")
            _record(summary, d.name, "listing", False, f"{type(e).__name__}: {e}")
            break
        except budget_error as e:
            _budget_stop(summary, e)
            _record(summary, d.name, "listing", False, BUDGET_LISTING, budget=True)
        except Exception as e:
            _record(summary, d.name, "listing", False, f"{type(e).__name__}: {e}")
    if capped:
        summary["notes"].append(f"--max-items: {capped} item(s) not listed this run")
    if held:
        summary["notes"].append(f"monthly spending cap reached: {held} more item(s) not listed this run")


def _publish(summary: dict, targets: list[Path], settings: dict, *, dry_run: bool) -> None:
    import publisher

    mode = (settings.get("compliance") or {}).get("publisher_mode", "draft_only")
    if mode != "draft_only":  # CLAUDE.md: the publisher only ever creates drafts
        summary["setup_errors"].append(f"publish: settings compliance.publisher_mode is {mode!r}; "
                                       "only 'draft_only' is allowed, so nothing was published")
        return
    setup = (MissingSecretError, publisher.AuthError, publisher.SafetyViolation)
    for d in targets:
        if _status(d) != "listed":
            continue
        try:  # the publisher's own review rule, asked first so no Etsy client is built for it
            wait = publisher.review_wait(d, settings=settings)
        except publisher.PublishGateError:
            wait = None  # unreadable item file: publish_draft reports it
        if wait:
            summary["awaiting_review"][d.name] = wait
            _record(summary, d.name, "publish", True, wait, skipped=True)
            continue
        try:
            rec = publisher.publish_draft(d, dry_run=dry_run, settings=settings)
            _record(summary, d.name, "publish", True,
                    f"draft {rec.get('listing_id')}" + (" (dry run)" if rec.get("dry_run") else ""))
        except publisher.ReviewPending as e:  # only the review gate stopped it
            summary["awaiting_review"][d.name] = str(e)
            _record(summary, d.name, "publish", True, str(e), skipped=True)
        except setup as e:
            # Wrong setup, not a bad item: stop instead of retrying it once per item.
            summary["setup_errors"].append(f"publish: {type(e).__name__}: {e}")
            _record(summary, d.name, "publish", False, f"{type(e).__name__}: {e}")
            break
        except Exception as e:
            _record(summary, d.name, "publish", False, f"{type(e).__name__}: {e}")


def exit_code(summary: dict) -> int:
    """0 = every stage that ran succeeded (items waiting for review count as success),
    1 = an item, brief or stage failed, 2 = setup error (missing secret, Etsy auth, safety
    layer), the same codes as `python -m publisher`.

    Reaching the monthly spending cap (summary["budget_reached"]) is not a failure: the
    cap is the user's own limit and the run stopped cleanly, so its item records
    ("budget": true) count as 0. Any other failure in the same run still gives 1."""
    if summary.get("setup_errors"):
        return 2
    if summary.get("errors"):
        return 1
    if any(not rec["ok"] and not rec.get("budget") for st in summary["items"].values() for rec in st.values()):
        return 1
    return 0


def _cell(rec: dict) -> str:
    """"wait" is an item the monthly spending cap stopped: a later run makes or lists it."""
    return "skip" if rec.get("skipped") else "ok" if rec["ok"] else "wait" if rec.get("budget") else "FAIL"


def format_summary(summary: dict) -> str:
    lines = [f"run {summary['date']} shop={summary['shop_id']} dry_run={summary['dry_run']}",
             f"briefs: {', '.join(summary['briefs']) or '(none)'}"]
    for item_id, stages in summary["items"].items():
        cells = [f"{st}={_cell(rec)}" for st, rec in stages.items()
                 if st != "generate" or not rec["ok"] or rec.get("skipped")]
        lines.append(f"  {item_id}: {' '.join(cells) or 'generated'}")
        lines += [f"      {st}: skip ({rec['detail']})" if rec.get("cooldown") else f"      {st}: {rec['detail']}"
                  for st, rec in stages.items() if (not rec["ok"] or rec.get("skipped")) and rec["detail"]]
    if summary.get("budget_reached"):
        lines.append("! monthly spending cap reached: no new images or listing copy were started after that "
                     "(settings spend.monthly_cap_usd); the rest waits for next month or a higher cap")
    lines += [f"! setup: {e}" for e in summary.get("setup_errors", [])]
    lines += [f"! {e}" for e in summary["errors"]]
    lines += [f"- {e}" for e in summary.get("notes", [])]
    if summary.get("awaiting_review"):
        lines.append(f"awaiting review: {len(summary['awaiting_review'])} item(s) "
                     "(`python -m qa review list`)")
    lines.append(f"summary: {summary['path']}")
    return "\n".join(lines)


def status_table(out_root: str | Path | None = None, review_dir: str | Path | None = None,
                 settings: dict | None = None) -> str:
    settings = settings or load_settings()
    out_root = Path(out_root) if out_root else items.output_root(settings)
    review_dir = Path(review_dir) if review_dir else items.review_dir_for_root(out_root, settings)
    rows = []
    for d in items.iter_item_dirs(out_root):
        m = _manifest(d)
        status = m.get("status", "") if m else "unreadable"
        try:
            entry = items.review_entry_path(d.name, review_dir)
            rstatus = _read_optional(entry).get("status", "") if entry.exists() else ""
        except ValueError:
            rstatus = ""
        draft = _read_optional(d / "publish.json").get("listing_id", "")
        rows.append(f"{d.name:<60} {status:<10} review={rstatus or '-':<9} draft={draft or '-'}")
    return "\n".join(rows) or f"no items in {out_root}"
