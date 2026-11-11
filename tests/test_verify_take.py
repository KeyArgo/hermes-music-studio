import copy

from music_studio import demo


def test_committed_take_matches():
    assert demo.verify_take()['ok']


def test_sample_rate_mismatch():
    card = demo.payload()
    card['sample_rate'] = 48000
    result = demo.verify_take(card)
    assert not result['ok'] and any('sample_rate' in p for p in result['problems'])


def test_duration_mismatch():
    card = demo.payload()
    card['duration_seconds'] += 1
    result = demo.verify_take(card)
    assert not result['ok'] and any('duration_seconds' in p for p in result['problems'])


def test_missing_licence():
    card = demo.payload()
    del card['licence']
    result = demo.verify_take(card)
    assert not result['ok'] and any('licence' in p for p in result['problems'])


def test_missing_audio_does_not_raise(tmp_path, monkeypatch):
    card = copy.deepcopy(demo.payload())
    monkeypatch.setattr(demo, 'DEMO_DIR', tmp_path)
    result = demo.verify_take(card)
    assert not result['ok'] and result['problems']
