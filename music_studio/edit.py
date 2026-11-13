"""Non-destructive local edits: splice a replacement section, fade a take out.

Everything here is plain ``wave`` + ``array`` arithmetic on the stored PCM, so an
edit is instant, deterministic, and never re-encodes or touches the parent. An
edit produces a NEW take; the take it came from stays byte-for-byte identical.

Why a local edit at all: the M3 ComfyUI graph has no repaint nodes, so asking it
to re-render part of a song changes the whole song. Replacing only the bytes the
user pointed at is the one operation that cannot drift.
"""
from __future__ import annotations

import io
import wave
from array import array


def read_pcm(data: bytes) -> tuple[array, int, int]:
    """(samples, rate, channels) from a WAV the studio wrote (16-bit PCM)."""
    with wave.open(io.BytesIO(data), 'rb') as wav:
        if wav.getsampwidth() != 2:
            raise ValueError('only 16-bit PCM takes can be edited locally')
        rate, channels = wav.getframerate(), wav.getnchannels()
        samples = array('h')
        samples.frombytes(wav.readframes(wav.getnframes()))
    if rate <= 0 or channels <= 0:
        raise ValueError('unreadable take')
    return samples, rate, channels


def write_pcm(samples: array, rate: int, channels: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    return out.getvalue()


def _mix(dst: array, dst_frame: int, src: array, src_frame: int, frames: int,
         channels: int, rising: bool) -> None:
    """out = dst*(1-a) + src*a over ``frames``, ``a`` rising (src in) or falling."""
    if frames <= 0:
        return
    for index in range(frames):
        ratio = (index + 1) / (frames + 1)
        alpha = ratio if rising else 1.0 - ratio
        base_dst = (dst_frame + index) * channels
        base_src = (src_frame + index) * channels
        for channel in range(channels):
            mixed = dst[base_dst + channel] * (1.0 - alpha) + src[base_src + channel] * alpha
            dst[base_dst + channel] = max(-32768, min(32767, int(round(mixed))))


SHORTFALL_TOLERANCE_S = 0.1


def splice(parent: bytes, candidate: bytes, start_s: float, end_s: float,
           fade_ms: int = 40) -> bytes:
    """Return the parent WAV with [start_s, end_s) taken from ``candidate``.

    Both files must agree on rate and channel count; the candidate is trimmed to
    the section length. A candidate shorter than the section is an error, never
    a silent gap. The seams get equal-gain ramps so the join has no click.
    """
    parent_pcm, rate, channels = read_pcm(parent)
    cand_pcm, cand_rate, cand_channels = read_pcm(candidate)
    if (cand_rate, cand_channels) != (rate, channels):
        raise ValueError('the replacement has a different sample rate or channel count')
    total_frames = len(parent_pcm) // channels
    start = max(0, min(total_frames - 1, int(round(float(start_s) * rate))))
    end = max(start + 1, min(total_frames, int(round(float(end_s) * rate))))
    length = end - start
    # Engines hand back a hair less than they were asked for (MiniMax via ComfyUI: 9.996 s for 10 s).
    # A shortfall that small ends the replacement where its audio ends, inside the crossfade, where
    # nobody can hear it; anything clearly short is still refused rather than leaving a gap.
    have = len(cand_pcm) // cand_channels
    if have < length:
        if length - have > int(SHORTFALL_TOLERANCE_S * rate):
            raise ValueError('the replacement is shorter than the section it must fill')
        length = have
        end = start + length
    need = length * channels
    cand_pcm = array('h', cand_pcm[:need])
    fade = max(0, min(int(round(fade_ms / 1000.0 * rate)), length // 2))

    out = array('h', parent_pcm)
    if fade and length > 2 * fade:
        # Candidate body, then ramps: parent fades out into it, it fades out into the tail.
        out[(start + fade) * channels:(end - fade) * channels] = \
            cand_pcm[fade * channels:(length - fade) * channels]
        # Leading seam: the parent hands over to the candidate.
        _mix(out, start, cand_pcm, 0, fade, channels, rising=True)
        if end < total_frames:
            # Trailing seam: the candidate hands back to the parent. ``out`` still holds the parent
            # here, so the candidate's last ``fade`` frames are mixed in falling. (This used to mix
            # the parent with itself, which cut the candidate off at end - fade with a click, and
            # passed a sample offset where a frame index belongs, overrunning on stereo.)
            _mix(out, end - fade, cand_pcm, length - fade, fade, channels, rising=False)
        else:
            # The section runs to the end of the song: the candidate fades to silence.
            out[(end - fade) * channels:end * channels] = cand_pcm[(length - fade) * channels:length * channels]
            _mix(out, end - fade, array('h', b'\x00\x00' * (fade * channels)), 0, fade,
                 channels, rising=True)
    else:
        out[start * channels:end * channels] = cand_pcm
    return write_pcm(out, rate, channels)


def fade_out(audio: bytes, seconds: float = 1.5) -> bytes:
    """Fade the last ``seconds`` to silence (the ending Suno users edit in a DAW)."""
    samples, rate, channels = read_pcm(audio)
    total_frames = len(samples) // channels
    frames = max(1, min(total_frames, int(round(float(seconds) * rate))))
    start_frame = total_frames - frames
    for index in range(frames):
        alpha = 1.0 - (index + 1) / frames
        base = (start_frame + index) * channels
        for channel in range(channels):
            samples[base + channel] = int(round(samples[base + channel] * alpha))
    return write_pcm(samples, rate, channels)


def measure(audio: bytes) -> dict:
    samples, rate, channels = read_pcm(audio)
    frames = len(samples) // channels
    peak = max((abs(value) for value in samples), default=0)
    return {'sample_rate': rate, 'channels': channels, 'frames': frames,
            'duration_seconds': round(frames / rate, 3) if rate else 0.0,
            'peak': peak}
