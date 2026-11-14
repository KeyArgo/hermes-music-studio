"""The creative partner's model catalogue: live, grouped, and secret-free."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_studio import model_catalog


def write_home(tmp_path, *, pool, env=None, config=None):
    home = tmp_path / 'hermes'
    home.mkdir()
    (home / 'auth.json').write_text(json.dumps({'credential_pool': pool, 'providers': {}}),
                                    encoding='utf-8')
    if env:
        (home / '.env').write_text(env, encoding='utf-8')
    if config:
        (home / 'config.yaml').write_text(config, encoding='utf-8')
    return home


@pytest.fixture
def no_cache():
    model_catalog.invalidate()
    yield
    model_catalog.invalidate()


def test_each_provider_lists_its_own_models(monkeypatch, tmp_path, no_cache):
    home = write_home(tmp_path, pool={
        'alpha': [{'base_url': 'http://alpha.test/v1', 'api_key': 'secret-a'}],
        'beta': [{'base_url': 'http://beta.test/v1', 'api_key': 'secret-b'}],
    })
    monkeypatch.setenv('HERMES_HOME', str(home))

    def fake_fetch(base, credential):
        return (['z-model', 'a-model'] if 'alpha' in base else ['b-model']), None

    monkeypatch.setattr(model_catalog, '_fetch', fake_fetch)
    state = model_catalog.discover(force=True)

    assert state['count'] == 3
    slugs = [row['slug'] for row in state['providers']]
    assert slugs == ['alpha', 'beta']
    assert [m['id'] for m in state['providers'][0]['models']] == ['a-model', 'z-model']
    assert state['providers'][0]['source'] == 'live'


def test_a_provider_without_a_credential_is_a_row_not_an_error(monkeypatch, tmp_path, no_cache):
    home = write_home(tmp_path, pool={'gamma': [{'base_url': 'http://gamma.test/v1'}]})
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(model_catalog, '_fetch', lambda base, cred: (['m'], None))
    state = model_catalog.discover(force=True)
    row = state['providers'][0]
    assert row['slug'] == 'gamma'
    assert row['count'] == 0
    assert 'credential' in row['detail']


def test_one_broken_provider_does_not_hide_the_others(monkeypatch, tmp_path, no_cache):
    home = write_home(tmp_path, pool={
        'good': [{'base_url': 'http://good.test/v1', 'api_key': 'k'}],
        'bad': [{'base_url': 'http://bad.test/v1', 'api_key': 'k'}],
    })
    monkeypatch.setenv('HERMES_HOME', str(home))

    def fake_fetch(base, credential):
        if 'bad' in base:
            return [], 'the provider answered HTTP 403'
        return ['good-model'], None

    monkeypatch.setattr(model_catalog, '_fetch', fake_fetch)
    state = model_catalog.discover(force=True)
    rows = {row['slug']: row for row in state['providers']}
    assert rows['good']['count'] == 1
    assert rows['bad']['count'] == 0
    assert '403' in rows['bad']['detail']


def test_no_credential_reaches_the_payload(monkeypatch, tmp_path, no_cache):
    home = write_home(tmp_path, pool={
        'alpha': [{'base_url': 'http://alpha.test/v1', 'api_key': 'SUPER-SECRET-TOKEN'}],
    }, env='ALPHA_API_KEY=SUPER-SECRET-TOKEN\n')
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(model_catalog, '_fetch', lambda base, cred: (['m'], None))
    blob = json.dumps(model_catalog.discover(force=True))
    assert 'SUPER-SECRET-TOKEN' not in blob
    assert 'api_key' not in blob


def test_the_cache_answers_without_asking_again(monkeypatch, tmp_path, no_cache):
    home = write_home(tmp_path, pool={'alpha': [{'base_url': 'http://a.test', 'api_key': 'k'}]})
    monkeypatch.setenv('HERMES_HOME', str(home))
    calls = []

    def counting_fetch(base, credential):
        calls.append(base)
        return ['m'], None

    monkeypatch.setattr(model_catalog, '_fetch', counting_fetch)
    model_catalog.discover(force=True)
    model_catalog.discover()
    assert len(calls) == 1
    model_catalog.discover(force=True)
    assert len(calls) == 2
