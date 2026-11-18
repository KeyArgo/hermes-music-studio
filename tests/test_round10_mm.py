"""Round 10: MiniMax weight alternatives, and tiled decode from a setting.

Health used to require the int8 file names and never read MODEL_ALTERNATIVES.
A server that only has the fp16 or bf16 weights was reported unavailable, and
the graph kept naming the missing int8 file. tiled_decode stayed False no
matter how much VRAM ComfyUI reported.

These tests speak HTTP. A tiny server answers /system_stats and /object_info
the way ComfyUI does, including vram_total in bytes (same unit as 24 << 30 in
fake_comfy).
"""
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from music_studio import settings
from music_studio.engines.contract import RenderRequest
from music_studio.engines.minimax_m3_comfy import (
    MODEL_ALTERNATIVES, MODEL_FILES, MiniMaxM3ComfyEngine)

FP16_BARE = MODEL_ALTERNATIVES['unet'][0]
FP16_PREFIXED = MODEL_ALTERNATIVES['unet'][1]
FP32_BARE = MODEL_ALTERNATIVES['unet'][2]
BF16_PREFIXED = MODEL_ALTERNATIVES['clip'][1]
GB = 1 << 30


def _request():
    return RenderRequest(caption='warm guitar', lyrics='one line',
                         duration_seconds=12, seed=7)


class _Stats:
    """One fake ComfyUI: model lists, vram_total, and the decode nodes."""

    def __init__(self, *, unets, clips, vaes, vram_total=24 * GB, devices=True,
                 tiled_node=True, plain_node=True):
        self.unets = list(unets)
        self.clips = list(clips)
        self.vaes = list(vaes)
        self.vram_total = vram_total
        self.devices = devices
        self.tiled_node = tiled_node
        self.plain_node = plain_node
        self.info = self._object_info()

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

        self.httpd = Server(('127.0.0.1', 0), self._handler())
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def _object_info(self):
        info = {
            'MiniMaxMusic3TextEncode': {'input': {'required': {}}},
            'EmptyMiniMaxMusic3LatentAudio': {'input': {'required': {}}},
            'UNETLoader': {'input': {'required': {'unet_name': [self.unets, {}]}}},
            'CLIPLoader': {'input': {'required': {
                'clip_name': [self.clips, {}],
                'type': [['minimax', 'stable_diffusion'], {}]}}},
            'VAELoader': {'input': {'required': {'vae_name': [self.vaes, {}]}}},
            'KSampler': {'input': {'required': {}}},
            'ConditioningZeroOut': {'input': {'required': {}}},
            'SaveAudioAdvanced': {'input': {'required': {
                'format': [['flac', 'mp3'], {}]}}},
        }
        if self.plain_node:
            info['VAEDecodeAudio'] = {'input': {'required': {}}}
        if self.tiled_node:
            info['VAEDecodeAudioTiled'] = {'input': {'required': {}}}
        return info

    def _handler(self):
        stats = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                return

            def _send(self, code, body):
                payload = json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == '/system_stats':
                    body = {'system': {'comfyui_version': 'fake'}}
                    if stats.devices:
                        device = {'name': 'Fake GPU'}
                        if stats.vram_total is not None:
                            device['vram_total'] = stats.vram_total
                            device['vram_free'] = stats.vram_total
                        body['devices'] = [device]
                    return self._send(200, body)
                if path.startswith('/object_info/'):
                    node = path.rsplit('/', 1)[-1]
                    if node not in stats.info:
                        return self._send(404, {'error': 'unknown node'})
                    return self._send(200, {node: stats.info[node]})
                if path == '/object_info':
                    return self._send(200, stats.info)
                return self._send(404, {'error': path})

        return Handler

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    @property
    def url(self):
        host, port = self.httpd.server_address[:2]
        return f'http://{host}:{port}'


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('COMFYUI_URL', raising=False)
    return tmp_path


def _engine(server, **kw):
    ffmpeg = shutil.which('ffmpeg') or ''
    kw.setdefault('base_url', server.url)
    kw.setdefault('timeout', 4)
    kw.setdefault('ffmpeg', ffmpeg)
    return MiniMaxM3ComfyEngine(**kw)


def _checks(status):
    return {item.name: item for item in status.checks}


def _graph(engine):
    return engine.build_workflow(_request())


# -- weight alternatives ---------------------------------------------------
def test_an_fp16_unet_and_bf16_clip_are_accepted_when_int8_is_missing(studio):
    """The int8 names are absent. The first listed alternative for each role is enough."""
    with _Stats(unets=[FP16_PREFIXED],
                clips=[BF16_PREFIXED],
                vaes=[MODEL_FILES['vae']]) as server:
        engine = _engine(server)
        status = engine.health()
        graph = _graph(engine)
    assert _checks(status)['models'].ok, status.detail
    assert _checks(status)['nodes'].ok, status.detail
    assert status.state != 'unavailable'
    assert graph['1']['inputs']['unet_name'] == FP16_PREFIXED
    assert graph['2']['inputs']['clip_name'] == BF16_PREFIXED
    assert graph['3']['inputs']['vae_name'] == MODEL_FILES['vae']
    assert status.extra['unet'] == FP16_PREFIXED
    assert status.extra['clip'] == BF16_PREFIXED


def test_the_graph_uses_the_listed_string_when_the_folder_prefix_differs(studio):
    """ComfyUI may list the same file under a Windows path. The graph must copy that string."""
    listed = 'diffusion_models\\minimax_music3_dit_fp16.safetensors'
    with _Stats(unets=[listed],
                clips=[MODEL_FILES['clip']],
                vaes=[MODEL_FILES['vae']]) as server:
        engine = _engine(server)
        status = engine.health()
        graph = _graph(engine)
    assert _checks(status)['models'].ok, status.detail
    assert graph['1']['inputs']['unet_name'] == listed
    assert status.extra['unet'] == listed


def test_a_later_alternative_is_used_when_the_earlier_ones_are_absent(studio):
    with _Stats(unets=[FP32_BARE],
                clips=[MODEL_ALTERNATIVES['clip'][0]],
                vaes=[MODEL_FILES['vae']]) as server:
        engine = _engine(server)
        status = engine.health()
        graph = _graph(engine)
    assert _checks(status)['models'].ok, status.detail
    assert graph['1']['inputs']['unet_name'] == FP32_BARE
    assert graph['2']['inputs']['clip_name'] == MODEL_ALTERNATIVES['clip'][0]


def test_the_int8_file_wins_when_an_alternative_is_also_listed(studio):
    listed_int8 = 'sub/' + MODEL_FILES['unet']
    with _Stats(unets=[listed_int8, FP16_BARE],
                clips=[MODEL_FILES['clip'], BF16_PREFIXED],
                vaes=[MODEL_FILES['vae']]) as server:
        engine = _engine(server)
        status = engine.health()
        graph = _graph(engine)
    assert _checks(status)['models'].ok, status.detail
    assert graph['1']['inputs']['unet_name'] == listed_int8
    assert graph['2']['inputs']['clip_name'] == MODEL_FILES['clip']


def test_no_alternative_keeps_the_int8_name_in_the_unavailable_detail(studio):
    with _Stats(unets=['diffusion_models/other.safetensors'],
                clips=[MODEL_FILES['clip']],
                vaes=[MODEL_FILES['vae']]) as server:
        status = _engine(server).health()
    assert status.state == 'unavailable'
    assert MODEL_FILES['unet'] in status.detail
    assert 'not listed by UNETLoader' in status.detail


def test_a_missing_vae_still_blocks_health_after_the_unet_alternative_matches(studio):
    with _Stats(unets=[FP16_PREFIXED],
                clips=[MODEL_FILES['clip']],
                vaes=['vae/other.safetensors']) as server:
        status = _engine(server).health()
    assert status.state == 'unavailable'
    assert MODEL_FILES['vae'] in status.detail
    assert 'not listed by UNETLoader' not in status.detail


# -- minimax_tiled_decode --------------------------------------------------
def test_minimax_tiled_decode_defaults_to_auto_and_only_accepts_three_values(studio):
    assert settings.DEFAULTS['minimax_tiled_decode'] == 'auto'
    assert settings.read(studio)['minimax_tiled_decode'] == 'auto'
    for value in ('auto', 'on', 'off'):
        saved = settings.write(studio, {'minimax_tiled_decode': value})
        assert saved['minimax_tiled_decode'] == value
        assert settings.read(studio)['minimax_tiled_decode'] == value
    with pytest.raises(ValueError, match='minimax_tiled_decode'):
        settings.write(studio, {'minimax_tiled_decode': 'yes'})


def test_saving_another_setting_keeps_minimax_tiled_decode(studio):
    settings.write(studio, {'minimax_tiled_decode': 'off',
                            'comfy_url': 'http://192.168.1.50:8188'})
    saved = settings.write(studio, {'scan_hosts': 'studio-pc'})
    assert saved['minimax_tiled_decode'] == 'off'
    assert saved['comfy_url'] == 'http://192.168.1.50:8188'
    assert saved['scan_hosts'] == 'studio-pc'


def _health_graph(studio, server, mode=None, pinned=None):
    if mode is not None:
        settings.write(studio, {'minimax_tiled_decode': mode})
    kw = {}
    if pinned is not None:
        kw['tiled_decode'] = pinned
    engine = _engine(server, **kw)
    status = engine.health()
    return status, _graph(engine)


def test_auto_turns_tiled_decode_on_only_when_vram_is_under_12gb(studio):
    """auto reads comfy_vram_total. Under 12 GB uses the tiled node. 12 GB and above does not."""
    weights = dict(unets=[MODEL_FILES['unet']], clips=[MODEL_FILES['clip']],
                   vaes=[MODEL_FILES['vae']])
    with _Stats(vram_total=(8 * GB), **weights) as small:
        status, graph = _health_graph(studio, small, mode='auto')
    assert _checks(status)['nodes'].ok, status.detail
    assert graph['8']['class_type'] == 'VAEDecodeAudioTiled'
    assert graph['8']['inputs']['tile_size'] == 1536
    assert status.extra['comfy_vram_total'] == 8 * GB

    with _Stats(vram_total=(12 * GB) - 1, **weights) as just_under:
        status, graph = _health_graph(studio, just_under)
    assert graph['8']['class_type'] == 'VAEDecodeAudioTiled'

    with _Stats(vram_total=(12 * GB), **weights) as exact:
        status, graph = _health_graph(studio, exact)
    assert graph['8']['class_type'] == 'VAEDecodeAudio'
    assert 'tile_size' not in graph['8']['inputs']

    with _Stats(vram_total=(24 * GB), **weights) as large:
        status, graph = _health_graph(studio, large)
    assert graph['8']['class_type'] == 'VAEDecodeAudio'
    assert status.extra['comfy_vram_total'] == 24 * GB


def test_auto_stays_off_when_comfy_reports_no_vram_total(studio):
    weights = dict(unets=[MODEL_FILES['unet']], clips=[MODEL_FILES['clip']],
                   vaes=[MODEL_FILES['vae']])
    with _Stats(devices=False, **weights) as server:
        status, graph = _health_graph(studio, server, mode='auto')
    assert _checks(status)['nodes'].ok, status.detail
    assert 'comfy_vram_total' not in status.extra
    assert graph['8']['class_type'] == 'VAEDecodeAudio'

    with _Stats(vram_total=None, **weights) as missing:
        status, graph = _health_graph(studio, missing)
    assert graph['8']['class_type'] == 'VAEDecodeAudio'


def test_on_uses_tiled_decode_on_a_large_card_and_off_does_not_on_a_small_one(studio):
    weights = dict(unets=[MODEL_FILES['unet']], clips=[MODEL_FILES['clip']],
                   vaes=[MODEL_FILES['vae']])
    with _Stats(vram_total=(24 * GB), **weights) as large:
        status, graph = _health_graph(studio, large, mode='on')
    assert _checks(status)['nodes'].ok, status.detail
    assert graph['8']['class_type'] == 'VAEDecodeAudioTiled'

    with _Stats(vram_total=(8 * GB), **weights) as small:
        status, graph = _health_graph(studio, small, mode='off')
    assert _checks(status)['nodes'].ok, status.detail
    assert graph['8']['class_type'] == 'VAEDecodeAudio'


def test_on_is_unavailable_when_the_tiled_node_is_missing(studio):
    with _Stats(unets=[MODEL_FILES['unet']], clips=[MODEL_FILES['clip']],
                vaes=[MODEL_FILES['vae']], vram_total=24 * GB, tiled_node=False) as server:
        status, _graph_ignored = _health_graph(studio, server, mode='on')
    assert status.state == 'unavailable'
    assert 'VAEDecodeAudioTiled' in status.detail


def test_auto_on_a_small_card_requires_the_tiled_node(studio):
    with _Stats(unets=[MODEL_FILES['unet']], clips=[MODEL_FILES['clip']],
                vaes=[MODEL_FILES['vae']], vram_total=8 * GB, tiled_node=False) as server:
        status, _graph_ignored = _health_graph(studio, server, mode='auto')
    assert status.state == 'unavailable'
    assert 'VAEDecodeAudioTiled' in status.detail


def test_health_reads_a_setting_saved_after_the_engine_was_built(studio):
    with _Stats(unets=[MODEL_FILES['unet']], clips=[MODEL_FILES['clip']],
                vaes=[MODEL_FILES['vae']], vram_total=24 * GB) as server:
        engine = _engine(server)
        settings.write(studio, {'minimax_tiled_decode': 'on'})
        status = engine.health()
        graph = _graph(engine)
    assert _checks(status)['nodes'].ok, status.detail
    assert graph['8']['class_type'] == 'VAEDecodeAudioTiled'


def test_an_explicit_tiled_decode_flag_wins_over_the_setting_and_vram(studio):
    weights = dict(unets=[MODEL_FILES['unet']], clips=[MODEL_FILES['clip']],
                   vaes=[MODEL_FILES['vae']])
    settings.write(studio, {'minimax_tiled_decode': 'on'})
    with _Stats(vram_total=(8 * GB), **weights) as small:
        status, graph = _health_graph(studio, small, pinned=False)
    assert graph['8']['class_type'] == 'VAEDecodeAudio'
    assert _checks(status)['nodes'].ok, status.detail

    settings.write(studio, {'minimax_tiled_decode': 'off'})
    with _Stats(vram_total=(24 * GB), **weights) as large:
        status, graph = _health_graph(studio, large, pinned=True)
    assert graph['8']['class_type'] == 'VAEDecodeAudioTiled'
    assert _checks(status)['nodes'].ok, status.detail
