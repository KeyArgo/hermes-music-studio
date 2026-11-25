"""Drafting when Hermes' saved default model is not one its provider serves.

On Linux the default was `hy3-preview` on OpenCode Go, which OpenCode Go does not include. Chats kept
working on a model picked for the chat, and "Discuss idea" failed with the provider's raw 400. These
tests pin what replaced that: drafting has its own model setting in Hermes, and a missing model comes
back as a plain sentence with the command that changes the default.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from music_studio import llm_bridge
from music_studio.drafting import DraftError, draft_song

ROOT = Path(__file__).resolve().parents[1]
MESSAGES = [{"role": "user", "content": "hi"}]
OPENCODE_400 = ("Error code: 400 - {'error': {'type': 'server_error', 'message': \"Model hy3-preview "
                "isn't included in OpenCode Go. See the models it includes at "
                "https://opencode.ai/console/go.\"}}")


class BadRequestError(Exception):
    """Shaped like openai.BadRequestError: the provider's text, and the status on the instance."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class NotFoundError(BadRequestError):
    def __init__(self, message):
        super().__init__(message, 404)


class PluginLlmTrustError(PermissionError):
    """Stand-in for Hermes' trust error; the bridge must not import the real one."""


class Facade:
    def __init__(self, *failures, text="hello"):
        self.failures = list(failures)
        self.text = text
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            failure = self.failures.pop(0)
            if callable(failure):
                failure(kwargs)
            else:
                raise failure
        return SimpleNamespace(text=self.text)


@pytest.fixture(autouse=True)
def _unbind_after():
    yield
    llm_bridge.unbind()


def test_drafting_asks_for_its_own_model_setting():
    facade = Facade()
    llm_bridge.bind(facade)
    assert llm_bridge.complete(MESSAGES) == "hello"
    assert facade.calls[0]["task"] == "music_studio_draft"


def test_a_model_the_provider_does_not_serve_says_how_to_fix_it():
    facade = Facade(BadRequestError(OPENCODE_400))
    llm_bridge.bind(facade)
    with pytest.raises(llm_bridge.LlmModelMissing) as caught:
        llm_bridge.complete(MESSAGES)
    message = str(caught.value)
    assert "default model is not available" in message
    assert "Music Studio drafting" in message
    assert "hy3-preview isn't included in OpenCode Go" in message
    assert caught.value.commands == ("hermes model",)
    # A setting, not a passing fault: one call, no retry on some other model.
    assert len(facade.calls) == 1


def test_a_404_for_the_model_is_the_same_case():
    llm_bridge.bind(Facade(NotFoundError("The model `gone-model` does not exist")))
    with pytest.raises(llm_bridge.LlmModelMissing):
        llm_bridge.complete(MESSAGES)


@pytest.mark.parametrize("text", [
    "Error code: 400 - {'error': 'max_tokens is too large'}",
    "Error code: 400 - {'error': 'context length exceeded'}",
])
def test_other_bad_requests_are_not_relabelled(text):
    llm_bridge.bind(Facade(BadRequestError(text)))
    with pytest.raises(llm_bridge.LlmFailed) as caught:
        llm_bridge.complete(MESSAGES)
    assert not isinstance(caught.value, llm_bridge.LlmModelMissing)


def test_the_draft_route_hands_the_page_the_command():
    llm_bridge.bind(Facade(BadRequestError(OPENCODE_400)))
    with pytest.raises(DraftError) as caught:
        draft_song({"prompt": "night", "action": "chat"}, llm_bridge.complete)
    assert caught.value.status == 502
    assert caught.value.commands == ["hermes model"]
    assert "Music Studio drafting" in caught.value.detail


def test_an_older_hermes_without_task_still_drafts():
    def old_signature(kwargs):
        if "task" in kwargs:
            raise TypeError("complete() got an unexpected keyword argument 'task'")
    facade = Facade(old_signature)
    llm_bridge.bind(facade)
    assert llm_bridge.complete(MESSAGES) == "hello"
    assert "task" not in facade.calls[-1] and facade.calls[-1]["timeout"]


def test_an_older_hermes_without_task_or_timeout_still_drafts():
    def oldest(kwargs):
        for keyword in ("task", "timeout"):
            if keyword in kwargs:
                raise TypeError(f"complete() got an unexpected keyword argument '{keyword}'")
    facade = Facade(oldest, oldest)
    llm_bridge.bind(facade)
    assert llm_bridge.complete(MESSAGES) == "hello"
    assert "task" not in facade.calls[-1] and "timeout" not in facade.calls[-1]


def test_a_process_that_does_not_know_the_task_uses_the_default_model():
    refusal = PluginLlmTrustError(
        "Plugin 'hermes-music-studio' cannot route through auxiliary task 'music_studio_draft' — a "
        "plugin may only pass a task key it registered itself")
    facade = Facade(refusal)
    llm_bridge.bind(facade)
    assert llm_bridge.complete(MESSAGES) == "hello"
    assert len(facade.calls) == 2 and "task" not in facade.calls[1]


def test_a_refused_model_override_is_still_the_trust_error():
    refusal = PluginLlmTrustError("set plugins.entries.hermes-music-studio.llm.allow_model_override")
    facade = Facade(refusal)
    llm_bridge.bind(facade)
    with pytest.raises(llm_bridge.LlmTrustError):
        llm_bridge.complete(MESSAGES, model="chosen")
    assert len(facade.calls) == 1


def _load_plugin():
    spec = importlib.util.spec_from_file_location("draft_task_entry", ROOT / "__init__.py")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin


def test_register_adds_the_drafting_model_setting():
    tasks = []
    ctx = SimpleNamespace(llm=Facade(), register_tool=lambda **kw: None,
                          register_skill=lambda *a, **kw: None,
                          register_auxiliary_task=lambda key, **kw: tasks.append((key, kw)))
    _load_plugin().register(ctx)
    assert [key for key, _ in tasks] == ["music_studio_draft"]
    assert tasks[0][1]["display_name"] == "Music Studio drafting"
    # No model is chosen for the owner: empty means the default model, as before.
    assert "defaults" not in tasks[0][1]


def test_register_survives_a_hermes_that_refuses_the_setting():
    def refuse(key, **kw):
        raise ValueError("already registered by another plugin")
    ctx = SimpleNamespace(llm=Facade(), register_tool=lambda **kw: None,
                          register_skill=lambda *a, **kw: None, register_auxiliary_task=refuse)
    _load_plugin().register(ctx)
    assert llm_bridge.available()
