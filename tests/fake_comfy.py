"""A scriptable ComfyUI HTTP server for adapter tests.

The real ComfyUI API surface the MiniMax adapter touches is small: system
stats, object_info (node schemas *and* the model lists they carry), prompt
submission, history polling and /view for the saved file. This module serves
exactly those routes from memory so tests can pin success, malformed replies,
execution errors, timeouts and missing models without a GPU.

Nothing here synthesises a *take*: the success path serves a real FLAC/WAV
fixture produced by ffmpeg (or a caller-supplied WAV), so the adapter's
transcode and WAV validation are exercised for real.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

NODES = ('UNETLoader', 'CLIPLoader', 'VAELoader', 'KSampler', 'VAEDecodeAudio',
         'ConditioningZeroOut', 'MiniMaxMusic3TextEncode',
         'EmptyMiniMaxMusic3LatentAudio', 'SaveAudioAdvanced')

MODELS = {
    'UNETLoader': ('unet_name', ['diffusion_models/minimax_music3_dit_int8_convrot.safetensors',
                                  'diffusion_models/other.safetensors']),
    'CLIPLoader': ('clip_name', ['text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors',
                                  'text_encoders/other.safetensors']),
    'VAELoader': ('vae_name', ['vae/minimax_music3_dav.safetensors',
                                'vae/other.safetensors']),
}

FORMAT_SPEC = {
    'required': {
        'audio': ['AUDIO', {}],
        'filename_prefix': ['STRING', {'default': 'audio/ComfyUI'}],
        'format': ['COMFY_DYNAMICCOMBO_V3', {'options': [
            {'key': 'flac', 'inputs': {'required': {}}},
            {'key': 'mp3', 'inputs': {'required': {'quality': ['COMBO', {'options': ['V0']}]}}},
            {'key': 'opus', 'inputs': {'required': {'quality': ['COMBO', {'options': ['128k']}]}}},
        ]}],
    },
    'hidden': {},
}


def tone_wav(seconds=2.0, rate=44100, channels=2, amplitude=9000):
    """A real, audible WAV (sine) — never silence, so level checks mean something."""
    import math
    import struct
    frames = int(seconds * rate)
    values = [int(amplitude * math.sin(2 * math.pi * 220 * i / rate)) for i in range(frames)]
    interleaved = [v for v in values for _ in range(channels)]
    buf = __import__('io').BytesIO()
    with wave.open(buf, 'wb') as out:
        out.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
        out.writeframes(struct.pack(f'<{len(interleaved)}h', *interleaved))
    return buf.getvalue()


def flac_bytes(wav: bytes, rate=44100) -> bytes:
    """Encode a WAV fixture to FLAC with ffmpeg (the server's real save format)."""
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg is required to build the FLAC fixture')
    with tempfile.TemporaryDirectory(prefix='fake-comfy-') as tmp:
        src, dst = Path(tmp) / 'in.wav', Path(tmp) / 'out.flac'
        src.write_bytes(wav)
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src),
                        '-ar', str(rate), '-acodec', 'flac', str(dst)], check=True)
        return dst.read_bytes()


def object_info(missing_nodes=(), missing_models=(), save_formats=('flac', 'mp3', 'opus')):
    info = {}
    for node in NODES:
        if node in missing_nodes:
            continue
        if node in MODELS:
            field, options = MODELS[node]
            required = {field: [options, {}]}
            if node == 'CLIPLoader':
                required['type'] = [['stable_diffusion', 'minimax'], {}]
            info[node] = {'input': {'required': required}}
        elif node == 'SaveAudioAdvanced':
            spec = json.loads(json.dumps(FORMAT_SPEC))
            spec['required']['format'][1]['options'] = [
                {'key': key, 'inputs': {'required': {}}} for key in save_formats]
            info[node] = {'input': spec}
        elif node == 'MiniMaxMusic3TextEncode':
            info[node] = {'input': {'required': {
                'clip': ['CLIP', {}], 'caption': ['STRING', {}], 'lyrics': ['STRING', {}],
                'seed': ['INT', {}], 'max_duration': ['FLOAT', {'min': 0.04, 'max': 360.0}],
                'cfg_scale': ['FLOAT', {}], 'top_k': ['INT', {}]}}}
        elif node == 'EmptyMiniMaxMusic3LatentAudio':
            info[node] = {'input': {'required': {
                'seconds': ['FLOAT', {'min': 0.04, 'max': 360.0}],
                'batch_size': ['INT', {'min': 1}]}}}
        elif node == 'VAEDecodeAudio':
            info[node] = {'input': {'required': {'samples': ['LATENT', {}], 'vae': ['VAE', {}]}}}
        else:
            info[node] = {'input': {'required': {}}}
    for node, (field, options) in MODELS.items():
        if node in info and missing_models:
            info[node]['input']['required'][field][0] = [
                o for o in options if o not in missing_models]
    return info


class _Server:
    def __init__(self, **config):
        self.config = {
            'artifact': None,             # bytes served by /view
            'artifact_suffix': 'flac',
            'complete_after': 0.05,       # seconds of fake compute
            'history_mode': 'ok',         # ok | malformed | error | empty_outputs
            'prompt_mode': 'ok',          # ok | malformed | http500
            'view_mode': 'ok',            # ok | http404
            'missing_nodes': (),
            'missing_models': (),
            'save_formats': ('flac', 'mp3', 'opus'),
        }
        self.config.update(config)
        self.submitted: list[dict] = []
        self.queued_at: dict[str, float] = {}
        if self.config['artifact'] is None:
            self.config['artifact'] = flac_bytes(tone_wav(2.0))

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

        self.httpd = Server(('127.0.0.1', 0), self._handler())
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    # -- lifecycle -------------------------------------------------------
    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f'http://{host}:{port}'

    # -- request handling ------------------------------------------------
    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):  # keep unittest output clean
                return

            def _send(self, code, body: bytes | dict | list = b'',
                      content_type='application/json'):
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True

            def do_GET(self):
                parts = urlsplit(self.path)
                path, query = parts.path, parse_qs(parts.query)
                cfg = server.config
                if path == '/system_stats':
                    return self._send(200, {'system': {'comfyui_version': 'fake'},
                                            'devices': [{'name': 'Fake GPU',
                                                         'vram_total': 24 << 30,
                                                         'vram_free': 20 << 30}]})
                if path == '/object_info':
                    return self._send(200, object_info(cfg['missing_nodes'],
                                                       cfg['missing_models'],
                                                       cfg['save_formats']))
                if path.startswith('/object_info/'):
                    node = path.rsplit('/', 1)[-1]
                    info = object_info(cfg['missing_nodes'], cfg['missing_models'],
                                       cfg['save_formats'])
                    if node not in info:
                        return self._send(404, {'error': 'unknown node'})
                    return self._send(200, {node: info[node]})
                if path == '/queue':
                    return self._send(200, {'queue_running': [], 'queue_pending': []})
                if path.startswith('/history/'):
                    return self._history(path.rsplit('/', 1)[-1])
                if path == '/view':
                    if cfg['view_mode'] == 'http404':
                        return self._send(404, {'error': 'no such file'})
                    data = cfg['artifact']
                    ctype = 'audio/flac' if cfg['artifact_suffix'] == 'flac' else 'audio/wav'
                    return self._send(200, data, ctype)
                return self._send(404, {'error': f'no route {path}'})

            def do_POST(self):
                path = urlsplit(self.path).path
                if path != '/prompt':
                    return self._send(404, {'error': f'no route {path}'})
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length)
                cfg = server.config
                if cfg['prompt_mode'] == 'http500':
                    return self._send(500, {'error': 'boom'})
                if cfg['prompt_mode'] == 'malformed':
                    return self._send(200, b'not json', 'application/json')
                try:
                    body = json.loads(raw)
                except ValueError:
                    return self._send(400, {'error': 'bad json'})
                graph = body.get('prompt') or {}
                server.submitted.append(graph)
                if not isinstance(graph, dict) or not graph:
                    return self._send(400, {'error': {'type': 'prompt_no_outputs',
                                                     'message': 'prompt has no outputs'}})
                prompt_id = uuid.uuid4().hex
                server.queued_at[prompt_id] = time.monotonic()
                return self._send(200, {'prompt_id': prompt_id, 'number': 1})

            def _history(self, prompt_id):
                cfg = server.config
                started = server.queued_at.get(prompt_id)
                if started is None or time.monotonic() - started < cfg['complete_after']:
                    return self._send(200, {})
                if cfg['history_mode'] == 'malformed':
                    return self._send(200, b'<html>proxy error</html>', 'text/html')
                if cfg['history_mode'] == 'error':
                    return self._send(200, {prompt_id: {
                        'status': {'status_str': 'error', 'completed': False,
                                   'messages': [['execution_error', {
                                       'exception_type': 'RuntimeError',
                                       'exception_message': 'CUDA out of memory'}]]},
                        'outputs': {}}})
                if cfg['history_mode'] == 'empty_outputs':
                    return self._send(200, {prompt_id: {
                        'status': {'status_str': 'success', 'completed': True},
                        'outputs': {}}})
                return self._send(200, {prompt_id: {
                    'status': {'status_str': 'success', 'completed': True, 'messages': []},
                    'outputs': {'9': {'audio': [{
                        'filename': f'music-studio_00001.{cfg["artifact_suffix"]}',
                        'subfolder': 'music-studio', 'type': 'output'}]}}}})

        return Handler
