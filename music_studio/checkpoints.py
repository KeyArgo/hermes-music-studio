"""Save your spot, at any moment, without losing it.

A checkpoint is a **copy** of everything needed to resume days later — not a pointer into the journal,
which keeps only the last 200 events:

- the whole draft (caption, lyrics, bpm, key, sections, engine, duration, instrumental, language)
- the kept take, with the **sha256 of its WAV** and, where it is honest, its seed
- the take lineage (id, engine, seed, parent) so a history can be read back
- what was queued or running, so a resume can re-queue or mark it interrupted
- the pending question, the playhead and the loop range

**Fidelity differs by engine, and that difference is the trap.** ACE replays exactly from a seed, so a
checkpoint can offer "rebuild from seed". MiniMax's seed replay was never measured — so for an M3 take the
checkpoint keeps the *audio file itself* and says so, instead of offering a rebuild that might not
reproduce the song.
"""

from __future__ import annotations

import hashlib
import time
import uuid

DRAFT_FIELDS = ('title', 'idea', 'caption', 'lyrics', 'duration_seconds', 'instrumental',
                'vocal_language', 'bpm', 'keyscale', 'engine', 'sections', 'minimax_guidance',
                'comfy_target_id')

SEED_REPLAY_ENGINES = ('ace', 'ace_local', 'ace_remote', 'ace_local_cpu')


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _kept_take(project):
    kept = (project.get('memory') or {}).get('kept_take_id')
    takes = project.get('takes') or []
    if kept:
        for take in takes:
            if take.get('take_id') == kept:
                return take
    finished = [t for t in takes if t.get('state') == 'succeeded']
    return finished[-1] if finished else None


def build(store, project_id, *, label=None, pending=None, playhead_s=None, loop=None):
    """Assemble the checkpoint; the caller decides when to write it."""
    project = store.get(project_id)
    take = _kept_take(project)
    engine = (take or {}).get('engine_id') or project.get('engine') or ''
    audio = None
    if take and take.get('audio_file'):
        path = store.root / take['audio_file']
        if path.is_file():
            audio = {'file': take['audio_file'], 'sha256': _sha256(path),
                     'bytes': path.stat().st_size}

    # A question this worries about: "can this be rebuilt from the seed, or must the file be kept?".
    fidelity = 'seed' if str(engine).split('_')[0] in ('ace',) or engine in SEED_REPLAY_ENGINES else 'audio'

    return {
        'checkpoint_id': str(uuid.uuid4()),
        'at': time.time(),
        'label': (label or '').strip()[:80] or None,
        'draft': {field: project.get(field) for field in DRAFT_FIELDS},
        'draft_updated_at': project.get('updated_at'),
        'approved': bool(project.get('approved')),
        'kept_take': ({
            'take_id': take.get('take_id'), 'engine_id': take.get('engine_id'),
            'seed': take.get('requested_seed') or take.get('seed'),
            'parent_take_id': take.get('parent_take_id'),
            # M3's seed replay was never measured: keep the file, offer no rebuild.
            'fidelity': fidelity, 'audio': audio,
        } if take else None),
        'takes': [{'take_id': t.get('take_id'), 'engine_id': t.get('engine_id'),
                   'state': t.get('state'), 'seed': t.get('requested_seed') or t.get('seed'),
                   'parent_take_id': t.get('parent_take_id'), 'kind': t.get('kind')}
                  for t in (project.get('takes') or [])],
        'jobs': [{'take_id': t.get('take_id'), 'state': t.get('state')}
                 for t in (project.get('takes') or []) if t.get('state') in ('queued', 'running')],
        'pending_question': pending,
        'playhead_s': playhead_s,
        'loop': loop,
        'journal_tip': len((project.get('memory') or {}).get('events') or []),
    }


def save(store, project_id, **kwargs):
    checkpoint = build(store, project_id, **kwargs)
    result = store.add_checkpoint(project_id, checkpoint)
    return {'success': True, 'checkpoint': checkpoint,
            'saved': len(result['checkpoints']), 'pruned': result['pruned'],
            'fidelity': (checkpoint.get('kept_take') or {}).get('fidelity'),
            'note': ('saved to the song itself; the audio it names is kept in the library' )}


def listing(store, project_id):
    rows = store.checkpoints(project_id)
    return {'success': True, 'project_id': project_id,
            'checkpoints': [{'checkpoint_id': c.get('checkpoint_id'), 'at': c.get('at'),
                             'label': c.get('label'), 'approved': c.get('approved'),
                             'kept_take_id': (c.get('kept_take') or {}).get('take_id'),
                             'fidelity': (c.get('kept_take') or {}).get('fidelity')}
                            for c in rows]}


def restore(store, project_id, checkpoint_id):
    """Put the draft and the keeper back as they were. Audio is never rewritten."""
    target = next((c for c in store.checkpoints(project_id)
                   if c.get('checkpoint_id') == checkpoint_id), None)
    if target is None:
        raise KeyError('checkpoint not found')
    draft = {k: v for k, v in (target.get('draft') or {}).items() if v is not None}
    # update() revokes the approval by design (it is a different draft); say so instead of hiding it.
    project = store.update(project_id, **draft)
    kept = (target.get('kept_take') or {}).get('take_id')
    if kept:
        store.remember(project_id, kind='restore', text=f'went back to {target.get("label") or "a saved spot"}',
                       take_id=kept, bump=False)
    return {'success': True, 'project': project, 'restored': target.get('label') or checkpoint_id,
            'kept_take_id': kept,
            'note': 'the draft is back and the approval is revoked — review it, then approve again'}
