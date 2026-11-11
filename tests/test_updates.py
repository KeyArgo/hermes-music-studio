"""Checking for a newer build, installing it, and going back — without touching the network or git."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import updates  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class CheckTests(unittest.TestCase):
    def latest(self):
        return {'sha': 'a' * 40,
                'commit': {'message': 'fix: something real\n\nbody',
                           'committer': {'date': '2026-11-10T08:00:00Z'}}}

    def test_a_newer_published_commit_is_reported_as_available(self):
        with patch.object(updates, 'current', lambda: {'commit': 'b' * 40, 'version': '1.0.0'}):
            with patch('urllib.request.urlopen', lambda *a, **k: FakeResponse(self.latest())):
                result = updates.check()
        self.assertTrue(result['update_available'])
        self.assertEqual(result['latest']['commit_short'], 'aaaaaaaa')
        self.assertEqual(result['latest']['message'], 'fix: something real')

    def test_the_same_commit_is_not_an_update(self):
        same = self.latest()
        with patch.object(updates, 'current', lambda: {'commit': same['sha'], 'version': '1.0.0'}):
            with patch('urllib.request.urlopen', lambda *a, **k: FakeResponse(same)):
                result = updates.check()
        self.assertFalse(result['update_available'])
        self.assertIn('newest published build', result['detail'])

    def test_github_unreachable_is_one_sentence(self):
        def boom(*args, **kwargs):
            raise OSError('network down')
        with patch('urllib.request.urlopen', boom):
            result = updates.check()
        self.assertFalse(result['ok'])
        self.assertIn('could not ask GitHub', result['detail'])


class ApplyTests(unittest.TestCase):
    def test_a_short_ref_is_refused_before_anything_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as raised:
                updates.apply(tmp, 'abc123')
        self.assertIn('40-character', str(raised.exception))

    def test_an_install_already_running_is_not_started_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, updates.JOB_FILE).write_text(json.dumps({'state': 'running'}))
            with self.assertRaises(ValueError) as raised:
                updates.apply(tmp, 'a' * 40)
        self.assertIn('already running', str(raised.exception))

    def test_the_previous_build_is_recorded_before_an_update_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(updates, 'current',
                              lambda: {'commit': 'c' * 40, 'version': '1.0.0'}):
                with patch.object(updates.subprocess, 'Popen') as popen:
                    popen.return_value.pid = 4242
                    job = updates.apply(tmp, 'd' * 40)
            previous = json.loads(Path(tmp, updates.PREVIOUS_FILE).read_text())
            self.assertEqual(previous['commit'], 'c' * 40)
            self.assertEqual(job['state'], 'running')
            self.assertEqual(job['ref'], 'd' * 40)

    def test_undo_without_a_previous_build_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as raised:
                updates.undo(tmp)
        self.assertIn('previous version', str(raised.exception))

    def test_undo_reinstalls_the_recorded_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, updates.PREVIOUS_FILE).write_text(json.dumps({'commit': 'e' * 40}))
            with patch.object(updates.subprocess, 'Popen') as popen:
                popen.return_value.pid = 11
                job = updates.undo(tmp)
        self.assertEqual(job['mode'], 'undo')
        self.assertEqual(job['ref'], 'e' * 40)

    def test_a_job_that_never_ran_is_idle_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(updates.job(tmp), {'state': 'idle'})
            self.assertEqual(updates.log_tail(tmp), '')


if __name__ == '__main__':
    unittest.main()
