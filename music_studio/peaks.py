"""Waveform peak buckets computed server-side, streamed and cached per take.

The browser needs a fixed number of min/max/rms triples to draw a waveform, not
an 8-40 MB WAV. The WAV is read through the take's already symlink-safe file
descriptor in bucket-sized chunks (never the whole file at once), and the result
is cached in a JSON sidecar keyed by the source file's size and mtime: a new
render writes a new file, so a cache entry can never describe different audio.
"""
import json
import os
import stat as stat_module
import struct
import tempfile
import wave
from pathlib import Path

from .store import fsync_dir, valid_id

PEAKS_MIN_BUCKETS = 64
PEAKS_MAX_BUCKETS = 2048
PEAKS_DEFAULT_BUCKETS = 512

# Bytes per sample for the PCM widths wave can hand us. 24-bit has no array
# type; it is decoded manually rather than refused (rare, but valid WAV).
_WIDTHS = {1: 'b', 2: 'h', 4: 'i'}
CACHE_VERSION = 1
MAX_CACHED_BUCKET_COUNTS = 16


def validate_buckets(buckets):
    if type(buckets) is not int or not PEAKS_MIN_BUCKETS <= buckets <= PEAKS_MAX_BUCKETS:
        raise ValueError(f'buckets must be between {PEAKS_MIN_BUCKETS} and '
                         f'{PEAKS_MAX_BUCKETS}')
    return buckets


def _decode(raw, width):
    """Return (signed sample values, full-scale divisor) for one PCM chunk."""
    if width in _WIDTHS:
        values = struct.unpack('<' + _WIDTHS[width] * (len(raw) // width), raw)
        if width == 1:
            # 8-bit WAV is unsigned with a 128 offset.
            return [value - 128 for value in values], 128.0
        return values, float(1 << (8 * width - 1))
    values = [int.from_bytes(raw[i:i + 3], 'little', signed=True)
              for i in range(0, len(raw) - 2, 3)]
    return values, float(1 << 23)


def _bucket_stats(values, scale):
    minimum = min(values) / scale
    maximum = max(values) / scale
    rms = (sum(value * value for value in values) / len(values)) ** 0.5 / scale
    return {'min': round(minimum, 6), 'max': round(maximum, 6),
            'rms': round(rms, 6)}


def compute_peaks(audio_file, buckets):
    """Stream a WAV file object bucket by bucket; return (duration, peaks)."""
    buckets = validate_buckets(buckets)
    audio_file.seek(0)
    with wave.open(audio_file, 'rb') as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        if frames <= 0 or rate <= 0 or channels <= 0 or width not in (1, 2, 3, 4):
            raise ValueError('unsupported WAV format')
        if frames < buckets:
            raise ValueError('WAV too short for bucket count')
        peaks = []
        for index in range(buckets):
            start = index * frames // buckets
            end = (index + 1) * frames // buckets
            count = end - start
            raw = wav.readframes(count)
            expected = count * channels * width
            if len(raw) != expected:
                raise ValueError('truncated WAV')
            values, scale = _decode(raw, width)
            peaks.append(_bucket_stats(values, scale))
        return round(frames / rate, 3), peaks


def _sidecar_path(root, take_id):
    return Path(root) / (valid_id(take_id) + '.peaks.json')


def _read_cache(path, size, mtime_ns):
    """Return the cached payload, or None; never follows a symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat_module.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, 'rb') as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or data.get('version') != CACHE_VERSION:
            return None
        source = data.get('source') or {}
        if source.get('size') != size or source.get('mtime_ns') != mtime_ns:
            return None
        if not isinstance(data.get('peaks'), dict):
            return None
        return data
    except (OSError, ValueError, TypeError):
        return None


def _write_cache(path, take_id, size, mtime_ns, duration, values):
    """Atomically replace the sidecar; a cache write never fails a request."""
    values = {key: entry for key, entry in values.items()
              if isinstance(entry, list)}
    if len(values) > MAX_CACHED_BUCKET_COUNTS:
        # Keep only the bucket count that was just computed: unbounded cache
        # growth is otherwise a slow disk leak for one take.
        values = {next(reversed(values)): values[next(reversed(values))]}
    payload = {'version': CACHE_VERSION, 'take_id': take_id,
               'source': {'size': size, 'mtime_ns': mtime_ns},
               'duration_seconds': duration, 'peaks': values}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix='.peaks-')
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump(payload, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, path)
            fsync_dir(path.parent)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    except OSError:
        pass


def peaks_for(jobs, project_id, take_id, buckets, edit_id=None):
    """Public entry point: cached-or-computed peaks for one succeeded take.

    ``edit_id`` requests the buckets of a named QC edit (a trimmed/capped file)
    instead of the raw take; the cache sidecar is keyed by the file that was
    actually read, so a raw take's peaks never describe its edit.
    """
    buckets = validate_buckets(buckets)
    audio = jobs.open_audio(project_id, take_id, edit_id=edit_id)
    try:
        info = os.fstat(audio.fileno())
        size, mtime_ns = info.st_size, info.st_mtime_ns
        key = edit_id or take_id
        path = _sidecar_path(jobs.store.root, key)
        cached = _read_cache(path, size, mtime_ns)
        if cached is not None:
            peaks = cached['peaks'].get(str(buckets))
            if isinstance(peaks, list) and len(peaks) == buckets:
                return {'success': True, 'buckets': buckets,
                        'duration_seconds': cached.get('duration_seconds'),
                        'peaks': peaks}
        duration, peaks = compute_peaks(audio, buckets)
        cached_values = dict(cached['peaks']) if cached is not None else {}
        _write_cache(path, key, size, mtime_ns, duration,
                     {**cached_values, str(buckets): peaks})
        return {'success': True, 'buckets': buckets,
                'duration_seconds': duration, 'peaks': peaks}
    finally:
        audio.close()
