"""Plain words in, a draft that changed out — deterministically, so it can be tested and undone.

Both engines take **one caption string**, one BPM and one key; neither has an instrument slot or a
per-section tempo (verified in `ace_engine.py` and `minimax_m3_comfy.py`). So "add a fiddle" cannot be a
knob: it has to become words in that one caption, and "half-time in the bridge" has to become a phrase
carried by a section, not a second BPM the engine would ignore.

Everything here is a **request**, and the caller says so: `apply()` returns the patch plus the words it
used, and `REQUESTS_NOTE` is the sentence the UI shows next to the result. Nothing in this module claims
an instrument was removed from audio — only that the next render was asked to leave it out.
"""

from __future__ import annotations

import re

REQUESTS_NOTE = ('These are instructions for the next render, not a done deal: both engines take one '
                 'style caption, so the result is auditioned rather than promised.')

BPM_MIN, BPM_MAX = 40, 220

# Feel phrases: what a short mood word expands into in the caption. Each entry is (match words, phrase).
FEELS = (
    (('dark', 'doomy', 'sinister'), 'dark, minor key, low register, sparse percussion'),
    (('heavy', 'hard', 'brutal'), 'heavy, distorted, dense drums, low tuning'),
    (('soft', 'delicate', 'gentle', 'tender'), 'soft, delicate, intimate, brushed drums'),
    (('bright', 'happy', 'uplifting'), 'bright, major key, open chords, light percussion'),
    (('sad', 'melancholy', 'wistful'), 'melancholy, minor key, slow strings'),
    (('epic', 'cinematic', 'huge'), 'epic, cinematic, wide strings, big drums'),
    (('intimate', 'close'), 'intimate, close-mic vocal, roomy'),
    (('driving', 'pumping', 'energetic'), 'driving, four-on-the-floor, tight bass'),
    (('faster', 'fast', 'quicker'), 'faster feel'),
    (('slower', 'slow', 'slower feel'), 'slower feel'),
)

# What the engines honestly cannot do, said as the sentence the person gets back.
LIMITS = (
    (('mute the', 'isolate the', 'remove the drums', 'remove the bass'),
     'There is no instrument channel to mute: both engines render a mixed stereo '
                       'take. Name what should be heard instead ("lead: fiddle") and render again.'),
    (('remove the vocal', 'remove vocals', 'no vocals', 'instrumental only'),
     'Vocals cannot be subtracted from a finished take. Set Instrumental on the draft and render — '
     'that produces a new instrumental version.'),
    (('per-section tempo', 'different tempo in', 'tempo change in'),
     'Neither engine accepts a second tempo. "Half-time" is sent as a feel on that section; the '
     'numeric BPM stays the song\'s one BPM.'),
)

_SECTION = re.compile(r'\b(verse|chorus|bridge|intro|outro|pre-chorus)\b', re.I)


_FILLER = {'make', 'set', 'change', 'it', 'the', 'a', 'an', 'to', 'at', 'play', 'song',
           'tempo', 'speed', 'and', 'of', 'this', 'that', "song's", 'please', 'now'}


def _meaningful(text):
    """Everything left after the filler words — '' means the instruction was only a tempo."""
    return ' '.join(word for word in str(text).split() if word.strip(' ,.').lower() not in _FILLER)


def _dedupe(parts):
    seen, out = set(), []
    for part in parts:
        key = part.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(part.strip())
    return out


def apply(text, draft):
    """Turn one instruction into a draft patch plus the sentences that explain it.

    Returns ``{'patch': {...}, 'notes': [...], 'section': str|None}``. ``patch`` only ever contains
    fields the store accepts (caption, bpm, keyscale, instrumental) so it can go straight to `update`.
    """
    words = str(text or '').strip()
    if not words:
        raise ValueError('say what to change — for example "add a fiddle" or "go dark"')

    lowered = words.lower()
    caption = str(draft.get('caption') or '')
    parts = [part for part in (p.strip() for p in caption.split(',')) if part]
    notes = []
    patch = {}
    section = None

    match = _SECTION.search(words)
    if match:
        section = match.group(1).lower()

    for triggers, sentence in LIMITS:
        if any(trigger in lowered for trigger in triggers):
            raise ValueError(sentence)

    add = re.match(r'^(?:add|bring in|put in|include)\s+(?:a |an |some |more )?(.+)$', lowered)
    drop = re.match(r'^(?:remove|drop|take out|cut)\s+(?:the |a |an )?(.+)$', lowered)

    if add:
        term = add.group(1).strip(' .')
        parts = _dedupe(parts + [term])
        notes.append(f'asked for: {term}')
    elif drop:
        term = drop.group(1).strip(' .')
        kept = [part for part in parts if term not in part.lower()]
        if len(kept) == len(parts):
            notes.append(f'"{term}" was not in the caption, so nothing was taken out of it')
        else:
            parts = kept
            notes.append(f'took "{term}" out of the caption (the next render is asked to leave it out)')
    else:
        matched = False
        for triggers, phrase in FEELS:
            if any(word in lowered for word in triggers):
                parts = _dedupe(parts + [phrase])
                notes.append(f'feel: {phrase}')
                matched = True
                break
        if not matched and ('half-time' in lowered or 'halftime' in lowered):
            where = f' in the {section}' if section else ''
            parts = _dedupe(parts + [f'half-time groove{where}'])
            notes.append(f'half-time{where} sent as a feel, not as a second tempo')
            matched = True
        if not matched:
            # Unrecognised words are still usable: they go in as the person wrote them — minus a tempo
            # they already gave numerically, which would otherwise be pasted in as style prose too.
            leftover = re.sub(r'\b\d{2,3}\s*(?:bpm|beats)\b', '', words, flags=re.I)
            leftover = re.sub(r'(?:tempo|speed)\D{0,12}\d{2,3}', '', leftover, flags=re.I)
            leftover = re.sub(r'^(?:make|set|change)\s+(?:it|the song|the tempo)\s*(?:to\s*)?', '',
                              leftover.strip(), flags=re.I).strip(' ,.')
            if _meaningful(leftover):
                parts = _dedupe(parts + [leftover])
                notes.append('kept your words as written and added them to the style')

    patch['caption'] = ', '.join(parts)[:512]

    tempo = (re.search(r'(\d{2,3})\s*(?:bpm|beats)', lowered)
              or re.search(r'(?:tempo|speed)\D{0,12}(\d{2,3})', lowered))
    if tempo:
        wanted = int(tempo.group(1))
        if not BPM_MIN <= wanted <= BPM_MAX:
            raise ValueError(f'bpm must be {BPM_MIN}-{BPM_MAX}')
        patch['bpm'] = wanted
        notes.append(f'tempo {wanted} bpm')
    elif any(word in lowered for word in ('faster', 'quicker', 'speed up')):
        patch['bpm'] = min(BPM_MAX, int(draft.get('bpm') or 100) + 10)
        notes.append(f'tempo {patch["bpm"]} bpm')
    elif any(word in lowered for word in ('slower', 'slow down', 'slower feel')):
        patch['bpm'] = max(BPM_MIN, int(draft.get('bpm') or 100) - 10)
        notes.append(f'tempo {patch["bpm"]} bpm')

    if 'instrumental' in lowered and any(w in lowered for w in ('make', 'render', 'set', 'no vocal')):
        patch['instrumental'] = True
        notes.append('marked instrumental')

    if patch.get('caption') == (draft.get('caption') or '') and len(patch) == 1:
        notes.append('the caption did not change; the instruction only restated what is already there')

    return {'patch': patch, 'notes': notes, 'section': section}


def describe(patch):
    """A tiny before/after for the chat or the screen: one line per field that changed."""
    shortcuts = {'caption': 'style', 'bpm': 'tempo', 'keyscale': 'key', 'instrumental': 'instrumental'}
    return [f"{shortcuts.get(field, field)}: {value}" for field, value in patch.items()]


def change(store, project_id, instruction):
    """Apply one instruction to a stored draft — the single implementation both surfaces call.

    Lives here rather than in each route so the tool, the dashboard and the standalone page cannot
    drift apart (a route implemented twice is a route that disagrees with itself within a month).
    """
    project = store.get(project_id)
    result = apply(instruction, project)
    store.update(project_id, **result['patch'])
    store.remember(project_id, kind='change', text=instruction, patch=result['patch'])
    # Re-read after the journal write: the caller renders one object, and it should carry the note it
    # just made (a stale copy showed no `memory` and looked like nothing was remembered).
    updated = store.get(project_id)
    return {'success': True, 'project': updated, 'changed': describe(result['patch']),
            'notes': result['notes'], 'section': result['section'], 'note': REQUESTS_NOTE}
