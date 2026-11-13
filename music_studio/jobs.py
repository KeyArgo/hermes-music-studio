"""Persistence-only job coordinator; no inference or background worker."""
import copy
import io
import json
import logging
import os
import random
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from .store import (EXTEND_MAX_OVERLAP_SECONDS, EXTEND_MAX_SECONDS,
                    EXTEND_MIN_SECONDS, EXTEND_SOURCE_MIN_SECONDS, FIELDS,
                    REPAINT_MAX_SECONDS, REPAINT_MIN_SECONDS, TOTAL_MAX_SECONDS,
                    fsync_dir, replace_with_retry, valid_id, validate_ready)
from .safeopen import open_nofollow

# A take whose worker pid has not been recorded yet (spawn in flight) is only
# considered abandoned after this long, so a status poll cannot kill a job that
# is still being launched.
SPAWN_GRACE_SECONDS = 10.0


def _pinned_target(snapshot):
    """The ComfyUI endpoint a parent take was pinned to, if any."""
    if not isinstance(snapshot, dict) or not snapshot.get('comfy_target_url'):
        return None
    return {'id': snapshot.get('comfy_target_id'),
            'url': snapshot['comfy_target_url'],
            'name': snapshot.get('comfy_target_name')}


def _stat_start_time(pid):
    """Kernel process start time (clock ticks since boot) or None."""
    try:
        with open(f'/proc/{pid}/stat', 'rb') as fh:
            raw = fh.read()
        # The comm field may contain spaces/parens; everything after the last ')' is fixed.
        return int(raw.rsplit(b')', 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def _stat_state(pid):
    """Single-letter process state ('R', 'S', 'Z', ...) or None when gone or unknown.

    Linux reads /proc. macOS and the BSDs have no /proc, so ``ps`` answers there; without it a
    zombie worker read as alive and kept the profile "GPU busy" until a manual recover. Windows
    has no zombies (pid_exists already reports an exited process as gone), so it answers None.
    """
    try:
        with open(f'/proc/{pid}/stat', 'rb') as fh:
            raw = fh.read()
        return raw.rsplit(b')', 1)[1].split()[0]
    except FileNotFoundError:
        # Linux always has /proc, so a missing stat file means the process is gone.
        if os.name == 'nt' or sys.platform.startswith('linux'):
            return None
    except (OSError, IndexError, ValueError):
        return None
    try:
        out = subprocess.run(['ps', '-o', 'stat=', '-p', str(int(pid))], capture_output=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out[:1] or None


def process_alive(pid, start_time=None):
    """True when pid exists, is not a zombie, and (if given) has the recorded start time.

    The start-time comparison rejects pid reuse, so a recycled pid never keeps a
    dead take alive. A zombie is reported dead: it has already exited, so it
    owns no GPU lease and no render; treating it as alive would leave the
    profile "GPU busy" until a manual /recover (the spawner may never reap it).
    """
    if type(pid) is not int or pid <= 0:
        return False
    from .procs import pid_exists
    if not pid_exists(pid):
        return False
    state = _stat_state(pid)
    if state is not None and state in (b'Z', b'X', b'x'):
        return False
    if start_time is not None:
        actual = _stat_start_time(pid)
        if actual is not None and actual != start_time:
            return False
    return True


def terminate_render(pid, start_time=None):
    """Kill one recorded ACE render and everything it spawned.

    ACE is started in its own session (so a timeout can kill only the render),
    which means killing the studio worker would otherwise leave it running next
    to the next render. The render pid is recorded on the take, and this is the
    reaper used when the worker itself died without cleanup. The start-time
    check makes pid reuse safe: a recycled pid is never signalled.
    """
    if type(pid) is not int or pid <= 0:
        return False
    if start_time is not None:
        actual = _stat_start_time(pid)
        if actual is not None and actual != start_time:
            return False
    if not process_alive(pid, start_time):
        return False
    try:
        from .procs import kill_tree
        return kill_tree(pid)
    except OSError:
        return False


class Jobs:
    def __init__(self, store):
        self.store = store

    def _assert_gpu_free(self):
        if any(t['state'] in ('queued', 'running') for x in self.store.all() for t in x['takes']):
            raise ValueError('GPU busy')

    def _new_take(self, project, *, seed, requested_seed, seed_locked,
                  batch_id=None, queue_position=None, kind='generate', parent=None,
                  extra=None, duration_override=None, engine_id=None, target=None):
        # Sections are nested mutable state: a take's snapshot must never share
        # them with the live draft (or with another take).
        snapshot = copy.deepcopy({k: project.get(k, v) for k, v in FIELDS.items()})
        if duration_override is not None:
            snapshot['duration_seconds'] = duration_override
        if target is not None:
            # Pin the exact endpoint this take was preflighted against. A
            # worker must render the snapshot URL, not a target that has since
            # been renamed, retargeted or deleted.
            snapshot['comfy_target_id'] = target['id']
            snapshot['comfy_target_url'] = target['url']
            snapshot['comfy_target_name'] = target['name']
        take = {'take_id': str(uuid.uuid4()), 'state': 'queued', 'seed': seed,
                'requested_seed': requested_seed, 'seed_locked': seed_locked,
                # The engine is pinned when the take is queued, from the
                # approved draft (or the user's choice). A worker may not swap
                # it: an explicit engine that is unavailable fails the take
                # instead of quietly rendering on another one.
                'engine_id': engine_id,
                'batch_id': batch_id, 'queue_position': queue_position,
                'kind': kind, 'parent_take_id': None, 'lineage': [],
                'extend_seconds': None, 'range_start_s': None, 'range_end_s': None,
                'splice_start': None, 'overlap_seconds': None,
                'source_duration': None, 'planned_duration': None,
                'audio_file': None, 'error': None,
                # Engine provenance (engine_id, licence notice, fell_back) is
                # recorded when the take succeeds.
                'metadata': None,
                'pid': None, 'pid_start': None, 'ace_pid': None, 'ace_pid_start': None,
                'created_at': time.time(),
                'started_at': None, 'finished_at': None, 'progress': None,
                'snapshot': snapshot}
        if parent is not None:
            if target is None:
                # A child queued without a pinned target must not inherit the
                # draft's target id: the draft may name a live default endpoint
                # that this take was never preflighted against.
                snapshot['comfy_target_id'] = None
            take['parent_take_id'] = parent['take_id']
            take['lineage'] = list(parent.get('lineage') or []) + [parent['take_id']]
        if extra:
            take.update(extra)
        return take

    def enqueue(self, project_id, seed, lock_seed=False, engine_id=None, target=None):
        """Queue one take.

        ``seed_locked`` records whether the caller pinned the seed: a locked take
        renders exactly ``requested_seed``, so re-running it reproduces the take.
        An unlocked take still uses the requested seed here (there is nothing to
        vary for a single take); the flag is what a batch uses to derive the
        per-take seeds from a shared base.

        ``engine_id`` pins the engine this take will render on. The service
        resolves it from the approved draft; ``None`` means "let the worker pick
        the default", which is the only case where the registry may fall back.
        """
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise ValueError('invalid seed')
        if type(lock_seed) is not bool:
            raise ValueError('lock_seed must be a boolean')
        with self.store.locked():
            p = self.store.get(project_id)
            expected_target_id = target['id'] if target is not None else None
            if p.get('comfy_target_id') != expected_target_id:
                raise ValueError('draft target changed since preflight')
            if not p['approved']:
                raise ValueError('draft requires approval')
            validate_ready(p)
            self._assert_gpu_free()
            take = self._new_take(p, seed=seed, requested_seed=seed, seed_locked=lock_seed,
                                  engine_id=engine_id, target=target)
            p['takes'].append(take)
            self.store._save(p)
            return take

    def enqueue_batch(self, project_id, count, seed_base=None, engine_id=None, target=None):
        """Queue ``count`` takes that one worker renders back to back.

        The batch shares ``requested_seed`` (what the user asked for) and derives
        a distinct render seed per take from it, because two identical seeds
        would just render the same audio twice. ``queue_position`` is 1-based and
        is the order the worker renders in.
        """
        if type(count) is not int or not 1 <= count <= 8:
            raise ValueError('count must be between 1 and 8')
        if seed_base is None:
            base = random.SystemRandom().randrange(0, 2**32 - count)
        elif type(seed_base) is int and 0 <= seed_base <= 2**32 - 1 - (count - 1):
            base = seed_base
        else:
            raise ValueError('invalid seed_base')
        with self.store.locked():
            p = self.store.get(project_id)
            expected_target_id = target['id'] if target is not None else None
            if p.get('comfy_target_id') != expected_target_id:
                raise ValueError('draft target changed since preflight')
            if not p['approved']:
                raise ValueError('draft requires approval')
            validate_ready(p)
            self._assert_gpu_free()
            batch_id = str(uuid.uuid4())
            takes = [self._new_take(p, seed=base + index, requested_seed=base,
                                    seed_locked=False, batch_id=batch_id,
                                    queue_position=index + 1, engine_id=engine_id,
                                    target=target)
                     for index in range(count)]
            p['takes'].extend(takes)
            self.store._save(p)
            return takes

    def _succeeded_parent(self, project, parent_take_id):
        parent = self._take(project, parent_take_id)
        if parent.get('state') != 'succeeded':
            raise ValueError('parent take must be succeeded')
        if not parent.get('audio_file'):
            raise ValueError('parent take has no audio')
        return parent

    def audio_duration(self, project_id, take_id):
        """Duration from the take's own WAV header (never a stored claim)."""
        with self.open_audio(project_id, take_id) as audio:
            with wave.open(audio, 'rb') as wav:
                frames = wav.getnframes()
                rate = wav.getframerate()
        if frames <= 0 or rate <= 0:
            raise ValueError('audio unavailable')
        return round(frames / rate, 3)

    def enqueue_section(self, project_id, parent_take_id, start_s, end_s, seed,
                        caption=None, engine_id=None):
        """Queue one section of a succeeded take as its own auditionable take.

        Unlike repaint, this asks nothing special of the engine: it renders a
        short take with the draft's caption replaced by the section direction.
        The user auditions it, then decides whether to splice it into the song —
        the parent's bytes are never touched.
        """
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise ValueError('invalid seed')
        start_s, end_s = round(float(start_s), 3), round(float(end_s), 3)
        if caption is not None and (not isinstance(caption, str) or not caption.strip()
                                    or len(caption) > 512):
            raise ValueError('caption must contain 1-512 characters')
        with self.store.locked():
            project = self.store.get(project_id)
            parent = self._succeeded_parent(project, parent_take_id)
            duration = self.audio_duration(project_id, parent_take_id)
            if start_s < 0 or end_s <= start_s or end_s > duration + 1e-6:
                raise ValueError('the section must sit inside the take')
            length = round(end_s - start_s, 3)
            self._assert_gpu_free()
            take = self._new_take(project, seed=seed, requested_seed=seed, seed_locked=True,
                                  kind='section', parent=parent, duration_override=length,
                                  engine_id=engine_id or parent.get('engine_id'),
                                  extra={'range_start_s': start_s, 'range_end_s': end_s,
                                         'section_of': {'project_id': project_id,
                                                        'take_id': parent_take_id,
                                                        'start_s': start_s, 'end_s': end_s}})
            if caption:
                take['snapshot']['caption'] = caption
                take['snapshot']['sections'] = []
            project['takes'].append(take)
            self.store._save(project)
            return take

    def enqueue_repaint(self, project_id, parent_take_id, start_s, end_s, seed,
                        caption=None, lyrics_seed=None, engine_id=None):
        """Queue a new take that regenerates one range of a succeeded parent.

        The parent is read-only: the new take is appended, the parent's record
        and WAV are never touched.
        """
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise ValueError('invalid seed')
        for value, name in ((start_s, 'start_s'), (end_s, 'end_s')):
            if type(value) not in (int, float):
                raise ValueError(f'{name} must be a number')
        start_s, end_s = round(float(start_s), 3), round(float(end_s), 3)
        if caption is not None and (not isinstance(caption, str) or not caption.strip()
                                    or len(caption) > 512):
            raise ValueError('caption must contain 1–512 characters')
        if lyrics_seed is not None and (not isinstance(lyrics_seed, str)
                                        or not lyrics_seed.strip()
                                        or len(lyrics_seed) > 4096):
            raise ValueError('lyrics_seed must contain 1–4096 characters')
        with self.store.locked():
            project = self.store.get(project_id)
            parent = self._succeeded_parent(project, parent_take_id)
            duration = self.audio_duration(project_id, parent_take_id)
            if start_s < 0 or end_s <= start_s:
                raise ValueError('end_s must be greater than start_s')
            if end_s > duration + 1e-6:
                raise ValueError(f'end_s must be within the take ({duration:.3f} s)')
            if not REPAINT_MIN_SECONDS <= end_s - start_s <= REPAINT_MAX_SECONDS:
                raise ValueError(f'repaint range must be {REPAINT_MIN_SECONDS}-'
                                 f'{REPAINT_MAX_SECONDS} seconds')
            self._assert_gpu_free()
            snapshot = parent.get('snapshot') or {}
            effective_caption = caption if caption is not None \
                else (snapshot.get('caption') or project.get('caption') or '')
            effective_lyrics = lyrics_seed if lyrics_seed is not None \
                else (snapshot.get('lyrics') or '')
            if not snapshot.get('instrumental') and not effective_lyrics.strip():
                raise ValueError('lyrics are required to repaint a vocal take')
            target = _pinned_target(snapshot)
            take = self._new_take(
                project, seed=seed, requested_seed=seed, seed_locked=False,
                kind='repaint', parent=parent,
                # A repaint is rendered by the engine that rendered its parent
                # unless the caller names another one; the pipeline (and so the
                # audio it can produce) must not change silently underneath it.
                engine_id=engine_id or parent.get('engine_id'), target=target,
                extra={'range_start_s': start_s, 'range_end_s': end_s,
                       'source_duration': duration, 'planned_duration': duration},
                duration_override=duration)
            take['snapshot']['caption'] = effective_caption
            take['snapshot']['lyrics'] = effective_lyrics
            project['takes'].append(take)
            self.store._save(project)
            return copy.deepcopy(take)

    def enqueue_extension(self, project_id, parent_take_id, extend_seconds, seed,
                          caption=None, lyrics=None, engine_id=None):
        """Queue a new take that extends a succeeded parent (repaint outpaint).

        Geometry follows the long-form spike: regenerate the last overlap
        seconds and out-paint to parent + extend. The parent is never mutated.
        """
        if type(extend_seconds) is not int or not EXTEND_MIN_SECONDS <= extend_seconds <= EXTEND_MAX_SECONDS:
            raise ValueError(f'extend_seconds must be an integer between '
                             f'{EXTEND_MIN_SECONDS} and {EXTEND_MAX_SECONDS}')
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise ValueError('invalid seed')
        if caption is not None and (not isinstance(caption, str) or not caption.strip()
                                    or len(caption) > 512):
            raise ValueError('caption must contain 1–512 characters')
        if lyrics is not None and (not isinstance(lyrics, str) or len(lyrics) > 4096):
            raise ValueError('lyrics must be at most 4096 characters')
        with self.store.locked():
            project = self.store.get(project_id)
            parent = self._succeeded_parent(project, parent_take_id)
            duration = self.audio_duration(project_id, parent_take_id)
            if duration < EXTEND_SOURCE_MIN_SECONDS:
                raise ValueError(f'take must be at least {EXTEND_SOURCE_MIN_SECONDS} s '
                                 f'to extend')
            planned = round(duration + extend_seconds, 3)
            if planned > TOTAL_MAX_SECONDS:
                raise ValueError(f'extended take would be {planned:.0f} s; the '
                                 f'maximum is {TOTAL_MAX_SECONDS} s')
            overlap = round(min(EXTEND_MAX_OVERLAP_SECONDS, 0.2 * duration), 3)
            self._assert_gpu_free()
            snapshot = parent.get('snapshot') or {}
            effective_caption = caption if caption is not None \
                else (snapshot.get('caption') or project.get('caption') or '')
            effective_lyrics = lyrics if lyrics is not None \
                else (snapshot.get('lyrics') or '')
            if not snapshot.get('instrumental') and not effective_lyrics.strip():
                raise ValueError('lyrics are required to extend a vocal take')
            target = _pinned_target(snapshot)
            take = self._new_take(
                project, seed=seed, requested_seed=seed, seed_locked=False,
                kind='extension', parent=parent,
                engine_id=engine_id or parent.get('engine_id'), target=target,
                extra={'extend_seconds': extend_seconds,
                       'splice_start': round(duration - overlap, 3),
                       'overlap_seconds': overlap,
                       'source_duration': duration,
                       'planned_duration': planned},
                duration_override=planned)
            take['snapshot']['caption'] = effective_caption
            take['snapshot']['lyrics'] = effective_lyrics
            project['takes'].append(take)
            self.store._save(project)
            return copy.deepcopy(take)

    def next_queued(self, project_id, batch_id):
        """The next take of this batch a worker should render, or None."""
        if not isinstance(batch_id, str):
            return None
        try:
            takes = self.store.get(project_id)['takes']
        except (OSError, ValueError, KeyError, TypeError):
            return None
        pending = [t for t in takes
                   if t.get('batch_id') == batch_id and t['state'] == 'queued']
        return min(pending, key=lambda t: t.get('queue_position') or 0) if pending else None

    def cancel_queued(self, project_id):
        """Mark every queued take of a project cancelled; the running one finishes."""
        with self.store.locked():
            p = self.store.get(project_id)
            cancelled = 0
            for take in p['takes']:
                if take['state'] == 'queued':
                    take.update(state='cancelled', finished_at=time.time())
                    cancelled += 1
            if cancelled:
                self.store._save(p)
            return cancelled

    def _take(self, project, take_id):
        valid_id(take_id)
        for take in project['takes']:
            if take['take_id'] == take_id:
                return take
        raise KeyError('take not found')

    def attach(self, project_id, take_id, pid):
        """Record the detached worker's pid while the take is queued/running."""
        if type(pid) is not int or pid <= 0:
            raise ValueError('invalid pid')
        with self.store.locked():
            p = self.store.get(project_id)
            take = self._take(p, take_id)
            if take['state'] not in ('queued', 'running'):
                raise ValueError('invalid transition')
            take.update(pid=pid, pid_start=_stat_start_time(pid))
            self.store._save(p)
            return take

    def attach_render(self, project_id, take_id, pid):
        """Record the ACE render pid so an orphan can be reaped after a crash.

        The engine starts ACE in its own session; the studio worker dies with
        SIGKILL, the kernel releases its frames, but the ACE child keeps
        rendering on the GPU. Recording its pid (and start time, for pid-reuse
        safety) is what lets the next status poll kill it.
        """
        if type(pid) is not int or pid <= 0:
            raise ValueError('invalid pid')
        with self.store.locked():
            p = self.store.get(project_id)
            take = self._take(p, take_id)
            if take['state'] != 'running':
                raise ValueError('invalid transition')
            take.update(ace_pid=pid, ace_pid_start=_stat_start_time(pid))
            self.store._save(p)
            return take

    def _reap_render(self, take):
        """Kill the ACE render recorded on a take that no longer has an owner."""
        terminate_render(take.get('ace_pid'), take.get('ace_pid_start'))

    def reap_render(self, project_id, take_id):
        """Kill this take's recorded ACE render, if it is still running."""
        try:
            take = self._take(self.store.get(project_id), take_id)
        except (OSError, ValueError, KeyError, TypeError):
            return False
        return terminate_render(take.get('ace_pid'), take.get('ace_pid_start'))

    def claim(self, project_id, take_id, pid):
        """Atomically take ownership of a queued take for this worker process.

        A queued take becomes running; an already-running take is only accepted
        when it carries the same pid (no double-claim, no hijack).
        """
        if type(pid) is not int or pid <= 0:
            raise ValueError('invalid pid')
        with self.store.locked():
            p = self.store.get(project_id)
            take = self._take(p, take_id)
            if take['state'] == 'queued':
                take.update(state='running', pid=pid, pid_start=_stat_start_time(pid),
                            started_at=take.get('started_at') or time.time(), progress=0)
            elif not (take['state'] == 'running' and take.get('pid') == pid):
                raise ValueError('take not claimable')
            self.store._save(p)
            return take

    def start(self, project_id, take_id, pid=None):
        with self.store.locked():
            p = self.store.get(project_id)
            take = self._take(p, take_id)
            if take['state'] != 'queued':
                raise ValueError('invalid transition')
            take['state'] = 'running'
            take['started_at'] = take.get('started_at') or time.time()
            take['progress'] = take.get('progress') or 0
            if pid is not None:
                if type(pid) is not int or pid <= 0:
                    raise ValueError('invalid pid')
                take.update(pid=pid, pid_start=_stat_start_time(pid))
            self.store._save(p)
            return take

    def progress(self, project_id, take_id, *, started_at=None, progress=None):
        """Record a coarse render milestone on a live take.

        Only the owning worker calls this; it is intentionally cheap (one
        locked read/write) because it runs a handful of times per render.
        """
        if progress is not None and (type(progress) is not int or not 0 <= progress <= 100):
            raise ValueError('invalid progress')
        with self.store.locked():
            p = self.store.get(project_id)
            take = self._take(p, take_id)
            if take['state'] not in ('queued', 'running'):
                raise ValueError('invalid transition')
            if started_at is not None:
                take['started_at'] = started_at
            if progress is not None:
                take['progress'] = progress
            self.store._save(p)
            return take

    def fail(self, project_id, take_id, error):
        if not isinstance(error, str) or not error.strip():
            raise ValueError('error required')
        with self.store.locked():
            p = self.store.get(project_id)
            take = self._take(p, take_id)
            if take['state'] != 'running':
                raise ValueError('invalid transition')
            take.update(state='failed', error=error, finished_at=time.time())
            self.store._save(p)
            return take

    def abort(self, project_id, take_id, error):
        """Fail a take that never reached a worker (queued or running)."""
        if not isinstance(error, str) or not error.strip():
            raise ValueError('error required')
        with self.store.locked():
            p = self.store.get(project_id)
            take = self._take(p, take_id)
            if take['state'] not in ('queued', 'running'):
                raise ValueError('invalid transition')
            take.update(state='failed', error=error, audio_file=None, finished_at=time.time())
            self.store._save(p)
            return take

    def succeed(self, project_id, take_id, data, metadata=None, qc_record=None, edit=None):
        """Commit the rendered WAV *and* its QC settle as one succeeded state.

        ``qc_record`` and ``edit`` are what the worker measured and pre-rendered
        from these exact bytes. Both land in the same locked save as
        ``state='succeeded'``, so a client that stops polling on success can
        never observe a succeeded take whose edit has not arrived yet. The raw
        bytes are written once and never rewritten; the edit is a separate file.
        QC is metadata: a malformed record or an edit that cannot be written
        degrades to no QC/measurement and never fails the take.
        """
        if not isinstance(data, bytes) or len(data) > 100 * 1024 * 1024:
            raise ValueError('invalid audio size or type')
        if metadata is not None:
            # Provenance (engine, licence notice, fallback) travels with the
            # take. Validate it before the audio is written so a take is never
            # half-committed because its metadata could not be stored.
            if not isinstance(metadata, dict) or 'engine_id' not in metadata:
                raise ValueError('metadata must be a dict including engine_id')
            try:
                json.dumps(metadata, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError('metadata must be JSON-serialisable') from exc
        if qc_record is not None:
            from .qc import validate_take_qc
            try:
                qc_record = validate_take_qc(qc_record)
            except ValueError:
                logging.exception('ignoring an invalid QC record')
                qc_record = None
        if edit is not None:
            if not (isinstance(edit, dict) and isinstance(edit.get('bytes'), bytes)
                    and isinstance(edit.get('plan'), dict)
                    and isinstance(edit.get('report'), dict)):
                edit = None
        try:
            with wave.open(io.BytesIO(data), 'rb') as audio:
                frames = audio.getnframes()
                if frames <= 0 or audio.getframerate() <= 0:
                    raise ValueError('empty audio')
                expected = frames * audio.getnchannels() * audio.getsampwidth()
                if len(audio.readframes(frames)) != expected:
                    raise ValueError('truncated audio')
        except (wave.Error, EOFError) as exc:
            raise ValueError('invalid WAV audio') from exc
        with self.store.locked():
            p = self.store.get(project_id)
            take = self._take(p, take_id)
            if take['state'] != 'running':
                raise ValueError('invalid transition')
            name = valid_id(take_id) + '.wav'
            fd, temp = tempfile.mkstemp(dir=self.store.root, prefix='.audio-')
            try:
                with os.fdopen(fd, 'wb') as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                # A crash before metadata commit can leave an orphan, never a false success.
                replace_with_retry(temp, self.store.root / name)
                fsync_dir(self.store.root)
                edit_record = None
                if edit is not None:
                    try:
                        edit_record = self._write_edit(
                            take_id, data, edit['plan'], edit['report'], edit['bytes'])
                    except Exception:
                        # The audio stands; only the cosmetic edit is lost.
                        logging.exception('QC edit could not be written; take kept as rendered')
                take.update(state='succeeded', audio_file=name, error=None,
                            finished_at=time.time(), progress=100,
                            metadata=copy.deepcopy(metadata))
                if qc_record is not None:
                    take['qc'] = qc_record
                if edit_record is not None:
                    take.setdefault('edits', []).append(edit_record)
                    take['playable_edit_id'] = edit_record['edit_id']
                try:
                    self.store._save(p)
                except BaseException:
                    if edit_record is not None:
                        try:
                            (self.store.root / edit_record['audio_file']).unlink()
                        except OSError:
                            pass
                    raise
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            return take

    def record_qc(self, project_id, take_id, record):
        """Store the QC measurements for a succeeded take (validated, never guessed).

        A malformed record is refused here rather than persisted: the take keeps
        the measurements it already had, so a QC bug can never make a
        succeeded take unusable.
        """
        from .qc import validate_take_qc
        clean = validate_take_qc(record)
        with self.store.locked():
            p = self.store.get(project_id)
            take = self._take(p, take_id)
            if take['state'] != 'succeeded':
                raise ValueError('invalid transition')
            take['qc'] = clean
            self.store._save(p)
            return take['qc']

    def _write_edit(self, take_id, raw, plan, report, edited):
        """Materialise one QC edit beside the raw take and return its record.

        The caller holds the store lock. ``raw`` is only read (its hash is
        recorded); ``edited`` is written under a fresh uuid. Any failure
        unlinks the half-written file, so a record that cannot be saved never
        leaves an orphan behind.
        """
        import hashlib

        from .qc import analyse_wav
        after = analyse_wav(edited)
        edit_id = str(uuid.uuid4())
        name = valid_id(edit_id) + '.wav'
        fd, temp = tempfile.mkstemp(dir=self.store.root, prefix='.audio-')
        written = None
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(edited)
                out.flush()
                os.fsync(out.fileno())
            replace_with_retry(temp, self.store.root / name)
            written = name
            fsync_dir(self.store.root)
            return {
                'edit_id': edit_id,
                'kind': plan['kind'],
                'source_take_id': take_id,
                'source_audio_file': valid_id(take_id) + '.wav',
                'source_sha256': hashlib.sha256(raw).hexdigest(),
                'audio_file': name,
                'duration_s': after['file_s'],
                'peak_dbfs': after['peak_dbfs'],
                'clipped_frames': after['clipped_frames'],
                'requested_s': report['file_s'],
                'transform': {
                    'trim_end_s': plan['trim_end_s'],
                    'fade_ms': plan['fade_ms'],
                    'gain_db': plan['gain_db'],
                    'peak_cap_dbfs': plan['peak_cap_dbfs'],
                    'source_file_s': report['file_s'],
                    'source_peak_dbfs': report['peak_dbfs'],
                    'tail_silence_s': report['tail_silence_s'],
                },
                'created_at': time.time(),
            }
        except BaseException:
            if written is not None:
                try:
                    (self.store.root / written).unlink()
                except OSError:
                    pass
            raise
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def apply_qc_edit(self, project_id, take_id, fade_ms=None):
        """Materialise the QC edit as a NEW file with its own id.

        Used to re-settle a take that is already succeeded (a legacy take, or a
        caller asking for a different fade); a fresh render commits its edit
        atomically with success through :meth:`succeed`. Returns ``None`` when
        the take needs neither a trim nor a peak cap, so the caller stores no
        empty edit.
        """
        from .qc import analyse_wav, plan_edit, render_edit, take_qc
        with self.open_audio(project_id, take_id) as audio:
            raw = audio.read()
        report = analyse_wav(raw)
        plan = plan_edit(report, fade_ms)
        if plan is None:
            return None
        edited = render_edit(raw, plan)
        # Write and record under the store lock a delete needs: otherwise a
        # concurrent delete can land between the rename and the record save and
        # leave a WAV nothing references.
        with self.store.locked():
            p = self.store.get(project_id)
            take = self._take(p, take_id)
            if take['state'] != 'succeeded':
                raise ValueError('invalid transition')
            edit = self._write_edit(take_id, raw, plan, report, edited)
            try:
                take.setdefault('edits', []).append(edit)
                take['playable_edit_id'] = edit['edit_id']
                # The QC block is the measurement of the RAW file; the edit
                # carries its own length so neither is mistaken for the other.
                take['qc'] = take.get('qc') or take_qc(report,
                                                       requested_s=report['file_s'])
                self.store._save(p)
            except BaseException:
                # A record that cannot be saved must not leave its WAV behind.
                try:
                    (self.store.root / edit['audio_file']).unlink()
                except OSError:
                    pass
                raise
            return edit

    def read_audio(self, project_id, take_id, edit_id=None):
        with self.open_audio(project_id, take_id, edit_id=edit_id) as audio:
            return audio.read()

    def open_audio(self, project_id, take_id, edit_id=None):
        p = self.store.get(project_id)
        take = self._take(p, take_id)
        name = valid_id(take_id) + '.wav'
        if take['state'] != 'succeeded' or take['audio_file'] != name:
            raise ValueError('audio unavailable')
        if edit_id is not None:
            valid_id(edit_id)
            for edit in take.get('edits') or []:
                if edit.get('edit_id') == edit_id:
                    name = edit.get('audio_file')
                    break
            else:
                raise ValueError('edit not found')
        if not isinstance(name, str) or not name.endswith('.wav'):
            raise ValueError('audio unavailable')
        # The record is data on disk and may have been hand-edited: a plain
        # basename whose stem is a UUID is the only shape that can be opened.
        # O_NOFOLLOW guards the last component only, so '../x.wav' must be
        # refused here or it escapes the store.
        if name != os.path.basename(name) or name.startswith('.'):
            raise ValueError('audio unavailable')
        try:
            valid_id(name[:-4])
        except ValueError:
            raise ValueError('audio unavailable') from None
        try:
            fd = open_nofollow(self.store.root / name)
        except OSError as exc:
            raise ValueError('audio unavailable') from exc
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ValueError('audio unavailable')
        return os.fdopen(fd, 'rb')

    def reconcile_dead_workers(self, *, grace_seconds=SPAWN_GRACE_SECONDS):
        """Fail queued/running takes whose worker process is gone.

        Safe to call at any time: live workers (verified by pid and start time)
        are never touched, and takes whose spawn is still in flight are skipped
        until the grace period expires. The GPU lease belongs to the worker, so
        failing a dead take releases it.
        """
        if type(grace_seconds) not in (int, float) or grace_seconds < 0:
            raise ValueError('invalid grace period')
        now = time.time()
        reconciled = 0
        with self.store.locked():
            for p in self.store.all():
                changed = False
                for t in p['takes']:
                    if t['state'] not in ('queued', 'running'):
                        continue
                    pid = t.get('pid')
                    if pid is None:
                        created = t.get('created_at')
                        # No pid and no timestamp: a pre-upgrade record whose owner is gone.
                        if created is not None and now - created < grace_seconds:
                            continue
                    elif process_alive(pid, t.get('pid_start')):
                        continue
                    # The owner is gone: never leave its ACE render burning the
                    # GPU behind a lock the kernel has already freed.
                    self._reap_render(t)
                    t.update(state='failed', error='worker died', audio_file=None,
                             finished_at=time.time())
                    reconciled += 1
                    changed = True
                if changed:
                    self.store._save(p)
        return reconciled

    def recover(self, *, grace_seconds=SPAWN_GRACE_SECONDS):
        """Self-healing sweep: called on status polls and at startup.

        The worker lease is the ground truth for a live render, so it is checked
        first: while a render owns it, only takes whose recorded worker is
        provably dead are failed. With the lease free a stuck queued/running take
        (dead pid, or no pid past the spawn grace) is failed too, which frees the
        profile for the next generate.
        """
        lease = self.store.worker_lock()
        if lease is not None:
            lease.close()
        return self.reconcile_dead_workers(grace_seconds=grace_seconds)

    def observed_render_seconds(self, duration_seconds=None, tolerance=5.0):
        """Mean wall-clock of this machine's successful renders of a similar length.

        Gives the per-duration ETA a measured rate once takes exist: only renders
        whose requested duration is within ``tolerance`` seconds of the requested
        one count, so a 20 s take's speed never prices a 180 s take. Returns None
        when nothing comparable has been measured (callers then use the model).
        """
        durations = []
        for project in self.store.all():
            for take in project['takes']:
                if take.get('state') != 'succeeded':
                    continue
                started, finished = take.get('started_at'), take.get('finished_at')
                if type(started) not in (int, float) or type(finished) not in (int, float):
                    continue
                if finished < started:
                    continue
                if duration_seconds is not None:
                    requested = (take.get('snapshot') or {}).get('duration_seconds')
                    if type(requested) not in (int, float) or \
                            abs(requested - duration_seconds) > tolerance:
                        continue
                durations.append(finished - started)
        if not durations:
            return None
        return sum(durations) / len(durations)

    def reconcile_interrupted(self, *, confirmed=False):
        """Recover only after the user has checked for orphan ACE processes."""
        if confirmed is not True:
            raise ValueError('confirm all old ACE workers have stopped before recovery')
        lease = self.store.worker_lock()
        if lease is None:
            raise ValueError('GPU busy')
        with lease:
            return self._reconcile_locked()

    def _reconcile_locked(self):
        """Caller must hold the worker lease."""
        with self.store.locked():
            count = 0
            for p in self.store.all():
                changed = False
                for t in p['takes']:
                    if t['state'] in ('queued', 'running'):
                        self._reap_render(t)
                        t.update(state='failed', error='interrupted by restart',
                                 audio_file=None, finished_at=time.time())
                        count += 1
                        changed = True
                if changed:
                    self.store._save(p)
            return count
