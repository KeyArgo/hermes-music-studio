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
import contextlib
import json
import logging
import os
import re
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
    from .network import open_url

    request = urllib.request.Request(url, headers=headers or {})
    with open_url(request, timeout) as response:
        body = response.read()
    return body


def _post(url, payload, headers=None, timeout=CHECK_TIMEOUT_S):
    from .network import open_url

    data = json.dumps(payload).encode()
    merged = {'Content-Type': 'application/json'}
    merged.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=merged, method='POST')
    with open_url(request, timeout) as response:
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
    from .network import _link_local
    if _link_local(parsed.hostname or ''):
        raise ValueError('the address must not be a link-local address')
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


# An ACE-Step server names itself in the JSON its root path returns. A ComfyUI, a proxy or a plain
# web server does not, which is the whole reason this probe reads the answer instead of the status.
_ACE_STEP_WORD = re.compile(r'\bace[-_ ]?step\b', re.IGNORECASE)


def _json_body(body):
    """True when a body is a JSON object or list."""
    try:
        return isinstance(json.loads(body), (dict, list))
    except Exception:
        return False


def _looks_like_ace(body):
    """True when a body is JSON that names ACE-Step; anything else is another kind of server."""
    try:
        payload = json.loads(body)
    except Exception:
        return False
    if not isinstance(payload, (dict, list)):
        return False
    return bool(_ACE_STEP_WORD.search(json.dumps(payload)))


def probe_ace(url):
    """Is this an ACE-Step server? Its /health answers 2xx, or / returns JSON naming ACE-Step.

    An HTTP error is *not* an answer about the software: a ComfyUI answers /health with 404 and a
    static web server answers 200 with a page. Both used to count as "an ACE server answers", and
    the page then saved that address as the ACE-Step engine — so the check is now the body, not the
    fact that a socket replied.
    """
    base = normalise_url(url)
    checks = []
    statuses = []
    for path in ('/health', '/'):
        try:
            body = _get(f'{base}{path}')
        except urllib.error.HTTPError as exc:
            if path == '/health' and exc.code in (401, 403):
                # An ACE-Step server behind a key refuses an unkeyed health check; a ComfyUI never
                # asks for one. The key is entered with the hosted fields and sent on every render.
                checks.append(_check(f'answers {path}', True, f'HTTP {exc.code}: needs its key'))
                return {'ok': True, 'url': base, 'checks': checks,
                        'detail': 'ACE server answers (it asks for a key)'}
            checks.append(_check(f'answers {path}', False, f'HTTP {exc.code}'))
            statuses.append(exc.code)
            continue
        except Exception as exc:
            checks.append(_check(f'answers {path}', False, exc))
            continue
        if path == '/health' and not _json_body(body):
            # ACE-Step's API answers /health in JSON. A page or plain text is some other server that
            # happens to answer every path, and saving it would only fail at the first render.
            checks.append(_check(f'answers {path}', False, 'answered, but not with a JSON health check'))
            continue
        if path == '/' and not _looks_like_ace(body):
            checks.append(_check(f'answers {path}', False, 'did not name ACE-Step'))
            continue
        checks.append(_check(f'answers {path}', True))
        return {'ok': True, 'url': base, 'checks': checks, 'detail': 'ACE server answers'}
    if statuses:
        return {'ok': False, 'url': base, 'checks': checks,
                'detail': f'{base} answers but is not an ACE-Step server: HTTP {statuses[0]}'}
    return {'ok': False, 'url': base, 'checks': checks,
            'detail': f'nothing answered at {base} as an ACE-Step server'}


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
    import os
    onedrive = os.environ.get('OneDrive')
    appdata = os.environ.get('APPDATA')
    if onedrive:
        candidates.append(Path(onedrive) / 'Documents' / 'ComfyUI' / 'models')
    if appdata:
        candidates.append(Path(appdata) / 'ComfyUI' / 'models')
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
    source = Path(folder).expanduser()
    try:
        source = source.resolve()
    except OSError as exc:
        raise ValueError(f'{folder} is not a folder') from exc
    allowed = _models_dir(data_dir).resolve()
    if source != allowed and allowed not in source.parents:
        raise ValueError('that folder is not a model this studio downloaded')
    if not source.is_dir():
        raise ValueError(f'{source} is not a folder')
    destination = comfy / 'diffusion_models' / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError(f'{destination} already exists; remove it first if you want a fresh copy')
    shutil.copytree(source, destination)
    return {'ok': True, 'copied_to': str(destination)}


# -- path 3: a hosted API ------------------------------------------------

# The hosted API is the remote ACE-Step engine. Its endpoint lives in the studio settings and its key
# lives in Hermes' credential store, so a render and the Setup panel read the same two things.
CLOUD_KEY_ENV = 'ACE_REMOTE_API_KEY'
# Only the one-time migration may name the old file. Nothing writes it, and no key is ever read from it
# after migration.
LEGACY_KEY_FILE = 'cloud-keys.json'
LEGACY_MESSAGE = 're-enter your hosted key in Setup'


def _stored_endpoint(data_dir):
    from . import settings

    saved = settings.read(Path(data_dir))
    entry = saved.get('ace_remote') if isinstance(saved.get('ace_remote'), dict) else {}
    return {'url': saved.get('ace_remote_url') or entry.get('url') or '',
            'model': entry.get('model') or '',
            'saved_at': entry.get('saved_at') or '',
            'key_env': entry.get('key_env') or CLOUD_KEY_ENV}


def _store_endpoint(data_dir, url, model):
    """Save the endpoint beside the other studio settings; the key is not part of it."""
    from . import settings

    base = normalise_url(url)
    # ``ace_remote_url`` is the one stored address. The nested ``url`` is cleared rather than
    # written so an old value read by a previous release can never win over the new setting.
    entry = {'url': '', 'model': (model or '').strip(),
             'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
             'key_env': CLOUD_KEY_ENV}
    settings.write(Path(data_dir), {'ace_remote': entry, 'ace_remote_url': base})
    return entry


def forget_endpoint(data_dir):
    """Forget the one stored server address, so nothing keeps pointing at a server that is gone.

    The address is a single setting, so removing the row on the Music engines page is clearing it.
    Nothing the server rendered is deleted, and the hosted key stays in Hermes: dropping a
    credential is never a side effect of removing an address.
    """
    from . import settings
    from .store import Store

    for project in Store(Path(data_dir)).all():
        for take in project.get('takes') or []:
            if (isinstance(take, dict) and take.get('state') in ('queued', 'running')
                    and take.get('engine_id') == 'ace_remote'):
                # Clearing the address now would fail that render halfway; the same rule the page
                # already applies to removing a saved ComfyUI engine.
                raise ValueError('This server is used by a song that is still being made.')
    # The nested ``url`` is cleared with it: an old value read by a previous release must not
    # outlive the address the page just removed.
    settings.write(Path(data_dir), {'ace_remote': {'url': '', 'model': '', 'saved_at': ''},
                                    'ace_remote_url': ''})
    # The launcher handed the saved address to this host as ACE_REMOTE_URL when it started, and the
    # engine falls back to that variable when the setting is blank, so the running host would keep
    # rendering on the removed server until Hermes restarts. Workers inherit it too. Drop it here.
    os.environ.pop('ACE_REMOTE_URL', None)
    return {'ok': True, 'ace_remote_url': ''}


@contextlib.contextmanager
def _cloud_key_guard(data_dir):
    """Serialise the legacy migration and the key write across processes."""
    from .network import _lock_ex, _lock_un

    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.cloud-key.lock').open('a') as lock:
        _lock_ex(lock)
        try:
            yield
        finally:
            _lock_un(lock)


def migrate_legacy_keys(data_dir):
    """Move a key from the old plaintext file into Hermes, once.

    Returns a message for the UI when the file cannot be migrated safely. The file is left untouched
    in that case: a key is only deleted after it has been read back from the credential store.
    """
    with _cloud_key_guard(data_dir):
        return _migrate_legacy_keys_unlocked(data_dir)


def _migrate_legacy_keys_unlocked(data_dir):
    path = Path(data_dir) / LEGACY_KEY_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return LEGACY_MESSAGE
    if not isinstance(data, dict):
        return LEGACY_MESSAGE
    keyed = [(name, entry) for name, entry in data.items()
             if isinstance(entry, dict) and str(entry.get('key') or '').strip()]
    # One hosted endpoint is supported. Several saved keys cannot be chosen between without guessing.
    if len(keyed) != 1:
        return LEGACY_MESSAGE
    _name, entry = keyed[0]
    legacy_key = str(entry['key']).strip()
    try:
        base = normalise_url(entry.get('url'))
    except Exception:
        return LEGACY_MESSAGE
    from .hermes_secrets import read_secret, save_secret

    existing = read_secret(CLOUD_KEY_ENV)
    if existing and existing != legacy_key:
        # Hermes already holds a different key. Which one is right is the owner's call, so neither is
        # discarded: the old file stays until they re-enter the key they want in Setup.
        return LEGACY_MESSAGE
    if not existing:
        try:
            saved = save_secret(CLOUD_KEY_ENV, legacy_key)
        except ValueError:
            return LEGACY_MESSAGE
        if not saved or read_secret(CLOUD_KEY_ENV) != legacy_key:
            return LEGACY_MESSAGE
    try:
        _store_endpoint(data_dir, base, entry.get('model') or '')
    except Exception:
        return LEGACY_MESSAGE
    try:
        path.unlink()
    except OSError:
        return LEGACY_MESSAGE
    logging.getLogger(__name__).info(
        'migrated the hosted API key from %s into Hermes credentials', LEGACY_KEY_FILE)
    return None


def _same_address(left, right):
    """One address written two ways (a trailing slash, a capitalised host) is still one address."""
    return (left or '').rstrip('/').lower() == (right or '').rstrip('/').lower()


def save_cloud(data_dir, name, url, key, model=''):
    """Save the hosted endpoint and put its key only in Hermes' credential store."""
    base = normalise_url(url)
    if not (key or '').strip():
        raise ValueError('an API key is required for a hosted endpoint')
    from .hermes_secrets import SAVE_FAILED, read_secret, save_secret

    with _cloud_key_guard(data_dir):
        # Migration and the owner's new key are one transaction: without the lock a concurrent
        # list_cloud could import the legacy key right after this one was saved.
        message = _migrate_legacy_keys_unlocked(data_dir)
        # The studio keeps one server address, shared with the ACE-Step server on the Music engines
        # page (a render reads the same setting), and a hosted save used to replace that server
        # without a word. A hosted save stamps ``saved_at``; saving an ACE-Step server, or Remove,
        # clears it. So a different address may be replaced only when a hosted save put it there; a key
        # left in Hermes after a Remove says nothing about what is in the slot now.
        stored = _stored_endpoint(data_dir)
        existing = stored['url']
        if existing and not _same_address(existing, base) and not stored['saved_at']:
            raise ValueError(f'{existing} is already saved as the studio\'s ACE-Step server address. '
                             'Remove it on the Music engines page, then save this one.')
        if not save_secret(CLOUD_KEY_ENV, key.strip()):
            raise ValueError(SAVE_FAILED)
        entry = _store_endpoint(data_dir, base, model)
        legacy = Path(data_dir) / LEGACY_KEY_FILE
        if message and legacy.is_file():
            # The owner just entered the key they want, and Hermes has confirmed it: the old plaintext
            # copy that migration could not resolve is no longer needed.
            try:
                legacy.unlink()
                message = None
            except OSError:
                pass
    result = {'ok': True, 'name': name or 'default', 'url': base, 'has_key': True}
    if message:
        result['message'] = message
    return result


def list_cloud(data_dir):
    """The one hosted endpoint, never its key — only whether Hermes holds one."""
    migrate_legacy_keys(data_dir)
    entry = _stored_endpoint(data_dir)
    if not entry['url']:
        return []
    from .hermes_secrets import read_secret

    return [{'name': 'default', 'url': entry['url'], 'model': entry['model'],
             'has_key': bool(read_secret(CLOUD_KEY_ENV))}]


def probe_cloud(data_dir, name='default'):
    """One small authenticated request: does this endpoint answer with the key Hermes holds?"""
    message = migrate_legacy_keys(data_dir)
    entry = _stored_endpoint(data_dir)
    if not entry['url']:
        raise ValueError('no hosted endpoint has been saved yet')
    from .hermes_secrets import read_secret

    key = read_secret(CLOUD_KEY_ENV)
    if not key:
        raise ValueError('the hosted endpoint has no key in Hermes; enter it in Setup')
    url = entry['url']
    headers = {'Authorization': f'Bearer {key}'}
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
        result = {'ok': True, 'url': url, 'checks': checks,
                  'detail': 'the endpoint accepted the key'}
    except urllib.error.HTTPError as exc:
        detail = 'the key was refused' if exc.code in (401, 403) else f'HTTP {exc.code}'
        result = {'ok': False, 'url': url,
                  'checks': [_check('endpoint answers', False, detail)],
                  'detail': f'{url} refused the request: {detail}'}
    except Exception as exc:
        result = {'ok': False, 'url': url,
                  'checks': [_check('endpoint answers', False, exc)],
                  'detail': f'nothing answered at {url}: {exc}'}
    if message:
        result['message'] = message
    return result
