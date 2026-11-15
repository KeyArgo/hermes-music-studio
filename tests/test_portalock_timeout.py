"""A held Windows lock must fail with the lock's name, not retry forever.

``msvcrt.locking`` has no blocking mode here, so the retry loop is ours. On a
stale lock the old loop lived for the whole process; CI could only show it as a
hang. This uses a fake clock so the test proves the bound without waiting the real timeout.
"""
from types import SimpleNamespace

import pytest

from music_studio import portalock


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _windows_lock_that_never_succeeds(monkeypatch, clock):
    def locked(*args):
        raise OSError(13, 'the file is locked')

    monkeypatch.setattr(portalock, 'fcntl', None)
    monkeypatch.setattr(portalock, 'msvcrt',
                        SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locked),
                        raising=False)
    monkeypatch.setattr(portalock, 'time',
                        SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))


def test_a_held_windows_lock_gives_up_and_names_the_file(tmp_path, monkeypatch):
    clock = _Clock()
    _windows_lock_that_never_succeeds(monkeypatch, clock)
    lock_path = tmp_path / 'ace-studio-gpu.lock'
    with lock_path.open('a') as lock:
        with pytest.raises(TimeoutError, match='ace-studio-gpu.lock'):
            portalock.lock_ex(lock)

    # The loop waited the whole budget (the fake clock only moves when it sleeps).
    assert clock.now <= portalock.LOCK_TIMEOUT_SECONDS + 1
    assert sum(clock.sleeps) >= portalock.LOCK_TIMEOUT_SECONDS
