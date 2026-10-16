"""One service tick: ingest -> research -> pipeline -> status (docs/SERVER.md section 5).

The systemd path unit starts a tick when an upload lands, and the daily timer starts
one with force=True. A tick:
1. (daily, live only) refreshes our own shop's stats from Etsy (`publisher own-stats`) and
   sends `token_expiring` once a day when the Etsy refresh token runs out within 10 days;
2. checks the monthly spending cap (core.spend): `spend_warning` and `budget_reached` are
   sent once per month, and while the cap is reached (or, with a cap set, the spending log
   cannot be written) a live tick skips the paid stages (generate and listing); QA and
   drafts of finished items still run;
3. ingests finished uploads; after an accepted file it waits until the inbox has been
   quiet for service.settle_s, so a batch of exports is researched together;
4. if anything was accepted (or force), works out the research stage (discovery or
   refined, research.stages.resolve_stage), runs research for every shop niche with that
   stage, writing history snapshots, then the pipeline (generate -> qa -> listing ->
   draft) for every shop, as a dry run unless service.live is true;
5. looks at the inbox again, and goes round once more for uploads that finished while
   it was busy (service.max_rounds), because systemd does not queue path events that
   arrive while the tick is still running;
6. tidies old files (service.cleanup), writes outbox/status.txt and status.json, and
   sends ONE Discord message for the event kinds in notify.discord.events.

What happened also goes to the event journal (core.events, data/events.jsonl), which the
daily log (service/digest.py) is built from. Small memories between ticks (the last
research stage, which spending messages, review holds and problems were already sent to
Discord) live in service.state_file (data/.service_state.json). Every pipeline run
records the items still waiting for review in the journal, so the daily log lists them
all, but Discord hears about each one once; the same problem is posted at most once a day.

A forced tick re-researches only when the market exports changed since the last
research, an export or trend file left the evidence window (research.window_days), or
the research stage changed: research names brief ids after the date, so
rebuilding unchanged briefs every day would make new items (and image spend) for the
same themes every day.

Every step records its problems and the tick carries on, so status.txt always says what
happened. The caller (python -m service) holds the service lock.

status.txt and status.json sit in the outbox, which the upload account reads and the
helper copies to the PC (where they may be pasted into a chat for help). So research
errors are recorded without the text they quote: a brief compliance failure names
the competitor shop, link or title it found, and that must stay in the server's
research inputs (CLAUDE.md: no competitor text in anything but the licensed exports).
The same scrubbed text is what reaches the event journal, the daily log and Discord.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import importlib
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

from core import events
from core.config import ROOT, check_date, resolve, section, today
from core.items import read_json, repo_relative
from service import inbox, status
from service.paths import ServicePaths, is_live, load_config, service_paths

log = logging.getLogger("service.tick")

RUN_STAGES = ("generate", "qa", "listing", "publish")
# What still runs while the monthly spending cap is reached: no image or text-model calls.
UNPAID_STAGES = ("qa", "publish")
RESEARCH_STAGES = ("discovery", "refined")

# Settings "service" keys this module adds (read with core.config.section).
DEFAULTS: dict = {
    "state_file": "data/.service_state.json",   # memories between ticks (not for people)
}
STATE_SCHEMA = "service-state/v1"
HELD_KEEP_DAYS = 120          # review holds already announced are remembered this long
ERRORS_KEEP_DAYS = 7          # problems already posted to Discord today are remembered this long
MAX_NOTIFY_LINES = 5          # titles, files or problems named in one Discord message
TOKEN_WARN_DAYS = 10          # token_expiring from this many days before the Etsy refresh token runs out


# ---------------------------------------------------------------- steps

def fetch_own_stats(settings: dict) -> int:
    """`python -m publisher own-stats` for the first shop: our own active listings only."""
    from publisher import own_stats
    from publisher.client import EtsyClient
    from publisher.defaults import load_cfg

    _, n = own_stats.write_own_stats(EtsyClient(settings=settings),
                                     resolve(load_cfg(settings)["own_stats_file"]))
    return n


def own_stats_step(settings: dict, rec: dict, *, live: bool) -> None:
    if not live:
        rec["own_stats"] = {"ran": False, "detail": "skipped in test mode (service.live is false)"}
        return
    try:
        n = fetch_own_stats(settings)
        rec["own_stats"] = {"ran": True, "detail": f"{n} of our own listings read from Etsy"}
    except Exception as e:  # missing secrets, Etsy down: note it and keep going
        rec["own_stats"] = {"ran": False, "detail": "failed (see Problems)"}
        rec["errors"].append(f"own-stats: {type(e).__name__}: {e}")


def token_days_left(settings: dict) -> int | None:
    """Days until the first shop's Etsy refresh token runs out (publisher.token_days_left)."""
    from publisher.auth import token_days_left as days_left

    return days_left(settings)


def token_step(settings: dict, state: dict, journal: "Journal", day: str) -> None:
    """`token_expiring` (days_left), at most once a day, from TOKEN_WARN_DAYS before the
    Etsy refresh token runs out (live mode, daily check). Never stops the tick."""
    try:
        left = token_days_left(settings)
    except Exception as e:  # noqa: BLE001 - a warning helper must never break the tick
        log.debug("Etsy token age not read: %s", e)
        return
    if left is None or left > TOKEN_WARN_DAYS or state.get("token_warned") == day:
        return
    journal.emit("token_expiring", days_left=max(0, int(left)))
    state["token_warned"] = day


class Journal:
    """The events of one tick: each goes to the event journal (core.events) and is kept
    here too, for the tick's one combined Discord message."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.items: list[dict] = []

    def emit(self, kind: str, **fields) -> None:
        events.emit(kind, settings=self.settings, **fields)
        self.items.append({"kind": kind, **fields})

    def record(self, kind: str, **fields) -> None:
        """Only the event journal (the daily log): already announced, so not in the message."""
        events.emit(kind, settings=self.settings, **fields)

    def kinds(self) -> list[str]:
        return sorted({e["kind"] for e in self.items})


_STORED_NAME = re.compile(r"(?:[a-z0-9_]+__)?(?:own_shop-|trends-)?(?P<tool>.+?)-\d{4}-\d{2}-\d{2}-[0-9a-f]{8}\.csv")


def export_info(path: Path, settings: dict) -> dict:
    """{tool, shape, rows} of an accepted export (read again; the tool from the name if not)."""
    try:
        from research import columns
        from research.defaults import load_config as research_config

        rcfg = research_config(settings)
        export = columns.load_export(path, rcfg["column_overrides"], rcfg["source_tool"])
        return {"tool": export.tool, "shape": export.shape, "rows": int(len(export.frame))}
    except Exception as e:  # noqa: BLE001 - the event is a summary; ingest already checked the file
        log.debug("could not read %s again for its event: %s", path.name, e)
        m = _STORED_NAME.fullmatch(Path(path).name)
        return {"tool": m.group("tool") if m else "unknown", "shape": None, "rows": None}


def _add_ingest(rec: dict, result: inbox.IngestResult, settings: dict, journal: Journal | None) -> None:
    d = result.to_dict()
    for key in ("accepted", "duplicates", "rejected"):
        rec["ingest"][key] += d[key]
    if journal is None:
        return
    for p in result.accepted:
        journal.emit("export_received", file=Path(p).name, **export_info(Path(p), settings))
    for name, reason in result.rejected:
        # A reason can quote what the file held (a CSV error names a cell): keep quotes and
        # links out of the journal, the daily log and Discord.
        journal.emit("export_rejected", name=scrub(name), reason=scrub(reason))


def ingest_step(paths: ServicePaths, settings: dict, rec: dict, day: str, *,
                settle: bool, sleep=time.sleep, journal: Journal | None = None) -> bool:
    """Ingest; True when a file was accepted. With settle, wait for a quiet inbox first.

    Files accepted before a later failure still count: they have already left the inbox,
    so no upload event would bring them back before the daily check.
    """
    cfg = load_config(settings)
    accepted = False
    try:
        result = inbox.ingest(paths.incoming, paths.inputs, paths.rejected, settings=settings, date=day)
        _add_ingest(rec, result, settings, journal)
        accepted = bool(result.accepted)
        if accepted and settle and float(cfg["settle_s"]) > 0:
            waited = inbox.wait_for_quiet(paths.incoming, float(cfg["settle_s"]),
                                          max_wait_s=float(cfg["settle_max_s"]), sleep=sleep)
            log.info("inbox quiet after %.0f s", waited)
            more = inbox.ingest(paths.incoming, paths.inputs, paths.rejected, settings=settings, date=day)
            _add_ingest(rec, more, settings, journal)
    except Exception as e:
        rec["errors"].append(f"ingest: {type(e).__name__}: {e}")
    return accepted


def _shop_niches(settings: dict) -> list[tuple[str, str]]:
    """(shop id, niche) for every shop niche, each niche once (brief files are per niche)."""
    seen, out = set(), []
    for shop in settings.get("shops") or []:
        for niche in shop.get("niches") or []:
            if niche not in seen:
                seen.add(niche)
                out.append((shop["id"], niche))
    return out


_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'" + r'|"(?:[^"\\]|\\.)*"')
_LINK = re.compile(r"(?:https?://|www\.)\S+|\b[\w.-]*etsy\.com/\S*", re.I)


def scrub(text: str) -> str:
    """One line, with quoted text replaced by '...' and links by [link]."""
    return _LINK.sub("[link]", _QUOTED.sub("'...'", " ".join(str(text).split())))


def quiet_error(where: str, e: BaseException) -> str:
    """`where: Type: message` with quoted text and links taken out.

    Research and brief errors quote what they found (a competitor's shop name, a listing
    link, a title), and this text goes to the outbox. The server log keeps the detail.
    """
    import research

    if isinstance(e, research.BriefComplianceError):
        kinds = []
        for problem in getattr(e, "problems", None) or []:
            kind = scrub(problem)
            if kind not in kinds:
                kinds.append(kind)
        detail = "brief failed compliance checks, so nothing was written for it: " + "; ".join(kinds[:8])
    else:
        detail = scrub(str(e))
    return f"{where}: {type(e).__name__}: {detail}"


def _brief_count(path: Path) -> int:
    try:
        return len(read_json(path).get("briefs") or [])
    except (OSError, ValueError, AttributeError):
        return 0


def resolve_stage(settings: dict) -> str:
    """The research stage for this tick: research.stages.resolve_stage (refined after 20
    real Etsy drafts with research.stage: auto)."""
    from research import stages

    return stages.resolve_stage(settings)


def research_all(settings: dict, paths: ServicePaths, day: str, *, stage: str | None = None) -> dict:
    """research.run for every shop niche, with history snapshots in paths.history.

    `stage` (discovery or refined) is passed to research.run; None works it out first.
    The result has `written`: one {niche, file, count} per brief file."""
    import research

    out = {"brief_files": [], "briefs": 0, "notes": [], "errors": [], "written": [], "stage": stage}
    if stage is None:
        try:
            stage = out["stage"] = resolve_stage(settings)
        except Exception as e:
            log.warning("research stage could not be worked out: %s", e)
            out["errors"].append(quiet_error("research stage", e))
            return out
    stale = stale_exports_note(paths.inputs, settings, day)
    if stale:
        out["notes"].append(stale)
    for shop_id, niche in _shop_niches(settings):
        try:
            written = research.run(niche=niche, shop_id=shop_id, inputs_dir=paths.inputs,
                                   briefs_dir=paths.briefs, date=day, settings=settings,
                                   history_dir=paths.history, stage=stage)
        except research.NoInputsError:
            # The same answer for every niche (it is about the folder): say it once.
            out["notes"].append("no research exports yet; waiting for the first upload")
            break
        except Exception as e:
            log.warning("research for %s failed: %s", niche, e)  # full detail stays on the server
            out["errors"].append(quiet_error(f"research[{niche}]", e))
            continue
        for p in written:
            count = _brief_count(Path(p))
            out["brief_files"].append(repo_relative(p))
            out["briefs"] += count
            out["written"].append({"niche": niche, "file": repo_relative(p), "count": count})
        if not written and not stale:
            out["notes"].append(f"{niche}: no brief file yet (not enough matching listings)")
    return out


def _input_files(inputs_dir: Path, settings: dict):
    from research.defaults import load_config as research_config
    from research.pipeline import discover_inputs

    known = list(settings.get("niches") or research_config(settings)["niche_rules"])
    return discover_inputs(Path(inputs_dir), known)


def stale_exports_note(inputs_dir: Path, settings: dict, day: str) -> str | None:
    """A plain note when there are market exports but every one is older than
    research.window_days, so research has nothing it may count; else None."""
    try:
        from research import window

        market = _input_files(inputs_dir, settings).market
        if not market or window.recent(market, day, settings, inputs_dir):
            return None
        days = window.window_config(settings)["window_days"]
    except Exception as e:  # noqa: BLE001 - only the wording of a note depends on it
        log.debug("could not check the export dates: %s", e)
        return None
    return f"all exports are older than {days} days; new exports are needed (see this week's search list)"


def run_all(settings: dict, paths: ServicePaths, day: str, *, live: bool, stages=RUN_STAGES) -> dict:
    """pipeline.run (generate, qa, listing, publish) for every shop that has brief files.
    `stages` is UNPAID_STAGES while the monthly spending cap is reached."""
    from pipeline import run as prun

    out = {"summaries": [], "notes": [], "errors": []}
    stages = tuple(stages)
    for shop in settings.get("shops") or []:
        niches = list(shop.get("niches") or [])
        if not prun.latest_briefs(paths.briefs, niches):
            out["notes"].append(f"shop {shop['id']}: no brief files yet; items start after "
                                "the first exports are researched")
            continue
        try:
            summary = prun.run(stages=stages, shop_id=shop["id"], dry_run=not live,
                               briefs_dir=paths.briefs, date=day, settings=settings)
        except Exception as e:
            log.warning("pipeline run for shop %s failed: %s", shop["id"], e)
            out["errors"].append(quiet_error(f"run[{shop['id']}]", e))
            continue
        out["summaries"].append(summary)
        if live and isinstance(summary, dict) and summary.get("budget_reached"):
            # The cap was reached during this shop's run: no paid stages for the next shops.
            stages = tuple(st for st in stages if st in UNPAID_STAGES)
    return out


def _add_summary(run_rec: dict, rec: dict, summary: dict) -> None:
    from pipeline import run as prun

    items = summary.get("items") or {}
    for item_id, stages in items.items():
        if not isinstance(stages, dict):
            continue
        gen, pub = stages.get("generate") or {}, stages.get("publish") or {}
        if gen.get("cooldown"):
            # Not an item: a brief skipped because the same theme was made recently.
            run_rec["cooldown"] = int(run_rec.get("cooldown") or 0) + 1
            continue
        run_rec["items"] += 1
        run_rec["generated"] += int(bool(gen.get("ok") and not gen.get("skipped")))
        run_rec["drafts"] += int(bool(pub.get("ok") and not pub.get("skipped")))
        if any(isinstance(r, dict) and r.get("budget") for r in stages.values()):
            # Stopped by the monthly spending cap: it waits for next month, it did not fail.
            run_rec["budget_wait"] = int(run_rec.get("budget_wait") or 0) + 1
        run_rec["failed"] += [f"{item_id}: {st}: {r.get('detail', '')}"
                              for st, r in stages.items()
                              if isinstance(r, dict) and not r.get("ok") and not r.get("budget")]
    run_rec["awaiting_review"] += len(summary.get("awaiting_review") or {})
    run_rec["notes"] += list(summary.get("notes") or [])
    if summary.get("path"):
        run_rec["summaries"].append(summary["path"])
    rec["errors"] += [f"setup: {e}" for e in summary.get("setup_errors") or []]
    # A brief file that breaks the brief rules is quoted in the error: keep that out of the outbox.
    rec["errors"] += [scrub(e) if "brief" in str(e).lower() else e for e in summary.get("errors") or []]
    try:
        code = prun.exit_code(summary)
    except (KeyError, TypeError, AttributeError):
        code = 1
    run_rec["exit_code"] = max(run_rec["exit_code"], code)


def inputs_fingerprint(inputs_dir: Path, settings: dict, day: str | None = None,
                       stage: str | None = None) -> str:
    """Names, sizes and times of the market exports research reads. Own-shop files are
    left out: they only boost demand, and the own-shop file changes daily.

    With `day`, also the names of the market exports and trend files inside the evidence
    window on that day (research.window.recent), so a forced tick re-researches when an
    export leaves the research.window_days window. With `stage`, the research stage, so it
    re-researches when the stage turns refined."""
    found = _input_files(inputs_dir, settings)
    h = hashlib.sha256()
    for p in found.market:
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{p.name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    if day is not None:
        from research import window

        inside = window.recent(found.market + found.trends, day, settings, inputs_dir)
        h.update(("window\0" + "\0".join(sorted(p.name for p in inside)) + "\n").encode())
    if stage is not None:
        h.update(f"stage\0{stage}\n".encode())
    return h.hexdigest()


# ---------------------------------------------------------------- state between ticks

def state_path(settings: dict) -> Path:
    return resolve(section(settings, "service", DEFAULTS).get("state_file") or DEFAULTS["state_file"])


def _off_limits(path: Path) -> bool:
    """While pytest runs, the state file inside the repo (the user's real data/) is off limits."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return False
    return True


def load_state(settings: dict) -> dict:
    """data/.service_state.json, or {} when there is none (or it is unreadable)."""
    path = state_path(settings)
    if _off_limits(path):
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_state(settings: dict, state: dict) -> None:
    """Write the state file (temp file + rename). A failure is logged, never raised."""
    path = state_path(settings)
    if _off_limits(path):
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({**state, "schema": STATE_SCHEMA}, f, indent=2, sort_keys=True, default=str)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as e:
        log.warning("could not save %s: %s", path, e)


# ---------------------------------------------------------------- stage and spending

def stage_step(settings: dict, state: dict, journal: Journal, rec: dict) -> str | None:
    """The research stage for this tick, with `stage_changed` when it differs from the
    stage the last tick used. None (and a problem in the record) when it cannot be worked out."""
    try:
        stage = resolve_stage(settings)
    except Exception as e:
        log.warning("research stage could not be worked out: %s", e)
        rec["errors"].append(quiet_error("research stage", e))
        return None
    previous = state.get("stage")
    if previous in RESEARCH_STAGES and previous != stage:
        try:
            from core.items import count_drafts

            drafts = count_drafts(settings=settings)
        except Exception:  # noqa: BLE001 - the count is only for the message
            drafts = None
        journal.emit("stage_changed", **{"from": previous, "to": stage, "drafts": drafts})
        log.info("research stage changed from %s to %s", previous, stage)
    state["stage"] = stage
    rec["research"]["stage"] = stage
    return stage


def _state_dict(state: dict, key: str) -> dict:
    """state[key] as a dict (a damaged state file must not stop every tick)."""
    if not isinstance(state.get(key), dict):
        state[key] = {}
    return state[key]


def _budget_events(b: dict, state: dict, journal: Journal, *, reached: bool = False) -> None:
    """spend_warning / budget_reached, each once per month (and again if the cap changes)."""
    sp = _state_dict(state, "spend")
    mark = {"month": b.get("month"), "cap": b.get("cap")}
    mtd = round(float(b.get("mtd") or 0.0), 2)
    if b.get("reached") or reached:
        if sp.get("reached") != mark:
            journal.emit("budget_reached", month_to_date=mtd, cap=b.get("cap"))
            sp["reached"] = mark
        sp["warned"] = mark            # no warning after the cap itself was reached
    elif b.get("warn") and sp.get("warned") != mark:
        journal.emit("spend_warning", month_to_date=mtd, cap=b.get("cap"), pct=b.get("pct"))
        sp["warned"] = mark


def _spend_problem(rec: dict, e: BaseException) -> None:
    error = f"spend: {type(e).__name__}: {scrub(e)}"
    if error not in rec["errors"]:          # checked more than once per tick
        rec["errors"].append(error)


def budget_step(settings: dict, state: dict, journal: Journal, rec: dict, *, reached: bool = False,
                live: bool = False) -> str | None:
    """Check the monthly cap (core.spend.budget_state). Returns why paid stages must not
    run (plain words), or None when they may.

    With `live`, also core.spend.check_budget: with a cap set, a spending log that cannot
    be written (LedgerUnavailable) pauses the paid stages, reported once per tick here
    instead of once per pipeline run."""
    from core import spend

    try:
        b = spend.budget_state(settings)
    except ValueError as e:  # a cap that is not a number: never "no cap"
        _spend_problem(rec, e)
        return ("the spending cap setting (spend.monthly_cap_usd) is not a number; paid steps wait "
                "until it is fixed")
    except Exception as e:  # the ledger cannot be read
        _spend_problem(rec, e)
        return "the spending log could not be read (see Problems); paid steps wait until it can"
    _budget_events(b, state, journal, reached=reached)
    if b.get("reached") or reached:
        return "the monthly spending cap is reached"
    if live and b.get("cap") is not None:
        try:
            spend.check_budget("text", settings)
        except spend.BudgetExceeded:
            return "the monthly spending cap is reached"
        except Exception as e:  # LedgerUnavailable (disk full, wrong folder owner), unreadable ledger
            _spend_problem(rec, e)
            return ("the spending log cannot be written, so the monthly cap cannot be checked (see "
                    "Problems); paid steps wait until it can")
    return None


# ---------------------------------------------------------------- events from a pipeline run

def _item_file(out_root: Path, item_id: str, name: str) -> dict:
    from core.items import valid_item_id

    if not valid_item_id(item_id):
        return {}
    try:
        doc = read_json(out_root / item_id / name)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _reasons(text) -> str:
    """A review wait in a few words: the stage's own hints (python -m commands with
    folders) are left out; the digest and Discord give the botsy command."""
    text = " ".join(str(text or "").split())
    for cut in ("; approve with", " approve with ", "; rerun "):
        text = text.split(cut)[0]
    return text[:300]


def _error(journal: Journal, state: dict, day: str, where: str, message: str) -> None:
    """An `error` event. The same problem (place and text) goes into the Discord message
    at most once a day; the journal, and so the daily log, gets every one."""
    sent = _state_dict(state, "errors_sent")
    key = hashlib.sha256(f"{where}\0{message}".encode("utf-8", "backslashreplace")).hexdigest()[:16]
    if sent.get(key) == day:
        journal.record("error", where=where, message=message)
    else:
        sent[key] = day
        journal.emit("error", where=where, message=message)


def summary_events(summary: dict, settings: dict, state: dict, journal: Journal, day: str) -> None:
    """item_generated, qa_failed, listing_written, draft_created, held_for_review and
    error events from one pipeline run summary (docs/CONTRACTS.md section 6)."""
    from core.items import stage_out_root

    dry = bool(summary.get("dry_run"))
    out_root = stage_out_root(None, settings, dry_run=dry)
    for item_id, stages in (summary.get("items") or {}).items():
        if not isinstance(stages, dict):
            continue
        gen, qa, lst, pub = (stages.get(k) or {} for k in ("generate", "qa", "listing", "publish"))
        if gen.get("ok") and not gen.get("skipped"):
            m = _item_file(out_root, item_id, "manifest.json")
            journal.emit("item_generated", item_id=item_id, niche=m.get("niche"), theme=m.get("theme"),
                         dry_run=dry)
        if qa and not qa.get("ok"):
            checks = [c.strip() for c in str(qa.get("detail") or "").split(",") if c.strip()]
            journal.emit("qa_failed", item_id=item_id, checks=checks)
        if lst.get("ok") and not lst.get("skipped"):
            title = str(lst.get("detail") or "") or _item_file(out_root, item_id, "listing.json").get("title")
            journal.emit("listing_written", item_id=item_id, title=title, dry_run=dry)
        if pub.get("ok") and not pub.get("skipped"):
            record = _item_file(out_root, item_id, "publish.json")
            title = _item_file(out_root, item_id, "listing.json").get("title") or str(lst.get("detail") or "")
            journal.emit("draft_created", item_id=item_id, listing_id=record.get("listing_id"),
                         title=title or None, dry_run=dry)
        for st, r in stages.items():
            # A stop at the monthly cap is not a problem with the item: budget_reached says it.
            if st != "qa" and isinstance(r, dict) and not r.get("ok") and not r.get("budget"):
                _error(journal, state, day, f"{st}[{item_id}]", status.redact(scrub(r.get("detail", ""))))
    held = _state_dict(state, "held")
    for item_id, reason in (summary.get("awaiting_review") or {}).items():
        if "rejected in the review queue" in str(reason):
            continue                       # the user already said no: nothing to ask
        key = f"{'dry' if dry else 'live'}:{item_id}"
        fields = {"item_id": item_id, "reasons": _reasons(reason), "dry_run": dry}
        if key in held:
            # Announced before: the journal still records it, so the daily log lists every
            # item that is waiting, but Discord is not told again.
            journal.record("held_for_review", **fields)
        else:
            journal.emit("held_for_review", **fields)
        held[key] = day                    # still waiting: remembered from the last time seen


def _prune_held(state: dict, day: str) -> None:
    """Forget review holds not seen for HELD_KEEP_DAYS and problems posted before ERRORS_KEEP_DAYS."""
    for key, days in (("held", HELD_KEEP_DAYS), ("errors_sent", ERRORS_KEEP_DAYS)):
        cutoff = (_dt.date.fromisoformat(day) - _dt.timedelta(days=days)).isoformat()
        state[key] = {k: v for k, v in _state_dict(state, key).items() if isinstance(v, str) and v >= cutoff}


def _error_events(errors: list[str], journal: Journal, state: dict, day: str) -> None:
    for text in errors:
        where, _, message = str(text).partition(": ")
        # Quotes and links out (an error can quote what it found), secrets replaced.
        _error(journal, state, day, scrub(where), status.redact(scrub(message or where)))


# ---------------------------------------------------------------- cleanup and Discord

def _count(value) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        return sum(_count(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return 0


def cleanup_step(settings: dict, rec: dict) -> None:
    """service.cleanup.run(settings): old rejected uploads, old exports, old dry-run items."""
    try:
        cleanup = importlib.import_module("service.cleanup")   # looked up at call time (tests swap it)
    except ModuleNotFoundError as e:
        if e.name != "service.cleanup":
            rec["errors"].append(f"cleanup: {type(e).__name__}: {scrub(e)}")
        else:
            log.debug("service.cleanup is not installed; nothing tidied")
        return
    try:
        removed = cleanup.run(settings)
    except Exception as e:
        log.warning("cleanup failed: %s", e)
        rec["errors"].append(f"cleanup: {type(e).__name__}: {scrub(e)}")
        return
    counts = {str(k): _count(v) for k, v in (removed or {}).items()} if isinstance(removed, dict) else {}
    counts = {k: n for k, n in counts.items() if n}
    if counts:
        rec["cleanup"] = counts
        describe = getattr(cleanup, "describe", None)
        try:
            text = describe(removed) if callable(describe) else ""
        except Exception:  # noqa: BLE001 - only the log line depends on it
            text = ""
        log.info("cleanup: %s", text or ", ".join(f"{k} {n}" for k, n in counts.items()))
    # Folders it could not tidy (for example a link): shown in status.txt, not a failure.
    problems = [scrub(x) for x in getattr(removed, "problems", None) or [] if str(x).strip()]
    if problems:
        rec["cleanup_problems"] = problems[:20]


def _names(items: list[str]) -> str:
    shown = "; ".join(" ".join(str(x).split())[:120] for x in items[:MAX_NOTIFY_LINES])
    return shown + (f" and {len(items) - MAX_NOTIFY_LINES} more" if len(items) > MAX_NOTIFY_LINES else "")


def _money(x) -> str:
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return "$?"


def _refined_weeks(settings: dict) -> int:
    """research.stages.refined.min_weeks_seen, for the message (3 when it cannot be read)."""
    try:
        from research import stages

        return int(stages.stage_thresholds(settings, "refined")["min_weeks_seen"])
    except Exception:  # noqa: BLE001 - only the wording of one line depends on it
        return 3


def notification(items: list[dict], settings: dict) -> tuple[str, list[str]]:
    """One plain message for the events of a tick whose kinds are in notify.discord.events.
    Returns (text, kinds in it); text is "" when there is nothing to say."""
    from service import digest, notify

    by: dict[str, list[dict]] = {}
    for e in items:
        if notify.wants(e["kind"], settings):
            by.setdefault(e["kind"], []).append(e)
    lines: list[str] = []
    drafts = by.get("draft_created", [])
    real = [e for e in drafts if not e.get("dry_run")]
    practice = [e for e in drafts if e.get("dry_run")]
    if real:
        lead = "1 new draft is" if len(real) == 1 else f"{len(real)} new drafts are"
        lines.append(f"{lead} waiting in Etsy: {_names([e.get('title') or e.get('item_id') for e in real])}")
    if practice:
        lines.append(f"{len(practice)} practice draft{'' if len(practice) == 1 else 's'} made (test mode, "
                     f"nothing sent to Etsy): {_names([e.get('title') or e.get('item_id') for e in practice])}")
    held = by.get("held_for_review", [])
    if held:
        lines.append(f"{len(held)} item{' is' if len(held) == 1 else 's are'} waiting for your review. "
                     "On the server type:")
        lines += [f"  {digest.review_command(str(e.get('item_id')), bool(e.get('dry_run')))}"
                  for e in held[:MAX_NOTIFY_LINES]]
        if len(held) > MAX_NOTIFY_LINES:
            lines.append(f"  ... and {len(held) - MAX_NOTIFY_LINES} more (botsy qa review list)")
    rejected = by.get("export_rejected", [])
    if rejected:
        lines.append(f"{len(rejected)} upload{' was' if len(rejected) == 1 else 's were'} not used: "
                     + _names([f"{e.get('name')} ({e.get('reason')})" for e in rejected]))
    for e in by.get("budget_reached", [])[-1:]:
        lines.append(f"Monthly spending cap reached: {_money(e.get('month_to_date'))} of {_money(e.get('cap'))}. "
                     "New images and listing text are paused until next month, or until you raise "
                     "spend.monthly_cap_usd.")
    for e in by.get("spend_warning", [])[-1:]:
        pct = e.get("pct")
        lines.append(f"Spending warning: {_money(e.get('month_to_date'))} of {_money(e.get('cap'))} used this "
                     "month" + (f" ({float(pct):.0%})." if isinstance(pct, (int, float)) else "."))
    for e in by.get("token_expiring", [])[-1:]:
        left = e.get("days_left")
        when = "has run out" if left == 0 else f"runs out in {left} day{'' if left == 1 else 's'}"
        lines.append(f"The Etsy connection {when}. On the server type: botsy publisher auth --paste")
    for e in by.get("stage_changed", []):
        if e.get("to") == "refined":
            lines.append(f"Research is now in the refined stage: a theme must show up in exports from "
                         f"{_refined_weeks(settings)} different weeks, with demand not falling, before it "
                         "becomes a product"
                         + (f" ({e['drafts']} Etsy drafts so far)." if isinstance(e.get("drafts"), int) else "."))
        else:
            lines.append(f"Research stage changed from {e.get('from')} to {e.get('to')}.")
    errors = by.get("error", [])
    if errors:
        lines.append(f"Problems: {len(errors)}. Details are in status.txt.")
        lines += [f"  ! {' '.join(str(e.get('where', '')).split())}: {' '.join(str(e.get('message', '')).split())[:200]}"
                  for e in errors[:MAX_NOTIFY_LINES]]
        if len(errors) > MAX_NOTIFY_LINES:
            lines.append(f"  ... and {len(errors) - MAX_NOTIFY_LINES} more")
    if not lines:
        return "", []
    return "Botsy:\n" + "\n".join(lines), sorted(by)


def notify_step(settings: dict, journal: Journal) -> bool | None:
    """Send the tick's one Discord message. None when there was nothing to send."""
    try:
        text, kinds = notification(journal.items, settings)
        if not text:
            return None
        from service import notify

        sent = notify.send(text, settings=settings, kinds=kinds)
        log.info("Discord message %s", "sent" if sent else "not sent")
        return sent
    except Exception as e:  # noqa: BLE001 - a message must never break the tick
        log.warning("Discord message not built: %s: %s", type(e).__name__, e)
        return False


# ---------------------------------------------------------------- the tick

def _exit_code(rec: dict) -> int:
    code = int(rec["run"].get("exit_code") or 0)
    if any(str(e).startswith("setup:") for e in rec["errors"]):
        code = max(code, 2)
    return max(code, 1) if rec["errors"] else code


def tick(settings: dict, *, inbox_dir: str | Path | None = None, force: bool = False,
         own_stats: bool = False, date: str | None = None, sleep=time.sleep) -> dict:
    """Run one tick and write the status files; returns the tick record (see status.py)."""
    paths = service_paths(settings, inbox_dir)
    cfg = load_config(settings)
    live = is_live(settings)
    day = check_date(date) if date else today()
    rec = status.new_record(date=day, forced=force, live=live)
    rec["ingest"]["rejected_dir"] = str(paths.rejected)
    last_research = (status.load(paths.outbox) or {}).get("last_research")
    state = load_state(settings)
    journal = Journal(settings)
    try:
        if own_stats:
            own_stats_step(settings, rec, live=live)
            if live:
                token_step(settings, state, journal, day)
        paused = budget_step(settings, state, journal, rec, live=live)
        new = ingest_step(paths, settings, rec, day, settle=True, sleep=sleep, journal=journal)
        rounds, stage, stage_known, paused_noted = 0, None, False, False
        while new or (force and rounds == 0):
            rounds += 1
            if not stage_known:
                stage, stage_known = stage_step(settings, state, journal, rec), True
            fingerprint = inputs_fingerprint(paths.inputs, settings, day, stage)
            if stage is None:
                rec["research"]["notes"].append("research skipped: the research stage could not be worked out "
                                                "(see Problems)")
            elif (new or not isinstance(last_research, dict) or last_research.get("inputs") != fingerprint
                  or last_research.get("stage", "discovery") != stage):
                res = research_all(settings, paths, day, stage=stage)
                rec["research"]["ran"] = True
                for key in ("brief_files", "notes"):
                    rec["research"][key] += res[key]
                rec["research"]["briefs"] += res["briefs"]
                rec["errors"] += res["errors"]
                for w in res["written"]:
                    journal.emit("briefs_written", niche=w["niche"], stage=stage, count=w["count"])
                if not res["errors"]:  # a failed niche is tried again on the next forced tick
                    last_research = {"date": day, "finished": status.now_iso(), "inputs": fingerprint,
                                     "stage": stage}
            else:
                rec["research"]["notes"].append(
                    f"research skipped: no new exports since the last research ({last_research.get('date')})")
            stages = RUN_STAGES
            if live and paused:
                stages = UNPAID_STAGES
                if not paused_noted:
                    rec["run"]["notes"].append(f"paid steps skipped: {paused}. No new items or listing text "
                                               "this time; QA and drafts of finished items still run.")
                    paused_noted = True
            ran = run_all(settings, paths, day, live=live, stages=stages)
            rec["run"]["notes"] += ran["notes"]
            rec["errors"] += ran["errors"]
            for summary in ran["summaries"]:
                rec["run"]["ran"] = True
                _add_summary(rec["run"], rec, summary)
                try:
                    summary_events(summary, settings, state, journal, day)
                except Exception as e:  # a summary we cannot read must not stop the tick
                    log.warning("events from the run summary failed: %s", e)
                    rec["errors"].append(f"events: {type(e).__name__}: {scrub(e)}")
                if summary.get("budget_reached"):
                    paused = budget_step(settings, state, journal, rec, reached=True) or paused
            if rounds >= max(1, int(cfg["max_rounds"])):
                break
            # Uploads that finished while research and the pipeline ran.
            new = ingest_step(paths, settings, rec, day, settle=True, sleep=sleep, journal=journal)
        if rec["run"]["ran"]:
            budget_step(settings, state, journal, rec)   # spend made during this tick
    except Exception as e:  # a bug, not a bad upload: keep the record and still write the status
        log.exception("tick failed")
        rec["errors"].append(f"tick: {type(e).__name__}: {e}")
    cleanup_step(settings, rec)
    _error_events(rec["errors"], journal, state, day)
    rec["finished"] = status.now_iso()
    rec["exit_code"] = _exit_code(rec)
    status.update(paths.outbox, rec, keep_days=int(cfg["status_keep_days"]),
                  last_research=last_research)
    _prune_held(state, day)
    save_state(settings, state)
    notify_step(settings, journal)
    return rec
