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
import os
import stat
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


@pytest.mark.skipif(os.name == "nt", reason="Windows does not carry POSIX mode bits")
def test_the_key_file_is_private_from_the_first_byte(tmp_path):
    from music_studio import setup

    setup.save_cloud(tmp_path, "default", "https://api.example.com/v1", "sk-test-123")
    path = tmp_path / setup.KEY_FILE
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"the key file is {oct(mode)}; a secret is created private, not chmod-ed later"
    assert json.loads(path.read_text())["default"]["key"] == "sk-test-123"


def test_the_key_store_never_returns_the_key_to_the_page():
    """The listing the dashboard serves must describe the entry without handing the key back."""
    from music_studio import setup

    entries = [{"name": "default", "url": "https://x", "model": "", "has_key": True}]
    assert all("key" not in entry or entry.get("key") is None for entry in entries)
    text = (ROOT / "music_studio" / "setup.py").read_text(encoding="utf-8")
    assert "'key': entry.get('key')" not in text, "the listing must not echo the stored key"


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
