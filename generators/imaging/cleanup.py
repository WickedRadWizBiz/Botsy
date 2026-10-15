"""Turn raw model output into print-ready art.

Coloring pages: grayscale -> threshold -> despeckle -> thicken thin lines -> pure black and
white -> crop/pad to the page's aspect, then vectorize with potrace (or vtracer binary mode).
Image models often add faint gray shading, specks and hairlines; this step removes them so
the PDF prints crisp and passes QA's minimum line weight.

Stickers: background removal -> crop -> resize -> white die-cut border made by dilating the
alpha mask (the cut line follows the outer silhouette) -> transparent PNG at 300 dpi.
Real runs use rembg; dry runs and tests use a deterministic flood fill from the image
border, so nothing is ever downloaded there.
"""
from __future__ import annotations

import logging
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from generators.imaging.client import ImageGenerationError

log = logging.getLogger(__name__)

# Default rembg model. Pinned on purpose: rembg's own default ("bria-rmbg", BRIA RMBG-2.0)
# is CC BY-NC 4.0, i.e. not licensed for selling products. IS-Net (DIS) is Apache-2.0.
REMBG_MODEL = "isnet-general-use"


def _ellipse(size: int) -> np.ndarray:
    size = max(1, int(size))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


# --- coloring pages ------------------------------------------------------------------------

def ink_mask(img: Image.Image, threshold: int = 180, grayscale: bool = True) -> np.ndarray:
    """True where a pixel is ink. grayscale=False uses the darkest channel, so colored
    lines count as ink too."""
    rgb = np.asarray(img.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY) if grayscale else rgb.min(axis=2)
    return gray < int(threshold)


def despeckle(mask: np.ndarray, max_area: int) -> np.ndarray:
    """Drop ink specks and fill pinholes smaller than max_area pixels."""
    if max_area <= 0:
        return mask
    out = mask.copy()
    for value in (True, False):          # True: ink specks; False: tiny white holes in ink
        n, labels, stats, _ = cv2.connectedComponentsWithStats((out == value).astype(np.uint8), connectivity=8)
        small = np.where(stats[1:, cv2.CC_STAT_AREA] < max_area)[0] + 1
        if len(small):
            out[np.isin(labels, small)] = not value
    return out


def thicken(mask: np.ndarray, min_px: int) -> np.ndarray:
    """Make every stroke at least min_px wide.

    An opening with a min_px disk erases exactly the strokes thinner than the disk; those
    thin parts are dilated by the same disk and added back. Strokes that are already thick
    enough are left alone.
    """
    if min_px <= 1:
        return mask
    ink = mask.astype(np.uint8) * 255
    kernel = _ellipse(min_px)
    opened = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    thin = cv2.subtract(ink, opened)
    return (ink | cv2.dilate(thin, kernel)) > 0


def crop_pad(mask: np.ndarray, aspect: float | None, pad_frac: float = 0.03) -> np.ndarray:
    """Crop to the ink's bounding box plus a margin, then pad with white to width/height = aspect."""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        raise ImageGenerationError("image has no line art after cleanup (blank page)")
    margin = int(round(pad_frac * max(mask.shape)))
    y0, y1 = max(0, ys.min() - margin), min(mask.shape[0], ys.max() + 1 + margin)
    x0, x1 = max(0, xs.min() - margin), min(mask.shape[1], xs.max() + 1 + margin)
    crop = mask[y0:y1, x0:x1]
    if not aspect:
        return crop
    h, w = crop.shape
    if w / h < aspect:
        new_w, new_h = int(round(h * aspect)), h
    else:
        new_w, new_h = w, int(round(w / aspect))
    out = np.zeros((new_h, new_w), dtype=bool)
    oy, ox = (new_h - h) // 2, (new_w - w) // 2
    out[oy:oy + h, ox:ox + w] = crop
    return out


def clean_line_art(img: Image.Image, *, threshold: int = 180, despeckle_px: int = 20, min_line_px: int = 4,
                   min_line_frac: float = 0.0, grayscale: bool = True, aspect: float | None = None,
                   pad_frac: float = 0.03) -> Image.Image:
    """Model output -> pure black-on-white line art ("L" image holding only 0 and 255).

    Lines end up at least max(min_line_px, min_line_frac * final width) pixels wide; the
    fraction lets the caller guarantee a printed line weight whatever the image size.
    Thickening runs after the crop because only then is the final width known.
    """
    mask = ink_mask(img, threshold, grayscale)
    mask = despeckle(mask, int(despeckle_px))
    mask = crop_pad(mask, aspect, pad_frac)
    mask = thicken(mask, min_line_width(mask.shape[1], min_line_px, min_line_frac))
    mask = despeckle(mask, int(despeckle_px))      # thickening can close tiny gaps into pinholes
    return Image.fromarray(np.where(mask, 0, 255).astype(np.uint8), "L")


def min_line_width(width_px: int, min_line_px: int, min_line_frac: float = 0.0) -> int:
    return max(int(min_line_px), math.ceil(min_line_frac * width_px))


def ink_fraction(img: Image.Image) -> float:
    return float((np.asarray(img.convert("L")) < 128).mean())


# --- vectorizing ---------------------------------------------------------------------------

def potrace_path(binary: str = "potrace") -> str | None:
    return shutil.which(binary)


def trace_potrace(img: Image.Image, out_svg: str | Path, *, potrace_bin: str | None = None,
                  turdsize: int = 2, alphamax: float = 1.0, opttolerance: float = 0.2) -> Path:
    """Vectorize black-on-white art with the potrace CLI (PBM in, SVG out, same pixel size)."""
    exe = potrace_bin or potrace_path()
    if not exe:
        raise FileNotFoundError("potrace not found; install it (see requirements.txt) or use tracer vtracer_binary")
    out_svg = Path(out_svg)
    with tempfile.TemporaryDirectory() as tmp:
        pbm = Path(tmp) / "art.pbm"
        img.convert("L").point(lambda v: 255 if v >= 128 else 0).convert("1").save(pbm)
        subprocess.run([exe, "--svg", "--turdsize", str(turdsize), "--alphamax", str(alphamax),
                        "--opttolerance", str(opttolerance), "--output", str(out_svg), str(pbm)],
                       check=True, capture_output=True, timeout=300)
    return out_svg


def trace_vtracer_binary(img: Image.Image, out_svg: str | Path, *, filter_speckle: int = 4,
                         corner_threshold: int = 60, path_precision: int = 8) -> Path:
    """Vectorize black-on-white art with vtracer's binary mode (pure Python wheel, no CLI)."""
    import io

    import vtracer

    buf = io.BytesIO()
    img.convert("L").point(lambda v: 255 if v >= 128 else 0).convert("RGB").save(buf, "PNG")
    svg = vtracer.convert_raw_image_to_svg(buf.getvalue(), img_format="png", colormode="binary",
                                           mode="spline", filter_speckle=filter_speckle,
                                           corner_threshold=corner_threshold, path_precision=path_precision)
    out_svg = Path(out_svg)
    out_svg.write_text(svg, encoding="utf-8")
    return out_svg


def vectorize_line_art(img: Image.Image, out_svg: str | Path, tracer: str = "potrace") -> str:
    """Write out_svg with the configured tracer; potrace falls back to vtracer if missing.
    Returns the tracer actually used."""
    if tracer not in ("potrace", "vtracer_binary"):
        raise ValueError(f"unknown coloring tracer {tracer!r}; use potrace or vtracer_binary")
    if tracer == "potrace" and potrace_path():
        trace_potrace(img, out_svg)
        return "potrace"
    if tracer == "potrace":
        log.warning("potrace not installed; using vtracer binary mode")
    trace_vtracer_binary(img, out_svg)
    return "vtracer_binary"


# --- stickers ------------------------------------------------------------------------------

def remove_background(img: Image.Image, method: str = "rembg", *, rembg_model: str = REMBG_MODEL,
                      tolerance: int = 24) -> Image.Image:
    """RGBA image with the background transparent. method: "rembg" or "floodfill"."""
    if method == "floodfill":
        return remove_background_floodfill(img, tolerance)
    if method == "rembg":
        return remove_background_rembg(img, rembg_model)
    raise ValueError(f"unknown background_removal {method!r}; use rembg or floodfill")


def remove_background_floodfill(img: Image.Image, tolerance: int = 24) -> Image.Image:
    """Make transparent every pixel connected to the image border that is close to the
    border's median color. Enclosed areas of the same color (eye whites) stay opaque."""
    rgb = np.asarray(img.convert("RGB"))
    edge = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(edge, axis=0).astype(np.uint8)
    close = (cv2.absdiff(rgb, np.full_like(rgb, bg)).max(axis=2) <= tolerance).astype(np.uint8)
    n, labels = cv2.connectedComponents(close, connectivity=4)
    is_bg = np.zeros(n, dtype=bool)
    is_bg[np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])] = True
    is_bg[0] = False                     # label 0 = pixels that are not background-colored
    alpha = np.where(is_bg[labels], 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")


_REMBG_SESSIONS: dict[str, object] = {}


def remove_background_rembg(img: Image.Image, model: str = REMBG_MODEL) -> Image.Image:
    """rembg (lazy import). Downloads the model to ~/.u2net on first use."""
    try:
        from rembg import new_session, remove
    except ImportError as exc:
        raise RuntimeError("rembg is not installed; pip install 'rembg[cpu]' or set "
                           "vectorize.stickers.background_removal: floodfill") from exc
    if model not in _REMBG_SESSIONS:
        log.info("loading rembg model %s (downloads on first use)", model)
        _REMBG_SESSIONS[model] = new_session(model)
    out = remove(img.convert("RGB"), session=_REMBG_SESSIONS[model]).convert("RGBA")
    a = np.asarray(out.getchannel("A")).copy()
    a[a < 16] = 0                       # faint halo -> fully transparent
    out.putalpha(Image.fromarray(a, "L"))
    return out


def drop_islands(rgba: Image.Image, min_area: int) -> Image.Image:
    """Clear small detached opaque specks so they do not get their own die-cut blob."""
    a = np.asarray(rgba.getchannel("A")).copy()
    n, labels, stats, _ = cv2.connectedComponentsWithStats((a > 0).astype(np.uint8), connectivity=8)
    small = np.where(stats[1:, cv2.CC_STAT_AREA] < min_area)[0] + 1
    if len(small):
        a[np.isin(labels, small)] = 0
    out = rgba.copy()
    out.putalpha(Image.fromarray(a, "L"))
    return out


def crop_to_content(rgba: Image.Image, pad: int = 0) -> Image.Image:
    bbox = rgba.getchannel("A").point(lambda v: 255 if v > 0 else 0).getbbox()
    if not bbox:
        raise ImageGenerationError("sticker is empty after background removal")
    x0, y0, x1, y1 = bbox
    return rgba.crop((max(0, x0 - pad), max(0, y0 - pad), min(rgba.width, x1 + pad), min(rgba.height, y1 + pad)))


def fit_long_side(img: Image.Image, px: int) -> Image.Image:
    scale = px / max(img.size)
    if abs(scale - 1) < 1e-6:
        return img
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(size, Image.LANCZOS)


def add_die_cut_border(rgba: Image.Image, border_px: int = 30, color: tuple[int, int, int] = (255, 255, 255),
                       *, shadow: bool = False, shadow_offset_px: int = 8, shadow_blur_px: int = 10,
                       shadow_opacity: float = 0.3) -> Image.Image:
    """Put a solid border of border_px around the subject's outer silhouette (holes filled),
    with smooth anti-aliased edges. Optional soft drop shadow behind it."""
    extra = (shadow_offset_px + 2 * shadow_blur_px) if shadow else 0
    pad = border_px + extra + 4            # room for the anti-aliased edge
    w, h = rgba.width + 2 * pad, rgba.height + 2 * pad
    subject = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    subject.paste(rgba, (pad, pad))
    mask = (np.asarray(subject.getchannel("A")) > 127).astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    solid = np.zeros_like(mask)
    cv2.drawContours(solid, contours, -1, 255, thickness=cv2.FILLED)
    outline = cv2.dilate(solid, _ellipse(2 * border_px + 1)) if border_px > 0 else solid
    if border_px > 2:                      # round off the dilation's jaggies
        blurred = _blur(outline, border_px / 4)
        outline = np.maximum(np.where(blurred >= 128, 255, 0).astype(np.uint8), solid)
    soft = _blur(outline, 0.8)

    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if shadow:
        sh = np.zeros_like(soft)
        o = shadow_offset_px
        sh[o:, o:] = soft[:h - o, :w - o] if o else soft
        sh = _blur(sh, max(0.1, shadow_blur_px / 2))
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.putalpha(Image.fromarray((sh * float(shadow_opacity)).astype(np.uint8), "L"))
        base = Image.alpha_composite(base, layer)
    border = Image.new("RGBA", (w, h), (*color, 0))
    border.putalpha(Image.fromarray(soft, "L"))
    return Image.alpha_composite(Image.alpha_composite(base, border), subject)


def count_pieces(rgba: Image.Image, min_alpha: int = 128) -> int:
    """Separate opaque shapes in a finished sticker. More than one means the die-cut border
    did not join the parts (e.g. "two cherries" drawn far apart), so a cutter would cut
    several small stickers instead of one."""
    mask = (np.asarray(rgba.getchannel("A")) >= min_alpha).astype(np.uint8)
    n, _ = cv2.connectedComponents(mask, connectivity=8)
    return n - 1


def _blur(mask: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur that treats everything outside the canvas as empty (the default
    reflected border would drag the outline out to the canvas edge)."""
    return cv2.GaussianBlur(mask, (0, 0), sigma, borderType=cv2.BORDER_CONSTANT)


def save_png(img: Image.Image, path: str | Path, dpi: int = 300) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", dpi=(dpi, dpi))
    return path


def on_background(rgba: Image.Image, kind: str = "checker", cell: int = 24) -> Image.Image:
    """Composite a transparent sticker on a light checkerboard (or white) for previews."""
    if kind == "white":
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    else:
        yy, xx = np.mgrid[0:rgba.height, 0:rgba.width]
        tiles = ((yy // cell + xx // cell) % 2).astype(bool)
        arr = np.where(tiles[..., None], 236, 255).astype(np.uint8).repeat(3, axis=2)
        bg = Image.fromarray(np.dstack([arr, np.full(tiles.shape, 255, np.uint8)]), "RGBA")
    return Image.alpha_composite(bg, rgba.convert("RGBA")).convert("RGB")
