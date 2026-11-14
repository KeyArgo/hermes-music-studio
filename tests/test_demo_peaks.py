"""The demo's waveform: the same peaks contract as a take, from the shipped WAV."""
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.plugin_api import router  # noqa: E402
from music_studio import demo  # noqa: E402
from music_studio.peaks import compute_peaks  # noqa: E402


def make_client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_demo_peaks_have_the_take_shape_and_default_buckets():
    body = make_client().get('/demo/peaks').json()
    assert set(body) == {'success', 'buckets', 'duration_seconds', 'peaks'}
    assert body['success'] is True
    assert body['buckets'] == 512
    assert len(body['peaks']) == 512
    assert body['duration_seconds'] > 0
    for bucket in body['peaks']:
        assert set(bucket) == {'min', 'max', 'rms'}
        assert bucket['min'] <= bucket['max']
        assert bucket['rms'] >= 0


def test_demo_peaks_match_the_shipped_wav():
    body = make_client().get('/demo/peaks?buckets=64').json()
    with demo.audio_path().open('rb') as audio:
        duration, expected = compute_peaks(audio, 64)
    assert body['buckets'] == 64
    assert body['duration_seconds'] == duration
    assert body['peaks'] == expected


def test_demo_peaks_reject_out_of_range_buckets():
    client = make_client()
    for buckets in (0, 1, 63, 2049, 4096):
        response = client.get(f'/demo/peaks?buckets={buckets}')
        assert response.status_code == 400
        assert 'buckets' in response.json()['detail']
    assert client.get('/demo/peaks?buckets=lots').status_code != 200
