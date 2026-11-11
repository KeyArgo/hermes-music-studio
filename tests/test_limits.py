"""The duration contract: store/engine caps, self-describing /status limits, 400s.

The cap is evidence-backed (see CHANGELOG): ACE-Step itself buckets audio up to
360 s, but this GPU rendered 180 s takes, so 180 is the product ceiling. These
tests pin the numbers, the boundary behaviour and the client-facing payload so a
client never has to hardcode a duration of its own.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from support import install_shared_runtime  # noqa: E402
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import ace_engine  # noqa: E402
from dashboard.plugin_api import router  # noqa: E402
from music_studio import service  # noqa: E402
from music_studio.jobs import Jobs  # noqa: E402
from music_studio.store import (DURATION_MAX_SECONDS, DURATION_MIN_SECONDS,  # noqa: E402
                                RECOMMENDED_DURATION_SECONDS, RENDER_BASE_SECONDS,
                                RENDER_SECONDS_PER_AUDIO_SECOND, Store,
                                estimate_render_seconds, limits, render_timeout_seconds)

store_model = (RENDER_BASE_SECONDS, RENDER_SECONDS_PER_AUDIO_SECOND)

# The worst 180 s render actually observed through the plugin path, with another
# render sharing the GPU (2026-11-07). The kill-switch must be >= 3x this.
OBSERVED_180_SECONDS = 553.5


def _draft(**kw):
    draft = {'caption': 'folk', 'lyrics': 'hello there'}
    draft.update(kw)
    return draft


def setUpModule():
    global _RUNTIME_PIN
    _RUNTIME_PIN = install_shared_runtime()


def tearDownModule():
    _RUNTIME_PIN.close()


class StoreDurationCapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_contract_numbers_are_the_measured_ceiling(self):
        self.assertEqual(DURATION_MIN_SECONDS, 10)
        self.assertEqual(DURATION_MAX_SECONDS, 180)
        self.assertTrue(DURATION_MIN_SECONDS < RECOMMENDED_DURATION_SECONDS
                        <= DURATION_MAX_SECONDS)

    def test_boundaries_are_accepted(self):
        for duration in (DURATION_MIN_SECONDS, 60, RECOMMENDED_DURATION_SECONDS,
                         DURATION_MAX_SECONDS):
            with self.subTest(duration=duration):
                project = self.store.create(**_draft(duration_seconds=duration))
                self.assertEqual(project['duration_seconds'], duration)
                self.assertTrue(self.store.approve(project['project_id'])['approved'])

    def test_out_of_range_is_rejected_with_the_range_in_the_message(self):
        for duration in (DURATION_MIN_SECONDS - 1, DURATION_MAX_SECONDS + 1,
                         0, 600, True, 120.5):
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError) as caught:
                    self.store.create(**_draft(duration_seconds=duration))
                message = str(caught.exception)
                self.assertIn('duration', message)
                self.assertIn(str(DURATION_MIN_SECONDS), message)
                self.assertIn(str(DURATION_MAX_SECONDS), message)

    def test_approval_rejects_a_legacy_over_cap_draft(self):
        project = self.store.create(**_draft(duration_seconds=DURATION_MAX_SECONDS))
        project['duration_seconds'] = DURATION_MAX_SECONDS + 1
        self.store._save(project)
        with self.assertRaises(ValueError):
            self.store.approve(project['project_id'])
        self.assertFalse(self.store.get(project['project_id'])['approved'])

    def test_update_cannot_raise_a_draft_past_the_cap(self):
        project = self.store.create(**_draft(duration_seconds=60))
        with self.assertRaises(ValueError):
            self.store.update(project['project_id'],
                              duration_seconds=DURATION_MAX_SECONDS + 1)
        # The rejected update left the stored draft untouched.
        self.assertEqual(self.store.get(project['project_id'])['duration_seconds'], 60)


class EngineDurationCapTests(unittest.TestCase):
    def test_engine_mirrors_the_store_contract(self):
        # ace_engine duplicates these deliberately (it must import without the
        # package); a drift here means an approved draft could fail at render.
        self.assertEqual(ace_engine.DURATION_MIN_SECONDS, DURATION_MIN_SECONDS)
        self.assertEqual(ace_engine.DURATION_MAX_SECONDS, DURATION_MAX_SECONDS)
        self.assertEqual(ace_engine.RENDER_BASE_SECONDS, 125.0)
        self.assertEqual(ace_engine.RENDER_SECONDS_PER_AUDIO_SECOND, 2.4)
        self.assertEqual(ace_engine.RENDER_BASE_SECONDS, store_model[0])
        self.assertEqual(ace_engine.RENDER_SECONDS_PER_AUDIO_SECOND, store_model[1])

    def test_generate_rejects_out_of_range_before_spawning(self):
        with tempfile.TemporaryDirectory() as d:
            for duration in (DURATION_MAX_SECONDS + 1, DURATION_MIN_SECONDS - 1):
                with self.subTest(duration=duration):
                    with self.assertRaisesRegex(ValueError, 'duration_seconds'):
                        ace_engine.generate(caption='folk', lyrics='hello',
                                            duration_seconds=duration, seed=1,
                                            output_dir=d)

    def test_timeout_is_at_least_three_times_the_modelled_render(self):
        for duration in (DURATION_MIN_SECONDS, 60, 120, DURATION_MAX_SECONDS):
            with self.subTest(duration=duration):
                self.assertGreaterEqual(render_timeout_seconds(duration),
                                        3 * estimate_render_seconds(duration))
                self.assertGreaterEqual(render_timeout_seconds(duration), 180)
        # A longer take gets a longer lease than a short one.
        self.assertLess(render_timeout_seconds(60),
                        render_timeout_seconds(DURATION_MAX_SECONDS))

    def test_timeout_covers_three_times_the_worst_observed_long_render(self):
        self.assertGreaterEqual(render_timeout_seconds(DURATION_MAX_SECONDS),
                                3 * OBSERVED_180_SECONDS)
        # ... and the engine's own ceiling accepts the computed lease.
        self.assertLessEqual(render_timeout_seconds(DURATION_MAX_SECONDS), 3600)

    def test_eta_model_grows_with_duration(self):
        self.assertLess(estimate_render_seconds(60), estimate_render_seconds(120))
        self.assertLess(estimate_render_seconds(120), estimate_render_seconds(180))
        self.assertEqual(ace_engine.estimate_render_seconds(DURATION_MAX_SECONDS),
                         estimate_render_seconds(DURATION_MAX_SECONDS))


class LimitsPayloadTests(unittest.TestCase):
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

    def test_status_returns_the_limits_contract(self):
        body = self.client.get('/status').json()
        self.assertEqual(body['limits'], {'min_seconds': DURATION_MIN_SECONDS,
                                          'max_seconds': DURATION_MAX_SECONDS,
                                          'recommended_seconds':
                                              RECOMMENDED_DURATION_SECONDS})
        # Exactly the documented keys: clients validate this shape.
        self.assertEqual(set(body['limits']), {'min_seconds', 'max_seconds',
                                               'recommended_seconds'})
        self.assertEqual(limits(), body['limits'])

    def test_create_above_the_cap_is_a_clear_400(self):
        response = self.client.post('/create', json={'draft': _draft(
            duration_seconds=DURATION_MAX_SECONDS + 1)})
        self.assertEqual(response.status_code, 400)
        detail = response.json()['detail']
        self.assertIn('duration', detail)
        self.assertIn(str(DURATION_MAX_SECONDS), detail)
        self.assertEqual(self.store.all(), [])

    def test_create_below_the_floor_is_a_clear_400(self):
        response = self.client.post('/create', json={'draft': _draft(
            duration_seconds=DURATION_MIN_SECONDS - 1)})
        self.assertEqual(response.status_code, 400)
        self.assertIn('duration', response.json()['detail'])

    def test_create_at_the_new_cap_succeeds(self):
        response = self.client.post('/create', json={'draft': _draft(
            duration_seconds=DURATION_MAX_SECONDS)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['project']['duration_seconds'],
                         DURATION_MAX_SECONDS)

    def test_update_above_the_cap_is_a_clear_400(self):
        project = self.store.create(**_draft(duration_seconds=60))
        response = self.client.post('/update', json={
            'project_id': project['project_id'],
            'draft': {'duration_seconds': DURATION_MAX_SECONDS + 1}})
        self.assertEqual(response.status_code, 400)
        self.assertIn('duration', response.json()['detail'])
        self.assertEqual(self.store.get(project['project_id'])['duration_seconds'], 60)


class EngineAwareLimitsTests(unittest.TestCase):
    """Validation must use the limits of the engine the draft actually pins.

    The top-level /status numbers are the default engine's contract; a draft
    pinned to MiniMax Music 3 may span MiniMax's window, and the error for a
    refusal must quote that engine's real bound rather than ACE's.
    """

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

    def test_status_labels_the_top_level_limits_with_their_engine(self):
        body = self.client.get('/status').json()
        self.assertEqual(body['limits_engine_id'], 'ace_local')
        ace = next(e for e in body['engines'] if e['id'] == 'ace_local')
        self.assertEqual(body['limits'], ace['limits'])
        minimax = next(e for e in body['engines'] if e['id'] == 'minimax_m3_comfy')
        self.assertEqual(minimax['limits']['max_seconds'], 360)
        # Per-engine windows really differ: the top-level number is not a cap
        # every engine has to live inside.
        self.assertNotEqual(minimax['limits']['max_seconds'],
                            body['limits']['max_seconds'])

    def test_a_draft_pinned_to_minimax_accepts_240_seconds(self):
        response = self.client.post('/create', json={'draft': _draft(
            engine='minimax_m3_comfy', duration_seconds=240)})
        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()['project']
        self.assertEqual(project['duration_seconds'], 240)

    def test_a_240_second_draft_on_ace_is_refused_with_aces_real_bound(self):
        response = self.client.post('/create', json={'draft': _draft(
            engine='ace_local', duration_seconds=240)})
        self.assertEqual(response.status_code, 400)
        detail = response.json()['detail']
        self.assertIn('duration', detail)
        self.assertIn(str(DURATION_MAX_SECONDS), detail)
        self.assertEqual(self.store.all(), [])

    def test_the_legacy_engine_name_resolves_to_the_same_bound(self):
        response = self.client.post('/create', json={'draft': _draft(
            engine='ace', duration_seconds=240)})
        self.assertEqual(response.status_code, 400)
        self.assertIn(str(DURATION_MAX_SECONDS), response.json()['detail'])

    def test_minimax_maximum_boundary_is_accepted_on_both_paths(self):
        created = self.client.post('/create', json={'draft': _draft(
            engine='minimax_m3_comfy', duration_seconds=360)})
        self.assertEqual(created.status_code, 200, created.text)
        project = created.json()['project']
        approved = self.client.post('/approve', json={
            'project_id': project['project_id'],
            'base_updated_at': project['updated_at']})
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertTrue(approved.json()['project']['approved'])
        over = self.client.post('/create', json={'draft': _draft(
            engine='minimax_m3_comfy', duration_seconds=361)})
        self.assertEqual(over.status_code, 400)
        self.assertIn('360', over.json()['detail'])

    def test_an_explicit_engine_override_is_checked_too(self):
        """A draft legal on MiniMax cannot be queued onto ACE at 240 s."""
        project = self.client.post('/create', json={'draft': _draft(
            engine='minimax_m3_comfy', duration_seconds=240)}).json()['project']
        self.client.post('/approve', json={
            'project_id': project['project_id'],
            'base_updated_at': project['updated_at']})
        with patch.object(service, 'resolve_engine', return_value='ace_local'):
            with self.assertRaisesRegex(ValueError, '10 and 180'):
                service.generate(project['project_id'], 1)
        # Nothing was queued.
        self.assertEqual(self.store.get(project['project_id'])['takes'], [])

    def test_a_duration_only_update_is_checked_against_the_pinned_engine(self):
        project = self.client.post('/create', json={'draft': _draft(
            engine='minimax_m3_comfy', duration_seconds=60)}).json()['project']
        raised = self.client.post('/update', json={
            'project_id': project['project_id'],
            'draft': {'duration_seconds': 240}})
        self.assertEqual(raised.status_code, 200, raised.text)
        self.assertEqual(raised.json()['project']['duration_seconds'], 240)

        ace = self.client.post('/create', json={'draft': _draft(
            engine='ace', duration_seconds=60)}).json()['project']
        refused = self.client.post('/update', json={
            'project_id': ace['project_id'],
            'draft': {'duration_seconds': 240}})
        self.assertEqual(refused.status_code, 400)
        self.assertIn(str(DURATION_MAX_SECONDS), refused.json()['detail'])
        self.assertEqual(self.store.get(ace['project_id'])['duration_seconds'], 60)


class PerDurationEtaTests(unittest.TestCase):
    """A long take must be priced by its own duration, not by a 60 s default."""

    def _running_eta(self, duration, elapsed):
        # One render per profile: a fresh data dir keeps the two measurements
        # from tripping the single-render ('GPU busy') guard.
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {'HERMES_HOME': home}):
                store = service.store()
                project = store.create(caption='folk', lyrics='hello there',
                                       duration_seconds=duration)
                store.approve(project['project_id'])
                jobs = Jobs(store)
                take = jobs.enqueue(project['project_id'], 1)
                jobs.start(project['project_id'], take['take_id'], pid=os.getpid())
                jobs.progress(project['project_id'], take['take_id'],
                              started_at=time.time() - elapsed, progress=0)
                body = service.status()
                decorated = next(t for p in body['projects'] for t in p['takes']
                                 if t['take_id'] == take['take_id'])
                return decorated['eta_seconds']

    def test_long_take_eta_scales_with_its_duration(self):
        short = self._running_eta(60, 10)
        long = self._running_eta(DURATION_MAX_SECONDS, 10)
        self.assertIsInstance(short, int)
        self.assertIsInstance(long, int)
        # Same elapsed time, three times the audio: the long ETA must be large.
        self.assertGreater(long, short)
        self.assertGreaterEqual(long, estimate_render_seconds(DURATION_MAX_SECONDS) - 11)


if __name__ == '__main__':
    unittest.main()
