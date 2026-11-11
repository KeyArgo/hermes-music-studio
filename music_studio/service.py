"""Profile-scoped studio operations and detached generation workers."""
import logging
import json
import os
import copy
import random
import subprocess
import sys
import uuid
import time
from pathlib import Path

# Resolve the root per call so profile switches and standalone isolation work
# even if this module was imported before the standalone app.
def default_data_dir(plugin_id):
    """The plugin's own data directory, never redirected by the library pointer."""
    standalone_root = os.environ.get('HEWN_STUDIO_DATA_DIR')
    if standalone_root:
        path = Path(standalone_root).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    try:
        from plugins.plugin_storage import plugin_data_dir as hermes_plugin_data_dir
    except ImportError:
        # Running outside Hermes (a clone, a test run): the same location convention, resolved
        # locally, so the package and its tests work without the host installed.
        base = Path(os.environ.get('HERMES_HOME') or (Path.home() / '.hermes'))
        path = base / 'plugin-data' / plugin_id
        path.mkdir(parents=True, exist_ok=True)
        return path
    return hermes_plugin_data_dir(plugin_id)


def plugin_data_dir(plugin_id):
    """Where the library lives: the plugin's own dir, or the one it points at."""
    base = default_data_dir(plugin_id)
    try:
        from . import settings
        moved = settings.library_dir(base)
    except Exception:
        moved = None
    if moved is not None and moved.resolve() != base.resolve():
        moved.mkdir(parents=True, exist_ok=True)
        return moved
    return base

from .store import (DEFAULT_ENGINE_ID, Store, estimate_render_seconds, limits,
                    sections_for)
from .jobs import Jobs
from .engines import (CapabilityUnsupported, EngineUnavailable, MiniMaxM3ComfyEngine,
                      Registry, resolve_id)
from .engines.registry import PREFERENCE_FILE
from .network import TargetStore
import ace_engine

PLUGIN_ID = 'hermes-music-studio'
_ROOT = Path(__file__).resolve().parents[1]

# Detached workers this process started; kept only so their exit status is
# collected (a short-lived agent can still leave a render running).
_workers = []

# One registry per data directory. The registry caches engine health for a few
# seconds, which is what makes a per-poll /status cheap enough to hit often.
_REGISTRIES = {}


def _reap_workers():
    for proc in list(_workers):
        if proc.poll() is not None:
            _workers.remove(proc)


def store():
    return Store(plugin_data_dir(PLUGIN_ID))


def registry(s=None):
    """The engine registry for this profile's data directory."""
    s = s or store()
    key = str(s.root)
    found = _REGISTRIES.get(key)
    if found is None:
        found = Registry(preference_path=s.root / PREFERENCE_FILE)
        _REGISTRIES[key] = found
    return found


def engine_id_for(project):
    """The engine an approved draft pins, in canonical id form.

    The draft's ``engine`` field is the legacy name ('ace'); the registry maps
    it to a real engine id. A take records this value at enqueue time, so the
    render cannot drift to a different engine after approval.
    """
    return resolve_id((project or {}).get('engine') or 'ace')


def _engine_status(reg, engine):
    """Refuse an engine that cannot take a generate right now, with its own words."""
    status = reg.health(engine, fresh=True)
    if not status.available:
        raise EngineUnavailable(
            f'{engine.label} is unavailable: {status.detail or status.state}',
            engine_id=engine.id, detail=status.detail)
    if not bool(getattr(engine, 'supports_generate', True)):
        raise CapabilityUnsupported(
            f'{engine.label} ({engine.id}) does not support generate',
            engine_id=engine.id, operation='generate')
    return engine.id


# Per-engine fix hints for the doctor. only add a command verified on a real machine
FIXES: dict[str, str | None] = {
    'ace_local': None,
    'ace_remote': None,
    'minimax_m3_comfy': None,
}


def doctor(reg=None):
    """Explain every registered engine's setup; never raises.

    One row per engine: whether it may render (``ready``), the engine's own
    state word and detail, its per-check breakdown, and a verified fix command
    when one exists. A health probe that blows up becomes that engine's row
    (state ``error``) and the rest are still reported.
    """
    reg = reg or registry()
    rows = []
    for engine in reg:
        try:
            status = reg.health(engine, fresh=True)
        except Exception as exc:
            rows.append({'id': engine.id, 'label': engine.label, 'ready': False,
                         'state': 'error',
                         'detail': f'health check raised '
                                   f'{type(exc).__name__}: {exc}',
                         'checks': [], 'fix': FIXES.get(engine.id)})
            continue
        checks = [c if isinstance(c, dict) else
                  {'name': c.name, 'ok': bool(c.ok), 'detail': c.detail}
                  for c in (status.checks or [])]
        rows.append({'id': engine.id, 'label': engine.label,
                     'ready': status.available,
                     'state': status.state, 'detail': status.detail,
                     'checks': checks, 'fix': FIXES.get(engine.id)})
    return rows


def _check_duration(project, engine_id):
    """Refuse a take whose requested duration the resolved engine cannot render.

    The draft may name one engine and the caller another (``engine_id`` on
    ``/generate``/``/batch``). Whichever engine will actually render decides the
    window, so a draft that is legal on MiniMax cannot be queued onto ACE at
    240 s, and vice versa.
    """
    from .store import engine_limits
    duration = (project or {}).get('duration_seconds')
    minimum, maximum, _ = engine_limits(engine_id)
    if type(duration) is int and not minimum <= duration <= maximum:
        raise ValueError(f'duration must be between {minimum} and '
                         f'{maximum} seconds')


def resolve_engine(project, engine_id=None, reg=None):
    """Resolve a generate target: explicit ``engine_id`` -> draft -> default.

    An explicit or draft-named engine that is unknown is refused (the API turns
    that into 400); one that is registered but not available is refused with
    its health detail (409). Neither case silently substitutes another engine —
    a different backend must be a different, visible choice.
    """
    reg = reg or registry()
    if engine_id is not None:
        if not isinstance(engine_id, str) or not engine_id.strip():
            raise ValueError('engine_id must be a non-empty string')
        try:
            engine = reg.get(resolve_id(engine_id) or engine_id)
        except KeyError:
            raise ValueError(f'unknown engine: {engine_id}') from None
        return _engine_status(reg, engine)

    draft_engine = (project or {}).get('engine')
    if draft_engine is not None:
        try:
            engine = reg.get(resolve_id(draft_engine) or draft_engine)
        except KeyError:
            raise ValueError(f'unknown engine: {draft_engine}') from None
        return _engine_status(reg, engine)

    default = reg.default_engine_id()
    if default is None:
        raise EngineUnavailable('no music engine is registered')
    return _engine_status(reg, reg.get(default))


def _selected_target(s, project):
    """The stored ComfyUI target a draft names, or None.

    A deleted target is refused rather than silently replaced by the engine's
    default URL: the user chose an endpoint, and a different one is a different
    backend.
    """
    target_id = (project or {}).get('comfy_target_id')
    if target_id is None:
        return None
    try:
        return TargetStore(s.root).get(target_id)
    except (KeyError, ValueError):
        raise ValueError(f'unknown target: {target_id}') from None


def _preflight_target(target, engine_id=None, draft_engine=None):
    """Probe the exact endpoint the take will render on, and pin its engine.

    This is a health read (no model launch). It never falls back to another
    engine or another URL: an unreachable target is a visible 409.
    """
    engine_id = engine_id if engine_id is not None else draft_engine
    if engine_id is not None and resolve_id(engine_id) != 'minimax_m3_comfy':
        raise ValueError('comfy_target_id requires engine minimax_m3_comfy')
    engine = MiniMaxM3ComfyEngine(base_url=target['url'])
    status = engine.health()
    if not status.available:
        raise EngineUnavailable(
            f'{engine.label} at {target["name"]} ({target["url"]}) is unavailable: '
            f'{status.detail or status.state}',
            engine_id=engine.id, detail=status.detail)
    if not bool(getattr(engine, 'supports_generate', True)):
        raise CapabilityUnsupported(
            f'{engine.label} ({engine.id}) does not support generate',
            engine_id=engine.id, operation='generate')
    return engine.id


def require_capability(engine_id, mode, reg=None):
    """Refuse a source-conditioned op the chosen engine cannot perform.

    Called before enqueue so the user gets a 409 naming the engine and the
    operation, and again by the worker so a stale client cannot bypass it.
    """
    if mode not in ('repaint', 'extend'):
        return
    if not engine_id:
        return
    reg = reg or registry()
    try:
        engine = reg.get(resolve_id(engine_id) or engine_id)
    except KeyError:
        raise ValueError(f'unknown engine: {engine_id}') from None
    attribute = 'repaint' if mode == 'repaint' else 'continuation'
    if not bool(getattr(engine.capabilities, attribute, False)):
        raise CapabilityUnsupported(
            f'{engine.label} ({engine.id}) does not support {mode} '
            f'(declared {attribute}=false)',
            engine_id=engine.id, operation=mode)
    return engine.id


def _parent_engine_id(s, project_id, take_id):
    """The engine a child take will inherit from its parent (or the draft)."""
    project = s.get(project_id)
    for take in project.get('takes') or []:
        if take.get('take_id') == take_id:
            return take.get('engine_id') or engine_id_for(project)
    raise KeyError(take_id)


def _recover(s):
    try:
        return Jobs(s).recover()
    except Exception:
        logging.exception('Unable to reconcile interrupted takes')
        return 0


def _eta_seconds(take, now, expected):
    """Rough remaining time for a live render, from elapsed time and progress.

    ``expected`` is this take's own modelled wall clock (per-duration, see
    store.estimate_render_seconds), refined by the worker's coarse progress
    milestone once one has landed. Returned raw: the caller smooths it against
    the previous poll.
    """
    if take.get('state') != 'running':
        return None
    started = take.get('started_at')
    if type(started) not in (int, float):
        return None
    elapsed = max(0.0, now - started)
    progress = take.get('progress')
    if type(progress) in (int, float) and progress > 0:
        remaining = (100.0 - progress) / progress * elapsed
    else:
        remaining = expected - elapsed
    return max(0.0, float(remaining))


# Per-take ETA smoothing. Progress arrives as a coarse staircase (5 s -> 10 %,
# 15 s -> 25 %, ...), so a raw recomputation swings by minutes at each step
# ("126 s left" one second, "45 s left" the next). This keeps the displayed
# value monotone and limits how fast it may fall.
_ETA_STATE = {}
_ETA_MAX_DROP = 0.3   # fraction of the previous estimate it may fall per poll
_ETA_STATE_TTL = 3600.0


def _smooth_eta(key, candidate, now):
    previous = _ETA_STATE.get(key)
    if previous is None or candidate is None:
        eta = candidate
    else:
        previous_value, _ = previous
        max_drop = max(1.0, previous_value * _ETA_MAX_DROP)
        # Never increase between polls, and never fall faster than max_drop.
        eta = min(previous_value, max(candidate, previous_value - max_drop))
    if eta is not None:
        _ETA_STATE[key] = (eta, now)
        if len(_ETA_STATE) > 512:
            for stale in [k for k, (_, at) in _ETA_STATE.items() if now - at > _ETA_STATE_TTL]:
                del _ETA_STATE[stale]
    return None if eta is None else max(0, int(round(eta)))


def _decorate(projects, jobs):
    """Add contract fields (timestamps, progress, ETA) to every project/take."""
    now = time.time()
    root = str(jobs.store.root)
    for project in projects:
        project.setdefault('updated_at', None)
        # Stored sections win; otherwise expose the structure the lyrics imply
        # so a client can render an editor before any section exists.
        project['sections'], project['sections_source'] = sections_for(project)
        for take in project['takes']:
            take.setdefault('started_at', None)
            take.setdefault('finished_at', None)
            take.setdefault('progress', None)
            # Legacy takes (and single renders) have no queue position.
            take.setdefault('batch_id', None)
            take.setdefault('queue_position', None)
            take.setdefault('requested_seed', take.get('seed'))
            take.setdefault('seed_locked', False)
            # Source-conditioned lineage. A plain render has none.
            take.setdefault('kind', 'generate')
            take.setdefault('parent_take_id', None)
            take.setdefault('lineage', [])
            take.setdefault('extend_seconds', None)
            take.setdefault('range_start_s', None)
            take.setdefault('range_end_s', None)
            take.setdefault('splice_start', None)
            take.setdefault('overlap_seconds', None)
            take.setdefault('source_duration', None)
            take.setdefault('planned_duration', None)
            # Provenance recorded by the worker when the take succeeded; a
            # legacy take predates it.
            take.setdefault('metadata', None)
            take.setdefault('qc', None)
            take.setdefault('edits', [])
            take.setdefault('playable_edit_id', None)
            # ETA is per duration: a 180 s take is not late because a 20 s take
            # finished quickly. The snapshot is what the render actually uses.
            snapshot = take.get('snapshot') or {}
            duration = snapshot.get('duration_seconds', project.get('duration_seconds'))
            try:
                expected = (jobs.observed_render_seconds(duration)
                            or estimate_render_seconds(duration))
            except (TypeError, ValueError):
                expected = estimate_render_seconds(60)
            if take['state'] == 'running':
                started = take.get('started_at')
                if take.get('progress') is None and type(started) in (int, float):
                    take['progress'] = min(99, int(100 * max(0.0, now - started) / expected))
            key = (root, take['take_id'], take.get('started_at'))
            take['eta_seconds'] = _smooth_eta(key, _eta_seconds(take, now, expected), now)
        active = [t for t in project['takes'] if t['state'] == 'running']
        queued = [t for t in project['takes'] if t['state'] == 'queued']
        project['active_take_id'] = (
            active[0]['take_id'] if active else
            (min(queued, key=lambda t: t.get('queue_position') or 0)['take_id']
             if queued else None))
    return projects


def status():
    s = store()
    _reap_workers()
    jobs = Jobs(s)
    _recover(s)
    warnings = []
    projects = _decorate(s.all(warnings=warnings), jobs)
    reg = registry(s)
    # The engine block is data: a client renders its picker from this list and
    # never hardcodes a backend name. ``engine_available`` is kept for clients
    # written before the list existed: "at least one engine can render now".
    engines = reg.describe_all()
    return {'success': True,
            'engine_available': any(e['available'] for e in engines),
            'engines': engines,
            'default_engine_id': reg.default_engine_id(),
            # The top-level duration contract is the *default drafting engine's*
            # window (ACE local, 10-180 s); it is not a cap on every engine.
            # MiniMax Music 3 declares 10-360 s in engines[].limits, and a draft
            # pinned to it is validated against that window instead.
            'limits': limits(),
            'limits_engine_id': DEFAULT_ENGINE_ID,
            'projects': projects, 'warnings': warnings}


def take_peaks(project_id, take_id, buckets=None, edit_id=None):
    """Server-side waveform buckets for one take (streamed, cached sidecar).

    ``edit_id`` draws the named QC edit instead of the raw take.
    """
    from .peaks import PEAKS_DEFAULT_BUCKETS, peaks_for
    if buckets is None:
        buckets = PEAKS_DEFAULT_BUCKETS
    return peaks_for(Jobs(store()), project_id, take_id, buckets, edit_id=edit_id)


def recover_on_load():
    """Fail takes whose worker vanished while no process was watching (import-time)."""
    try:
        _recover(store())
    except Exception:
        logging.exception('Unable to reconcile interrupted takes at import')


def recover_interrupted(*, confirmed=False):
    """Recover after the user verifies no old ACE subprocess is still running."""
    return {'success': True, 'recovered': Jobs(store()).reconcile_interrupted(confirmed=confirmed)}


def _spawn_worker(s, project_id, take_id):
    """Start the render in a detached session; the worker owns the GPU lease."""
    _reap_workers()
    log_dir = s.root / 'worker-logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join(p for p in (str(_ROOT), env.get('PYTHONPATH', '')) if p)
    args = [sys.executable, '-m', 'music_studio.worker',
            '--data-dir', str(s.root), '--project', project_id, '--take', take_id]
    with (log_dir / f'{take_id}.log').open('ab') as log:
        proc = subprocess.Popen(
            args, cwd=str(_ROOT), env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True)
    _workers.append(proc)
    return proc


def _engine_and_target(s, project, engine_id=None):
    """The engine this take will render on, and the ComfyUI endpoint it may use.

    A named target belongs to the ComfyUI engine, not to the song. Before this, a draft pinned to a
    target made every other engine refuse — so "compare this song on both models" failed on the ACE
    row with "comfy_target_id requires engine minimax_m3_comfy". The pin now simply does not apply to
    an engine that has no use for it, and it is still probed before an M3 take is queued.
    """
    target = _selected_target(s, project)
    resolved = resolve_engine(project, engine_id, reg=registry(s))
    if target is not None:
        if resolve_id(resolved) == 'minimax_m3_comfy':
            resolved = _preflight_target(target, resolved, draft_engine=project.get('engine'))
        else:
            target = None
    return resolved, target


def generate(project_id, seed, lock_seed=False, engine_id=None):
    s = store()
    jobs = Jobs(s)
    take = None
    try:
        project = s.get(project_id)
        resolved, target = _engine_and_target(s, project, engine_id)
        _check_duration(project, resolved)
        take = jobs.enqueue(project_id, seed, lock_seed=lock_seed,
                            engine_id=resolved, target=target)
        try:
            proc = _spawn_worker(s, project_id, take['take_id'])
        except Exception as exc:
            raise RuntimeError('worker could not start') from exc
        try:
            jobs.attach(project_id, take['take_id'], proc.pid)
        except Exception:
            # The worker records its own pid on claim; attach is best-effort.
            logging.exception('Unable to attach worker pid to take')
    except BaseException:
        if take is not None:
            try:
                jobs.abort(project_id, take['take_id'], 'worker could not start')
            except Exception:
                logging.exception('Unable to abort unstartable take')
        raise
    return {'success': True, 'take': take}


def _launch(s, project_id, take):
    """Start the detached worker for a just-enqueued take; abort it on failure."""
    jobs = Jobs(s)
    try:
        proc = _spawn_worker(s, project_id, take['take_id'])
    except Exception as exc:
        raise RuntimeError('worker could not start') from exc
    try:
        jobs.attach(project_id, take['take_id'], proc.pid)
    except Exception:
        # The worker records its own pid on claim; attach is best-effort.
        logging.exception('Unable to attach worker pid to take')
    return take


def repaint(project_id, take_id, start_s, end_s, caption=None, lyrics_seed=None):
    """Queue a range repaint as a NEW take derived from a succeeded take."""
    s = store()
    jobs = Jobs(s)
    take = None
    seed = random.SystemRandom().randrange(0, 2**32)
    try:
        # Refuse before enqueue: the child inherits the parent's engine, and an
        # engine without repaint would otherwise queue a render destined to fail.
        require_capability(_parent_engine_id(s, project_id, take_id), 'repaint',
                           reg=registry(s))
        take = jobs.enqueue_repaint(project_id, take_id, start_s, end_s, seed,
                                    caption=caption, lyrics_seed=lyrics_seed)
        return {'success': True, 'take': _launch(s, project_id, take)}
    except BaseException:
        if take is not None:
            try:
                jobs.abort(project_id, take['take_id'], 'worker could not start')
            except Exception:
                logging.exception('Unable to abort unstartable repaint')
        raise


def snippet(project_id, take_id, start_s, end_s, instruction):
    """Change one slice of a finished take: words in, a repaint of that range queued.

    ACE is the only engine that can do this, and the refusal for the others is the engine's own
    capability check — never a silent fallback to a whole-song render that would sound different
    everywhere. The follow-up (`splice_snippet`) copies the parent back outside the range, so the rest
    of the song is byte-identical rather than "close".
    """
    from . import intent

    s = store()
    project = s.get(project_id)
    result = intent.apply(instruction, project)
    caption = result['patch'].get('caption') or None
    queued = repaint(project_id, take_id, start_s, end_s, caption=caption)
    s.remember(project_id, kind='snippet', text=instruction,
               section=f'{start_s}-{end_s}', take_id=queued['take']['take_id'],
               patch={'caption': caption})
    queued['notes'] = result['notes']
    queued['caption'] = caption
    queued['note'] = ('the range is re-rendered as its own take; splice it back to make the rest of '
                      'the song byte-identical')
    return queued


def splice_snippet(project_id, child_take_id):
    """Splice a finished repaint back into its parent: the untouched audio is copied, not re-rendered."""
    s = store()
    project = s.get(project_id)
    child = next((t for t in project.get('takes') or [] if t.get('take_id') == child_take_id), None)
    if child is None:
        raise KeyError('take not found')
    parent_id = child.get('parent_take_id')
    if not parent_id:
        raise ValueError('that take has no parent to splice into')
    return splice_section(project_id, parent_id, child_take_id)


def extend(project_id, take_id, extend_seconds, caption=None, lyrics=None):
    """Queue an extension as a NEW take derived from a succeeded take."""
    s = store()
    jobs = Jobs(s)
    take = None
    seed = random.SystemRandom().randrange(0, 2**32)
    try:
        require_capability(_parent_engine_id(s, project_id, take_id), 'extend',
                           reg=registry(s))
        take = jobs.enqueue_extension(project_id, take_id, extend_seconds, seed,
                                      caption=caption, lyrics=lyrics)
        return {'success': True, 'take': _launch(s, project_id, take)}
    except BaseException:
        if take is not None:
            try:
                jobs.abort(project_id, take['take_id'], 'worker could not start')
            except Exception:
                logging.exception('Unable to abort unstartable extension')
        raise


def batch(project_id, count, seed_base=None, engine_id=None):
    """Queue a serial batch and start the one worker that drains it.

    A single detached worker owns the GPU lease for the whole batch and renders
    the takes back to back, so no further user action (and no second render)
    is involved. A failed take is recorded and the worker moves on.
    """
    s = store()
    jobs = Jobs(s)
    takes = None
    try:
        project = s.get(project_id)
        target = _selected_target(s, project)
        if target is not None:
            resolved = _preflight_target(target, engine_id, draft_engine=project.get('engine'))
        else:
            resolved = resolve_engine(project, engine_id, reg=registry(s))
        _check_duration(project, resolved)
        takes = jobs.enqueue_batch(project_id, count, seed_base,
                                   engine_id=resolved, target=target)
        try:
            proc = _spawn_worker(s, project_id, takes[0]['take_id'])
        except Exception as exc:
            raise RuntimeError('worker could not start') from exc
        for take in takes:
            # Every queued take records the worker that will render it: recovery
            # treats a take with no pid past the spawn grace as orphaned, so an
            # unattached take would be failed mid-batch while its worker is busy
            # with the take ahead of it.
            try:
                jobs.attach(project_id, take['take_id'], proc.pid)
            except Exception:
                logging.exception('Unable to attach worker pid to batch take')
    except BaseException:
        for take in takes or []:
            try:
                jobs.abort(project_id, take['take_id'], 'worker could not start')
            except Exception:
                logging.exception('Unable to abort unstartable batch take')
        raise
    return {'success': True, 'takes': takes}


def cancel_batch(project_id):
    """Cancel everything still queued for a project; the running take finishes."""
    return {'success': True, 'cancelled': Jobs(store()).cancel_queued(project_id)}


def rename(project_id, title):
    return {'success': True, 'project': store().rename(project_id, title)}


def delete(project_id):
    """Delete a project and its WAVs, after reaping takes no worker owns."""
    s = store()
    _recover(s)  # a stale queued take of a dead worker must not block deletion
    return {'success': True, 'project_id': s.delete(project_id)}


def targets():
    """List the profile's named ComfyUI endpoints (no probing)."""
    return {'success': True, 'targets': TargetStore(store().root).list()}


def list_targets():
    return targets()


def create_target(body):
    if not isinstance(body, dict):
        raise ValueError('invalid target body')
    target = TargetStore(store().root).create(body.get('name'), body.get('url'))
    return {'success': True, 'target': target}


def delete_target(target_id):
    target = TargetStore(store().root).delete(target_id)
    return {'success': True, 'target': target, 'target_id': target['id']}


def probe_target(target_id):
    """Probe one target's exact ComfyUI endpoint, on demand only."""
    target = TargetStore(store().root).get(target_id)
    engine = MiniMaxM3ComfyEngine(base_url=target['url'])
    return {'success': True, 'target': target,
            'status': engine.health().as_dict()}


def hardware():
    """Local GPU devices and nvidia-smi's PID-to-GPU mapping."""
    from standalone.hardware import hardware_info
    return {'success': True, **hardware_info()}


def presets():
    from .presets import Presets
    return {'success': True, 'presets': Presets(store().root).all()}


def save_preset(body):
    from .presets import Presets
    return {'success': True, 'presets': Presets(store().root).save(body)}


def delete_preset(name):
    from .presets import Presets
    return {'success': True, 'presets': Presets(store().root).delete(name)}


# A plugin host that exits without calling status() still recovers on the next import.
recover_on_load()


# -- settings, endpoints and file management -------------------------------
def settings_info():
    """Everything the settings screen needs: values, allowed ranges, library."""
    from . import settings as st

    base = default_data_dir(PLUGIN_ID)
    return {
        'success': True,
        'settings': st.read(base),
        'defaults': st.DEFAULTS,
        'limits': {key: list(bounds) for key, bounds in st.LIMITS.items()},
        'samplers': list(st.SAMPLERS),
        'schedulers': list(st.SCHEDULERS),
        'library': _library_payload(),
    }


def save_settings(payload):
    from . import settings as st

    base = default_data_dir(PLUGIN_ID)
    written = st.write(base, payload)
    return {'success': True, 'settings': written, 'library': _library_payload()}


def _export_ledger():
    """Where each take was exported, so adding it twice does not write a second copy."""
    path = default_data_dir(PLUGIN_ID) / 'exports.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    return path, data if isinstance(data, dict) else {}


def _same_destination(known, folder, title):
    """A reused export must be the one this request asked for.

    Choosing a different folder (or renaming the take) is a request for a new copy; returning the
    old path would silently export to the wrong place.
    """
    if folder:
        try:
            wanted = Path(str(folder)).expanduser()
        except (TypeError, ValueError):
            return False
        if wanted.is_absolute() and Path(known['path']).parent != wanted:
            return False
    if title and title != (known.get('title') or ''):
        return False
    return True


def export_take(project_id, take_id, folder=None, title=None):
    """Copy one finished take into a normal music folder and say where it landed.

    A take exported before is reused while its file is still there: adding the same song to a
    second playlist must not litter the folder with copies.
    """
    from . import export

    ledger_path, ledger = _export_ledger()
    key = f'{project_id}:{take_id}'
    known = ledger.get(key) or {}
    if known.get('path') and Path(known['path']).is_file() \
            and _same_destination(known, folder, title):
        return {'success': True, 'path': known['path'], 'folder': str(Path(known['path']).parent),
                'title': known.get('title') or title or 'Music Studio take', 'bytes': Path(known['path']).stat().st_size,
                'reused': True}
    project = store().get(project_id)
    try:
        result = export.export_take(Jobs(store()), project, take_id, folder=folder, title=title)
    except ValueError as exc:
        raise ValueError(str(exc)) from None
    ledger[key] = {'path': result['path'], 'title': result.get('title') or '',
                   'folder': str(Path(result['path']).parent)}
    try:
        tmp = ledger_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(ledger, indent=2), encoding='utf-8')
        os.replace(tmp, ledger_path)
    except OSError:
        pass
    return result


def discover_renderers():
    """Scan for a ComfyUI the person already runs and report what answered."""
    from . import discovery, settings as st

    base = default_data_dir(PLUGIN_ID)
    hosts = (st.read(base).get('scan_hosts') or '').strip() or os.environ.get('MUSIC_STUDIO_SCAN_HOSTS', '')
    return discovery.scan(saved=st.comfy_url(base), extra_hosts=hosts)


def test_endpoint(url=None):
    """Probe one ComfyUI endpoint with the saved knobs; never renders anything."""
    from . import settings as st

    base = default_data_dir(PLUGIN_ID)
    chosen = (url or '').strip() or st.comfy_url(base) or os.environ.get('COMFYUI_URL')
    engine = MiniMaxM3ComfyEngine(base_url=chosen) if chosen else MiniMaxM3ComfyEngine()
    status = engine.health()
    return {'success': True, 'url': engine.base_url, 'status': status.as_dict(),
            'minimax': st.minimax_kwargs(base)}


def _library_payload():
    from . import settings as st

    base = default_data_dir(PLUGIN_ID)
    active = plugin_data_dir(PLUGIN_ID)
    projects = [p for p in active.glob('*.json')
                if p.stem not in (st.SETTINGS_FILE.replace('.json', ''), 'library', 'studio-host')]
    audio = list(active.glob('*.wav'))
    total = 0
    for path in active.glob('*'):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return {
        'default': str(base),
        'active': str(active),
        'moved': active.resolve() != base.resolve(),
        'projects': len(projects),
        'audio_files': len(audio),
        'bytes': total,
    }


def library_info():
    return {'success': True, **_library_payload()}


_LIBRARY_SKIP = ('engine-settings.json', 'library.json', 'studio-host.json', 'studio-host.log')


def relocate_library(target, mode='copy'):
    """Move or copy the library to ``target``, then point this profile at it.

    ``mode='copy'`` leaves the old files where they are (nothing is destroyed);
    ``mode='move'`` deletes the copies it made after the pointer is written, and
    the pointer file itself never moves with them.
    """
    import shutil

    from . import settings as st

    if mode not in ('copy', 'move'):
        raise ValueError("mode must be 'copy' or 'move'")
    base = default_data_dir(PLUGIN_ID)
    source = plugin_data_dir(PLUGIN_ID)
    raw = str(target or '').strip()
    if not raw:
        raise ValueError('a target directory is required')
    destination = Path(raw).expanduser().resolve()
    if destination == source.resolve():
        raise ValueError('that is already the library directory')
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in source.iterdir():
        if item.name in _LIBRARY_SKIP or item.name.startswith('.'):
            continue
        if item.name == 'library.json':
            continue
        dest = destination / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            elif not dest.exists():
                shutil.copy2(item, dest)
            copied += 1
        except OSError as exc:
            raise ValueError(f'could not copy {item.name}: {exc}') from None
    st.point_library_at(base, destination)
    if mode == 'move':
        # Drop the copies that were just made. The pointer, the settings file and
        # dotfiles live in the plugin's own directory and are never moved.
        for item in source.iterdir():
            if item.name in _LIBRARY_SKIP or item.name.startswith('.'):
                continue
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except OSError:
                pass
    payload = _library_payload()
    return {'success': True, 'copied': copied, 'mode': mode, 'library': payload}


def reset_library():
    """Point the profile back at the plugin's own data directory."""
    from . import settings as st

    base = default_data_dir(PLUGIN_ID)
    st.clear_library_pointer(base)
    return {'success': True, 'library': _library_payload()}


def duplicate_project(project_id, title=None):
    """Copy a project (and its audio) under a new id; the copy needs approval."""
    s = store()
    with s.locked():
        source = s.get(project_id)
        clone = copy.deepcopy(source)
        clone['project_id'] = str(uuid.uuid4())
        base_title = (title or source.get('title') or 'Untitled').strip() or 'Untitled'
        clone['title'] = base_title + ' (copy)'
        clone['approved'] = False
        clone['takes'] = []
        for take in source.get('takes') or []:
            made = copy.deepcopy(take)
            old_id = made.get('take_id')
            new_id = str(uuid.uuid4())
            made['take_id'] = new_id
            made['audio_file'] = _clone_audio(s.root, take.get('audio_file'), new_id)
            edits = []
            for edit in made.get('edits') or []:
                if not isinstance(edit, dict):
                    edits.append(edit)
                    continue
                copied = copy.deepcopy(edit)
                copied['audio_file'] = _clone_audio(s.root, edit.get('audio_file'), str(uuid.uuid4()))
                edits.append(copied)
            made['edits'] = edits
            clone['takes'].append(made)
        s._save(clone)
    return {'success': True, 'project': clone, 'source_project_id': project_id}


def _clone_audio(root, name, new_id):
    """Copy one take's WAV (and peaks) under ``new_id``; None when absent."""
    import shutil

    from .store import valid_id

    if not isinstance(name, str) or not name.endswith('.wav'):
        return None
    try:
        if valid_id(name[:-len('.wav')]) + '.wav' != name:
            return None
    except ValueError:
        return None
    source = root / name
    if not source.is_file():
        return None
    target = root / (new_id + '.wav')
    shutil.copy2(source, target)
    peaks = root / (name[:-len('.wav')] + '.peaks.json')
    if peaks.is_file():
        try:
            shutil.copy2(peaks, root / (new_id + '.peaks.json'))
        except OSError:
            pass
    return target.name


def delete_take(project_id, take_id):
    """Delete one take and its audio, leaving the project and other takes intact."""
    s = store()
    with s.locked():
        project = s.get(project_id)
        takes = project.get('takes') or []
        index = next((i for i, t in enumerate(takes) if t.get('take_id') == take_id), None)
        if index is None:
            raise KeyError('take not found')
        take = takes[index]
        if take.get('state') in ('queued', 'running'):
            raise ValueError('GPU busy: take is rendering')
        names = [take.get('audio_file')]
        names += [edit.get('audio_file') for edit in take.get('edits') or [] if isinstance(edit, dict)]
        for name in names:
            if isinstance(name, str) and name.endswith('.wav'):
                try:
                    from .store import valid_id
                    if valid_id(name[:-len('.wav')]) + '.wav' == name:
                        s._remove_audio_file(name)
                except ValueError:
                    pass
        project['takes'] = takes[:index] + takes[index + 1:]
        s._save(project)
    return {'success': True, 'project': project, 'deleted_take_id': take_id}


# -- revisions: versions, section replacement, endings ---------------------
def versions(project_id, count=3, seed_base=None):
    """Queue several takes of the same approved draft, each a different seed.

    Suno's "generate and compare" loop, minus the part where the model drifts:
    every take here is the same approved draft, only the seed differs, and the
    seed is stored so a winner can be reproduced exactly.
    """
    if type(count) is not int or not 1 <= count <= 8:
        raise ValueError('count must be between 1 and 8')
    return batch(project_id, count, seed_base)


def render_section(project_id, take_id, start_s, end_s, caption=None):
    """Queue one section of a succeeded take as its own auditionable take.

    Step one of "replace this part": the section is rendered on its own, so the
    user hears the candidate by itself before anything is merged.
    """
    s = store()
    jobs = Jobs(s)
    take = None
    seed = random.SystemRandom().randrange(0, 2**32)
    try:
        project = s.get(project_id)
        parent = next((t for t in project.get('takes') or [] if t.get('take_id') == take_id), None)
        if parent is None:
            raise KeyError('take not found')
        if parent.get('state') != 'succeeded':
            raise ValueError('only a succeeded take can be revised')
        engine = parent.get('engine_id') or engine_id_for(project)
        take = jobs.enqueue_section(project_id, take_id, start_s, end_s, seed,
                                    caption=caption, engine_id=engine)
        return {'success': True, 'take': _launch(s, project_id, take)}
    except BaseException:
        if take is not None:
            try:
                jobs.abort(project_id, take['take_id'], 'worker could not start')
            except Exception:
                logging.exception('Unable to abort unstartable section render')
        raise


def splice_section(project_id, take_id, candidate_take_id, fade_ms=40):
    """Merge an auditioned section render into its parent as a NEW take.

    The parent's bytes are untouched: the merge is a byte-level copy of the
    parent with the section swapped, so the rest of the song cannot drift — the
    exact failure that makes Suno's Replace Section unreliable.
    """
    from . import edit

    s = store()
    jobs = Jobs(s)
    with s.locked():
        project = s.get(project_id)
        parent = next((t for t in project.get('takes') or [] if t.get('take_id') == take_id), None)
        cand = next((t for t in project.get('takes') or [] if t.get('take_id') == candidate_take_id), None)
        if parent is None or cand is None:
            raise KeyError('take not found')
        if parent.get('state') != 'succeeded' or not parent.get('audio_file'):
            raise ValueError('the parent take has no audio')
        if cand.get('state') != 'succeeded' or not cand.get('audio_file'):
            raise ValueError('the replacement has not finished rendering')
        # A replacement is any take that says which range it regenerated: a section render tags
        # `section_of`, a repaint tags `range_start_s`/`range_end_s` instead. Accepting only the first
        # shape made a repaint usable in the player but un-spliceable — the one step that turns it
        # bit-exact.
        span = dict(cand.get('section_of') or {})
        if not span and cand.get('range_start_s') is not None and cand.get('parent_take_id') == take_id:
            span = {'take_id': take_id, 'start_s': cand.get('range_start_s'),
                    'end_s': cand.get('range_end_s')}
        if span.get('take_id') != take_id:
            raise ValueError('that take is not a replacement for this section')
        parent_bytes = (s.root / parent['audio_file']).read_bytes()
        cand_bytes = (s.root / cand['audio_file']).read_bytes()
        merged = edit.splice(parent_bytes, cand_bytes, span['start_s'], span['end_s'], fade_ms)
        take = jobs._new_take(project, seed=int(parent.get('seed') or 0),
                              requested_seed=int(parent.get('requested_seed') or 0),
                              seed_locked=False, kind='splice', parent=parent,
                              extra={'section_of': dict(span),
                                     'candidate_take_id': candidate_take_id,
                                     'range_start_s': span['start_s'],
                                     'range_end_s': span['end_s']})
        project['takes'].append(take)
        s._save(project)
    jobs.start(project_id, take['take_id'], os.getpid())
    metadata = {'engine_id': 'splice', 'engine_label': 'Section splice (local edit)',
                'parent_take_id': take_id,
                'replaced_section': {'start_s': span['start_s'], 'end_s': span['end_s']},
                'candidate_take_id': candidate_take_id,
                'measurements': edit.measure(merged)}
    jobs.succeed(project_id, take['take_id'], merged, metadata=metadata)
    return {'success': True, 'take': next(t for t in s.get(project_id)['takes']
                                          if t['take_id'] == take['take_id'])}


def fade_take(project_id, take_id, seconds=1.5):
    """Fade a take's ending out, as a new take. Fixes the abrupt-cut ending."""
    from . import edit

    if not isinstance(seconds, (int, float)) or not 0.05 <= float(seconds) <= 30:
        raise ValueError('fade must be between 0.05 and 30 seconds')
    s = store()
    jobs = Jobs(s)
    with s.locked():
        project = s.get(project_id)
        parent = next((t for t in project.get('takes') or [] if t.get('take_id') == take_id), None)
        if parent is None:
            raise KeyError('take not found')
        if parent.get('state') != 'succeeded' or not parent.get('audio_file'):
            raise ValueError('the take has no audio')
        audio = (s.root / parent['audio_file']).read_bytes()
        faded = edit.fade_out(audio, float(seconds))
        take = jobs._new_take(project, seed=int(parent.get('seed') or 0),
                              requested_seed=int(parent.get('requested_seed') or 0),
                              seed_locked=False, kind='edit', parent=parent,
                              extra={'fade_out_seconds': float(seconds)})
        project['takes'].append(take)
        s._save(project)
    jobs.start(project_id, take['take_id'], os.getpid())
    metadata = {'engine_id': 'edit', 'engine_label': 'Local edit (fade out)',
                'parent_take_id': take_id, 'fade_out_seconds': float(seconds),
                'measurements': edit.measure(faded)}
    jobs.succeed(project_id, take['take_id'], faded, metadata=metadata)
    return {'success': True, 'take': next(t for t in s.get(project_id)['takes']
                                          if t['take_id'] == take['take_id'])}
