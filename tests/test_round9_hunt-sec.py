"""Round 9 security hunt: redirects, schemes, symlink writes, child env, copy paths.

A pasted engine list and a test-server URL are http(s) after validation. urllib still
follows a redirect onto ftp, file, or the link-local metadata address, and it forwards
Authorization while it does. The ACE download also appends through a symlink named
``.part``. Windows ``taskkill`` / ``ps`` inherited the whole environment.
"""
from __future__ import annotations

import hashlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from music_studio import discovery, engine_catalog, local_install, setup
from music_studio.engines.ace_remote import AceRemoteEngine
from music_studio.network import TargetStore


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _serve(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_pasted_engines_reject_file_ftp_and_link_local(tmp_path):
    """The import validates every address before anything is saved or fetched."""
    for url in ('file:///etc/passwd', 'ftp://127.0.0.1/x', 'http://169.254.169.254:8188'):
        text = json.dumps({'music_studio_engines': 1, 'engines': [
            {'name': 'x', 'url': url, 'models': ['minimax_m3']}]})
        with pytest.raises(ValueError):
            engine_catalog.import_engines(tmp_path, text)
    assert TargetStore(tmp_path).list() == []


def test_test_server_does_not_follow_a_redirect_to_link_local_or_ftp():
    """The address that was validated is the only address that may be contacted."""
    destinations = []
    real = socket.create_connection

    def spy(address, *args, **kwargs):
        destinations.append(address[0])
        if address[0] not in ('127.0.0.1', '::1', 'localhost'):
            raise OSError(f'refused {address[0]}')
        return real(address, *args, **kwargs)

    class Bounce(_Quiet):
        def do_GET(self):
            target = ('ftp://169.254.169.254/secret' if self.path.startswith('/ftp')
                      else 'http://169.254.169.254/latest/meta-data/')
            self.send_response(302)
            self.send_header('Location', target)
            self.end_headers()

    server, thread = _serve(Bounce)
    socket_mod = socket
    original = socket_mod.create_connection
    socket_mod.create_connection = spy
    try:
        http_result = engine_catalog.test_server(
            'comfyui', f'http://127.0.0.1:{server.server_port}/ok')
        ftp_result = engine_catalog.test_server(
            'comfyui', f'http://127.0.0.1:{server.server_port}/ftp')
    finally:
        socket_mod.create_connection = original
        _stop(server, thread)
    assert '169.254.169.254' not in destinations
    assert http_result['ok'] is False
    assert ftp_result['ok'] is False


def test_a_file_redirect_is_not_read(tmp_path):
    secret = tmp_path / 'secret.txt'
    secret.write_text('TOPSECRET-FILE-REDIRECT')
    opened = []

    def file_open(self, req):
        opened.append(getattr(req, 'full_url', str(req)))
        raise OSError('refused')

    class Bounce(_Quiet):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', secret.as_uri())
            self.end_headers()

    server, thread = _serve(Bounce)
    import urllib.request
    original = urllib.request.FileHandler.file_open
    urllib.request.FileHandler.file_open = file_open
    try:
        result = engine_catalog.test_server(
            'comfyui', f'http://127.0.0.1:{server.server_port}/')
    finally:
        urllib.request.FileHandler.file_open = original
        _stop(server, thread)
    assert opened == []
    assert 'TOPSECRET-FILE-REDIRECT' not in json.dumps(result)


def test_probe_cloud_does_not_send_the_key_to_a_redirect(tmp_path, monkeypatch):
    stolen = []

    class Steal(_Quiet):
        def do_GET(self):
            stolen.append(self.headers.get('Authorization'))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[]}')

    steal, steal_thread = _serve(Steal)

    class Bounce(_Quiet):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', f'http://127.0.0.1:{steal.server_port}/models')
            self.end_headers()

    bounce, bounce_thread = _serve(Bounce)
    monkeypatch.setattr('music_studio.hermes_secrets.read_secret',
                        lambda name: 'sk-should-not-travel')
    try:
        from music_studio import settings
        settings.write(tmp_path, {
            'ace_remote_url': f'http://127.0.0.1:{bounce.server_port}'})
        setup.probe_cloud(tmp_path)
    finally:
        _stop(bounce, bounce_thread)
        _stop(steal, steal_thread)
    assert stolen == []


def test_an_ace_file_url_is_not_opened(tmp_path):
    secret = tmp_path / 'secret.txt'
    secret.write_text('TOPSECRET-ACE')
    opened = []

    def file_open(self, req):
        opened.append(getattr(req, 'full_url', str(req)))
        raise OSError('refused')

    import urllib.request
    original = urllib.request.FileHandler.file_open
    urllib.request.FileHandler.file_open = file_open
    try:
        status = AceRemoteEngine(base_url=secret.as_uri()).health()
    finally:
        urllib.request.FileHandler.file_open = original
    assert opened == []
    text = str(getattr(status, 'detail', status))
    assert 'TOPSECRET-ACE' not in text


def test_settings_refuse_file_and_link_local_engine_urls(tmp_path):
    from music_studio import settings

    with pytest.raises(ValueError):
        settings.write(tmp_path, {'ace_remote_url': 'file:///etc/passwd'})
    with pytest.raises(ValueError):
        settings.write(tmp_path, {'comfy_url': 'http://169.254.169.254:8188'})
    with pytest.raises(ValueError):
        setup.normalise_url('http://169.254.169.254/latest')


def test_discovery_does_not_probe_file_or_link_local():
    urls = discovery.candidates(
        saved='file:///etc/passwd', extra_hosts='169.254.169.254, studio-pc')
    assert not any(url.startswith('file:') or '169.254.169.254' in url for url in urls)
    assert any(url.startswith('http://studio-pc:') for url in urls)


def test_export_text_does_not_contain_the_hosted_key(tmp_path, monkeypatch):
    key = 'sk-live-copy-engines-secret'
    monkeypatch.setattr('music_studio.hermes_secrets.save_secret', lambda name, value: True)
    monkeypatch.setattr('music_studio.hermes_secrets.read_secret', lambda name: key)
    setup.save_cloud(tmp_path, 'default', 'https://api.example.com/v1', key, 'music-3')
    text = engine_catalog.export_engines(tmp_path)['text']
    assert key not in text


def test_download_requests_only_the_pinned_huggingface_url(tmp_path, monkeypatch):
    seen = []

    def fake(request, timeout=None):
        seen.append(request.full_url)
        raise OSError('stop')

    monkeypatch.setattr(local_install, '_fetch', fake)
    monkeypatch.setattr(local_install.shutil, 'disk_usage', lambda path: (0, 0, 10 ** 12))
    data = b'pinned'
    monkeypatch.setattr(local_install, 'ACE15_FILES', [{
        'path': 'split_files/vae/ace_1.5_vae.safetensors',
        'folder': 'vae', 'size': len(data),
        'sha256': hashlib.sha256(data).hexdigest()}])
    root = tmp_path / 'ComfyUI' / 'models'
    root.mkdir(parents=True)
    job = local_install.start(str(root))['job_id']
    deadline = __import__('time').monotonic() + 5
    while __import__('time').monotonic() < deadline:
        result = local_install.status(job)
        if result['state'] not in ('running', 'cancelling'):
            break
        __import__('time').sleep(0.01)
    else:
        pytest.fail('download did not finish')
    assert seen, 'the download never asked for a URL'
    pinned = ('https://huggingface.co/Comfy-Org/ACE-Step-1.5/resolve/'
              '371bb1680f8c88d8c1083f6ffb6330d3d55bff76/')
    assert all(url.startswith(pinned) for url in seen)


def test_a_download_redirect_cannot_reach_link_local_or_ftp(tmp_path, monkeypatch):
    destinations = []
    real = socket.create_connection

    def spy(address, *args, **kwargs):
        destinations.append(address[0])
        if address[0] not in ('127.0.0.1', '::1', 'localhost'):
            raise OSError(f'refused {address[0]}')
        return real(address, *args, **kwargs)

    class Bounce(_Quiet):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', 'ftp://169.254.169.254/model')
            self.end_headers()

    server, thread = _serve(Bounce)
    data = b'pinned-body'
    monkeypatch.setattr(local_install, 'BASE_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setattr(local_install, 'ACE15_FILES', [{
        'path': 'split/model.bin', 'folder': 'vae', 'size': len(data),
        'sha256': hashlib.sha256(data).hexdigest()}])
    monkeypatch.setattr(local_install.shutil, 'disk_usage', lambda path: (0, 0, 10 ** 12))
    root = tmp_path / 'ComfyUI' / 'models'
    root.mkdir(parents=True)
    original = socket.create_connection
    socket.create_connection = spy
    try:
        job = local_install.start(str(root))['job_id']
        deadline = __import__('time').monotonic() + 5
        while __import__('time').monotonic() < deadline:
            result = local_install.status(job)
            if result['state'] not in ('running', 'cancelling'):
                break
            __import__('time').sleep(0.01)
        else:
            pytest.fail('download did not finish')
    finally:
        socket.create_connection = original
        _stop(server, thread)
    assert '169.254.169.254' not in destinations
    assert result['state'] == 'failed'


def test_a_partial_symlink_is_not_appended_to(tmp_path, monkeypatch):
    data = b'abcdef' * 1000
    outside = tmp_path / 'secret.txt'
    outside.write_bytes(b'KEEP')

    class Handler(_Quiet):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server, thread = _serve(Handler)
    monkeypatch.setattr(local_install, 'BASE_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setattr(local_install, 'ACE15_FILES', [{
        'path': 'split/test.bin', 'folder': 'diffusion_models',
        'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}])
    monkeypatch.setattr(local_install.shutil, 'disk_usage', lambda path: (0, 0, 10 ** 12))
    root = tmp_path / 'ComfyUI' / 'models'
    part = root / 'diffusion_models' / 'test.bin.part'
    part.parent.mkdir(parents=True)
    try:
        part.symlink_to(outside)
    except OSError:
        _stop(server, thread)
        pytest.skip('symlinks are not available')
    try:
        job = local_install.start(str(root))['job_id']
        deadline = __import__('time').monotonic() + 5
        while __import__('time').monotonic() < deadline:
            result = local_install.status(job)
            if result['state'] not in ('running', 'cancelling'):
                break
            __import__('time').sleep(0.01)
        else:
            pytest.fail('download did not finish')
    finally:
        _stop(server, thread)
    assert outside.read_bytes() == b'KEEP'


def test_copy_into_comfy_refuses_a_folder_it_did_not_download(tmp_path, monkeypatch):
    comfy = tmp_path / 'ComfyUI' / 'models'
    comfy.mkdir(parents=True)
    monkeypatch.setattr(setup, 'find_local_comfy_models', lambda: comfy)
    secret = tmp_path / 'secret'
    secret.mkdir()
    (secret / 'id_rsa').write_text('PRIVATE')
    with pytest.raises(ValueError):
        setup.copy_into_comfy(str(secret), tmp_path / 'data')
    assert not (comfy / 'diffusion_models' / 'secret').exists()


def test_taskkill_and_ps_do_not_inherit_provider_keys(monkeypatch):
    from music_studio import procs

    monkeypatch.setenv('OPENAI_API_KEY', 'sk-should-not-leak')
    seen = []

    def fake_run(args, **kwargs):
        seen.append(kwargs.get('env'))
        return type('R', (), {'stdout': b'', 'returncode': 0})()

    monkeypatch.setattr(procs.subprocess, 'run', fake_run)
    monkeypatch.setattr(procs, '_windows', lambda: True)
    monkeypatch.setattr(procs, 'WINDOWS', True)
    monkeypatch.setattr(procs, 'pid_exists', lambda pid: True)
    monkeypatch.setattr(procs, 'KILL_WAIT_SECONDS', 0)
    procs.kill_tree(4321)
    assert seen and seen[0] is not None
    assert 'OPENAI_API_KEY' not in seen[0]

    seen.clear()
    monkeypatch.setattr(procs, '_windows', lambda: False)
    monkeypatch.setattr(procs, 'WINDOWS', False)
    procs._gone(2_000_000_000)
    assert seen and seen[0] is not None
    assert 'OPENAI_API_KEY' not in seen[0]
