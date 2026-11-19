"""Radio mode: a station that keeps writing songs into a REVamp playlist.

The loop is a daemon thread in the plugin process, never the page. Drafting goes through Hermes'
own model facade (`ctx.llm`), which exists only here — a standalone tab has no host model to
borrow — and a page that is closed or reloaded must not stop the station.

One song is in flight at a time. The render queue's own ``GPU busy`` refusal is the throttle, so
nothing is ever pre-queued: the loop waits for the take it queued to finish before it drafts the
next song, and the playlist grows one finished file at a time while it plays.

State lives in ``<data dir>/radio.json`` so a Hermes restart can pick the station back up, and a
``.radio.lock`` file held for the thread's lifetime makes a second Start a refusal rather than a
second loop. The person's Start is the approval for every song the station writes; each project is
still approved through ``Store.approve`` before it is queued, and carries ``approved_by='radio'``
so it is honest about where the approval came from.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

from fastapi import HTTPException

from . import activity, llm_bridge, service
from .jobs import Jobs
from .portalock import lock_ex, unlock
from .store import FIELDS, read_text_with_retry, replace_with_retry

RADIO_FILE = 'radio.json'
LOCK_NAME = '.radio.lock'
ARTIST = 'Hermes Music Studio'
DEFAULT_PLAYLIST = 'Music Studio Radio'
PLAYLIST_NAME_MAX = 80
THEME_MAX = 300

MIN_SONGS = 1
MAX_SONGS = 50
FAILURE_LIMIT = 3
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024

# Kept as module names so tests can shorten them without waiting out a real render.
POLL_SECONDS = 2.0
BUSY_SECONDS = 5.0

# One station thread and one lock handle per process. The lock is the cross-process guard; the
# module lock keeps the routes and the thread from interleaving a read-modify-write of radio.json.
_FILE_LOCK = threading.RLock()
_THREAD = None
_LOCK_HANDLE = None
# Start, Resume and the restart pickup are one at a time: their checks and the thread they start
# must not interleave with a second click or a second request.
_START_LOCK = threading.Lock()


def _base():
    """The plugin's data directory — the store root, where the library and radio.json live."""
    return Path(service.store().root)


def state_path():
    return _base() / RADIO_FILE


def read():
    """The station record, or None when no station has ever run here.

    Read with the store's retry: on Windows a reader that arrives while ``_write`` swaps the file in
    gets a sharing violation, and reading that as "no station" made a running station stop.
    """
    try:
        data = json.loads(read_text_with_retry(state_path()))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


_STATE_DEPTH = threading.local()


class _state_lock:
    """Serialise read-modify-write of radio.json across threads AND Hermes processes.

    ``_FILE_LOCK`` covers this process; a second Hermes process (a Pause from another profile's
    window) is covered by an exclusive lock on ``.radio-state.lock``. Re-entrant within a thread.
    """

    def __enter__(self):
        _FILE_LOCK.acquire()
        depth = getattr(_STATE_DEPTH, 'n', 0)
        _STATE_DEPTH.n = depth + 1
        self.handle = None
        if depth == 0:
            try:
                path = _base() / '.radio-state.lock'
                path.parent.mkdir(parents=True, exist_ok=True)
                self.handle = path.open('a')
                lock_ex(self.handle)
            except OSError:
                if self.handle is not None:
                    self.handle.close()
                self.handle = None
        return self

    def __exit__(self, *exc):
        try:
            if self.handle is not None:
                try:
                    unlock(self.handle)
                finally:
                    self.handle.close()
        finally:
            _STATE_DEPTH.n -= 1
            _FILE_LOCK.release()
        return False


def _write(state):
    """Persist atomically: a poll must never read a half-written station."""
    state['updated_at'] = time.time()
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2), encoding='utf-8')
    replace_with_retry(temp, path)
    return state


def _change(**fields):
    with _state_lock():
        state = read() or {}
        state.update(fields)
        _write(state)
        return state


def _sentence(exc):
    """One plain line for the status area: what broke, in the engine's own words where it has them."""
    text = str(exc).strip()
    return text or f'{type(exc).__name__} while writing the station.'


# -- the single-flight lock -------------------------------------------------

def _take_lock():
    """Hold ``.radio.lock`` for the station's lifetime, or return None when it is taken.

    Already held in this process means a station owns it: None, never a second handout, or two
    overlapping Starts would both be told the lock is theirs.
    """
    global _LOCK_HANDLE
    if _LOCK_HANDLE is not None:
        return None
    path = _base() / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a')
    try:
        lock_ex(handle, blocking=False)
    except BlockingIOError:
        handle.close()
        return None
    except BaseException:
        handle.close()
        raise
    _LOCK_HANDLE = handle
    return handle


def _release_lock():
    global _LOCK_HANDLE
    handle, _LOCK_HANDLE = _LOCK_HANDLE, None
    if handle is not None:
        try:
            unlock(handle)
        finally:
            handle.close()


def _thread_alive():
    return _THREAD is not None and _THREAD.is_alive()


def _start_thread(station_id):
    """Run the station in the plugin process, so closing the page does not stop it."""
    global _THREAD
    thread = threading.Thread(target=_loop, args=(station_id,),
                              name='music-studio-radio', daemon=True)
    _THREAD = thread
    thread.start()
    return thread


# -- the station itself -----------------------------------------------------

def _export_folder(base, playlist_name):
    """One folder per station, inside the person's export folder when they have set one."""
    from . import settings
    from .export import default_folder, safe_name

    configured = str((settings.read(base) or {}).get('export_dir') or '').strip()
    root = Path(configured).expanduser() if configured else default_folder()
    return root / safe_name('Radio - ' + playlist_name, fallback='Radio')


def _ensure_playlist(revamp, name):
    """Reuse a playlist with exactly this name, or make an empty one.

    ``playlist_from_takes`` replaces a playlist wholesale by name; a station only ever appends,
    because the person may be listening to that list while it grows.
    """
    listing = revamp._call('GET', '/api/playlists')
    rows = listing.get('playlists') if isinstance(listing, dict) else listing
    for row in rows or []:
        if isinstance(row, dict) and str(row.get('name')) == name:
            return row
    return revamp._call('POST', '/api/playlists', {'name': name, 'items': []})


def _engine_problem(engine_id):
    """The sentence to show when this engine cannot render now, or '' when it can."""
    from .engines.registry import resolve_id

    try:
        registry = service.registry()
        engine = registry.get(resolve_id(engine_id) or engine_id)
    except (KeyError, ValueError):
        return f'That music engine is not set up here: {engine_id}.'
    status = registry.health(engine, fresh=True)
    if not status.available:
        return f'{engine.label} cannot render right now: {status.detail or status.state}'
    if not bool(getattr(engine, 'supports_generate', True)):
        return f'{engine.label} cannot render songs.'
    return ''


def _count(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _start(body=None):
    """Begin a station: one theme, one playlist, one song at a time.

    Starting is the person's approval for every song the station writes, so the page says so
    next to the button. Everything that would only fail later — no model, no REVamp, no engine,
    a theme or count out of bounds — is refused here with one plain sentence.
    """
    body = body or {}
    theme = str(body.get('theme') or '').strip()
    if not theme:
        raise ValueError('Give the station a theme first — a few words about the sound.')
    if len(theme) > THEME_MAX:
        raise ValueError(f'Keep the theme under {THEME_MAX} characters.')
    count = _count(body.get('target_count', body.get('count')), 10)
    if not MIN_SONGS <= count <= MAX_SONGS:
        raise ValueError(f'Choose between {MIN_SONGS} and {MAX_SONGS} songs.')
    engine_id = str(body.get('engine_id') or '').strip()
    if not engine_id:
        raise ValueError('Choose a music engine first.')
    playlist_name = (str(body.get('playlist_name') or '').strip()[:PLAYLIST_NAME_MAX]
                     or DEFAULT_PLAYLIST)
    if not llm_bridge.available():
        raise ValueError(llm_bridge.MISSING)

    problem = _engine_problem(engine_id)
    if problem:
        raise ValueError(problem)

    from standalone import revamp

    if not revamp.plugin_installed() or revamp._client_path() is None:
        raise ValueError('REVamp is not installed. Radio mode needs it to play what it writes.')
    try:
        revamp._call('GET', '/api/state')
    except HTTPException:
        raise ValueError('REVamp is not answering. Open it in Hermes, then start the station.') from None

    if _thread_alive():
        raise ValueError('Radio is already running. Pause or stop it, then start again.')
    if _take_lock() is None:
        raise ValueError('Radio is already running in another Hermes process. '
                         'Pause or stop it there first.')

    try:
        base = _base()
        folder = _export_folder(base, playlist_name)
        folder.mkdir(parents=True, exist_ok=True)
        # Once, at start: a folder the player cannot see would mean songs nobody can hear.
        revamp._ensure_library_folder(str(folder))
        playlist = _ensure_playlist(revamp, playlist_name)
        if not str(playlist.get('id') or ''):
            raise ValueError('REVamp did not return a playlist; nothing was started.')
        # Make the new folder's files visible before the first song asks for one of them.
        revamp._call('POST', '/api/library/rescan', {})
    except HTTPException:
        _release_lock()
        raise ValueError('REVamp is not answering; nothing was started.') from None
    except OSError as exc:
        _release_lock()
        raise ValueError(f'The station folder could not be made: {exc}') from None
    except BaseException:
        _release_lock()
        raise

    now = time.time()
    state = {
        'station_id': str(uuid.uuid4()),
        'state': 'running',
        'theme': theme,
        'instrumental': bool(body.get('instrumental')),
        'engine_id': engine_id,
        'target_count': count,
        'done': 0,
        'playlist_name': playlist_name,
        'playlist_id': str(playlist.get('id') or ''),
        'folder': str(folder),
        'songs': [],
        'last_error': None,
        'consecutive_failures': 0,
        # The song in flight, so Stop can cancel its queued take and the status line can name it.
        'current_project_id': None,
        'current_take_id': None,
        'current_title': None,
        'started_at': now,
        'updated_at': now,
    }
    with _state_lock():
        _write(state)
    activity.record(base, 'radio started', theme=theme, songs=count, playlist=playlist_name)
    _start_thread(state['station_id'])
    return snapshot()


def pause(body=None):
    """Stop after the song in flight; the thread notices between songs and lets go of the lock."""
    state = read() or {}
    if state.get('state') != 'running':
        raise ValueError('The station is not running.')
    reason = str((body or {}).get('reason') or '').strip() or 'Paused by you.'
    _change(state='paused', last_error=reason)
    activity.record(_base(), 'radio paused', reason=reason)
    return snapshot()


def stop(body=None):
    """Stop after the song in flight and cancel anything queued for it."""
    state = read() or {}
    if state.get('state') not in ('running', 'paused'):
        raise ValueError('The station is not running.')
    _change(state='stopped', last_error=None)
    project_id = state.get('current_project_id')
    if project_id:
        try:
            Jobs(service.store()).cancel_queued(project_id)
        except (OSError, ValueError, KeyError):
            # The take is already gone, or the project was deleted: nothing left to cancel.
            pass
    activity.record(_base(), 'radio stopped')
    return snapshot()


def _resume(body=None):
    """Continue from where the station stopped, retrying any append that did not land."""
    state = read() or {}
    if not state:
        raise ValueError('No station has run here yet.')
    if state.get('state') not in ('paused', 'stopped'):
        raise ValueError('The station is not paused.')
    pending = _pending_append(state)
    if int(state.get('done') or 0) >= int(state.get('target_count') or 0) and pending is None:
        raise ValueError('The station has already written every song it was asked for.')
    if not llm_bridge.available():
        raise ValueError(llm_bridge.MISSING)

    from standalone import revamp

    if not revamp.plugin_installed() or revamp._client_path() is None:
        raise ValueError('REVamp is not installed. Radio mode needs it to play what it writes.')

    if _thread_alive():
        raise ValueError('The station is still finishing a song. Try again in a moment.')
    if _take_lock() is None:
        raise ValueError('The station is still finishing a song. Try again in a moment.')
    _change(state='running', last_error=None)
    activity.record(_base(), 'radio started', resumed=True,
                    playlist=state.get('playlist_name'))
    _start_thread(state['station_id'])
    return snapshot()


def _resume_on_load():
    """Pick a running station back up after Hermes restarted.

    Called from ``register()``. The lock decides: if another process already holds it, this one
    stays out rather than running a second loop over the same playlist.
    """
    state = read() or {}
    if state.get('state') != 'running' or not state.get('station_id'):
        return False
    if _thread_alive() or not llm_bridge.available():
        return False
    if _take_lock() is None:
        return False
    try:
        _start_thread(state['station_id'])
    except BaseException:
        _release_lock()
        raise
    return True


def _message(state):
    """The one line the page shows under the buttons."""
    name = state.get('state')
    done = _count(state.get('done'), 0)
    target = _count(state.get('target_count'), 0)
    title = str(state.get('current_title') or '')
    if name == 'running':
        if state.get('current_take_id') and title:
            return f'Song {min(done + 1, target or done + 1)} of {target}: {title}, rendering…'
        return f'Writing song {min(done + 1, target or done + 1)} of {target}…'
    if name == 'paused':
        return 'Paused: ' + str(state.get('last_error') or 'paused by you.')
    if name == 'finished':
        return f'Finished: {done} of {target} songs are in “{state.get("playlist_name")}”.'
    if name == 'stopped':
        return f'Stopped after {done} of {target} songs.'
    return 'No station has run yet.'


def snapshot():
    """Everything the page needs: the station record, a sentence, and whether the loop is alive."""
    state = read()
    if state is None:
        return {'success': True,
                'station': {'state': 'stopped', 'songs': [], 'done': 0, 'target_count': 0,
                            'playlist_name': '', 'last_error': None},
                'songs': [], 'message': 'No station has run yet.', 'running': False}
    return {'success': True, 'station': state, 'songs': list(state.get('songs') or []),
            'message': _message(state), 'running': _thread_alive()}


# -- the loop ---------------------------------------------------------------

def _running(station_id):
    state = read()
    if state is None:
        # The record could not be read this instant (a Windows swap in flight): keep going and look
        # again, rather than stopping a station whose file says it is running.
        time.sleep(POLL_SECONDS)
        state = read()
        if state is None:
            return True
    return state.get('station_id') == station_id and state.get('state') == 'running'


def _pending_append(state):
    """A song that was exported but never reached the playlist — finish that before a new draft."""
    for song in state.get('songs') or []:
        if isinstance(song, dict) and not song.get('appended'):
            return song
    return None


def _low_disk(state):
    """True when either the library or the station folder has under 2 GB free."""
    free = []
    for path in (_base(), Path(state.get('folder') or '')):
        try:
            free.append(shutil.disk_usage(str(path)).free)
        except OSError:
            continue
    return bool(free) and min(free) < MIN_FREE_BYTES


def _loop(station_id):
    try:
        while _running(station_id):
            if not _tick(station_id):
                return
    except Exception as exc:  # noqa: BLE001 - an unexpected fault pauses; it never leaves a dead run
        try:
            _pause(_sentence(exc))
        except Exception:  # noqa: BLE001 - a station record that cannot be written stops here
            pass
    finally:
        _release_lock()


def _tick(station_id):
    """One step of the station: finish a pending append, close it out, or write another song."""
    state = read()
    if state is None:
        time.sleep(POLL_SECONDS)   # unreadable this instant; never read as "0 of 0 done"
        return True
    if not _revamp_present():
        _pause('REVamp is not answering; resume when it is running.')
        return False
    pending = _pending_append(state)
    if pending is not None:
        return _append_pending(state, pending)
    if state.get('current_take_id') and state.get('current_project_id'):
        # A take was queued before Hermes restarted: finish that song instead of writing another.
        return _finish_song(state, state['current_project_id'], state['current_take_id'],
                            state.get('current_title'))
    if _count(state.get('done'), 0) >= _count(state.get('target_count'), 0):
        _close('finished')
        return False
    return _one_song(state)


def _revamp_present():
    """True while REVamp is installed AND answering, so no song is written into a dead player."""
    from standalone import revamp

    if not revamp.plugin_installed() or revamp._client_path() is None:
        return False
    try:
        revamp._call('GET', '/api/state')
    except HTTPException:
        return False
    return True


def _one_song(state):
    """Draft, render, export and append one song. False means the loop must stop."""
    station_id = state.get('station_id')
    if not _running(station_id):
        return False
    if _low_disk(state):
        _pause('Not enough free disk space to keep writing songs (under 2 GB left).')
        return False

    number = _count(state.get('done'), 0) + 1
    try:
        draft = _draft_song(state, number)
        project = _open_project(state, draft)
    except llm_bridge.LlmUnavailable:
        _pause(llm_bridge.MISSING)
        return False
    except llm_bridge.LlmTrustError as exc:
        _pause(str(exc))
        return False
    except Exception as exc:  # noqa: BLE001 - a draft that fails is a strike, not a crash
        return _counted_failure(_sentence(exc))

    result = _render(project['project_id'], state.get('engine_id'), station_id,
                     project.get('title'))
    return _after_render(state, project['project_id'], project.get('title'), result)


def _finish_song(state, project_id, take_id, title):
    """Wait for a take that is already queued, then export and append it like any other song."""
    return _after_render(state, project_id, title, _wait_for_take(project_id, take_id))


def _after_render(state, project_id, title, result):
    station_id = state.get('station_id')
    project = {'project_id': project_id, 'title': title}
    if result['state'] == 'succeeded':
        pass
    elif result['state'] == 'stopped':
        # Pause or stop landed before anything was queued.
        return False
    elif result['state'] == 'cancelled':
        # Stop cancelled the queued take: the person asked for that, so it is no strike. A resumed
        # station clears it and writes the next song.
        if not _running(station_id):
            return False
        _change(current_project_id=None, current_take_id=None, current_title=None)
        return True
    else:
        return _counted_failure(result['error'] or 'The render did not finish.')

    try:
        written = service.export_take(project['project_id'], result['take_id'],
                                      folder=state.get('folder') or None,
                                      title=project.get('title'))
    except Exception as exc:  # noqa: BLE001 - a finished song that cannot be copied is a strike
        return _counted_failure(f'The song finished but could not be copied to the playlist '
                                f'folder: {_sentence(exc)}')

    song = {'project_id': project['project_id'], 'take_id': result['take_id'],
            'title': written.get('title') or project.get('title') or 'Radio song',
            'exported_path': written['path'],
            'track_id': _track_id(written['path']),
            'appended': False}
    with _state_lock():
        fresh = read() or {}
        fresh.setdefault('songs', []).append(song)
        # Persisted before the append: a REVamp that stops answering resumes from this file and
        # puts this exact file in the playlist instead of exporting the song a second time.
        _write(fresh)
    return _append_pending(fresh, song)


def _draft_song(state, number):
    """One song draft, from the station's theme and everything already written on it."""
    from standalone.ideas import build_request, parse_draft

    target = _count(state.get('target_count'), 0)
    prompt = (f'Write song {number} of {target} for a radio station.\n'
              f'Theme: {state.get("theme")}\n')
    history = _history(state)
    if history:
        prompt += ('Songs already on this station — write a different subject, hook and tempo '
                   'within this theme:\n' + '\n'.join(f'- {row}' for row in history) + '\n')
    if state.get('instrumental'):
        prompt += 'This station is instrumental: no lyrics.\n'
    else:
        prompt += 'Include lyrics with [verse] and [chorus] markers.\n'
    prompt += ('Return one JSON object with title, caption, lyrics, bpm, keyscale and '
               'duration_seconds.')
    request = build_request(None, None, [{'role': 'user', 'content': prompt}], 'draft', '', 50)
    content = llm_bridge.complete(request['messages'], provider=request['provider'],
                                  model=request['model'], max_tokens=request['max_tokens'],
                                  temperature=request['temperature'])
    return parse_draft(content)


def _history(state, limit=12):
    """Titles (and the captions behind them) already on the station, newest last."""
    store = service.store()
    rows = []
    for song in (state.get('songs') or [])[-limit:]:
        title = str(song.get('title') or '')
        caption = ''
        try:
            caption = str(store.get(song.get('project_id')).get('caption') or '')
        except (OSError, ValueError, KeyError):
            pass
        rows.append(f'{title}: {caption[:120]}' if caption else title)
    return [row for row in rows if row]


def _open_project(state, draft):
    """Create the project and approve it — the station's Start is the approval that matters."""
    store = service.store()
    fields = {key: value for key, value in draft.items() if key in FIELDS}
    fields['engine'] = state.get('engine_id')
    fields['instrumental'] = bool(state.get('instrumental'))
    fields['approved_by'] = 'radio'
    project = store.create(**fields)
    project = store.approve(project['project_id'], base_updated_at=project['updated_at'])
    _change(current_project_id=project['project_id'], current_title=project.get('title'))
    return project


def _render(project_id, engine_id, station_id, title):
    """Queue one take and wait for it to finish. One take in flight, never a pre-queued batch."""
    while True:
        if not _running(station_id):
            return {'take_id': None, 'state': 'stopped', 'error': None}
        try:
            take = service.generate(project_id, engine_id=engine_id)['take']
            break
        except ValueError as exc:
            if 'GPU busy' not in str(exc):
                return {'take_id': None, 'state': 'failed', 'error': _sentence(exc)}
            # Another render owns the GPU. Wait for it rather than burning a strike on it, and let a
            # render whose worker died release the GPU: with no page open nothing else checks.
            _reconcile()
            time.sleep(BUSY_SECONDS)
        except Exception as exc:  # noqa: BLE001 - engine trouble is a strike, not a crash
            return {'take_id': None, 'state': 'failed', 'error': _sentence(exc)}

    take_id = take['take_id']
    _change(current_take_id=take_id, current_title=title)
    return _wait_for_take(project_id, take_id)


def _reconcile():
    """Fail takes whose worker is gone; live workers are never touched. Best effort."""
    try:
        Jobs(service.store()).reconcile_dead_workers()
    except Exception:  # noqa: BLE001 - a failed check is retried on the next poll
        pass


def _wait_for_take(project_id, take_id):
    while True:
        # With no page open nothing else polls status, so a worker that died would leave this take
        # "running" for ever. The same check the status poll runs.
        _reconcile()
        row = _take(project_id, take_id)
        if not row:
            return {'take_id': take_id, 'state': 'failed',
                    'error': 'The take disappeared from the library.'}
        name = row.get('state')
        if name in ('succeeded', 'failed', 'cancelled'):
            return {'take_id': take_id, 'state': name, 'error': row.get('error')}
        time.sleep(POLL_SECONDS)


def _take(project_id, take_id):
    try:
        project = service.store().get(project_id)
    except (OSError, ValueError, KeyError):
        return {}
    return next((row for row in project.get('takes') or []
                 if row.get('take_id') == take_id), {})


def _track_id(path):
    from standalone.revamp import _track_id as revamp_track_id
    return revamp_track_id(path)


def _append_pending(state, song):
    """Put an exported song into the playlist if it is not there, then count it. False = paused."""
    if not _add_to_playlist(state, song):
        _pause('REVamp is not answering; resume when it is running.')
        return False
    _mark_appended(song.get('take_id'))
    activity.record(_base(), 'radio song added', song=song.get('title'),
                    playlist=state.get('playlist_name'))
    return True


def _add_to_playlist(state, song):
    """Append one track to the station's playlist, leaving every existing item where it is."""
    from standalone import revamp

    playlist_id = str(state.get('playlist_id') or '')
    tid = str(song.get('track_id') or '')
    if not playlist_id or not tid:
        return False
    path = '/api/playlists/' + urllib.parse.quote(playlist_id, safe='')
    try:
        # The file is new to the library, so ask for a scan before pointing a playlist at it.
        revamp._call('POST', '/api/library/rescan', {})
        current = revamp._call('GET', path)
        if not isinstance(current, dict) or not isinstance(current.get('items'), list):
            return False
        if not _already_listed(current['items'], tid):
            item = {'kind': 'track', 'track_id': tid, 'url': '/api/media/' + tid,
                    'title': song.get('title') or 'Radio song', 'artist': ARTIST,
                    'playback': {'backend': 'webamp', 'url': '/api/media/' + tid}}
            revamp._call('PUT', path, {'items': [*current['items'], item]})
    except HTTPException:
        return False
    return True


def _already_listed(items, track_id):
    return any(isinstance(row, dict) and row.get('track_id') == track_id for row in items)


def _mark_appended(take_id):
    """Mark the song in the playlist, count it, and clear the song in flight."""
    with _state_lock():
        state = read() or {}
        for song in state.get('songs') or []:
            if song.get('take_id') == take_id:
                song['appended'] = True
        # Counted from the record rather than incremented, so a retried append cannot count twice.
        state['done'] = sum(1 for song in state.get('songs') or [] if song.get('appended'))
        state['consecutive_failures'] = 0
        state['last_error'] = None
        state['current_project_id'] = None
        state['current_take_id'] = None
        state['current_title'] = None
        _write(state)
        return state


def _counted_failure(sentence):
    """One strike toward the three-strike pause. True means the loop may try again."""
    with _state_lock():
        state = read() or {}
        count = _count(state.get('consecutive_failures'), 0) + 1
        state['consecutive_failures'] = count
        state['last_error'] = sentence
        state['current_project_id'] = None
        state['current_take_id'] = None
        state['current_title'] = None
        if count >= FAILURE_LIMIT:
            state['state'] = 'paused'
        _write(state)
    if count >= FAILURE_LIMIT:
        activity.record(_base(), 'radio paused', reason=sentence)
        return False
    time.sleep(BUSY_SECONDS)
    return True


def _pause(sentence):
    with _state_lock():
        state = read() or {}
        state['state'] = 'paused'
        state['last_error'] = sentence
        _write(state)
    activity.record(_base(), 'radio paused', reason=sentence)


def _close(name):
    with _state_lock():
        state = read() or {}
        state['state'] = name
        state['last_error'] = None
        state['current_project_id'] = None
        state['current_take_id'] = None
        state['current_title'] = None
        _write(state)
    if name == 'finished':
        activity.record(_base(), 'radio finished', songs=state.get('done'))


def _one_at_a_time(action, *args):
    if not _START_LOCK.acquire(blocking=False):
        raise ValueError('The station is already starting. Try again in a moment.')
    try:
        return action(*args)
    finally:
        _START_LOCK.release()


def start(body=None):
    """Begin a station (see ``_start``), one Start at a time."""
    return _one_at_a_time(_start, body)


def resume(body=None):
    """Continue a paused or stopped station (see ``_resume``), one at a time."""
    return _one_at_a_time(_resume, body)


def resume_on_load():
    """Pick a running station back up after a restart (see ``_resume_on_load``)."""
    if not _START_LOCK.acquire(blocking=False):
        return False
    try:
        return _resume_on_load()
    finally:
        _START_LOCK.release()
