"""T17: MiniMax Music 3 over ComfyUI — health, render, capabilities, residency.

The unit tests run against ``fake_comfy`` (a real HTTP server speaking the
ComfyUI routes) so submission, polling, /view, malformed replies, execution
errors, timeouts and missing weights are all exercised without a GPU. The one
live test talks to Spark E and is skipped whenever that host is unreachable.

No test accepts synthetic audio as a render result: the fake server serves a
real ffmpeg-encoded FLAC of a real tone, and the live test asserts the model's
own 44.1 kHz stereo WAV with nonzero levels.
"""
import io
import os
import shutil
import socket
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from fake_comfy import NODES, flac_bytes, object_info, tone_wav  # noqa: E402
from fake_comfy import _Server as FakeComfy  # noqa: E402
from support import install_shared_runtime  # noqa: E402
from music_studio.engines import (EngineBusy, EngineError, EngineUnavailable,  # noqa: E402
                                  MiniMaxM3ComfyEngine, RenderRequest, RequestInvalid)
from music_studio.engines import minimax_m3_comfy as mm3  # noqa: E402

# The fake server speaks the real save format: FLAC, transcoded to WAV by the
# adapter with ffmpeg. Without ffmpeg neither fixture nor render path exists,
# so those tests are skipped rather than silently weakened.
HAVE_FFMPEG = shutil.which('ffmpeg') is not None
FFMPEG_REASON = ('ffmpeg is not installed: FakeComfy serves FLAC and the '
                 'adapter transcodes it to WAV with ffmpeg')
FLAC = flac_bytes(tone_wav(2.0)) if HAVE_FFMPEG else b''
LIVE_URL = os.environ.get('MUSIC_STUDIO_LIVE_COMFY_URL', 'http://192.0.2.52:8188')


def live_reachable() -> bool:
    from urllib.parse import urlsplit
    parts = urlsplit(LIVE_URL)
    try:
        with socket.create_connection((parts.hostname, parts.port or 8188), timeout=1.5):
            return True
    except OSError:
        return False


def _request(**kw):
    fields = dict(caption='warm indie folk, fingerpicked guitar, plain female lead',
                  lyrics='[verse]\nThe kettle sings at half past six',
                  duration_seconds=10, seed=4242)
    fields.update(kw)
    return RenderRequest(**fields)


def _rms_and_peak(wav_bytes: bytes):
    with wave.open(io.BytesIO(wav_bytes), 'rb') as wav:
        frames = wav.readframes(wav.getnframes())
        width = wav.getsampwidth()
    samples = struct.unpack(f'<{len(frames) // 2}h', frames) if width == 2 else ()
    if not samples:
        return 0.0, 0
    peak = max(abs(s) for s in samples)
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
    return rms, peak


def setUpModule():
    # The local engine's default GPU lock comes from ace_engine.LOCK; pin it
    # into the shared fixture runtime so residency tests never write to a host
    # ACE install (or fail on a read-only shared drive).
    global _RUNTIME_PIN
    _RUNTIME_PIN = install_shared_runtime()


def tearDownModule():
    _RUNTIME_PIN.close()


@unittest.skipUnless(HAVE_FFMPEG, FFMPEG_REASON)
class FakeComfyCase(unittest.TestCase):
    server_config: dict = {}

    def setUp(self):
        self.server = FakeComfy(**self.server_config)
        self.server.__enter__()
        self.addCleanup(self.server.__exit__)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def engine(self, **kw):
        kw.setdefault('base_url', self.server.url)
        kw.setdefault('ffmpeg', '/usr/bin/ffmpeg' if Path('/usr/bin/ffmpeg').exists() else None)
        engine = MiniMaxM3ComfyEngine(**kw)
        engine.poll_seconds = 0.05
        return engine


class HealthTests(FakeComfyCase):
    def test_ready_only_when_nodes_and_named_weights_are_present(self):
        status = self.engine().health()
        self.assertEqual(status.state, 'ready', status.detail)
        self.assertTrue(status.ok)
        extra = status.extra
        self.assertIn(mm3.MODEL_FILES['unet'], extra['available_unets'])
        self.assertIn(mm3.MODEL_FILES['clip'], extra['available_clips'])
        self.assertIn(mm3.MODEL_FILES['vae'], extra['available_vaes'])
        self.assertEqual(extra['licence'], 'MiniMax-Music3')
        self.assertIn('flac', extra['save_formats'])
        self.assertEqual(extra['comfy_device'], 'Fake GPU')
        checks = {c.name: c for c in status.checks}
        self.assertTrue(checks['nodes'].ok)
        self.assertTrue(checks['models'].ok)
        self.assertTrue(checks['wav_output'].ok)

    def test_reports_every_required_node_when_one_is_missing(self):
        engine = self.engine()
        with FakeComfy(missing_nodes=('MiniMaxMusic3TextEncode',)) as broken:
            engine.base_url = broken.url
            status = engine.health()
            self.assertEqual(status.state, 'unavailable')
            self.assertIn('MiniMaxMusic3TextEncode', status.detail)
            with self.assertRaises(EngineUnavailable):
                engine.render(_request())

    def test_missing_weight_file_is_a_honest_unavailable(self):
        engine = self.engine()
        with FakeComfy(missing_models=(mm3.MODEL_FILES['vae'],)) as broken:
            engine.base_url = broken.url
            status = engine.health()
            self.assertEqual(status.state, 'unavailable')
            self.assertIn(mm3.MODEL_FILES['vae'], status.detail)
            with self.assertRaises(EngineUnavailable) as caught:
                engine.render(_request())
            self.assertIn('not listed by VAELoader', str(caught.exception))

    def test_unreachable_host_is_unavailable_with_the_url(self):
        engine = MiniMaxM3ComfyEngine(base_url='http://127.0.0.1:9', timeout=0.5)
        status = engine.health()
        self.assertEqual(status.state, 'unavailable')
        self.assertIn('127.0.0.1:9', status.detail)

    def test_no_ffmpeg_degrades_rather_than_pretending(self):
        status = self.engine(ffmpeg='').health()
        self.assertEqual(status.state, 'degraded')
        self.assertIn('ffmpeg', status.detail)
        # It is still offered (degraded) but a render fails honestly.
        with self.assertRaises(EngineError):
            self.engine(ffmpeg='').render(_request())

    def test_health_never_claims_repaint_or_continuation(self):
        engine = self.engine()
        caps = engine.capabilities
        self.assertTrue(caps.instrumental)
        self.assertTrue(caps.vocal_language)
        self.assertFalse(caps.repaint)
        self.assertFalse(caps.continuation)
        self.assertFalse(caps.batch)
        self.assertEqual((engine.limits.min_seconds, engine.limits.max_seconds), (10, 360))


class CapabilityGateTests(FakeComfyCase):
    def test_repaint_is_refused_before_any_http_request(self):
        with self.assertRaises(RequestInvalid):
            self.engine().render(_request(mode='repaint', source_wav=tone_wav(10.0),
                                          range_start_s=1.0, range_end_s=4.0))
        self.assertEqual(self.server.submitted, [])

    def test_extend_is_refused_before_any_http_request(self):
        with self.assertRaises(RequestInvalid):
            self.engine().render(_request(mode='extend', source_wav=tone_wav(10.0),
                                          extend_seconds=10))
        self.assertEqual(self.server.submitted, [])

    def test_duration_window_is_10_to_360_seconds(self):
        for seconds in (9, 9.9, 361):
            with self.assertRaises(RequestInvalid):
                self.engine().render(_request(duration_seconds=seconds))
        self.assertEqual(self.server.submitted, [])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.engine = MiniMaxM3ComfyEngine(base_url='http://127.0.0.1:9')

    def test_graph_uses_the_installed_weights_and_minimax_clip_type(self):
        graph = self.engine.build_workflow(_request())
        self.assertEqual(graph['1']['inputs']['unet_name'], mm3.MODEL_FILES['unet'])
        self.assertEqual(graph['2']['inputs']['type'], 'minimax')
        self.assertEqual(graph['3']['inputs']['vae_name'], mm3.MODEL_FILES['vae'])
        self.assertEqual(graph['4']['class_type'], 'MiniMaxMusic3TextEncode')
        self.assertEqual(graph['5']['inputs']['seconds'], ['4', 1])
        self.assertEqual(graph['9']['class_type'], 'SaveAudioAdvanced')
        self.assertEqual(graph['9']['inputs']['format'], 'flac')

    def test_graph_carries_caption_lyrics_seed_and_duration(self):
        request = _request(duration_seconds=47.5, seed=77)
        graph = self.engine.build_workflow(request)
        inputs = graph['4']['inputs']
        self.assertEqual(inputs['caption'], request.caption)
        self.assertEqual(inputs['lyrics'], request.lyrics)
        self.assertEqual(inputs['seed'], 77)
        self.assertEqual(inputs['max_duration'], 47.5)
        self.assertEqual(graph['7']['inputs']['seed'], 77)

    def test_instrumental_take_sends_empty_lyrics(self):
        graph = self.engine.build_workflow(_request(instrumental=True, lyrics='ignored'))
        self.assertEqual(graph['4']['inputs']['lyrics'], '')

    def test_every_submitted_node_class_is_one_health_probes(self):
        graph = self.engine.build_workflow(_request())
        probed = set(NODES)
        for node in graph.values():
            self.assertIn(node['class_type'], probed)


class RenderTests(FakeComfyCase):
    def test_render_returns_real_pcm_wav_with_provenance(self):
        status = self.engine().health()
        self.assertEqual(status.state, 'ready', status.detail)
        result = self.engine().render(_request())
        info = {k: result.metadata[k] for k in ('sample_rate', 'channels', 'audio_seconds')}
        self.assertEqual(info['sample_rate'], 44100, 'native MiniMax rate is preserved')
        self.assertEqual(info['channels'], 2)
        self.assertAlmostEqual(info['audio_seconds'], 2.0, delta=0.02)
        self.assertEqual(result.metadata['engine_id'], 'minimax_m3_comfy')
        self.assertEqual(result.metadata['seed'], 4242)
        self.assertEqual(result.metadata['model'], mm3.MODEL_FILES['unet'])
        self.assertEqual(result.metadata['source_format'], 'flac')
        self.assertEqual(result.metadata['transcode'], 'ffmpeg')
        self.assertEqual(result.metadata['licence'], 'MiniMax-Music3')
        self.assertEqual(result.metadata['model_licence'], 'MiniMax-Music3 Community License')
        self.assertIn('MiniMax-Music3', result.metadata['licence_notice'])
        rms, peak = _rms_and_peak(result.wav)
        self.assertGreater(peak, 2000)
        self.assertGreater(rms, 1000)

    def test_submitted_graph_is_the_built_graph(self):
        engine = self.engine()
        engine.render(_request(seed=99, duration_seconds=15))
        self.assertEqual(len(self.server.submitted), 1)
        graph = self.server.submitted[0]
        self.assertEqual(graph['4']['inputs']['seed'], 99)
        self.assertEqual(graph['4']['inputs']['max_duration'], 15.0)
        self.assertEqual(graph['9']['inputs']['filename_prefix'], 'music-studio/minimax-music3')

    def test_native_wav_output_skips_transcode(self):
        engine = self.engine()
        with FakeComfy(artifact=tone_wav(2.0), artifact_suffix='wav') as wav_server:
            engine.base_url = wav_server.url
            result = engine.render(_request())
        self.assertIsNone(result.metadata['transcode'])
        self.assertEqual(result.metadata['source_format'], 'wav')
        self.assertEqual(result.metadata['sample_rate'], 44100)

    def test_success_without_audio_is_an_error_not_an_empty_take(self):
        engine = self.engine()
        with FakeComfy(history_mode='empty_outputs') as broken:
            engine.base_url = broken.url
            with self.assertRaises(EngineError) as caught:
                engine.render(_request())
        self.assertIn('saved no audio', str(caught.exception))

    def test_execution_error_is_surfaced(self):
        engine = self.engine()
        with FakeComfy(history_mode='error') as broken:
            engine.base_url = broken.url
            with self.assertRaises(EngineError) as caught:
                engine.render(_request())
        self.assertIn('CUDA out of memory', str(caught.exception))

    def test_malformed_history_json_is_refused(self):
        engine = self.engine()
        with FakeComfy(history_mode='malformed') as broken:
            engine.base_url = broken.url
            with self.assertRaises(EngineError) as caught:
                engine.render(_request())
        self.assertIn('non-JSON', str(caught.exception))

    def test_malformed_prompt_reply_is_refused(self):
        engine = self.engine()
        with FakeComfy(prompt_mode='malformed') as broken:
            engine.base_url = broken.url
            with self.assertRaises(EngineError) as caught:
                engine.render(_request())
        self.assertIn('non-JSON', str(caught.exception))

    def test_prompt_http_error_includes_the_reason(self):
        engine = self.engine()
        with FakeComfy(prompt_mode='http500') as broken:
            engine.base_url = broken.url
            with self.assertRaises(EngineError) as caught:
                engine.render(_request())
        self.assertIn('HTTP 500', str(caught.exception))
        self.assertIn('boom', str(caught.exception))

    def test_view_404_is_reported(self):
        engine = self.engine()
        with FakeComfy(view_mode='http404') as broken:
            engine.base_url = broken.url
            with self.assertRaises(EngineError) as caught:
                engine.render(_request())
        self.assertIn('HTTP 404', str(caught.exception))

    def test_timeout_raises_and_does_not_invent_audio(self):
        engine = self.engine()
        with FakeComfy(complete_after=30.0) as slow:
            engine.base_url = slow.url
            started = time.monotonic()
            with self.assertRaises(EngineError) as caught:
                engine.render(_request(timeout_seconds=1))
        self.assertIn('timed out', str(caught.exception))
        self.assertLess(time.monotonic() - started, 10)

    def test_ffmpeg_failure_is_reported(self):
        engine = self.engine()
        with FakeComfy(artifact=b'fLaC not really a flac stream') as broken:
            engine.base_url = broken.url
            with self.assertRaises(EngineError) as caught:
                engine.render(_request())
        self.assertIn('ffmpeg', str(caught.exception).lower())


class ResidencyTests(FakeComfyCase):
    def test_default_guard_is_the_ace_gpu_lock_not_the_studio_worker_lease(self):
        import ace_engine
        self.assertIsNone(self.engine().gpu_lease_path)
        self.assertEqual(mm3._default_gpu_lease_path(), Path(ace_engine.LOCK))

    def test_a_held_ace_gpu_lock_makes_a_local_comfy_render_busy(self):
        import ace_engine
        import fcntl
        Path(ace_engine.LOCK).parent.mkdir(parents=True, exist_ok=True)
        held = Path(ace_engine.LOCK).open('a')
        self.addCleanup(held.close)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            self.assertEqual(self.engine().health().state, 'busy')
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)

    def test_the_studio_worker_lease_does_not_block_a_local_comfy_render(self):
        # The worker holds .worker.lock for its whole life; consulting it here
        # would report busy forever even while the GPU is idle.
        import fcntl
        import os
        from music_studio import service as studio_service
        # The studio's own resolver, not the host's: a clone has no `plugins` package, and this
        # test is about where the lease lives, not about who provides that path.
        with patch.dict(os.environ, {'HERMES_HOME': self.tmp.name}):
            lease = (Path(studio_service.plugin_data_dir('hermes-music-studio'))
                     / '.worker.lock')
            held = lease.open('a')
            self.addCleanup(held.close)
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                self.assertIsNone(self.engine()._gpu_lease_conflict())
            finally:
                fcntl.flock(held, fcntl.LOCK_UN)

    def test_same_host_ace_lease_makes_the_engine_busy(self):
        lease = Path(self.tmp.name) / '.worker.lock'
        engine = self.engine(gpu_lease_path=lease)
        self.assertEqual(engine.health().state, 'ready')
        held = lease.open('a')
        self.addCleanup(held.close)
        import fcntl
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = engine.health()
        self.assertEqual(status.state, 'busy')
        self.assertTrue(status.available)
        with self.assertRaises(EngineBusy):
            engine.render(_request())
        self.assertEqual(self.server.submitted, [])

    def test_remote_comfy_is_not_blocked_by_a_local_ace_lease(self):
        lease = Path(self.tmp.name) / '.worker.lock'
        held = lease.open('a')
        self.addCleanup(held.close)
        import fcntl
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        remote = MiniMaxM3ComfyEngine(base_url='http://10.255.255.1:8188',
                                      gpu_lease_path=lease)
        self.assertFalse(remote._host_is_local())
        self.assertIsNone(remote._gpu_lease_conflict())


def _live_skip_reason():
    # A real GPU render is opt-in: the default sweep must not occupy Spark E,
    # and it needs ffmpeg locally to transcode the returned FLAC.
    if os.environ.get('MUSIC_STUDIO_LIVE_TESTS') != '1':
        return 'live GPU render is opt-in: set MUSIC_STUDIO_LIVE_TESTS=1'
    if not HAVE_FFMPEG:
        return FFMPEG_REASON
    if not live_reachable():
        return f'ComfyUI at {LIVE_URL} is unreachable'
    return None


_LIVE_SKIP = _live_skip_reason()


@unittest.skipUnless(_LIVE_SKIP is None, _LIVE_SKIP or '')
class LiveMiniMaxTests(unittest.TestCase):
    """One real render against Spark E — skipped when the host is down."""

    def test_live_ten_second_render_is_real_audio(self):
        engine = MiniMaxM3ComfyEngine(base_url=LIVE_URL, timeout=30)
        engine.poll_seconds = 1.0
        status = engine.health()
        self.assertEqual(status.state, 'ready', status.detail)
        started = time.monotonic()
        result = engine.render(_request(duration_seconds=10, seed=4242,
                                        timeout_seconds=600))
        wall = time.monotonic() - started
        meta = result.metadata
        self.assertEqual(meta['sample_rate'], 44100)
        self.assertEqual(meta['channels'], 2)
        self.assertEqual(meta['licence'], 'MiniMax-Music3')
        self.assertIn('MiniMax-Music3', meta['licence_notice'])
        self.assertGreaterEqual(meta['audio_seconds'], 8.0)
        self.assertLessEqual(meta['audio_seconds'], 11.0)
        rms, peak = _rms_and_peak(result.wav)
        self.assertGreater(peak, 500, 'render must not be silence')
        self.assertGreater(rms, 50, 'render must have audible level')
        self.assertGreater(len(result.wav), 100_000)
        print(f'\n[live] {meta["audio_seconds"]}s {meta["sample_rate"]} Hz '
              f'{meta["channels"]}ch {len(result.wav)} bytes in {wall:.1f}s wall '
              f'(rms={rms:.0f}, peak={peak})', file=sys.stderr)


if __name__ == '__main__':
    unittest.main()
