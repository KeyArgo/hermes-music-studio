"""Round 18: MiniMax guidance defaults, ACE form given a ComfyUI, library dates."""
import json
import tempfile
import unittest
from pathlib import Path

from music_studio import settings
from music_studio.engines.minimax_m3_comfy import MiniMaxM3ComfyEngine


class MiniMaxGuidanceDefaults(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())

    def _save(self, minimax):
        settings.settings_path(self.base).parent.mkdir(parents=True, exist_ok=True)
        settings.settings_path(self.base).write_text(json.dumps({'minimax': minimax}), encoding='utf-8')

    def test_defaults_match_comfyui_minimax_workflow(self):
        knobs = settings.read(self.base)['minimax']
        self.assertEqual((knobs['cfg'], knobs['cfg_scale']), (1.7, 1.7))

    def test_engine_without_saved_settings_sends_the_workflow_guidance(self):
        from unittest import mock
        from music_studio.engines import minimax_m3_comfy as mod
        from music_studio.engines.contract import RenderRequest
        with mock.patch.object(mod, '_saved_settings', return_value={}):
            engine = MiniMaxM3ComfyEngine(base_url='http://127.0.0.1:9')
        graph = engine.build_workflow(RenderRequest(caption='folk', duration_seconds=30, seed=1))
        sampler = next(n for n in graph.values() if n['class_type'] == 'KSampler')
        encode = next(n for n in graph.values() if n['class_type'] == 'MiniMaxMusic3TextEncode')
        self.assertEqual(sampler['inputs']['cfg'], 1.7)
        self.assertEqual(encode['inputs']['cfg_scale'], 1.7)

    def test_old_saved_default_pair_follows_new_defaults(self):
        self._save({'steps': 30, 'cfg': 4.0, 'cfg_scale': 1.5, 'top_k': 50,
                    'sampler': 'euler', 'scheduler': 'simple'})
        knobs = settings.read(self.base)['minimax']
        self.assertEqual((knobs['cfg'], knobs['cfg_scale']), (1.7, 1.7))

    def test_a_chosen_value_is_kept(self):
        self._save({'cfg': 4.0, 'cfg_scale': 2.2})
        knobs = settings.read(self.base)['minimax']
        self.assertEqual((knobs['cfg'], knobs['cfg_scale']), (4.0, 2.2))
        self._save({'cfg': 3.0, 'cfg_scale': 1.5})
        knobs = settings.read(self.base)['minimax']
        self.assertEqual((knobs['cfg'], knobs['cfg_scale']), (3.0, 1.5))


if __name__ == '__main__':
    unittest.main()


class AceFormGivenComfyUI(unittest.TestCase):
    """The owner typed a ComfyUI address into the ACE-Step server form and hit a dead end."""

    def _fake_get(self, routes):
        import urllib.error

        def get(url, *a, **k):
            for suffix, body in routes.items():
                if url.endswith(suffix):
                    if isinstance(body, int):
                        raise urllib.error.HTTPError(url, body, 'x', {}, None)
                    return body
            raise urllib.error.HTTPError(url, 404, 'x', {}, None)
        return get

    def test_comfyui_address_says_choose_comfyui(self):
        from unittest import mock
        from music_studio import engine_catalog, setup
        routes = {'/system_stats': json.dumps({'system': {'os': 'posix'}, 'devices': []})}
        with mock.patch.object(setup, '_get', side_effect=self._fake_get(routes)):
            result = engine_catalog.test_server('ace_server', 'http://10.0.0.5:8188')
        self.assertFalse(result['ok'])
        self.assertEqual(result['problem'], 'is_comfyui')
        self.assertIn('Choose ComfyUI', result['message'])

    def test_other_web_server_is_still_not_ace(self):
        from unittest import mock
        from music_studio import engine_catalog, setup
        with mock.patch.object(setup, '_get', side_effect=self._fake_get({})):
            result = engine_catalog.test_server('ace_server', 'http://10.0.0.5:8080')
        self.assertEqual(result['problem'], 'not_ace')


class SongsSayWhenTheyWereMade(unittest.TestCase):
    def test_a_new_song_has_created_at_and_an_edit_keeps_it(self):
        import time
        from music_studio.store import Store
        root = tempfile.mkdtemp()
        before = time.time()
        project = Store(root).create(title='A')
        made = Store(root).get(project['project_id']).get('created_at')
        self.assertIsNotNone(made)
        self.assertGreaterEqual(made, before)
        Store(root).rename(project['project_id'], 'B') if hasattr(Store, 'rename') else None
        self.assertEqual(Store(root).get(project['project_id'])['created_at'], made)

    def test_an_old_song_is_not_given_a_made_up_date(self):
        from music_studio.store import Store
        root = Path(tempfile.mkdtemp())
        project = Store(root).create(title='Old')
        path = root / (project['project_id'] + '.json')
        data = json.loads(path.read_text())
        data.pop('created_at')
        path.write_text(json.dumps(data))
        store = Store(root)
        store._save(store.get(project['project_id']))
        self.assertNotIn('created_at', store.get(project['project_id']))


class ActivityLog(unittest.TestCase):
    def setUp(self):
        from music_studio import activity
        self.activity = activity
        self.base = Path(tempfile.mkdtemp())

    def _text(self):
        return self.activity.log_path(self.base).read_text(encoding='utf-8')

    def test_a_line_names_the_event_and_its_fields(self):
        self.activity.record(self.base, 'engine test', kind='comfyui', url='http://10.0.0.5:8188', ok=True)
        line = self._text().strip()
        self.assertIn('engine test', line)
        self.assertIn("url='http://10.0.0.5:8188'", line)
        self.assertEqual(len(self._text().splitlines()), 1)

    def test_keys_and_tokens_never_reach_the_file(self):
        self.activity.record(self.base, 'render failed',
                             error='401 from server: Authorization: Bearer abc123secret '
                                   'api_key=sk-live-999 token="tok-42" password: hunter2')
        text = self._text()
        for secret in ('abc123secret', 'sk-live-999', 'live-999', 'tok-42', 'hunter2'):
            self.assertNotIn(secret, text)
        self.assertIn('[hidden]', text)

    def test_the_file_rotates_and_keeps_three_old_copies(self):
        from unittest import mock
        with mock.patch.object(self.activity, 'MAX_BYTES', 200):
            for i in range(60):
                self.activity.record(self.base, 'render finished', take=f'take-{i:03d}', note='x' * 40)
        names = sorted(p.name for p in self.activity.log_path(self.base).parent.iterdir())
        self.assertEqual(names, ['studio.log', 'studio.log.1', 'studio.log.2', 'studio.log.3'])
        self.assertIn('take-059', self._text())

    def test_logging_never_raises(self):
        blocker = self.base / 'logs'
        blocker.write_text('a file where the folder should be')
        self.activity.record(self.base, 'render started', take='t')   # must not raise
        self.activity.record(None, 'render started', take='t')

    def test_a_render_is_logged_start_to_finish(self):
        from unittest import mock
        from music_studio.jobs import Jobs
        from music_studio.store import Store
        sys_path_tests = Path(__file__).parent
        import sys
        sys.path.insert(0, str(sys_path_tests))
        from test_jobs import _tone
        root = tempfile.mkdtemp()
        with mock.patch.object(self.activity, 'data_dir', return_value=self.base):
            store = Store(root)
            pid = store.create(title='Logged', caption='music', lyrics='words')['project_id']
            store.approve(pid)
            jobs = Jobs(store)
            take = jobs.enqueue(pid, seed=1)
            jobs.start(pid, take['take_id'])
            jobs.succeed(pid, take['take_id'], _tone(seconds=1))
            second = jobs.enqueue(pid, seed=2)
            jobs.start(pid, second['take_id'])
            jobs.fail(pid, second['take_id'], 'server said no')
        text = self._text()
        self.assertIn('render started', text)
        self.assertIn('render finished', text)
        self.assertIn("error='server said no'", text)
        self.assertNotIn('words', text, 'lyrics are never logged')
