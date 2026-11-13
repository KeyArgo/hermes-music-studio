"""The pane frames a loopback host; these tests pin what that host promises.

The host is the shipped standalone app (same UI, same router) started against the
profile's plugin data directory, so the agent tool and the pane read one library.
"""
import os
import signal
import socket
import urllib.request

import pytest
from fastapi.testclient import TestClient

# `conftest` is importable when pytest loads the suite, but a single file run from the repo root needs
# the tests folder on the path to import the shared studio_client helper.
import sys as _sys
from pathlib import Path as _Path
if str(_Path(__file__).resolve().parent) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))


@pytest.fixture()
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'library'))
    import standalone.app as module

    return module


@pytest.fixture()
def client(app_module):
    from conftest import studio_client

    return studio_client(app_module.app)


def test_serves_the_studio_page(client):
    res = client.get('/')
    assert res.status_code == 200
    body = res.text
    assert 'HERMES' in body and 'MUSIC STUDIO' in body
    # The modern UI's rail, not the old console dashboard.
    for nav in ('Create', 'Library', 'Workspaces', 'Compute', 'REVamp'):
        assert nav in body


def test_static_assets_are_the_modern_ui(client):
    css = client.get('/static/style.css')
    assert css.status_code == 200 and 'text/css' in css.headers['content-type']
    js = client.get('/static/app.js')
    assert js.status_code == 200 and 'javascript' in js.headers['content-type']
    assert 'STUDIO' in js.text


def test_api_answers_on_the_host(client):
    status = client.get('/api/status')
    assert status.status_code == 200
    assert status.json()['success'] is True
    sample = client.get('/api/studio/sample')
    assert sample.status_code == 200 and sample.content[:4] == b'RIFF'


def test_writes_follow_the_same_rules_as_standalone(client):
    created = client.post('/api/create', json={'draft': {
        'title': 'host test', 'caption': 'quiet', 'lyrics': 'one line', 'duration_seconds': 12}})
    assert created.status_code == 200
    project = created.json()['project']
    assert project['approved'] is False


def test_refuses_a_non_loopback_host_header(client):
    res = client.get('/', headers={'host': 'evil.example.com'})
    assert res.status_code == 400


def test_a_cross_site_write_without_the_token_is_refused(client):
    # The client fixture carries the token in a default header, so this case names what it must not
    # have: a page the person merely visited has no token, and its write is refused at the origin.
    from conftest import studio_client

    bare = studio_client(client.app)
    del bare.headers['X-Studio-Token']
    res = bare.post('/api/create', headers={'sec-fetch-site': 'cross-site',
                                            'origin': 'https://evil.example'},
                    json={'draft': {'title': 'x'}})
    assert res.status_code == 403


def test_a_cross_site_write_with_the_token_is_served(client):
    """The sandboxed pane is exactly this request: opaque origin, cross-site, token present."""
    res = client.post('/api/create', headers={'sec-fetch-site': 'cross-site', 'origin': 'null'},
                      json={'draft': {'title': 'from the pane'}})
    assert res.status_code == 200, res.text
    assert client.get('/api/status').status_code == 200


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _stop_host(root):
    import json
    state = root / 'studio-host.json'
    try:
        pid = json.loads(state.read_text(encoding='utf-8')).get('pid')
    except (OSError, ValueError):
        pid = None
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass


def _start_and_check(tmp_path, monkeypatch, extra_env=None):
    from music_studio import host_launcher

    library = tmp_path / 'library'
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(library))
    port = _free_port()
    monkeypatch.setenv('HEWN_STUDIO_HOST_PORT', str(port))
    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)
    info = host_launcher.ensure_host()
    try:
        assert info['port'] == port and info['started'] is True
        probe = urllib.request.Request(f"{info['url']}/api/status")
        probe.add_header('X-Studio-Token', info.get('token') or '')
        with urllib.request.urlopen(probe, timeout=5) as res:
            assert res.status == 200
        again = host_launcher.ensure_host()
        assert again['port'] == port and again['started'] is False
        assert (library / host_launcher.STATE_FILE).is_file()
    finally:
        _stop_host(library)


def test_launcher_starts_a_host_on_the_plugin_library(tmp_path, monkeypatch):
    _start_and_check(tmp_path, monkeypatch)


def test_launcher_survives_the_gateway_pythonpath(tmp_path, monkeypatch):
    """The gateway exports PYTHONPATH at its own venv; a system interpreter that
    inherits it dies importing pydantic (wrong minor version)."""
    _start_and_check(tmp_path, monkeypatch, {
        'PYTHONPATH': '/home/alex/.hermes/installs/does-not-exist/venv/lib/python3.14/site-packages',
        'PYTHONHOME': '/home/alex/.hermes/installs/does-not-exist/venv',
    })


def test_the_host_is_started_with_the_profile_base_not_the_moved_library(tmp_path, monkeypatch):
    """Settings and host state stay in the profile; the library may live elsewhere.

    Passing the redirected library as HEWN_STUDIO_DATA_DIR made the pane read settings from a
    different place than the agent process, so engine and export choices could disagree.
    """
    from music_studio import host_launcher, service

    base = tmp_path / 'profile'
    moved = tmp_path / 'moved-library'
    base.mkdir()
    moved.mkdir()
    monkeypatch.setattr(service, 'default_data_dir', lambda plugin_id: base)
    monkeypatch.setattr(service, 'plugin_data_dir', lambda plugin_id: moved)

    assert host_launcher.data_dir() == base
    assert host_launcher.data_dir() != moved
