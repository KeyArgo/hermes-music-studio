"""Signed links for one take's audio, so REVamp's player can stream it without the studio token.

Review item 3 put the per-launch token on every ``/api`` route. REVamp's Webamp player fetches a take
from its own page, which never holds that token, so every song sent to REVamp stopped playing. Handing
REVamp the token would give a stored playlist the key to the whole studio. A signature instead names
exactly one take: it opens ``GET/HEAD /api/projects/<project>/takes/<take>/audio`` for that take and
nothing else, and it outlives a host restart because the key is per install, not per launch.

The key lives beside the library in ``media-key``, created owner-only (0600) at open time.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path

KEY_FILE = 'media-key'
# The audio route, and only the audio route: no edits, peaks, downloads or anything that writes.
AUDIO_PATH = re.compile(r'^/api/projects/([^/]+)/takes/([^/]+)/audio$')


def _key(root: Path) -> bytes:
    path = Path(root) / KEY_FILE
    try:
        existing = path.read_bytes().strip()
        if len(existing) >= 32:
            return existing
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32).encode('ascii')
    tmp = path.with_name(KEY_FILE + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
        os.write(fd, value)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        # Two first requests at once: whoever lands second keeps the first one's key, so a link
        # signed a moment ago stays valid.
        os.link(tmp, path)
    except FileExistsError:
        pass
    except OSError:
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return path.read_bytes().strip()


def sign(root: Path, project_id: str, take_id: str) -> str:
    message = f'{project_id}/{take_id}'.encode('utf-8')
    return hmac.new(_key(root), message, hashlib.sha256).hexdigest()[:32]


def verify(root: Path, path: str, signature: str) -> bool:
    """True when ``signature`` signs the take named by the audio ``path``; False for any other path."""
    match = AUDIO_PATH.match(path or '')
    if not match or not signature:
        return False
    return hmac.compare_digest(sign(root, match.group(1), match.group(2)), str(signature))


def audio_url(base: str, root: Path, project_id: str, take_id: str) -> str:
    return (base.rstrip('/') + f'/api/projects/{project_id}/takes/{take_id}/audio'
            + '?sig=' + sign(root, project_id, take_id))
