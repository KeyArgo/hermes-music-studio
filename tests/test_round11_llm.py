"""Round 11: a draft that runs out of time says so, instead of a bare 500.

Proven cause: Hermes' ``ctx.llm.complete()`` applies a 30 second default. A longer draft raised
``openai.APITimeoutError`` — which is not a ``RuntimeError`` — so ``/studio/draft`` (which caught
only ``LlmUnavailable`` / ``LlmTrustError`` / ``RuntimeError``) let it escape as
``hermes.api error 500: internal server error``.

These tests pin the bridge end: a 120 second timeout is asked for, a facade without the keyword still
drafts, and every non-trust failure becomes ``LlmFailed`` carrying a sentence the page can show.
``tests/test_round11_pane.mjs`` pins the pane end: that sentence reaches the person, not raw JSON.
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

DRAFT_JSON = {
    "title": "Night Drive",
    "caption": "warm analog synthwave, slow arpeggio, no vocals",
    "lyrics": "[verse]\nheadlights on the empty road",
    "bpm": 96,
    "keyscale": "A minor",
    "duration_seconds": 60,
    "instrumental": False,
}

TIMEOUT_SENTENCE = ("Hermes' model did not answer within 120 seconds. "
                    "Try again, or pick a faster model.")


class APITimeoutError(Exception):
    """The class openai raises for a slow provider: an Exception, not a RuntimeError.

    It is not a subclass of TimeoutError either, which is why the bridge checks the class name.
    """


class TimeoutLlm:
    def complete(self, **kwargs):
        raise APITimeoutError("Request timed out.")


class BrokenLlm:
    def complete(self, **kwargs):
        raise ValueError("boom")


class FakeResult:
    """What ``ctx.llm.complete`` returns: an object with ``.text``."""

    def __init__(self, text):
        self.text = text


class RecordingLlm:
    def __init__(self, reply=DRAFT_JSON):
        self.calls = []
        self.reply = reply

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return FakeResult(json.dumps(self.reply))


class OldFacade:
    """A facade whose complete() predates the timeout keyword."""

    def complete(self, messages, max_tokens, temperature):
        return FakeResult(json.dumps(DRAFT_JSON))


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """The plugin route with the real bridge: the fakes are the host's model, not the bridge."""
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    from dashboard.plugin_api import router

    app = FastAPI()
    app.include_router(router, prefix='/api')
    return TestClient(app, base_url='http://127.0.0.1')


@pytest.fixture(autouse=True)
def _unbind_after():
    yield
    from music_studio import llm_bridge

    llm_bridge.unbind()


def use(model):
    from music_studio import llm_bridge

    llm_bridge.bind(model)
    return llm_bridge


def test_a_timeout_is_a_502_with_a_sentence_a_person_can_act_on(client):
    """The bug: APITimeoutError escaped every except clause and became an empty 500."""
    use(TimeoutLlm())
    answer = client.post('/api/studio/draft', json={'prompt': 'a night drive'})
    assert answer.status_code == 502, answer.text
    assert answer.json() == {'detail': TIMEOUT_SENTENCE}


def test_any_other_model_error_keeps_its_class_and_message(client):
    """A provider error is still readable: the sentence names what was raised."""
    use(BrokenLlm())
    answer = client.post('/api/studio/draft', json={'prompt': 'a night drive'})
    assert answer.status_code == 502, answer.text
    detail = answer.json()['detail']
    assert 'ValueError: boom' in detail, detail


def test_the_bridge_asks_for_a_long_enough_timeout(client):
    """Hermes' 30 second default is the cause; the fix is to ask for TIMEOUT_SECONDS itself."""
    from music_studio import llm_bridge

    assert llm_bridge.TIMEOUT_SECONDS == 120
    fake = RecordingLlm()
    use(fake)
    answer = client.post('/api/studio/draft', json={'prompt': 'a night drive'})
    assert answer.status_code == 200, answer.text
    assert fake.calls[0]['timeout'] == 120, fake.calls[0].keys()


def test_a_facade_without_a_timeout_parameter_still_drafts(client):
    """An older Hermes rejects the keyword with a TypeError; that is retried once without it."""
    use(OldFacade())
    answer = client.post('/api/studio/draft', json={'prompt': 'a night drive'})
    assert answer.status_code == 200, answer.text
    assert answer.json()['draft']['title'] == 'Night Drive'


def test_the_original_exception_is_kept_as_the_cause():
    """`raise LlmFailed(...) from exc` — the traceback in the log still names the real failure."""
    from music_studio import llm_bridge

    llm_bridge.bind(BrokenLlm())
    with pytest.raises(llm_bridge.LlmFailed) as caught:
        llm_bridge.complete([{'role': 'user', 'content': 'hi'}])
    assert isinstance(caught.value.__cause__, ValueError)
    assert 'boom' in str(caught.value)


def test_the_standalone_ideas_route_passes_the_sentence_through(client):
    """ideas.py used to turn every failure into a vague 503 "connection failed"."""
    use(TimeoutLlm())
    answer = client.post('/api/studio/ideas', json={
        'messages': [{'role': 'user', 'content': 'a song about the last bus'}],
        'action': 'draft', 'tone': '', 'style': 50,
    })
    assert answer.status_code == 502, answer.text
    assert answer.json() == {'detail': TIMEOUT_SENTENCE}
