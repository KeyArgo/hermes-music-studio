"""The baked-in demo take: playable with no engine installed.

The assets in ``assets/demo`` are committed fixtures produced once on real
hardware; this module only locates them and reports their metadata, so the
dashboard can offer a working "listen" affordance before any engine exists.
"""
import json
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent / 'assets' / 'demo'

AUDIO_URL = '/api/plugins/hermes-music-studio/demo/audio'


def available() -> bool:
    return (DEMO_DIR / 'demo-take.wav').is_file() and (DEMO_DIR / 'demo-take.json').is_file()


def payload() -> dict:
    if not available():
        raise FileNotFoundError('demo take assets missing')
    data = json.loads((DEMO_DIR / 'demo-take.json').read_text(encoding='utf-8'))
    data['demo'] = True
    data['audio_url'] = AUDIO_URL
    return data


def audio_path() -> Path:
    return DEMO_DIR / 'demo-take.wav'


def verify_take(card: dict | None = None) -> dict:
    """Compare the demo card with its WAV, reporting problems without raising."""
    import math
    import wave

    problems = []
    measured = {}
    if card is None:
        try:
            card = payload()
        except (OSError, ValueError) as exc:
            return {'ok': False, 'problems': [f'card: {exc}'], 'measured': measured}
    if not isinstance(card, dict):
        return {'ok': False, 'problems': ['card must be a dictionary'], 'measured': measured}
    for field in ('engine', 'model', 'licence'):
        if not isinstance(card.get(field), str) or not card[field].strip():
            problems.append(f'{field} must be non-empty')
    if type(card.get('seed')) is not int:
        problems.append('seed must be an integer')
    try:
        with wave.open(str(audio_path()), 'rb') as wav:
            rate, channels, frames = wav.getframerate(), wav.getnchannels(), wav.getnframes()
            data = wav.readframes(frames)
            if len(data) != frames * channels * wav.getsampwidth():
                problems.append('audio is truncated')
            measured = {'sample_rate': rate, 'channels': channels,
                        'duration_seconds': frames / rate, 'frames': frames}
    except (OSError, EOFError, wave.Error, ZeroDivisionError) as exc:
        problems.append(f'audio: {exc}')
    if measured:
        for field in ('sample_rate', 'channels'):
            if type(card.get(field)) is not int or card[field] != measured[field]:
                problems.append(f'{field} does not match audio')
        duration = card.get('duration_seconds')
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or abs(duration - measured['duration_seconds']) >= 0.01):
            problems.append('duration_seconds does not match audio')
    return {'ok': not problems, 'problems': problems, 'measured': measured}
