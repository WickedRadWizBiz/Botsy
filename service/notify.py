"""Discord notifications (docs/SERVER.md section 11).

    send(text, *, file=None, settings=None, kinds=(), urgent=False, filename=None,
         now=None, session=None, sleep=time.sleep) -> bool
    problem(settings=None) -> str | None     (why nothing would be sent, in plain words)
    wants(kind, settings=None) -> bool       (kind is in notify.discord.events)

Messages go to the Discord webhook in the environment variable DISCORD_WEBHOOK_URL (a
secret: it lives only in /opt/botsy/.env). The webhook link holds a token, so it is never
written to a log, a status file or an error message: failures are logged by type and HTTP
status only, and urllib3's debug lines that name a webhook path are dropped.

- Discord takes at most 2000 characters of content. Longer text is attached as a .txt
  file, with the beginning of it as the message.
- Timeout 10 s (notify.discord.timeout_s). One retry on HTTP 429, after Discord's
  retry_after (at most notify.discord.max_retry_wait_s).
- send() never raises. It returns True only when Discord accepted the message.
- Quiet hours (notify.quiet_hours, default 22:00 to 07:00) are read on the wall clock of
  schedule.timezone (America/New_York by default; core.config.tz), never the server's own
  zone, so a server left on UTC still keeps quiet from 10 pm to 7 am New York time, through
  the change-of-clocks weeks too. A message that is not urgent is not sent then. It is
  recorded in the event journal as `notification_held`, and the morning digest
  (service/digest.py) includes it; it is not sent again later. Urgent kinds
  (notify.urgent: budget_reached, error, and the Level 3 approvals order_approved,
  plan_approved, plan_mode_changed) always go out.
- Mentions are switched off (allowed_mentions), so no text can ping @everyone.

While pytest runs, the real webhook is never called: tests pass a fake `session`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from core.config import load_env, load_settings, now_local, section, tz

log = logging.getLogger("service.notify")

ENV_VAR = "DISCORD_WEBHOOK_URL"
CONTENT_LIMIT = 2000          # Discord's limit for message content
PREVIEW_CHARS = 1500          # the start of a long text shown as the message
DISCORD_HOSTS = ("discord.com", "discordapp.com")

# Settings section "notify" (read with core.config.section(settings, "notify", DEFAULTS)).
DEFAULTS: dict = {
    "discord": {
        "enabled": True,              # also needs DISCORD_WEBHOOK_URL in .env
        # Event kinds that post right away (one combined message per tick).
        # search_list_written posts the weekly list with search-list.txt attached.
        "events": ["draft_created", "held_for_review", "export_rejected", "budget_reached",
                   "spend_warning", "token_expiring", "stage_changed", "error",
                   "search_list_written"],
        "digest": True,               # post the daily log (python -m service digest)
        "timeout_s": 10,
        "max_retry_wait_s": 60,       # longest wait for Discord's retry_after on HTTP 429
    },
    "quiet_hours": ["22:00", "07:00"],   # in schedule.timezone; [] or null = no quiet hours
    "urgent": ["budget_reached", "error", "order_approved", "plan_approved", "plan_mode_changed"],  # sent even in quiet hours
}

_HHMM = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
_WEBHOOK_PATH = re.compile(r"/api/(?:v\d+/)?webhooks/\d+/\S+")


class _HideWebhook(logging.Filter):
    """Drops log lines that name a webhook path (urllib3 logs request lines at DEBUG)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return "/webhooks/" not in record.getMessage()
        except Exception:  # noqa: BLE001 - a broken record is not our problem here
            return True


for _name in ("urllib3.connectionpool", "urllib3", "requests"):
    _logger = logging.getLogger(_name)
    if not any(isinstance(f, _HideWebhook) for f in _logger.filters):
        _logger.addFilter(_HideWebhook())


def config(settings: dict | None = None) -> dict:
    return section(settings if settings is not None else load_settings(), "notify", DEFAULTS)


def _webhook_url() -> str:
    load_env()
    return os.environ.get(ENV_VAR, "").strip()


def _valid_url(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return (u.scheme == "https" and any(host == h or host.endswith("." + h) for h in DISCORD_HOSTS)
            and bool(_WEBHOOK_PATH.fullmatch(u.path)))


def problem(settings: dict | None = None) -> str | None:
    """Why send() would not post anything, in plain words; None when Discord is set up."""
    cfg = config(settings)
    if (cfg.get("discord") or {}).get("enabled") is False:
        return "Discord messages are switched off (notify.discord.enabled is false in the settings)."
    url = _webhook_url()
    if not url:
        return (f"{ENV_VAR} is not set in /opt/botsy/.env, so no Discord messages are sent "
                "(deploy/lightsail/README.md, 'Discord messages').")
    if not _valid_url(url):
        return (f"{ENV_VAR} in .env does not look like a Discord webhook link. It should start "
                "with https://discord.com/api/webhooks/ (copy it again in Discord).")
    return None


def wants(kind: str, settings: dict | None = None) -> bool:
    """True when this event kind posts to Discord (notify.discord.events)."""
    kinds = (config(settings).get("discord") or {}).get("events") or []
    return kind in kinds if isinstance(kinds, (list, tuple, set)) else False


def is_urgent(kinds, settings: dict | None = None) -> bool:
    urgent = config(settings).get("urgent") or []
    return any(k in urgent for k in (kinds or ()))


def _parse_hhmm(value) -> int | None:
    m = _HHMM.fullmatch(str(value).strip()) if value is not None else None
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def in_quiet_hours(settings: dict | None = None, now: datetime | None = None) -> bool:
    """True when `now` falls in notify.quiet_hours [start, end) on schedule.timezone's clock.

    A time-zone aware `now` is converted to that zone; a naive one is taken as already
    being that zone's wall clock. None means the current time."""
    settings = settings if settings is not None else load_settings()
    hours = config(settings).get("quiet_hours")
    if not hours:
        return False
    if not isinstance(hours, (list, tuple)) or len(hours) != 2:
        log.warning("notify.quiet_hours should be two times such as [\"22:00\", \"07:00\"]; ignored")
        return False
    start, end = (_parse_hhmm(h) for h in hours)
    if start is None or end is None:
        log.warning("notify.quiet_hours has a time that is not HH:MM; ignored")
        return False
    if start == end:
        return False
    if now is None:
        now = now_local(settings)
    elif now.tzinfo is not None:
        now = now.astimezone(tz(settings))         # Botsy's clock, not the server's
    minute = now.hour * 60 + now.minute
    return start <= minute < end if start < end else (minute >= start or minute < end)


def _preview(text: str) -> str:
    """The start of a long text, cut at a line end, for the message itself."""
    cut = text[:PREVIEW_CHARS]
    if "\n" in cut:
        cut = cut[:cut.rindex("\n")]
    return cut.rstrip() + "\n\n(The full text is in the attached file.)"


def _retry_after(response, cap: float) -> float:
    wait = None
    try:
        body = response.json()
        if isinstance(body, dict):
            wait = body.get("retry_after")
    except Exception:  # noqa: BLE001 - no JSON body
        wait = None
    if wait is None:
        try:
            wait = float((getattr(response, "headers", None) or {}).get("Retry-After"))
        except (TypeError, ValueError):
            wait = 1.0
    try:
        wait = float(wait)
    except (TypeError, ValueError):
        wait = 1.0
    return max(0.0, min(float(cap), wait))


def send(text: str, *, file: str | Path | None = None, settings: dict | None = None, kinds=(),
         urgent: bool = False, filename: str | None = None, now: datetime | None = None,
         session=None, sleep=time.sleep) -> bool:
    """Post `text` (and `file`, attached) to the Discord webhook. Never raises.

    kinds: the event kinds the message is about (an urgent kind beats quiet hours).
    urgent: send even during quiet hours (the digest and notify-test use it).
    filename: the attachment name used when the text is too long for one message.
    Returns True when Discord accepted it; False when it was not sent (switched off, not
    set up, held for quiet hours, or a failure, which is logged without the link)."""
    try:
        return _send(text, file=file, settings=settings, kinds=tuple(kinds or ()), urgent=urgent,
                     filename=filename, now=now, session=session, sleep=sleep)
    except Exception as e:  # noqa: BLE001 - never raises, by contract
        log.warning("Discord message not sent: %s", type(e).__name__)
        return False


def _send(text, *, file, settings, kinds, urgent, filename, now, session, sleep) -> bool:
    from service.status import redact

    settings = settings if settings is not None else load_settings()
    cfg = config(settings)
    why = problem(settings)
    if why:
        log.info("Discord message not sent: %s", why)
        return False
    text = redact(str(text or "")).replace("\r\n", "\n").strip()
    if not text and file is None:
        return False
    if not urgent and not is_urgent(kinds, settings) and in_quiet_hours(settings, now):
        from core import events

        events.emit("notification_held", settings=settings, kinds=list(kinds), text=text[:4000],
                    file=Path(file).name if file is not None else None)
        log.info("Discord message held for the morning log (quiet hours)")
        return False
    if session is None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            log.info("test run: the real Discord webhook is not called")
            return False
        import requests

        session = requests

    attachments: list[tuple[str, bytes]] = []
    content = text
    if len(text) > CONTENT_LIMIT:
        content = _preview(text)
        attachments.append((filename or "botsy-message.txt",
                            text.replace("\n", "\r\n").encode("utf-8") + b"\r\n"))
    if file is not None:
        try:
            attachments.append((Path(file).name, Path(file).read_bytes()))
        except OSError as e:
            log.warning("Could not attach %s to the Discord message: %s", Path(file).name, type(e).__name__)
    payload = {"content": content, "allowed_mentions": {"parse": []}}
    discord = cfg.get("discord") or {}
    timeout = float(discord.get("timeout_s") or DEFAULTS["discord"]["timeout_s"])
    cap = float(discord.get("max_retry_wait_s") or DEFAULTS["discord"]["max_retry_wait_s"])
    url = _webhook_url()

    for attempt in (1, 2):
        try:
            if attachments:
                files = {f"files[{i}]": (name, data, "text/plain; charset=utf-8")
                         for i, (name, data) in enumerate(attachments)}
                response = session.post(url, data={"payload_json": json.dumps(payload)}, files=files,
                                        timeout=timeout)
            else:
                response = session.post(url, json=payload, timeout=timeout)
        except Exception as e:  # noqa: BLE001 - the message of a requests error names the link
            log.warning("Discord message not sent: %s (the server could not reach Discord)", type(e).__name__)
            return False
        code = int(getattr(response, "status_code", 0) or 0)
        if 200 <= code < 300:
            return True
        if code == 429 and attempt == 1:
            wait = _retry_after(response, cap)
            log.info("Discord asked to wait %.1f s (HTTP 429); trying once more", wait)
            sleep(wait)
            continue
        log.warning("Discord message not sent: HTTP %s%s", code,
                    " (the webhook was deleted or the link is wrong)" if code in (401, 403, 404) else "")
        return False
    return False


__all__ = ["CONTENT_LIMIT", "DEFAULTS", "ENV_VAR", "config", "in_quiet_hours", "is_urgent", "problem",
           "send", "wants"]
