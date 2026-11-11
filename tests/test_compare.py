"""One approved draft, two engines, two takes — the comparison the studio used to make people repeat by hand."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import compare  # noqa: E402


class FakeEngine:
    def __init__(self, engine_id, state='ready', generate=True):
        self.id = engine_id
        self.label = engine_id.upper()
        self.kind = 'local'
        self._state = state
        self.capabilities = type('C', (), {'generate': generate})()

    def health(self):
        return type('S', (), {'state': self._state, 'detail': 'test'})()


class FakeRegistry:
    """The registry surface compare actually uses: iterate engines, ask each for health."""

    def __init__(self, engines):
        self.engines = list(engines)

    def __iter__(self):
        return iter(self.engines)

    def health(self, engine, fresh=False):
        return engine.health()


class UsableEngineTests(unittest.TestCase):
    def test_every_ready_engine_is_offered_not_a_fixed_pair(self):
        reg = FakeRegistry([FakeEngine('ace_local'), FakeEngine('minimax_m3_comfy'),
                            FakeEngine('some_future_engine')])
        self.assertEqual(compare.usable_engines(reg),
                         ['ace_local', 'minimax_m3_comfy', 'some_future_engine'])

    def test_an_unavailable_engine_is_listed_but_not_offered(self):
        reg = FakeRegistry([FakeEngine('ace_local'), FakeEngine('minimax_m3_comfy', 'unavailable')])
        listed = compare.available_engines(reg)
        self.assertEqual([e['engine_id'] for e in listed], ['ace_local', 'minimax_m3_comfy'])
        self.assertFalse(listed[1]['ready'])
        self.assertEqual(compare.usable_engines(reg), ['ace_local'])

    def test_a_self_describing_label_travels_with_the_engine(self):
        reg = FakeRegistry([FakeEngine('ace_local')])
        listed = compare.available_engines(reg)[0]
        self.assertEqual(listed['label'], 'ACE_LOCAL')
        self.assertEqual(listed['kind'], 'local')

    def test_one_ready_engine_is_not_a_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('music_studio.service.store') as store:
                store.return_value.get.return_value = {'approved': True}
                with patch.object(compare, 'usable_engines', lambda reg=None: ['ace_local']):
                    with self.assertRaises(ValueError) as raised:
                        compare.start('p1', data_dir=tmp)
        self.assertIn('two engines', str(raised.exception))


class StartTests(unittest.TestCase):
    def test_an_unapproved_draft_is_refused_before_anything_is_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('music_studio.service.store') as store:
                store.return_value.get.return_value = {'approved': False}
                with self.assertRaises(ValueError) as raised:
                    compare.start('p1', data_dir=tmp)
        self.assertIn('approval', str(raised.exception))

    def test_the_plan_names_each_engine_and_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('music_studio.service.store') as store, \
                    patch.object(compare, 'usable_engines', lambda reg=None: ['ace_local', 'minimax_m3_comfy']), \
                    patch.object(compare, 'service') as svc:
                svc.store.return_value.get.return_value = {'approved': True}
                svc.generate.side_effect = RuntimeError('no gpu in this test')
                plan = compare.start('p1', data_dir=tmp)
                self.assertEqual([r['engine_id'] for r in plan['engines']],
                                 ['ace_local', 'minimax_m3_comfy'])
                # The worker thread writes the plan; let it finish before the temp dir is removed,
                # or the cleanup races the write and fails on a not-empty directory.
                import time
                for _ in range(80):
                    if compare.read(tmp, 'p1').get('state') == 'done':
                        break
                    time.sleep(0.05)
                written = json.loads((Path(tmp) / compare.COMPARE_FILE.format(project='p1')).read_text())
                self.assertEqual(written['project_id'], 'p1')

    def test_a_failed_engine_does_not_stop_the_other(self):
        calls = []

        def fake_generate(project_id, seed, engine_id=None):
            calls.append(engine_id)
            raise RuntimeError(f'{engine_id} exploded')

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(compare, 'usable_engines', lambda reg=None: ['ace_local', 'minimax_m3_comfy']), \
                    patch.object(compare.service, 'store') as store, \
                    patch.object(compare.service, 'generate', fake_generate):
                store.return_value.get.return_value = {'approved': True}
                compare.start('p1', data_dir=tmp)
                for _ in range(60):
                    plan = compare.read(tmp, 'p1')
                    if plan.get('state') == 'done':
                        break
                    import time
                    time.sleep(0.05)
        self.assertEqual(calls, ['ace_local', 'minimax_m3_comfy'],
                         'the second engine is still tried after the first fails')


class StatusTests(unittest.TestCase):
    def test_status_without_a_plan_is_idle_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = compare.read(tmp, 'nope')
        self.assertEqual(plan['state'], 'idle')

    def test_status_reports_the_real_take_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, compare.COMPARE_FILE.format(project='p1')).write_text(json.dumps({
                'state': 'running', 'project_id': 'p1',
                'engines': [{'engine_id': 'ace_local', 'take_id': 't1', 'state': 'running'}],
            }))
            project = {'takes': [{'take_id': 't1', 'state': 'succeeded',
                                  'qc': {'file_s': 179.8, 'peak_dbfs': -0.6}}]}
            with patch.object(compare.service, 'store') as store:
                store.return_value.get.return_value = project
                plan = compare.status('p1', data_dir=tmp)
        self.assertEqual(plan['engines'][0]['state'], 'succeeded')
        self.assertEqual(plan['engines'][0]['seconds'], 179.8)
        self.assertEqual(plan['state'], 'done')


if __name__ == '__main__':
    unittest.main()


def test_the_engines_route_is_not_swallowed_by_the_project_route():
    """`/compare/engines` must not be read as a project id — FastAPI matches in declaration order."""
    import dashboard.plugin_api as api

    paths = [getattr(route, 'path', '') for route in api.router.routes]
    engines = paths.index('/compare/engines')
    project = paths.index('/compare/{project_id}')
    assert engines < project, 'declare the literal route first, or "engines" becomes a project id'
