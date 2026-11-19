"""Round 13, the fixes the owner's own live test turned up.

Each test here failed on the build he was running:

1. Any HTTP error counted as "an ACE server answers", so a ComfyUI (or any web server) could be
   tested and then saved as the ACE-Step engine.
2. Saving a hosted key wrote the hosted URL into ``ace_remote_url``, replacing the ACE-Step server
   saved on the Music engines page without a word.
3. That page had no way to remove the ACE-Step server once it was saved.
4. "Change this part" on an engine without repaint sent a 0-8 s range to MiniMax, which refuses
   anything under 10 s, and used the typed words *as* the caption instead of appending them to the
   parent take's own.
5. The ACE "not configured" sentence named a field on a page that does not have one.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import struct
import sys
import threading
import types
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from music_studio import setup  # noqa: E402


# -- a stub server whose answers the test dictates -------------------------

class _Stub(BaseHTTPRequestHandler):
    routes: dict = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        status, payload, content_type = _route(self.path)
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _route(path):
    """``(status, payload)`` or ``(status, payload, content type)`` for one path."""
    status, payload, *rest = _Stub.routes.get(path, (404, {'detail': 'no'}))
    return status, payload, (rest[0] if rest else 'application/json')


@contextlib.contextmanager
def serve(routes):
    _Stub.routes = routes
    server = HTTPServer(('127.0.0.1', 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()


@pytest.fixture
def hermes(monkeypatch):
    """A fake Hermes credential store backed by a dict, on the public plugin modules."""
    values: dict[str, str] = {}

    config = types.ModuleType('hermes_cli.config')

    def save_env_value(name, value):
        values[name] = value

    config.save_env_value = save_env_value
    hermes_cli = types.ModuleType('hermes_cli')
    hermes_cli.config = config

    scope = types.ModuleType('agent.secret_scope')

    def get_secret(name, default=None):
        return values.get(name, default)

    def invalidate_env_file_cache():
        return None

    scope.get_secret = get_secret
    scope.invalidate_env_file_cache = invalidate_env_file_cache
    agent = types.ModuleType('agent')
    agent.secret_scope = scope

    monkeypatch.setitem(sys.modules, 'hermes_cli', hermes_cli)
    monkeypatch.setitem(sys.modules, 'hermes_cli.config', config)
    monkeypatch.setitem(sys.modules, 'agent', agent)
    monkeypatch.setitem(sys.modules, 'agent.secret_scope', scope)
    return values


# -- 1: a 404 is not an ACE server -----------------------------------------

def test_a_comfyui_that_404s_health_is_not_an_ace_server():
    """The ComfyUI on the owner's network answers /health with 404 — and was saved as ACE-Step."""
    with serve({'/health': (404, {'detail': 'not found'})}) as base:
        result = setup.probe_ace(base)
    assert result['ok'] is False
    assert 'HTTP 404' in result['detail']
    assert 'not an ACE-Step server' in result['detail']


def test_a_plain_text_health_answer_is_not_an_ace_server():
    """Codex, round 17: ACE-Step answers /health in JSON; "OK" in plain text is some other server."""
    with serve({'/health': (200, b'OK', 'text/plain'), '/': (404, {'detail': 'no'})}) as base:
        result = setup.probe_ace(base)
    assert result['ok'] is False


def test_a_json_health_answer_is_an_ace_server():
    with serve({'/health': (200, {'data': {'status': 'ok', 'models_initialized': True}})}) as base:
        result = setup.probe_ace(base)
    assert result['ok'] is True


def test_an_ace_server_behind_a_key_still_answers():
    """DeepSeek, round 17: a key-protected ACE-Step server answers /health with 401, and was accepted
    before this round; the stricter check must not lock it out."""
    with serve({'/health': (401, {'detail': 'missing key'})}) as base:
        result = setup.probe_ace(base)
    assert result['ok'] is True


def test_a_web_server_answering_a_page_on_every_path_is_not_an_ace_server():
    page = b'<!doctype html><title>hello</title>'
    with serve({'/health': (200, page, 'text/html'), '/': (200, page, 'text/html')}) as base:
        result = setup.probe_ace(base)
    assert result['ok'] is False


def test_a_health_answer_in_json_is_an_ace_server():
    with serve({'/health': (200, {'data': {'status': 'ok'}})}) as base:
        result = setup.probe_ace(base)
    assert result['ok'] is True


def test_a_root_that_names_ace_step_is_an_ace_server_even_when_health_404s():
    with serve({'/health': (404, {'detail': 'no'}),
                '/': (200, {'server': 'ACE-Step 1.5 api', 'status': 'ok'})}) as base:
        result = setup.probe_ace(base)
    assert result['ok'] is True


def test_a_404_server_is_never_saved_as_the_ace_engine(monkeypatch, tmp_path):
    """The whole bug: the page tested an address, got a 404 and SAVED it as ACE-Step."""
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    import warnings

    from fastapi import FastAPI

    from dashboard.plugin_api import router
    from music_studio import service, settings

    app = FastAPI()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        app.include_router(router, prefix='/api')
    from starlette.testclient import TestClient

    client = TestClient(app, base_url='http://127.0.0.1')
    with serve({'/health': (404, {'detail': 'no'})}) as base:
        response = client.post('/api/studio/setup/ace', json={'url': base})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['ok'] is False
    assert 'HTTP 404' in body['detail']
    assert not body.get('saved'), 'a failed test must not save the address'
    data_dir = service.plugin_data_dir(service.PLUGIN_ID)
    assert settings.read(data_dir).get('ace_remote_url', '') == ''
    assert setup._stored_endpoint(data_dir)['url'] == ''


# -- 2: a hosted key never takes the ACE-Step server's slot ----------------

def _save_ace_server(data_dir, url):
    """What POST /studio/setup/ace writes when a test passes (dashboard/plugin_api.py setup_ace)."""
    from music_studio import settings

    settings.write(data_dir, {'ace_remote_url': url,
                              'ace_remote': {'url': '', 'model': '', 'saved_at': '', 'key_env': ''}})


def test_saving_a_hosted_key_does_not_replace_a_saved_ace_server(tmp_path, hermes):
    ace = 'http://192.0.2.9:8001'
    _save_ace_server(tmp_path, ace)
    with pytest.raises(ValueError) as raised:
        setup.save_cloud(tmp_path, 'default', 'https://api.example.com/v1', 'sk-test')
    assert ace in str(raised.value)
    assert 'Music engines page' in str(raised.value)
    from music_studio import settings

    assert settings.read(tmp_path)['ace_remote_url'] == ace
    assert hermes.get('ACE_REMOTE_API_KEY') is None, 'nothing was half-saved'


def test_the_same_address_can_be_saved_twice(tmp_path, hermes):
    """Re-entering a key for the same endpoint is not a change of address."""
    setup._store_endpoint(tmp_path, 'https://api.example.com/v1', '')
    saved = setup.save_cloud(tmp_path, 'default', 'https://api.example.com/v1', 'sk-test')
    assert saved['ok'] is True
    assert saved['url'] == 'https://api.example.com/v1'


def test_a_hosted_endpoint_can_still_be_moved_to_another_address(tmp_path, hermes):
    """Changing your hosted provider is allowed: the slot holds a hosted endpoint, not a server."""
    setup.save_cloud(tmp_path, 'default', 'https://old.example/v1', 'sk-old')
    saved = setup.save_cloud(tmp_path, 'default', 'https://new.example/v1', 'sk-new')
    assert saved['url'] == 'https://new.example/v1'
    assert hermes['ACE_REMOTE_API_KEY'] == 'sk-new'


def test_a_key_left_after_remove_does_not_unlock_the_ace_server_slot(tmp_path, hermes):
    """Codex, round 17: Remove keeps the hosted key, so "a key exists" proved nothing about the slot."""
    setup.save_cloud(tmp_path, 'default', 'https://old.example/v1', 'sk-old')
    setup.forget_endpoint(tmp_path)
    ace = 'http://192.0.2.9:8001'
    _save_ace_server(tmp_path, ace)
    with pytest.raises(ValueError):
        setup.save_cloud(tmp_path, 'default', 'https://other.example/v1', 'sk-new')
    from music_studio import settings

    assert settings.read(tmp_path)['ace_remote_url'] == ace


# -- 3: the ACE-Step row can be removed -----------------------------------

def test_forgetting_the_endpoint_clears_the_one_saved_address(tmp_path):
    from music_studio import settings

    setup._store_endpoint(tmp_path, 'http://192.0.2.9:8001', 'ace')
    result = setup.forget_endpoint(tmp_path)
    assert result['ok'] is True
    saved = settings.read(tmp_path)
    assert saved['ace_remote_url'] == ''
    assert saved['ace_remote']['url'] == ''
    assert setup._stored_endpoint(tmp_path)['url'] == ''


def test_the_address_is_not_cleared_under_a_render_that_is_using_it(tmp_path):
    from music_studio.store import Store

    setup._store_endpoint(tmp_path, 'http://192.0.2.9:8001', '')
    store = Store(tmp_path)
    project = store.create(title='A', caption='music', lyrics='words', engine='ace_remote')
    store._save({**store.get(project['project_id']), 'takes': [
        {'take_id': 'take-1', 'state': 'running', 'engine_id': 'ace_remote'}]})
    with pytest.raises(ValueError, match='still being made'):
        setup.forget_endpoint(tmp_path)
    assert setup._stored_endpoint(tmp_path)['url'] == 'http://192.0.2.9:8001'


def test_remove_stops_the_running_host_using_the_old_address(tmp_path, monkeypatch):
    """Grok, round 17: the host got the address as ACE_REMOTE_URL at start, and the engine falls back
    to it when the setting is blank, so a removed server kept receiving renders."""
    monkeypatch.setenv('ACE_REMOTE_URL', 'http://192.0.2.9:8001')
    setup._store_endpoint(tmp_path, 'http://192.0.2.9:8001', '')
    setup.forget_endpoint(tmp_path)
    assert 'ACE_REMOTE_URL' not in os.environ


def test_the_engines_page_has_the_route_that_removes_the_ace_server():
    import warnings

    from fastapi import FastAPI

    from dashboard.plugin_api import router

    app = FastAPI()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        app.include_router(router, prefix='/api')
    paths = app.openapi()['paths']
    assert 'post' in paths.get('/api/studio/setup/ace/remove', {})


# -- 4: "Change this part" keeps the song, keeps its facts -----------------

def _tone(seconds=3.0, rate=8000):
    frames = int(seconds * rate)
    values = [int(0.4 * 32767) if i % 2 else -int(0.4 * 32767) for i in range(frames)]
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as audio:
        audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(struct.pack(f'<{len(values)}h', *values))
    return buf.getvalue()


def test_a_section_render_appends_the_typed_words_to_the_parents_caption(tmp_path):
    """The typed words describe a change to this part; they are not the whole song's caption."""
    from music_studio.jobs import Jobs
    from music_studio.store import Store

    store = Store(tmp_path)
    project_id = store.create(title='A', caption='warm folk, close vocals',
                              lyrics='hello there', bpm=90, keyscale='C major',
                              engine='ace_local')['project_id']
    store.approve(project_id)
    jobs = Jobs(store)
    parent = jobs.enqueue(project_id, seed=1, engine_id='ace_local')
    jobs.start(project_id, parent['take_id'], pid=os.getpid())
    jobs.succeed(project_id, parent['take_id'], _tone(),
                 metadata={'engine_id': 'ace_local'})
    # The draft moves on after the take was rendered.
    store.update(project_id, caption='a different song entirely', lyrics='new words',
                 bpm=140, keyscale='F# minor')

    child = jobs.enqueue_section(project_id, parent['take_id'], 0.5, 2.5, seed=2,
                                 caption='add a climax')
    snapshot = child['snapshot']
    assert snapshot['caption'] == 'warm folk, close vocals. Change: add a climax'
    assert snapshot['lyrics'] == 'hello there', 'the lyrics of the take that was rendered'
    assert snapshot['bpm'] == 90
    assert snapshot['keyscale'] == 'C major'


def test_a_section_without_words_still_carries_the_parents_caption(tmp_path):
    from music_studio.jobs import Jobs
    from music_studio.store import Store

    store = Store(tmp_path)
    project_id = store.create(title='A', caption='warm folk', lyrics='hello',
                              engine='ace_local')['project_id']
    store.approve(project_id)
    jobs = Jobs(store)
    parent = jobs.enqueue(project_id, seed=1, engine_id='ace_local')
    jobs.start(project_id, parent['take_id'], pid=os.getpid())
    jobs.succeed(project_id, parent['take_id'], _tone(),
                 metadata={'engine_id': 'ace_local'})
    store.update(project_id, caption='rewritten later')

    child = jobs.enqueue_section(project_id, parent['take_id'], 0.5, 2.5, seed=2)
    assert child['snapshot']['caption'] == 'warm folk'


def test_a_section_of_an_instrumental_take_stays_instrumental(tmp_path):
    """Codex, round 17: lyrics came from the parent but "instrumental" from the live draft, so a draft
    switched to vocals asked for lyrics the instrumental parent never had."""
    from music_studio.jobs import Jobs
    from music_studio.store import Store

    store = Store(tmp_path)
    project_id = store.create(title='A', caption='ambient pads', lyrics='', instrumental=True,
                              engine='ace_local')['project_id']
    store.approve(project_id)
    jobs = Jobs(store)
    parent = jobs.enqueue(project_id, seed=1, engine_id='ace_local')
    jobs.start(project_id, parent['take_id'], pid=os.getpid())
    jobs.succeed(project_id, parent['take_id'], _tone(),
                 metadata={'engine_id': 'ace_local'})
    store.update(project_id, instrumental=False, lyrics='now with words')

    child = jobs.enqueue_section(project_id, parent['take_id'], 0.5, 2.5, seed=2)
    assert child['snapshot']['instrumental'] is True
    assert child['snapshot']['lyrics'] == ''


def test_the_appended_caption_stays_inside_the_engine_limit():
    from music_studio.jobs import _captioned_change

    merged = _captioned_change('x' * 500, 'y' * 40)
    assert len(merged) <= 512
    assert merged.endswith('Change: ' + 'y' * 40)


# -- 5: the ACE sentence names a way that exists ---------------------------

def test_the_unconfigured_sentence_does_not_point_at_a_page_field(monkeypatch):
    import ace_engine

    monkeypatch.setattr(ace_engine, '_RUNTIME', '')
    monkeypatch.setattr(ace_engine, '_SOURCE', '')
    reason = ace_engine.unavailable_reason()
    assert 'Music engines' not in reason, 'that page has no field for these folders'
    assert 'ACE_RUNTIME_DIR' in reason and 'ACE_SOURCE_DIR' in reason
