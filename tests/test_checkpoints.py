"""Save your spot: a copy of everything needed to resume, and the rule that differs by engine.

The trap this pins: ACE replays exactly from a seed, so a spot can offer "rebuild from seed". MiniMax's
seed replay was never measured, so an M3 spot must keep the audio file itself — a checkpoint that stored
only a seed for M3 could fail to reproduce the song it claims to save.
"""
import hashlib
import sys
import tempfile
import unittest
import uuid
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import checkpoints  # noqa: E402
from music_studio.store import Store  # noqa: E402


def _wav(path: Path, seconds=1.0, rate=8000):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b'\x00\x01' * int(rate * seconds))
    return path


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.project = self.store.create(title='Saver', caption='folk, fiddle', lyrics='[chorus]\nla',
                                         duration_seconds=30, bpm=96, keyscale='D minor')
        self.pid = self.project['project_id']

    def tearDown(self):
        self.tmp.cleanup()

    def add_take(self, engine='ace_local', state='succeeded', seed=7, audio=True):
        take_id = str(uuid.uuid4())
        record = {'take_id': take_id, 'state': state, 'engine_id': engine,
                  'requested_seed': seed, 'seed': seed, 'edits': []}
        if audio:
            name = f'{take_id}.wav'
            _wav(self.root / name)
            record['audio_file'] = name
        fresh = self.store.get(self.pid)
        fresh['takes'].append(record)
        self.store._save(fresh)
        return record

    def test_a_spot_is_a_copy_of_the_draft_not_an_index(self):
        spot = checkpoints.save(self.store, self.pid, label='before the bridge')['checkpoint']
        self.assertEqual(spot['draft']['caption'], 'folk, fiddle')
        self.assertEqual(spot['draft']['bpm'], 96)
        self.assertEqual(spot['label'], 'before the bridge')
        # Change the draft afterwards: the spot must still hold the old text.
        self.store.update(self.pid, caption='metal')
        again = self.store.checkpoints(self.pid)[0]
        self.assertEqual(again['draft']['caption'], 'folk, fiddle')

    def test_the_kept_take_is_recorded_with_the_hash_of_its_audio(self):
        take = self.add_take()
        self.store.remember(self.pid, kind='keep', text='', take_id=take['take_id'], bump=False)

        spot = checkpoints.save(self.store, self.pid)['checkpoint']

        self.assertEqual(spot['kept_take']['take_id'], take['take_id'])
        digest = hashlib.sha256((self.root / take['audio_file']).read_bytes()).hexdigest()
        self.assertEqual(spot['kept_take']['audio']['sha256'], digest)

    def test_ace_can_be_rebuilt_from_its_seed(self):
        take = self.add_take(engine='ace_local')
        self.store.remember(self.pid, kind='keep', text='', take_id=take['take_id'], bump=False)
        spot = checkpoints.save(self.store, self.pid)['checkpoint']
        self.assertEqual(spot['kept_take']['fidelity'], 'seed')

    def test_minimax_keeps_the_audio_because_its_seed_replay_was_never_measured(self):
        take = self.add_take(engine='minimax_m3_comfy')
        self.store.remember(self.pid, kind='keep', text='', take_id=take['take_id'], bump=False)
        spot = checkpoints.save(self.store, self.pid)['checkpoint']
        self.assertEqual(spot['kept_take']['fidelity'], 'audio')
        self.assertIsNotNone(spot['kept_take']['audio'], 'an M3 spot must point at the file it keeps')

    def test_a_spot_is_not_a_draft_edit_so_it_does_not_revoke_approval(self):
        self.store.approve(self.pid, base_updated_at=self.store.get(self.pid)['updated_at'])
        checkpoints.save(self.store, self.pid)
        self.assertTrue(self.store.get(self.pid)['approved'])

    def test_old_spots_are_pruned_but_a_pinned_one_survives(self):
        pinned = checkpoints.save(self.store, self.pid, label='pinned')['checkpoint']
        fresh = self.store.get(self.pid)
        fresh['checkpoints'][0]['pinned'] = True
        self.store._save(fresh, bump=False)
        for index in range(22):
            checkpoints.save(self.store, self.pid, label=f'spot {index}')
        rows = self.store.checkpoints(self.pid)
        self.assertEqual(len(rows), 20)
        self.assertTrue(any(c['checkpoint_id'] == pinned['checkpoint_id'] for c in rows))

    def test_going_back_restores_the_draft_and_revokes_the_approval(self):
        spot = checkpoints.save(self.store, self.pid, label='before')['checkpoint']
        self.store.update(self.pid, caption='something else entirely')
        self.store.approve(self.pid, base_updated_at=self.store.get(self.pid)['updated_at'])

        result = checkpoints.restore(self.store, self.pid, spot['checkpoint_id'])

        self.assertEqual(result['project']['caption'], 'folk, fiddle')
        self.assertFalse(result['project']['approved'], 'a restored draft is not the approved one')
        self.assertIn('approval is revoked', result['note'])

    def test_an_unknown_spot_is_a_sentence_not_a_crash(self):
        with self.assertRaises(KeyError):
            checkpoints.restore(self.store, self.pid, 'nope')

    def test_the_listing_is_short_enough_to_show_in_a_chat(self):
        checkpoints.save(self.store, self.pid, label='one')
        rows = checkpoints.listing(self.store, self.pid)['checkpoints']
        self.assertEqual(rows[0]['label'], 'one')
        self.assertLessEqual(set(rows[0]), {'checkpoint_id', 'at', 'label', 'approved',
                                            'kept_take_id', 'fidelity'})


if __name__ == '__main__':
    unittest.main()
