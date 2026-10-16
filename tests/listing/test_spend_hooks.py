"""Spend hooks in listing/model.py: check_budget("text") before every real request, one
record() per answered request, nothing in dry runs, and BudgetExceeded never swallowed by
the retry loop or the template fallback. The ledger lives in tmp_path."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from anthropic import DefaultHttpxClient

import listing
from core import spend
from core.compliance import Blocklist
from core.config import section
from core.items import read_manifest
from core.spend import BudgetExceeded
from listing import model

from .conftest import GOOD, ITEM_ID, FakeClient, good, response

LOGGER = "listing.model"


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "spend" / "ledger.jsonl"


@pytest.fixture
def spend_settings(settings, ledger, monkeypatch) -> dict:
    """Listing settings with the spend ledger in tmp_path. The hooks get these settings
    whether or not the listing stage passes them on (settings=None loads them)."""
    settings["spend"] = {"ledger_file": str(ledger), "monthly_cap_usd": None}
    monkeypatch.setattr(spend, "load_settings", lambda: settings)
    for state in (spend._warned, spend._unwritable, spend._writable):
        state.clear()
    return settings


def paid(payload, input_tokens=1234, output_tokens=567, **kw) -> SimpleNamespace:
    """A fake response that carries `usage`, as every real SDK response does."""
    r = response(payload, **kw)
    r.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return r


def lines(ledger) -> list[dict]:
    if not ledger.exists():
        return []
    return [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]


def seed(ledger, dollars: float) -> None:
    """Earlier spending this month (Opus input tokens at $4 per million)."""
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing",
                 input_tokens=round(dollars / 4 * 1_000_000), settings={"spend": {"ledger_file": str(ledger)}})


# --- recording ---------------------------------------------------------------------------------

def test_one_record_per_successful_call(item, spend_settings, ledger):
    client = FakeClient(paid(GOOD))
    lst = listing.write_listing(item, client=client, settings=spend_settings)
    assert lst["writer"]["kind"] == "model" and len(client.calls) == 1
    (line,) = lines(ledger)
    assert {k: line[k] for k in ("provider", "model", "kind", "stage", "input_tokens", "output_tokens", "images")} == {
        "provider": "anthropic", "model": "claude-opus-5-5", "kind": "text", "stage": "listing",
        "input_tokens": 1234, "output_tokens": 567, "images": 0}
    assert line["cost_usd"] == pytest.approx(1234 * 4 / 1e6 + 567 * 20 / 1e6) and line["estimated"] is True
    assert line["item_id"] in (None, ITEM_ID)        # named once the listing stage passes item_id on


def test_model_write_names_the_item_and_uses_the_given_settings(spend_settings, ledger):
    cfg = section(spend_settings, "listing", listing.DEFAULTS)
    facts = {"images": ["photo"] * len(GOOD["alt_texts"])}
    result = model.write(FakeClient(paid(GOOD)), facts, cfg, model="claude-opus-5-5", max_tokens=16000,
                         blocklist=Blocklist.load(), settings=spend_settings, item_id="item-7")
    assert result.errors == [] and result.attempts == 1
    (line,) = lines(ledger)
    assert line["item_id"] == "item-7"


def test_every_retry_is_a_paid_call(item, spend_settings, ledger):
    long_title = "Farm Animal Coloring Pages " + "with lots of extra words " * 6
    client = FakeClient(paid(good(title=long_title), 100, 10), paid(GOOD, 200, 20))
    lst = listing.write_listing(item, client=client, settings=spend_settings)
    assert lst["writer"]["attempts"] == 2
    assert [(x["input_tokens"], x["output_tokens"]) for x in lines(ledger)] == [(100, 10), (200, 20)]


def test_a_refusal_is_still_recorded_then_the_template_is_used(item, spend_settings, ledger):
    client = FakeClient(paid("", 300, 5, stop_reason="refusal"))
    lst = listing.write_listing(item, client=client, settings=spend_settings)
    assert lst["writer"]["kind"] == "template" and len(client.calls) == 1
    (line,) = lines(ledger)
    assert line["input_tokens"] == 300


def test_fallback_model_is_priced_at_its_own_rate(item, spend_settings, ledger):
    client = FakeClient(paid(GOOD, 1_000_000, 0, model="claude-opus-5"))
    listing.write_listing(item, client=client, settings=spend_settings)
    (line,) = lines(ledger)
    assert line["model"] == "claude-opus-5" and line["cost_usd"] == 5.0


def test_a_declined_attempt_before_the_fallback_is_counted_too(item, spend_settings, ledger):
    """Server-side fallback: top-level usage covers only the attempt that answered;
    usage.iterations also holds the declined Opus 5.5 attempt, which may be billed."""
    r = paid(GOOD, 800, 300, model="claude-opus-5")
    r.usage.iterations = [SimpleNamespace(type="message", model=None, input_tokens=1000, output_tokens=40),
                          SimpleNamespace(type="fallback_message", model="claude-opus-5", input_tokens=800,
                                          output_tokens=300)]
    client = FakeClient(r)
    listing.write_listing(item, client=client, settings=spend_settings)
    assert len(client.calls) == 1
    got = {x["model"]: (x["input_tokens"], x["output_tokens"], x["cost_usd"]) for x in lines(ledger)}
    assert got == {"claude-opus-5-5": (1000, 40, pytest.approx(1000 * 4 / 1e6 + 40 * 20 / 1e6)),
                   "claude-opus-5": (800, 300, pytest.approx(800 * 5 / 1e6 + 300 * 25 / 1e6))}


def test_billed_attempts():
    def resp(usage, model="claude-opus-5-5"):
        return SimpleNamespace(usage=usage, model=model)

    assert model.billed_attempts(SimpleNamespace(), "claude-opus-5-5") == []          # a fake without usage
    plain = SimpleNamespace(input_tokens=10, output_tokens=3, iterations=None)
    assert model.billed_attempts(resp(plain), "claude-opus-5-5") == [("claude-opus-5-5", 10, 3)]
    one = SimpleNamespace(input_tokens=10, output_tokens=3,
                          iterations=[SimpleNamespace(type="message", input_tokens=10, output_tokens=3)])
    assert model.billed_attempts(resp(one), "claude-opus-5-5") == [("claude-opus-5-5", 10, 3)]
    sticky = SimpleNamespace(input_tokens=10, output_tokens=3, iterations=[
        SimpleNamespace(type="fallback_message", model="claude-opus-4-8", input_tokens=10, output_tokens=3)])
    assert model.billed_attempts(resp(sticky, "claude-opus-4-8"), "claude-opus-5-5") == [("claude-opus-4-8", 10, 3)]
    short = SimpleNamespace(input_tokens=99, output_tokens=3,                      # iterations under-report: top level
                            iterations=[SimpleNamespace(type="message", input_tokens=1, output_tokens=1)])
    assert model.billed_attempts(resp(short), "claude-opus-5-5") == [("claude-opus-5-5", 99, 3)]


def test_cache_tokens_count_as_input(monkeypatch):
    usage = SimpleNamespace(input_tokens=10, output_tokens=3, cache_creation_input_tokens=5,
                            cache_read_input_tokens=None)
    seen = {}
    monkeypatch.setattr(spend, "record", lambda *a, **kw: seen.update(kw) or 0.0)
    model.record_usage(SimpleNamespace(usage=usage, model="claude-opus-5-5"), "claude-opus-5-5")
    assert seen["input_tokens"] == 15 and seen["output_tokens"] == 3 and seen["stage"] == "listing"


def test_responses_without_usage_record_nothing(item, spend_settings, ledger):
    listing.write_listing(item, client=FakeClient(GOOD), settings=spend_settings)
    assert not ledger.exists()


def test_a_ledger_failure_never_loses_the_paid_draft(item, spend_settings, ledger, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(spend, "record", broken)
    caplog.set_level(logging.ERROR, logger=LOGGER)
    client = FakeClient(paid(GOOD))
    lst = listing.write_listing(item, client=client, settings=spend_settings)
    assert lst["title"] == GOOD["title"] and len(client.calls) == 1
    assert "could not add the listing request to the spend ledger" in caplog.text


def test_through_the_real_sdk(item, spend_settings, ledger):
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                        {"type": "text", "text": json.dumps(GOOD)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 900, "output_tokens": 450, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0, "output_tokens_details": {"thinking_tokens": 300}}})

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    listing.write_listing(item, client=client, settings=spend_settings)
    (line,) = lines(ledger)
    assert (line["input_tokens"], line["output_tokens"]) == (900, 450)      # output already includes thinking
    assert line["cost_usd"] == pytest.approx(900 * 4 / 1e6 + 450 * 20 / 1e6)


def test_fallback_iterations_through_the_real_sdk(item, spend_settings, ledger):
    """The beta (fallback) request path parses usage.iterations into SDK types."""
    spend_settings.setdefault("listing", {})["refusal_fallback"] = True
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers.get("anthropic-beta"))
        return httpx2.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5",
            "content": [{"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"}},
                        {"type": "text", "text": json.dumps(GOOD)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 700, "output_tokens": 200, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0, "iterations": [
                          {"type": "message", "input_tokens": 700, "output_tokens": 25,
                           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                          {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": 700,
                           "output_tokens": 200, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}]}})

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    listing.write_listing(item, client=client, settings=spend_settings)
    assert seen and "server-side-fallback" in (seen[0] or "")
    got = sorted((x["model"], x["input_tokens"], x["output_tokens"]) for x in lines(ledger))
    assert got == [("claude-opus-5", 700, 200), ("claude-opus-5-5", 700, 25)]
    assert spend.month_to_date(spend_settings)["total"] == pytest.approx(
        (700 * 4 + 25 * 20 + 700 * 5 + 200 * 25) / 1e6)


# --- dry runs ----------------------------------------------------------------------------------

def test_dry_run_never_checks_or_records(item, spend_settings, ledger, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a dry run must not touch the spend tracker")

    spend_settings["spend"]["monthly_cap_usd"] = 0.01
    seed(ledger, 5.0)                                             # over the cap: a dry run does not care
    monkeypatch.setattr(spend, "check_budget", forbidden)
    monkeypatch.setattr(spend, "record", forbidden)
    lst = listing.write_listing(item, dry_run=True, settings=spend_settings)
    assert lst["writer"]["kind"] == "template" and lst["writer"]["dry_run"] is True


# --- the cap -----------------------------------------------------------------------------------

def test_budget_reached_stops_before_the_paid_call(item, spend_settings, ledger):
    spend_settings["spend"]["monthly_cap_usd"] = 1.0
    seed(ledger, 1.0)
    client = FakeClient(paid(GOOD))
    with pytest.raises(BudgetExceeded, match="Monthly spending cap reached"):
        listing.write_listing(item, client=client, settings=spend_settings)
    assert client.calls == []                                     # nothing was paid for
    assert not (item / "listing.json").exists()                   # no template copy either
    assert read_manifest(item)["status"] == "qa_passed"           # the next month lists it
    assert len(lines(ledger)) == 1


def test_budget_reached_mid_retry_is_not_swallowed(item, spend_settings, ledger):
    spend_settings["spend"]["monthly_cap_usd"] = 0.02
    long_title = "Farm Animal Coloring Pages " + "with lots of extra words " * 6
    client = FakeClient(paid(good(title=long_title), 1000, 1000), paid(GOOD))    # first call costs $0.024
    with pytest.raises(BudgetExceeded):
        listing.write_listing(item, client=client, settings=spend_settings)
    assert len(client.calls) == 1                                 # the re-ask was never sent
    assert not (item / "listing.json").exists()                   # no best-draft or template fallback
    assert len(lines(ledger)) == 1


def test_budget_exceeded_is_not_an_api_error():
    assert not issubclass(BudgetExceeded, model.API_ERRORS or ())


def test_under_the_cap_lists_normally(item, spend_settings, ledger):
    spend_settings["spend"]["monthly_cap_usd"] = 10.0
    seed(ledger, 9.0)
    lst = listing.write_listing(item, client=FakeClient(paid(GOOD)), settings=spend_settings)
    assert lst["writer"]["kind"] == "model" and len(lines(ledger)) == 2


def test_an_unwritable_ledger_stops_before_the_paid_call(item, spend_settings, ledger):
    """With a cap set, spending that cannot be written down must not go on uncounted."""
    spend_settings["spend"]["monthly_cap_usd"] = 25
    ledger.mkdir(parents=True)                                    # cannot be opened for appending
    client = FakeClient(paid(GOOD))
    with pytest.raises(spend.LedgerUnavailable):
        listing.write_listing(item, client=client, settings=spend_settings)
    assert client.calls == [] and not (item / "listing.json").exists()
    assert read_manifest(item)["status"] == "qa_passed"


def test_an_odd_usage_shape_never_loses_the_paid_draft(item, spend_settings, ledger, caplog):
    class Weird:
        input_tokens, output_tokens = 5, 5

        @property
        def iterations(self):
            raise TypeError("unexpected usage format")

    r = response(GOOD)
    r.usage = Weird()
    caplog.set_level(logging.ERROR, logger=LOGGER)
    lst = listing.write_listing(item, client=FakeClient(r), settings=spend_settings)
    assert lst["title"] == GOOD["title"]
    assert "could not read the token usage" in caplog.text
