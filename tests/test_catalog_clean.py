"""Catalog review: no private Hermes surfaces, one Hermes home, no model catalogue."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from music_studio import covers

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64

FORBIDDEN = ('auth.json', 'credential_pool', 'config.yaml', "'.hermes'",
             'agent.home', 'agent.paths')


def _patch_home(monkeypatch, home: Path) -> None:
    module = types.ModuleType('hermes_constants')
    module.get_hermes_home = lambda: home
    monkeypatch.setitem(sys.modules, 'hermes_constants', module)


def test_plugin_code_imports_no_private_hermes_surfaces():
    """Only public API: no auth file, no credential pool, no config file, no guessed home."""
    offenders = []
    for path in ROOT.rglob('*.py'):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in ('tests', 'docs') or '__pycache__' in relative.parts:
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for token in FORBIDDEN:
            if token in text:
                offenders.append(f'{relative}: {token}')
    assert not offenders, offenders


def test_covers_refuses_downloads_and_accepts_the_profile_image_cache(tmp_path, monkeypatch):
    profile = tmp_path / 'profile'
    cache = profile / 'cache' / 'images'
    cache.mkdir(parents=True)
    _patch_home(monkeypatch, profile)

    studio = tmp_path / 'studio'
    studio.mkdir()
    temp = tmp_path / 'temp'
    downloads = tmp_path / 'Downloads'
    downloads.mkdir()

    refused = downloads / 'cover.png'
    refused.write_bytes(PNG)
    with pytest.raises(covers.CoverError):
        covers.cover_from_file(studio, 'p1', str(refused), temp=temp)

    accepted = cache / 'cover.png'
    accepted.write_bytes(PNG)
    result = covers.cover_from_file(studio, 'p1', str(accepted), temp=temp)
    assert result['success'] is True and result['mime'] == 'image/png'


def test_revamp_uses_only_the_patched_hermes_home(tmp_path, monkeypatch):
    from standalone import revamp

    home = tmp_path / 'hermes-home'
    _patch_home(monkeypatch, home)
    assert revamp._hermes_homes() == [home]


def test_studio_models_returns_the_default_only_without_the_provider_registry():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from standalone.ideas import router

    app = FastAPI()
    app.include_router(router)
    payload = TestClient(app).get('/studio/models').json()
    assert payload == {'default': {'label': 'Your Hermes model'}, 'providers': []}


def test_ideas_complete_forwards_the_chosen_provider_and_model(monkeypatch):
    from standalone import ideas

    seen = []

    def fake_complete(messages, **kwargs):
        seen.append(kwargs)
        return '{"title": "A song"}'

    monkeypatch.setattr(ideas.llm_bridge, 'complete', fake_complete)
    body = ideas.build_request('provider', 'model',
                               [{'role': 'user', 'content': 'a song'}], 'draft', '', 50)
    assert ideas._complete(body) == '{"title": "A song"}'
    assert seen and seen[0].get('provider') == 'provider' and seen[0].get('model') == 'model'


def test_the_host_is_told_the_active_home(monkeypatch, tmp_path):
    """The host may not import hermes_constants; the launcher hands it the public helper's answer."""
    import sys, types
    from music_studio import hermes_home
    profile = tmp_path / 'profiles' / 'work'
    fake = types.ModuleType('hermes_constants')
    fake.get_hermes_home = lambda: profile
    monkeypatch.setitem(sys.modules, 'hermes_constants', fake)
    assert hermes_home.active_home() == profile
    monkeypatch.setitem(sys.modules, 'hermes_constants', None)  # import now fails, like the host
    monkeypatch.setenv('HERMES_HOME', str(profile))
    assert hermes_home.active_home() == profile
    monkeypatch.delenv('HERMES_HOME')
    assert hermes_home.active_home() is None


def test_launcher_passes_hermes_home_to_the_host():
    source = (Path(__file__).resolve().parents[1] / 'music_studio' / 'host_launcher.py').read_text()
    assert "env['HERMES_HOME'] = str(home)" in source
