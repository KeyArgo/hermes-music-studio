"""File locks that work on POSIX (fcntl.flock) and Windows (msvcrt.locking).

A non-blocking attempt that finds the lock held raises ``BlockingIOError`` on both platforms, so
callers keep one ``except BlockingIOError`` branch. On Windows the lock covers the first byte of the
file, which every caller opens in append mode only to hold the lock.
"""

from __future__ import annotations

import time

# Windows has no blocking msvcrt lock, so the retry loop has to give up on its
# own. A stale lock is a bug to report, not a reason to wedge the worker forever.
LOCK_TIMEOUT_SECONDS = 120.0

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt


def lock_ex(handle, blocking: bool = True) -> None:
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    handle.seek(0)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if not blocking:
                raise BlockingIOError(str(exc)) from exc
            if time.monotonic() >= deadline:
                name = getattr(handle, 'name', '<unnamed>')
                raise TimeoutError(
                    f'timed out after {LOCK_TIMEOUT_SECONDS:g} s waiting for lock {name}'
                ) from exc
            time.sleep(0.05)


def unlock(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_UN)
        return
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
