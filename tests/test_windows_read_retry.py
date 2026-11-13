"""A Windows reader racing a writer's ``os.replace`` waits it out instead of failing.

CI on windows-latest failed `WorkerLeaseTests` with `PermissionError: [Errno 13]` on a project's
.json: the render worker was swapping the file in while the test read it. Windows refuses other
openers for that moment; Linux and macOS never do, which is why only Windows was red.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from music_studio import store


def test_a_sharing_violation_during_a_replace_is_waited_out(tmp_path, monkeypatch):
    target = tmp_path / 'p.json'
    target.write_text('{"ok": true}')
    real = Path.read_text
    refusals = {'left': 3}

    def flaky(self, *args, **kwargs):
        if self == target and refusals['left']:
            refusals['left'] -= 1
            raise PermissionError(13, 'Permission denied')
        return real(self, *args, **kwargs)

    monkeypatch.setattr(store, 'os', SimpleNamespace(name='nt'))
    with patch.object(Path, 'read_text', flaky):
        assert store.read_text_with_retry(target, delay=0) == '{"ok": true}'
    assert refusals['left'] == 0


def test_a_file_that_stays_locked_still_raises(tmp_path, monkeypatch):
    target = tmp_path / 'p.json'
    target.write_text('{}')

    def locked(self, *args, **kwargs):
        raise PermissionError(13, 'Permission denied')

    monkeypatch.setattr(store, 'os', SimpleNamespace(name='nt'))
    with patch.object(Path, 'read_text', locked), pytest.raises(PermissionError):
        store.read_text_with_retry(target, attempts=3, delay=0)


def test_project_reads_go_through_the_retry(tmp_path, monkeypatch):
    calls = []
    real = store.read_text_with_retry
    monkeypatch.setattr(store, 'read_text_with_retry', lambda path, **kw: calls.append(path) or real(path, **kw))
    library = store.Store(tmp_path) if hasattr(store, 'Store') else None
    if library is None:
        pytest.skip('no Store class')
    pid = library.create(caption='folk', lyrics='hi', duration_seconds=10)['project_id']
    library.get(pid)
    assert calls and calls[-1].name == pid + '.json'
