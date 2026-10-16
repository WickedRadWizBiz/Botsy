"""Spend hooks in generators/imaging/client.py: GeminiImageClient checks the cap before every
request and records each answered request once; retries are not recorded twice and never
retry a BudgetExceeded; FakeImageClient and dry runs never check or record. Real google-genai
types, a fake SDK client, the ledger in tmp_path."""
from __future__ import annotations

import io
import json
import logging

import pytest
from google.genai import errors, types
from PIL import Image

from core import spend
from core.spend import BudgetExceeded
from generators import coloring_pages
from generators.imaging import client as client_mod
from generators.imaging.client import FakeImageClient, GeminiImageClient, ImageGenerationError, usage_tokens

MODEL = "gemini-nano-banana-2.1"
NO_JITTER = {"backoff_jitter": 0.0, "backoff_base_s": 2.0, "backoff_max_s": 60.0}


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "spend" / "ledger.jsonl"


@pytest.fixture
def spend_settings(settings, ledger) -> dict:
    settings["spend"] = {"ledger_file": str(ledger), "monthly_cap_usd": None}
    for state in (spend._warned, spend._unwritable, spend._writable):
        state.clear()
    return settings


def lines(ledger) -> list[dict]:
    if not ledger.exists():
        return []
    return [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]


def seed(ledger, dollars: float) -> None:
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing",
                 input_tokens=round(dollars / 4 * 1_000_000), settings={"spend": {"ledger_file": str(ledger)}})


def png_bytes(size=(30, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def usage(prompt=100, thoughts=50, image=1120, text=0) -> types.GenerateContentResponseUsageMetadata:
    details = [types.ModalityTokenCount(modality=types.MediaModality.IMAGE, token_count=image)]
    if text:
        details.append(types.ModalityTokenCount(modality=types.MediaModality.TEXT, token_count=text))
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt, thoughts_token_count=thoughts, candidates_token_count=image + text,
        candidates_tokens_details=details, total_token_count=prompt + thoughts + image + text)


def image_response(meta=None) -> types.GenerateContentResponse:
    part = types.Part(inline_data=types.Blob(data=png_bytes(), mime_type="image/png"))
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[part]), finish_reason=types.FinishReason.STOP)],
        usage_metadata=meta)


def text_response(meta=None) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text="I can't draw that")]),
        finish_reason=types.FinishReason.STOP)], usage_metadata=meta)


class FakeSDK:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outcomes.pop(0)
        if callable(out) and not isinstance(out, types.GenerateContentResponse):
            out = out()
        if isinstance(out, BaseException):
            raise out
        return out


def make_client(settings, outcomes):
    sdk, sleeps = FakeSDK(outcomes), []
    c = GeminiImageClient(MODEL, cfg=NO_JITTER, sdk_client=sdk, sleep=sleeps.append, settings=settings)
    return c, sdk, sleeps


def server_error(code=503):
    return errors.ServerError(code, {"error": {"code": code, "message": "busy", "status": "UNAVAILABLE"}})


# --- recording ---------------------------------------------------------------------------------

def test_one_record_per_successful_generate(spend_settings, ledger):
    c, sdk, _ = make_client(spend_settings, [image_response(usage())])
    c.item_id = "2026-10-16-coloring_pages-farm-animals-1a2b"
    c.generate("a cow", aspect_ratio="3:4", image_size="4K")
    (line,) = lines(ledger)
    assert {k: line[k] for k in ("provider", "model", "kind", "stage", "item_id", "images", "image_size",
                                 "input_tokens", "output_tokens")} == {
        "provider": "google", "model": MODEL, "kind": "image", "stage": "generate",
        "item_id": "2026-10-16-coloring_pages-farm-animals-1a2b", "images": 1, "image_size": "4K",
        "input_tokens": 100, "output_tokens": 50}            # image tokens are covered by the per-image price
    assert line["cost_usd"] == pytest.approx(0.1134 + 100 * 1.5 / 1e6 + 50 * 7.5 / 1e6)


def test_without_usage_metadata_one_image_is_recorded(spend_settings, ledger):
    c, _, _ = make_client(spend_settings, [image_response()])
    c.generate("a cow", aspect_ratio="1:1", image_size="2K")
    (line,) = lines(ledger)
    assert (line["images"], line["input_tokens"], line["output_tokens"]) == (1, 0, 0)
    assert line["cost_usd"] == pytest.approx(0.0504) and line["item_id"] is None


def test_retries_are_recorded_once(spend_settings, ledger, monkeypatch):
    checks = []
    real_check = spend.check_budget
    monkeypatch.setattr(spend, "check_budget", lambda kind, **kw: checks.append(kind) or real_check(kind, **kw))
    c, sdk, sleeps = make_client(spend_settings, [server_error(503), server_error(429), image_response(usage())])
    c.generate("a cow", aspect_ratio="3:4", image_size="4K")
    assert len(sdk.calls) == 3 and len(sleeps) == 2
    assert checks == ["image"] * 3                           # checked before every request
    assert len(lines(ledger)) == 1


def test_text_answer_records_its_tokens_but_no_image(spend_settings, ledger):
    c, _, _ = make_client(spend_settings, [text_response(usage(image=0, thoughts=0, text=12))])
    with pytest.raises(ImageGenerationError):
        c.generate("a cow", aspect_ratio="3:4", image_size="4K")
    (line,) = lines(ledger)
    assert (line["images"], line["input_tokens"], line["output_tokens"]) == (0, 100, 12)


def test_an_unusable_image_is_still_counted_as_an_image(spend_settings, ledger):
    """The model drew an image we cannot use (bytes Pillow cannot read): it is still billed."""
    part = types.Part(inline_data=types.Blob(data=b"not a png", mime_type="image/png"))
    bad = types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[part]), finish_reason=types.FinishReason.STOP)],
        usage_metadata=usage())
    c, _, _ = make_client(spend_settings, [bad])
    with pytest.raises(ImageGenerationError, match="unreadable"):
        c.generate("a cow", aspect_ratio="3:4", image_size="2K")
    (line,) = lines(ledger)
    assert (line["images"], line["input_tokens"], line["output_tokens"]) == (1, 100, 50)
    assert line["cost_usd"] == pytest.approx(0.0504 + 100 * 1.5 / 1e6 + 50 * 7.5 / 1e6)


def test_only_interim_thought_images_count_as_an_image(spend_settings, ledger):
    part = types.Part(inline_data=types.Blob(data=png_bytes(), mime_type="image/png"), thought=True)
    drafts = types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[part]), finish_reason=types.FinishReason.STOP)])
    c, _, _ = make_client(spend_settings, [drafts])
    with pytest.raises(ImageGenerationError, match="interim"):
        c.generate("a cow", aspect_ratio="3:4", image_size="1K")
    (line,) = lines(ledger)
    assert line["images"] == 1 and line["cost_usd"] == pytest.approx(0.0336)


def test_image_tokens_without_image_data_count_as_an_image():
    meta = usage(image=1120)
    assert client_mod.made_an_image(text_response(meta)) is True
    assert client_mod.made_an_image(text_response(usage(image=0, text=12))) is False
    assert client_mod.made_an_image(text_response()) is False


def test_text_answer_without_usage_records_nothing(spend_settings, ledger):
    c, _, _ = make_client(spend_settings, [text_response()])
    with pytest.raises(ImageGenerationError):
        c.generate("a cow", aspect_ratio="3:4", image_size="4K")
    assert lines(ledger) == []


def test_usage_tokens_without_a_modality_breakdown():
    meta = types.GenerateContentResponseUsageMetadata(prompt_token_count=7, candidates_token_count=1120,
                                                      thoughts_token_count=3)
    assert usage_tokens(image_response(meta), images=1) == {"input_tokens": 7, "output_tokens": 3}
    assert usage_tokens(text_response(meta), images=0) == {"input_tokens": 7, "output_tokens": 1123}
    assert usage_tokens(image_response(), images=1) == {"input_tokens": 0, "output_tokens": 0}


def test_a_ledger_failure_never_loses_the_paid_image(spend_settings, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(spend, "record", broken)
    caplog.set_level(logging.ERROR, logger=client_mod.__name__)
    c, sdk, _ = make_client(spend_settings, [image_response(usage())])
    img = c.generate("a cow", aspect_ratio="3:4", image_size="4K")
    assert img.size == (30, 40) and len(sdk.calls) == 1
    assert "could not add the image request to the spend ledger" in caplog.text


def test_from_settings_tracks_spend_with_those_settings(spend_settings, ledger):
    c = GeminiImageClient.from_settings(spend_settings, sdk_client=FakeSDK([image_response()]))
    assert c.settings is spend_settings
    c.generate("a cow", aspect_ratio="1:1", image_size="1K")
    assert [x["cost_usd"] for x in lines(ledger)] == [pytest.approx(0.0336)]


def test_the_shared_run_client_uses_the_latest_settings(spend_settings, tmp_path):
    """The run client is cached per process (the image cap spans a run), but a raised cap or
    another ledger in later settings applies to its next image."""
    client_mod.reset_run_clients()
    try:
        first = client_mod.run_client(spend_settings)
        later = {**spend_settings, "spend": {"ledger_file": str(tmp_path / "other.jsonl"), "monthly_cap_usd": 50}}
        assert client_mod.run_client(later) is first and first.settings is later
    finally:
        client_mod.reset_run_clients()


# --- the cap -----------------------------------------------------------------------------------

def test_budget_reached_stops_before_the_paid_call(spend_settings, ledger):
    spend_settings["spend"]["monthly_cap_usd"] = 1.0
    seed(ledger, 1.0)
    c, sdk, sleeps = make_client(spend_settings, [image_response()])
    with pytest.raises(BudgetExceeded):
        c.generate("a cow", aspect_ratio="3:4", image_size="4K")
    assert sdk.calls == [] and sleeps == [] and c.used == 0
    assert len(lines(ledger)) == 1


def test_budget_exceeded_is_not_retried(spend_settings, ledger):
    """The cap is reached while a request is being retried (say another run spent the rest)."""
    spend_settings["spend"]["monthly_cap_usd"] = 1.0

    def fail_after_others_spent():
        seed(ledger, 1.0)
        return server_error(503)

    c, sdk, sleeps = make_client(spend_settings, [fail_after_others_spent, image_response()])
    with pytest.raises(BudgetExceeded):
        c.generate("a cow", aspect_ratio="3:4", image_size="4K")
    assert len(sdk.calls) == 1 and len(sleeps) == 1             # the retry stopped at the check


def test_budget_exceeded_propagates_out_of_the_generator(tmp_path, spend_settings, ledger, coloring_brief):
    """The first 4K page ($0.1134) reaches a $0.10 cap: the second page is never paid for and the
    error is not turned into a skipped subject."""
    spend_settings["spend"]["monthly_cap_usd"] = 0.10
    sdk = FakeSDK([image_response(), image_response()])
    client = GeminiImageClient.from_settings(spend_settings, sdk_client=sdk)
    with pytest.raises(BudgetExceeded):
        coloring_pages.generate({**coloring_brief, "page_count": 2}, out_root=tmp_path / "out", date="2026-10-16",
                                dry_run=False, client=client, settings=spend_settings)
    assert len(sdk.calls) == 1 and len(lines(ledger)) == 1
    assert not any(p.name == "manifest.json" for p in (tmp_path / "out").rglob("*"))


# --- fakes and dry runs never check or record --------------------------------------------------

@pytest.fixture
def forbidden(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("fake clients and dry runs must not touch the spend tracker")

    monkeypatch.setattr(spend, "check_budget", boom)
    monkeypatch.setattr(spend, "record", boom)


def test_fake_client_never_checks_or_records(forbidden):
    fake = FakeImageClient("coloring", long_side_px=200)
    fake.generate("a cow", aspect_ratio="3:4", image_size="4K")
    assert len(fake.calls) == 1


def test_dry_run_never_checks_or_records(tmp_path, spend_settings, coloring_brief, forbidden):
    injected = GeminiImageClient.from_settings(spend_settings, sdk_client=FakeSDK([]))
    (d,) = coloring_pages.generate({**coloring_brief, "page_count": 2}, out_root=tmp_path / "out",
                                   date="2026-10-16", dry_run=True, client=injected, settings=spend_settings)
    assert (d / "manifest.json").exists() and injected.used == 0


def test_an_unwritable_ledger_stops_before_the_paid_call(spend_settings, ledger):
    spend_settings["spend"]["monthly_cap_usd"] = 25
    ledger.mkdir(parents=True)                                    # cannot be opened for appending
    c, sdk, sleeps = make_client(spend_settings, [image_response()])
    with pytest.raises(spend.LedgerUnavailable):
        c.generate("a cow", aspect_ratio="3:4", image_size="4K")
    assert sdk.calls == [] and sleeps == [] and c.used == 0


def test_an_odd_usage_shape_never_loses_the_paid_image(spend_settings, ledger, caplog):
    class Weird:
        @property
        def candidates_tokens_details(self):
            raise TypeError("unexpected usage format")

    r = image_response()
    object.__setattr__(r, "usage_metadata", Weird())
    caplog.set_level(logging.ERROR, logger=client_mod.__name__)
    c, _, _ = make_client(spend_settings, [r])
    assert c.generate("a cow", aspect_ratio="3:4", image_size="4K").size == (30, 40)
    assert "could not add the image request to the spend ledger" in caplog.text


def test_generate_batch_names_the_item_on_a_spend_tracking_client():
    """The pack's item id reaches a client that records spending (GeminiImageClient.item_id)."""
    from core.compliance import Blocklist
    from generators.imaging.common import generate_batch
    from generators.imaging.prompts import PlannedImage

    class Recording(FakeImageClient):
        item_id = None

        def generate(self, prompt, **kw):
            self.seen = self.item_id
            return super().generate(prompt, **kw)

    c = Recording()
    plan = [PlannedImage("a friendly frog", "Simple coloring page of a friendly frog")]
    generate_batch(c, plan, 1, aspect_ratio="1:1", image_size="1K", blocklist=Blocklist.load(),
                   process=lambda image, i: i, max_failures=0, item_id="2026-10-16-stickers-frogs-1a2b")
    assert c.seen == "2026-10-16-stickers-frogs-1a2b"
    plain = FakeImageClient()                          # no item_id attribute: left alone
    generate_batch(plain, plan, 1, aspect_ratio="1:1", image_size="1K", blocklist=Blocklist.load(),
                   process=lambda image, i: i, max_failures=0, item_id="x")
    assert not hasattr(plain, "item_id")
