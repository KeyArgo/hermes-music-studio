"""The host's state file carries the bearer token, so its permissions are part of the security story.

Review item: `studio-host.json` was written with the default 0644 under a 022 umask, so another local
user could read the token and drive every `/api` route (move the library, change the ACE runtime, spend
model credits). `os.open(..., 0o600)` alone is not enough: it only applies when the file is created, and
a `.tmp` left behind by a crash keeps whatever bits it had.
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import host_launcher  # noqa: E402

pytestmark = pytest.mark.skipif(not hasattr(os, 'chmod') or os.name == 'nt',
                                reason='POSIX permission bits only')


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_the_state_file_is_created_owner_only(tmp_path):
    host_launcher._write_state(tmp_path, {'pid': 1, 'port': 8775, 'token': 'a-secret'})
    state = tmp_path / host_launcher.STATE_FILE
    assert json.loads(state.read_text())['token'] == 'a-secret'
    assert _mode(state) == 0o600, f'the token file is readable by others: {oct(_mode(state))}'


def test_an_existing_world_readable_state_file_is_tightened(tmp_path):
    state = tmp_path / host_launcher.STATE_FILE
    state.write_text(json.dumps({'pid': 0, 'token': 'old'}))
    state.chmod(0o644)

    host_launcher._write_state(tmp_path, {'pid': 2, 'port': 8776, 'token': 'new'})

    assert _mode(state) == 0o600, 'an older install keeps its 0644 file after an update'


def test_a_leftover_temp_file_from_a_crash_does_not_keep_its_permissions(tmp_path):
    tmp = tmp_path / (host_launcher.STATE_FILE + '.tmp')
    tmp.write_text('{}')
    tmp.chmod(0o644)

    host_launcher._write_state(tmp_path, {'pid': 3, 'port': 8777, 'token': 'third'})

    state = tmp_path / host_launcher.STATE_FILE
    assert _mode(state) == 0o600
    assert not tmp.exists(), 'the temp file must be gone after the rename'
