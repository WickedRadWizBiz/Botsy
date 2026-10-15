"""Small Pillow drawing helpers for the mockups: fonts, gradients, shadows, text fitting.

Fonts come from the machine (or settings qa.mockups.fonts), never from the network.
The fallback is Pillow's built-in scalable font (Pillow >= 10.1 with FreeType).
"""
from __future__ import annotations

from functools import cache, lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BOLD_FONTS = [
    "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
REGULAR_FONTS = [
    "/usr/share/fonts/opentype/inter/Inter-Medium.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

Color = tuple[int, int, int]


@cache
def _font_path(bold: bool, extra: tuple[str, ...]) -> str | None:
    for p in (*extra, *(BOLD_FONTS if bold else REGULAR_FONTS)):
        if p and Path(p).is_file():
            return p
    return None


@lru_cache(maxsize=256)
def font(size: int, bold: bool = True, extra: tuple[str, ...] = ()) -> ImageFont.ImageFont:
    """A font of `size` px. `extra` paths (settings) are tried before the system list."""
    size = max(6, int(size))
    path = _font_path(bold, extra)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has only the tiny bitmap font
        return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, f) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=f)
    return right - left, bottom - top


def wrap(draw: ImageDraw.ImageDraw, text: str, f, max_w: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and text_size(draw, trial, f)[0] > max_w:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


def fit_text(draw: ImageDraw.ImageDraw, text: str, max_w: int, size: int, *, min_size: int = 12,
             max_lines: int = 2, bold: bool = True, fonts: tuple[str, ...] = ()) -> tuple[object, list[str]]:
    """Largest font (down to min_size) at which `text` wraps into max_lines within max_w."""
    while True:
        f = font(size, bold, fonts)
        lines = wrap(draw, text, f, max_w)
        if (len(lines) <= max_lines and all(text_size(draw, ln, f)[0] <= max_w for ln in lines)) or size <= min_size:
            return f, lines[:max_lines]
        size = max(min_size, int(size * 0.92))


def draw_lines(draw: ImageDraw.ImageDraw, lines: list[str], f, x: int, y: int, fill: Color,
               *, center_w: int | None = None, spacing: float = 1.18) -> int:
    """Draw lines top-down from y (centered in [x, x+center_w] if given). Returns the new y."""
    line_h = int(f.size * spacing) if hasattr(f, "size") else text_size(draw, "Ag", f)[1] + 4
    for ln in lines:
        w, _ = text_size(draw, ln, f)
        lx = x + (center_w - w) // 2 if center_w else x
        draw.text((lx, y), ln, font=f, fill=fill)
        y += line_h
    return y


def gradient(size: tuple[int, int], top: Color, bottom: Color) -> Image.Image:
    w, h = size
    t = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    column = np.array(top, np.float32) * (1 - t) + np.array(bottom, np.float32) * t
    return Image.fromarray(column.round().astype(np.uint8), "RGB").resize((w, h), Image.NEAREST)


def soft_blobs(canvas: Image.Image, color: Color, rng, count: int = 4, alpha: int = 46) -> None:
    """A few large, blurred pastel circles for a calm background."""
    w, h = canvas.size
    layer = Image.new("L", (w // 4, h // 4), 0)
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        r = rng.uniform(0.12, 0.28) * w / 4
        cx, cy = rng.uniform(0, w / 4), rng.uniform(0, h / 4)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=alpha)
    layer = layer.filter(ImageFilter.GaussianBlur(w / 160)).resize((w, h), Image.BILINEAR)
    canvas.paste(Image.new("RGB", (w, h), color), (0, 0), layer)


def paper(img: Image.Image, edge: Color = (222, 222, 222)) -> Image.Image:
    """A page preview as an RGBA sheet of paper with a hairline edge."""
    sheet = img.convert("RGB").copy()
    ImageDraw.Draw(sheet).rectangle((0, 0, sheet.width - 1, sheet.height - 1), outline=edge,
                                    width=max(1, sheet.width // 500))
    return sheet.convert("RGBA")


def fit(img: Image.Image, box: tuple[int, int]) -> Image.Image:
    """Resize to fit inside box, keeping the aspect ratio."""
    scale = min(box[0] / img.width, box[1] / img.height)
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)


def paste_with_shadow(canvas: Image.Image, layer: Image.Image, center: tuple[float, float], *,
                      angle: float = 0.0, blur: float = 18, offset: tuple[float, float] = (10, 16),
                      opacity: int = 90) -> tuple[int, int, int, int]:
    """Rotate an RGBA layer, drop a soft shadow under it and paste it centered at `center`.
    The shadow is blurred at quarter size, which looks the same and is much faster.
    Returns the pasted box."""
    layer = layer.convert("RGBA")
    if angle:
        layer = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    x = round(center[0] - layer.width / 2)
    y = round(center[1] - layer.height / 2)
    small = layer.getchannel("A").resize((max(1, layer.width // 4), max(1, layer.height // 4)), Image.BILINEAR)
    pad = int(blur)  # room for the blur to spread at quarter scale
    mask = Image.new("L", (small.width + 2 * pad, small.height + 2 * pad), 0)
    mask.paste(small, (pad, pad))
    mask = mask.filter(ImageFilter.GaussianBlur(max(1.0, blur / 4)))
    mask = mask.point(lambda v: v * opacity // 255).resize((mask.width * 4, mask.height * 4), Image.BILINEAR)
    sx, sy = x - pad * 4 + round(offset[0]), y - pad * 4 + round(offset[1])
    canvas.paste((40, 32, 28), (sx, sy, sx + mask.width, sy + mask.height), mask)
    canvas.paste(layer, (x, y), layer)
    return x, y, x + layer.width, y + layer.height


def die_cut(sticker: Image.Image, border: int, color: Color = (255, 255, 255)) -> Image.Image:
    """A sticker with a white cut-line border around its alpha shape."""
    import cv2

    s = sticker.convert("RGBA")
    pad = border + 2
    big = Image.new("RGBA", (s.width + 2 * pad, s.height + 2 * pad), (0, 0, 0, 0))
    big.paste(s, (pad, pad), s)
    alpha = np.asarray(big.getchannel("A"))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * border + 1, 2 * border + 1))
    grown = cv2.dilate((alpha > 40).astype(np.uint8) * 255, k)
    grown = cv2.GaussianBlur(grown, (3, 3), 0)
    base = Image.new("RGBA", big.size, color + (0,))
    base.putalpha(Image.fromarray(grown))
    return Image.alpha_composite(base, big)


def rounded_card(canvas: Image.Image, box: tuple[int, int, int, int], fill: Color, radius: int,
                 shadow: bool = True) -> None:
    w, h = box[2] - box[0], box[3] - box[1]
    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=fill + (255,))
    if shadow:
        paste_with_shadow(canvas, card, (box[0] + w / 2, box[1] + h / 2), blur=radius, offset=(0, radius / 3),
                          opacity=55)
    else:
        canvas.paste(card, box[:2], card)
