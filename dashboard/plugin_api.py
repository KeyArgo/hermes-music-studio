"""Dashboard backend, mounted by Hermes under /api/plugins/hermes-music-studio."""
import json
import sys
import base64
import os
import threading
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from music_studio import demo, service
from music_studio.engines import CapabilityUnsupported, EngineUnavailable
from music_studio.jobs import Jobs
from music_studio.sections import validate_sections
from music_studio.store import validate
from standalone.ideas import router as ideas_router
from standalone.revamp import router as revamp_router
from standalone.workspaces import MAX_IMAGE as MAX_COVER_BYTES
from standalone.workspaces import get_cover as workspace_cover
from standalone.workspaces import router as workspaces_router

router = APIRouter()

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_STANDALONE_STATIC = _PLUGIN_ROOT / 'standalone' / 'static'
_SAMPLE_AUDIO = _PLUGIN_ROOT / 'assets' / 'demo' / 'afterglow.wav'

# Exact whitelist, not a directory mount: the name is a dict key, so no request
# can resolve outside the three committed UI files.
_STUDIO_ASSETS = {
    'index.html': ('index.html', 'text/html; charset=utf-8'),
    'app.js': ('app.js', 'application/javascript; charset=utf-8'),
    'style.css': ('style.css', 'text/css; charset=utf-8'),
}

# One JSON request is draft-sized; anything larger is a paste bomb that would be
# duplicated into every take snapshot and re-serialized by /status.
MAX_JSON_BODY_BYTES = 256 * 1024

_CONFLICT_HINTS = ('approval', 'busy', 'stale draft', 'confirm')


def invoke(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='project not found') from None
    except KeyError:
        raise HTTPException(status_code=404, detail='take not found') from None
    # An engine that cannot take this operation is a conflict with the requested
    # state, not a malformed request: 409 plus the engine's own explanation.
    except CapabilityUnsupported as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except EngineUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        detail = str(exc)
        conflict = any(hint in detail for hint in _CONFLICT_HINTS)
        raise HTTPException(status_code=409 if conflict else 400, detail=detail) from None
    except TypeError:
        raise HTTPException(status_code=400, detail='invalid request') from None
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=500, detail='studio operation failed') from None


async def json_body(request: Request):
    """Parse a bounded JSON object body for every mutating route.

    The cap is enforced while reading, not after buffering: a chunked upload
    without Content-Length is abandoned as soon as it crosses the limit, so an
    oversized body is never fully materialised in memory.
    """
    declared = request.headers.get('content-length')
    if declared is not None:
        try:
            if int(declared) > MAX_JSON_BODY_BYTES:
                raise HTTPException(status_code=413, detail='request body too large')
        except ValueError:
            raise HTTPException(status_code=400, detail='invalid content-length') from None
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_JSON_BODY_BYTES:
            raise HTTPException(status_code=413, detail='request body too large')
    try:
        body = json.loads(bytes(raw) or b'{}')
    except ValueError:
        raise HTTPException(status_code=400, detail='invalid JSON body') from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail='invalid request body')
    return body


@router.get('/demo')
def demo_meta():
    """The shipped demo take's metadata; served with no engine installed."""
    if not demo.available():
        raise HTTPException(status_code=404, detail='demo take not found')
    payload = demo.payload()
    if 'peak_dbfs' not in payload:
        from music_studio import qc
        payload['peak_dbfs'] = qc.analyse_wav(demo.audio_path().read_bytes())['peak_dbfs']
    return payload


@router.get('/demo/audio')
def demo_audio():
    """The shipped demo take's WAV, straight from the committed fixture."""
    path = demo.audio_path()
    if not path.is_file():
        raise HTTPException(status_code=404, detail='demo take not found')
    return FileResponse(path, media_type='audio/wav',
                        headers={'X-Content-Type-Options': 'nosniff'})


@router.get('/demo/audio-json')
def demo_audio_json():
    """The demo WAV as base64 JSON, the shape the desktop client plays from."""
    path = demo.audio_path()
    if not path.is_file():
        raise HTTPException(status_code=404, detail='demo take not found')
    data = path.read_bytes()
    if len(data) > MAX_DESKTOP_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail='Audio exceeds the 8 MiB desktop playback limit')
    return {'success': True, 'media_type': 'audio/wav',
            'audio_base64': base64.b64encode(data).decode('ascii')}


@router.get('/status')
def status():
    return invoke(service.status)


@router.get('/doctor')
def doctor():
    return {'success': True, 'engines': invoke(service.doctor)}


def _draft(body):
    draft = body.get('draft')
    if not isinstance(draft, dict):
        raise HTTPException(status_code=400, detail='invalid draft')
    return draft


@router.post('/create')
def create(body: dict = Depends(json_body)):
    draft = _draft(body)
    return {'success': True, 'project': invoke(lambda: service.store().create(**draft))}


@router.post('/update')
def update(body: dict = Depends(json_body)):
    draft = _draft(body)
    base_updated_at = body.get('base_updated_at')
    def apply():
        store = service.store()
        with store.locked():
            project = store.get(body.get('project_id'))
            if base_updated_at is not None and project.get('updated_at') != base_updated_at:
                raise ValueError('stale draft')
            merged = {**project, **draft}
            # The duration cap belongs to the engine this draft will render on;
            # a duration-only edit keeps the stored engine.
            validate(draft, engine=merged.get('engine'))
            if 'engine' in draft:
                # An engine switch is validated against the duration it keeps.
                validate({'duration_seconds': draft.get(
                    'duration_seconds', project.get('duration_seconds'))},
                    engine=draft['engine'])
            # Sections are a whole-song partition: validate the merged draft,
            # not just the fields this request happens to send.
            validate_sections(merged.get('sections', []), merged.get('duration_seconds'))
            project.update(draft)
            project['approved'] = False
            store._save(project)
            return project
    return {'success': True, 'project': invoke(apply)}


@router.post('/change')
def change_draft(body: dict = Depends(json_body)):
    """Plain words → a different draft. One implementation, called by the tool, the page and the pane."""
    from music_studio import intent

    project_id, instruction = body.get('project_id'), body.get('instruction')
    if not project_id or not instruction:
        raise HTTPException(status_code=400, detail='project_id and instruction are required')
    return invoke(lambda: intent.change(service.store(), project_id, instruction))


@router.post('/approve')
def approve(body: dict = Depends(json_body)):
    """Approve the exact draft version the user was looking at.

    ``base_updated_at`` is mandatory: it is the token the form was loaded with.
    A missing or different token is a conflict, never a silent approval of text
    the reviewer has not seen (the draft may have been rewritten by the agent
    between the poll and the click).
    """
    base_updated_at = body.get('base_updated_at')
    if base_updated_at is None:
        raise HTTPException(status_code=409, detail='stale draft')
    return {'success': True,
            'project': invoke(lambda: service.store().approve(
                body.get('project_id'), base_updated_at=base_updated_at))}


@router.post('/generate')
def generate(body: dict = Depends(json_body)):
    """Queue one take, optionally on an explicitly chosen engine.

    ``engine_id`` is optional so older clients keep working; when present the
    service resolves it (explicit -> draft -> default) and refuses an unknown
    id (400) or an unavailable engine (409) instead of substituting one.
    """
    return invoke(service.generate, body.get('project_id'), body.get('seed'),
                  lock_seed=body.get('lock_seed', False),
                  engine_id=body.get('engine_id'))


@router.post('/repaint')
def repaint(body: dict = Depends(json_body)):
    """Regenerate one range of a succeeded take as a new take."""
    if 'start_s' not in body or 'end_s' not in body:
        raise HTTPException(status_code=400, detail='start_s and end_s are required')
    return invoke(service.repaint, body.get('project_id'), body.get('take_id'),
                  body.get('start_s'), body.get('end_s'),
                  caption=body.get('caption'), lyrics_seed=body.get('lyrics_seed'))


@router.post('/checkpoint')
def save_checkpoint(body: dict = Depends(json_body)):
    """Save the spot: draft, keeper, lineage, pending work and playhead, as a copy."""
    from music_studio import checkpoints

    if not body.get('project_id'):
        raise HTTPException(status_code=400, detail='project_id is required')
    return invoke(lambda: checkpoints.save(service.store(), body['project_id'],
                                           label=body.get('label'),
                                           pending=body.get('pending'),
                                           playhead_s=body.get('playhead_s'),
                                           loop=body.get('loop')))


@router.get('/checkpoints/{project_id}')
def list_checkpoints(project_id: str):
    from music_studio import checkpoints

    return invoke(lambda: checkpoints.listing(service.store(), project_id))


@router.post('/checkpoint/restore')
def restore_checkpoint(body: dict = Depends(json_body)):
    from music_studio import checkpoints

    if not body.get('project_id') or not body.get('checkpoint_id'):
        raise HTTPException(status_code=400, detail='project_id and checkpoint_id are required')
    return invoke(lambda: checkpoints.restore(service.store(), body['project_id'],
                                              body['checkpoint_id']))


@router.post('/take-check')
def take_check(body: dict = Depends(json_body)):
    """Did this take do what was asked? Facts only; unmeasured checks say so.

    `retry: true` re-renders with a fresh seed, but only when a measurable check actually failed.
    """
    from music_studio import takecheck

    project_id, take_id = body.get('project_id'), body.get('take_id')
    if not project_id or not take_id:
        raise HTTPException(status_code=400, detail='project_id and take_id are required')
    if body.get('retry'):
        return invoke(lambda: takecheck.retry(project_id, take_id, service.store(),
                                             lambda pid, seed: service.generate(pid, seed)))
    return invoke(lambda: takecheck.report(project_id, take_id, service.store()))


@router.post('/snippet')
def snippet(body: dict = Depends(json_body)):
    """Change one range of a finished take in plain words. ACE only; the refusal is the engine's own."""
    for field in ('project_id', 'take_id', 'start_s', 'end_s', 'instruction'):
        if body.get(field) in (None, ''):
            raise HTTPException(status_code=400, detail=f'{field} is required')
    return invoke(service.snippet, body['project_id'], body['take_id'],
                  body['start_s'], body['end_s'], body['instruction'])


@router.post('/snippet/splice')
def snippet_splice(body: dict = Depends(json_body)):
    """Copy the parent's audio back outside the changed range, so only that range differs."""
    if not body.get('project_id') or not body.get('take_id'):
        raise HTTPException(status_code=400, detail='project_id and take_id are required')
    return invoke(service.splice_snippet, body['project_id'], body['take_id'])


@router.post('/extend')
def extend(body: dict = Depends(json_body)):
    """Append a continuation to a succeeded take as a new take."""
    if 'extend_seconds' not in body:
        raise HTTPException(status_code=400, detail='extend_seconds is required')
    return invoke(service.extend, body.get('project_id'), body.get('take_id'),
                  body.get('extend_seconds'), caption=body.get('caption'),
                  lyrics=body.get('lyrics'))


@router.post('/batch')
def batch(body: dict = Depends(json_body)):
    """Queue a serial batch: one detached worker renders the takes in order."""
    return invoke(service.batch, body.get('project_id'), body.get('count'),
                  body.get('seed_base'), engine_id=body.get('engine_id'))


@router.post('/batch/cancel')
def batch_cancel(body: dict = Depends(json_body)):
    return invoke(service.cancel_batch, body.get('project_id'))


@router.post('/rename')
def rename(body: dict = Depends(json_body)):
    return invoke(service.rename, body.get('project_id'), body.get('title'))


@router.post('/delete')
def delete_project(body: dict = Depends(json_body)):
    """Delete a project and the WAVs it produced; refused while it is rendering."""
    return invoke(service.delete, body.get('project_id'))


@router.get('/presets')
def presets():
    return invoke(service.presets)


@router.post('/presets')
def save_preset(body: dict = Depends(json_body)):
    return invoke(service.save_preset, body)


@router.delete('/presets/{name}')
def delete_preset(name: str):
    return invoke(service.delete_preset, name)


@router.post('/recover')
def recover(body: dict = Depends(json_body)):
    """Un-wedge a stuck profile after the user verified no render is running."""
    if body.get('confirm') is not True:
        raise HTTPException(status_code=409,
                            detail='confirm all old ACE workers have stopped before recovery')
    return invoke(lambda: service.recover_interrupted(confirmed=True))


# Bounded to keep JSON + plugin-host IPC copies manageable; not a streaming API.
MAX_DESKTOP_AUDIO_BYTES = 8 * 1024 * 1024


@router.get('/projects/{project_id}/takes/{take_id}/peaks')
def peaks(project_id: str, take_id: str, buckets: int = 512, edit: str | None = None):
    """Waveform buckets computed here, so the client never downloads the WAV.

    ``edit=<edit_id>`` asks for the named QC edit's waveform (the trimmed file),
    which is a different length from the raw take by design.
    """
    from music_studio.peaks import validate_buckets
    try:
        validate_buckets(buckets)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return invoke(service.take_peaks, project_id, take_id, buckets, edit_id=edit)


@router.get('/projects/{project_id}/takes/{take_id}/audio-json')
def audio_json(project_id: str, take_id: str, edit: str | None = None):
    audio_file = invoke(Jobs(service.store()).open_audio, project_id, take_id,
                        edit_id=edit)
    with audio_file:
        data = audio_file.read(MAX_DESKTOP_AUDIO_BYTES + 1)
    if len(data) > MAX_DESKTOP_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail='Audio exceeds the 8 MiB desktop playback limit')
    return {'success': True, 'media_type': 'audio/wav', 'audio_base64': base64.b64encode(data).decode('ascii')}


def _byte_range(header, size):
    """``(start, end)`` inclusive for a single ``bytes=`` range, None for no/odd ranges, or
    ``'unsatisfiable'``. Multi-range and suffix forms other than ``bytes=-N`` are served whole."""
    if not header or not header.startswith('bytes=') or ',' in header:
        return None
    first, _, last = header[len('bytes='):].strip().partition('-')
    try:
        if not first:
            length = int(last)
            if length <= 0:
                return None
            return max(0, size - length), size - 1
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return None
    if start >= size:
        return 'unsatisfiable'
    if end < start:
        return None
    return start, min(end, size - 1)


def _stream(audio_file, headers, range_header=None):
    """Stream a take, but say how long it is, and answer byte ranges.

    A chunked response has no Content-Length, and a media element that cannot learn the total
    length reports ``duration === Infinity`` — which leaves the player's scrubber with a max of
    zero and nothing to drag. The size is read from the file itself, so the header is exact.
    A browser seeks by asking ``Range: bytes=N-``; answered with the whole file as 200, the
    player snaps back to the start whenever someone clicks the waveform or drags the bar.
    """
    headers = dict(headers)
    size = None
    try:
        position = audio_file.tell()
        size = audio_file.seek(0, os.SEEK_END)
        audio_file.seek(position)
        headers.setdefault('Content-Length', str(size))
        headers['Accept-Ranges'] = 'bytes'
    except (AttributeError, OSError, ValueError):
        pass
    span = _byte_range(range_header, size) if size is not None else None
    if span == 'unsatisfiable':
        audio_file.close()
        return Response(status_code=416, headers={'Content-Range': f'bytes */{size}',
                                                  'X-Content-Type-Options': 'nosniff'})
    remaining = None
    status = 200
    if span is not None:
        start, end = span
        audio_file.seek(start)
        remaining = end - start + 1
        status = 206
        headers['Content-Length'] = str(remaining)
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'

    def chunks():
        left = remaining
        with audio_file:
            while left is None or left > 0:
                chunk = audio_file.read(64 * 1024 if left is None else min(64 * 1024, left))
                if not chunk:
                    break
                if left is not None:
                    left -= len(chunk)
                yield chunk
    return StreamingResponse(chunks(), status_code=status, media_type='audio/wav', headers=headers)


@router.get('/projects/{project_id}/takes/{take_id}/audio')
def audio(project_id: str, take_id: str, request: Request, edit: str | None = None):
    audio_file = invoke(Jobs(service.store()).open_audio, project_id, take_id,
                        edit_id=edit)
    return _stream(audio_file, {'X-Content-Type-Options': 'nosniff'},
                   request.headers.get('range'))


@router.get('/projects/{project_id}/takes/{take_id}/download')
def download(project_id: str, take_id: str, edit: str | None = None):
    """Same bytes as /audio, but named as a file for browser/OS download."""
    audio_file = invoke(Jobs(service.store()).open_audio, project_id, take_id,
                        edit_id=edit)
    name = edit or take_id
    return _stream(audio_file, {
        'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': f'attachment; filename="{name}.wav"',
    })


# ---------------------------------------------------------------- standalone
# The standalone server exposes a richer local UI plus network/hardware routes.
# They are mounted here too, without importing standalone.app (which would set
# the process-global HEWN_STUDIO_DATA_DIR and break profile-scoped storage).

@router.get('/studio/ui')
def studio_ui():
    """The standalone UI, served from the committed static files."""
    path = _STANDALONE_STATIC / 'index.html'
    if not path.is_file():
        raise HTTPException(status_code=404, detail='studio UI not found')
    return FileResponse(path, media_type='text/html; charset=utf-8',
                        headers={'X-Content-Type-Options': 'nosniff'})


@router.get('/studio/assets/{name}')
def studio_asset(name: str):
    """One of the three known UI files; any other name is a 404."""
    known = _STUDIO_ASSETS.get(name)
    if known is None:
        raise HTTPException(status_code=404, detail='asset not found')
    filename, media_type = known
    path = _STANDALONE_STATIC / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail='asset not found')
    return FileResponse(path, media_type=media_type,
                        headers={'X-Content-Type-Options': 'nosniff'})


@router.get('/studio/sample')
def studio_sample():
    """The shipped instrumental sample, as the standalone UI expects."""
    from music_studio.sample import sample_response
    return sample_response()


@router.get('/studio/covers/{project_id}/json')
def studio_cover_json(project_id: str):
    """The stored cover as base64 JSON, the shape ctx.rest can carry.

    The workspace router owns the safe lookup: UUID validation, project
    existence and the MIME magic-byte check stay there. This route only
    re-frames the same bytes for a JSON client, with the same 2 MiB cap.
    """
    cover = workspace_cover(project_id)
    data = cover.body
    if len(data) > MAX_COVER_BYTES:
        raise HTTPException(status_code=413,
                            detail='cover exceeds the 2 MiB limit')
    return {'success': True, 'mime': cover.media_type,
            'data': base64.b64encode(data).decode('ascii')}


def _studio_call(fn, *args, **kwargs):
    """Map target errors to the standalone app's status codes."""
    try:
        return fn(*args, **kwargs)
    except KeyError:
        raise HTTPException(status_code=404, detail='target not found') from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get('/studio/targets')
def studio_targets():
    return _studio_call(service.targets)


@router.post('/studio/targets')
def create_studio_target(body: dict = Depends(json_body)):
    return _studio_call(service.create_target, body)


@router.delete('/studio/targets/{target_id}')
def delete_studio_target(target_id: str):
    return _studio_call(service.delete_target, target_id)


@router.post('/studio/targets/{target_id}/probe')
def probe_studio_target(target_id: str):
    return _studio_call(service.probe_target, target_id)


@router.get('/studio/hardware')
def studio_hardware():
    return _studio_call(service.hardware)


@router.get('/studio/host')
def studio_host():
    """URL of the loopback host the desktop pane frames (same UI, same library).

    The pane cannot render the standalone HTML through the plugin SDK, so a
    small host serves it on loopback reading this profile's plugin data. First
    call starts the host; later calls reuse it.
    """
    from music_studio import host_launcher

    try:
        return host_launcher.ensure_host()
    except Exception as exc:  # noqa: BLE001 - the pane needs the reason, not a traceback
        raise HTTPException(status_code=503, detail=f'studio host unavailable: {exc}') from None



@router.get('/settings')
def studio_settings():
    """Saved endpoint + MiniMax Music 3 knobs, their ranges, and the library."""
    return invoke(service.settings_info)


@router.post('/settings')
def save_studio_settings(body: dict = Depends(json_body)):
    return invoke(service.save_settings, body)


@router.post('/export')
def export_studio_take(body: dict):
    """Copy a finished take into the person's own music folder."""
    project_id = str(body.get('project_id') or '')
    take_id = str(body.get('take_id') or '')
    if not project_id or not take_id:
        raise HTTPException(400, 'project_id and take_id are required')
    from music_studio.export import ExportError

    try:
        return service.export_take(project_id, take_id, folder=body.get('folder'),
                                   title=body.get('title'))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except ExportError as exc:
        # A folder that cannot be written (permissions, a stale mount) is the person's problem to
        # fix, so it gets a sentence and a 507 rather than a stack trace in the console.
        raise HTTPException(507, str(exc)) from None


@router.post('/host/close')
def studio_host_close():
    """Close the studio from its own page. The reply goes out before the process stops."""
    from music_studio import host_launcher

    host_launcher.shutdown_self()
    return {'success': True, 'closing': True}


@router.post('/discover')
def studio_discover():
    """Every ComfyUI this machine can see, so a first run is a choice, not an error."""
    return _studio_call(service.discover_renderers)


@router.post('/settings/test')
def test_studio_endpoint(body: dict = Depends(json_body)):
    """Probe a ComfyUI endpoint (the posted one, else the saved one)."""
    return invoke(service.test_endpoint, body.get('url'))


@router.get('/library')
def studio_library():
    return invoke(service.library_info)


@router.post('/library/relocate')
def relocate_studio_library(body: dict = Depends(json_body)):
    """Copy or move the library to another folder and point this profile at it."""
    return invoke(service.relocate_library, body.get('path'), body.get('mode') or 'copy')


@router.post('/library/reset')
def reset_studio_library():
    return invoke(service.reset_library)


@router.post('/duplicate')
def duplicate_project(body: dict = Depends(json_body)):
    """Copy a project, its takes and their audio under a new id."""
    return invoke(service.duplicate_project, body.get('project_id'), body.get('title'))


@router.post('/take/delete')
def delete_take(body: dict = Depends(json_body)):
    return invoke(service.delete_take, body.get('project_id'), body.get('take_id'))


@router.post('/versions')
def studio_versions(body: dict = Depends(json_body)):
    """Queue N takes of the same approved draft, one seed each."""
    return invoke(service.versions, body.get('project_id'), int(body.get('count') or 3),
                  body.get('seed_base'))


@router.post('/section/render')
def studio_section_render(body: dict = Depends(json_body)):
    """Render one section as its own take, so it can be auditioned before merging."""
    return invoke(service.render_section, body.get('project_id'), body.get('take_id'),
                  body.get('start_s'), body.get('end_s'), body.get('caption'))


@router.post('/section/splice')
def studio_section_splice(body: dict = Depends(json_body)):
    """Merge an auditioned section into its parent as a new take."""
    return invoke(service.splice_section, body.get('project_id'), body.get('take_id'),
                  body.get('candidate_take_id'), body.get('fade_ms') or 40)


@router.post('/fade')
def studio_fade(body: dict = Depends(json_body)):
    """Fade a take out as a new take."""
    return invoke(service.fade_take, body.get('project_id'), body.get('take_id'),
                  body.get('seconds') or 1.5)


@router.post('/studio/draft')
def studio_draft(body: dict = Depends(json_body)):
    """Draft a song with the agent's own model, from the process that owns it.

    The framed pane cannot reach Hermes' model facade directly (it is a sandboxed page talking to a
    separate loopback host), so the pane forwards a draft request here over its plugin route and
    posts the answer back. One model path, one place it lives.
    """
    from music_studio import llm_bridge
    from standalone.ideas import build_request, parse_draft

    prompt = str(body.get('prompt') or '').strip()
    if not prompt:
        raise HTTPException(400, 'prompt is required')
    if not llm_bridge.available():
        raise HTTPException(503, llm_bridge.MISSING)
    try:
        request = build_request(body.get('provider'), body.get('model'),
                                [{'role': 'user', 'content': prompt}], 'draft',
                                body.get('tone'), int(body.get('style') or 50))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        content = llm_bridge.complete(request['messages'], provider=request['provider'],
                                      model=request['model'], max_tokens=request['max_tokens'],
                                      temperature=request['temperature'])
    except llm_bridge.LlmUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except llm_bridge.LlmTrustError as exc:
        raise HTTPException(403, str(exc)) from None
    draft = parse_draft(content)
    project = None
    if body.get('create'):
        from music_studio import service
        project = service.store().create(**draft)
    return {'success': True, 'draft': draft, 'written_by': 'ctx.llm', 'project': project}


@router.get('/verify.json')
def verify_json():
    """The same measurements the page shows, for a script or a bug report."""
    from music_studio import verify as verify_module
    return verify_module.summary()


@router.get('/verify')
def verify_page():
    """The product's own proof page: identity + what it can do on this machine, measured now."""
    from fastapi.responses import HTMLResponse
    from music_studio import verify as verify_module
    info = verify_module.summary()
    return HTMLResponse(verify_module.render_html(info), status_code=200 if info['ok'] else 200)


@router.get('/v1/version')
def version_route():
    """Exactly which build is answering: version, commit, dates, dirty state, channel.

    Same source as the chip in the studio header, so a screenshot can be matched to a commit.
    """
    from music_studio.version_stamp import as_dict
    info = as_dict()
    return {**info, 'name': 'Hermes Music Studio'}


@router.get('/about')
def studio_about():
    """Version, contribution links, and whether REVamp is around."""
    from music_studio import about as about_module

    return about_module.about()


@router.post('/about/diagnostics')
def studio_diagnostics():
    """A paste-ready bug report built from this process's own facts."""
    from music_studio import about as about_module

    return invoke(about_module.diagnostics)


router.include_router(ideas_router)
router.include_router(workspaces_router)
router.include_router(revamp_router)


def _autostart_host() -> None:
    """Start the loopback studio host beside the gateway, in the background.

    The desktop pane frames that host, so it should exist after any gateway
    restart without waiting for the first click. Skipped in tests, in the
    standalone server (it *is* the host), and when HEWN_STUDIO_NO_HOST says so.
    """
    if os.environ.get('HEWN_STUDIO_NO_HOST') or 'pytest' in sys.modules:
        return

    def start():
        try:
            from music_studio import host_launcher
            host_launcher.ensure_host()
        except Exception:  # noqa: BLE001 - a host that cannot start must not break the plugin
            import logging
            logging.getLogger(__name__).exception('Music Studio host did not start')

    threading.Thread(target=start, name='music-studio-host', daemon=True).start()


# -- setting up an engine, and updating the install ------------------------
# Both follow the shape the Compute screen renders: probe what exists, name the check that failed,
# and never claim a step that has not happened.

def _studio_data():
    return service.plugin_data_dir(service.PLUGIN_ID)


@router.post('/studio/setup/comfy')
def setup_comfy(body: dict = Depends(json_body)):
    """Test a ComfyUI address, then save it if it can actually render Music 3."""
    from music_studio import setup as studio_setup

    result = studio_setup.probe_comfy(body.get('url'))
    if result.get('ok') and body.get('save', True):
        service.save_settings({'comfy_url': result['url']})
        result['saved'] = True
    return result


@router.post('/studio/setup/ace')
def setup_ace(body: dict = Depends(json_body)):
    """Test an ACE-Step server address and keep it as the remote engine's address."""
    from music_studio import setup as studio_setup

    result = studio_setup.probe_ace(body.get('url'))
    if result.get('ok') and body.get('save', True):
        service.save_settings({'ace_remote_url': result['url']})
        result['saved'] = True
    return result


@router.get('/studio/setup/hf')
def setup_hf_status():
    from music_studio import setup as studio_setup

    return {'success': True, 'status': studio_setup.hf_status()}


@router.post('/studio/setup/hf')
def setup_hf(body: dict = Depends(json_body)):
    """Plan a Hugging Face download, or start one when `start` is true."""
    from music_studio import setup as studio_setup

    data = _studio_data()
    plan = studio_setup.hf_plan(body.get('model_id'), data)
    if not body.get('start'):
        return {'success': True, 'plan': plan, 'status': studio_setup.hf_status()}
    started = studio_setup.hf_download(body.get('model_id'), data,
                                       revision=body.get('revision') or None)
    return {'success': True, 'plan': plan, 'status': started.get('status')}


@router.post('/studio/setup/hf/copy')
def setup_hf_copy(body: dict = Depends(json_body)):
    from music_studio import setup as studio_setup

    return studio_setup.copy_into_comfy(body.get('folder'), _studio_data())


@router.get('/studio/setup/cloud')
def setup_cloud_list():
    from music_studio import setup as studio_setup

    return {'success': True, 'endpoints': studio_setup.list_cloud(_studio_data())}


@router.post('/studio/setup/cloud')
def setup_cloud_save(body: dict = Depends(json_body)):
    from music_studio import setup as studio_setup

    return studio_setup.save_cloud(_studio_data(), body.get('name') or 'default',
                                   body.get('url'), body.get('key'), body.get('model') or '')


@router.post('/studio/setup/cloud/test')
def setup_cloud_test(body: dict = Depends(json_body)):
    from music_studio import setup as studio_setup

    return studio_setup.probe_cloud(_studio_data(), body.get('name') or 'default')


@router.get('/update')
def update_check():
    """The newest published build, and whether this copy is behind it."""
    from music_studio import updates

    return updates.check()


@router.get('/update/job')
def update_job():
    from music_studio import updates

    job = updates.job(_studio_data())
    job['log'] = updates.log_tail(_studio_data())
    return job


@router.post('/update/apply')
def update_apply(body: dict = Depends(json_body)):
    """Install the newest published build in a detached process; the page polls /update/job."""
    from music_studio import updates

    return {'success': True, 'job': updates.apply(_studio_data(), body.get('ref'))}


@router.post('/update/undo')
def update_undo():
    from music_studio import updates

    return {'success': True, 'job': updates.undo(_studio_data())}

@router.post('/preview/chorus')
def preview_chorus(body: dict = Depends(json_body)):
    """Hear the chorus of this draft before building the whole song."""
    from music_studio import chorus

    project_id = body.get('project_id')
    if not project_id:
        raise HTTPException(status_code=400, detail='project_id is required')
    return {'success': True,
            'preview': invoke(lambda: chorus.preview(project_id, body.get('seconds') or 25))}

# -- one set of instructions, two engines ---------------------------------

@router.post('/compare')
def compare_start(body: dict = Depends(json_body)):
    """Render the same approved draft on each ready engine, one after another."""
    from music_studio import compare

    project_id = body.get('project_id')
    if not project_id:
        raise HTTPException(status_code=400, detail='project_id is required')
    return {'success': True,
            'plan': invoke(lambda: compare.start(project_id, body.get('engines'), body.get('seed')))}


@router.get('/compare/engines')
def compare_engines():
    """Which engines a comparison can use on this machine, right now."""
    from music_studio import compare

    return {'success': True, 'engines': compare.available_engines()}

@router.get('/compare/{project_id}')
def compare_status(project_id: str):
    from music_studio import compare

    return {'success': True, 'plan': compare.status(project_id)}


_autostart_host()

