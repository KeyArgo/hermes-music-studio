"""Radio mode: one station, one song at a time, appended to a REVamp playlist as it goes.

These tests drive the real loop with a fake model, a fake render and a fake REVamp client, so
what is checked is the behaviour the feature was asked for: a second Start is refused, only one
take is ever in flight, each song's project is approved before it is queued, the playlist grows
without losing what is in it, and every failure ends in a pause with a sentence rather than a
crash or a loop that keeps rendering songs nobody can hear.
"""
import json
import os
import sys
import threading
import time
import wave
from collections import namedtuple
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from music_studio import llm_bridge, radio, service
from music_studio.jobs import Jobs
from standalone import revamp

USAGE = namedtuple('usage', 'total used free')


def tiny_wav(seconds=0.2):
    """A real WAV, because a succeeded take is checked against its own header."""
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b'\x00\x00' * int(8000 * seconds))
    return buffer.getvalue()


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class FakeRevamp:
    """revampctl, as far as a station can tell: playlists, items, and a way to fall over."""

    def __init__(self):
        self.playlists = []
        self.calls = []
        self.down = False
        self.fail_put = False

    def call(self, method, path, body=None):
        if self.down:
            raise HTTPException(503, 'REVamp is unavailable.')
        self.calls.append((method, path, body))
        if path == '/api/state':
            return {'live': {'player_connected': True}}
        if path == '/api/playlists' and method == 'GET':
            return {'playlists': [{'id': row['id'], 'name': row['name']}
                                  for row in self.playlists]}
        if path == '/api/playlists' and method == 'POST':
            row = {'id': 'pl%02d' % (len(self.playlists) + 1), 'name': body['name'],
                   'items': list(body.get('items') or [])}
            self.playlists.append(row)
            return dict(row)
        if path == '/api/library/rescan':
            return {'started': True}
        if path.startswith('/api/playlists/'):
            playlist_id = path.rsplit('/', 1)[-1].replace('%20', ' ')
            row = next((row for row in self.playlists if row['id'] == playlist_id), None)
            if row is None:
                raise HTTPException(404, 'no such playlist')
            if method == 'PUT':
                if self.fail_put:
                    raise HTTPException(503, 'REVamp is unavailable.')
                row['items'] = list(body['items'])
            return {'id': row['id'], 'name': row['name'], 'items': list(row['items'])}
        raise HTTPException(404, path)


class FakeRender:
    """service.generate, with the studio's real queue so approval and single-flight are real."""

    def __init__(self):
        self.calls = 0
        self.fail_with = None
        self.gate = None
        self.before_succeed = None

    def generate(self, project_id, seed=None, lock_seed=False, engine_id=None):
        store = service.store()
        project = store.get(project_id)
        assert project['approved'] is True, 'a radio song is approved before it is queued'
        assert project.get('approved_by') == 'radio', 'the approval says where it came from'
        busy = [take for row in store.all() for take in row['takes']
                if take['state'] in ('queued', 'running')]
        assert not busy, f'another take was still in flight: {[t["state"] for t in busy]}'
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        jobs = Jobs(store)
        take = jobs.enqueue(project_id, 1000 + self.calls, engine_id=engine_id)
        jobs.claim(project_id, take['take_id'], os.getpid())
        if self.gate is not None:
            self.gate.wait(5)
        if self.before_succeed is not None:
            self.before_succeed()
        jobs.succeed(project_id, take['take_id'], tiny_wav())
        return {'success': True, 'take': take}


class World:
    def __init__(self):
        self.revamp = FakeRevamp()
        self.render = FakeRender()
        self.prompts = []
        self.exports = 0
        self.folders = []


@pytest.fixture(autouse=True)
def clean_radio(tmp_path, monkeypatch):
    """Every test gets its own library, its own lock and a loop that does not really wait."""
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    monkeypatch.setattr(radio, 'POLL_SECONDS', 0.01)
    monkeypatch.setattr(radio, 'BUSY_SECONDS', 0.01)
    monkeypatch.setattr(radio, 'MIN_FREE_BYTES', 0)
    yield
    gate = getattr(radio, '_THREAD', None)
    if gate is not None:
        gate.join(2)
    radio._THREAD = None
    radio._release_lock()


@pytest.fixture
def world(tmp_path, monkeypatch):
    world = World()
    music = tmp_path / 'music'
    music.mkdir()
    monkeypatch.setattr('music_studio.settings.read', lambda base: {'export_dir': str(music)})
    monkeypatch.setattr(revamp, '_call', world.revamp.call)
    monkeypatch.setattr(revamp, '_client_path', lambda: '/opt/revampctl')
    monkeypatch.setattr(revamp, '_ensure_library_folder',
                        lambda folder: world.folders.append(folder))
    # The engine probe talks to a real server; the station's own refusal is a separate test.
    monkeypatch.setattr(radio, '_engine_problem', lambda engine_id: '')
    monkeypatch.setattr(llm_bridge, 'available', lambda: True)

    def complete(messages, **kwargs):
        world.prompts.append(messages[-1]['content'])
        number = len(world.prompts)
        return json.dumps({'title': f'Radio Song {number}',
                           'caption': f'folk sketch {number}',
                           'lyrics': '[verse]\nla la\n[chorus]\nla la la',
                           'bpm': 80 + number, 'keyscale': 'C major',
                           'duration_seconds': 30})

    monkeypatch.setattr(llm_bridge, 'complete', complete)
    real_export = service.export_take

    def export(project_id, take_id, folder=None, title=None):
        world.exports += 1
        return real_export(project_id, take_id, folder=folder, title=title)

    monkeypatch.setattr(service, 'export_take', export)
    monkeypatch.setattr(service, 'generate', world.render.generate)
    world.music = music
    return world


def start_world(**overrides):
    body = {'theme': 'Warm folk for a long drive', 'target_count': 1,
            'instrumental': False, 'engine_id': 'ace_local',
            'playlist_name': 'Drive mix'}
    body.update(overrides)
    return radio.start(body)


def playlist(world, name='Drive mix'):
    return next((row for row in world.revamp.playlists if row['name'] == name), None)


# -- starting ---------------------------------------------------------------

def test_start_refuses_when_revamp_is_missing(world, monkeypatch):
    monkeypatch.setattr(revamp, '_client_path', lambda: None)
    with pytest.raises(ValueError, match='not installed'):
        start_world()
    assert radio.read() is None
    assert world.render.calls == 0
    assert world.revamp.playlists == []


def test_start_refuses_a_theme_that_is_empty_or_too_long(world):
    with pytest.raises(ValueError, match='theme'):
        start_world(theme='   ')
    with pytest.raises(ValueError, match='300'):
        start_world(theme='x' * 301)
    assert world.render.calls == 0


def test_start_refuses_a_song_count_outside_one_to_fifty(world):
    for count in (0, 51):
        with pytest.raises(ValueError, match='between 1 and 50'):
            start_world(target_count=count)
    assert world.render.calls == 0


def test_start_refuses_an_engine_that_cannot_render(world, monkeypatch):
    monkeypatch.setattr(radio, '_engine_problem',
                        lambda engine_id: 'ACE-Step 1.5 cannot render right now: no model')
    with pytest.raises(ValueError, match='cannot render'):
        start_world()
    assert radio.read() is None


def test_a_second_start_is_refused_while_the_first_is_running(world):
    world.render.gate = threading.Event()
    start_world(target_count=2)
    assert wait_for(lambda: any(take['state'] == 'running'
                                for row in service.store().all() for take in row['takes']))
    with pytest.raises(ValueError, match='already running'):
        start_world(target_count=2)
    assert world.render.calls == 1
    world.render.gate.set()
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'finished')


# -- the loop ---------------------------------------------------------------

def test_never_two_takes_in_flight_and_each_song_is_approved_by_radio(world):
    start_world(target_count=2)
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'finished')
    state = radio.read()
    assert world.render.calls == 2
    assert state['done'] == 2
    store = service.store()
    for song in state['songs']:
        project = store.get(song['project_id'])
        assert project['approved'] is True
        assert project['approved_by'] == 'radio'


def test_songs_get_different_titles_and_the_prompt_lists_earlier_songs(world):
    start_world(target_count=2)
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'finished')
    assert len(world.prompts) == 2
    assert 'Radio Song 1' not in world.prompts[0]
    assert 'Radio Song 1' in world.prompts[1], 'the station tells the model what it already wrote'
    assert 'folk sketch 1' in world.prompts[1]
    titles = [song['title'] for song in radio.read()['songs']]
    assert titles == ['Radio Song 1', 'Radio Song 2']


def test_song_two_is_appended_without_losing_song_one(world):
    start_world(target_count=2)
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'finished')
    rows = playlist(world)['items']
    assert [row['title'] for row in rows] == ['Radio Song 1', 'Radio Song 2']
    assert [row['track_id'] for row in rows] == [row['track_id'] for row in radio.read()['songs']]


def test_a_finished_song_is_exported_once_and_marked_appended(world):
    start_world(target_count=1)
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'finished')
    song = radio.read()['songs'][0]
    assert song['appended'] is True
    assert Path(song['exported_path']).is_file()
    assert Path(song['exported_path']).parent == Path(radio.read()['folder'])
    assert world.exports == 1
    assert playlist(world)['items'][0]['track_id'] == song['track_id']


def test_editing_a_radio_song_clears_the_station_approval(world):
    start_world(target_count=1)
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'finished')
    song = radio.read()['songs'][0]
    store = service.store()
    store.update(song['project_id'], caption='a different caption')
    project = store.get(song['project_id'])
    assert project['approved'] is False
    assert project['approved_by'] is None, 'edited words are never still labelled station-approved'


# -- failures ---------------------------------------------------------------

def test_the_same_track_id_is_not_added_twice(world):
    start_world(target_count=1)
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'finished')
    # Pretend the last append was lost (the playlist got it, the record did not). Resume must
    # look at the playlist instead of appending the same track again.
    state = radio.read()
    state['state'] = 'paused'
    state['songs'][0]['appended'] = False
    state['done'] = 0
    radio._write(state)
    radio.resume()
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'finished')
    assert len(playlist(world)['items']) == 1
    assert world.exports == 1, 'a retried append never exports the song again'


def test_an_append_that_fails_pauses_and_resume_adds_it_without_exporting(world):
    world.revamp.fail_put = True
    start_world(target_count=1)
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'paused')
    state = radio.read()
    assert state['songs'][0]['appended'] is False
    assert 'not answering' in state['last_error']
    assert world.exports == 1
    assert playlist(world)['items'] == []

    world.revamp.fail_put = False
    radio.resume()
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'finished')
    assert radio.read()['songs'][0]['appended'] is True
    assert world.exports == 1, 'the exported file is reused, never written again'
    assert len(playlist(world)['items']) == 1
    assert world.render.calls == 1


def test_revamp_going_away_mid_run_pauses_and_renders_nothing_more(world):
    world.render.before_succeed = lambda: setattr(world.revamp, 'down', True)
    start_world(target_count=4)
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'paused')
    state = radio.read()
    assert 'not answering' in state['last_error']
    assert world.render.calls == 1, 'no second song is rendered while the playlist cannot grow'
    world.revamp.down = False


def test_a_client_that_disappears_stops_the_station_before_the_next_song(world, monkeypatch):
    world.render.before_succeed = lambda: monkeypatch.setattr(revamp, '_client_path', lambda: None)
    start_world(target_count=4)
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'paused')
    assert 'not answering' in radio.read()['last_error']
    assert world.render.calls == 1


def test_three_failures_pause_the_station_with_the_reason(world):
    world.render.fail_with = RuntimeError('ACE-Step is unavailable: no model')
    start_world(target_count=5)
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'paused')
    state = radio.read()
    assert world.render.calls == 3
    assert state['consecutive_failures'] == 3
    assert 'unavailable' in state['last_error']


def test_low_disk_pauses_before_any_song_is_written(world, monkeypatch):
    monkeypatch.setattr(radio, 'MIN_FREE_BYTES', 2 * 1024 ** 3)
    monkeypatch.setattr(radio.shutil, 'disk_usage',
                        lambda path: USAGE(10 ** 12, 10 ** 12 - 1, 1))
    start_world(target_count=3)
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'paused')
    assert 'disk' in radio.read()['last_error']
    assert world.render.calls == 0
    assert world.prompts == []


# -- stop and restart -------------------------------------------------------

def test_stop_leaves_the_running_take_alone(world):
    world.render.gate = threading.Event()
    start_world(target_count=2)
    assert wait_for(lambda: any(take['state'] == 'running'
                                for row in service.store().all() for take in row['takes']))
    radio.stop()
    assert radio.read()['state'] == 'stopped'
    running = [take for row in service.store().all() for take in row['takes']
               if take['state'] == 'running']
    assert len(running) == 1, 'a take that is already rendering is never killed'
    world.render.gate.set()
    assert wait_for(lambda: (radio.read() or {}).get('done') == 1)
    assert world.render.calls == 1, 'the station stops between songs'
    assert radio.read()['state'] == 'stopped'


def test_resume_after_restart_continues_from_done(world):
    now = time.time()
    fresh = world.revamp.call('POST', '/api/playlists',
                              {'name': 'Drive mix', 'items': []})
    state = {
        'station_id': 'restarted-station', 'state': 'running',
        'theme': 'Warm folk for a long drive', 'instrumental': False,
        'engine_id': 'ace_local', 'target_count': 3, 'done': 2,
        'playlist_name': 'Drive mix', 'playlist_id': fresh['id'],
        'folder': str(world.music / 'Radio - Drive mix'),
        'songs': [
            {'project_id': '11111111-1111-1111-1111-111111111111',
             'take_id': '22222222-2222-2222-2222-222222222222', 'title': 'Sunday Drive',
             'exported_path': '/old/sunday.wav', 'track_id': 'aaaaaaaaaaaaaaaa',
             'appended': True},
            {'project_id': '33333333-3333-3333-3333-333333333333',
             'take_id': '44444444-4444-4444-4444-444444444444', 'title': 'Dust Road',
             'exported_path': '/old/dust.wav', 'track_id': 'bbbbbbbbbbbbbbbb',
             'appended': True},
        ],
        'last_error': None, 'consecutive_failures': 0,
        'current_project_id': None, 'current_take_id': None, 'current_title': None,
        'started_at': now, 'updated_at': now,
    }
    radio._write(state)
    assert radio.resume_on_load() is True
    assert wait_for(lambda: (radio.read() or {}).get('state') == 'finished')
    current = radio.read()
    assert current['done'] == 3
    assert world.render.calls == 1, 'it continues from done, it does not start over'
    assert 'Sunday Drive' in world.prompts[0], 'the restart still knows the earlier songs'
    titles = [row['title'] for row in playlist(world)['items']]
    assert titles == ['Radio Song 1'], 'only the new song was appended'


# -- routes -----------------------------------------------------------------

def route_client(monkeypatch, tmp_path):
    from dashboard.plugin_api import router
    from conftest import studio_client

    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    app = FastAPI()
    app.include_router(router, prefix='/api')
    return studio_client(app, base_url='http://127.0.0.1')


def test_the_state_route_answers_before_any_station_ran(monkeypatch, tmp_path):
    client = route_client(monkeypatch, tmp_path)
    response = client.get('/api/studio/radio')
    assert response.status_code == 200
    assert response.json()['station']['state'] == 'stopped'
    assert response.json()['message'] == 'No station has run yet.'


def test_the_start_route_refuses_with_one_plain_sentence(monkeypatch, tmp_path):
    client = route_client(monkeypatch, tmp_path)
    response = client.post('/api/studio/radio/start', json={'theme': ''})
    assert response.status_code == 400
    assert response.json() == {'success': False,
                              'error': 'Give the station a theme first — a few words about the sound.'}


def test_a_worker_that_dies_with_no_page_open_does_not_hang_the_station(world, monkeypatch):
    # The take is claimed but never finishes, as when its worker process is killed. With no page
    # polling status, only the station's own wait loop can notice that the worker is gone.
    def generate(project_id, seed=None, lock_seed=False, engine_id=None):
        store = service.store()
        jobs = Jobs(store)
        take = jobs.enqueue(project_id, 7, engine_id=engine_id)
        jobs.claim(project_id, take['take_id'], os.getpid())
        return {'success': True, 'take': take}

    def reconcile(self, **kwargs):
        store = service.store()
        for project in store.all():
            for take in project['takes']:
                if take['state'] in ('queued', 'running'):
                    Jobs(store).fail(project['project_id'], take['take_id'], 'worker died')

    monkeypatch.setattr(service, 'generate', generate)
    monkeypatch.setattr(Jobs, 'reconcile_dead_workers', reconcile)
    start_world(target_count=2)
    assert wait_for(lambda: (radio.read() or {}).get('consecutive_failures', 0) >= 1)
    assert 'worker died' in (radio.read() or {}).get('last_error', '')
