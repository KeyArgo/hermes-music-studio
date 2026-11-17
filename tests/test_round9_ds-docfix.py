"""Round 9 (lane ds-docfix): an omitted seed is a random seed, and an unknown download is a 404.

Both bugs come from the docs-vs-code report: REFERENCE.md says an omitted `seed` means a random
seed, but `Jobs.enqueue` refuses `None`; and the install routes answered 400 for a job id that is
not there, where the page's error handler maps `KeyError` to a 404.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from dashboard.plugin_api import router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from music_studio import local_install, service  # noqa: E402
from music_studio.engines import (Capabilities, Engine, EngineStatus,  # noqa: E402
                                  Limits, Registry)


class StubEngine(Engine):
    kind = 'local'
    requirements = ()

    def __init__(self):
        super().__init__()
        self.id = 'ace_local'
        self.label = 'ACE-Step 1.5 (local GPU)'

    @property
    def limits(self):
        return Limits(10, 180, 30)

    @property
    def capabilities(self):
        return Capabilities(True, True, True, True, True)

    def health(self):
        return EngineStatus(engine_id=self.id, state='ready')

    def _render(self, request, on_progress=None, on_process=None):
        raise NotImplementedError('the stub never renders')


class FakeProc:
    pid = os.getpid()


@pytest.fixture
def approved_project(monkeypatch):
    """A factory for approved ten-second drafts, with the registry and worker spawn stubbed."""
    registry = Registry([StubEngine()])
    monkeypatch.setattr(service, 'registry', lambda s=None: registry)
    monkeypatch.setattr(service, '_spawn_worker', lambda *a, **k: FakeProc())

    def make(title='Night Drive'):
        s = service.store()
        project = s.create(title=title, caption='dreamy synthwave',
                           lyrics='[verse]\nstreetlights', duration_seconds=10)
        s.approve(project['project_id'],
                  base_updated_at=s.get(project['project_id'])['updated_at'])
        return project['project_id']

    return make


def test_omitted_seed_takes_a_random_seed(approved_project, monkeypatch):
    import secrets

    monkeypatch.setattr(secrets, 'randbelow', lambda n: 424242)
    result = service.generate(approved_project(), None)
    take = result['take']
    assert take['seed'] == 424242
    assert take['requested_seed'] == 424242
    assert take['seed_locked'] is False


def test_omitted_seed_stays_inside_the_seed_range(approved_project):
    take = service.generate(approved_project(), None)['take']
    assert type(take['seed']) is int
    assert 0 <= take['seed'] <= 2**32 - 1


@pytest.mark.parametrize('seed', [-1, 2**32, 'abc', 1.5, True])
def test_a_seed_that_is_given_but_invalid_is_still_refused(approved_project, seed):
    with pytest.raises(ValueError, match='invalid seed'):
        service.generate(approved_project(), seed)


def test_status_of_an_unknown_download_raises_keyerror():
    with pytest.raises(KeyError) as caught:
        local_install.status('no-such-job')
    assert 'Download not found.' in str(caught.value)


def test_cancel_of_an_unknown_download_raises_keyerror():
    with pytest.raises(KeyError) as caught:
        local_install.cancel('no-such-job')
    assert 'Download not found.' in str(caught.value)


def test_unknown_download_routes_answer_404():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get('/studio/install/jobs/no-such-job').status_code == 404
    assert client.post('/studio/install/jobs/no-such-job/cancel').status_code == 404
