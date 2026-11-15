"""The remote ACE key is declared, resolved through the host, and never written world-readable.

Review item 6. `ace_remote.py` read `ACE_REMOTE_API_KEY` from the bare environment, which bypasses the
profile scope (a key in the active profile's `.env` was invisible, a key from an unrelated environment was
not), and the manifest never declared it. `setup.save_cloud` wrote the key file and chmod-ed it afterwards,
leaving a window where it was readable — and a crash in that window leaves it readable for good.

These fail on the reviewed tree: there was no `requires_env`, the module read `os.environ`, and the file
was created with default permissions.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_studio(tmp_path, monkeypatch):
    """Import the modules with a throwaway library, so no later test writes into the real one.

    `music_studio.service` and `standalone.app` resolve their data directory at import time; a test that
    imports them with the default in place leaves every test after it pointed at the owner's studio.
    """
    monkeypatch.setenv("HEWN_STUDIO_DATA_DIR", str(tmp_path / "studio"))
    return tmp_path


@pytest.fixture
def hermes_store(monkeypatch):
    """A fake Hermes credential store, on the modules the plugin is allowed to import."""
    values: dict[str, str] = {}

    config = types.ModuleType("hermes_cli.config")

    def save_env_value(name, value):
        values[name] = value

    config.save_env_value = save_env_value
    hermes_cli = types.ModuleType("hermes_cli")
    hermes_cli.config = config

    scope = types.ModuleType("agent.secret_scope")

    def get_secret(name, default=None):
        return values.get(name, default)

    scope.get_secret = get_secret
    scope.invalidate_env_file_cache = lambda: None
    agent = types.ModuleType("agent")
    agent.secret_scope = scope

    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", config)
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)
    return values


def test_the_manifest_declares_the_remote_key():
    text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert "requires_env" in text
    assert "ACE_REMOTE_API_KEY" in text, "the catalog must prompt for a key the engine reads"


def test_the_engine_reads_the_key_through_the_secret_scope(monkeypatch):
    """A key in the profile scope must win over the bare environment."""
    calls: list[str] = []

    module = types.ModuleType("agent.secret_scope")

    def get_secret(name, default=None):
        calls.append(name)
        return "from-the-profile"

    module.get_secret = get_secret
    package = types.ModuleType("agent")
    package.secret_scope = module
    monkeypatch.setitem(sys.modules, "agent", package)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", module)
    monkeypatch.setenv("ACE_REMOTE_API_KEY", "from-the-environment")

    from music_studio.engines.ace_remote import _remote_key

    assert _remote_key() == "from-the-profile"
    assert calls == ["ACE_REMOTE_API_KEY"]


def test_the_engine_still_works_without_hermes(monkeypatch):
    """A standalone install has no `agent` package; the environment is the honest fallback."""
    monkeypatch.setitem(sys.modules, "agent", None)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", None)
    monkeypatch.setenv("ACE_REMOTE_API_KEY", "from-the-environment")

    from music_studio.engines.ace_remote import _remote_key

    assert _remote_key() == "from-the-environment"


def test_the_key_is_saved_through_hermes_not_a_file(tmp_path, hermes_store):
    from music_studio import setup

    setup.save_cloud(tmp_path, "default", "https://api.example.com/v1", "sk-test-123")

    assert hermes_store["ACE_REMOTE_API_KEY"] == "sk-test-123"
    assert not (tmp_path / "cloud-keys.json").exists(), "a key file is not the store any more"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert "sk-test-123" not in path.read_text(errors="ignore"), (
                f"the key was written to {path}")


def test_the_key_store_never_returns_the_key_to_the_page(tmp_path, hermes_store):
    """Call the real save/list path: the response the page receives must not carry the secret."""
    from music_studio import setup

    saved = setup.save_cloud(tmp_path, "default", "https://api.example.com/v1", "sk-test-123")
    assert saved["has_key"] is True
    assert "sk-test-123" not in json.dumps(saved), "save_cloud echoed the key back"

    listing = setup.list_cloud(tmp_path)
    text = json.dumps(listing)
    assert "sk-test-123" not in text, f"the listing leaked the key: {text}"
    assert listing, f"the listing returned nothing to inspect: {listing}"
    for row in listing:
        assert "key" not in row, f"a row carries the key: {row}"
        assert row["has_key"] is True


def test_saving_again_replaces_the_key_hermes_holds(tmp_path, hermes_store):
    from music_studio import setup

    hermes_store["ACE_REMOTE_API_KEY"] = "sk-old"
    setup.save_cloud(tmp_path, "default", "https://api.example.com/v1", "sk-new-key")

    assert hermes_store["ACE_REMOTE_API_KEY"] == "sk-new-key"


def test_a_failed_credential_write_is_reported_and_writes_no_secret(tmp_path, hermes_store,
                                                                   monkeypatch):
    """A save that Hermes refuses must fail loudly, not fall back to a plaintext file."""
    from music_studio import setup

    config = sys.modules["hermes_cli.config"]

    def refuse(name, value):
        raise PermissionError("the credential store is not writable")

    monkeypatch.setattr(config, "save_env_value", refuse)
    with pytest.raises(ValueError, match="could not be saved to Hermes"):
        setup.save_cloud(tmp_path, "default", "https://api.example.com/v1", "sk-test-123")
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == [], (
        "a secret was written by a fallback")


def test_a_scoped_miss_never_falls_back_to_the_process_environment(monkeypatch):
    """The reason the scope exists: a refusal must not be answered with somebody else's key.

    Under profile multiplexing `get_secret` raises `UnscopedSecretError` rather than guessing. The old
    code caught every exception and returned `os.environ[...]`, so a key exported by another profile (or
    by a parent process) was sent to the remote server. Only a *missing Hermes module* is a fallback now.
    """
    class UnscopedSecretError(RuntimeError):
        pass

    module = types.ModuleType("agent.secret_scope")

    def get_secret(name, default=None):
        raise UnscopedSecretError("no secret scope in this process")

    module.get_secret = get_secret
    module.UnscopedSecretError = UnscopedSecretError
    package = types.ModuleType("agent")
    package.secret_scope = module
    monkeypatch.setitem(sys.modules, "agent", package)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", module)
    monkeypatch.setenv("ACE_REMOTE_API_KEY", "another-profile-key")

    from music_studio.engines import ace_remote

    with pytest.raises(UnscopedSecretError):
        ace_remote._remote_key()
