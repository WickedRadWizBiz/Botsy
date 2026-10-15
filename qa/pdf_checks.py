"""PDF checks: the file opens, page size and count match the manifest, no blank or solid
black pages, and embedded images are sharp enough to print."""
from __future__ import annotations

from pathlib import Path

from core.render import POINTS_PER_INCH, page_size_points, pdf_info
from qa.checks import WARNING, Item, page_list, result
from qa.lineart import ink_coverage, iter_pdf_pages


def check_pdf(item: Item, entry: dict) -> list[dict]:
    rel, path = entry["path"], item.path(entry)
    try:
        info = pdf_info(path)
    except Exception as e:  # pypdfium2 raises PdfiumError on damaged files
        return [result("pdf_opens", False, f"{rel}: {type(e).__name__}: {e}")]
    if not info["pages"]:
        return [result("pdf_opens", False, f"{rel}: no pages")]
    return [
        result("pdf_opens", True, f"{rel}: {info['pages']} pages"),
        _page_size(item, entry, info),
        _page_count(entry, info),
        _blank_pages(item, path, rel),
        _image_dpi(item, path, rel),
    ]


def _page_size(item: Item, entry: dict, info: dict) -> dict:
    rel, name = entry["path"], entry.get("page_size")
    try:
        want = page_size_points(name, item.settings)
    except (KeyError, TypeError):
        return result("pdf_page_size", False, f"{rel}: unknown page_size {name!r}")
    tol = float(item.cfg["page_size_tolerance_pt"])
    bad = [i for i, size in enumerate(info["sizes_pt"], start=1) if not _same_size(size, want, tol)]
    detail = f"{rel}: {name} {want[0]:.0f}x{want[1]:.0f}pt"
    if bad:
        w, h = info["sizes_pt"][bad[0] - 1]
        detail += f"; pages {page_list(bad)} differ by more than {tol:g}pt (page {bad[0]} is {w:.1f}x{h:.1f}pt)"
    return result("pdf_page_size", not bad, detail)


def _same_size(size: tuple[float, float], want: tuple[float, float], tol: float) -> bool:
    """Same paper size in either orientation (a landscape Letter page is still Letter)."""
    w, h = size
    return any(abs(w - a) <= tol and abs(h - b) <= tol for a, b in (want, want[::-1]))


def _page_count(entry: dict, info: dict) -> dict:
    declared = entry.get("pages")
    return result("pdf_page_count", declared == info["pages"],
                  f"{entry['path']}: {info['pages']} pages, manifest says {declared}")


def _blank_pages(item: Item, path: Path, rel: str) -> dict:
    """A blank page or a page that rendered solid black is a broken product page."""
    cfg = item.cfg
    blank, black = [], []
    for i, img in iter_pdf_pages(path, float(cfg["blank_check_dpi"])):  # one page in memory at a time
        ink, dark = ink_coverage(img)
        if ink < float(cfg["blank_min_ink"]):
            blank.append(i)
        elif dark > float(cfg["black_max_ink"]):
            black.append(i)
    problems = []
    if blank:
        problems.append(f"nearly blank pages: {page_list(blank)}")
    if black:
        problems.append(f"nearly all-black pages: {page_list(black)}")
    return result("pdf_blank_pages", not problems, f"{rel}: " + ("; ".join(problems) or "every page has content"))


def _image_dpi(item: Item, path: Path, rel: str) -> dict:
    """Effective resolution of raster images placed in the PDF (pixels / placed inches).
    Vector-only PDFs have none and pass. A warning, not an error: a low-res image may be a
    small decoration, so a person looks at it."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    need = float(item.settings["print"]["dpi"])
    floor = need * (1 - float(item.cfg["dpi_tolerance"]))
    low, count = [], 0
    doc = pdfium.PdfDocument(str(path))
    try:
        for i in range(len(doc)):
            for obj in doc[i].get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
                px = (obj.get_px_size if hasattr(obj, "get_px_size") else obj.get_size)()
                left, bottom, right, top = (obj.get_bounds if hasattr(obj, "get_bounds") else obj.get_pos)()
                inches = ((right - left) / POINTS_PER_INCH, (top - bottom) / POINTS_PER_INCH)
                if min(inches) < 0.05 or min(px) < 1:
                    continue
                count += 1
                # pair long side with long side so a rotated image is measured correctly
                dpi = min(max(px) / max(inches), min(px) / min(inches))
                if dpi < floor:
                    low.append((i + 1, dpi))
    finally:
        doc.close()
    if not count:
        return result("pdf_image_dpi", True, f"{rel}: vector only, no embedded images", WARNING)
    if low:
        page, dpi = min(low, key=lambda t: t[1])
        return result("pdf_image_dpi", False,
                      f"{rel}: {len(low)} of {count} images below {need:.0f} DPI (lowest {dpi:.0f} DPI on page {page})",
                      WARNING)
    return result("pdf_image_dpi", True, f"{rel}: {count} embedded images, all at least {need:.0f} DPI", WARNING)
