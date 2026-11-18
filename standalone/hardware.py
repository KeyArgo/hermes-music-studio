"""Read-only local GPU inventory for the hardware panel.

Facts come from the vendor's own read-only query: ``nvidia-smi`` (NVIDIA), ``rocm-smi --json``
(AMD) or ``sysctl hw.memsize`` (Apple Silicon, unified memory). Nothing is guessed: a value the
tool does not report stays ``None``, and with no tool at all the backend is ``cpu``.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys

from music_studio.childenv import minimal_env

TIMEOUT_SECONDS = 2.0
GPU_FIELDS = ('index', 'uuid', 'name', 'memory_total_mb', 'memory_used_mb',
              'utilization_percent')
APP_FIELDS = ('pid', 'process_name', 'used_memory_mb', 'gpu_uuid')
NO_NVIDIA_GPU = 'no NVIDIA GPU found'
NO_GPU = ('No supported GPU found. Rendering on this computer will be slow or may not work; '
          'a ComfyUI or ACE-Step server on another computer works fine.')


def _run(args):
    try:
        # nvidia-smi has no use for a provider key (review item 4): allowlisted env only.
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=TIMEOUT_SECONDS, check=False,
                              env=minimal_env()), None
    except FileNotFoundError:
        return None, NO_NVIDIA_GPU
    except subprocess.TimeoutExpired:
        return None, f'nvidia-smi timed out after {TIMEOUT_SECONDS:g} s'
    except OSError:
        return None, NO_NVIDIA_GPU


def _number(value):
    text = value.strip()
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def _lines(output):
    return [line.strip() for line in (output or '').splitlines() if line.strip()]


def _devices():
    code, error = _run(['nvidia-smi',
                        '--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu',
                        '--format=csv,noheader,nounits'])
    if code is None:
        return [], error
    if code.returncode != 0:
        return [], NO_NVIDIA_GPU
    devices = []
    for line in _lines(code.stdout):
        try:
            # GPU names may contain commas; the three numeric/uuid fields after
            # the name are fixed.
            head, total, used, utilization = line.rsplit(',', 3)
            index_text, uuid, name = head.split(',', 2)
        except ValueError:
            continue
        devices.append({
            'index': _number(index_text.strip()),
            'uuid': uuid.strip(),
            'name': name.strip(),
            'memory_total_mb': _number(total),
            'memory_used_mb': _number(used),
            'utilization_percent': _number(utilization),
        })
    return devices, None


def _processes(devices):
    code, error = _run(['nvidia-smi',
                        '--query-compute-apps=pid,process_name,used_memory,gpu_uuid',
                        '--format=csv,noheader,nounits'])
    if code is None:
        return [], {}, error
    if code.returncode != 0:
        return [], {}, NO_NVIDIA_GPU
    by_uuid = {d['uuid']: d['index'] for d in devices if d.get('uuid')}
    processes = []
    pid_to_gpu = {}
    for line in _lines(code.stdout):
        # process_name may contain commas; the last two fields are fixed.
        try:
            head, used_memory, gpu_uuid = line.rsplit(',', 2)
            pid_text, process_name = head.split(',', 1)
        except ValueError:
            continue
        pid = _number(pid_text.strip())
        if pid is None:
            continue
        gpu_uuid = gpu_uuid.strip()
        gpu_index = by_uuid.get(gpu_uuid)
        processes.append({
            'pid': pid,
            'process_name': process_name.strip(),
            'used_memory_mb': _number(used_memory),
            'gpu_uuid': gpu_uuid,
            'gpu_index': gpu_index,
        })
        pid_to_gpu[str(pid)] = gpu_index
    return processes, pid_to_gpu, None


def _apple_silicon():
    """True on an Apple Silicon Mac, where there is no nvidia-smi to query."""
    return ((sys.platform == 'darwin' or platform.system() == 'Darwin')
            and platform.machine() == 'arm64')


def _mac_memory_bytes():
    """Unified memory the MPS GPU shares, from ``sysctl -n hw.memsize``; None when it will not say."""
    try:
        result = subprocess.run(['sysctl', '-n', 'hw.memsize'], capture_output=True, text=True,
                                timeout=TIMEOUT_SECONDS, check=False, env=minimal_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _number(result.stdout or '') if result.returncode == 0 else None


def _apple_silicon_info():
    """The one local accelerator a Mac has: the Apple Silicon GPU via MPS."""
    memory = _mac_memory_bytes()
    return {
        'available': True,
        'backend': 'mps',
        'devices': [{
            'index': 0,
            'uuid': 'apple-silicon-mps',
            'name': 'Apple Silicon (MPS)',
            'memory_total_mb': memory // (1024 * 1024) if memory is not None and memory >= 0 else None,
            'memory_used_mb': None,
            'utilization_percent': None,
        }],
        'processes': [],
        'pid_to_gpu': {},
        'error': None,
    }


def _rocm_devices():
    code, _ = _run(['rocm-smi', '--showproductname', '--showmeminfo', 'vram', '--json'])
    if code is None or code.returncode != 0:
        return []
    try:
        data = json.loads(code.stdout)
        if not isinstance(data, dict):
            return []
        devices = []
        for card, values in data.items():
            if not card.startswith('card') or not isinstance(values, dict):
                continue
            # rocm-smi spells these "Card Series" / "Card series" depending on the release.
            values = {str(key).lower(): value for key, value in values.items()}
            total = _number(str(values.get('vram total memory (b)', '')))
            used = _number(str(values.get('vram total used memory (b)', '')))
            devices.append({
                'index': _number(card[4:]),
                'uuid': None,
                'name': values.get('card series') or values.get('card model') or 'AMD GPU',
                'memory_total_mb': total // (1024 * 1024) if total is not None else None,
                'memory_used_mb': used // (1024 * 1024) if used is not None else None,
                'utilization_percent': None,
            })
        return devices
    except (ValueError, TypeError, AttributeError):
        return []


def hardware_info():
    """Local GPU devices and available process information."""
    if _apple_silicon():
        # A Mac has no nvidia-smi or rocm-smi to ask; MPS is its one accelerator.
        return _apple_silicon_info()
    devices, device_error = _devices()
    if devices and device_error is None:
        processes, pid_to_gpu, process_error = _processes(devices)
        return {
            'available': True, 'backend': 'cuda', 'devices': devices,
            'processes': processes, 'pid_to_gpu': pid_to_gpu, 'error': process_error,
        }
    if device_error is not None and device_error != NO_NVIDIA_GPU:
        # nvidia-smi exists but did not answer in time (a busy card): that is an NVIDIA machine.
        return {'available': False, 'backend': 'cuda', 'devices': [], 'processes': [],
                'pid_to_gpu': {}, 'error': device_error}
    devices = _rocm_devices()
    if devices:
        return {
            'available': True, 'backend': 'rocm', 'devices': devices,
            'processes': [], 'pid_to_gpu': {}, 'error': None,
        }
    return {
        'available': False, 'backend': 'cpu', 'devices': [],
        'processes': [], 'pid_to_gpu': {}, 'error': NO_GPU,
    }
