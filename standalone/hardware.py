"""Read-only local GPU inventory for the hardware panel.

Every fact here comes from ``nvidia-smi`` query output. Nothing is inferred
from a filename, a config file or a guessed bus id: when a value is missing or
``nvidia-smi`` cannot answer within the deadline, it is reported as such and
the PID is left unmapped.

Only the two query commands are used (both read-only):
    --query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu
    --query-compute-apps=pid,process_name,used_memory,gpu_uuid
"""
from __future__ import annotations

import platform
import subprocess
import sys

TIMEOUT_SECONDS = 2.0
GPU_FIELDS = ('index', 'uuid', 'name', 'memory_total_mb', 'memory_used_mb',
              'utilization_percent')
APP_FIELDS = ('pid', 'process_name', 'used_memory_mb', 'gpu_uuid')
NO_NVIDIA_GPU = 'no NVIDIA GPU found'


def _run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=TIMEOUT_SECONDS, check=False), None
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


def _apple_silicon_info():
    """The one local accelerator a Mac has: the Apple Silicon GPU via MPS."""
    return {
        'available': True,
        'backend': 'mps',
        'devices': [{
            'index': 0,
            'uuid': 'apple-silicon-mps',
            'name': 'Apple Silicon (MPS)',
            'memory_total_mb': None,
            'memory_used_mb': None,
            'utilization_percent': None,
        }],
        'processes': [],
        'pid_to_gpu': {},
        'error': None,
    }


def hardware_info():
    """Local GPU devices plus the PIDs nvidia-smi attributes to each GPU."""
    if _apple_silicon():
        return _apple_silicon_info()
    devices, device_error = _devices()
    if device_error is not None:
        return {
            'available': False,
            'devices': [],
            'processes': [],
            'pid_to_gpu': {},
            'error': device_error,
        }
    processes, pid_to_gpu, process_error = _processes(devices)
    return {
        'available': True,
        'devices': devices,
        'processes': processes,
        'pid_to_gpu': pid_to_gpu,
        'error': process_error,
    }
