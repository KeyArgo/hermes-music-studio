import tempfile
import unittest
from music_studio.store import Store


class StoreTests(unittest.TestCase):
    def test_project_survives_reopening(self):
        with tempfile.TemporaryDirectory() as root:
            project = Store(root).create(title='A')
            self.assertEqual(Store(root).get(project['project_id'])['title'], 'A')

    def test_edit_invalidates_approval(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            project = store.create(title='A', caption='music', lyrics='hello')
            pid = project['project_id']
            store.approve(pid)
            self.assertTrue(store.get(pid)['approved'])
            store.update(pid, lyrics='changed')
            self.assertFalse(store.get(pid)['approved'])

    def test_rejects_invalid_ids_and_protected_fields(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            pid = s.create()['project_id']
            for invalid in ('../outside', '/tmp/x', '', pid.upper()):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    s.get(invalid)
            with self.assertRaises(ValueError):
                s.update(pid, approved=True)
            with self.assertRaises(ValueError):
                s.update(pid, duration_seconds=True)
