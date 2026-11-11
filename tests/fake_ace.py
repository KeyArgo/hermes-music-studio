"""A stand-in ACE runtime so generation can be tested end to end, out of process."""
import os
import stat
import wave
from pathlib import Path


def wav_bytes(frames=80, rate=8000):
    import io
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        out.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        out.writeframes(b'\x00\x00' * frames)
    return buf.getvalue()


def make_runtime(root, *, delay=0.0, fail=False, frames=80, fail_seed=None):
    """Create <root>/venv/bin/python that renders a WAV and <root>/source/acestep.

    With ``fail_seed`` the stand-in fails for exactly that seed, so a batch can
    prove one failed take does not stop the ones queued after it.

    The stand-in also implements the two source-conditioned modes:
    ``repaint`` emits a take the same length as its source with the requested
    range zeroed, and ``extend`` emits its source plus silence up to
    ``repainting_end``. That is enough for the adapter/queue tests; the real
    engine's musical semantics are proven by the GPU evidence run.
    """
    root = Path(root)
    (root / 'venv/bin').mkdir(parents=True, exist_ok=True)
    (root / 'checkpoints').mkdir(exist_ok=True)
    (root / 'source/acestep').mkdir(parents=True, exist_ok=True)
    script = root / 'venv/bin/python'
    script.write_text(
        '#!/usr/bin/env python3\n'
        'import json, sys, time, wave\n'
        'job = sys.argv[2]\n'
        f'time.sleep({float(delay)!r})\n'
        f'if {bool(fail)!r}:\n'
        '    sys.stderr.write("fake ace render failed\\n")\n'
        '    sys.exit(3)\n'
        'payload = json.load(open(job + "/input.json"))\n'
        'mode = payload.get("mode", "text2music")\n'
        f'if {fail_seed!r} is not None and payload.get("seed") == {fail_seed!r}:\n'
        '    sys.stderr.write("fake ace refused seed\\n")\n'
        '    sys.exit(4)\n'
        'if mode in ("repaint", "extend"):\n'
        '    src = wave.open(job + "/" + payload["src_audio"], "rb")\n'
        '    rate, ch, width = src.getframerate(), src.getnchannels(), src.getsampwidth()\n'
        '    src_frames = src.getnframes()\n'
        '    raw = src.readframes(src_frames)\n'
        '    src.close()\n'
        '    if mode == "extend":\n'
        '        target = int(round(payload["repainting_end"] * rate))\n'
        '    else:\n'
        '        target = src_frames\n'
        '    need = target * ch * width\n'
        '    out = bytearray(raw[:need])\n'
        '    if len(out) < need:\n'
        '        out.extend(b"\\x00" * (need - len(out)))\n'
        '    if mode == "repaint":\n'
        '        start = int(round(payload["repainting_start"] * rate))\n'
        '        end = int(round(payload["repainting_end"] * rate))\n'
        '        out[start * ch * width:end * ch * width] = b"\\x00" * ((end - start) * ch * width)\n'
        '    w = wave.open(job + "/output.wav", "wb")\n'
        '    w.setparams((ch, width, rate, 0, "NONE", "not compressed"))\n'
        '    w.writeframes(bytes(out))\n'
        '    w.close()\n'
        'else:\n'
        '    w = wave.open(job + "/output.wav", "wb")\n'
        '    w.setparams((1, 2, 8000, 0, "NONE", "not compressed"))\n'
        f'    w.writeframes(b"\\x00\\x00" * {int(frames)})\n'
        '    w.close()\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return root


def runtime_env(runtime, base=None):
    env = dict(os.environ if base is None else base)
    env['ACE_RUNTIME_DIR'] = str(runtime)
    env['ACE_SOURCE_DIR'] = str(Path(runtime) / 'source')
    return env


def pin_runtime(runtime):
    """Point ``ace_engine`` at ``runtime`` in *this* process.

    ``ace_engine`` freezes ``ACE_RUNTIME_DIR``/``ACE_SOURCE_DIR`` into module
    constants at import time. ``runtime_env`` therefore only reaches child
    processes (the worker's render subprocess); an in-process caller — ``/status``
    or ``worker._render_take`` — keeps whatever paths were imported at startup.
    That made a test's answer depend on whether the host happened to have a real
    ACE install. Patching the constants pins the adapter's live health probe to
    the fixture, so availability is a property of the test on every host.

    Returns an ``ExitStack``; call ``close()`` in tearDown.
    """
    import contextlib
    from unittest.mock import patch

    import ace_engine

    runtime = Path(runtime)
    stack = contextlib.ExitStack()
    stack.enter_context(patch.object(ace_engine, 'RUNTIME', runtime))
    stack.enter_context(patch.object(ace_engine, 'SOURCE', runtime / 'source'))
    stack.enter_context(patch.object(ace_engine, 'PYTHON',
                                     runtime / 'venv/bin/python'))
    # LOCK is a separate constant derived from the imported RUNTIME, so a host
    # without a shared drive would otherwise fail on the lock mkdir even
    # with RUNTIME pinned.
    stack.enter_context(patch.object(ace_engine, 'LOCK',
                                     runtime / 'ace-studio-gpu.lock'))
    return stack
