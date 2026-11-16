"""A chosen provider and model is sent once. A trust refusal comes back with the two commands.

Hermes raises when a plugin passes ``provider=`` or ``model=`` without
``allow_provider_override`` / ``allow_model_override``. The studio must not hide that by
drafting again on the host's default model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from music_studio import llm_bridge

ROOT = Path(__file__).resolve().parents[1]
PANE = ROOT / "desktop" / "plugin.js"

ALLOW_PROVIDER = (
    "hermes config set plugins.entries.hermes-music-studio.llm.allow_provider_override true"
)
ALLOW_MODEL = (
    "hermes config set plugins.entries.hermes-music-studio.llm.allow_model_override true"
)
COMMANDS = [ALLOW_PROVIDER, ALLOW_MODEL]


class PluginLlmTrustError(PermissionError):
    """Stand-in for ``agent.plugin_llm.PluginLlmTrustError``. The bridge must not import it."""


class RecordingLlm:
    def __init__(self, text: str = '{"title": "Night Drive"}', refuse=None):
        self.text = text
        self.refuse = refuse
        self.calls: list[dict] = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.refuse is not None and ("provider" in kwargs or "model" in kwargs):
            raise self.refuse
        return type("Result", (), {"text": self.text})()


@pytest.fixture(autouse=True)
def _unbind_after():
    yield
    llm_bridge.unbind()


def _messages():
    return [{"role": "user", "content": "a night drive"}]


def test_a_chosen_provider_and_model_are_passed_to_the_facade():
    llm = RecordingLlm("hello")
    llm_bridge.bind(llm)
    assert llm_bridge.complete(_messages(), provider="nous", model="model-x") == "hello"
    assert len(llm.calls) == 1
    assert llm.calls[0]["provider"] == "nous"
    assert llm.calls[0]["model"] == "model-x"


def test_an_empty_choice_sends_no_override():
    llm = RecordingLlm("hello")
    llm_bridge.bind(llm)
    assert llm_bridge.complete(_messages(), provider="", model=None) == "hello"
    assert "provider" not in llm.calls[0] and "model" not in llm.calls[0]


def test_plugin_llm_trust_error_returns_both_commands_and_does_not_retry():
    llm = RecordingLlm(refuse=PluginLlmTrustError("not on the allowlist"))
    llm_bridge.bind(llm)
    with pytest.raises(llm_bridge.LlmTrustError) as caught:
        llm_bridge.complete(_messages(), provider="nous", model="model-x")
    assert len(llm.calls) == 1
    assert "provider" in llm.calls[0] and "model" in llm.calls[0]
    assert list(caught.value.commands) == COMMANDS
    assert "then restart Hermes" in str(caught.value)
    body = caught.value.payload()
    assert set(body) == {"error", "commands"}
    assert body["commands"] == COMMANDS
    assert body["error"] == str(caught.value)


def test_a_message_that_names_the_flags_is_the_same_refusal():
    llm = RecordingLlm(refuse=RuntimeError(
        "Plugin 'hermes-music-studio' cannot override the model (set "
        "plugins.entries.hermes-music-studio.llm.allow_model_override to true to allow)."
    ))
    llm_bridge.bind(llm)
    with pytest.raises(llm_bridge.LlmTrustError) as caught:
        llm_bridge.complete(_messages(), model="model-x")
    assert len(llm.calls) == 1
    assert list(caught.value.commands) == COMMANDS
    assert ALLOW_PROVIDER in caught.value.commands and ALLOW_MODEL in caught.value.commands


def test_an_ordinary_failure_is_not_relabelled_or_retried():
    llm = RecordingLlm(refuse=RuntimeError("connection reset"))
    llm_bridge.bind(llm)
    with pytest.raises(RuntimeError, match="connection reset"):
        llm_bridge.complete(_messages(), model="model-x")
    assert len(llm.calls) == 1


def _ideas_client() -> TestClient:
    from standalone.ideas import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _idea_body(**extra) -> dict:
    body = {
        "messages": [{"role": "user", "content": "a night drive"}],
        "action": "draft",
        "tone": "warm",
        "style": 70,
    }
    body.update(extra)
    return body


def test_ideas_forwards_the_body_provider_and_model(monkeypatch):
    from standalone import ideas

    seen = []

    def fake_complete(messages, **kwargs):
        seen.append(kwargs)
        return '{"title": "Night Drive", "caption": "warm"}'

    monkeypatch.setattr(ideas.llm_bridge, "complete", fake_complete)
    body = ideas.build_request(
        "nous", "model-x", [{"role": "user", "content": "a night drive"}], "draft", "warm", 70
    )
    assert body["provider"] == "nous" and body["model"] == "model-x"
    assert ideas._complete(body) == '{"title": "Night Drive", "caption": "warm"}'
    assert seen[0]["provider"] == "nous" and seen[0]["model"] == "model-x"


def test_the_ideas_route_returns_the_trust_error_as_json():
    llm = RecordingLlm(refuse=PluginLlmTrustError(
        "cannot override the provider (allow_provider_override)"
    ))
    llm_bridge.bind(llm)
    response = _ideas_client().post("/studio/ideas", json=_idea_body(provider="nous", model="model-x"))
    assert 400 <= response.status_code < 500
    assert response.status_code == 403
    body = response.json()
    assert set(body) == {"error", "commands"}
    assert body["commands"] == COMMANDS
    assert "then restart Hermes" in body["error"]
    assert "detail" not in body
    assert len(llm.calls) == 1
    assert llm.calls[0]["provider"] == "nous" and llm.calls[0]["model"] == "model-x"


def test_the_draft_route_returns_the_same_json(monkeypatch, tmp_path):
    monkeypatch.setenv("HEWN_STUDIO_DATA_DIR", str(tmp_path))
    from dashboard.plugin_api import router

    llm = RecordingLlm(refuse=RuntimeError(
        "set plugins.entries.hermes-music-studio.llm.allow_provider_override to true"
    ))
    llm_bridge.bind(llm)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    response = TestClient(app).post(
        "/api/studio/draft",
        json={"prompt": "a night drive", "tone": "warm", "style": 70,
              "provider": "nous", "model": "model-x"},
    )
    assert 400 <= response.status_code < 500
    body = response.json()
    assert body["commands"] == COMMANDS
    assert "then restart Hermes" in body["error"]
    assert len(llm.calls) == 1
    assert llm.calls[0]["provider"] == "nous" and llm.calls[0]["model"] == "model-x"


def test_the_pane_draft_body_carries_provider_and_model():
    text = PANE.read_text(encoding="utf-8")
    start = text.index("hermes-music-studio:draft")
    handler = text[start:text.index("hermes-music-studio:closed", start)]
    assert "body.provider = data.provider" in handler
    assert "body.model = data.model" in handler
