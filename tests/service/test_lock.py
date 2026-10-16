"""service.lock: one service command at a time; the second waits, then times out (exit 3)."""
from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from core.config import ROOT
from service import lock as lockmod
from service.__main__ import main
from service.lock import LockTimeout, service_lock

from ._support import make_settings

HOLDER = """
import sys
from service.lock import service_lock
with service_lock(sys.argv[1], 5):
    print("locked", flush=True)
    sys.stdin.read()          # hold the lock until the test closes stdin
"""


@pytest.fixture
def holder(tmp_path):
    """Another process holding the lock (like a long tick)."""
    path = tmp_path / "data" / ".service.lock"
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(path)], cwd=ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "locked"
        yield path, proc
    finally:
        if proc.poll() is None:
            proc.stdin.close()
            proc.wait(timeout=10)


def test_lock_can_be_taken_again_after_release(tmp_path):
    path = tmp_path / "x.lock"
    with service_lock(path, 1):
        pass
    with service_lock(path, 1) as held:
        assert held == path
        assert path.read_text().strip().isdigit()     # the holder's pid, for people


def test_second_process_times_out(holder):
    path, _ = holder
    start = time.monotonic()
    with pytest.raises(LockTimeout, match="still running"), service_lock(path, 0.4, poll_s=0.05):
        pytest.fail("got a lock another process holds")
    assert 0.35 <= time.monotonic() - start < 5


def test_lock_is_released_when_the_holder_ends(holder):
    path, proc = holder
    proc.stdin.close()
    proc.wait(timeout=10)
    with service_lock(path, 2, poll_s=0.05):
        pass


def test_waits_for_a_release_within_the_timeout(tmp_path):
    path = tmp_path / "x.lock"
    got = threading.Event()
    release = threading.Event()

    def hold():
        with service_lock(path, 1):
            got.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    assert got.wait(5)
    threading.Timer(0.3, release.set).start()
    start = time.monotonic()
    with service_lock(path, 5, poll_s=0.05):    # flock is per open file, so threads conflict too
        waited = time.monotonic() - start
    t.join(5)
    assert 0.2 <= waited < 5


def test_cli_exits_3_while_another_command_runs(holder, tmp_path, capsys):
    path, _ = holder
    _, settings_path = make_settings(tmp_path, service={"lock_file": str(path), "lock_timeout_s": 0.3})
    assert main(["status", "--settings", str(settings_path)]) == lockmod.EXIT_LOCKED == 3
    assert "another Botsy service command" in capsys.readouterr().err


def test_waiting_is_announced_once(holder, caplog):
    path, _ = holder
    with caplog.at_level("INFO", logger="service.lock"):
        with pytest.raises(LockTimeout):
            with service_lock(path, 0.3, poll_s=0.05):
                pass
    waits = [r for r in caplog.records if "waiting up to" in r.getMessage()]
    assert len(waits) == 1
