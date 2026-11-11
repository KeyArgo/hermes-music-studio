import base64
import importlib.util
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
from dashboard.plugin_api import router
from music_studio import service

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_ace import make_runtime, wav_bytes  # noqa: E402
from support import install_shared_runtime, pin_fixture_runtime  # noqa: E402


def setUpModule():
    global _RUNTIME_PIN
    _RUNTIME_PIN = install_shared_runtime()


def tearDownModule():
    _RUNTIME_PIN.close()


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime_dir = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()
        self.runtime = make_runtime(self.runtime_dir.name)
        # /status and service.generate probe ace_engine in this process, where
        # the runtime paths were frozen at import: pin them to the fixture.
        self.pinned = pin_fixture_runtime(self.runtime)
        self.pinned.__enter__()
        app = FastAPI(); app.include_router(router)
        self.client = TestClient(app)
        self.pid = service.store().create(caption='folk', lyrics='hello', duration_seconds=10)['project_id']

    def tearDown(self):
        self.pinned.close()
        self.env.stop()
        self.runtime_dir.cleanup()
        self.temp.cleanup()

    def use_fake(self, **kwargs):
        make_runtime(self.runtime, **kwargs)

    def approve(self, pid):
        """Approve bound to the version on screen, as both clients now do."""
        return self.client.post('/approve', json={
            'project_id': pid,
            'base_updated_at': service.store().get(pid)['updated_at']})

    def wait(self, tid, limit=15):
        for _ in range(int(limit / .02)):
            for project in self.client.get('/status').json()['projects']:
                if project['project_id'] != self.pid:
                    continue
                for take in project['takes']:
                    if take['take_id'] == tid and take['state'] in ('succeeded', 'failed'):
                        return take
            time.sleep(.02)
        self.fail('worker did not finish')

    def test_explicit_recovery_preserves_live_worker(self):
        from music_studio.jobs import Jobs
        s = service.store()
        s.approve(self.pid)
        orphan = Jobs(s).enqueue(self.pid, 1)
        Jobs(s).start(self.pid, orphan['take_id'])
        self.client.get('/status')
        self.assertEqual(s.get(self.pid)['takes'][0]['state'], 'running')
        with self.assertRaisesRegex(ValueError, 'confirm'):
            service.recover_interrupted()
        self.assertEqual(self.client.post('/generate', json={'project_id': self.pid, 'seed': 2}).status_code, 409)
        self.assertEqual(service.recover_interrupted(confirmed=True)['recovered'], 1)
        self.assertEqual(s.get(self.pid)['takes'][0]['state'], 'failed')
        self.use_fake(delay=1.0)
        response = self.client.post('/generate', json={'project_id': self.pid, 'seed': 2})
        self.assertEqual(response.status_code, 200)
        tid = response.json()['take']['take_id']
        for _ in range(300):
            if s.get(self.pid)['takes'][1]['state'] == 'running':
                break
            time.sleep(.02)
        self.assertEqual(s.get(self.pid)['takes'][1]['state'], 'running')
        self.client.get('/status')
        self.assertEqual(s.get(self.pid)['takes'][1]['state'], 'running')
        with self.assertRaisesRegex(ValueError, 'busy'):
            service.recover_interrupted(confirmed=True)
        self.assertEqual(self.client.post('/generate', json={'project_id': self.pid, 'seed': 3}).status_code, 409)
        self.assertEqual(self.wait(tid)['state'], 'succeeded')

    def test_api_create_and_update_revoke_approval_and_validate_drafts(self):
        created = self.client.post('/create', json={'draft': {'title': 'New', 'caption': 'music', 'lyrics': 'words'}})
        self.assertEqual(created.status_code, 200)
        pid = created.json()['project']['project_id']
        self.assertEqual(self.approve(pid).status_code, 200)
        changed = self.client.post('/update', json={'project_id': pid, 'draft': {'lyrics': 'new words'}})
        self.assertEqual(changed.status_code, 200)
        self.assertFalse(changed.json()['project']['approved'])
        self.assertEqual(self.client.post('/generate', json={'project_id': pid, 'seed': 1}).status_code, 409)
        self.assertEqual(self.approve(pid).status_code, 200)
        unchanged = self.client.post('/update', json={'project_id': pid, 'draft': {'lyrics': 'new words'}})
        self.assertFalse(unchanged.json()['project']['approved'])
        self.assertEqual(self.client.post('/update', json={'project_id': pid, 'draft': {'approved': True}}).status_code, 400)
        self.assertEqual(self.client.post('/create', json={'draft': []}).status_code, 400)

    def test_approved_background_job_persists_and_streams_audio(self):
        self.assertEqual(self.client.post('/generate', json={'project_id': self.pid, 'seed': 1}).status_code, 409)
        self.approve(self.pid)
        response = self.client.post('/generate', json={'project_id': self.pid, 'seed': 1})
        self.assertEqual(response.status_code, 200)
        tid = response.json()['take']['take_id']
        take = self.wait(tid)
        self.assertEqual(take['state'], 'succeeded')
        audio = self.client.get(f'/projects/{self.pid}/takes/{tid}/audio')
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.content, wav_bytes())
        encoded = self.client.get(f'/projects/{self.pid}/takes/{tid}/audio-json')
        self.assertEqual(encoded.status_code, 200)
        self.assertEqual(base64.b64decode(encoded.json()['audio_base64']), wav_bytes())
        self.assertEqual(encoded.json()['media_type'], 'audio/wav')
        with patch('dashboard.plugin_api.MAX_DESKTOP_AUDIO_BYTES', len(wav_bytes()) - 1):
            self.assertEqual(self.client.get(f'/projects/{self.pid}/takes/{tid}/audio-json').status_code, 413)
        self.assertEqual(self.client.get(f'/projects/{self.pid}/takes/not-a-uuid/audio-json').status_code, 400)
        self.assertEqual(self.client.get(f'/projects/{self.pid}/takes/not-a-uuid/audio').status_code, 400)
        path = service.store().root / take['audio_file']
        path.unlink()
        path.symlink_to('/etc/passwd')
        self.assertNotEqual(self.client.get(f'/projects/{self.pid}/takes/{tid}/audio').status_code, 200)

    def test_engine_failure_is_recorded_and_can_retry(self):
        self.approve(self.pid)
        self.use_fake(fail=True)
        r = self.client.post('/generate', json={'project_id': self.pid, 'seed': 2})
        self.assertEqual(r.status_code, 200)
        take = self.wait(r.json()['take']['take_id'])
        self.assertEqual(take['state'], 'failed')
        self.assertIn('fake ace render failed', take['error'])
        self.assertIsNone(take['audio_file'])
        self.use_fake()
        retry = self.client.post('/generate', json={'project_id': self.pid, 'seed': 3})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(self.wait(retry.json()['take']['take_id'])['state'], 'succeeded')

    def test_agent_draft_shared_with_api_and_edits_revoke_approval(self):
        spec = importlib.util.spec_from_file_location('studio_entry', Path(__file__).resolve().parents[1] / '__init__.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        class Context:
            def register_tool(self, **kwargs): self.handler = kwargs['handler']
        ctx = Context(); module.register(ctx)
        project = json.loads(ctx.handler({'action': 'create', 'draft': {'title': 'shared', 'caption': 'music', 'lyrics': 'words'}}))['project']
        pid = project['project_id']
        self.assertIn(pid, [p['project_id'] for p in self.client.get('/status').json()['projects']])
        self.assertEqual(self.approve(pid).status_code, 200)
        updated = json.loads(ctx.handler({'action': 'update', 'project_id': pid, 'draft': {'lyrics': 'changed'}}))
        self.assertFalse(updated['project']['approved'])
        self.assertEqual(self.client.post('/generate', json={'project_id': pid, 'seed': 1}).status_code, 409)


if __name__ == '__main__':
    unittest.main()


class AudioLengthTests(unittest.TestCase):
    """A chunked take has no length, so the browser cannot seek it."""

    def test_take_audio_declares_its_length(self):
        import io
        from dashboard import plugin_api

        payload = b'RIFF' + b'\x00' * 2044
        response = plugin_api._stream(io.BytesIO(payload), {'X-Content-Type-Options': 'nosniff'})
        self.assertEqual(response.headers['content-length'], str(len(payload)))
        self.assertEqual(response.headers['content-type'], 'audio/wav')
