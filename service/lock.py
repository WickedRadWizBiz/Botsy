"""One service command at a time: an exclusive lock on data/.service.lock.

A path-triggered tick, the daily timer tick and a command typed by hand can start
together, and all of them write data/research_inputs and the item folders. So every
command takes this lock first; the second one waits (service.lock_timeout_s, 30
minutes) and then gives up with exit code 3. The lock is fcntl.flock on an open file,
so the kernel drops it when the process ends: a crashed tick never leaves a stale lock.

The server is Linux. On Windows (someone running the tests on their PC) msvcrt.locking
stands in, so the test suite still runs there.
"""
from __future__ import annotations

import errno
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

try:  # Linux and macOS
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

log = logging.getLogger("service.lock")

EXIT_LOCKED = 3
_BUSY = {errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK, getattr(errno, "EDEADLK", -1),
         getattr(errno, "EDEADLOCK", -1)}


class LockTimeout(RuntimeError):
    """Another service command held the lock for longer than the timeout."""


def _try_lock(fd: int) -> bool:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as e:
        if e.errno in _BUSY or isinstance(e, BlockingIOError):
            return False
        raise
    return True


def _unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    else:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


@contextmanager
def service_lock(path: str | Path, timeout_s: float, *, poll_s: float = 0.5):
    """Hold the service lock for the `with` block, waiting up to timeout_s for it.

    Raises LockTimeout when another process keeps it longer. The file holds the
    holder's process id, only so a person can see who has it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o640)
    try:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        waiting = False
        while not _try_lock(fd):
            if not waiting:  # say so once: a command typed by hand would otherwise look stuck
                waiting = True
                log.info("another Botsy service command is running; waiting up to %.0f minutes for it",
                         float(timeout_s) / 60)
            left = deadline - time.monotonic()
            if left <= 0:
                raise LockTimeout(
                    f"another Botsy service command is still running (lock {path}); waited "
                    f"{float(timeout_s):g} s. It will pick up any new uploads itself.")
            time.sleep(min(poll_s, left))
        try:
            try:
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, f"{os.getpid()}\n".encode())
            except OSError:
                pass  # the note is for people; the lock itself is what matters
            yield path
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
