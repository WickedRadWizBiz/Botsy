"""OAuth 2.0 for our own Etsy shop: authorization code + PKCE, and token storage.

`python -m publisher auth` prints Etsy's connect URL for the user to open; it never opens
or drives a browser (CLAUDE.md: no browser automation on etsy.com). After the user
approves, Etsy redirects to ETSY_REDIRECT_URI with `code` and `state`. A one-shot local
HTTP server on that host:port catches it, or the user pastes the redirected URL from the
address bar (`--paste`, and the fallback when the redirect URI is not a local http URL).

Etsy facts (developer.etsy.com/documentation/essentials/authentication, checked
2026-10-15): connect URL https://www.etsy.com/oauth/connect, code_challenge_method must
be S256, verifier 43-128 chars of [A-Za-z0-9._~-]; token endpoint
https://api.etsy.com/v3/public/oauth/token (form-encoded); access tokens are
"<user_id>.<token>" and last 3600 s; refresh tokens last 90 days. Whether a refresh
rotates the refresh token is not documented, so every refresh response is saved.

Tokens live in ETSY_TOKEN_FILE (default .etsy_tokens.json, gitignored), written with
mode 600. In CI there is no token file; ETSY_REFRESH_TOKEN seeds one (see TokenStore).
The file records `refresh_issued`, the date the current refresh token was issued, so the
client can warn REFRESH_WARN_DAYS before the 90-day expiry (in CI the date comes from
ETSY_REFRESH_TOKEN_ISSUED).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import ssl
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

log = logging.getLogger("publisher.auth")

VERIFIER_RE = re.compile(r"[A-Za-z0-9._~-]{43,128}")
REFRESH_TOKEN_DAYS = 90   # Etsy: refresh tokens last 90 days
REFRESH_WARN_DAYS = 80    # warn this long after issue
LOCAL_HOSTS = {"localhost", "127.0.0.1"}  # the catcher binds 127.0.0.1 only


class AuthError(RuntimeError):
    """The OAuth flow failed or was not completed."""


class AuthRequired(AuthError):
    """No usable Etsy tokens: run `python -m publisher auth` (or set ETSY_REFRESH_TOKEN)."""


# --- PKCE and the connect URL ---------------------------------------------------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def code_challenge(verifier: str) -> str:
    """S256: base64url(sha256(verifier)) without padding (RFC 7636 section 4.2)."""
    if not VERIFIER_RE.fullmatch(verifier):
        raise ValueError("PKCE verifier must be 43-128 characters of [A-Za-z0-9._~-]")
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def make_pkce() -> tuple[str, str]:
    """(verifier, challenge). 32 random bytes give a 43-character verifier, as Etsy suggests."""
    verifier = _b64url(secrets.token_bytes(32))
    return verifier, code_challenge(verifier)


def new_state() -> str:
    """Single-use, unguessable CSRF token for the redirect."""
    return secrets.token_urlsafe(32)


def connect_url(keystring: str, redirect_uri: str, scopes: list[str], state: str,
                challenge: str, base: str = "https://www.etsy.com/oauth/connect") -> str:
    query = {
        "response_type": "code",
        "client_id": keystring,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),  # Etsy wants spaces encoded as %20, hence quote below
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{base}?{urlencode(query, quote_via=quote)}"


def parse_callback(redirect: str, expected_state: str) -> str:
    """Return the authorization code from the redirect URL (or its query string).

    A missing or different `state` means the redirect did not come from our request
    (CSRF), so the flow stops."""
    query = urlsplit(redirect).query if "?" in redirect else redirect.lstrip("?")
    params = parse_qs(query, keep_blank_values=True)

    def get(key: str) -> str:
        return (params.get(key) or [""])[0]
    if get("error"):
        raise AuthError(f"Etsy refused authorization: {get('error')}: {get('error_description')}")
    if not hmac.compare_digest(get("state").encode(), expected_state.encode()):
        raise AuthError("state mismatch: the redirect does not belong to this auth request; stopped")
    if not get("code"):
        raise AuthError("the redirect has no authorization code")
    return get("code")


# --- one-shot local redirect catcher --------------------------------------------------

def can_catch_locally(redirect_uri: str, has_tls: bool = False) -> bool:
    """A local listener works for http://localhost:<port>/..., or https with a cert."""
    parts = urlsplit(redirect_uri)
    return parts.hostname in LOCAL_HOSTS and (parts.scheme == "http" or (parts.scheme == "https" and has_tls))


def wait_for_redirect(redirect_uri: str, timeout: float = 300,
                      ssl_context: ssl.SSLContext | None = None) -> str:
    """Serve one request on the redirect URI's host:port and return its path + query.

    Binds to the loopback address only, answers only the redirect path, and stops after
    the first matching request or `timeout` seconds."""
    parts = urlsplit(redirect_uri)
    if parts.hostname not in LOCAL_HOSTS:
        raise AuthError(f"{parts.hostname} is not this machine; use --paste")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    received: dict[str, str] = {}
    conn_timeout = min(5.0, max(0.5, timeout))

    class Handler(BaseHTTPRequestHandler):
        # A connection that never sends a request would otherwise block past `timeout`.
        timeout = conn_timeout

        def do_GET(self):
            if urlsplit(self.path).path != path:
                self.send_error(404)
                return
            received["url"] = self.path
            body = (b"<!doctype html><meta charset=utf-8><title>Etsy authorization</title>"
                    b"<p>Authorization received. You can close this tab and return to the terminal.")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the code out of the terminal log
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    if ssl_context is not None:
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
    server.timeout = 0.5
    deadline = time.monotonic() + timeout
    try:
        while "url" not in received and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "url" not in received:
        raise AuthError(f"no redirect to {redirect_uri} within {int(timeout)} s")
    return received["url"]


# --- token storage --------------------------------------------------------------------

def tokens_from_response(resp: dict, now: float | None = None) -> dict:
    """Token endpoint JSON -> what we store (absolute expiry, user id from the prefix)."""
    now = time.time() if now is None else now
    access = resp.get("access_token") or ""
    if not access or not resp.get("refresh_token"):
        raise AuthError("token response is missing access_token or refresh_token")
    prefix = access.split(".", 1)[0]
    return {
        "access_token": access,
        "refresh_token": resp["refresh_token"],
        "token_type": resp.get("token_type", "Bearer"),
        "scope": resp.get("scope", ""),
        "expires_at": now + float(resp.get("expires_in") or 3600),
        "user_id": int(prefix) if prefix.isdigit() else None,
        "obtained": datetime.fromtimestamp(now, UTC).isoformat(timespec="seconds"),
    }


def carry_refresh_issued(new: dict, old: dict | None) -> dict:
    """Set new["refresh_issued"]: the old date when Etsy kept the same refresh token, else
    now (a new token was issued; also after `auth`). Unknown stays None."""
    if old and old.get("refresh_token") == new.get("refresh_token"):
        new["refresh_issued"] = old.get("refresh_issued")
    else:
        new["refresh_issued"] = str(new.get("obtained") or datetime.now(UTC).isoformat(timespec="seconds"))[:10]
    return new


def refresh_token_age_days(tokens: dict | None, now: float | None = None) -> int | None:
    """Whole days since tokens["refresh_issued"] (YYYY-MM-DD or ISO time), or None if unknown."""
    issued = str((tokens or {}).get("refresh_issued") or "")[:10]
    try:
        day = datetime.strptime(issued, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None
    now_dt = datetime.fromtimestamp(time.time() if now is None else now, UTC)
    return (now_dt - day).days


def refresh_age_warning(tokens: dict | None, now: float | None = None) -> str | None:
    """A warning once the refresh token is REFRESH_WARN_DAYS old, else None."""
    age = refresh_token_age_days(tokens, now)
    if age is None or age < REFRESH_WARN_DAYS:
        return None
    when = "has expired" if age >= REFRESH_TOKEN_DAYS else f"expires at {REFRESH_TOKEN_DAYS} days"
    return (f"the Etsy refresh token was issued {age} days ago ({tokens['refresh_issued'][:10]}) and {when}; "
            "run `python -m publisher auth` (and update ETSY_REFRESH_TOKEN and ETSY_REFRESH_TOKEN_ISSUED in CI)")


class TokenStore:
    """Tokens in a JSON file (mode 600), seeded from ETSY_REFRESH_TOKEN when the file is absent.

    CI and refresh-token rotation: the weekly workflow has no token file, so the first
    request refreshes with ETSY_REFRESH_TOKEN and saves the result to ETSY_TOKEN_FILE in
    the runner's temp dir, which is deleted with the job. If Etsy rotates refresh tokens
    (undocumented), the secret is spent after one run and the next run fails with
    invalid_grant: re-run `python -m publisher auth` locally and paste the new
    `refresh_token` from the token file into the repository secret. Either way the
    secret expires 90 days after it was issued.
    """

    def __init__(self, path: str | Path, env_refresh_token: str | None = None,
                 env_refresh_issued: str | None = None):
        self.path = Path(path)
        self.env_refresh_token = (env_refresh_token or "").strip() or None
        # ETSY_REFRESH_TOKEN_ISSUED (YYYY-MM-DD): when the seed token was issued, if known.
        self.env_refresh_issued = (env_refresh_issued or "").strip() or None

    def load(self) -> dict | None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise AuthRequired(f"token file {self.path.name} is unreadable ({type(e).__name__}); "
                                   "run `python -m publisher auth` again") from e
            if isinstance(data, dict) and data.get("refresh_token"):
                return data
        if self.env_refresh_token:
            seed = {"refresh_token": self.env_refresh_token, "expires_at": 0, "source": "env"}
            if self.env_refresh_issued:
                seed["refresh_issued"] = self.env_refresh_issued
            return seed
        return None

    def save(self, tokens: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
        os.chmod(tmp, 0o600)  # in case the file existed with looser permissions
        os.replace(tmp, self.path)


class MemoryTokenStore:
    """In-memory store for dry runs and tests."""

    def __init__(self, tokens: dict | None = None):
        self.tokens = dict(tokens) if tokens else None
        self.saved: list[dict] = []

    def load(self) -> dict | None:
        return dict(self.tokens) if self.tokens else None

    def save(self, tokens: dict) -> None:
        self.tokens = dict(tokens)
        self.saved.append(dict(tokens))


# --- the interactive flow -------------------------------------------------------------

def authorize(client, redirect_uri: str, scopes: list[str], *, timeout: float = 300,
              paste: bool = False, ssl_context: ssl.SSLContext | None = None,
              connect_base: str = "https://www.etsy.com/oauth/connect",
              out=print, read_line=input) -> dict:
    """Run the PKCE flow with an EtsyClient; tokens are saved by client.exchange_code()."""
    verifier, challenge = make_pkce()
    state = new_state()
    url = connect_url(client.keystring, redirect_uri, scopes, state, challenge, connect_base)
    out("Open this URL in your browser, sign in as the shop owner and approve access:\n")
    out(f"  {url}\n")
    if paste or not can_catch_locally(redirect_uri, ssl_context is not None):
        if not paste:
            out(f"{redirect_uri} is not a local http address this tool can listen on.")
        out("After approving, copy the full URL from the browser's address bar (the page "
            "itself may fail to load; that is fine) and paste it here.")
        redirect = read_line("Redirected URL: ").strip()
    else:
        out(f"Waiting up to {int(timeout)} s for the redirect to {redirect_uri} ...")
        redirect = wait_for_redirect(redirect_uri, timeout, ssl_context)
    code = parse_callback(redirect, state)
    return client.exchange_code(code, verifier, redirect_uri)
