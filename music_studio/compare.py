"""One set of instructions, two engines, two takes — the A/B the studio kept making people do by hand.

Why it lives here and not in the UI: the interesting part is the ordering. This machine can only run
one render at a time (the worker lease is exclusive for ACE, and the ComfyUI queue is one queue), so
"both versions" is a queue of one engine after another, with the results recorded as ordinary takes
of the same project. Nothing new is stored: a take already pins its own engine.

The plan file is what makes it survivable: a comparison that outlives the page, the chat or a
gateway restart can be read back and resumed by name instead of restarted from scratch.
"""
import json
import random
import threading
import time
from pathlib import Path

from . import service

COMPARE_FILE = 'compare-{project}.json'
POLL_SECONDS = 2.0
DEFAULT_ENGINES = ('ace_local', 'minimax_m3_comfy')


def _path(data_dir, project_id):
    return Path(data_dir) / COMPARE_FILE.format(project=project_id)


def read(data_dir, project_id):
    path = _path(data_dir, project_id)
    if not path.is_file():
        return {'state': 'idle', 'project_id': project_id, 'engines': []}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {'state': 'unreadable', 'project_id': project_id, 'engines': []}


def _write(data_dir, project_id, payload):
    _path(data_dir, project_id).write_text(json.dumps(payload, indent=2))
    return payload


def available_engines(reg=None):
    """Every engine registered here, with the one fact a chooser needs: can it render now?

    Deliberately not a hardcoded pair: a person who cannot run MiniMax still compares the local
    engine against whatever they do have — a second ComfyUI, an ACE server on another machine — and
    the list has to come from the registry, not from what happens to be installed on this box.
    """
    reg = reg or service.registry()
    out = []
    for engine in reg:
        try:
            status = reg.health(engine)
        except Exception as exc:  # a broken engine must not break the list
            out.append({'engine_id': getattr(engine, 'id', '?'), 'label': getattr(engine, 'label', '?'),
                        'ready': False, 'state': 'error', 'detail': str(exc)[:200]})
            continue
        caps = getattr(engine, 'capabilities', None)
        renders = bool(getattr(caps, 'generate', True)) if caps is not None else True
        out.append({
            'engine_id': engine.id,
            'label': getattr(engine, 'label', engine.id),
            'kind': getattr(engine, 'kind', ''),
            'ready': status.state == 'ready' and renders,
            'state': status.state,
            'detail': (status.detail or '')[:200],
        })
    return out


def usable_engines(reg=None):
    """The ids that can render a full song here, in registry order."""
    return [e['engine_id'] for e in available_engines(reg) if e['ready']]


def start(project_id, engines=None, seed=None, data_dir=None):
    """Queue one take per engine, sequentially. Returns the plan, not the audio."""
    store = service.store()
    project = store.get(project_id)
    if not project.get('approved'):
        raise ValueError('draft requires approval — approve the draft once, then compare')
    chosen = [e for e in (engines or usable_engines()) if e]
    if len(chosen) < 2:
        raise ValueError('a comparison needs two engines that are ready here; '
                         f'found {len(chosen)}')
    plan = {
        'state': 'running', 'project_id': project_id, 'started': time.time(),
        'seed': seed, 'engines': [{'engine_id': e, 'take_id': None, 'state': 'queued'} for e in chosen],
    }
    data_dir = data_dir or service.plugin_data_dir(service.PLUGIN_ID)
    _write(data_dir, project_id, plan)

    def work():
        current = read(data_dir, project_id)
        for row in current['engines']:
            if current.get('cancelled'):
                row.update({'state': 'cancelled'})
                continue
            try:
                # A take needs an explicit seed, and the seed is the replay handle: draw one per
                # engine and record it on the row so a winner can be reproduced exactly.
                take_seed = seed if isinstance(seed, int) else random.randrange(1, 2 ** 31 - 1)
                row['seed'] = take_seed
                result = service.generate(project_id, take_seed, engine_id=row['engine_id'])
                take_id = (result.get('take') or {}).get('take_id')
                row.update({'take_id': take_id, 'state': 'running'})
                _write(data_dir, project_id, current)
                deadline = time.time() + 3600
                while time.time() < deadline:
                    take = _take(store.get(project_id), take_id)
                    if take and take.get('state') in ('succeeded', 'failed', 'cancelled'):
                        row.update({'state': take['state'],
                                    'error': take.get('error')})
                        break
                    time.sleep(POLL_SECONDS)
                else:
                    row.update({'state': 'timeout'})
            except Exception as exc:  # a failed engine must not stop the other one
                row.update({'state': 'failed', 'error': str(exc)})
            _write(data_dir, project_id, current)
        current['state'] = 'done'
        current['finished'] = time.time()
        _write(data_dir, project_id, current)

    threading.Thread(target=work, name=f'ms-compare-{project_id[:8]}', daemon=True).start()
    return plan


def _take(project, take_id):
    for take in project.get('takes') or []:
        if take.get('take_id') == take_id:
            return take
    return None


def status(project_id, data_dir=None):
    """The plan plus the real take state, so a caller never reads a stale 'running'."""
    data_dir = data_dir or service.plugin_data_dir(service.PLUGIN_ID)
    plan = read(data_dir, project_id)
    try:
        project = service.store().get(project_id)
    except Exception:
        return plan
    for row in plan.get('engines', []):
        take = _take(project, row.get('take_id')) if row.get('take_id') else None
        if take:
            row['state'] = take.get('state')
            row['error'] = take.get('error')
            qc = take.get('qc') or {}
            row['seconds'] = qc.get('file_s')
            row['peak_dbfs'] = qc.get('peak_dbfs')
    if plan.get('state') == 'running' and plan.get('engines') and \
            all(r.get('state') in ('succeeded', 'failed', 'cancelled', 'timeout')
                for r in plan['engines']):
        plan['state'] = 'done'
        _write(data_dir, project_id, plan)
    return plan
