"""Contract tests for the Suno-class surface: metadata drafts, seeds, batch queue,
project management and presets.

Every test here fails on eb45de5 (the adapter only forwarded caption/lyrics/
duration/seed and there was one take per request).
"""
import json
import os
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
from music_studio.jobs import Jobs  # noqa: E402
from music_studio.store import FIELDS, Store, validate_ready  # noqa: E402
from fake_ace import make_runtime, wav_bytes  # noqa: E402
from support import install_shared_runtime, pin_fixture_runtime  # noqa: E402


def setUpModule():
    global _RUNTIME_PIN
    _RUNTIME_PIN = install_shared_runtime()


def tearDownModule():
    _RUNTIME_PIN.close()


class DraftFieldTests(unittest.TestCase):
    """Draft metadata: instrumental / vocal_language / bpm / keyscale."""

    def test_defaults_are_present_on_a_new_project(self):
        with tempfile.TemporaryDirectory() as root:
            project = Store(root).create(title='A')
        self.assertIs(project['instrumental'], False)
        self.assertEqual(project['vocal_language'], 'en')
        self.assertIsNone(project['bpm'])
        self.assertIsNone(project['keyscale'])
        for key, value in (('instrumental', False), ('vocal_language', 'en'),
                           ('bpm', None), ('keyscale', None)):
            self.assertEqual(FIELDS[key], value)

    def test_metadata_fields_round_trip_and_validate(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            created = s.create(caption='c', lyrics='l', instrumental=False,
                               vocal_language='ja', bpm=90, keyscale='F# minor')
            pid = created['project_id']
            stored = Store(root).get(pid)
            self.assertEqual((stored['vocal_language'], stored['bpm'], stored['keyscale'],
                              stored['instrumental']), ('ja', 90, 'F# minor', False))
            for field, value in (('instrumental', 'yes'), ('instrumental', 1),
                                 ('vocal_language', 'xx'), ('vocal_language', ''),
                                 ('bpm', 39), ('bpm', 221), ('bpm', 120.5),
                                 ('bpm', True), ('bpm', '120'),
                                 ('keyscale', 'x' * 25), ('keyscale', 7)):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, field):
                        s.update(pid, **{field: value})
            s.update(pid, bpm=40, keyscale='x' * 24, vocal_language='auto', instrumental=True)
            s.update(pid, bpm=None, keyscale=None, vocal_language='zh', instrumental=False)
            s.update(pid, keyscale='C Major')

    def test_instrumental_draft_may_have_empty_lyrics(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            pid = s.create(caption='lo-fi beat', lyrics='', instrumental=True)['project_id']
            self.assertTrue(s.approve(pid)['approved'])
            # Non-instrumental drafts still require lyrics.
            pid = s.create(caption='lo-fi beat', lyrics='')['project_id']
            with self.assertRaisesRegex(ValueError, 'lyrics'):
                s.approve(pid)
            # Whitespace is emptiness, not content.
            pid = s.create(caption='lo-fi beat', lyrics='  \n', instrumental=True)['project_id']
            self.assertTrue(s.approve(pid)['approved'])

    def test_validate_ready_still_requires_a_caption_even_instrumentally(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            pid = s.create(caption='', lyrics='words', instrumental=True)['project_id']
            with self.assertRaisesRegex(ValueError, 'caption'):
                s.approve(pid)

    def test_legacy_project_without_metadata_is_treated_as_defaults(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(root)
            pid = s.create(caption='c', lyrics='l')['project_id']
            project = s.get(pid)
            for key in ('instrumental', 'vocal_language', 'bpm', 'keyscale'):
                project.pop(key)
            s._save(project)
            self.assertTrue(s.approve(pid)['approved'])
            self.assertEqual(Store(root).get(pid)['vocal_language'], 'en')


class EngineParameterTests(unittest.TestCase):
    """The engine boundary must forward the new metadata, and only what ACE accepts."""

    def test_engine_accepts_empty_lyrics_only_when_instrumental(self):
        import ace_engine
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'lyrics'):
                ace_engine.generate(caption='folk', lyrics='', duration_seconds=15,
                                    seed=1, output_dir=d)

    def test_engine_hands_the_new_params_to_the_render_and_never_cfg_or_steps(self):
        import ace_engine
        seen = {}

        class FakeProcess:
            returncode = 0

            def __init__(self, command, **kwargs):
                seen['job'] = Path(command[-1])
                seen['command'] = command
                seen['input'] = json.loads((Path(command[-1]) / 'input.json').read_text())

            def wait(self, timeout=None):
                with open(Path(seen['job']) / 'output.wav', 'wb') as fh:
                    fh.write(wav_bytes())
                return 0

            def poll(self):
                return 0

        with tempfile.TemporaryDirectory() as d, \
                patch('ace_engine.subprocess.Popen', FakeProcess), \
                patch('ace_engine.LOCK', Path(d) / 'gpu.lock'):
            self.assertTrue(ace_engine.generate(caption='beat', lyrics='', instrumental=True,
                                                vocal_language='auto', bpm=120,
                                                keyscale='C Major', duration_seconds=15,
                                                seed=5, output_dir=d))
        sent = seen['input']
        self.assertIs(sent['instrumental'], True)
        self.assertEqual(sent['lyrics'], '', 'instrumental takes must not send a [inst] sentinel')
        self.assertEqual(sent['vocal_language'], 'unknown', "ACE's vocabulary has no 'auto'")
        self.assertEqual(sent['bpm'], 120)
        self.assertEqual(sent['keyscale'], 'C Major')
        self.assertEqual(sent['seed'], 5)
        for forbidden in ('guidance_scale', 'inference_steps', 'cfg', 'steps'):
            self.assertNotIn(forbidden, sent)

    def test_worker_maps_auto_language_to_the_runtime_value_and_forwards_metadata(self):
        from types import SimpleNamespace
        from ace_worker import run
        calls = {}

        class Params:
            def __init__(self, **kw):
                calls['params'] = kw

        class Config:
            def __init__(self, **kw):
                calls['config'] = kw

        class Handler:
            def initialize_service(self, **kw):
                return 'ready', True

        def generate_music(handler, llm, params, config, save_dir):
            path = Path(save_dir) / 'p.wav'
            path.write_bytes(b'rendered')
            return SimpleNamespace(success=True, audios=[{'path': str(path)}], error=None)

        with tempfile.TemporaryDirectory() as d:
            job = Path(d)
            (job / 'input.json').write_text(json.dumps(dict(
                caption='beat', lyrics='', instrumental=True, vocal_language='auto',
                bpm=120, keyscale='C Major', duration_seconds=15, seed=9)))
            run(job, Handler, lambda: None, Params, Config, generate_music)
        params = calls['params']
        self.assertIs(params['instrumental'], True)
        self.assertEqual(params['lyrics'], '')
        self.assertEqual(params['vocal_language'], 'unknown')  # ACE's 'auto' vocabulary
        self.assertEqual(params['bpm'], 120)
        self.assertEqual(params['keyscale'], 'C major')  # ACE's canonical spelling
        self.assertEqual(params['seed'], 9)

    def test_worker_canonicalises_keyscale_only_when_it_has_an_exact_form(self):
        from ace_worker import _keyscale
        for given, expected in (('C Major', 'C major'), ('c major', 'C major'.lower()),
                                ('F# minor', 'F# minor'), ('F#m', 'F# minor'),
                                ('Am', 'A minor'), ('B♭ major', 'B♭ major'),
                                ('D minor', 'D minor'), ('modal', 'modal'),
                                ('Lydian #4', 'Lydian #4'), ('', ''), (None, '')):
            with self.subTest(given=given):
                self.assertEqual(_keyscale(given), expected)

    def test_worker_sends_english_vocal_language_untouched(self):
        from types import SimpleNamespace
        from ace_worker import run
        calls = {}

        class Params:
            def __init__(self, **kw):
                calls['params'] = kw

        class Config:
            def __init__(self, **kw):
                calls['config'] = kw

        class Handler:
            def initialize_service(self, **kw):
                return 'ready', True

        def generate_music(handler, llm, params, config, save_dir):
            path = Path(save_dir) / 'p.wav'
            path.write_bytes(b'rendered')
            return SimpleNamespace(success=True, audios=[{'path': str(path)}], error=None)

        with tempfile.TemporaryDirectory() as d:
            job = Path(d)
            (job / 'input.json').write_text(json.dumps(dict(
                caption='song', lyrics='[Verse]\nhello', instrumental=False,
                vocal_language='en', bpm=None, keyscale=None,
                duration_seconds=15, seed=9)))
            run(job, Handler, lambda: None, Params, Config, generate_music)
        params = calls['params']
        self.assertIs(params['instrumental'], False)
        self.assertEqual(params['vocal_language'], 'en')
        self.assertIsNone(params['bpm'])
        self.assertEqual(params['keyscale'], '')
        self.assertEqual(params['lyrics'], '[Verse]\nhello')


class RouterTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime_dir = tempfile.TemporaryDirectory()
        self.runtime = make_runtime(self.runtime_dir.name)
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()
        # Availability is a property of this fixture, not of the host.
        self.pinned = pin_fixture_runtime(self.runtime)
        self.pinned.__enter__()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.store = service.store()

    def tearDown(self):
        for project in self.store.all():
            for take in project['takes']:
                for pid in (take.get('ace_pid'), take.get('pid')):
                    if isinstance(pid, int):
                        try:
                            os.kill(pid, 0)
                        except OSError:
                            pass
        self.pinned.close()
        self.env.stop()
        self.runtime_dir.cleanup()
        self.temp.cleanup()

    def project(self, **draft):
        draft.setdefault('caption', 'music')
        draft.setdefault('lyrics', 'words')
        return self.client.post('/create', json={'draft': draft}).json()['project']

    def approve(self, pid):
        return self.client.post('/approve', json={
            'project_id': pid, 'base_updated_at': self.store.get(pid)['updated_at']})

    def render(self, **kwargs):
        """Replace the fake runtime (delay/fail knobs) without touching the env."""
        make_runtime(self.runtime, **kwargs)

    def wait(self, tid, limit=20):
        deadline = time.time() + limit
        while time.time() < deadline:
            for project in self.store.all():
                for take in project['takes']:
                    if take['take_id'] == tid and take['state'] in ('succeeded', 'failed', 'cancelled'):
                        return take
            time.sleep(.02)
        self.fail(f'take {tid} never finished')

    def status_takes(self, pid):
        return next(p for p in self.client.get('/status').json()['projects']
                    if p['project_id'] == pid)['takes']


class SeedContractTests(RouterTestCase):
    def test_generate_records_requested_seed_and_lock_flag(self):
        pid = self.project()['project_id']
        self.approve(pid)
        locked = self.client.post('/generate', json={'project_id': pid, 'seed': 4242,
                                                     'lock_seed': True})
        self.assertEqual(locked.status_code, 200)
        take = locked.json()['take']
        self.assertEqual((take['requested_seed'], take['seed'], take['seed_locked']),
                         (4242, 4242, True))
        self.wait(take['take_id'])
        unlocked = self.client.post('/generate', json={'project_id': pid, 'seed': 4243})
        take = unlocked.json()['take']
        self.assertEqual((take['requested_seed'], take['seed_locked']), (4243, False))
        self.wait(take['take_id'])
        # The lock flag survives on the persisted record, not just the response.
        stored = self.store.get(pid)['takes'][0]
        self.assertEqual((stored['requested_seed'], stored['seed_locked']), (4242, True))

    def test_generate_still_rejects_a_bad_seed(self):
        pid = self.project()['project_id']
        self.approve(pid)
        for seed in (None, -1, 2**32, 'x', True):
            with self.subTest(seed=seed):
                response = self.client.post('/generate', json={'project_id': pid, 'seed': seed})
                self.assertEqual(response.status_code, 400)


class BatchQueueTests(RouterTestCase):
    def test_batch_enqueues_count_takes_with_queue_positions(self):
        pid = self.project()['project_id']
        self.approve(pid)
        response = self.client.post('/batch', json={'project_id': pid, 'count': 3, 'seed_base': 500})
        self.assertEqual(response.status_code, 200)
        takes = response.json()['takes']
        self.assertEqual([t['queue_position'] for t in takes], [1, 2, 3])
        self.assertEqual([t['seed'] for t in takes], [500, 501, 502])
        self.assertEqual([t['requested_seed'] for t in takes], [500, 500, 500])
        self.assertTrue(all(t['state'] == 'queued' for t in takes))
        self.assertEqual(len({t['batch_id'] for t in takes}), 1)
        status = self.status_takes(pid)
        self.assertEqual([t['queue_position'] for t in status], [1, 2, 3])
        project = next(p for p in self.client.get('/status').json()['projects']
                       if p['project_id'] == pid)
        self.assertIn('active_take_id', project)
        for take in takes:
            self.wait(take['take_id'])
        self.assertEqual([t['state'] for t in self.status_takes(pid)],
                         ['succeeded', 'succeeded', 'succeeded'])

    def test_batch_rejects_bad_counts_and_unapproved_drafts(self):
        pid = self.project()['project_id']
        # Not approved yet: the queue must refuse.
        self.assertEqual(self.client.post('/batch', json={'project_id': pid, 'count': 2}).status_code, 409)
        self.approve(pid)
        for count in (0, 9, -1, 'two', 2.5, None):
            with self.subTest(count=count):
                self.assertEqual(self.client.post('/batch', json={'project_id': pid,
                                                                  'count': count}).status_code, 400)

    def test_batch_renders_strictly_one_at_a_time(self):
        pid = self.project()['project_id']
        self.approve(pid)
        self.render(delay=0.35)
        takes = self.client.post('/batch', json={'project_id': pid, 'count': 3}).json()['takes']
        running_seen = []
        samples = []
        deadline = time.time() + 25
        while time.time() < deadline:
            current = self.status_takes(pid)
            running = [t['take_id'] for t in current if t['state'] == 'running']
            samples.append(running)
            self.assertLessEqual(len(running), 1, f'two renders on the GPU at once: {samples}')
            if len(running) == 1 and (not running_seen or running_seen[-1] != running[0]):
                running_seen.append(running[0])
            if all(t['state'] in ('succeeded', 'failed') for t in current):
                break
            time.sleep(.02)
        finished = self.status_takes(pid)
        self.assertEqual([t['state'] for t in finished], ['succeeded'] * 3)
        self.assertEqual(running_seen, [takes[0]['take_id'], takes[1]['take_id'], takes[2]['take_id']])
        for previous, current in zip(finished, finished[1:]):
            self.assertGreaterEqual(current['started_at'], previous['finished_at'],
                                    'the next take started before the previous one finished')
            self.assertEqual((current['queue_position'], previous['queue_position']),
                             (previous['queue_position'] + 1, previous['queue_position']))

    def test_a_failed_take_does_not_stop_the_rest_of_the_batch(self):
        pid = self.project()['project_id']
        self.approve(pid)
        self.render(fail_seed=700)
        takes = self.client.post('/batch', json={'project_id': pid, 'count': 3,
                                                 'seed_base': 700}).json()['takes']
        for take in takes:
            self.wait(take['take_id'])
        states = [t['state'] for t in self.status_takes(pid)]
        self.assertEqual(states, ['failed', 'succeeded', 'succeeded'])

    def test_queued_takes_of_a_live_batch_are_not_reaped_as_orphans(self):
        """A batch take waiting behind the active render must not die of 'worker died'."""
        pid = self.project()['project_id']
        self.approve(pid)
        self.render(delay=5.0)
        takes = self.client.post('/batch', json={'project_id': pid, 'count': 2}).json()['takes']
        stored = self.store.get(pid)['takes']
        worker_pid = stored[0]['pid']
        self.assertIsInstance(worker_pid, int)
        self.assertEqual({t['pid'] for t in stored}, {worker_pid},
                         'every queued batch take must record the worker that owns it')
        deadline = time.time() + 10
        while time.time() < deadline and self.store.get(pid)['takes'][0]['state'] != 'running':
            time.sleep(.02)
        # Past the spawn grace the queued take is still owned by a live worker.
        Jobs(self.store).reconcile_dead_workers(grace_seconds=0)
        self.assertEqual([t['state'] for t in self.store.get(pid)['takes']],
                         ['running', 'queued'])
        self.client.post('/batch/cancel', json={'project_id': pid})
        for take in takes:
            self.wait(take['take_id'], limit=30)
        self.assertEqual([t['state'] for t in self.status_takes(pid)], ['succeeded', 'cancelled'])

    def test_batch_cancel_marks_queued_takes_cancelled(self):
        pid = self.project()['project_id']
        self.approve(pid)
        self.render(delay=0.4)
        takes = self.client.post('/batch', json={'project_id': pid, 'count': 3}).json()['takes']
        deadline = time.time() + 15
        while time.time() < deadline:
            if any(t['state'] == 'running' for t in self.status_takes(pid)):
                break
            time.sleep(.01)
        cancelled = self.client.post('/batch/cancel', json={'project_id': pid})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()['cancelled'], 2)
        states = [t['state'] for t in self.status_takes(pid)]
        self.assertEqual(states[1:], ['cancelled', 'cancelled'])
        for take in takes:
            self.wait(take['take_id'])
        self.assertEqual([t['state'] for t in self.status_takes(pid)],
                         ['succeeded', 'cancelled', 'cancelled'])


class ProjectManagementTests(RouterTestCase):
    def test_rename_updates_the_title(self):
        pid = self.project(title='old')['project_id']
        response = self.client.post('/rename', json={'project_id': pid, 'title': 'new name'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['project']['title'], 'new name')
        self.assertEqual(self.store.get(pid)['title'], 'new name')
        self.assertEqual(self.client.post('/rename', json={'project_id': pid,
                                                           'title': 'x' * 201}).status_code, 400)
        self.assertEqual(self.client.post('/rename', json={'project_id': pid,
                                                           'title': ''}).status_code, 400)
        self.assertEqual(self.client.post('/rename', json={'project_id': 'nope',
                                                           'title': 'x'}).status_code, 400)
        absent = str(__import__('uuid').uuid4())
        self.assertEqual(self.client.post('/rename', json={'project_id': absent,
                                                           'title': 'x'}).status_code, 404)

    def test_delete_removes_the_project_and_its_wavs(self):
        pid = self.project()['project_id']
        self.approve(pid)
        take = Jobs(self.store).enqueue(pid, 1)
        Jobs(self.store).start(pid, take['take_id'], pid=os.getpid())
        take = Jobs(self.store).succeed(pid, take['take_id'], wav_bytes())
        wav = self.store.root / take['audio_file']
        self.assertTrue(wav.is_file())
        response = self.client.post('/delete', json={'project_id': pid})
        self.assertEqual(response.status_code, 200)
        self.assertFalse((self.store.root / (pid + '.json')).exists())
        self.assertFalse(wav.exists())
        self.assertNotIn(pid, [p['project_id'] for p in self.client.get('/status').json()['projects']])
        self.assertEqual(self.client.post('/delete', json={'project_id': pid}).status_code, 404)

    def test_delete_refuses_while_the_project_has_an_active_render(self):
        pid = self.project()['project_id']
        self.approve(pid)
        take = Jobs(self.store).enqueue(pid, 1)
        Jobs(self.store).start(pid, take['take_id'], pid=os.getpid())
        response = self.client.post('/delete', json={'project_id': pid})
        self.assertEqual(response.status_code, 409)
        self.assertIn('busy', response.json()['detail'])
        self.assertTrue((self.store.root / (pid + '.json')).exists())
        Jobs(self.store).abort(pid, take['take_id'], 'test cleanup')
        self.assertEqual(self.client.post('/delete', json={'project_id': pid}).status_code, 200)


class PresetTests(RouterTestCase):
    def test_presets_round_trip_in_their_own_file(self):
        created = self.client.post('/presets', json={
            'name': 'Night Drive', 'caption': 'synthwave, analog bass, neon pads',
            'bpm': 112, 'keyscale': 'A minor', 'vocal_language': 'en', 'instrumental': True})
        self.assertEqual(created.status_code, 200)
        presets = self.client.get('/presets').json()['presets']
        self.assertEqual([p['name'] for p in presets], ['Night Drive'])
        preset = presets[0]
        self.assertEqual((preset['caption'], preset['bpm'], preset['keyscale'],
                          preset['vocal_language'], preset['instrumental']),
                         ('synthwave, analog bass, neon pads', 112, 'A minor', 'en', True))
        self.assertEqual(preset['preset_id'], preset['name'])
        # Its own JSON file, never a project.
        self.assertTrue((self.store.root / 'presets.json').is_file())
        self.assertEqual(self.store.all(), [])
        self.assertEqual(self.client.get('/status').json()['projects'], [])
        # Upsert on the same name, newest caption wins.
        self.client.post('/presets', json={'name': 'Night Drive', 'caption': 'changed'})
        presets = self.client.get('/presets').json()['presets']
        self.assertEqual(len(presets), 1)
        self.assertEqual(presets[0]['caption'], 'changed')
        self.assertIsNone(presets[0]['bpm'])
        self.assertEqual(presets[0]['vocal_language'], 'en')

    def test_presets_validate_and_delete(self):
        for body in ({'caption': 'x'},                       # no name
                     {'name': '', 'caption': 'x'},
                     {'name': 'x' * 65, 'caption': 'x'},
                     {'name': 'ok', 'caption': ''},
                     {'name': 'ok', 'caption': 'x', 'bpm': 999},
                     {'name': 'ok', 'caption': 'x', 'vocal_language': 'klingon'},
                     {'name': 'ok', 'caption': 'x', 'keyscale': 'y' * 25},
                     {'name': 'ok', 'caption': 'x', 'instrumental': 'maybe'}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/presets', json=body).status_code, 400)
        self.assertEqual(self.client.get('/presets').json()['presets'], [])
        self.assertEqual(self.client.post('/presets',
                                          json={'name': 'A/B', 'caption': 'x'}).status_code, 400)
        self.client.post('/presets', json={'name': 'Keep me', 'caption': 'x'})
        removed = self.client.request('DELETE', '/presets/Keep%20me')
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.json()['presets'], [])
        # Idempotent: deleting an absent preset is not an error.
        self.assertEqual(self.client.request('DELETE', '/presets/Keep%20me').status_code, 200)

    def test_presets_survive_a_corrupt_file(self):
        self.store.root.mkdir(parents=True, exist_ok=True)
        (self.store.root / 'presets.json').write_text('{not json')
        self.assertEqual(self.client.get('/presets').json()['presets'], [])
        self.assertEqual(self.client.post('/presets',
                                          json={'name': 'Fresh', 'caption': 'x'}).status_code, 200)


if __name__ == '__main__':
    unittest.main()
