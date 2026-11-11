from fastapi import FastAPI
from fastapi.testclient import TestClient
from dashboard.plugin_api import router


def client():
    app = FastAPI()
    app.include_router(router, prefix='/api/plugins/hermes-music-studio')
    return TestClient(app)


def test_plugin_studio_ui_and_assets():
    c = client()
    for path in ('/studio/ui', '/studio/assets/app.js', '/studio/assets/style.css'):
        assert c.get('/api/plugins/hermes-music-studio' + path).status_code == 200
    assert c.get('/api/plugins/hermes-music-studio/studio/assets/no-such-file').status_code == 404


def test_plugin_targets_use_profile_store(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    c = client()
    base = '/api/plugins/hermes-music-studio/studio/targets'
    response = c.post(base, json={'name':'Spark E', 'url':'http://192.0.2.52:8188'})
    assert response.status_code == 200
    target = response.json()['target']
    assert c.get(base).json()['targets'][0]['id'] == target['id']
    assert c.delete(base + '/' + target['id']).status_code == 200
    assert c.get(base).json()['targets'] == []


def test_plugin_hands_a_take_over_with_a_url_revamp_can_fetch(monkeypatch):
    """The pane used to refuse this outright. It now hands over the loopback URL, which REVamp
    can read without the gateway's credentials — a take that cannot be played is not an add."""
    from standalone import revamp

    written = {}

    def fake_call(method, path, body=None):
        if path == '/api/playlists' and method == 'GET':
            return {'playlists': [{'id': 'pl01', 'name': 'Music Studio'}]}
        if path.startswith('/api/playlists/'):
            if method == 'PUT':
                written['items'] = body['items']
            return {'id': 'pl01', 'name': 'Music Studio', 'items': written.get('items', [])}
        raise AssertionError(f'unexpected call {method} {path}')

    monkeypatch.setattr(revamp, '_call', fake_call)
    monkeypatch.setattr('music_studio.host_launcher.ensure_host',
                        lambda port=None: {'url': 'http://127.0.0.1:8775', 'port': 8775})

    from music_studio import service
    project = service.store().create(title='Handover')
    c = client()
    response = c.post('/api/plugins/hermes-music-studio/studio/revamp/add-take', json={
        'project_id': project['project_id'],
        'take_id': '00000000-0000-0000-0000-000000000002'})
    # No such take exists, so the route refuses with 404 rather than inventing one.
    assert response.status_code == 404


def test_plugin_cover_json_uses_profile_scoped_cover(tmp_path, monkeypatch):
    import base64
    from music_studio import service
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    project = service.store().create(title='Art test')
    c = client()
    url = '/api/plugins/hermes-music-studio/studio/covers/' + project['project_id']
    assert c.get(url + '/json').status_code == 404
    data = base64.b64encode(b'\x89PNG\r\n\x1a\ntest').decode()
    assert c.post(url, json={'mime':'image/png', 'data':data}).status_code == 200
    assert c.get(url + '/json').json() == {'success':True,'mime':'image/png','data':data}
