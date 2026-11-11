"""Check for a newer release, install it, and undo — from inside the running studio.

The install is a copy of this tree, so "update" means running the same installer a person would run,
from a detached process: replacing the files a live process is importing would break it mid-request.
That is why `apply()` returns immediately with a job id and the page polls `job()`.

Undo is the same operation pointed at the revision that was installed before, which is recorded
before the first byte is written. Nothing is force-pushed and nothing is deleted.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = 'KeyArgo/hermes-music-studio'
API = f'https://api.github.com/repos/{REPO}'
PREVIOUS_FILE = 'update-previous.json'
JOB_FILE = 'update-job.json'
LOG_FILE = 'update.log'
TIMEOUT_S = 8


def _installed_dir():
    """The directory this code is running from (the install root)."""
    return Path(__file__).resolve().parent.parent


def _git(*args, cwd=None):
    try:
        out = subprocess.run(['git', *args], cwd=str(cwd or _installed_dir()),
                             capture_output=True, text=True, timeout=TIMEOUT_S)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def current():
    """What is installed right now: version, commit, and whether git knows this copy."""
    root = _installed_dir()
    commit = _git('rev-parse', 'HEAD', cwd=root)
    version = None
    try:
        import yaml  # noqa: F401  (optional; fall back to a line read)
        manifest = root / 'plugin.yaml'
        for line in manifest.read_text().splitlines():
            if line.startswith('version:'):
                version = line.split(':', 1)[1].strip().strip('"\'')
                break
    except Exception:
        pass
    return {'root': str(root), 'commit': commit,
            'commit_short': commit[:8] if commit else None, 'version': version,
            'managed_by_git': commit is not None}


def check(timeout=TIMEOUT_S):
    """The newest commit on the public repo's main branch, and whether it is newer than this copy."""
    here = current()
    try:
        request = urllib.request.Request(f'{API}/commits/main',
                                         headers={'Accept': 'application/vnd.github+json',
                                                  'User-Agent': 'hermes-music-studio'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except Exception as exc:
        return {'ok': False, 'installed': here, 'detail': f'could not ask GitHub: {exc}'}
    latest = payload.get('sha')
    message = (payload.get('commit') or {}).get('message', '').splitlines()[0]
    date = ((payload.get('commit') or {}).get('committer') or {}).get('date')
    behind = bool(latest and here['commit'] and latest != here['commit'])
    return {
        'ok': True, 'installed': here,
        'latest': {'commit': latest, 'commit_short': (latest or '')[:8], 'message': message,
                   'date': date},
        'update_available': behind,
        'detail': ('a newer build is published' if behind else
                   'this copy is the newest published build'),
    }


def _write_job(data_dir, **fields):
    path = Path(data_dir) / JOB_FILE
    path.write_text(json.dumps(fields, indent=2))
    return fields


def job(data_dir):
    path = Path(data_dir) / JOB_FILE
    if not path.is_file():
        return {'state': 'idle'}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {'state': 'unreadable'}


def _previous(data_dir):
    path = Path(data_dir) / PREVIOUS_FILE
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _spawn_install(data_dir, ref, mode='update'):
    """Run the installer detached so this process can keep answering while it runs."""
    root = _installed_dir()
    log = Path(data_dir) / LOG_FILE
    here = current()
    if mode == 'update':
        Path(data_dir, PREVIOUS_FILE).write_text(
            json.dumps({'commit': here['commit'], 'version': here['version'],
                        'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S')}, indent=2))
    args = [shutil.which('hermes') or 'hermes', 'plugins', 'install', REPO,
            '--ref', ref, '--force', '--yes-deps', '--enable']
    with open(log, 'ab') as handle:
        handle.write(f'\n=== {time.strftime("%Y-%m-%dT%H:%M:%S")} {" ".join(args)}\n'.encode())
        handle.flush()
        process = subprocess.Popen(args, cwd=str(root), stdout=handle, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    return _write_job(data_dir, state='running', mode=mode, ref=ref, pid=process.pid,
                      started=time.strftime('%Y-%m-%dT%H:%M:%S'), log=str(log))


def apply(data_dir, ref=None):
    """Install the newest published commit, or an explicit one. Returns the job, not the result."""
    target = (ref or '').strip()
    if not target:
        found = check()
        if not found.get('ok'):
            raise ValueError(found.get('detail') or 'could not reach GitHub')
        target = (found.get('latest') or {}).get('commit') or ''
    if not re.fullmatch(r'[0-9a-f]{40}', target):
        raise ValueError('the installer needs a full 40-character commit id')
    if job(data_dir).get('state') == 'running':
        raise ValueError('an install is already running; wait for it to finish')
    return _spawn_install(data_dir, target, mode='update')


def undo(data_dir):
    """Go back to the revision that was installed before the last update."""
    previous = (_previous(data_dir) or {}).get('commit')
    if not previous:
        raise ValueError('there is no recorded previous version to go back to')
    if job(data_dir).get('state') == 'running':
        raise ValueError('an install is already running; wait for it to finish')
    return _spawn_install(data_dir, previous, mode='undo')


def log_tail(data_dir, lines=20):
    path = Path(data_dir) / LOG_FILE
    if not path.is_file():
        return ''
    try:
        return '\n'.join(path.read_text(errors='replace').splitlines()[-lines:])
    except Exception:
        return ''
