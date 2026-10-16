"""Image model clients: the real Gemini client, and a deterministic fake for dry runs and tests.

Both implement `ImageClient.generate(prompt, *, aspect_ratio, image_size) -> PIL.Image` (RGB).
Generators never import google-genai themselves; they get a client from `run_client()` or
from their `client=` argument, so tests and dry runs never touch the network.

Verified against google-genai 2.31.0 (by introspection):
- request: `client.models.generate_content(model=..., contents=prompt,
  config=types.GenerateContentConfig(response_modalities=[...],
  image_config=types.ImageConfig(aspect_ratio="3:4", image_size="4K")))`.
- response: `response.parts` (first candidate) holds `Part.inline_data` (`Blob.data` bytes,
  `Blob.mime_type`); parts with `Part.thought` true are interim "thinking" images.
- the SDK does not retry unless `HttpOptions.retry_options` is set, so the backoff here is
  the only retry layer. `HttpOptions.timeout` is in milliseconds.
- `response.usage_metadata` (GenerateContentResponseUsageMetadata): prompt_token_count,
  thoughts_token_count, candidates_token_count and candidates_tokens_details (per modality).

Spending (docs/SERVER.md section 10): GeminiImageClient calls core.spend.check_budget("image")
before every request (its BudgetExceeded is never retried) and records each answered request
once in the spend ledger. FakeImageClient never checks or records.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import math
import os
import random
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from PIL import Image, ImageDraw, ImageFilter

from core import spend
from core.config import require_env, section

log = logging.getLogger(__name__)

# Settings section "imaging" (see the YAML block reported for config/settings.yaml).
DEFAULTS: dict = {
    "max_images_per_run": 80,          # hard cost cap per process (CLI run / pipeline run)
    "max_retries": 5,                  # retries after the first attempt, on 408/429/5xx/network
    "backoff_base_s": 2.0,             # delay = base * 2**attempt, capped, plus jitter
    "backoff_max_s": 60.0,
    "backoff_jitter": 0.25,            # +0..25% random jitter so parallel jobs spread out
    "timeout_s": 300,                  # per request; 4K images can take a while
    "response_modalities": ["IMAGE"],  # image only: we never use the model's text
    "vertex_location": "global",       # used when GOOGLE_CLOUD_LOCATION is not set
    "fake_long_side_px": 1200,         # FakeImageClient output size (keeps dry runs fast)
}

TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class ImageCapExceeded(RuntimeError):
    """The per-run image cap (imaging.max_images_per_run) would be exceeded."""


class ImageGenerationError(RuntimeError):
    """The model answered but returned no usable image (safety block, text only, blank)."""


@runtime_checkable
class ImageClient(Protocol):
    model: str

    def generate(self, prompt: str, *, aspect_ratio: str, image_size: str) -> Image.Image:
        """Return one RGB image for the prompt."""
        ...


class _Budget:
    """Counts billable images so a run can never exceed its cap, however many briefs it has."""

    def __init__(self, max_images: int | None):
        self.max_images = max_images
        self.used = 0

    @property
    def remaining(self) -> int | None:
        return None if self.max_images is None else max(0, self.max_images - self.used)

    def check(self) -> None:
        if self.max_images is not None and self.used >= self.max_images:
            raise ImageCapExceeded(f"image cap reached ({self.used}/{self.max_images} this run); "
                                   "raise imaging.max_images_per_run to allow more")


class GeminiImageClient(_Budget):
    """Google Gemini image model via `generate_content`.

    The google-genai SDK client is created on the first call, so building this object needs
    no secrets. `sdk_client` and `sleep` are injectable for tests.

    Spend tracking uses `settings` (cap and ledger; None = config/settings.yaml). Set
    `item_id` before a pack's images so the ledger can name the item they were for.
    """

    stage = "generate"   # pipeline stage named in the spend ledger

    def __init__(self, model: str, *, api: str = "gemini", cfg: dict | None = None,
                 sdk_client=None, sleep: Callable[[float], None] = time.sleep,
                 rng: random.Random | None = None, settings: dict | None = None):
        cfg = {**DEFAULTS, **(cfg or {})}
        super().__init__(cfg["max_images_per_run"])
        if api not in ("gemini", "vertex"):
            raise ValueError(f"models.image.api must be 'gemini' or 'vertex', not {api!r}")
        self.model = model
        self.api = api
        self.cfg = cfg
        self._sdk = sdk_client
        self._sleep = sleep
        self._rng = rng or random.Random()
        self.settings = settings
        self.item_id: str | None = None

    @classmethod
    def from_settings(cls, settings: dict, **kwargs) -> GeminiImageClient:
        image = settings.get("models", {}).get("image", {})
        provider = image.get("provider", "google_gemini")
        if provider != "google_gemini":
            raise ValueError(f"unsupported image provider {provider!r}; this client is google_gemini")
        if not image.get("model"):
            raise ValueError("settings models.image.model is not set")
        return cls(image["model"], api=image.get("api", "gemini"),
                   cfg=section(settings, "imaging", DEFAULTS), settings=settings, **kwargs)

    def _client(self):
        if self._sdk is None:
            from google import genai
            from google.genai import types

            http = types.HttpOptions(timeout=int(float(self.cfg["timeout_s"]) * 1000))
            if self.api == "vertex":
                project = require_env("GOOGLE_CLOUD_PROJECT")
                location = os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip() or self.cfg["vertex_location"]
                # `enterprise` replaces the legacy `vertexai` flag (google-genai >= 2.31, the floor).
                self._sdk = genai.Client(enterprise=True, project=project, location=location, http_options=http)
            else:
                self._sdk = genai.Client(api_key=require_env("GOOGLE_API_KEY"), http_options=http)
        return self._sdk

    def build_config(self, aspect_ratio: str, image_size: str):
        from google.genai import types

        return types.GenerateContentConfig(
            response_modalities=list(self.cfg["response_modalities"]),
            image_config=types.ImageConfig(aspect_ratio=aspect_ratio, image_size=image_size),
        )

    def generate(self, prompt: str, *, aspect_ratio: str, image_size: str) -> Image.Image:
        config = self.build_config(aspect_ratio, image_size)
        retries = int(self.cfg["max_retries"])
        for attempt in range(retries + 1):
            self.check()
            spend.check_budget("image", settings=self.settings)  # before every paid request, never retried
            log.info("image request model=%s api=%s aspect=%s size=%s attempt=%d prompt=%r",
                     self.model, self.api, aspect_ratio, image_size, attempt + 1, prompt)
            try:
                response = self._client().models.generate_content(model=self.model, contents=prompt,
                                                                  config=config)
            except Exception as exc:
                if not is_transient(exc) or attempt == retries:
                    raise
                delay = self.backoff(attempt)
                log.warning("image request failed (%s); retry %d/%d in %.1fs", _describe(exc),
                            attempt + 1, retries, delay)
                self._sleep(delay)
                continue
            self.used += 1          # an answered request is billable, image or not
            try:
                image = image_from_response(response)
            except ImageGenerationError:
                # The tokens are still billed, and so is an image the model made that we
                # cannot use (unreadable, or only interim thought images).
                self.record_spend(response, image_size, images=None)
                raise
            self.record_spend(response, image_size, images=1)
            return image
        raise AssertionError("unreachable")

    def record_spend(self, response, image_size: str, *, images: int | None) -> None:
        """One spend-ledger line for an answered request. `images` None means "work it out":
        1 when the model drew an image we cannot use (see made_an_image), else 0. Nothing is
        written when there is nothing to bill (no image and no usage_metadata tokens). Never
        raises: the image is paid for, and losing it over a ledger problem would only pay for
        another one. At most one image is counted per request, at the price for image_size."""
        try:
            if images is None:
                images = 1 if made_an_image(response) else 0
            tokens = usage_tokens(response, images)
            if not images and not any(tokens.values()):
                return
            spend.record("google", self.model, "image", stage=self.stage, item_id=self.item_id, images=images,
                         image_size=image_size, settings=self.settings, **tokens)
        except Exception:  # never lose a paid image over the ledger (see docstring)
            log.exception("could not add the image request to the spend ledger")

    def backoff(self, attempt: int) -> float:
        base = min(float(self.cfg["backoff_max_s"]), float(self.cfg["backoff_base_s"]) * 2 ** attempt)
        return base * (1 + self._rng.random() * float(self.cfg["backoff_jitter"]))


def usage_tokens(response, images: int) -> dict:
    """{"input_tokens", "output_tokens"} from `usage_metadata` (zeros when it is missing).
    Image output tokens are left out, since the per-image price covers them: output_tokens is
    thinking plus any text. Without a per-modality breakdown, the candidate tokens count as
    image tokens when an image came back, and as text when none did."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return {"input_tokens": 0, "output_tokens": 0}
    details = getattr(meta, "candidates_tokens_details", None) or []
    if details:
        text = sum(_n(getattr(d, "token_count", None)) for d in details
                   if _enum_name(getattr(d, "modality", None)).upper() != "IMAGE")
    else:
        text = 0 if images else _n(getattr(meta, "candidates_token_count", None))
    return {"input_tokens": _n(getattr(meta, "prompt_token_count", None)),
            "output_tokens": _n(getattr(meta, "thoughts_token_count", None)) + text}


def made_an_image(response) -> bool:
    """True when the response holds image data (usable or not) or reports image output
    tokens: the model drew something, so the request is priced as one image."""
    for p in getattr(response, "parts", None) or []:
        blob = getattr(p, "inline_data", None)
        if blob is not None and (getattr(blob, "mime_type", None) or "image/").startswith("image/"):
            return True
    details = getattr(getattr(response, "usage_metadata", None), "candidates_tokens_details", None) or []
    return any(_enum_name(getattr(d, "modality", None)).upper() == "IMAGE" and _n(getattr(d, "token_count", None))
               for d in details)


def _n(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def is_transient(exc: BaseException) -> bool:
    """Rate limits, server errors and dropped connections are worth retrying; 4xx are not."""
    if _status(exc) in TRANSIENT_STATUS:
        return True
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    try:
        import httpx
    except ImportError:          # pragma: no cover - httpx ships with google-genai
        return False
    return isinstance(exc, httpx.TransportError)


def _status(exc: BaseException) -> int | None:
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def _describe(exc: BaseException) -> str:
    status = _status(exc)
    return f"{type(exc).__name__} {status}" if status else type(exc).__name__


def image_from_response(response) -> Image.Image:
    """The final image of a generate_content response, as RGB.

    Thought parts are interim drafts; the last non-thought image part is the answer. A
    response with only drafts, no image, or bytes Pillow cannot decode raises
    ImageGenerationError, so the generator can use a spare subject. The model's own text
    is logged but never put in the exception: that message lands in manifest.json, and a
    refusal that names a brand would make QA's blocklist check fail the whole item.
    """
    parts = getattr(response, "parts", None) or []
    images = [p for p in parts if getattr(p, "inline_data", None) is not None
              and (p.inline_data.mime_type or "image/").startswith("image/")]
    final = [p for p in images if not getattr(p, "thought", False)]
    if not final:
        texts = [p.text for p in parts if getattr(p, "text", None)]
        if texts:
            log.warning("model answered with text instead of an image: %r", texts[0][:300])
        why = "only interim thought images" if images else _why_empty(response, bool(texts))
        raise ImageGenerationError(f"model returned no image ({why})")
    data = final[-1].inline_data.data
    if isinstance(data, str):
        data = base64.b64decode(data)
    try:
        img = Image.open(io.BytesIO(data or b""))
        img.load()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageGenerationError(f"model returned an unreadable image ({type(exc).__name__})") from exc
    return img.convert("RGB")


def _why_empty(response, has_text: bool = False) -> str:
    bits = []
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        bits.append(f"block_reason={_enum_name(feedback.block_reason)}")
    for cand in getattr(response, "candidates", None) or []:
        if getattr(cand, "finish_reason", None):
            bits.append(f"finish_reason={_enum_name(cand.finish_reason)}")
    if has_text:
        bits.append("text only")
    return ", ".join(bits) or "empty response"


def _enum_name(value) -> str:
    return getattr(value, "name", None) or str(value)


# --- one client per run --------------------------------------------------------------------

_RUN_CLIENTS: dict[tuple, GeminiImageClient] = {}


def run_client(settings: dict) -> GeminiImageClient:
    """The process-wide Gemini client, so the image cap spans every brief in a run."""
    image = settings.get("models", {}).get("image", {})
    key = (image.get("model"), image.get("api", "gemini"))
    if key not in _RUN_CLIENTS:
        _RUN_CLIENTS[key] = GeminiImageClient.from_settings(settings)
    client = _RUN_CLIENTS[key]
    client.settings = settings      # spend cap and ledger follow the caller's current settings
    return client


def reset_run_clients() -> None:
    _RUN_CLIENTS.clear()


# --- fake client ---------------------------------------------------------------------------

STICKER_COLORS = [(239, 83, 80), (255, 167, 38), (255, 213, 79), (102, 187, 106), (66, 165, 245),
                  (171, 71, 188), (236, 64, 122), (38, 198, 218), (141, 110, 99)]


class FakeImageClient(_Budget):
    """Draws simple deterministic art from a hash of the prompt. No network, no secrets.

    style "coloring": black outlines on white, with a little anti-aliasing gray, a thin
    detail line and a few specks so the cleanup step has real work to do.
    style "sticker": a colorful outlined shape with white "eyes" on a plain white background.
    style "auto" picks "sticker" when the prompt mentions a sticker.
    """

    model = "fake-image"

    def __init__(self, style: str = "auto", *, long_side_px: int = DEFAULTS["fake_long_side_px"],
                 max_images: int | None = None):
        super().__init__(max_images)
        if style not in ("auto", "coloring", "sticker"):
            raise ValueError(f"unknown fake style {style!r}")
        self.style = style
        self.long_side_px = long_side_px
        self.calls: list[dict] = []

    def generate(self, prompt: str, *, aspect_ratio: str, image_size: str) -> Image.Image:
        self.check()
        self.calls.append({"prompt": prompt, "aspect_ratio": aspect_ratio, "image_size": image_size})
        self.used += 1
        size = size_for_aspect(aspect_ratio, self.long_side_px)
        rng = random.Random(int.from_bytes(hashlib.sha256(prompt.encode()).digest()[:8], "big"))
        style = self.style
        if style == "auto":
            style = "sticker" if "sticker" in prompt.lower() else "coloring"
        return _fake_sticker(size, rng) if style == "sticker" else _fake_coloring(size, rng)


def size_for_aspect(aspect_ratio: str, long_side: int) -> tuple[int, int]:
    w, h = (float(x) for x in aspect_ratio.split(":"))
    if w >= h:
        return long_side, max(1, round(long_side * h / w))
    return max(1, round(long_side * w / h)), long_side


def _fake_coloring(size: tuple[int, int], rng: random.Random) -> Image.Image:
    w, h = size
    img = Image.new("L", size, 255)
    d = ImageDraw.Draw(img)
    lw = max(3, w // 90)
    cx, cy = w / 2, h * 0.55
    bw, bh = w * rng.uniform(0.25, 0.32), h * rng.uniform(0.16, 0.22)
    d.ellipse([cx - bw, cy - bh, cx + bw, cy + bh], outline=0, width=lw)              # body
    r = w * rng.uniform(0.10, 0.14)
    hx, hy = cx + rng.choice([-1, 1]) * bw * 0.7, cy - bh - r * 0.6
    d.ellipse([hx - r, hy - r, hx + r, hy + r], fill=255, outline=0, width=lw)        # head
    for ex in (-0.35, 0.35):
        d.ellipse([hx + ex * r - lw, hy - r * 0.2 - lw, hx + ex * r + lw, hy - r * 0.2 + lw], fill=0)
    for i in range(rng.randint(2, 4)):                                                # legs / props
        x = cx - bw * 0.6 + i * bw * 0.4
        d.rectangle([x, cy + bh * 0.8, x + w * 0.04, cy + bh + h * 0.08], fill=255, outline=0, width=lw)
    sun_r = w * 0.07
    sx, sy = rng.uniform(0.15, 0.3) * w, h * 0.14
    d.ellipse([sx - sun_r, sy - sun_r, sx + sun_r, sy + sun_r], outline=0, width=lw)
    d.line([(w * 0.08, h * 0.86), (w * 0.92, h * 0.86)], fill=0, width=lw)            # ground
    d.line([(w * 0.2, h * 0.9), (w * 0.8, h * 0.9)], fill=60, width=1)                # thin detail
    for _ in range(12):                                                               # specks
        x, y = rng.uniform(0.05, 0.95) * w, rng.uniform(0.05, 0.95) * h
        d.ellipse([x, y, x + 2, y + 2], fill=0)
    return img.filter(ImageFilter.GaussianBlur(0.8)).convert("RGB")                   # soft edges


def _fake_sticker(size: tuple[int, int], rng: random.Random) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    lw = max(3, w // 70)
    color = rng.choice(STICKER_COLORS)
    cx, cy, r = w / 2, h / 2, min(w, h) * rng.uniform(0.24, 0.3)
    shape = rng.choice(["circle", "star", "blob"])
    if shape == "circle":
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color, outline=(30, 30, 30), width=lw)
    else:
        n = 5 if shape == "star" else 9
        inner = 0.5 if shape == "star" else 0.85
        pts = []
        for i in range(n * 2):
            rad = r if i % 2 == 0 else r * inner
            a = math.pi * i / n - math.pi / 2
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        d.polygon(pts, fill=color, outline=(30, 30, 30), width=lw)
    er = r * 0.16
    for ex in (-0.3, 0.3):                    # white eyes: enclosed white must survive bg removal
        x, y = cx + ex * r, cy - r * 0.1
        d.ellipse([x - er, y - er, x + er, y + er], fill=(255, 255, 255), outline=(30, 30, 30), width=lw // 2)
        d.ellipse([x - er / 3, y - er / 3, x + er / 3, y + er / 3], fill=(30, 30, 30))
    return img
