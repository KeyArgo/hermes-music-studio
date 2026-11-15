"""Regression tests for the 2026-11-15 bug hunt (lane: bugfix).

Each test names the behaviour it pins; the ones marked "failed before" in the lane report are written
against the broken tree first, so a green run means the fix stayed.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import types
import uuid
from pathlib import Path

import pytest

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 64


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

    scope.get_secret = get_secret
    scope.invalidate_env_file_cache = lambda: None
    agent = types.ModuleType('agent')
    agent.secret_scope = scope

    monkeypatch.setitem(sys.modules, 'hermes_cli', hermes_cli)
    monkeypatch.setitem(sys.modules, 'hermes_cli.config', config)
    monkeypatch.setitem(sys.modules, 'agent', agent)
    monkeypatch.setitem(sys.modules, 'agent.secret_scope', scope)
    return values


def _graph(**inputs):
    fields = {'style': '{{style}}', 'lyrics': '{{lyrics}}'}
    fields.update(inputs)
    return {
        '1': {'class_type': 'TextEncode', 'inputs': fields},
        '2': {'class_type': 'SaveAudio', 'inputs': {'filename_prefix': 'old'}},
    }


def _request(**kw):
    from music_studio.engines import RenderRequest

    fields = {'caption': 'warm folk', 'lyrics': 'la la la', 'duration_seconds': 42.5,
              'seed': 7, 'title': 'Kettle song'}
    fields.update(kw)
    return RenderRequest(**fields)


# 1 ------------------------------------------------------------------------
def test_a_cached_ace_engine_sees_a_newly_saved_url(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ACE_REMOTE_URL', 'http://old.example')
    from music_studio import service, settings

    engine = service.registry().get('ace_remote')
    assert engine.base_url == 'http://old.example'

    settings.write(tmp_path, {'ace_remote_url': 'http://new.example'})

    # The same cached instance, not only a freshly built one.
    assert engine.base_url == 'http://new.example'
    assert service.registry().get('ace_remote').base_url == 'http://new.example'


# 2 ------------------------------------------------------------------------
def test_ace_remote_url_is_the_single_stored_address(tmp_path, hermes, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('ACE_REMOTE_URL', raising=False)
    from music_studio import service, settings, setup

    setup.save_cloud(tmp_path, 'default', 'https://hosted.example/v1', 'sk-hosted',
                     model='music-3')
    saved = settings.read(tmp_path)
    assert saved['ace_remote_url'] == 'https://hosted.example/v1'
    assert not saved['ace_remote'].get('url')

    # The Compute screen saves a local server the way the server path does.
    service.save_settings({'ace_remote_url': 'http://local.example:8001'})

    listing = setup.list_cloud(tmp_path)
    assert listing == [{'name': 'default', 'url': 'http://local.example:8001',
                        'model': 'music-3', 'has_key': True}]
    assert service.registry().get('ace_remote').base_url == 'http://local.example:8001'


# 3 ------------------------------------------------------------------------
def test_registry_sees_a_workflow_written_by_another_process(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    from music_studio import service
    from music_studio.network import TargetStore
    from music_studio.workflows import WorkflowStore, engine_id

    root = service.store().root
    target = TargetStore(root).create('box', 'http://192.0.2.9:8188')
    assert engine_id  # imported for the assertions below

    first = service.registry()
    assert [e.id for e in first] == ['ace_local', 'ace_remote', 'minimax_m3_comfy']

    # A different process writes the file; this process never calls _invalidate_registry.
    record = WorkflowStore(root).create('Other process graph', target['id'], _graph())
    assert service.registry().get(engine_id(record['id'])).id == engine_id(record['id'])

    # ...and deletes it the same way.
    WorkflowStore(root).delete(record['id'])
    with pytest.raises(KeyError):
        service.registry().get(engine_id(record['id']))


# 4 ------------------------------------------------------------------------
def _write_project(root: Path, takes: list[dict]) -> str:
    project_id = str(uuid.uuid4())
    (root / f'{project_id}.json').write_text(json.dumps({
        'project_id': project_id, 'title': 'Take in flight', 'takes': takes,
        'duration_seconds': 30}), encoding='utf-8')
    return project_id


def test_deleting_a_workflow_in_use_by_a_queued_take_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    from music_studio import service
    from music_studio.network import TargetStore
    from music_studio.workflows import WorkflowStore, engine_id

    root = service.store().root
    target = TargetStore(root).create('box', 'http://192.0.2.9:8188')
    record = WorkflowStore(root).create('In use', target['id'], _graph())
    take = {'take_id': str(uuid.uuid4()), 'state': 'queued',
            'engine_id': engine_id(record['id'])}
    project_id = _write_project(root, [take])

    with pytest.raises(ValueError, match='workflow is in use by a queued take'):
        service.delete_workflow(record['id'])
    assert WorkflowStore(root).get(record['id'])['name'] == 'In use'

    # The worker finished the take: the workflow is no longer in use.
    path = root / f'{project_id}.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['takes'][0]['state'] = 'succeeded'
    path.write_text(json.dumps(data), encoding='utf-8')

    assert service.delete_workflow(record['id'])['workflow_id'] == record['id']
    with pytest.raises(KeyError):
        WorkflowStore(root).get(record['id'])


# 5 ------------------------------------------------------------------------
def test_a_placeholder_inside_a_comfyui_link_is_not_text():
    from music_studio.workflows import find_placeholders, substitute_placeholders, validate_workflow

    graph = {'1': {'class_type': 'TextEncode', 'inputs': {'image': ['{{style}}', 0]}},
             '2': {'class_type': 'SaveAudio', 'inputs': {}}}

    assert find_placeholders(graph) == ()
    with pytest.raises(ValueError, match=r'\{\{style\}\} or \{\{lyrics\}\}'):
        validate_workflow(graph)
    assert substitute_placeholders(graph, _request())['1']['inputs']['image'] == ['{{style}}', 0]


# 6 ------------------------------------------------------------------------
def test_user_text_containing_a_placeholder_is_not_rewritten():
    from music_studio.workflows import _replace_text

    values = {'lyrics': 'sing {{seed}}', 'style': '', 'duration': 42,
              'seed': 7, 'title': '', 'instrumental': False}
    assert _replace_text('Lyrics: {{lyrics}}', values) == 'Lyrics: sing {{seed}}'


# 7 ------------------------------------------------------------------------
def test_workflow_store_writes_through_replace_with_retry(tmp_path, monkeypatch):
    from music_studio import workflows
    from music_studio.network import TargetStore

    calls = []
    real = workflows.replace_with_retry

    def spy(source, target, *args, **kwargs):
        calls.append((str(source), str(target)))
        return real(source, target, *args, **kwargs)

    monkeypatch.setattr(workflows, 'replace_with_retry', spy)
    target = TargetStore(tmp_path).create('box', 'http://192.0.2.9:8188')
    workflows.WorkflowStore(tmp_path).create('Mine', target['id'], _graph())

    assert calls, 'WorkflowStore._write bypassed replace_with_retry'


# 8 ------------------------------------------------------------------------
def test_case_insensitive_containment_is_decided_by_file_identity(tmp_path):
    from music_studio import covers

    studio = tmp_path / 'studio'
    studio.mkdir()
    temp = tmp_path / 'tmp'
    temp.mkdir()
    image = studio / 'cover.png'
    image.write_bytes(PNG)
    swapped = Path(str(image).swapcase())
    if not os.path.exists(str(swapped)):
        pytest.skip('requires a case-insensitive filesystem')

    assert covers.cover_from_file(studio, 'p1', str(swapped), temp=temp)['success'] is True


def test_a_path_outside_every_root_is_still_refused(tmp_path):
    from music_studio import covers

    studio = tmp_path / 'studio'
    studio.mkdir()
    temp = tmp_path / 'tmp'
    temp.mkdir()
    outside = tmp_path / 'private'
    outside.mkdir()
    image = outside / 'cover.png'
    image.write_bytes(PNG)

    with pytest.raises(covers.CoverError, match='studio folder'):
        covers.cover_from_file(studio, 'p1', str(image), temp=temp)


def test_a_symlink_inside_a_root_pointing_outside_is_still_refused(tmp_path):
    from music_studio import covers

    studio = tmp_path / 'studio'
    studio.mkdir()
    temp = tmp_path / 'tmp'
    temp.mkdir()
    outside = tmp_path / 'private'
    outside.mkdir()
    image = outside / 'cover.png'
    image.write_bytes(PNG)
    link = studio / 'link.png'
    os.symlink(image, link)

    with pytest.raises(covers.CoverError, match='link'):
        covers.cover_from_file(studio, 'p1', str(link), temp=temp)


# 9 ------------------------------------------------------------------------
def test_a_save_racing_the_legacy_migration_ends_with_the_saved_key(tmp_path, hermes, monkeypatch):
    from music_studio import hermes_secrets, setup

    original_read = hermes_secrets.read_secret
    started = threading.Event()
    release = threading.Event()
    delayed = {'done': False}

    def slow_read(name):
        value = original_read(name)
        if (not delayed['done'] and threading.current_thread().name == 'lister'
                and not value):
            delayed['done'] = True
            started.set()
            release.wait(10)
        return value

    monkeypatch.setattr(hermes_secrets, 'read_secret', slow_read)
    legacy = tmp_path / 'cloud-keys.json'
    legacy.write_text(json.dumps({'default': {
        'url': 'https://old.example/v1', 'key': 'K1', 'model': ''}}), encoding='utf-8')

    errors = []

    def lister():
        try:
            for _ in range(10):
                setup.list_cloud(tmp_path)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    saver_started = threading.Event()

    def saver():
        saver_started.set()
        setup.save_cloud(tmp_path, 'default', 'https://new.example/v1', 'K2')

    listing_thread = threading.Thread(target=lister, name='lister')
    listing_thread.start()
    assert started.wait(10), 'list_cloud never reached its migration read'

    save_thread = threading.Thread(target=saver, name='saver')
    save_thread.start()
    assert saver_started.wait(10)
    # Give the saver a moment to enter save_cloud before the lister is released.
    time.sleep(0.05)
    release.set()

    listing_thread.join(15)
    save_thread.join(15)
    assert not listing_thread.is_alive() and not save_thread.is_alive()
    assert not errors
    assert original_read('ACE_REMOTE_API_KEY') == 'K2'


# 10 -----------------------------------------------------------------------
def test_duration_placeholder_is_typed_by_its_value():
    from music_studio.workflows import substitute_placeholders

    whole = substitute_placeholders(_graph(duration='{{duration}}'),
                                    _request(duration_seconds=42))
    assert whole['1']['inputs']['duration'] == 42
    assert type(whole['1']['inputs']['duration']) is int

    fractional = substitute_placeholders(_graph(duration='{{duration}}'),
                                         _request(duration_seconds=42.5))
    assert fractional['1']['inputs']['duration'] == 42.5
    assert type(fractional['1']['inputs']['duration']) is float


def test_the_registry_cache_keeps_one_entry_per_data_dir(tmp_path, monkeypatch):
    """A rebuilt registry replaces the stale one instead of piling up beside it."""
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    from music_studio import service

    service._REGISTRIES.clear()
    s = service.store()
    service.registry(s)
    for n in range(3):
        (s.root / 'engine-settings.json').write_text('{"n": %d}' % n + ' ' * n)
        service.registry(s)
    assert len([k for k in service._REGISTRIES if k[0] == str(s.root)]) == 1
