"""Checks and helpers every generator shares (activity sheets and the image niches).

Brief checks. Brief files are committed and anyone can edit them (the weekly Action starts
from data/briefs/*.json, and --brief takes any path), so a generator does not assume that
research wrote the brief it gets. check_brief() runs before anything is planned or paid for
and refuses a brief that breaks a CLAUDE.md compliance rule: it reruns research's own
document check (core.briefs.doc_problems) on the brief together with the header of the
file it came from, so the brief must be backed by at least 10 listings
(evidence.listing_count, min_listings_per_pattern, pattern counts) and hold no URLs, image
names, listing ids, identifying fields or blocklisted terms. Shop names and verbatim
competitor titles need the licensed exports to detect, which only research has; that is why
brief free text (style notes, audience) never reaches an image prompt as written
(generators/imaging/prompts.py).

Item folders. An item that already has a real Etsy draft (publish.json without
dry_run: true) is never touched. A dry run never replaces a real, paid item. A rebuilt item
loses what later stages made from its old files (QA report, mockups, listing, a dry-run
publish record, an approval in the review queue), so stale photos can never be uploaded next
to new files and an approval never covers art nobody looked at.
"""
from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from core import etsy
from core.briefs import HARD_FLOOR, Guard, doc_problems
from core.compliance import Blocklist, ComplianceResult, find_urls
from core.config import get_shop, resolve
from core.items import (check_date, clear_downstream, read_json, read_manifest, repo_relative, stage_out_root,
                        write_manifest)

log = logging.getLogger(__name__)

# Etsy digital listings: up to 5 files, 20 MB (20,000,000 bytes) each, file names of at most
# 70 letters, digits, periods, underscores or hyphens (core/etsy.py, shared with QA and the
# publisher).
ETSY_MAX_FILES = etsy.MAX_FILES
ETSY_MAX_FILE_BYTES = etsy.MAX_FILE_BYTES
ETSY_MAX_FILE_MB = etsy.MAX_FILE_BYTES // 1_000_000
ETSY_MAX_NAME_CHARS = etsy.MAX_FILE_NAME_CHARS

BRIEF_TEXT_FIELDS = ("theme", "keywords", "audience", "style_notes")


@dataclass
class CheckedBrief:
    theme: str
    blocklist: Blocklist
    check: ComplianceResult               # blocklist result over the brief's text fields
    review_reasons: list[str] = field(default_factory=list)


def as_list(value) -> list[str]:
    """Brief list fields as a list of strings; a lone string is one entry, not its letters."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    return [str(value)]


def check_brief(brief: dict, niche: str, settings: dict, brief_file: str | Path | None = None) -> CheckedBrief:
    """Refuse (ValueError) a brief that is for another niche, has no theme, or breaks a
    compliance rule. Returns the theme, the blocklist and review reasons (flagged terms)."""
    if not isinstance(brief, dict):
        raise ValueError(f"a brief must be a JSON object, not {type(brief).__name__}")
    if brief.get("niche", niche) != niche:
        raise ValueError(f"brief niche {brief.get('niche')!r} is not {niche!r}")
    theme = str(brief.get("theme") or "").strip()
    if not theme:
        raise ValueError("brief has no theme")
    blocklist = Blocklist.load(settings.get("compliance", {}).get("trademark_blocklist"))
    texts = {k: brief.get(k) for k in BRIEF_TEXT_FIELDS}
    check = blocklist.check_obj(texts)
    if check.blocked:
        raise ValueError(f"brief contains blocked terms {check.blocked}; not generating")
    compliance = brief.get("compliance")
    marked = compliance.get("blocked") if isinstance(compliance, dict) else None
    if marked:
        raise ValueError(f"brief is marked with blocked terms {marked}; not generating")
    urls = find_urls(texts)
    if urls:
        raise ValueError(f"brief contains URLs {urls[:3]}; briefs must not reference listings")
    problems = brief_problems(brief, brief_file, blocklist)
    if problems:
        raise ValueError("brief failed compliance checks; not generating: " + "; ".join(problems))
    reasons = []
    if check.flagged:
        reasons.append(f"brief mentions review terms {check.flagged}")
    if brief.get("needs_review"):
        reasons.append("brief marked needs_review by research")
    return CheckedBrief(theme, blocklist, check, reasons)


def brief_problems(brief: dict, brief_file: str | Path | None, blocklist: Blocklist) -> list[str]:
    """The brief-document check (core.briefs, the one research runs before writing) on
    this brief plus the header of its brief file.

    The header (min_listings_per_pattern, patterns, sources) comes from brief_file when it
    is a brief document; a lone brief, a list of briefs or no file gets the 10-listing
    floor. Other briefs in the file are checked when they are generated, so one bad brief
    does not stop the rest. A brief_file that does not exist is only a label (tests).
    """
    header: dict = {"min_listings_per_pattern": HARD_FLOOR}
    path = brief_path(brief_file)
    if path is not None and path.is_file():
        try:
            doc = read_json(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return [f"cannot read brief file {path.name}: {exc}"]
        if isinstance(doc, dict) and "briefs" in doc:
            header = {k: v for k, v in doc.items() if k != "briefs"}
    try:
        return doc_problems({**header, "briefs": [brief]}, Guard((), (), blocklist))
    except (AttributeError, TypeError) as exc:                  # e.g. evidence or patterns of the wrong type
        return [f"malformed brief data ({exc})"]


def brief_path(brief_file: str | Path | None) -> Path | None:
    """brief_file as an absolute path. A relative one is relative to the repo root, as
    manifests record it (docs/CONTRACTS.md); the CLIs pass absolute paths."""
    return None if brief_file is None else resolve(brief_file)


def recorded_brief_file(brief_file: str | Path | None) -> str | None:
    """manifest brief_file: repo-relative inside the repo, else absolute."""
    path = brief_path(brief_file)
    return None if path is None else repo_relative(path)


def item_date(value) -> str:
    """The item date becomes part of a folder name, so it must be a plain, real YYYY-MM-DD."""
    try:
        return check_date(value)
    except ValueError as exc:
        raise ValueError(f"date {value!r} is not a real YYYY-MM-DD date: {exc}") from None


def find_shop(settings: dict, shop_id: str | None) -> dict:
    """The settings entry for shop_id. An unknown shop is an error, not a pack without a price."""
    try:
        return get_shop(settings, shop_id)
    except KeyError as exc:
        msg = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
        raise ValueError(msg if msg.startswith("unknown shop") else f"unknown shop id {shop_id!r}") from None


def brief_price(brief: dict, shop: dict, niche: str, reasons: list[str]):
    """The brief's price_usd when it is a positive number, else the shop's default price for
    the niche (listing uses the same rule). A bad brief price adds a review reason."""
    default = (shop.get("default_price_usd") or {}).get(niche)
    price = brief.get("price_usd")
    if price is None:
        return default
    if isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0:
        return price
    reasons.append(f"brief price_usd {price!r} is not a positive number; used the shop default {default!r}")
    return default


def page_sizes_for(brief: dict, settings: dict) -> list[str]:
    """Brief's page sizes that settings know (default: all of them); only the first unless
    print.deliver_both_sizes is true."""
    known = list(settings["print"]["page_sizes"])
    asked = [s for s in as_list(brief.get("page_sizes")) if s in known] or known
    return asked if settings["print"].get("deliver_both_sizes", True) else asked[:1]


def file_name(slug: str, suffix: str) -> str:
    """'<slug><suffix>' that Etsy accepts as a digital file name: only letters, digits, '.',
    '_' and '-', and at most 70 characters (the theme slug is shortened to fit)."""
    suffix = re.sub(r"[^A-Za-z0-9._-]+", "-", suffix)
    room = ETSY_MAX_NAME_CHARS - len(suffix)
    if room < 3:
        raise ValueError(f"file name suffix {suffix!r} leaves no room for a name under {ETSY_MAX_NAME_CHARS} chars")
    return (slug[:room].rstrip("-") or "item") + suffix


def has_real_draft(d: Path) -> bool:
    """True if d/publish.json records a real Etsy draft: anything but an explicit dry run
    (an unreadable record counts as real, so the item is left alone)."""
    p = Path(d) / "publish.json"
    if not p.exists():
        return False
    try:
        return read_json(p).get("dry_run") is not True
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return True


def _is_dry_run_item(d: Path) -> bool:
    try:
        gen = read_manifest(d).get("generator")
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return False                                    # unreadable: treat as real
    return isinstance(gen, dict) and gen.get("dry_run") is True


def keep_existing(d: Path, item_id: str, dry_run: bool) -> bool:
    """Run before anything is planned or paid for. True: d has a real Etsy draft, leave it
    untouched. Raises ValueError when a dry run would replace a real (paid) item."""
    if has_real_draft(d):
        log.warning("%s already has an Etsy draft; leaving its files untouched", item_id)
        return True
    if dry_run and (Path(d) / "manifest.json").exists() and not _is_dry_run_item(d):
        raise ValueError(f"{item_id} is a real item; not overwriting it with dry-run output "
                         "(dry runs belong in their own --out, e.g. output/dry-run)")
    return False


def swap_in(build: Path, d: Path, subdirs: Iterable[str], manifest: dict) -> None:
    """Replace d's generated subfolders with the finished build, then write the manifest.

    What later stages made from the old files goes first (core.items.clear_downstream:
    qa_report.json, listing.json, mockups/, a dry-run publish.json, and an approval in the
    review queue, which covered the old art). clear_downstream refuses if a real draft
    appeared since keep_existing().
    """
    d.mkdir(parents=True, exist_ok=True)
    clear_downstream(d)
    for sub in subdirs:
        shutil.rmtree(d / sub, ignore_errors=True)
        (build / sub).replace(d / sub)
    write_manifest(d, manifest)


def default_out_root(out: str | Path | None, settings: dict, dry_run: bool) -> Path | None:
    """CLI output root: --out, else settings pipeline.dry_run_output_dir for a dry run (as
    `python -m pipeline run --dry-run` does), else None (the generator's paths.output_dir)."""
    if out or dry_run:
        return stage_out_root(out, settings, dry_run=dry_run)
    return None
