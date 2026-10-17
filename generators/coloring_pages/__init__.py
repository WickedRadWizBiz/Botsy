"""AI-assisted coloring pages: image model -> line-art cleanup -> SVG -> PDF, as themed packs.

A pack-engine plugin (generators/packs, docs/PACKS.md section 3): one brief becomes one item
per size in brief["pack_sizes"] (else [page_count]), each page a different subject of the
theme (generators.packs.subjects: the text model, topped up from generators/imaging/subjects.py).
A design set's page is the model's image cleaned to pure black line art, thick enough to print
at print.min_line_weight_pt on every page size, plus its SVG trace and a quality score from its
ink balance (curation of derived packs prefers good pages). Each item holds:
    files/<theme>-coloring-pages-<size>-pages-<page size>.pdf   one per page size (Letter + A4
                                       when print.deliver_both_sizes), one art per page
    assets/page-NN.png, page-NN.svg    the pack's cleaned rasters and traces (QA checks them)
    previews/page-NN.png               150 dpi RGB renders of the first pages
    manifest.json                      docs/CONTRACTS.md section 2, ai_assisted: true

Dry runs use the offline FakeImageClient (small images, generators.imaging.common.fake_long_side)
and make every page of the pack. Paid images are made once per design set: a rerun of the same
brief reassembles its packs from the set instead of paying again.
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

from PIL import Image

from core.config import load_settings, section
from core.render import pdf_info, render_pdf_pages
from generators.imaging import cleanup
from generators.imaging.client import ImageClient
from generators.imaging.common import (
    by_plan, check_budget, copy_asset, fake_long_side, generate_batch, image_params, model_name, planned_images,
    resolve_client, style_review_reasons,
)
from generators.imaging.pdf import coloring_pdf, load_svg, printable_box
from generators.imaging.prompts import coloring_prompt
from generators.imaging.subjects import subject_pool
from generators.shared import page_sizes_for

__all__ = ["DEFAULTS", "NICHE", "PAGE_TYPES", "generate", "page_sizes_for", "plan_pages", "preview",
           "render_pages"]

log = logging.getLogger(__name__)

NICHE = "coloring_pages"
PAGE_TYPES = ("coloring_page",)
AI_ASSISTED = True
USES_SUBJECTS = True
FILE_LABEL = "coloring-pages"

DEFAULTS: dict = {
    "default_pages": 20,          # no brief pack_sizes / page_count and no niches.coloring_pages.pages_per_pack
    "max_pages": 50,
    "spare_subjects": 3,          # planned extra subjects that replace refused or blank images
    "max_failures": 3,            # more refused/blank images than this fails the design set
    "pad_frac": 0.03,             # white margin kept around the art when cropping
    "max_ink_fraction": 0.45,     # more black than this looks like a filled silhouette -> needs_review
    "vector_in_pdf": True,        # place the SVG trace; false places the cleaned raster
    "preview_dpi": 150,
    "footer_text": "",            # optional small line at the bottom of every page
}

PRE_DEFAULTS = {"grayscale": True, "threshold": 180, "despeckle_px": 20, "min_line_px": 4}


def _cfg(settings: dict) -> dict:
    return section(settings, NICHE, DEFAULTS)


def generate(brief: dict, *, brief_file: str | Path | None = None, shop_id: str = "main",
             out_root: str | Path | None = None, date: str | None = None, dry_run: bool = False,
             client: ImageClient | None = None, settings: dict | None = None, **kw) -> list[Path]:
    """Build every coloring pack of one brief. Returns one item dir per pack (master first)."""
    from generators.packs import generate_packs

    return generate_packs(brief, plugin=sys.modules[__name__], brief_file=brief_file, shop_id=shop_id,
                          out_root=out_root, date=date, dry_run=dry_run, client=client,
                          settings=settings or load_settings(), **kw)


# --- plugin hooks ----------------------------------------------------------------------------

def default_pack_size(settings: dict) -> int:
    options = (settings.get("niches", {}).get(NICHE) or {}).get("pages_per_pack") or [_cfg(settings)["default_pages"]]
    return int(options[0])


def max_pack_size(settings: dict) -> int:
    return int(_cfg(settings)["max_pages"])


def spare_pages(settings: dict) -> int:
    return int(_cfg(settings)["spare_subjects"])


def files_per_pack(page_sizes: list[str]) -> int:
    return len(page_sizes)


def brief_review_reasons(brief: dict, settings: dict) -> list[str]:
    return style_review_reasons(brief)


def resolve_image_client(client, *, dry_run: bool, settings: dict):
    return resolve_client(client, dry_run, settings, "coloring", fake_long_side(settings))


def _brief_like(ctx: dict) -> dict:
    """The prompt builder's input: our own theme, the audience (a fixed phrase) and the
    allowlisted style clauses only (generators/imaging/prompts.py)."""
    return {"theme": ctx.get("theme"), "audience": ctx.get("audience"), "style_notes": ctx.get("style")}


def plan_pages(ctx: dict, n: int, *, seed: str, settings: dict) -> list[dict]:
    """n pages, one subject each (ctx["subjects"] from generators.packs.subjects, else the
    built-in table), with the prompt that draws it. Deterministic for (ctx, n)."""
    subjects = [s for s in ctx.get("subjects") or [] if isinstance(s, str) and s.strip()]
    if not subjects:
        subjects = subject_pool(str(ctx.get("theme") or ""), list(ctx.get("keywords") or []))
    if len(subjects) < n:
        raise ValueError(f"theme {ctx.get('theme')!r} has only {len(subjects)} distinct subjects; {n} were asked for")
    brief = _brief_like(ctx)
    return [{"page_id": f"p{i:02d}", "type": "coloring_page", "subject": s,
             "spec": {"prompt": coloring_prompt(brief, s)}} for i, s in enumerate(subjects[:n], 1)]


def print_geometry(sizes: list[str], settings: dict) -> tuple[float, float]:
    """(art aspect w/h, min line width as a fraction of the art's width).

    Art is padded to the first page size's printable aspect. On every page size it is
    scaled to fit, so the smallest fitted width decides how thick a pixel line prints.
    """
    boxes = [printable_box(s, settings)[2] for s in sizes]
    aspect = boxes[0].w / boxes[0].h
    fitted_width_pt = min(min(b.w, b.h * aspect) for b in boxes)
    return aspect, float(settings["print"]["min_line_weight_pt"]) / fitted_width_pt


def page_quality(ink: float, max_ink: float) -> float:
    """QA-style score in 0..1 from the ink balance: open line art scores 1.0; nearly blank
    pages and heavy, shaded-looking pages score lower (curation picks them last)."""
    if ink > max_ink:
        return 0.3
    if ink < 0.01:
        return 0.4
    if ink < 0.03:
        return round(0.7 + 0.3 * (ink - 0.01) / 0.02, 3)
    high = 0.6 * max_ink
    if ink > high:
        return round(1.0 - 0.4 * (ink - high) / (max_ink - high), 3)
    return 1.0


def build_pages(candidates: list[dict], n: int, build: Path, *, settings: dict, ctx: dict, client, blocklist,
                item_id: str | None = None, dry_run: bool = False) -> dict:
    """Generate and clean n pages into the design set's pages/ folder (spare subjects replace
    refused or blank images). Every prompt is re-checked against the blocklist right before
    its call; the image cap is checked before the first."""
    cfg = _cfg(settings)
    vec = section(settings.get("vectorize", {}), NICHE, {"preprocess": PRE_DEFAULTS, "tracer": "potrace"})
    pre = vec["preprocess"]
    check_budget(client, n)
    aspect_ratio, image_size = image_params(settings, "coloring")
    sizes = list(ctx.get("page_sizes") or settings["print"]["page_sizes"])
    page_aspect, min_line_frac = print_geometry(sizes, settings)
    dpi = int(settings["print"].get("dpi", 300))
    planned = planned_images(candidates)
    lookup = by_plan(candidates, planned)

    def process(image: Image.Image, i: int) -> dict:
        clean = cleanup.clean_line_art(image, threshold=int(pre["threshold"]), despeckle_px=int(pre["despeckle_px"]),
                                       min_line_px=int(pre["min_line_px"]), min_line_frac=min_line_frac,
                                       grayscale=bool(pre.get("grayscale", True)), aspect=page_aspect,
                                       pad_frac=float(cfg["pad_frac"]))
        png, svg = f"pages/page-{i:02d}.png", f"pages/page-{i:02d}.svg"
        cleanup.save_png(clean, build / png, dpi=dpi)
        tracer = cleanup.vectorize_line_art(clean, build / svg, vec.get("tracer", "potrace"))
        return {"png": png, "svg": svg, "tracer": tracer, "ink": cleanup.ink_fraction(clean),
                "size_px": list(clean.size),
                "min_line_px": cleanup.min_line_width(clean.width, int(pre["min_line_px"]), min_line_frac)}

    results, skipped = generate_batch(client, planned, n, aspect_ratio=aspect_ratio, image_size=image_size,
                                      blocklist=blocklist, process=process, max_failures=int(cfg["max_failures"]),
                                      item_id=item_id)
    pages = []
    for plan, r in results:
        page = lookup[id(plan)]
        spec = dict(page.get("spec") or {}, size_px=r["size_px"], min_line_px=r["min_line_px"],
                    ink_fraction=round(r["ink"], 4), tracer=r["tracer"], aspect_ratio=aspect_ratio,
                    image_size=image_size)
        pages.append(dict(page, spec=spec, asset={"png": r["png"], "svg": r["svg"]},
                          quality=page_quality(r["ink"], float(cfg["max_ink_fraction"]))))
    return {"pages": pages, "skipped": skipped, "model": model_name(client)}


def _art(page: dict, vector: bool):
    files = page.get("asset_files") or {}
    drawing = load_svg(files["svg"]) if vector and files.get("svg") else None
    if drawing is None:
        if vector:
            log.warning("could not read the trace of %s; placing the raster instead", page.get("page_id"))
        with Image.open(files["png"]) as img:
            return img.convert("L")
    return drawing


def _title(ctx: dict) -> str:
    theme = " ".join(w[:1].upper() + w[1:] for w in str(ctx.get("theme") or "").split())   # not str.title(): "Cat'S"
    return f"{theme} Coloring Pages"


def render_pages(pages: list[dict], out_pdf: Path, *, page_size: str, settings: dict, ctx: dict) -> int:
    """One PDF page per art, fit inside the print margins. Returns the page count."""
    cfg = _cfg(settings)
    art = [_art(p, bool(cfg["vector_in_pdf"])) for p in pages]
    coloring_pdf(art, out_pdf, page_size, settings, title=_title(ctx), footer_text=cfg["footer_text"])
    return pdf_info(out_pdf)["pages"]


def preview(pages: list[dict], out_dir: Path, *, settings: dict, ctx: dict, limit: int = 6) -> list[Path]:
    """PNG renders of the first `limit` pages (first page size)."""
    cfg = _cfg(settings)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    size = (list(ctx.get("page_sizes") or []) or list(settings["print"]["page_sizes"]))[0]
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "preview.pdf"
        n = render_pages(pages[:limit], pdf, page_size=size, settings=settings, ctx=ctx)
        out = []
        for i, img in enumerate(render_pdf_pages(pdf, dpi=int(cfg["preview_dpi"]), pages=list(range(n))), 1):
            p = out_dir / f"page-{i:02d}.png"
            img.convert("RGB").save(p, "PNG")
            out.append(p)
    return out


def item_assets(pages: list[dict], build: Path) -> list[dict]:
    """The pack's cleaned rasters and traces, numbered by pack page (QA checks line weight)."""
    out = []
    for i, p in enumerate(pages, 1):
        files = p.get("asset_files") or {}
        for key, kind in (("png", "png"), ("svg", "svg")):
            if files.get(key):
                rel = f"assets/page-{i:02d}.{key}"
                copy_asset(files[key], build / rel)
                out.append({"path": rel, "kind": kind, "page": i})
    return out


def manifest_previews(build: Path, files: list[dict], pages: list[dict], *, settings: dict, ctx: dict) -> list[dict]:
    from generators.packs.engine import pdf_previews, preview_limit

    return pdf_previews(build, files, preview_limit(settings), dpi=int(_cfg(settings)["preview_dpi"]))


def printed_text(pages: list[dict], ctx: dict) -> list[str]:
    """Text the files carry: the PDF title (the pages themselves hold no text)."""
    return [_title(ctx)]


def review_reasons(pages: list[dict], *, settings: dict) -> list[str]:
    limit = float(_cfg(settings)["max_ink_fraction"])
    out = []
    for i, p in enumerate(pages, 1):
        ink = (p.get("spec") or {}).get("ink_fraction")
        if isinstance(ink, (int, float)) and ink > limit:
            out.append(f"page {i} is {ink:.0%} black; may be shaded rather than line art")
    return out


def pack_fields(pages: list[dict], files: list[dict], *, settings: dict, ctx: dict, set_doc: dict) -> dict:
    pack = ctx.get("pack") or {}
    own = pack.get("source") in ("master", "fresh")
    specs = [p.get("spec") or {} for p in pages]
    first = specs[0] if specs else {}
    return {
        "requested_pages": int(pack.get("size") or len(pages)),
        "pages": len(pages),
        "dry_run_capped": False,
        "aspect_ratio": first.get("aspect_ratio"),
        "image_size": first.get("image_size"),
        "tracers": sorted({str(s.get("tracer")) for s in specs if s.get("tracer")}),
        "min_line_weight_pt": float(settings["print"]["min_line_weight_pt"]),
        "contents": [{"page": i, "subject": p.get("subject"), "size_px": s.get("size_px"),
                      "min_line_px": s.get("min_line_px"), "ink_fraction": s.get("ink_fraction")}
                     for i, (p, s) in enumerate(zip(pages, specs), 1)],
        "skipped": list(set_doc.get("skipped") or []) if own else [],
    }
