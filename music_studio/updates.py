"""What build is installed. Updating is Hermes's job (`hermes plugins update`), never the studio's.

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
from pathlib import Path

from .childenv import minimal_env

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
        # git needs PATH/HOME, which the allowlist keeps; it has no use for provider keys.
        out = subprocess.run(['git', *args], cwd=str(cwd or _installed_dir()),
                             capture_output=True, text=True, timeout=TIMEOUT_S,
                             env=minimal_env())
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


HOW = 'Updates come through Hermes: run `hermes plugins update hermes-music-studio`.'


def check(timeout=TIMEOUT_S):
    """Report the installed build. The studio never fetches or installs code itself (catalog rule 3)."""
    return {'ok': True, 'installed': current(), 'update_available': False, 'detail': HOW}


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


def apply(data_dir, ref=None):
    """Refused: Hermes owns installs and updates."""
    raise ValueError(HOW)


def undo(data_dir):
    """Refused: Hermes owns installs and updates."""
    raise ValueError(HOW)


def log_tail(data_dir, lines=20):
    path = Path(data_dir) / LOG_FILE
    if not path.is_file():
        return ''
    try:
        return '\n'.join(path.read_text(errors='replace').splitlines()[-lines:])
    except Exception:
        return ''
