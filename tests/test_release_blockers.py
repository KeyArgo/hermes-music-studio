"""Tests for the release-blocking defects from the final review.

Every test here fails on 43b0a4b and passes on the fixed tree:

* C1  /approve must be bound to the version the user reviewed.
* C4  a killed/aborted worker must not leave a live ACE render behind.
* C5  a zombie worker must not keep the profile "GPU busy".
* C6  take.error must not ship ACE tracebacks with absolute paths.
* C7  a streamed (chunked) body over the cap must be aborted, not buffered.
* C9  eta_seconds must not jump by minutes between polls of one take.
"""
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from dashboard.plugin_api import router  # noqa: E402
from music_studio import service  # noqa: E402
from music_studio.jobs import Jobs, process_alive  # noqa: E402
from fake_ace import make_runtime, wav_bytes  # noqa: E402
from support import install_shared_runtime, pin_fixture_runtime  # noqa: E402


def _proc_state(pid):
    """Single-letter process state, or None when the process is gone.

    Uses the product's own reader (/proc on Linux, ps on macOS). Windows has no state letters and no
    zombies, so a live process reads 'R' there."""
    from music_studio.jobs import _stat_state
    from music_studio.procs import pid_exists
    if os.name == 'nt':
        return 'R' if pid_exists(pid) else None
    state = _stat_state(pid)
    return state.decode() if state else None


def _hard_kill(pid):
    """SIGKILL: the worker gets no chance to clean up (TerminateProcess on Windows)."""
    os.kill(pid, getattr(signal, 'SIGKILL', signal.SIGTERM))


def _wait_until(predicate, limit=15.0, interval=.02):
    deadline = time.time() + limit
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def setUpModule():
    global _RUNTIME_PIN
    _RUNTIME_PIN = install_shared_runtime()


def tearDownModule():
    _RUNTIME_PIN.close()


class RouterTestCase(unittest.TestCase):
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

    def token(self, pid):
        return self.store.get(pid)['updated_at']


class ApprovalTokenTests(RouterTestCase):
    """C1: /approve must bind to the draft version the user actually reviewed."""

    def test_approve_requires_a_base_token(self):
        pid = self.project()['project_id']
        response = self.client.post('/approve', json={'project_id': pid})
        self.assertEqual(response.status_code, 409)
        self.assertIn('stale draft', response.json()['detail'])
        self.assertFalse(self.store.get(pid)['approved'])

    def test_approve_rejects_a_stale_token_and_keeps_text_unapproved(self):
        project = self.project(lyrics='WHAT THE USER REVIEWED')
        pid = project['project_id']
        reviewed = project['updated_at']
        # Another writer (the agent) replaces the draft while the form is open.
        edited = self.client.post('/update', json={
            'project_id': pid, 'draft': {'lyrics': 'AGENT TEXT THE USER NEVER SAW'},
            'base_updated_at': reviewed})
        self.assertEqual(edited.status_code, 200)
        self.assertIsNotNone(edited.json()['project']['updated_at'])

        stale = self.client.post('/approve', json={'project_id': pid, 'base_updated_at': reviewed})
        self.assertEqual(stale.status_code, 409)
        self.assertIn('stale draft', stale.json()['detail'])
        self.assertFalse(self.store.get(pid)['approved'])

        # Reloading the form and approving the text actually on screen works.
        fresh = self.client.post('/approve', json={'project_id': pid,
                                                   'base_updated_at': self.token(pid)})
        self.assertEqual(fresh.status_code, 200)
        self.assertTrue(fresh.json()['project']['approved'])
        self.assertEqual(self.store.get(pid)['lyrics'], 'AGENT TEXT THE USER NEVER SAW')

    def test_updated_at_is_strictly_increasing_so_tokens_cannot_collide(self):
        pid = self.project()['project_id']
        seen = [self.store.get(pid)['updated_at']]
        for text in ('a', 'b', 'c'):
            self.store.update(pid, lyrics=text)
            seen.append(self.store.get(pid)['updated_at'])
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(set(seen)), len(seen))


class BodyCapTests(RouterTestCase):
    """C7: an oversized chunked body must be refused while streaming."""

    def test_streamed_body_over_cap_is_aborted_not_buffered(self):
        """Drive json_body's ASGI receive directly: TestClient pre-buffers bodies."""
        import asyncio
        from starlette.requests import Request
        from dashboard.plugin_api import json_body, MAX_JSON_BODY_BYTES
        from fastapi import HTTPException
        chunk = 16 * 1024
        total = 64  # 1 MiB, four times the cap
        pulled = []

        async def receive():
            index = len(pulled)
            pulled.append(index)
            if index >= total:
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            return {'type': 'http.request', 'body': b'x' * chunk, 'more_body': True}

        request = Request({'type': 'http', 'method': 'POST', 'headers': [],
                           'path': '/create', 'query_string': b''}, receive)
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(json_body(request))
        self.assertEqual(raised.exception.status_code, 413)
        self.assertLessEqual(len(pulled), MAX_JSON_BODY_BYTES // chunk + 2,
                            'server kept pulling an oversized streamed body')

    def test_streamed_body_over_cap_is_413_through_the_router(self):
        def body():
            for _ in range(64):
                yield b'x' * (16 * 1024)

        response = self.client.post('/create', content=body(),
                                    headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 413)

    def test_declared_oversize_content_length_is_refused_up_front(self):
        response = self.client.post('/create', content=b'{}',
                                    headers={'content-type': 'application/json',
                                             'content-length': str(10 * 1024 * 1024)})
        self.assertEqual(response.status_code, 413)


@unittest.skipIf(os.name == 'nt', 'Windows has no zombie processes: an exited pid is gone')
class ZombieWorkerTests(unittest.TestCase):
    """C5: a SIGKILLed, unreaped worker must not read as a live render."""

    def test_zombie_reads_dead_without_proc_like_on_macos(self):
        """macOS has no /proc: the state must come from ps, or a zombie reads as alive."""
        import builtins
        real_open = builtins.open

        def no_proc(path, *a, **k):
            if str(path).startswith('/proc/'):
                raise FileNotFoundError(path)
            return real_open(path, *a, **k)

        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        try:
            self.assertTrue(_wait_until(lambda: _proc_state(child.pid) == 'Z', limit=10))
            with patch('builtins.open', no_proc), patch('sys.platform', 'darwin'):
                self.assertFalse(process_alive(child.pid), 'a zombie must not count as alive')
        finally:
            child.wait()

    def test_zombie_pid_is_not_alive(self):
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        try:
            self.assertTrue(_wait_until(lambda: _proc_state(child.pid) == 'Z', limit=10),
                            'child never became a zombie')
            self.assertFalse(process_alive(child.pid), 'a zombie must not count as alive')
        finally:
            child.wait()

    def test_zombie_worker_frees_the_profile_within_one_status_poll(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {'HERMES_HOME': home}):
                store = service.store()
                pid = store.create(caption='music', lyrics='words',
                                   duration_seconds=10)['project_id']
                store.approve(pid, base_updated_at=store.get(pid)['updated_at'])
                zombie = subprocess.Popen([sys.executable, '-c', 'pass'])
                try:
                    self.assertTrue(_wait_until(lambda: _proc_state(zombie.pid) == 'Z', limit=10))
                    jobs = Jobs(store)
                    take = jobs.enqueue(pid, 1)
                    jobs.start(pid, take['take_id'], pid=zombie.pid)
                    body = service.status()  # one poll must reconcile it
                    state = next(t for p in body['projects'] for t in p['takes']
                                 if t['take_id'] == take['take_id'])
                    self.assertEqual(state['state'], 'failed')
                    self.assertEqual(state['error'], 'worker died')
                    lease = store.worker_lock()
                    self.assertIsNotNone(lease, 'zombie worker must not hold the GPU lease')
                    lease.close()
                    # And the profile accepts the next render immediately.
                    retry = jobs.enqueue(pid, 2)
                    self.assertEqual(retry['state'], 'queued')
                    jobs.abort(pid, retry['take_id'], 'test cleanup')
                finally:
                    zombie.wait()


LONG_RENDER_SCRIPT = '''#!/usr/bin/env python3
import os, sys, time
with open(os.environ['ACE_ORPHAN_PIDFILE'], 'w') as fh:
    fh.write(str(os.getpid()))
time.sleep(120)
'''

TRACEBACK_SCRIPT = '''#!/usr/bin/env python3
import sys
sys.stderr.write("Traceback (most recent call last):\\n")
sys.stderr.write('  File "/home/alex/secret/path/ace_worker.py", line 33, in <module>\\n')
sys.stderr.write('  File "/home/alex/secret/path/ace_worker.py", line 18, in run\\n')
sys.stderr.write("RuntimeError: CUDA out of memory while loading /home/alex/secret/ckpt/song.pt\\n")
sys.exit(1)
'''


class OrphanRenderTests(unittest.TestCase):
    """C4: worker death must take the ACE process group with it."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.runtime_dir = tempfile.TemporaryDirectory()
        self.pidfile = Path(self.home.name) / 'ace.pid'
        self.runtime = make_runtime(self.runtime_dir.name)
        self.env = patch.dict(os.environ, {
            'HERMES_HOME': self.home.name,
            'ACE_ORPHAN_PIDFILE': str(self.pidfile)})
        self.env.start()
        self.pinned = pin_fixture_runtime(self.runtime)
        self.pinned.__enter__()
        self.store = service.store()
        self.project_id = self.store.create(caption='folk', lyrics='hello there',
                                            duration_seconds=10)['project_id']
        self.store.approve(self.project_id,
                           base_updated_at=self.store.get(self.project_id)['updated_at'])

    def tearDown(self):
        for project in self.store.all():
            for take in project['takes']:
                self._kill(take.get('ace_pid'))
                self._kill(take.get('pid'))
        self.pinned.close()
        self.env.stop()
        self.runtime_dir.cleanup()
        self.home.cleanup()

    def _kill(self, pid):
        if isinstance(pid, int) and pid > 0:
            from music_studio.procs import kill_tree
            try:
                kill_tree(pid)
            except OSError:
                pass

    def start_long_render(self):
        script = Path(self.runtime) / 'venv/bin/python'
        script.write_text(LONG_RENDER_SCRIPT)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        take = service.generate(self.project_id, 1)['take']
        take_id = take['take_id']
        self.assertTrue(_wait_until(lambda: self.pidfile.exists()), 'render never started')
        self.assertTrue(_wait_until(
            lambda: self.store.get(self.project_id)['takes'][0].get('pid')), 'worker never attached')
        take = self.store.get(self.project_id)['takes'][0]
        # The engine must have recorded the render it started, so a later poll
        # can reap it even when this worker died without cleanup.
        self.assertTrue(_wait_until(
            lambda: self.store.get(self.project_id)['takes'][0].get('ace_pid')),
            'worker never recorded its ACE render pid')
        take = self.store.get(self.project_id)['takes'][0]
        ace_pid = int(self.pidfile.read_text())
        self.assertEqual(take['ace_pid'], ace_pid)
        self.assertTrue(_wait_until(lambda: _proc_state(ace_pid) in ('R', 'S')), ace_pid)
        self.assertEqual(take_id, take['take_id'])
        return take, ace_pid

    def test_killed_worker_reaps_its_ace_render_on_the_next_status_poll(self):
        take, ace_pid = self.start_long_render()
        _hard_kill(take['pid'])  # OOM-killer / crash: no cleanup runs
        self.assertTrue(_wait_until(lambda: not process_alive(take['pid'], take['pid_start'])),
                        'killed worker still reads as alive')
        body = service.status()
        state = next(t for p in body['projects'] for t in p['takes']
                     if t['take_id'] == take['take_id'])
        self.assertEqual(state['state'], 'failed')
        self.assertEqual(state['error'], 'worker died')
        self.assertTrue(_wait_until(lambda: _proc_state(ace_pid) not in ('R', 'S', 'D'), limit=10),
                        f'ACE render {ace_pid} survived the death of worker {take["pid"]}')
        lease = self.store.worker_lock()
        self.assertIsNotNone(lease)
        lease.close()

    @unittest.skipIf(os.name == 'nt', 'Windows has no SIGTERM handler: terminating a worker is the '
                     'hard kill covered by the test above')
    def test_worker_terminated_mid_render_fails_the_take_and_kills_ace(self):
        take, ace_pid = self.start_long_render()
        os.kill(take['pid'], signal.SIGTERM)
        self.assertTrue(_wait_until(lambda: self.store.get(self.project_id)['takes'][0]['state']
                                    == 'failed'))
        failed = self.store.get(self.project_id)['takes'][0]
        self.assertIn('without committing', failed['error'])
        self.assertTrue(_wait_until(lambda: _proc_state(ace_pid) not in ('R', 'S', 'D'), limit=10),
                        f'ACE render {ace_pid} outlived an aborted take')
        lease = self.store.worker_lock()
        self.assertIsNotNone(lease, 'aborted take must release the lease')
        lease.close()


class ErrorSanitizationTests(unittest.TestCase):
    """C6: ACE output must be summarised, never dumped verbatim with paths."""

    def test_engine_error_is_one_line_without_absolute_paths(self):
        import ace_engine
        with tempfile.TemporaryDirectory() as runtime, tempfile.TemporaryDirectory() as out:
            make_runtime(runtime)
            script = Path(runtime) / 'venv/bin/python'
            script.write_text(TRACEBACK_SCRIPT)
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
            original = (ace_engine.RUNTIME, ace_engine.SOURCE, ace_engine.PYTHON, ace_engine.LOCK)
            self.addCleanup(lambda: (setattr(ace_engine, 'RUNTIME', original[0]),
                                     setattr(ace_engine, 'SOURCE', original[1]),
                                     setattr(ace_engine, 'PYTHON', original[2]),
                                     setattr(ace_engine, 'LOCK', original[3])))
            import importlib
            with patch.dict(os.environ, {'ACE_RUNTIME_DIR': runtime,
                                         'ACE_SOURCE_DIR': str(Path(runtime) / 'source')}):
                importlib.reload(ace_engine)
                try:
                    ace_engine.generate(caption='jazz', lyrics='hello', duration_seconds=10,
                                        seed=1, output_dir=out)
                except RuntimeError as exc:
                    message = str(exc)
                else:
                    self.fail('engine should have raised')
        self.assertIn('CUDA out of memory', message)
        self.assertNotIn('/home/alex/secret', message)
        self.assertNotIn('\n', message)
        self.assertLessEqual(len(message), 300)

    def test_take_error_never_ships_absolute_paths(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as runtime:
            make_runtime(runtime)
            script = Path(runtime) / 'venv/bin/python'
            script.write_text(TRACEBACK_SCRIPT)
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
            with patch.dict(os.environ, {'HERMES_HOME': home}), \
                    pin_fixture_runtime(Path(runtime)):
                store = service.store()
                pid = store.create(caption='music', lyrics='words',
                                   duration_seconds=10)['project_id']
                store.approve(pid, base_updated_at=store.get(pid)['updated_at'])
                take = service.generate(pid, 1)['take']
                self.assertTrue(_wait_until(
                    lambda: store.get(pid)['takes'][0]['state'] in ('failed', 'succeeded')))
                failed = store.get(pid)['takes'][0]
            self.assertEqual(failed['state'], 'failed')
            self.assertIn('CUDA out of memory', failed['error'])
            self.assertNotIn('/home/alex/secret', failed['error'])
            self.assertLessEqual(len(failed['error']), 300)


class EtaSmoothingTests(RouterTestCase):
    """C9: eta_seconds must fall smoothly, never jump up, for one take."""

    def test_eta_never_increases_and_never_jumps_by_minutes(self):
        pid = self.project()['project_id']
        self.store.approve(pid, base_updated_at=self.token(pid))
        jobs = Jobs(self.store)
        take = jobs.enqueue(pid, 1)
        jobs.start(pid, take['take_id'], pid=os.getpid())

        # started_at is recorded once by the worker, as in a real render.
        jobs.progress(pid, take['take_id'], started_at=time.time() - 14, progress=0)

        def poll(progress):
            jobs.progress(pid, take['take_id'], progress=progress)
            body = self.client.get('/status').json()
            return next(t for p in body['projects'] for t in p['takes']
                        if t['take_id'] == take['take_id'])['eta_seconds']

        first = poll(10)
        self.assertIsInstance(first, int)
        steps = [first]
        for progress in (10, 10, 25, 25, 45, 45):
            steps.append(poll(progress))
        for previous, current in zip(steps, steps[1:]):
            self.assertLessEqual(current, previous,
                                 f'eta increased between polls: {steps}')
            self.assertLessEqual(previous - current, max(15, previous * 0.35),
                                 f'eta jumped by minutes between polls: {steps}')


if __name__ == '__main__':
    unittest.main()
