"""Round 10: the pane's draft request has to keep its action, all the way to the route.

``app.js`` sends ``action: 'chat'`` for "Discuss idea" and ``action: 'draft'`` for "Fill my draft".
``desktop/plugin.js`` dropped ``action`` from the body it forwarded, and ``/studio/draft`` always
built a *draft* request, so "Discuss idea" came back as a draft — or, because a chat reply is prose
and not JSON, as a 502 from ``parse_draft``.

These tests pin the plugin route end: a chat request answers with the model's text in
``content``, exactly like the standalone ``/api/studio/ideas`` route does, and a draft request is
unchanged. ``tests/test_round10_pane.mjs`` pins the pane end: the action the frame sends is the
action the route receives.
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
    "caption": "warm analog synthwave, slow arpeggio, soft drums, no vocals",
    "lyrics": "[verse]\nheadlights on the empty road",
    "bpm": 96,
    "keyscale": "A minor",
    "duration_seconds": 60,
    "instrumental": False,
}

CHAT_REPLY = "Try a quiet bridge in A minor. Muted strings under the second verse would open it up."


class FakeLlm:
    """The host's model, as ``llm_bridge.complete`` sees it."""

    def __init__(self, reply=DRAFT_JSON):
        self.calls = []
        self.reply = reply if isinstance(reply, str) else json.dumps(reply)

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.reply


@pytest.fixture()
def draft_route(monkeypatch, tmp_path):
    """The plugin route with a stand-in model; returns (client, factory of fakes)."""
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    from dashboard.plugin_api import router
    from music_studio import llm_bridge

    def build(reply=DRAFT_JSON):
        fake = FakeLlm(reply)
        monkeypatch.setattr(llm_bridge, 'available', lambda: True)
        monkeypatch.setattr(llm_bridge, 'complete', fake.complete)
        app = FastAPI()
        app.include_router(router, prefix='/api')
        return TestClient(app, base_url='http://127.0.0.1'), fake

    return build


def test_a_chat_request_comes_back_as_chat_text(draft_route):
    """The bug: "Discuss idea" asked for chat and the route answered with a draft or a 502."""
    client, _ = draft_route(CHAT_REPLY)
    answer = client.post('/api/studio/draft',
                         json={'prompt': 'how could I open the second verse?', 'action': 'chat',
                               'tone': 'warm', 'style': 60})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body['action'] == 'chat', body
    assert body['content'] == CHAT_REPLY, 'app.js reads d.content as the chat reply'
    assert 'draft' not in body, 'a chat reply must not be handed over as a draft'


def test_a_chat_request_uses_the_chat_prompt(draft_route):
    """The system prompt and the token cap follow the action, not a hardcoded 'draft'."""
    from standalone.ideas import CHAT_SYSTEM_PROMPT

    client, fake = draft_route(CHAT_REPLY)
    answer = client.post('/api/studio/draft',
                         json={'prompt': 'how could I open the second verse?', 'action': 'chat'})
    assert answer.status_code == 200, answer.text
    assert fake.calls, 'the route did not call the model'
    system = fake.calls[0]['messages'][0]
    assert system['role'] == 'system'
    assert system['content'].startswith(CHAT_SYSTEM_PROMPT), system['content'][:80]
    assert fake.calls[0]['max_tokens'] == 800, 'chat replies are capped lower than drafts'


def test_a_draft_request_still_returns_a_draft(draft_route):
    client, fake = draft_route()
    answer = client.post('/api/studio/draft',
                         json={'prompt': 'a night drive', 'action': 'draft', 'tone': 'warm',
                               'style': 70})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body['draft']['title'] == 'Night Drive'
    assert body['written_by'] == 'ctx.llm'
    assert 'content' not in body, 'a draft answer stays a draft'
    assert fake.calls[0]['max_tokens'] == 1200


def test_a_body_without_an_action_still_drafts(draft_route):
    """The route's original contract: an older pane sends no action and must keep working."""
    client, _ = draft_route()
    answer = client.post('/api/studio/draft', json={'prompt': 'a night drive'})
    assert answer.status_code == 200, answer.text
    assert answer.json()['draft']['title'] == 'Night Drive'


def test_an_unknown_action_is_treated_as_a_draft(draft_route):
    """Only 'chat' is special. Anything else keeps the draft behaviour instead of a 500."""
    client, _ = draft_route()
    for action in ('delete', 'CHAT', ' draft ', None, 3):
        answer = client.post('/api/studio/draft', json={'prompt': 'a night drive',
                                                        'action': action})
        assert answer.status_code == 200, (action, answer.text)
        assert answer.json()['draft']['title'] == 'Night Drive', action


def test_a_chat_with_an_empty_reply_is_a_502(draft_route):
    """Same refusal the standalone /api/studio/ideas route makes, not an empty chat bubble."""
    client, _ = draft_route('   ')
    answer = client.post('/api/studio/draft',
                         json={'prompt': 'how could I open the second verse?', 'action': 'chat'})
    assert answer.status_code == 502, answer.text
    assert answer.json() == {'detail': 'Hermes returned no content'}


def test_a_chat_reply_that_is_too_long_is_a_502(draft_route):
    from standalone.ideas import MAX_REPLY_CHARS

    client, _ = draft_route('x' * (MAX_REPLY_CHARS + 1))
    answer = client.post('/api/studio/draft',
                         json={'prompt': 'how could I open the second verse?', 'action': 'chat'})
    assert answer.status_code == 502, answer.text
    assert answer.json() == {'detail': 'Hermes returned an oversized response'}
