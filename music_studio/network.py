"""Named ComfyUI endpoints for remote MiniMax renders.

A target is a user-owned ComfyUI base URL, persisted next to the studio's
projects. The URL is validated once at creation time: only plain HTTP(S) base
URLs are accepted, never a URL that embeds credentials, a query or a fragment,
and never a link-local address (the cloud metadata services live there).

The store is deliberately small and lock-protected because it is shared by the
dashboard process, the detached worker, and the standalone server.
"""
import copy
import ipaddress
import json
import os
import tempfile
import urllib.parse
import uuid
from pathlib import Path

TARGETS_FILE = 'comfy-targets.json'

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows CI only
    fcntl = None
    import msvcrt


def _lock_ex(handle):
    """Take an exclusive lock on an open file, on either platform.

    Windows has no fcntl; msvcrt.locking locks a byte range at the current position, so it is a
    different call with the same meaning. The store is written by the dashboard process, the
    detached worker and the standalone server, so the lock cannot be POSIX-only.
    """
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_EX)
        return
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)


def _lock_un(handle):
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_UN)
        return
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

MAX_NAME_CHARS = 80


def _link_local(host):
    """True for IPv4/IPv6 link-local addresses (including metadata endpoints)."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_link_local)


def validate_base_url(url):
    """Validate and normalise a ComfyUI base URL; return the stored form.

    Accepted: ``http(s)://host[:port][/path]``. Rejected: any other scheme,
    credentials in the authority, a query or fragment, a missing host, and
    link-local IPs such as 169.254.169.254. The path is allowed because a
    reverse proxy may mount ComfyUI under a prefix; it is stored without a
    trailing slash so adapters can append ``/system_stats`` unambiguously.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError('target URL must be a non-empty string')
    text = url.strip()
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError as exc:
        raise ValueError(f'invalid target URL: {url}') from exc
    if parts.scheme.lower() not in ('http', 'https'):
        raise ValueError('target URL must use http or https')
    if not parts.netloc or not parts.hostname:
        raise ValueError('target URL must include a host')
    try:
        parts.port
    except ValueError as exc:
        raise ValueError(f'invalid target URL: {url}') from exc
    if '@' in parts.netloc or parts.username is not None or parts.password is not None:
        raise ValueError('target URL must not contain credentials')
    if parts.query:
        raise ValueError('target URL must not contain a query')
    if parts.fragment:
        raise ValueError('target URL must not contain a fragment')
    if _link_local(parts.hostname):
        raise ValueError('target URL must not be a link-local address')
    path = parts.path.rstrip('/')
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc, path, '', ''))


def validate_name(name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('target name must be a non-empty string')
    name = name.strip()
    if len(name) > MAX_NAME_CHARS:
        raise ValueError(f'target name must be at most {MAX_NAME_CHARS} characters')
    return name


def _valid_id(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        raise ValueError('invalid target id') from None
    return value


class TargetStore:
    """Persist named ComfyUI targets in ``root/comfy-targets.json``."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / TARGETS_FILE

    def _lock(self):
        return (self.root / '.comfy-targets.lock').open('a')

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data
                if isinstance(item, dict) and isinstance(item.get('id'), str)]

    def _write(self, targets):
        fd, name = tempfile.mkstemp(dir=self.root, prefix='.comfy-targets-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(targets, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.path)
            if os.name != 'nt':  # Windows cannot open a directory to fsync it
                fd = os.open(self.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def create(self, name, url):
        name = validate_name(name)
        url = validate_base_url(url)
        target = {'id': str(uuid.uuid4()), 'name': name, 'url': url}
        with self._lock() as lock:
            _lock_ex(lock)
            try:
                targets = self._read()
                targets.append(target)
                self._write(targets)
            finally:
                _lock_un(lock)
        return copy.deepcopy(target)

    def list(self):
        with self._lock() as lock:
            _lock_ex(lock)
            try:
                return copy.deepcopy(self._read())
            finally:
                _lock_un(lock)

    def get(self, target_id):
        target_id = _valid_id(target_id)
        for target in self.list():
            if target['id'] == target_id:
                return target
        raise KeyError(target_id)

    def delete(self, target_id):
        target_id = _valid_id(target_id)
        with self._lock() as lock:
            _lock_ex(lock)
            try:
                targets = self._read()
                remaining = [t for t in targets if t['id'] != target_id]
                if len(remaining) == len(targets):
                    raise KeyError(target_id)
                self._write(remaining)
                return copy.deepcopy(next(t for t in targets
                                          if t['id'] == target_id))
            finally:
                _lock_un(lock)
