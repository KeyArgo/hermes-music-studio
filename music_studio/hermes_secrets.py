"""Credentials, held by Hermes and never by this plugin.

The plugin's public API has no key store of its own: ``agent.secret_scope.get_secret`` reads the
active profile and ``hermes_cli.config.save_env_value`` writes it. A standalone install without those
modules cannot save a key at all — which is the honest answer, not a plaintext fallback.
"""
from __future__ import annotations

SAVE_FAILED = ('the key could not be saved to Hermes; set ACE_REMOTE_API_KEY in Hermes '
               '(hermes config set ACE_REMOTE_API_KEY <key>) and try again')


def read_secret(name: str) -> str:
    """The secret Hermes holds for ``name``, or an empty string when there is none."""
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return ''
    try:
        value = get_secret(name)
    except Exception:  # noqa: BLE001 - a profile with no value is not an error here
        return ''
    return value if isinstance(value, str) else ''


def save_secret(name: str, value: str) -> bool:
    """Save ``value`` through Hermes and verify it reads back. Raises if Hermes is unavailable."""
    try:
        from hermes_cli.config import save_env_value
        from agent.secret_scope import get_secret  # noqa: F401 - required for read-back
    except ImportError as exc:
        raise ValueError(SAVE_FAILED) from exc
    try:
        save_env_value(name, value)
    except Exception as exc:  # noqa: BLE001 - any write failure is reported the same way
        raise ValueError(SAVE_FAILED) from exc
    try:
        from agent.secret_scope import invalidate_env_file_cache

        invalidate_env_file_cache()
    except Exception:  # noqa: BLE001 - the cache is an optimisation, not the store
        pass
    return read_secret(name) == value
