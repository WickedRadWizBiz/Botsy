"""QA stage: automated checks, listing-photo mockups and the human review queue.

    run(item_dir, *, settings=None, review_dir=None) -> dict      # one item
    run_all(out_root=None, review_dir=None) -> list[dict]          # every item awaiting QA

Writes <item>/qa_report.json and <item>/mockups/, sets the manifest status to qa_passed
or qa_failed, and queues passing items in the review folder. See docs/CONTRACTS.md
section 3. Nothing here touches the network.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path

from core import etsy
from core.compliance import Blocklist
from core.config import load_settings, section, today
from core.items import (
    iter_item_dirs,
    output_root,
    read_manifest,
    review_dir_for,
    set_status,
    write_json,
)
from qa import review
from qa.checks import (
    ERROR,
    GROUPS,
    WARNING,
    Item,
    check_compliance,
    check_etsy_limits,
    check_generator_review,
    check_manifest,
    check_paths,
    result,
)

log = logging.getLogger("qa")

DEFAULTS = {
    # Etsy digital-file limits: "up to five digital files ... each no larger than 20MB".
    # Source: Etsy Help Center, "How to Manage Your Digital Listings"
    # (help.etsy.com/hc/en-us/articles/115015628347), checked 2026-10-15. The Open API v3
    # uploadListingFile spec states no limit of its own. 20 MB is read as 20,000,000 bytes,
    # the stricter of the two readings.
    # The same page: file names up to 70 characters of letters, numbers, periods, underscores
    # and hyphens, and a name cannot be changed after upload (checked by qa.checks).
    # The values live in core/etsy.py, shared with the generators, listing and publisher.
    "etsy_max_files": etsy.MAX_FILES,
    "etsy_max_file_bytes": etsy.MAX_FILE_BYTES,
    "etsy_max_file_name_chars": etsy.MAX_FILE_NAME_CHARS,
    "page_size_tolerance_pt": 1.0,
    "dpi_tolerance": 0.02,          # 2480 px across A4 is still "300 DPI"
    "blank_check_dpi": 36,
    "blank_min_ink": 0.002,         # share of non-white pixels below which a page is blank
    "black_max_ink": 0.85,          # share of dark pixels above which a page is solid black
    "line_check_dpi": 200,
    "line_check_max_pages": 40,
    "line_percentile": 5,           # low percentile of stroke widths, ignores tapering ends
    "min_component_in": 0.3,        # ignore pieces smaller than this (text, specks) for line weight
    "gray_max_fraction": 0.005,     # coloring pages: share of mid-gray pixels allowed
    "color_max_fraction": 0.001,
    "min_regions": 3,               # enclosed areas to color per page
    "min_region_sq_in": 0.02,
    "regions_max_bad_share": 0.25,  # pages allowed below min_regions (covers, text pages)
    "transparent_border_min": 0.98,
    "subject_min_fraction": 0.01,
    # Pack checks (qa/packs.py, docs/PACKS.md section 5). Perceptual-hash distance (of 64
    # bits) at or below duplicate_distance fails the item; at or below near_distance it goes
    # to review.
    "packs": {"duplicate_distance": 3, "near_distance": 6},
    # Stationery and ttrpg print rules (qa/packs.py): printed text at least 7 pt, writing
    # lines at least 6 mm apart (estimated from a render at `dpi`).
    "print_rules": {"min_font_pt": 7.0, "font_tolerance_pt": 0.05, "min_line_gap_mm": 6.0,
                    "line_gap_tolerance_mm": 0.4, "dpi": 150, "dark_level": 235, "min_run_mm": 20,
                    "light_level": 90, "max_pages": 60},
    "mockups": {
        "enabled": True,
        "width": 2700,              # 4:3; Etsy recommends at least 2000 px on both sides
        "height": 2025,
        "quality": 90,
        "max_bytes": 1_000_000,     # Etsy Help Center: images over 1 MB may not finish uploading
        "min_count": 5,
        "max_grid": 8,
        "max_stickers": 12,
        "render_dpi": 110,          # PDF render when an item has no previews
        "fonts": [],                # extra .ttf/.otf paths to try before the system fonts
        "size_labels": {"us_letter": ["US Letter", "8.5 x 11 in"], "a4": ["A4", "210 x 297 mm"]},
        "pack_badge": True,         # "20 PAGES" / "SET OF 5" on the cover photo of a pack
        "desk_niches": ["stationery", "ttrpg"],  # a sixth photo: printed pages on a desk
    },
}

DONE_STATUSES = ("listed", "drafted")  # past QA: rechecking must not move them back


def run(item_dir: str | Path, *, settings: dict | None = None, review_dir: str | Path | None = None,
        mockups: bool = True) -> dict:
    """QA one item. Always writes qa_report.json; never raises on a broken item."""
    settings = settings or load_settings()
    cfg = section(settings, "qa", DEFAULTS)
    d = Path(item_dir)
    if not d.is_dir():
        raise FileNotFoundError(f"no item folder {d}")
    rdir = Path(review_dir) if review_dir else review_dir_for(d, settings)

    try:
        manifest = read_manifest(d)
        if not isinstance(manifest, dict):
            raise TypeError("manifest.json is not a JSON object")
    except (OSError, ValueError, TypeError) as e:
        report = _report(d.name, [result("manifest_readable", False, f"{type(e).__name__}: {e}")], [])
        write_json(d / "qa_report.json", report)
        _remove_photos(d)
        _update_queue(report, {}, rdir)
        return report

    item = Item(d, manifest, settings, cfg)
    blocklist = Blocklist.load(settings.get("compliance", {}).get("trademark_blocklist"))
    checks = run_checks(item, blocklist)

    shots: list[str] = []
    if not _errors(checks):
        if mockups and cfg["mockups"].get("enabled", True):
            from qa import mockups as mk

            shots, mockup_checks = _guard("mockups", lambda: mk.build(item, blocklist))
            checks += mockup_checks
        else:  # mockups switched off for this run: keep and report the ones already made
            shots = [p.relative_to(d).as_posix() for p in _photos(d)]
    if _errors(checks):  # a failed item keeps no listing photos, from an earlier pass or a half-built set
        _remove_photos(d)
        shots = []

    report = _report(d.name, checks, shots)
    report["content_digest"] = content_digest(item, shots) if report["passed"] else None
    write_json(d / "qa_report.json", report)

    status = manifest.get("status")
    if status in DONE_STATUSES:
        log.warning("%s is already %s; QA report updated, status left as is", d.name, status)
    else:
        set_status(d, "qa_passed" if report["passed"] else "qa_failed")
    _update_queue(report, manifest, rdir)
    return report


def content_digest(item: Item, shots: list[str]) -> str | None:
    """sha256 over the paths and bytes of the item's files, previews, assets and listing
    photos: what a person reviewing the item looks at. The review entry keeps it, so an
    approval only stands for the content it covered. None if a file cannot be read."""
    parts = []
    try:
        for group in GROUPS:
            for e in item.entries(group):  # only paths check_paths approved
                parts.append([group, e["path"], _sha256(item.path(e))])
        for rel in shots:
            parts.append(["mockups", rel, _sha256(item.dir / rel)])
    except OSError as e:
        log.warning("%s: content digest not computed: %s", item.dir.name, e)
        return None
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _photos(d: Path) -> list[Path]:
    return sorted((d / "mockups").glob("mockup-*.jpg"))


def _remove_photos(d: Path) -> None:
    for p in _photos(d):
        p.unlink()


def _update_queue(report: dict, manifest: dict, rdir: Path) -> None:
    """Queue a passing item; drop a stale pending entry of a failing one."""
    try:
        if report["passed"]:
            review.submit(report, manifest, rdir)
        else:
            review.withdraw(report["item_id"], rdir)
    except (OSError, ValueError) as e:  # invalid item id, or an unreadable entry that may hold a decision
        log.error("%s: review queue not updated: %s", report["item_id"], e)


def run_checks(item: Item, blocklist: Blocklist) -> list[dict]:
    """Every automated check, in report order. check_paths runs first: later checks only
    open the paths it approved."""
    from qa.file_text import check_file_text
    from qa.lineart import check_coloring
    from qa.packs import check_pack, check_print_rules
    from qa.pdf_checks import check_pdf
    from qa.raster import (
        check_assets,
        check_png_file,
        check_previews,
        check_svg,
        check_zip,
    )

    checks = _guard("manifest_schema", lambda: check_manifest(item))
    checks += _guard("paths_safe", lambda: check_paths(item))
    checks += _guard("etsy_limits", lambda: check_etsy_limits(item))
    for e in item.entries("files"):
        kind = e.get("kind")
        if kind == "pdf":
            checks += _guard("pdf_opens", lambda e=e: check_pdf(item, e))
        elif kind in ("png", "png_transparent"):
            checks += _guard("png_readable", lambda e=e: check_png_file(item, e))
        elif kind == "zip":
            checks += _guard("zip_valid", lambda e=e: check_zip(item, e))
        elif kind == "svg":
            checks += _guard("svg_valid", lambda e=e: [check_svg(item, e)])
    checks += _guard("previews_readable", lambda: check_previews(item))
    checks += _guard("assets_readable", lambda: check_assets(item))
    if item.manifest.get("niche") == "coloring_pages":
        checks += _guard("line_weight", lambda: check_coloring(item))
    checks += _guard("pack_checks", lambda: check_pack(item))
    checks += _guard("print_rules", lambda: check_print_rules(item))
    checks += _guard("compliance_blocklist", lambda: check_compliance(item, blocklist))
    checks += _guard("compliance_file_text", lambda: check_file_text(item, blocklist))
    checks += _guard("generator_needs_review", lambda: [check_generator_review(item)])
    return checks


def run_all(out_root: str | Path | None = None, review_dir: str | Path | None = None, *,
            settings: dict | None = None, mockups: bool = True) -> list[dict]:
    """QA every item in out_root except those already listed or drafted (use run() on
    one of those to recheck it without changing its status)."""
    settings = settings or load_settings()
    root = Path(out_root) if out_root else output_root(settings)
    reports = []
    for d in iter_item_dirs(root):
        try:
            status = read_manifest(d).get("status")
        except (OSError, ValueError, AttributeError):
            status = None  # unreadable: run() reports it
        if status in DONE_STATUSES:
            log.info("skipping %s (%s)", d.name, status)
            continue
        reports.append(run(d, settings=settings, review_dir=review_dir, mockups=mockups))
    return reports


def _guard(name: str, fn: Callable):
    """Run a check group; a crash becomes a failed check instead of stopping QA."""
    try:
        return fn()
    except Exception as e:
        log.exception("QA check %s crashed", name)
        failed = [result(name, False, f"check crashed: {type(e).__name__}: {e}")]
        return ([], failed) if name == "mockups" else failed


def _errors(checks: list[dict]) -> list[dict]:
    return [c for c in checks if not c["passed"] and c["severity"] == ERROR]


def _report(item_id: str, checks: list[dict], shots: list[str]) -> dict:
    warnings = [c for c in checks if not c["passed"] and c["severity"] == WARNING]
    return {
        "schema": "qa/v1",
        "item_id": item_id,
        "checked": today(),
        "passed": not _errors(checks),
        "needs_review": bool(warnings),
        "checks": checks,
        "mockups": shots,
        "reasons": [f"{c['name']}: {c['detail']}" for c in warnings],
        "content_digest": None,  # set by run() for a passing item
    }


__all__ = ["DEFAULTS", "review", "run", "run_all"]
