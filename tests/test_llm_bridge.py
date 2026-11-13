"""Drafting uses the host's public LLM facade, and nothing private.

Review item 1. The studio drafted by spawning `hermes --print-runtime-command` and running a worker that
imported core modules, built an `AIAgent` with resolved provider credentials and called a private image
handler. These tests pin the replacement: `ctx.llm.complete` through `music_studio.llm_bridge`, no
subprocess, no private imports, and cover-art *generation* gone with the route that used it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from music_studio import llm_bridge

ROOT = Path(__file__).resolve().parents[1]


class FakeResult:
    def __init__(self, text: str):
        self.text = text


class FakeLlm:
    """The shape `ctx.llm` exposes: `complete(**kwargs)` returning an object with `.text`."""

    def __init__(self, text='{"title": "Night Bus"}', refuse_override: bool = False):
        self.text = text
        self.refuse_override = refuse_override
        self.calls: list[dict] = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.refuse_override and ("model" in kwargs or "provider" in kwargs):
            raise PermissionError(
                "Plugin 'hermes-music-studio' cannot override the model (set "
                "plugins.entries.hermes-music-studio.llm.allow_model_override to true to allow)."
            )
        return FakeResult(self.text)


@pytest.fixture(autouse=True)
def _unbind_after():
    yield
    llm_bridge.unbind()


def test_completion_goes_through_the_bound_facade():
    llm = FakeLlm("hello")
    llm_bridge.bind(llm)
    assert llm_bridge.complete([{"role": "user", "content": "hi"}]) == "hello"
    assert llm.calls and llm.calls[0]["messages"][0]["content"] == "hi"


def test_an_untrusted_override_falls_back_to_the_host_default():
    llm = FakeLlm('{"title": "Fallback"}', refuse_override=True)
    llm_bridge.bind(llm)
    assert llm_bridge.complete([{"role": "user", "content": "hi"}], model="some-model") == '{"title": "Fallback"}'
    assert len(llm.calls) == 2
    assert "model" in llm.calls[0] and "model" not in llm.calls[1], (
        "the retry must drop the override, not fail the draft"
    )


def test_without_a_host_the_error_says_where_to_draft():
    llm_bridge.unbind()
    with pytest.raises(llm_bridge.LlmUnavailable) as caught:
        llm_bridge.complete([{"role": "user", "content": "hi"}])
    assert "Hermes Desktop" in str(caught.value)


def test_the_route_drafts_without_spawning_anything(monkeypatch):
    """A subprocess is the reviewed design; this asserts the new one never reaches for it."""
    from standalone import ideas

    llm_bridge.bind(FakeLlm('{"title": "Night Bus", "caption": "dreamy synth", "lyrics": "la la"}'))

    def forbidden(*args, **kwargs):  # pragma: no cover - only runs if the old path returns
        raise AssertionError("drafting must not spawn a process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    payload = ideas.IdeaRequest(
        provider=None, model=None, action="draft", tone="", style=50,
        messages=[ideas.ChatMessage(role="user", content="a song about the last bus")],
    )
    response = ideas.studio_ideas(payload)
    body = response.body if hasattr(response, "body") else response
    assert b"Night Bus" in body


def test_the_worker_and_private_imports_are_gone():
    import ast

    assert not (ROOT / "standalone" / "hermes_worker.py").exists(), (
        "the private-core worker must not come back"
    )
    # `subprocess` is legitimate elsewhere (renderers, the host launcher); what must never come back is
    # a *core* import, and a subprocess in the drafting path specifically.
    forbidden = {"hermes_bootstrap", "run_agent", "hermes_cli.runtime_provider",
                 "tools.image_generation_tool", "agent.image_gen_provider"}
    offenders: list[str] = []
    for path in list((ROOT / "standalone").glob("*.py")) + list((ROOT / "music_studio").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == bad or name.startswith(bad + ".") for bad in forbidden):
                    offenders.append(f"{path.relative_to(ROOT)} imports {name}")
    ideas_tree = ast.parse((ROOT / "standalone" / "ideas.py").read_text(encoding="utf-8"))
    for node in ast.walk(ideas_tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "subprocess" for alias in node.names), (
                "drafting must not spawn a process any more"
            )

    assert not offenders, "private core imports:\n" + "\n".join(offenders)


def test_cover_generation_is_not_a_route():
    from standalone.workspaces import router

    paths = {getattr(route, "path", "") for route in router.routes}
    assert not any(path.endswith("/generate") for path in paths), (
        "cover-art generation used a private core call; it comes back only with a public API"
    )
    assert any(path.endswith("/studio/covers/{project_id}") for path in paths), "upload must still work"


def test_cover_generation_stays_gone_until_hermes_offers_a_public_call():
    """The plugin must not reach into core's private image tool to get a picture back.

    Issue drafts live outside the repository now, so this test pins the code, not a document: no
    route and no import of the private handler, and uploads still work (asserted above).
    """
    text = (ROOT / "standalone" / "ideas.py").read_text(encoding="utf-8")
    imports = [line for line in text.splitlines() if line.strip().startswith(("import ", "from "))]
    assert not any("image_generation" in line for line in imports), (
        "the docstring may name the private surface; the code may not import it"
    )
