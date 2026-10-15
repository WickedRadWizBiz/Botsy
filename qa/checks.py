"""Metadata checks: manifest schema, safe paths, Etsy digital-file limits, compliance.

Every check is a dict {name, passed, severity, detail} (docs/CONTRACTS.md section 3).
A failed `error` fails the item; a failed `warning` lets it pass but sends it to human
review. Checks never raise on bad input: a broken item must produce a report, not a crash.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from core.compliance import Blocklist, find_urls, iter_strings
from core.items import STATUSES

ERROR, WARNING = "error", "warning"
KINDS = ("pdf", "png", "png_transparent", "zip", "svg")
GROUPS = ("files", "previews", "assets")

# Required manifest fields and their JSON types (manifest/v1).
REQUIRED: dict[str, type] = {
    "schema": str, "item_id": str, "niche": str, "shop_id": str, "theme": str,
    "keywords": list, "created": str, "ai_assisted": bool, "needs_review": bool,
    "generator": dict, "prompts": list, "files": list, "previews": list, "assets": list,
    "status": str,
}


def result(name: str, passed: bool, detail: str = "", severity: str = ERROR) -> dict:
    return {"name": name, "passed": bool(passed), "severity": severity, "detail": detail}


@dataclass
class Item:
    """One item under QA. `usable` holds the manifest paths that are safe to open and exist;
    later checks only ever open those, so a crafted path is never read."""

    dir: Path
    manifest: dict
    settings: dict
    cfg: dict
    usable: set[str] = field(default_factory=set)

    def entries(self, group: str, *kinds: str) -> list[dict]:
        """Usable entries of files/previews/assets, optionally only the given kinds."""
        return [e for e in _dicts(self.manifest.get(group))
                if e.get("path") in self.usable and (not kinds or e.get("kind") in kinds)]

    def path(self, entry: dict) -> Path:
        return self.dir / entry["path"]


def page_list(nums: list[int], limit: int = 12) -> str:
    """'1, 4, 7 (+3 more)' for check details."""
    shown = ", ".join(str(n) for n in nums[:limit])
    return shown + (f" (+{len(nums) - limit} more)" if len(nums) > limit else "")


def _dicts(value) -> list[dict]:
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


def check_manifest(item: Item) -> list[dict]:
    m, problems = item.manifest, []
    if m.get("schema") != "manifest/v1":
        problems.append(f"schema is {m.get('schema')!r}, expected 'manifest/v1'")
    for key, typ in REQUIRED.items():
        if key not in m:
            problems.append(f"missing {key}")
        elif not isinstance(m[key], typ):
            problems.append(f"{key} should be a {typ.__name__}")
    if isinstance(m.get("item_id"), str) and m["item_id"] != item.dir.name:
        problems.append(f"item_id {m['item_id']!r} does not match the folder {item.dir.name!r}")
    niches = item.settings.get("niches") or {}
    if niches and m.get("niche") not in niches:
        problems.append(f"unknown niche {m.get('niche')!r}")
    if isinstance(m.get("theme"), str) and not m["theme"].strip():
        problems.append("theme is empty")
    if not all(isinstance(k, str) for k in m.get("keywords") or []):
        problems.append("keywords must be strings")
    if isinstance(m.get("status"), str) and m["status"] not in STATUSES:
        problems.append(f"unknown status {m['status']!r}")
    if isinstance(m.get("generator"), dict) and not m["generator"].get("name"):
        problems.append("generator.name missing")
    if isinstance(m.get("files"), list) and not m["files"]:
        problems.append("files is empty (an Etsy digital listing needs at least one file)")
    page_sizes = item.settings.get("print", {}).get("page_sizes", {})
    for group in GROUPS:
        raw = m.get(group) if isinstance(m.get(group), list) else []
        for i, e in enumerate(raw):
            where = f"{group}[{i}]"
            if not isinstance(e, dict) or not isinstance(e.get("path"), str) or not e["path"]:
                problems.append(f"{where} has no path")
                continue
            if group != "previews" and e.get("kind") not in KINDS:
                problems.append(f"{where} kind {e.get('kind')!r} is not one of {', '.join(KINDS)}")
            if group == "files" and e.get("kind") == "pdf":
                if e.get("page_size") not in page_sizes:
                    problems.append(f"{where} page_size {e.get('page_size')!r} is not in settings print.page_sizes")
                if not isinstance(e.get("pages"), int) or isinstance(e.get("pages"), bool) or e["pages"] < 1:
                    problems.append(f"{where} pages must be a positive integer")
    return [result("manifest_schema", not problems,
                   "; ".join(problems) or "manifest/v1 with all required fields")]


def unsafe_reason(rel: str) -> str | None:
    """Why a manifest path must not be opened, or None if it is a plain relative path.

    Checked with both POSIX and Windows rules, because the pipeline also runs on Windows,
    where `..\\x` and `C:x` escape the item folder too."""
    if not rel or "\x00" in rel:
        return "empty or contains NUL"
    for pure in (PurePosixPath(rel), PureWindowsPath(rel)):
        if pure.is_absolute() or pure.anchor:
            return "absolute path"
        if ".." in pure.parts:
            return "parent reference (..)"
    return None


def check_paths(item: Item) -> list[dict]:
    """Every files/previews/assets path is relative, stays inside the item dir and exists.
    Fills item.usable with the paths that pass."""
    root = item.dir.resolve()
    unsafe, missing = [], []
    for group in GROUPS:
        for e in _dicts(item.manifest.get(group)):
            rel = e.get("path")
            if not isinstance(rel, str):
                continue  # reported by manifest_schema
            reason = unsafe_reason(rel)
            target = item.dir / rel
            if reason is None:
                try:
                    target.resolve().relative_to(root)
                except ValueError:
                    reason = "resolves outside the item folder (symlink?)"
            if reason:
                unsafe.append(f"{rel!r}: {reason}")
            elif not target.is_file():
                missing.append(rel)
            else:
                item.usable.add(rel)
    return [
        result("paths_safe", not unsafe, "; ".join(unsafe) or "all paths relative and inside the item folder"),
        result("paths_exist", not missing,
               f"missing: {', '.join(missing)}" if missing else f"{len(item.usable)} referenced files found"),
    ]


def check_etsy_limits(item: Item) -> list[dict]:
    """Etsy accepts a limited number of digital files per listing, each under a size cap
    (values and source in qa.DEFAULTS). Exceeding either makes the publisher's upload fail."""
    max_n, max_bytes = int(item.cfg["etsy_max_files"]), int(item.cfg["etsy_max_file_bytes"])
    n = len(_dicts(item.manifest.get("files")))
    too_big = []
    for e in item.entries("files"):
        size = item.path(e).stat().st_size
        if size > max_bytes:
            too_big.append(f"{e['path']} is {size / 1e6:.1f} MB")
    return [
        result("etsy_file_count", 1 <= n <= max_n, f"{n} digital files (Etsy allows 1 to {max_n})"),
        result("etsy_file_size", not too_big,
               "; ".join(too_big) + f" (limit {max_bytes / 1e6:.0f} MB)" if too_big
               else f"every file under {max_bytes / 1e6:.0f} MB"),
    ]


# Top-level manifest fields holding ids, dates, paths, tool names or other checks' notes
# rather than product text. Every other field is checked, so a text field a generator adds
# later (a subtitle, say) is covered without a change here.
NOT_TEXT = frozenset({"schema", "item_id", "niche", "shop_id", "brief_id", "brief_file", "created", "status",
                      "generator", "review_reasons", *GROUPS, "pack"})


def manifest_texts(m: dict) -> list[str]:
    """Manifest text that ends up in front of a buyer or a model: theme, keywords, audience,
    prompts and any other text field, file names and the pack contents (page titles)."""
    texts = [s for k, v in m.items() if k not in NOT_TEXT for s in iter_strings(v)]
    for group in GROUPS:
        texts += [PurePosixPath(str(e.get("path", "")).replace("\\", "/")).name for e in _dicts(m.get(group))]
    texts += list(iter_strings(m.get("pack") or {}))
    return [t for t in texts if isinstance(t, str) and t.strip()]


def check_compliance(item: Item, blocklist: Blocklist) -> list[dict]:
    texts = manifest_texts(item.manifest)
    r = blocklist.check(*texts)
    urls = find_urls(texts)
    return [
        result("compliance_blocklist", not r.blocked,
               f"blocked terms: {', '.join(r.blocked)}" if r.blocked else f"no blocked terms in {len(texts)} text fields"),
        result("compliance_review_terms", not r.flagged,
               f"review terms: {', '.join(r.flagged)}" if r.flagged else "no review terms", WARNING),
        result("compliance_urls", not urls, f"URLs found: {', '.join(urls[:5])}" if urls else "no URLs"),
        check_ai_flag(item),
    ]


def check_ai_flag(item: Item) -> dict:
    """ai_assisted must be true when an image model was used, so the listing stage adds the
    AI disclosure (Etsy Creativity Standards). A recorded model or prompts means one was used."""
    m = item.manifest
    gen = m.get("generator") if isinstance(m.get("generator"), dict) else {}
    flag = m.get("ai_assisted")
    if gen.get("model") or m.get("prompts"):
        return result("ai_assisted", flag is True,
                      f"image model {gen.get('model') or '(prompts recorded)'} used; ai_assisted={flag}")
    niche = (item.settings.get("niches") or {}).get(m.get("niche")) or {}
    if niche.get("generator") == "image_api" and flag is not True and not gen.get("dry_run"):
        return result("ai_assisted", False,
                      f"{m.get('niche')} normally uses an image model but no model is recorded and "
                      f"ai_assisted={flag}; confirm before listing", WARNING)
    return result("ai_assisted", True, f"no image model recorded; ai_assisted={flag}")


def check_generator_review(item: Item) -> dict:
    """The generator sets needs_review (with review_reasons) for review terms in the brief
    or printed text, or for its own doubts about the output."""
    flagged = bool(item.manifest.get("needs_review"))
    why = [str(r) for r in item.manifest.get("review_reasons") or [] if r]
    detail = "generator marked this item needs_review" + (f": {'; '.join(why)}" if why else "")
    return result("generator_needs_review", not flagged, detail if flagged else "not flagged by the generator", WARNING)
