import importlib.util
import io
import wave
import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.plugin_api import router
from music_studio.jobs import Jobs
from music_studio.store import Store


def _tone():
    data = io.BytesIO()
    with wave.open(data, 'wb') as audio:
        audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x00\x00' * 100)
    return data.getvalue()


@pytest.fixture
def job(tmp_path):
    store = Store(tmp_path)
    project = store.create(title='Stop', caption='music', lyrics='words')
    project['approved'] = True
    store._save(project)
    jobs = Jobs(store)
    take = jobs.enqueue(project['project_id'], seed=1)
    return store, jobs, project['project_id'], take['take_id']


def test_stop_queued_and_finished(job):
    store, jobs, pid, tid = job
    assert jobs.stop(pid, tid)['stopped']
    take = jobs._take(store.get(pid), tid)
    assert take['state'] == 'cancelled' and take['finished_at']
    assert jobs.stop(pid, tid) == {'stopped': False, 'state': 'cancelled', 'detail': 'already finished'}
    with pytest.raises(ValueError, match='invalid transition'):
        jobs.succeed(pid, tid, _tone())
    assert jobs._take(store.get(pid), tid)['state'] == 'cancelled'


def test_stop_running_kills_worker_and_releases_lease(job):
    store, jobs, pid, tid = job
    code = 'import sys,time; from music_studio.store import Store; x=Store(sys.argv[1]).worker_lock(); print("ready",flush=True); time.sleep(60)'
    proc = subprocess.Popen([sys.executable, '-c', code, str(store.root)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == 'ready'
        jobs.start(pid, tid, proc.pid)
        assert jobs.stop(pid, tid)['stopped']
        proc.wait(timeout=5)
        lease = store.worker_lock()
        assert lease is not None
        lease.close()
        assert jobs._take(store.get(pid), tid)['state'] == 'cancelled'
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()


@pytest.mark.parametrize('queue,expected', [
    ({'queue_pending': [[1, 'ours']], 'queue_running': []}, ('/queue', {'delete': ['ours']})),
    ({'queue_pending': [], 'queue_running': [[1, 'ours']]}, ('/interrupt', {'prompt_id': 'ours'})),
    ({'queue_pending': [], 'queue_running': [[1, 'other']]}, None),
])
def test_stop_comfy_queue(job, queue, expected):
    store, jobs, pid, tid = job
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(queue).encode())
        def do_POST(self):
            calls.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self, *args):
            pass
    try:
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    except PermissionError:
        pytest.skip('sandbox disallows TCP sockets')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        jobs.start(pid, tid)
        jobs.attach_remote(pid, tid, {'kind': 'comfyui', 'url': f'http://127.0.0.1:{server.server_port}', 'prompt_id': 'ours'})
        assert jobs.stop(pid, tid)['stopped']
        assert calls == ([] if expected is None else [expected])
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_stop_route_and_tool(job):
    store, jobs, pid, tid = job
    from dashboard.plugin_api import take_stop
    with patch('music_studio.service.store', return_value=store):
        result = take_stop({'project_id': pid, 'take_id': tid})
        assert result['stopped']
        assert any(route.path == '/take/stop' for route in router.routes)
        spec = importlib.util.spec_from_file_location('plugin_stop_test', '__init__.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert 'stop' in module.SCHEMA['parameters']['properties']['action']['enum']
        assert module._handler({'action': 'stop', 'project_id': pid, 'take_id': tid})['detail'] == 'already finished'


@pytest.mark.parametrize('queue,expected', [
    ({'queue_pending': [[1, 'ours']], 'queue_running': []}, ('/queue', {'delete': ['ours']})),
    ({'queue_pending': [], 'queue_running': [[1, 'ours']]}, ('/interrupt', {'prompt_id': 'ours'})),
    ({'queue_pending': [], 'queue_running': [[1, 'other']]}, None),
])
def test_stop_comfy_transport(job, queue, expected):
    store, jobs, pid, tid = job
    calls = []
    class Engine:
        def __init__(self, **kwargs):
            pass
        def _get(self, path):
            assert path == '/queue'
            return queue
        def _post(self, path, body):
            calls.append((path, body))
    jobs.start(pid, tid)
    jobs.attach_remote(pid, tid, {'kind': 'comfyui', 'url': 'http://127.0.0.1:8188', 'prompt_id': 'ours'})
    with patch('music_studio.engines.minimax_m3_comfy.MiniMaxM3ComfyEngine', Engine):
        assert jobs.stop(pid, tid)['stopped']
    assert calls == ([] if expected is None else [expected])


def test_stopping_a_running_batch_take_cancels_its_queued_siblings(tmp_path):
    # The worker that renders a batch is killed by the stop, so the batch's queued takes have no
    # one left to render them. They are cancelled with a reason, not left to fail as "worker died".
    store = Store(tmp_path)
    project = store.create(title='Batch', caption='music', lyrics='words')
    project['approved'] = True
    store._save(project)
    jobs = Jobs(store)
    pid = project['project_id']
    takes = jobs.enqueue_batch(pid, 3)
    takes = takes['takes'] if isinstance(takes, dict) else takes
    first, *rest = [t['take_id'] for t in takes]
    jobs.start(pid, first)
    assert jobs.stop(pid, first)['stopped']
    saved = {t['take_id']: t for t in store.get(pid)['takes']}
    assert saved[first]['state'] == 'cancelled'
    for take_id in rest:
        assert saved[take_id]['state'] == 'cancelled'
        assert saved[take_id]['error'] == 'Stopped with the rest of its batch'


def test_stopping_one_queued_take_leaves_its_batch_alone(tmp_path):
    store = Store(tmp_path)
    project = store.create(title='Batch', caption='music', lyrics='words')
    project['approved'] = True
    store._save(project)
    jobs = Jobs(store)
    pid = project['project_id']
    takes = jobs.enqueue_batch(pid, 2)
    takes = takes['takes'] if isinstance(takes, dict) else takes
    first, second = [t['take_id'] for t in takes]
    assert jobs.stop(pid, second)['stopped']
    saved = {t['take_id']: t for t in store.get(pid)['takes']}
    assert saved[first]['state'] == 'queued'
    assert saved[second]['state'] == 'cancelled'


def test_stop_before_the_prompt_id_is_recorded_still_cancels_the_server_prompt(job):
    # The race: ComfyUI accepted the prompt, the person pressed Stop, and only then did the worker
    # try to record the prompt id. The take is already cancelled, so the worker cancels the prompt.
    from music_studio import worker
    store, jobs, pid, tid = job
    info = {'kind': 'comfyui', 'url': 'http://127.0.0.1:8188', 'prompt_id': 'late'}
    cancelled = []

    class Registry:
        def render(self, request, *, engine_id=None, on_progress=None, on_process=None, on_remote=None):
            jobs.stop(pid, tid)
            on_remote(info)
            raise AssertionError('on_remote must raise once the take is stopped')

    with patch.object(worker, '_registry', return_value=Registry()), \
            patch.object(worker, '_render_request', return_value=SimpleNamespace(mode='generate')), \
            patch.object(worker, '_enforce_capability'), \
            patch('music_studio.jobs.terminate_render') as terminate, \
            patch('music_studio.jobs.cancel_remote', side_effect=lambda remote: cancelled.append(remote) if remote else None):
        code, _ = worker._render_take(jobs, store, pid, tid)
    assert code == worker.EXIT_NOT_CLAIMABLE
    # The claimed pid is this test process: stop must have tried to end it, and the patch kept it alive.
    assert terminate.called
    assert cancelled == [info]
    assert jobs._take(store.get(pid), tid)['state'] == 'cancelled'
