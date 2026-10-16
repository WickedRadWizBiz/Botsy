"""AI-assisted sticker packs: brief -> image model -> background removal -> die-cut PNGs.

One brief becomes one item with brief["page_count"] stickers (default 12), each a different
subject from the theme. output/<item_id>/ holds:
    files/<theme>-stickers-png.zip            the transparent PNG stickers (kind "zip"); split into
                                              ...-png-part-N.zip when one archive would pass
                                              stickers.zip_max_mb (Etsy's 20 MB per-file limit)
    files/<theme>-sticker-sheet-<size>.pdf    printable sheet(s), one PDF per page size; stickers
                                              flattened on white as JPEG to stay small
    assets/sticker-NN.png                     each transparent sticker, 300 dpi (png_transparent)
    previews/sheet-NN.png                     150 dpi render of each sheet page
    previews/sticker-NN.png                   the first few stickers on a light checkerboard
    manifest.json                             docs/CONTRACTS.md section 2, ai_assisted: true
Etsy allows 5 digital files per listing, 20 MB each. Model art is textured, so a sticker
PNG is often 1-3 MB and a 12-sticker ZIP can pass 20 MB: the PNGs are packed into as
many parts as needed, and the generator refuses up front a page-size list that leaves no
room for the ZIP. If the parts plus the PDFs still exceed 5 files, the item is marked
needs_review (QA then fails it on Etsy's file count).

Background removal follows settings vectorize.stickers.background_removal (rembg in real
runs). Dry runs and the FakeImageClient always use the deterministic flood fill, so tests
and the GitHub Action never download a model. Dry runs make at most
stickers.dry_run_max_stickers stickers. Like coloring pages, the item is built in a
scratch folder and swapped in only when complete. A sticker whose die-cut shape falls
apart into several pieces marks the item needs_review.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from PIL import Image

from core.config import load_settings, section
from core.items import slugify
from core.render import pdf_info, render_pdf_pages
from generators.imaging import cleanup
from generators.imaging.client import FakeImageClient, ImageClient
from generators.imaging.common import (
    base_manifest, build_dir, check_budget, check_file_count, commit, generate_batch, image_params,
    oversize_reasons, resolve_client, start_item,
)
from generators.imaging.pdf import sticker_sheet_pdf
from generators.imaging.prompts import plan_images
from generators.shared import file_name, page_sizes_for

__all__ = ["DEFAULTS", "NICHE", "generate"]

log = logging.getLogger(__name__)

NICHE = "stickers"

DEFAULTS: dict = {
    "default_count": 12,           # brief has no page_count (= sticker count)
    "max_count": 60,
    "dry_run_max_stickers": 6,     # dry runs only; null = no cap
    "spare_subjects": 3,           # planned extra subjects that replace refused or empty images
    "max_failures": 3,
    "sticker_px": 1500,            # long side of the artwork before the border (5 in at 300 dpi)
    "die_cut_border_px": 36,       # white border around the silhouette, at sticker_px scale
    "border_color": [255, 255, 255],
    "shadow": False,               # soft drop shadow behind the die-cut shape
    "shadow_offset_px": 8,
    "shadow_blur_px": 10,
    "shadow_opacity": 0.3,
    "floodfill_tolerance": 24,     # max per-channel distance from the background color
    "rembg_model": cleanup.REMBG_MODEL,   # not rembg's default: that model is non-commercial
    "min_island_px": 400,          # detached specks smaller than this are dropped
    "sheet": {"cols": 3, "rows": 4, "gap_in": 0.25},
    "preview_dpi": 150,
    "preview_stickers": 4,
    "preview_background": "checker",   # checker | white
    "preview_px": 800,
    "footer_text": "",
    "zip_max_mb": 19.0,            # split the PNG ZIP into parts below Etsy's 20 MB per-file limit
    "sheet_jpeg_quality": 92,      # sticker sheets: art flattened on white as JPEG; null = lossless PNG data
}


def generate(brief: dict, *, brief_file: str | Path | None = None, shop_id: str = "main",
             out_root: str | Path | None = None, date: str | None = None, dry_run: bool = False,
             client: ImageClient | None = None, settings: dict | None = None) -> list[Path]:
    """Build one sticker pack from one brief. Returns [item_dir]."""
    settings = settings or load_settings()
    cfg = section(settings, NICHE, DEFAULTS)
    vec = section(settings.get("vectorize", {}), NICHE, {"background_removal": "rembg"})
    img_cfg = section(settings, "imaging", {"fake_long_side_px": 1200})
    ctx = start_item(brief, NICHE, settings, shop_id=shop_id, out_root=out_root, date=date,
                     brief_file=brief_file, dry_run=dry_run)
    if ctx.skip:
        return [ctx.dir]

    asked = brief.get("page_count")
    requested = int(asked) if asked not in (None, "") else int(cfg["default_count"])
    if not 1 <= requested <= int(cfg["max_count"]):
        raise ValueError(f"sticker count {requested} outside 1..{cfg['max_count']}")
    n = min(requested, int(cfg["dry_run_max_stickers"])) if dry_run and cfg.get("dry_run_max_stickers") else requested
    planned, prompt_check = plan_images(brief, "sticker", n, ctx.blocklist, spares=int(cfg["spare_subjects"]))
    client = resolve_client(client, dry_run, settings, "sticker", int(img_cfg["fake_long_side_px"]))
    check_budget(client, n)
    aspect_ratio, image_size = image_params(settings, "sticker")
    method = "floodfill" if dry_run or isinstance(client, FakeImageClient) else vec["background_removal"]
    sizes = page_sizes_for(brief, settings)
    check_file_count(1 + len(sizes))                     # at least one ZIP plus one sheet PDF per page size
    slug = slugify(ctx.theme)
    sheet_names = {name: "files/" + file_name(slug, f"-sticker-sheet-{name}.pdf") for name in sizes}  # fail early
    dpi = int(settings["print"].get("dpi", 300))

    with build_dir(ctx) as build:
        def process(image: Image.Image, i: int) -> tuple[str, int]:
            sticker = make_sticker(image, cfg, method)
            rel = f"assets/sticker-{i:02d}.png"
            cleanup.save_png(sticker, build / rel, dpi=dpi)
            return rel, cleanup.count_pieces(sticker)

        results, skipped = generate_batch(client, planned, n, aspect_ratio=aspect_ratio, image_size=image_size,
                                          blocklist=ctx.blocklist, process=process,
                                          max_failures=int(cfg["max_failures"]), item_id=ctx.item_id)
        assets = [rel for _, (rel, _) in results]
        members = [(build / a, f"{slug}-sticker-{i:02d}.png") for i, a in enumerate(assets, 1)]
        parts = split_by_size(members, int(float(cfg["zip_max_mb"]) * 1e6))
        files = []
        for k, part in enumerate(parts, 1):
            suffix = "-stickers-png.zip" if len(parts) == 1 else f"-stickers-png-part-{k}.zip"
            zip_rel = "files/" + file_name(slug, suffix)
            write_zip(build / zip_rel, part)
            entry = {"path": zip_rel, "kind": "zip", "count": len(part), "dpi": dpi}
            if len(parts) > 1:
                entry.update(part=k, parts=len(parts))
            files.append(entry)

        sheet = cfg["sheet"]
        cols, rows = int(sheet["cols"]), int(sheet["rows"])
        stickers = [_load(build / a) for a in assets]
        title = f"{ctx.theme.title()} Stickers"
        sheets = []
        for name in sizes:
            rel = sheet_names[name]
            sticker_sheet_pdf(stickers, build / rel, name, settings, cols=cols, rows=rows,
                              gap_in=float(sheet["gap_in"]), title=title, footer_text=cfg["footer_text"],
                              jpeg_quality=cfg.get("sheet_jpeg_quality"))
            sheets.append({"path": rel, "kind": "pdf", "page_size": name, "pages": pdf_info(build / rel)["pages"],
                           "dpi": dpi})
        files += sheets

        manifest = base_manifest(ctx, brief, brief_file=brief_file, shop_id=shop_id, settings=settings,
                                 client=client, dry_run=dry_run, prompts=[p.prompt for p, _ in results],
                                 prompt_check=prompt_check)
        manifest["files"] = files
        manifest["previews"] = _previews(build, sheets[0], stickers, cfg, cols * rows)
        manifest["assets"] = [{"path": a, "kind": "png_transparent", "sticker": i} for i, a in enumerate(assets, 1)]
        reasons = manifest["review_reasons"]
        for i, (_, (_, pieces)) in enumerate(results, 1):
            if pieces > 1:
                reasons.append(f"sticker {i} is {pieces} separate die-cut pieces; check it cuts as one sticker")
        reasons += oversize_reasons(build, files)
        manifest["needs_review"] = bool(reasons)
        manifest["pack"] = {
            "requested_stickers": requested,
            "stickers": len(assets),
            "dry_run_capped": len(assets) < requested,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "background_removal": method,
            "rembg_model": cfg["rembg_model"] if method == "rembg" else None,
            "sheet_grid": [cols, rows],
            "zip_parts": len(parts),
            "contents": [{"sticker": i, "subject": plan.subject, "size_px": list(stickers[i - 1].size),
                          "pieces": pieces} for i, (plan, (_, pieces)) in enumerate(results, 1)],
            "skipped": skipped,
        }
        commit(build, ctx.dir, manifest)
    log.info("generated %s (%d stickers, %s)", ctx.item_id, len(assets), [f["path"] for f in files])
    return [ctx.dir]


def make_sticker(image: Image.Image, cfg: dict, method: str) -> Image.Image:
    """Raw model image -> transparent die-cut sticker (RGBA)."""
    rgba = cleanup.remove_background(image, method, rembg_model=cfg["rembg_model"],
                                     tolerance=int(cfg["floodfill_tolerance"]))
    rgba = cleanup.fit_long_side(cleanup.crop_to_content(rgba), int(cfg["sticker_px"]))
    rgba = cleanup.crop_to_content(cleanup.drop_islands(rgba, int(cfg["min_island_px"])))
    return cleanup.add_die_cut_border(rgba, int(cfg["die_cut_border_px"]), tuple(cfg["border_color"]),
                                      shadow=bool(cfg["shadow"]), shadow_offset_px=int(cfg["shadow_offset_px"]),
                                      shadow_blur_px=int(cfg["shadow_blur_px"]),
                                      shadow_opacity=float(cfg["shadow_opacity"]))


def split_by_size(members: list[tuple[Path, str]], max_bytes: int) -> list[list[tuple[Path, str]]]:
    """Group files, in order, into parts whose summed size stays under max_bytes. PNGs barely
    compress further, so the summed file size is a safe estimate of the ZIP size (the few
    hundred bytes of ZIP headers per member are covered by the margin below 20 MB)."""
    parts: list[list[tuple[Path, str]]] = []
    size = 0
    for member in members:
        b = member[0].stat().st_size + 200
        if not parts or (size + b > max_bytes and parts[-1]):
            parts.append([])
            size = 0
        parts[-1].append(member)
        size += b
    return parts


def _load(path: Path) -> Image.Image:
    img = Image.open(path)
    img.load()                    # read now: the build folder is gone once the item is committed
    return img


def write_zip(path: Path, members: list[tuple[Path, str]]) -> Path:
    """ZIP with fixed timestamps, so identical stickers give an identical archive. PNGs are
    already compressed, so members are stored: deflating them again only costs time, and
    stored members make the archive size exactly predictable for split_by_size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as z:
        for src, name in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, src.read_bytes())
    return path


def _previews(d: Path, sheet_pdf: dict, stickers: list[Image.Image], cfg: dict, per_sheet: int) -> list[dict]:
    """Sheet renders first (the natural hero image), then a few single stickers.
    Every entry has the sheet page it belongs to, so `page` is always an int."""
    out = []
    for i in range(sheet_pdf["pages"]):
        rel = f"previews/sheet-{i + 1:02d}.png"
        render_pdf_pages(d / sheet_pdf["path"], dpi=int(cfg["preview_dpi"]), pages=[i])[0].save(d / rel, "PNG")
        out.append({"path": rel, "page": i + 1})
    for i, img in enumerate(stickers[:int(cfg["preview_stickers"])], 1):
        rel = f"previews/sticker-{i:02d}.png"
        shown = cleanup.on_background(cleanup.fit_long_side(img.convert("RGBA"), int(cfg["preview_px"])),
                                      cfg["preview_background"])
        shown.save(d / rel, "PNG")
        out.append({"path": rel, "page": (i - 1) // per_sheet + 1, "sticker": i})
    return out
