"""Bug hunt 2026-11-20: REVamp hand-over edge cases found in review and reproduced here."""
import ntpath
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from standalone import revamp  # noqa: E402
from tests.test_revamp_playlist import FakeRevamp  # noqa: E402


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeRevamp()
    monkeypatch.setattr(revamp, '_call', fake)
    return fake


def test_remove_never_saves_an_empty_list_over_a_playlist_it_could_not_read(monkeypatch, fake):
    playlist = revamp.studio_playlist()
    fake('PUT', '/api/playlists/' + playlist['id'], {'items': [
        {'id': 'studio:a:b', 'source': 'studio'}, {'id': 'studio:a:c', 'source': 'studio'}]})
    real = fake.__call__

    def odd_reply(method, path, body=None):
        if method == 'GET' and path.startswith('/api/playlists/'):
            return {'error': 'busy'}          # a reply with no items list
        return real(method, path, body)
    monkeypatch.setattr(revamp, '_call', odd_reply)
    with pytest.raises(HTTPException) as caught:
        revamp.remove_from_playlist(revamp.RemoveStudioTake(item_id='studio:a:c'))
    assert caught.value.status_code == 502
    assert len(fake('GET', '/api/playlists/' + playlist['id'])['items']) == 2, 'the playlist was not emptied'


@pytest.mark.parametrize('error', [FileNotFoundError('gone'), KeyError('take')])
def test_a_deleted_song_in_create_playlist_is_a_plain_400_not_a_crash(monkeypatch, fake, tmp_path, error):
    def export_take(project_id, take_id, folder=None, title=None):
        raise error
    monkeypatch.setattr('music_studio.service.export_take', export_take)
    monkeypatch.setattr('music_studio.settings.read', lambda base: {'export_dir': str(tmp_path)})
    body = revamp.PlaylistFromTakes(name='Drive', takes=[{'project_id': 'p', 'take_id': 't'}])
    with pytest.raises(HTTPException) as caught:
        revamp.playlist_from_takes(body)
    assert caught.value.status_code == 400
    assert 'deleted' in caught.value.detail


def test_a_library_folder_in_another_letter_case_is_the_same_folder_on_windows(monkeypatch, fake):
    # Windows paths ignore case: REVamp may hold c:\users\argo\music while the export is C:\Users\Argo\Music.
    monkeypatch.setattr(revamp.os, 'path', ntpath)
    monkeypatch.setattr(revamp.os, 'sep', '\\')
    fake.folders = [{'path': 'c:\\users\\argo\\music', 'kind': 'music'}]
    revamp._ensure_library_folder('C:\\Users\\Argo\\Music\\Studio')
    revamp._ensure_library_folder('C:\\Users\\Argo\\Music')
    assert [row['path'] for row in fake.folders] == ['c:\\users\\argo\\music'], 'no second copy of the same folder'
