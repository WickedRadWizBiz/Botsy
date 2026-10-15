"""Shared fixtures for the image generator tests. Fully offline: fake image clients, no
rembg model, no API keys; every output goes to tmp_path."""
import copy

import pytest

from core.compliance import Blocklist
from core.config import load_settings
from generators.imaging import cleanup, common
from generators.imaging import client as client_mod


@pytest.fixture(scope="session")
def base_settings() -> dict:
    return load_settings()


@pytest.fixture
def settings(base_settings) -> dict:
    """Real settings with smaller images so the tests stay fast."""
    s = copy.deepcopy(base_settings)
    s.setdefault("imaging", {})["fake_long_side_px"] = 480
    s.setdefault("stickers", {}).update({"sticker_px": 360, "die_cut_border_px": 10, "min_island_px": 30,
                                          "preview_px": 300})
    return s


@pytest.fixture(scope="session")
def blocklist() -> Blocklist:
    return Blocklist.load()


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    """Any attempt to build the real Gemini client or load rembg in a test is a bug."""
    def boom(settings):
        raise AssertionError("tests must not create the real Gemini client")
    def no_rembg(img, model=None):
        raise AssertionError("tests must not load rembg (it downloads a model); use floodfill")
    monkeypatch.setattr(common, "run_client", boom)
    monkeypatch.setattr(cleanup, "remove_background_rembg", no_rembg)
    client_mod.reset_run_clients()


@pytest.fixture
def coloring_brief() -> dict:
    return {
        "brief_id": "coloring_pages-2026-10-15-01",
        "niche": "coloring_pages",
        "theme": "farm animals",
        "audience": "kids 4-8",
        "keywords": ["farm animal coloring pages", "barn animals"],
        "page_count": 3,
        "price_usd": 4.0,
        "page_sizes": ["us_letter", "a4"],
        "style_notes": "bold thick outlines, large simple shapes, no shading",
        "activity_types": [],
        "evidence": {"listing_count": 23, "score": 0.81},
        "compliance": {"blocked": [], "flagged": []},
        "needs_review": False,
    }


@pytest.fixture
def sticker_brief() -> dict:
    return {
        "brief_id": "stickers-2026-10-15-01",
        "niche": "stickers",
        "theme": "kawaii food",
        "audience": "digital planner and journaling fans, teens and adults",
        "keywords": ["kawaii food stickers"],
        "page_count": 4,
        "price_usd": 3.5,
        "page_sizes": ["us_letter", "a4"],
        "style_notes": "bold clean outlines with a white die-cut border, flat cheerful colors, transparent background",
        "activity_types": [],
        "evidence": {"listing_count": 15, "score": 0.7},
        "compliance": {"blocked": [], "flagged": []},
        "needs_review": False,
    }
