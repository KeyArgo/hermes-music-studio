"""A render moving through its take states must not make an open draft look stale.

The page uses the project's ``updated_at`` token to tell that someone else changed the draft. A take
queued, started or finished is not a draft edit, so it must leave that token alone; an actual edit
still has to move it.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from fake_ace import wav_bytes  # noqa: E402
from music_studio.jobs import Jobs  # noqa: E402
from music_studio.store import Store  # noqa: E402


def test_a_take_from_queue_to_success_leaves_the_draft_token_alone(tmp_path):
    store = Store(tmp_path / 'library')
    project = store.create(title='Song', caption='quiet', lyrics='one line',
                           duration_seconds=12)
    pid = project['project_id']
    store.approve(pid, base_updated_at=store.get(pid)['updated_at'])
    token = store.get(pid)['updated_at']
    jobs = Jobs(store)

    take = jobs.enqueue(pid, 1)
    assert store.get(pid)['updated_at'] == token

    jobs.start(pid, take['take_id'], pid=os.getpid())
    jobs.progress(pid, take['take_id'], progress=40)
    assert store.get(pid)['updated_at'] == token

    jobs.succeed(pid, take['take_id'], wav_bytes())
    assert store.get(pid)['updated_at'] == token

    store.update(pid, lyrics='two lines')
    assert store.get(pid)['updated_at'] != token
