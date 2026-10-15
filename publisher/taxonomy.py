"""Seller taxonomy lookup, to fill settings listing.taxonomy_ids (one id per niche).

getSellerTaxonomyNodes returns the whole tree (each node: id, level, name, parent_id,
children, full_path_taxonomy_ids) and needs only the API key, no OAuth token.
"""
from __future__ import annotations


def flatten(nodes: list[dict], parents: tuple[str, ...] = ()) -> list[dict]:
    """Tree -> [{"id", "path": "A > B > C", "level", "leaf"}], depth first."""
    rows = []
    for node in nodes:
        names = (*parents, str(node.get("name", "")))
        children = node.get("children") or []
        rows.append({"id": node.get("id"), "path": " > ".join(names),
                     "level": node.get("level", len(parents)), "leaf": not children})
        rows += flatten(children, names)
    return rows


def search(rows: list[dict], text: str | None) -> list[dict]:
    """Rows whose path contains every word of `text` (case-insensitive)."""
    words = (text or "").lower().split()
    return [r for r in rows if all(w in r["path"].lower() for w in words)]


def list_nodes(client, text: str | None = None) -> list[dict]:
    return search(flatten(client.get_seller_taxonomy_nodes()), text)
