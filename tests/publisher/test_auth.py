"""OAuth: PKCE (S256), connect URL, state check, local redirect catcher, token storage."""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from publisher import auth
from publisher.auth import AuthError, MemoryTokenStore, TokenStore
from publisher.fake import FakeEtsyClient

from .conftest import SHOP_ID


def test_code_challenge_matches_rfc7636_vector():
    # RFC 7636 Appendix B
    assert auth.code_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == \
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_make_pkce_is_s256_without_padding():
    verifier, challenge = auth.make_pkce()
    assert auth.VERIFIER_RE.fullmatch(verifier) and len(verifier) == 43
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and "=" not in challenge
    assert auth.make_pkce()[0] != verifier  # random each time


@pytest.mark.parametrize("bad", ["short", "x" * 129, "a" * 42 + "!"])
def test_code_challenge_rejects_invalid_verifier(bad):
    with pytest.raises(ValueError):
        auth.code_challenge(bad)


def test_connect_url_has_etsy_params():
    url = auth.connect_url("KEY", "http://localhost:3003/callback", ["listings_w", "shops_r"],
                           "STATE", "CHAL")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.etsy.com/oauth/connect"
    assert "scope=listings_w%20shops_r" in parts.query  # %20, not +
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert q == {"response_type": "code", "client_id": "KEY", "redirect_uri": "http://localhost:3003/callback",
                 "scope": "listings_w shops_r", "state": "STATE", "code_challenge": "CHAL",
                 "code_challenge_method": "S256"}


def test_parse_callback_accepts_url_or_query():
    assert auth.parse_callback("http://localhost:3003/callback?code=abc&state=S1", "S1") == "abc"
    assert auth.parse_callback("?state=S1&code=xyz", "S1") == "xyz"


@pytest.mark.parametrize("redirect, msg", [
    ("http://localhost/callback?code=abc&state=OTHER", "state mismatch"),
    ("http://localhost/callback?code=abc", "state mismatch"),
    ("http://localhost/callback?state=S1", "no authorization code"),
    ("http://localhost/callback?error=access_denied&error_description=User+denied&state=S1", "access_denied"),
])
def test_parse_callback_refuses(redirect, msg):
    with pytest.raises(AuthError, match=msg):
        auth.parse_callback(redirect, "S1")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback only


def _get(url: str) -> int:
    for _ in range(50):  # the server thread may not be listening yet
        try:
            with _NO_PROXY.open(url, timeout=2) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except urllib.error.URLError:
            time.sleep(0.05)
    raise AssertionError("local server never answered")


def test_wait_for_redirect_catches_only_the_callback_path():
    port = _free_port()
    result = {}
    t = threading.Thread(target=lambda: result.setdefault(
        "url", auth.wait_for_redirect(f"http://localhost:{port}/callback", timeout=10)))
    t.start()
    assert _get(f"http://127.0.0.1:{port}/favicon.ico") == 404
    assert _get(f"http://127.0.0.1:{port}/callback?code=C1&state=S1") == 200
    t.join(5)
    assert result["url"] == "/callback?code=C1&state=S1"


def test_wait_for_redirect_times_out_and_refuses_remote_hosts():
    with pytest.raises(AuthError, match="no redirect"):
        auth.wait_for_redirect(f"http://localhost:{_free_port()}/callback", timeout=0.6)
    with pytest.raises(AuthError, match="not this machine"):
        auth.wait_for_redirect("https://example.com/callback", timeout=1)
    assert auth.can_catch_locally("http://localhost:3003/callback")
    assert not auth.can_catch_locally("https://localhost:3003/callback")
    assert auth.can_catch_locally("https://localhost:3003/callback", has_tls=True)


def _paste_flow(settings, tamper_state: bool = False):
    store = MemoryTokenStore()
    client = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, token_store=store)
    printed = []

    def read_line(prompt):
        url = next(line.strip() for line in printed if "oauth/connect" in line)
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        state = "forged" if tamper_state else q["state"]
        printed.append(q)
        return f"https://localhost:3003/callback?code=THE-CODE&state={state}"

    tok = auth.authorize(client, "https://localhost:3003/callback", ["listings_w"], paste=True,
                         out=printed.append, read_line=read_line)
    return client, store, printed, tok


def test_authorize_paste_flow_exchanges_code_with_matching_verifier(settings):
    client, store, printed, tok = _paste_flow(settings)
    connect_q = printed[-1]
    (call,) = client.session.calls("oauthToken")
    sent = call["data"]
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "THE-CODE"
    assert sent["client_id"] == client.keystring
    assert sent["redirect_uri"] == "https://localhost:3003/callback"
    assert auth.code_challenge(sent["code_verifier"]) == connect_q["code_challenge"]
    assert store.tokens["access_token"] == tok["access_token"] and tok["user_id"] == 1


def test_authorize_stops_on_state_mismatch(settings):
    with pytest.raises(AuthError, match="state mismatch"):
        _paste_flow(settings, tamper_state=True)


def test_tokens_from_response_sets_expiry_and_user():
    tok = auth.tokens_from_response({"access_token": "777.abc", "refresh_token": "777.def",
                                     "expires_in": 3600, "token_type": "Bearer", "scope": "listings_w"}, now=1000)
    assert tok["expires_at"] == 4600 and tok["user_id"] == 777 and tok["refresh_token"] == "777.def"
    with pytest.raises(AuthError):
        auth.tokens_from_response({"access_token": "1.x"})


def test_token_store_file_mode_600_and_env_fallback(tmp_path):
    path = tmp_path / "tokens.json"
    store = TokenStore(path, env_refresh_token="9.from-env")
    assert store.load() == {"refresh_token": "9.from-env", "expires_at": 0, "source": "env"}
    store.save({"access_token": "9.a", "refresh_token": "9.file", "expires_at": 5})
    assert store.load()["refresh_token"] == "9.file"  # the file wins over the env seed
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert TokenStore(tmp_path / "none.json").load() is None


def test_idle_connection_does_not_hold_the_catcher_past_its_timeout():
    port = _free_port()
    errors = []
    t = threading.Thread(target=lambda: errors.append(_expect_timeout(port)))
    t.start()
    for _ in range(50):
        try:
            idle = socket.create_connection(("127.0.0.1", port), timeout=1)  # never sends
            break
        except OSError:
            time.sleep(0.05)
    started = time.monotonic()
    t.join(8)
    idle.close()
    assert not t.is_alive() and errors == [True] and time.monotonic() - started < 6


def _expect_timeout(port: int) -> bool:
    try:
        auth.wait_for_redirect(f"http://localhost:{port}/callback", timeout=1.0)
    except AuthError:
        return True
    return False
