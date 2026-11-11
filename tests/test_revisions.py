"""Revisions: several versions, a replaced section, and a clean ending.

The splice tests are the important ones: replace a range and prove the rest of
the file is byte-identical apart from the ramps at the seams.
"""
import struct
import uuid
import wave
from io import BytesIO
from pathlib import Path

import pytest

from music_studio import edit, service


def _tone(seconds: float, frequency: int = 440, rate: int = 8000, channels: int = 1,
          amplitude: int = 8000) -> bytes:
    import math

    frames = int(seconds * rate)
    samples = bytearray()
    for index in range(frames):
        value = int(amplitude * math.sin(2 * math.pi * frequency * index / rate))
        for _ in range(channels):
            samples += struct.pack('<h', value)
    out = BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(samples))
    return out.getvalue()


@pytest.fixture()
def library(tmp_path, monkeypatch):
    root = tmp_path / 'lib'
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(root))
    return root


def _project_with_take(library: Path, audio: bytes, duration: int = 12) -> dict:
    store = service.store()
    project = store.create(title='Song', caption='quiet', lyrics='line',
                           duration_seconds=duration)
    take_id = str(uuid.uuid4())
    (library / f'{take_id}.wav').write_bytes(audio)
    project['takes'] = [{'take_id': take_id, 'state': 'succeeded', 'requested_seed': 1,
                         'seed': 1, 'engine_id': 'ace_local', 'edits': [],
                         'audio_file': f'{take_id}.wav'}]
    store._save(project)
    return store.get(project['project_id'])


def test_splice_keeps_everything_outside_the_section(library):
    parent = _tone(12.0, frequency=440)
    candidate = _tone(1.0, frequency=880)
    merged = edit.splice(parent, candidate, 1.0, 2.0, fade_ms=10)

    parent_pcm, rate, channels = edit.read_pcm(parent)
    merged_pcm, rate2, channels2 = edit.read_pcm(merged)
    assert (rate2, channels2) == (rate, channels)
    assert len(merged_pcm) == len(parent_pcm), 'duration must not change'

    seam = int(0.02 * rate)
    # Before the section and after it, the bytes are untouched.
    assert merged_pcm[:int(1.0 * rate) - seam] == parent_pcm[:int(1.0 * rate) - seam]
    assert merged_pcm[int(2.0 * rate) + seam:] == parent_pcm[int(2.0 * rate) + seam:]
    # Inside, it is audibly different from the parent.
    assert merged_pcm[int(1.2 * rate):int(1.8 * rate)] != parent_pcm[int(1.2 * rate):int(1.8 * rate)]
    # And there is no click at the seam: no jump bigger than a quarter of full scale.
    boundary = int(1.0 * rate)
    jumps = [abs(merged_pcm[i + 1] - merged_pcm[i]) for i in range(boundary - 5, boundary + 5)]
    assert max(jumps) < 8192


def test_splice_refuses_a_short_replacement(library):
    with pytest.raises(ValueError, match='shorter'):
        edit.splice(_tone(12.0), _tone(0.5), 1.0, 2.0)


def test_fade_out_ends_at_silence(library):
    faded = edit.fade_out(_tone(2.0), seconds=0.5)
    samples, rate, _ = edit.read_pcm(faded)
    assert abs(samples[-1]) < 200
    assert abs(samples[50]) > 100  # the start is untouched


def test_splice_section_creates_a_new_take_and_leaves_the_parent(library):
    parent_audio = _tone(12.0)
    project = _project_with_take(library, parent_audio)
    parent = project['takes'][0]

    # A finished section render, tagged with the range it replaces.
    candidate_id = str(uuid.uuid4())
    (library / f'{candidate_id}.wav').write_bytes(_tone(1.0, frequency=880))
    store = service.store()
    fresh = store.get(project['project_id'])
    fresh['takes'].append({'take_id': candidate_id, 'state': 'succeeded', 'requested_seed': 2,
                           'seed': 2, 'engine_id': 'ace_local', 'edits': [],
                           'audio_file': f'{candidate_id}.wav',
                           'section_of': {'project_id': project['project_id'],
                                          'take_id': parent['take_id'],
                                          'start_s': 1.0, 'end_s': 2.0}})
    store._save(fresh)

    result = service.splice_section(project['project_id'], parent['take_id'], candidate_id)
    merged = result['take']

    assert merged['take_id'] not in (parent['take_id'], candidate_id)
    assert merged['state'] == 'succeeded'
    assert merged['parent_take_id'] == parent['take_id']
    assert merged['metadata']['engine_id'] == 'splice'
    assert (library / parent['audio_file']).read_bytes() == parent_audio, 'parent untouched'
    merged_audio = (library / merged['audio_file']).read_bytes()
    assert edit.measure(merged_audio)['duration_seconds'] == pytest.approx(12.0, abs=0.01)


def test_splice_refuses_a_take_from_another_section(library):
    project = _project_with_take(library, _tone(12.0))
    parent = project['takes'][0]
    other = str(uuid.uuid4())
    (library / f'{other}.wav').write_bytes(_tone(1.0))
    store = service.store()
    fresh = store.get(project['project_id'])
    fresh['takes'].append({'take_id': other, 'state': 'succeeded', 'audio_file': f'{other}.wav',
                           'section_of': {'take_id': 'someone-else', 'start_s': 0, 'end_s': 1}})
    store._save(fresh)
    with pytest.raises(ValueError, match='not a replacement'):
        service.splice_section(project['project_id'], parent['take_id'], other)


def test_fade_take_creates_a_new_take(library):
    project = _project_with_take(library, _tone(12.0))
    parent = project['takes'][0]
    result = service.fade_take(project['project_id'], parent['take_id'], 0.5)
    faded = result['take']
    assert faded['take_id'] != parent['take_id']
    assert faded['metadata']['engine_id'] == 'edit'
    samples, _, _ = edit.read_pcm((library / faded['audio_file']).read_bytes())
    assert abs(samples[-1]) < 200


def test_versions_validates_the_count(library):
    project = _project_with_take(library, _tone(12.0))
    with pytest.raises(ValueError, match='count'):
        service.versions(project['project_id'], 0)
    with pytest.raises(ValueError, match='count'):
        service.versions(project['project_id'], 9)


def test_a_repaint_child_splices_and_the_rest_of_the_song_is_byte_identical(library):
    """A repaint tags `range_start_s`/`range_end_s`, not `section_of`.

    Accepting only the section shape made a repaint playable but un-spliceable, so "change 8 seconds"
    could never give back a take whose remaining audio is the parent's own bytes.
    """
    parent_audio = _tone(12.0)
    project = _project_with_take(library, parent_audio)
    parent = project['takes'][0]

    child_id = str(uuid.uuid4())
    (library / f'{child_id}.wav').write_bytes(_tone(12.0, frequency=880))
    store = service.store()
    fresh = store.get(project['project_id'])
    fresh['takes'].append({'take_id': child_id, 'state': 'succeeded', 'requested_seed': 5,
                           'seed': 5, 'engine_id': 'ace_local', 'edits': [],
                           'audio_file': f'{child_id}.wav',
                           'parent_take_id': parent['take_id'],
                           'range_start_s': 3.0, 'range_end_s': 11.0})
    store._save(fresh)

    merged = service.splice_section(project['project_id'], parent['take_id'], child_id)['take']

    parent_pcm, rate, _ = edit.read_pcm(parent_audio)
    merged_pcm, _, _ = edit.read_pcm((library / merged['audio_file']).read_bytes())
    seam = int(0.02 * rate)
    assert merged_pcm[:int(3.0 * rate) - seam] == parent_pcm[:int(3.0 * rate) - seam]
    assert merged_pcm[int(11.0 * rate) + seam:] == parent_pcm[int(11.0 * rate) + seam:]
    assert merged_pcm[int(4.0 * rate):int(10.0 * rate)] != parent_pcm[int(4.0 * rate):int(10.0 * rate)]
