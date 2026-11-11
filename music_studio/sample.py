"""The shipped demo take, served the same way by both surfaces.

The standalone app and the dashboard router used to answer this route with two copies of the same
six lines; a change to one (content type, headers, the file it points at) silently skipped the
other. One function, two thin routes.
"""
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_AUDIO = PLUGIN_ROOT / 'assets' / 'demo' / 'afterglow.wav'


def sample_response() -> FileResponse:
    """The instrumental sample, or a 404 that says which file is missing."""
    if not SAMPLE_AUDIO.is_file():
        raise HTTPException(status_code=404, detail='sample not found')
    return FileResponse(SAMPLE_AUDIO, media_type='audio/wav',
                        headers={'X-Content-Type-Options': 'nosniff'})
