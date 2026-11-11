"""Local ACE-Step 1.5 adapter: a thin delegate to ``ace_engine``.

This is deliberately *not* a second render implementation. The studio's proven
path — bounded subprocess, the detached ``ace_worker.py``, the ACE flock lease,
progress milestones, process-group kill and WAV validation — lives in
``ace_engine.py`` at the repo root, and that is the only thing that talks to the
GPU. The adapter's job is to translate the engine contract into those calls and
to report the runtime's real availability.

If this module ever grows its own ``subprocess.Popen``, the two paths will drift
and one of them will render without the lease the other one holds. Keep it a
delegate.
"""
from __future__ import annotations

import tempfile
import time
from typing import Any, Callable

import ace_engine

from .contract import (Capabilities, Engine, EngineStatus, HealthCheck, Limits,
                       RenderRequest, RenderResult)

# The product's measured window (see music_studio.store). ACE-Step itself
# buckets audio up to 600 s, but this GPU (RTX 4070 Ti, 12 GB) was rendered at
# 180 s; the adapter refuses longer requests rather than queueing a render that
# will OOM. Raising it needs a new measurement, not a source read.
DEFAULT_LIMITS = Limits(min_seconds=10, max_seconds=180, recommended_seconds=120)


class AceLocalEngine(Engine):
    id = 'ace_local'
    label = 'ACE-Step 1.5 (local GPU)'
    kind = 'local'
    requirements = ('an ACE-Step 1.5 runtime with checkpoints on this machine',
                    'the ACE source tree (acestep package) importable by that runtime',
                    'a CUDA GPU the runtime can load the model on; ~12 GB measured',
                    'exclusive access to the GPU for the duration of a render')

    def __init__(self, limits: Limits | None = None):
        super().__init__()
        # Overridable only so a test can pin a narrower window; production uses
        # the measured one, which a test asserts equals music_studio.store's.
        self._limits = limits

    # -- description -----------------------------------------------------
    @property
    def limits(self) -> Limits:
        return self._limits or DEFAULT_LIMITS

    @property
    def capabilities(self) -> Capabilities:
        # Each of these is exercised by the suite through the fake runtime and
        # by the GPU evidence runs recorded in CHANGELOG/NIGHTLOG. ``batch`` is
        # not: no adapter renders several takes in one call (the studio's batch
        # is a worker looping over takes), so it must not be advertised.
        return Capabilities(instrumental=True, vocal_language=True,
                            repaint=True, continuation=True, batch=False)

    def health(self) -> EngineStatus:
        """Cheap probe of the paths ``ace_engine`` will actually use.

        Reads ``ace_engine``'s module attributes rather than caching a copy, so a
        reload of that module (tests redirect the runtime this way) is reflected
        here instead of being shadowed by a stale second config.
        """
        started = time.monotonic()
        checks = [
            HealthCheck('interpreter', ace_engine.PYTHON.is_file(), str(ace_engine.PYTHON)),
            HealthCheck('checkpoints', (ace_engine.RUNTIME / 'checkpoints').is_dir(),
                        str(ace_engine.RUNTIME / 'checkpoints')),
            HealthCheck('source', (ace_engine.SOURCE / 'acestep').is_dir(),
                        str(ace_engine.SOURCE)),
        ]
        ok = ace_engine.available()
        missing = '; '.join(f'{c.name} missing at {c.detail}' for c in checks if not c.ok)
        return EngineStatus(
            self.id, 'ready' if ok else 'unavailable',
            'local ACE-Step runtime ready' if ok else ace_engine.unavailable_reason() + f' ({missing})',
            checks, latency_ms=int((time.monotonic() - started) * 1000),
            extra={'runtime': str(ace_engine.RUNTIME),
                   'source': str(ace_engine.SOURCE),
                   'model': 'acestep-v15-turbo'})

    # -- rendering -------------------------------------------------------
    def _render(self, request: RenderRequest,
                on_progress: Callable[[int], None] | None = None,
                on_process: Callable[[int], None] | None = None) -> RenderResult:
        # Contract: output_dir is a trusted scratch directory and None means
        # "use the system temp dir". Never invent placeholder audio; the
        # runtime validates the directory it is given.
        output_dir = request.output_dir or tempfile.gettempdir()
        options: dict[str, Any] = dict(
            caption=request.caption, lyrics=request.lyrics, seed=request.seed,
            output_dir=output_dir, timeout_seconds=request.timeout_seconds,
            instrumental=request.instrumental, vocal_language=request.vocal_language,
            bpm=request.bpm, keyscale=request.keyscale,
            on_progress=on_progress, on_process=on_process)
        if request.mode == 'repaint':
            data = ace_engine.repaint(
                source_wav=request.source_wav, start_s=request.range_start_s,
                end_s=request.range_end_s, **options)
        elif request.mode == 'extend':
            data = ace_engine.extend(
                source_wav=request.source_wav,
                extend_seconds=request.extend_seconds, **options)
        else:
            data = ace_engine.generate(
                duration_seconds=request.duration_seconds, **options)
        return RenderResult(data, {'engine_id': self.id, 'model': 'acestep-v15-turbo',
                                   'inference_steps': 8})
