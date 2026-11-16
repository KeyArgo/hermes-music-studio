"""Adding and removing a music engine: one target, its proven workflow, no probes."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import engine_catalog  # noqa: E402
from music_studio.network import TargetStore  # noqa: E402
from music_studio.workflows import (WorkflowStore, find_placeholders,  # noqa: E402
                                    validate_workflow)

URL = 'http://192.0.2.9:8188'


def add(tmp_path, models, name='Studio box', url=URL):
    return engine_catalog.add_engine(tmp_path, name=name, url=url, models=models)


def test_ace_add_writes_a_target_and_the_proven_workflow(tmp_path):
    result = add(tmp_path, ['ace15_turbo'])

    assert result['ok'] is True
    assert result['target']['name'] == 'Studio box'
    assert result['target']['url'] == URL
    assert len(result['workflows']) == 1
    summary = result['workflows'][0]
    assert summary['name'] == 'ACE-Step 1.5'
    assert summary['engine_id'] == 'comfy_workflow:' + summary['id']

    targets = TargetStore(tmp_path).list()
    assert [item['id'] for item in targets] == [result['target']['id']]

    records = WorkflowStore(tmp_path).all()
    assert len(records) == 1
    record = records[0]
    assert record['target_id'] == result['target']['id']
    assert set(find_placeholders(record['workflow'])) == {
        'style', 'lyrics', 'duration', 'seed'}
    validate_workflow(record['workflow'])
    assert record['workflow']['10']['inputs']['filename_prefix'] == 'music-studio'


def test_minimax_needs_no_workflow_because_the_engine_builds_its_graph(tmp_path):
    result = add(tmp_path, ['minimax_m3'])
    assert result['ok'] is True
    assert result['workflows'] == []
    assert len(TargetStore(tmp_path).list()) == 1
    assert WorkflowStore(tmp_path).all() == []


def test_both_models_share_one_target_and_one_workflow(tmp_path):
    result = add(tmp_path, ['minimax_m3', 'ace15_turbo'])
    assert len(TargetStore(tmp_path).list()) == 1
    assert len(WorkflowStore(tmp_path).all()) == 1
    assert len(result['workflows']) == 1


def test_a_repeated_model_is_not_duplicated(tmp_path):
    add(tmp_path, ['ace15_turbo', 'ace15_turbo'])
    assert len(WorkflowStore(tmp_path).all()) == 1


def test_an_unknown_model_is_rejected_before_anything_is_saved(tmp_path):
    with pytest.raises(ValueError, match='unknown music model'):
        add(tmp_path, ['yue2'])
    assert TargetStore(tmp_path).list() == []
    assert WorkflowStore(tmp_path).all() == []


def test_remove_deletes_the_workflows_and_then_the_target(tmp_path):
    result = add(tmp_path, ['ace15_turbo'])
    removed = engine_catalog.remove_engine(tmp_path, result['target']['id'])

    assert removed['ok'] is True
    assert removed['target_id'] == result['target']['id']
    assert removed['workflows'] == [result['workflows'][0]['id']]
    assert TargetStore(tmp_path).list() == []
    assert WorkflowStore(tmp_path).all() == []


def test_removing_a_missing_engine_raises_a_clear_error(tmp_path):
    with pytest.raises(KeyError):
        engine_catalog.remove_engine(tmp_path, str(uuid.uuid4()))


def test_list_engines_reads_stores_and_never_probes(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError('list_engines must not make a network request')

    monkeypatch.setattr(engine_catalog.setup, '_get', refuse)
    add(tmp_path, ['minimax_m3', 'ace15_turbo'])

    listing = engine_catalog.list_engines(tmp_path)
    assert len(listing['engines']) == 1
    engine = listing['engines'][0]
    assert engine['kind'] == 'comfyui'
    assert engine['name'] == 'Studio box'
    assert engine['url'] == URL
    assert [item['name'] for item in engine['workflows']] == ['ACE-Step 1.5']
    assert engine['workflows'][0]['engine_id'].startswith('comfy_workflow:')
    assert listing['ace_remote_url'] == ''
    assert listing['hosted'] == []
