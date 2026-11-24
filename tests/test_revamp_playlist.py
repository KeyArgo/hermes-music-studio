"""Music Studio handoff through the REVamp client."""
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
        self.indexed = {}
        self.pending = {}
        self.folders = [{'path': '/home/alex/Music', 'kind': 'music'}]

    def scan(self):
        self.pending = {revamp._track_id(str(file)): [file, 1] for root in self.folders
                        for file in Path(root['path']).glob('*.wav')}

    @staticmethod
    def normalized(items):
        return [dict({k: v for k, v in item.items() if k not in ('track_id', 'url')},
                     kind='music', duration=item.get('duration'))
                for item in items]

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == '/api/playlists' and method == 'GET':
            return [{'id': p['id'], 'name': p['name']} for p in self.playlists]
        if path == '/api/playlists' and method == 'POST':
            row = {'id': 'pl%02d' % (len(self.playlists) + 1), 'name': body['name'],
                   'items': self.normalized(body.get('items') or [])}
            self.playlists.append(row)
            return row
        if path == '/api/library/folders':
            if method == 'GET':
                return {'folders': self.folders}
            if method != 'PUT':
                raise HTTPException(404, 'unknown route')
            self.folders = list((body or {}).get('folders') or [])
            self.scan()
            return {'folders': self.folders}
        if path == '/api/library/rescan' and method == 'POST':
            self.scan()
            return {'started': True}
        if method == 'GET' and path.startswith('/api/sources/local/item/'):
            tid = path.rsplit('/', 1)[1]
            if tid in self.pending:
                entry = self.pending[tid]
                if entry[1] == 0:
                    self.indexed[tid] = self.pending.pop(tid)[0]
                else:
                    entry[1] -= 1
            if tid in self.indexed:
                return {'id': 'local:track:' + tid}
            raise HTTPException(404, 'no such item')
        if method == 'GET' and path.startswith('/api/media/'):
            file = self.indexed.get(path.rsplit('/', 1)[1])
            if file:
                return file.read_bytes()
            raise HTTPException(404, 'unknown track id')
        if path.startswith('/api/playlists/'):
            pid = path.rsplit('/', 1)[-1].replace('%20', ' ')
            row = next((p for p in self.playlists if p['id'] == pid), None)
            if row is None:
                raise HTTPException(404, 'no such playlist')
            if method == 'PUT':
                row['items'] = self.normalized(body['items'])
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
    # REVamp returns its normalized item and drops the legacy top-level fields.
    assert 'track_id' not in item and 'url' not in item and item['kind'] == 'music'
    assert result['indexing'] is False
    assert item['playback']['url'] == '/api/media/' + track_id
    assert not item['playback']['url'].startswith('http')
    assert item['title'] == 'Afterglow'
    assert 'duration' in item and item == result['playlist']['items'][0]
    assert result['requires_studio_running'] is False
    assert str(fake.exported) in [row['path'] for row in fake.folders], 'REVamp indexes the export folder'
    # Registering a root already schedules a scan.
    assert ('POST', '/api/library/rescan', {}) not in fake.calls
    assert fake('GET', item['playback']['url']) == b'RIFF'


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

    monkeypatch.setattr(revamp, '_client_path', lambda: None)
    monkeypatch.setattr(revamp, '_call', missing)
    state = revamp.status()
    assert state['available'] is False
    assert 'not installed' in state['reason']


def test_a_new_playlist_is_built_from_exported_files_in_order(monkeypatch, tmp_path, fake):
    """Export first, index second, playlist third — the playlist points at real files."""
    order = []
    fake.folders.append({'path': str(tmp_path), 'kind': 'music'})

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
        if path == '/api/library/folders':
            order.append(('folder', None))
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
    assert item['id'] == 'studio:11111111-1111-1111-1111-111111111111:22222222-2222-2222-2222-222222222222'
    assert item['source'] == 'studio' and item['kind'] == 'music'
    assert result['indexing'] is False


def test_a_playlist_needs_at_least_one_take(fake):
    with pytest.raises(HTTPException) as caught:
        revamp.playlist_from_takes(revamp.PlaylistFromTakes(name='Empty', takes=[]))
    assert caught.value.status_code == 400


def test_a_folder_inside_a_library_folder_is_not_added_again(fake):
    # /home/alex/Music is already a library root in the fake, so a folder beneath it needs no
    # new root: the rescan covers it.
    revamp._ensure_library_folder('/home/alex/Music/Hermes Music Studio')
    assert not [c for c in fake.calls if c[1] == '/api/library/folders' and c[0] == 'PUT']


def test_a_folder_outside_every_library_folder_is_added(fake):
    fake.folders[0]['kind'] = 'audiobook'
    revamp._ensure_library_folder('/srv/music/studio')
    # The real server accepts folder changes only via PUT.
    posted = [c for c in fake.calls if c[1] == '/api/library/folders' and c[0] == 'PUT']
    assert fake.folders[0] == {'path': '/home/alex/Music', 'kind': 'audiobook'}
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

@pytest.mark.parametrize('extension', ['.exe', '.cmd', '.bat'])
def test_revamp_client_skips_windows_wrappers(monkeypatch, tmp_path, extension):
    wrapper = tmp_path / ('revampctl' + extension)
    wrapper.touch()
    monkeypatch.setenv('HERMES_MUSIC_REVAMPCTL', str(wrapper))
    monkeypatch.setattr(revamp.shutil, 'which', lambda _: str(wrapper))
    monkeypatch.setattr(revamp.Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(revamp, '_hermes_homes', lambda: [])
    assert revamp._client_path() is None


def test_revamp_errors_and_status(monkeypatch):
    import io
    from urllib.error import HTTPError, URLError

    monkeypatch.setattr(revamp, '_client_path', lambda: None)
    with pytest.raises(HTTPException) as caught:
        revamp._call('GET', '/api/state')
    assert caught.value.status_code == 503 and 'not installed' in caught.value.detail

    monkeypatch.setattr(revamp, '_client_path', lambda: 'revampctl')
    path = '/api/library/folders'
    error = HTTPError(path, 400, 'Bad Request', {}, io.BytesIO(b'{"error":"existing absolute directories"}'))

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(revamp.runpy, 'run_path', lambda _: {'call': fail})
    with pytest.raises(HTTPException) as caught:
        revamp._call('PUT', path, {})
    assert caught.value.status_code == 400
    assert caught.value.detail == 'REVamp refused PUT /api/library/folders: 400 existing absolute directories'
    error = URLError('refused')
    state = revamp.status()
    assert state['installed'] and not state['reachable'] and not state['player_connected']
    assert state['reason'] == 'REVamp is installed but not running. Open REVamp in Hermes.'
    monkeypatch.setenv('REVAMP_PORT', '9234')
    monkeypatch.setattr(revamp, '_call', lambda *args: {'live': {'player_connected': True}})
    state = revamp.status()
    assert state['reachable'] and state['player_connected'] and state['url'].endswith(':9234/')


def test_revamp_unindexed_take_is_saved(monkeypatch, fake):
    monkeypatch.setattr(revamp, 'INDEX_WAIT', 0)
    result = revamp.playlist_from_takes(revamp.PlaylistFromTakes(
        name='Waiting', takes=[{'project_id': 'p', 'take_id': 't'}]))
    assert result['indexing']
    item, = result['playlist']['items']
    with pytest.raises(HTTPException) as caught:
        fake('GET', item['playback']['url'])
    assert caught.value.status_code == 404


def test_revamp_tracking_playlist_survives_builder_collision(fake):
    other = fake('POST', '/api/playlists', {'name': 'Music Studio', 'items': [{'id': 'keep'}]})
    result = revamp.playlist_from_takes(revamp.PlaylistFromTakes(
        name='Music Studio', takes=[{'project_id': 'p', 'take_id': 't'}]))
    assert result['existing'] and result['playlist']['id'] == other['id']
    assert [row['id'] for row in other['items']] == ['keep']


@pytest.mark.parametrize('accepted,delivered', [(True, 1), (False, 0)])
def test_revamp_reports_player_delivery(monkeypatch, accepted, delivered):
    monkeypatch.setattr(revamp, '_call', lambda *args: {'accepted': accepted, 'delivered': delivered})
    result = revamp.play(revamp.Track(track_id='0123456789abcdef'))
    assert result['accepted'] is accepted and result['delivered'] == delivered
    assert result['success'] is accepted
    if not delivered:
        assert result['detail'] == "Open REVamp's player first"

def test_revamp_added_and_remove_keep_playlist_membership(fake, monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr('music_studio.service.store', lambda: SimpleNamespace(root=tmp_path))
    request = FakeRequest('/api/studio/revamp/added')
    assert revamp.added(request)['items'] == []
    assert fake.playlists == []
    item = {'id': 'studio:p:t', 'source': 'studio', 'playback': {'url': '/api/media/0123456789abcdef'}}
    first, second = [fake('POST', '/api/playlists', {'name': name, 'items': [item]})
                     for name in ('Drive', 'Work')]
    assert [(row['playlist_name'], row['playlist_id']) for row in revamp.added(request)['items']] == [
        ('Drive', first['id']), ('Work', second['id'])]
    revamp.remove_from_playlist(revamp.RemoveStudioTake(item_id=item['id'], playlist_id=second['id']))
    assert first['items'] and not second['items']


def test_revamp_refuses_binary_client_without_extension(monkeypatch, tmp_path):
    client = tmp_path / 'revampctl'
    client.write_bytes(bytes([0, 255]))
    monkeypatch.setenv('HERMES_MUSIC_REVAMPCTL', str(client))
    monkeypatch.setattr(revamp.shutil, 'which', lambda _: str(client))
    monkeypatch.setattr(revamp.Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(revamp, '_hermes_homes', lambda: [])
    assert revamp._client_path() is None


# ------------------------------------------------ a REVamp reply in another shape is not a crash
# An older or newer REVamp can answer with fields missing. Each route must degrade to a sentence,
# never a bare 500.


def test_play_without_acceptance_fields_asks_for_the_player(monkeypatch):
    monkeypatch.setattr(revamp, '_call', lambda method, path, body=None: {})
    answer = revamp.play(revamp.Track(track_id='0123456789abcdef', title='x'))
    assert answer['success'] is False
    assert answer['detail'] == "Open REVamp's player first"


def test_status_with_an_unexpected_state_reply_is_still_reachable(monkeypatch):
    monkeypatch.setattr(revamp, '_client_path', lambda: '/fake/revampctl')
    monkeypatch.setattr(revamp, '_call', lambda method, path, body=None: ['not', 'a', 'dict'])
    answer = revamp.status()
    assert answer['installed'] is True and answer['reachable'] is True
    assert answer['player_connected'] is False


def test_playlist_rows_without_a_name_or_id_are_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    def call(method, path, body=None):
        if path == '/api/playlists':
            return [{'id': 'ok', 'name': 'Drive'}, {'id': 'noname'}, 'junk', {'name': 'noid'}]
        if path == '/api/playlists/ok':
            return {'id': 'ok', 'name': 'Drive', 'items': [
                {'id': 'studio:p:t', 'source': 'studio', 'title': 'Song'}]}
        raise AssertionError(path)
    monkeypatch.setattr(revamp, '_call', call)
    monkeypatch.setattr(revamp, '_audio_base', lambda request: 'http://127.0.0.1:8775')
    monkeypatch.setattr(revamp, '_take_exists', lambda store, project, take: True)
    answer = revamp.added(object())
    assert [(row['playlist_id'], row['title']) for row in answer['items']] == [('ok', 'Song')]
    assert revamp._existing_playlist('drive')['playlist']['id'] == 'ok'


def test_one_playlist_with_an_odd_id_does_not_hide_the_others(monkeypatch, tmp_path):
    # Found in review: a single playlist id outside the safe pattern used to turn the whole added
    # list into a 502, so no studio song showed at all.
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    def call(method, path, body=None):
        if path == '/api/playlists':
            return [{'id': 'odd.id/with..dots', 'name': 'Odd'}, {'id': 'ok', 'name': 'Drive'}]
        if path == '/api/playlists/ok':
            return {'id': 'ok', 'name': 'Drive', 'items': [
                {'id': 'studio:p:t', 'source': 'studio', 'title': 'Song'}]}
        raise AssertionError(f'unexpected {method} {path}')
    monkeypatch.setattr(revamp, '_call', call)
    monkeypatch.setattr(revamp, '_audio_base', lambda request: 'http://127.0.0.1:8775')
    monkeypatch.setattr(revamp, '_take_exists', lambda store, project, take: True)
    answer = revamp.added(object())
    assert [row['title'] for row in answer['items']] == ['Song']
