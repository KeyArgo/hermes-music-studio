from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from standalone import revamp


def test_overlapping_add_and_remove_preserve_both_edits():
    items = [{'id': 'old'}]
    first_read, second_read = Event(), Event()

    def call(method, path, body=None):
        if method == 'GET':
            snapshot = list(items)
            if not first_read.is_set():
                first_read.set()
                second_read.wait(.3)
            else:
                second_read.set()
            return {'items': snapshot}
        items[:] = body['items']
        return {'items': list(items)}

    with patch.object(revamp, '_call', call), ThreadPoolExecutor(2) as pool:
        track_id = '1' * 16
        add = pool.submit(revamp.add_to_playlist, revamp.AddTrack(playlist_id='mix', track_id=track_id))
        assert first_read.wait(1)
        remove = pool.submit(revamp.remove_from_playlist, revamp.RemoveStudioTake(playlist_id='mix', item_id='old'))
        add.result()
        remove.result()
    assert [item.get('track_id') for item in items] == [track_id]


def test_hidden_export_folder():
    folder = '/music/.renders/album'
    with patch.object(revamp, '_call', return_value=[]), pytest.raises(HTTPException) as exc:
        revamp._ensure_library_folder(folder)
    assert exc.value.status_code == 400
    assert folder in exc.value.detail and 'Settings' in exc.value.detail and 'hidden' in exc.value.detail


@pytest.mark.parametrize('supplied,code,detail', [('../mix', 400, 'invalid playlist id'),
                                                  (None, 502, 'REVamp returned an invalid playlist')])
def test_invalid_remove_id(supplied, code, detail):
    with patch.object(revamp, 'studio_playlist', return_value={'id': '../mix'}), pytest.raises(HTTPException) as exc:
        revamp.remove_from_playlist(revamp.RemoveStudioTake(playlist_id=supplied, item_id='old'))
    assert (exc.value.status_code, exc.value.detail) == (code, detail)


@pytest.mark.parametrize('bad_id', ['x' * 129, [], 5])
def test_remove_rejects_malformed_client_id(bad_id):
    with pytest.raises(HTTPException) as exc:
        revamp.remove_from_playlist(revamp.RemoveStudioTake(playlist_id=bad_id, item_id='old'))
    assert (exc.value.status_code, exc.value.detail) == (400, 'invalid playlist id')
