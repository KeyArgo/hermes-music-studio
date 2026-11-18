import json
import subprocess
import sys
from types import SimpleNamespace

import ace_worker
from music_studio.childenv import minimal_env
from standalone import hardware


def test_rocm_inventory_and_fallback(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == 'nvidia-smi':
            raise FileNotFoundError()
        # rocm-smi --showproductname --showmeminfo vram --json
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            'card0': {'Card series': 'AMD Radeon RX 7900 XTX',
                      'VRAM Total Memory (B)': '25769803776',
                      'VRAM Total Used Memory (B)': '1073741824'}
        }))
    monkeypatch.setattr(hardware.subprocess, 'run', run)
    monkeypatch.setattr(hardware, '_apple_silicon', lambda: False)
    info = hardware.hardware_info()
    assert info['backend'] == 'rocm'
    assert info['devices'][0]['name'] == 'AMD Radeon RX 7900 XTX'
    assert info['devices'][0]['memory_total_mb'] == 24576
    assert info['devices'][0]['memory_used_mb'] == 1024
    assert calls[1][0] == ['rocm-smi', '--showproductname', '--showmeminfo', 'vram', '--json']
    assert all(c[1]['timeout'] == hardware.TIMEOUT_SECONDS and 'env' in c[1] for c in calls)


def test_apple_unified_memory(monkeypatch):
    def run(args, **kwargs):
        if args[0] != 'sysctl':
            raise FileNotFoundError()
        return SimpleNamespace(returncode=0, stdout='17179869184\n')
    monkeypatch.setattr(hardware.subprocess, 'run', run)
    monkeypatch.setattr(hardware, '_apple_silicon', lambda: True)
    info = hardware.hardware_info()
    assert info['backend'] == 'mps'
    assert info['devices'][0]['memory_total_mb'] == 16384


def test_cpu_fallback_when_tools_fail(monkeypatch):
    def run(args, **kwargs):
        raise FileNotFoundError(args[0])
    monkeypatch.setattr(hardware.subprocess, 'run', run)
    monkeypatch.setattr(hardware, '_apple_silicon', lambda: False)
    info = hardware.hardware_info()
    assert info['backend'] == 'cpu'
    assert not info['available']
    assert info['error'] == ('No supported GPU found. Rendering on this computer will be slow or may not work; '
                             'a ComfyUI or ACE-Step server on another computer works fine.')


def test_cuda_backend(monkeypatch):
    def run(args, **kwargs):
        output = '0, GPU-123, NVIDIA RTX, 24576, 42, 5\n' if '--query-gpu=' in args[1] else ''
        return SimpleNamespace(returncode=0, stdout=output)
    monkeypatch.setattr(hardware.subprocess, 'run', run)
    assert hardware.hardware_info()['backend'] == 'cuda'


def test_gpu_environment_allowlist_and_secret_guard():
    names = ('HIP_VISIBLE_DEVICES ROCR_VISIBLE_DEVICES HSA_OVERRIDE_GFX_VERSION ROCM_PATH ROCM_HOME '
             'PYTORCH_HIP_ALLOC_CONF ONEAPI_ROOT ZE_AFFINITY_MASK SYCL_DEVICE_FILTER ONEAPI_DEVICE_SELECTOR '
             'PYTORCH_ENABLE_MPS_FALLBACK PYTORCH_MPS_HIGH_WATERMARK_RATIO').split()
    source = {name: 'value' for name in names}
    source.update({'HIP_API_KEY': 'secret', 'PYTORCH_SECRET': 'secret'})
    assert minimal_env(source=source) == {name: 'value' for name in names}


def test_xpu_device_and_override(monkeypatch):
    fake = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False),
                           backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
                           xpu=SimpleNamespace(is_available=lambda: True))
    monkeypatch.setitem(sys.modules, 'torch', fake)
    monkeypatch.delenv('ACE_DEVICE', raising=False)
    assert ace_worker._device() == 'xpu'
    monkeypatch.setenv('ACE_DEVICE', ' CPU ')
    assert ace_worker._device() == 'cpu'


def test_rocm_names_are_read_whatever_the_capitalisation(monkeypatch):
    sample = {'card0': {'Card Series': 'Navi 31 [Radeon RX 7900 XTX]', 'Card Model': '0x744c',
                        'VRAM Total Memory (B)': '25753026560', 'VRAM Total Used Memory (B)': '1073741824'}}

    def fake_run(args):
        if args[0] == 'rocm-smi':
            return subprocess.CompletedProcess(args, 0, json.dumps(sample), ''), None
        return None, hardware.NO_NVIDIA_GPU
    monkeypatch.setattr(hardware, '_run', fake_run)
    info = hardware.hardware_info()
    assert info['backend'] == 'rocm'
    assert info['devices'][0]['name'] == 'Navi 31 [Radeon RX 7900 XTX]'
    assert info['devices'][0]['memory_total_mb'] == 24560


def test_a_busy_nvidia_card_is_not_reported_as_no_gpu(monkeypatch):
    def fake_run(args):
        if args[0] == 'nvidia-smi':
            return None, 'nvidia-smi timed out after 5 s'
        raise AssertionError('no other tool should be asked')
    monkeypatch.setattr(hardware, '_run', fake_run)
    info = hardware.hardware_info()
    assert info['backend'] == 'cuda'
    assert 'timed out' in info['error']
