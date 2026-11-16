"""Routes for the Music engines page.

The catalog and the local installer are not part of this checkout. Each route
imports its module when the request arrives, so the tests register stand-in
modules in ``sys.modules`` and the real files are never loaded. The token gate
is the studio host's, the same one the workflow routes use.
"""
import inspect
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# A single-file run still needs the shared studio_client helper.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

URL = 'http://192.0.2.50:8188'
NAME = 'Studio PC'
MODELS = ['ace15_turbo', 'minimax_m3']
MODELS_DIR = 'C:/Users/me/Documents/ComfyUI/models'
EXPORT_TEXT = '{"music_studio_engines": 1, "engines": [], "ace_server": null}'

# method, path, JSON body or None, function name, which stand-in owns it.
ROUTE_CASES = (
    ('GET', '/api/studio/engines', None, 'list_engines', 'catalog'),
    ('POST', '/api/studio/engines/test',
     {'kind': 'comfyui', 'url': URL}, 'test_server', 'catalog'),
    ('POST', '/api/studio/engines',
     {'name': NAME, 'url': URL, 'models': MODELS}, 'add_engine', 'catalog'),
    ('DELETE', '/api/studio/engines/tgt-1', None, 'remove_engine', 'catalog'),
    ('GET', '/api/studio/engines/export', None, 'export_engines', 'catalog'),
    ('POST', '/api/studio/engines/import',
     {'text': EXPORT_TEXT}, 'import_engines', 'catalog'),
    ('GET', '/api/studio/install/look-local', None, 'look_local', 'install'),
    ('POST', '/api/studio/install/plan',
     {'models_dir': MODELS_DIR}, 'plan', 'install'),
    ('GET', '/api/studio/install/suggest', None, 'suggest_models_dir', 'install'),
    ('POST', '/api/studio/install/jobs',
     {'models_dir': MODELS_DIR}, 'start', 'install'),
    ('GET', '/api/studio/install/jobs/job-1', None, 'status', 'install'),
    ('POST', '/api/studio/install/jobs/job-1/cancel', None, 'cancel', 'install'),
)

RETURNS = {
    'list_engines': {'engines': [{'id': 'e1', 'kind': 'comfyui', 'name': NAME,
                                  'url': URL, 'workflows': []}],
                     'ace_remote_url': '', 'hosted': []},
    'test_server': {'ok': True, 'kind': 'comfyui', 'url': URL,
                    'message': 'This is ComfyUI. ACE-Step 1.5 is ready.',
                    'models': [], 'checks': []},
    'add_engine': {'ok': True, 'target': {'id': 'tgt-1', 'name': NAME, 'url': URL},
                   'workflows': []},
    'remove_engine': {'ok': True},
    'export_engines': {'text': EXPORT_TEXT},
    'import_engines': {'added': [NAME], 'skipped': [], 'errors': []},
    'look_local': {'found': [{'url': 'http://127.0.0.1:8188', 'ok': True}],
                   'ports_checked': [8000, 8188]},
    'plan': {'ok': True, 'models_dir': MODELS_DIR, 'files': [], 'need_bytes': 0,
             'free_bytes': 1, 'enough_space': True, 'gpu': 'unknown', 'message': 'ok'},
    'suggest_models_dir': MODELS_DIR,
    'start': {'job_id': 'job-1'},
    'status': {'state': 'running', 'file': 'acestep_v1.5_turbo.safetensors',
               'done_bytes': 1, 'total_bytes': 2, 'files_done': 0, 'files_total': 4,
               'error': '', 'message': ''},
    'cancel': {'state': 'cancelled'},
}


def _expected(fn, data_dir):
    return {
        'list_engines': ('list_engines', data_dir),
        'test_server': ('test_server', 'comfyui', URL),
        'add_engine': ('add_engine', data_dir, NAME, URL, MODELS),
        'remove_engine': ('remove_engine', data_dir, 'tgt-1'),
        'export_engines': ('export_engines', data_dir),
        'import_engines': ('import_engines', data_dir, EXPORT_TEXT),
        'look_local': ('look_local',),
        'plan': ('plan', MODELS_DIR),
        'suggest_models_dir': ('suggest_models_dir',),
        'start': ('start', MODELS_DIR),
        'status': ('status', 'job-1'),
        'cancel': ('cancel', 'job-1'),
    }[fn]


def _send(client, method, path, body):
    if body is None:
        return client.request(method, path)
    return client.request(method, path, json=body)


def _install_standins(calls):
    """Modules the routes import, with the real files absent from this checkout."""
    catalog = types.ModuleType('music_studio.engine_catalog')
    install = types.ModuleType('music_studio.local_install')

    def list_engines(data_dir):
        calls.append(('list_engines', data_dir))
        return RETURNS['list_engines']

    def test_server(kind, url):
        calls.append(('test_server', kind, url))
        return RETURNS['test_server']

    def add_engine(data_dir, *, name, url, models, **extra):
        call = ('add_engine', data_dir, name, url, models)
        # The page may send the wizard's kind and the detector's resolved
        # names; the route forwards them and the stand-in records them.
        for key in ('kind', 'resolved'):
            if key in extra:
                call += (extra[key],)
        calls.append(call)
        return RETURNS['add_engine']

    def remove_engine(data_dir, target_id):
        calls.append(('remove_engine', data_dir, target_id))
        return RETURNS['remove_engine']

    def export_engines(data_dir):
        calls.append(('export_engines', data_dir))
        return RETURNS['export_engines']

    def import_engines(data_dir, text):
        calls.append(('import_engines', data_dir, text))
        return RETURNS['import_engines']

    def look_local():
        calls.append(('look_local',))
        return RETURNS['look_local']

    def plan(models_dir):
        calls.append(('plan', models_dir))
        return RETURNS['plan']

    def suggest_models_dir():
        calls.append(('suggest_models_dir',))
        return RETURNS['suggest_models_dir']

    def start(models_dir):
        calls.append(('start', models_dir))
        return RETURNS['start']

    def status(job_id):
        calls.append(('status', job_id))
        return RETURNS['status']

    def cancel(job_id):
        calls.append(('cancel', job_id))
        return RETURNS['cancel']

    catalog.list_engines = list_engines
    catalog.test_server = test_server
    catalog.add_engine = add_engine
    catalog.remove_engine = remove_engine
    catalog.export_engines = export_engines
    catalog.import_engines = import_engines
    install.look_local = look_local
    install.plan = plan
    install.suggest_models_dir = suggest_models_dir
    install.start = start
    install.status = status
    install.cancel = cancel
    return catalog, install


@pytest.fixture
def engines(monkeypatch):
    calls = []
    catalog, install = _install_standins(calls)
    # `from music_studio import engine_catalog` resolves this entry before it
    # looks for a file, which is what lets the suite run without those modules.
    monkeypatch.setitem(sys.modules, 'music_studio.engine_catalog', catalog)
    monkeypatch.setitem(sys.modules, 'music_studio.local_install', install)
    import music_studio
    monkeypatch.setattr(music_studio, 'engine_catalog', catalog, raising=False)
    monkeypatch.setattr(music_studio, 'local_install', install, raising=False)
    return catalog, install, calls


@pytest.fixture
def client(monkeypatch, tmp_path, engines):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    from standalone import app as host_app
    from dashboard.plugin_api import router
    from conftest import studio_client

    app = FastAPI()
    app.middleware('http')(host_app.same_origin)
    app.include_router(router, prefix='/api')
    return studio_client(app, base_url='http://127.0.0.1')


def test_the_router_does_not_import_either_module_at_load():
    """A missing catalog or installer must not stop the rest of the plugin loading."""
    from dashboard import plugin_api

    text = Path(plugin_api.__file__).read_text(encoding='utf-8')
    top_level = [
        line for line in text.splitlines()
        if line.startswith('from music_studio import') or line.startswith('import music_studio')
    ]
    assert not any('engine_catalog' in line or 'local_install' in line for line in top_level)
    for fn in (plugin_api.studio_engines, plugin_api.studio_engines_test,
               plugin_api.studio_engines_add, plugin_api.studio_engines_remove,
               plugin_api.studio_engines_export, plugin_api.studio_engines_import):
        assert 'from music_studio import engine_catalog' in inspect.getsource(fn)
    for fn in (plugin_api.studio_install_look_local, plugin_api.studio_install_plan,
               plugin_api.studio_install_suggest, plugin_api.studio_install_start,
               plugin_api.studio_install_status, plugin_api.studio_install_cancel):
        assert 'from music_studio import local_install' in inspect.getsource(fn)


def test_every_new_route_is_401_without_the_token(client, engines):
    from conftest import studio_client

    bare = studio_client(client.app, base_url='http://127.0.0.1')
    del bare.headers['X-Studio-Token']
    for method, path, body, fn, owner in ROUTE_CASES:
        response = _send(bare, method, path, body)
        assert response.status_code == 401, (method, path, response.status_code, response.text)
        assert response.json()['detail'] == 'studio token required'
    assert engines[2] == [], 'a missing token is refused before the catalog or installer runs'


@pytest.mark.parametrize(
    'method, path, body, fn, owner', ROUTE_CASES, ids=[case[3] for case in ROUTE_CASES])
def test_each_route_returns_what_the_module_returned(client, engines, method, path, body, fn, owner):
    from dashboard.plugin_api import _studio_data

    response = _send(client, method, path, body)
    assert response.status_code == 200, response.text
    assert response.json() == RETURNS[fn]
    assert engines[2] == [_expected(fn, _studio_data())]


@pytest.mark.parametrize(
    'method, path, body, fn, owner', ROUTE_CASES, ids=[case[3] for case in ROUTE_CASES])
def test_a_value_error_is_a_400(client, engines, monkeypatch, method, path, body, fn, owner):
    module = engines[0] if owner == 'catalog' else engines[1]

    def raising(*args, **kwargs):
        raise ValueError('bad address')

    monkeypatch.setattr(module, fn, raising)
    response = _send(client, method, path, body)
    assert response.status_code == 400, response.text
    assert response.json() == {'success': False, 'error': 'bad address'}


def test_a_value_error_about_a_busy_take_stays_a_400(client, engines, monkeypatch):
    """A queued take can refuse the removal. The sentence comes back as the 400 body.

    The refusal includes "busy" and "confirm", and ``_page_error`` keeps that
    sentence on a 400.
    """
    def raising(data_dir, target_id):
        raise ValueError('a queued take is busy; confirm before removing it')

    monkeypatch.setattr(engines[0], 'remove_engine', raising)
    response = client.delete('/api/studio/engines/tgt-1')
    assert response.status_code == 400, response.text
    assert response.json() == {
        'success': False,
        'error': 'a queued take is busy; confirm before removing it',
    }


@pytest.mark.parametrize(
    'method, path, body, fn, owner', ROUTE_CASES, ids=[case[3] for case in ROUTE_CASES])
def test_a_missing_record_is_a_404_not_a_server_error(client, engines, monkeypatch,
                                                      method, path, body, fn, owner):
    """``TargetStore.get`` raises KeyError for an id that is not saved.

    Uncaught, that is a 500 with no JSON body, which the page cannot explain.
    """
    module = engines[0] if owner == 'catalog' else engines[1]

    def raising(*args, **kwargs):
        raise KeyError('nope')

    monkeypatch.setattr(module, fn, raising)
    response = _send(client, method, path, body)
    assert response.status_code == 404, response.text
    assert response.json() == {'success': False, 'error': 'Not found.'}


def test_the_add_route_forwards_the_wizard_kind_and_resolved_names(client, engines):
    """The engine the page picked and the loader options detection resolved."""
    from dashboard.plugin_api import _studio_data

    resolved = {'qwen_0.6b_ace15.safetensors': 'sub/clip/qwen_0.6b_ace15.safetensors'}
    response = client.post('/api/studio/engines', json={
        'name': NAME, 'url': URL, 'models': MODELS,
        'kind': 'comfyui', 'resolved': resolved})
    assert response.status_code == 200, response.text
    assert engines[2] == [('add_engine', _studio_data(), NAME, URL, MODELS,
                           'comfyui', resolved)]


def test_the_import_route_surfaces_bad_text_as_a_400(client, engines, monkeypatch):
    """A pasted export that is not one is the page's sentence to show."""
    def raising(data_dir, text):
        raise ValueError('That text is not a list of music engines.')

    monkeypatch.setattr(engines[0], 'import_engines', raising)
    response = client.post('/api/studio/engines/import', json={'text': 'nope'})
    assert response.status_code == 400, response.text
    assert response.json() == {'success': False,
                              'error': 'That text is not a list of music engines.'}


def test_comfy_desktop_port_is_scanned_with_8188_still_first(monkeypatch):
    """tests/test_discovery.py walks discovery.PORTS, so 8000 joins that scan."""
    monkeypatch.delenv('COMFYUI_URL', raising=False)
    monkeypatch.delenv('MUSIC_STUDIO_SCAN_HOSTS', raising=False)
    from music_studio import discovery

    assert discovery.PORTS[0] == 8188
    assert 8000 in discovery.PORTS
    urls = discovery.candidates(saved='', extra_hosts='studio-pc')
    assert 'http://127.0.0.1:8000' in urls
    assert 'http://studio-pc:8000' in urls
    assert urls.index('http://127.0.0.1:8188') < urls.index('http://127.0.0.1:8000')
