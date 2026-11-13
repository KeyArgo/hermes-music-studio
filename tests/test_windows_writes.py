"""Windows briefly locks newly replaced files; every commit waits it out.

Defender and the search indexer open a fresh file just after it appears, so a
bare ``os.replace`` into that name fails with ``PermissionError``. The store's
retry helper is now shared by the take and settings commits, and the ACE job
directory no longer discards a good render when cleanup itself is refused.
"""
from __future__ import annotations

import io
import os
import struct
import time
import wave
from pathlib import Path

import pytest

import ace_engine
from music_studio import settings, store
from music_studio.jobs import Jobs


def _wav_bytes(seconds=0.1, rate=8000):
    frames = int(seconds * rate)
    values = [0 if i % 2 else 1000 for i in range(frames)]
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as audio:
        audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(struct.pack(f'<{len(values)}h', *values))
    return buf.getvalue()


def _fake_os(name):
    """A stand-in for the store module's ``os``; pathlib still sees the real one."""
    class FakeOs:
        def __getattr__(self, attribute):
            return getattr(os, attribute)

    FakeOs.name = name
    return FakeOs()


def _locked_replace(monkeypatch, failures, only=None):
    """Patch ``os.replace`` to refuse ``failures`` times, then work normally."""
    real = os.replace
    calls = []
    sleeps = []
    remaining = {'left': failures}

    def replace(source, target):
        calls.append(Path(target))
        if only is not None and Path(target) != Path(only):
            return real(source, target)
        if remaining['left']:
            remaining['left'] -= 1
            raise PermissionError(13, 'file is locked by another process')
        return real(source, target)

    monkeypatch.setattr(os, 'replace', replace)
    monkeypatch.setattr(time, 'sleep', sleeps.append)
    return calls, sleeps


def test_replace_with_retry_waits_out_a_windows_lock(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.write_text('rendered')
    monkeypatch.setattr(store, 'os', _fake_os('nt'))
    calls, _sleeps = _locked_replace(monkeypatch, failures=2)

    store.replace_with_retry(source, target)

    assert target.read_text() == 'rendered'
    assert len(calls) == 3


def test_replace_with_retry_gives_up_after_a_bounded_wait(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.write_text('rendered')
    monkeypatch.setattr(store, 'os', _fake_os('nt'))
    _calls, sleeps = _locked_replace(monkeypatch, failures=10**6)

    with pytest.raises(PermissionError):
        store.replace_with_retry(source, target)

    # 60 attempts, backing off to 0.1 s: about 5.6 s, the budget that made Windows CI green.
    assert 4.0 <= sum(sleeps) <= 7.0


def test_replace_with_retry_is_immediate_off_windows(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.write_text('rendered')
    monkeypatch.setattr(store, 'os', _fake_os('posix'))
    calls, sleeps = _locked_replace(monkeypatch, failures=1)

    with pytest.raises(PermissionError):
        store.replace_with_retry(source, target)

    assert len(calls) == 1
    assert sleeps == []


def test_a_take_wav_commit_survives_a_windows_lock(tmp_path, monkeypatch):
    library = store.Store(tmp_path)
    project = library.create(title='Locked', caption='folk', lyrics='words')
    pid = project['project_id']
    library.approve(pid)
    jobs = Jobs(library)
    take = jobs.enqueue(pid, seed=1)
    jobs.start(pid, take['take_id'])
    monkeypatch.setattr(store, 'os', _fake_os('nt'))
    _calls, _sleeps = _locked_replace(
        monkeypatch, failures=2, only=tmp_path / (take['take_id'] + '.wav'))

    saved = jobs.succeed(pid, take['take_id'], _wav_bytes(),
                         metadata={'engine_id': 'ace_local'})

    assert saved['state'] == 'succeeded'
    assert (tmp_path / (take['take_id'] + '.wav')).is_file()


def test_a_settings_save_survives_a_windows_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'os', _fake_os('nt'))
    _locked_replace(monkeypatch, failures=2)

    settings.write(tmp_path, {'comfy_url': 'http://127.0.0.1:8188'})

    assert settings.read(tmp_path)['comfy_url'] == 'http://127.0.0.1:8188'


def test_a_workspace_save_survives_a_windows_lock(tmp_path, monkeypatch):
    from standalone import workspaces

    monkeypatch.setattr(store, 'os', _fake_os('nt'))
    _locked_replace(monkeypatch, failures=2, only=tmp_path / 'studio-workspaces.json')

    workspace = workspaces.WorkspaceStore(tmp_path).create('Mixes')

    assert workspaces.WorkspaceStore(tmp_path).list()[-1]['id'] == workspace['id']


def test_ace_job_cleanup_never_discards_a_good_render(tmp_path, monkeypatch):
    recorded = {}

    class Stop(Exception):
        pass

    class FakeTemporaryDirectory:
        def __init__(self, *args, **kwargs):
            recorded.update(kwargs)
            raise Stop()

    monkeypatch.setattr(ace_engine.tempfile, 'TemporaryDirectory', FakeTemporaryDirectory)
    monkeypatch.setattr(ace_engine, 'LOCK', tmp_path / 'ace.lock')
    output = tmp_path / 'output'
    output.mkdir()

    with pytest.raises(Stop):
        ace_engine._render(job_input={}, output_dir=output,
                           timeout_seconds=1, expected_seconds=1)

    assert recorded.get('ignore_cleanup_errors') is True
