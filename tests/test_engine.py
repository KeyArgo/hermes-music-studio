import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ace_engine import generate

class EngineTests(unittest.TestCase):
    def test_rejects_empty_lyrics_before_starting_worker(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'lyrics'):
                generate(caption='folk', lyrics='', duration_seconds=25, seed=42, output_dir=d)

    def test_subprocess_receives_local_checkpoint_and_offline_flags(self):
        import os
        import wave
        import ace_engine
        from unittest.mock import patch
        seen = {}
        class FakeProcess:
            returncode = 0
            def __init__(self, command, **kwargs):
                self.command = command
                seen.update(kwargs)
                seen['command'] = command
            def wait(self, timeout=None):
                with wave.open(os.path.join(self.command[-1], 'output.wav'), 'wb') as f:
                    f.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                    f.writeframes(b'\0\0' * 8)
                return 0
            def poll(self):
                return self.returncode
        # The GPU lock must be redirected into the temp dir: patching Popen alone
        # leaves this test contending with a real render on the machine.
        with tempfile.TemporaryDirectory() as d, \
                patch('ace_engine.subprocess.Popen', FakeProcess), \
                patch('ace_engine.LOCK', Path(d) / 'gpu.lock'):
            self.assertTrue(generate(caption='jazz', lyrics='hello', duration_seconds=25, seed=1, output_dir=d))
        env = seen['env']
        self.assertEqual(env['ACESTEP_CHECKPOINTS_DIR'], str(ace_engine.RUNTIME / 'checkpoints'))
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        self.assertEqual(seen['start_new_session'], True)

    def test_worker_passes_inputs_to_ace_and_copies_returned_audio(self):
        import json
        from types import SimpleNamespace
        from ace_worker import run
        calls = {}
        class Params:
            def __init__(self, **kw): calls['params'] = kw
        class Config:
            def __init__(self, **kw): calls['config'] = kw
        class Handler:
            def initialize_service(self, **kw):
                calls['init'] = kw
                return 'ready', True
        def generate_music(handler, llm, params, config, save_dir):
            path = Path(save_dir) / 'produced.wav'
            path.write_bytes(b'rendered')
            return SimpleNamespace(success=True, audios=[{'path': str(path)}], error=None)
        with tempfile.TemporaryDirectory() as d:
            job = Path(d)
            (job / 'input.json').write_text(json.dumps(dict(caption='jazz', lyrics='[Verse]\nHello', duration_seconds=25, seed=123)))
            run(job, Handler, lambda: None, Params, Config, generate_music)
            self.assertEqual((job / 'output.wav').read_bytes(), b'rendered')
        self.assertEqual(calls['params']['caption'], 'jazz')
        self.assertEqual(calls['params']['lyrics'], '[Verse]\nHello')
        self.assertEqual(calls['params']['duration'], 25)
        self.assertEqual(calls['params']['seed'], 123)
        self.assertEqual(calls['config']['audio_format'], 'wav')
        self.assertFalse(calls['config']['use_random_seed'])

if __name__ == '__main__':
    unittest.main()
