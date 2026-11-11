"""T18: the user chooses the engine, end to end.

Pins the resolution order (explicit -> draft -> default), the two honest
refusals (unknown 400, unavailable 409 with the engine's own detail), legacy
draft compatibility ('ace' -> ace_local), and the capability gate for
source-conditioned operations on both the request path and in the worker.
"""
import io
import os
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from support import install_shared_runtime  # noqa: E402
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from dashboard.plugin_api import router  # noqa: E402
from music_studio import service, worker as studio_worker  # noqa: E402
from music_studio.engines import (Capabilities, Engine, EngineStatus,  # noqa: E402
                                  Limits, Registry)
from music_studio.jobs import Jobs  # noqa: E402
from music_studio.store import Store  # noqa: E402


def _tone(seconds=30.0, rate=8000):
    frames = int(seconds * rate)
    values = [int(4000 * (1 if i % 2 else -1)) for i in range(frames)]
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        out.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        out.writeframes(struct.pack(f'<{len(values)}h', *values))
    return buf.getvalue()


def setUpModule():
    global _RUNTIME_PIN
    _RUNTIME_PIN = install_shared_runtime()


def tearDownModule():
    _RUNTIME_PIN.close()


class StubEngine(Engine):
    kind = 'local'
    requirements = ()

    def __init__(self, engine_id, *, state='ready', detail='', repaint=True,
                 continuation=True, wav=None):
        super().__init__()
        self.id = engine_id
        self.label = f'{engine_id} label'
        self._state = state
        self._detail = detail
        self._caps = Capabilities(True, True, repaint, continuation, True)
        self.wav = wav or _tone()
        self.extra_metadata = {}
        self.renders = 0

    @property
    def limits(self):
        return Limits(10, 180, 30)

    @property
    def capabilities(self):
        return self._caps

    def health(self):
        return EngineStatus(engine_id=self.id, state=self._state,
                            detail=self._detail)

    def _render(self, request, on_progress=None, on_process=None):
        self.renders += 1
        metadata = {'engine_id': self.id, **self.extra_metadata}
        return type('R', (), {'wav': self.wav, 'metadata': metadata})()


class FakeProc:
    pid = os.getpid()


class EngineSelectionTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()
        self.ready = StubEngine('stub_ready')
        self.minimax = StubEngine('minimax_m3_comfy')
        self.down = StubEngine('stub_down', state='unavailable',
                               detail='ComfyUI refused connection at 192.0.2.52:8188')
        self.flat = StubEngine('stub_flat', repaint=False, continuation=False)
        self.ace = StubEngine('ace_local')
        self.registry = Registry([self.ready, self.minimax, self.down,
                                  self.flat, self.ace])
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)
        self.store = service.store()
        self.patches = [
            patch.object(service, 'registry', lambda s=None: self.registry),
            patch.object(service, '_spawn_worker', lambda *a, **k: FakeProc()),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.env.stop()
        self.temp.cleanup()

    def project(self, **draft):
        draft.setdefault('caption', 'music')
        draft.setdefault('lyrics', 'words')
        draft.setdefault('duration_seconds', 10)
        return self.store.create(**draft)

    def approve(self, pid):
        return self.store.approve(pid, base_updated_at=self.store.get(pid)['updated_at'])

    def generate(self, pid, engine_id=None, seed=7):
        body = {'project_id': pid, 'seed': seed}
        if engine_id is not None:
            body['engine_id'] = engine_id
        return self.client.post('/generate', json=body)


class GenerateResolutionTests(EngineSelectionTestCase):
    def test_explicit_engine_is_accepted_and_recorded_on_the_take(self):
        pid = self.project()['project_id']
        self.approve(pid)
        response = self.generate(pid, engine_id='minimax_m3_comfy')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['take']['engine_id'], 'minimax_m3_comfy')
        stored = self.store.get(pid)['takes'][0]
        self.assertEqual(stored['engine_id'], 'minimax_m3_comfy')

    def test_explicit_engine_wins_over_the_draft(self):
        pid = self.project(engine='minimax_m3_comfy')['project_id']
        self.approve(pid)
        response = self.generate(pid, engine_id='ace_local')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['take']['engine_id'], 'ace_local')

    def test_draft_engine_is_used_when_no_choice_is_sent(self):
        pid = self.project(engine='minimax_m3_comfy')['project_id']
        self.approve(pid)
        response = self.generate(pid)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['take']['engine_id'], 'minimax_m3_comfy')

    def test_legacy_ace_draft_still_renders_on_ace_local(self):
        pid = self.project(engine='ace')['project_id']
        self.approve(pid)
        response = self.generate(pid)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['take']['engine_id'], 'ace_local')

    def test_unknown_engine_is_400_and_enqueues_nothing(self):
        pid = self.project()['project_id']
        self.approve(pid)
        response = self.generate(pid, engine_id='does_not_exist')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('unknown engine: does_not_exist', response.json()['detail'])
        self.assertEqual(self.store.get(pid)['takes'], [])

    def test_unavailable_engine_is_409_with_its_own_detail_and_no_substitute(self):
        pid = self.project()['project_id']
        self.approve(pid)
        response = self.generate(pid, engine_id='stub_down')
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()['detail']
        self.assertIn('stub_down label', detail)
        self.assertIn('ComfyUI refused connection at 192.0.2.52:8188', detail)
        self.assertEqual(self.store.get(pid)['takes'], [],
                         'an unavailable choice must not fall back to another engine')
        self.assertEqual(self.ready.renders, 0)
        self.assertEqual(self.minimax.renders, 0)

    def test_unavailable_draft_engine_is_refused_not_substituted(self):
        pid = self.project(engine='ace_local')['project_id']
        self.approve(pid)
        down = StubEngine('ace_local', state='unavailable',
                          detail='local runtime missing')
        with patch.object(service, 'registry',
                          lambda s=None: Registry([down, self.ready])):
            response = self.generate(pid)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('local runtime missing', response.json()['detail'])
        self.assertEqual(self.store.get(pid)['takes'], [])


class StoreEngineValidationTests(unittest.TestCase):
    def test_any_registered_engine_is_a_legal_draft_value(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            for value in ('ace', 'ace_local', 'minimax_m3_comfy'):
                with self.subTest(value=value):
                    project = s.create(caption='c', lyrics='l', engine=value)
                    self.assertEqual(project['engine'], value)

    def test_unknown_engine_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            with self.assertRaisesRegex(ValueError, 'unsupported engine'):
                s.create(caption='c', lyrics='l', engine='not_an_engine')
            pid = s.create(caption='c', lyrics='l')['project_id']
            with self.assertRaisesRegex(ValueError, 'unsupported engine'):
                s.update(pid, engine='nope')


class CapabilityGateTests(EngineSelectionTestCase):
    def _parent(self, engine_id='stub_flat', seconds=30.0):
        pid = self.project()['project_id']
        self.approve(pid)
        jobs = Jobs(self.store)
        take = jobs.enqueue(pid, 9, engine_id=engine_id)
        jobs.start(pid, take['take_id'], pid=os.getpid())
        jobs.succeed(pid, take['take_id'], _tone(seconds))
        return pid, take['take_id']

    def test_repaint_on_a_non_repaint_engine_is_409_naming_engine_and_op(self):
        pid, parent = self._parent()
        response = self.client.post('/repaint', json={
            'project_id': pid, 'take_id': parent, 'start_s': 1.0, 'end_s': 5.0})
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()['detail']
        self.assertIn('stub_flat label', detail)
        self.assertIn('stub_flat', detail)
        self.assertIn('repaint', detail)
        self.assertEqual(len(self.store.get(pid)['takes']), 1, 'nothing was queued')

    def test_extend_on_a_non_continuation_engine_is_409(self):
        pid, parent = self._parent()
        response = self.client.post('/extend', json={
            'project_id': pid, 'take_id': parent, 'extend_seconds': 10})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('extend', response.json()['detail'])
        self.assertEqual(len(self.store.get(pid)['takes']), 1)

    def test_worker_rechecks_so_a_stale_take_cannot_bypass_the_gate(self):
        pid, parent = self._parent()
        jobs = Jobs(self.store)
        take = jobs.enqueue_repaint(pid, parent, 1.0, 5.0, 11)
        with patch.object(studio_worker, '_registry',
                          lambda store: Registry([self.flat])):
            code, _ = studio_worker._render_take(jobs, self.store, pid,
                                                 take['take_id'])
        self.assertEqual(code, studio_worker.EXIT_FAILED)
        failed = next(t for t in self.store.get(pid)['takes']
                      if t['take_id'] == take['take_id'])
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('does not support repaint', failed['error'])
        self.assertIsNone(failed['audio_file'])
        self.assertEqual(self.flat.renders, 0)

    def test_repaint_still_works_on_a_capable_engine(self):
        pid, parent = self._parent(engine_id='stub_ready')
        response = self.client.post('/repaint', json={
            'project_id': pid, 'take_id': parent, 'start_s': 1.0, 'end_s': 5.0})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['take']['engine_id'], 'stub_ready')


class StatusSelectableTests(EngineSelectionTestCase):
    def test_status_marks_selectable_engines_and_reasons(self):
        body = self.client.get('/status').json()
        by_id = {e['id']: e for e in body['engines']}
        self.assertTrue(by_id['stub_ready']['selectable'])
        self.assertTrue(by_id['stub_ready']['supports_generate'])
        self.assertIsNone(by_id['stub_ready']['selectable_reason'])
        self.assertFalse(by_id['stub_down']['selectable'])
        self.assertFalse(by_id['stub_down']['supports_generate'] is False,
                         'the engine can generate; it is merely down')
        self.assertEqual(by_id['stub_down']['selectable_reason'],
                         'ComfyUI refused connection at 192.0.2.52:8188')

    def test_an_engine_that_cannot_generate_is_not_selectable(self):
        self.ready.supports_generate = False
        body = self.client.get('/status').json()
        engine = next(e for e in body['engines'] if e['id'] == 'stub_ready')
        self.assertFalse(engine['selectable'])
        self.assertIn('generate', engine['selectable_reason'])


class WorkerMetadataTests(EngineSelectionTestCase):
    def test_engine_metadata_reaches_the_take_and_the_status_payload(self):
        notice = ('MiniMax Music 3 — governed by the MiniMax Community License; '
                  'outputs must carry this notice')
        self.minimax.extra_metadata = {
            'licence': 'MiniMax-Music3',
            'model_licence': 'MiniMax Community License',
            'licence_notice': notice,
        }
        pid = self.project()['project_id']
        self.approve(pid)
        jobs = Jobs(self.store)
        take = jobs.enqueue(pid, 7, engine_id='minimax_m3_comfy')
        with patch.object(studio_worker, '_registry', lambda s: self.registry):
            code, _ = studio_worker._render_take(jobs, self.store, pid, take['take_id'])
        self.assertEqual(code, studio_worker.EXIT_OK)
        stored = next(t for t in self.store.get(pid)['takes']
                      if t['take_id'] == take['take_id'])
        self.assertEqual(stored['state'], 'succeeded')
        self.assertEqual(stored['metadata']['licence_notice'], notice)
        # /status is where a client reads a take; the notice must be there.
        payload = self.client.get('/status').json()
        served = next(t for project in payload['projects'] for t in project['takes']
                      if t['take_id'] == take['take_id'])
        self.assertEqual(served['metadata']['licence_notice'], notice)
        self.assertEqual(served['metadata']['engine_id'], 'minimax_m3_comfy')


if __name__ == '__main__':
    unittest.main()
