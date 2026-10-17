"""Shared fixtures for the tabletop RPG generator tests (offline, outputs in tmp_path)."""
import copy

import pypdfium2 as pdfium
import pytest

from core.compliance import Blocklist
from core.config import load_settings
from generators.ttrpg.catalog import VARIANTS
from generators.ttrpg.plan import page_spec, system_fields

SIZES = ("us_letter", "a4")


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


def make_ctx(theme: str = "dark fantasy", system: str = "generic", **kw) -> dict:
    ctx = {"theme": theme, "niche": "ttrpg", "audience": "adults", "keywords": [], "style": "", "system": system,
           "item_seed": "", "page_sizes": list(SIZES)}
    ctx.update(kw)
    return ctx


def every_layout(system: str, genre: str = "classic_fantasy", settings: dict | None = None) -> list[dict]:
    """One page for every page type and layout variant, for one game system."""
    fields, fields_from = system_fields(system, settings)
    pages = []
    for page_type, variants in VARIANTS.items():
        for v in variants:
            pages.append(page_spec(page_type, v.name, genre=genre, fields=fields, fields_from=fields_from,
                                   seed="test", index=len(pages) + 1, page_id=f"{page_type}-{v.name}"))
    return pages


def pdf_texts(path) -> list[str]:
    """The text of every page, as pypdfium2 extracts it (what QA and a buyer's reader see)."""
    doc = pdfium.PdfDocument(str(path))
    try:
        out = []
        for i in range(len(doc)):
            tp = doc[i].get_textpage()
            out.append(tp.get_text_range())
            tp.close()
        return out
    finally:
        doc.close()


def pdf_title(path) -> str:
    doc = pdfium.PdfDocument(str(path))
    try:
        return str(doc.get_metadata_dict().get("Title") or "")
    finally:
        doc.close()


@pytest.fixture
def ctx():
    return make_ctx()
