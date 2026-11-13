"""MiniMax Music 3 through a ComfyUI server (native MiniMaxMusic3 nodes).

The API graph mirrors ComfyUI's published MiniMax Music 3 text-to-music
template, and the node names/inputs come from ComfyUI's own
``comfy_extras/nodes_minimax_music.py``:

    MiniMaxMusic3TextEncode        clip, caption, lyrics, seed, max_duration,
                                   cfg_scale, top_k  -> CONDITIONING, seconds
    EmptyMiniMaxMusic3LatentAudio  seconds, batch_size -> LATENT
    SaveAudioAdvanced              audio, filename_prefix, format[, quality]

ComfyUI has no WAV save option (flac / mp3 / opus only), so a render asks for
FLAC — lossless — and this adapter transcodes it to PCM WAV with ffmpeg while
preserving the model's native sample rate (44.1 kHz) and channel count. That is
a real conversion of the model's own output, never synthesised audio.

The graph has no repaint/continuation conditioning nodes, so the engine
declares ``repaint=False`` and ``continuation=False``; the contract refuses
those modes before any HTTP request is made.

Licence: MiniMax Music 3 weights and outputs are governed by the MiniMax
Community License. ``LICENCE_NOTICE`` is attached to every take's metadata so
the identifier 'MiniMax-Music3' travels with the audio.
"""
from __future__ import annotations

from ..portalock import lock_ex, unlock
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from .contract import (Capabilities, Engine, EngineError, EngineStatus, HealthCheck,
                       InvalidAudio, Limits, RenderRequest, RenderResult, inspect_wav)

# No address is baked in: a ComfyUI host is per-machine. Set it in Settings -> Compute, in
# COMFYUI_URL, or pass base_url. With none of those the engine reports itself unavailable and says
# what to do — it never guesses another machine's address.
DEFAULT_URL = ''
# The weights actually installed on the Spark E ComfyUI host. ComfyUI lists
# them under their models/<dir>/ names, which is what the loaders accept.
MODEL_FILES = {
    'unet': 'diffusion_models/minimax_music3_dit_int8_convrot.safetensors',
    'clip': 'text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors',
    'vae': 'vae/minimax_music3_dav.safetensors',
}
# Alternative weights documented by ComfyUI (fp16/fp32 for larger cards).
MODEL_ALTERNATIVES = {
    'unet': ('minimax_music3_dit_fp16.safetensors',
             'diffusion_models/minimax_music3_dit_fp16.safetensors',
             'minimax_music3_dit_fp32.safetensors'),
    'clip': ('minimax_music3_text_encoder_pruned_bf16.safetensors',
             'text_encoders/minimax_music3_text_encoder_pruned_bf16.safetensors'),
}
CLIP_TYPE = 'minimax'
MINIMAX_NODES = ('MiniMaxMusic3TextEncode', 'EmptyMiniMaxMusic3LatentAudio')
LOADER_NODES = ('UNETLoader', 'CLIPLoader', 'VAELoader')
SAMPLER_NODES = ('KSampler', 'ConditioningZeroOut', 'VAEDecodeAudio', 'SaveAudioAdvanced')
POLL_SECONDS = 2.0
MAX_ERROR_CHARS = 300

LICENCE_ID = 'MiniMax-Music3'
LICENCE_NAME = 'MiniMax-Music3 Community License'
LICENCE_URL = 'https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE'
LICENCE_NOTICE = (
    'MiniMax-Music3. Generated with the MiniMax Music 3 model. The weights and '
    'this output are governed by the MiniMax-Music3 Community License '
    '(Copyright (c) 2026 MiniMax; ' + LICENCE_URL + '). Include this copyright '
    'and permission notice with copies, and display "MiniMax-Music3" in the '
    'interface of any commercial product or service that uses it.'
)

FFMPEG_HELP = ("ffmpeg is not installed: install it with 'winget install ffmpeg' "
               "on Windows or 'brew install ffmpeg' on macOS, or set HEWN_FFMPEG "
               "to its full path")


def _ffmpeg_candidates() -> tuple[str, ...]:
    """The usual ffmpeg locations that a desktop shortcut's PATH misses."""
    if os.name == 'nt' or sys.platform.startswith('win'):
        candidates = []
        local = os.environ.get('LOCALAPPDATA')
        if local:
            candidates.append(local.rstrip('\\/') + r'\Microsoft\WinGet\Links\ffmpeg.exe')
        candidates.append(r'C:\ffmpeg\bin\ffmpeg.exe')
        program_files = os.environ.get('ProgramFiles') or os.environ.get('PROGRAMFILES')
        if program_files:
            candidates.append(program_files.rstrip('\\/') + r'\ffmpeg\bin\ffmpeg.exe')
        return tuple(candidates)
    if sys.platform == 'darwin' or platform.system() == 'Darwin':
        return ('/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg')
    return ()


def _find_ffmpeg() -> str | None:
    """HEWN_FFMPEG, then PATH, then the per-platform install directories."""
    override = os.environ.get('HEWN_FFMPEG')
    if override:
        return os.path.expanduser(os.path.expandvars(override))
    found = shutil.which('ffmpeg')
    if found:
        return found
    for candidate in _ffmpeg_candidates():
        if os.path.isfile(candidate):
            return candidate
    return None


def _saved_settings() -> dict:
    """The operator's saved endpoint and sampler knobs, when there are any."""
    try:
        from .. import settings
        base = settings.base_dir()
        return settings.read(base) if base is not None else {}
    except Exception:  # noqa: BLE001 - a broken settings file must not block the engine
        return {}


class _ComfyTransportError(EngineError):
    """Transient network failure; history polling may retry through it."""


class MiniMaxM3ComfyEngine(Engine):
    id = 'minimax_m3_comfy'
    label = 'MiniMax Music 3 (ComfyUI)'
    kind = 'remote'
    requirements = ('ComfyUI with MiniMaxMusic3 nodes + minimax CLIP type',
                    'MiniMax Music 3 DiT, text encoder and DAV VAE weights',
                    '>= 8 GB VRAM with int8 weights + tiled decode; '
                    '>= 16-24 GB for fp16')

    def __init__(self, base_url: str | None = None, timeout: float = 10.0,
                 unet_name: str | None = None, clip_name: str | None = None,
                 vae_name: str | None = None, tiled_decode: bool = False,
                 cfg_scale: float | None = None, cfg: float | None = None,
                 top_k: int | None = None,
                 steps: int | None = None, ffmpeg: str | None = None,
                 gpu_lease_path: str | os.PathLike | None = None):
        super().__init__()
        # The saved settings are the operator's answer to "which server, which
        # sampler knobs"; explicit arguments (tests, a named target) still win.
        saved = _saved_settings()
        if base_url is None and not os.environ.get('COMFYUI_URL') and saved.get('comfy_url'):
            base_url = saved['comfy_url']
        knobs = saved.get('minimax') or {}
        self.base_url = (base_url or os.environ.get('COMFYUI_URL') or DEFAULT_URL).rstrip('/')
        self.timeout = timeout
        self.unet_name = unet_name or MODEL_FILES['unet']
        self.clip_name = clip_name or MODEL_FILES['clip']
        self.vae_name = vae_name or MODEL_FILES['vae']
        self.tiled_decode = tiled_decode
        self.cfg_scale = knobs.get('cfg_scale', 1.5) if cfg_scale is None else cfg_scale
        self.cfg = knobs.get('cfg', 4.0) if cfg is None else cfg
        self.top_k = knobs.get('top_k', 50) if top_k is None else top_k
        self.steps = knobs.get('steps', 30) if steps is None else steps
        self.sampler = knobs.get('sampler', 'euler')
        self.scheduler = knobs.get('scheduler', 'simple')
        self.ffmpeg = ffmpeg if ffmpeg is not None else _find_ffmpeg()
        self._format_options: tuple[str, ...] = ('flac',)
        # Overridable so tests do not sleep on the production poll interval.
        self.poll_seconds = POLL_SECONDS
        # Same-host GPU guard only. A lease path is consulted only when the
        # ComfyUI host is this machine; a Spark render never blocks local ACE.
        self.gpu_lease_path = Path(gpu_lease_path) if gpu_lease_path else None

    # -- description -----------------------------------------------------
    @property
    def limits(self) -> Limits:
        # MiniMaxMusic3TextEncode.max_duration is 0.04-360 s; the studio's
        # product window starts at 10 s.
        return Limits(min_seconds=10, max_seconds=360, recommended_seconds=120)

    @property
    def capabilities(self) -> Capabilities:
        # No repaint/continuation nodes exist in the MiniMax graph, so those
        # are refused rather than silently rendered as a fresh take.
        return Capabilities(instrumental=True, vocal_language=True,
                            repaint=False, continuation=False, batch=False)

    def health(self) -> EngineStatus:
        started = time.monotonic()
        checks: list[HealthCheck] = []
        extra: dict = {'base_url': self.base_url, 'unet': self.unet_name,
                       'clip': self.clip_name, 'vae': self.vae_name}

        def check(name, ok, detail=''):
            checks.append(HealthCheck(name, bool(ok), detail))
            return bool(ok)

        if not self.base_url:
            return EngineStatus(
                self.id, 'unavailable',
                'no ComfyUI address is set: add one in Settings -> Compute, or set COMFYUI_URL '
                '(this build ships no address, because a ComfyUI host is per-machine)',
                [HealthCheck('base_url', False, 'not configured')], latency_ms=0, extra=extra)

        lease = self._gpu_lease_conflict()
        if lease:
            return EngineStatus(
                self.id, 'busy',
                f'local GPU is busy with an ACE render ({lease}); this ComfyUI '
                'host runs on the same machine', checks, latency_ms=0, extra=extra)
        check('gpu_lease', True, 'ComfyUI host is remote or the local GPU is free')

        try:
            stats = self._get('/system_stats', timeout=min(self.timeout, 4.0))
        except EngineError as exc:
            check('http', False, str(exc))
            return EngineStatus(self.id, 'unavailable', str(exc), checks,
                                latency_ms=int((time.monotonic() - started) * 1000),
                                extra=extra)
        check('http', True, 'reachable')
        if isinstance(stats, dict):
            devices = stats.get('devices') or []
            if devices and isinstance(devices[0], dict):
                extra['comfy_device'] = devices[0].get('name')
                extra['comfy_vram_total'] = devices[0].get('vram_total')
                extra['comfy_vram_free'] = devices[0].get('vram_free')
            system = stats.get('system') or {}
            if isinstance(system, dict) and system.get('comfyui_version'):
                extra['comfyui_version'] = system.get('comfyui_version')

        # Every node the submitted graph uses, not just the MiniMax ones.
        required = (*MINIMAX_NODES, *LOADER_NODES, 'KSampler', 'ConditioningZeroOut',
                    'VAEDecodeAudioTiled' if self.tiled_decode else 'VAEDecodeAudio',
                    'SaveAudioAdvanced')
        info = {}
        missing_nodes = []
        for node in required:
            try:
                payload = self._get(f'/object_info/{node}', timeout=min(self.timeout, 4.0))
                if not isinstance(payload, dict) or node not in payload:
                    raise EngineError(f'{node} is not registered')
                info[node] = payload
            except EngineError:
                missing_nodes.append(node)
        check('nodes', not missing_nodes,
              'all present' if not missing_nodes else 'missing: ' + ', '.join(missing_nodes))

        models_ok = True
        model_errors: list[str] = []
        if not missing_nodes:
            for kind, node, field, name in (
                    ('unet', 'UNETLoader', 'unet_name', self.unet_name),
                    ('clip', 'CLIPLoader', 'clip_name', self.clip_name),
                    ('vae', 'VAELoader', 'vae_name', self.vae_name)):
                options = _combo_options(info.get(node), node, field)
                if options is None:
                    models_ok = False
                    model_errors.append(f'{node} exposes no {field} list')
                    continue
                extra[f'available_{kind}s'] = list(options)
                if name not in options:
                    models_ok = False
                    model_errors.append(f'{name} is not listed by {node}')
            clip_types = _combo_options(info.get('CLIPLoader'), 'CLIPLoader', 'type')
            if clip_types is not None and CLIP_TYPE not in clip_types:
                models_ok = False
                model_errors.append(f'CLIPLoader has no type={CLIP_TYPE!r}')
            check('clip_type', clip_types is None or CLIP_TYPE in clip_types,
                  f'type={CLIP_TYPE!r} supported' if clip_types else 'type list unreadable')
        check('models', models_ok,
              'all referenced weights present' if models_ok else '; '.join(model_errors))

        options = _combo_options(info.get('SaveAudioAdvanced'), 'SaveAudioAdvanced', 'format')
        if options:
            self._format_options = tuple(options)
            extra['save_formats'] = list(options)
        wav_direct = 'wav' in self._format_options
        lossless = wav_direct or 'flac' in self._format_options
        check('wav_output', lossless and (wav_direct or bool(self.ffmpeg)),
              'native wav' if wav_direct else
              ('flac + ffmpeg transcode' if lossless and self.ffmpeg else
               ('no flac option' if not lossless else FFMPEG_HELP)))
        extra['licence'] = LICENCE_ID

        hard = [c for c in checks if not c.ok and c.name != 'wav_output']
        if hard:
            state, detail = 'unavailable', '; '.join(f'{c.name}: {c.detail}' for c in hard)
        elif not (wav_direct or (lossless and self.ffmpeg)):
            state, detail = 'degraded', 'ComfyUI cannot emit WAV and ' + FFMPEG_HELP
        else:
            state, detail = 'ready', 'ComfyUI MiniMax Music 3 ready'
        return EngineStatus(self.id, state, detail, checks,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            extra=extra)

    # -- rendering -------------------------------------------------------
    def _render(self, request: RenderRequest,
                on_progress: Callable[[int], None] | None = None,
                on_process: Callable[[int], None] | None = None) -> RenderResult:
        # ComfyUI renders server-side; there is no local child process to report.
        graph = self.build_workflow(request)
        self._emit(on_progress, 2)
        created = self._post('/prompt', {'prompt': graph, 'client_id': uuid.uuid4().hex})
        prompt_id = created.get('prompt_id') if isinstance(created, dict) else None
        if not prompt_id:
            raise EngineError(_clean(f'ComfyUI rejected the workflow: {created}'),
                              engine_id=self.id)
        self._emit(on_progress, 5)

        deadline = time.monotonic() + request.timeout_seconds
        expected = max(30.0, request.duration_seconds * 1.5)
        started = time.monotonic()
        history = None
        while time.monotonic() <= deadline:
            time.sleep(self.poll_seconds)
            try:
                payload = self._get(f'/history/{urllib.parse.quote(str(prompt_id))}')
            except _ComfyTransportError:
                # A dropped poll is retried; a malformed or 4xx reply is not.
                payload = {}
            entry = payload.get(prompt_id) if isinstance(payload, dict) else None
            if isinstance(entry, dict):
                status = entry.get('status') or {}
                if status.get('status_str') == 'error':
                    raise EngineError(_history_error(entry), engine_id=self.id)
                if status.get('completed') or entry.get('outputs'):
                    history = entry
                    break
            self._emit(on_progress, min(90, 5 + int(85 * (time.monotonic() - started) / expected)))
        if history is None:
            raise EngineError(
                f'ComfyUI render timed out after {request.timeout_seconds:g}s',
                engine_id=self.id)

        artifact = _saved_audio(history, self.save_node)
        if artifact is None:
            raise EngineError('ComfyUI reported success but saved no audio', engine_id=self.id)
        query = {'filename': artifact.get('filename', ''),
                 'subfolder': artifact.get('subfolder', ''),
                 'type': artifact.get('type', 'output')}
        if not query['filename']:
            raise EngineError('ComfyUI saved audio without a filename', engine_id=self.id)
        raw = self._get('/view', query=query, raw=True)
        if not raw:
            raise EngineError('ComfyUI returned an empty audio file', engine_id=self.id)
        self._emit(on_progress, 92)

        source_format = (Path(query['filename']).suffix.lstrip('.').lower() or 'flac')
        if source_format == 'wav':
            wav = raw
            transcode = None
        else:
            wav = self._to_wav(raw, source_format)
            transcode = 'ffmpeg'
        try:
            info = inspect_wav(wav)
        except InvalidAudio as exc:
            raise EngineError(f'ComfyUI audio could not be converted to WAV: {exc}',
                              engine_id=self.id) from exc
        return RenderResult(wav, {
            'engine_id': self.id,
            'model': self.unet_name,
            'model_family': LICENCE_ID,
            'licence': LICENCE_ID,
            'model_licence': LICENCE_NAME,
            'licence_url': LICENCE_URL,
            'licence_notice': LICENCE_NOTICE,
            'comfy_prompt_id': prompt_id,
            'comfy_url': self.base_url,
            'source_format': source_format,
            'transcode': transcode,
            'steps': self.steps,
            'cfg': self.cfg,
            'cfg_scale': request.minimax_guidance if request.minimax_guidance is not None else self.cfg_scale,
            'sample_rate': info['sample_rate'],
            'channels': info['channels'],
            'frames': info['frames'],
            'audio_seconds': info['seconds'],
        })

    save_node = '9'

    def build_workflow(self, request: RenderRequest) -> dict:
        """The API-format graph. Pure function: unit-tested without a server."""
        save_format = 'wav' if 'wav' in self._format_options else 'flac'
        decode_node = '8'
        graph = {
            '1': {'class_type': 'UNETLoader',
                  'inputs': {'unet_name': self.unet_name, 'weight_dtype': 'default'}},
            '2': {'class_type': 'CLIPLoader',
                  'inputs': {'clip_name': self.clip_name, 'type': CLIP_TYPE,
                             'device': 'default'}},
            '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': self.vae_name}},
            '4': {'class_type': 'MiniMaxMusic3TextEncode',
                  'inputs': {'clip': ['2', 0],
                             'caption': request.caption,
                             'lyrics': '' if request.instrumental else request.lyrics,
                             'seed': request.seed,
                             'max_duration': float(request.duration_seconds),
                             'cfg_scale': request.minimax_guidance if request.minimax_guidance is not None else self.cfg_scale,
                             'top_k': self.top_k}},
            '5': {'class_type': 'EmptyMiniMaxMusic3LatentAudio',
                  'inputs': {'seconds': ['4', 1], 'batch_size': 1}},
            '6': {'class_type': 'ConditioningZeroOut',
                  'inputs': {'conditioning': ['4', 0]}},
            '7': {'class_type': 'KSampler',
                  'inputs': {'model': ['1', 0], 'positive': ['4', 0],
                             'negative': ['6', 0], 'latent_image': ['5', 0],
                             'seed': request.seed, 'steps': self.steps,
                             'cfg': self.cfg, 'sampler_name': self.sampler,
                             'scheduler': self.scheduler, 'denoise': 1.0}},
        }
        if self.tiled_decode:
            graph[decode_node] = {'class_type': 'VAEDecodeAudioTiled',
                                  'inputs': {'samples': ['7', 0], 'vae': ['3', 0],
                                             'tile_size': 1536, 'overlap': 64}}
        else:
            graph[decode_node] = {'class_type': 'VAEDecodeAudio',
                                  'inputs': {'samples': ['7', 0], 'vae': ['3', 0]}}
        graph[self.save_node] = {
            'class_type': 'SaveAudioAdvanced',
            'inputs': {'audio': [decode_node, 0],
                       'filename_prefix': 'music-studio/minimax-music3',
                       'format': save_format}}
        return graph

    # -- conversion ------------------------------------------------------
    def _to_wav(self, raw: bytes, source_format: str = 'flac') -> bytes:
        """Lossless transcode of the server's audio to PCM WAV via ffmpeg.

        Native rate and channel layout are preserved: MiniMax Music 3 emits
        44.1 kHz stereo and the WAV must say exactly that.
        """
        if not self.ffmpeg:
            raise EngineError('server returned non-WAV audio and ' + FFMPEG_HELP,
                              engine_id=self.id)
        suffix = '.' + source_format if source_format else '.flac'
        with tempfile.TemporaryDirectory(prefix='mm3-wav-') as tmp:
            src = Path(tmp) / f'source{suffix}'
            dst = Path(tmp) / 'out.wav'
            src.write_bytes(raw)
            try:
                done = subprocess.run(
                    [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                     '-i', str(src), '-vn', '-acodec', 'pcm_s16le', str(dst)],
                    capture_output=True, timeout=120, check=False)
            except (OSError, subprocess.SubprocessError) as exc:
                raise EngineError(f'ffmpeg failed to transcode: {exc}',
                                  engine_id=self.id) from exc
            if done.returncode != 0 or not dst.is_file():
                raise EngineError('ffmpeg failed to transcode ComfyUI audio: '
                                  + _clean(done.stderr.decode('utf-8', 'replace')),
                                  engine_id=self.id)
            return dst.read_bytes()

    # -- same-host residency ---------------------------------------------
    def _gpu_lease_conflict(self) -> str | None:
        """Why a *local* ComfyUI render must not start, or None.

        A render on another machine never touches this host's GPU, so the
        ACE flock is only consulted when ComfyUI resolves to this machine.
        """
        if not self._host_is_local():
            return None
        path = self.gpu_lease_path or _default_gpu_lease_path()
        if path is None:
            return None
        try:
            handle = open(path, 'a')
        except OSError:
            return None
        try:
            try:
                lock_ex(handle, blocking=False)
            except BlockingIOError:
                return str(path)
            except OSError:
                return None
            unlock(handle)
            return None
        finally:
            handle.close()

    def _host_is_local(self) -> bool:
        host = urllib.parse.urlsplit(self.base_url).hostname
        if not host or host in ('localhost', '127.0.0.1', '::1'):
            return True
        try:
            resolved = {info[4][0] for info in socket.getaddrinfo(host, None)}
        except OSError:
            return False
        local = {'127.0.0.1', '::1'}
        for probe in (socket.gethostname(), socket.getfqdn()):
            try:
                local |= {info[4][0] for info in socket.getaddrinfo(probe, None)}
            except OSError:
                pass
        return bool(resolved & local)

    # -- transport -------------------------------------------------------
    def _get(self, path: str, query: dict | None = None, raw: bool = False,
             timeout: float | None = None):
        return self._request('GET', path, None, query, raw, timeout)

    def _post(self, path: str, body: dict):
        return self._request('POST', path, body, None, False, None)

    def _request(self, method, path, body, query, raw, timeout):
        url = self.base_url + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        data = json.dumps(body).encode('utf-8') if body is not None else None
        headers = {'Accept': 'audio/wav' if raw else 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = _clean(exc.read().decode('utf-8', 'replace') or str(exc.reason))
            raise EngineError(f'ComfyUI HTTP {exc.code}: {detail}',
                              engine_id=self.id) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, 'reason', exc)
            raise _ComfyTransportError(f'ComfyUI unreachable at {self.base_url}: {reason}',
                                       engine_id=self.id) from exc
        if raw:
            return payload
        try:
            return json.loads(payload)
        except ValueError as exc:
            raise EngineError('ComfyUI returned non-JSON', engine_id=self.id) from exc


def _default_gpu_lease_path() -> Path | None:
    """The ACE GPU lock, when this process can find it.

    This is ``ace_engine.LOCK`` — the lease a local ACE render actually holds.
    The studio's own ``.worker.lock`` is *not* it: the worker holds that for
    its whole life, so consulting it would report a local ComfyUI render busy
    forever, even with an idle GPU.
    """
    override = os.environ.get('MUSIC_STUDIO_GPU_LOCK')
    if override:
        return Path(override)
    try:
        import ace_engine
        return Path(ace_engine.LOCK)
    except Exception:
        return None


def _combo_options(info, node: str, field: str) -> tuple[str, ...] | None:
    """Read a combo input's option list out of /object_info.

    Handles the plain ``[[opts...], {...}]`` shape, a dict of DynamicCombo
    choices, and ComfyUI 0.44's ``["COMFY_DYNAMICCOMBO_V3", {"options":
    [{"key": ...}]}]`` shape used by SaveAudioAdvanced.
    """
    if not isinstance(info, dict):
        return None
    spec = info.get(node) or {}
    inputs = (spec.get('input') or {}).get('required') or {}
    entry = inputs.get(field)
    if not isinstance(entry, list) or not entry:
        return None
    if len(entry) > 1 and isinstance(entry[1], dict) and isinstance(entry[1].get('options'), list):
        items = entry[1]['options']
        keys = [item.get('key') if isinstance(item, dict) else item for item in items]
        return tuple(str(k) for k in keys if k is not None)
    options = entry[0]
    if isinstance(options, dict):  # DynamicCombo: {"flac": [], "mp3": [...]}
        return tuple(str(o) for o in options.keys())
    if not isinstance(options, list):
        return None
    return tuple(str(o.get('name') if isinstance(o, dict) else o) for o in options)


def _saved_audio(history: dict, save_node: str) -> dict | None:
    """First saved audio descriptor from a /history entry."""
    outputs = history.get('outputs') or {}
    candidates = []
    for key in (save_node, *outputs.keys()):
        node_out = outputs.get(key)
        if isinstance(node_out, dict):
            candidates.extend(node_out.get('audio') or [])
            candidates.extend(node_out.get('images') or [])
    return next((c for c in candidates if isinstance(c, dict) and c.get('filename')), None)


def _history_error(entry: dict) -> str:
    status = entry.get('status') or {}
    for message in status.get('messages') or []:
        if isinstance(message, list) and len(message) == 2 and message[0] == 'execution_error':
            data = message[1] or {}
            return 'ComfyUI error: ' + _clean(
                str(data.get('exception_message') or data.get('exception_type') or 'execution failed'))
    return 'ComfyUI reported a failed render'


def _clean(text: str) -> str:
    text = ' '.join(str(text).split())
    return text[:MAX_ERROR_CHARS - 1] + '…' if len(text) > MAX_ERROR_CHARS else text
