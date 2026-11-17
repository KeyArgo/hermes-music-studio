"""Download the pinned ACE-Step files into a local ComfyUI models folder."""
import hashlib
import os
import shutil
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import setup

BASE_URL = 'https://huggingface.co/Comfy-Org/ACE-Step-1.5/resolve/371bb1680f8c88d8c1083f6ffb6330d3d55bff76'
ACE15_FILES = [
    {'path': 'split_files/diffusion_models/acestep_v1.5_turbo.safetensors', 'folder': 'diffusion_models', 'size': 4787825604, 'sha256': '3f6e0797fad420a39bd33979eb6e840e30989e34a3794e843d23b60ec6e422d7'},
    {'path': 'split_files/text_encoders/qwen_0.6b_ace15.safetensors', 'folder': 'text_encoders', 'size': 1191588248, 'sha256': 'fd4590c82153b8ddb67e15a2e7aaa8afa8b83a858c8a9b82a4831063156aa7a7'},
    {'path': 'split_files/text_encoders/qwen_4b_ace15.safetensors', 'folder': 'text_encoders', 'size': 8379154232, 'sha256': 'ffe5ffb855086c2ab55e467e9859fb01894781020a0376484dd19de166b79873'},
    {'path': 'split_files/vae/ace_1.5_vae.safetensors', 'folder': 'vae', 'size': 337431732, 'sha256': '6de92e3a862acd287e08b024ac90f0783a8635451b728721a33ff03565bcb2bb'},
]
_MARGIN = 64 * 1024 * 1024
_CHUNK = 256 * 1024
_lock = threading.Lock()
_jobs = {}
_running = None


def look_local():
    found = []
    for port in (8000, 8188):
        url = f'http://127.0.0.1:{port}'
        try:
            request = urllib.request.Request(url + '/system_stats', headers={'User-Agent': 'hermes-music-studio'})
            with urllib.request.urlopen(request, timeout=1) as response:
                ok = response.status == 200
        except (OSError, ValueError):
            ok = False
        found.append({'url': url, 'ok': ok})
    return {'found': found, 'ports_checked': [8000, 8188]}


def suggest_models_dir():
    candidate = setup.find_local_comfy_models()
    if candidate and Path(candidate).is_dir():
        return str(candidate)
    candidate = Path.home() / 'Documents' / 'ComfyUI' / 'models'
    return str(candidate) if candidate.is_dir() else None


def plan(models_dir):
    if not isinstance(models_dir, str) or not models_dir.strip():
        raise ValueError('Choose the folder ComfyUI calls models.')
    path = Path(models_dir)
    if not path.is_dir() or not (path.name.lower() == 'models' or
                                 (path / 'diffusion_models').is_dir() or
                                 (path / 'checkpoints').is_dir()):
        return {'ok': False, 'message': 'Choose the folder ComfyUI calls models.'}
    files = []
    for item in ACE15_FILES:
        name = Path(item['path']).name
        target = path / item['folder'] / name
        present = target.is_file() and target.stat().st_size == item['size']
        files.append({'name': name, 'folder': item['folder'], 'size': item['size'], 'present': present})
    need = _MARGIN
    for item in files:
        if not item['present']:
            part = path / item['folder'] / (item['name'] + '.part')
            partial = part.stat().st_size if part.is_file() else 0
            need += item['size'] - (partial if partial <= item['size'] else 0)
    parent = path
    while not parent.exists():
        parent = parent.parent
    free = shutil.disk_usage(parent)[2]
    enough = free >= need
    return {'ok': True, 'models_dir': str(path), 'files': files, 'need_bytes': need,
            'free_bytes': free, 'enough_space': enough, 'gpu': 'unknown',
            'message': 'Ready to download.' if enough else 'There is not enough free space.'}


def status(job_id):
    with _lock:
        if job_id not in _jobs:
            raise KeyError('Download not found.')
        return dict(_jobs[job_id]['status'])


def cancel(job_id):
    with _lock:
        if job_id not in _jobs:
            raise KeyError('Download not found.')
        _jobs[job_id]['cancel'].set()
        if _jobs[job_id]['status']['state'] == 'running':
            _jobs[job_id]['status']['state'] = 'cancelling'
        return dict(_jobs[job_id]['status'])


def start(models_dir):
    global _running
    if not isinstance(models_dir, str) or not models_dir.strip():
        raise ValueError('Choose the folder ComfyUI calls models.')
    root = Path(models_dir).resolve()
    while True:
        with _lock:
            if _running is not None:
                job = _jobs[_running]
                if not job['cancel'].is_set():
                    if job['models_dir'] != os.path.normcase(str(root)):
                        raise ValueError(f"Another download is already running for {job['display_dir']}.")
                    return {'job_id': _running}
                thread = job['thread']
            else:
                details = plan(str(root))
                if not details['ok'] or not details['enough_space']:
                    raise ValueError(details['message'])
                job_id = uuid.uuid4().hex
                state = {'state': 'running', 'file': None, 'done_bytes': 0,
                         'total_bytes': sum(item['size'] for item in details['files']),
                         'files_done': 0, 'files_total': len(details['files']),
                         'error': None, 'message': 'Starting download.'}
                thread = threading.Thread(target=_download, args=(job_id, root),
                                          name='ms-local-download', daemon=True)
                _jobs[job_id] = {'status': state, 'cancel': threading.Event(),
                                 'models_dir': os.path.normcase(str(root)),
                                 'display_dir': str(root), 'thread': thread}
                _running = job_id
                thread.start()
                return {'job_id': job_id}
        thread.join(timeout=5)
        if thread.is_alive():
            raise ValueError('The previous download is still cancelling. Please try again.')


def _download(job_id, root):
    global _running
    job = _jobs[job_id]
    state, stop = job['status'], job['cancel']

    def update(**values):
        with _lock:
            state.update(values)

    try:
        for item in ACE15_FILES:
            name = Path(item['path']).name
            final = root / item['folder'] / name
            part = final.with_name(final.name + '.part')
            update(file=name, message='Downloading ' + name)
            if stop.is_set():
                raise InterruptedError()
            if final.exists():
                if not final.is_file() or final.stat().st_size != item['size']:
                    raise ValueError(f'{name} already exists with a different size.')
                digest = hashlib.sha256()
                with final.open('rb') as existing:
                    while True:
                        if stop.is_set():
                            raise InterruptedError()
                        chunk = existing.read(_CHUNK)
                        if not chunk:
                            break
                        digest.update(chunk)
                if digest.hexdigest() != item['sha256']:
                    raise ValueError(f'{name} is already in the folder but is damaged. Delete it and press Resume.')
                with _lock:
                    state['done_bytes'] += item['size']
                    state['files_done'] += 1
                continue
            final.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            offset = part.stat().st_size if part.exists() else 0
            if offset > item['size']:
                part.unlink()
                offset = 0
            if offset:
                with part.open('rb') as existing:
                    while True:
                        chunk = existing.read(_CHUNK)
                        if not chunk:
                            break
                        if stop.is_set():
                            raise InterruptedError()
                        digest.update(chunk)
            with _lock:
                state['done_bytes'] += offset
            if offset < item['size']:
                headers = {'User-Agent': 'hermes-music-studio'}
                if offset:
                    headers['Range'] = f'bytes={offset}-'
                url = BASE_URL.rstrip('/') + '/' + item['path']
                request = urllib.request.Request(url, headers=headers)
                response = urllib.request.urlopen(request, timeout=30)
                if offset and (response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')):
                    response.close()
                    with _lock:
                        state['done_bytes'] -= offset
                    offset = 0
                    digest = hashlib.sha256()
                    with part.open('wb'):
                        pass
                    request = urllib.request.Request(url, headers={'User-Agent': 'hermes-music-studio'})
                    response = urllib.request.urlopen(request, timeout=30)
                with response:
                    with part.open('ab' if offset else 'wb') as output:
                        while True:
                            if stop.is_set():
                                raise InterruptedError()
                            chunk = response.read(_CHUNK)
                            if not chunk:
                                break
                            output.write(chunk)
                            digest.update(chunk)
                            offset += len(chunk)
                            with _lock:
                                state['done_bytes'] += len(chunk)
                            if offset > item['size']:
                                break
            if stop.is_set():
                raise InterruptedError()
            if offset < item['size']:
                raise ValueError(f'{name}: connection dropped, press Resume')
            if offset > item['size']:
                raise ValueError(f'{name} exceeded the expected size. Please retry.')
            if digest.hexdigest() != item['sha256']:
                part.unlink(missing_ok=True)
                raise ValueError(f'{name} did not pass its size and checksum check. Please retry.')
            if final.exists():
                raise ValueError(f'{name} already exists; it was not replaced.')
            os.replace(part, final)
            with _lock:
                state['files_done'] += 1
        update(state='done', message='Download complete. Restart ComfyUI.')
    except InterruptedError:
        update(state='cancelled', message='Download cancelled. You can resume it later.')
    except Exception as exc:
        update(state='failed', error=str(exc), message='Download failed. ' + str(exc))
    finally:
        with _lock:
            if _running == job_id:
                _running = None
