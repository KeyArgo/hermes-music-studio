"""Audio QC gate: honest lengths, a reversible tail edit, a peak cap, vocals.

Three facts a render cannot promise are measured instead of assumed:

``requested_s``
    What the caller asked the engine for. Divergence from the file is normal:
    a requested 15 s take really renders ~11.7 s of music and then a 3.0-3.3 s
    dead tail (~20% of the file).
``file_s``
    The rendered WAV's own duration.
``musical_end_s``
    The last frame above −50 dBFS. ``tail_silence_s = file_s − musical_end_s``.

Trimming is a *named, reversible edit*: :func:`plan_edit` decides what to do,
:func:`render_edit` produces new bytes, and the caller (``jobs.apply_qc_edit``)
stores them under their own id while the raw take keeps its untouched bytes and
records the transform. The cut is faded over 40-150 ms and the result is capped
at −1 dBFS, so the edit never introduces a click or a clipped sample.

Vocals are tri-state and deliberately pessimistic. Band modulation is a
diagnostic number, never a badge: without a real ASR pass the answer is
``unknown``. Only :func:`vocal_evidence` with an ASR result that names its run
can ever return ``confirmed``.
"""
from __future__ import annotations

import io
import math
import struct
import wave

TAIL_SILENCE_DBFS = -50.0
PEAK_CAP_DBFS = -1.0
FADE_MS_MIN = 40
FADE_MS_MAX = 150
FADE_MS_DEFAULT = 80
TRIM_MIN_TAIL_SECONDS = 0.2

# A file of digital silence has no finite peak; this floor is what we store so
# the value stays JSON-safe and still reads as "far below anything audible".
SILENCE_FLOOR_DBFS = -120.0

VOCAL_STATES = ('confirmed', 'not_confirmed', 'unknown')
VOCAL_CONFIRM_RATIO = 0.3

_WIDTHS = {1: 'b', 2: 'h', 4: 'i'}


def _db(ratio):
    if ratio <= 0:
        return SILENCE_FLOOR_DBFS
    return max(SILENCE_FLOOR_DBFS, 20.0 * math.log10(ratio))


def _decode(raw, width):
    """Return (signed samples, full-scale divisor) for one PCM chunk."""
    if width in _WIDTHS:
        values = struct.unpack('<' + _WIDTHS[width] * (len(raw) // width), raw)
        if width == 1:
            return [value - 128 for value in values], 128.0
        return values, float(1 << (8 * width - 1))
    if width == 3:
        values = [int.from_bytes(raw[i:i + 3], 'little', signed=True)
                  for i in range(0, len(raw) - 2, 3)]
        return values, float(1 << 23)
    raise ValueError('unsupported sample width')


def _clip_floor(width):
    """Smallest integer sample magnitude that counts as full scale."""
    if width == 1:
        return 127
    return (1 << (8 * width - 1)) - 1


def analyse_wav(data):
    """Measure tail, peak and clipping of WAV ``data`` (never mutates it)."""
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError('audio must be bytes')
    with wave.open(io.BytesIO(data), 'rb') as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        if frames <= 0 or rate <= 0 or channels <= 0:
            raise ValueError('empty audio')
        raw = wav.readframes(frames)
    if len(raw) != frames * channels * width:
        raise ValueError('truncated audio')
    values, scale = _decode(raw, width)
    if len(values) != frames * channels:
        raise ValueError('truncated audio')

    magnitude = max(max(values), -min(values))
    threshold = scale * (10.0 ** (TAIL_SILENCE_DBFS / 20.0))

    # Scan whole frames from the end in chunks: the silent tail is what we walk,
    # and a chunk with any content above the threshold narrows to one frame.
    end_frame = 0
    chunk_frames = 4096
    frame = frames
    while frame > 0:
        start = max(0, frame - chunk_frames)
        block = values[start * channels:frame * channels]
        if max(block) > threshold or -min(block) > threshold:
            for index in range(frame - 1, start - 1, -1):
                frame_values = values[index * channels:(index + 1) * channels]
                if (max(frame_values) > threshold
                        or -min(frame_values) > threshold):
                    end_frame = index + 1
                    break
            break
        frame = start

    full_scale = _clip_floor(width)
    clipped_frames = 0
    if magnitude >= full_scale:
        for index in range(frames):
            frame_values = values[index * channels:(index + 1) * channels]
            if max(frame_values) >= full_scale or -min(frame_values) >= full_scale:
                clipped_frames += 1

    file_s = frames / rate
    musical_end_s = end_frame / rate
    return {
        'sample_rate': rate,
        'channels': channels,
        'sample_width': width,
        'frames': frames,
        'file_s': round(file_s, 3),
        'musical_end_s': round(musical_end_s, 3),
        'tail_silence_s': round(max(0.0, file_s - musical_end_s), 3),
        'peak_dbfs': round(_db(magnitude / scale), 2),
        'clipped_frames': clipped_frames,
        'tail_silence_dbfs': TAIL_SILENCE_DBFS,
        'peak_cap_dbfs': PEAK_CAP_DBFS,
    }


def _validate_fade(fade_ms):
    if fade_ms is None:
        return FADE_MS_DEFAULT
    if type(fade_ms) is not int:
        raise ValueError('fade_ms must be an integer number of milliseconds')
    if not FADE_MS_MIN <= fade_ms <= FADE_MS_MAX:
        raise ValueError(f'fade_ms must be between {FADE_MS_MIN} and {FADE_MS_MAX}')
    return fade_ms


def plan_edit(report, fade_ms=None):
    """Decide the named edit a report calls for, or ``None`` for a clean take."""
    fade_ms = _validate_fade(fade_ms)
    tail = report['tail_silence_s']
    peak = report['peak_dbfs']
    trim = tail >= TRIM_MIN_TAIL_SECONDS and report['musical_end_s'] > 0
    cap = peak > PEAK_CAP_DBFS + 1e-9
    if not trim and not cap:
        return None
    # Name every transform actually applied: a clipped take with a dead tail
    # gets both, and calling that a trim would misstate the stored audio.
    kind = 'trim+peak_cap' if trim and cap else ('trim' if trim else 'peak_cap')
    plan = {
        'kind': kind,
        'trim_end_s': round(report['musical_end_s'], 3) if trim else None,
        'fade_ms': fade_ms if trim else 0,
        'gain_db': round(PEAK_CAP_DBFS - peak, 3) if cap else 0.0,
        'peak_cap_dbfs': PEAK_CAP_DBFS,
        'source_file_s': report['file_s'],
        'source_peak_dbfs': peak,
        'tail_silence_s': tail,
    }
    return plan


def _write_wav(samples, params):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setparams(params)
        if params.sampwidth == 3:
            wav.writeframes(b''.join(
                int(max(-(1 << 23), min((1 << 23) - 1, value))).to_bytes(
                    3, 'little', signed=True) for value in samples))
        else:
            wav.writeframes(struct.pack('<' + _WIDTHS[params.sampwidth] * len(samples),
                                        *samples))
    return buf.getvalue()


def render_edit(data, plan):
    """Apply ``plan`` and return new WAV bytes; ``data`` is left untouched."""
    if not isinstance(plan, dict):
        raise ValueError('plan required')
    with wave.open(io.BytesIO(data), 'rb') as wav:
        params = wav.getparams()
        frames = wav.getnframes()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        raw = wav.readframes(frames)
    values, scale = _decode(raw, width)
    full_scale = float(1 << (8 * width - 1)) if width != 1 else 128.0

    keep_frames = frames
    if plan.get('trim_end_s') is not None:
        keep_frames = max(1, min(frames, int(round(plan['trim_end_s'] * rate))))
    out = list(values[:keep_frames * channels])

    fade_ms = plan.get('fade_ms') or 0
    if fade_ms > 0:
        fade_frames = min(keep_frames, max(1, int(round(fade_ms * rate / 1000.0))))
        first = (keep_frames - fade_frames) * channels
        span = float(fade_frames)
        for offset in range(fade_frames):
            gain = 1.0 - (offset + 1) / span
            base = first + offset * channels
            for channel in range(channels):
                out[base + channel] = int(round(out[base + channel] * gain))

    gain_db = plan.get('gain_db') or 0.0
    if gain_db:
        gain = 10.0 ** (gain_db / 20.0)
        limit = full_scale - 1
        out = [max(-limit, min(limit, int(round(value * gain)))) for value in out]

    return _write_wav(out, params)


def _validated_evidence(value):
    """Rebuild an evidence block through :func:`vocal_evidence`.

    A stored block is a *claim*; only the constructor may decide what it
    means. A forged ``{'status': 'confirmed'}`` therefore degrades to
    ``unknown`` instead of being trusted.
    """
    if value is None:
        return vocal_evidence()
    if not isinstance(value, dict):
        raise ValueError('vocal_evidence must be a dict')
    if value.get('source') == 'asr':
        return vocal_evidence(asr={
            'words_recognised': value.get('words_recognised'),
            'total': value.get('total'),
            'run_id': value.get('run_id'),
            'model': value.get('model'),
        })
    modulation = value.get('modulation_dbfs')
    if modulation is not None and (type(modulation) not in (int, float)
                                   or not math.isfinite(modulation)):
        raise ValueError('modulation_dbfs must be a finite number')
    return vocal_evidence(modulation_dbfs=modulation)


def validate_take_qc(record):
    """Validate the QC block stored on a take; raise rather than store junk."""
    if not isinstance(record, dict):
        raise ValueError('qc record must be a dict')
    required = ('requested_s', 'file_s', 'musical_end_s', 'tail_silence_s',
                'peak_dbfs', 'clipped_frames')
    clean: dict = {}
    for key in required:
        if key not in record:
            raise ValueError(f'qc record missing {key}')
        value = record[key]
        if key == 'clipped_frames':
            if type(value) is not int or value < 0:
                raise ValueError('clipped_frames must be a non-negative integer')
        else:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f'{key} must be a finite number')
            if value < 0 and key != 'peak_dbfs':
                raise ValueError(f'{key} must not be negative')
            value = round(float(value), 3)
        clean[key] = value
    if clean['musical_end_s'] > clean['file_s'] + 0.05:
        raise ValueError('musical_end_s cannot exceed file_s')
    supplied = record.get('vocal_presence')
    if supplied is not None and supplied not in VOCAL_STATES:
        raise ValueError('vocal_presence must be one of ' + ', '.join(VOCAL_STATES))
    # Presence is derived, never taken from the caller: a verdict without an
    # ASR run behind it can only be 'unknown', and a caller that says otherwise
    # is refused rather than believed.
    evidence = _validated_evidence(record.get('vocal_evidence'))
    presence = evidence['status']
    if supplied is not None and supplied != presence:
        raise ValueError(f'vocal_presence {supplied!r} contradicts its evidence '
                         f'({presence!r})')
    clean['vocal_presence'] = presence
    clean['vocal_evidence'] = evidence
    if record.get('edit_plan') is not None:
        clean['edit_plan'] = record['edit_plan']
    return clean


def vocal_evidence(asr=None, modulation_dbfs=None):
    """Tri-state vocal evidence; ``unknown`` unless a real ASR pass says more.

    ``modulation_dbfs`` is accepted purely so a caller can persist the
    diagnostic band-modulation number next to the verdict. It never changes the
    verdict: a heuristic is not evidence of a voice.
    """
    evidence = {'status': 'unknown', 'source': 'none',
                'detail': 'no ASR pass has run on this take'}
    if modulation_dbfs is not None:
        evidence['modulation_dbfs'] = float(round(modulation_dbfs, 2))
    if asr is None:
        return evidence
    if not isinstance(asr, dict):
        raise ValueError('asr must be a dict')
    recognised = asr.get('words_recognised')
    total = asr.get('total')
    run_id = asr.get('run_id')
    model = asr.get('model')
    if type(recognised) is not int or type(total) is not int or total <= 0:
        raise ValueError('asr needs integer words_recognised and a positive total')
    if not 0 <= recognised <= total:
        raise ValueError('words_recognised must be within 0..total')
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError('asr needs the id of the run that produced it')
    if not isinstance(model, str) or not model.strip():
        raise ValueError('asr needs the model name')
    ratio = recognised / total
    status = 'confirmed' if ratio >= VOCAL_CONFIRM_RATIO else 'not_confirmed'
    return {'status': status, 'source': 'asr', 'model': model,
            'run_id': run_id, 'words_recognised': recognised, 'total': total,
            'ratio': round(ratio, 3),
            'detail': f'{recognised}/{total} words recognised'}


def take_qc(report, requested_s, fade_ms=None):
    """Build the QC block for a take from its rendered-audio report."""
    if type(requested_s) not in (int, float) or not math.isfinite(requested_s):
        raise ValueError('requested_s must be a finite number')
    record = {
        'requested_s': round(float(requested_s), 3),
        'file_s': report['file_s'],
        'musical_end_s': report['musical_end_s'],
        'tail_silence_s': report['tail_silence_s'],
        'peak_dbfs': report['peak_dbfs'],
        'clipped_frames': report['clipped_frames'],
        'vocal_presence': 'unknown',
        'vocal_evidence': vocal_evidence(),
    }
    plan = plan_edit(report, fade_ms)
    if plan is not None:
        record['edit_plan'] = plan
    return record
