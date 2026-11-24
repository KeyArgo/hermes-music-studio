"""Optional REVamp integration through its installed local client."""
import hashlib
import json
import os
import re
import runpy
import shutil
import threading
import time
import urllib.error
import urllib.parse
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from music_studio import media_sign

router = APIRouter()
STUDIO_PLAYLIST = 'Music Studio'
TRACK_ID = re.compile(r'^[0-9a-f]{16}$')
PLAYLIST_ID = re.compile(r'^[a-zA-Z0-9_-]{1,128}$')

REVAMPCTL_NAME = 'revampctl'
# The client is a Python script, not an executable: on Windows it has no
# extension and never lands on PATH, so shutil.which('revampctl') misses it.
REVAMPCTL_VARIANTS = ('', '.py')
INSTALL = 'hermes plugins install KeyArgo/hermes-revamp'
# Leave room for revampctl's listener proof (2s) and request (5s) on the last poll.
INDEX_WAIT = 12.0
_playlist_create_lock = threading.Lock()


def _hermes_homes():
    """The running Hermes home, from Hermes' own public helper."""
    from music_studio.hermes_home import active_home
    home = active_home()
    return [home] if home else []


def _python_client(path):
    if not path or Path(path).suffix.lower() not in REVAMPCTL_VARIANTS:
        return False
    if not Path(path).is_file():
        return False
    try:
        source = Path(path).read_bytes()
        compile(source, str(path), 'exec')
    except (OSError, ValueError, SyntaxError):
        return False
    return True


def _client_path():
    """Locate REVamp's installed ``revampctl`` client, or None.

    Order: an explicit override, then PATH, then the two places the client is
    actually installed — ``~/.local/bin`` on a POSIX host and the REVamp plugin's
    own ``bin/`` inside the Hermes home, which is where a Windows install puts it.
    """
    override = os.environ.get('HERMES_MUSIC_REVAMPCTL')
    if _python_client(override):
        return override

    for name in REVAMPCTL_VARIANTS:
        found = shutil.which(REVAMPCTL_NAME + name)
        if _python_client(found):
            return found

    roots = [Path.home() / '.local' / 'bin']
    for home in _hermes_homes():
        roots.append(home / 'plugins' / 'revamp' / 'bin')
    for root in roots:
        for name in REVAMPCTL_VARIANTS:
            candidate = root / (REVAMPCTL_NAME + name)
            if _python_client(candidate):
                return str(candidate)
    return None


def _call(method, path, body=None):
    executable = _client_path()
    if not executable:
        raise HTTPException(503, 'REVamp is not installed: add its revampctl client or set '
                                 'HERMES_MUSIC_REVAMPCTL.')
    try:
        # The installed REVamp client handles its own local authentication.
        client = runpy.run_path(executable)
        return client['call'](method, path, body=body, timeout=5.0)
    except urllib.error.HTTPError as exc:
        with exc:
            try:
                payload = json.loads(exc.read(8192))
                detail = payload.get('error') if isinstance(payload, dict) else None
            except (ValueError, OSError):
                detail = None
        message = ' '.join(str(detail or exc.reason).split())
        message = ''.join(c for c in message if c.isprintable())
        raise HTTPException(exc.code, f'REVamp refused {method} {path}: {exc.code} {message}'[:300]) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        raise HTTPException(503, 'REVamp is installed but not running. Open REVamp in Hermes.') from None
    except Exception:
        raise HTTPException(502, 'The installed REVamp client could not complete the request.') from None


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
    playlist_id: str | None = Field(default=None, max_length=128)
    item_id: str = Field(min_length=3, max_length=200)


def studio_playlist():
    """The studio's own playlist, created on first use."""
    return _create_or_select(STUDIO_PLAYLIST, [])['playlist']


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
    installed = bool(_client_path())
    port = os.environ.get('REVAMP_PORT', '8114')
    url = f'http://127.0.0.1:{int(port)}/' if re.fullmatch(r'[0-9]{1,5}', port) and 0 < int(port) < 65536 else None
    result = {'installed': installed, 'reachable': False, 'available': False, 'player_connected': False,
              'install': INSTALL, 'url': url, 'reason': 'REVamp is not installed.'}
    if installed:
        try:
            state = _call('GET', '/api/state')
            result.update(reachable=True, available=True, reason='',
                          player_connected=bool(((state if isinstance(state, dict) else {}).get('live') or {})
                                                .get('player_connected')))
        except HTTPException as exc:
            result['reason'] = exc.detail
    return result

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
    return _create_or_select(name, [])

def _rows(value):
    """REVamp's playlist summaries: a bare list of dicts. Anything else is read as no rows, never a crash."""
    rows = value.get('playlists') if isinstance(value, dict) else value
    return [row for row in rows or [] if isinstance(row, dict)
            and isinstance(row.get('id'), str) and isinstance(row.get('name'), str)]


def _existing_playlist(name):
    for row in _rows(_call('GET', '/api/playlists')):
        if row['name'].casefold() == name.casefold():
            return {'success': True, 'existing': True, 'playlist': row,
                    'message': f"A playlist named {row['name']} already exists — it is selected"}


def _create_or_select(name, items):
    with _playlist_create_lock:
        match = _existing_playlist(name)
        if match:
            return match
        return {'playlist': _call('POST', '/api/playlists', {'name': name, 'items': items})}


@router.post('/studio/revamp/play')
def play(body: Track):
    result = _call('POST', '/api/command', {'action':'load','mode':'replace','play':True,'items':[_track(body)]})
    # An older REVamp answers without these fields; read them as "not confirmed", not a crash.
    result = result if isinstance(result, dict) else {}
    accepted, delivered = bool(result.get('accepted', True)), int(result.get('delivered') or 0)
    return {'success': bool(accepted and delivered), 'accepted': accepted, 'delivered': delivered,
            'detail': '' if accepted and delivered else "Open REVamp's player first"}

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
    """Export one finished take into the selected REVamp playlist."""
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
    # REVamp plays a web address only through its station relay, which refuses this computer's own
    # addresses, so a link to the studio never plays there. Hand REVamp a file in its own library
    # instead, the way Radio does: it then plays even while the studio is closed.
    title = project.get('title') or 'Music Studio take'
    try:
        written = service.export_take(body.project_id, body.take_id, title=title)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        raise HTTPException(400, f'could not export the take for REVamp: {exc}') from None
    indexing = _index_exports(written['folder'], [written])
    item = _studio_item(body.project_id, body.take_id, written)
    current = _call('GET', path)
    if not isinstance(current, dict) or not isinstance(current.get('items'), list):
        raise HTTPException(502, 'REVamp returned an invalid playlist')
    items = [row for row in current['items']
             if not (isinstance(row, dict) and row.get('id') == item['id'])]
    result = _call('PUT', path, {'items': [*items, item]})
    stored_items = result.get('items') if isinstance(result, dict) else None
    stored = next((row for row in stored_items or [] if isinstance(row, dict) and row.get('id') == item['id']), None)
    if stored is None:
        raise HTTPException(502, 'REVamp did not store the take.')
    return {'success': True, 'playlist': result, 'item': stored, 'indexing': indexing,
            'requires_studio_running': False}


def _audio_base(request: Request) -> str:
    """A URL the REVamp player can fetch.

    Under the Hermes plugin the page is behind gateway auth, so REVamp cannot read that
    address; the studio's own loopback host serves the same audio to a link signed for that take.
    """
    if '/api/plugins/hermes-music-studio/' not in request.url.path:
        return str(request.base_url).rstrip('/')
    from music_studio import host_launcher
    return str(host_launcher.ensure_host().get('url') or str(request.base_url).rstrip('/')).rstrip('/')


@router.get('/studio/revamp/added')
def added(request: Request):
    """Only the takes Music Studio handed over — never the rest of the REVamp library."""
    items = []
    for playlist in _rows(_call('GET', '/api/playlists')):
        playlist_id = playlist['id']
        if not PLAYLIST_ID.fullmatch(playlist_id):
            # One playlist with an id this route will not put in a URL must not hide every other
            # playlist's songs: skip it.
            continue
        current = _call('GET', '/api/playlists/' + playlist_id)
        if not isinstance(current, dict) or not isinstance(current.get('items'), list):
            raise HTTPException(502, 'REVamp returned an invalid playlist')
        items.extend(dict(item, playlist_id=playlist_id, playlist_name=playlist['name'])
                     for item in _studio_items(current))
    from music_studio import service
    base = _audio_base(request)
    store = service.store()
    for item in items:
        parts = str(item.get('id') or '').split(':')
        if len(parts) == 3 and not _take_exists(store, parts[1], parts[2]):
            # Deleted in Music Studio: say so, instead of a link that fails in the browser.
            item['missing'] = True
        elif not str((item.get('playback') or {}).get('url') or '').startswith('/api/media/'):
            # Added before songs were handed over as files: REVamp cannot play the old link.
            item['relink'] = True
        playback = item.get('playback') or {}
        url = str(playback.get('url') or '')
        marker = '/api/projects/'
        if marker in url:
            # Re-sign on the way out, so a take added before signing existed plays again too.
            route = url[url.index(marker):].split('?', 1)[0]
            match = media_sign.AUDIO_PATH.match(route)
            playback['url'] = (media_sign.audio_url(base, service.store().root, *match.groups())
                               if match else base + route)
    return {'success': True, 'items': items}



def _track_id(path: str) -> str:
    """REVamp's own track identity: sha1 of the absolute path, first 16 hex characters."""
    return hashlib.sha1(os.path.abspath(path).encode('utf-8')).hexdigest()[:16]


def _ensure_library_folder(folder: str) -> bool:
    """REVamp can only play what it has indexed, so the folder it writes into must be one
    of its library folders."""
    payload = _call('GET', '/api/library/folders')
    rows = payload.get('folders') if isinstance(payload, dict) else payload
    rows = [row for row in (rows or []) if isinstance(row, dict) and row.get('path')]
    # normcase: on Windows c:\users\me\music and C:\Users\Me\Music are one folder (a no-op elsewhere).
    paths = [os.path.normcase(os.path.normpath(str(row['path']))) for row in rows]
    wanted = os.path.normpath(folder)
    wanted_key = os.path.normcase(wanted)
    # A folder inside a library folder is already covered by that folder's scan; adding it
    # again would nest two roots and confuse the library.
    for path in paths:
        if wanted_key == path or wanted_key.startswith(path.rstrip(os.sep) + os.sep):
            return False
    cleaned = [{'path': row['path'], 'kind': row.get('kind') or 'music'} for row in rows]
    cleaned.append({'path': wanted, 'kind': 'music'})
    _call('PUT', '/api/library/folders', {'folders': cleaned})
    return True


def _studio_item(project_id, take_id, written):
    url = '/api/media/' + _track_id(written['path'])
    return {'id': f'studio:{project_id}:{take_id}', 'source': 'studio', 'kind': 'music',
            'title': written.get('title') or Path(written['path']).stem, 'artist': 'Hermes Music Studio',
            'playback': {'backend': 'webamp', 'url': url}, 'capabilities': ['seek']}


def _index_exports(folder, files):
    if not _ensure_library_folder(folder):
        _call('POST', '/api/library/rescan', {})
    deadline = time.monotonic() + INDEX_WAIT
    for track_id in dict.fromkeys(_track_id(row['path']) for row in files):
        while time.monotonic() < deadline:
            try:
                _call('GET', '/api/sources/local/item/' + track_id)
                break
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        else:
            return True
    return False


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
    match = _existing_playlist(name)
    if match:
        return match
    base = service.default_data_dir(service.PLUGIN_ID)
    folder = (body.folder or st.read(base).get('export_dir') or '').strip()
    files = []
    items = []
    for row in body.takes:
        project_id = str(row.get('project_id') or '')
        take_id = str(row.get('take_id') or '')
        if not project_id or not take_id:
            raise HTTPException(400, 'each take needs project_id and take_id')
        try:
            written = service.export_take(project_id, take_id, folder=folder or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except (FileNotFoundError, KeyError):
            raise HTTPException(400, 'a song in this list was deleted in Music Studio; remove it and try again') from None
        files.append(written)
        items.append(_studio_item(project_id, take_id, written))
    destination = files[0]['folder']
    indexing = _index_exports(destination, files)
    result = _create_or_select(name, items)
    return {'success': True, **result, 'files': [row['path'] for row in files],
            'folder': destination, 'indexing': indexing}


def forget_deleted(project_id, take_id=None):
    """Take a deleted song (or one deleted take) out of every REVamp playlist it was added to.

    A take handed to REVamp stays there after the song is deleted in Music Studio, and its link then
    points at nothing: REVamp and Play here fail with a browser error. Best effort: REVamp may be
    missing or closed, and the delete itself has already succeeded.
    """
    if not project_id or _client_path() is None:
        return 0
    prefix = 'studio:' + str(project_id) + ':'
    wanted = (lambda item_id: item_id == prefix + str(take_id)) if take_id else (
        lambda item_id: item_id.startswith(prefix))
    removed = 0
    try:
        listing = _call('GET', '/api/playlists')
        rows = listing.get('playlists') if isinstance(listing, dict) else listing
        for row in rows or []:
            playlist_id = str(row.get('id') or '') if isinstance(row, dict) else ''
            if not PLAYLIST_ID.fullmatch(playlist_id):
                continue
            path = '/api/playlists/' + urllib.parse.quote(playlist_id, safe='')
            current = _call('GET', path)
            items = current.get('items') if isinstance(current, dict) else None
            if not isinstance(items, list):
                continue
            kept = [item for item in items
                    if not (isinstance(item, dict) and wanted(str(item.get('id') or '')))]
            if len(kept) != len(items):
                _call('PUT', path, {'items': kept})
                removed += len(items) - len(kept)
    except Exception:  # noqa: BLE001 - REVamp closed or refusing: the song is still deleted
        return removed
    return removed


def _take_exists(store, project_id, take_id):
    try:
        project = store.get(project_id)
    except Exception:  # noqa: BLE001 - a deleted song reads as missing, whatever the store raises
        return False
    return any(isinstance(t, dict) and t.get('take_id') == take_id for t in project.get('takes') or [])


@router.post('/studio/revamp/remove')
def remove_from_playlist(body: RemoveStudioTake):
    """Take one item back out of the studio's REVamp playlist."""
    playlist_id = body.playlist_id if body.playlist_id is not None else str((studio_playlist() or {}).get('id') or '')
    if not PLAYLIST_ID.fullmatch(playlist_id):
        raise HTTPException(400, 'REVamp returned an invalid playlist')
    path = '/api/playlists/' + urllib.parse.quote(playlist_id, safe='')
    current = _call('GET', path)
    # A reply without an items list must not be read as an empty playlist: saving that would wipe it.
    if not isinstance(current, dict) or not isinstance(current.get('items'), list):
        raise HTTPException(502, 'REVamp returned an invalid playlist')
    items = [row for row in current['items']
             if not (isinstance(row, dict) and row.get('id') == body.item_id)]
    result = _call('PUT', path, {'items': items})
    return {'success': True, 'playlist': result, 'items': _studio_items(result)}
