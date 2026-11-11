"""Engine registry: user preference, honest fallback, no mocks.

Rules this module enforces:

* A preference names an engine; it is used when that engine is available.
* If it is not, the registry may pick another *real* engine and records both
  the requested and the used engine in the result metadata, plus the reason.
* An explicitly requested engine that is unavailable raises
  ``EngineUnavailable`` — it never silently renders on a different engine.
* When nothing is available, ``NoEngineAvailable`` lists every engine's actual
  health, so the UI can show why. There is no silent mock engine.
* A render that *starts* and then fails is never retried on another engine;
  that would surprise the user with a second paid/slow render.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .ace_local import AceLocalEngine
from .ace_remote import AceRemoteEngine
from .contract import (Engine, EngineError, EngineStatus, EngineUnavailable, RenderRequest,
                       RenderResult)
from .minimax_m3_comfy import MiniMaxM3ComfyEngine

PREFERENCE_FILE = 'engine-preference.json'
HEALTH_TTL_SECONDS = 10.0

# Legacy names that appear in already-approved project drafts. A draft's
# ``engine`` field said 'ace' long before engines had ids; mapping it here keeps
# old approvals renderable without letting a client name a nonexistent engine.
ALIASES = {'ace': 'ace_local'}


def resolve_id(engine_id: str | None) -> str | None:
    """Canonical engine id for a legacy or current name."""
    if engine_id is None:
        return None
    return ALIASES.get(engine_id, engine_id)


class NoEngineAvailable(EngineUnavailable):
    """No registered engine can render right now."""

    def __init__(self, statuses: dict[str, dict]):
        summary = '; '.join(f"{eid}={s['state']}" for eid, s in statuses.items()) or 'none registered'
        super().__init__(f'no music engine is available ({summary})')
        self.statuses = statuses


@dataclass
class Selection:
    engine: Engine | None
    requested_id: str | None
    reason: str
    skipped: list[dict] = field(default_factory=list)

    @property
    def fell_back(self) -> bool:
        return bool(self.engine and self.requested_id
                    and self.engine.id != self.requested_id)

    def as_dict(self) -> dict:
        return {'engine_id': self.engine.id if self.engine else None,
                'requested_id': self.requested_id,
                'fell_back': self.fell_back,
                'reason': self.reason,
                'skipped': self.skipped}


class PreferenceStore:
    """Tiny JSON file holding the user's engine choice (profile-scoped)."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, preferred: str | None, fallback: bool = True) -> dict:
        data = {'preferred': preferred, 'allow_fallback': bool(fallback),
                'updated_at': time.time()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix='.pref-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return data


class Registry:
    """Ordered collection of engines plus the selection policy."""

    def __init__(self, engines: Sequence[Engine] | None = None,
                 preference_path: str | os.PathLike | None = None,
                 health_ttl: float = HEALTH_TTL_SECONDS):
        self.engines = list(engines) if engines is not None else default_engines()
        ids = [e.id for e in self.engines]
        if len(ids) != len(set(ids)):
            raise ValueError('engine ids must be unique')
        self.prefs = PreferenceStore(preference_path) if preference_path else None
        self.health_ttl = health_ttl
        self._cache: dict[str, tuple[float, EngineStatus]] = {}
        self._lock = threading.Lock()

    # -- engine lookup ---------------------------------------------------
    def get(self, engine_id: str) -> Engine:
        engine_id = resolve_id(engine_id)
        for engine in self.engines:
            if engine.id == engine_id:
                return engine
        raise KeyError(engine_id)

    def __iter__(self):
        return iter(self.engines)

    # -- health / description -------------------------------------------
    def health(self, engine: Engine, fresh: bool = False) -> EngineStatus:
        """Cached health probe. Remote engines are never polled per keystroke."""
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(engine.id)
            if cached and not fresh and now - cached[0] < self.health_ttl:
                return cached[1]
        status = engine.health()
        with self._lock:
            self._cache[engine.id] = (now, status)
        return status

    def invalidate(self, engine_id: str | None = None) -> None:
        with self._lock:
            if engine_id is None:
                self._cache.clear()
            else:
                self._cache.pop(engine_id, None)

    def describe_all(self, fresh: bool = False) -> list[dict]:
        described = []
        for engine in self.engines:
            status = self.health(engine, fresh=fresh)
            # A picker needs one unambiguous answer per engine: may this engine
            # take a plain generate right now, and if not, why not. Generation is
            # the base mode every Engine implements; an engine may still opt out
            # with a class attribute so a future adapter is not misrepresented.
            supports_generate = bool(getattr(engine, 'supports_generate', True))
            selectable = bool(status.available and supports_generate)
            if selectable:
                selectable_reason = None
            elif not status.available:
                selectable_reason = status.detail or f'engine is {status.state}'
            else:
                selectable_reason = 'engine does not support generate'
            described.append({
                'id': engine.id,
                'label': engine.label,
                'kind': engine.kind,
                'available': status.available,
                'supports_generate': supports_generate,
                'selectable': selectable,
                'selectable_reason': selectable_reason,
                'requirements': list(engine.requirements),
                'limits': engine.limits.as_dict(),
                'capabilities': engine.capabilities.as_dict(),
                'status': status.as_dict(),
            })
        return described

    def statuses(self, fresh: bool = False) -> dict[str, dict]:
        return {e.id: self.health(e, fresh=fresh).as_dict() for e in self.engines}

    def default_engine_id(self) -> str | None:
        """The engine a request with no explicit choice would use right now.

        A picker UI needs this to show a preselection without re-implementing
        the selection policy. It is the stored preference when that engine is
        selectable, otherwise the first selectable engine; when nothing is
        available it still reports the preference (or the first known id) so the
        UI can name what the user *asked* for alongside its unavailable state.
        """
        selection = self.select()
        if selection.engine is not None:
            return selection.engine.id
        preferred = resolve_id(self.preference()['preferred'])
        if preferred and preferred in {e.id for e in self.engines}:
            return preferred
        return self.engines[0].id if self.engines else None

    def status_payload(self, fresh: bool = False) -> dict:
        """The /status engine block: everything a client needs, nothing hardcoded."""
        return {'engines': self.describe_all(fresh=fresh),
                'default_engine_id': self.default_engine_id()}

    # -- preference ------------------------------------------------------
    def preference(self) -> dict:
        data = self.prefs.load() if self.prefs else {}
        return {'preferred': data.get('preferred'),
                'allow_fallback': bool(data.get('allow_fallback', True))}

    def set_preference(self, preferred: str | None, fallback: bool = True) -> dict:
        preferred = resolve_id(preferred)
        if preferred is not None and preferred not in {e.id for e in self.engines}:
            raise ValueError(f'unknown engine: {preferred}')
        if not self.prefs:
            raise RuntimeError('registry has no preference store')
        data = self.prefs.save(preferred, fallback)
        return {'preferred': data['preferred'], 'allow_fallback': data['allow_fallback']}

    # -- selection -------------------------------------------------------
    def select(self, preferred: str | None = None) -> Selection:
        """Pick an engine. ``preferred=None`` means "use the stored preference"."""
        stored = self.preference()
        requested = resolve_id(preferred if preferred is not None else stored['preferred'])
        allow_fallback = stored['allow_fallback'] if preferred is None else True
        statuses = {e.id: self.health(e) for e in self.engines}
        skipped: list[dict] = []
        # A busy engine is installed and correct, but it cannot take this render
        # now. Only a ready/degraded engine is selected when a preference is set;
        # a busy preference falls back (and says so) rather than failing a take
        # that another engine could render.
        selectable = ('ready', 'degraded')

        if requested and requested in statuses:
            status = statuses[requested]
            if status.state in selectable:
                return Selection(self.get(requested), requested,
                                 f'preferred engine {requested} is {status.state}')
            skipped.append({'engine_id': requested, 'state': status.state,
                            'detail': status.detail})
            if not allow_fallback:
                return Selection(None, requested,
                                 f'preferred engine {requested} is {status.state} and '
                                 f'fallback is disabled', skipped)
            reason_prefix = (f'preferred engine {requested} is {status.state} '
                             f'({status.detail}); ')
        elif requested:
            skipped.append({'engine_id': requested, 'state': 'unknown',
                            'detail': 'not registered on this host'})
            reason_prefix = f'preferred engine {requested} is not registered; '
        else:
            reason_prefix = 'no preference set; '

        for engine in self.engines:
            if engine.id == requested:
                continue
            status = statuses[engine.id]
            if status.state in selectable:
                return Selection(engine, requested,
                                 reason_prefix + f'using {engine.id} ({status.state})',
                                 skipped)
            skipped.append({'engine_id': engine.id, 'state': status.state,
                            'detail': status.detail})
        return Selection(None, requested,
                         reason_prefix + 'no other engine is available', skipped)

    # -- rendering -------------------------------------------------------
    def render(self, request: RenderRequest, engine_id: str | None = None,
               preferred: str | None = None,
               on_progress: Callable[[int], None] | None = None,
               on_process: Callable[[int], None] | None = None) -> RenderResult:
        """Render on the selected engine, recording the selection in metadata.

        ``engine_id`` is an explicit user choice: if unavailable it raises. No
        explicit engine means the registry may fall back, and says so.
        """
        if engine_id is not None:
            engine_id = resolve_id(engine_id)
            try:
                engine = self.get(engine_id)
            except KeyError as exc:
                raise EngineUnavailable(f'unknown engine: {engine_id}') from exc
            status = self.health(engine)
            if not status.available:
                raise EngineUnavailable(
                    f'{engine.label} is unavailable: {status.detail or status.state}',
                    engine_id=engine.id, detail=status.detail)
            requested, fell_back, reason = engine.id, False, 'explicit engine choice'
        else:
            selection = self.select(preferred)
            if selection.engine is None:
                raise NoEngineAvailable(self.statuses(fresh=True))
            engine = selection.engine
            requested, fell_back, reason = selection.requested_id, selection.fell_back, selection.reason

        result = engine.render(request, on_progress=on_progress, on_process=on_process)
        result.metadata.update({
            'requested_engine': requested,
            'engine_id': engine.id,
            'fell_back': fell_back,
            'selection_reason': reason,
        })
        return result


def default_engines() -> list[Engine]:
    """The engines this build knows about, in preference-free order."""
    return [AceLocalEngine(), AceRemoteEngine(), MiniMaxM3ComfyEngine()]
