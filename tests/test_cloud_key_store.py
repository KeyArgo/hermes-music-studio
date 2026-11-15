"""The hosted API key belongs to Hermes, not to a file under the studio data directory."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


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


def _all_bytes(root: Path) -> bytes:
    chunks = []
    for path in root.rglob('*'):
        if path.is_file():
            chunks.append(path.read_bytes())
    return b'\n'.join(chunks)


def test_save_writes_the_key_into_no_file_under_the_data_dir(tmp_path, hermes):
    from music_studio import setup

    setup.save_cloud(tmp_path, 'default', 'https://api.example.com/v1', 'sk-hidden-123')

    assert hermes['ACE_REMOTE_API_KEY'] == 'sk-hidden-123'
    assert not (tmp_path / 'cloud-keys.json').exists()
    assert b'sk-hidden-123' not in _all_bytes(tmp_path)


def test_the_saved_key_is_readable_through_hermes(tmp_path, hermes):
    from music_studio import setup
    from music_studio.hermes_secrets import read_secret

    setup.save_cloud(tmp_path, 'default', 'https://api.example.com/v1', 'sk-read-back')

    assert read_secret('ACE_REMOTE_API_KEY') == 'sk-read-back'


def test_list_cloud_never_returns_the_key(tmp_path, hermes):
    from music_studio import setup

    setup.save_cloud(tmp_path, 'default', 'https://api.example.com/v1', 'sk-never-listed',
                     model='music-3')

    listing = setup.list_cloud(tmp_path)
    text = json.dumps(listing)
    assert 'sk-never-listed' not in text
    assert listing == [{'name': 'default', 'url': 'https://api.example.com/v1',
                        'model': 'music-3', 'has_key': True}]


def test_renders_use_the_saved_url(tmp_path, hermes):
    from music_studio import setup, settings

    setup.save_cloud(tmp_path, 'default', 'https://api.example.com/v1/', 'sk-url')

    saved = settings.read(tmp_path)
    assert saved['ace_remote_url'] == 'https://api.example.com/v1'
    assert saved['ace_remote']['url'] == 'https://api.example.com/v1'
    assert saved['ace_remote']['key_env'] == 'ACE_REMOTE_API_KEY'
    assert 'sk-url' not in json.dumps(saved)


def test_migration_imports_the_legacy_key_then_deletes_the_file(tmp_path, hermes):
    from music_studio import setup, settings
    from music_studio.hermes_secrets import read_secret

    legacy = tmp_path / 'cloud-keys.json'
    legacy.write_text(json.dumps({'default': {'url': 'https://api.example.com/v1',
                                             'key': 'sk-legacy', 'model': 'music-3'}}))

    listing = setup.list_cloud(tmp_path)

    assert read_secret('ACE_REMOTE_API_KEY') == 'sk-legacy'
    assert not legacy.exists()
    assert listing == [{'name': 'default', 'url': 'https://api.example.com/v1',
                        'model': 'music-3', 'has_key': True}]
    assert settings.read(tmp_path)['ace_remote_url'] == 'https://api.example.com/v1'
    assert b'sk-legacy' not in _all_bytes(tmp_path)


def test_migration_failure_keeps_the_legacy_file_and_reports(tmp_path, hermes, monkeypatch):
    from music_studio import setup

    legacy = tmp_path / 'cloud-keys.json'
    original = {'default': {'url': 'https://api.example.com/v1', 'key': 'sk-legacy', 'model': ''}}
    legacy.write_text(json.dumps(original))
    config = sys.modules['hermes_cli.config']

    def refuse(name, value):
        raise PermissionError('the credential store is read-only')

    monkeypatch.setattr(config, 'save_env_value', refuse)

    message = setup.migrate_legacy_keys(tmp_path)

    assert message == 're-enter your hosted key in Setup'
    assert legacy.exists()
    assert json.loads(legacy.read_text()) == original
    assert 'sk-legacy' not in ''.join(p.read_text(errors='ignore')
                                      for p in tmp_path.rglob('*') if p.is_file()
                                      and p != legacy)


def test_migration_keeps_a_different_key_already_in_hermes(tmp_path, hermes):
    from music_studio import setup
    from music_studio.hermes_secrets import read_secret

    hermes['ACE_REMOTE_API_KEY'] = 'sk-already-there'
    legacy = tmp_path / 'cloud-keys.json'
    legacy.write_text(json.dumps({'default': {'url': 'https://api.example.com/v2',
                                             'key': 'sk-legacy', 'model': ''}}))

    # Two different keys: the owner chooses. Neither is discarded until they do.
    assert setup.migrate_legacy_keys(tmp_path) == setup.LEGACY_MESSAGE
    assert read_secret('ACE_REMOTE_API_KEY') == 'sk-already-there'
    assert legacy.exists()

    # Re-entering the key in Setup is that choice; then the plaintext copy goes.
    result = setup.save_cloud(tmp_path, 'default', 'https://api.example.com/v2', 'sk-chosen')
    assert read_secret('ACE_REMOTE_API_KEY') == 'sk-chosen'
    assert not legacy.exists()
    assert 'message' not in result
    assert b'sk-chosen' not in _all_bytes(tmp_path) and b'sk-legacy' not in _all_bytes(tmp_path)


def test_save_without_hermes_importable_raises_and_writes_nothing(tmp_path, monkeypatch):
    from music_studio import setup

    monkeypatch.setitem(sys.modules, 'hermes_cli', None)
    monkeypatch.setitem(sys.modules, 'hermes_cli.config', None)
    monkeypatch.setitem(sys.modules, 'agent', None)
    monkeypatch.setitem(sys.modules, 'agent.secret_scope', None)

    with pytest.raises(ValueError, match='could not be saved to Hermes'):
        setup.save_cloud(tmp_path, 'default', 'https://api.example.com/v1', 'sk-no-hermes')

    assert [p for p in tmp_path.rglob('*') if p.is_file()] == []


def test_a_failed_credential_write_raises_and_writes_nothing(tmp_path, hermes, monkeypatch):
    from music_studio import setup

    config = sys.modules['hermes_cli.config']

    def refuse(name, value):
        raise RuntimeError('no')

    monkeypatch.setattr(config, 'save_env_value', refuse)

    with pytest.raises(ValueError, match='could not be saved to Hermes'):
        setup.save_cloud(tmp_path, 'default', 'https://api.example.com/v1', 'sk-refused')

    assert [p for p in tmp_path.rglob('*') if p.is_file()] == []
