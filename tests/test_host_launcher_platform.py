"""Finding an interpreter and a free port on a real Windows/macOS install.

CI ran on Linux, where /usr/bin/python3.13 and sys.executable both exist and a port with
SO_REUSEADDR still refuses a second bind. The launcher now tries the interpreters people
actually have, can spawn the Windows ``py -3`` launcher, and asks Windows for exclusive use
of a port instead of politely sharing it.

These read the shipped module and fake the platform, the probe and the socket, so they fail on
the reviewed tree without needing Windows or a second Python install.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class _Probe:
    def __init__(self, returncode):
        self.returncode = returncode


def _probe_only(monkeypatch, works):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return _Probe(0 if works(command) else 1)

    monkeypatch.setattr('music_studio.host_launcher.subprocess.run', fake_run)
    return calls


def _no_interpreter(monkeypatch):
    monkeypatch.delenv('HEWN_STUDIO_HOST_PYTHON', raising=False)
    monkeypatch.setattr('music_studio.host_launcher.os.path.exists', lambda path: False)
    monkeypatch.setattr('music_studio.host_launcher.shutil.which', lambda name: None)


def test_windows_tries_the_py_launcher_as_a_command_list(monkeypatch):
    from music_studio import host_launcher

    _no_interpreter(monkeypatch)
    monkeypatch.setattr(host_launcher.sys, 'platform', 'win32')
    monkeypatch.setattr(host_launcher.shutil, 'which',
                        lambda name: '/fake/py' if name == 'py' else None)
    calls = _probe_only(monkeypatch, lambda command: command[:2] == ['py', '-3'])

    assert host_launcher._python() == ['py', '-3']
    assert calls == [['py', '-3', '-c', 'import uvicorn, fastapi, starlette']]


def test_darwin_tries_homebrew_and_usr_local(monkeypatch):
    from music_studio import host_launcher

    _no_interpreter(monkeypatch)
    monkeypatch.setattr(host_launcher.sys, 'platform', 'darwin')
    monkeypatch.setattr(host_launcher.os.path, 'exists',
                        lambda path: path == '/opt/homebrew/bin/python3')
    _probe_only(monkeypatch, lambda command: command[0] == '/opt/homebrew/bin/python3')

    assert host_launcher._python() == ['/opt/homebrew/bin/python3']


def test_the_python_beside_sys_executable_is_tried(monkeypatch):
    from music_studio import host_launcher

    _no_interpreter(monkeypatch)
    monkeypatch.setattr(host_launcher.sys, 'platform', 'linux')
    monkeypatch.setattr(host_launcher.sys, 'executable', '/fake/venv/bin/python3.13')
    sibling = '/fake/venv/bin/python'
    monkeypatch.setattr(host_launcher.os.path, 'exists',
                        lambda path: path in ('/fake/venv/bin/python3.13', sibling))
    _probe_only(monkeypatch, lambda command: command[0] == sibling)

    assert host_launcher._python() == [sibling]


def test_the_failure_message_names_the_candidates_and_the_install_command(monkeypatch):
    from music_studio import host_launcher

    _no_interpreter(monkeypatch)
    monkeypatch.setattr(host_launcher.sys, 'platform', 'win32')
    _probe_only(monkeypatch, lambda command: False)

    with pytest.raises(RuntimeError) as error:
        host_launcher._python()

    message = str(error.value)
    assert 'tried' in message and '/usr/bin/python3.13' in message
    assert 'py -3 -m pip install "fastapi" "uvicorn[standard]"' in message


def test_the_spawner_accepts_a_command_list(monkeypatch, tmp_path):
    from music_studio import host_launcher

    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(host_launcher, '_python', lambda: ['py', '-3'])
    monkeypatch.setattr(host_launcher, '_port_free', lambda port: True)
    monkeypatch.setattr(host_launcher, '_alive', lambda port, timeout=1.0: True)
    captured = {}

    class FakeProc:
        pid = 4321

        def poll(self):
            return None

        def terminate(self):
            return None

    def fake_popen(args, **kwargs):
        captured['args'] = list(args)
        return FakeProc()

    monkeypatch.setattr(host_launcher.subprocess, 'Popen', fake_popen)

    result = host_launcher.ensure_host()

    assert result['started'] is True
    assert captured['args'] == ['py', '-3', '-m', 'music_studio.host']


class _RecordingSocket:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def setsockopt(self, level, option, value):
        self.options.append(option)

    def bind(self, address):
        return None


def _fake_socket(monkeypatch, factory):
    from music_studio import host_launcher

    monkeypatch.setattr(host_launcher.socket, 'SO_EXCLUSIVEADDRUSE', 0x1234, raising=False)
    monkeypatch.setattr(host_launcher.socket, 'socket', lambda *args, **kwargs: factory())


def test_windows_asks_for_exclusive_use_not_reuse(monkeypatch):
    from music_studio import host_launcher

    monkeypatch.setattr(host_launcher.sys, 'platform', 'win32')
    options = []
    _fake_socket(monkeypatch, lambda: _RecordingSocket(options))

    assert host_launcher._port_free(8775) is True
    assert host_launcher.socket.SO_REUSEADDR not in options
    assert 0x1234 in options


def test_windows_reports_a_port_in_use_as_busy(monkeypatch):
    from music_studio import host_launcher

    monkeypatch.setattr(host_launcher.sys, 'platform', 'win32')
    options = []

    class BusySocket(_RecordingSocket):
        def bind(self, address):
            # What Windows really does with SO_REUSEADDR: the bind succeeds on a busy port.
            if host_launcher.socket.SO_REUSEADDR in self.options:
                return None
            error = OSError(10048, 'address in use')
            error.winerror = 10048
            raise error

    _fake_socket(monkeypatch, lambda: BusySocket(options))

    assert host_launcher._port_free(8775) is False


def test_posix_keeps_reuseaddr(monkeypatch):
    from music_studio import host_launcher

    monkeypatch.setattr(host_launcher.sys, 'platform', 'linux')
    options = []
    _fake_socket(monkeypatch, lambda: _RecordingSocket(options))

    assert host_launcher._port_free(8775) is True
    assert host_launcher.socket.SO_REUSEADDR in options


def test_the_scan_covers_at_least_fifty_ports():
    from music_studio import host_launcher

    assert host_launcher.PORT_SPAN >= 50


def test_the_pane_shows_the_hosts_message_instead_of_a_hardcoded_python():
    pane = (ROOT / 'desktop' / 'plugin.js').read_text(encoding='utf-8')
    assert 'python3.13 -m music_studio.host' not in pane
    assert 'host.error' in pane
