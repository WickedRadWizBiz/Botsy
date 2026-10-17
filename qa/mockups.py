"""Etsy listing photos built with Pillow from an item's page previews and sticker PNGs.

Five generic layouts of our own: hero (fanned pages + title banner), page grid, close-up,
"what you get" card, and either a two-sizes card or, for stickers, a desk scene. A pack's
hero carries a size badge ("20 PAGES", "SET OF 5") and its grid shows the pack's own pages;
stationery and ttrpg packs get a sixth photo, printed pages on a desk. Every string drawn
on an image goes through the trademark blocklist first (game-system names included: they
are never on a mockup, even for a ttrpg item made for that system), and the random
placement is seeded by the item id so a rerun produces the same photos.
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from core.compliance import Blocklist
from core.items import content_count as items_content_count
from qa import draw
from qa.checks import WARNING, Item, result, system_mentions
from qa.lineart import iter_pdf_pages
from qa.raster import open_image

log = logging.getLogger("qa.mockups")

NICHE_LABELS = {
    "coloring_pages": ("Coloring Page", "Coloring Pages", "PRINTABLE COLORING PAGES"),
    "activity_sheets": ("Activity Page", "Activity Pages", "PRINTABLE ACTIVITY PACK"),
    "stickers": ("Sticker", "Stickers", "DIGITAL STICKERS"),
    "stationery": ("Planner Page", "Planner Pages", "PRINTABLE PLANNER PAGES"),
    "ttrpg": ("RPG Sheet", "RPG Sheets", "PRINTABLE RPG SHEETS"),
}
FILE_TYPES = {"pdf": "PDF", "png": "PNG", "png_transparent": "PNG", "zip": "ZIP", "svg": "SVG"}
PALETTES = {
    "coloring_pages": {"top": (255, 248, 238), "bottom": (252, 226, 211), "blob": (255, 204, 176),
                       "accent": (222, 102, 78), "ink": (52, 42, 54)},
    "activity_sheets": {"top": (238, 247, 253), "bottom": (212, 232, 247), "blob": (176, 212, 240),
                        "accent": (36, 118, 186), "ink": (28, 42, 58)},
    "stickers": {"top": (253, 242, 246), "bottom": (245, 219, 231), "blob": (240, 188, 212),
                 "accent": (186, 66, 120), "ink": (60, 36, 52)},
    "stationery": {"top": (246, 247, 240), "bottom": (226, 233, 219), "blob": (204, 220, 196),
                   "accent": (74, 124, 89), "ink": (38, 50, 42)},
    "ttrpg": {"top": (246, 240, 230), "bottom": (229, 214, 192), "blob": (214, 190, 160),
              "accent": (128, 54, 44), "ink": (52, 38, 30)},
}
WHITE = (255, 255, 255)


@dataclass
class Ctx:
    """Everything a layout needs: canvas size, source images, the strings and the style."""

    size: tuple[int, int]
    niche: str
    pages: list[Image.Image]
    stickers: list[Image.Image]
    text: dict
    pal: dict
    fonts: tuple[str, ...]
    seed: int
    sizes: list[tuple[str, str, float, float]] = field(default_factory=list)

    @property
    def u(self) -> float:
        """Layout unit: 1.0 at the default 2700 px width, so smaller test sizes scale down."""
        return self.size[0] / 2700

    def rng(self, salt: str) -> random.Random:
        return random.Random(f"{self.seed}-{salt}")

    def font(self, px: float, bold: bool = True):
        return draw.font(round(px * self.u), bold, self.fonts)


def build(item: Item, blocklist: Blocklist) -> tuple[list[str], list[dict]]:
    """Render the mockups into <item>/mockups/. Returns (relative paths, checks)."""
    cfg = item.cfg["mockups"]
    out_dir = item.dir / "mockups"
    for old in out_dir.glob("mockup-*.jpg"):  # reruns must not leave stale photos behind
        old.unlink()
    ctx = _context(item, cfg)
    strings = _strings(ctx.text)
    r = blocklist.check(*strings)  # no game system allowed: never on a mockup
    blocked = r.blocked + [f"game system {s}" for s in system_mentions(strings)]
    checks = [result("mockup_text", not blocked,
                     f"blocked terms in mockup text: {', '.join(blocked)}" if blocked
                     else f"{len(strings)} strings clean")]
    if r.flagged:
        checks.append(result("mockup_review_terms", False, f"review terms in mockup text: {', '.join(r.flagged)}", WARNING))
    if blocked:
        return [], checks
    if not ctx.pages and not ctx.stickers:
        return [], checks + [result("mockups", False, "no previews, PDF pages or sticker PNGs to build photos from")]

    layouts = [hero, grid, closeup, what_you_get, desk if ctx.niche == "stickers" else sizes_or_detail]
    if ctx.niche in (cfg.get("desk_niches") or ()) and ctx.niche != "stickers" and ctx.pages:
        layouts.append(desk_pages)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths, failed, heavy = [], [], []
    for fn in layouts:
        try:
            img = fn(ctx)
        except Exception as e:  # one bad layout should not cost the item its other photos
            log.exception("mockup %s failed for %s", fn.__name__, item.dir.name)
            failed.append(f"{fn.__name__}: {type(e).__name__}: {e}")
            continue
        rel = f"mockups/mockup-{len(paths) + 1:02d}.jpg"
        size = save_jpeg(img, item.dir / rel, int(cfg["quality"]), int(cfg.get("max_bytes") or 0))
        if cfg.get("max_bytes") and size > int(cfg["max_bytes"]):
            heavy.append(f"{rel} {size / 1e6:.2f} MB")
        paths.append(rel)
    w, h = ctx.size
    checks.append(result("mockups", bool(paths), f"{len(paths)} photos {w}x{h} JPEG q{cfg['quality']}"
                         + (f"; failed: {'; '.join(failed)}" if failed else "")))
    if paths and (failed or len(paths) < int(cfg["min_count"])):
        checks.append(result("mockup_count", False, f"{len(paths)} of {cfg['min_count']} photos made", WARNING))
    if heavy:
        checks.append(result("mockup_file_size", False, f"over {int(cfg['max_bytes']) / 1e6:g} MB even at JPEG "
                             f"q{MIN_QUALITY}, Etsy may not finish the upload: {', '.join(heavy)}", WARNING))
    return paths, checks


MIN_QUALITY = 70


def save_jpeg(img: Image.Image, path: Path, quality: int, max_bytes: int) -> int:
    """Save as JPEG; if the file is over max_bytes (Etsy: listing images over 1 MB may not
    finish uploading), step the quality down to MIN_QUALITY. Returns the final size in bytes."""
    rgb = img.convert("RGB")
    while True:
        rgb.save(path, "JPEG", quality=quality, optimize=True)
        size = path.stat().st_size
        if not max_bytes or size <= max_bytes or quality <= MIN_QUALITY:
            return size
        quality = max(MIN_QUALITY, quality - 8)


# ---------------------------------------------------------------- inputs and strings

def _context(item: Item, cfg: dict) -> Ctx:
    m = item.manifest
    niche = m.get("niche", "")
    size = (int(cfg["width"]), int(cfg["height"]))
    pages = _load_pages(item, cfg, size)
    stickers = _load_stickers(item, cfg) if niche == "stickers" else []
    sizes = _page_sizes(item, cfg)
    seed = int(hashlib.sha1(str(m.get("item_id", item.dir.name)).encode()).hexdigest()[:8], 16)
    text = _texts(item, sizes, len(stickers))
    if not cfg.get("pack_badge", True):
        text["badge"] = ""
    return Ctx(size, niche, pages, stickers, text,
               PALETTES.get(niche, PALETTES["coloring_pages"]), tuple(cfg.get("fonts") or ()), seed, sizes)


def _load_pages(item: Item, cfg: dict, size: tuple[int, int]) -> list[Image.Image]:
    """Page images in page order: the previews, or the first PDF rendered when there are none.
    The pack's hero page (manifest pack.hero_page, the page its style ranks highest) comes
    first when it has a preview, so the cover photo leads with it."""
    want = max(int(cfg["max_grid"]), 3)
    cap = max(size) * 1.1  # enough pixels for the close-up, no more
    previews = sorted(item.entries("previews"), key=lambda e: e["page"] if isinstance(e.get("page"), int) else 0)
    hero_page = (item.manifest.get("pack") or {}).get("hero_page") if isinstance(item.manifest.get("pack"), dict) else None
    if isinstance(hero_page, int) and not isinstance(hero_page, bool):
        lead = [e for e in previews if e.get("page") == hero_page][:1]
        previews = lead + [e for e in previews if e not in lead]
    pages = []
    for e in previews[:want]:
        try:
            pages.append(open_image(item.path(e)).convert("RGB"))
        except Exception:
            continue  # reported by previews_readable
    if not pages:
        pdfs = item.entries("files", "pdf")
        if pdfs:
            pages = [img for _, img in iter_pdf_pages(item.path(pdfs[0]), float(cfg["render_dpi"]), max_pages=want)]
    for p in pages:
        p.thumbnail((cap, cap), Image.LANCZOS)
    return pages


def _load_stickers(item: Item, cfg: dict) -> list[Image.Image]:
    entries = item.entries("assets", "png_transparent") or item.entries("files", "png_transparent")
    out = []
    for e in entries[: int(cfg["max_stickers"])]:
        try:
            img = open_image(item.path(e)).convert("RGBA")
        except Exception:
            continue
        box = img.getbbox()  # trim the transparent margin so stickers sit where we put them
        if box:
            img = img.crop(box)
        img.thumbnail((900, 900), Image.LANCZOS)
        out.append(img)
    return out


def _page_sizes(item: Item, cfg: dict) -> list[tuple[str, str, float, float]]:
    """(label, dimensions, width_in, height_in) for each page size delivered, in file order."""
    known = item.settings["print"]["page_sizes"]
    labels = cfg.get("size_labels") or {}
    out, seen = [], set()
    for e in item.entries("files", "pdf"):
        name = e.get("page_size")
        if name in known and name not in seen:
            seen.add(name)
            w, h = float(known[name]["width_in"]), float(known[name]["height_in"])
            label, dims = labels.get(name) or (name.replace("_", " ").title(), f"{w:g} x {h:g} in")
            out.append((label, dims, w, h))
    return out


ACRONYMS = {"adhd": "ADHD", "wfh": "WFH", "rpg": "RPG", "ttrpg": "TTRPG", "npc": "NPC", "abc": "ABC", "diy": "DIY"}


def title_case(text: str) -> str:
    return " ".join(ACRONYMS.get(w.lower(), w[:1].upper() + w[1:]) for w in text.split())


def content_count(m: dict, n_stickers: int) -> int | None:
    """What the pack holds, for the "20 Coloring Pages" line (core.items.content_count, the
    rule the listing copy uses). `n_stickers` is a fallback only: the mockups load at most
    max_stickers images, not the whole pack."""
    return items_content_count(m, n_stickers)


def _texts(item: Item, sizes: list, n_stickers: int) -> dict:
    m = item.manifest
    niche = m.get("niche", "")
    one, many, kicker = NICHE_LABELS.get(niche, ("Page", "Pages", "PRINTABLE"))
    n = content_count(m, n_stickers)
    count_line = f"{n} {one if n == 1 else many}" if n else f"{one} Pack"
    kinds = [e.get("kind") for e in m.get("files") or [] if isinstance(e, dict)]
    types = " + ".join(dict.fromkeys(FILE_TYPES[k] for k in kinds if k in FILE_TYPES)) or "Digital file"
    size_line = " + ".join(s[0] for s in sizes)
    pack = m.get("pack") if isinstance(m.get("pack"), dict) else {}
    first = count_line + (" + answer keys" if pack.get("answer_key_pages") else "")
    bullets = [first]
    if size_line:
        bullets.append(f"Sizes: {size_line}")
    bullets.append(f"File type: {types}")
    if "png_transparent" in kinds or (niche == "stickers" and n_stickers):
        bullets.append("Transparent backgrounds")
    bullets += ["Instant digital download", "Print at home or at a print shop", "No physical item is shipped"]
    return {
        "badge": pack_badge(m, n),
        "title": title_case(str(m.get("theme") or "").strip()) or many,
        "count": count_line,
        "kicker": kicker,
        "hero_lines": [x for x in (size_line, f"{types}  |  Instant download") if x],
        "grid_title": "A look inside",
        "closeup_title": "Up close",
        "closeup_sub": "Clean transparent edges" if niche == "stickers" else "Crisp, print-ready detail",
        "wyg_title": "What you get",
        "bullets": bullets,
        "sizes_title": "Two sizes included",
        "size_labels": [f"{s[0]}|{s[1]}" for s in sizes],
    }


def pack_badge(m: dict, n: int | None) -> str:
    """'20 PAGES' or, for stickers, 'SET OF 5': the pack size on the cover photo. Empty for
    an item made before packs (no pack.size)."""
    pack = m.get("pack") if isinstance(m.get("pack"), dict) else {}
    size = pack.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        return ""
    count = n or size
    if m.get("niche") == "stickers":
        return f"SET OF {count}"
    return f"{count} PAGE" if count == 1 else f"{count} PAGES"


def _strings(text: dict) -> list[str]:
    out = []
    for v in text.values():
        out += v if isinstance(v, list) else [v]
    return [s.replace("|", " ") for s in out if isinstance(s, str)]


# ---------------------------------------------------------------- layouts

def background(ctx: Ctx, salt: str) -> Image.Image:
    c = draw.gradient(ctx.size, ctx.pal["top"], ctx.pal["bottom"])
    draw.soft_blobs(c, ctx.pal["blob"], ctx.rng(salt))
    return c


def hero(ctx: Ctx) -> Image.Image:
    W, H = ctx.size
    u = ctx.u
    c = background(ctx, "hero")
    left_cx = 0.30 * W
    if ctx.pages:
        stack = ctx.pages[:3]
        angles, shifts = [0, -5, 6][: len(stack)], [0, -0.045, 0.05][: len(stack)]
        for img, ang, dx in reversed(list(zip(stack, angles, shifts))):
            sheet = draw.paper(draw.fit(img, (int(0.42 * W), int(0.74 * H))))
            draw.paste_with_shadow(c, sheet, (left_cx + dx * W, 0.5 * H), angle=ang, blur=34 * u, offset=(14 * u, 22 * u))
        if ctx.stickers:
            _scatter(c, ctx, ctx.stickers[:3], (0.08 * W, 0.62 * H, 0.52 * W, 0.95 * H), 0.24 * H, "hero")
    else:
        _scatter(c, ctx, ctx.stickers[:6], (0.04 * W, 0.08 * H, 0.56 * W, 0.92 * H), 0.34 * H, "hero")

    x0, x1 = int(0.585 * W), int(0.955 * W)
    pad = int(80 * u)
    panel = _panel(ctx, x1 - x0 - 2 * pad, lambda d, w: _hero_text(ctx, d, w))
    card = _card_with(c, ctx, panel, x0, x1, pad, min_h=int(0.5 * H))
    if ctx.text.get("badge"):
        _badge(c, ctx, ctx.text["badge"], badge_center(ctx, card))
    return c


def badge_center(ctx: Ctx, card: tuple[int, int, int, int]) -> tuple[float, float]:
    """Where the size badge sits: on the title card's top-right corner, half off the card,
    so it never covers the pages (their titles are what shoppers read first) or the card's
    text, which starts at the card's left edge and below its top padding."""
    W, _ = ctx.size
    r = BADGE_RADIUS * ctx.u
    _, y0, x1, _ = card
    cx = min(x1 - 0.35 * r, W - 1.1 * r)
    cy = max(1.15 * r, y0 - 0.1 * r)
    return cx, cy


BADGE_RADIUS = 150  # at the 2700 px reference width


def _badge(c: Image.Image, ctx: Ctx, text: str, center: tuple[float, float]) -> None:
    """A round accent badge with the pack size ("20 PAGES" on two lines, "SET OF 5")."""
    u = ctx.u
    words = text.split()
    lines = [words[0], " ".join(words[1:])] if words and words[0].isdigit() else [" ".join(words[:-1]), words[-1]]
    lines = [x for x in lines if x]
    r = int(BADGE_RADIUS * u)
    disc = Image.new("RGBA", (2 * r, 2 * r), (0, 0, 0, 0))
    d = ImageDraw.Draw(disc)
    d.ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=ctx.pal["accent"] + (255,))
    d.ellipse((int(10 * u), int(10 * u), 2 * r - 1 - int(10 * u), 2 * r - 1 - int(10 * u)),
              outline=WHITE + (255,), width=max(2, int(5 * u)))
    sizes = [round(118 * u) if x.isdigit() else round(52 * u) for x in lines]   # the number big
    heights, fonts = [], []
    for line, px in zip(lines, sizes):
        f, wrapped = draw.fit_text(d, line, int(1.5 * r), px, min_size=max(8, round(20 * u)), max_lines=1,
                                   fonts=ctx.fonts)
        fonts.append((f, wrapped[0]))
        heights.append(draw.text_size(d, wrapped[0], f)[1])
    y = r - (sum(heights) + int(10 * u) * (len(heights) - 1)) / 2
    for (f, line), h in zip(fonts, heights):
        d.text((r, y + h / 2), line, font=f, fill=WHITE, anchor="mm")
        y += h + int(10 * u)
    draw.paste_with_shadow(c, disc, center, angle=-8, blur=18 * u, offset=(6 * u, 10 * u), opacity=90)


def _hero_text(ctx: Ctx, d: ImageDraw.ImageDraw, w: int) -> int:
    u = ctx.u
    fk, kicker = draw.fit_text(d, ctx.text["kicker"], w, round(40 * u), min_size=round(20 * u), max_lines=1,
                               fonts=ctx.fonts)
    d.text((0, 0), kicker[0], font=fk, fill=ctx.pal["accent"])
    y = int(95 * u)
    ft, lines = draw.fit_text(d, ctx.text["title"], w, round(140 * u), min_size=round(56 * u), max_lines=3,
                              fonts=ctx.fonts)
    y = draw.draw_lines(d, lines, ft, 0, y, ctx.pal["ink"], spacing=1.08) + int(40 * u)
    y = _pill(d, ctx, ctx.text["count"], 0, y, w, round(66 * u)) + int(60 * u)
    for line in ctx.text["hero_lines"]:
        fl, wrapped = draw.fit_text(d, line, w, round(46 * u), min_size=round(24 * u), max_lines=1, bold=False,
                                    fonts=ctx.fonts)
        d.text((0, y), wrapped[0], font=fl, fill=ctx.pal["ink"])
        y += int(72 * u)
    return y


def _panel(ctx: Ctx, width: int, paint) -> Image.Image:
    """Draw text on a transparent white layer and crop it to what was drawn, so the card
    around it can be sized to the content. `paint(draw, width)` returns the used height."""
    layer = Image.new("RGBA", (width, ctx.size[1] * 2), WHITE + (0,))
    used = paint(ImageDraw.Draw(layer), width)
    return layer.crop((0, 0, width, max(1, min(layer.height, int(used)))))


def _card_with(c: Image.Image, ctx: Ctx, panel: Image.Image, x0: int, x1: int, pad: int,
               min_h: int = 0) -> tuple[int, int, int, int]:
    """A white rounded card from x0 to x1, vertically centered, holding the panel. Returns
    the card's box."""
    H = ctx.size[1]
    h = min(int(0.9 * H), max(min_h, panel.height + 2 * pad))
    y0 = (H - h) // 2
    draw.rounded_card(c, (x0, y0, x1, y0 + h), WHITE, radius=int(46 * ctx.u))
    c.paste(panel, (x0 + pad, y0 + (h - panel.height) // 2), panel)
    return x0, y0, x1, y0 + h


def _pill(d: ImageDraw.ImageDraw, ctx: Ctx, text: str, x: int, y: int, max_w: int, size: int) -> int:
    """Accent pill with white text. Returns its bottom y."""
    u = ctx.u
    pad_x, pad_y = int(42 * u), int(26 * u)
    f, lines = draw.fit_text(d, text, max_w - 2 * pad_x, size, min_size=round(24 * u), max_lines=1, fonts=ctx.fonts)
    tw, th = draw.text_size(d, lines[0], f)
    box = (x, y, x + tw + 2 * pad_x, y + th + 2 * pad_y)
    d.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=ctx.pal["accent"])
    d.text((x + pad_x, (box[1] + box[3]) // 2), lines[0], font=f, fill=WHITE, anchor="lm")
    return box[3]


def _header(c: Image.Image, ctx: Ctx, title: str, sub: str | None, top: float) -> float:
    """Centered title (+ accent subtitle). Returns the y below it."""
    W, _ = ctx.size
    u = ctx.u
    d = ImageDraw.Draw(c)
    f, lines = draw.fit_text(d, title, int(0.86 * W), round(110 * u), min_size=round(40 * u), max_lines=1, fonts=ctx.fonts)
    y = draw.draw_lines(d, lines, f, 0, int(top), ctx.pal["ink"], center_w=W)
    if sub:
        fs, sl = draw.fit_text(d, sub, int(0.86 * W), round(60 * u), min_size=round(28 * u), max_lines=1, fonts=ctx.fonts)
        y = draw.draw_lines(d, sl, fs, 0, y + int(6 * u), ctx.pal["accent"], center_w=W)
    return y


def grid(ctx: Ctx) -> Image.Image:
    W, H = ctx.size
    u = ctx.u
    c = background(ctx, "grid")
    top = _header(c, ctx, ctx.text["grid_title"], ctx.text["count"], 0.06 * H) + 40 * u
    use_stickers = bool(ctx.stickers) and ctx.niche == "stickers"
    items = (ctx.stickers if use_stickers else ctx.pages)[: (8 if not use_stickers else 12)]
    n = len(items)
    rows = 1 if n <= 4 else (2 if n <= 8 else 3)
    cols = math.ceil(n / rows)
    x0, x1, y0, y1 = 0.05 * W, 0.95 * W, top, 0.95 * H
    cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
    d = ImageDraw.Draw(c)
    fb = ctx.font(34)
    for i, img in enumerate(items):
        r, k = divmod(i, cols)
        row_n = cols if r < rows - 1 else n - cols * (rows - 1)  # center a short last row
        cx = x0 + (k + 0.5) * cw + (cols - row_n) * cw / 2
        cy = y0 + (r + 0.5) * ch
        if use_stickers:
            s = draw.die_cut(draw.fit(img, (int(cw * 0.74), int(ch * 0.74))), max(3, int(10 * u)))
            draw.paste_with_shadow(c, s, (cx, cy), angle=ctx.rng(f"g{i}").uniform(-6, 6), blur=14 * u,
                                   offset=(5 * u, 8 * u), opacity=70)
            continue
        sheet = draw.paper(draw.fit(img, (int(cw * 0.86), int(ch * 0.9))))
        box = draw.paste_with_shadow(c, sheet, (cx, cy), blur=18 * u, offset=(6 * u, 10 * u), opacity=70)
        rad = int(30 * u)
        bx, by = box[2] - rad - int(10 * u), box[3] - rad - int(10 * u)
        d.ellipse((bx - rad, by - rad, bx + rad, by + rad), fill=ctx.pal["accent"])
        d.text((bx, by), str(i + 1), font=fb, fill=WHITE, anchor="mm")
    return c


def closeup(ctx: Ctx, index: int = 1) -> Image.Image:
    """One page (or sticker) large with a magnified detail. Page 2 by default, since
    page 1 is often a cover."""
    W, H = ctx.size
    u = ctx.u
    c = background(ctx, f"closeup{index}")
    sticker = ctx.niche == "stickers" and bool(ctx.stickers)
    pool = ctx.stickers if sticker else ctx.pages
    src = pool[index if index < len(pool) else 0]
    if sticker:
        shown = draw.die_cut(draw.fit(src, (int(0.44 * W), int(0.70 * H))), max(4, int(16 * u)))
        draw.paste_with_shadow(c, shown, (0.29 * W, 0.5 * H), angle=-4, blur=24 * u, offset=(10 * u, 16 * u))
    else:
        shown = draw.paper(draw.fit(src, (int(0.50 * W), int(0.88 * H))))
        draw.paste_with_shadow(c, shown, (0.29 * W, 0.5 * H), blur=30 * u, offset=(12 * u, 20 * u))

    # magnifier: a zoomed crop around the densest part of the art
    diam = int(0.34 * W)
    cx, cy = _ink_center(src)
    span = int(min(src.size) * 0.34)
    x0 = min(max(0, cx - span // 2), src.width - span)
    y0 = min(max(0, cy - span // 2), src.height - span)
    crop = src.crop((x0, y0, x0 + span, y0 + span)).convert("RGBA")
    base = _checker((span, span), max(4, span // 14)) if sticker else Image.new("RGBA", (span, span), WHITE + (255,))
    lens = Image.alpha_composite(base, crop).resize((diam, diam), Image.LANCZOS)
    ring = int(18 * u)
    disc = Image.new("RGBA", (diam + 2 * ring, diam + 2 * ring), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((0, 0, disc.width - 1, disc.height - 1), fill=WHITE + (255,))
    mask = Image.new("L", (diam, diam), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diam - 1, diam - 1), fill=255)
    disc.paste(lens, (ring, ring), mask)
    ImageDraw.Draw(disc).ellipse((ring // 2, ring // 2, disc.width - ring // 2, disc.height - ring // 2),
                                 outline=ctx.pal["accent"], width=max(2, int(8 * u)))
    lens_c = (0.765 * W, 0.42 * H)
    draw.paste_with_shadow(c, disc, lens_c, blur=30 * u, offset=(10 * u, 18 * u))

    d = ImageDraw.Draw(c)
    y = int(lens_c[1] + disc.height / 2 + 50 * u)
    x0, w = int(0.58 * W), int(0.37 * W)
    f, lines = draw.fit_text(d, ctx.text["closeup_title"], w, round(84 * u), min_size=round(32 * u), max_lines=1,
                             fonts=ctx.fonts)
    y = draw.draw_lines(d, lines, f, x0, y, ctx.pal["ink"], center_w=w)
    f2, l2 = draw.fit_text(d, ctx.text["closeup_sub"], w, round(48 * u), min_size=round(22 * u), max_lines=2,
                           bold=False, fonts=ctx.fonts)
    draw.draw_lines(d, l2, f2, x0, y + int(8 * u), ctx.pal["accent"], center_w=w)
    return c


def _ink_center(img: Image.Image, frac: float = 0.34) -> tuple[int, int]:
    """Center of the square window (side frac * short side) holding the most art, so the
    close-up shows detail rather than empty paper. For a cut-out sticker "art" is its
    outline, so the lens shows the clean edge against the transparency checkerboard."""
    small = img.copy()
    small.thumbnail((240, 240))
    if small.mode == "RGBA":
        solid = np.pad(np.asarray(small.getchannel("A")) > 128, 1)
        inner = solid[1:-1, 1:-1] & solid[:-2, 1:-1] & solid[2:, 1:-1] & solid[1:-1, :-2] & solid[1:-1, 2:]
        ink = solid[1:-1, 1:-1] & ~inner
    else:
        ink = np.asarray(small.convert("L")) < 200
    k = max(2, int(min(small.size) * frac))
    if not ink.any() or k >= min(ink.shape):
        return img.width // 2, img.height // 2
    sums = np.pad(ink.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    win = sums[k:, k:] - sums[:-k, k:] - sums[k:, :-k] + sums[:-k, :-k]
    y, x = np.unravel_index(int(win.argmax()), win.shape)
    sx, sy = img.width / small.width, img.height / small.height
    return int((x + k / 2) * sx), int((y + k / 2) * sy)


def _checker(size: tuple[int, int], cell: int) -> Image.Image:
    yy, xx = np.indices((size[1], size[0]))
    on = ((yy // cell + xx // cell) % 2).astype(bool)
    arr = np.where(on[..., None], np.array([226, 226, 226, 255], np.uint8), np.array([250, 250, 250, 255], np.uint8))
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def what_you_get(ctx: Ctx) -> Image.Image:
    W, H = ctx.size
    u = ctx.u
    c = background(ctx, "wyg")
    if ctx.pages and not (ctx.niche == "stickers" and ctx.stickers):
        for img, ang, dx in ((ctx.pages[min(1, len(ctx.pages) - 1)], 6, 0.03), (ctx.pages[0], -3, 0)):
            sheet = draw.paper(draw.fit(img, (int(0.28 * W), int(0.66 * H))))
            draw.paste_with_shadow(c, sheet, ((0.80 + dx) * W, 0.5 * H), angle=ang, blur=26 * u, offset=(10 * u, 16 * u))
    else:
        _scatter(c, ctx, ctx.stickers[:4] or ctx.pages[:1], (0.64 * W, 0.12 * H, 0.97 * W, 0.88 * H), 0.30 * H, "wyg")
    x0, x1, pad = int(0.05 * W), int(0.63 * W), int(90 * u)
    panel = _panel(ctx, x1 - x0 - 2 * pad, lambda d, w: _wyg_text(ctx, d, w))
    _card_with(c, ctx, panel, x0, x1, pad)
    return c


def _wyg_text(ctx: Ctx, d: ImageDraw.ImageDraw, w: int) -> int:
    u, H = ctx.u, ctx.size[1]
    ft, lines = draw.fit_text(d, ctx.text["wyg_title"], w, round(110 * u), min_size=round(40 * u), max_lines=1,
                              fonts=ctx.fonts)
    y = draw.draw_lines(d, lines, ft, 0, 0, ctx.pal["ink"]) + int(40 * u)
    bullets = ctx.text["bullets"]
    step = min(int(132 * u), int((0.86 * H - 180 * u - y) / max(len(bullets), 1)))
    fb = draw.font(max(12, int(step * 0.42)), False, ctx.fonts)
    r = int(step * 0.21)
    for b in bullets:
        cy = y + step // 2
        d.ellipse((0, cy - r, 2 * r, cy + r), fill=ctx.pal["accent"])
        d.line([(r * 0.55, cy + r * 0.02), (r * 0.9, cy + r * 0.4), (r * 1.5, cy - r * 0.4)], fill=WHITE,
               width=max(2, r // 4), joint="curve")  # check mark
        fl, wrapped = draw.fit_text(d, b, w - 3 * r, fb.size, min_size=max(10, int(fb.size * 0.6)), max_lines=1,
                                    bold=False, fonts=ctx.fonts)
        d.text((3 * r, cy), wrapped[0], font=fl, fill=ctx.pal["ink"], anchor="lm")
        y += step
    return y


def sizes_or_detail(ctx: Ctx) -> Image.Image:
    """Both page sizes side by side at true relative scale, or a second close-up when the
    item comes in one size only."""
    if len(ctx.sizes) < 2 or not ctx.pages:
        return closeup(ctx, index=2 if len(ctx.pages) > 2 else 0)
    W, H = ctx.size
    u = ctx.u
    c = background(ctx, "sizes")
    top = _header(c, ctx, ctx.text["sizes_title"], None, 0.07 * H) + 50 * u
    shown = ctx.sizes[:3]
    label_h = 190 * u
    px_per_in = min((0.95 * H - top - label_h) / max(s[3] for s in shown),
                    0.8 * W / sum(s[2] for s in shown) / 1.25)
    gap = 0.25 * max(s[2] for s in shown) * px_per_in
    total = sum(s[2] for s in shown) * px_per_in + gap * (len(shown) - 1)
    x = (W - total) / 2
    page_bottom = top + max(s[3] for s in shown) * px_per_in
    d = ImageDraw.Draw(c)
    fl, fd = ctx.font(64), ctx.font(44, bold=False)
    for label, dims, w_in, h_in in shown:
        pw, ph = int(w_in * px_per_in), int(h_in * px_per_in)
        sheet = Image.new("RGB", (pw, ph), WHITE)
        art = draw.fit(ctx.pages[0], (pw, ph))
        sheet.paste(art, ((pw - art.width) // 2, (ph - art.height) // 2))
        draw.paste_with_shadow(c, draw.paper(sheet), (x + pw / 2, page_bottom - ph / 2), blur=24 * u,
                               offset=(8 * u, 14 * u))
        d.text((x + pw / 2, page_bottom + 40 * u), label, font=fl, fill=ctx.pal["ink"], anchor="mt")
        d.text((x + pw / 2, page_bottom + 120 * u), dims, font=fd, fill=ctx.pal["accent"], anchor="mt")
        x += pw + gap
    return c


def desk(ctx: Ctx) -> Image.Image:
    """Stickers: the sheet and a few loose die-cut stickers on a wooden desk."""
    W, H = ctx.size
    u = ctx.u
    c = _wood(ctx.size, ctx.seed)
    area = (0.04 * W, 0.06 * H, 0.96 * W, 0.94 * H)
    if ctx.pages:
        sheet = draw.paper(draw.fit(ctx.pages[0], (int(0.48 * W), int(0.84 * H))))
        draw.paste_with_shadow(c, sheet, (0.32 * W, 0.5 * H), angle=-4, blur=30 * u, offset=(16 * u, 24 * u),
                               opacity=120)
        area = (0.58 * W, 0.08 * H, 0.97 * W, 0.92 * H)
    _scatter(c, ctx, ctx.stickers[:6], area, 0.28 * H, "desk", opacity=120)
    return c


def desk_pages(ctx: Ctx) -> Image.Image:
    """Stationery and ttrpg: printed pages on a wooden desk with a pencil, drawn by us."""
    W, H = ctx.size
    u = ctx.u
    c = _wood(ctx.size, ctx.seed + 7)
    rng = ctx.rng("desk-pages")
    shown = ctx.pages[1:3] if len(ctx.pages) > 2 else ctx.pages[:2]
    spots = [((0.34, 0.52), -6), ((0.64, 0.48), 5)]
    for img, ((fx, fy), ang) in zip(shown, spots):
        sheet = draw.paper(draw.fit(img, (int(0.40 * W), int(0.86 * H))))
        draw.paste_with_shadow(c, sheet, (fx * W, fy * H), angle=ang + rng.uniform(-1.5, 1.5), blur=28 * u,
                               offset=(14 * u, 22 * u), opacity=120)
    draw.paste_with_shadow(c, _pencil(ctx), (0.86 * W, 0.62 * H), angle=58, blur=12 * u, offset=(8 * u, 12 * u),
                           opacity=110)
    return c


def _pencil(ctx: Ctx) -> Image.Image:
    """A plain yellow pencil (no brand markings), lying flat."""
    u = ctx.u
    length, width = int(980 * u), max(8, int(64 * u))
    img = Image.new("RGBA", (length, width), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    tip = int(150 * u)
    d.rectangle((tip, 0, length - int(90 * u), width - 1), fill=(240, 196, 64, 255))
    d.rectangle((tip, width // 3, length - int(90 * u), 2 * width // 3), fill=(226, 178, 48, 255))
    d.rectangle((length - int(90 * u), 0, length - int(40 * u), width - 1), fill=(176, 176, 182, 255))
    d.rounded_rectangle((length - int(52 * u), 0, length - 1, width - 1), radius=width // 3,
                        fill=(232, 140, 150, 255))
    d.polygon([(tip, 0), (tip, width - 1), (0, width // 2)], fill=(232, 206, 160, 255))
    d.polygon([(int(tip * 0.32), int(width * 0.34)), (int(tip * 0.32), int(width * 0.66)), (0, width // 2)],
              fill=(60, 56, 60, 255))
    return img


def _scatter(c: Image.Image, ctx: Ctx, imgs: list[Image.Image], area: tuple[float, float, float, float],
             size: float, salt: str, opacity: int = 80) -> None:
    """Place images on a loose grid inside `area`, slightly rotated, with die-cut borders."""
    if not imgs:
        return
    rng = ctx.rng(salt)
    x0, y0, x1, y1 = area
    cols = max(1, round(math.sqrt(len(imgs) * (x1 - x0) / max(y1 - y0, 1))))
    rows = math.ceil(len(imgs) / cols)
    cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
    s = min(size, cw * 0.95, ch * 0.95)
    for i, img in enumerate(imgs):
        r, k = divmod(i, cols)
        cx = x0 + (k + 0.5) * cw + rng.uniform(-0.12, 0.12) * cw
        cy = y0 + (r + 0.5) * ch + rng.uniform(-0.12, 0.12) * ch
        piece = draw.fit(img, (int(s), int(s)))
        if piece.mode == "RGBA":
            piece = draw.die_cut(piece, max(3, int(12 * ctx.u)))
        else:
            piece = draw.paper(piece)
        draw.paste_with_shadow(c, piece, (cx, cy), angle=rng.uniform(-14, 14), blur=16 * ctx.u,
                               offset=(6 * ctx.u, 10 * ctx.u), opacity=opacity)


def _wood(size: tuple[int, int], seed: int) -> Image.Image:
    """Procedural light-oak texture: wavy grain from smoothed noise, no image files.
    Built at a third of the size and scaled up; the grain is soft, so nothing is lost."""
    W, H = size
    w, h = max(8, W // 3), max(8, H // 3)
    rng = np.random.default_rng(seed)
    warp = Image.fromarray((rng.random((max(2, h // 30), max(2, w // 80))) * 255).astype(np.uint8))
    warp = np.asarray(warp.resize((w, h), Image.BICUBIC), np.float32) / 255.0
    yy = np.arange(h, dtype=np.float32)[:, None]
    grain = np.sin(yy / max(h / 160.0, 0.5) + warp * 9.0) * 0.5 + 0.5
    fine = Image.fromarray((rng.random((h, max(2, w // 4))) * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)
    t = grain * 0.55 + np.asarray(fine, np.float32) / 255.0 * 0.15
    light, dark = np.array([214, 174, 128], np.float32), np.array([178, 132, 90], np.float32)
    arr = light * (1 - t[..., None]) + dark * t[..., None]
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB").resize((W, H), Image.BICUBIC)
