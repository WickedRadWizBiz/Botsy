"""Checks for PNG, ZIP and SVG files plus the previews and assets kept for QA.

Print resolution is pixels divided by the intended print size. The size comes from the
manifest entry (`print_size_in: [w, h]`, `width_in`/`height_in`, or a `page_size` key),
otherwise from the PNG's DPI metadata, which is what buyers' print dialogs use.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from qa.checks import WARNING, Item, result, unsafe_reason

ALPHA_MODES = ("RGBA", "LA", "PA")


def open_image(path: Path | io.BytesIO) -> Image.Image:
    img = Image.open(path)
    img.load()  # decode now, so a truncated file fails here and not later in mockups
    return img


def has_alpha(img: Image.Image) -> bool:
    return img.mode in ALPHA_MODES or "transparency" in img.info


def intended_size_in(entry: dict, settings: dict) -> tuple[float, float] | None:
    """Print size in inches from the manifest entry, or None when it declares none (or
    declares nonsense such as zero or text, which then falls back to the DPI metadata)."""
    page = entry.get("page_size")
    known = settings["print"]["page_sizes"].get(page) if isinstance(page, str) else None
    page_in = (known.get("width_in"), known.get("height_in")) if isinstance(known, dict) else None
    for size in (entry.get("print_size_in"), (entry.get("width_in"), entry.get("height_in")), page_in):
        if isinstance(size, (list, tuple)) and len(size) == 2 and all(_positive(v) for v in size):
            return float(size[0]), float(size[1])
    return None


def _positive(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 1000


def dpi_metadata(img: Image.Image) -> float | None:
    """Smaller of the two DPI values Pillow reads from pHYs, or None when absent."""
    dpi = img.info.get("dpi")
    if not dpi:
        return None
    try:
        value = min(float(dpi[0]), float(dpi[1]))
    except (TypeError, ValueError, IndexError):
        return None
    return value if value > 1 else None  # pHYs with "unknown unit" reads as ~0.0254


def check_png_file(item: Item, entry: dict) -> list[dict]:
    rel = entry["path"]
    try:
        img = open_image(item.path(entry))
    except Exception as e:
        return [result("png_readable", False, f"{rel}: {type(e).__name__}: {e}")]
    out = [result("png_readable", True, f"{rel}: {img.width}x{img.height} {img.mode}"),
           _resolution(item, entry, img), _dpi_metadata(item, entry, img)]
    if entry.get("kind") == "png_transparent":
        out.append(result("png_transparency", *transparency(item, img, rel)))
    return out


def _resolution(item: Item, entry: dict, img: Image.Image) -> dict:
    rel = entry["path"]
    need = float(item.settings["print"]["dpi"])
    floor = need * (1 - float(item.cfg["dpi_tolerance"]))
    size = intended_size_in(entry, item.settings)
    if size:
        px = sorted(img.size)
        inches = sorted(size)
        dpi = min(px[0] / inches[0], px[1] / inches[1])
        return result("png_resolution", dpi >= floor,
                      f"{rel}: {img.width}x{img.height}px for {size[0]:g}x{size[1]:g}in = {dpi:.0f} DPI (need {need:.0f})")
    meta = dpi_metadata(img)
    if meta:
        return result("png_resolution", meta >= floor,
                      f"{rel}: {meta:.0f} DPI from metadata, prints at {img.width / meta:.2f}x{img.height / meta:.2f}in "
                      f"(need {need:.0f})")
    return result("png_resolution", False,
                  f"{rel}: no print size in the manifest and no DPI metadata; cannot confirm {need:.0f} DPI", WARNING)


def _dpi_metadata(item: Item, entry: dict, img: Image.Image) -> dict:
    """Missing or low DPI metadata makes apps print the PNG at the wrong size (often 72 DPI)."""
    rel, need = entry["path"], float(item.settings["print"]["dpi"])
    meta = dpi_metadata(img)
    if meta is None:
        return result("png_dpi_metadata", False, f"{rel}: no DPI metadata (save with dpi=({need:.0f}, {need:.0f}))", WARNING)
    ok = meta >= need * (1 - float(item.cfg["dpi_tolerance"]))
    return result("png_dpi_metadata", ok, f"{rel}: metadata says {meta:.0f} DPI", WARNING)


def transparency(item: Item, img: Image.Image, rel: str) -> tuple[bool, str]:
    """A cut-out sticker needs an alpha channel, a fully transparent border (no leftover
    background, nothing clipped at the edge) and a visible subject."""
    if not has_alpha(img):
        return False, f"{rel}: no alpha channel ({img.mode})"
    a = np.asarray(img.convert("RGBA"))[..., 3]
    ring = 2 if min(a.shape) >= 20 else 1  # the outermost pixels; tight crops are fine
    border = np.concatenate([a[:ring].ravel(), a[-ring:].ravel(), a[:, :ring].ravel(), a[:, -ring:].ravel()])
    clear = float((border <= 8).mean())
    subject = float((a >= 128).mean())
    problems = []
    if clear < float(item.cfg["transparent_border_min"]):
        problems.append(f"only {clear:.0%} of edge pixels are transparent (background left in, or subject cut off)")
    if subject < float(item.cfg["subject_min_fraction"]):
        problems.append(f"subject covers only {subject:.1%} of the image")
    detail = "; ".join(problems) or f"transparent edges, subject covers {subject:.0%}"
    return not problems, f"{rel}: {detail}"


MAX_MEMBER_BYTES = 200_000_000  # never inflate more than this per member (Etsy caps the zip at 20 MB)


def check_zip(item: Item, entry: dict) -> list[dict]:
    """The archive opens, every member's CRC is right, member paths are safe, the file count
    matches the manifest's `count`, and the PNGs inside (what a sticker buyer actually gets)
    decode, carry DPI metadata and, for stickers, have clean transparent edges."""
    rel = entry["path"]
    try:
        with zipfile.ZipFile(item.path(entry)) as z:
            bad_member = z.testzip()
            members = [i for i in z.infolist() if not i.is_dir()]
            pngs = _zip_pngs(item, z, members) if not bad_member else None
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as e:
        return [result("zip_valid", False, f"{rel}: {type(e).__name__}: {e}")]
    names = [i.filename for i in members]
    unsafe = [n for n in names if unsafe_reason(n)]
    problems = []
    if bad_member:
        problems.append(f"corrupt member {bad_member}")
    if not names:
        problems.append("empty archive")
    if unsafe:
        problems.append(f"unsafe member paths: {', '.join(unsafe[:5])}")
    count = entry.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count != len(names):
        problems.append(f"{len(names)} files, manifest count says {count}")
    out = [result("zip_valid", not problems, f"{rel}: " + ("; ".join(problems) or f"{len(names)} files"))]
    if pngs:
        broken, low_dpi, total = pngs
        ok_detail = f"{total} PNGs decode" + (" with clean transparent edges" if _is_stickers(item) else "")
        out.append(result("zip_png", not broken, f"{rel}: " + ("; ".join(broken[:5]) or ok_detail)))
        out.append(result("zip_png_dpi", not low_dpi,
                          f"{rel}: no or low DPI metadata: {', '.join(low_dpi[:5])}" if low_dpi
                          else f"{rel}: every PNG says at least {item.settings['print']['dpi']} DPI", WARNING))
    return out


def _zip_pngs(item: Item, z: zipfile.ZipFile, members: list) -> tuple[list[str], list[str], int] | None:
    """(problems, PNGs without enough DPI metadata, PNG count), or None if there are none."""
    pngs = [i for i in members if i.filename.lower().endswith(".png")]
    if not pngs:
        return None
    need = float(item.settings["print"]["dpi"]) * (1 - float(item.cfg["dpi_tolerance"]))
    stickers = _is_stickers(item)
    broken, low_dpi = [], []
    for info in pngs:
        name = info.filename
        if info.file_size > MAX_MEMBER_BYTES:
            broken.append(f"{name} inflates to {info.file_size / 1e6:.0f} MB")
            continue
        try:
            img = open_image(io.BytesIO(z.read(info)))
        except Exception as e:
            broken.append(f"{name}: {type(e).__name__}")
            continue
        meta = dpi_metadata(img)
        if meta is None or meta < need:
            low_dpi.append(name)
        if stickers:
            ok, detail = transparency(item, img, name)
            if not ok:
                broken.append(detail)
    return broken, low_dpi, len(pngs)


def _is_stickers(item: Item) -> bool:
    """Sticker PNGs are die-cut, so they must have transparent edges."""
    return item.manifest.get("niche") == "stickers"


def check_svg(item: Item, entry: dict, name: str = "svg_valid") -> dict:
    rel = entry["path"]
    try:
        root = ET.parse(item.path(entry)).getroot()
    except (ET.ParseError, OSError) as e:
        return result(name, False, f"{rel}: {type(e).__name__}: {e}")
    ok = root.tag.rsplit("}", 1)[-1] == "svg"
    return result(name, ok, f"{rel}: " + ("valid SVG" if ok else f"root element is <{root.tag}>"))


def check_previews(item: Item) -> list[dict]:
    """Previews feed the mockups; an item without any falls back to rendering its PDF."""
    entries = item.entries("previews")
    broken = []
    for e in entries:
        try:
            open_image(item.path(e))
        except Exception as ex:
            broken.append(f"{e['path']} ({type(ex).__name__})")
    if not entries:
        return [result("previews_readable", True, "no previews; mockups use the PDF pages", WARNING)]
    return [result("previews_readable", not broken,
                   f"unreadable: {', '.join(broken)}" if broken else f"{len(entries)} previews readable")]


def check_assets(item: Item) -> list[dict]:
    """Assets are intermediates kept for QA: transparent sticker PNGs and SVGs. Summarized
    in one or two checks so a 40-sticker pack does not produce 80 lines."""
    broken, opaque, checked_png = [], [], 0
    for e in item.entries("assets"):
        kind, rel = e.get("kind"), e["path"]
        if kind == "svg":
            r = check_svg(item, e)
            if not r["passed"]:
                broken.append(r["detail"])
            continue
        if kind not in ("png", "png_transparent"):
            continue
        try:
            img = open_image(item.path(e))
        except Exception as ex:
            broken.append(f"{rel}: {type(ex).__name__}")
            continue
        if kind == "png_transparent":
            checked_png += 1
            ok, detail = transparency(item, img, rel)
            if not ok:
                opaque.append(detail)
    out = [result("assets_readable", not broken, "; ".join(broken[:5]) or "all assets readable")]
    if checked_png:
        out.append(result("asset_transparency", not opaque,
                          "; ".join(opaque[:5]) or f"{checked_png} transparent PNGs have clean edges"))
    return out
