"""The product facts a listing is written from: our own item data only.

Everything here comes from our manifest (built from our aggregated brief), our QA report
and our settings. Nothing from another seller's listing ever reaches this dict, and it is
the only thing the text model sees (CLAUDE.md compliance rules). It holds no file paths,
shop names or URLs.
"""
from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath

from core.items import content_count

NICHES = {
    "coloring_pages": {"one": "coloring page", "many": "coloring pages", "tag": "coloring",
                       "product": "printable coloring pages"},
    "activity_sheets": {"one": "activity page", "many": "activity pages", "tag": "activity",
                        "product": "printable activity pages"},
    "stickers": {"one": "digital sticker", "many": "digital stickers", "tag": "stickers",
                 "product": "digital stickers (transparent PNG files) with a printable sticker sheet"},
}
OTHER_NICHE = {"one": "printable page", "many": "printable pages", "tag": "printable", "product": "digital download"}

ACTIVITY_LABELS = {
    "maze": ("maze", "mazes"),
    "word_search": ("word search", "word searches"),
    "tracing": ("tracing page", "tracing pages"),
    "dot_to_dot": ("dot-to-dot", "dot-to-dots"),
}

FILE_TYPE_LABELS = {"pdf": "PDF", "png": "PNG", "png_transparent": "PNG (transparent background)",
                    "zip": "ZIP", "svg": "SVG"}


def niche_labels(niche: str) -> dict:
    return NICHES.get(niche, OTHER_NICHE)


def safe_rel(path) -> str | None:
    """A path stored in our JSON, if it stays inside the item folder; otherwise None."""
    if not isinstance(path, str) or not path.strip() or "\\" in path:
        return None
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts:
        return None
    return p.as_posix()


def page_size_label(key: str, cfg: dict, settings: dict) -> str:
    labels = cfg.get("page_size_labels") or {}
    if key in labels:
        return labels[key]
    size = (settings.get("print", {}).get("page_sizes") or {}).get(key)
    name = key.replace("_", " ").title()
    return f"{name} ({size['width_in']:g} x {size['height_in']:g} in)" if size else name


def mockup_descriptions(manifest: dict, n_images: int, n_sizes: int, cfg: dict) -> list[str]:
    """What each listing photo shows, for alt text. QA's photo layouts come in a fixed
    order; when QA made every layout we can name each one. If any layout failed, the
    order is unknown, so we fall back to a generic description rather than risk alt
    text that describes the wrong picture."""
    layouts = cfg.get("mockup_layouts") or {}
    niche = manifest.get("niche")
    known = list(layouts.get(niche) or layouts.get("default") or [])
    if known and n_images == len(known):
        if niche != "stickers" and n_sizes < 2:  # QA shows a second close-up instead of the sizes card
            known[-1] = layouts.get("single_size_last") or known[-1]
        return known
    return [f"listing photo {i} of {n_images}: a preview of the product" for i in range(1, n_images + 1)]


def gather(manifest: dict, image_paths: list[str], cfg: dict, settings: dict) -> dict:
    """The model-facing facts for one item (JSON-serializable, deterministic)."""
    niche = str(manifest.get("niche") or "")
    labels = niche_labels(niche)
    pack = manifest.get("pack") if isinstance(manifest.get("pack"), dict) else {}
    files = _entries(manifest, "files")

    size_keys = list(dict.fromkeys(e["page_size"] for e in files
                                   if e.get("kind") == "pdf" and isinstance(e.get("page_size"), str)))
    kinds = list(dict.fromkeys(e.get("kind") for e in files if e.get("kind")))
    pdf_pages = sorted({e["pages"] for e in files if e.get("kind") == "pdf" and isinstance(e.get("pages"), int)})
    dpis = sorted({e["dpi"] for e in files if isinstance(e.get("dpi"), int)})
    max_subjects = int(cfg.get("max_subjects", 12))

    contents = [c for c in pack.get("contents") or [] if isinstance(c, dict)]
    subjects = list(dict.fromkeys(str(c["subject"]).strip() for c in contents
                                  if isinstance(c.get("subject"), str) and c["subject"].strip()))
    # Activity mix with counts from the pack contents, or just the types (count None).
    activity: dict = dict(Counter(c.get("type") for c in contents if c.get("type") in ACTIVITY_LABELS))
    if not activity:
        activity = {t: None for t in pack.get("activity_types") or manifest.get("activity_types") or []
                    if t in ACTIVITY_LABELS}

    zips = _zips(manifest)
    descriptions = mockup_descriptions(manifest, len(image_paths), len(size_keys), cfg)
    return {
        "niche": niche,
        "product_type": labels["product"],
        "theme": str(manifest.get("theme") or "").strip(),
        "audience": str(manifest.get("audience") or "").strip(),
        "keywords": [str(k).strip() for k in manifest.get("keywords") or [] if str(k).strip()],
        "content_count": content_count(manifest),
        "content_unit": labels["many"],
        "answer_key_pages": _count(pack.get("answer_key_pages")),
        "activities": activity,
        "subjects": subjects[:max_subjects],
        "page_sizes": [page_size_label(k, cfg, settings) for k in size_keys],
        "pdf_pages": pdf_pages[0] if len(pdf_pages) == 1 else None,
        "file_types": [FILE_TYPE_LABELS.get(k, str(k).upper()) for k in kinds],
        "png_in_zip": sum(e["count"] for e in zips),
        "zip_files": len(zips),
        "dpi": dpis[0] if len(dpis) == 1 else None,
        "ai_assisted": bool(manifest.get("ai_assisted")),
        "images": [{"image": i, "shows": d} for i, d in enumerate(descriptions, 1)],
    }


def _entries(manifest: dict, key: str) -> list[dict]:
    return [e for e in manifest.get(key) or [] if isinstance(e, dict)]


def _count(value) -> int:
    """A positive count from our JSON, else 0 (never a bool or a negative number)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _zips(manifest: dict) -> list[dict]:
    """Delivered ZIP files that say how many files they hold."""
    return [e for e in _entries(manifest, "files") if e.get("kind") == "zip" and _count(e.get("count"))]
