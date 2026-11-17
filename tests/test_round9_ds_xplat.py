"""Windows refuses a rename, a read and an unlink while another process holds the file open.

`music_studio.store` already waits that window out for the files it commits itself
(`replace_with_retry`, `read_text_with_retry`). These are the same files, written and read from
the same processes, through calls that went around those helpers:

* the launcher's `studio-host.json` — the pane and the gateway read it while a launch rewrites it;
* the store's project record on delete — `/status` reads every record twice a second;
* the store's orphan list on delete — written by the previous delete, read by the next one;
* the export ledger — a lost commit there is a second copy of the same song in the music folder;
* a project's cover — served to the page while a new one is being committed.

Two more tests cover an orphan list this code did not write (a foreign key, half a file), which
made `delete` fail after it had already removed the record, or refuse every delete in the library.

Each Windows test fakes ``os.name`` and refuses the first attempts with ``PermissionError``, which
is what Windows raises for a sharing violation (WinError 32).
"""
from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

from music_studio import export, host_launcher, service, store


class _FakeOs:
    """The store's ``os`` with a platform name; everything else is the real module."""

    def __init__(self, name):
        self.name = name

    def __getattr__(self, attribute):
        return getattr(os, attribute)


def _windows(monkeypatch, *modules):
    """Tell each module it is running on Windows, which is what turns the retries on."""
    for module in modules or (store,):
        monkeypatch.setattr(module, 'os', _FakeOs('nt'))


def _locked_replace(monkeypatch, failures, only=None):
    """Patch ``os.replace`` to refuse ``failures`` times, then work normally."""
    real = os.replace
    calls = []

    def replace(source, target):
        calls.append(Path(target))
        if only is not None and Path(target) != Path(only):
            return real(source, target)
        if state['left']:
            state['left'] -= 1
            raise PermissionError(13, 'The process cannot access the file: WinError 32')
        return real(source, target)

    state = {'left': failures}
    monkeypatch.setattr(os, 'replace', replace)
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    return calls


def _refuse_once(monkeypatch, method, target):
    """Make ``Path.<method>`` refuse ``target`` once, as Windows does while it is open elsewhere."""
    real = getattr(Path, method)
    state = {'left': 1}

    def flaky(self, *args, **kwargs):
        if self == target and state['left']:
            state['left'] -= 1
            raise PermissionError(13, 'The process cannot access the file: WinError 32')
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, flaky)
    return state


# -- the launcher's state file ---------------------------------------------

def test_the_host_state_file_survives_a_windows_lock(tmp_path, monkeypatch):
    """A pane reading studio-host.json must not make the launch that writes it fail."""
    _windows(monkeypatch, store, host_launcher)
    calls = _locked_replace(monkeypatch, failures=2,
                            only=tmp_path / host_launcher.STATE_FILE)

    host_launcher._write_state(tmp_path, {'port': 8775, 'pid': 4321, 'token': 'secret'})

    saved = json.loads((tmp_path / host_launcher.STATE_FILE).read_text(encoding='utf-8'))
    assert saved == {'port': 8775, 'pid': 4321, 'token': 'secret'}
    assert len(calls) == 3


# -- the store's own delete ------------------------------------------------

def test_deleting_a_project_survives_a_locked_record(tmp_path, monkeypatch):
    """The record is the last thing delete removes; a busy reader must not fail the whole call."""
    library = store.Store(tmp_path)
    project = library.create(title='Locked record')
    record = tmp_path / (project['project_id'] + '.json')
    _windows(monkeypatch)
    refusals = _refuse_once(monkeypatch, 'unlink', record)

    library.delete(project['project_id'])

    assert refusals['left'] == 0
    assert not record.exists()


def test_deleting_a_project_survives_a_locked_orphan_list(tmp_path, monkeypatch):
    """The orphan list is written by one delete and read by the next; that read can be refused."""
    library = store.Store(tmp_path)
    first = library.create(title='First')
    second = library.create(title='Second')
    library.delete(first['project_id'])
    pending = tmp_path / '.orphaned-audio.json'
    assert pending.is_file(), 'the previous delete should have left its record behind'
    _windows(monkeypatch)
    refusals = _refuse_once(monkeypatch, 'read_text', pending)

    library.delete(second['project_id'])

    assert refusals['left'] == 0
    assert not (tmp_path / (second['project_id'] + '.json')).exists()


# -- the orphan list this code did not write -------------------------------

def test_a_foreign_key_in_the_orphan_list_does_not_fail_the_delete(tmp_path):
    """`valid_id` on a key read back from disk is input, not a promise: one bad key is not a crash."""
    library = store.Store(tmp_path)
    project = library.create(title='Song')
    (tmp_path / '.orphaned-audio.json').write_text(json.dumps({'not-a-project-id': []}))

    library.delete(project['project_id'])

    assert not (tmp_path / (project['project_id'] + '.json')).exists()
    pending = json.loads((tmp_path / '.orphaned-audio.json').read_text())
    assert 'not-a-project-id' in pending, 'the entry this code cannot use is kept, not dropped'


def test_a_truncated_orphan_list_does_not_block_every_delete(tmp_path):
    """Half a file left by a crash used to refuse every later delete, on every platform."""
    library = store.Store(tmp_path)
    project = library.create(title='Song')
    (tmp_path / '.orphaned-audio.json').write_text('{"11111111-1111-1111-1111-111')

    library.delete(project['project_id'])

    assert not (tmp_path / (project['project_id'] + '.json')).exists()
    assert isinstance(json.loads((tmp_path / '.orphaned-audio.json').read_text()), dict)


# -- the export ledger -----------------------------------------------------

def test_the_export_ledger_survives_a_windows_lock(tmp_path, monkeypatch):
    """A lost ledger commit is a second copy of the same song: the reuse check never sees it."""
    studio = tmp_path / 'studio'
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(studio))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    (tmp_path / 'Music').mkdir()

    class Audio:
        """Stands in for Jobs: the ledger, not the audio, is what this test is about."""

        def __init__(self, *args, **kwargs):
            pass

        def open_audio(self, project_id, take_id, edit_id=None):
            return io.BytesIO(b'RIFF-fake-wave')

    monkeypatch.setattr(service, 'Jobs', Audio)
    library = service.store()
    project = library.create(title='Night Bus')
    take_id = '22222222-2222-2222-2222-222222222222'
    project['takes'] = [{'take_id': take_id, 'state': 'succeeded'}]
    library._save(project)
    _windows(monkeypatch)
    _locked_replace(monkeypatch, failures=1, only=studio / 'exports.json')

    first = service.export_take(project['project_id'], take_id)
    second = service.export_take(project['project_id'], take_id)

    folder = Path(first['path']).parent
    written = sorted(path.name for path in folder.glob('*.wav'))
    assert written == ['Night Bus.wav'], f'the same take was exported twice: {written}'
    assert second['path'] == first['path']
    assert second.get('reused') is True
    entry = json.loads((studio / 'exports.json').read_text(encoding='utf-8'))[
        f"{project['project_id']}:{take_id}"]
    assert entry['path'] == first['path'], 'the ledger lost the commit, so the next export repeats'


def test_the_ledger_still_names_the_file_it_wrote(tmp_path, monkeypatch):
    """A guard for the fix above: the ledger still records the path and title it actually wrote."""
    studio = tmp_path / 'studio'
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(studio))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    (tmp_path / 'Music').mkdir()

    class Audio:
        def __init__(self, *args, **kwargs):
            pass

        def open_audio(self, project_id, take_id, edit_id=None):
            return io.BytesIO(b'RIFF-fake-wave')

    monkeypatch.setattr(service, 'Jobs', Audio)
    library = service.store()
    project = library.create(title='Cursor Blink')
    take_id = '33333333-3333-3333-3333-333333333333'
    project['takes'] = [{'take_id': take_id, 'state': 'succeeded'}]
    library._save(project)

    result = service.export_take(project['project_id'], take_id)

    assert Path(result['path']).read_bytes() == b'RIFF-fake-wave'
    entry = json.loads((studio / 'exports.json').read_text(encoding='utf-8'))[
        f"{project['project_id']}:{take_id}"]
    assert entry['path'] == result['path']
    assert entry['title'] == export.safe_name('Cursor Blink')


# -- cover art -------------------------------------------------------------

def test_a_new_cover_survives_a_windows_lock(tmp_path, monkeypatch):
    """The library draws the cover it already has while a new one is committed."""
    from music_studio import covers

    _windows(monkeypatch)
    _locked_replace(monkeypatch, failures=2, only=tmp_path / 'studio-covers' / 'p1.cover')
    image = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32

    covers.write_cover(tmp_path, 'p1', image, 'image/png')

    cover = covers.read_cover(tmp_path, 'p1')
    assert cover is not None
    mime, stored = cover
    assert mime == 'image/png'
    assert stored == image
