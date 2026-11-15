"""Bring-your-own ComfyUI workflows: placeholders, validation, store, health, render.

The render test runs against a tiny HTTP server that speaks the real ComfyUI
routes (``/object_info``, ``/prompt``, ``/history/<id>``, ``/view``) and serves a
real PCM WAV built with the ``wave`` module — never synthetic silence.
"""
from __future__ import annotations

import io
import json
import sys
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio.engines import RenderRequest  # noqa: E402
from music_studio.engines.comfy_workflow import ComfyWorkflowEngine  # noqa: E402
from music_studio.network import TargetStore  # noqa: E402
from music_studio.workflows import (MAX_WORKFLOW_BYTES, WorkflowStore,  # noqa: E402
                                    engine_id, find_placeholders,
                                    substitute_placeholders, validate_workflow)


def tone_wav(seconds=1.0, rate=8000, channels=1):
    frames = int(seconds * rate)
    values = [int(3000 * (1 if i % 2 else -1)) for i in range(frames)]
    interleaved = [v for v in values for _ in range(channels)]
    import struct
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        out.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
        out.writeframes(struct.pack(f'<{len(interleaved)}h', *interleaved))
    return buf.getvalue()


def graph(**inputs):
    fields = {'style': '{{style}}', 'lyrics': '{{lyrics}}',
              'duration': '{{duration}}', 'seed': '{{seed}}'}
    fields.update(inputs)
    return {
        '1': {'class_type': 'TextEncode', 'inputs': fields},
        '2': {'class_type': 'SaveAudio',
              'inputs': {'audio': ['1', 0], 'filename_prefix': 'old/prefix'}},
    }


def record(tmp_path, workflow=None, name='Test graph'):
    target = TargetStore(tmp_path).create('Test box', 'http://192.0.2.9:8188')
    return WorkflowStore(tmp_path).create(name, target['id'], workflow or graph())


def request(**kw):
    fields = {'caption': 'warm folk', 'lyrics': 'la la la', 'duration_seconds': 42.5,
              'seed': 7, 'title': 'Kettle song'}
    fields.update(kw)
    return RenderRequest(**fields)


class StubComfy:
    """A minimal ComfyUI server for one render, with configurable node types."""

    def __init__(self, nodes=('TextEncode', 'SaveAudio'), wav=None, missing=(),
                 system_stats=True):
        self.nodes = [n for n in nodes if n not in missing]
        self.system_stats = system_stats
        self.wav = wav or tone_wav()
        self.submitted: list[dict] = []
        self._queued: dict[str, float] = {}
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                return

            def _send(self, code, body=b'', content_type='application/json'):
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == '/system_stats':
                    if not stub.system_stats:
                        return self._send(404, {'error': path})
                    return self._send(200, {'system': {'comfyui_version': 'stub'},
                                            'devices': [{'name': 'Stub GPU'}]})
                if path == '/object_info':
                    return self._send(200, {node: {'input': {'required': {}}}
                                            for node in stub.nodes})
                if path.startswith('/history/'):
                    prompt_id = path.rsplit('/', 1)[-1]
                    if prompt_id not in stub._queued:
                        return self._send(200, {})
                    return self._send(200, {prompt_id: {
                        'status': {'status_str': 'success', 'completed': True},
                        'outputs': {'2': {'audio': [{
                            'filename': 'music-studio_00001.wav',
                            'subfolder': 'music-studio', 'type': 'output'}]}}}})
                if path == '/view':
                    return self._send(200, stub.wav, 'audio/wav')
                return self._send(404, {'error': path})

            def do_POST(self):
                if urlsplit(self.path).path != '/prompt':
                    return self._send(404, {'error': 'no route'})
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}')
                stub.submitted.append(body.get('prompt') or {})
                prompt_id = uuid.uuid4().hex
                stub._queued[prompt_id] = time.monotonic()
                return self._send(200, {'prompt_id': prompt_id})

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

        self.httpd = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    @property
    def url(self):
        host, port = self.httpd.server_address[:2]
        return f'http://{host}:{port}'


# -- placeholders ----------------------------------------------------------
def test_exact_placeholders_become_typed_values():
    out = substitute_placeholders(graph(), request())
    inputs = out['1']['inputs']
    assert inputs['style'] == 'warm folk'
    assert inputs['lyrics'] == 'la la la'
    assert inputs['duration'] == 42.5 and type(inputs['duration']) is float
    assert inputs['seed'] == 7 and type(inputs['seed']) is int


def test_placeholders_inside_longer_strings_are_text():
    out = substitute_placeholders(graph(title='Song: {{title}}', seed='seed {{seed}}'),
                                  request(title='Kettle song'))
    inputs = out['1']['inputs']
    assert inputs['title'] == 'Song: Kettle song'
    assert inputs['seed'] == 'seed 7'


def test_instrumental_sends_empty_lyrics_and_a_bool():
    out = substitute_placeholders(graph(instrumental='{{instrumental}}'),
                                  request(instrumental=True, lyrics='ignored'))
    inputs = out['1']['inputs']
    assert inputs['lyrics'] == ''
    assert inputs['instrumental'] is True


def test_save_node_prefix_is_forced():
    out = substitute_placeholders(graph(), request())
    assert out['2']['inputs']['filename_prefix'] == 'music-studio/workflow'


def test_placeholder_report_lists_what_was_found():
    assert find_placeholders(graph()) == ('lyrics', 'style', 'duration', 'seed')
    found = find_placeholders(graph(extra='before {{title}} after'))
    assert 'title' in found


# -- validation ------------------------------------------------------------
def test_validation_rejects_oversized_uploads(tmp_path):
    huge = '{{style}}' + ('x' * MAX_WORKFLOW_BYTES)
    target = TargetStore(tmp_path).create('box', 'http://192.0.2.9:8188')
    with pytest.raises(ValueError, match='larger than'):
        WorkflowStore(tmp_path).create('huge', target['id'],
                                      graph(style=huge))


def test_validation_requires_a_save_audio_node():
    bad = {'1': {'class_type': 'TextEncode', 'inputs': {'style': '{{style}}'}}}
    with pytest.raises(ValueError, match='SaveAudio'):
        validate_workflow(bad)


def test_validation_rejects_two_save_nodes():
    bad = graph()
    bad['3'] = {'class_type': 'SaveAudioAdvanced', 'inputs': {}}
    with pytest.raises(ValueError, match='more than one SaveAudio'):
        validate_workflow(bad)


def test_validation_requires_style_or_lyrics_and_names_what_it_saw():
    with pytest.raises(ValueError) as caught:
        validate_workflow(graph(style='plain', lyrics='plain', duration='{{duration}}',
                                seed='{{seed}}'))
    message = str(caught.value)
    assert '{{style}} or {{lyrics}}' in message
    assert '{{duration}}' in message and '{{seed}}' in message


def test_validation_rejects_a_non_graph():
    with pytest.raises(ValueError, match='non-empty dict'):
        validate_workflow(['not', 'a', 'graph'])
    with pytest.raises(ValueError, match='no class_type'):
        validate_workflow({'1': {'inputs': {}}})


# -- store -----------------------------------------------------------------
def test_store_create_list_delete(tmp_path):
    target = TargetStore(tmp_path).create('box', 'http://192.0.2.9:8188')
    created = WorkflowStore(tmp_path).create('Mine', target['id'], graph())
    listed = WorkflowStore(tmp_path).list()
    assert [w['id'] for w in listed] == [created['id']]
    assert listed[0]['name'] == 'Mine' and listed[0]['target_id'] == target['id']
    assert listed[0]['placeholders'] == ['lyrics', 'style', 'duration', 'seed']
    assert WorkflowStore(tmp_path).get(created['id'])['workflow']['2']['class_type'] == 'SaveAudio'
    removed = WorkflowStore(tmp_path).delete(created['id'])
    assert removed['id'] == created['id']
    assert WorkflowStore(tmp_path).list() == []
    with pytest.raises(KeyError):
        WorkflowStore(tmp_path).delete(created['id'])


def test_store_refuses_an_unknown_target(tmp_path):
    with pytest.raises(ValueError, match='unknown target'):
        WorkflowStore(tmp_path).create('Mine', str(uuid.uuid4()), graph())


# -- health ----------------------------------------------------------------
def test_health_lists_every_missing_node_type(tmp_path):
    with StubComfy(nodes=('TextEncode',)) as server:
        engine = ComfyWorkflowEngine(record(tmp_path), base_url=server.url,
                                     gpu_lease_path=tmp_path / '.lease')
        status = engine.health()
    assert status.state == 'unavailable'
    assert 'missing: SaveAudio' in status.detail
    assert status.extra['missing_nodes'] == ['SaveAudio']


def test_health_is_ready_when_the_server_has_every_node(tmp_path):
    with StubComfy() as server:
        engine = ComfyWorkflowEngine(record(tmp_path), base_url=server.url,
                                     gpu_lease_path=tmp_path / '.lease')
        status = engine.health()
    assert status.state == 'ready', status.detail
    checks = {c.name: c for c in status.checks}
    assert checks['http'].ok and checks['nodes'].ok and checks['placeholders'].ok


def test_object_info_alone_is_enough_for_health(tmp_path):
    with StubComfy(system_stats=False) as server:
        engine = ComfyWorkflowEngine(record(tmp_path), base_url=server.url,
                                     gpu_lease_path=tmp_path / '.lease')
        status = engine.health()
    assert status.state == 'ready', status.detail
    assert [c.name for c in status.checks] == ['gpu_lease', 'http', 'nodes', 'placeholders']


# -- render ----------------------------------------------------------------
def test_render_through_a_stub_comfyui_produces_a_wav(tmp_path):
    wav = tone_wav(1.0)
    with StubComfy(wav=wav) as server:
        engine = ComfyWorkflowEngine(record(tmp_path), base_url=server.url,
                                     gpu_lease_path=tmp_path / '.lease')
        engine.poll_seconds = 0.01
        result = engine.render(request())
    assert result.wav == wav
    assert result.metadata['engine_id'].startswith('comfy_workflow:')
    assert result.metadata['workflow_name'] == 'Test graph'
    assert result.metadata['source_format'] == 'wav'
    assert result.metadata['sample_rate'] == 8000
    assert result.metadata['audio_seconds'] == 1.0
    submitted = server.submitted[0]
    assert submitted['1']['inputs']['style'] == 'warm folk'
    assert submitted['1']['inputs']['seed'] == 7
    assert submitted['2']['inputs']['filename_prefix'] == 'music-studio/workflow'


# -- API -------------------------------------------------------------------
@pytest.fixture()
def api_client(monkeypatch, tmp_path):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    monkeypatch.setenv('HEWN_STUDIO_TOKEN', 'token-abc')
    from standalone import app as host_app
    from dashboard.plugin_api import router

    app = FastAPI()
    app.middleware('http')(host_app.same_origin)
    app.include_router(router, prefix='/api')
    return TestClient(app, base_url='http://127.0.0.1:8775')


def test_workflow_routes_need_the_token(api_client):
    assert api_client.get('/api/studio/workflows').status_code == 401
    assert api_client.post('/api/studio/workflows', json={}).status_code == 401
    assert api_client.delete('/api/studio/workflows/' + str(uuid.uuid4())).status_code == 401
    assert api_client.post('/api/studio/workflows/' + str(uuid.uuid4()) + '/test').status_code == 401
    response = api_client.get('/api/studio/workflows',
                              headers={'X-Studio-Token': 'token-abc'})
    assert response.status_code == 200
    assert response.json()['workflows'] == []


def test_api_uploads_lists_deletes_a_workflow(api_client, monkeypatch, tmp_path):
    from music_studio import service
    from music_studio.network import TargetStore

    target = TargetStore(service.store().root).create('box', 'http://192.0.2.9:8188')
    auth = {'X-Studio-Token': 'token-abc'}
    created = api_client.post('/api/studio/workflows',
                              json={'name': 'Mine', 'target_id': target['id'],
                                    'workflow': graph()}, headers=auth)
    assert created.status_code == 200, created.text
    workflow = created.json()['workflow']
    assert workflow['name'] == 'Mine'

    listed = api_client.get('/api/studio/workflows', headers=auth).json()['workflows']
    assert [w['id'] for w in listed] == [workflow['id']]
    assert api_client.delete('/api/studio/workflows/' + workflow['id'],
                             headers=auth).status_code == 200
    assert api_client.get('/api/studio/workflows', headers=auth).json()['workflows'] == []


def test_a_workflow_is_a_draft_engine_choice(api_client, monkeypatch):
    from music_studio import service
    from music_studio.network import TargetStore

    target = TargetStore(service.store().root).create('box', 'http://192.0.2.9:8188')
    auth = {'X-Studio-Token': 'token-abc'}
    workflow = api_client.post('/api/studio/workflows',
                               json={'name': 'Mine', 'target_id': target['id'],
                                     'workflow': graph()}, headers=auth).json()['workflow']
    draft = api_client.post('/api/create', json={'draft': {
        'caption': 'tune', 'lyrics': 'words', 'duration_seconds': 30,
        'engine': engine_id(workflow['id'])}}, headers=auth)
    assert draft.status_code == 200, draft.text
    assert draft.json()['project']['engine'] == engine_id(workflow['id'])
    reg = service.registry()
    assert reg.get(engine_id(workflow['id'])).id == engine_id(workflow['id'])


def test_a_workflow_take_is_not_refused_for_the_drafts_minimax_target():
    """Found on the real ACE-Step 1.5 run: the workflow's own endpoint is the pinned target, and the
    draft names no MiniMax target, so the pin check refused every workflow render."""
    from music_studio import jobs
    from music_studio.workflows import engine_id

    manager = jobs.Jobs.__new__(jobs.Jobs)
    target = {'id': '4c0eafb7-9a5b-40ea-9c7d-367928da2290', 'url': 'http://10.0.0.52:8188', 'name': 'E'}
    workflow_engine = engine_id('6fc6fd1b-4770-447d-a180-67da812a8d1b')
    manager._check_target({'comfy_target_id': None}, workflow_engine, target)
    manager._check_target({'comfy_target_id': 'other'}, workflow_engine, target)
    with pytest.raises(ValueError):
        manager._check_target({'comfy_target_id': None}, 'minimax_m3_comfy', target)
