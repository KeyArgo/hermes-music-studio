"""Optional REVamp integration through its installed local client."""
import hashlib
import os
import re
import runpy
import shutil
import urllib.parse
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()
# Music Studio keeps exactly one playlist in REVamp. Everything the studio hands over lands
# here, so its own screen can list those items and nothing else — no audiobooks, no albums.
STUDIO_PLAYLIST = 'Music Studio'
TRACK_ID = re.compile(r'^[0-9a-f]{16}$')
PLAYLIST_ID = re.compile(r'^[a-zA-Z0-9_-]{1,128}$')

def _call(method, path, body=None):
    executable = os.environ.get('HERMES_MUSIC_REVAMPCTL') or shutil.which('revampctl')
    if not executable:
        executable = str(Path.home() / '.local' / 'bin' / 'revampctl')
    try:
        # The installed REVamp client handles its own local authentication.
        client = runpy.run_path(executable)
        return client['call'](method, path, body=body, timeout=5.0)
    except Exception:
        raise HTTPException(503, 'REVamp is unavailable. Open REVamp in Hermes and try again.') from None

class Name(BaseModel):
    name: str = Field(min_length=1, max_length=80)

class Track(BaseModel):
    track_id: str = Field(min_length=16, max_length=16)
    title: str = Field(default='Track', max_length=200)
    artist: str = Field(default='', max_length=200)

class AddTrack(Track):
    playlist_id: str = Field(min_length=1, max_length=128)

class AddStudioTake(BaseModel):
    project_id: str = Field(min_length=36, max_length=36)
    take_id: str = Field(min_length=36, max_length=36)
    # Optional: the studio's own playlist by default, or any playlist the person picked.
    playlist_id: str | None = Field(default=None, max_length=128)


class PlaylistFromTakes(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    folder: str | None = Field(default=None, max_length=1024)
    takes: list[dict] = Field(default_factory=list)


class RemoveStudioTake(BaseModel):
    item_id: str = Field(min_length=3, max_length=200)


def studio_playlist():
    """The studio's own playlist, created on first use."""
    lists = _call('GET', '/api/playlists')
    rows = lists.get('playlists') if isinstance(lists, dict) else lists
    for row in rows or []:
        if isinstance(row, dict) and row.get('name') == STUDIO_PLAYLIST:
            return row
    return _call('POST', '/api/playlists', {'name': STUDIO_PLAYLIST, 'items': []})


def _studio_items(playlist):
    items = playlist.get('items') if isinstance(playlist, dict) else None
    return [item for item in (items or [])
            if isinstance(item, dict) and str(item.get('source') or '') == 'studio']

def _track(track: Track):
    if not TRACK_ID.fullmatch(track.track_id): raise HTTPException(400, 'invalid track id')
    return {'kind':'track', 'track_id':track.track_id, 'url':'/api/media/'+track.track_id,
            'title':track.title, 'artist':track.artist or None}

@router.get('/studio/revamp/status')
def status():
    """REVamp is optional: report it plainly instead of failing the whole screen."""
    try:
        state = _call('GET', '/api/state')
    except HTTPException:
        return {'available': False, 'player_connected': False,
                'reason': 'REVamp is not installed. Music Studio works on its own — you can '
                          'still play and export your takes.'}
    live = state.get('live', {}) if isinstance(state, dict) else {}
    return {'available': True, 'player_connected': bool(live.get('player_connected'))}

@router.get('/studio/revamp/tracks')
def tracks(q: str = ''):
    if len(q)>200: raise HTTPException(400, 'query too long')
    return _call('GET', '/api/library/tracks?' + urllib.parse.urlencode({'q':q,'limit':50}))

@router.get('/studio/revamp/playlists')
def playlists():
    return {'playlists': _call('GET', '/api/playlists')}

@router.post('/studio/revamp/playlists')
def create_playlist(body: Name):
    name=body.name.strip()
    if not name: raise HTTPException(400, 'name is required')
    return {'playlist': _call('POST', '/api/playlists', {'name':name, 'items':[]})}

@router.post('/studio/revamp/play')
def play(body: Track):
    result = _call('POST', '/api/command', {'action':'load','mode':'replace','play':True,'items':[_track(body)]})
    return {'success':True, 'delivered':result.get('delivered', 0)}

@router.post('/studio/revamp/add')
def add_to_playlist(body: AddTrack):
    if not PLAYLIST_ID.fullmatch(body.playlist_id): raise HTTPException(400, 'invalid playlist id')
    path='/api/playlists/'+urllib.parse.quote(body.playlist_id, safe='')
    playlist=_call('GET', path)
    if not isinstance(playlist,dict) or not isinstance(playlist.get('items'),list):
        raise HTTPException(502, 'REVamp returned an invalid playlist')
    result=_call('PUT', path, {'items':[*playlist['items'], _track(body)]})
    return {'success':True,'playlist':result}

@router.post('/studio/revamp/add-take')
def add_take_to_playlist(body: AddStudioTake, request: Request):
    """Put one finished studio take into the studio's own REVamp playlist."""
    try:
        uuid.UUID(body.project_id); uuid.UUID(body.take_id)
        from music_studio import service
        project = service.store().get(body.project_id)
        take = next(t for t in project.get('takes', []) if t.get('take_id') == body.take_id)
        if take.get('state') != 'succeeded':
            raise ValueError('take is not finished')
    except (ValueError, FileNotFoundError, KeyError, StopIteration):
        raise HTTPException(404, 'finished take not found') from None
    if body.playlist_id:
        if not PLAYLIST_ID.fullmatch(body.playlist_id):
            raise HTTPException(400, 'invalid playlist id')
        playlist_id = body.playlist_id
    else:
        playlist = studio_playlist()
        playlist_id = str(playlist.get('id') or '')
    if not PLAYLIST_ID.fullmatch(playlist_id):
        raise HTTPException(502, 'REVamp returned an invalid playlist')
    path = '/api/playlists/' + urllib.parse.quote(playlist_id, safe='')
    current = _call('GET', path)
    if not isinstance(current, dict) or not isinstance(current.get('items'), list):
        raise HTTPException(502, 'REVamp returned an invalid playlist')
    item = {'id': 'studio:' + body.project_id + ':' + body.take_id, 'source': 'studio',
            'kind': 'music', 'title': project.get('title') or 'Music Studio take',
            'artist': 'Hermes Music Studio',
            'playback': {'backend': 'webamp', 'url': _audio_base(request) +
                         '/api/projects/' + body.project_id + '/takes/' + body.take_id + '/audio'},
            'capabilities': ['seek']}
    items = [row for row in current['items']
             if not (isinstance(row, dict) and row.get('id') == item['id'])]
    result = _call('PUT', path, {'items': [*items, item]})
    return {'success': True, 'playlist': result, 'item': item, 'requires_studio_running': True}


def _audio_base(request: Request) -> str:
    """A URL the REVamp player can fetch.

    Under the Hermes plugin the page is behind gateway auth, so REVamp cannot read that
    address; the studio's own loopback host serves the same audio with no credentials.
    """
    if '/api/plugins/hermes-music-studio/' not in request.url.path:
        return str(request.base_url).rstrip('/')
    from music_studio import host_launcher
    return str(host_launcher.ensure_host().get('url') or str(request.base_url).rstrip('/')).rstrip('/')


@router.get('/studio/revamp/added')
def added(request: Request):
    """Only the takes Music Studio handed over — never the rest of the REVamp library."""
    playlist = studio_playlist()
    playlist_id = str(playlist.get('id') or '')
    if not PLAYLIST_ID.fullmatch(playlist_id):
        raise HTTPException(502, 'REVamp returned an invalid playlist')
    current = _call('GET', '/api/playlists/' + urllib.parse.quote(playlist_id, safe=''))
    items = _studio_items(current)
    base = _audio_base(request)
    for item in items:
        playback = item.get('playback') or {}
        url = str(playback.get('url') or '')
        marker = '/api/projects/'
        if marker in url:
            playback['url'] = base + url[url.index(marker):]
    return {'success': True, 'playlist': playlist_id, 'name': STUDIO_PLAYLIST, 'items': items}



def _track_id(path: str) -> str:
    """REVamp's own track identity: sha1 of the absolute path, first 16 hex characters."""
    return hashlib.sha1(os.path.abspath(path).encode('utf-8')).hexdigest()[:16]


def _ensure_library_folder(folder: str) -> None:
    """REVamp can only play what it has indexed, so the folder it writes into must be one
    of its library folders."""
    payload = _call('GET', '/api/library/folders')
    rows = payload.get('folders') if isinstance(payload, dict) else payload
    rows = [row for row in (rows or []) if isinstance(row, dict) and row.get('path')]
    paths = [os.path.normpath(str(row['path'])) for row in rows]
    wanted = os.path.normpath(folder)
    # A folder inside a library folder is already covered by that folder's scan; adding it
    # again would nest two roots and confuse the library.
    for path in paths:
        if wanted == path or wanted.startswith(path.rstrip(os.sep) + os.sep):
            return
    cleaned = [{'path': row['path'], 'kind': row.get('kind') or 'music'} for row in rows]
    cleaned.append({'path': wanted, 'kind': 'music'})
    _call('POST', '/api/library/folders', {'folders': cleaned})


@router.post('/studio/revamp/playlist')
def playlist_from_takes(body: PlaylistFromTakes):
    """Export the takes first, then build a REVamp playlist that points at those files.

    Order matters and is the whole point: a playlist entry that points at nothing is worse
    than no playlist. Each take is written to the person's folder, REVamp is told to index
    that folder, and only then is the playlist created — its items reference REVamp's own
    ``/api/media/<id>`` for the exported file, so playback no longer depends on this studio
    being open.
    """
    from music_studio import service, settings as st

    if not body.takes:
        raise HTTPException(400, 'choose at least one take')
    name = body.name.strip()
    if not name:
        raise HTTPException(400, 'a playlist needs a name')
    base = service.default_data_dir(service.PLUGIN_ID)
    folder = (body.folder or st.read(base).get('export_dir') or '').strip()
    files = []
    for row in body.takes:
        project_id = str(row.get('project_id') or '')
        take_id = str(row.get('take_id') or '')
        if not project_id or not take_id:
            raise HTTPException(400, 'each take needs project_id and take_id')
        try:
            written = service.export_take(project_id, take_id, folder=folder or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        files.append(written)
    destination = files[0]['folder']
    _ensure_library_folder(destination)
    _call('POST', '/api/library/rescan', {})
    items = []
    for written in files:
        tid = _track_id(written['path'])
        # The song's name, not the file's: the file is "Song (4).wav" when a folder already
        # holds copies, and a playlist should read like a playlist.
        title = written.get('title') or os.path.splitext(os.path.basename(written['path']))[0]
        items.append({'kind': 'track', 'track_id': tid, 'url': '/api/media/' + tid,
                      'title': title, 'artist': 'Hermes Music Studio',
                      'playback': {'backend': 'webamp', 'url': '/api/media/' + tid}})
    existing = _call('GET', '/api/playlists')
    rows = existing.get('playlists') if isinstance(existing, dict) else existing
    match = next((row for row in rows or []
                  if isinstance(row, dict) and str(row.get('name')) == name), None)
    if match and PLAYLIST_ID.fullmatch(str(match.get('id') or '')):
        result = _call('PUT', '/api/playlists/' + urllib.parse.quote(str(match['id']), safe=''),
                       {'items': items})
    else:
        result = _call('POST', '/api/playlists', {'name': name, 'items': items})
    return {'success': True, 'playlist': result, 'files': [row['path'] for row in files],
            'folder': destination}


@router.post('/studio/revamp/remove')
def remove_from_playlist(body: RemoveStudioTake):
    """Take one item back out of the studio's REVamp playlist."""
    playlist = studio_playlist()
    playlist_id = str(playlist.get('id') or '')
    if not PLAYLIST_ID.fullmatch(playlist_id):
        raise HTTPException(502, 'REVamp returned an invalid playlist')
    path = '/api/playlists/' + urllib.parse.quote(playlist_id, safe='')
    current = _call('GET', path)
    items = [row for row in (current.get('items') if isinstance(current, dict) else []) or []
             if not (isinstance(row, dict) and row.get('id') == body.item_id)]
    result = _call('PUT', path, {'items': items})
    return {'success': True, 'playlist': result, 'items': _studio_items(result)}
