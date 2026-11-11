"""The render must outlive the process that requested it, and dead workers must be recoverable."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from fake_ace import make_runtime, wav_bytes  # noqa: E402
from support import install_shared_runtime, pin_fixture_runtime  # noqa: E402
from music_studio import service  # noqa: E402
from music_studio.jobs import Jobs, process_alive  # noqa: E402


def setUpModule():
    global _RUNTIME_PIN
    _RUNTIME_PIN = install_shared_runtime()


def tearDownModule():
    _RUNTIME_PIN.close()


class WorkerProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime_dir = tempfile.TemporaryDirectory()
        self.runtime = make_runtime(self.runtime_dir.name)
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()
        # In-process generate()/status() must see the fixture runtime even though
        # ace_engine froze its paths before the test started.
        self.pinned = pin_fixture_runtime(self.runtime)
        self.pinned.__enter__()
        self.store = service.store()
        self.pid = self.store.create(caption='folk', lyrics='hello there',
                                     duration_seconds=10)['project_id']
        self.store.approve(self.pid)

    def tearDown(self):
        self.pinned.close()
        self.env.stop()
        self.runtime_dir.cleanup()
        self.temp.cleanup()

    # -- helpers -------------------------------------------------------
    def spawn_requester(self, seed=7):
        """A short-lived 'agent' process that asks for a render and exits at once."""
        script = textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {str(ROOT)!r})
            from music_studio import service
            try:
                result = service.generate({self.pid!r}, {seed})
            except Exception as exc:
                print(json.dumps({{'error': str(exc)}}))
                sys.exit(2)
            print(json.dumps({{'take_id': result['take']['take_id']}}))
        """)
        # Keep the inherited PYTHONPATH: the child must still be able to import
        # `plugins` (music_studio.service imports plugins.plugin_storage). On a
        # plain interpreter the package lives on PYTHONPATH, not in a .pth file.
        inherited = [p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p]
        child_env = {**os.environ,
                     'PYTHONPATH': os.pathsep.join([str(ROOT), *inherited])}
        return subprocess.run([sys.executable, '-c', script], capture_output=True,
                              text=True, timeout=30, env=child_env)

    def wait_state(self, take_id, states=('succeeded', 'failed'), limit=20):
        deadline = time.time() + limit
        while time.time() < deadline:
            service.status()  # exercises automatic reconciliation on every poll
            for project in self.store.all():
                for take in project['takes']:
                    if take['take_id'] == take_id and take['state'] in states:
                        return take
            time.sleep(.02)
        self.fail(f'take {take_id} never reached {states}')

    def dead_pid(self):
        proc = subprocess.Popen([sys.executable, '-c', 'pass'])
        proc.wait()
        return proc.pid

    def live_process(self):
        return subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])

    # -- tests ---------------------------------------------------------
    def test_render_survives_the_requesting_process_exiting(self):
        make_runtime(self.runtime, delay=1.0)  # still rendering when the parent is gone
        done = self.spawn_requester()
        self.assertEqual(done.returncode, 0, done.stderr)
        take_id = json.loads(done.stdout)['take_id']
        take = next(t for t in self.store.get(self.pid)['takes'] if t['take_id'] == take_id)
        self.assertIn(take['state'], ('queued', 'running'), 'parent must not finish the render')

        take = self.wait_state(take_id)
        self.assertEqual(take['state'], 'succeeded', take['error'])
        self.assertFalse(any(t['state'] in ('queued', 'running') for t in self.store.get(self.pid)['takes']))
        deadline = time.time() + 5
        while process_alive(take['pid']) and time.time() < deadline:
            time.sleep(.02)
        self.assertFalse(process_alive(take['pid']), 'worker should exit after committing')
        self.assertEqual(Jobs(self.store).read_audio(self.pid, take_id), wav_bytes())
        self.assertEqual((self.store.root / take['audio_file']).read_bytes(), wav_bytes())
        lease = self.store.worker_lock()
        self.assertIsNotNone(lease, 'worker must release the GPU lease')
        lease.close()

    def test_status_marks_take_of_dead_worker_failed_and_frees_lock(self):
        jobs = Jobs(self.store)
        take = jobs.enqueue(self.pid, 3)
        jobs.start(self.pid, take['take_id'], pid=self.dead_pid())
        result = service.status()
        state = next(t for p in result['projects'] for t in p['takes']
                     if t['take_id'] == take['take_id'])
        self.assertEqual(state['state'], 'failed')
        self.assertEqual(state['error'], 'worker died')
        self.assertIsNone(state['audio_file'])
        lease = self.store.worker_lock()
        self.assertIsNotNone(lease, 'dead take must not keep the GPU lease')
        lease.close()

        retry = service.generate(self.pid, 4)
        self.assertEqual(self.wait_state(retry['take']['take_id'])['state'], 'succeeded')

    def test_live_worker_is_not_reconciled_and_dead_one_is(self):
        worker = self.live_process()
        jobs = Jobs(self.store)
        take = jobs.enqueue(self.pid, 5)
        jobs.start(self.pid, take['take_id'], pid=worker.pid)
        try:
            service.status()
            self.assertEqual(self.store.get(self.pid)['takes'][0]['state'], 'running')
        finally:
            worker.terminate()
            worker.wait()
        service.status()
        failed = self.store.get(self.pid)['takes'][0]
        self.assertEqual((failed['state'], failed['error']), ('failed', 'worker died'))
        lease = self.store.worker_lock()
        self.assertIsNotNone(lease)
        lease.close()

    def test_spawn_in_flight_take_is_never_reconciled(self):
        jobs = Jobs(self.store)
        take = jobs.enqueue(self.pid, 6)
        # pid not attached yet: still inside the grace period, must survive a status poll.
        service.status()
        self.assertEqual(self.store.get(self.pid)['takes'][0]['state'], 'queued')
        reconciled = jobs.reconcile_dead_workers(grace_seconds=0)
        self.assertEqual(reconciled, 1)
        self.assertEqual(self.store.get(self.pid)['takes'][0]['error'], 'worker died')

    def test_import_recovery_fails_legacy_take_without_pid(self):
        jobs = Jobs(self.store)
        take = jobs.enqueue(self.pid, 9)
        project = self.store.get(self.pid)
        project['takes'][0].update(state='running', created_at=None)
        self.store._save(project)
        service.recover_on_load()
        failed = self.store.get(self.pid)['takes'][0]
        self.assertEqual((failed['state'], failed['error']), ('failed', 'worker died'))
        lease = self.store.worker_lock()
        self.assertIsNotNone(lease)
        lease.close()
        self.assertEqual(self.store.get(self.pid)['takes'][0]['take_id'], take['take_id'])

    def test_no_double_claim_of_a_take(self):
        jobs = Jobs(self.store)
        take = jobs.enqueue(self.pid, 8)
        first = jobs.claim(self.pid, take['take_id'], os.getpid())
        self.assertEqual(first['state'], 'running')
        self.assertEqual(jobs.claim(self.pid, take['take_id'], os.getpid())['state'], 'running')
        with self.assertRaisesRegex(ValueError, 'not claimable'):
            jobs.claim(self.pid, take['take_id'], os.getpid() + 1)
        jobs.abort(self.pid, take['take_id'], 'worker died')
        with self.assertRaisesRegex(ValueError, 'not claimable'):
            jobs.claim(self.pid, take['take_id'], os.getpid())

    def test_concurrent_generate_admits_exactly_one_worker(self):
        from concurrent.futures import ThreadPoolExecutor
        make_runtime(self.runtime, delay=0.5)

        def submit(seed):
            try:
                return service.generate(self.pid, seed)['take']['take_id']
            except (ValueError, RuntimeError):
                return 'busy'

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(submit, range(6)))
        self.assertEqual(results.count('busy'), 5)
        admitted = [r for r in results if r != 'busy']
        self.assertEqual(len(admitted), 1)
        self.assertEqual(admitted[0] and self.wait_state(admitted[0])['state'], 'succeeded')
        self.assertEqual(len(service.store().get(self.pid)['takes']), 1)


if __name__ == '__main__':
    unittest.main()
