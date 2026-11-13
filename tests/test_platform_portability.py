"""W4/W5: real Windows 11 and macOS installs, not just the Linux CI box.

Every OS fact is simulated with monkeypatch (``os.name``, ``sys.platform``,
``platform.machine`` and ``subprocess``) so the same suite exercises the fixes
on Linux.
"""
import json
import sys
import tempfile
import types
from pathlib import Path

import pytest

from music_studio.engines import minimax_m3_comfy as mm3
from standalone import hardware


def _fake_torch(cuda=False, mps=False):
    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: cuda),
        backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: mps)))


def _worker_job(directory):
    job = Path(directory)
    (job / 'input.json').write_text(json.dumps(dict(
        caption='folk', lyrics='hello', duration_seconds=15, seed=7)))
    return job


class _Params:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Config:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _generate_music(handler, llm, params, config, save_dir):
    path = Path(save_dir) / 'produced.wav'
    path.write_bytes(b'rendered')
    return types.SimpleNamespace(success=True, audios=[{'path': str(path)}], error=None)


# -- ACE device selection -------------------------------------------------

def test_worker_device_env_override_wins(monkeypatch):
    import ace_worker

    monkeypatch.setenv('ACE_DEVICE', 'MPS')
    monkeypatch.setitem(sys.modules, 'torch', _fake_torch(cuda=True))
    assert ace_worker._device() == 'mps'


def test_worker_device_prefers_cuda_then_mps_then_cpu(monkeypatch):
    import ace_worker

    monkeypatch.delenv('ACE_DEVICE', raising=False)
    monkeypatch.setitem(sys.modules, 'torch', _fake_torch(cuda=True, mps=True))
    assert ace_worker._device() == 'cuda'

    monkeypatch.setitem(sys.modules, 'torch', _fake_torch(cuda=False, mps=True))
    assert ace_worker._device() == 'mps'

    monkeypatch.setitem(sys.modules, 'torch', None)
    assert ace_worker._device() == 'cpu'


def test_worker_passes_the_detected_device_to_ace(monkeypatch):
    import ace_worker

    monkeypatch.delenv('ACE_DEVICE', raising=False)
    monkeypatch.setitem(sys.modules, 'torch', _fake_torch(mps=True))
    seen = {}

    class Handler:
        def initialize_service(self, **kwargs):
            seen['init'] = kwargs
            return 'ready', True

    with tempfile.TemporaryDirectory() as directory:
        ace_worker.run(_worker_job(directory), Handler, lambda: None,
                       _Params, _Config, _generate_music)
    assert seen['init']['device'] == 'mps'


def test_worker_cpu_failure_is_honest_instead_of_a_cuda_error(monkeypatch):
    import ace_worker

    monkeypatch.setenv('ACE_DEVICE', 'cpu')

    class Handler:
        def initialize_service(self, **kwargs):
            return 'no CUDA device', False

    with tempfile.TemporaryDirectory() as directory:
        with pytest.raises(RuntimeError) as caught:
            ace_worker.run(_worker_job(directory), Handler, lambda: None,
                           _Params, _Config, _generate_music)
    message = str(caught.value)
    assert 'CPU' in message
    assert 'slow' in message
    assert 'unsupported' in message


def test_worker_cpu_initialization_exception_is_wrapped(monkeypatch):
    import ace_worker

    monkeypatch.setenv('ACE_DEVICE', 'cpu')

    class Handler:
        def initialize_service(self, **kwargs):
            raise RuntimeError('Found no NVIDIA driver')

    with tempfile.TemporaryDirectory() as directory:
        with pytest.raises(RuntimeError) as caught:
            ace_worker.run(_worker_job(directory), Handler, lambda: None,
                           _Params, _Config, _generate_music)
    assert 'CPU' in str(caught.value)


# -- hardware panel -------------------------------------------------------

def test_hardware_reports_apple_silicon_mps_on_arm64_mac(monkeypatch):
    monkeypatch.setattr(hardware.sys, 'platform', 'darwin')
    monkeypatch.setattr(hardware.platform, 'machine', lambda: 'arm64')

    def never(args):
        raise AssertionError('nvidia-smi must not run on an Apple Silicon Mac')

    monkeypatch.setattr(hardware, '_run', never)
    info = hardware.hardware_info()

    assert info['available'] is True
    assert info['error'] is None
    assert 'Apple Silicon' in info['devices'][0]['name']
    assert 'MPS' in info['devices'][0]['name']


def test_hardware_reports_no_nvidia_gpu_instead_of_a_tool_error(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(hardware.subprocess, 'run', missing)
    info = hardware.hardware_info()

    assert info['available'] is False
    assert info['error'] == 'no NVIDIA GPU found'


def test_hardware_treats_a_failed_nvidia_smi_as_no_gpu(monkeypatch):
    class Result:
        returncode = 1
        stdout = ''
        stderr = 'NVIDIA-SMI has failed'

    monkeypatch.setattr(hardware.subprocess, 'run', lambda *a, **k: Result())
    info = hardware.hardware_info()

    assert info['error'] == 'no NVIDIA GPU found'


# -- ffmpeg discovery -----------------------------------------------------

def test_ffmpeg_env_override_wins_over_path(monkeypatch):
    monkeypatch.setenv('HEWN_FFMPEG', '/opt/tools/ffmpeg')
    monkeypatch.setattr(mm3.shutil, 'which', lambda name: '/usr/bin/ffmpeg')

    engine = mm3.MiniMaxM3ComfyEngine(base_url='http://127.0.0.1:1', ffmpeg=None)
    assert engine.ffmpeg == '/opt/tools/ffmpeg'


def test_ffmpeg_falls_back_to_the_winget_install_dir_on_windows(monkeypatch):
    monkeypatch.delenv('HEWN_FFMPEG', raising=False)
    monkeypatch.setattr(mm3.os, 'name', 'nt')
    monkeypatch.setenv('LOCALAPPDATA', r'C:\Users\me\AppData\Local')
    monkeypatch.setenv('ProgramFiles', r'C:\Program Files')
    monkeypatch.setattr(mm3.shutil, 'which', lambda name: None)
    expected = r'C:\Users\me\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe'
    monkeypatch.setattr(mm3.os.path, 'isfile', lambda path: path == expected)

    assert mm3._find_ffmpeg() == expected


def test_ffmpeg_falls_back_to_homebrew_on_macos(monkeypatch):
    monkeypatch.delenv('HEWN_FFMPEG', raising=False)
    monkeypatch.setattr(mm3.sys, 'platform', 'darwin')
    monkeypatch.setattr(mm3.shutil, 'which', lambda name: None)
    expected = '/opt/homebrew/bin/ffmpeg'
    monkeypatch.setattr(mm3.os.path, 'isfile', lambda path: path == expected)

    assert mm3._find_ffmpeg() == expected


def test_missing_ffmpeg_error_says_how_to_install():
    engine = mm3.MiniMaxM3ComfyEngine(base_url='http://127.0.0.1:1', ffmpeg='')
    with pytest.raises(mm3.EngineError) as caught:
        engine._to_wav(b'not audio', 'flac')
    message = str(caught.value)
    assert 'winget install ffmpeg' in message
    assert 'brew install ffmpeg' in message
    assert 'HEWN_FFMPEG' in message


def test_missing_ffmpeg_health_says_how_to_install(monkeypatch):
    engine = mm3.MiniMaxM3ComfyEngine(base_url='http://127.0.0.1:1', ffmpeg='')
    monkeypatch.setattr(engine, '_gpu_lease_conflict', lambda: None)

    def get(path, **kwargs):
        if path == '/system_stats':
            return {'devices': [], 'system': {}}
        node = path.rsplit('/', 1)[-1]
        if node == 'UNETLoader':
            return {node: {'input': {'required': {
                'unet_name': [[mm3.MODEL_FILES['unet']]]}}}}
        if node == 'CLIPLoader':
            return {node: {'input': {'required': {
                'clip_name': [[mm3.MODEL_FILES['clip']]],
                'type': [['minimax']]}}}}
        if node == 'VAELoader':
            return {node: {'input': {'required': {
                'vae_name': [[mm3.MODEL_FILES['vae']]]}}}}
        if node == 'SaveAudioAdvanced':
            return {node: {'input': {'required': {'format': [['flac']]}}}}
        return {node: {}}

    monkeypatch.setattr(engine, '_get', get)
    status = engine.health()

    assert status.state == 'degraded'
    assert 'winget install ffmpeg' in status.detail
    assert 'brew install ffmpeg' in status.detail
    assert 'HEWN_FFMPEG' in status.detail
