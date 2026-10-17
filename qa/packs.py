"""Pack checks (docs/PACKS.md section 5) and the print rules of stationery and ttrpg pages.

Pack items carry manifest `pack: {size, source, set_id, page_ids, ...}`; an item made
before packs (no pack.size) gets none of the pack checks.

- pack_page_count: the designs the buyer gets (core.items.content_count) equal pack.size,
  and every PDF has pack.size pages plus the extra pages the niche declares (title_pages,
  answer_key_pages, extra_pages). Sticker sheets are laid out several to a page, so for
  stickers the ZIP counts and the transparent PNG assets are compared instead.
- pack_page_ids: page_ids are strings, one per page, all different.
- pack_duplicates: no two pages of an image pack look the same (perceptual hash of the
  cleaned page or sticker images; distance <= duplicate_distance fails, <= near_distance
  sends the item to review).
- pack_fresh_pages: a fresh pack shares no page_id with any other item under the same
  output root, and no other pack reuses a fresh pack's pages.
- print_font_size / print_line_spacing (stationery, ttrpg): the smallest printed text is at
  least min_font_pt (7 pt, read from the PDF's text: font size times the text matrix), and
  ruled writing lines are at least min_line_gap_mm (6 mm) apart. Spacing is estimated from
  a render: long, thin, light (gray) horizontal lines with the same left and right ends,
  three or more evenly spaced, are a block of writing lines (dark box borders and filled
  bars are not). The
  generator's own measurements in the pack fields (min_font_pt, min_line_gap_mm) are
  checked too.
"""
from __future__ import annotations

import ctypes
import math
from pathlib import Path

import numpy as np
from PIL import Image

from core.items import content_count, iter_item_dirs, read_manifest
from qa.checks import WARNING, Item, result

IMAGE_NICHES = ("coloring_pages", "stickers")
PRINT_RULE_NICHES = ("stationery", "ttrpg")
MM_PER_IN = 25.4


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _extra(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def pack_of(item: Item) -> dict:
    pack = item.manifest.get("pack")
    return pack if isinstance(pack, dict) else {}


# ------------------------------------------------------------------ pack checks

def check_pack(item: Item) -> list[dict]:
    """Every pack check for a pack item; [] for an item without pack.size."""
    pack = pack_of(item)
    size = _count(pack.get("size"))
    if not size:
        return []
    out = [_page_count(item, pack, size), _page_ids(pack, size)]
    if item.manifest.get("niche") in IMAGE_NICHES:
        out += _duplicates(item)
    out.append(_fresh_pages(item, pack))
    return out


def _page_count(item: Item, pack: dict, size: int) -> dict:
    m = item.manifest
    problems = []
    n = content_count(m)
    if n != size:
        problems.append(f"the pack holds {n} designs")
    if m.get("niche") == "stickers":
        zips = [e for e in m.get("files") or [] if isinstance(e, dict) and e.get("kind") == "zip"]
        in_zips = sum(_count(e.get("count")) for e in zips)
        if zips and in_zips != size:
            problems.append(f"the ZIP files hold {in_zips} PNG files")
        stickers = item.entries("assets", "png_transparent")
        if stickers and len(stickers) != size:
            problems.append(f"{len(stickers)} transparent sticker images are kept")
        want = f"{size} stickers"
    else:
        extra = sum(_extra(pack.get(k)) for k in ("title_pages", "answer_key_pages", "extra_pages"))
        want = f"{size} pages" + (f" + {extra} title/answer pages" if extra else "")
        for e in m.get("files") or []:
            if isinstance(e, dict) and e.get("kind") == "pdf" and e.get("pages") != size + extra:
                problems.append(f"{e.get('path')} has {e.get('pages')} pages")
    return result("pack_page_count", not problems,
                  f"pack.size {size}, expected {want}" + (f": {'; '.join(problems)}" if problems else ", as made"))


def _page_ids(pack: dict, size: int) -> dict:
    ids = pack.get("page_ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        return result("pack_page_ids", False, "pack.page_ids must be a list of page id strings")
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    problems = []
    if dupes:
        problems.append(f"repeated: {', '.join(dupes[:6])}")
    if len(ids) != size:
        problems.append(f"{len(ids)} page ids for pack.size {size}")
    return result("pack_page_ids", not problems,
                  "; ".join(problems) or f"{len(ids)} different pages")


# ---- perceptual hash

_DCT: dict[int, np.ndarray] = {}


def _dct_matrix(n: int) -> np.ndarray:
    if n not in _DCT:
        k = np.arange(n)[:, None]
        x = np.arange(n)[None, :]
        m = np.cos(np.pi * (2 * x + 1) * k / (2 * n)) * math.sqrt(2 / n)
        m[0] /= math.sqrt(2)
        _DCT[n] = m
    return _DCT[n]


def phash(img: Image.Image, size: int = 32, low: int = 8) -> int:
    """64-bit perceptual hash (DCT of a 32x32 grayscale, low 8x8 frequencies against their
    median). A transparent sticker is laid on white first."""
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        base = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(base, rgba)
    g = np.asarray(img.convert("L").resize((size, size), Image.LANCZOS), dtype=np.float64)
    c = _dct_matrix(size)
    coeffs = (c @ g @ c.T)[:low, :low].flatten()
    bits = coeffs > np.median(coeffs[1:])
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _page_images(item: Item) -> tuple[str, list[tuple[str, Path]]]:
    """(what, [(label, path)]): the pack's page images, one per page, for the duplicate check."""
    if item.manifest.get("niche") == "stickers":
        entries = item.entries("assets", "png_transparent") or item.entries("files", "png_transparent")
        return "stickers", [(f"sticker {e.get('sticker') or i}", item.path(e)) for i, e in enumerate(entries, 1)]
    entries = item.entries("assets", "png")
    if not entries:
        entries = item.entries("previews")
        return "previews", [(f"preview {e.get('page') or i}", item.path(e)) for i, e in enumerate(entries, 1)]
    return "pages", [(f"page {e.get('page') or i}", item.path(e)) for i, e in enumerate(entries, 1)]


def _duplicates(item: Item) -> list[dict]:
    cfg = item.cfg["packs"]
    dup, near = int(cfg["duplicate_distance"]), int(cfg["near_distance"])
    what, images = _page_images(item)
    hashes, unread = [], []
    for label, path in images:
        try:
            with Image.open(path) as im:
                im.load()
                hashes.append((label, phash(im)))
        except Exception:  # damaged: the image checks report it
            unread.append(label)
    same, close = [], []
    for i, (la, ha) in enumerate(hashes):
        for lb, hb in hashes[i + 1:]:
            d = hamming(ha, hb)
            if d <= dup:
                same.append(f"{la} and {lb} ({d})")
            elif d <= near:
                close.append(f"{la} and {lb} ({d})")
    scope = f"{len(hashes)} {what}" + (f" ({len(unread)} unreadable)" if unread else "")
    return [
        result("pack_duplicates", not same,
               f"duplicate pages in the pack: {', '.join(same[:6])}" if same
               else f"no duplicates among {scope} (perceptual hash, distance > {dup})"),
        result("pack_near_duplicates", not close,
               f"pages that look alike: {', '.join(close[:6])}; check they are different designs" if close
               else f"no near-duplicates among {scope} (distance > {near})", WARNING),
    ]


# ---- fresh packs

def _fresh_pages(item: Item, pack: dict) -> dict:
    """Fresh pages are made for one pack only: no other item under this output root may
    hold one of them (docs/PACKS.md section 3), in either direction."""
    mine = {i for i in pack.get("page_ids") or [] if isinstance(i, str)}
    fresh = pack.get("source") == "fresh"
    root = item.dir.parent
    clashes = []
    try:
        dirs = iter_item_dirs(root)
    except OSError:
        dirs = []
    for d in dirs:
        if d.name == item.dir.name:
            continue
        try:
            other = read_manifest(d).get("pack") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(other, dict) or not (fresh or other.get("source") == "fresh"):
            continue
        shared = mine & {i for i in other.get("page_ids") or [] if isinstance(i, str)}
        if shared:
            clashes.append(f"{d.name} ({len(shared)} pages, {other.get('source')})")
    what = "this fresh pack" if fresh else "fresh packs"
    return result("pack_fresh_pages", not clashes,
                  f"pages of {what} also in: {'; '.join(clashes[:5])}" if clashes
                  else f"no page shared with {'any other item' if fresh else 'a fresh pack'} under {root.name}/")


# ------------------------------------------------------------------ print rules (stationery, ttrpg)

def check_print_rules(item: Item) -> list[dict]:
    if item.manifest.get("niche") not in PRINT_RULE_NICHES:
        return []
    cfg = item.cfg["print_rules"]
    min_pt, min_mm = float(cfg["min_font_pt"]), float(cfg["min_line_gap_mm"])
    pack = pack_of(item)
    fonts, gaps, notes_f, notes_g = [], [], [], []
    for e in item.entries("files", "pdf"):
        rel = e["path"]
        try:
            low = min_font_size(item.path(e))
        except Exception as ex:  # damaged file: pdf_opens reports it
            notes_f.append(f"{rel}: unreadable ({type(ex).__name__})")
            low = None
        if low is not None:
            fonts.append((low[0], rel, low[1]))
        try:
            gap = min_line_gap_mm(item.path(e), dpi=float(cfg["dpi"]), dark=int(cfg["dark_level"]),
                                  min_run_mm=float(cfg["min_run_mm"]), max_pages=int(cfg["max_pages"]),
                                  light=int(cfg["light_level"]))
        except Exception as ex:
            notes_g.append(f"{rel}: not measured ({type(ex).__name__})")
            gap = None
        if gap is not None:
            gaps.append((gap[0], rel, gap[1]))
    declared_font = pack.get("min_font_pt")
    declared_gap = pack.get("min_line_gap_mm")
    tol_pt, tol_mm = float(cfg["font_tolerance_pt"]), float(cfg["line_gap_tolerance_mm"])

    problems = []
    if fonts:
        size, rel, page = min(fonts)
        if size < min_pt - tol_pt:
            problems.append(f"{rel} page {page} has {size:.1f} pt text")
        f_detail = f"smallest printed text {size:.1f} pt ({rel} page {page})"
    else:
        f_detail = "no printed text found"
    if isinstance(declared_font, (int, float)) and not isinstance(declared_font, bool) and declared_font < min_pt:
        problems.append(f"the generator measured {declared_font:g} pt text")
    font_check = result("print_font_size", not problems,
                        (f"text below {min_pt:g} pt: {'; '.join(problems)}" if problems
                         else f"{f_detail}, at least {min_pt:g} pt")
                        + (f"; {'; '.join(notes_f)}" if notes_f else ""))

    problems = []
    if gaps:
        gap, rel, page = min(gaps)
        if gap < min_mm - tol_mm:
            problems.append(f"{rel} page {page}: writing lines about {gap:.1f} mm apart")
        g_detail = f"closest writing lines about {gap:.1f} mm apart ({rel} page {page})"
    else:
        g_detail = "no block of writing lines found"
    if isinstance(declared_gap, (int, float)) and not isinstance(declared_gap, bool) and declared_gap < min_mm:
        problems.append(f"the generator measured lines {declared_gap:g} mm apart")
    gap_check = result("print_line_spacing", not problems,
                       (f"writing lines closer than {min_mm:g} mm: {'; '.join(problems)}" if problems
                        else f"{g_detail}, at least {min_mm:g} mm")
                       + (f"; {'; '.join(notes_g)}" if notes_g else ""))
    return [font_check, gap_check]


def min_font_size(path: str | Path) -> tuple[float, int] | None:
    """(smallest effective font size in points, its 1-based page) over every printed
    character, or None when the PDF has no text. Effective size = the font size times the
    vertical scale of the character's matrix, so text drawn scaled down counts as small."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    best: tuple[float, int] | None = None
    doc = pdfium.PdfDocument(str(path))
    try:
        for i in range(len(doc)):
            page = doc[i]
            tp = page.get_textpage()
            try:
                for k in range(raw.FPDFText_CountChars(tp.raw)):
                    code = raw.FPDFText_GetUnicode(tp.raw, k)
                    if code <= 32 or chr(code).isspace():
                        continue
                    size = float(raw.FPDFText_GetFontSize(tp.raw, k))
                    m = raw.FS_MATRIX()
                    if raw.FPDFText_GetMatrix(tp.raw, k, ctypes.byref(m)):
                        scale = math.hypot(m.c, m.d)
                        if scale > 0:
                            size *= scale
                    if size > 0 and (best is None or size < best[0]):
                        best = (size, i + 1)
            finally:
                tp.close()
                page.close()
    finally:
        doc.close()
    return best


def min_line_gap_mm(path: str | Path, *, dpi: float = 150, dark: int = 235, min_run_mm: float = 20,
                    max_pages: int = 60, light: int = 90) -> tuple[float, int] | None:
    """(smallest spacing in mm between ruled writing lines, its page), or None when no page
    has a block of writing lines (three or more evenly spaced long, thin, light lines with
    the same ends)."""
    from qa.lineart import iter_pdf_pages

    best: tuple[float, int] | None = None
    for page, img in iter_pdf_pages(path, dpi, max_pages=max_pages):
        gap = page_line_gap_px(np.asarray(img.convert("L")), dpi=dpi, dark=dark, min_run_mm=min_run_mm,
                               light=light)
        if gap is not None:
            mm = gap / dpi * MM_PER_IN
            if best is None or mm < best[0]:
                best = (mm, page)
    return best


def page_line_gap_px(gray: np.ndarray, *, dpi: float, dark: int = 235, min_run_mm: float = 20,
                     light: int = 90) -> float | None:
    """The smallest spacing in pixels within any block of writing lines on one page.

    Writing lines are drawn light (gray); box borders and rules are dark. A line counts as
    a writing line when the median ink of its darkest pixel row is lighter than `light`
    (0 = black), so the bottom row of a table, the box around it and the next box's border
    are never read as a block of lines."""
    ink = gray < dark
    h, w = ink.shape
    run = max(2, int(round(min_run_mm / MM_PER_IN * dpi)))
    if w <= run:
        return None
    cs = np.pad(np.cumsum(ink, axis=1, dtype=np.int32), ((0, 0), (1, 0)))
    full = (cs[:, run:] - cs[:, :-run]) == run           # a run of `run` dark pixels starts here
    rows = np.flatnonzero(full.any(axis=1))
    if rows.size < 3:
        return None
    x0 = full.argmax(axis=1)
    x1 = full.shape[1] - 1 - full[:, ::-1].argmax(axis=1) + run
    thick = max(1, int(round(1.0 / MM_PER_IN * dpi)))   # a writing line is at most 1 mm thick
    bands = []                                           # (y center, x0, x1)
    start = prev = rows[0]
    for r in [*rows[1:], -10]:
        if r == prev + 1:
            prev = r
            continue
        if prev - start + 1 <= thick:
            seg = slice(start, prev + 1)
            a, b = int(x0[seg].min()), int(x1[seg].max())
            strip = gray[seg, a:b]
            tone = min(float(np.median(row[row < dark])) for row in strip if (row < dark).any())
            if tone >= light:
                bands.append(((start + prev) / 2, a, b))
        start = prev = r
    snap = max(2, int(round(1.5 / MM_PER_IN * dpi)))    # same ends within 1.5 mm
    groups: list[tuple[int, int, list[float]]] = []
    for y, a, b in bands:
        for ga, gb, ys in groups:
            if abs(ga - a) <= snap and abs(gb - b) <= snap:
                ys.append(y)
                break
        else:
            groups.append((a, b, [y]))
    tol = max(1.5, dpi / MM_PER_IN * 0.3)                # evenly spaced within 0.3 mm
    best = None
    for _, _, ys in groups:
        ys.sort()
        gaps = np.diff(ys)
        for i in range(len(gaps) - 1):                   # two equal gaps in a row: three lines
            if abs(gaps[i] - gaps[i + 1]) <= tol:
                g = float(min(gaps[i], gaps[i + 1]))
                best = g if best is None else min(best, g)
    return best


__all__ = ["check_pack", "check_print_rules", "hamming", "min_font_size", "min_line_gap_mm",
           "page_line_gap_px", "phash"]
