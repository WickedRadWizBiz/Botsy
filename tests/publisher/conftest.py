"""Fake items and a fake Etsy for publisher tests. Everything lives in tmp_path; no network."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from PIL import Image

from core.compliance import load_ai_disclosure
from core.config import load_settings
from core.items import write_json, write_manifest
from publisher.fake import FakeEtsyClient, FakeEtsySession

SHOP_ID = 12345678
ITEM_ID = "2026-10-15-coloring_pages-farm-animals-1a2b"


@pytest.fixture(scope="session")
def base_settings() -> dict:
    return load_settings()


@pytest.fixture
def settings(base_settings) -> dict:
    s = copy.deepcopy(base_settings)
    s["shops"][0]["name"] = "Test Shop"  # the shipped placeholder name is refused for real drafts
    s["publisher"] = {"retries": 2}
    # Pin what the user fills in or may switch in config/settings.yaml, so the tests do not
    # depend on it: no taxonomy ids chosen yet, and the disclosure on every listing.
    lst = s.setdefault("listing", {})
    lst["taxonomy_ids"] = {niche: None for niche in ("stickers", "coloring_pages", "activity_sheets")}
    lst["always_disclose_ai"] = True
    return s


@pytest.fixture
def out_root(tmp_path) -> Path:
    return tmp_path / "out"


@pytest.fixture
def review_dir(out_root) -> Path:
    return out_root / "review"


@pytest.fixture
def session() -> FakeEtsySession:
    return FakeEtsySession(SHOP_ID)


@pytest.fixture
def client(session, settings) -> FakeEtsyClient:
    return FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)


def make_item(out_root: Path, settings: dict, item_id: str = ITEM_ID, *, manifest: dict | None = None,
              listing: dict | None = None, qa: dict | None = None, n_images: int = 2,
              n_files: int = 1, dry_run_item: bool = False) -> Path:
    """A listed coloring-page item: manifest, passing QA report, listing.json, JPEG mockups, a PDF."""
    d = out_root / item_id
    (d / "mockups").mkdir(parents=True)
    (d / "files").mkdir()
    images = []
    for i in range(n_images):
        rel = f"mockups/mockup-{i + 1:02d}.jpg"
        Image.new("RGB", (40, 30), (200, 180 - i * 20, 160)).save(d / rel, "JPEG")
        images.append(rel)
    files = []
    for i in range(n_files):
        rel = f"files/farm-animals-{i + 1}.pdf"
        (d / rel).write_bytes(b"%PDF-1.4\n% fake test pdf\n" + bytes(200))
        files.append(rel)
    shop_name = settings["shops"][0]["name"]
    m = {"schema": "manifest/v1", "item_id": item_id, "niche": "coloring_pages", "shop_id": "main",
         "theme": "farm animals", "price_usd": 4.0, "created": "2026-10-15", "ai_assisted": True,
         "needs_review": False, "generator": {"name": "coloring_pages", "dry_run": dry_run_item},
         "files": [{"path": f, "kind": "pdf"} for f in files], "status": "listed"}
    m.update(manifest or {})
    write_manifest(d, m)
    q = {"schema": "qa/v1", "item_id": item_id, "passed": True, "needs_review": False, "checks": [],
         "mockups": images}
    q.update(qa or {})
    write_json(d / "qa_report.json", q)
    tags = ["farm coloring", "animal coloring", "kids coloring", "printable pages", "cow coloring",
            "barn animals", "coloring book", "kids activity", "farm animals", "pig coloring",
            "horse coloring", "digital download", "coloring pdf"]
    lst = {"schema": "listing/v1", "item_id": item_id, "shop_id": "main",
           "title": "Farm Animal Coloring Pages for Kids, 20 Printable Pages",
           "tags": tags,
           "description": "Twenty farm animal coloring pages.\n\n" + load_ai_disclosure(shop_name),
           "alt_texts": [f"Farm animal coloring page mockup {i + 1}" for i in range(n_images)],
           "price_usd": 4.0, "quantity": 999, "who_made": "i_did", "when_made": "made_to_order",
           "taxonomy_id": 1234, "type": "download", "image_paths": images, "file_paths": files,
           "ai_disclosure_included": True, "compliance": {"blocked": [], "flagged": []},
           "needs_review": False, "model": "claude-opus-5-5", "created": "2026-10-15"}
    lst.update(listing or {})
    write_json(d / "listing.json", lst)
    return d


@pytest.fixture
def item(out_root, settings) -> Path:
    return make_item(out_root, settings)
