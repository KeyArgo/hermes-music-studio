"""The shipped demo take: metadata, licence and playable audio, no engine needed."""
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from dashboard.plugin_api import router  # noqa: E402
from music_studio import demo  # noqa: E402


def test_demo_take_ships():
    assert demo.available() is True
    p = demo.payload()
    assert p["demo"] is True and p["word_recovery"] == "3/3"
    assert p["licence"] == "MiniMax-Music3" and p["licence_notice"]
    assert p["audio_url"] == "/api/plugins/hermes-music-studio/demo/audio"


def test_demo_audio_path_is_the_shipped_wav():
    path = demo.audio_path()
    assert path.name == 'demo-take.wav'
    assert path.is_file() and path.stat().st_size > 0


def test_demo_routes():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    payload = client.get('/demo')
    assert payload.status_code == 200
    body = payload.json()
    assert body['demo'] is True
    assert body['audio_url'] == '/api/plugins/hermes-music-studio/demo/audio'
    audio = client.get('/demo/audio')
    assert audio.status_code == 200
    assert audio.headers['content-type'].startswith('audio/wav')
    assert len(audio.content) == demo.audio_path().stat().st_size


def test_demo_audio_json_is_the_desktop_shape():
    import base64
    app = FastAPI()
    app.include_router(router)
    body = TestClient(app).get('/demo/audio-json').json()
    assert body['success'] is True and body['media_type'] == 'audio/wav'
    assert base64.b64decode(body['audio_base64']) == demo.audio_path().read_bytes()
