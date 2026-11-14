"""T-doctor: doctor() explains every engine's setup and never raises.

One row per registered engine, per-check detail straight from the engine's own
health probe, a health() blow-up turned into an 'error' row (doctor continues),
and a fix hint that is None until a verified command exists for it.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import install_shared_runtime  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from music_studio import service  # noqa: E402
from music_studio.engines import (Capabilities, Engine, EngineStatus,  # noqa: E402
                                  HealthCheck, Limits, Registry, default_engines)


class DoctorEngine(Engine):
    kind = 'local'
    requirements = ()

    def __init__(self, engine_id, *, state='ready', detail='', checks=None,
                 health_raises=None):
        super().__init__()
        self.id = engine_id
        self.label = f'{engine_id} label'
        self._state = state
        self._detail = detail
        self._checks = checks or []
        self._health_raises = health_raises
        self._caps = Capabilities(True, True, True, True, False)

    @property
    def limits(self):
        return Limits(10, 180, 30)

    @property
    def capabilities(self):
        return self._caps

    def health(self):
        if self._health_raises is not None:
            raise self._health_raises
        return EngineStatus(engine_id=self.id, state=self._state,
                            detail=self._detail, checks=list(self._checks))

    def _render(self, request, on_progress=None, on_process=None):
        raise AssertionError('doctor must not render')


def setUpModule():
    global _RUNTIME_PIN
    _RUNTIME_PIN = install_shared_runtime()


def tearDownModule():
    _RUNTIME_PIN.close()


class DoctorTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def doctor_with(self, *engines):
        registry = Registry(list(engines))
        with patch.object(service, 'registry', lambda s=None: registry):
            return service.doctor()


class DoctorShapeTests(DoctorTestCase):
    def test_one_row_per_engine_with_contract_keys(self):
        rows = self.doctor_with(
            DoctorEngine('stub_ok', state='ready', detail='all good',
                         checks=[HealthCheck('venv', True, 'present')]),
            DoctorEngine('stub_down', state='unavailable',
                         detail='no model dir',
                         checks=[HealthCheck('venv', True, 'present'),
                                 HealthCheck('model', False, 'missing dir')]))
        self.assertEqual([row['id'] for row in rows], ['stub_ok', 'stub_down'])
        ok, down = rows
        for row in (ok, down):
            self.assertEqual(set(row), {'id', 'label', 'ready', 'state',
                                        'detail', 'checks', 'fix'})
            self.assertIsInstance(row['ready'], bool)
            self.assertIsInstance(row['detail'], str)
            self.assertIsInstance(row['checks'], list)
        self.assertTrue(ok['ready'])
        self.assertEqual(ok['state'], 'ready')
        self.assertEqual(ok['detail'], 'all good')
        self.assertEqual(ok['checks'], [{'name': 'venv', 'ok': True,
                                         'detail': 'present'}])
        self.assertFalse(down['ready'])
        self.assertEqual(down['state'], 'unavailable')
        self.assertEqual(down['detail'], 'no model dir')
        self.assertEqual(down['checks'],
                         [{'name': 'venv', 'ok': True, 'detail': 'present'},
                          {'name': 'model', 'ok': False, 'detail': 'missing dir'}])

    def test_engine_with_raising_health_is_marked_error_and_doctor_continues(self):
        rows = self.doctor_with(
            DoctorEngine('stub_boom', health_raises=RuntimeError('no venv')),
            DoctorEngine('stub_after', state='ready', detail='fine'))
        boom, after = rows
        self.assertEqual(boom['id'], 'stub_boom')
        self.assertFalse(boom['ready'])
        self.assertEqual(boom['state'], 'error')
        self.assertEqual(boom['detail'], 'health check raised RuntimeError: no venv')
        self.assertEqual(boom['checks'], [])
        self.assertEqual(after['id'], 'stub_after')
        self.assertTrue(after['ready'])
        self.assertEqual(after['state'], 'ready')

    def test_fix_is_none_for_every_engine(self):
        rows = self.doctor_with(
            DoctorEngine('stub_ok'),
            DoctorEngine('stub_boom', health_raises=OSError('gone')))
        self.assertEqual([row['fix'] for row in rows], [None, None])

    def test_every_registered_engine_has_a_plain_language_fix(self):
        engine_ids = [engine.id for engine in default_engines()]
        self.assertTrue(engine_ids)
        for engine_id in engine_ids:
            fix = service.FIXES.get(engine_id)
            self.assertIsInstance(fix, str, engine_id)
            self.assertTrue(fix.strip(), engine_id)


if __name__ == '__main__':
    unittest.main()
