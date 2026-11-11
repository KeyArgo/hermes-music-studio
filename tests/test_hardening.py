"""Regression tests for the hardening pass: recovery, contract caps, store, tokens, progress."""
import json
import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from dashboard.plugin_api import router  # noqa: E402
from music_studio import service  # noqa: E402
from music_studio.jobs import Jobs  # noqa: E402
from music_studio.store import (DURATION_MAX_SECONDS, DURATION_MIN_SECONDS,  # noqa: E402
                                Store)
from fake_ace import make_runtime, wav_bytes  # noqa: E402
from support import install_shared_runtime, pin_fixture_runtime  # noqa: E402


def setUpModule():
    global _RUNTIME_PIN
    _RUNTIME_PIN = install_shared_runtime()


def tearDownModule():
    _RUNTIME_PIN.close()


class StoreHardeningTests(unittest.TestCase):
    def test_all_skips_corrupt_project_file_and_surfaces_warning(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            good = s.create(title='A')
            bad_id = str(uuid.uuid4())
            (Path(root) / (bad_id + '.json')).write_text('{not json')
            projects = s.all()
            self.assertEqual([p['project_id'] for p in projects], [good['project_id']])
            self.assertTrue(any(bad_id in w for w in s.warnings), s.warnings)

    def test_all_skips_structurally_broken_project(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            good = s.create(title='A')
            broken_id = str(uuid.uuid4())
            (Path(root) / (broken_id + '.json')).write_text(json.dumps({'project_id': broken_id}))
            self.assertEqual(len(s.all()), 1)
            self.assertTrue(s.warnings)

    def test_save_fsyncs_the_directory_after_replace(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            with patch('music_studio.store.fsync_dir') as fsync:
                s.create(title='B')
            fsync.assert_called()
            self.assertEqual(Path(fsync.call_args[0][0]), s.root)

    def test_text_field_lengths_are_capped(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            pid = s.create(caption='music', lyrics='words')['project_id']
            for draft, field in (({'lyrics': 'x' * 4097}, 'lyrics'),
                                 ({'caption': 'x' * 513}, 'caption'),
                                 ({'title': 'x' * 201}, 'title'),
                                 ({'idea': 'x' * 2001}, 'idea')):
                with self.subTest(field=field):
                    with self.assertRaisesRegex(ValueError, field):
                        s.create(**draft)
                    with self.assertRaisesRegex(ValueError, field):
                        s.update(pid, **draft)
            # Boundary values are accepted.
            s.update(pid, caption='x' * 512, lyrics='y' * 4096)


class ContractValidationTests(unittest.TestCase):
    def test_validate_ready_enforces_engine_caps(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            pid = s.create(caption='music', lyrics='words')['project_id']
            for field, value in (('caption', 'x' * 513), ('lyrics', 'y' * 4097),
                                 ('duration_seconds', DURATION_MAX_SECONDS + 1),
                                 ('duration_seconds', DURATION_MIN_SECONDS - 1)):
                with self.subTest(field=field):
                    p = s.get(pid)
                    p.update(caption='music', lyrics='words', duration_seconds=30)
                    p[field] = value
                    s._save(p)
                    with self.assertRaisesRegex(ValueError, field.split('_')[0]):
                        s.approve(pid)
                    self.assertFalse(s.get(pid)['approved'])
            p = s.get(pid)
            p.update(caption='x' * 512, lyrics='y' * 4096,
                     duration_seconds=DURATION_MAX_SECONDS)
            s._save(p)
            self.assertTrue(s.approve(pid)['approved'])


class ApiHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.store = service.store()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def project(self, **draft):
        draft.setdefault('caption', 'music')
        draft.setdefault('lyrics', 'words')
        return self.client.post('/create', json={'draft': draft}).json()['project']

    def test_status_survives_one_corrupt_project_file(self):
        good = self.project(title='keep me')
        bad_id = str(uuid.uuid4())
        (self.store.root / (bad_id + '.json')).write_text('{"project_id": ')
        response = self.client.get('/status')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([p['project_id'] for p in body['projects']], [good['project_id']])
        self.assertTrue(body['warnings'])

    def test_stuck_queued_take_is_auto_failed_and_generation_unwedges(self):
        pid = self.project()['project_id']
        self.store.approve(pid)
        take = Jobs(self.store).enqueue(pid, 1)
        project = self.store.get(pid)
        project['takes'][0]['created_at'] = time.time() - 60
        self.store._save(project)
        body = self.client.get('/status').json()
        stuck = next(t for p in body['projects'] for t in p['takes']
                     if t['take_id'] == take['take_id'])
        self.assertEqual(stuck['state'], 'failed')
        self.assertTrue(stuck['error'])
        self.assertIsNotNone(stuck['finished_at'])
        # The profile is no longer wedged: a new take is admitted (and cleaned up).
        retry = Jobs(self.store).enqueue(pid, 2)
        self.assertEqual(retry['state'], 'queued')
        Jobs(self.store).abort(pid, retry['take_id'], 'test cleanup')

    def test_recover_requires_confirm_and_recovers_orphans(self):
        project = self.project()
        pid = project['project_id']
        self.store.approve(pid)
        take = Jobs(self.store).enqueue(pid, 1)
        Jobs(self.store).start(pid, take['take_id'], pid=os.getpid())
        self.assertEqual(self.client.post('/recover', json={}).status_code, 409)
        self.assertEqual(self.client.post('/recover', json={'confirm': False}).status_code, 409)
        self.assertEqual(self.store.get(pid)['takes'][0]['state'], 'running')
        recovered = self.client.post('/recover', json={'confirm': True})
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json(), {'success': True, 'recovered': 1})
        failed = self.store.get(pid)['takes'][0]
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('interrupted', failed['error'])

    def test_recover_is_409_when_a_render_holds_the_lease(self):
        project = self.project()
        pid = project['project_id']
        self.store.approve(pid)
        Jobs(self.store).enqueue(pid, 1)
        lease = self.store.worker_lock()
        self.assertIsNotNone(lease)
        try:
            self.assertEqual(self.client.post('/recover', json={'confirm': True}).status_code, 409)
        finally:
            lease.close()

    def test_worker_exiting_without_commit_marks_take_failed(self):
        pid = self.project()['project_id']
        self.store.approve(pid)
        take = Jobs(self.store).enqueue(pid, 1)
        lease = self.store.worker_lock()  # another render owns the GPU
        try:
            from music_studio import worker
            code = worker.main(['--data-dir', str(self.store.root),
                                '--project', pid, '--take', take['take_id']])
        finally:
            lease.close()
        self.assertEqual(code, 3)
        failed = self.store.get(pid)['takes'][0]
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('busy', failed['error'].lower())

    def test_spawn_failure_marks_take_failed_and_frees_generation(self):
        pid = self.project()['project_id']
        self.store.approve(pid)
        with patch('music_studio.service._spawn_worker', side_effect=OSError('boom')):
            with self.assertRaisesRegex(RuntimeError, 'could not start'):
                service.generate(pid, 1)
        take = self.store.get(pid)['takes'][0]
        self.assertEqual(take['state'], 'failed')
        self.assertIn('could not start', take['error'])

    def test_update_sets_updated_at_and_rejects_stale_token(self):
        project = self.project()
        pid = project['project_id']
        self.assertIsInstance(project['updated_at'], (int, float))
        first = project['updated_at']
        ok = self.client.post('/update', json={'project_id': pid, 'draft': {'lyrics': 'one'},
                                               'base_updated_at': first})
        self.assertEqual(ok.status_code, 200)
        second = ok.json()['project']['updated_at']
        self.assertGreaterEqual(second, first)
        stale = self.client.post('/update', json={'project_id': pid, 'draft': {'lyrics': 'two'},
                                                  'base_updated_at': first})
        self.assertEqual(stale.status_code, 409)
        self.assertIn('stale draft', stale.json()['detail'])
        self.assertEqual(self.store.get(pid)['lyrics'], 'one')
        no_token = self.client.post('/update', json={'project_id': pid, 'draft': {'lyrics': 'three'}})
        self.assertEqual(no_token.status_code, 200)

    def test_json_body_size_is_capped(self):
        huge = {'draft': {'title': 'x' * (512 * 1024), 'caption': 'music', 'lyrics': 'words'}}
        self.assertEqual(self.client.post('/create', json=huge).status_code, 413)

    def test_take_status_reports_progress_eta_and_timestamps(self):
        pid = self.project()['project_id']
        self.store.approve(pid)
        queued = Jobs(self.store).enqueue(pid, 1)
        take = next(t for p in self.client.get('/status').json()['projects']
                    for t in p['takes'] if t['take_id'] == queued['take_id'])
        for key in ('started_at', 'finished_at', 'progress', 'eta_seconds'):
            self.assertIn(key, take)
            self.assertIsNone(take[key])
        take = Jobs(self.store).start(pid, queued['take_id'], pid=os.getpid())
        Jobs(self.store).progress(pid, queued['take_id'], started_at=time.time() - 40, progress=50)
        take = next(t for p in self.client.get('/status').json()['projects']
                    for t in p['takes'] if t['take_id'] == queued['take_id'])
        self.assertIsNotNone(take['started_at'])
        self.assertEqual(take['progress'], 50)
        self.assertIsInstance(take['eta_seconds'], int)
        self.assertGreaterEqual(take['eta_seconds'], 0)
        done = Jobs(self.store).succeed(pid, queued['take_id'], wav_bytes())
        self.assertIsNotNone(done['finished_at'])
        self.assertEqual(done['progress'], 100)
        take = next(t for p in self.client.get('/status').json()['projects']
                    for t in p['takes'] if t['take_id'] == queued['take_id'])
        self.assertIsNone(take['eta_seconds'])

    def test_download_route_serves_attachment_bytes(self):
        pid = self.project()['project_id']
        self.store.approve(pid)
        take = Jobs(self.store).enqueue(pid, 1)
        Jobs(self.store).start(pid, take['take_id'], pid=os.getpid())
        Jobs(self.store).succeed(pid, take['take_id'], wav_bytes())
        response = self.client.get(f'/projects/{pid}/takes/{take["take_id"]}/download')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, wav_bytes())
        self.assertTrue(response.headers['content-disposition'].startswith('attachment'))
        self.assertIn(take['take_id'], response.headers['content-disposition'])
        audio = self.client.get(f'/projects/{pid}/takes/{take["take_id"]}/audio')
        self.assertEqual(audio.content, response.content)


class WorkerLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime_dir = tempfile.TemporaryDirectory()
        self.runtime = make_runtime(self.runtime_dir.name)
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()
        self.pinned = pin_fixture_runtime(self.runtime)
        self.pinned.__enter__()
        self.store = service.store()
        pid = self.store.create(caption='folk', lyrics='hello there', duration_seconds=10)['project_id']
        self.store.approve(pid)
        self.pid = pid

    def tearDown(self):
        self.pinned.close()
        self.env.stop()
        self.runtime_dir.cleanup()
        self.temp.cleanup()

    def wait_for(self, predicate, limit=20.0):
        deadline = time.time() + limit
        while time.time() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.02)
        self.fail('condition never met')

    def test_lease_is_owned_by_the_worker_not_the_caller(self):
        make_runtime(self.runtime_dir.name, delay=1.0)
        take = service.generate(self.pid, 1)['take']['take_id']

        def worker_owns_the_lease():
            # Only a *claimed, running* take can own the lease: before the worker
            # reaches its acquire, this process would win the race and read the
            # lease as busy for the wrong reason.
            if self.store.get(self.pid)['takes'][0]['state'] != 'running':
                return False
            lease = self.store.worker_lock()
            if lease is None:
                return True
            lease.close()
            return False

        # The requesting process is gone from generate(); a live render still owns the lease.
        # 60 s, not the 20 s default: a full-suite run starts many workers at once and this one
        # has to be scheduled, claim the take and acquire the lease inside the window.
        self.assertTrue(self.wait_for(worker_owns_the_lease, limit=60.0),
                        'the running worker must hold the lease')
        self.wait_for(lambda: self.store.get(self.pid)['takes'][0]['state'] == 'succeeded')

        def worker_released_lease():
            # "succeeded" means the WAV is committed; the worker then writes its
            # post-commit metadata and only closes the lease on exit. Poll for
            # the release instead of asserting the instant the take flips, or
            # this races that window and fails on a loaded machine.
            lease = self.store.worker_lock()
            if lease is None:
                return False
            lease.close()
            return True

        self.assertTrue(self.wait_for(worker_released_lease, limit=60.0),
                        'worker must release the lease when it exits')
        self.assertIsNotNone(take)


class EngineProcessGroupTests(unittest.TestCase):
    def test_timeout_kills_the_ace_process_group(self):
        import ace_engine
        original = (ace_engine.RUNTIME, ace_engine.SOURCE, ace_engine.PYTHON, ace_engine.LOCK)
        def restore():
            (ace_engine.RUNTIME, ace_engine.SOURCE,
             ace_engine.PYTHON, ace_engine.LOCK) = original
        self.addCleanup(restore)
        with tempfile.TemporaryDirectory() as runtime, tempfile.TemporaryDirectory() as out:
            make_runtime(runtime)
            script = Path(runtime) / 'venv/bin/python'
            script.write_text(
                '#!/usr/bin/env python3\n'
                'import os, subprocess, sys, time\n'
                'job = sys.argv[2]\n'
                'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])\n'
                'with open(os.path.join(os.path.dirname(job), "grandchild.pid"), "w") as fh:\n'
                '    fh.write(str(child.pid))\n'
                'time.sleep(120)\n')
            script.chmod(0o755)
            with patch.dict(os.environ, {'ACE_RUNTIME_DIR': runtime,
                                         'ACE_SOURCE_DIR': str(Path(runtime) / 'source')}):
                import importlib
                importlib.reload(ace_engine)
                with self.assertRaisesRegex(TimeoutError, 'timed out'):
                    ace_engine.generate(caption='jazz', lyrics='hello', duration_seconds=10,
                                        seed=1, output_dir=out, timeout_seconds=1.5)
            pid_file = Path(out) / 'grandchild.pid'
            deadline = time.time() + 5
            while not pid_file.exists() and time.time() < deadline:
                time.sleep(.05)
            self.assertTrue(pid_file.exists(), 'fake ACE never started its child')
            child_pid = int(pid_file.read_text())
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    with open(f'/proc/{child_pid}/stat', 'rb') as fh:
                        state = fh.read().rsplit(b')', 1)[1].split()[0]
                    if state == b'Z':
                        break
                except OSError:
                    break
                time.sleep(.05)
            else:
                self.fail('ACE grandchild survived the timeout (process group not killed)')


if __name__ == '__main__':
    unittest.main()
