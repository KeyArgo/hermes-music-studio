import pytest
from standalone.ideas import build_request, parse_draft
from standalone.workspaces import WorkspaceStore


def test_standalone_data_root_is_resolved_at_call_time(tmp_path, monkeypatch):
    from music_studio import service
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    assert service.store().root == tmp_path


def test_new_instrumental_sample_matches_its_card():
    import json, wave
    from pathlib import Path
    root=Path(__file__).resolve().parents[1] / 'assets' / 'demo'
    card=json.loads((root / 'afterglow.json').read_text())
    with wave.open(str(root / 'afterglow.wav'),'rb') as audio:
        assert audio.getframerate() == card['sample_rate']
        assert audio.getnchannels() == card['channels']
        assert abs(audio.getnframes()/audio.getframerate() - card['duration_seconds']) < .01
    assert card['instrumental'] is True and card['engine'] == 'ace_local'


def test_minimax_guidance_survives_draft_and_reaches_workflow(tmp_path):
    from music_studio.store import Store
    from music_studio.worker import _render_request
    from music_studio.engines.minimax_m3_comfy import MiniMaxM3ComfyEngine
    store=Store(tmp_path)
    project=store.create(title='A song',caption='gentle music',lyrics='words',
                         engine='minimax_m3_comfy',minimax_guidance=2.2)
    take={'kind':'generate','seed':123}
    request=_render_request(store,None,project['project_id'],project,take)
    request.validate_common()
    graph=MiniMaxM3ComfyEngine(base_url='http://127.0.0.1:9').build_workflow(request)
    assert graph['4']['inputs']['cfg_scale']==2.2
    with pytest.raises(ValueError):
        store.create(caption='x', lyrics='words', minimax_guidance=100)
    for bad in (float('nan'), True, 10**400):
        with pytest.raises(ValueError):
            store.create(caption='x', lyrics='words', minimax_guidance=bad)


def test_workspace_assignment_is_persistent(tmp_path):
    s = WorkspaceStore(tmp_path)
    a = s.create('Night sessions')
    s.assign('project-1', a['id'])
    items = WorkspaceStore(tmp_path).list()
    assert any(w['id'] == a['id'] and w['name'] == 'Night sessions' and 'project-1' in w['project_ids'] for w in items)


def test_workspace_rejects_unknown_target(tmp_path):
    with pytest.raises(ValueError):
        WorkspaceStore(tmp_path).assign('project-1', 'missing')


def test_draft_parser_filters_and_bounds_model_output():
    draft = parse_draft('{"title":"Hello","caption":"soft jazz","lyrics":"a line","bpm":90,"danger":"ignore"}')
    assert draft['title'] == 'Hello' and draft['caption'] == 'soft jazz' and draft['bpm'] == 90
    assert 'danger' not in draft
    with pytest.raises(ValueError):
        parse_draft('{"title": 42, "caption": ["wrong"]}')


def test_model_request_keeps_user_selected_provider_and_model():
    data = build_request('nous', 'example-model', [{'role': 'user', 'content': 'dreamy synth pop'}], 'draft', 'warm', 70)
    assert data['model'] == 'example-model' and data['provider'] == 'nous'
    assert any('dreamy synth pop' in m['content'] for m in data['messages'])


def test_api_workspaces_and_uploaded_art(tmp_path, monkeypatch):
    import base64
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    from fastapi.testclient import TestClient
    from standalone.app import app
    from conftest import studio_client
    client = studio_client(app)
    p = client.post('/api/create', json={'draft': {'title': 'Track', 'caption': 'ambient', 'lyrics': 'words'}}).json()['project']
    w = client.post('/api/studio/workspaces', json={'name': 'Album A'}).json()['workspace']
    response = client.post('/api/studio/assign', json={'project_id': p['project_id'], 'workspace_id': w['id']})
    assert response.status_code == 200
    assert p['project_id'] in next(x for x in client.get('/api/studio/workspaces').json()['workspaces'] if x['id'] == w['id'])['project_ids']
    image = b'\x89PNG\r\n\x1a\n' + b'test-image'
    url = '/api/studio/covers/' + p['project_id']
    assert client.post(url, json={'mime': 'image/png', 'data': base64.b64encode(image).decode()}).status_code == 200
    assert client.get(url).content == image
    assert client.post(url, json={'mime': 'image/jpeg', 'data': base64.b64encode(image).decode()}).status_code == 400
    # Cover *generation* went with the private image tool (review item 1): the route is gone, and the
    # page no longer offers a button that would call it. Uploading a cover is now the only way in.
    assert client.post(url + '/generate', json={'prompt': 'A warm night sky'}).status_code in (404, 405)
    second = b'\x89PNG\r\n\x1a\n' + b'second-image'
    assert client.post(url, json={'mime': 'image/png', 'data': base64.b64encode(second).decode()}).status_code == 200
    assert client.get(url).content == second


def test_idea_bridge_forwards_selected_model(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    from fastapi.testclient import TestClient
    from standalone.app import app
    import standalone.ideas as ideas
    requests = []
    def fake_complete(messages, *, provider=None, model=None, max_tokens=1200, temperature=0.7):
        requests.append({'provider': provider, 'model': model, 'messages': messages})
        return '{"title":"Night Drive","caption":"warm synths","lyrics":"a line"}'
    monkeypatch.setattr(ideas.llm_bridge, 'available', lambda: True)
    monkeypatch.setattr(ideas.llm_bridge, 'complete', fake_complete)
    from conftest import studio_client
    client = studio_client(app)
    response = client.post('/api/studio/ideas', json={'provider':'nous','model':'model-x','messages':[{'role':'user','content':'night drive'}],'action':'draft','tone':'warm','style':70})
    assert response.status_code == 200, response.text
    assert response.json()['draft']['title'] == 'Night Drive'
    assert requests[0]['provider'] == 'nous' and requests[0]['model'] == 'model-x', (
        "a trusted override still reaches the host's LLM — as a request, which the host may ignore"
    )


def test_revamp_playlist_and_play_routes(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    from fastapi.testclient import TestClient
    from standalone.app import app
    import standalone.revamp as revamp
    calls=[]
    def fake_call(method, path, body=None):
        calls.append((method,path,body))
        if method == 'GET' and path == '/api/playlists/a-list': return {'id':'a-list','items':[]}
        if method == 'POST' and path == '/api/playlists': return {'id':'a-list','name':body['name']}
        if method == 'POST' and path == '/api/command': return {'delivered':1}
        if method == 'PUT': return {'id':'a-list','items':body['items']}
        return []
    monkeypatch.setattr(revamp, '_call', fake_call)
    from conftest import studio_client
    c=studio_client(app)
    assert c.post('/api/studio/revamp/playlists',json={'name':'Favorites'}).json()['playlist']['id']=='a-list'
    track={'track_id':'0123456789abcdef','title':'Song','artist':'Artist'}
    assert c.post('/api/studio/revamp/play',json=track).json()['delivered']==1
    assert c.post('/api/studio/revamp/add',json={**track,'playlist_id':'a-list'}).status_code==200
    assert calls[-1][2]['items'][0]['url']=='/api/media/0123456789abcdef'
    assert c.post('/api/studio/revamp/play',json={**track,'track_id':'../not-valid-id!'}).status_code in (400,422)


def test_revamp_can_reference_finished_studio_take(tmp_path, monkeypatch):
    import uuid
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    from fastapi.testclient import TestClient
    from standalone.app import app
    from music_studio import service
    import standalone.revamp as revamp
    calls=[]
    def fake_call(method,path,body=None):
        calls.append((method,path,body))
        return {'id':'favorites','items':[] if body is None else body['items']}
    monkeypatch.setattr(revamp,'_call',fake_call)
    store=service.store(); project=store.create(title='My song',caption='sound',lyrics='words')
    take_id=str(uuid.uuid4()); project['takes']=[{'take_id':take_id,'state':'succeeded'}]
    with store.locked(): store._save(project)
    from conftest import studio_client
    c=studio_client(app,'http://127.0.0.1:8765')
    response=c.post('/api/studio/revamp/add-take',json={'playlist_id':'favorites','project_id':project['project_id'],'take_id':take_id})
    assert response.status_code==200, response.text
    assert response.json()['requires_studio_running'] is True
    item=calls[-1][2]['items'][0]
    assert item['playback']['url'].endswith('/takes/'+take_id+'/audio')
    audio=c.get('/api/projects/'+project['project_id']+'/takes/'+take_id+'/audio',headers={'origin':'http://127.0.0.1:8114'})
    assert audio.headers['access-control-allow-origin']=='http://127.0.0.1:8114'
