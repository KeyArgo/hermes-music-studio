"""Music Studio's own slice of REVamp.

The rule the owner asked for: the REVamp screen inside Music Studio shows **only** what was
added from Music Studio. REVamp's library — audiobooks, albums — belongs to REVamp and must
never appear there. These tests pin the single playlist that makes that true.
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from standalone import revamp


class FakeRevamp:
    """A tiny stand-in for revampctl: playlists and items, nothing else."""

    def __init__(self):
        self.playlists = []
        self.calls = []
        self.folders = [{'path': '/home/alex/Music', 'kind': 'music'}]

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == '/api/playlists' and method == 'GET':
            return {'playlists': [{'id': p['id'], 'name': p['name']} for p in self.playlists]}
        if path == '/api/playlists' and method == 'POST':
            row = {'id': 'pl%02d' % (len(self.playlists) + 1), 'name': body['name'],
                   'items': list(body.get('items') or [])}
            self.playlists.append(row)
            return row
        if path == '/api/library/folders':
            if method == 'GET':
                return {'folders': self.folders}
            self.folders = list((body or {}).get('folders') or [])
            return {'folders': self.folders}
        if path == '/api/library/rescan':
            return {'started': True}
        if path.startswith('/api/playlists/'):
            pid = path.rsplit('/', 1)[-1].replace('%20', ' ')
            row = next((p for p in self.playlists if p['id'] == pid), None)
            if row is None:
                raise HTTPException(404, 'no such playlist')
            if method == 'PUT':
                row['items'] = list(body['items'])
            return dict(row)
        raise HTTPException(404, path)


class FakeRequest:
    def __init__(self, path, base='http://127.0.0.1:8770/'):
        self.url = type('URL', (), {'path': path})()
        self.base_url = base


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    fake = FakeRevamp()
    monkeypatch.setattr(revamp, '_call', fake)
    exported = tmp_path / 'exported'

    def export_take(project_id, take_id, folder=None, title=None):
        exported.mkdir(exist_ok=True)
        path = exported / f'{title or "take"}.wav'
        path.write_bytes(b'RIFF')
        return {'success': True, 'path': str(path), 'folder': str(exported), 'title': title}
    monkeypatch.setattr('music_studio.service.export_take', export_take)
    fake.exported = exported
    return fake


def test_the_studio_playlist_is_created_once_and_reused(fake):
    first = revamp.studio_playlist()
    second = revamp.studio_playlist()
    assert first['name'] == 'Music Studio'
    assert first['id'] == second['id']
    assert sum(1 for row in fake.playlists if row['name'] == 'Music Studio') == 1


def test_an_audiobook_in_revamp_never_reaches_the_studio_screen(fake):
    playlist = revamp.studio_playlist()
    playlist['items'].append({'id': 'audiobook:1', 'title': 'Dune', 'source': 'library'})
    svc = revamp._call  # same fake
    assert svc is fake
    current = fake('GET', '/api/playlists/' + playlist['id'])
    assert [item['id'] for item in revamp._studio_items(current)] == []


def test_adding_a_take_hands_revamp_a_file_in_its_own_library(monkeypatch, fake, tmp_path):
    # REVamp plays a web address only through its station relay, which refuses this computer's own
    # addresses: a link to the studio never played there (seen on Windows). The take is exported into
    # a REVamp library folder and the item points at REVamp's own media route.
    class Store:
        root = tmp_path

        def get(self, project_id):
            return {'title': 'Afterglow', 'takes': [{'take_id': '22222222-2222-2222-2222-222222222222', 'state': 'succeeded'}]}

    monkeypatch.setattr('music_studio.service.store', lambda: Store())
    body = revamp.AddStudioTake(project_id='11111111-1111-1111-1111-111111111111', take_id='22222222-2222-2222-2222-222222222222')
    result = revamp.add_take_to_playlist(body, FakeRequest('/api/plugins/hermes-music-studio/host/close'))
    item = result['item']
    assert item['source'] == 'studio'
    assert item['id'] == 'studio:11111111-1111-1111-1111-111111111111:22222222-2222-2222-2222-222222222222'
    track_id = revamp._track_id(str(fake.exported / 'Afterglow.wav'))
    assert item['track_id'] == track_id
    assert item['playback']['url'] == '/api/media/' + track_id
    assert not item['playback']['url'].startswith('http')
    assert item['title'] == 'Afterglow'
    assert result['requires_studio_running'] is False
    assert str(fake.exported) in [row['path'] for row in fake.folders], 'REVamp indexes the export folder'
    assert ('POST', '/api/library/rescan', {}) in fake.calls


def test_an_entry_added_the_old_way_is_flagged_for_a_fix(monkeypatch, fake, tmp_path):
    class Store:
        root = tmp_path

        def get(self, project_id):
            return {'title': 'Old', 'takes': [{'take_id': 't', 'state': 'succeeded'},
                                              {'take_id': 'u', 'state': 'succeeded'}]}

    monkeypatch.setattr('music_studio.service.store', lambda: Store())
    playlist = revamp.studio_playlist()
    fake('PUT', '/api/playlists/' + playlist['id'], {'items': [
        {'id': 'studio:p:t', 'source': 'studio', 'title': 'Old',
         'playback': {'url': '/api/radio/stream?url=http%3A%2F%2F127.0.0.1%3A8775%2Fapi%2Fprojects%2Fp'}},
        {'id': 'studio:p:u', 'source': 'studio', 'title': 'New', 'playback': {'url': '/api/media/0123456789abcdef'}}]})
    listed = revamp.added(FakeRequest('/api/studio/revamp/added'))
    assert {item['id']: bool(item.get('relink')) for item in listed['items']} == {'studio:p:t': True, 'studio:p:u': False}


def test_adding_the_same_take_twice_keeps_one_copy(monkeypatch, fake, tmp_path):
    class Store:
        root = tmp_path  # the signing key for REVamp links lives beside the library

        def get(self, project_id):
            return {'title': 'Afterglow', 'takes': [{'take_id': '22222222-2222-2222-2222-222222222222', 'state': 'succeeded'}]}

    monkeypatch.setattr('music_studio.service.store', lambda: Store())
    body = revamp.AddStudioTake(project_id='11111111-1111-1111-1111-111111111111', take_id='22222222-2222-2222-2222-222222222222')
    revamp.add_take_to_playlist(body, FakeRequest('/api/x'))
    revamp.add_take_to_playlist(body, FakeRequest('/api/x'))
    playlist = revamp.studio_playlist()
    current = fake('GET', '/api/playlists/' + playlist['id'])
    assert len(current['items']) == 1


def test_removing_a_take_leaves_the_other_items_alone(fake):
    playlist = revamp.studio_playlist()
    keep = {'id': 'studio:a:b', 'source': 'studio', 'title': 'Keep'}
    drop = {'id': 'studio:a:c', 'source': 'studio', 'title': 'Drop'}
    fake('PUT', '/api/playlists/' + playlist['id'], {'items': [keep, drop]})
    result = revamp.remove_from_playlist(revamp.RemoveStudioTake(item_id='studio:a:c'))
    assert [item['id'] for item in result['items']] == ['studio:a:b']


def test_a_take_that_is_not_finished_is_refused(monkeypatch, fake, tmp_path):
    class Store:
        root = tmp_path  # the signing key for REVamp links lives beside the library

        def get(self, project_id):
            return {'title': 'Draft', 'takes': [{'take_id': '22222222-2222-2222-2222-222222222222', 'state': 'failed'}]}

    monkeypatch.setattr('music_studio.service.store', lambda: Store())
    body = revamp.AddStudioTake(project_id='11111111-1111-1111-1111-111111111111', take_id='22222222-2222-2222-2222-222222222222')
    with pytest.raises(HTTPException) as caught:
        revamp.add_take_to_playlist(body, FakeRequest('/api/x'))
    assert caught.value.status_code == 404


def test_a_person_can_pick_their_own_playlist(monkeypatch, fake, tmp_path):
    class Store:
        root = tmp_path  # the signing key for REVamp links lives beside the library

        def get(self, project_id):
            return {'title': 'Cursor Blink', 'takes': [{'take_id': '22222222-2222-2222-2222-222222222222',
                                                         'state': 'succeeded'}]}

    monkeypatch.setattr('music_studio.service.store', lambda: Store())
    other = fake('POST', '/api/playlists', {'name': 'Drive mix', 'items': []})
    body = revamp.AddStudioTake(project_id='11111111-1111-1111-1111-111111111111',
                                take_id='22222222-2222-2222-2222-222222222222',
                                playlist_id=other['id'])
    revamp.add_take_to_playlist(body, FakeRequest('/api/x'))
    current = fake('GET', '/api/playlists/' + other['id'])
    assert [item['title'] for item in current['items']] == ['Cursor Blink']
    studio = revamp.studio_playlist()
    assert fake('GET', '/api/playlists/' + studio['id'])['items'] == []


def test_revamp_being_absent_is_a_state_not_an_error(monkeypatch):
    def missing(*args, **kwargs):
        raise HTTPException(503, 'REVamp is unavailable.')

    monkeypatch.setattr(revamp, '_call', missing)
    state = revamp.status()
    assert state['available'] is False
    assert 'not installed' in state['reason']


def test_a_new_playlist_is_built_from_exported_files_in_order(monkeypatch, tmp_path, fake):
    """Export first, index second, playlist third — the playlist points at real files."""
    order = []
    monkeypatch.setattr(revamp, '_ensure_library_folder', lambda folder: order.append(('folder', folder)))

    written = {}

    def fake_export(project_id, take_id, folder=None):
        order.append(('export', take_id))
        path = tmp_path / 'Cursor Blink.wav'
        path.write_bytes(b'RIFF')
        written['path'] = str(path)
        return {'success': True, 'path': str(path), 'folder': str(tmp_path), 'bytes': 4}

    monkeypatch.setattr('music_studio.service.export_take', fake_export)
    monkeypatch.setattr('music_studio.settings.read', lambda base: {'export_dir': str(tmp_path)})

    original_call = fake

    def recording_call(method, path, body=None):
        if path == '/api/library/rescan':
            order.append(('rescan', None))
        if path == '/api/playlists' and method == 'POST':
            order.append(('create', body['name']))
        return original_call(method, path, body)

    monkeypatch.setattr(revamp, '_call', recording_call)

    body = revamp.PlaylistFromTakes(
        name='Drive mix',
        takes=[{'project_id': '11111111-1111-1111-1111-111111111111',
                'take_id': '22222222-2222-2222-2222-222222222222'}])
    result = revamp.playlist_from_takes(body)

    assert [step[0] for step in order][:3] == ['export', 'folder', 'rescan'], order
    assert order[-1][0] == 'create'
    assert result['files'] == [written['path']]
    item = result['playlist']['items'][0]
    assert item['playback']['url'] == '/api/media/' + revamp._track_id(written['path'])
    assert item['title'] == 'Cursor Blink'


def test_a_playlist_needs_at_least_one_take(fake):
    with pytest.raises(HTTPException) as caught:
        revamp.playlist_from_takes(revamp.PlaylistFromTakes(name='Empty', takes=[]))
    assert caught.value.status_code == 400


def test_a_folder_inside_a_library_folder_is_not_added_again(fake):
    # /home/alex/Music is already a library root in the fake, so a folder beneath it needs no
    # new root: the rescan covers it.
    revamp._ensure_library_folder('/home/alex/Music/Hermes Music Studio')
    assert not [c for c in fake.calls if c[1] == '/api/library/folders' and c[0] == 'POST']


def test_a_folder_outside_every_library_folder_is_added(fake):
    revamp._ensure_library_folder('/srv/music/studio')
    posted = [c for c in fake.calls if c[1] == '/api/library/folders' and c[0] == 'POST']
    assert posted, 'an outside folder has to be added so REVamp can index it'
    kept = {row['path'] for row in fake.folders}
    assert str(Path('/srv/music/studio')) in {str(Path(p)) for p in kept} and '/home/alex/Music' in kept


def test_the_client_is_found_inside_the_installed_plugin(monkeypatch, tmp_path):
    """A Windows install has no PATH entry and no ~/.local/bin.

    The client ships in the REVamp plugin's own bin/ directory, so that is the
    place to look after PATH; without it the handoff is a permanent 503 while
    REVamp is installed and running.
    """
    home = tmp_path / 'hermes'
    bin_dir = home / 'plugins' / 'revamp' / 'bin'
    bin_dir.mkdir(parents=True)
    client = bin_dir / 'revampctl'
    client.write_text('def call(*a, **k):\n    return {}\n', encoding='utf-8')

    monkeypatch.delenv('HERMES_MUSIC_REVAMPCTL', raising=False)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(revamp.shutil, 'which', lambda name: None)
    # Never the real ~/.local/bin: a developer box with REVamp installed has a client there.
    monkeypatch.setattr(revamp.Path, 'home', classmethod(lambda cls: tmp_path / 'no-home'))
    assert revamp._client_path() == str(client)


def test_the_explicit_override_wins(monkeypatch, tmp_path):
    client = tmp_path / 'revampctl'
    client.write_text('def call(*a, **k):\n    return {}\n', encoding='utf-8')
    monkeypatch.setenv('HERMES_MUSIC_REVAMPCTL', str(client))
    assert revamp._client_path() == str(client)


def test_no_client_anywhere_is_none(monkeypatch, tmp_path):
    monkeypatch.delenv('HERMES_MUSIC_REVAMPCTL', raising=False)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'empty'))
    monkeypatch.setattr(revamp.shutil, 'which', lambda name: None)
    # Never the real ~/.local/bin: a developer box with REVamp installed has a client there.
    monkeypatch.setattr(revamp.Path, 'home', classmethod(lambda cls: tmp_path / 'no-home'))
    monkeypatch.setattr(revamp, '_hermes_homes', lambda: [tmp_path / 'empty'])
    assert revamp._client_path() is None
