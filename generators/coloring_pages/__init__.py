"""AI-assisted coloring pages: brief -> image model -> line-art cleanup -> SVG -> PDF.

One brief becomes one item with brief["page_count"] pages, each a different subject from
the theme (generators/imaging/subjects.py). output/<item_id>/ holds:
    files/<theme>-coloring-pages-<size>.pdf   one per page size (Letter + A4 when
                                              print.deliver_both_sizes), one art per page
    assets/page-NN.png, page-NN.svg           cleaned black-and-white raster and its trace
    previews/page-NN.png                      150 dpi RGB render of every page
    manifest.json                             docs/CONTRACTS.md section 2, ai_assisted: true

Dry runs use the offline FakeImageClient and, for speed, at most
coloring_pages.dry_run_max_pages pages (manifest pack.requested_pages keeps the brief's
number). Lines are thickened so that, scaled onto the smallest page, none prints thinner
than print.min_line_weight_pt.

Everything is built in a scratch folder next to the item and swapped in only once every
file is written, so a rerun that fails (network, image cap, refusals) leaves the previous
item untouched; a rerun that succeeds drops the old QA report, mockups and listing. An item
that already has a real Etsy draft (publish.json) is never touched, and a dry run never
replaces a real item (generators/shared.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from core.config import load_settings, section
from core.items import slugify
from core.render import pdf_info, render_pdf_pages
from generators.imaging import cleanup
from generators.imaging.client import ImageClient
from generators.imaging.common import (
    base_manifest, build_dir, check_budget, check_file_count, commit, generate_batch, image_params,
    oversize_reasons, resolve_client, start_item,
)
from generators.imaging.pdf import coloring_pdf, load_svg, printable_box
from generators.imaging.prompts import plan_images
from generators.shared import file_name, page_sizes_for

__all__ = ["DEFAULTS", "NICHE", "generate"]

log = logging.getLogger(__name__)

NICHE = "coloring_pages"

DEFAULTS: dict = {
    "default_pages": 20,          # brief has no page_count and niches.coloring_pages.pages_per_pack is unset
    "max_pages": 50,
    "dry_run_max_pages": 6,       # dry runs only; null = no cap
    "spare_subjects": 3,          # planned extra subjects that replace refused or blank images
    "max_failures": 3,            # more refused/blank images than this fails the item
    "pad_frac": 0.03,             # white margin kept around the art when cropping
    "max_ink_fraction": 0.45,     # more black than this looks like a filled silhouette -> needs_review
    "vector_in_pdf": True,        # place the SVG trace; false places the cleaned raster
    "preview_dpi": 150,
    "footer_text": "",            # optional small line at the bottom of every page
}

PRE_DEFAULTS = {"grayscale": True, "threshold": 180, "despeckle_px": 20, "min_line_px": 4}


@dataclass
class PageResult:
    png: str
    svg: str
    tracer: str
    ink_fraction: float
    size: tuple[int, int]
    min_line_px: int


def generate(brief: dict, *, brief_file: str | Path | None = None, shop_id: str = "main",
             out_root: str | Path | None = None, date: str | None = None, dry_run: bool = False,
             client: ImageClient | None = None, settings: dict | None = None) -> list[Path]:
    """Build one coloring-page pack from one brief. Returns [item_dir]."""
    settings = settings or load_settings()
    cfg = section(settings, NICHE, DEFAULTS)
    vec = section(settings.get("vectorize", {}), NICHE, {"preprocess": PRE_DEFAULTS, "tracer": "potrace"})
    img_cfg = section(settings, "imaging", {"fake_long_side_px": 1200})
    ctx = start_item(brief, NICHE, settings, shop_id=shop_id, out_root=out_root, date=date,
                     brief_file=brief_file, dry_run=dry_run)
    if ctx.skip:
        return [ctx.dir]

    requested = _page_count(brief, settings, cfg)
    n = min(requested, int(cfg["dry_run_max_pages"])) if dry_run and cfg.get("dry_run_max_pages") else requested
    planned, prompt_check = plan_images(brief, "coloring", n, ctx.blocklist, spares=int(cfg["spare_subjects"]))
    client = resolve_client(client, dry_run, settings, "coloring", int(img_cfg["fake_long_side_px"]))
    check_budget(client, n)
    aspect_ratio, image_size = image_params(settings, "coloring")

    sizes = page_sizes_for(brief, settings)
    check_file_count(len(sizes))
    page_aspect, min_line_frac = _print_geometry(sizes, settings)
    slug = slugify(ctx.theme)
    pdf_names = {name: "files/" + file_name(slug, f"-coloring-pages-{name}.pdf") for name in sizes}   # fail early
    pre = vec["preprocess"]
    dpi = int(settings["print"].get("dpi", 300))

    with build_dir(ctx) as build:
        def process(image: Image.Image, i: int) -> PageResult:
            clean = cleanup.clean_line_art(image, threshold=int(pre["threshold"]),
                                           despeckle_px=int(pre["despeckle_px"]), min_line_px=int(pre["min_line_px"]),
                                           min_line_frac=min_line_frac, grayscale=bool(pre.get("grayscale", True)),
                                           aspect=page_aspect, pad_frac=float(cfg["pad_frac"]))
            png, svg = f"assets/page-{i:02d}.png", f"assets/page-{i:02d}.svg"
            cleanup.save_png(clean, build / png, dpi=dpi)
            tracer = cleanup.vectorize_line_art(clean, build / svg, vec.get("tracer", "potrace"))
            min_px = cleanup.min_line_width(clean.width, int(pre["min_line_px"]), min_line_frac)
            return PageResult(png, svg, tracer, cleanup.ink_fraction(clean), clean.size, min_px)

        results, skipped = generate_batch(client, planned, n, aspect_ratio=aspect_ratio, image_size=image_size,
                                          blocklist=ctx.blocklist, process=process,
                                          max_failures=int(cfg["max_failures"]))
        pages = [r for _, r in results]
        art = [_art(build, p, bool(cfg["vector_in_pdf"])) for p in pages]
        title = f"{ctx.theme.title()} Coloring Pages"
        files = []
        for name in sizes:
            rel = pdf_names[name]
            coloring_pdf(art, build / rel, name, settings, title=title, footer_text=cfg["footer_text"])
            files.append({"path": rel, "kind": "pdf", "page_size": name, "pages": pdf_info(build / rel)["pages"],
                          "dpi": dpi})

        manifest = base_manifest(ctx, brief, brief_file=brief_file, shop_id=shop_id, settings=settings,
                                 client=client, dry_run=dry_run, prompts=[p.prompt for p, _ in results],
                                 prompt_check=prompt_check)
        manifest["files"] = files
        manifest["previews"] = _previews(build, files[0], int(cfg["preview_dpi"]))
        manifest["assets"] = [{"path": path, "kind": kind, "page": i} for i, p in enumerate(pages, 1)
                              for path, kind in ((p.png, "png"), (p.svg, "svg"))]
        reasons = manifest["review_reasons"]
        for i, p in enumerate(pages, 1):
            if p.ink_fraction > float(cfg["max_ink_fraction"]):
                reasons.append(f"page {i} is {p.ink_fraction:.0%} black; may be shaded rather than line art")
        reasons += oversize_reasons(build, files)
        manifest["needs_review"] = bool(reasons)
        manifest["pack"] = {
            "requested_pages": requested,
            "pages": len(pages),
            "dry_run_capped": len(pages) < requested,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "tracers": sorted({p.tracer for p in pages}),
            "min_line_weight_pt": float(settings["print"]["min_line_weight_pt"]),
            "contents": [{"page": i, "subject": plan.subject, "size_px": list(r.size), "min_line_px": r.min_line_px,
                          "ink_fraction": round(r.ink_fraction, 4)} for i, (plan, r) in enumerate(results, 1)],
            "skipped": skipped,
        }
        commit(build, ctx.dir, manifest)
    log.info("generated %s (%d pages, %s)", ctx.item_id, len(pages), [f["page_size"] for f in files])
    return [ctx.dir]


def _page_count(brief: dict, settings: dict, cfg: dict) -> int:
    options = (settings.get("niches", {}).get(NICHE) or {}).get("pages_per_pack") or [cfg["default_pages"]]
    asked = brief.get("page_count")
    n = int(asked) if asked not in (None, "") else int(options[0])
    if not 1 <= n <= int(cfg["max_pages"]):
        raise ValueError(f"page_count {n} outside 1..{cfg['max_pages']}")
    return n


def _print_geometry(sizes: list[str], settings: dict) -> tuple[float, float]:
    """(art aspect w/h, min line width as a fraction of the art's width).

    Art is padded to the first page size's printable aspect. On every page size it is
    scaled to fit, so the smallest fitted width decides how thick a pixel line prints.
    """
    boxes = [printable_box(s, settings)[2] for s in sizes]
    aspect = boxes[0].w / boxes[0].h
    fitted_width_pt = min(min(b.w, b.h * aspect) for b in boxes)
    return aspect, float(settings["print"]["min_line_weight_pt"]) / fitted_width_pt


def _art(d: Path, page: PageResult, vector: bool):
    drawing = load_svg(d / page.svg) if vector else None
    if drawing is None:
        if vector:
            log.warning("could not read %s; placing the raster instead", page.svg)
        return Image.open(d / page.png).convert("L")
    return drawing


def _previews(d: Path, pdf: dict, dpi: int) -> list[dict]:
    out = []
    for i in range(pdf["pages"]):
        rel = f"previews/page-{i + 1:02d}.png"
        render_pdf_pages(d / pdf["path"], dpi=dpi, pages=[i])[0].save(d / rel, "PNG")
        out.append({"path": rel, "page": i + 1})
    return out
