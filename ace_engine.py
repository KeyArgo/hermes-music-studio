"""Bounded subprocess adapter for the locally installed ACE-Step runtime."""
from music_studio.childenv import minimal_env
from music_studio.portalock import lock_ex
import io
import json
import os
import sys
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import wave

# No paths are baked in: an ACE-Step install is per-machine. Set ACE_RUNTIME_DIR / ACE_SOURCE_DIR
# (the studio's Compute settings write them, or export them yourself). Empty means "not installed
# here", and the engine says so instead of pointing at a folder that belongs to another machine.
_RUNTIME = os.environ.get('ACE_RUNTIME_DIR') or ''
_SOURCE = os.environ.get('ACE_SOURCE_DIR') or ''
# A path that is never created stands in for "not installed": `available()` then answers False and
# nothing else in the module has to special-case an empty value.
RUNTIME = Path(_RUNTIME) if _RUNTIME else Path('/nonexistent/ace-runtime')
SOURCE = Path(_SOURCE) if _SOURCE else Path('/nonexistent/ace-source')
PYTHON = RUNTIME / 'venv/bin/python'


def _interpreter():
    """The runtime's Python: venv/bin/python on POSIX, venv/Scripts/python.exe on Windows."""
    if os.name == 'nt':
        exe = RUNTIME / 'venv/Scripts/python.exe'
        if exe.is_file():
            return exe
    return PYTHON


def _interpreter_cmd():
    """argv prefix to run the runtime's Python; a script stand-in is run through this Python on Windows."""
    exe = _interpreter()
    if os.name == 'nt' and exe.suffix.lower() != '.exe':
        return [sys.executable, str(exe)]
    return [str(exe)]
WORKER = Path(__file__).with_name('ace_worker.py')
LOCK = RUNTIME / 'ace-studio-gpu.lock'

# Coarse progress milestones (fraction of a take's expected wall clock -> percent).
# ACE-Step gives no per-step callbacks in this adapter, so the worker reports a
# monotonic estimate and /status derives the ETA from it. Fractions, not seconds:
# the same milestone must land at the right proportion of a 20 s take and of a
# 180 s one, whose wall clocks differ by more than 3x.
MILESTONES = ((0.04, 10), (0.12, 25), (0.25, 45), (0.55, 70), (0.8, 85))
POLL_SECONDS = 0.5

# Duration contract at the engine boundary. Mirrors music_studio.store (which
# owns the product-facing numbers); tests/test_limits.py asserts they agree, so
# an approved draft can always render.
DURATION_MIN_SECONDS = 10
DURATION_MAX_SECONDS = 180

# Source-conditioned render contract (mirrors music_studio.store).
REPAINT_MIN_SECONDS = 3
REPAINT_MAX_SECONDS = 30
EXTEND_MIN_SECONDS = 10
EXTEND_MAX_SECONDS = 60
EXTEND_MAX_OVERLAP_SECONDS = 8.0
EXTEND_SOURCE_MIN_SECONDS = 20
TOTAL_MAX_SECONDS = 600

# Wall-clock model, same coefficients as music_studio.store (whole render,
# including model load), fitted to the contended-GPU observation: 553.5 s for a
# 180 s take through the plugin path while another render shared the GPU.
RENDER_BASE_SECONDS = 125.0
RENDER_SECONDS_PER_AUDIO_SECOND = 2.4


def estimate_render_seconds(duration_seconds):
    return RENDER_BASE_SECONDS + RENDER_SECONDS_PER_AUDIO_SECOND * float(duration_seconds)


def render_timeout_seconds(duration_seconds):
    """Allow 3x the modelled render time, never less than 3 minutes."""
    return max(180, int(3 * estimate_render_seconds(duration_seconds) + 0.999))


def available():
    return _interpreter().is_file() and (RUNTIME / 'checkpoints').is_dir() and (SOURCE / 'acestep').is_dir()


def unavailable_reason():
    """Why ACE cannot render here, in the words a person needs to fix it."""
    if not _RUNTIME or not _SOURCE:
        return ('ACE-Step is not configured on this machine: set the runtime and source folders in '
                'Settings -> Compute, or export ACE_RUNTIME_DIR and ACE_SOURCE_DIR')
    if not _interpreter().is_file():
        return f'ACE-Step runtime has no interpreter at {_interpreter()}'
    if not (RUNTIME / 'checkpoints').is_dir():
        return f'ACE-Step checkpoints are missing from {RUNTIME}'
    return f'ACE-Step source is missing from {SOURCE}'


def _emit(on_progress, percent):
    if on_progress is None:
        return
    try:
        on_progress(percent)
    except Exception:
        # Progress reporting must never break a render.
        pass


def _detach():
    from music_studio.procs import detach_kwargs
    return detach_kwargs()


def _kill_process_group(proc):
    """Kill the render and every descendant it started (ACE spawns helpers)."""
    from music_studio.procs import kill_tree
    try:
        kill_tree(proc.pid)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


# An absolute path anywhere in ACE output is noise the user cannot act on (and
# leaks server layout). Reduce every path to its basename before it is stored.
_ABS_PATH = re.compile(r'/[^\s\'"()<>:;,]+(?:/[^\s\'"()<>:;,]+)*')
MAX_ERROR_CHARS = 200


def _summarize_failure(text, returncode):
    """One meaningful, path-free line describing an ACE failure.

    ACE writes a full traceback with absolute checkpoint/source paths on a
    model-load or CUDA failure. The take list is user-facing, so only the last
    non-empty line is kept, with paths reduced to basenames and the whole
    message capped.
    """
    lines = [line.strip() for line in str(text).strip().splitlines() if line.strip()]
    detail = lines[-1] if lines else 'no output'
    detail = _ABS_PATH.sub(lambda m: m.group(0).rsplit('/', 1)[-1], detail)
    if len(detail) > MAX_ERROR_CHARS:
        detail = detail[:MAX_ERROR_CHARS - 1].rstrip() + '…'
    return f'ACE worker failed ({returncode}): {detail}'


def _wait_with_progress(proc, timeout_seconds, on_progress, expected_seconds):
    """Wait for the child, emitting milestones; raise TimeoutError past the limit."""
    started = time.monotonic()
    emitted = 0
    while True:
        try:
            return proc.wait(timeout=POLL_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        elapsed = time.monotonic() - started
        while emitted < len(MILESTONES) and elapsed >= MILESTONES[emitted][0] * expected_seconds:
            _emit(on_progress, MILESTONES[emitted][1])
            emitted += 1
        if elapsed >= timeout_seconds:
            raise TimeoutError('ACE worker timed out')


# Vocabularies fixed by the frozen client contract (mirrors music_studio.store).
VOCAL_LANGUAGES = ('en', 'es', 'fr', 'de', 'it', 'pt', 'ja', 'ko', 'zh', 'auto')
# The runtime's own vocabulary has no 'auto'; it spells "let the model decide"
# as 'unknown'. Verbatim from acestep/constants.py:VALID_LANGUAGES.
RUNTIME_LANGUAGE_AUTO = 'unknown'
BPM_MIN, BPM_MAX = 40, 220
KEYSCALE_MAX = 24


def _engine_language(vocal_language, instrumental):
    """Translate the studio's language vocabulary into ACE's at the boundary.

    An instrumental render has no vocals, so the language hint is dropped
    entirely ('unknown'); otherwise 'auto' becomes ACE's 'unknown'.
    """
    if instrumental or vocal_language == 'auto':
        return RUNTIME_LANGUAGE_AUTO
    return vocal_language


def generate(*, caption, lyrics, duration_seconds, seed, output_dir, timeout_seconds=None,
             instrumental=False, vocal_language='en', bpm=None, keyscale=None,
             on_progress=None, on_process=None):
    """Return WAV bytes from one genuine local render; raises on failure.

    output_dir is a trusted, server-owned scratch directory, not user input.
    timeout_seconds defaults to three times this duration's modelled wall clock,
    so a slow-but-working long take is never killed by a fixed cap.
    on_progress, when given, is called with a coarse integer percentage.
    on_process, when given, is called once with the ACE child's pid so the
    caller can reap the render if its own process dies without cleanup.
    """
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 512:
        raise ValueError('caption must contain 1–512 characters')
    if type(instrumental) is not bool:
        raise ValueError('instrumental must be a boolean')
    if not isinstance(lyrics, str) or len(lyrics) > 4096:
        raise ValueError('lyrics must contain 1–4096 characters')
    # An instrumental take legitimately has no lyrics; the runtime is told
    # instrumental=True and must receive EMPTY lyrics, never the '[inst]'
    # sentinel (the adapter, not the user, owns that distinction).
    sent_lyrics = '' if instrumental else lyrics.strip()
    if not instrumental and not sent_lyrics:
        raise ValueError('lyrics must contain 1–4096 characters')
    if vocal_language not in VOCAL_LANGUAGES:
        raise ValueError('vocal_language must be one of ' + ', '.join(VOCAL_LANGUAGES))
    if bpm is not None and (type(bpm) is not int or not BPM_MIN <= bpm <= BPM_MAX):
        raise ValueError(f'bpm must be {BPM_MIN}-{BPM_MAX} or null')
    if keyscale is not None and (not isinstance(keyscale, str) or len(keyscale) > KEYSCALE_MAX):
        raise ValueError(f'keyscale must be at most {KEYSCALE_MAX} characters or null')
    if type(duration_seconds) not in (int, float) or not \
            DURATION_MIN_SECONDS <= duration_seconds <= DURATION_MAX_SECONDS:
        raise ValueError(f'duration_seconds must be between {DURATION_MIN_SECONDS} and '
                         f'{DURATION_MAX_SECONDS}')
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError('invalid seed')
    if timeout_seconds is None:
        timeout_seconds = render_timeout_seconds(duration_seconds)
    if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 3600:
        raise ValueError('timeout_seconds must be between 1 and 3600')
    return _render(
        job_input=dict(caption=caption, lyrics=sent_lyrics,
                       duration_seconds=duration_seconds, seed=seed,
                       instrumental=instrumental,
                       vocal_language=_engine_language(vocal_language, instrumental),
                       bpm=bpm, keyscale=keyscale),
        output_dir=output_dir, timeout_seconds=timeout_seconds,
        expected_seconds=estimate_render_seconds(duration_seconds),
        on_progress=on_progress, on_process=on_process)


def _wav_info(data):
    """Header facts for a WAV blob, or ValueError. Never trusts a claimed duration."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError('source audio must be non-empty WAV bytes')
    if len(data) > 100 * 1024 * 1024:
        raise ValueError('source audio exceeds 100 MiB')
    try:
        with wave.open(io.BytesIO(bytes(data)), 'rb') as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            if frames <= 0 or rate <= 0 or channels <= 0:
                raise ValueError('empty WAV')
            if len(wav.readframes(frames)) != frames * channels * width:
                raise ValueError('truncated WAV')
    except (wave.Error, EOFError, ValueError) as exc:
        raise ValueError('source audio must be a valid WAV') from exc
    return {'frames': frames, 'rate': rate, 'channels': channels,
            'width': width, 'duration_seconds': frames / rate}


def _render(*, job_input, output_dir, timeout_seconds, expected_seconds,
            on_progress=None, on_process=None, source_wav=None):
    """Run one job through the ACE subprocess and return its validated WAV bytes.

    The GPU lease, private job dir, env, progress and output validation live
    here once so every render kind shares exactly one concurrency path.
    """
    out = Path(output_dir).resolve(strict=True)
    if not out.is_dir():
        raise ValueError('output_dir must be a directory')
    _emit(on_progress, 1)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a') as lock:
        try:
            lock_ex(lock, blocking=False)
        except BlockingIOError as exc:
            raise RuntimeError('ACE GPU worker busy') from exc
        with tempfile.TemporaryDirectory(prefix='ace-job-', dir=out) as job:
            job = Path(job)
            if source_wav is not None:
                # The engine loads src_audio from a path; the private job dir is
                # the only place it is ever visible.
                (job / 'source.wav').write_bytes(source_wav)
            (job / 'input.json').write_text(json.dumps(job_input), encoding='utf-8')
            # Review item 4: the ACE runtime gets an allowlisted environment (PATH, HOME, LANG, the
            # CUDA/HF/ACE knobs) rather than every provider key the Hermes process happens to hold.
            env = minimal_env({
                'ACESTEP_CHECKPOINTS_DIR': str(RUNTIME / 'checkpoints'),
                'HF_HUB_OFFLINE': '1',
                'TRANSFORMERS_OFFLINE': '1',
                'HF_DATASETS_OFFLINE': '1',
                'PYTHONPATH': str(SOURCE),
                'ACE_SOURCE_DIR': str(SOURCE),
                'HF_HOME': str(RUNTIME / 'hfmodules'),
                'TRITON_CACHE_DIR': str(RUNTIME / 'triton'),
            })
            _emit(on_progress, 3)
            with (job / 'worker.log').open('wb') as log:
                proc = subprocess.Popen([*_interpreter_cmd(), str(WORKER), str(job)],
                                        stdout=log, stderr=subprocess.STDOUT,
                                        env=env, **_detach())
                if on_process is not None:
                    try:
                        on_process(proc.pid)
                    except Exception:
                        # Recording the pid is best-effort; the render must not fail.
                        pass
                try:
                    returncode = _wait_with_progress(proc, timeout_seconds, on_progress,
                                                     expected_seconds)
                except BaseException:
                    # Timeout or cancellation: the child runs in its own session,
                    # so kill the whole group or descendants keep the GPU.
                    _kill_process_group(proc)
                    raise
            if returncode:
                text = (job / 'worker.log').read_text(errors='replace')
                raise RuntimeError(_summarize_failure(text, returncode))
            path = job / 'output.wav'
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 100 * 1024 * 1024:
                raise RuntimeError('ACE worker returned no valid WAV')
            try:
                with wave.open(str(path), 'rb') as wav:
                    if wav.getnframes() <= 0 or wav.getframerate() <= 0:
                        raise ValueError('empty WAV')
                    expected = wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
                    if len(wav.readframes(wav.getnframes())) != expected:
                        raise ValueError('truncated WAV')
            except (wave.Error, EOFError, ValueError) as exc:
                raise RuntimeError('ACE worker returned invalid WAV') from exc
            _emit(on_progress, 95)
            return path.read_bytes()


def repaint(*, source_wav, start_s, end_s, caption, lyrics, seed, output_dir,
            timeout_seconds=None, instrumental=False, vocal_language='en',
            bpm=None, keyscale=None, on_progress=None, on_process=None):
    """Regenerate one time range of ``source_wav``; the rest is kept by ACE.

    Returns a take the same length as the source. ACE repaints by masking the
    source's latent frames inside [start, end] and preserving everything
    outside; it does not shorten the output, and the output length is the
    padded source length (here, equal to the source because end <= duration).
    """
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 512:
        raise ValueError('caption must contain 1–512 characters')
    if type(instrumental) is not bool:
        raise ValueError('instrumental must be a boolean')
    if not isinstance(lyrics, str) or len(lyrics) > 4096:
        raise ValueError('lyrics must contain 1–4096 characters')
    sent_lyrics = '' if instrumental else lyrics.strip()
    if not instrumental and not sent_lyrics:
        raise ValueError('lyrics must contain 1–4096 characters')
    if vocal_language not in VOCAL_LANGUAGES:
        raise ValueError('vocal_language must be one of ' + ', '.join(VOCAL_LANGUAGES))
    if bpm is not None and (type(bpm) is not int or not BPM_MIN <= bpm <= BPM_MAX):
        raise ValueError(f'bpm must be {BPM_MIN}-{BPM_MAX} or null')
    if keyscale is not None and (not isinstance(keyscale, str) or len(keyscale) > KEYSCALE_MAX):
        raise ValueError(f'keyscale must be at most {KEYSCALE_MAX} characters or null')
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError('invalid seed')
    for value, name in ((start_s, 'start_s'), (end_s, 'end_s')):
        if type(value) not in (int, float):
            raise ValueError(f'{name} must be a number')
    start_s, end_s = float(start_s), float(end_s)
    info = _wav_info(source_wav)
    duration = info['duration_seconds']
    if start_s < 0 or end_s <= start_s:
        raise ValueError('end_s must be greater than start_s')
    if end_s > duration + 1e-6:
        raise ValueError(f'end_s must be within the take ({duration:.3f} s)')
    if not REPAINT_MIN_SECONDS <= end_s - start_s <= REPAINT_MAX_SECONDS:
        raise ValueError(f'repaint range must be {REPAINT_MIN_SECONDS}-'
                         f'{REPAINT_MAX_SECONDS} seconds')
    if timeout_seconds is None:
        timeout_seconds = render_timeout_seconds(duration)
    if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 3600:
        raise ValueError('timeout_seconds must be between 1 and 3600')
    data = _render(
        job_input=dict(mode='repaint', src_audio='source.wav',
                       repainting_start=round(start_s, 3),
                       repainting_end=round(end_s, 3),
                       duration_seconds=round(duration, 3),
                       caption=caption, lyrics=sent_lyrics, seed=seed,
                       instrumental=instrumental,
                       vocal_language=_engine_language(vocal_language, instrumental),
                       bpm=bpm, keyscale=keyscale),
        output_dir=output_dir, timeout_seconds=timeout_seconds,
        expected_seconds=estimate_render_seconds(duration),
        on_progress=on_progress, on_process=on_process, source_wav=source_wav)
    rendered = _wav_info(data)['duration_seconds']
    if abs(rendered - duration) > 0.25:
        raise RuntimeError(f'repaint returned {rendered:.3f} s, expected {duration:.3f} s')
    return data


def plan_extension(source_duration, extend_seconds):
    """Repaint geometry for 'extend this take' (see the long-form spike design).

    The last ``overlap`` seconds of the source are regenerated so the engine
    produces new material from inside the take, and the result must reach
    ``source + extend``. The overlap is capped at 20 % of the take so a short
    take still has untouched head.
    """
    if type(extend_seconds) is not int or not EXTEND_MIN_SECONDS <= extend_seconds <= EXTEND_MAX_SECONDS:
        raise ValueError(f'extend_seconds must be an integer between {EXTEND_MIN_SECONDS} '
                         f'and {EXTEND_MAX_SECONDS}')
    if source_duration < EXTEND_SOURCE_MIN_SECONDS:
        raise ValueError(f'take must be at least {EXTEND_SOURCE_MIN_SECONDS} s to extend')
    total = round(source_duration + extend_seconds, 3)
    if total > TOTAL_MAX_SECONDS:
        raise ValueError(f'extended take would be {total:.0f} s; the maximum is '
                         f'{TOTAL_MAX_SECONDS} s')
    overlap = round(min(EXTEND_MAX_OVERLAP_SECONDS, 0.2 * source_duration), 3)
    return {'source_duration': round(source_duration, 3),
            'extend_seconds': extend_seconds,
            'overlap_seconds': overlap,
            'splice_start': round(source_duration - overlap, 3),
            'planned_duration': total}


def extend(*, source_wav, caption, lyrics, extend_seconds, seed, output_dir,
           timeout_seconds=None, instrumental=False, vocal_language='en',
           bpm=None, keyscale=None, on_progress=None, on_process=None):
    """Render a continuation of ``source_wav`` and return the stitched WAV.

    One repaint call regrows the tail overlap and out-paints to
    ``source + extend``; the returned audio is the source's head plus a
    raised-cosine equal-gain crossfade plus the new material.
    """
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 512:
        raise ValueError('caption must contain 1–512 characters')
    if type(instrumental) is not bool:
        raise ValueError('instrumental must be a boolean')
    if not isinstance(lyrics, str) or len(lyrics) > 4096:
        raise ValueError('lyrics must contain 1–4096 characters')
    sent_lyrics = '' if instrumental else lyrics.strip()
    if not instrumental and not sent_lyrics:
        raise ValueError('lyrics must contain 1–4096 characters')
    if vocal_language not in VOCAL_LANGUAGES:
        raise ValueError('vocal_language must be one of ' + ', '.join(VOCAL_LANGUAGES))
    if bpm is not None and (type(bpm) is not int or not BPM_MIN <= bpm <= BPM_MAX):
        raise ValueError(f'bpm must be {BPM_MIN}-{BPM_MAX} or null')
    if keyscale is not None and (not isinstance(keyscale, str) or len(keyscale) > KEYSCALE_MAX):
        raise ValueError(f'keyscale must be at most {KEYSCALE_MAX} characters or null')
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError('invalid seed')
    info = _wav_info(source_wav)
    plan = plan_extension(info['duration_seconds'], extend_seconds)
    if timeout_seconds is None:
        timeout_seconds = render_timeout_seconds(plan['planned_duration'])
    if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 3600:
        raise ValueError('timeout_seconds must be between 1 and 3600')
    data = _render(
        job_input=dict(mode='extend', src_audio='source.wav',
                       repainting_start=plan['splice_start'],
                       repainting_end=plan['planned_duration'],
                       duration_seconds=plan['planned_duration'],
                       overlap_seconds=plan['overlap_seconds'],
                       extend_seconds=extend_seconds,
                       caption=caption, lyrics=sent_lyrics, seed=seed,
                       instrumental=instrumental,
                       vocal_language=_engine_language(vocal_language, instrumental),
                       bpm=bpm, keyscale=keyscale),
        output_dir=output_dir, timeout_seconds=timeout_seconds,
        expected_seconds=estimate_render_seconds(plan['planned_duration']),
        on_progress=on_progress, on_process=on_process, source_wav=source_wav)
    rendered = _wav_info(data)['duration_seconds']
    if abs(rendered - plan['planned_duration']) > 0.25:
        raise RuntimeError(f'repaint returned {rendered:.3f} s, expected '
                           f"{plan['planned_duration']:.3f} s")
    from music_studio.longform import stitch_wavs
    stitched, report = stitch_wavs(
        source_wav, data, splice_start=plan['splice_start'],
        overlap_seconds=plan['overlap_seconds'],
        expected_duration=plan['planned_duration'])
    if not report['head_bit_identical']:
        raise RuntimeError('stitched take changed the source head')
    if report['peak'] >= 1.0:
        raise RuntimeError('stitched take clips')
    return stitched
