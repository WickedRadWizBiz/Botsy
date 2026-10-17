"""Curation of a derived pack: k pages out of a master set (docs/PACKS.md section 3).

Greedy and deterministic. Each pick takes the best remaining page by, in order:
    1. quality at least packs.min_quality (weaker pages only when nothing else is left);
    2. not used yet by another derived pack of the same size (two "Set of 5" packs of a
       theme should not sell the same page twice);
    3. variety: the page type picked least so far (round-robin across types);
    4. how well the page fits the theme (optional page "fit", 0..1, default 1.0: a
       dinosaur dot-to-dot before a house in a dinosaur pack);
    5. not used yet by a derived pack of another size (a buyer of the 5-page and the
       10-page pack gets as few pages twice as the master set allows);
    6. a subject not picked yet, then higher quality;
    7. a stable hash of (seed, page_id) to break ties.
The chosen pages keep their order in the set, so a pack reads like the master does.
"""
from __future__ import annotations

import hashlib
from collections import Counter

from core.compliance import normalize


def _quality(page: dict) -> float:
    q = page.get("quality", 1.0)
    return float(q) if isinstance(q, (int, float)) and not isinstance(q, bool) else 1.0


def _fit(page: dict) -> float:
    f = page.get("fit", 1.0)
    return float(f) if isinstance(f, (int, float)) and not isinstance(f, bool) else 1.0


def _tie(seed: str, page_id: str) -> str:
    return hashlib.sha256(f"{seed}|{page_id}".encode()).hexdigest()


def curate(set_doc: dict, k: int, *, size: int | None = None, seed: str = "", exclude_item: str | None = None,
           min_quality: float = 0.5) -> list[dict]:
    """k pages of set_doc for a derived pack of `size` (default k). `exclude_item` is the
    pack's own item id: its earlier use of pages does not count, so a rebuild picks the
    same pages. Raises ValueError when the set has fewer than k pages."""
    pages = list(set_doc.get("pages") or [])
    size = k if size is None else size
    if k < 1 or k > len(pages):
        raise ValueError(f"cannot curate {k} pages from a set of {len(pages)}")
    items = {it.get("item_id"): it for it in set_doc.get("items") or [] if isinstance(it, dict)}

    def derived_uses(page: dict) -> list[int]:
        """Sizes of the other derived packs that use this page."""
        out = []
        for iid in page.get("used_in") or []:
            it = items.get(iid)
            if iid != exclude_item and it and it.get("source") == "derived":
                out.append(it.get("size"))
        return out

    order = {p["page_id"]: i for i, p in enumerate(pages)}
    uses = {p["page_id"]: derived_uses(p) for p in pages}
    reused = {pid: size in u for pid, u in uses.items()}
    shared = {pid: bool(u) for pid, u in uses.items()}
    ties = {p["page_id"]: _tie(seed, p["page_id"]) for p in pages}
    remaining = list(pages)
    chosen: list[dict] = []
    types: Counter = Counter()
    subjects: set[str] = set()
    while len(chosen) < k:
        def key(p: dict):
            subject = normalize(str(p.get("subject") or ""))
            return (_quality(p) < min_quality, reused[p["page_id"]], types[p.get("type")], -_fit(p),
                    shared[p["page_id"]], bool(subject) and subject in subjects, -_quality(p), ties[p["page_id"]])

        best = min(remaining, key=key)
        remaining.remove(best)
        chosen.append(best)
        types[best.get("type")] += 1
        subjects.add(normalize(str(best.get("subject") or "")))
    return sorted(chosen, key=lambda p: order[p["page_id"]])
