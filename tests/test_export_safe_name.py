"""An exported file must be a name Windows will actually create.

``CON``, ``NUL``, ``COM1`` and friends are reserved device names in every
directory, with or without an extension: writing ``con.wav`` targets the
console, not a file. The exported name is also capped so a very long title
cannot exceed the platform's path/name limits.
"""
from __future__ import annotations

import pytest

from music_studio import export


@pytest.mark.parametrize('raw, expected', [
    ('CON', 'CON_'),
    ('con', 'con_'),
    ('Con.wav', 'Con_.wav'),
    ('nul.txt', 'nul_.txt'),
    ('COM1', 'COM1_'),
    ('lpt9.wav', 'lpt9_.wav'),
    ('console', 'console'),
])
def test_reserved_device_names_are_suffixed(raw, expected):
    assert export.safe_name(raw, fallback=raw) == expected


def test_reserved_device_names_with_an_extension_stay_unreserved():
    for raw in ('CON.wav', 'con.mp3', 'NUL.txt', 'AUX.flac', 'COM1.wav', 'LPT9.ogg'):
        stem = export.safe_name(raw).split('.')[0].upper()
        assert stem not in export._RESERVED_NAMES


def test_a_long_name_is_capped_at_120_characters():
    name = export.safe_name('a' * 300)
    assert len(name) == 120
    assert name == 'a' * 120


def test_the_cap_still_applies_when_a_reserved_name_is_suffixed():
    name = export.safe_name('CON' + ' ' + 'a' * 200)
    assert len(name) <= 120
    assert name.split('.')[0].upper() not in export._RESERVED_NAMES
