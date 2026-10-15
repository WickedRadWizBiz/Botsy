"""Line-art cleanup, vectorizing, sticker background removal and die-cut border."""
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PIL import Image, ImageDraw

from generators.imaging import cleanup
from generators.imaging.client import FakeImageClient, ImageGenerationError
from generators.imaging.pdf import load_svg


def line_art(size=(300, 400)) -> Image.Image:
    """A thick circle, a 1 px hairline, gray anti-aliasing, faint shading and specks."""
    img = Image.new("L", size, 255)
    d = ImageDraw.Draw(img)
    d.ellipse([60, 80, 240, 260], outline=0, width=8)
    d.line([(40, 330), (260, 330)], fill=0, width=1)          # hairline
    d.rectangle([100, 120, 140, 160], fill=210)               # faint gray shading
    for x, y in [(20, 20), (280, 30), (150, 380)]:
        d.point((x, y), fill=0)                               # specks
    d.line([(60, 300), (240, 300)], fill=120, width=6)        # mid-gray line counts as ink
    return img.convert("RGB")


def stroke_thickness(mask_col: np.ndarray) -> list[int]:
    """Lengths of ink runs along one column."""
    runs, n = [], 0
    for v in mask_col:
        if v:
            n += 1
        elif n:
            runs.append(n)
            n = 0
    return runs + ([n] if n else [])


def test_clean_line_art_is_pure_black_and_white_with_thick_lines():
    out = cleanup.clean_line_art(line_art(), threshold=180, despeckle_px=20, min_line_px=5, aspect=0.75)
    arr = np.asarray(out)
    assert out.mode == "L" and set(np.unique(arr)) <= {0, 255}
    assert abs(out.width / out.height - 0.75) < 0.01
    ink = arr == 0
    # every ink run crossing the hairline column is at least min_line_px thick
    col = ink[:, out.width // 2]
    assert stroke_thickness(col) and min(stroke_thickness(col)) >= 5
    # specks are gone: no ink component smaller than despeckle_px
    import cv2
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    assert stats[1:, cv2.CC_STAT_AREA].min() >= 20
    # light gray shading (210 > threshold) is dropped, not turned black
    assert cleanup.ink_fraction(out) < 0.2


def test_min_line_fraction_scales_with_width():
    assert cleanup.min_line_width(3000, 4, 1.5 / 523) == 9
    assert cleanup.min_line_width(500, 4, 1.5 / 523) == 4


def test_blank_image_is_rejected():
    with pytest.raises(ImageGenerationError, match="blank"):
        cleanup.clean_line_art(Image.new("RGB", (100, 100), "white"))


def test_fake_coloring_output_cleans_up():
    raw = FakeImageClient("coloring", long_side_px=480).generate("a cow", aspect_ratio="3:4", image_size="4K")
    out = cleanup.clean_line_art(raw, min_line_px=4, aspect=0.75)
    assert set(np.unique(np.asarray(out))) <= {0, 255} and 0.01 < cleanup.ink_fraction(out) < 0.3


def _check_svg(path):
    root = ET.parse(path).getroot()
    assert root.tag.endswith("svg")
    assert any(el.tag.endswith("path") for el in root.iter())
    drawing = load_svg(path)
    assert drawing is not None and drawing.width > 0


@pytest.mark.skipif(not cleanup.potrace_path(), reason="potrace not installed")
def test_potrace_produces_valid_svg(tmp_path):
    art = cleanup.clean_line_art(line_art(), min_line_px=4)
    out = cleanup.trace_potrace(art, tmp_path / "page.svg")
    _check_svg(out)
    root = ET.parse(out).getroot()
    assert root.get("viewBox").split()[2:] == [f"{art.width:.6f}", f"{art.height:.6f}"]
    assert cleanup.vectorize_line_art(art, tmp_path / "v.svg", "potrace") == "potrace"


def test_vtracer_binary_produces_valid_svg(tmp_path):
    art = cleanup.clean_line_art(line_art(), min_line_px=4)
    _check_svg(cleanup.trace_vtracer_binary(art, tmp_path / "page.svg"))


def test_falls_back_to_vtracer_without_potrace(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "potrace_path", lambda binary="potrace": None)
    art = cleanup.clean_line_art(line_art(), min_line_px=4)
    assert cleanup.vectorize_line_art(art, tmp_path / "page.svg", "potrace") == "vtracer_binary"
    _check_svg(tmp_path / "page.svg")


def sticker_art() -> Image.Image:
    img = Image.new("RGB", (200, 200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([50, 50, 150, 150], fill=(240, 80, 80), outline=(20, 20, 20), width=5)
    d.ellipse([80, 80, 100, 100], fill=(255, 255, 255), outline=(20, 20, 20), width=2)   # enclosed white
    return img


def test_floodfill_background_removal_keeps_subject():
    rgba = cleanup.remove_background(sticker_art(), "floodfill")
    a = np.asarray(rgba.getchannel("A"))
    assert rgba.mode == "RGBA"
    assert (a[0, :] == 0).all() and (a[-1, :] == 0).all() and (a[:, 0] == 0).all() and (a[:, -1] == 0).all()
    assert a[100, 100] == 255 and a[60, 100] == 255          # subject kept
    assert a[90, 90] == 255                                   # enclosed white "eye" is not background


def test_unknown_background_method_raises():
    with pytest.raises(ValueError):
        cleanup.remove_background(sticker_art(), "magic")


def test_die_cut_border_surrounds_subject():
    rgba = cleanup.crop_to_content(cleanup.remove_background(sticker_art(), "floodfill"))
    border = 12
    out = cleanup.add_die_cut_border(rgba, border)
    pad = (out.width - rgba.width) // 2
    assert pad >= border and out.size == (rgba.width + 2 * pad, rgba.height + 2 * pad)
    a = np.asarray(out.getchannel("A"))
    rgb = np.asarray(out.convert("RGB"))
    cy = out.height // 2
    x_edge = pad                                          # leftmost subject column at mid-height
    ring = (cy, x_edge - border // 2)                     # inside the border ring
    assert a[ring] == 255 and tuple(rgb[ring]) == (255, 255, 255)
    assert a[cy, 0] == 0 and a[0, 0] == 0                 # outside the ring stays transparent
    assert a[cy, out.width // 2] == 255                   # subject still opaque


def test_die_cut_shadow_is_optional():
    rgba = cleanup.crop_to_content(cleanup.remove_background(sticker_art(), "floodfill"))
    plain = np.asarray(cleanup.add_die_cut_border(rgba, 8).getchannel("A"))
    shaded = cleanup.add_die_cut_border(rgba, 8, shadow=True, shadow_offset_px=6, shadow_blur_px=4)
    a = np.asarray(shaded.getchannel("A"))
    assert ((a > 0) & (a < 255)).sum() > ((plain > 0) & (plain < 255)).sum()


def test_drop_islands_and_crop():
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 70, 70], fill=(255, 0, 0, 255))
    d.point((5, 5), fill=(0, 0, 0, 255))
    out = cleanup.crop_to_content(cleanup.drop_islands(img, 10))
    assert out.size == (31, 31)


def test_png_saved_at_300_dpi(tmp_path):
    p = cleanup.save_png(Image.new("RGBA", (10, 10)), tmp_path / "s.png", dpi=300)
    dpi = Image.open(p).info["dpi"]
    assert round(dpi[0]) == 300 and round(dpi[1]) == 300


def test_preview_backgrounds():
    rgba = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    checker = cleanup.on_background(rgba, "checker", cell=10)
    assert checker.mode == "RGB" and checker.getpixel((5, 5)) != checker.getpixel((15, 5))
    assert cleanup.on_background(rgba, "white").getpixel((5, 5)) == (255, 255, 255)


def test_every_stroke_reaches_min_width_in_any_direction():
    """Diagonal and curved hairlines too: an opening with a min_px disk keeps (almost)
    every ink pixel only if every stroke is at least min_px wide."""
    import cv2

    img = Image.new("L", (400, 400), 255)
    d = ImageDraw.Draw(img)
    d.line([(30, 30), (370, 300)], fill=0, width=1)                 # diagonal hairline
    d.arc([60, 60, 340, 340], 200, 340, fill=0, width=2)            # thin curve
    d.ellipse([150, 150, 250, 250], outline=0, width=12)            # already thick
    min_px = 7
    out = cleanup.clean_line_art(img.convert("RGB"), min_line_px=min_px, despeckle_px=20)
    ink = (np.asarray(out) == 0).astype(np.uint8)
    kept = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (min_px, min_px)))
    assert ink.sum() > 0 and (ink & ~kept.astype(bool)).sum() / ink.sum() < 0.01


def test_count_pieces_spots_stickers_that_fall_apart():
    one = Image.new("RGBA", (120, 60), (0, 0, 0, 0))
    ImageDraw.Draw(one).ellipse([10, 10, 50, 50], fill=(200, 0, 0, 255))
    assert cleanup.count_pieces(one) == 1
    two = one.copy()
    ImageDraw.Draw(two).ellipse([70, 10, 110, 50], fill=(200, 0, 0, 255))
    assert cleanup.count_pieces(two) == 2
    joined = cleanup.add_die_cut_border(two, 15)                    # border bridges a 20 px gap
    assert cleanup.count_pieces(joined) == 1
