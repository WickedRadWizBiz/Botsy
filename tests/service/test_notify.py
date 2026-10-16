"""service.notify: Discord webhook posts with a fake requests session (never the network)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from core import events
from service import notify

URL = "https://discord.com/api/webhooks/123456789/SECRET-webhook-token-abcdef"
NOON = datetime(2026, 10, 16, 12, 0)        # server local time
NIGHT = datetime(2026, 10, 16, 23, 30)


class Response:
    def __init__(self, status_code=204, body=None, headers=None):
        self.status_code, self._body, self.headers = status_code, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeSession:
    def __init__(self, *responses, error=None):
        self.responses = list(responses) or [Response(204)]
        self.error = error
        self.calls: list[dict] = []

    def post(self, url, **kw):
        self.calls.append({"url": url, **kw})
        if self.error:
            raise self.error
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(notify, "load_env", lambda: None)     # never read the repo's .env
    monkeypatch.setenv(notify.ENV_VAR, URL)


@pytest.fixture
def settings(tmp_path):
    return {"notify": {}, "service": {"events_file": str(tmp_path / "events.jsonl")}}


def test_short_message_is_one_json_post(settings):
    s = FakeSession()
    assert notify.send("3 new drafts are waiting in Etsy", settings=settings, session=s, now=NOON) is True
    (call,) = s.calls
    assert call["url"] == URL and call["timeout"] == 10
    assert call["json"] == {"content": "3 new drafts are waiting in Etsy", "allowed_mentions": {"parse": []}}
    assert "files" not in call


def test_long_text_is_attached_as_a_txt_file(settings):
    s = FakeSession(Response(200))
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(100))
    assert notify.send(text, settings=settings, session=s, now=NOON, filename="daily-log.txt")
    (call,) = s.calls
    payload = json.loads(call["data"]["payload_json"])
    assert len(payload["content"]) <= notify.CONTENT_LIMIT and payload["content"].startswith("line 0")
    assert "attached file" in payload["content"] and payload["allowed_mentions"] == {"parse": []}
    name, data, ctype = call["files"]["files[0]"]
    assert name == "daily-log.txt" and ctype.startswith("text/plain")
    assert data.decode("utf-8").replace("\r\n", "\n").strip() == text
    assert b"\r\n" in data                                  # Notepad-friendly


def test_a_file_is_attached(settings, tmp_path):
    f = tmp_path / "search-list.txt"
    f.write_bytes(b"axolotl coloring pages\r\n")
    s = FakeSession()
    assert notify.send("This week's search list", file=f, settings=settings, session=s, now=NOON)
    files = s.calls[0]["files"]
    assert files["files[0]"][:2] == ("search-list.txt", b"axolotl coloring pages\r\n")
    assert json.loads(s.calls[0]["data"]["payload_json"])["content"] == "This week's search list"


def test_429_is_retried_once_after_retry_after(settings):
    waits = []
    s = FakeSession(Response(429, {"retry_after": 0.5}), Response(204))
    assert notify.send("hi", settings=settings, session=s, now=NOON, sleep=waits.append) is True
    assert waits == [0.5] and len(s.calls) == 2

    waits.clear()
    s = FakeSession(Response(429, {"retry_after": 9999}), Response(429, {"retry_after": 1}))
    assert notify.send("hi", settings=settings, session=s, now=NOON, sleep=waits.append) is False
    assert waits == [60.0] and len(s.calls) == 2            # capped, and only one retry

    waits.clear()
    s = FakeSession(Response(429, None, {"Retry-After": "2"}), Response(204))
    assert notify.send("hi", settings=settings, session=s, now=NOON, sleep=waits.append) is True
    assert waits == [2.0]


@pytest.mark.parametrize("session", [
    FakeSession(Response(500)),
    FakeSession(Response(404)),
    FakeSession(error=ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {URL}")),
    FakeSession(error=RuntimeError("weird")),
])
def test_failures_return_false_and_never_log_the_link(settings, session, caplog):
    with caplog.at_level(logging.DEBUG):
        assert notify.send("hi", settings=settings, session=session, now=NOON) is False
    assert "Discord message not sent" in caplog.text
    assert "SECRET-webhook-token" not in caplog.text and "api/webhooks" not in caplog.text


def test_urllib3_request_lines_with_the_webhook_path_are_dropped(caplog):
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("urllib3.connectionpool").debug(
            '%s "POST /api/webhooks/1/SECRET-webhook-token HTTP/1.1" 204 0', "https://discord.com:443")
        logging.getLogger("urllib3.connectionpool").debug("Starting new HTTPS connection (1): example.org:443")
    assert "SECRET-webhook-token" not in caplog.text and "example.org" in caplog.text


def test_not_set_up_or_switched_off_sends_nothing(settings, monkeypatch):
    s = FakeSession()
    monkeypatch.delenv(notify.ENV_VAR)
    assert notify.send("hi", settings=settings, session=s, now=NOON) is False
    assert "not set" in notify.problem(settings)
    for bad in ("http://discord.com/api/webhooks/1/x", "https://example.com/api/webhooks/1/x",
                "https://discord.com/channels/1", "https://discord.com/api/webhooks/",
                "https://discord.com.evil.example/api/webhooks/1/x"):
        monkeypatch.setenv(notify.ENV_VAR, bad)
        assert notify.send("hi", settings=settings, session=s, now=NOON) is False
        assert "does not look like" in notify.problem(settings)
    for good in (URL, "https://ptb.discord.com/api/v10/webhooks/1/abc", "https://discordapp.com/api/webhooks/1/a"):
        monkeypatch.setenv(notify.ENV_VAR, good)
        assert notify.problem(settings) is None, good
    monkeypatch.setenv(notify.ENV_VAR, URL)
    off = {**settings, "notify": {"discord": {"enabled": False}}}
    assert notify.send("hi", settings=off, session=s, now=NOON) is False
    assert "switched off" in notify.problem(off)
    assert notify.problem(settings) is None
    assert s.calls == []
    assert notify.send("   ", settings=settings, session=s, now=NOON) is False     # nothing to say


def test_quiet_hours_hold_non_urgent_messages_for_the_digest(settings):
    s = FakeSession()
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert notify.send("2 new drafts", settings=settings, session=s, now=NIGHT,
                       kinds=["draft_created"]) is False
    assert s.calls == []
    held = events.read(start, settings=settings, kinds="notification_held")
    assert len(held) == 1 and held[0]["text"] == "2 new drafts" and held[0]["kinds"] == ["draft_created"]

    assert notify.send("cap reached", settings=settings, session=s, now=NIGHT,
                       kinds=["draft_created", "budget_reached"]) is True       # urgent kind
    assert notify.send("daily log", settings=settings, session=s, now=NIGHT, urgent=True) is True
    assert notify.send("2 new drafts", settings=settings, session=s, now=NOON,
                       kinds=["draft_created"]) is True
    assert len(s.calls) == 3
    assert len(events.read(start, settings=settings, kinds="notification_held")) == 1   # not re-sent


@pytest.mark.parametrize("hours, when, quiet", [
    (["22:00", "07:00"], datetime(2026, 1, 1, 22, 0), True),
    (["22:00", "07:00"], datetime(2026, 1, 1, 6, 59), True),
    (["22:00", "07:00"], datetime(2026, 1, 1, 7, 0), False),
    (["22:00", "07:00"], datetime(2026, 1, 1, 21, 59), False),
    (["01:00", "05:30"], datetime(2026, 1, 1, 3, 0), True),
    (["01:00", "05:30"], datetime(2026, 1, 1, 23, 0), False),
    ([], datetime(2026, 1, 1, 23, 0), False),
    (None, datetime(2026, 1, 1, 23, 0), False),
    (["late", "07:00"], datetime(2026, 1, 1, 23, 0), False),
    (["07:00", "07:00"], datetime(2026, 1, 1, 7, 0), False),
])
def test_quiet_hours_window(hours, when, quiet):
    assert notify.in_quiet_hours({"notify": {"quiet_hours": hours}}, when) is quiet


def test_secrets_in_the_text_are_replaced(settings, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0123456789abcdef")
    s = FakeSession()
    notify.send(f"listing failed: bad key sk-ant-test-0123456789abcdef at {URL}", settings=settings,
                session=s, now=NOON)
    content = s.calls[0]["json"]["content"]
    assert "sk-ant-test" not in content and "SECRET-webhook-token" not in content
    assert content.count("[secret]") == 2


def test_no_real_post_while_testing(settings):
    assert notify.send("hi", settings=settings, now=NOON) is False      # no session: pytest guard


def test_wants_and_urgent(settings):
    assert notify.wants("draft_created", settings) and notify.wants("search_list_written", settings)
    assert not notify.wants("item_generated", settings)
    custom = {"notify": {"discord": {"events": ["error"]}}}
    assert notify.wants("error", custom) and not notify.wants("draft_created", custom)
    assert notify.is_urgent(["error"], settings) and not notify.is_urgent(["draft_created"], settings)


def test_send_never_raises(settings, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("bug")

    monkeypatch.setattr(notify, "_send", broken)
    assert notify.send("hi", settings=settings, session=FakeSession()) is False
