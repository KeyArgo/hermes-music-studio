"""Tests for adding any reachable ComfyUI server without recognised music models."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import engine_catalog  # noqa: E402
from music_studio.network import TargetStore  # noqa: E402
from music_studio.workflows import WorkflowStore  # noqa: E402

URL = 'http://192.0.2.9:8188'


def add(tmp_path, models, name='Studio box', url=URL, **kwargs):
    return engine_catalog.add_engine(tmp_path, name=name, url=url, models=models, **kwargs)


def test_add_engine_with_empty_models_saves_target_only_and_marks_connect_only(tmp_path):
    result = add(tmp_path, [])

    assert result['ok'] is True
    assert result['connect_only'] is True
    assert result['workflows'] == []
    assert result['target']['name'] == 'Studio box'
    assert result['target']['url'] == URL

    targets = TargetStore(tmp_path).list()
    assert len(targets) == 1
    assert targets[0]['id'] == result['target']['id']
    assert targets[0]['name'] == 'Studio box'
    assert targets[0]['url'] == URL

    # No workflow is created for a connect-only engine.
    assert WorkflowStore(tmp_path).all() == []

    # Models record should remember an empty list for this target.
    recorded = engine_catalog._models_record(tmp_path)
    assert recorded.get(result['target']['id']) == []

    # list_engines lists this engine with an empty workflow list.
    listing = engine_catalog.list_engines(tmp_path)
    assert len(listing['engines']) == 1
    engine = listing['engines'][0]
    assert engine['kind'] == 'comfyui'
    assert engine['name'] == 'Studio box'
    assert engine['url'] == URL
    assert engine['workflows'] == []

    # export_engines records models as an empty list.
    exported = engine_catalog.export_engines(tmp_path)
    payload = json.loads(exported['text'])
    assert payload['engines'] == [{'name': 'Studio box', 'url': URL, 'models': []}]


def test_duplicate_connect_only_engine_is_refused_on_import(tmp_path):
    add(tmp_path, [], name='Studio box', url=URL)
    exported_text = engine_catalog.export_engines(tmp_path)['text']

    # Re-importing the same server skips it because the address is already saved.
    result = engine_catalog.import_engines(tmp_path, exported_text)
    assert result == {'added': [], 'skipped': ['Studio box'], 'errors': []}
    assert len(TargetStore(tmp_path).list()) == 1
    assert WorkflowStore(tmp_path).all() == []


def test_invalid_and_unreachable_urls_are_refused_for_connect_only_engine(tmp_path):
    invalid_urls = [
        'http://169.254.169.254:8188',       # link-local
        'ftp://192.0.2.9:8188',              # bad scheme
        'http://user:pass@192.0.2.9:8188',   # credentials
        'http://192.0.2.9:8188/?q=1',        # query
        'http://192.0.2.9:8188/#frag',       # fragment
        'not a valid url',                   # malformed
    ]
    for bad_url in invalid_urls:
        with pytest.raises(ValueError):
            add(tmp_path, [], url=bad_url)

    assert TargetStore(tmp_path).list() == []
    assert WorkflowStore(tmp_path).all() == []


def test_failed_add_cleans_up_target_and_recorded_models(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise OSError('Disk write failure')

    monkeypatch.setattr(engine_catalog, '_remember_models', boom)
    with pytest.raises(OSError, match='Disk write failure'):
        add(tmp_path, [])

    assert TargetStore(tmp_path).list() == []
    assert WorkflowStore(tmp_path).all() == []
    assert engine_catalog._models_record(tmp_path) == {}


def test_non_comfyui_kind_with_empty_models_is_refused(tmp_path):
    with pytest.raises(ValueError, match='Use the ACE-Step or hosted form.'):
        add(tmp_path, [], kind='ace_server')

    assert TargetStore(tmp_path).list() == []
    assert WorkflowStore(tmp_path).all() == []


def test_remove_connect_only_engine_succeeds_and_cleans_records(tmp_path):
    result = add(tmp_path, [])
    target_id = result['target']['id']

    removed = engine_catalog.remove_engine(tmp_path, target_id)
    assert removed['ok'] is True
    assert removed['target_id'] == target_id
    assert removed['workflows'] == []

    assert TargetStore(tmp_path).list() == []
    assert WorkflowStore(tmp_path).all() == []
    assert engine_catalog._models_record(tmp_path) == {}


def test_a_plain_comfyui_survives_copy_and_paste_as_a_plain_comfyui(tmp_path):
    """Copied with ``models: []`` it must not come back on the other machine as a MiniMax server."""
    one, two = tmp_path / 'one', tmp_path / 'two'
    one.mkdir(); two.mkdir()
    engine_catalog.add_engine(one, name='Plain box', url='http://192.168.1.50:8188', models=[])
    text = engine_catalog.export_engines(one)['text']
    engine_catalog.import_engines(two, text)
    [entry] = json.loads(engine_catalog.export_engines(two)['text'])['engines']
    assert entry['models'] == []


def test_an_old_export_without_a_models_list_still_means_minimax(tmp_path):
    text = json.dumps({'music_studio_engines': 1,
                       'engines': [{'name': 'Old', 'url': 'http://192.168.1.50:8188'}]})
    engine_catalog.import_engines(tmp_path, text)
    [entry] = json.loads(engine_catalog.export_engines(tmp_path)['text'])['engines']
    assert entry['models'] == ['minimax_m3']
