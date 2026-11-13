"""W6: the pid-reuse guard must read a start time on Windows and macOS too."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from music_studio import jobs, procs  # noqa: E402


def test_windows_start_time_reads_the_creation_filetime(monkeypatch):
    opened, closed = [], []

    class FILETIME:
        def __init__(self):
            self.dwLowDateTime = 0
            self.dwHighDateTime = 0

    class Kernel32:
        def OpenProcess(self, access, inherit, pid):
            opened.append((access, inherit, pid))
            return 99

        def GetProcessTimes(self, handle, created, exited, kernel, user):
            created.dwLowDateTime = 0x55667788
            created.dwHighDateTime = 0x00000001
            return 1

        def CloseHandle(self, handle):
            closed.append(handle)
            return 1

    fake = SimpleNamespace(WinDLL=lambda *a, **k: Kernel32(),
                           byref=lambda value: value,
                           wintypes=SimpleNamespace(FILETIME=FILETIME))
    monkeypatch.setitem(sys.modules, 'ctypes', fake)
    monkeypatch.setattr(jobs.os, 'name', 'nt')

    assert jobs._stat_start_time(4242) == (1 << 32) | 0x55667788
    assert opened == [(0x1000, False, 4242)]
    assert closed == [99]


def test_macos_start_time_comes_from_ps(monkeypatch):
    seen = []
    monkeypatch.setattr(jobs.os, 'name', 'posix')
    monkeypatch.setattr(jobs.sys, 'platform', 'darwin')
    monkeypatch.setattr(
        jobs.subprocess, 'run',
        lambda cmd, **kw: seen.append(cmd) or SimpleNamespace(
            stdout=b'Fri Nov 13 10:00:00 2026\n'))

    assert jobs._stat_start_time(4242) == 'Fri Nov 13 10:00:00 2026'
    assert seen[0] == ['ps', '-o', 'lstart=', '-p', '4242']


def test_windows_pid_reuse_is_rejected(monkeypatch):
    monkeypatch.setattr(jobs.os, 'name', 'nt')
    monkeypatch.setattr(jobs, '_windows_start_time', lambda pid: 222, raising=False)
    monkeypatch.setattr(procs, 'pid_exists', lambda pid: True)

    assert jobs.process_alive(4242, 111) is False
    assert jobs.process_alive(4242, 222) is True


def test_unknown_start_time_falls_back_to_plain_pid_liveness(monkeypatch):
    monkeypatch.setattr(jobs.os, 'name', 'nt')
    monkeypatch.setattr(jobs, '_windows_start_time', lambda pid: None, raising=False)
    monkeypatch.setattr(procs, 'pid_exists', lambda pid: True)

    assert jobs.process_alive(4242, 111) is True
    monkeypatch.setattr(procs, 'pid_exists', lambda pid: False)
    assert jobs.process_alive(4242, 111) is False
