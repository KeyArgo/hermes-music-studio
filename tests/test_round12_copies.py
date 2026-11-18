"""Round 12: which profile's copy of Music Studio answered, and a draft that never dies as a 500.

Hermes serves every profile from ONE process and imports plugin code with no isolation, so the first
``music_studio`` / ``standalone`` to load is the one each profile's routes silently run on. When that
copy is an older one, ``/studio/draft`` asks ``standalone.ideas`` for ``trust_error_response`` — a name
the old copy does not have — and the person saw ``hermes.api error 500: Internal Server Error``.

These tests pin the three halves of the fix:

* ``copies.foreign_copy`` names the other folder and the command that updates it;
* ``/studio/draft`` answers 502 with a sentence for a missing name, and for any unexpected error,
  instead of letting the exception escape as a bare 500;
* ``/verify.json`` says which folder's code is answering.
"""
import json
import os
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DRAFT_JSON = {
    "title": "Night Drive",
    "caption": "warm analog synthwave, slow arpeggio, no vocals",
    "lyrics": "[verse]\nheadlights on the empty road",
    "bpm": 96,
    "keyscale": "A minor",
    "duration_seconds": 60,
    "instrumental": False,
}

# The path a second profile's copy would live at, and the folder it was loaded from.
JESTER_FILE = '/x/profiles/jester/plugins/hermes-music-studio/music_studio/__init__.py'
JESTER_ROOT = '/x/profiles/jester/plugins/hermes-music-studio'


class FakeResult:
    """What ``ctx.llm.complete`` returns: an object with ``.text``."""

    def __init__(self, text):
        self.text = text


class FakeLlm:
    def __init__(self, reply=DRAFT_JSON):
        self.reply = reply

    def complete(self, messages, **kwargs):
        return FakeResult(json.dumps(self.reply))


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """The plugin route, with ``raise_server_exceptions=False`` so a bare 500 is visible as one."""
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    from dashboard.plugin_api import router

    app = FastAPI()
    app.include_router(router, prefix='/api')
    return TestClient(app, base_url='http://127.0.0.1', raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _unbind_after():
    yield
    from music_studio import llm_bridge

    llm_bridge.unbind()


# ---------------------------------------------------------------- which copy is loaded


def test_our_own_root_is_not_a_foreign_copy():
    """The normal case: the packages in sys.modules are this checkout's, so nothing is reported."""
    from music_studio import copies

    assert copies.foreign_copy(str(ROOT)) is None


def test_a_copy_under_another_profile_is_named_with_the_update_command(monkeypatch):
    """The bug: another profile's older copy won the import, and the person needs its name."""
    from music_studio import copies

    fake = types.ModuleType('music_studio')
    fake.__file__ = JESTER_FILE
    # monkeypatch puts the real module back when the test ends, so nothing leaks into other tests.
    monkeypatch.setitem(sys.modules, 'music_studio', fake)

    sentence = copies.foreign_copy(str(ROOT))

    assert sentence is not None
    assert JESTER_ROOT in sentence, sentence
    assert 'jester' in sentence, sentence
    assert 'hermes -p jester plugins update hermes-music-studio' in sentence, sentence
    # It has to say why updating this profile alone will not help.
    assert 'one process' in sentence, sentence


def test_a_copy_from_a_checkout_without_a_profile_says_the_other_profile(monkeypatch):
    """A git checkout has no ``profiles/<name>`` in its path; guessing a name would be a lie."""
    from music_studio import copies

    fake = types.ModuleType('standalone')
    fake.__file__ = '/opt/builds/hermes-music-studio/standalone/__init__.py'
    monkeypatch.setitem(sys.modules, 'standalone', fake)

    sentence = copies.foreign_copy(str(ROOT))

    assert sentence is not None
    assert 'the other profile' in sentence, sentence


# ---------------------------------------------------------------- /studio/draft never leaks a 500


def test_a_name_the_old_copy_lacks_is_a_502_with_a_sentence(client, monkeypatch):
    """An older ``standalone.ideas`` has no ``trust_error_response``: ImportError, not a bare 500."""
    from music_studio import llm_bridge

    llm_bridge.bind(FakeLlm())
    # An empty module is exactly what an older copy's ideas offers to this newer import line.
    monkeypatch.setitem(sys.modules, 'standalone.ideas', types.ModuleType('standalone.ideas'))

    answer = client.post('/api/studio/draft', json={'prompt': 'a night drive'})

    assert answer.status_code == 502, (answer.status_code, answer.text)
    detail = answer.json()['detail']
    assert 'Music Studio failed: ImportError' in detail, detail


def test_a_mismatch_answers_with_the_update_command(client, monkeypatch):
    """The headline case: the loaded copy is another profile's, so name it instead of the class."""
    from music_studio import llm_bridge

    llm_bridge.bind(FakeLlm())
    fake = types.ModuleType('music_studio')
    fake.__file__ = JESTER_FILE
    monkeypatch.setitem(sys.modules, 'music_studio', fake)
    # The old copy's ideas module: the name this route imports is the one it does not have.
    monkeypatch.setitem(sys.modules, 'standalone.ideas', types.ModuleType('standalone.ideas'))

    answer = client.post('/api/studio/draft', json={'prompt': 'a night drive'})

    assert answer.status_code == 502, (answer.status_code, answer.text)
    detail = answer.json()['detail']
    assert JESTER_ROOT in detail and 'jester' in detail, detail
    assert 'plugins update hermes-music-studio' in detail, detail


def test_an_unexpected_error_is_a_502_naming_the_class(client, monkeypatch):
    """Anything the route never heard of: a sentence with the class and message, and a logged warning."""
    from music_studio import llm_bridge
    from standalone import ideas

    llm_bridge.bind(FakeLlm())

    def explode(text):
        raise KeyError('draft')

    monkeypatch.setattr(ideas, 'parse_draft', explode)
    answer = client.post('/api/studio/draft', json={'prompt': 'a night drive'})

    assert answer.status_code == 502, (answer.status_code, answer.text)
    assert answer.json()['detail'].startswith('Music Studio failed: KeyError'), answer.text


def test_the_routes_own_refusals_are_unchanged(client):
    """Wrapping the body must not turn a 400/422 into a 502: those sentences are already right."""
    from music_studio import llm_bridge

    llm_bridge.bind(FakeLlm())
    empty = client.post('/api/studio/draft', json={'prompt': '   '})
    assert empty.status_code == 400, empty.text
    bad_style = client.post('/api/studio/draft', json={'prompt': 'x', 'style': {'a': 1}})
    assert bad_style.status_code == 422, bad_style.text


# ---------------------------------------------------------------- /verify.json says which copy


def test_the_verify_summary_says_which_folder_answered():
    """A bug report has to name the copy that answered; the existing fields stay put."""
    from music_studio import verify

    info = verify.summary()

    assert info['code_folder'] == os.path.realpath(str(ROOT / 'music_studio')), info['code_folder']
    assert {'ok', 'rows', 'stamp', 'checked_at'} <= set(info), sorted(info)
