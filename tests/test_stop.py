import importlib.util
import io
import wave
import json
import subprocess
import sys
import threading
import urllib.error
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
            if self.path.endswith('/cancel'):
                self.send_error(404)
                return
            calls.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            queue['queue_pending' if self.path == '/queue' else 'queue_running'] = []
            self.send_response(200)
            self.end_headers()
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
            if path.endswith('/cancel'):
                raise urllib.error.HTTPError(path, 404, 'missing', {}, None)
            calls.append((path, body))
            queue['queue_pending' if path == '/queue' else 'queue_running'] = []
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


@pytest.mark.parametrize('recorded,current', [(None, 10), (10, None), (10, 11)])
def test_unconfirmed_pid_is_never_signalled(recorded, current):
    from music_studio.jobs import terminate_render
    with patch('music_studio.jobs.process_alive', return_value=True), \
            patch('music_studio.jobs._stat_start_time', return_value=current), \
            patch('music_studio.procs.kill_tree') as kill:
        outcome = terminate_render(4100, recorded)
        kill.assert_not_called()
        assert outcome == 'unconfirmed'


def test_publish_before_submission():
    from music_studio.engines.minimax_m3_comfy import MiniMaxM3ComfyEngine
    engine = MiniMaxM3ComfyEngine()
    recorded = []
    def submit(path, body):
        assert recorded, 'submitted before recording a cancellable identity'
        assert recorded[0]['prompt_id'] == body['prompt_id']
        assert recorded[0]['client_id'] == body['client_id']
        raise TimeoutError
    with patch.object(engine, '_post', side_effect=submit), pytest.raises(TimeoutError):
        engine._render_graph({}, None, on_remote=recorded.append)


def test_late_submission_uses_job_cancel():
    from music_studio.jobs import cancel_remote
    with patch('music_studio.engines.minimax_m3_comfy.MiniMaxM3ComfyEngine') as cls:
        engine = cls.return_value
        engine._post.side_effect = [{'cancelled': False}, {'cancelled': True}]
        assert cancel_remote({'kind': 'comfyui', 'url': 'http://localhost:8188', 'prompt_id': 'ours'}) is None
        assert [args.args for args in engine._post.call_args_list] == [('/api/jobs/ours/cancel', {})] * 2


def test_legacy_delete_racing_start():
    from music_studio.jobs import cancel_remote
    calls = []
    def post(path, body):
        if path.endswith('/cancel'):
            raise urllib.error.HTTPError(path, 404, 'missing', {}, None)
        calls.append((path, body))
    with patch('music_studio.engines.minimax_m3_comfy.MiniMaxM3ComfyEngine') as cls:
        engine = cls.return_value
        empty = {'queue_pending': [], 'queue_running': []}
        engine._get.side_effect = [{**empty, 'queue_pending': [[1, 'ours']]},
                                   {**empty, 'queue_running': [[1, 'ours']]}, empty]
        engine._post.side_effect = post
        assert cancel_remote({'kind': 'comfyui', 'url': 'http://localhost:8188', 'prompt_id': 'ours'}) is None
        assert calls == [('/queue', {'delete': ['ours']}), ('/interrupt', {'prompt_id': 'ours'})]


@pytest.mark.parametrize('path', ['/queue', '/interrupt'])
def test_empty_cancel_response(path):
    from music_studio.engines.minimax_m3_comfy import MiniMaxM3ComfyEngine
    with patch('music_studio.network.open_url', return_value=io.BytesIO()):
        MiniMaxM3ComfyEngine(base_url='http://localhost:8188')._post(path, {})


def test_worker_continues_after_first_queued_stop(tmp_path):
    from music_studio import worker
    store = Store(tmp_path)
    project = store.create(title='Batch', caption='music', lyrics='words')
    project['approved'] = True
    store._save(project)
    jobs = Jobs(store)
    pid = project['project_id']
    first, second = jobs.enqueue_batch(pid, 2)
    jobs.stop(pid, first['take_id'])
    def render(jobs, store, project_id, take_id, release):
        try:
            take = jobs.claim(project_id, take_id, 12345)
        except ValueError:
            return worker.EXIT_NOT_CLAIMABLE, None
        jobs.succeed(project_id, take_id, _tone())
        return worker.EXIT_OK, take['batch_id']
    with patch.object(worker, '_render_take', render), patch('signal.signal'):
        worker.main(['--project', pid, '--take', first['take_id'], '--data-dir', str(tmp_path)])
    assert jobs._take(store.get(pid), second['take_id'])['state'] == 'succeeded'


@pytest.mark.parametrize('alive,killed,expected', [
    (False, False, 'The render had already ended; it is marked stopped.'),
    (True, True, 'Render stopped.'),
    (True, False, 'Stop sent, but this computer could not confirm the render process ended.')])
def test_local_stop_detail(job, alive, killed, expected):
    store, jobs, pid, tid = job
    with patch('music_studio.jobs._stat_start_time', return_value=123), \
            patch('music_studio.jobs.process_alive', return_value=alive), \
            patch('music_studio.procs.kill_tree', return_value=killed):
        jobs.start(pid, tid, 4100)
        assert jobs.stop(pid, tid)['detail'] == expected


def test_ace_remote_stop_detail(job):
    store, jobs, pid, tid = job
    project = store.get(pid)
    jobs._take(project, tid)['engine_id'] = 'ace_remote'
    store.save_take(project)
    jobs.start(pid, tid)
    assert 'stopped here; the ACE server may finish the job' in jobs.stop(pid, tid)['detail']


def test_legacy_queue_matches_submission_client_after_late_arrival():
    from music_studio.jobs import cancel_remote
    queue = {'queue_running': [], 'queue_pending': []}
    other = [1, 'another-prompt', {}, {'client_id': 'someone'}]
    ours = [2, 'minted-by-server', {}, {'client_id': 'our-submission'}]
    with patch('music_studio.engines.minimax_m3_comfy.MiniMaxM3ComfyEngine') as cls:
        engine = cls.return_value
        engine._post.side_effect = [urllib.error.HTTPError('cancel', 404, 'missing', {}, None), {}]
        engine._get.side_effect = [queue, {**queue, 'queue_pending': [other, ours]},
                                   {**queue, 'queue_pending': [other]}]
        assert cancel_remote({'kind': 'comfyui', 'url': 'http://localhost:8188',
                              'prompt_id': 'requested', 'client_id': 'our-submission'}) is None
        assert engine._post.call_args.args == ('/queue', {'delete': ['minted-by-server']})


def test_explicit_prompt_id_rejection_retries_same_client_only():
    from music_studio.engines.minimax_m3_comfy import EngineError, MiniMaxM3ComfyEngine
    engine = MiniMaxM3ComfyEngine(base_url='http://localhost:8188')
    requests, recorded = [], []
    def submit(path, body):
        requests.append(dict(body))
        if len(requests) == 1:
            raise EngineError('unsupported prompt_id', engine_id=engine.id) from urllib.error.HTTPError(
                path, 400, 'bad field', {}, None)
        return {'prompt_id': 'server-id'}
    def record(info):
        recorded.append(info)
        if len(recorded) == 2:
            raise InterruptedError
    with patch.object(engine, '_post', side_effect=submit), pytest.raises(InterruptedError):
        engine._render_graph({}, None, on_remote=record)
    assert requests[0]['prompt_id'] == recorded[0]['prompt_id']
    assert 'prompt_id' not in requests[1]
    assert requests[0]['client_id'] == requests[1]['client_id'] == recorded[0]['client_id']
    assert recorded[1]['prompt_id'] == 'server-id'
