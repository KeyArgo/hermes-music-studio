"""Engine contract shared by every Music Studio backend.

The contract is deliberately small: an engine can describe itself, say whether
it can render right now, and render one take. Everything a client needs to
choose an engine (limits, capabilities, availability, why it is unavailable)
comes from these three calls, never from backend-specific knowledge.

There is no mock engine and no fallback that synthesises audio. An engine that
cannot render reports that fact and raises ``EngineUnavailable``; the registry
may choose a *different real engine*, but it never invents a result.
"""
from __future__ import annotations

import io
import math
import time
import wave
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

# Studio-level vocabulary, stable across engines. Engines that do not support a
# language declare it in capabilities and translate at their own boundary.
VOCAL_LANGUAGES = ('en', 'es', 'fr', 'de', 'it', 'pt', 'ja', 'ko', 'zh', 'auto')
BPM_MIN, BPM_MAX = 40, 220
KEYSCALE_MAX = 24

KINDS = ('local', 'remote')
STATES = ('ready', 'busy', 'degraded', 'unavailable', 'error')
# A render is either a fresh take or a source-conditioned one. The mode lives in
# the contract (not in a per-engine call) so the worker has exactly one entry
# point through the registry, and an engine that cannot repaint says so itself.
MODES = ('generate', 'repaint', 'extend')


class EngineError(RuntimeError):
    """Base class for engine failures that are safe to show a user."""

    def __init__(self, message: str, *, engine_id: str = '', detail: str = ''):
        super().__init__(message)
        self.engine_id = engine_id
        self.detail = detail


class EngineUnavailable(EngineError):
    """The engine cannot render at all (not installed, not reachable, no GPU).

    Raised *before* any work starts. Callers must surface this to the user; it
    is never a cue to fabricate audio.
    """


class EngineBusy(EngineError):
    """The engine is installed and healthy but another render owns it."""


class RequestInvalid(EngineError, ValueError):
    """The request does not fit this engine's limits or the shared vocabulary."""


class CapabilityUnsupported(RequestInvalid):
    """The engine declares this operation false; a UI must not offer it.

    It stays a ``RequestInvalid`` (so the contract's own pre-render checks keep
    their meaning) but carries enough structure for the API to answer 409 with
    the engine and the operation named, rather than a generic 400.
    """

    def __init__(self, message: str, *, engine_id: str = '', operation: str = ''):
        super().__init__(message, engine_id=engine_id)
        self.operation = operation


class InvalidAudio(EngineError):
    """The engine returned bytes that are not a usable WAV."""


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class Limits:
    """Duration window an engine will actually honour, in seconds."""

    min_seconds: float
    max_seconds: float
    recommended_seconds: float

    def __post_init__(self):
        if not (0 < self.min_seconds <= self.recommended_seconds <= self.max_seconds):
            raise ValueError('limits must satisfy 0 < min <= recommended <= max')

    def as_dict(self) -> dict:
        return asdict(self)

    def contains(self, seconds: float) -> bool:
        return self.min_seconds <= seconds <= self.max_seconds


@dataclass(frozen=True)
class Capabilities:
    """What the engine can express. False means the studio must not offer it.

    ``batch`` is engine-level: several takes rendered by one call. The studio's
    batch endpoint is a worker looping over takes, so no adapter currently
    declares it; that flag must stay False until a real batch path exists.
    """

    instrumental: bool
    vocal_language: bool
    repaint: bool
    continuation: bool
    batch: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class HealthCheck:
    name: str
    ok: bool
    detail: str = ''

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class EngineStatus:
    """Structured health snapshot returned by ``Engine.health()``."""

    engine_id: str
    state: str
    detail: str = ''
    checks: list[HealthCheck] = field(default_factory=list)
    latency_ms: int | None = None
    checked_at: float = field(default_factory=_now)
    # Engine-specific facts a setup UI may show (paths, VRAM, model names).
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.state not in STATES:
            raise ValueError(f'unknown engine state: {self.state}')

    @property
    def available(self) -> bool:
        # A busy engine is available: it will refuse this render honestly, but
        # it is installed and correct, so it is offered rather than hidden.
        return self.state in ('ready', 'busy', 'degraded')

    @property
    def ok(self) -> bool:
        return self.state == 'ready'

    def as_dict(self) -> dict:
        return {
            'engine_id': self.engine_id,
            'state': self.state,
            'ok': self.ok,
            'available': self.available,
            'detail': self.detail,
            'checks': [c.as_dict() for c in self.checks],
            'latency_ms': self.latency_ms,
            'checked_at': self.checked_at,
            'extra': dict(self.extra),
        }


@dataclass
class RenderRequest:
    """One take request, expressed in studio vocabulary (not engine vocabulary)."""

    caption: str
    duration_seconds: float
    seed: int
    lyrics: str = ''
    instrumental: bool = False
    vocal_language: str = 'en'
    bpm: int | None = None
    keyscale: str | None = None
    # Trusted, server-owned scratch directory. Engines may create temp dirs
    # inside it; None means "use the system temp dir".
    output_dir: str | None = None
    timeout_seconds: float = 900.0
    # Source-conditioned renders. ``source_wav`` is the parent take's bytes;
    # engines must never write it back. Geometry is in parent-take seconds.
    mode: str = 'generate'
    source_wav: bytes | None = None
    range_start_s: float | None = None
    range_end_s: float | None = None
    extend_seconds: int | None = None
    # Optional MiniMax text-encoder CFG; ACE-Step turbo does not use this.
    minimax_guidance: float | None = None

    def validate_common(self) -> None:
        """Engine-independent shape checks (the same rules for every backend)."""
        if not isinstance(self.caption, str) or not self.caption.strip() \
                or len(self.caption) > 512:
            raise RequestInvalid('caption must contain 1-512 characters')
        if type(self.instrumental) is not bool:
            raise RequestInvalid('instrumental must be a boolean')
        if not isinstance(self.lyrics, str) or len(self.lyrics) > 4096:
            raise RequestInvalid('lyrics must contain at most 4096 characters')
        if not self.instrumental and not self.lyrics.strip():
            raise RequestInvalid('lyrics are required for a vocal take')
        if self.vocal_language not in VOCAL_LANGUAGES:
            raise RequestInvalid('vocal_language must be one of ' + ', '.join(VOCAL_LANGUAGES))
        if self.bpm is not None and (type(self.bpm) is not int
                                     or not BPM_MIN <= self.bpm <= BPM_MAX):
            raise RequestInvalid(f'bpm must be {BPM_MIN}-{BPM_MAX} or null')
        if self.keyscale is not None and (not isinstance(self.keyscale, str)
                                          or len(self.keyscale) > KEYSCALE_MAX):
            raise RequestInvalid(f'keyscale must be at most {KEYSCALE_MAX} characters or null')
        if self.minimax_guidance is not None and (type(self.minimax_guidance) not in (int, float)
                or not 0.5 <= self.minimax_guidance <= 3.0 or not math.isfinite(self.minimax_guidance)):
            raise RequestInvalid('MiniMax guidance must be between 0.5 and 3.0')
        if type(self.duration_seconds) not in (int, float) or self.duration_seconds <= 0:
            raise RequestInvalid('duration_seconds must be a positive number')
        if type(self.seed) is not int or not 0 <= self.seed <= 2**32 - 1:
            raise RequestInvalid('invalid seed')
        if type(self.timeout_seconds) not in (int, float) \
                or not 1 <= self.timeout_seconds <= 3600:
            raise RequestInvalid('timeout_seconds must be between 1 and 3600')
        if self.mode not in MODES:
            raise RequestInvalid('mode must be one of ' + ', '.join(MODES))
        if self.mode == 'generate':
            return
        # A source-conditioned render is meaningless without the parent audio;
        # refusing here keeps a mis-wired caller from getting a fresh take
        # labelled as a repaint.
        if not isinstance(self.source_wav, (bytes, bytearray)) or not self.source_wav:
            raise RequestInvalid('source_wav is required for a source-conditioned render')
        if self.mode == 'repaint':
            start, end = self.range_start_s, self.range_end_s
            for value, name in ((start, 'range_start_s'), (end, 'range_end_s')):
                if type(value) not in (int, float):
                    raise RequestInvalid(f'{name} must be a number')
            if not 0 <= start < end:  # type: ignore[operator]
                raise RequestInvalid('range_end_s must be greater than range_start_s')
        elif type(self.extend_seconds) is not int or self.extend_seconds <= 0:
            raise RequestInvalid('extend_seconds must be a positive integer')


@dataclass
class RenderResult:
    """WAV bytes plus provenance. Metadata is stored with the take."""

    wav: bytes
    metadata: dict[str, Any]

    def __post_init__(self):
        if not isinstance(self.wav, (bytes, bytearray)) or not self.wav:
            raise InvalidAudio('engine returned no audio bytes')
        self.wav = bytes(self.wav)
        if not isinstance(self.metadata, dict) or 'engine_id' not in self.metadata:
            raise ValueError('render metadata must include engine_id')
        info = inspect_wav(self.wav)
        self.metadata.setdefault('sample_rate', info['sample_rate'])
        self.metadata.setdefault('channels', info['channels'])
        self.metadata.setdefault('frames', info['frames'])
        self.metadata.setdefault('audio_seconds', info['seconds'])

    def as_http_payload(self) -> dict:
        """Metadata without the bytes, for JSON status endpoints."""
        return dict(self.metadata)


def inspect_wav(data: bytes) -> dict:
    """Validate a RIFF/WAVE blob and return its shape, or raise InvalidAudio.

    Checks framing *and* that the declared frame count is fully present, so a
    truncated render is rejected here rather than played as a short take.
    """
    try:
        with wave.open(io.BytesIO(data), 'rb') as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            if frames <= 0 or rate <= 0 or channels <= 0:
                raise InvalidAudio('engine returned an empty WAV')
            expected = frames * channels * width
            if len(wav.readframes(frames)) != expected:
                raise InvalidAudio('engine returned a truncated WAV')
            return {'frames': frames, 'sample_rate': rate, 'channels': channels,
                    'seconds': round(frames / rate, 3)}
    except (wave.Error, EOFError) as exc:
        raise InvalidAudio('engine returned bytes that are not a WAV') from exc


ProgressCallback = Callable[[int], None]
ProcessCallback = Callable[[int], None]


class Engine(ABC):
    """Base class for a render backend.

    Subclasses declare ``id``, ``label`` and ``kind`` as class attributes and
    implement ``limits``, ``capabilities``, ``health`` and ``_render``.
    """

    id: str = ''
    label: str = ''
    kind: str = ''
    requirements: tuple[str, ...] = ()

    def __init__(self):
        if self.kind not in KINDS:
            raise ValueError(f'{type(self).__name__}.kind must be one of {KINDS}')

    # -- description -----------------------------------------------------
    @property
    @abstractmethod
    def limits(self) -> Limits:
        ...

    @property
    @abstractmethod
    def capabilities(self) -> Capabilities:
        ...

    @abstractmethod
    def health(self) -> EngineStatus:
        """Probe the backend. Must be cheap enough to call from a status poll."""

    def describe(self) -> dict:
        """Everything a picker UI or the agent needs, in one JSON-safe dict."""
        status = self.health()
        supports_generate = bool(getattr(self, 'supports_generate', True))
        selectable = bool(status.available and supports_generate)
        if selectable:
            selectable_reason = None
        elif not status.available:
            selectable_reason = status.detail or f'engine is {status.state}'
        else:
            selectable_reason = 'engine does not support generate'
        return {
            'id': self.id,
            'label': self.label,
            'kind': self.kind,
            'available': status.available,
            'supports_generate': supports_generate,
            'selectable': selectable,
            'selectable_reason': selectable_reason,
            'requirements': list(self.requirements),
            'limits': self.limits.as_dict(),
            'capabilities': self.capabilities.as_dict(),
            'status': status.as_dict(),
        }

    # -- rendering -------------------------------------------------------
    def render(self, request: RenderRequest,
               on_progress: ProgressCallback | None = None,
               on_process: ProcessCallback | None = None) -> RenderResult:
        """Render one take or raise. Never returns placeholder audio."""
        request.validate_common()
        status = self.health()
        if status.state == 'busy':
            raise EngineBusy(f'{self.label} is busy with another render',
                             engine_id=self.id, detail=status.detail)
        if not status.available:
            raise EngineUnavailable(
                f'{self.label} is unavailable: {status.detail or status.state}',
                engine_id=self.id, detail=status.detail)
        if request.mode == 'repaint' and not self.capabilities.repaint:
            raise CapabilityUnsupported(
                f'{self.label} ({self.id}) does not support repaint '
                f'(declared repaint=false)', engine_id=self.id, operation='repaint')
        if request.mode == 'extend' and not self.capabilities.continuation:
            raise CapabilityUnsupported(
                f'{self.label} ({self.id}) does not support extend '
                f'(declared continuation=false)', engine_id=self.id, operation='extend')
        # The duration window bounds *requested takes*. A repaint inherits its
        # parent's length and an extend deliberately exceeds the single-take
        # cap, so the engine validates those geometries itself.
        if request.mode == 'generate' and not self.limits.contains(request.duration_seconds):
            raise RequestInvalid(
                f'{self.label} supports {self.limits.min_seconds:g}-'
                f'{self.limits.max_seconds:g}s takes, not {request.duration_seconds:g}s',
                engine_id=self.id)
        started = time.monotonic()
        result = self._render(request, on_progress, on_process)
        result.metadata.setdefault('engine_id', self.id)
        result.metadata.setdefault('engine_kind', self.kind)
        result.metadata.setdefault('seed', request.seed)
        result.metadata.setdefault('requested_seconds', request.duration_seconds)
        result.metadata.setdefault('elapsed_seconds', round(time.monotonic() - started, 3))
        result.metadata.setdefault('rendered_at', _now())
        return result

    @abstractmethod
    def _render(self, request: RenderRequest,
                on_progress: ProgressCallback | None = None,
                on_process: ProcessCallback | None = None) -> RenderResult:
        ...

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _emit(on_progress: ProgressCallback | None, percent: int) -> None:
        """Progress reporting must never break a render."""
        if on_progress is None:
            return
        try:
            on_progress(max(0, min(100, int(percent))))
        except Exception:
            pass

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f'<{type(self).__name__} id={self.id!r} kind={self.kind!r}>'
