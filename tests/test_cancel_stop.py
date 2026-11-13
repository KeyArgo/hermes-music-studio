"""W6: cancelling a render must stop it, and a kill that did not work must be reported."""
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ace_engine  # noqa: E402
from music_studio import procs  # noqa: E402


class _Proc:
    def __init__(self, pid=4321):
        self.pid = pid
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def test_windows_kill_tree_reports_whether_the_pid_is_gone(monkeypatch):
    monkeypatch.setattr(procs, 'WINDOWS', True)
    monkeypatch.setattr(procs, 'KILL_WAIT_SECONDS', 0)
    issued = []
    monkeypatch.setattr(procs.subprocess, 'run',
                        lambda cmd, **kw: issued.append(cmd) or SimpleNamespace(returncode=0))

    monkeypatch.setattr(procs, 'pid_exists', lambda pid: True)
    assert procs.kill_tree(4321) is False
    assert issued and issued[0] == ['taskkill', '/PID', '4321', '/T', '/F']

    monkeypatch.setattr(procs, 'pid_exists', lambda pid: False)
    assert procs.kill_tree(4321) is True


@pytest.mark.skipif(os.name == 'nt', reason='POSIX SIGTERM/SIGKILL path')
def test_posix_kill_tree_escalates_after_the_grace_period(monkeypatch):
    with tempfile.TemporaryDirectory() as root:
        ready = Path(root) / 'ready'
        child = subprocess.Popen([
            sys.executable, '-c',
            'import pathlib, signal, time; '
            'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
            f"pathlib.Path({str(ready)!r}).write_text('x'); time.sleep(30)"])
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists(), 'child never installed its SIGTERM handler'
            monkeypatch.setattr(procs, 'TERM_GRACE_SECONDS', 0.2)
            monkeypatch.setattr(procs, 'KILL_WAIT_SECONDS', 2.0)
            assert procs.kill_tree(child.pid) is True
            child.wait(timeout=5)
            assert child.poll() is not None
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


def test_failed_kill_is_logged_and_raised(monkeypatch, caplog):
    monkeypatch.setattr(procs, 'kill_tree', lambda pid: False)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match='could not stop the render process 4321'):
            ace_engine._kill_process_group(_Proc(4321))
    assert 'could not stop the render process 4321' in caplog.text


def test_successful_kill_returns_quietly(monkeypatch):
    monkeypatch.setattr(procs, 'kill_tree', lambda pid: True)
    assert ace_engine._kill_process_group(_Proc(4321)) is None
