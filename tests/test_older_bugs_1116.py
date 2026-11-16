import json
import uuid

import pytest

from music_studio import service
from music_studio.jobs import Jobs
from music_studio.store import Store


def test_reconcile_reaps_outside_store_lock_and_preserves_live_worker(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project = store.create(title='Song')
    project['takes'] = [
        {'state': 'running', 'pid': 101, 'ace_pid': 201},
        {'state': 'running', 'pid': 102, 'ace_pid': 202},
    ]
    store._save(project)
    monkeypatch.setattr('music_studio.jobs.process_alive', lambda pid, start: pid == 102)
    reaped = []

    def reap(take):
        from music_studio.portalock import lock_ex, unlock
        with (tmp_path / '.lock').open('a') as lock:
            lock_ex(lock, blocking=False)
            unlock(lock)
        reaped.append(take['ace_pid'])

    monkeypatch.setattr(Jobs, '_reap_render', lambda self, take: reap(take))
    assert Jobs(store).reconcile_dead_workers(grace_seconds=0) == 1
    assert reaped == [201]
    assert [t['state'] for t in store.get(project['project_id'])['takes']] == ['failed', 'running']
    lease = store.worker_lock()
    assert lease is not None
    lease.close()


def test_delete_locked_audio_records_orphan_and_retries(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project = store.create(title='Song')
    names = [f'{uuid.uuid4()}.wav' for _ in range(2)]
    for name in names:
        (tmp_path / name).write_bytes(b'audio')
    project['takes'] = [{'state': 'succeeded', 'audio_file': n} for n in names]
    store._save(project)
    remove = store._remove_audio_file

    def locked(name):
        if name == names[1]:
            raise PermissionError('playing')
        remove(name)

    monkeypatch.setattr(store, '_remove_audio_file', locked)
    store.delete(project['project_id'])
    assert not (tmp_path / (project['project_id'] + '.json')).exists()
    assert (tmp_path / names[1]).exists()
    assert names[1] in json.loads((tmp_path / '.orphaned-audio.json').read_text())[project['project_id']]
    monkeypatch.setattr(store, '_remove_audio_file', remove)
    store.delete(store.create(title='Next')['project_id'])
    assert not (tmp_path / names[1]).exists()


@pytest.mark.parametrize('state', ['queued', 'running'])
def test_relocate_refuses_active_take(tmp_path, monkeypatch, state):
    root = tmp_path / 'library'
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(root))
    store = Store(root)
    project = store.create(title='Song')
    project['takes'] = [{'state': state}]
    store._save(project)
    with pytest.raises(ValueError, match=r'^A song is still being made\. Wait for it to finish, then move the library\.$'):
        service.relocate_library(tmp_path / 'destination', 'move')
    assert (root / (project['project_id'] + '.json')).exists()
