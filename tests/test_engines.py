"""T1: the engine contract, the registry's fallback policy and /status.

These tests pin three promises:

* every registered engine answers the same three questions (describe / health /
  render) with the same shapes, so no client hardcodes an engine;
* the registry fails closed — an explicit engine that is unavailable raises, it
  never renders on another one, and nothing synthesises audio;
* the existing generate / repaint / extend paths reach the engine *through* the
  registry, with the engine pinned on the take at enqueue time.
"""
import io
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from fake_ace import make_runtime, pin_runtime, runtime_env, wav_bytes  # noqa: E402
from dashboard.plugin_api import router  # noqa: E402
from music_studio import service, store as studio_store  # noqa: E402
from music_studio.engines import (AceLocalEngine, AceRemoteEngine, Capabilities,  # noqa: E402
                                  Engine, EngineStatus, EngineUnavailable, KINDS, Limits,
                                  MiniMaxM3ComfyEngine, NoEngineAvailable, Registry,
                                  RenderRequest, RenderResult, RequestInvalid,
                                  default_engines, resolve_id, MODES)
from music_studio.engines.contract import STATES  # noqa: E402
from music_studio.jobs import Jobs  # noqa: E402
from music_studio import worker as studio_worker  # noqa: E402

ENGINE_BLOCK_KEYS = {'id', 'label', 'kind', 'available', 'supports_generate',
                     'selectable', 'selectable_reason', 'requirements',
                     'limits', 'capabilities', 'status'}
LIMIT_KEYS = {'min_seconds', 'max_seconds', 'recommended_seconds'}
CAPABILITY_KEYS = {'instrumental', 'vocal_language', 'repaint', 'continuation', 'batch'}


def _tone(seconds=12.0, rate=48000):
    """A real (non-silent) mono WAV, so stitching and length checks are meaningful."""
    import math
    import struct
    frames = int(seconds * rate)
    values = [int(12000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(frames)]
    # Stereo 48 kHz, like the real engine's output: the long-form stitcher (used
    # by extend) validates that exact layout.
    interleaved = [value for value in values for _ in (0, 1)]
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        out.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        out.writeframes(struct.pack(f'<{len(interleaved)}h', *interleaved))
    return buf.getvalue()


class _StubEngine(Engine):
    """A real contract implementation that returns fixed, real WAV bytes."""

    id = 'stub'
    label = 'Stub'
    kind = 'local'
    requirements = ()

    def __init__(self, *, state='ready', wav=None, capabilities=None):
        super().__init__()
        self._state = state
        self.wav = wav
        self.renders = 0
        self._caps = capabilities or Capabilities(True, True, True, True, True)

    @property
    def limits(self):
        return Limits(min_seconds=10, max_seconds=180, recommended_seconds=30)

    @property
    def capabilities(self):
        return self._caps

    def health(self):
        return EngineStatus(self.id, self._state, '')

    def _render(self, request, on_progress=None, on_process=None):
        self.renders += 1
        return RenderResult(self.wav, {'engine_id': self.id})


def _request(**kw):
    fields = dict(caption='folk', lyrics='hello there', duration_seconds=12, seed=1,
                  output_dir=None)
    fields.update(kw)
    return RenderRequest(**fields)


class ContractConformanceTests(unittest.TestCase):
    def setUp(self):
        # Health must not depend on whether *this* host can reach Spark E.
        self.env = patch.dict(os.environ, {'COMFYUI_URL': 'http://127.0.0.1:9'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_every_registered_engine_answers_the_same_shape(self):
        engines = default_engines()
        self.assertEqual([e.id for e in engines],
                         ['ace_local', 'ace_remote', 'minimax_m3_comfy'])
        self.assertEqual(len({e.id for e in engines}), len(engines), 'ids must be unique')
        for engine in engines:
            described = engine.describe()
            self.assertEqual(set(described), ENGINE_BLOCK_KEYS, engine.id)
            self.assertIn(described['kind'], KINDS, engine.id)
            self.assertIsInstance(described['available'], bool, engine.id)
            self.assertEqual(set(described['limits']), LIMIT_KEYS, engine.id)
            limits = engine.limits
            self.assertLessEqual(limits.min_seconds, limits.recommended_seconds)
            self.assertLessEqual(limits.recommended_seconds, limits.max_seconds)
            self.assertEqual(set(described['capabilities']), CAPABILITY_KEYS, engine.id)
            for value in described['capabilities'].values():
                self.assertIsInstance(value, bool, engine.id)
            status = described['status']
            self.assertIn(status['state'], STATES, engine.id)
            self.assertEqual(status['engine_id'], engine.id)
            # describe() must not be a promise of capability: the health state
            # is what decides availability.
            self.assertEqual(described['available'], status['available'], engine.id)

    def test_local_engine_limits_are_the_product_duration_contract(self):
        """The adapter may not advertise a window the store would reject."""
        engine = AceLocalEngine()
        self.assertEqual(engine.limits.as_dict(), studio_store.limits())

    def test_local_capabilities_cover_the_shipped_render_kinds(self):
        caps = AceLocalEngine().capabilities
        self.assertTrue(caps.instrumental and caps.vocal_language)
        self.assertTrue(caps.repaint and caps.continuation)
        # No adapter renders a batch in one call — the studio's batch is a
        # worker looping over takes — so no engine may advertise one.
        self.assertFalse(caps.batch)

    def test_local_render_without_an_output_dir_uses_the_system_temp_dir(self):
        import ace_engine
        seen = {}

        def fake_generate(**kwargs):
            seen.update(kwargs)
            return _tone(1.0)

        with patch.object(ace_engine, 'generate', side_effect=fake_generate):
            result = AceLocalEngine()._render(_request(output_dir=None))
        self.assertEqual(seen['output_dir'], tempfile.gettempdir())
        self.assertEqual(result.wav, _tone(1.0))
        self.assertEqual(result.metadata['engine_id'], 'ace_local')

    def test_request_modes_are_the_studio_kinds(self):
        self.assertEqual(MODES, ('generate', 'repaint', 'extend'))

    def test_legacy_draft_engine_name_resolves_to_a_real_engine(self):
        self.assertEqual(resolve_id('ace'), 'ace_local')
        self.assertEqual(resolve_id('ace_local'), 'ace_local')
        self.assertIsNone(resolve_id(None))

    def test_remote_engines_are_honestly_unavailable_without_a_server(self):
        # Pinned to a closed local port: the test must not depend on a LAN host
        # being down (and must not take its timeout when it is up).
        for engine in (AceRemoteEngine(base_url='http://127.0.0.1:9', timeout=0.5),
                       MiniMaxM3ComfyEngine(base_url='http://127.0.0.1:9', timeout=0.5)):
            status = engine.health()
            self.assertFalse(status.available, engine.id)
            self.assertIn(status.state, ('unavailable', 'error'), engine.id)
            self.assertTrue(status.detail.strip(), engine.id)
            # ...and rendering refuses instead of inventing audio.
            with self.assertRaises(EngineUnavailable):
                engine.render(_request(output_dir=tempfile.gettempdir()))

    def test_repaint_is_refused_by_an_engine_without_the_capability(self):
        stub = _StubEngine(capabilities=Capabilities(True, True, False, False, True))
        with self.assertRaises(RequestInvalid):
            stub.render(_request(mode='repaint', source_wav=_tone(), range_start_s=1.0,
                                 range_end_s=5.0))


class RegistryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.wav = _tone()
        self.down = _StubEngine(state='unavailable', wav=self.wav)
        self.down.id = 'stub_down'
        self.ready = _StubEngine(wav=self.wav)
        self.ready.id = 'stub_ready'
        self.preferences = tempfile.TemporaryDirectory()
        self.addCleanup(self.preferences.cleanup)
        self.pref_path = Path(self.preferences.name) / 'engine-preference.json'

    def registry(self, engines=None):
        return Registry(engines or [self.down, self.ready],
                        preference_path=self.pref_path)

    def test_explicit_unavailable_engine_never_falls_back(self):
        reg = self.registry()
        with self.assertRaises(EngineUnavailable):
            reg.render(_request(), engine_id='stub_down')
        self.assertEqual(self.ready.renders, 0, 'no silent substitution')
        self.assertEqual(self.down.renders, 0, 'and no invented audio')

    def test_unknown_engine_id_is_refused(self):
        with self.assertRaises(EngineUnavailable):
            self.registry().render(_request(), engine_id='nope')

    def test_fallback_happens_only_when_no_engine_is_named_and_says_so(self):
        reg = self.registry()
        reg.set_preference('stub_down', fallback=True)
        result = reg.render(_request())
        self.assertEqual(result.metadata['engine_id'], 'stub_ready')
        self.assertEqual(result.metadata['requested_engine'], 'stub_down')
        self.assertTrue(result.metadata['fell_back'])
        self.assertIn('stub_down', result.metadata['selection_reason'])

    def test_fallback_disabled_means_no_render(self):
        reg = self.registry()
        reg.set_preference('stub_down', fallback=False)
        with self.assertRaises(NoEngineAvailable):
            reg.render(_request())
        self.assertEqual(self.ready.renders, 0)

    def test_no_available_engine_fails_closed_with_every_state(self):
        reg = self.registry([self.down])
        with self.assertRaises(NoEngineAvailable) as caught:
            reg.render(_request())
        self.assertIn('stub_down', caught.exception.statuses)

    def test_status_payload_names_every_engine_and_one_default(self):
        reg = self.registry()
        payload = reg.status_payload()
        self.assertEqual([e['id'] for e in payload['engines']],
                         ['stub_down', 'stub_ready'])
        self.assertEqual(payload['default_engine_id'], 'stub_ready')

    def test_default_stays_the_preference_when_nothing_is_available(self):
        reg = self.registry([self.down])
        reg.set_preference('stub_down')
        self.assertEqual(reg.default_engine_id(), 'stub_down')


class StatusPayloadTests(unittest.TestCase):
    """GET /status carries the engine list; a client never hardcodes it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime_dir = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'HERMES_HOME': self.temp.name,
            # /status and the worker must not depend on a live ComfyUI on the LAN.
            'COMFYUI_URL': 'http://127.0.0.1:9',
        })
        self.env.start()
        self.runtime = make_runtime(self.runtime_dir.name)
        # /status probes ace_engine in-process, so the env override below (which
        # only reaches child processes) is not enough: pin the module's live
        # paths to the fixture. Otherwise the answer depends on whether *this*
        # host has a real ACE install.
        self.pinned = pin_runtime(self.runtime)
        self.engine_env = patch.dict(os.environ, runtime_env(self.runtime))
        self.engine_env.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.pinned.close()
        self.engine_env.stop()
        self.env.stop()
        self.runtime_dir.cleanup()
        self.temp.cleanup()

    def test_status_exposes_the_engine_contract_and_the_default(self):
        body = self.client.get('/status').json()
        self.assertTrue(body['success'])
        engines = body['engines']
        self.assertEqual({e['id'] for e in engines},
                         {'ace_local', 'ace_remote', 'minimax_m3_comfy'})
        for engine in engines:
            self.assertEqual(set(engine), ENGINE_BLOCK_KEYS, engine['id'])
            self.assertEqual(set(engine['limits']), LIMIT_KEYS)
            self.assertEqual(set(engine['capabilities']), CAPABILITY_KEYS)
            for value in engine['capabilities'].values():
                self.assertIsInstance(value, bool)
        by_id = {e['id']: e for e in engines}
        # The fake runtime exists in this test, so the local engine is the only
        # one that can honestly claim availability.
        self.assertTrue(by_id['ace_local']['available'])
        self.assertFalse(by_id['ace_remote']['available'])
        self.assertFalse(by_id['minimax_m3_comfy']['available'])
        self.assertEqual(body['default_engine_id'], 'ace_local')
        self.assertIn(body['default_engine_id'], by_id)
        self.assertTrue(body['engine_available'])
        # The pre-registry field is still served for old clients.
        self.assertIsInstance(body['engine_available'], bool)

    def test_local_engine_is_honestly_unavailable_without_a_runtime(self):
        """No runtime ⇒ unavailable, with the contract still served in full."""
        with tempfile.TemporaryDirectory() as empty:
            missing = pin_runtime(empty)
            try:
                body = self.client.get('/status').json()
            finally:
                missing.close()
        self.assertTrue(body['success'])
        engines = {e['id']: e for e in body['engines']}
        self.assertFalse(engines['ace_local']['available'])
        self.assertEqual(engines['ace_local']['status']['state'], 'unavailable')
        # The message must tell a person what to do; the exact wording moved from "not found" to
        # "not configured", which is the honest state when no paths are set at all.
        detail = engines['ace_local']['status']['detail'].lower()
        self.assertTrue('not configured' in detail or 'not found' in detail, detail)
        for engine in engines.values():
            self.assertEqual(set(engine), ENGINE_BLOCK_KEYS)
            self.assertEqual(set(engine['limits']), LIMIT_KEYS)
        self.assertIn(body['default_engine_id'], engines)
        self.assertFalse(body['engine_available'])

    def test_engine_list_is_not_a_hardcoded_client_contract(self):
        """A changed registry changes /status without touching the API layer."""
        fake = _StubEngine(wav=_tone())
        fake.id = 'only_engine'
        with patch.object(service, 'registry', lambda s=None: Registry([fake])):
            body = service.status()
        self.assertEqual([e['id'] for e in body['engines']], ['only_engine'])
        self.assertEqual(body['default_engine_id'], 'only_engine')


class RegistryRenderPathTests(unittest.TestCase):
    """The real worker path: enqueue pins the engine, the worker renders through
    the registry, and repaint/extend stay on the same engine."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime_dir = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'HERMES_HOME': self.temp.name,
            'COMFYUI_URL': 'http://127.0.0.1:9',
        })
        self.env.start()
        self.runtime = make_runtime(self.runtime_dir.name)
        # /generate probes the local engine in this process, where ace_engine's
        # paths were frozen at import: pin them to the fixture runtime.
        self.pinned = pin_runtime(self.runtime)
        self.pinned.__enter__()
        self.engine_env = patch.dict(os.environ, runtime_env(self.runtime))
        self.engine_env.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.store = service.store()
        self.pid = self.store.create(caption='folk', lyrics='[verse]\nhello',
                                     duration_seconds=10)['project_id']
        self.store.approve(self.pid)
        self.jobs = Jobs(self.store)

    def tearDown(self):
        self.pinned.close()
        self.engine_env.stop()
        self.env.stop()
        self.runtime_dir.cleanup()
        self.temp.cleanup()

    def wait(self, take_id, limit=40):
        deadline = time.time() + limit
        while time.time() < deadline:
            for project in self.client.get('/status').json()['projects']:
                for take in project['takes']:
                    if take['take_id'] == take_id and take['state'] in ('succeeded', 'failed'):
                        # A succeeded take is committed before its worker exits;
                        # wait for the GPU lease too, or the next request races
                        # the exiting worker (the same window the batch does).
                        self.wait_for_lease()
                        return take
            time.sleep(.02)
        self.fail('worker did not finish')

    def wait_for_lease(self, limit=10):
        deadline = time.time() + limit
        while time.time() < deadline:
            lease = self.store.worker_lock()
            if lease is not None:
                lease.close()
                return
            time.sleep(.02)
        self.fail('worker never released the GPU lease')

    def test_generate_pins_the_approved_engine_on_the_take(self):
        response = self.client.post('/generate', json={'project_id': self.pid, 'seed': 3})
        self.assertEqual(response.status_code, 200)
        take_id = response.json()['take']['take_id']
        take = self.wait(take_id)
        self.assertEqual(take['state'], 'succeeded', take.get('error'))
        self.assertEqual(take['engine_id'], 'ace_local')
        self.assertEqual(self.jobs.read_audio(self.pid, take_id), wav_bytes())

    def test_repaint_and_extend_stay_on_the_parents_engine(self):
        # A real-tone parent: the fake runtime's 10 ms take is too short to
        # repaint (3 s minimum) or extend (20 s minimum).
        parent_id = self._seeded_parent(30.0)
        repaint = self.client.post('/repaint', json={
            'project_id': self.pid, 'take_id': parent_id,
            'start_s': 10.0, 'end_s': 20.0})
        self.assertEqual(repaint.status_code, 200, repaint.text)
        child = self.wait(repaint.json()['take']['take_id'])
        self.assertEqual(child['state'], 'succeeded', child.get('error'))
        self.assertEqual(child['engine_id'], 'ace_local')
        self.assertEqual(child['kind'], 'repaint')
        extend = self.client.post('/extend', json={
            'project_id': self.pid, 'take_id': parent_id, 'extend_seconds': 10})
        self.assertEqual(extend.status_code, 200, extend.text)
        grown = self.wait(extend.json()['take']['take_id'])
        self.assertEqual(grown['state'], 'succeeded', grown.get('error'))
        self.assertEqual(grown['engine_id'], 'ace_local')
        self.assertEqual(grown['kind'], 'extension')
        # The parent is never mutated by either child.
        self.assertEqual(self.jobs.read_audio(self.pid, parent_id), _tone(30.0))

    def _seeded_parent(self, seconds):
        """A succeeded parent take rendered by ace_local, as the studio records it."""
        take = self.jobs.enqueue(self.pid, 9, engine_id='ace_local')
        self.jobs.start(self.pid, take['take_id'], pid=__import__('os').getpid())
        self.jobs.succeed(self.pid, take['take_id'], _tone(seconds))
        return take['take_id']

    def test_a_take_pinned_to_an_unavailable_engine_fails_without_audio(self):
        take = self.jobs.enqueue(self.pid, 6, engine_id='ace_remote')
        code, _ = studio_worker._render_take(self.jobs, self.store, self.pid,
                                            take['take_id'])
        self.assertEqual(code, studio_worker.EXIT_FAILED)
        failed = next(t for t in self.store.get(self.pid)['takes']
                      if t['take_id'] == take['take_id'])
        self.assertEqual(failed['state'], 'failed')
        self.assertIsNone(failed['audio_file'])
        self.assertIn('unavailable', failed['error'].lower())
        self.assertFalse((self.store.root / f"{take['take_id']}.wav").exists())


if __name__ == '__main__':
    unittest.main()


def test_a_comfyui_engine_with_no_address_says_so_instead_of_guessing():
    """No machine default: an unset address is a state with an instruction, not a silent failure."""
    from music_studio.engines import minimax_m3_comfy

    engine = minimax_m3_comfy.MiniMaxM3ComfyEngine(base_url='', timeout=1.0)
    status = engine.health()
    assert status.state == 'unavailable'
    assert 'Settings' in status.detail and 'COMFYUI_URL' in status.detail


def test_ace_reports_why_it_cannot_render_instead_of_pointing_at_a_stranger_s_path():
    """No personal default: unset paths are a configuration step, not a silent '.'. """
    import ace_engine

    assert str(ace_engine.RUNTIME) != '.'
    assert str(ace_engine.SOURCE) != '.'
    reason = ace_engine.unavailable_reason()
    assert 'ACE_RUNTIME_DIR' in reason or 'ACE-Step runtime' in reason
