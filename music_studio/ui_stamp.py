"""One token per install, shared by the page and the files it names.

Hermes Desktop frames the studio in a webview, and a webview caches ``app.js`` and ``style.css`` by
URL. An update replaced the files without changing the URLs, so the pane kept running the old script
against the new routes until the whole app was restarted. The page now asks for
``app.js?v=<token>``: a new install is a new URL, so there is nothing stale left to reuse.

The token is a short hash of the three UI files' contents. A commit or version was tried first and
was wrong in one case that matters: dashboard/plugin_api.py loads this file by path to dodge another
profile's older copy, and reading the stamp through ``music_studio`` would then name THAT copy's commit.
The contents can only describe the files actually being served.

The page carries :data:`PAGE_TOKEN` where the version query goes; whoever serves it swaps in the
real token, so the file on disk stays a plain, openable HTML page.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# The marker index.html carries in its two asset URLs, e.g. ``app.js?v=__STUDIO_STAMP__``.
PAGE_TOKEN = '__STUDIO_STAMP__'

_FILES = ('index.html', 'app.js', 'style.css')


def build_token(static_dir) -> str:
    """12 hex characters that change exactly when one of the served UI files changes."""
    digest = hashlib.sha256()
    for name in _FILES:
        digest.update(name.encode())
        try:
            digest.update((Path(static_dir) / name).read_bytes())
        except OSError:
            # A missing file still yields a token; the route that serves it reports the 404.
            digest.update(b'\0missing')
    return digest.hexdigest()[:12]


def render(page: str, token: str) -> str:
    """The page with every :data:`PAGE_TOKEN` replaced by ``token``."""
    return page.replace(PAGE_TOKEN, token)
