"""Did the take do what the person asked? Evidence, not a vibe.

Three of the panel's reviewers asked for this and only one would have built it the same way: Argon
said "verify after render, retry with a new seed"; Codex wanted CLAP; Grok said measurements only.
All three agree on the shape kept here:

- **checks that are facts** run in this module: did the words the person asked for reach the engine's
  caption, is the tempo the one they set, is the length the one they asked for, is the file clipped,
  does it end in silence, and does the vocal evidence contradict "instrumental".
- **checks that need a model are `unknown`, never guessed.** No ASR and no CLAP is installed on this
  machine, so `vocal_presence` and anything semantic stays `unknown` and says why. A fabricated
  "looks good" is worse than an honest "not measured".
- **a retry fires only on a fact that failed** — a stale caption, a wrong tempo, a clipped file — and
  it draws a fresh seed, so the next take is a different sample rather than the same one re-run.
"""

from __future__ import annotations

import random

MAX_RETRIES = 2
DURATION_TOLERANCE_S = 2.0
DURATION_TOLERANCE_FRACTION = 0.05
TAIL_SILENCE_FRACTION = 0.10
PEAK_LIMIT_DBFS = -0.1


def _journal_terms(project):
    """The words a change added to the style, from the song's own journal."""
    terms, tempos = [], []
    for event in (project.get('memory') or {}).get('events') or []:
        patch = event.get('patch') or {}
        caption = patch.get('caption')
        if isinstance(caption, str) and caption.strip():
            terms.extend(part.strip().lower() for part in caption.split(',') if part.strip())
        if isinstance(patch.get('bpm'), int):
            tempos.append(patch['bpm'])
    return terms, (tempos[-1] if tempos else None)


def checks(project, take):
    """Everything that can be checked without a model, each row carrying its evidence."""
    snapshot = take.get('snapshot') or {}
    qc = take.get('qc') or {}
    lowered = str(snapshot.get('caption') or '').lower()
    terms, tempo = _journal_terms(project)
    rows = []

    if terms:
        absent = [term for term in terms if term not in lowered]
        rows.append({'name': 'the words reached the engine', 'ok': not absent,
                     'detail': (f'caption carries {len(terms) - len(absent)}/{len(terms)}' +
                                (f'; missing: {", ".join(absent[:4])}' if absent else ''))})

    if tempo is not None:
        got = snapshot.get('bpm')
        rows.append({'name': 'tempo is the one that was set', 'ok': got == tempo,
                     'detail': f'asked {tempo} bpm, rendered {got if got is not None else "engine default"}'})

    requested_s = qc.get('requested_s') or snapshot.get('duration_seconds')
    file_s = qc.get('file_s')
    if requested_s and file_s:
        tolerance = max(DURATION_TOLERANCE_S, float(requested_s) * DURATION_TOLERANCE_FRACTION)
        rows.append({'name': 'length is what was asked for',
                     'ok': abs(float(file_s) - float(requested_s)) <= tolerance,
                     'detail': f'asked {float(requested_s):.1f}s, file is {float(file_s):.1f}s'})

    peak, clipped = qc.get('peak_dbfs'), qc.get('clipped_frames')
    if peak is not None or clipped is not None:
        ok = (peak is None or float(peak) <= PEAK_LIMIT_DBFS) and not int(clipped or 0)
        rows.append({'name': 'no clipping', 'ok': ok,
                     'detail': f'peak {peak} dBFS, {int(clipped or 0)} clipped frames'})

    tail = qc.get('tail_silence_s')
    if tail is not None and file_s:
        share = float(tail) / float(file_s) if float(file_s) else 0
        rows.append({'name': 'does not end in a long silence', 'ok': share <= TAIL_SILENCE_FRACTION,
                     'detail': f'{float(tail):.1f}s of silence at the end ({share:.0%})'})

    if snapshot.get('instrumental'):
        presence = qc.get('vocal_presence')
        rows.append({'name': 'instrumental stayed instrumental',
                     'ok': None if presence in (None, 'unknown') else presence != 'present',
                     'detail': (qc.get('vocal_evidence') or {}).get('detail')
                     or 'no vocal evidence: no ASR pass has run on this take'})

    return rows


def summary(rows):
    decided = [row for row in rows if row['ok'] is not None]
    failed = [row for row in decided if not row['ok']]
    if failed:
        return 'failed', f'{len(failed)} check(s) failed: ' + '; '.join(row['name'] for row in failed)
    if not decided:
        return 'unknown', 'nothing could be measured without a model — the checks say why'
    return 'passed', f'all {len(decided)} measurable check(s) passed'


def _take(project, take_id):
    take = next((t for t in project.get('takes') or [] if t.get('take_id') == take_id), None)
    if take is None:
        raise KeyError('take not found')
    return take


def report(project_id, take_id, store):
    project = store.get(project_id)
    take = _take(project, take_id)
    if take.get('state') != 'succeeded':
        raise ValueError(f'that take is {take.get("state")}, so there is nothing to check yet')
    rows = checks(project, take)
    state, sentence = summary(rows)
    return {'success': True, 'project_id': project_id, 'take_id': take_id, 'state': state,
            'summary': sentence, 'checks': rows}


def retry(project_id, take_id, store, generate):
    """Re-render only when a fact failed, with a fresh seed.

    A check that could not be measured (`None`) never causes a retry: spending GPU minutes on a
    question nobody answered is the behaviour this module exists to avoid.
    """
    project = store.get(project_id)
    take = _take(project, take_id)
    rows = checks(project, take)
    failed = [row for row in rows if row['ok'] is False]
    if not failed:
        return {'success': True, 'retried': False, 'reason': 'nothing measurable failed',
                'checks': rows}
    seed = random.randrange(1, 2 ** 31 - 1)
    queued = generate(project_id, seed)
    return {'success': True, 'retried': True, 'seed': seed,
            'because': [row['name'] for row in failed],
            'take': (queued.get('take') or {}).get('take_id'),
            'note': (f'a new take is rendering with seed {seed}; the failed take stays in the '
                     'library — it is the evidence')}
