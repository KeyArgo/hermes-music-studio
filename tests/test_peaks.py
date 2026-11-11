"""Waveform peaks: server-side streaming, sidecar cache, range and symlink safety."""
import io
import os
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.plugin_api import router  # noqa: E402
from music_studio import service  # noqa: E402
from music_studio.jobs import Jobs  # noqa: E402

RATE = 48000
CHANNELS = 2


def tone_wav(seconds=5.0, amplitude=0.5, frequency=440.0, rate=RATE):
    """Stereo 16-bit sine at a known amplitude, for exact peak expectations."""
    import math
    frames = int(round(seconds * rate))
    out = bytearray()
    step = 2 * math.pi * frequency / rate
    for i in range(frames):
        value = int(round(amplitude * 32767 * math.sin(step * i)))
        out += struct.pack('<hh', value, value)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setparams((CHANNELS, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(bytes(out))
    return buf.getvalue()


def silent_wav(seconds=5.0, rate=RATE):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setparams((CHANNELS, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x00\x00\x00\x00' * int(round(seconds * rate)))
    return buf.getvalue()


class PeaksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.store = service.store()
        project = self.store.create(caption='folk', lyrics='hello', duration_seconds=10)
        self.pid = project['project_id']
        self.store.approve(self.pid)
        jobs = Jobs(self.store)
        take = jobs.enqueue(self.pid, 1)
        jobs.start(self.pid, take['take_id'], pid=os.getpid())
        jobs.succeed(self.pid, take['take_id'], tone_wav(5.0))
        self.tid = take['take_id']

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def peaks(self, buckets=None):
        url = f'/projects/{self.pid}/takes/{self.tid}/peaks'
        if buckets is not None:
            url += f'?buckets={buckets}'
        return self.client.get(url)

    def test_payload_shape_and_default_bucket_count(self):
        response = self.peaks()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {'success', 'buckets', 'duration_seconds', 'peaks'})
        self.assertTrue(body['success'])
        self.assertEqual(body['buckets'], 512)
        self.assertAlmostEqual(body['duration_seconds'], 5.0, places=3)
        self.assertEqual(len(body['peaks']), 512)
        for bucket in body['peaks']:
            self.assertEqual(set(bucket), {'min', 'max', 'rms'})
            self.assertLessEqual(bucket['min'], bucket['max'])
            self.assertGreaterEqual(bucket['rms'], 0.0)

    def test_bucket_counts_at_the_contract_boundaries(self):
        for buckets in (64, 512, 2048):
            with self.subTest(buckets=buckets):
                response = self.peaks(buckets)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['buckets'], buckets)
                self.assertEqual(len(response.json()['peaks']), buckets)

    def test_out_of_range_and_garbage_bucket_counts_are_rejected(self):
        for buckets in (0, 1, 63, 2049, 4096, -5):
            with self.subTest(buckets=buckets):
                response = self.peaks(buckets)
                self.assertEqual(response.status_code, 400)
                self.assertIn('buckets', response.json()['detail'])
        self.assertNotEqual(self.peaks('lots').status_code, 200)

    def test_peaks_match_the_waveform(self):
        import numpy as np
        body = self.peaks(64).json()
        with wave.open(io.BytesIO(tone_wav(5.0)), 'rb') as wav:
            frames = wav.getnframes()
            samples = np.frombuffer(wav.readframes(frames), dtype='<i2').astype(np.float64) / 32768.0
        for index in (0, 13, 31, 47, 63):
            start = index * frames // 64
            end = (index + 1) * frames // 64
            window = samples[start * CHANNELS:end * CHANNELS]
            bucket = body['peaks'][index]
            self.assertAlmostEqual(bucket['min'], float(window.min()), places=6)
            self.assertAlmostEqual(bucket['max'], float(window.max()), places=6)
            self.assertAlmostEqual(bucket['rms'], float(np.sqrt(np.mean(window ** 2))), places=6)

    def test_cache_sidecar_is_written_and_reused(self):
        first = self.peaks(128)
        self.assertEqual(first.status_code, 200)
        sidecar = self.store.root / f'{self.tid}.peaks.json'
        self.assertTrue(sidecar.is_file())
        stamp = sidecar.stat().st_mtime_ns
        second = self.peaks(128)
        self.assertEqual(second.json(), first.json())
        # A cache hit does not rewrite the sidecar.
        self.assertEqual(sidecar.stat().st_mtime_ns, stamp)

    def test_cache_is_invalidated_by_size_and_mtime(self):
        self.assertEqual(self.peaks(64).json()['duration_seconds'], 5.0)
        path = self.store.root / f'{self.tid}.wav'
        # Same byte size, different audio, newer mtime: size alone would miss it.
        path.write_bytes(silent_wav(5.0))
        os.utime(path, ns=(1, 2))
        body = self.peaks(64).json()
        self.assertEqual(body['duration_seconds'], 5.0)
        self.assertEqual(body['peaks'][10]['max'], 0.0)
        self.assertEqual(body['peaks'][10]['rms'], 0.0)

    def test_symlinked_take_audio_is_refused(self):
        path = self.store.root / f'{self.tid}.wav'
        target = self.store.root / 'elsewhere.wav'
        target.write_bytes(tone_wav(5.0))
        path.unlink()
        path.symlink_to(target)
        response = self.peaks(64)
        self.assertNotEqual(response.status_code, 200)

    def test_symlinked_sidecar_is_not_followed(self):
        target = self.store.root / 'hostile.json'
        target.write_text('{"source": {"size": 1, "mtime_ns": 1}, "peaks": {"64": []}}')
        (self.store.root / f'{self.tid}.peaks.json').symlink_to(target)
        response = self.peaks(64)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['peaks']), 64)


if __name__ == '__main__':
    unittest.main()
