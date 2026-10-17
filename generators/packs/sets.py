"""Design sets: the pages generated for one theme (docs/PACKS.md section 3).

    <out_root>/sets/<set_id>/set.json   {schema: "set/v1", set_id, niche, theme, brief_id, created,
                                         pages: [{page_id, type, subject, spec, asset, quality,
                                         used_in: [item_id]}], ...}
    <out_root>/sets/<set_id>/pages/     image assets (coloring PNG + SVG, sticker PNG); none for
                                        procedural pages, which are rendered from their spec

A page is a procedural spec (type + params + seed, rendered at any page size) or an image
asset, or both. `kind` is "master" (the theme's master set, the source of derived packs)
or "fresh" (pages for one fresh pack, never reused by another pack). `items` lists every
item made from the set, with its size and source, so curation can avoid reusing a page in
two derived packs of the same size and the listing stage can name the overlap.

set.json is written atomically (core.items.write_json). A set is built in a scratch folder
next to it and swapped in when complete, so a failed build never leaves half a set.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from core.items import read_json, write_json

log = logging.getLogger(__name__)

SCHEMA = "set/v1"
SETS_DIR = "sets"


def sets_root(out_root: str | Path) -> Path:
    return Path(out_root) / SETS_DIR


def set_dir(out_root: str | Path, set_id: str) -> Path:
    return sets_root(out_root) / set_id


def load_set(out_root: str | Path, set_id: str) -> dict | None:
    """The set's document, or None when it is missing or unreadable."""
    path = set_dir(out_root, set_id) / "set.json"
    try:
        doc = read_json(path)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == SCHEMA else None


def save_set(out_root: str | Path, doc: dict) -> Path:
    return write_json(set_dir(out_root, doc["set_id"]) / "set.json", doc)


def iter_sets(out_root: str | Path) -> list[dict]:
    """Every readable set under <out_root>/sets, sorted by set id."""
    root = sets_root(out_root)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and (d / "set.json").is_file():
            doc = load_set(out_root, d.name)
            if doc is not None:
                out.append(doc)
    return out


def new_set_doc(set_id: str, *, niche: str, theme: str, theme_key: str, shop_id: str, brief_id, created: str,
                kind: str, size: int, dry_run: bool, model: str | None, ai_assisted: bool,
                system: str = "generic") -> dict:
    return {"schema": SCHEMA, "set_id": set_id, "niche": niche, "theme": theme, "theme_key": theme_key,
            "shop_id": shop_id, "brief_id": brief_id, "created": created, "kind": kind, "size": size,
            "system": system, "dry_run": bool(dry_run), "model": model, "ai_assisted": bool(ai_assisted),
            "pages": [], "items": []}


def page_entry(page: dict) -> dict:
    """A plugin page dict as stored in set.json (unknown keys kept)."""
    out = {"page_id": page["page_id"], "type": page.get("type"), "subject": page.get("subject"),
           "spec": page.get("spec") or {}, "asset": page.get("asset"), "quality": float(page.get("quality", 1.0)),
           "used_in": list(page.get("used_in") or [])}
    for k, v in page.items():
        out.setdefault(k, v)
    return out


def asset_paths(page: dict) -> list[str]:
    """The page's asset files, relative to its set folder."""
    asset = page.get("asset")
    if isinstance(asset, str):
        return [asset]
    if isinstance(asset, dict):
        return [v for v in asset.values() if isinstance(v, str)]
    return []


def is_complete(out_root: str | Path, doc: dict, size: int | None = None) -> bool:
    """Every page is there (with its asset files) and, when given, there are `size` of them."""
    pages = doc.get("pages") or []
    if not pages or (size is not None and len(pages) != size):
        return False
    base = set_dir(out_root, doc["set_id"])
    return all((base / rel).is_file() for p in pages for rel in asset_paths(p))


def record_item(doc: dict, item_id: str, *, page_ids: list[str], size: int, source: str, created: str) -> dict:
    """Note that item_id uses page_ids (used_in) and is made from the set (items)."""
    wanted = set(page_ids)
    for p in doc.get("pages") or []:
        used = [i for i in p.get("used_in") or [] if i != item_id]
        if p["page_id"] in wanted:
            used.append(item_id)
        p["used_in"] = used
    items = [it for it in doc.get("items") or [] if it.get("item_id") != item_id]
    items.append({"item_id": item_id, "size": size, "source": source, "created": created})
    doc["items"] = items
    return doc


@contextmanager
def building(out_root: str | Path, set_id: str) -> Iterator[Path]:
    """A scratch set folder (with pages/) next to the real one, removed afterwards; commit()
    moves it into place."""
    root = sets_root(out_root)
    made_root = not root.exists()
    root.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix=f".build-{set_id}-", dir=root))
    try:
        (build / "pages").mkdir()
        yield build
    finally:
        shutil.rmtree(build, ignore_errors=True)
        if made_root:
            try:
                root.rmdir()                # a first build that failed leaves no empty sets/ behind
            except OSError:
                pass                        # not empty: a set was committed


def commit(out_root: str | Path, build: Path, doc: dict) -> Path:
    """Replace the set's folder with the finished build and write set.json."""
    d = set_dir(out_root, doc["set_id"])
    d.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(d / "pages", ignore_errors=True)
    (build / "pages").replace(d / "pages")
    save_set(out_root, doc)
    return d
