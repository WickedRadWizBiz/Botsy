"""Shared fixtures for the activity-sheet generator tests (offline, outputs in tmp_path)."""
import copy

import pytest

from core.compliance import Blocklist
from core.config import load_settings
from generators.activity_sheets import DEFAULTS, generate


@pytest.fixture(scope="session")
def settings() -> dict:
    return load_settings()


@pytest.fixture(scope="session")
def blocklist() -> Blocklist:
    return Blocklist.load()


@pytest.fixture
def brief() -> dict:
    return {
        "brief_id": "activity_sheets-2026-10-15-01",
        "niche": "activity_sheets",
        "theme": "farm animals",
        "audience": "kids 4-8",
        "keywords": ["farm animal activity pages", "barn animals"],
        "page_count": 6,
        "price_usd": 4.0,
        "page_sizes": ["us_letter", "a4"],
        "style_notes": "",
        "activity_types": ["maze", "word_search", "tracing", "dot_to_dot"],
        "evidence": {"listing_count": 23, "score": 0.8},
        "compliance": {"blocked": [], "flagged": []},
        "needs_review": False,
    }


@pytest.fixture
def both_sizes(settings) -> dict:
    s = copy.deepcopy(settings)
    s["print"]["deliver_both_sizes"] = True
    return s


@pytest.fixture(scope="session")
def pack_dir(tmp_path_factory, settings):
    """One generated pack shared by the read-only tests (generation takes about a second)."""
    s = copy.deepcopy(settings)
    s["print"]["deliver_both_sizes"] = True
    brief = {
        "brief_id": "activity_sheets-2026-10-15-02", "niche": "activity_sheets", "theme": "under the sea",
        "audience": "kids 4-8", "keywords": ["ocean activity pages"], "page_count": 9, "price_usd": 4.0,
        "page_sizes": ["us_letter", "a4"], "activity_types": ["maze", "word_search", "tracing", "dot_to_dot"],
        "evidence": {"listing_count": 12, "score": 0.6}, "needs_review": False,
    }
    out = tmp_path_factory.mktemp("out")
    [d] = generate(brief, brief_file="data/briefs/activity_sheets-2026-10-15.json", out_root=out,
                   date="2026-10-15", settings=s)
    return d


@pytest.fixture
def cfg() -> dict:
    return copy.deepcopy(DEFAULTS)
