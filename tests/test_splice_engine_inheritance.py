"""A splice is rendered on the engine that rendered the take it replaces.

Found live: a spliced take came back with ``engine_id: null`` because the splice was built without
one; only its metadata named the local edit. A client that offers repaint/extend per engine then had
no engine to ask about, so the controls could not be gated.
"""
import struct
import uuid
import wave
from io import BytesIO
from pathlib import Path

import pytest

from music_studio import service


def _tone(seconds: float, frequency: int = 440, rate: int = 8000) -> bytes:
    import math

    frames = int(seconds * rate)
    samples = bytearray()
    for index in range(frames):
        samples += struct.pack('<h', int(8000 * math.sin(2 * math.pi * frequency * index / rate)))
    out = BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1)
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


def test_a_splice_inherits_the_parent_takes_engine(library: Path):
    store = service.store()
    project = store.create(title='Song', caption='quiet', lyrics='line', duration_seconds=12)
    parent_id = str(uuid.uuid4())
    (library / f'{parent_id}.wav').write_bytes(_tone(12.0))
    project['takes'] = [{'take_id': parent_id, 'state': 'succeeded', 'requested_seed': 1,
                         'seed': 1, 'engine_id': 'minimax_m3_comfy', 'edits': [],
                         'audio_file': f'{parent_id}.wav'}]
    store._save(project)

    candidate_id = str(uuid.uuid4())
    (library / f'{candidate_id}.wav').write_bytes(_tone(1.0, frequency=880))
    fresh = store.get(project['project_id'])
    fresh['takes'].append({'take_id': candidate_id, 'state': 'succeeded', 'requested_seed': 2,
                           'seed': 2, 'engine_id': 'minimax_m3_comfy', 'edits': [],
                           'audio_file': f'{candidate_id}.wav',
                           'section_of': {'project_id': project['project_id'],
                                          'take_id': parent_id, 'start_s': 1.0, 'end_s': 2.0}})
    store._save(fresh)

    take = service.splice_section(project['project_id'], parent_id, candidate_id)['take']

    assert take['engine_id'] == 'minimax_m3_comfy'
    assert take['metadata']['engine_id'] == 'splice'


def test_a_fade_inherits_the_parent_takes_engine(library: Path):
    """Found in the same live run: a faded copy also came back with ``engine_id: null``."""
    store = service.store()
    project = store.create(title='Song', caption='quiet', lyrics='line', duration_seconds=12)
    parent_id = str(uuid.uuid4())
    (library / f'{parent_id}.wav').write_bytes(_tone(12.0))
    project['takes'] = [{'take_id': parent_id, 'state': 'succeeded', 'requested_seed': 1,
                         'seed': 1, 'engine_id': 'minimax_m3_comfy', 'edits': [],
                         'audio_file': f'{parent_id}.wav'}]
    store._save(project)

    faded = service.fade_take(project["project_id"], parent_id, 1.5)["take"]

    assert faded['engine_id'] == 'minimax_m3_comfy'
