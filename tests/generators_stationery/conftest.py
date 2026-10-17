"""Shared fixtures for the stationery generator tests (offline, outputs in tmp_path)."""
import copy

import pytest

from core.compliance import Blocklist
from core.config import load_settings
from generators.stationery.catalog import CATALOG
from generators.stationery.plan import page_spec
from generators.stationery.styles import STYLES


@pytest.fixture(scope="session")
def base_settings() -> dict:
    return load_settings()


@pytest.fixture
def settings(base_settings) -> dict:
    s = copy.deepcopy(base_settings)
    s["print"]["deliver_both_sizes"] = True
    return s


@pytest.fixture(scope="session")
def blocklist() -> Blocklist:
    return Blocklist.load()


def make_ctx(theme: str = "adhd", **kw) -> dict:
    ctx = {"theme": theme, "niche": "stationery", "audience": "adults", "keywords": [], "style": "",
           "system": "generic", "item_seed": "", "page_sizes": ["us_letter", "a4"]}
    ctx.update(kw)
    return ctx


def every_layout(style: str, season: str | None = None) -> list[dict]:
    """One page for every page type and layout variant, in one style."""
    pages = []
    for page_type, variants in CATALOG.items():
        for v in variants:
            pages.append(page_spec(page_type, v.name, style=style, season=season, seed="test", index=len(pages) + 1,
                                   page_id=f"{page_type}-{v.name}"))
    return pages


ALL_STYLES = [(s, "autumn" if s == "seasonal" else None) for s in STYLES] + [("seasonal", "winter"),
                                                                             ("seasonal", "spring"),
                                                                             ("seasonal", "summer")]


@pytest.fixture
def ctx():
    return make_ctx()
