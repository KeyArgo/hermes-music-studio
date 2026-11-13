"""One-shot ACE runtime subprocess. Launched only by ace_engine.

This module runs inside the ACE runtime's own venv (it imports ``acestep``), so
it must stay dependency-free and importable without the studio package.
"""
import json
import os
from pathlib import Path
import re
import shutil
import sys

# The runtime vocabulary (acestep/constants.py:VALID_LANGUAGES) has no 'auto'.
RUNTIME_LANGUAGE_AUTO = 'unknown'

# ACE's canonical keyscale spelling is acestep/constants.VALID_KEYSCALES:
# "<Note><accidental> <mode>" with a lowercase mode ("C major", "F# minor").
# Clients send whatever they like ("C Major", "Am"), so canonicalise the forms
# that have an exact equivalent and pass anything else through untouched.
_KEYSCALE_LONG = re.compile(r'^([A-G][#♯b♭]?)\s*(major|minor)$', re.IGNORECASE)
_KEYSCALE_SHORT = re.compile(r'^([A-G][#♯b♭]?)\s*m$', re.IGNORECASE)


def _keyscale(value):
    """'C Major' -> 'C major', 'F#m' -> 'F# minor', anything else verbatim."""
    if not value:
        return ''
    text = str(value).strip()
    match = _KEYSCALE_LONG.match(text)
    if match:
        return f'{match.group(1)} {match.group(2).lower()}'
    match = _KEYSCALE_SHORT.match(text)
    if match:
        return f'{match.group(1)} minor'
    return text


def _language(value, instrumental):
    """Translate a studio language code into the one ACE understands.

    Instrumental renders have no vocals, so no language hint is sent at all;
    'auto' means the same thing to ACE as 'unknown' (it lets the 5 Hz LM decide).
    """
    if instrumental or not value or value == 'auto':
        return RUNTIME_LANGUAGE_AUTO
    return value


# ACE-Step is a GPU model. It can technically run on the CPU, but slowly enough
# that it is not a supported configuration; say so instead of letting the
# runtime fail with a CUDA-specific error on a machine that has no CUDA.
CPU_UNSUPPORTED = ('ACE-Step on CPU is very slow and unsupported (not a supported '
                   'configuration); use a CUDA or Apple MPS device')


def _device():
    """The compute device for this render.

    ACE_DEVICE wins when set, so an operator can pin a device (or force the CPU
    path) without editing code. Otherwise prefer CUDA, then Apple MPS, then CPU.
    torch lives in the ACE runtime's venv, not necessarily in this process, so
    the import stays lazy and a failure means "no accelerator we can see".
    """
    override = os.environ.get('ACE_DEVICE')
    if override and override.strip():
        return override.strip().lower()
    try:
        import torch
    except Exception:  # noqa: BLE001 - torch is optional outside the ACE runtime
        return 'cpu'
    try:
        if torch.cuda.is_available():
            return 'cuda'
    except Exception:  # noqa: BLE001 - a broken CUDA build is not an accelerator
        pass
    try:
        mps = getattr(getattr(torch, 'backends', None), 'mps', None)
        if mps is not None and mps.is_available():
            return 'mps'
    except Exception:  # noqa: BLE001 - old torch builds have no MPS backend
        pass
    return 'cpu'


def run(job, Handler, LLM, Params, Config, generate_music):
    job = Path(job).resolve()
    data = json.loads((job / 'input.json').read_text(encoding='utf-8'))
    instrumental = bool(data.get('instrumental', False))
    # Empty lyrics + instrumental=True is the runtime's instrumental path; the
    # '[inst]' sentinel is never sent from this adapter.
    lyrics = '' if instrumental else data.get('lyrics', '')
    mode = data.get('mode', 'text2music')
    fields = dict(
        caption=data['caption'], lyrics=lyrics,
        instrumental=instrumental,
        vocal_language=_language(data.get('vocal_language'), instrumental),
        bpm=data.get('bpm'),
        # ACE spells "let the model pick" as an empty string, not None.
        keyscale=_keyscale(data.get('keyscale')),
        thinking=False, inference_steps=8, guidance_scale=1.0, seed=data['seed'])
    if mode in ('repaint', 'extend'):
        # src_audio is a path inside the engine's private job directory. Resolve
        # it strictly so a crafted input.json can never point ACE at another file.
        name = data.get('src_audio')
        if not isinstance(name, str) or Path(name).name != name:
            raise RuntimeError('invalid source audio name')
        source = (job / name).resolve(strict=True)
        if source.parent != job or not source.is_file() or source.is_symlink():
            raise RuntimeError('source audio is outside the job directory')
        fields.update(
            task_type='repaint', src_audio=str(source),
            repainting_start=float(data['repainting_start']),
            repainting_end=float(data['repainting_end']),
            duration=float(data.get('duration_seconds', data['repainting_end'])))
    else:
        fields.update(task_type='text2music', duration=data['duration_seconds'])
    params = Params(**fields)
    config = Config(batch_size=1, use_random_seed=False, audio_format='wav')
    handler = Handler()
    device = _device()
    try:
        status, ok = handler.initialize_service(project_root=os.environ.get('ACE_SOURCE_DIR', os.path.expanduser('~/ACE-Step-1.5')), config_path='acestep-v15-turbo', device=device, offload_to_cpu=True, compile_model=False)
    except Exception as exc:  # noqa: BLE001 - ACE raises CUDA errors as plain exceptions
        if device == 'cpu':
            raise RuntimeError(f'ACE initialization failed: {exc} ({CPU_UNSUPPORTED})') from exc
        raise
    if not ok:
        detail = str(status)
        if device == 'cpu':
            detail += f' ({CPU_UNSUPPORTED})'
        raise RuntimeError('ACE initialization failed: ' + detail)
    result = generate_music(handler, LLM(), params, config, save_dir=str(job))
    if not result.success:
        raise RuntimeError('ACE generation failed: ' + str(result.error or result.status_message))
    if len(result.audios) != 1:
        raise RuntimeError('ACE did not return exactly one audio file')
    source = Path(result.audios[0]['path']).resolve(strict=True)
    if source.parent != job or not source.is_file():
        raise RuntimeError('ACE returned audio outside job directory')
    shutil.copyfile(source, job / 'output.wav')


def main():
    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler
    from acestep.inference import GenerationParams, GenerationConfig, generate_music
    run(Path(sys.argv[1]), AceStepHandler, LLMHandler, GenerationParams, GenerationConfig, generate_music)


if __name__ == '__main__':
    main()
