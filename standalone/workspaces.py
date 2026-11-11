"""Standalone workspace groupings and uploaded cover art."""
import base64
import binascii
from music_studio.portalock import lock_ex, unlock
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter()
MAX_BODY = 3 * 1024 * 1024
MAX_IMAGE = 2 * 1024 * 1024
TYPES = {'image/png': ('.png', b'\x89PNG\r\n\x1a\n'), 'image/jpeg': ('.jpg', b'\xff\xd8\xff'), 'image/webp': ('.webp', b'RIFF')}

class WorkspaceStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'studio-workspaces.json'

    @contextmanager
    def lock(self):
        with (self.root / '.studio-workspaces.lock').open('a') as handle:
            lock_ex(handle)
            try: yield
            finally: unlock(handle)

    def _read(self):
        if not self.path.exists():
            return [{'id': 'default', 'name': 'My workspace', 'project_ids': []}]
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data, list): raise ValueError('invalid workspace data')
        return data

    def _write(self, data):
        fd, path = tempfile.mkstemp(prefix='.workspace-', dir=self.root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, ensure_ascii=False)
                handle.flush(); os.fsync(handle.fileno())
            os.replace(path, self.path)
            dfd = os.open(str(self.root), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try: os.fsync(dfd)
            finally: os.close(dfd)
        finally:
            if os.path.exists(path): os.unlink(path)

    def list(self):
        with self.lock(): return self._read()

    def create(self, name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValueError('workspace name must be 1–80 characters')
        item = {'id': str(uuid.uuid4()), 'name': name.strip(), 'project_ids': []}
        with self.lock():
            data = self._read(); data.append(item); self._write(data)
        return item

    def assign(self, project_id, workspace_id):
        if not isinstance(project_id, str) or not project_id or len(project_id) > 100:
            raise ValueError('invalid project id')
        with self.lock():
            data = self._read()
            target = next((w for w in data if w['id'] == workspace_id), None)
            if target is None: raise ValueError('unknown workspace')
            for w in data: w['project_ids'] = [p for p in w['project_ids'] if p != project_id]
            target['project_ids'].append(project_id); self._write(data)
        return target

def _store():
    from music_studio import service
    return service.store()

def _project(project_id):
    try:
        if not isinstance(project_id, str): raise ValueError('invalid project id')
        uuid.UUID(project_id)
        store = _store(); store.get(project_id)
        return store
    except (ValueError, FileNotFoundError, KeyError):
        raise HTTPException(404, 'project not found') from None

async def _body(request, limit=16384):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > limit: raise HTTPException(413, 'request too large')
    try: data = json.loads(content)
    except (ValueError, UnicodeDecodeError): raise HTTPException(400, 'invalid JSON') from None
    if not isinstance(data, dict): raise HTTPException(400, 'invalid request')
    return data

@router.get('/studio/workspaces')
def list_workspaces():
    return {'workspaces': WorkspaceStore(_store().root).list()}

@router.post('/studio/workspaces')
async def create_workspace(request: Request):
    data = await _body(request)
    try: return {'workspace': WorkspaceStore(_store().root).create(data.get('name'))}
    except ValueError as exc: raise HTTPException(400, str(exc)) from None

@router.post('/studio/assign')
async def assign_workspace(request: Request):
    data = await _body(request); store = _project(data.get('project_id', ''))
    try: return {'workspace': WorkspaceStore(store.root).assign(data['project_id'], data.get('workspace_id'))}
    except ValueError as exc: raise HTTPException(400, str(exc)) from None

@router.post('/studio/covers/{project_id}')
async def save_cover(project_id: str, request: Request):
    store = _project(project_id); data = await _body(request, MAX_BODY)
    return _write_cover(store, project_id, data)

def _write_cover(store, project_id, data):
    mime, encoded = data.get('mime'), data.get('data')
    if mime not in TYPES or not isinstance(encoded, str): raise HTTPException(400, 'unsupported image')
    try: image = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError): raise HTTPException(400, 'invalid image') from None
    if len(image) > MAX_IMAGE: raise HTTPException(413, 'image exceeds 2 MiB')
    ext, magic = TYPES[mime]
    if not image.startswith(magic) or (mime == 'image/webp' and image[8:12] != b'WEBP'):
        raise HTTPException(400, 'image does not match its type')
    directory = Path(store.root) / 'studio-covers'; directory.mkdir(exist_ok=True)
    # One fixed extension; the MIME accompanies bytes in a small header.
    fd, temp = tempfile.mkstemp(prefix='.cover-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write((mime + '\n').encode('ascii') + image)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, directory / (project_id + '.cover'))
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return {'success': True}

@router.post('/studio/covers/{project_id}/generate')
async def generate_cover(project_id: str, request: Request):
    _project(project_id)
    data = await _body(request)
    prompt = data.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
        raise HTTPException(400, 'art direction must contain 1–2000 characters')
    from starlette.concurrency import run_in_threadpool
    from standalone.ideas import _run_hermes
    image = await run_in_threadpool(_run_hermes, {'action':'art', 'prompt':prompt.strip()})
    return _write_cover(_project(project_id), project_id, image)

@router.get('/studio/covers/{project_id}')
def get_cover(project_id: str):
    from fastapi.responses import Response
    store = _project(project_id)
    path = Path(store.root) / 'studio-covers' / (project_id + '.cover')
    try: mime, image = path.read_bytes().split(b'\n', 1)
    except (OSError, ValueError): raise HTTPException(404, 'cover not found') from None
    if mime.decode('ascii', errors='ignore') not in TYPES: raise HTTPException(404, 'cover not found')
    return Response(image, media_type=mime.decode('ascii'), headers={'X-Content-Type-Options':'nosniff'})
