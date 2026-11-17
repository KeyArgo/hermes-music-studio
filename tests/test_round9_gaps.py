"""Regression gaps for the routes and functions added since ba18a43.

Every route added to ``dashboard/plugin_api.py`` since that commit gets its error paths pinned:
bad input is a 400 (never a 500), an unknown id is a 404, a missing token is a 401 on the standalone
host, and the happy path still answers. The engines/install surface is exercised against the real
``engine_catalog`` and ``local_install`` modules here — the older route tests stand them in — and the
standalone host is checked to expose the same routes as the plugin.
"""
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

TOKEN = 'round9-gaps-token'
TARGET_URL = 'http://192.168.1.50:8188'
P = '11111111-1111-4111-8111-111111111111'
T = '22222222-2222-4222-8222-222222222222'


def _host_app():
    """The plugin routes behind the standalone host's own middleware, as app.py mounts them."""
    from dashboard.plugin_api import router
    from standalone import app as host_app

    app = FastAPI()
    app.middleware('http')(host_app.same_origin)
    app.include_router(router, prefix='/api')
    return app


def _client(app, token=True):
    """A TestClient that answers with a 500 instead of re-raising, so a crash is visible."""
    client = TestClient(app, base_url='http://127.0.0.1:8775', raise_server_exceptions=False)
    if token:
        client.headers['X-Studio-Token'] = TOKEN
    return client


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    monkeypatch.setenv('HEWN_STUDIO_TOKEN', TOKEN)
    return _client(_host_app())


# -- bugs ------------------------------------------------------------------
def test_a_non_ascii_signature_is_a_401_not_a_500(client):
    """``hmac.compare_digest`` refuses a non-ASCII str, so REVamp's route 500ed on one bad link.

    The token gate compares bytes for exactly this reason (see ``same_origin``); the signature check
    was left comparing str, so any percent-encoded non-ASCII ``sig`` crashed the hot path that every
    unauthenticated audio request runs through. ``client`` only supplies the isolated data dir.
    """
    from music_studio import media_sign, service

    root = service.store().root
    path = f'/api/projects/{P}/takes/{T}/audio'
    # The unit, and the route that runs it on every unauthenticated audio request.
    assert media_sign.verify(root, path, 'caf\u00e9') is False
    response = _client(_host_app(), token=False).get(path + '?sig=caf%C3%A9')
    assert response.status_code == 401, response.text
    assert response.json()['detail'] == 'studio token required'


def test_a_style_that_is_not_a_number_is_a_422_not_a_500(client, monkeypatch):
    """``int(body.get('style') or 50)`` raised TypeError on a container, which escaped as a 500."""
    from music_studio import llm_bridge

    monkeypatch.setattr(llm_bridge, 'available', lambda: True)
    monkeypatch.setattr(llm_bridge, 'complete', lambda *args, **kwargs: '{}')
    for style in ({'a': 1}, [1], ['high']):
        response = client.post('/api/studio/draft',
                               json={'prompt': 'a night drive', 'style': style})
        assert response.status_code == 422, (style, response.status_code, response.text)
        assert isinstance(response.json()['detail'], str)


# -- the workflow upload body ----------------------------------------------
# These routes take their own body parser (workflow_json_body), whose three refusals had no test.
def test_a_workflow_body_that_is_not_json_is_a_400(client):
    response = client.post('/api/studio/workflows', content=b'{nope',
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 400, response.text
    assert response.json() == {'detail': 'invalid JSON body'}


def test_a_workflow_body_that_is_not_an_object_is_a_400(client):
    response = client.post('/api/studio/workflows', json=['not', 'an', 'object'])
    assert response.status_code == 400, response.text
    assert response.json() == {'detail': 'invalid request body'}


def test_an_oversized_workflow_body_is_a_413_not_a_500(client):
    limit = 2 * 1024 * 1024
    response = client.post('/api/studio/workflows', content=b'x' * (limit + 10),
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 413, response.text
    assert response.json() == {'detail': 'workflow upload too large'}


def test_an_oversized_chunked_upload_is_refused_while_it_is_read(client):
    """No Content-Length to check first: the cap has to be enforced as the body arrives."""
    chunks = (b'x' * 8192 for _ in range(300))  # 2.4 MiB, sent chunked
    response = client.post('/api/studio/workflows', content=chunks,
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 413, response.text
    assert response.json() == {'detail': 'workflow upload too large'}


def test_a_content_length_that_is_not_a_number_is_a_400(client):
    response = client.post('/api/studio/workflows', content=b'{}',
                           headers={'Content-Type': 'application/json',
                                    'Content-Length': 'abc'})
    assert response.status_code == 400, response.text
    assert response.json() == {'detail': 'invalid content-length'}


# -- the workflow routes ---------------------------------------------------
def _graph(**inputs):
    fields = {'style': '{{style}}', 'lyrics': '{{lyrics}}', 'seed': '{{seed}}'}
    fields.update(inputs)
    return {'1': {'class_type': 'TextEncode', 'inputs': fields},
            '2': {'class_type': 'SaveAudio',
                  'inputs': {'audio': ['1', 0], 'filename_prefix': 'old/prefix'}}}


def _target():
    from music_studio import service
    from music_studio.network import TargetStore

    return TargetStore(service.store().root).create('Studio PC', TARGET_URL)


def test_a_workflow_with_no_graph_is_a_400(client):
    response = client.post('/api/studio/workflows', json={'name': 'Mine'})
    assert response.status_code == 400, response.text
    assert response.json() == {'detail': 'workflow JSON is required'}


def test_a_workflow_for_an_unknown_target_is_a_400_not_a_500(client):
    response = client.post('/api/studio/workflows',
                           json={'name': 'Mine', 'target_id': str(uuid.uuid4()),
                                 'workflow': _graph()})
    assert response.status_code == 400, response.text
    assert response.json()['detail'].startswith('unknown target:')


def test_deleting_a_workflow_that_is_not_there_is_a_404(client):
    response = client.delete('/api/studio/workflows/' + str(uuid.uuid4()))
    assert response.status_code == 404, response.text
    assert 'detail' in response.json()


def test_a_workflow_id_that_is_not_an_id_is_a_400(client):
    """``_valid_id`` runs before the lookup, so a junk path segment is the caller's mistake."""
    for method, path in ((client.delete, '/api/studio/workflows/not-a-uuid'),
                         (client.post, '/api/studio/workflows/not-a-uuid/test')):
        response = method(path)
        assert response.status_code == 400, (path, response.text)


def test_testing_a_workflow_that_is_not_there_is_a_404(client):
    response = client.post('/api/studio/workflows/' + str(uuid.uuid4()) + '/test')
    assert response.status_code == 404, response.text


def test_the_test_route_reports_the_graph_without_rendering(client, monkeypatch):
    """The route only wires the saved record to the engine; health is the engine's own answer."""
    from music_studio.engines.comfy_workflow import ComfyWorkflowEngine

    target = _target()
    created = client.post('/api/studio/workflows',
                          json={'name': 'Mine', 'target_id': target['id'],
                                'workflow': _graph()})
    assert created.status_code == 200, created.text
    workflow = created.json()['workflow']

    class Status:
        def as_dict(self):
            return {'state': 'ready', 'detail': 'stub'}

    monkeypatch.setattr(ComfyWorkflowEngine, 'health', lambda self: Status())
    response = client.post('/api/studio/workflows/' + workflow['id'] + '/test')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['success'] is True
    assert body['workflow_id'] == workflow['id']
    assert body['name'] == 'Mine'
    assert body['target']['url'] == TARGET_URL
    assert set(body['placeholders']) == {'style', 'lyrics', 'seed'}
    assert body['status'] == {'state': 'ready', 'detail': 'stub'}


# -- the draft route -------------------------------------------------------
def test_a_draft_without_a_prompt_is_a_400(client, monkeypatch):
    from music_studio import llm_bridge

    monkeypatch.setattr(llm_bridge, 'available', lambda: True)
    monkeypatch.setattr(llm_bridge, 'complete', lambda *args, **kwargs: '{}')
    for body in ({}, {'prompt': ''}, {'prompt': '   '}, {'prompt': None}):
        response = client.post('/api/studio/draft', json=body)
        assert response.status_code == 400, (body, response.text)
        assert response.json() == {'detail': 'prompt is required'}


def test_a_draft_style_outside_the_range_is_a_422(client, monkeypatch):
    from music_studio import llm_bridge

    monkeypatch.setattr(llm_bridge, 'available', lambda: True)
    monkeypatch.setattr(llm_bridge, 'complete', lambda *args, **kwargs: '{}')
    for style in (-1, 101, 'abc'):
        response = client.post('/api/studio/draft',
                               json={'prompt': 'a night drive', 'style': style})
        assert response.status_code == 422, (style, response.text)


def test_a_draft_with_no_model_bound_is_a_503(client, monkeypatch):
    from music_studio import llm_bridge

    monkeypatch.setattr(llm_bridge, 'available', lambda: False)
    response = client.post('/api/studio/draft', json={'prompt': 'a night drive'})
    assert response.status_code == 503, response.text
    assert response.json() == {'detail': llm_bridge.MISSING}


def test_a_draft_can_open_the_project_it_describes(client, monkeypatch):
    """``create: true`` is the only route path that writes a project; without it none is made."""
    import json

    from music_studio import llm_bridge

    monkeypatch.setattr(llm_bridge, 'available', lambda: True)
    monkeypatch.setattr(llm_bridge, 'complete', lambda *args, **kwargs: json.dumps({
        'title': 'Night Drive', 'caption': 'warm analog synthwave', 'lyrics': '',
        'bpm': 96, 'keyscale': 'A minor', 'duration_seconds': 60, 'instrumental': True,
    }))
    response = client.post('/api/studio/draft',
                           json={'prompt': 'a night drive', 'create': True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['project']['title'] == 'Night Drive'
    assert body['project']['project_id']

    plain = client.post('/api/studio/draft', json={'prompt': 'a night drive'})
    assert plain.status_code == 200
    assert plain.json()['project'] is None


def test_demo_peaks_reports_a_missing_demo_as_a_404(client, monkeypatch, tmp_path):
    """The new route reads the shipped WAV; without it the page gets a sentence, not a 500."""
    from music_studio import demo

    monkeypatch.setattr(demo, 'audio_path', lambda: tmp_path / 'gone.wav')
    response = client.get('/api/demo/peaks?buckets=64')
    assert response.status_code == 404, response.text
    assert response.json() == {'detail': 'demo take not found'}
    # An out-of-range bucket count is the caller's mistake, refused before the file is read.
    assert client.get('/api/demo/peaks?buckets=2').status_code == 400


# -- helpers added with the routes -----------------------------------------
def test_the_active_home_never_guesses_when_hermes_is_absent(monkeypatch, tmp_path):
    """``hermes_home.active_home`` is the only place the studio asks where the profile lives.

    Its fallback branch (no ``hermes_constants``, as in the host process) must read ``HERMES_HOME`` and
    answer None rather than guessing ``~/.hermes``.
    """
    from music_studio import hermes_home

    monkeypatch.setitem(sys.modules, 'hermes_constants', None)  # makes the import raise ImportError
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'profile'))
    assert hermes_home.active_home() == tmp_path / 'profile'

    monkeypatch.delenv('HERMES_HOME', raising=False)
    assert hermes_home.active_home() is None


# -- the Music engines page, against the real modules ----------------------
# tests/test_engines_routes.py swaps both modules for stand-ins, so nothing exercised the real
# catalog and installer through HTTP. These do.
ENGINE_ROUTES = (
    ('GET', '/api/studio/engines'),
    ('POST', '/api/studio/engines'),
    ('POST', '/api/studio/engines/test'),
    ('DELETE', '/api/studio/engines/' + P),
    ('GET', '/api/studio/engines/export'),
    ('POST', '/api/studio/engines/import'),
    ('GET', '/api/studio/install/look-local'),
    ('POST', '/api/studio/install/plan'),
    ('GET', '/api/studio/install/suggest'),
    ('POST', '/api/studio/install/jobs'),
    ('GET', '/api/studio/install/jobs/' + P),
    ('POST', '/api/studio/install/jobs/' + P + '/cancel'),
)


def test_the_engines_list_is_empty_on_a_fresh_profile(client):
    response = client.get('/api/studio/engines')
    assert response.status_code == 200, response.text
    assert response.json() == {'engines': [], 'ace_remote_url': '', 'hosted': []}


def test_an_engine_is_added_listed_exported_and_removed(client):
    """The whole page in one request trail, on the real catalog: add, list, export, re-import, remove."""
    added = client.post('/api/studio/engines', json={
        'name': 'Studio PC', 'url': TARGET_URL, 'models': ['minimax_m3', 'ace15_turbo'],
        'kind': 'comfyui'})
    assert added.status_code == 200, added.text
    body = added.json()
    assert body['ok'] is True
    assert body['target']['url'] == TARGET_URL
    assert [w['name'] for w in body['workflows']] == ['ACE-Step 1.5']
    assert body['workflows'][0]['engine_id'].startswith('comfy_workflow:')

    listed = client.get('/api/studio/engines').json()
    assert [engine['name'] for engine in listed['engines']] == ['Studio PC']
    assert [w['name'] for w in listed['engines'][0]['workflows']] == ['ACE-Step 1.5']

    exported = client.get('/api/studio/engines/export')
    assert exported.status_code == 200, exported.text
    text = exported.json()['text']
    assert TARGET_URL in text
    # Importing the same handover back is a no-op, not a duplicate.
    reimported = client.post('/api/studio/engines/import', json={'text': text})
    assert reimported.status_code == 200, reimported.text
    assert reimported.json() == {'added': [], 'skipped': ['Studio PC'], 'errors': []}

    removed = client.delete('/api/studio/engines/' + body['target']['id'])
    assert removed.status_code == 200, removed.text
    assert removed.json()['target_id'] == body['target']['id']
    assert client.get('/api/studio/engines').json()['engines'] == []


def test_adding_an_engine_twice_is_allowed_but_a_bad_model_is_a_400(client):
    for models in ([], ['not_a_model'], 'ace15_turbo', [{'a': 1}]):
        response = client.post('/api/studio/engines', json={
            'name': 'Studio PC', 'url': TARGET_URL, 'models': models})
        assert response.status_code == 400, (models, response.text)
        assert response.json()['success'] is False
        assert response.json()['error']


def test_removing_an_engine_that_is_not_there_is_a_404(client):
    response = client.delete('/api/studio/engines/' + str(uuid.uuid4()))
    assert response.status_code == 404, response.text
    assert response.json() == {'success': False, 'error': 'Not found.'}


def test_a_bad_address_or_kind_is_refused_without_a_request(client):
    kind = client.post('/api/studio/engines/test', json={'kind': 'nonsense', 'url': TARGET_URL})
    assert kind.status_code == 400, kind.text
    assert kind.json() == {'success': False, 'error': 'unknown engine kind: nonsense'}

    # A bad address is the page's own sentence (a 200 with problem=bad_address), never a 500.
    address = client.post('/api/studio/engines/test', json={'kind': 'comfyui', 'url': 'nope'})
    assert address.status_code == 200, address.text
    assert address.json()['ok'] is False
    assert address.json()['problem'] == 'bad_address'


def test_a_pasted_handover_that_is_not_one_is_a_400(client):
    for text in (None, '', 'nope', '[]', '{"engines": "x"}', 42):
        response = client.post('/api/studio/engines/import', json={'text': text})
        assert response.status_code == 400, (text, response.text)
        assert response.json() == {'success': False,
                                   'error': 'That text is not a list of music engines.'}


def test_the_installer_plan_refuses_a_folder_that_is_not_a_models_folder(client, tmp_path):
    """A string that names the wrong folder is an answer; only a non-string is a refusal."""
    for value in (str(tmp_path), str(tmp_path / 'missing')):
        response = client.post('/api/studio/install/plan', json={'models_dir': value})
        assert response.status_code == 200, (value, response.text)
        assert response.json() == {'ok': False,
                                   'message': 'Choose the folder ComfyUI calls models.'}

    for value in (None, '', 42):
        response = client.post('/api/studio/install/plan', json={'models_dir': value})
        assert response.status_code == 400, (value, response.text)
        assert response.json()['error'] == 'Choose the folder ComfyUI calls models.'


def test_the_installer_plan_measures_a_real_models_folder(client, tmp_path):
    models = tmp_path / 'models'
    models.mkdir()
    response = client.post('/api/studio/install/plan', json={'models_dir': str(models)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['ok'] is True and body['models_dir'] == str(models)
    assert len(body['files']) == 4
    assert all(item['present'] is False for item in body['files'])
    assert body['need_bytes'] > 0 and body['free_bytes'] > 0
    assert isinstance(body['enough_space'], bool)


def test_starting_a_download_in_a_wrong_folder_is_a_400(client, tmp_path):
    response = client.post('/api/studio/install/jobs', json={'models_dir': str(tmp_path)})
    assert response.status_code == 400, response.text
    assert response.json()['error'] == 'Choose the folder ComfyUI calls models.'


def test_a_download_job_that_does_not_exist_is_a_plain_refusal(client):
    """``local_install`` raises ValueError for an unknown job, which the page shows as a 400."""
    for method, path in ((client.get, '/api/studio/install/jobs/' + str(uuid.uuid4())),
                         (client.post, '/api/studio/install/jobs/' + str(uuid.uuid4()) + '/cancel')):
        response = method(path)
        assert response.status_code == 400, response.text
        assert response.json() == {'success': False, 'error': 'Download not found.'}


def test_looking_for_a_local_comfyui_names_both_ports(client):
    response = client.get('/api/studio/install/look-local')
    assert response.status_code == 200, response.text
    assert response.json()['ports_checked'] == [8000, 8188]
    urls = [entry['url'] for entry in response.json()['found']]
    assert urls == ['http://127.0.0.1:8000', 'http://127.0.0.1:8188']
    assert all(isinstance(entry['ok'], bool) for entry in response.json()['found'])
    suggest = client.get('/api/studio/install/suggest')
    assert suggest.status_code == 200
    assert suggest.json() is None or isinstance(suggest.json(), str)


# -- the host and the plugin must expose the same engine routes ------------
def _engine_surface(app):
    """The (method, path) pairs an app publishes for the Music engines page."""
    return {(method.upper(), path) for path, operations in app.openapi()['paths'].items()
            for method in operations
            if '/studio/engines' in path or '/studio/install' in path}


def test_the_standalone_host_exposes_exactly_the_plugins_engine_routes():
    """Both surfaces are meant to be the same routes with the same behaviour.

    The host mounts the plugin router at ``/api``, so a route added to one side only — or shadowed by
    a second, hand-written definition on the app — is a mismatch the page would hit as a 404.
    """
    import warnings

    from dashboard.plugin_api import router
    from standalone import app as host_app

    plugin = FastAPI()
    plugin.include_router(router, prefix='/api')
    plugged = _engine_surface(plugin)
    with warnings.catch_warnings():
        # The host defines a few routes on the app as well as in the mounted router, which FastAPI
        # reports as duplicate operation ids. That is what this test compares, not a warning here.
        warnings.simplefilter('ignore', UserWarning)
        hosted = _engine_surface(host_app.app)
    assert hosted == plugged, (sorted(plugged - hosted), sorted(hosted - plugged))
    assert len(plugged) == len(ENGINE_ROUTES) == 12


def test_every_engine_route_needs_the_token_on_the_host(monkeypatch, tmp_path):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    monkeypatch.setenv('HEWN_STUDIO_TOKEN', TOKEN)
    bare = _client(_host_app(), token=False)
    for method, path in ENGINE_ROUTES:
        response = bare.request(method, path, json={} if method == 'POST' else None)
        assert response.status_code == 401, (method, path, response.status_code, response.text)
        assert response.json()['detail'] == 'studio token required'


def test_a_host_with_no_token_refuses_every_engine_route(monkeypatch, tmp_path):
    """Fail closed: the installer and the catalog both write, so an ungated host serves neither."""
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    monkeypatch.delenv('HEWN_STUDIO_TOKEN', raising=False)
    bare = _client(_host_app(), token=False)
    for method, path in ENGINE_ROUTES:
        response = bare.request(method, path, json={} if method == 'POST' else None)
        assert response.status_code == 503, (method, path, response.text)
        assert 'HEWN_STUDIO_TOKEN' in response.json()['detail']



