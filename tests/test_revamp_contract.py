"""Music Studio's HTTP routes against REVamp's installed client and real HTTP server."""
import os
import socket
import sys
import threading
import urllib.request
import uuid
import wave
from pathlib import Path

import pytest

# A single file run from the repo root needs the tests folder on the path for the shared helper,
# the same way test_host.py does it.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import studio_client  # noqa: E402
from music_studio import service, settings
from standalone.app import app


@pytest.fixture
def revamp_server(monkeypatch, tmp_path):
    src = Path(os.environ.get('REVAMP_SRC') or Path(__file__).resolve().parents[1] / '.contract/revamp')
    if not src.is_dir():
        pytest.skip('REVamp source not supplied')
    monkeypatch.setattr(sys, 'dont_write_bytecode', True)
    monkeypatch.syspath_prepend(str(src))
    home, music = tmp_path / 'player', tmp_path / 'music'
    music.mkdir()
    monkeypatch.setenv('REVAMP_HOME', str(home))
    monkeypatch.setenv('REVAMP_LIBRARY_DIRS', str(music))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv('REVAMP_PORT', str(port))
    monkeypatch.setenv('HERMES_MUSIC_REVAMPCTL', str(src / 'bin/revampctl'))
    from revamp_core import library, server, store

    monkeypatch.setattr(library, '_INDEX', {})
    monkeypatch.setattr(store, '_STATE', None)
    httpd = server.build_server(port=port, data=str(home))
    token = server.get_token()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{port}', token
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def test_revamp_playlist_handoff(revamp_server, tmp_path, monkeypatch):
    base, token = revamp_server
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    store = service.store()
    settings.write(store.root, {'export_dir': str(tmp_path / 'exports')})
    project = store.create(title='Afterglow', caption='sound', lyrics='words')
    take_id = str(uuid.uuid4())
    audio = store.root / (take_id + '.wav')
    with wave.open(str(audio), 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', ''))
        wav.writeframes(bytes(1600))
    project['takes'] = [{'take_id': take_id, 'state': 'succeeded', 'audio_file': audio.name}]
    with store.locked():
        store._save(project)
    client = studio_client(app)
    route = '/api/studio/revamp/'
    def post(path, **body):
        response = client.post(route + path, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    assert client.get(route + 'added').json()['items'] == []
    assert client.get(route + 'playlists').json()['playlists'] == []
    playlist = post('playlists', name='Drive')['playlist']
    assert playlist['items'] == []
    result = post('add-take', project_id=project['project_id'], take_id=take_id, playlist_id=playlist['id'])
    item = result['item']
    assert item == result['playlist']['items'][0]
    assert item['id'] == f"studio:{project['project_id']}:{take_id}"
    assert item['source'] == 'studio' and item['kind'] == 'music'
    assert not result['indexing']
    req = urllib.request.Request(base + item['playback']['url'], headers={'X-Revamp-Token': token})
    with urllib.request.urlopen(req, timeout=5) as response:
        assert response.read() == audio.read_bytes()
    duplicate = post('playlists', name='Drive')
    assert duplicate['existing'] and duplicate['playlist']['id'] == playlist['id']
    listed, = client.get(route + 'added').json()['items']
    assert (listed['playlist_name'], listed['playlist_id']) == ('Drive', playlist['id'])
    assert (listed['id'], listed['source'], listed['playback']) == (item['id'], 'studio', item['playback'])
    removed = post('remove', item_id=item['id'], playlist_id=playlist['id'])
    assert removed['playlist']['items'] == []
    assert client.get(route + 'added').json()['items'] == []
