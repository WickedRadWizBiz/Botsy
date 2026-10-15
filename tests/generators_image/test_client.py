"""GeminiImageClient against a mocked google-genai client (real SDK types, no network),
retry/backoff, the per-run image cap, and the deterministic fake client."""
import io

import pytest
from google.genai import errors, types
from PIL import Image

import core.config
from core.config import MissingSecretError
from generators.imaging import client as client_mod
from generators.imaging.client import (
    FakeImageClient, GeminiImageClient, ImageCapExceeded, ImageGenerationError, image_from_response,
)

NO_JITTER = {"backoff_jitter": 0.0, "backoff_base_s": 2.0, "backoff_max_s": 60.0}


def png_bytes(size=(30, 40), color=(255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def response_with(*parts) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=list(parts)), finish_reason=types.FinishReason.STOP)])


def image_part(data: bytes, thought: bool = False) -> types.Part:
    return types.Part(inline_data=types.Blob(data=data, mime_type="image/png"), thought=thought or None)


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


class FakeSDK:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)


def make_client(outcomes, sleeps=None, **cfg):
    sdk = FakeSDK(outcomes)
    sleeps = [] if sleeps is None else sleeps
    c = GeminiImageClient("gemini-nano-banana-2.1", api="gemini", cfg={**NO_JITTER, **cfg}, sdk_client=sdk,
                          sleep=sleeps.append)
    return c, sdk, sleeps


def test_builds_generate_content_request_and_parses_inline_image():
    final = png_bytes((30, 40))
    draft = png_bytes((10, 10), (0, 255, 0))
    resp = response_with(types.Part(text="here you go"), image_part(draft, thought=True), image_part(final))
    c, sdk, _ = make_client([resp])

    img = c.generate("a cow coloring page", aspect_ratio="3:4", image_size="4K")

    assert img.size == (30, 40) and img.mode == "RGB"           # final image, not the thought draft
    (call,) = sdk.models.calls
    assert call["model"] == "gemini-nano-banana-2.1"
    assert call["contents"] == "a cow coloring page"
    config = call["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.response_modalities == ["IMAGE"]
    assert config.image_config.aspect_ratio == "3:4"
    assert config.image_config.image_size == "4K"
    assert c.used == 1


def test_from_settings_reads_models_image(base_settings):
    c = GeminiImageClient.from_settings(base_settings)
    image = base_settings["models"]["image"]
    assert c.model == image["model"] and c.api == image["api"]
    with pytest.raises(ValueError, match="unsupported image provider"):
        GeminiImageClient.from_settings({"models": {"image": {"provider": "other", "model": "x"}}})


def test_sdk_client_gets_api_key_from_env(monkeypatch):
    created = {}

    class Recorder:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self.models = FakeModels([response_with(image_part(png_bytes()))])

    import google.genai
    monkeypatch.setattr(google.genai, "Client", Recorder)
    monkeypatch.setattr(core.config, "load_env", lambda: None)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    c = GeminiImageClient("m", api="gemini")
    c.generate("p", aspect_ratio="1:1", image_size="2K")
    assert created["api_key"] == "test-key"
    assert created["http_options"].timeout == 300_000                  # milliseconds

    created.clear()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    GeminiImageClient("m", api="vertex").generate("p", aspect_ratio="1:1", image_size="2K")
    assert created["enterprise"] is True and "vertexai" not in created
    assert created["project"] == "proj" and created["location"] == "global"


def test_missing_key_raises_before_any_request(monkeypatch):
    monkeypatch.setattr(core.config, "load_env", lambda: None)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(MissingSecretError, match="GOOGLE_API_KEY"):
        GeminiImageClient("m").generate("p", aspect_ratio="1:1", image_size="1K")


def test_retries_rate_limits_and_server_errors_with_exponential_backoff():
    outcomes = [
        errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}),
        errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}}),
        ConnectionError("reset"),
        response_with(image_part(png_bytes())),
    ]
    c, sdk, sleeps = make_client(outcomes)
    c.generate("p", aspect_ratio="1:1", image_size="2K")
    assert sleeps == [2.0, 4.0, 8.0]
    assert len(sdk.models.calls) == 4
    assert c.used == 1                       # failed attempts are not billed against the cap


def test_backoff_is_capped_and_jittered():
    c, _, _ = make_client([], backoff_base_s=10, backoff_max_s=30, backoff_jitter=0.25)
    assert c.backoff(0) >= 10 and c.backoff(0) <= 12.5
    assert 30 <= c.backoff(5) <= 37.5


def test_gives_up_after_max_retries():
    boom = errors.ServerError(500, {"error": {"code": 500, "message": "x", "status": "INTERNAL"}})
    c, sdk, sleeps = make_client([boom] * 3, max_retries=2)
    with pytest.raises(errors.ServerError):
        c.generate("p", aspect_ratio="1:1", image_size="2K")
    assert len(sdk.models.calls) == 3 and len(sleeps) == 2


def test_client_errors_are_not_retried():
    bad = errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})
    c, sdk, sleeps = make_client([bad])
    with pytest.raises(errors.ClientError):
        c.generate("p", aspect_ratio="1:1", image_size="2K")
    assert len(sdk.models.calls) == 1 and sleeps == []


def test_text_only_or_blocked_response_raises_generation_error():
    blocked = types.GenerateContentResponse(candidates=[types.Candidate(
        content=None, finish_reason=types.FinishReason.IMAGE_SAFETY)])
    c, _, _ = make_client([response_with(types.Part(text="I can't draw Mickey Mouse")), blocked])
    with pytest.raises(ImageGenerationError, match="text only") as info:
        c.generate("p", aspect_ratio="1:1", image_size="2K")
    # the model's own words never reach the message (it is stored in manifest.json, and QA
    # would fail the item for a brand named in a refusal)
    assert "Mickey" not in str(info.value)
    with pytest.raises(ImageGenerationError, match="finish_reason=IMAGE_SAFETY"):
        c.generate("p", aspect_ratio="1:1", image_size="2K")
    assert c.used == 2                       # answered requests count toward the cap


def test_thought_only_response_is_not_used_as_the_final_image():
    resp = response_with(image_part(png_bytes((10, 10)), thought=True))
    with pytest.raises(ImageGenerationError, match="thought"):
        image_from_response(resp)


def test_unreadable_image_bytes_raise_generation_error():
    resp = response_with(types.Part(inline_data=types.Blob(data=b"not a png", mime_type="image/png")))
    with pytest.raises(ImageGenerationError, match="unreadable"):
        image_from_response(resp)


def test_base64_string_payload_is_decoded():
    import base64
    from types import SimpleNamespace as NS
    part = NS(inline_data=NS(data=base64.b64encode(png_bytes((5, 6))).decode(), mime_type="image/png"),
              thought=None)
    assert image_from_response(NS(parts=[part])).size == (5, 6)


def test_image_cap_is_enforced_before_calling_the_api():
    c, sdk, _ = make_client([response_with(image_part(png_bytes()))] * 3, max_images_per_run=2)
    c.generate("a", aspect_ratio="1:1", image_size="1K")
    c.generate("b", aspect_ratio="1:1", image_size="1K")
    assert c.remaining == 0
    with pytest.raises(ImageCapExceeded):
        c.generate("c", aspect_ratio="1:1", image_size="1K")
    assert len(sdk.models.calls) == 2


def test_run_client_is_shared_so_the_cap_spans_a_run(base_settings):
    client_mod.reset_run_clients()
    a = client_mod.run_client(base_settings)
    assert client_mod.run_client(base_settings) is a
    assert a.max_images == client_mod.DEFAULTS["max_images_per_run"]
    client_mod.reset_run_clients()


def test_fake_client_is_deterministic_and_capped():
    f = FakeImageClient(long_side_px=300, max_images=3)
    a = f.generate("coloring page: a cow", aspect_ratio="3:4", image_size="4K")
    b = f.generate("coloring page: a cow", aspect_ratio="3:4", image_size="4K")
    c = f.generate("die-cut sticker: a cow", aspect_ratio="1:1", image_size="2K")
    assert a.size == (225, 300) and c.size == (300, 300)
    assert a.tobytes() == b.tobytes()
    r, g, bl = a.split()
    assert r.tobytes() == g.tobytes() == bl.tobytes()                  # coloring: gray only
    assert c.getpixel((0, 0)) == (255, 255, 255)                       # sticker: plain background...
    assert any(len(set(px)) > 1 for _, px in c.getcolors(1 << 16))     # ...and a colorful subject
    assert [x["image_size"] for x in f.calls] == ["4K", "4K", "2K"]
    with pytest.raises(ImageCapExceeded):
        f.generate("x", aspect_ratio="1:1", image_size="1K")
