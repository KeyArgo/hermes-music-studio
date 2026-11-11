import io
import os
import struct
import tempfile
import unittest
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from music_studio.jobs import Jobs
from music_studio.store import Store


def _tone(seconds=0.5, rate=8000, amplitude=0.4):
    frames = int(seconds * rate)
    values = [int(amplitude * 32767) if i % 2 else -int(amplitude * 32767)
              for i in range(frames)]
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as audio:
        audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(struct.pack(f'<{len(values)}h', *values))
    return buf.getvalue()


def _loud_with_tail(rate=8000):
    """Full-scale tone for 0.5 s then 1.5 s silence: needs trim *and* a cap."""
    music = [32767 if i % 2 else -32767 for i in range(rate // 2)]
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as audio:
        audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(struct.pack(f'<{len(music)}h', *music)
                          + b'\x00\x00' * (rate * 3 // 2))
    return buf.getvalue()


class JobTests(unittest.TestCase):
    def test_enqueue_requires_approval(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            p = s.create(title='A')
            with self.assertRaises(ValueError):
                Jobs(s).enqueue(p['project_id'], seed=42)

    def test_snapshot_is_persisted_and_busy_refuses_second_job(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            pid = s.create(title='A', caption='music', lyrics='original')['project_id']
            s.approve(pid)
            job = Jobs(s).enqueue(pid, seed=42)
            self.assertEqual(job['state'], 'queued')
            self.assertEqual(job['snapshot']['lyrics'], 'original')
            with self.assertRaisesRegex(ValueError, 'busy'):
                Jobs(Store(root)).enqueue(pid, seed=43)
            s.update(pid, lyrics='changed')
            self.assertEqual(Store(root).get(pid)['takes'][0]['snapshot']['lyrics'], 'original')

    def test_lifecycle_and_restart_interruption(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            pid = s.create(title='A', caption='music', lyrics='words')['project_id']
            s.approve(pid)
            jobs = Jobs(s)
            take = jobs.enqueue(pid, seed=1)
            jobs.start(pid, take['take_id'])
            with self.assertRaisesRegex(ValueError, 'confirm'):
                Jobs(Store(root)).reconcile_interrupted()
            Jobs(Store(root)).reconcile_interrupted(confirmed=True)
            self.assertEqual(s.get(pid)['takes'][0]['state'], 'failed')
            self.assertIn('interrupted', s.get(pid)['takes'][0]['error'])

    def test_two_completed_takes_have_distinct_valid_audio(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            pid = s.create(title='A', caption='music', lyrics='words')['project_id']
            s.approve(pid)
            j = Jobs(s)
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as audio:
                audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\x00\x00' * 100)
            data = buf.getvalue()
            names = []
            for seed in (1, 2):
                take = j.enqueue(pid, seed)
                j.start(pid, take['take_id'])
                with self.assertRaises(ValueError):
                    j.succeed(pid, take['take_id'], b'not audio')
                result = j.succeed(pid, take['take_id'], data)
                self.assertEqual(result['state'], 'succeeded')
                self.assertEqual(j.read_audio(pid, take['take_id']), data)
                names.append(result['audio_file'])
            self.assertNotEqual(*names)
            self.assertEqual(len(Store(root).get(pid)['takes']), 2)

    def test_concurrent_enqueue_admits_exactly_one(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            pid = s.create(caption='music', lyrics='words')['project_id']
            s.approve(pid)
            def submit(seed):
                try:
                    return Jobs(Store(root)).enqueue(pid, seed)['state']
                except ValueError:
                    return 'busy'
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(submit, range(8)))
            self.assertEqual(results.count('queued'), 1)
            self.assertEqual(results.count('busy'), 7)
            self.assertEqual(len(s.get(pid)['takes']), 1)

    def test_invalid_transitions_and_ids_refused(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            pid = s.create(caption='music', lyrics='words')['project_id']
            s.approve(pid)
            j = Jobs(s)
            take = j.enqueue(pid, 1)
            with self.assertRaises(ValueError):
                j.fail(pid, take['take_id'], 'failed')
            with self.assertRaises(ValueError):
                j.read_audio(pid, '../outside')
            j.start(pid, take['take_id'])
            j.fail(pid, take['take_id'], 'engine failed')
            with self.assertRaises(ValueError):
                j.start(pid, take['take_id'])
            self.assertIsNone(s.get(pid)['takes'][0]['audio_file'])


class TakeMetadataTests(unittest.TestCase):
    """The engine's provenance must reach the take, licence notice included."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.pid = self.store.create(caption='music', lyrics='words')['project_id']
        self.store.approve(self.pid)
        self.jobs = Jobs(self.store)
        self.take = self.jobs.enqueue(self.pid, 1)
        self.jobs.start(self.pid, self.take['take_id'])

    def _stored(self):
        return self.store.get(self.pid)['takes'][0]

    def test_succeed_stores_the_engine_metadata_on_the_take(self):
        metadata = {'engine_id': 'minimax_m3_comfy', 'licence': 'MiniMax-Music3',
                    'licence_notice': 'Licensed under the MiniMax Community License',
                    'fell_back': False}
        take = self.jobs.succeed(self.pid, self.take['take_id'], _tone(),
                                 metadata=metadata)
        self.assertEqual(take['metadata'], metadata)
        self.assertEqual(self._stored()['metadata'], metadata)

    def test_metadata_that_cannot_be_stored_is_refused_before_the_audio(self):
        with self.assertRaises(ValueError):
            self.jobs.succeed(self.pid, self.take['take_id'], _tone(),
                              metadata={'engine_id': object()})
        stored = self._stored()
        self.assertEqual(stored['state'], 'running')
        self.assertIsNone(stored['audio_file'])
        self.assertFalse((self.store.root / f"{self.take['take_id']}.wav").exists())


class EditSafetyTests(unittest.TestCase):
    """Edits are files with ids; neither a crafted record nor a crash may escape."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.pid = self.store.create(caption='music', lyrics='words')['project_id']
        self.store.approve(self.pid)
        self.jobs = Jobs(self.store)
        self.take = self.jobs.enqueue(self.pid, 1)
        self.jobs.start(self.pid, self.take['take_id'])
        self.jobs.succeed(self.pid, self.take['take_id'], _loud_with_tail())

    def _edit(self, audio_file):
        edit_id = str(uuid.uuid4())
        with self.store.locked():
            project = self.store.get(self.pid)
            project['takes'][0].setdefault('edits', []).append(
                {'edit_id': edit_id, 'kind': 'trim', 'audio_file': audio_file})
            self.store._save(project)
        return edit_id

    def test_an_edit_record_cannot_point_outside_the_store(self):
        escape = Path(self.tmp.name).parent / f'{uuid.uuid4()}.wav'
        escape.write_bytes(_tone())
        self.addCleanup(lambda: escape.unlink(missing_ok=True))
        edit_id = self._edit('../' + escape.name)
        with self.assertRaises(ValueError):
            self.jobs.read_audio(self.pid, self.take['take_id'], edit_id=edit_id)

    def test_the_edit_file_is_written_while_the_store_lock_is_held(self):
        # A concurrent delete takes the same lock; writing under it is what
        # stops a live edit from becoming an orphan.
        from music_studio import portalock
        observed = {}
        real_replace = os.replace
        take_name = f"{self.take['take_id']}.wav"

        def replace(src, dst):
            real_replace(src, dst)
            if str(dst).endswith('.wav') and Path(dst).name != take_name:
                with open(Path(self.store.root) / '.lock', 'a') as probe:
                    try:
                        portalock.lock_ex(probe, blocking=False)
                    except BlockingIOError:
                        observed['locked'] = True
                    else:
                        portalock.unlock(probe)
                        observed['locked'] = False

        with patch('music_studio.jobs.os.replace', side_effect=replace):
            edit = self.jobs.apply_qc_edit(self.pid, self.take['take_id'])
        self.assertIsNotNone(edit)
        self.assertTrue(observed.get('locked'), 'edit WAV was written outside the store lock')

    def test_an_edit_that_cannot_be_recorded_leaves_no_orphan_wav(self):
        before = {name for name in os.listdir(self.store.root) if name.endswith('.wav')}
        with patch.object(self.store, '_save', side_effect=ValueError('disk full')):
            with self.assertRaises(ValueError):
                self.jobs.apply_qc_edit(self.pid, self.take['take_id'])
        after = {name for name in os.listdir(self.store.root) if name.endswith('.wav')}
        self.assertEqual(after - before, set())
