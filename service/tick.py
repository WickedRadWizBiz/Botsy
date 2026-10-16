"""One service tick: ingest -> research -> pipeline -> status (docs/SERVER.md section 5).

The systemd path unit starts a tick when an upload lands, and the daily timer starts
one with force=True. A tick:
1. (daily, live only) refreshes our own shop's stats from Etsy (`publisher own-stats`);
2. ingests finished uploads; after an accepted file it waits until the inbox has been
   quiet for service.settle_s, so a batch of exports is researched together;
3. if anything was accepted (or force), runs research for every shop niche, writing
   history snapshots, then the pipeline (generate -> qa -> listing -> draft) for every
   shop, as a dry run unless service.live is true;
4. looks at the inbox again, and goes round once more for uploads that finished while
   it was busy (service.max_rounds), because systemd does not queue path events that
   arrive while the tick is still running;
5. writes outbox/status.txt and status.json.

A forced tick re-researches only when the market exports changed since the last
research: research names brief ids after the date, so rebuilding unchanged briefs every
day would make new items (and image spend) for the same themes every day.

Every step records its problems and the tick carries on, so status.txt always says what
happened. The caller (python -m service) holds the service lock.

status.txt and status.json sit in the outbox, which the upload account reads and the
helper copies to the PC (where they may be pasted into a chat for help). So research
errors are recorded without the text they quote: a brief compliance failure names
the competitor shop, link or title it found, and that must stay in the server's
research inputs (CLAUDE.md: no competitor text in anything but the licensed exports).
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path

from core.config import check_date, resolve, today
from core.items import read_json, repo_relative
from service import inbox, status
from service.paths import ServicePaths, is_live, load_config, service_paths

log = logging.getLogger("service.tick")

RUN_STAGES = ("generate", "qa", "listing", "publish")


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


def _add_ingest(rec: dict, result: inbox.IngestResult) -> None:
    d = result.to_dict()
    for key in ("accepted", "duplicates", "rejected"):
        rec["ingest"][key] += d[key]


def ingest_step(paths: ServicePaths, settings: dict, rec: dict, day: str, *,
                settle: bool, sleep=time.sleep) -> bool:
    """Ingest; True when a file was accepted. With settle, wait for a quiet inbox first.

    Files accepted before a later failure still count: they have already left the inbox,
    so no upload event would bring them back before the daily check.
    """
    cfg = load_config(settings)
    accepted = False
    try:
        result = inbox.ingest(paths.incoming, paths.inputs, paths.rejected, settings=settings, date=day)
        _add_ingest(rec, result)
        accepted = bool(result.accepted)
        if accepted and settle and float(cfg["settle_s"]) > 0:
            waited = inbox.wait_for_quiet(paths.incoming, float(cfg["settle_s"]),
                                          max_wait_s=float(cfg["settle_max_s"]), sleep=sleep)
            log.info("inbox quiet after %.0f s", waited)
            more = inbox.ingest(paths.incoming, paths.inputs, paths.rejected, settings=settings, date=day)
            _add_ingest(rec, more)
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


def research_all(settings: dict, paths: ServicePaths, day: str) -> dict:
    """research.run for every shop niche, with history snapshots in paths.history."""
    import research

    out = {"brief_files": [], "briefs": 0, "notes": [], "errors": []}
    for shop_id, niche in _shop_niches(settings):
        try:
            written = research.run(niche=niche, shop_id=shop_id, inputs_dir=paths.inputs,
                                   briefs_dir=paths.briefs, date=day, settings=settings,
                                   history_dir=paths.history)
        except research.NoInputsError:
            # The same answer for every niche (it is about the folder): say it once.
            out["notes"].append("no research exports yet; waiting for the first upload")
            break
        except Exception as e:
            log.warning("research for %s failed: %s", niche, e)  # full detail stays on the server
            out["errors"].append(quiet_error(f"research[{niche}]", e))
            continue
        for p in written:
            out["brief_files"].append(repo_relative(p))
            out["briefs"] += _brief_count(Path(p))
        if not written:
            out["notes"].append(f"{niche}: no brief file yet (not enough matching listings)")
    return out


def run_all(settings: dict, paths: ServicePaths, day: str, *, live: bool) -> dict:
    """pipeline.run (generate, qa, listing, publish) for every shop that has brief files."""
    from pipeline import run as prun

    out = {"summaries": [], "notes": [], "errors": []}
    for shop in settings.get("shops") or []:
        niches = list(shop.get("niches") or [])
        if not prun.latest_briefs(paths.briefs, niches):
            out["notes"].append(f"shop {shop['id']}: no brief files yet; items start after "
                                "the first exports are researched")
            continue
        try:
            out["summaries"].append(prun.run(stages=RUN_STAGES, shop_id=shop["id"], dry_run=not live,
                                             briefs_dir=paths.briefs, date=day, settings=settings))
        except Exception as e:
            log.warning("pipeline run for shop %s failed: %s", shop["id"], e)
            out["errors"].append(quiet_error(f"run[{shop['id']}]", e))
    return out


def _add_summary(run_rec: dict, rec: dict, summary: dict) -> None:
    from pipeline import run as prun

    items = summary.get("items") or {}
    run_rec["items"] += len(items)
    for item_id, stages in items.items():
        gen, pub = stages.get("generate") or {}, stages.get("publish") or {}
        run_rec["generated"] += int(bool(gen.get("ok") and not gen.get("skipped")))
        run_rec["drafts"] += int(bool(pub.get("ok") and not pub.get("skipped")))
        run_rec["failed"] += [f"{item_id}: {st}: {r.get('detail', '')}"
                              for st, r in stages.items() if not r.get("ok")]
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


def inputs_fingerprint(inputs_dir: Path, settings: dict) -> str:
    """Names, sizes and times of the market exports research reads. Own-shop and trend
    files are left out: they only boost demand, and the own-shop file changes daily."""
    from research.defaults import load_config as research_config
    from research.pipeline import discover_inputs

    known = list(settings.get("niches") or research_config(settings)["niche_rules"])
    h = hashlib.sha256()
    for p in discover_inputs(Path(inputs_dir), known).market:
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{p.name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


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
    try:
        if own_stats:
            own_stats_step(settings, rec, live=live)
        new = ingest_step(paths, settings, rec, day, settle=True, sleep=sleep)
        rounds = 0
        while new or (force and rounds == 0):
            rounds += 1
            fingerprint = inputs_fingerprint(paths.inputs, settings)
            if new or not isinstance(last_research, dict) or last_research.get("inputs") != fingerprint:
                res = research_all(settings, paths, day)
                rec["research"]["ran"] = True
                for key in ("brief_files", "notes"):
                    rec["research"][key] += res[key]
                rec["research"]["briefs"] += res["briefs"]
                rec["errors"] += res["errors"]
                if not res["errors"]:  # a failed niche is tried again on the next forced tick
                    last_research = {"date": day, "finished": status.now_iso(), "inputs": fingerprint}
            else:
                rec["research"]["notes"].append(
                    f"research skipped: no new exports since the last research ({last_research.get('date')})")
            ran = run_all(settings, paths, day, live=live)
            rec["run"]["notes"] += ran["notes"]
            rec["errors"] += ran["errors"]
            for summary in ran["summaries"]:
                rec["run"]["ran"] = True
                _add_summary(rec["run"], rec, summary)
            if rounds >= max(1, int(cfg["max_rounds"])):
                break
            # Uploads that finished while research and the pipeline ran.
            new = ingest_step(paths, settings, rec, day, settle=True, sleep=sleep)
    except Exception as e:  # a bug, not a bad upload: keep the record and still write the status
        log.exception("tick failed")
        rec["errors"].append(f"tick: {type(e).__name__}: {e}")
    rec["finished"] = status.now_iso()
    rec["exit_code"] = _exit_code(rec)
    status.update(paths.outbox, rec, keep_days=int(cfg["status_keep_days"]),
                  last_research=last_research)
    return rec
