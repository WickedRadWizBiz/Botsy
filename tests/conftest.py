"""Suite-wide isolation: a developer's or the server's config/settings.local.yaml never
changes test results (docs/SERVER.md section 14).

BOTSY_IGNORE_LOCAL_SETTINGS=1 is set before collection (some test modules read the
settings at import time) and kept for the whole session by an autouse fixture. A test of
the merge itself deletes it with monkeypatch and points core.config.CONFIG_DIR at tmp_path.
"""
from __future__ import annotations

import os

import pytest

IGNORE_LOCAL_ENV = "BOTSY_IGNORE_LOCAL_SETTINGS"   # core.config.IGNORE_LOCAL_ENV
_before: dict[str, str | None] = {}


def pytest_configure(config):
    _before[IGNORE_LOCAL_ENV] = os.environ.get(IGNORE_LOCAL_ENV)
    os.environ[IGNORE_LOCAL_ENV] = "1"


def pytest_unconfigure(config):
    if IGNORE_LOCAL_ENV in _before:
        old = _before.pop(IGNORE_LOCAL_ENV)
        if old is None:
            os.environ.pop(IGNORE_LOCAL_ENV, None)
        else:
            os.environ[IGNORE_LOCAL_ENV] = old


@pytest.fixture(autouse=True, scope="session")
def ignore_local_settings():
    """The whole session reads only config/settings.yaml (never settings.local.yaml)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(IGNORE_LOCAL_ENV, "1")
        yield
