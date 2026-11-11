import tempfile
import unittest
from music_studio.store import DURATION_MAX_SECONDS, DURATION_MIN_SECONDS, Store
from music_studio.jobs import Jobs


class DraftRegressionTests(unittest.TestCase):
    def test_approval_requires_generation_ready_draft(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            for draft in (dict(caption='', lyrics='words'), dict(caption='  ', lyrics='words'),
                          dict(caption='music', lyrics='\n')):
                with self.subTest(draft=draft):
                    pid = s.create(**draft)['project_id']
                    with self.assertRaises(ValueError):
                        s.approve(pid)
                    self.assertFalse(s.get(pid)['approved'])
            # A duration outside the measured cap never even becomes a draft.
            for duration in (DURATION_MIN_SECONDS - 1, DURATION_MAX_SECONDS + 1):
                with self.subTest(duration=duration):
                    with self.assertRaises(ValueError):
                        s.create(caption='music', lyrics='words', duration_seconds=duration)
            for duration in (DURATION_MIN_SECONDS, DURATION_MAX_SECONDS):
                pid = s.create(caption='music', lyrics='words', duration_seconds=duration)['project_id']
                self.assertTrue(s.approve(pid)['approved'])

    def test_enqueue_revalidates_legacy_approved_draft(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            p = s.create(caption='music', lyrics='words', duration_seconds=DURATION_MAX_SECONDS)
            p['duration_seconds'] = DURATION_MAX_SECONDS + 1
            p['approved'] = True
            s._save(p)
            with self.assertRaises(ValueError):
                Jobs(s).enqueue(p['project_id'], 1)
            self.assertEqual(s.get(p['project_id'])['takes'], [])
