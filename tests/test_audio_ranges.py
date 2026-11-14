"""The player can only seek when the audio routes answer byte ranges.

A browser that asks ``Range: bytes=N-`` and gets the whole file back as 200 cannot jump into the
song: clicking the waveform or dragging the seek bar snaps playback back to the start.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from music_studio.jobs import Jobs  # noqa: E402
from fake_ace import wav_bytes  # noqa: E402
from test_contract import RouterTestCase  # noqa: E402


class AudioRangeTests(RouterTestCase):
    def take_url(self):
        pid = self.project()['project_id']
        self.store.approve(pid)
        jobs = Jobs(self.store)
        take = jobs.enqueue(pid, 1)
        jobs.start(pid, take['take_id'], pid=os.getpid())
        jobs.succeed(pid, take['take_id'], wav_bytes(frames=4000))
        return f'/projects/{pid}/takes/{take["take_id"]}/audio'

    def test_whole_file_says_it_accepts_ranges(self):
        response = self.client.get(self.take_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('accept-ranges'), 'bytes')
        self.assertEqual(response.content, wav_bytes(frames=4000))

    def test_a_range_request_gets_206_and_exactly_those_bytes(self):
        whole = wav_bytes(frames=4000)
        response = self.client.get(self.take_url(), headers={'Range': 'bytes=100-199'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, whole[100:200])
        self.assertEqual(response.headers['content-range'], f'bytes 100-199/{len(whole)}')
        self.assertEqual(response.headers['content-length'], '100')

    def test_an_open_range_runs_to_the_end(self):
        whole = wav_bytes(frames=4000)
        response = self.client.get(self.take_url(), headers={'Range': 'bytes=5000-'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, whole[5000:])

    def test_a_range_past_the_end_is_416(self):
        whole = wav_bytes(frames=4000)
        response = self.client.get(self.take_url(), headers={'Range': f'bytes={len(whole) + 10}-'})
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response.headers['content-range'], f'bytes */{len(whole)}')

    def test_the_demo_seeks_too(self):
        response = self.client.get('/demo/audio', headers={'Range': 'bytes=0-9'})
        if response.status_code == 404:
            self.skipTest('no demo fixture in this checkout')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), 10)
