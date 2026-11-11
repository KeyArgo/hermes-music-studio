"""Song sections: the editable structure a take is rendered from.

A section is ``{id, tag, label, start_s, end_s, lyric_lines}``. Sections are a
whole-song partition: contiguous, non-overlapping, starting at 0 and ending at
the draft's duration. The tag vocabulary is fixed so clients can style and
re-order sections without guessing.
"""
import re

SECTION_TAGS = ('intro', 'verse', 'prechorus', 'chorus', 'bridge', 'outro',
                'instrumental', 'custom')
MAX_SECTIONS = 24
MAX_SECTION_ID = 64
MAX_SECTION_LABEL = 80
MAX_LYRIC_LINES = 128
MAX_LYRIC_LINE_CHARS = 200
MIN_SECTION_SECONDS = 0.25
# Timing is stored to milliseconds; this absorbs the rounding in derivation and
# in a client that sends 10.0/20.0 splits.
TIME_TOLERANCE = 0.05

SECTION_FIELDS = {'id', 'tag', 'label', 'start_s', 'end_s', 'lyric_lines'}

# Bracket headers, e.g. "[Verse 1]", "[pre-chorus]", "[Chorus]".
_HEADER = re.compile(r'^\s*\[([^\]]{1,40})\]\s*$')
_TAG_ALIASES = {
    'intro': 'intro',
    'verse': 'verse',
    'prechorus': 'prechorus', 'pre-chorus': 'prechorus', 'pre chorus': 'prechorus',
    'chorus': 'chorus',
    'bridge': 'bridge',
    'outro': 'outro',
    'instrumental': 'instrumental', 'inst': 'instrumental',
    'custom': 'custom',
}


def _is_number(value):
    return type(value) in (int, float)


def _tag_of(header):
    """Map a bracket header to (tag, label); unknown headers are ``custom``."""
    text = header.strip()
    lowered = text.lower()
    # "verse 1" and "chorus (repeat)" share the tag of their first word.
    words = lowered.replace('(', ' ').replace(')', ' ').split()
    key = ''.join(ch for ch in words[0] if ch.isalpha()) if words else ''
    if lowered in _TAG_ALIASES:
        return _TAG_ALIASES[lowered], text
    if key in _TAG_ALIASES:
        return _TAG_ALIASES[key], text
    return 'custom', text


def validate_sections(sections, duration_seconds=None):
    """Raise ValueError unless ``sections`` is a valid partition.

    ``duration_seconds`` may be None when a partial draft edit cannot know the
    merged duration yet; ordering and internal contiguity are still checked,
    and full coverage is checked by the store once the draft is merged.
    """
    if not isinstance(sections, list):
        raise ValueError('sections must be a list')
    if len(sections) > MAX_SECTIONS:
        raise ValueError(f'at most {MAX_SECTIONS} sections are allowed')
    if not sections:
        return
    ids = set()
    expected_start = 0.0
    coverage_end = 0.0
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError('each section must be an object')
        if set(section) != SECTION_FIELDS:
            raise ValueError('a section has exactly id, tag, label, start_s, end_s, '
                             'lyric_lines')
        identifier = section['id']
        if not isinstance(identifier, str) or not identifier.strip() \
                or len(identifier) > MAX_SECTION_ID:
            raise ValueError(f'section id must be 1-{MAX_SECTION_ID} characters')
        if identifier in ids:
            raise ValueError('section ids must be unique')
        ids.add(identifier)
        if section['tag'] not in SECTION_TAGS:
            raise ValueError('section tag must be one of ' + ', '.join(SECTION_TAGS))
        if not isinstance(section['label'], str) or len(section['label']) > MAX_SECTION_LABEL:
            raise ValueError(f'section label must be at most {MAX_SECTION_LABEL} characters')
        for field in ('start_s', 'end_s'):
            if not _is_number(section[field]):
                raise ValueError('section times must be numbers')
        start, end = float(section['start_s']), float(section['end_s'])
        if start < 0 or end <= start:
            raise ValueError('section end_s must be greater than start_s')
        if end - start < MIN_SECTION_SECONDS:
            raise ValueError(f'sections must be at least {MIN_SECTION_SECONDS} s long')
        lines = section['lyric_lines']
        if not isinstance(lines, list) or len(lines) > MAX_LYRIC_LINES:
            raise ValueError(f'lyric_lines must be a list of at most {MAX_LYRIC_LINES} lines')
        for line in lines:
            if not isinstance(line, str) or len(line) > MAX_LYRIC_LINE_CHARS:
                raise ValueError(f'each lyric line must be at most {MAX_LYRIC_LINE_CHARS} characters')
        if abs(start - expected_start) > TIME_TOLERANCE:
            raise ValueError('sections must be contiguous, in order, and must not overlap')
        expected_start = end
        coverage_end = end
    if duration_seconds is not None:
        if not _is_number(duration_seconds):
            raise ValueError('duration must be a number when sections are present')
        if abs(coverage_end - float(duration_seconds)) > TIME_TOLERANCE:
            raise ValueError('sections must end at the draft duration')


def derive_sections(lyrics, duration_seconds):
    """Build a contiguous section list from ``[tag]`` lyric headers.

    Timing is not encoded in lyrics, so the song is partitioned evenly across
    its headers. Returns [] when the lyrics carry no headers: inventing a
    structure the writer never wrote would be worse than showing none.
    """
    if not isinstance(lyrics, str) or not _is_number(duration_seconds) \
            or duration_seconds <= 0:
        return []
    groups = []
    current = None
    for raw in lyrics.splitlines():
        match = _HEADER.match(raw)
        if match:
            tag, label = _tag_of(match.group(1))
            current = {'tag': tag, 'label': label, 'lyric_lines': []}
            groups.append(current)
            continue
        if current is not None:
            line = raw.strip()
            if line:
                current['lyric_lines'].append(line[:MAX_LYRIC_LINE_CHARS])
    if not groups:
        return []
    # A derived list must obey the same cap as a user-authored one: merge the
    # overflow into the last kept section rather than emitting an invalid list.
    merged = []
    for group in groups:
        if len(merged) < MAX_SECTIONS:
            merged.append(group)
        else:
            merged[-1]['lyric_lines'].extend(group['lyric_lines'])
    count = len(merged)
    duration = round(float(duration_seconds), 3)
    sections = []
    for index, group in enumerate(merged):
        start = round(duration * index / count, 3)
        end = duration if index == count - 1 else round(duration * (index + 1) / count, 3)
        sections.append({'id': f'sec-{index + 1}', 'tag': group['tag'],
                         'label': group['label'], 'start_s': start, 'end_s': end,
                         'lyric_lines': group['lyric_lines']})
    return sections
