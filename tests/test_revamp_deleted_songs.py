"""A song deleted in Music Studio must not stay in REVamp as a link to nothing.

Found on Windows: four songs were deleted from the library, but their entries stayed in the REVamp
playlist, and Play here failed with the browser's "no supported source was found".
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from standalone import revamp  # noqa: E402
from tests.test_revamp_playlist import FakeRequest, FakeRevamp  # noqa: E402

P1, P2 = '11111111-1111-1111-1111-111111111111', '33333333-3333-3333-3333-333333333333'
T1, T2, T3 = ('22222222-2222-2222-2222-222222222222', '44444444-4444-4444-4444-444444444444',
              '55555555-5555-5555-5555-555555555555')


def entry(project_id, take_id, title='Song'):
    return {'id': f'studio:{project_id}:{take_id}', 'source': 'studio', 'title': title,
            'playback': {'backend': 'webamp',
                         'url': f'http://127.0.0.1:8775/api/projects/{project_id}/takes/{take_id}/audio?sig=x'}}


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeRevamp()
    fake.playlists = [
        {'id': 'music-studio', 'name': 'Music Studio',
         'items': [entry(P1, T1), entry(P1, T2), entry(P2, T3),
                   {'id': 'track:abc', 'source': 'library', 'title': 'Dune'}]},
        {'id': 'drive', 'name': 'Drive mix', 'items': [entry(P1, T1)]},
    ]
    monkeypatch.setattr(revamp, '_call', fake)
    monkeypatch.setattr(revamp, '_client_path', lambda: '/fake/revampctl')
    return fake


def ids(fake, playlist_id):
    return [item['id'] for item in next(p for p in fake.playlists if p['id'] == playlist_id)['items']]


def test_deleting_a_song_takes_every_one_of_its_takes_out_of_every_playlist(fake):
    assert revamp.forget_deleted(P1) == 3
    assert ids(fake, 'music-studio') == [f'studio:{P2}:{T3}', 'track:abc']
    assert ids(fake, 'drive') == []


def test_deleting_one_take_leaves_the_songs_other_takes(fake):
    assert revamp.forget_deleted(P1, T2) == 1
    assert ids(fake, 'music-studio') == [f'studio:{P1}:{T1}', f'studio:{P2}:{T3}', 'track:abc']
    assert ids(fake, 'drive') == [f'studio:{P1}:{T1}']


def test_revamp_closed_never_fails_the_delete(monkeypatch, fake):
    def closed(*_args, **_kwargs):
        raise revamp.HTTPException(503, 'REVamp is unavailable.')
    monkeypatch.setattr(revamp, '_call', closed)
    assert revamp.forget_deleted(P1) == 0


def test_without_revamp_nothing_is_called(monkeypatch, fake):
    monkeypatch.setattr(revamp, '_client_path', lambda: None)
    assert revamp.forget_deleted(P1) == 0
    assert fake.calls == []


def test_a_song_already_deleted_shows_as_deleted_not_as_a_broken_link(monkeypatch, fake, tmp_path):
    class Store:
        root = tmp_path

        def get(self, project_id):
            if project_id != P2:
                raise FileNotFoundError(project_id)
            return {'takes': [{'take_id': T3, 'state': 'succeeded'}]}

    monkeypatch.setattr('music_studio.service.store', lambda: Store())
    listed = revamp.added(FakeRequest('/api/studio/revamp/added'))
    missing = {item['id']: bool(item.get('missing')) for item in listed['items']}
    assert missing == {f'studio:{P1}:{T1}': True, f'studio:{P1}:{T2}': True, f'studio:{P2}:{T3}': False}


def test_the_delete_routes_clean_revamp_too(monkeypatch):
    from dashboard import plugin_api
    gone = []
    monkeypatch.setattr(revamp, 'forget_deleted', lambda *args: gone.append(args))
    monkeypatch.setattr('music_studio.service.delete', lambda project_id: {'success': True})
    monkeypatch.setattr('music_studio.service.delete_take', lambda project_id, take_id: {'success': True})
    plugin_api.delete_project({'project_id': P1})
    plugin_api.delete_take({'project_id': P1, 'take_id': T2})
    assert gone == [(P1,), (P1, T2)]


def test_a_refused_delete_leaves_revamp_alone(monkeypatch):
    from dashboard import plugin_api
    gone = []
    monkeypatch.setattr(revamp, 'forget_deleted', lambda *args: gone.append(args))

    def busy(project_id):
        raise ValueError('cannot delete a song while it is rendering')
    monkeypatch.setattr('music_studio.service.delete', busy)
    with pytest.raises(plugin_api.HTTPException):
        plugin_api.delete_project({'project_id': P1})
    assert gone == []
