"""Multi-engine layer for Music Studio.

Public surface::

    from music_studio.engines import Registry, RenderRequest
    reg = Registry(preference_path=...)
    reg.describe_all()
    result = reg.render(RenderRequest(caption=..., duration_seconds=30, seed=1))
    result.wav, result.metadata
"""
from .contract import (BPM_MAX, BPM_MIN, KINDS, MODES, STATES, VOCAL_LANGUAGES, Capabilities,
                       CapabilityUnsupported, Engine, EngineBusy,
                       EngineError, EngineStatus, EngineUnavailable, HealthCheck, InvalidAudio,
                       Limits, RenderRequest, RenderResult, RequestInvalid, inspect_wav)
from .ace_local import AceLocalEngine
from .ace_remote import AceRemoteEngine
from .minimax_m3_comfy import MiniMaxM3ComfyEngine
from .registry import (NoEngineAvailable, PreferenceStore, Registry, Selection,
                       default_engines, resolve_id)

__all__ = [
    'BPM_MAX', 'BPM_MIN', 'KINDS', 'MODES', 'STATES', 'VOCAL_LANGUAGES', 'AceLocalEngine',
    'AceRemoteEngine',
    'Capabilities', 'CapabilityUnsupported', 'Engine', 'EngineBusy', 'EngineError', 'EngineStatus',
    'EngineUnavailable', 'HealthCheck', 'InvalidAudio', 'Limits', 'MiniMaxM3ComfyEngine',
    'NoEngineAvailable', 'PreferenceStore', 'Registry', 'RenderRequest', 'RenderResult',
    'RequestInvalid', 'Selection', 'default_engines', 'inspect_wav', 'resolve_id',
]
# The layer ships with the studio: its version is the build's version, read from the stamp.
from ..version_stamp import version as _version

__version__ = _version()
