"""Loopback host for the modern studio UI inside the Hermes desktop pane.

The pane can frame a URL; it cannot reproduce a 13 KB HTML document plus 32 KB
of plain JS through the plugin SDK, and it cannot send the gateway's auth header
from inside an iframe. So the pane frames this: the same ``standalone.app`` the
owner already runs on 8770, bound to loopback on a drone port, reading the SAME
data directory the agent tool writes to (``HEWN_STUDIO_DATA_DIR`` is set by the
launcher, so ``music_studio.service`` resolves to the profile's plugin data).

Security posture is identical to the shipped standalone server: loopback bind,
loopback ``Host`` required, cross-site writes rejected. No new credential is
introduced, because a token would have to reach the page through the iframe and
the page's own ``fetch()`` calls, which is exactly the round trip that breaks
across the app's origin.

Run it directly for a manual check::

    HEWN_STUDIO_DATA_DIR=... python3.13 -m music_studio.host
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import after the path is fixed: ``standalone.app`` sets the data directory from
# the environment at import time, which is why the launcher must export it first.
from standalone.app import app  # noqa: E402
from music_studio import lifecycle, service  # noqa: E402

# One watchdog for the process. Every request proves the page is still being read; a tick with no
# request, no render in flight and a dead launcher ends the process instead of leaving 58 MB behind.
_watchdog = lifecycle.Watchdog(store_factory=service.store)


@app.middleware('http')
async def _studio_activity(request, call_next):
    _watchdog.touch()
    return await call_next(request)


def main() -> None:
    import uvicorn

    host = os.environ.get('HEWN_STUDIO_HOST', '127.0.0.1')
    port = int(os.environ.get('HEWN_STUDIO_HOST_PORT', '8775'))
    _watchdog.start()
    # No access log line per poll: the studio UI polls twice a second.
    uvicorn.run(app, host=host, port=port, log_level='warning', access_log=False)


if __name__ == '__main__':
    main()
