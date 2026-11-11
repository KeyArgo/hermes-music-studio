"""About panel: companion plugin facts, links, and a paste-ready bug report."""
import uuid
from pathlib import Path

import pytest

from music_studio import about, service


@pytest.fixture()
def library(tmp_path, monkeypatch):
    root = tmp_path / 'lib'
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(root))
    return root


def test_about_reports_version_links_and_revamp(library):
    data = about.about()
    assert data['success'] is True
    assert data['version'] and data['version'] != 'unknown'
    assert data['repo'].startswith('https://github.com/')
    assert data['visibility'] in ('private', 'public')
    assert data['paths']['tests'].startswith('python3.13 -m pytest')
    assert data['paths']['issues'].endswith('/issues/new')
    revamp = data['revamp']
    assert revamp['install'] == 'hermes plugins install revamp'
    assert revamp['repo'].endswith('/hermes-revamp')
    assert revamp['state'] in ('running', 'installed', 'mcp-only', 'absent')


def test_diagnostics_contains_the_machines_own_facts(library):
    data = about.diagnostics()
    text = data['text']
    assert 'Music Studio' in text
    assert 'Platform:' in text and 'Python:' in text
    assert str(library) in text
    assert 'Most recent failed take:' in text


def test_diagnostics_names_the_newest_failure(library):
    store = service.store()
    project = store.create(title='Broken', caption='x', lyrics='y', duration_seconds=12)
    project['takes'] = [
        {'take_id': str(uuid.uuid4()), 'state': 'failed', 'error': 'ComfyUI refused the workflow',
         'finished_at': 100},
        {'take_id': str(uuid.uuid4()), 'state': 'failed', 'error': 'newer failure',
         'finished_at': 200},
    ]
    store._save(project)
    text = about.diagnostics()['text']
    assert 'newer failure' in text
    assert 'ComfyUI refused the workflow' not in text
