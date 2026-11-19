"""Round 13, fix 2: after an update the studio host must run the new code.

``hermes plugins install --force`` replaces the plugin folder, but the host started from the old
folder is still alive and still answers ``/api/health`` as a token-gated host, so the launcher reused
it: the pane kept last version's page and last version's routes until somebody killed the process by
hand. The launcher now records a fingerprint of the code it started from and stops a host whose
fingerprint is different — except while that host is rendering, because a render outlives the page
that started it.

Everything here is faked: no port is bound, no process is started and no signal is sent.
"""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class _FakeProc:
    pid = 4242
    returncode = None

    def poll(self):
        return None

    def terminate(self):
        return None


class _Reply:
    """What urllib gives back for a 200 with a JSON body."""

    status = 200

    def __init__(self, body=b'{}'):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def _write_state(tmp_path, **fields):
    from music_studio import host_launcher

    state = {'port': 8775, 'pid': 111, 'token': 'old-token', **fields}
    host_launcher._write_state(tmp_path, state)
    return state


def _fake_launcher(monkeypatch, tmp_path, *, stamp='current', rendering=False, gated=True):
    """The launcher's decisions, with the process, the socket and the network taken out."""
    from music_studio import host_launcher

    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(host_launcher, 'code_stamp', lambda root=None: stamp)
    monkeypatch.setattr(host_launcher, '_probe',
                        lambda port, timeout=1.0: {'alive': gated, 'token_gated': gated})
    monkeypatch.setattr(host_launcher, '_alive', lambda port, timeout=1.0: True)
    monkeypatch.setattr(host_launcher, '_rendering', lambda port, token, timeout=3.0: rendering)
    monkeypatch.setattr(host_launcher, '_python', lambda: sys.executable)
    monkeypatch.setattr(host_launcher, '_port_free', lambda port: True)
    stopped, started = [], []

    def fake_stop(state, port):
        stopped.append((dict(state), port))
        return True

    monkeypatch.setattr(host_launcher, '_stop_host', fake_stop)
    monkeypatch.setattr(host_launcher.subprocess, 'Popen',
                        lambda *args, **kwargs: started.append(kwargs.get('env') or {}) or _FakeProc())
    return stopped, started


# --------------------------------------------------------------- reuse or replace


def test_a_host_running_older_code_is_stopped_and_replaced(monkeypatch, tmp_path):
    from music_studio import host_launcher

    _write_state(tmp_path, code='older-build')
    stopped, started = _fake_launcher(monkeypatch, tmp_path)

    result = host_launcher.ensure_host()

    assert result['started'] is True, 'an old host was reused, so the update never reached the pane'
    assert [port for _, port in stopped] == [8775], 'the old host must be stopped, on its own port'
    assert stopped[0][0]['pid'] == 111, 'only the pid the state file records is stopped'
    assert started and started[0]['HEWN_STUDIO_TOKEN'], 'a fresh host is started with a token'
    assert started[0]['HEWN_STUDIO_TOKEN'] != 'old-token', 'and not with the stopped host\'s token'
    recorded = host_launcher._read_state(tmp_path)
    assert recorded is not None
    assert recorded['code'] == 'current', 'the new host records the code it runs'
    assert recorded['pid'] == 4242


def test_a_host_running_the_same_code_is_reused(monkeypatch, tmp_path):
    from music_studio import host_launcher

    _write_state(tmp_path, code='current')
    stopped, started = _fake_launcher(monkeypatch, tmp_path)

    result = host_launcher.ensure_host()

    assert result['started'] is False, 'a host on the same code is the host we want'
    assert result['token'] == 'old-token', 'it keeps the token it was started with'
    assert stopped == [] and started == [], 'nothing was stopped and nothing was spawned'


def test_a_state_file_from_before_the_stamp_counts_as_stale(monkeypatch, tmp_path):
    # A host started by the previous launcher has no `code` field at all: it cannot prove it is current.
    from music_studio import host_launcher

    _write_state(tmp_path)          # the old shape: port, pid, token
    stopped, started = _fake_launcher(monkeypatch, tmp_path)

    result = host_launcher.ensure_host()

    assert result['started'] is True
    assert [port for _, port in stopped] == [8775]
    assert started


def test_a_rendering_host_is_kept_and_named_in_the_log(monkeypatch, tmp_path):
    from music_studio import host_launcher

    _write_state(tmp_path, code='older-build')
    stopped, started = _fake_launcher(monkeypatch, tmp_path, rendering=True)

    result = host_launcher.ensure_host()

    assert result['started'] is False, 'a host with a take in flight must not be cut off'
    assert stopped == [] and started == []
    log = (tmp_path / host_launcher.LOG_FILE).read_text(encoding='utf-8')
    lines = [line for line in log.splitlines() if 'older code' in line]
    assert lines, f'the log must say why the old host stayed: {log!r}'
    assert 'render' in lines[0], 'and that a render is what keeps it'


def test_a_busy_check_that_cannot_read_the_status_counts_as_rendering(monkeypatch):
    """A status call we cannot read must not become a licence to kill a render."""
    from music_studio import host_launcher

    def refuse(*args, **kwargs):
        raise urllib.error.URLError('connection refused')

    monkeypatch.setattr(host_launcher.urllib.request, 'urlopen', refuse)

    assert host_launcher._rendering(8775, 'token') is True
    assert host_launcher._rendering(8775, '') is True, 'no token means no way to ask'


def test_a_running_take_is_read_from_the_status_route(monkeypatch):
    from music_studio import host_launcher

    body = b'{"projects": [{"takes": [{"state": "running"}]}]}'
    seen = []

    def fake_urlopen(request, timeout=None):
        seen.append(request)
        return _Reply(body)

    monkeypatch.setattr(host_launcher.urllib.request, 'urlopen', fake_urlopen)

    assert host_launcher._rendering(8775, 'tok') is True
    assert seen[0].full_url.endswith('/api/status')
    assert seen[0].get_header('X-studio-token') == 'tok'

    body = b'{"projects": [{"takes": [{"state": "done"}]}]}'
    assert host_launcher._rendering(8775, 'tok') is False


# --------------------------------------------------------------- how a stale host is stopped


def test_the_hosts_own_close_route_is_used_first(monkeypatch, tmp_path):
    from music_studio import host_launcher

    requests = []
    killed = []
    monkeypatch.setattr(host_launcher.urllib.request, 'urlopen',
                        lambda request, timeout=None: requests.append(request) or _Reply())
    monkeypatch.setattr(host_launcher, '_port_free', lambda port: True)
    monkeypatch.setattr(host_launcher, '_probe',
                        lambda port, timeout=1.0: {'alive': True, 'token_gated': True})
    monkeypatch.setattr('music_studio.procs.kill_tree', lambda pid: killed.append(pid) or True)

    state = _write_state(tmp_path)

    assert host_launcher._stop_host(state, 8775) is True
    assert requests and requests[0].full_url.endswith('/api/host/close')
    assert requests[0].get_method() == 'POST'
    assert requests[0].get_header('X-studio-token') == 'old-token', 'the route needs the token'
    assert killed == [], 'a host that closed itself is not killed'


def test_a_host_without_the_route_is_killed_by_its_recorded_pid(monkeypatch, tmp_path):
    from music_studio import host_launcher

    killed = []

    def refuse(*args, **kwargs):
        raise urllib.error.URLError('no such route')

    monkeypatch.setattr(host_launcher.urllib.request, 'urlopen', refuse)
    monkeypatch.setattr(host_launcher, '_port_free', lambda port: True)
    monkeypatch.setattr(host_launcher, '_probe',
                        lambda port, timeout=1.0: {'alive': True, 'token_gated': True})
    monkeypatch.setattr('music_studio.procs.kill_tree', lambda pid: killed.append(pid) or True)

    state = _write_state(tmp_path)

    assert host_launcher._stop_host(state, 8775) is True
    assert killed == [111], 'the tree-kill helper (taskkill on Windows) gets the recorded pid'


def test_a_recycled_pid_is_not_killed(monkeypatch, tmp_path):
    """The OS hands pids out again: only a host that still answers as ours may be killed."""
    from music_studio import host_launcher

    killed = []

    def refuse(*args, **kwargs):
        raise urllib.error.URLError('no such route')

    monkeypatch.setattr(host_launcher.urllib.request, 'urlopen', refuse)
    monkeypatch.setattr(host_launcher, '_port_free', lambda port: True)
    monkeypatch.setattr(host_launcher, '_probe',
                        lambda port, timeout=1.0: {'alive': True, 'token_gated': False})
    monkeypatch.setattr('music_studio.procs.kill_tree', lambda pid: killed.append(pid) or True)

    state = _write_state(tmp_path)

    host_launcher._stop_host(state, 8775)
    assert killed == [], 'a pid is only killed while the port answers as our token-gated host'


# --------------------------------------------------------------- the stamp itself


def _fake_plugin(root: Path, *, body='console.log(1);'):
    (root / 'music_studio' / 'engines').mkdir(parents=True)
    (root / 'music_studio' / '__init__.py').write_text('', encoding='utf-8')
    (root / 'music_studio' / 'host.py').write_text('print(1)\n', encoding='utf-8')
    (root / 'music_studio' / 'engines' / 'ace.py').write_text('print(2)\n', encoding='utf-8')
    (root / 'standalone').mkdir()
    (root / 'standalone' / 'app.py').write_text('app = None\n', encoding='utf-8')
    (root / 'standalone' / 'static').mkdir()
    for name in ('index.html', 'app.js', 'style.css'):
        text = body if name == 'app.js' else name
        (root / 'standalone' / 'static' / name).write_text(text, encoding='utf-8')
    return root


def test_the_stamp_follows_the_page_and_every_module(monkeypatch, tmp_path):
    from music_studio import host_launcher

    plugin = _fake_plugin(tmp_path / 'plugin')
    first = host_launcher.code_stamp(plugin)
    assert len(first) == 16 and first.isalnum()

    # Cached per process, because ensure_host runs on every pane open.
    assert host_launcher.code_stamp(plugin) == first

    for path in (plugin / 'standalone' / 'static' / 'app.js',
                 plugin / 'music_studio' / 'engines' / 'ace.py'):
        text = path.read_text(encoding='utf-8')
        path.write_text(text + '\n// new build\n', encoding='utf-8')
        monkeypatch.setattr(host_launcher, '_code_stamps', {})   # the next process
        assert host_launcher.code_stamp(plugin) != first, f'{path} is not covered by the stamp'
        first = host_launcher.code_stamp(plugin)


def test_the_stamp_covers_the_file_list_it_promises(monkeypatch, tmp_path):
    from music_studio import host_launcher

    plugin = _fake_plugin(tmp_path / 'plugin')
    names = [path.relative_to(plugin).as_posix()
             for path in host_launcher._code_files(plugin)]

    assert 'music_studio/host.py' in names
    assert 'music_studio/engines/ace.py' in names, 'a subpackage module is code the host runs'
    assert 'standalone/app.py' in names
    for page in ('index.html', 'app.js', 'style.css'):
        assert f'standalone/static/{page}' in names
    assert not [name for name in names if '__pycache__' in name]


def test_the_stamp_is_taken_over_the_folder_this_launcher_runs_from():
    from music_studio import host_launcher

    repo = Path(host_launcher.__file__).resolve().parents[1]
    assert repo == ROOT
    assert host_launcher.code_stamp() == host_launcher.code_stamp(repo)
