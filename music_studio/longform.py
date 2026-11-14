"""Long-form stitching: join a take with its extension at a raised-cosine fade.

This is the equal-gain law proven in the long-form spike: the outgoing take
starts the fade at full weight and the incoming take ends it at full weight,
with zero slope at both ends (``w_a + w_b == 1``). Both sides carry the same
music (the incoming take's overlap is the outgoing take re-rendered), so
equal-power would sum correlated content to +3 dB; equal-gain stays at unity
and cannot raise the peak above the louder input.

The studio's engine output is pcm_s16le 48 kHz stereo, so only that format is
accepted here. PCM float or other rates are refused rather than silently
resampled: a wrong join should fail loudly, not sound almost right.
"""
import array
import io
import math
import sys
import wave

SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2
PEAK_CEILING = 0.999


def _read(data):
    """Parse a 48 kHz stereo pcm_s16le WAV; return (array('h'), frames)."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError('audio must be non-empty WAV bytes')
    with wave.open(io.BytesIO(bytes(data)), 'rb') as wav:
        if wav.getframerate() != SAMPLE_RATE:
            raise ValueError(f'stitching requires {SAMPLE_RATE} Hz audio')
        if wav.getnchannels() != CHANNELS:
            raise ValueError(f'stitching requires {CHANNELS} channels')
        if wav.getsampwidth() != SAMPLE_WIDTH:
            raise ValueError('stitching requires 16-bit PCM')
        frames = wav.getnframes()
        if frames <= 0:
            raise ValueError('empty WAV')
        raw = wav.readframes(frames)
        expected = frames * CHANNELS * SAMPLE_WIDTH
        if len(raw) != expected:
            raise ValueError('truncated WAV')
    samples = array.array('h')
    samples.frombytes(raw)
    if sys.byteorder == 'big':
        samples.byteswap()
    return samples, frames


def _wav_bytes(samples):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setparams((CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
        wav.writeframes(samples.tobytes())
    return buf.getvalue()


def _number(value, name):
    if type(value) not in (int, float):
        raise ValueError(f'{name} must be a number')
    return float(value)


def stitch_wavs(a_bytes, b_bytes, *, splice_start, overlap_seconds,
                expected_duration=None, peak_ceiling=PEAK_CEILING):
    """Join A[0:splice] + fade(A,B over the overlap) + B[splice+overlap:].

    ``b_bytes`` must be A's audio extended past its end (the repaint path), so
    its overlap window is the regenerated version of A's last overlap seconds.
    Returns ``(stitched_wav_bytes, report)``.
    """
    splice = round(_number(splice_start, 'splice_start'), 3)
    overlap = round(_number(overlap_seconds, 'overlap_seconds'), 3)
    a, a_frames = _read(a_bytes)
    b, b_frames = _read(b_bytes)
    a_duration = a_frames / SAMPLE_RATE
    b_duration = b_frames / SAMPLE_RATE
    if splice <= 0:
        raise ValueError('splice_start must be greater than 0')
    if overlap < 1.0:
        raise ValueError('overlap must be at least 1 second')
    if splice + overlap > a_duration + 1e-6:
        raise ValueError(f'overlap window exceeds the source take ({a_duration:.3f} s)')
    if b_duration < a_duration - 1e-3:
        raise ValueError(f'extended take ({b_duration:.3f} s) is not longer than '
                         f'the source ({a_duration:.3f} s)')

    f_splice = int(round(splice * SAMPLE_RATE))
    f_overlap = int(round(overlap * SAMPLE_RATE))
    if f_splice + f_overlap > a_frames:
        raise ValueError('overlap window exceeds the source take')
    if f_splice + f_overlap > b_frames:
        raise ValueError('overlap window exceeds the extended take')
    left = f_splice * CHANNELS
    window = f_overlap * CHANNELS
    blended = array.array('h', bytes(window * SAMPLE_WIDTH))
    for frame in range(f_overlap):
        # Zero slope at both ends of the fade; weights sum to exactly 1.
        weight_b = 0.5 - 0.5 * math.cos(math.pi * (frame + 0.5) / f_overlap)
        weight_a = 1.0 - weight_b
        base = (f_splice + frame) * CHANNELS
        for channel in range(CHANNELS):
            value = (a[base + channel] * weight_a + b[base + channel] * weight_b)
            blended[frame * CHANNELS + channel] = max(-32768, min(32767, int(round(value))))

    head = a[:left]
    new = array.array('h', blended)
    new.extend(b[left + window:])

    # The head is the caller's untouched take, so never attenuate it: only the
    # material this stitch adds may be limited. A loud source therefore keeps a
    # bit-identical head even when the new part needs the ceiling gain.
    new_peak = max(max(new), -min(new)) / 32768.0 if new else 0.0
    gain = 1.0
    if new_peak > peak_ceiling:
        gain = peak_ceiling / new_peak
        for index, sample in enumerate(new):
            new[index] = max(-32768, min(32767, int(round(sample * gain))))
    out = array.array('h', head)
    out.extend(new)

    maximum = max(out)
    minimum = min(out)
    peak_raw = maximum if maximum >= -minimum else -minimum
    peak = peak_raw / 32768.0
    clipped = out.count(32767) + out.count(-32768)
    duration = len(out) / CHANNELS / SAMPLE_RATE
    if expected_duration is not None:
        expected = _number(expected_duration, 'expected_duration')
        if abs(duration - expected) > 0.25:
            raise ValueError(f'stitched duration {duration:.3f} s deviates from the '
                             f'planned {expected:.3f} s by more than 250 ms')

    report = {
        'duration_seconds': round(duration, 4),
        'expected_duration_seconds': round(_number(expected_duration, 'expected_duration'), 4)
        if expected_duration is not None else round(b_duration, 4),
        'sample_rate': SAMPLE_RATE,
        'channels': CHANNELS,
        'splice_start': splice,
        'overlap_seconds': overlap,
        'head_bit_identical': out[:left] == a[:left],
        'peak': round(peak, 6),
        'peak_dbfs': round(20 * math.log10(peak) if peak > 0 else -999.0, 3),
        'clipped_frames': clipped,
        'gain_applied_db': round(20 * math.log10(gain), 4),
    }
    return _wav_bytes(out), report
