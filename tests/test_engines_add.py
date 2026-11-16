"""Adding and removing a music engine: one target, its proven workflow, no probes."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import engine_catalog  # noqa: E402
from music_studio.network import TargetStore  # noqa: E402
from music_studio.store import Store  # noqa: E402
from music_studio.workflows import (WorkflowStore, engine_id, find_placeholders,  # noqa: E402
                                    validate_workflow)

URL = 'http://192.0.2.9:8188'
ACE_URL = 'http://192.0.2.20:8001'


def add(tmp_path, models, name='Studio box', url=URL, **kwargs):
    return engine_catalog.add_engine(tmp_path, name=name, url=url, models=models, **kwargs)


def queue_take(data_dir, *, engine, target_id=None, state='queued'):
    """Write a project whose take is in flight, the way a queued render leaves it.

    The take carries what ``Jobs._new_take`` pins: the resolved engine id and,
    in the snapshot, the target the take was preflighted against.
    """
    store = Store(data_dir)
    project = store.create(title='Busy song', caption='warm strings',
                           lyrics='[verse]\nla la', duration_seconds=30)
    path = store.root / (project['project_id'] + '.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    data['takes'].append({'take_id': str(uuid.uuid4()), 'state': state,
                          'engine_id': engine,
                          'snapshot': {'comfy_target_id': target_id}})
    path.write_text(json.dumps(data), encoding='utf-8')
    return project


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


# -- a take still being made owns its engine -------------------------------

def test_remove_refuses_while_a_take_uses_one_of_its_workflows(tmp_path):
    """The worker resolves ``comfy_workflow:<id>`` when it picks the take up.

    Deleting the record first turns a queued render into "unknown workflow".
    """
    result = add(tmp_path, ['ace15_turbo'])
    workflow_id = result['workflows'][0]['id']
    queue_take(tmp_path, engine=engine_id(workflow_id))

    with pytest.raises(ValueError,
                       match='This engine is used by a song that is still being made.'):
        engine_catalog.remove_engine(tmp_path, result['target']['id'])

    assert [t['id'] for t in TargetStore(tmp_path).list()] == [result['target']['id']]
    assert [w['id'] for w in WorkflowStore(tmp_path).all()] == [workflow_id]


def test_remove_refuses_while_a_take_is_pinned_to_the_target(tmp_path):
    """A MiniMax take pins the endpoint it was preflighted against."""
    result = add(tmp_path, ['minimax_m3'])
    queue_take(tmp_path, engine='minimax_m3_comfy', target_id=result['target']['id'])

    with pytest.raises(ValueError,
                       match='This engine is used by a song that is still being made.'):
        engine_catalog.remove_engine(tmp_path, result['target']['id'])
    assert len(TargetStore(tmp_path).list()) == 1


def test_remove_ignores_a_take_that_finished(tmp_path):
    result = add(tmp_path, ['ace15_turbo'])
    queue_take(tmp_path, engine=engine_id(result['workflows'][0]['id']), state='succeeded')
    removed = engine_catalog.remove_engine(tmp_path, result['target']['id'])
    assert removed['ok'] is True
    assert TargetStore(tmp_path).list() == []


# -- a half-saved engine is rolled back ------------------------------------

def test_a_failed_workflow_leaves_no_target_behind(monkeypatch, tmp_path):
    """A disk that fills while the workflow is written must not keep the engine."""
    def failing(self, name, target_id, workflow):
        raise OSError('No space left on device')

    monkeypatch.setattr(WorkflowStore, 'create', failing)
    with pytest.raises(OSError):
        add(tmp_path, ['ace15_turbo'])
    assert TargetStore(tmp_path).list() == []
    assert WorkflowStore(tmp_path).all() == []


# -- only a ComfyUI server, with real model ids ---------------------------

def test_a_non_comfyui_kind_is_refused_with_a_pointer_to_the_right_form(tmp_path):
    with pytest.raises(ValueError, match='Use the ACE-Step or hosted form.'):
        add(tmp_path, ['minimax_m3'], kind='ace_server')
    assert TargetStore(tmp_path).list() == []


def test_an_empty_model_list_is_refused(tmp_path):
    with pytest.raises(ValueError, match='models must be a non-empty list'):
        add(tmp_path, [])
    assert TargetStore(tmp_path).list() == []


def test_a_non_string_model_id_is_a_value_error_not_a_type_error(tmp_path):
    """``{'models': [{}]}`` used to die in set membership with a TypeError."""
    with pytest.raises(ValueError, match='models must be a non-empty list'):
        add(tmp_path, [{}])
    assert TargetStore(tmp_path).list() == []


def test_a_model_list_that_is_not_a_list_is_refused(tmp_path):
    with pytest.raises(ValueError, match='models must be a non-empty list'):
        add(tmp_path, 'ace15_turbo')


# -- the exact loader option, carried into the workflow --------------------

def test_add_writes_the_nested_loader_options_into_the_ace_workflow(tmp_path):
    add(tmp_path, ['ace15_turbo'], resolved={
        'acestep_v1.5_turbo.safetensors': 'diffusion_models/acestep_v1.5_turbo.safetensors',
        'qwen_0.6b_ace15.safetensors': 'sub/clip/qwen_0.6b_ace15.safetensors',
    })
    record = WorkflowStore(tmp_path).all()[0]
    graph = record['workflow']
    assert graph['1']['inputs']['unet_name'] == 'diffusion_models/acestep_v1.5_turbo.safetensors'
    assert graph['2']['inputs']['clip_name1'] == 'sub/clip/qwen_0.6b_ace15.safetensors'
    # A file the server listed bare keeps its bare name.
    assert graph['2']['inputs']['clip_name2'] == 'qwen_4b_ace15.safetensors'
    assert graph['3']['inputs']['vae_name'] == 'ace_1.5_vae.safetensors'
    validate_workflow(graph)


# -- carrying the engines to another computer ------------------------------

def export_text(data_dir):
    return engine_catalog.export_engines(data_dir)['text']


def test_export_carries_names_urls_and_models_only(tmp_path):
    add(tmp_path, ['minimax_m3', 'ace15_turbo'], name='Spark E')
    from music_studio import settings

    settings.write(tmp_path, {'ace_remote_url': ACE_URL})

    text = export_text(tmp_path)
    payload = json.loads(text)
    assert payload['music_studio_engines'] == 1
    assert payload['engines'] == [{'name': 'Spark E', 'url': URL, 'models': ['ace15_turbo',
                                                                             'minimax_m3']}]
    assert payload['ace_server'] == ACE_URL
    lowered = text.lower()
    for secret_word in ('key', 'token', 'secret', 'password'):
        assert secret_word not in lowered
    assert str(tmp_path) not in text, 'a local path must never travel'


def test_a_round_trip_rebuilds_the_engines_and_the_ace_workflow(tmp_path):
    old, new = tmp_path / 'old', tmp_path / 'new'
    add(old, ['minimax_m3', 'ace15_turbo'], name='Spark E')
    from music_studio import settings

    settings.write(old, {'ace_remote_url': ACE_URL})

    result = engine_catalog.import_engines(new, export_text(old))
    assert result == {'added': ['Spark E'], 'skipped': [], 'errors': []}

    targets = TargetStore(new).list()
    assert [(t['name'], t['url']) for t in targets] == [('Spark E', URL)]
    records = WorkflowStore(new).all()
    assert [r['name'] for r in records] == ['ACE-Step 1.5']
    assert records[0]['target_id'] == targets[0]['id']
    assert set(find_placeholders(records[0]['workflow'])) == {
        'style', 'lyrics', 'duration', 'seed'}
    assert engine_catalog.list_engines(new)['ace_remote_url'] == ACE_URL


def test_a_second_import_skips_the_engine_it_already_has(tmp_path):
    add(tmp_path, ['minimax_m3'], name='Spark E')
    text = export_text(tmp_path)
    result = engine_catalog.import_engines(tmp_path, text)
    assert result == {'added': [], 'skipped': ['Spark E'], 'errors': []}
    assert len(TargetStore(tmp_path).list()) == 1


def test_an_import_does_not_overwrite_a_saved_ace_server(tmp_path):
    from music_studio import settings

    settings.write(tmp_path, {'ace_remote_url': ACE_URL})
    text = json.dumps({'music_studio_engines': 1, 'engines': [], 'ace_server': 'http://192.0.2.77:8001'})
    engine_catalog.import_engines(tmp_path, text)
    assert engine_catalog.list_engines(tmp_path)['ace_remote_url'] == ACE_URL


def test_garbage_text_is_refused_with_one_plain_sentence(tmp_path):
    for text in ('not json at all', '[]', '{"engines": "nope"}', ''):
        with pytest.raises(ValueError, match='not a list of music engines'):
            engine_catalog.import_engines(tmp_path, text)
    assert TargetStore(tmp_path).list() == []


def test_an_imported_link_local_address_is_refused(tmp_path):
    text = json.dumps({'music_studio_engines': 1, 'ace_server': None,
                       'engines': [{'name': 'Self', 'url': 'http://169.254.169.254:8188',
                                    'models': ['minimax_m3']}]})
    with pytest.raises(ValueError, match='link-local'):
        engine_catalog.import_engines(tmp_path, text)
    assert TargetStore(tmp_path).list() == []


def test_an_import_is_bounded(tmp_path):
    engines = [{'name': f'box {index}', 'url': f'http://192.0.2.{index + 1}:8188',
                'models': ['minimax_m3']} for index in range(51)]
    with pytest.raises(ValueError, match='more than 50 engines'):
        engine_catalog.import_engines(tmp_path, json.dumps({'engines': engines}))
    assert TargetStore(tmp_path).list() == []


def test_an_unknown_model_id_in_the_text_falls_back_to_minimax(tmp_path):
    text = json.dumps({'music_studio_engines': 1, 'ace_server': None,
                       'engines': [{'name': 'Box', 'url': URL, 'models': ['yue2']}]})
    result = engine_catalog.import_engines(tmp_path, text)
    assert result['added'] == ['Box']
    assert result['errors'] == []
    assert WorkflowStore(tmp_path).all() == []
    assert len(TargetStore(tmp_path).list()) == 1


def test_resolved_names_must_be_the_same_file(tmp_path):
    """A page-sent loader name may add a subfolder, never swap in a different file."""
    graph = engine_catalog._ace_template({
        'qwen_0.6b_ace15.safetensors': 'sub/qwen_0.6b_ace15.safetensors',
        'qwen_4b_ace15.safetensors': 'sub/something_else.safetensors',
        'ace_1.5_vae.safetensors': 'vae\\ace_1.5_vae.safetensors',
    })
    values = [v for node in graph.values() for v in (node.get('inputs') or {}).values()
              if isinstance(v, str)]
    assert 'sub/qwen_0.6b_ace15.safetensors' in values
    assert 'vae\\ace_1.5_vae.safetensors' in values
    assert 'qwen_4b_ace15.safetensors' in values
    assert 'sub/something_else.safetensors' not in values
