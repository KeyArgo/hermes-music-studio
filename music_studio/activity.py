"""``logs/studio.log``: one plain line for each thing the studio did, so a person (or a bug report)
can see what happened without reading code.

What is written: renders (queued, started, finished, failed), engine tests, and engines added or
removed. What is never written: keys, tokens or passwords (they are masked even if one arrives inside
an error message), lyrics, or captions. The file is opened, appended and closed for each line, so no
process holds it open (Windows refuses to rotate a file another process has open); at 1 MB it moves to
``studio.log.1`` and older copies shift up to ``.3``.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

LOG_NAME = 'studio.log'
MAX_BYTES = 1_000_000
KEEP = 3
_LOCK = threading.Lock()
# A value after one of these is a secret, wherever it shows up in a line.
_SECRET = re.compile(r'(?i)(bearer\s+|api[_-]?key["\']?\s*[=:]\s*["\']?|token["\']?\s*[=:]\s*["\']?|'
                     r'password["\']?\s*[=:]\s*["\']?|sk-)[^\s&"\',}]+')


def log_path(base) -> Path:
    return Path(base) / 'logs' / LOG_NAME


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: m.group(1) + '[hidden]', text)


def _rotate(path: Path) -> None:
    for index in range(KEEP - 1, 0, -1):
        older = path.with_name(f'{LOG_NAME}.{index}')
        if older.exists():
            os.replace(older, path.with_name(f'{LOG_NAME}.{index + 1}'))
    os.replace(path, path.with_name(f'{LOG_NAME}.1'))


def record(base, event: str, **fields) -> None:
    """Append one line. Never raises: a log that cannot be written must not stop a render."""
    if base is None:
        return
    try:
        path = log_path(base)
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        details = ' '.join(f'{key}={str(value)[:300]!r}' for key, value in fields.items()
                           if value not in (None, ''))
        line = redact(f'{stamp} {event} {details}'.rstrip()).replace('\n', ' ') + '\n'
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if path.stat().st_size > MAX_BYTES:
                    _rotate(path)
            except OSError:
                pass
            with path.open('a', encoding='utf-8') as out:
                out.write(line)
    except Exception:  # noqa: BLE001 - logging is best effort by design
        return


def data_dir():
    """The plugin data folder, or None outside Hermes (tests and scripts log nothing)."""
    try:
        from . import settings
        return settings.base_dir()
    except Exception:  # noqa: BLE001
        return None
