"""Fake items for QA tests: small reportlab PDFs, PNG previews and sticker cut-outs, all in tmp_path."""
from __future__ import annotations

import copy
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import qa
from core.config import load_settings, section
from core.items import write_manifest
from core.render import render_pdf_pages
from qa.checks import Item, check_paths

LETTER = (612.0, 792.0)
A4 = (595.44, 841.68)


@pytest.fixture(scope="session")
def base_settings() -> dict:
    return load_settings()


@pytest.fixture
def settings(base_settings) -> dict:
    """Real settings with small mockups so tests stay fast."""
    s = copy.deepcopy(base_settings)
    s["qa"] = {"mockups": {"width": 800, "height": 600}}
    return s


def draw_art(c: canvas.Canvas, size, line_pt: float = 3.0, gray_fill: bool = False) -> None:
    """Simple closed shapes: several regions to color, outlines `line_pt` thick."""
    w, h = size
    c.setLineWidth(line_pt)
    c.setStrokeGray(0)
    for i, (fx, fy) in enumerate([(0.3, 0.7), (0.7, 0.7), (0.5, 0.45)]):
        c.circle(w * fx, h * fy, w * 0.13, stroke=1, fill=0)
    c.rect(w * 0.15, h * 0.12, w * 0.3, h * 0.15, stroke=1, fill=0)
    c.rect(w * 0.55, h * 0.12, w * 0.3, h * 0.15, stroke=1, fill=0)
    if gray_fill:
        c.setFillGray(0.5)
        c.rect(w * 0.1, h * 0.3, w * 0.8, h * 0.05, stroke=0, fill=1)


def make_pdf(path: Path, size=LETTER, pages: int = 2, line_pt: float = 3.0, blank=(), black=(),
             gray_fill: bool = False, image: Image.Image | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=size)
    for i in range(pages):
        if i in black:
            c.setFillGray(0)
            c.rect(0, 0, size[0], size[1], stroke=0, fill=1)
        elif image is not None:
            c.drawImage(ImageReader(image), 36, 36, width=size[0] - 72, height=size[1] - 72)
        elif i not in blank:
            draw_art(c, size, line_pt, gray_fill)
        c.showPage()
    c.save()
    return path


def sticker_png(path: Path, size: int = 600, dpi: int | None = 300, alpha: bool = True,
                opaque_border: bool = False, empty: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not alpha:
        img = Image.new("RGB", (size, size), "white")
        ImageDraw.Draw(img).ellipse((size * 0.15, size * 0.15, size * 0.85, size * 0.85), fill=(240, 120, 80))
    else:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if opaque_border:
            d.rectangle((0, 0, size, size), fill=(255, 255, 255, 255))
        if not empty:
            d.ellipse((size * 0.15, size * 0.15, size * 0.85, size * 0.85), fill=(240, 120, 80, 255))
            d.ellipse((size * 0.35, size * 0.35, size * 0.5, size * 0.5), fill=(40, 40, 40, 255))
    img.save(path, "PNG", **({"dpi": (dpi, dpi)} if dpi else {}))
    return path


def base_manifest(item_id: str, niche: str = "coloring_pages", **overrides) -> dict:
    m = {
        "schema": "manifest/v1", "item_id": item_id, "niche": niche, "shop_id": "main",
        "brief_id": f"{niche}-2026-10-15-01", "brief_file": f"data/briefs/{niche}-2026-10-15.json",
        "theme": "farm animals", "keywords": ["farm animal coloring pages", "barn animals"],
        "audience": "kids 4-8", "price_usd": 4.0, "created": "2026-10-15",
        "ai_assisted": True, "needs_review": False,
        "generator": {"name": niche, "model": "fake-image-model", "dry_run": True},
        "prompts": ["Simple coloring page of a friendly cow, bold outlines"],
        "files": [], "previews": [], "assets": [], "status": "generated",
    }
    m.update(overrides)
    return m


def write_previews(d: Path, pdf: Path, dpi: int = 40) -> list[dict]:
    out = []
    for i, img in enumerate(render_pdf_pages(pdf, dpi=dpi), start=1):
        rel = f"previews/page-{i:02d}.png"
        (d / "previews").mkdir(parents=True, exist_ok=True)
        img.save(d / rel)
        out.append({"path": rel, "page": i})
    return out


def coloring_item(root: Path, item_id: str = "2026-10-15-coloring_pages-farm-animals-0001", *,
                  line_pt: float = 3.0, pages: int = 2, **overrides) -> Path:
    """A complete, valid coloring-page item: Letter + A4 PDFs, previews, manifest."""
    d = root / item_id
    files = []
    for name, size in (("us_letter", LETTER), ("a4", A4)):
        rel = f"files/farm-animals-{name}.pdf"
        make_pdf(d / rel, size, pages=pages, line_pt=line_pt)
        files.append({"path": rel, "kind": "pdf", "page_size": name, "pages": pages, "dpi": 300})
    previews = write_previews(d, d / files[0]["path"])
    write_manifest(d, base_manifest(item_id, files=files, previews=previews, **overrides))
    return d


def sticker_item(root: Path, item_id: str = "2026-10-15-stickers-happy-frogs-0001", n: int = 4) -> Path:
    d = root / item_id
    assets = []
    for i in range(1, n + 1):
        rel = f"assets/sticker-{i:02d}.png"
        sticker_png(d / rel)
        assets.append({"path": rel, "kind": "png_transparent"})
    zrel = "files/happy-frogs-stickers.zip"
    (d / "files").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(d / zrel, "w") as z:
        for a in assets:
            z.write(d / a["path"], Path(a["path"]).name)
    sheet = make_pdf(d / "files/happy-frogs-sheet-us_letter.pdf", LETTER, pages=1)
    files = [{"path": "files/happy-frogs-sheet-us_letter.pdf", "kind": "pdf", "page_size": "us_letter", "pages": 1,
              "dpi": 300}, {"path": zrel, "kind": "zip"}]
    write_manifest(d, base_manifest(item_id, "stickers", theme="happy frogs", keywords=["frog stickers"],
                                    prompts=["Cute frog sticker"], files=files,
                                    previews=write_previews(d, sheet), assets=assets))
    return d


def make_item(d: Path, settings: dict, **manifest) -> Item:
    """An Item for unit-testing single checks; check_paths fills `usable`."""
    manifest.setdefault("item_id", d.name)
    item = Item(d, base_manifest(**manifest), settings, section(settings, "qa", qa.DEFAULTS))
    check_paths(item)
    return item


def by_name(checks: list[dict], name: str) -> list[dict]:
    found = [c for c in checks if c["name"] == name]
    assert found, f"no check named {name}: {[c['name'] for c in checks]}"
    return found

