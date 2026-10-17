"""Shared fixtures for the pack engine tests (docs/PACKS.md section 3). Offline: fake image
and text clients only; every output, design set and pack state file is in tmp_path."""
import copy
import importlib

import pytest

from core.config import MissingSecretError, load_settings
from generators.imaging import cleanup, common
from generators.imaging import client as client_mod

DATE = "2026-10-15"


@pytest.fixture(scope="session")
def base_settings() -> dict:
    return load_settings()


@pytest.fixture
def settings(base_settings) -> dict:
    """Real settings with small fake images and stickers so the tests stay fast."""
    s = copy.deepcopy(base_settings)
    s.setdefault("imaging", {})["fake_long_side_px"] = 360
    s.setdefault("stickers", {}).update({"sticker_px": 300, "die_cut_border_px": 8, "min_island_px": 20,
                                          "preview_px": 200})
    s["print"]["deliver_both_sizes"] = True
    return s


@pytest.fixture(autouse=True)
def no_real_clients(monkeypatch):
    """Building the real Gemini client or loading rembg is a bug here; the text model has no key."""
    def boom(*a, **k):
        raise AssertionError("tests must not create a real API client")

    def no_key():
        raise MissingSecretError("ANTHROPIC_API_KEY is not set (tests never call the real API)")

    monkeypatch.setattr(common, "run_client", boom)
    monkeypatch.setattr(cleanup, "remove_background_rembg", boom)
    subjects_mod = importlib.import_module("generators.packs.subjects")   # the module, not packs.subjects()
    monkeypatch.setattr(subjects_mod, "_make_client", no_key)   # a real run without a key: built-in tables
    client_mod.reset_run_clients()


def make_brief(niche: str, theme: str = "farm animals", *, n: int = 1, sizes=(5, 20, 30), **extra) -> dict:
    brief = {
        "brief_id": f"{niche}-{DATE}-{n:02d}",
        "niche": niche,
        "theme": theme,
        "audience": "kids 4-8",
        "keywords": [theme],
        "price_usd": 4.0,
        "page_sizes": ["us_letter", "a4"],
        "style_notes": "bold thick outlines",
        "activity_types": ["maze", "word_search", "tracing", "dot_to_dot"],
        "evidence": {"listing_count": 23, "score": 0.8},
        "compliance": {"blocked": [], "flagged": []},
        "needs_review": False,
    }
    if sizes:
        brief["pack_sizes"] = list(sizes)
        brief["page_count"] = max(sizes)
    brief.update(extra)
    return brief


@pytest.fixture
def brief_for():
    return make_brief
