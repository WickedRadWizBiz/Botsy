"""AI-assisted sticker packs: image model -> background removal -> die-cut PNGs, as themed packs.

A pack-engine plugin (generators/packs, docs/PACKS.md section 3): one brief becomes one item
per size in brief["pack_sizes"] (else [page_count], default stickers.default_count), each
sticker a different subject of the theme. A design set's page is one transparent die-cut
sticker PNG (300 dpi) with a quality score (a sticker that falls apart into several pieces
scores low, so derived packs pick it last). Each item holds:
    files/<theme>-stickers-<size>-png.zip       the pack's transparent PNG stickers (kind "zip"); split into
                                                ...-png-part-N.zip when one archive would pass
                                                stickers.zip_max_mb (Etsy's 20 MB per-file limit)
    files/<theme>-sticker-sheet-<size>-<page size>.pdf   printable sheet(s), one PDF per page size;
                                                stickers flattened on white as JPEG to stay small
    assets/sticker-NN.png                       each transparent sticker (png_transparent)
    previews/sheet-NN.png, sticker-NN.png       sheet renders, then a few single stickers
    manifest.json                               docs/CONTRACTS.md section 2, ai_assisted: true
Etsy allows 5 digital files per listing, 20 MB each: a page-size list that leaves no room for
the ZIP is refused before anything is paid for, and ZIP parts plus PDFs past 5 files mark the
item needs_review (QA then fails it on Etsy's file count).

Background removal follows settings vectorize.stickers.background_removal (rembg in real runs).
Dry runs and the FakeImageClient always use the deterministic flood fill, so tests and the
GitHub Action never download a model. Dry runs make every sticker of the pack, at a small size.
"""
from __future__ import annotations

import logging
import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from core.config import load_settings, section
from core.render import pdf_info, render_pdf_pages
from generators.imaging import cleanup
from generators.imaging.client import FakeImageClient, ImageClient
from generators.imaging.common import (
    by_plan, check_budget, copy_asset, fake_long_side, generate_batch, image_params, model_name, planned_images,
    resolve_client, style_review_reasons,
)
from generators.imaging.pdf import sheet_cell_px, sticker_sheet_pdf
from generators.imaging.prompts import sticker_prompt
from generators.imaging.subjects import subject_pool
from generators.shared import file_name, page_sizes_for

__all__ = ["DEFAULTS", "NICHE", "PAGE_TYPES", "generate", "make_sticker", "page_sizes_for", "plan_pages", "preview",
           "render_pages", "split_by_size", "write_zip"]

log = logging.getLogger(__name__)

NICHE = "stickers"
PAGE_TYPES = ("sticker",)
AI_ASSISTED = True
USES_SUBJECTS = True
FILE_LABEL = "stickers"

DEFAULTS: dict = {
    "default_count": 12,           # brief has no pack_sizes / page_count (= sticker count)
    "max_count": 60,
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


def _cfg(settings: dict) -> dict:
    return section(settings, NICHE, DEFAULTS)


def generate(brief: dict, *, brief_file: str | Path | None = None, shop_id: str = "main",
             out_root: str | Path | None = None, date: str | None = None, dry_run: bool = False,
             client: ImageClient | None = None, settings: dict | None = None, **kw) -> list[Path]:
    """Build every sticker pack of one brief. Returns one item dir per pack (master first)."""
    from generators.packs import generate_packs

    return generate_packs(brief, plugin=sys.modules[__name__], brief_file=brief_file, shop_id=shop_id,
                          out_root=out_root, date=date, dry_run=dry_run, client=client,
                          settings=settings or load_settings(), **kw)


# --- plugin hooks ----------------------------------------------------------------------------

def default_pack_size(settings: dict) -> int:
    return int(_cfg(settings)["default_count"])


def max_pack_size(settings: dict) -> int:
    return int(_cfg(settings)["max_count"])


def spare_pages(settings: dict) -> int:
    return int(_cfg(settings)["spare_subjects"])


def files_per_pack(page_sizes: list[str]) -> int:
    return 1 + len(page_sizes)                           # at least one ZIP plus one sheet PDF per page size


def brief_review_reasons(brief: dict, settings: dict) -> list[str]:
    return style_review_reasons(brief)


def resolve_image_client(client, *, dry_run: bool, settings: dict):
    return resolve_client(client, dry_run, settings, "sticker", fake_long_side(settings))


def plan_pages(ctx: dict, n: int, *, seed: str, settings: dict) -> list[dict]:
    """n stickers, one subject each (ctx["subjects"], else the built-in table), with the
    prompt that draws it. Deterministic for (ctx, n)."""
    subjects = [s for s in ctx.get("subjects") or [] if isinstance(s, str) and s.strip()]
    if not subjects:
        subjects = subject_pool(str(ctx.get("theme") or ""), list(ctx.get("keywords") or []))
    if len(subjects) < n:
        raise ValueError(f"theme {ctx.get('theme')!r} has only {len(subjects)} distinct subjects; {n} were asked for")
    brief = {"theme": ctx.get("theme"), "audience": ctx.get("audience"), "style_notes": ctx.get("style")}
    return [{"page_id": f"s{i:02d}", "type": "sticker", "subject": s,
             "spec": {"prompt": sticker_prompt(brief, s)}} for i, s in enumerate(subjects[:n], 1)]


def scaled_for_fake_art(cfg: dict, long_side_px: int) -> dict:
    """Dry runs: fake art is upscaled only as far as a sheet cell needs at print dpi, not to
    sticker_px (that only costs time); the border, shadow and speck sizes shrink with it."""
    px = int(cfg["sticker_px"])
    if long_side_px <= 0 or long_side_px >= px:
        return cfg
    f = long_side_px / px
    out = dict(cfg, sticker_px=long_side_px)
    out["die_cut_border_px"] = max(2, round(int(cfg["die_cut_border_px"]) * f))
    out["min_island_px"] = max(1, round(int(cfg["min_island_px"]) * f * f))
    out["shadow_offset_px"] = max(1, round(int(cfg["shadow_offset_px"]) * f))
    out["shadow_blur_px"] = max(1, round(int(cfg["shadow_blur_px"]) * f))
    return out


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


def build_pages(candidates: list[dict], n: int, build: Path, *, settings: dict, ctx: dict, client, blocklist,
                item_id: str | None = None, dry_run: bool = False) -> dict:
    """Generate n die-cut stickers into the design set's pages/ folder (spare subjects replace
    refused or empty images). The image cap is checked before the first call."""
    cfg = _cfg(settings)
    vec = section(settings.get("vectorize", {}), NICHE, {"background_removal": "rembg"})
    check_budget(client, n)
    aspect_ratio, image_size = image_params(settings, "sticker")
    fake = dry_run or isinstance(client, FakeImageClient)
    method = "floodfill" if fake else vec["background_removal"]
    if fake:
        sheet = cfg["sheet"]
        need = max(sheet_cell_px(s, settings, cols=int(sheet["cols"]), rows=int(sheet["rows"]),
                                 gap_in=float(sheet["gap_in"]), footer_text=cfg["footer_text"])
                   for s in list(ctx.get("page_sizes") or settings["print"]["page_sizes"]))
        cfg = scaled_for_fake_art(cfg, max(need, int(getattr(client, "long_side_px", 0) or fake_long_side(settings))))
    dpi = int(settings["print"].get("dpi", 300))
    planned = planned_images(candidates)
    lookup = by_plan(candidates, planned)

    def process(image: Image.Image, i: int) -> dict:
        sticker = make_sticker(image, cfg, method)
        rel = f"pages/sticker-{i:02d}.png"
        cleanup.save_png(sticker, build / rel, dpi=dpi)
        return {"png": rel, "pieces": cleanup.count_pieces(sticker), "size_px": list(sticker.size)}

    results, skipped = generate_batch(client, planned, n, aspect_ratio=aspect_ratio, image_size=image_size,
                                      blocklist=blocklist, process=process, max_failures=int(cfg["max_failures"]),
                                      item_id=item_id)
    pages = []
    for plan, r in results:
        page = lookup[id(plan)]
        spec = dict(page.get("spec") or {}, size_px=r["size_px"], pieces=r["pieces"], background_removal=method,
                    rembg_model=cfg["rembg_model"] if method == "rembg" else None, aspect_ratio=aspect_ratio,
                    image_size=image_size)
        pages.append(dict(page, spec=spec, asset={"png": r["png"]}, quality=1.0 if r["pieces"] <= 1 else 0.4))
    return {"pages": pages, "skipped": skipped, "model": model_name(client)}


def _load(path: str | Path) -> Image.Image:
    img = Image.open(path)
    img.load()                    # read now: the file may move once the item is committed
    return img


def _title(ctx: dict) -> str:
    theme = " ".join(w[:1].upper() + w[1:] for w in str(ctx.get("theme") or "").split())   # not str.title(): "Cat'S"
    return f"{theme} Stickers"


def _sheet(pages: list[dict], out_pdf: Path, page_size: str, settings: dict, ctx: dict) -> int:
    cfg = _cfg(settings)
    sheet = cfg["sheet"]
    stickers = [_load(p["asset_files"]["png"]) for p in pages]
    sticker_sheet_pdf(stickers, out_pdf, page_size, settings, cols=int(sheet["cols"]), rows=int(sheet["rows"]),
                      gap_in=float(sheet["gap_in"]), title=_title(ctx), footer_text=cfg["footer_text"],
                      jpeg_quality=cfg.get("sheet_jpeg_quality"))
    return pdf_info(out_pdf)["pages"]


def render_pages(pages: list[dict], out_pdf: Path, *, page_size: str, settings: dict, ctx: dict) -> int:
    """The pack's printable sticker sheet(s) for one page size. Returns the page count."""
    return _sheet(pages, Path(out_pdf), page_size, settings, ctx)


def preview(pages: list[dict], out_dir: Path, *, settings: dict, ctx: dict, limit: int = 6) -> list[Path]:
    """Single stickers on a light checkerboard (up to `limit`)."""
    cfg = _cfg(settings)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, p in enumerate(pages[:limit], 1):
        img = _load(p["asset_files"]["png"]).convert("RGBA")
        shown = cleanup.on_background(cleanup.fit_long_side(img, int(cfg["preview_px"])), cfg["preview_background"])
        path = out_dir / f"sticker-{i:02d}.png"
        shown.convert("RGB").save(path, "PNG")
        out.append(path)
    return out


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


def write_zip(path: Path, members: list[tuple[Path, str]]) -> Path:
    """ZIP with fixed timestamps, so identical stickers give an identical archive. PNGs are
    already compressed, so members are stored: deflating them again only costs time, and
    stored members make the archive size exactly predictable for split_by_size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as z:
        for src, name in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, Path(src).read_bytes())
    return path


def write_files(pages: list[dict], build: Path, *, slug: str, sizes: list[str], settings: dict,
                ctx: dict) -> list[dict]:
    """The PNG ZIP (in parts below the per-file limit) and one sheet PDF per page size."""
    cfg = _cfg(settings)
    size = int((ctx.get("pack") or {}).get("size") or len(pages))
    dpi = int(settings["print"].get("dpi", 300))
    sheet_names = {name: "files/" + file_name(slug, f"-sticker-sheet-{size}-{name}.pdf") for name in sizes}
    members = [(Path(p["asset_files"]["png"]), f"{slug}-sticker-{i:02d}.png") for i, p in enumerate(pages, 1)]
    parts = split_by_size(members, int(float(cfg["zip_max_mb"]) * 1e6))
    files = []
    for k, part in enumerate(parts, 1):
        suffix = f"-stickers-{size}-png.zip" if len(parts) == 1 else f"-stickers-{size}-png-part-{k}.zip"
        rel = "files/" + file_name(slug, suffix)
        write_zip(build / rel, part)
        entry = {"path": rel, "kind": "zip", "count": len(part), "dpi": dpi}
        if len(parts) > 1:
            entry.update(part=k, parts=len(parts))
        files.append(entry)
    for name in sizes:
        rel = sheet_names[name]
        pages_n = _sheet(pages, build / rel, name, settings, ctx)
        files.append({"path": rel, "kind": "pdf", "page_size": name, "pages": pages_n, "dpi": dpi})
    return files


def item_assets(pages: list[dict], build: Path) -> list[dict]:
    out = []
    for i, p in enumerate(pages, 1):
        rel = f"assets/sticker-{i:02d}.png"
        copy_asset(p["asset_files"]["png"], build / rel)
        out.append({"path": rel, "kind": "png_transparent", "sticker": i})
    return out


def manifest_previews(build: Path, files: list[dict], pages: list[dict], *, settings: dict, ctx: dict) -> list[dict]:
    """Sheet renders first (the natural hero image), then a few single stickers. Every
    entry has the sheet page it belongs to, so `page` is always an int."""
    cfg = _cfg(settings)
    sheet_pdf = next(f for f in files if f.get("kind") == "pdf")
    per_sheet = int(cfg["sheet"]["cols"]) * int(cfg["sheet"]["rows"])
    out = []
    for i, img in enumerate(render_pdf_pages(build / sheet_pdf["path"], dpi=int(cfg["preview_dpi"]),
                                             pages=list(range(sheet_pdf["pages"]))), 1):
        rel = f"previews/sheet-{i:02d}.png"
        img.convert("RGB").save(build / rel, "PNG")
        out.append({"path": rel, "page": i})
    with tempfile.TemporaryDirectory() as tmp:
        for i, p in enumerate(preview(pages, Path(tmp), settings=settings, ctx=ctx,
                                      limit=int(cfg["preview_stickers"])), 1):
            rel = f"previews/sticker-{i:02d}.png"
            copy_asset(p, build / rel)
            out.append({"path": rel, "page": (i - 1) // per_sheet + 1, "sticker": i})
    return out


def printed_text(pages: list[dict], ctx: dict) -> list[str]:
    """Text the files carry: the sheet PDFs' title (the stickers themselves hold no text)."""
    return [_title(ctx)]


def review_reasons(pages: list[dict], *, settings: dict) -> list[str]:
    out = []
    for i, p in enumerate(pages, 1):
        pieces = (p.get("spec") or {}).get("pieces")
        if isinstance(pieces, int) and pieces > 1:
            out.append(f"sticker {i} is {pieces} separate die-cut pieces; check it cuts as one sticker")
    return out


def pack_fields(pages: list[dict], files: list[dict], *, settings: dict, ctx: dict, set_doc: dict) -> dict:
    cfg = _cfg(settings)
    pack = ctx.get("pack") or {}
    own = pack.get("source") in ("master", "fresh")
    specs = [p.get("spec") or {} for p in pages]
    first = specs[0] if specs else {}
    method = first.get("background_removal")
    return {
        "requested_stickers": int(pack.get("size") or len(pages)),
        "stickers": len(pages),
        "dry_run_capped": False,
        "aspect_ratio": first.get("aspect_ratio"),
        "image_size": first.get("image_size"),
        "background_removal": method,
        "rembg_model": first.get("rembg_model") if method == "rembg" else None,
        "sheet_grid": [int(cfg["sheet"]["cols"]), int(cfg["sheet"]["rows"])],
        "zip_parts": sum(1 for f in files if f.get("kind") == "zip"),
        "contents": [{"sticker": i, "subject": p.get("subject"), "size_px": s.get("size_px"),
                      "pieces": s.get("pieces")} for i, (p, s) in enumerate(zip(pages, specs), 1)],
        "skipped": list(set_doc.get("skipped") or []) if own else [],
    }
