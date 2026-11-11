"""Hear the chorus before spending three minutes on the whole song.

The flow this serves is the one everybody actually wants: type an idea, get a draft, hear the part
that decides whether it is worth building, and only then render the full thing — by clicking buttons.

A preview is an ordinary project with ordinary takes, not a special mode: it copies the draft's
caption, tempo, key and engine, keeps only the chorus, and renders a short take. If the person then
builds the full song, both exist and the preview stays as the reference they approved.
"""
import random
import re

from . import service

TAG = re.compile(r'^\s*\[([^\]]+)\]\s*$')
DEFAULT_PREVIEW_SECONDS = 25


def chorus_block(lyrics):
    """The lines under the first [chorus] tag, or None when the draft has no chorus.

    Returns the tag as well: the engine reads section tags, so dropping the bracket would change how
    the preview is sung relative to the finished song.
    """
    if not isinstance(lyrics, str) or not lyrics.strip():
        return None
    lines = lyrics.splitlines()
    collecting = False
    block = []
    for line in lines:
        match = TAG.match(line)
        if match:
            if collecting:
                break
            collecting = 'chorus' in match.group(1).lower()
            if collecting:
                block.append(line.strip())
            continue
        if collecting and line.strip():
            block.append(line.rstrip())
    return '\n'.join(block).strip() or None


def preview(project_id, seconds=DEFAULT_PREVIEW_SECONDS):
    """Create a short chorus-only take of this draft and queue it. Returns the ids, not audio."""
    store = service.store()
    project = store.get(project_id)
    block = chorus_block(project.get('lyrics'))
    if not block:
        raise ValueError('this draft has no [chorus] section — write one, or build the full song')
    seconds = int(seconds)
    if not 10 <= seconds <= 60:
        raise ValueError('a chorus preview is 10-60 seconds; longer is the full song')

    draft = {
        'title': f"{project.get('title') or 'Untitled'} — chorus",
        'idea': project.get('idea') or '',
        'caption': project.get('caption') or '',
        'lyrics': block,
        'engine': project.get('engine'),
        'duration_seconds': seconds,
        'instrumental': False,
        'vocal_language': project.get('vocal_language') or 'en',
        'bpm': project.get('bpm'),
        'keyscale': project.get('keyscale'),
    }
    created = store.create(**draft)
    # The click on "Hear the chorus" is the approval: this draft is four lines long, was assembled
    # from the person's own text, and is shown to them before the button is pressed.
    store.approve(created['project_id'], base_updated_at=created['updated_at'])
    seed = random.randrange(1, 2 ** 31 - 1)
    result = service.generate(created['project_id'], seed)
    return {
        'preview_project_id': created['project_id'],
        'source_project_id': project_id,
        'seconds': seconds,
        'lyrics': block,
        'seed': seed,
        'take': (result.get('take') or {}),
    }
