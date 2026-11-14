"""A take sent to REVamp still plays after the token gate, and the link opens that take and nothing else.

Found in the 11-14 review pass: review item 3 put the per-launch token on every ``/api`` route, and
REVamp's player fetches ``/api/projects/<p>/takes/<t>/audio`` from its own page, which never has that
token. Every song sent to REVamp answered 401. These fail on the tree before ``media_sign``.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TOKEN = 'test-token-abc123'
P = '11111111-1111-4111-8111-111111111111'
T = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'


@pytest.fixture()
def host(monkeypatch, tmp_path):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    monkeypatch.setenv('HEWN_STUDIO_TOKEN', TOKEN)
    from standalone import app as host_app
    from music_studio import media_sign, service

    app = FastAPI()
    app.middleware('http')(host_app.same_origin)
    router = APIRouter()

    @router.get('/projects/{p}/takes/{t}/audio')
    def audio(p: str, t: str):
        return {'audio': f'{p}/{t}'}

    @router.get('/projects/{p}/takes/{t}/peaks')
    def peaks(p: str, t: str):
        return {'peaks': []}

    @router.post('/projects/{p}/takes/{t}/audio')
    def write(p: str, t: str):
        return {'wrote': True}

    @router.get('/projects')
    def projects():
        return {'secret': 'library listing'}

    app.include_router(router, prefix='/api')
    client = TestClient(app, base_url='http://127.0.0.1:8775')
    root = service.store().root
    return client, root, media_sign


def test_a_signed_link_plays_its_take_without_the_token(host):
    client, root, media_sign = host
    sig = media_sign.sign(root, P, T)
    response = client.get(f'/api/projects/{P}/takes/{T}/audio?sig={sig}')
    assert response.status_code == 200
    assert response.json() == {'audio': f'{P}/{T}'}


def test_without_a_signature_the_audio_still_needs_the_token(host):
    client, _, _ = host
    assert client.get(f'/api/projects/{P}/takes/{T}/audio').status_code == 401
    assert client.get(f'/api/projects/{P}/takes/{T}/audio?sig=nonsense').status_code == 401


def test_a_signature_opens_only_its_own_take(host):
    client, root, media_sign = host
    sig = media_sign.sign(root, P, T)
    assert client.get(f'/api/projects/{P}/takes/{OTHER}/audio?sig={sig}').status_code == 401
    assert client.get(f'/api/projects/{OTHER}/takes/{T}/audio?sig={sig}').status_code == 401


def test_a_signature_opens_nothing_but_the_audio_route(host):
    client, root, media_sign = host
    sig = media_sign.sign(root, P, T)
    assert client.get(f'/api/projects/{P}/takes/{T}/peaks?sig={sig}').status_code == 401
    assert client.get(f'/api/projects?sig={sig}').status_code == 401
    assert client.post(f'/api/projects/{P}/takes/{T}/audio?sig={sig}', json={}).status_code == 401


def test_the_key_is_owner_only_and_survives_a_restart(host):
    _, root, media_sign = host
    first = media_sign.sign(root, P, T)
    key = Path(root) / media_sign.KEY_FILE
    assert key.is_file()
    if os.name != 'nt':
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
    # A new launch gets a new token, but the same install keeps the same key: stored playlist links
    # keep working.
    assert media_sign.sign(root, P, T) == first


def test_revamp_is_handed_a_signed_link(monkeypatch, tmp_path):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    from music_studio import media_sign, service
    from standalone import revamp

    root = service.store().root
    url = media_sign.audio_url('http://127.0.0.1:8775', root, P, T)
    assert url.startswith(f'http://127.0.0.1:8775/api/projects/{P}/takes/{T}/audio?sig=')
    assert media_sign.verify(root, f'/api/projects/{P}/takes/{T}/audio', url.split('sig=', 1)[1])

    # The studio's own REVamp page re-signs what REVamp stored, including a pre-signing link.
    monkeypatch.setattr(revamp, 'studio_playlist', lambda: {'id': 'pl1'})
    stored = {'items': [{'id': f'studio:{P}:{T}', 'source': 'studio', 'kind': 'music',
                         'playback': {'backend': 'webamp',
                                      'url': f'http://old:1/api/projects/{P}/takes/{T}/audio'}}]}
    monkeypatch.setattr(revamp, '_call', lambda method, path, body=None: stored)
    monkeypatch.setattr(revamp, '_studio_items', lambda current: current['items'])
    monkeypatch.setattr(revamp, '_audio_base', lambda request: 'http://127.0.0.1:9999')
    result = revamp.added(request=None)
    played = result['items'][0]['playback']['url']
    assert played == media_sign.audio_url('http://127.0.0.1:9999', root, P, T)


def test_concurrent_first_calls_use_unique_temp_files(tmp_path, monkeypatch):
    """Two first requests at once must not write the same '<key>.tmp'. Sharing it let one thread
    truncate or unlink the other's temp before it was linked, so a signing key could be lost."""
    import threading

    from music_studio import media_sign

    root = tmp_path / 'library'
    root.mkdir()
    opens = []
    lock = threading.Lock()
    real_open = os.open
    start = threading.Barrier(8)
    opened = threading.Barrier(8)

    def recording_open(path, flags, mode=0o777):
        text = str(path)
        if text.endswith('.tmp') or 'media-key.' in text:
            with lock:
                opens.append(text)
            # Hold every writer at its temp-file open, so all eight are first callers at once.
            opened.wait(timeout=10)
        return real_open(path, flags, mode)

    monkeypatch.setattr(media_sign.os, 'open', recording_open)
    keys = []
    errors = []

    def worker():
        try:
            start.wait(timeout=5)
            keys.append(media_sign._key(root))
        except BaseException as exc:  # noqa: BLE001 - the test reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, errors
    assert len(keys) == 8 and len(set(keys)) == 1, 'concurrent callers disagreed on the key'
    assert len(set(opens)) == 8, f'temporary files were shared: {opens}'
