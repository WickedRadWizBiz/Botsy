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
from core.items import write_json

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


@pytest.fixture
def settings(base_settings) -> dict:
    s = copy.deepcopy(base_settings)
    s["shops"][0]["name"] = "Test Shop"
    return s


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
