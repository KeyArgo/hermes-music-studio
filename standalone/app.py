"""Local-only standalone server for the existing Music Studio API."""
import hmac
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

default_root = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
os.environ.setdefault('HEWN_STUDIO_DATA_DIR', str(default_root / 'hewn-music-studio'))
# This server IS the host the desktop pane frames; it must not start a second one.
os.environ['HEWN_STUDIO_NO_HOST'] = '1'

from dashboard.plugin_api import router  # noqa: E402
from music_studio import media_sign, service  # noqa: E402
from standalone.ideas import router as ideas_router  # noqa: E402
from standalone.workspaces import router as workspaces_router  # noqa: E402
from standalone.revamp import router as revamp_router  # noqa: E402

ROOT = Path(__file__).parent
# The liveness route. It needs the token like every other /api route (review item 3); a 401 still
# tells the launcher and the pane's no-cors probe that something is listening.
HEALTH_PATH = '/api/health'
app = FastAPI(title='Hermes Music Studio', docs_url=None, redoc_url=None)


# A framed page in Hermes Desktop lives in a sandboxed frame: its origin is opaque ("null"), so every
# fetch it makes is cross-origin and every write arrives with Origin: null and Sec-Fetch-Site:
# cross-site. The old middleware refused both, which made the pane's own API unreachable. The rule now
# is: a request that carries the per-launch token is trusted regardless of which document sent it —
# the token is the credential, the origin is not — and the response gets the CORS headers the browser
# needs to hand it back to that document.
CORS_ALLOW_HEADERS = 'Content-Type, X-Studio-Token'
CORS_ALLOW_METHODS = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
LOOPBACK_NAMES = ('127.0.0.1', 'localhost', '::1')


def _cors_origin(request: Request) -> str | None:
    """The origin to echo back, or None. Only an opaque frame or a loopback page is allowed."""
    origin = request.headers.get('origin')
    if not origin:
        return None
    if origin == 'null':
        return 'null'
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return None
    if parsed.scheme in ('http', 'https') and parsed.hostname in LOOPBACK_NAMES:
        return origin
    return None


def _with_cors(response, request: Request):
    allowed = _cors_origin(request)
    if allowed:
        response.headers['Access-Control-Allow-Origin'] = allowed
        response.headers['Access-Control-Allow-Headers'] = CORS_ALLOW_HEADERS
        response.headers['Access-Control-Allow-Methods'] = CORS_ALLOW_METHODS
        response.headers['Access-Control-Max-Age'] = '600'
        response.headers['Vary'] = 'Origin'
    return response


@app.middleware('http')
async def same_origin(request: Request, call_next):
    # Limit browser access to loopback names as well as binding to loopback;
    # this prevents a remote DNS name resolving to the local server.
    host = request.headers.get('host', '')
    try:
        hostname = urlsplit('//' + host).hostname
    except ValueError:
        hostname = None
    if hostname not in ('127.0.0.1', 'localhost', '::1'):
        return _with_cors(JSONResponse({'detail': 'loopback host required'}, status_code=400), request)
    expected = os.environ.get('HEWN_STUDIO_TOKEN', '').strip()
    path = request.url.path
    supplied = request.headers.get('x-studio-token') or request.query_params.get('token') or ''
    authorized = bool(expected) and hmac.compare_digest(supplied, expected)

    # A preflight carries no token header (the browser asks before it may send one), so it is answered
    # from the origin alone and carries no data.
    if path.startswith('/api/') and request.method == 'OPTIONS':
        from starlette.responses import Response as PlainResponse

        return _with_cors(PlainResponse(status_code=204), request)

    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not authorized:
        if request.method in ('POST', 'PUT', 'PATCH'):
            content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
            length = request.headers.get('content-length')
            has_body = (length not in (None, '0')
                        or bool(request.headers.get('transfer-encoding')))
            # A body-less action (e.g. POST /targets/{id}/probe) has nothing to
            # parse, so it is not forced to declare a JSON content type. The
            # probe route ignores its body entirely.
            body_optional = (request.method == 'POST'
                             and request.url.path.endswith('/probe'))
            if not body_optional and has_body and content_type != 'application/json':
                return _with_cors(JSONResponse({'detail': 'application/json required'}, status_code=415), request)
        origin = request.headers.get('origin')
        if request.headers.get('sec-fetch-site', '').lower() == 'cross-site':
            return _with_cors(JSONResponse({'detail': 'cross-site request rejected'}, status_code=403), request)
        if origin:
            parsed = urlsplit(origin)
            expected = request.headers.get('host', '').lower()
            if parsed.scheme != request.url.scheme or parsed.netloc.lower() != expected:
                return _with_cors(JSONResponse({'detail': 'cross-site request rejected'}, status_code=403), request)
    # Review item 3: the token gate, closed by default. Every /api/* route needs it, the liveness probe
    # included (a 401 still tells a launcher or the pane's no-cors fetch that the host is alive) — a local process could otherwise change the ACE runtime dir (which picks the Python that
    # runs), move the library, copy folders and spend model credits. A host started without a token
    # refuses /api rather than serving everything: fail-open is how this gate was defeated before.
    # The one exception, and it is narrow: REVamp's player streams a take it was handed with a
    # signature for that one take's audio (music_studio/media_sign.py). It opens nothing else.
    signed_audio = (not authorized and request.method in ('GET', 'HEAD')
                    and media_sign.verify(service.store().root, path,
                                          request.query_params.get('sig', '')))
    if path.startswith('/api/') and not signed_audio:
        if not expected:
            return _with_cors(JSONResponse(
                {'detail': 'this host has no token, so it refuses every /api route; '
                           'start the studio through the plugin, or set HEWN_STUDIO_TOKEN '
                           'before starting it by hand'},
                status_code=503), request)
        if not authorized:
            return _with_cors(
                JSONResponse({'detail': 'studio token required'}, status_code=401), request)
    response = await call_next(request)
    # REVamp's local player may stream an explicitly selected studio take.
    port = os.environ.get('REVAMP_PORT', '8114')
    allowed = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
    origin = request.headers.get('origin')
    if (request.method in ('GET', 'HEAD') and request.url.path.startswith('/api/projects/')
            and request.url.path.endswith('/audio') and origin in allowed):
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
    # The framed page has an opaque origin, so its own reads need these headers too.
    return _with_cors(response, request)

app.include_router(router, prefix='/api')
app.include_router(ideas_router, prefix='/api')
app.include_router(workspaces_router, prefix='/api')
app.include_router(revamp_router, prefix='/api')
app.mount('/static', StaticFiles(directory=str(ROOT / 'static'), check_dir=False), name='static')


@app.get(HEALTH_PATH)
def health():
    """Liveness, plus whether this host enforces the token (the launcher restarts one that does not)."""
    return {'ok': True, 'token_gated': bool(os.environ.get('HEWN_STUDIO_TOKEN', '').strip())}

@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')

@app.get('/api/studio/sample')
def sample():
    from music_studio.sample import sample_response
    return sample_response()


def _studio_call(fn, *args, **kwargs):
    """Map store/target errors to the API's small, honest status codes."""
    try:
        return fn(*args, **kwargs)
    except KeyError:
        raise HTTPException(status_code=404, detail='target not found') from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


async def _json_body(request):
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail='invalid JSON body') from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail='invalid request body') from None
    return body


@app.get('/api/studio/targets')
def studio_targets():
    return _studio_call(service.targets)


@app.post('/api/studio/targets')
async def create_studio_target(request: Request):
    return _studio_call(service.create_target, await _json_body(request))


@app.delete('/api/studio/targets/{target_id}')
def delete_studio_target(target_id: str):
    return _studio_call(service.delete_target, target_id)


@app.post('/api/studio/targets/{target_id}/probe')
def probe_studio_target(target_id: str):
    return _studio_call(service.probe_target, target_id)


@app.get('/api/studio/hardware')
def studio_hardware():
    return _studio_call(service.hardware)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8765)
