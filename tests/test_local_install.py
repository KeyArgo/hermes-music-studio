import hashlib
import http.server
import threading
import time
from pathlib import Path

import pytest

from music_studio import local_install as install


@pytest.fixture
def server(monkeypatch):
    data = b'abcdef0123456789' * 65536
    requests = []
    started = threading.Event()
    release = threading.Event()
    slow = [False]

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get('Range'), self.headers.get('User-Agent')))
            offset = int(self.headers['Range'][6:-1]) if self.headers.get('Range') else 0
            self.send_response(206 if offset else 200)
            if offset:
                self.send_header('Content-Range', f'bytes {offset}-{len(data)-1}/{len(data)}')
            self.send_header('Content-Length', str(len(data) - offset))
            self.end_headers()
            try:
                for pos in range(offset, len(data), 16384):
                    self.wfile.write(data[pos:pos + 16384])
                    self.wfile.flush()
                    if slow[0]:
                        started.set()
                        release.wait(3)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(install, 'BASE_URL', f'http://127.0.0.1:{httpd.server_port}')
    monkeypatch.setattr(install, 'ACE15_FILES', [{'path': 'split/test.bin', 'folder': 'diffusion_models',
                                                  'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}])
    yield data, requests, started, release, slow
    release.set()
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def wait(job):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = install.status(job)
        if result['state'] not in ('running', 'cancelling'):
            return result
        time.sleep(.01)
    pytest.fail('download did not finish')


def models(tmp_path):
    path = tmp_path / 'ComfyUI Windows' / 'models'
    path.mkdir(parents=True)
    return path


def test_download(server, tmp_path):
    data, requests, *_ = server
    root = models(tmp_path)
    result = wait(install.start(str(root))['job_id'])
    assert result['state'] == 'done'
    assert result['done_bytes'] == result['total_bytes'] == len(data)
    assert result['files_done'] == result['files_total'] == 1
    assert (root / 'diffusion_models' / 'test.bin').read_bytes() == data
    assert requests == [('/split/test.bin', None, 'hermes-music-studio')]


def test_resume(server, tmp_path):
    data, requests, *_ = server
    root = models(tmp_path)
    part = root / 'diffusion_models' / 'test.bin.part'
    part.parent.mkdir()
    part.write_bytes(data[:12345])
    assert wait(install.start(str(root))['job_id'])['state'] == 'done'
    assert requests[0][1] == 'bytes=12345-'
    assert part.with_suffix('').read_bytes() == data


def test_bad_hash_removes_part(server, tmp_path, monkeypatch):
    root = models(tmp_path)
    monkeypatch.setitem(install.ACE15_FILES[0], 'sha256', '0' * 64)
    result = wait(install.start(str(root))['job_id'])
    assert result['state'] == 'failed'
    assert 'checksum' in result['error']
    assert not (root / 'diffusion_models' / 'test.bin').exists()
    assert not (root / 'diffusion_models' / 'test.bin.part').exists()


def test_cancel_and_single_job(server, tmp_path):
    _, _, started, release, slow = server
    slow[0] = True
    root = models(tmp_path)
    job = install.start(str(root))['job_id']
    assert started.wait(3)
    assert install.start(str(root))['job_id'] == job
    install.cancel(job)
    release.set()
    assert wait(job)['state'] == 'cancelled'
    assert (root / 'diffusion_models' / 'test.bin.part').exists()


def test_existing_final_is_never_overwritten(server, tmp_path):
    data, requests, *_ = server
    root = models(tmp_path)
    final = root / 'diffusion_models' / 'test.bin'
    final.parent.mkdir()
    final.write_bytes(b'x' * len(data))
    result = wait(install.start(str(root))['job_id'])
    assert result['state'] == 'failed'
    assert result['error'] == 'test.bin is already in the folder but is damaged. Delete it and press Resume.'
    assert final.read_bytes() == b'x' * len(data)
    assert not requests


def test_existing_wrong_size_is_not_overwritten(server, tmp_path):
    root = models(tmp_path)
    final = root / 'diffusion_models' / 'test.bin'
    final.parent.mkdir()
    final.write_bytes(b'keep')
    assert wait(install.start(str(root))['job_id'])['state'] == 'failed'
    assert final.read_bytes() == b'keep'


def test_plan_math(server, tmp_path, monkeypatch):
    data, *_ = server
    root = models(tmp_path)
    monkeypatch.setattr(install.shutil, 'disk_usage', lambda path: (100, 50, 999999999))
    result = install.plan(str(root))
    assert result['need_bytes'] == len(data) + 64 * 1024 * 1024
    assert result['free_bytes'] == 999999999 and result['enough_space']
    final = root / 'diffusion_models' / 'test.bin'
    final.parent.mkdir()
    final.write_bytes(data)
    result = install.plan(str(root))
    assert result['files'][0]['present']
    assert result['need_bytes'] == 64 * 1024 * 1024


def test_invalid_folder(tmp_path):
    assert install.plan(str(tmp_path))['message'] == 'Choose the folder ComfyUI calls models.'
    with pytest.raises(ValueError, match='Choose the folder'):
        install.start(str(tmp_path))


def test_suggest(monkeypatch, tmp_path):
    monkeypatch.setattr(install.setup, 'find_local_comfy_models', lambda: tmp_path)
    assert install.suggest_models_dir() == str(tmp_path)


def test_look_local(monkeypatch):
    urls = []
    def fail(request, timeout):
        urls.append(request.full_url)
        raise OSError('offline')
    monkeypatch.setattr(install.urllib.request, 'urlopen', fail)
    result = install.look_local()
    assert result['ports_checked'] == [8000, 8188]
    assert urls == ['http://127.0.0.1:8000/system_stats', 'http://127.0.0.1:8188/system_stats']
    assert not any(item['ok'] for item in result['found'])

@pytest.fixture
def offline(monkeypatch):
    data = b'abcdef' * 1000
    monkeypatch.setattr(install, 'ACE15_FILES', [{'path': 'split/test.bin', 'folder': 'diffusion_models', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}])
    return data


def test_plan_subtracts_partial(offline, tmp_path, monkeypatch):
    root = models(tmp_path)
    part = root / 'diffusion_models' / 'test.bin.part'
    part.parent.mkdir()
    part.write_bytes(offline[:123])
    monkeypatch.setattr(install.shutil, 'disk_usage', lambda path: (0, 0, len(offline) - 123 + install._MARGIN))
    result = install.plan(str(root))
    assert result['need_bytes'] == len(offline) - 123 + install._MARGIN
    assert result['enough_space']


def test_short_read_keeps_partial(offline, tmp_path, monkeypatch):
    import io
    class Response(io.BytesIO):
        status = 200
        headers = {}
    monkeypatch.setattr(install.urllib.request, 'urlopen', lambda *a, **k: Response(offline[:100]))
    root = models(tmp_path)
    result = wait(install.start(str(root))['job_id'])
    assert result['state'] == 'failed'
    assert 'connection dropped, press Resume' in result['error']
    assert (root / 'diffusion_models' / 'test.bin.part').read_bytes() == offline[:100]


@pytest.mark.parametrize('response_status,content_range', [(200, ''), (206, 'bytes 1-10/100')])
def test_bad_range_restarts_with_fresh_request(offline, tmp_path, monkeypatch, response_status, content_range):
    import io
    root = models(tmp_path)
    part = root / 'diffusion_models' / 'test.bin.part'
    part.parent.mkdir()
    part.write_bytes(offline[:123])
    requests = []
    class Response(io.BytesIO):
        def __init__(self, body, status, headers):
            super().__init__(body)
            self.status, self.headers = status, headers
    def fetch(request, timeout):
        requests.append(request.get_header('Range'))
        if len(requests) == 1:
            return Response(offline[1:100], response_status, {'Content-Range': content_range})
        return Response(offline, 200, {})
    monkeypatch.setattr(install.urllib.request, 'urlopen', fetch)
    assert wait(install.start(str(root))['job_id'])['state'] == 'done'
    assert requests == ['bytes=123-', None]
    assert part.with_suffix('').read_bytes() == offline


def test_running_folder_and_cancel_restart(offline, tmp_path, monkeypatch):
    import io
    started, release = threading.Event(), threading.Event()
    class Response(io.BytesIO):
        status = 200
        headers = {}
        def read(self, size):
            started.set()
            release.wait(3)
            return super().read(size)
    monkeypatch.setattr(install.urllib.request, 'urlopen', lambda *a, **k: Response(offline))
    root = models(tmp_path)
    other = tmp_path / 'other' / 'models'
    other.mkdir(parents=True)
    job = install.start(str(root))['job_id']
    try:
        assert started.wait(3)
        assert install.start(str(root / '..' / 'models'))['job_id'] == job
        with pytest.raises(ValueError, match='Another download is already running for'):
            install.start(str(other))
        assert install.cancel(job)['state'] == 'cancelling'
        timer = threading.Timer(.1, release.set)
        timer.start()
        new = install.start(str(root))['job_id']
        timer.join()
        assert new != job
        assert wait(new)['state'] == 'done'
    finally:
        release.set()


@pytest.mark.parametrize('value', [None, '', '  ', 123, Path('models')])
def test_models_dir_requires_nonempty_string(value):
    for function in (install.plan, install.start):
        with pytest.raises(ValueError, match='Choose the folder ComfyUI calls models'):
            function(value)


@pytest.mark.parametrize('variable,subdir', [('OneDrive', 'Documents/ComfyUI/models'), ('APPDATA', 'ComfyUI/models')])
def test_suggest_windows_folders(monkeypatch, tmp_path, variable, subdir):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    monkeypatch.delenv('OneDrive', raising=False)
    monkeypatch.delenv('APPDATA', raising=False)
    monkeypatch.setenv(variable, str(tmp_path / 'windows'))
    target = tmp_path / 'windows' / subdir
    target.mkdir(parents=True)
    assert install.setup.find_local_comfy_models() == target
    assert install.suggest_models_dir() == str(target)


def test_existing_final_checksum(offline, tmp_path):
    root = models(tmp_path)
    final = root / 'diffusion_models' / 'test.bin'
    final.parent.mkdir()
    final.write_bytes(b'x' * len(offline))
    result = wait(install.start(str(root))['job_id'])
    assert result['state'] == 'failed'
    assert result['error'] == 'test.bin is already in the folder but is damaged. Delete it and press Resume.'
    assert final.read_bytes() == b'x' * len(offline)
    final.write_bytes(offline)
    result = wait(install.start(str(root))['job_id'])
    assert result['state'] == 'done'
    assert result['done_bytes'] == len(offline) and result['files_done'] == 1
