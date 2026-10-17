"""Fixtures for listing tests: fake items in tmp_path and a fake Anthropic client.

No network: an autouse fixture makes any socket connection fail, and the fake client
records every request so tests can check what the model would have been sent.
"""
from __future__ import annotations

import copy
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config import load_settings
from core.items import read_json, write_json

ITEM_ID = "2026-10-15-coloring_pages-farm-animals-1a2b"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted in a listing test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(scope="session")
def base_settings() -> dict:
    return load_settings()


def pinned(base: dict) -> dict:
    """A copy of `base` with the values the user fills in during setup (shop name, default
    prices, taxonomy ids) set to fixed test values, so the tests stay green after setup."""
    s = copy.deepcopy(base)
    s["shops"][0]["name"] = "Test Shop"
    s["shops"][0]["default_price_usd"] = {"stickers": 3.5, "coloring_pages": 4.0, "activity_sheets": 4.0}
    s.setdefault("listing", {})["taxonomy_ids"] = {"stickers": None, "coloring_pages": None, "activity_sheets": None}
    return s


@pytest.fixture
def settings(base_settings) -> dict:
    return pinned(base_settings)


def make_item(root: Path, *, niche: str = "coloring_pages", item_id: str = ITEM_ID, ai_assisted: bool = True,
              mockups: int = 5, status: str = "qa_passed", qa_passed: bool = True, **overrides) -> Path:
    """A QA-passed item folder with placeholder files (the listing stage only needs them to exist)."""
    d = root / item_id
    (d / "files").mkdir(parents=True, exist_ok=True)
    (d / "mockups").mkdir(exist_ok=True)
    sizes = ("us_letter", "a4")
    if niche == "stickers":
        files = [{"path": "files/farm-animals-stickers-png.zip", "kind": "zip", "count": 12, "dpi": 300}]
        files += [{"path": f"files/farm-animals-sticker-sheet-{s}.pdf", "kind": "pdf", "page_size": s, "pages": 1,
                   "dpi": 300} for s in sizes]
        pack = {"stickers": 12, "contents": [{"sticker": 1, "subject": "happy cow"}, {"sticker": 2, "subject": "tractor"}]}
    elif niche in ("stationery", "ttrpg"):
        label = "planner" if niche == "stationery" else "rpg-sheets"
        files = [{"path": f"files/farm-animals-{label}-20-pages-{s}.pdf", "kind": "pdf", "page_size": s, "pages": 20,
                  "dpi": 300} for s in sizes]
        types = (["daily_planner"] * 8 + ["brain_dump"] * 6 + ["habit_tracker"] * 6 if niche == "stationery"
                 else ["character_sheet"] * 8 + ["session_notes"] * 6 + ["npc_tracker"] * 6)
        pack = {"pages": 20, "title_pages": 0, "page_types": types,
                "contents": [{"page": i, "type": t, "title": f"Page {i}"} for i, t in enumerate(types, 1)]}
    elif niche == "activity_sheets":
        files = [{"path": f"files/farm-animals-activity-pack-{s}.pdf", "kind": "pdf", "page_size": s, "pages": 14,
                  "dpi": 300} for s in sizes]
        pack = {"activity_pages": 10, "title_pages": 1, "answer_key_pages": 3,
                "activity_types": ["maze", "word_search"],
                "contents": [{"page": i, "type": "maze" if i % 2 else "word_search", "title": f"Page {i}"}
                             for i in range(1, 11)]}
    else:
        files = [{"path": f"files/farm-animals-coloring-pages-{s}.pdf", "kind": "pdf", "page_size": s, "pages": 21,
                  "dpi": 300} for s in sizes]
        pack = {"pages": 20, "contents": [{"page": 1, "subject": "friendly cow"}, {"page": 2, "subject": "piglet"}]}
    for f in files:
        (d / f["path"]).write_bytes(b"placeholder")
    shots = []
    for i in range(1, mockups + 1):
        rel = f"mockups/mockup-{i:02d}.jpg"
        (d / rel).write_bytes(b"placeholder")
        shots.append(rel)
    manifest = {
        "schema": "manifest/v1", "item_id": item_id, "niche": niche, "shop_id": "main",
        "brief_id": f"{niche}-2026-10-15-01", "brief_file": f"data/briefs/{niche}-2026-10-15.json",
        "theme": "farm animals", "keywords": ["farm animal coloring pages", "barn animals"],
        "audience": "kids 4-8", "price_usd": 4.0, "created": "2026-10-15", "ai_assisted": ai_assisted,
        "needs_review": False, "generator": {"name": niche, "dry_run": True}, "prompts": [],
        "files": files, "previews": [], "assets": [], "status": status, "pack": pack,
    }
    manifest.update(overrides)
    write_json(d / "manifest.json", manifest)
    write_json(d / "qa_report.json", {"schema": "qa/v1", "item_id": item_id, "checked": "2026-10-15",
                                      "passed": qa_passed, "needs_review": False, "checks": [], "mockups": shots,
                                      "reasons": []})
    return d


@pytest.fixture
def item(tmp_path) -> Path:
    return make_item(tmp_path / "out")


def pack_item(root: Path, *, niche: str = "coloring_pages", size: int = 20, source: str = "master",
              related=(), item_id: str | None = None, **overrides) -> Path:
    """A pack item (docs/PACKS.md section 3): manifest pack {size, source, set_id, page_ids,
    page_types, type_counts, related} on top of make_item's niche fields."""
    from collections import Counter

    import hashlib

    item_id = item_id or f"2026-10-16-{niche}-farm-animals-{hashlib.sha1(f'{size}{source}'.encode()).hexdigest()[:4]}"
    d = make_item(root, niche=niche, item_id=item_id, **overrides)
    m = read_json(d / "manifest.json")
    pack = dict(m["pack"])
    n = size
    if niche == "stickers":
        pack.update(stickers=n, requested_stickers=n)
        for f in m["files"]:
            if f["kind"] == "zip":
                f["count"] = n
    elif niche == "activity_sheets":
        pack.update(activity_pages=n)
    else:
        pack["pages"] = n
        if niche in ("stationery", "ttrpg"):
            pack["page_types"] = (pack["page_types"] * 3)[:n]
            pack["contents"] = [{"page": i, "type": t, "title": f"Page {i}"}
                                for i, t in enumerate(pack["page_types"], 1)]
            for f in m["files"]:
                f["pages"] = n
    types = pack.get("page_types") or [niche] * n
    pack.update(size=size, source=source, n=1, set_id=f"2026-10-16-{niche}-farm-animals-0a0a", theme=m["theme"],
                page_ids=[f"p{i:02d}" for i in range(1, n + 1)], page_types=types, type_counts=dict(Counter(types)),
                related=[dict(r) for r in related])
    m["pack"] = pack
    write_json(d / "manifest.json", m)
    return d


# ------------------------------------------------------------------ game systems (fake registry)

SYSTEMS = {
    "generic": {"id": "generic", "label": "System-Neutral", "names": [], "owner": None, "owner_names": [],
                "disclaimer": "", "default": True},
    "dnd5e": {"id": "dnd5e", "label": "D&D 5e",
              "names": ["D&D 5e", "Dungeons & Dragons 5e", "5e", "DnD 5e", "Fifth Edition", "D&D", "DnD",
                        "Dungeons & Dragons"],
              "owner": "Wizards of the Coast", "owner_names": ["Wizards of the Coast"],
              "disclaimer": "Unofficial fan-made product, not affiliated with or endorsed by Wizards of the Coast. "
                            "Dungeons & Dragons and D&D are trademarks of Wizards of the Coast.", "default": False},
    "pf2e": {"id": "pf2e", "label": "Pathfinder 2e", "names": ["Pathfinder 2e", "PF2e", "Pathfinder"],
             "owner": "Paizo Inc.", "owner_names": ["Paizo"],
             "disclaimer": "Unofficial fan-made product, not affiliated with or endorsed by Paizo Inc. "
                           "Pathfinder is a registered trademark of Paizo Inc.", "default": False},
}


class FakeSystems:
    """Stands in for core.systems (owned by the ttrpg build): the same functions over SYSTEMS."""

    def __init__(self, registry: dict):
        self.registry = copy.deepcopy(registry)

    def load_systems(self, settings=None, **kw):
        return copy.deepcopy(self.registry)

    def default_system(self, settings=None, **kw):
        return "generic"

    def _get(self, sid):
        if sid not in self.registry:
            raise KeyError(f"unknown game system id {sid!r}")
        return self.registry[sid]

    def names_for(self, sid, settings=None, **kw):
        return list(self._get(sid)["names"])

    def label_for(self, sid, settings=None, **kw):
        return self._get(sid)["label"]

    def disclaimer_for(self, sid, settings=None, **kw):
        return self._get(sid)["disclaimer"]

    def detect_systems(self, text, settings=None, **kw):
        from core.compliance import Blocklist

        out = []
        for sid, e in self.registry.items():
            terms = [*e["names"], *([e["owner"]] if e["owner"] else []), *e["owner_names"]]
            if terms and Blocklist(terms).check(text or "").blocked:
                out.append(sid)
        return out


@pytest.fixture
def fake_systems(monkeypatch) -> FakeSystems:
    import core.systems

    fake = FakeSystems(SYSTEMS)
    for name in ("load_systems", "default_system", "names_for", "label_for", "disclaimer_for", "detect_systems"):
        monkeypatch.setattr(core.systems, name, getattr(fake, name))
    return fake


GOOD = {
    "title": "Farm Animal Coloring Pages for Kids, 20 Printable Barn Animal Pages, US Letter and A4 PDF",
    "tags": ["farm animal coloring", "barn animals", "coloring pages", "kids coloring pages", "printable coloring",
             "farm coloring sheets", "cow coloring page", "animal coloring", "preschool coloring",
             "rainy day activity", "farm birthday", "coloring for kids", "instant download"],
    "hook": "Twenty friendly farm animals are ready to color, from a smiling cow to a muddy little piglet.",
    "about": "Each page has bold outlines and big, simple shapes that suit young colorers. "
             "The set works well for children aged four to eight.",
    "suggested_uses": ["Rainy-day fun at home", "Farm-themed birthday party activity", "Quiet time after school"],
    "alt_texts": [f"Listing photo {i} showing farm animal coloring pages" for i in range(1, 6)],
}


def good(**changes) -> dict:
    d = copy.deepcopy(GOOD)
    d.update(changes)
    return d


class FakeMessages:
    def __init__(self, owner: FakeClient):
        self.owner = owner

    def create(self, **kwargs):
        self.owner.calls.append(kwargs)
        if not self.owner.responses:
            raise AssertionError("fake client ran out of canned responses")
        r = self.owner.responses.pop(0)
        return r if isinstance(r, SimpleNamespace) else response(r)


class FakeClient:
    """Stands in for anthropic.Anthropic: .messages.create and .beta.messages.create."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.messages = FakeMessages(self)
        self.beta = SimpleNamespace(messages=FakeMessages(self))


def response(payload, *, stop_reason: str = "end_turn", model: str = "claude-opus-5-5", blocks=None) -> SimpleNamespace:
    """A Message-like object whose text block holds `payload` as JSON (or a raw string)."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    content = blocks if blocks is not None else [SimpleNamespace(type="thinking", thinking=""),
                                                 SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=content, stop_reason=stop_reason, model=model, stop_details=None)
