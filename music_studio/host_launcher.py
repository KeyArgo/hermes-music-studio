"""Start (or find) the loopback host that the desktop pane frames.

One host per data directory, recorded in ``studio-host.json`` next to the
library. A host that answers ``/api/status`` is reused; otherwise a new one is
spawned with the profile's plugin data directory and the pane's port. Nothing
here touches engines, GPUs or jobs: the host only serves the UI and the same
router the agent tool already uses.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess

from .procs import detach_kwargs
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import service

STATE_FILE = 'studio-host.json'
LOG_FILE = 'studio-host.log'
DEFAULT_PORT = 8775
PORT_SPAN = 50
START_TIMEOUT_S = 20.0

_lock = threading.Lock()
_spawned: list[subprocess.Popen] = []


def data_dir() -> Path:
    """The profile's plugin base — never the redirected library.

    Settings, the host state file and the log live here; the library pointer (moved by the person)
    is resolved by the host itself. Passing the redirected directory as HEWN_STUDIO_DATA_DIR made
    the pane read settings from the moved library while the agent read them from the profile, so
    the two could disagree about the engine and the export folder.
    """
    return Path(service.default_data_dir(service.PLUGIN_ID))


def _token_for(root: Path, state: dict | None, fresh: bool) -> str:
    """A per-launch token for the loopback host.

    Review item 3: the host served every ``/api/*`` route to any local process, and those routes can
    change the ACE runtime directory (which picks the Python that later runs), move the library, copy
    folders and spend model credits. The token is generated here, handed to the host in its
    environment, and returned to the pane so the framed page can send it back.
    """
    if not fresh and state and isinstance(state.get('token'), str) and state['token']:
        return state['token']
    return secrets.token_urlsafe(32)


def _is_windows() -> bool:
    return os.name == 'nt' or sys.platform.startswith('win')


def _is_darwin() -> bool:
    return sys.platform == 'darwin'


def _hermes_python() -> str | None:
    """The interpreter beside ``sys.executable`` — a venv's own ``python`` shim."""
    executable = getattr(sys, 'executable', '') or ''
    if not executable:
        return None
    return str(Path(executable).with_name('python.exe' if _is_windows() else 'python'))


def _candidate_command(candidate) -> list[str] | None:
    """A runnable command for one candidate, or None when it is not installed."""
    if isinstance(candidate, (list, tuple)):
        command = [str(part) for part in candidate]
        if not command:
            return None
        first = command[0]
        return command if os.path.exists(first) or shutil.which(first) else None
    if not candidate or not os.path.exists(candidate):
        return None
    return [str(candidate)]


def _dedupe_key(command: list[str]) -> tuple:
    return tuple(os.path.normcase(os.path.abspath(part)) if os.path.exists(part) else part
                 for part in command)


def _python() -> list[str]:
    """An interpreter that can import uvicorn (the Hermes runtime need not have it).

    Returns a command list, because the Windows launcher is ``[py, -3]`` rather than a path.
    Probed with a cleaned environment: a ``PYTHONPATH``/``VIRTUAL_ENV`` inherited
    from the gateway points at the Hermes venv's site-packages (a different minor
    version), and a system interpreter honouring it dies importing pydantic.
    """
    windows = _is_windows()
    candidates = []
    override = os.environ.get('HEWN_STUDIO_HOST_PYTHON')
    if override:
        candidates.append(override)
    if sys.executable:
        candidates.append(sys.executable)
    sibling = _hermes_python()
    if sibling:
        candidates.append(sibling)
    if windows:
        candidates.append(['py', '-3'])
    for name in ('python3.13', 'python3.12', 'python3', 'python'):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    if _is_darwin():
        candidates += ['/opt/homebrew/bin/python3', '/usr/local/bin/python3']
    candidates.append('/usr/bin/python3.13')

    seen = set()
    tried = []
    for candidate in candidates:
        command = _candidate_command(candidate)
        if command is not None:
            display = ' '.join(command)
            key = _dedupe_key(command)
        else:
            display = (' '.join(str(part) for part in candidate)
                       if isinstance(candidate, (list, tuple)) else str(candidate))
            key = display
        if not display or key in seen:
            continue
        seen.add(key)
        tried.append(display if command is not None else f'{display} (not found)')
        if command is None:
            continue
        try:
            # Probe everything the host imports, not just one of them: an interpreter with
            # uvicorn but without fastapi would be selected and then die on start-up, and the
            # next candidate would never be tried.
            probe = subprocess.run([*command, '-c', 'import uvicorn, fastapi, starlette'],
                                   capture_output=True, timeout=20, env=_clean_env())
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return command

    installer = 'py -3' if windows else (sys.executable or 'python3')
    raise RuntimeError(
        'no interpreter with uvicorn found for the studio host; '
        f"tried {', '.join(tried) or '(none)'}. Install what it needs with: "
        f'{installer} -m pip install "fastapi" "uvicorn[standard]"')


#: Inherited from the gateway and fatal to a system interpreter: they point
#: site-packages at the Hermes venv, whose extension modules are built for another
#: Python version (``ImportError: cannot import name 'ArgsKwargs' from
#: pydantic_core._pydantic_core``).
_INHERITED_LEAKS = ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'PYTHONSTARTUP')


def _clean_env() -> dict:
    return {key: value for key, value in os.environ.items() if key not in _INHERITED_LEAKS}


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if _is_windows():
            # Windows reads SO_REUSEADDR as "share the port": bind succeeds on a port somebody
            # else is listening on, and the host then fails to start. Exclusive use (or no
            # option at all) makes bind tell the truth.
            exclusive = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)
            if exclusive is not None:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
                except OSError:
                    pass
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('127.0.0.1', port))
        except OSError:
            # Another socket owns the port — on Windows WSAEACCES (10013) or WSAEADDRINUSE
            # (10048) — or this address cannot be bound here at all: try the next one either way.
            return False
    return True


def _probe(port: int, timeout: float = 1.0) -> dict:
    """What is listening on ``port``: alive, and does it enforce the token?

    `/api/health` needs the token like every other route, and a launcher asks before it has one. A
    gated host answers 401, which proves both that it is alive and that it enforces the token. A host
    that answers 200 without a token predates the gate — the pane would be handed a token that host
    ignores, so it is not reused.
    """
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=timeout) as res:
            if res.status != 200:
                return {'alive': False, 'token_gated': False}
            try:
                body = json.loads(res.read() or b'{}')
            except ValueError:
                body = {}
            return {'alive': True, 'token_gated': bool(body.get('token_gated'))}
    except urllib.error.HTTPError as err:
        # Only our own refusal counts: another program on this port may answer 401 too.
        try:
            ours = err.code == 401 and json.loads(err.read() or b'{}').get('detail') == 'studio token required'
        except (ValueError, AttributeError, OSError):
            ours = False
        return {'alive': ours, 'token_gated': ours}
    except (urllib.error.URLError, OSError, ValueError):
        return {'alive': False, 'token_gated': False}


def _alive(port: int, timeout: float = 1.0) -> bool:
    return _probe(port, timeout)['alive']


def _read_state(root: Path) -> dict | None:
    try:
        data = json.loads((root / STATE_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(root: Path, state: dict) -> None:
    """Record the host so the next launch can reuse it — with owner-only permissions.

    The file carries the bearer token for every ``/api`` route, so 0644 under a 022 umask let another
    local user read it and drive the studio. ``os.open(..., 0o600)`` only applies at creation, and the
    temp file survives a crash, so the mode is set explicitly and an existing file is tightened too.
    """
    target = root / STATE_FILE
    tmp = root / (STATE_FILE + '.tmp')
    payload = json.dumps(state).encode('utf-8')
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)          # an existing .tmp keeps its old bits without this
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)
    if hasattr(os, 'chmod'):
        try:
            os.chmod(target, 0o600)       # belt and braces: the rename should already carry 0600
        except OSError:                   # pragma: no cover - Windows without POSIX modes
            pass


def forget() -> None:
    """Drop the recorded host, so the next ``ensure_host`` starts a fresh one."""
    try:
        (data_dir() / STATE_FILE).unlink()
    except OSError:
        pass


def shutdown_self(delay: float = 0.3) -> None:
    """Close the studio from inside it: answer the request, then stop the process.

    A window that cannot be closed is a window somebody has to kill by hand; the page
    offers this so the person can shut the studio down like any other app.
    """
    import threading

    forget()

    def _exit() -> None:
        import os
        os._exit(0)

    threading.Timer(max(0.05, float(delay)), _exit).start()


def _reap() -> None:
    _spawned[:] = [proc for proc in _spawned if proc.poll() is None]


def ensure_host(port: int | None = None) -> dict:
    """Return ``{'url', 'port', 'pid', 'started'}`` for a host serving this library."""
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    wanted = int(port or os.environ.get('HEWN_STUDIO_HOST_PORT') or 0)
    with _lock:
        _reap()
        state = _read_state(root)
        if state:
            known = int(state.get('port') or 0)
            probe = _probe(known) if known else {'alive': False, 'token_gated': False}
            if probe['alive'] and probe['token_gated']:
                token = _token_for(root, state, fresh=False)
                if token != state.get('token'):
                    _write_state(root, {**state, 'token': token})
                return {'url': f'http://127.0.0.1:{known}', 'port': known,
                        'pid': state.get('pid'), 'started': False, 'token': token}
        candidates = [wanted] if wanted else list(range(DEFAULT_PORT, DEFAULT_PORT + PORT_SPAN))
        chosen = next((p for p in candidates if _port_free(p)), None)
        if chosen is None:
            raise RuntimeError('no free loopback port for the studio host')
        token = _token_for(root, state, fresh=True)
        env = {**_clean_env(),
               'HEWN_STUDIO_DATA_DIR': str(root),
               # The framed page and the pane both need it; the host requires it on every /api route.
               'HEWN_STUDIO_TOKEN': token,
               'HEWN_STUDIO_HOST_PORT': str(chosen),
               'HEWN_STUDIO_HOST': '127.0.0.1',
               # The host watches this pid: when Hermes is gone, the host goes too. Without it a
               # detached host outlives the app that started it (measured: 58 MB, forever).
               'HEWN_STUDIO_PARENT_PID': str(os.getpid()),
               'PYTHONUNBUFFERED': '1'}
        # Machine-specific engine paths come from the person's own settings, never from the code.
        try:
            from . import settings as studio_settings
            saved = studio_settings.read(data_dir())
            for var, key in (('ACE_RUNTIME_DIR', 'ace_runtime_dir'),
                             ('ACE_SOURCE_DIR', 'ace_source_dir'),
                             ('ACE_REMOTE_URL', 'ace_remote_url')):
                if saved.get(key):
                    env[var] = str(saved[key])
        except Exception:  # pragma: no cover - settings are optional for starting the host
            pass
        log = open(root / LOG_FILE, 'ab', buffering=0)
        try:
            log.write(f'--- starting studio host on 127.0.0.1:{chosen} ---\n'.encode('utf-8'))
            python = _python()
            if isinstance(python, str):
                python = [python]
            proc = subprocess.Popen([*python, '-m', 'music_studio.host'],
                                    cwd=str(Path(__file__).resolve().parents[1]),
                                    env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, **detach_kwargs())
        finally:
            log.close()
        _spawned.append(proc)
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f'studio host exited ({proc.returncode}); see {root / LOG_FILE}')
            if _alive(chosen, timeout=0.5):
                _write_state(root, {'port': chosen, 'pid': proc.pid, 'token': token})
                return {'url': f'http://127.0.0.1:{chosen}', 'port': chosen,
                        'pid': proc.pid, 'started': True, 'token': token}
            time.sleep(0.15)
        proc.terminate()
        raise RuntimeError(f'studio host did not answer within {START_TIMEOUT_S:.0f}s')
