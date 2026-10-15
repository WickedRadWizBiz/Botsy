"""The real anthropic SDK builds and parses our request (offline, via a mock transport).

Catches parameter names the fake client would accept but the SDK would reject.
"""
from __future__ import annotations

import json

import anthropic
import httpx2
from anthropic import DefaultHttpxClient

import listing
from listing import model

from .conftest import GOOD


def _client(seen: list, text: str):
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append({"url": str(request.url), "beta": request.headers.get("anthropic-beta"),
                     "body": json.loads(request.content)})
        return httpx2.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": text}],
            "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 10}})

    transport = httpx2.MockTransport(handler)
    return anthropic.Anthropic(api_key="test-key", max_retries=0, http_client=DefaultHttpxClient(transport=transport))


def test_write_listing_through_the_real_sdk(item, settings):
    seen: list = []
    lst = listing.write_listing(item, client=_client(seen, json.dumps(GOOD)), settings=settings)
    assert lst["title"] == GOOD["title"] and lst["model"] == "claude-opus-5-5"
    req = seen[0]
    assert req["url"].startswith("https://api.anthropic.com/v1/messages")
    assert req["beta"] == model.FALLBACK_BETA
    body = req["body"]
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16000 and body["fallbacks"] == "default"
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": model.SCHEMA}, "effort": "low"}
    assert body["messages"][0]["role"] == "user" and isinstance(body["system"], str)
    assert "thinking" not in body


def test_plain_messages_endpoint_when_fallback_is_off(item, settings):
    settings["listing"]["refusal_fallback"] = False
    seen: list = []
    listing.write_listing(item, client=_client(seen, json.dumps(GOOD)), settings=settings)
    assert seen[0]["beta"] is None and "fallbacks" not in seen[0]["body"]
