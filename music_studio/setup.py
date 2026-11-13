"""Getting an engine onto a machine, from inside the studio.

Three paths, in the order people actually meet them:

1. **A server that is already running** — point at any ComfyUI (MiniMax Music 3) or ACE-Step server
   and prove it answers before saving it.
2. **A model from Hugging Face** — download the weights here and say exactly where to put them, or
   copy them into a local ComfyUI install when we can find one. A remote ComfyUI cannot be written
   from this machine, and pretending otherwise is how a "one click" install becomes a support thread.
3. **A hosted API** — an endpoint and a key, tested with one small request. The key is stored in its
   own file with 0600 permissions and never echoed back to the page.

Nothing here renders audio. Every function answers `{ok, checks, detail}` in the same shape the
Compute screen already renders for `/settings/test`, so a failure names the check that failed.
"""
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CHECK_TIMEOUT_S = 8
DOWNLOAD_DIRNAME = 'models'


# -- shared ---------------------------------------------------------------

def _check(name, ok, detail=''):
    return {'name': name, 'ok': bool(ok), 'detail': str(detail)[:400]}


def _get(url, headers=None, timeout=CHECK_TIMEOUT_S):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return body


def _post(url, payload, headers=None, timeout=CHECK_TIMEOUT_S):
    data = json.dumps(payload).encode()
    merged = {'Content-Type': 'application/json'}
    merged.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=merged, method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def normalise_url(raw):
    """A base URL, or ValueError. Only http(s), no credentials, no query."""
    text = (raw or '').strip().rstrip('/')
    if not text:
        raise ValueError('a server address is required, for example http://192.168.1.20:8188')
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError('the address must start with http:// or https://')
    if parsed.username or parsed.password:
        raise ValueError('do not put credentials in the address')
    if parsed.query or parsed.fragment:
        raise ValueError('the address cannot carry a query or a fragment')
    return text


# -- path 1: a server that already runs -----------------------------------

def probe_comfy(url):
    """Is this a ComfyUI that can render MiniMax Music 3?"""
    base = normalise_url(url)
    checks = []
    try:
        stats = json.loads(_get(f'{base}/system_stats'))
        checks.append(_check('server answers', True,
                             (stats.get('system') or {}).get('comfyui_version', 'ComfyUI')))
    except Exception as exc:
        return {'ok': False, 'url': base,
                'checks': [_check('server answers', False, exc)],
                'detail': f'nothing answered at {base}: {exc}'}

    try:
        info = json.loads(_get(f'{base}/object_info'))
        checks.append(_check('node list', True, f'{len(info)} nodes'))
    except Exception as exc:
        return {'ok': False, 'url': base, 'checks': checks,
                'detail': f'the server answered but would not list its nodes: {exc}'}

    # The node names come from the engine that builds the workflow, not from memory: a probe with
    # invented names fails on a perfectly good server (and passes on nothing).
    from music_studio.engines.minimax_m3_comfy import MINIMAX_NODES

    needed = list(MINIMAX_NODES)
    missing = [name for name in needed if name not in info]
    checks.append(_check('MiniMax Music 3 nodes', not missing,
                         'all present' if not missing else 'missing: ' + ', '.join(missing)))
    ok = not missing
    return {'ok': ok, 'url': base, 'checks': checks,
            'detail': ('ready to render Music 3' if ok else
                       'this ComfyUI has no MiniMax Music 3 nodes; install the MiniMax node pack '
                       'on that machine, then test again')}


def probe_ace(url):
    """Is this an ACE-Step server? It answers /health or returns audio on /query_result."""
    base = normalise_url(url)
    checks = []
    for path in ('/health', '/'):
        try:
            _get(f'{base}{path}')
            checks.append(_check(f'answers {path}', True))
            return {'ok': True, 'url': base, 'checks': checks, 'detail': 'ACE server answers'}
        except urllib.error.HTTPError as exc:
            checks.append(_check(f'answers {path}', True, f'HTTP {exc.code}'))
            return {'ok': True, 'url': base, 'checks': checks,
                    'detail': 'ACE server answers (it refused that path, which still means it is up)'}
        except Exception as exc:
            checks.append(_check(f'answers {path}', False, exc))
    return {'ok': False, 'url': base, 'checks': checks,
            'detail': f'nothing answered at {base}'}


# -- path 2: a model from Hugging Face ------------------------------------

def _models_dir(data_dir):
    """Where downloads land: the library's models folder, beside the projects it serves."""
    path = Path(data_dir) / DOWNLOAD_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_local_comfy_models():
    """A local ComfyUI's models folder, if one is installed where people usually put it."""
    candidates = [
        Path.home() / 'ComfyUI' / 'models',
        Path.home() / 'comfy' / 'ComfyUI' / 'models',
        Path('/opt/ComfyUI/models'),
        Path.home() / 'Documents' / 'ComfyUI' / 'models',
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def hf_plan(model_id, data_dir):
    """What a download would do, before doing it — so the page can show the truth first."""
    model_id = (model_id or '').strip()
    if model_id.count('/') != 1 or ' ' in model_id:
        raise ValueError('a model id looks like MiniMaxAI/MiniMax-Music3, not a URL')
    target = _models_dir(data_dir)
    return {
        'model_id': model_id,
        'download_to': str(target),
        'local_comfy': str(find_local_comfy_models()) if find_local_comfy_models() else None,
        'size_hint': ('weights are often several GB; the download resumes and is written beside '
                      'the target so a half-file never looks finished'),
        'where_it_goes': ('a local ComfyUI: <comfy>/models/diffusion_models/ '
                          '(or the folder the model card names)'),
    }


_state = {'running': False, 'model_id': None, 'detail': 'idle', 'started': None, 'done': None}
_state_lock = threading.Lock()


def hf_status():
    with _state_lock:
        return dict(_state)


def hf_download(model_id, data_dir, revision=None, allow_patterns=None):
    """Start a download in the background; the page polls hf_status().

    Returns immediately. A download that dies leaves `detail` explaining it — never a silent stop.
    """
    plan = hf_plan(model_id, data_dir)
    with _state_lock:
        if _state['running']:
            raise ValueError(f"a download is already running ({_state['model_id']}); wait for it")
        _state.update({'running': True, 'model_id': plan['model_id'], 'detail': 'starting',
                       'started': time.time(), 'done': None})

    def work():
        try:
            from huggingface_hub import snapshot_download
        except Exception as exc:  # pragma: no cover - dependency present on this machine
            with _state_lock:
                _state.update({'running': False, 'detail': f'huggingface_hub is not installed: {exc}'})
            return
        try:
            with _state_lock:
                _state['detail'] = 'downloading'
            path = snapshot_download(
                repo_id=plan['model_id'], revision=revision,
                local_dir=str(Path(plan['download_to']) / plan['model_id'].replace('/', '__')),
                allow_patterns=allow_patterns or None, resume_download=True)
            with _state_lock:
                _state.update({'running': False, 'done': path,
                               'detail': f'downloaded to {path}'})
        except Exception as exc:
            with _state_lock:
                _state.update({'running': False, 'detail': f'download failed: {exc}'})
    threading.Thread(target=work, name='ms-hf-download', daemon=True).start()
    return {'ok': True, **plan, 'status': hf_status()}


def copy_into_comfy(folder, data_dir):
    """Copy a downloaded model tree into a local ComfyUI's models folder, if there is one."""
    comfy = find_local_comfy_models()
    if comfy is None:
        raise ValueError('no local ComfyUI was found; copy the folder named above into that '
                         "machine's models folder yourself, or point the studio at a server")
    source = Path(folder)
    if not source.is_dir():
        raise ValueError(f'{source} is not a folder')
    destination = comfy / 'diffusion_models' / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError(f'{destination} already exists; remove it first if you want a fresh copy')
    shutil.copytree(source, destination)
    return {'ok': True, 'copied_to': str(destination)}


# -- path 3: a hosted API ------------------------------------------------

KEY_FILE = 'cloud-keys.json'


def _key_path(data_dir):
    return Path(data_dir) / KEY_FILE


def _write_private(path: Path, text: str) -> None:
    """Write a small secret file with owner-only permissions from the first byte.

    Review item 6: `write_text` then `chmod` leaves a window where the key is readable by anyone, and a
    crash inside that window leaves it readable for good.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except OSError:  # pragma: no cover - Windows ignores the permission bits
        Path(path).write_text(text)
        return
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(text)


def save_cloud(data_dir, name, url, key, model=''):
    """Store a hosted endpoint and its key. The key never leaves this file for the page."""
    base = normalise_url(url)
    if not (key or '').strip():
        raise ValueError('an API key is required for a hosted endpoint')
    path = _key_path(data_dir)
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
    data[name or 'default'] = {'url': base, 'key': key.strip(), 'model': (model or '').strip(),
                               'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    # Review item 6: create the file 0600 in the first place. Writing it world-readable and chmod-ing
    # afterwards leaves a window (and a crash leaves the window open for good).
    _write_private(path, json.dumps(data, indent=2))
    return {'ok': True, 'name': name or 'default', 'url': base, 'has_key': True}


def list_cloud(data_dir):
    path = _key_path(data_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    return [{'name': name, 'url': entry.get('url'), 'model': entry.get('model'),
             'has_key': bool(entry.get('key'))} for name, entry in data.items()]


def probe_cloud(data_dir, name='default'):
    """One small authenticated request: does this endpoint answer with the key we hold?"""
    path = _key_path(data_dir)
    if not path.is_file():
        raise ValueError('no hosted endpoint has been saved yet')
    entry = json.loads(path.read_text()).get(name)
    if not entry:
        raise ValueError(f'no hosted endpoint called {name}')
    url = entry['url']
    headers = {'Authorization': f"Bearer {entry['key']}"}
    checks = []
    endpoint = url + ('/models' if not url.endswith('/models') else '')
    try:
        body = _get(endpoint, headers=headers)
        checks.append(_check('endpoint answers', True))
        try:
            listing = json.loads(body)
            names = [m.get('id') for m in listing.get('data', [])][:8]
            checks.append(_check('model list', bool(names), ', '.join(names) or 'empty list'))
        except Exception:
            checks.append(_check('model list', True, 'answered, not a model listing'))
        return {'ok': True, 'url': url, 'checks': checks,
                'detail': 'the endpoint accepted the key'}
    except urllib.error.HTTPError as exc:
        detail = 'the key was refused' if exc.code in (401, 403) else f'HTTP {exc.code}'
        return {'ok': False, 'url': url,
                'checks': [_check('endpoint answers', False, detail)],
                'detail': f'{url} refused the request: {detail}'}
    except Exception as exc:
        return {'ok': False, 'url': url,
                'checks': [_check('endpoint answers', False, exc)],
                'detail': f'nothing answered at {url}: {exc}'}
