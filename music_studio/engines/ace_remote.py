"""ACE-Step 1.5 over HTTP (a Spark or any host running ``acestep.api_server``).

Protocol (verified against the ACE-Step 1.5 source, not
against a live server — no ACE API host was reachable when this was written):

    GET  /health              -> {"data": {"status": "ok", "models_initialized": …}}
    GET  /v1/models           -> {"data": {"models": [...], "default_model": …}}
    POST /release_task        -> {"data": {"task_id": "...", "status": "queued"}}
    POST /query_result        -> {"data": [{"task_id", "status", "result", "progress_text"}]}
                                 status 0 = queued/running, 1 = succeeded, 2 = failed
    GET  /v1/audio?path=…     -> raw audio bytes

The server keeps its queue in memory and must run with ``--workers 1``. Auth is
optional; when enabled the server accepts ``Authorization: Bearer <key>``.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from .contract import (Capabilities, Engine, EngineError, EngineStatus,
                       HealthCheck, InvalidAudio, Limits, RenderRequest, RenderResult,
                       inspect_wav)

# No address is baked in: an ACE server belongs to whoever runs it. Empty means "not pointed at
# anything yet", and health says exactly what to set instead of dialling a stranger's LAN.
DEFAULT_URL = ''
MAX_ERROR_CHARS = 300
POLL_SECONDS = 2.0
# ACE's request model has no `instrumental` flag: an empty lyrics string with a
# caption that says "instrumental" is the documented text2music path.
DEFAULT_TIMEOUT = 10.0


class AceRemoteEngine(Engine):
    id = 'ace_remote'
    label = 'ACE-Step 1.5 (remote)'
    kind = 'remote'
    requirements = ('a host serving acestep.api_server (--workers 1)',
                    'network reachability from this machine',
                    'the same model family available on that host')

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT):
        super().__init__()
        self.base_url = (base_url or os.environ.get('ACE_REMOTE_URL') or DEFAULT_URL).rstrip('/')
        self.api_key = api_key if api_key is not None else os.environ.get('ACE_REMOTE_API_KEY')
        self.timeout = timeout
        # Overridable so tests do not sleep on the production poll interval.
        self.poll_seconds = POLL_SECONDS

    # -- description -----------------------------------------------------
    @property
    def limits(self) -> Limits:
        # The HTTP model accepts arbitrary audio_duration; ACE buckets audio up
        # to 360 s. 10 s is the shortest useful phrase.
        return Limits(min_seconds=10, max_seconds=360, recommended_seconds=120)

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(instrumental=True, vocal_language=True,
                            repaint=False, continuation=False, batch=False)

    def health(self) -> EngineStatus:
        started = time.monotonic()
        checks: list[HealthCheck] = []
        extra: dict = {'base_url': self.base_url}
        if not self.base_url:
            checks.append(HealthCheck('address', False, 'no ACE server address is set'))
            return EngineStatus(
                self.id, 'unavailable',
                'No ACE-Step server is configured: set its address in Settings -> Compute '
                '(or export ACE_REMOTE_URL) if you run one, otherwise use the local engine',
                checks, latency_ms=int((time.monotonic() - started) * 1000), extra=extra)
        try:
            payload = self._request('GET', '/health', timeout=min(self.timeout, 4.0))
        except EngineError as exc:
            checks.append(HealthCheck('http', False, str(exc)))
            return EngineStatus(self.id, 'unavailable', str(exc), checks,
                                latency_ms=int((time.monotonic() - started) * 1000),
                                extra=extra)
        checks.append(HealthCheck('http', True, 'reachable'))
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            checks.append(HealthCheck('payload', False, 'unexpected /health body'))
            return EngineStatus(self.id, 'error', 'ACE API returned an unexpected /health body',
                                checks, latency_ms=int((time.monotonic() - started) * 1000),
                                extra=extra)
        extra.update({'service': data.get('service'), 'version': data.get('version'),
                      'loaded_model': data.get('loaded_model')})
        models_ready = bool(data.get('models_initialized'))
        checks.append(HealthCheck('models_initialized', models_ready,
                                  str(data.get('loaded_model') or 'no model loaded')))
        if data.get('loaded_model'):
            extra['model'] = data['loaded_model']
        state = 'ready' if models_ready else 'degraded'
        detail = ('remote ACE ready' if models_ready else
                  'remote ACE reachable but has no model loaded yet; the first render will load one')
        return EngineStatus(self.id, state, detail, checks,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            extra=extra)

    # -- rendering -------------------------------------------------------
    def _render(self, request: RenderRequest,
                on_progress: Callable[[int], None] | None,
                on_process: Callable[[int], None] | None = None) -> RenderResult:
        # on_process is accepted for contract symmetry; an HTTP render has no
        # local child process to reap, so it is deliberately unused here.
        body = {
            'prompt': request.caption,
            'lyrics': '' if request.instrumental else request.lyrics,
            'thinking': False,
            'task_type': 'text2music',
            'audio_duration': request.duration_seconds,
            'bpm': request.bpm,
            'key_scale': request.keyscale or '',
            'vocal_language': request.vocal_language,
            'seed': request.seed,
            'use_random_seed': False,
            'batch_size': 1,
            'audio_format': 'wav',
        }
        if request.instrumental:
            # The only signal the HTTP contract has for this.
            body['prompt'] = f"{request.caption} (instrumental, no vocals)"
        self._emit(on_progress, 2)
        created = self._request('POST', '/release_task', body)
        data = created.get('data') if isinstance(created, dict) else None
        task_id = (data or {}).get('task_id')
        if not task_id:
            raise EngineError('ACE remote did not return a task id', engine_id=self.id)
        self._emit(on_progress, 5)

        deadline = time.monotonic() + request.timeout_seconds
        last_progress = 5
        while True:
            if time.monotonic() > deadline:
                raise EngineError('remote ACE render timed out', engine_id=self.id)
            time.sleep(self.poll_seconds)
            payload = self._request('POST', '/query_result',
                                    {'task_id_list': json.dumps([task_id])})
            items = payload.get('data') if isinstance(payload, dict) else None
            item = items[0] if isinstance(items, list) and items else None
            if not isinstance(item, dict):
                continue
            status = item.get('status')
            if status == 2:
                raise EngineError(_failure(item), engine_id=self.id)
            if status == 1:
                path = _audio_path(item.get('result'))
                if not path:
                    raise EngineError('remote ACE reported success without an audio path',
                                      engine_id=self.id)
                self._emit(on_progress, 95)
                raw = self._request('GET', '/v1/audio',
                                    query={'path': path}, raw=True)
                try:
                    info = inspect_wav(raw)
                except InvalidAudio as exc:
                    raise EngineError(f'remote ACE returned unusable audio: {exc}',
                                      engine_id=self.id) from exc
                return RenderResult(raw, {
                    'engine_id': self.id,
                    'model': (self.health().extra or {}).get('model'),
                    'remote_task_id': task_id,
                    'sample_rate': info['sample_rate'],
                    'channels': info['channels'],
                    'frames': info['frames'],
                })
            # status 0: queued or running. progress_text is free text; only
            # advance the coarse bar so a stalled queue still looks alive.
            last_progress = min(90, last_progress + 3)
            self._emit(on_progress, last_progress)

    # -- transport -------------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None,
                 query: dict | None = None, raw: bool = False, timeout: float | None = None):
        url = self.base_url + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        data = None
        headers = {'Accept': 'audio/wav' if raw else 'application/json'}
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = _clean(exc.read().decode('utf-8', 'replace') or exc.reason)
            raise EngineError(f'ACE remote HTTP {exc.code}: {detail}',
                              engine_id=self.id) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, 'reason', exc)
            raise EngineError(f'ACE remote unreachable at {self.base_url}: {reason}',
                              engine_id=self.id) from exc
        if raw:
            return payload
        try:
            parsed = json.loads(payload)
        except ValueError as exc:
            raise EngineError('ACE remote returned non-JSON', engine_id=self.id) from exc
        if isinstance(parsed, dict) and parsed.get('code') not in (None, 200, 0):
            raise EngineError(f"ACE remote error: {_clean(str(parsed.get('error') or parsed.get('msg')))}",
                              engine_id=self.id)
        return parsed


def _audio_path(result) -> str:
    """Pull the first audio path out of /query_result's JSON-in-a-string."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return ''
    if isinstance(result, dict):
        result = [result]
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and isinstance(item.get('file'), str) and item['file']:
                return item['file']
    return ''


def _failure(item: dict) -> str:
    text = item.get('progress_text') or item.get('result') or 'remote ACE render failed'
    return 'remote ACE failed: ' + _clean(str(text))


def _clean(text: str) -> str:
    text = ' '.join(str(text).split())
    return text[:MAX_ERROR_CHARS - 1] + '…' if len(text) > MAX_ERROR_CHARS else text
