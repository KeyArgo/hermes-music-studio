"""The active Hermes profile's home, from Hermes' public helper only.

Inside Hermes this is ``hermes_constants.get_hermes_home()`` (public API: it follows the context-local
override of a multiplexed gateway before ``HERMES_HOME``). The studio host runs as its own process,
where that module may not import; the launcher resolves the home with the same helper and passes it as
``HERMES_HOME``, so the host sees the one profile it was started for. Nothing here guesses ``~/.hermes``.
"""
from __future__ import annotations

import os
from pathlib import Path


def active_home() -> Path | None:
    try:
        from hermes_constants import get_hermes_home
    except ImportError:
        raw = os.environ.get('HERMES_HOME', '').strip()
        return Path(raw).expanduser() if raw else None
    return Path(get_hermes_home()).expanduser()
