"""Range repaint and long-form extension: new takes, lineage, parent immutability."""
import io
import math
import os
import struct
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

import ace_engine  # noqa: E402
from dashboard.plugin_api import router  # noqa: E402
from music_studio import service  # noqa: E402
from music_studio.jobs import Jobs  # noqa: E402
from music_studio.store import Store  # noqa: E402

from fake_ace import make_runtime  # noqa: E402
from support import install_shared_runtime, pin_fixture_runtime  # noqa: E402

RATE = 48000
CHANNELS = 2


def tone_wav(seconds, amplitude=0.3, frequency=220.0, rate=RATE):
    """Deterministic stereo audio with non-zero samples everywhere."""
    frames = int(round(seconds * rate))
    out = bytearray()
    step = 2 * math.pi * frequency / rate
    for index in range(frames):
        value = int(round(amplitude * 32767 * math.sin(step * index)))
        out += struct.pack('<hh', value, value)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setparams((CHANNELS, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(bytes(out))
    return buf.getvalue()


def frames_of(data):
    with wave.open(io.BytesIO(data), 'rb') as wav:
        return wav.getnframes(), wav.getframerate(), wav.getnchannels()


def samples(data):
    with wave.open(io.BytesIO(data), 'rb') as wav:
        raw = wav.readframes(wav.getnframes())
    return struct.unpack(f'<{len(raw) // 2}h', raw)


def with_silence(data, extra_seconds):
    """Append digital silence to a WAV blob, keeping it a valid WAV."""
    frames, rate, channels = frames_of(data)
    values = samples(data) + (0,) * (int(round(extra_seconds * rate)) * channels)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(struct.pack(f'<{len(values)}h', *values))
    return buf.getvalue()


_CACHE = {}


def cached_tone(seconds):
    if seconds not in _CACHE:
        _CACHE[seconds] = tone_wav(seconds)
    return _CACHE[seconds]


def setUpModule():
    global _RUNTIME_PIN
    _RUNTIME_PIN = install_shared_runtime()


def tearDownModule():
    _RUNTIME_PIN.close()


class StudioCase(unittest.TestCase):
    parent_seconds = 30

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime_dir = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()
        self.runtime = make_runtime(self.runtime_dir.name)
        # service/jobs probe ace_engine in-process; pin its frozen paths.
        self.pinned = pin_fixture_runtime(self.runtime)
        self.pinned.__enter__()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.store = service.store()
        self.pid = self.store.create(
            caption='folk', lyrics='[verse]\nhello', duration_seconds=self.parent_seconds,
        )['project_id']
        self.store.approve(self.pid)
        self.jobs = Jobs(self.store)
        self.parent = self.jobs.enqueue(self.pid, 7)
        self.jobs.start(self.pid, self.parent['take_id'], pid=os.getpid())
        self.jobs.succeed(self.pid, self.parent['take_id'],
                          cached_tone(self.parent_seconds))
        self.parent_id = self.parent['take_id']

    def tearDown(self):
        self.pinned.close()
        self.env.stop()
        self.runtime_dir.cleanup()
        self.temp.cleanup()

    def wait(self, take_id, limit=60):
        for _ in range(int(limit / 0.02)):
            for project in self.client.get('/status').json()['projects']:
                if project['project_id'] != self.pid:
                    continue
                for take in project['takes']:
                    if take['take_id'] == take_id and take['state'] in ('succeeded', 'failed'):
                        return take
            time.sleep(0.02)
        self.fail('worker did not finish')

    def parent_take(self):
        return next(t for t in self.store.get(self.pid)['takes']
                    if t['take_id'] == self.parent_id)


class RepaintTests(StudioCase):
    parent_seconds = 30

    def test_repaint_renders_only_the_range_and_never_mutates_the_parent(self):
        before_audio = self.jobs.read_audio(self.pid, self.parent_id)
        before_take = dict(self.parent_take())
        response = self.client.post('/repaint', json={
            'project_id': self.pid, 'take_id': self.parent_id,
            'start_s': 10.0, 'end_s': 20.0, 'caption': 'chorus lift'})
        self.assertEqual(response.status_code, 200)
        child_id = response.json()['take']['take_id']
        child = self.wait(child_id)
        self.assertEqual(child['state'], 'succeeded', child.get('error'))
        self.assertEqual(child['kind'], 'repaint')
        self.assertEqual(child['parent_take_id'], self.parent_id)
        self.assertEqual((child['range_start_s'], child['range_end_s']), (10.0, 20.0))
        self.assertIsNone(child['extend_seconds'])
        self.assertEqual(child['lineage'], [self.parent_id])
        self.assertEqual(child['snapshot']['caption'], 'chorus lift')
        self.assertEqual(child['progress'], 100)

        # The parent record and its audio are byte-identical.
        self.assertEqual(self.parent_take(), before_take)
        self.assertEqual(self.jobs.read_audio(self.pid, self.parent_id), before_audio)

        # Only the requested range changed: head/tail samples match exactly.
        parent_samples = samples(before_audio)
        child_samples = samples(self.jobs.read_audio(self.pid, child_id))
        self.assertEqual(len(child_samples), len(parent_samples))
        frame_len = CHANNELS
        start = 10 * RATE * frame_len
        end = 20 * RATE * frame_len
        self.assertEqual(child_samples[:start], parent_samples[:start])
        self.assertEqual(child_samples[end:], parent_samples[end:])
        self.assertNotEqual(child_samples[start:end], parent_samples[start:end])
        # Still a full-length take.
        frames, rate, _ = frames_of(self.jobs.read_audio(self.pid, child_id))
        self.assertEqual(rate, RATE)
        self.assertEqual(frames, self.parent_seconds * RATE)

    def test_repaint_range_validation(self):
        bad = [
            {'start_s': 0, 'end_s': 2.9},        # shorter than 3 s
            {'start_s': 0, 'end_s': 30.1},       # longer than 30 s
            {'start_s': 25, 'end_s': 55},        # past the parent
            {'start_s': -1, 'end_s': 5},
            {'start_s': 20, 'end_s': 10},
            {'start_s': True, 'end_s': 10},
            {'start_s': '10', 'end_s': 20},
            {'start_s': 10},
        ]
        for case in bad:
            with self.subTest(case=case):
                response = self.client.post('/repaint', json={
                    'project_id': self.pid, 'take_id': self.parent_id, **case})
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(len(self.store.get(self.pid)['takes']), 1)
        response = self.client.post('/repaint', json={
            'project_id': self.pid, 'take_id': self.parent_id,
            'start_s': 5, 'end_s': 10, 'lyrics_seed': ''})
        self.assertEqual(response.status_code, 400)

    def test_repaint_requires_a_succeeded_parent(self):
        failed = self.jobs.enqueue(self.pid, 8)
        self.jobs.start(self.pid, failed['take_id'], pid=os.getpid())
        self.jobs.fail(self.pid, failed['take_id'], 'boom')
        response = self.client.post('/repaint', json={
            'project_id': self.pid, 'take_id': failed['take_id'],
            'start_s': 1, 'end_s': 10})
        self.assertNotEqual(response.status_code, 200)
        self.assertIn('succeed', response.json()['detail'])

    def test_repaint_reports_progress_and_eta_like_a_normal_take(self):
        # 2.5 s, not 1: on a Windows runner a single /status round-trip can take longer than the render,
        # and then the take went from queued to succeeded between two polls. The behaviour was fine; the
        # test was racing the engine.
        make_runtime(self.runtime, delay=2.5)
        response = self.client.post('/repaint', json={
            'project_id': self.pid, 'take_id': self.parent_id,
            'start_s': 5, 'end_s': 12})
        self.assertEqual(response.status_code, 200)
        child_id = response.json()['take']['take_id']
        seen_running = False
        deadline = time.time() + 30
        while time.time() < deadline:
            body = self.client.get('/status').json()
            take = next(t for p in body['projects'] for t in p['takes']
                        if t['take_id'] == child_id)
            if take['state'] == 'running':
                seen_running = True
                self.assertIsInstance(take['progress'], int)
                self.assertIsInstance(take['eta_seconds'], int)
                break
            if take['state'] in ('succeeded', 'failed', 'cancelled'):
                break
            time.sleep(0.02)
        self.assertTrue(seen_running, 'take never reported running')
        self.assertEqual(self.wait(child_id)['state'], 'succeeded')


class ExtendTests(StudioCase):
    parent_seconds = 25

    def test_extend_appends_a_longer_take_with_splice_metadata(self):
        before_audio = self.jobs.read_audio(self.pid, self.parent_id)
        before_take = dict(self.parent_take())
        response = self.client.post('/extend', json={
            'project_id': self.pid, 'take_id': self.parent_id,
            'extend_seconds': 40, 'caption': 'big outro',
            'lyrics': '[outro]\nlet it ring'})
        self.assertEqual(response.status_code, 200)
        child_id = response.json()['take']['take_id']
        child = self.wait(child_id)
        self.assertEqual(child['state'], 'succeeded', child.get('error'))
        self.assertEqual(child['kind'], 'extension')
        self.assertEqual(child['parent_take_id'], self.parent_id)
        self.assertEqual(child['extend_seconds'], 40)
        self.assertEqual(child['source_duration'], 25.0)
        self.assertEqual(child['planned_duration'], 65.0)
        self.assertEqual(child['overlap_seconds'], 5.0)  # 20 % of the parent
        self.assertEqual(child['splice_start'], 20.0)
        self.assertEqual(child['lineage'], [self.parent_id])
        self.assertEqual(child['snapshot']['caption'], 'big outro')
        self.assertEqual(child['snapshot']['duration_seconds'], 65.0)

        frames, rate, _ = frames_of(self.jobs.read_audio(self.pid, child_id))
        self.assertEqual(rate, RATE)
        self.assertEqual(frames, 65 * RATE)

        # The parent is untouched and the child's head is its samples verbatim.
        self.assertEqual(self.parent_take(), before_take)
        self.assertEqual(self.jobs.read_audio(self.pid, self.parent_id), before_audio)
        parent_samples = samples(before_audio)
        child_samples = samples(self.jobs.read_audio(self.pid, child_id))
        splice = 20 * RATE * CHANNELS
        self.assertEqual(child_samples[:splice], parent_samples[:splice])

    def test_extend_seconds_are_bounded(self):
        for value in (9, 61, 0, -5, True, 20.5, '30'):
            with self.subTest(value=value):
                response = self.client.post('/extend', json={
                    'project_id': self.pid, 'take_id': self.parent_id,
                    'extend_seconds': value})
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(len(self.store.get(self.pid)['takes']), 1)

    def test_total_duration_ceiling_is_enforced_before_spawning(self):
        with patch.object(Jobs, 'audio_duration', return_value=590.0):
            with self.assertRaisesRegex(ValueError, '600'):
                self.jobs.enqueue_extension(self.pid, self.parent_id, 60, seed=1)

    def test_extend_requires_a_succeeded_parent(self):
        response = self.client.post('/extend', json={
            'project_id': self.pid, 'take_id': '00000000-0000-4000-8000-000000000000',
            'extend_seconds': 30})
        self.assertEqual(response.status_code, 404)


class EnginePreflightTests(unittest.TestCase):
    """Refuse bad geometry before a GPU process exists."""

    def test_repaint_refuses_out_of_contract_geometry(self):
        source = cached_tone(30)
        with tempfile.TemporaryDirectory() as output:
            for case in ({'start_s': 0, 'end_s': 2},
                         {'start_s': 0, 'end_s': 31},
                         {'start_s': 10, 'end_s': 5},
                         {'start_s': -1, 'end_s': 10}):
                with self.subTest(case=case), self.assertRaises(ValueError):
                    ace_engine.repaint(source_wav=source, caption='folk',
                                       lyrics='words', seed=1, output_dir=output,
                                       **case)

    def test_extend_refuses_a_short_source(self):
        source = cached_tone(15)
        with tempfile.TemporaryDirectory() as output, \
                self.assertRaisesRegex(ValueError, '20'):
            ace_engine.extend(source_wav=source, caption='folk', lyrics='words',
                              extend_seconds=30, seed=1, output_dir=output)


class LongformStitchTests(unittest.TestCase):
    def test_stitch_preserves_the_head_and_adds_the_extension(self):
        from music_studio.longform import stitch_wavs
        a = cached_tone(25)
        b = with_silence(a, 40)
        data, report = stitch_wavs(a, b, splice_start=20.0, overlap_seconds=5.0,
                                   expected_duration=65.0)
        frames, rate, _ = frames_of(data)
        self.assertEqual(frames, 65 * RATE)
        self.assertTrue(report['head_bit_identical'])
        self.assertLess(report['peak'], 1.0)
        self.assertEqual(samples(data)[:20 * RATE * CHANNELS],
                         samples(a)[:20 * RATE * CHANNELS])

    def test_full_scale_source_keeps_a_bit_identical_head(self):
        from music_studio.longform import stitch_wavs
        a = tone_wav(25, amplitude=1.0)
        b = with_silence(a, 40)
        data, report = stitch_wavs(a, b, splice_start=20.0, overlap_seconds=5.0,
                                   expected_duration=65.0)
        self.assertTrue(report['head_bit_identical'])
        self.assertEqual(samples(data)[:20 * RATE * CHANNELS],
                         samples(a)[:20 * RATE * CHANNELS])
        self.assertLess(report['peak'], 1.0)

    def test_stitch_refuses_non_48k_and_a_bad_plan(self):
        from music_studio.longform import stitch_wavs
        a = cached_tone(25)
        b = with_silence(a, 40)
        with self.assertRaises(ValueError):
            stitch_wavs(a, a, splice_start=20.0, overlap_seconds=8.0)
        with self.assertRaises(ValueError):
            stitch_wavs(a, b, splice_start=22.0, overlap_seconds=5.0)


if __name__ == '__main__':
    unittest.main()
