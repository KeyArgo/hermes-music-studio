"""Local JSON store. Caller supplies the plugin's private data directory."""
import contextlib
import copy
import fcntl
import json
import math
import os
import tempfile
import time
import uuid
from pathlib import Path
from .sections import derive_sections, validate_sections

FIELDS = {'title': '', 'idea': '', 'caption': '', 'lyrics': '', 'engine': 'ace',
          'duration_seconds': 30, 'instrumental': False, 'vocal_language': 'en',
          'bpm': None, 'keyscale': None, 'sections': [], 'minimax_guidance': None,
          # A named ComfyUI endpoint, when the user chose one. The id is
          # resolved against TargetStore; None means the engine's default URL.
          'comfy_target_id': None}

# Registered engine ids, loaded lazily so the store (and its tests) does not
# import the ACE runtime just to validate a draft. 'ace' stays valid for
# already-approved drafts; resolve_id maps it to the local engine.
_ENGINE_IDS = None


def registered_engine_ids():
    global _ENGINE_IDS
    if _ENGINE_IDS is None:
        from .engines.registry import default_engines
        _ENGINE_IDS = frozenset(e.id for e in default_engines())
    return _ENGINE_IDS


def engine_is_registered(value):
    from .engines.registry import resolve_id
    return isinstance(value, str) and resolve_id(value) in registered_engine_ids()

# Input bounds. caption/lyrics mirror the ACE engine's own limits exactly, so an
# approved draft can always render; title/idea are UI-sized metadata.
TEXT_LIMITS = {'title': 200, 'idea': 2000, 'caption': 512, 'lyrics': 4096}

# Duration contract. The floor is the shortest take that still holds a musical
# phrase. The ceiling is NOT the model's own limit (ACE-Step buckets audio up to
# 360 s); it is what this GPU (RTX 4070 Ti, 12 GB, offload_to_cpu=True) was
# actually measured to render without OOM. Raising it further requires a new
# measurement, not a source-code reading.
DURATION_MIN_SECONDS = 10
DURATION_MAX_SECONDS = 180
RECOMMENDED_DURATION_SECONDS = 120

# The engine an unnamed draft renders on. Drafts carry ``engine`` from FIELDS,
# so this is what the top-level /status limits describe; a draft pinned to
# another engine is validated against *that* engine's declared window instead
# (MiniMax Music 3 renders 10-360 s), never against ACE's 180 s ceiling.
DEFAULT_ENGINE_ID = 'ace_local'


# Source-conditioned renders. Repaint regenerates one in-place range: short
# enough that the model keeps the surrounding arrangement, long enough that it
# can sing a phrase. Extension regenerates a tail overlap and appends new
# material; the engine's own single-call cap is 600 s (acestep DURATION_MAX).
REPAINT_MIN_SECONDS = 3
REPAINT_MAX_SECONDS = 30
EXTEND_MIN_SECONDS = 10
EXTEND_MAX_SECONDS = 60
EXTEND_MAX_OVERLAP_SECONDS = 8.0   # longform spike: 6-10 s tolerates a section change
EXTEND_SOURCE_MIN_SECONDS = 20     # an 8 s overlap on a shorter take leaves nothing
TOTAL_MAX_SECONDS = 600

# Wall-clock ETA model, in whole-render terms (model load included, because a
# take's clock starts when the worker spawns the ACE child). Two measurements on
# this GPU: an uncontended first pass rendered 60/120/180 s in 88-120 s total,
# but the same 180 s take through the plugin path, with another render competing
# for the GPU, took 553.5 s. The product runs on the contended box, so the model
# is fitted to that case; on an idle GPU it overstates and the progress-derived
# ETA converges down. Timeouts are 3x this value, i.e. >= 3x the worst observed
# render for that duration.
RENDER_BASE_SECONDS = 125.0
RENDER_SECONDS_PER_AUDIO_SECOND = 2.4


def estimate_render_seconds(duration_seconds):
    """Expected wall-clock render time for a take of this duration."""
    return RENDER_BASE_SECONDS + RENDER_SECONDS_PER_AUDIO_SECOND * float(duration_seconds)


def render_timeout_seconds(duration_seconds):
    """Kill a render only after 3x its modelled wall clock, never below 3 min."""
    return max(180, int(3 * estimate_render_seconds(duration_seconds) + 0.999))


def _integral(value):
    """Limits are declared in whole seconds; keep the payload JSON-int clean."""
    return int(value) if float(value).is_integer() else value


def engine_limits(engine=None):
    """The duration window of ``engine``, in seconds.

    ``engine`` is a draft's (or take's) engine name; legacy names resolve, and
    an unnamed draft gets the contract default. An unknown name raises: the
    caller is validating a draft that named an engine the build does not have.
    """
    from .engines.registry import default_engines, resolve_id
    wanted = resolve_id(engine) if isinstance(engine, str) and engine else None
    wanted = wanted or DEFAULT_ENGINE_ID
    for candidate in default_engines():
        if candidate.id == wanted:
            declared = candidate.limits
            return (_integral(declared.min_seconds),
                    _integral(declared.max_seconds),
                    _integral(declared.recommended_seconds))
    raise ValueError(f'unknown engine: {engine}')


def limits_for(engine=None):
    """The self-describing duration contract for one engine (default if None)."""
    minimum, maximum, recommended = engine_limits(engine)
    return {'min_seconds': minimum, 'max_seconds': maximum,
            'recommended_seconds': recommended}


def limits():
    """The top-level duration contract served by /status (the default engine's)."""
    return limits_for()



# Vocabularies fixed by the frozen client contract. 'auto' is a studio-level
# choice (let the model decide); the engine's own vocabulary spells it 'unknown',
# and ace_worker translates it at that boundary.
VOCAL_LANGUAGES = ('en', 'es', 'fr', 'de', 'it', 'pt', 'ja', 'ko', 'zh', 'auto')
BPM_MIN, BPM_MAX = 40, 220
KEYSCALE_MAX = 24


def valid_id(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        raise ValueError('invalid UUID') from None
    return value


def normalize(draft):
    """Treat a blank keyscale as 'no key', the way the contract's null does."""
    if draft.get('keyscale') == '':
        draft = {**draft, 'keyscale': None}
    return draft


def validate(draft, *, engine=None):
    """Validate a draft (or the supplied subset of one).

    ``engine`` is the engine the draft will render on when the draft itself
    does not name one (a partial update). Duration is checked against that
    engine's declared window, so a MiniMax-pinned draft can be longer than
    ACE's measured ceiling — the engine is the authority, not a global number.
    """
    if set(draft) - FIELDS.keys():
        raise ValueError('unknown draft field')
    engine_name = engine if engine is not None else draft.get('engine')
    if not (isinstance(engine_name, str) and engine_is_registered(engine_name)):
        # An unknown engine is refused by the engine branch below with the
        # contract's own message; bound the duration by the default meanwhile.
        engine_name = None
    for key, value in draft.items():
        if key == 'duration_seconds':
            minimum, maximum, _ = engine_limits(engine_name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f'duration must be between {minimum} and '
                                 f'{maximum} seconds')
        elif key == 'instrumental':
            if type(value) is not bool:
                raise ValueError('instrumental must be a boolean')
        elif key == 'vocal_language':
            if not isinstance(value, str) or value not in VOCAL_LANGUAGES:
                raise ValueError('vocal_language must be one of ' + ', '.join(VOCAL_LANGUAGES))
        elif key == 'bpm':
            # bool is an int subclass; True must never read as 1 bpm.
            if value is not None and (type(value) is not int or not BPM_MIN <= value <= BPM_MAX):
                raise ValueError(f'bpm must be {BPM_MIN}-{BPM_MAX} or null')
        elif key == 'keyscale':
            if value is not None and (not isinstance(value, str) or len(value) > KEYSCALE_MAX):
                raise ValueError(f'keyscale must be at most {KEYSCALE_MAX} characters or null')
        elif key == 'minimax_guidance':
            if value is not None and (type(value) not in (int, float) or not 0.5 <= value <= 3.0 or not math.isfinite(value)):
                raise ValueError('MiniMax guidance must be between 0.5 and 3.0 or null')
        elif key == 'sections':
            # Internal contiguity is checked here; coverage of the duration is
            # checked against the merged draft by create/update/approve.
            validate_sections(value, draft.get('duration_seconds'))
        elif key == 'comfy_target_id':
            # Existence is checked by the Store, which knows the data root;
            # this only rejects the wrong shape (a bool, a list, an empty id).
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError('comfy_target_id must be a target id or null')
        elif key == 'engine':
            # Any registered engine is a legal draft value (a draft may pin
            # 'minimax_m3_comfy'); the legacy name 'ace' stays valid and maps to
            # ace_local at resolution time.
            if not engine_is_registered(value):
                raise ValueError('unsupported engine')
        elif not isinstance(value, str):
            raise ValueError('draft text fields must be strings')
        elif key in TEXT_LIMITS and len(value) > TEXT_LIMITS[key]:
            raise ValueError(f'{key} must be at most {TEXT_LIMITS[key]} characters')


def validate_ready(project):
    # Legacy projects predate the metadata fields; a missing field means its
    # default, never a failed approval.
    draft = {key: project.get(key, default) for key, default in FIELDS.items()}
    validate(draft)
    if not draft['caption'].strip():
        raise ValueError('caption is required')
    # An instrumental take may legitimately have no lyrics; the engine is told
    # instrumental=True and gets empty lyrics (no [inst] sentinel).
    if not draft['instrumental'] and not draft['lyrics'].strip():
        raise ValueError('lyrics is required')
    # Enforce the engine's real caps here, not only on draft input: a legacy or
    # hand-edited project must never pass approval only to fail in the worker.
    if len(draft['caption']) > 512:
        raise ValueError('caption must be at most 512 characters')
    if len(draft['lyrics']) > 4096:
        raise ValueError('lyrics must be at most 4096 characters')
    minimum, maximum, _ = engine_limits(draft.get('engine'))
    if not minimum <= draft['duration_seconds'] <= maximum:
        raise ValueError(f'duration must be between {minimum} and '
                         f'{maximum} seconds')
    validate_sections(draft['sections'], draft['duration_seconds'])


def sections_for(project):
    """Stored sections, or the structure implied by the lyrics when there are none."""
    stored = project.get('sections') or []
    if stored:
        return stored, 'stored'
    return derive_sections(project.get('lyrics', ''), project.get('duration_seconds')), 'lyrics'


def fsync_dir(path):
    """Persist a directory entry after os.replace, so a rename survives power loss."""
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Populated by all(); lets service.status surface skipped files.
        self.warnings = []

    def worker_lock(self):
        """Acquire the process-shared worker lease, or return None if busy."""
        lock = (self.root / '.worker.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return None
        except BaseException:
            lock.close()
            raise
        return lock

    @contextlib.contextmanager
    def locked(self):
        with (self.root / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _require_target(self, target_id):
        """Refuse a draft that names a ComfyUI target this profile does not have."""
        if target_id is None:
            return
        from .network import TargetStore
        try:
            TargetStore(self.root).get(target_id)
        except (KeyError, ValueError):
            raise ValueError(f'unknown target: {target_id}') from None

    def create(self, **draft):
        draft = normalize(draft)
        validate(draft)
        self._require_target(draft.get('comfy_target_id'))
        project = {'project_id': str(uuid.uuid4()), **copy.deepcopy(FIELDS),
                   **copy.deepcopy(draft), 'approved': False, 'takes': []}
        # A partial draft may not have named its duration; the default is what
        # the sections must cover.
        validate_sections(project['sections'], project['duration_seconds'])
        with self.locked():
            self._save(project)
        return project

    def approve(self, project_id, *, base_updated_at=None):
        """Approve a draft, optionally bound to the version the user reviewed.

        The dashboard always passes the ``updated_at`` token its form was loaded
        with, so an approval can never attach to text that changed underneath
        the user. The token is checked inside the write lock, i.e. atomically
        with the approval itself. ``None`` is reserved for internal callers that
        are not acting on a human review (tests, migrations).
        """
        with self.locked():
            project = self.get(project_id)
            if base_updated_at is not None and project.get('updated_at') != base_updated_at:
                raise ValueError('stale draft')
            validate_ready(project)
            self._require_target(project.get('comfy_target_id'))
            # Approval is the migration point for pre-metadata projects: once a
            # user approves, the record carries every field a render reads.
            for key, default in FIELDS.items():
                project.setdefault(key, default)
            project['approved'] = True
            self._save(project)
            return project

    def update(self, project_id, *, base_updated_at=None, **draft):
        draft = normalize(draft)
        with self.locked():
            project = self.get(project_id)
            if base_updated_at is not None and project.get('updated_at') != base_updated_at:
                raise ValueError('stale draft')
            # A partial edit may change only the duration; the engine it will
            # render on is then the stored one, and that engine decides the cap.
            validate(draft, engine=draft.get('engine', project.get('engine')))
            if 'comfy_target_id' in draft:
                self._require_target(draft.get('comfy_target_id'))
            if 'engine' in draft:
                # Switching engines must not smuggle in a duration the new
                # engine cannot render.
                validate({'duration_seconds': draft.get(
                    'duration_seconds', project.get('duration_seconds'))},
                    engine=draft['engine'])
            merged = {**project, **draft}
            # A partial edit (sections alone, or a duration change) is only
            # valid against the merged draft it produces.
            validate_sections(merged.get('sections', []), merged.get('duration_seconds'))
            if any(project.get(k) != v for k, v in draft.items()):
                project.update(draft)
                project['approved'] = False
            self._save(project)
            return project

    def rename(self, project_id, title):
        """Retitle a project without touching what a render would use.

        The title is not part of a take's snapshot, so renaming never revokes an
        approval: the user renames a song, they do not edit what was reviewed.
        """
        validate({'title': title})
        if not title.strip():
            raise ValueError('title is required')
        with self.locked():
            project = self.get(valid_id(project_id))
            project['title'] = title
            self._save(project)
            return project

    def delete(self, project_id):
        """Delete a project together with every WAV rendered for it.

        Refused while the project has an active render: deleting the record
        under a live worker would strand an ACE process with nothing to commit
        to (and the worker would then fail a project that no longer exists).
        """
        pid = valid_id(project_id)
        with self.locked():
            project = self.get(pid)
            if any(t.get('state') in ('queued', 'running') for t in project['takes']):
                raise ValueError('GPU busy: project has an active render')
            for take in project['takes']:
                names = [take.get('audio_file')]
                names += [edit.get('audio_file') for edit in take.get('edits') or []
                          if isinstance(edit, dict)]
                for name in names:
                    if not isinstance(name, str) or not name.endswith('.wav'):
                        continue
                    try:
                        if valid_id(name[:-len('.wav')]) + '.wav' != name:
                            continue
                    except ValueError:
                        continue
                    self._remove_audio_file(name)
            (self.root / (pid + '.json')).unlink()
            fsync_dir(self.root)
        return pid

    def _remove_audio_file(self, name):
        """Remove one uuid-named WAV inside the store and its peaks sidecar."""
        path = self.root / name
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
        except OSError:
            pass
        # The peaks sidecar is derived state; never leave it behind.
        try:
            peaks = self.root / (name[:-len('.wav')] + '.peaks.json')
            if peaks.is_file() and not peaks.is_symlink():
                peaks.unlink()
        except OSError:
            pass

    def remember(self, project_id, *, kind, text, take_id=None, section=None, patch=None,
                 bump=True):
        """Append one line to the song's journal — the memory the person asked for.

        It lives on the project file, so it survives a reload, a pane restart and a gateway bounce.
        `bump=False` records a like or a preview without turning the draft unapproved.
        """
        with self.locked():
            project = self.get(project_id)
            memory = project.setdefault('memory', {})
            events = memory.setdefault('events', [])
            events.append({'at': time.time(), 'kind': kind, 'text': text,
                           'take_id': take_id, 'section': section, 'patch': patch})
            del events[:-200]            # a song keeps its recent history, not an unbounded log
            if kind == 'keep' and take_id:
                memory['kept_take_id'] = take_id
            if kind == 'keep' and not text:
                memory.setdefault('preferences', [])
            self._save(project, bump=bump)
            return memory

    def add_checkpoint(self, project_id, checkpoint, *, keep=20):
        """Store one checkpoint on the project and prune the oldest beyond ``keep``.

        A checkpoint is a *copy* of the draft, not an index into the journal: the journal keeps 200
        events, so an index would one day point at history that has been trimmed away.
        """
        with self.locked():
            project = self.get(project_id)
            saved = project.setdefault('checkpoints', [])
            saved.append(checkpoint)
            pruned = []
            while len(saved) > keep:
                victim = next((c for c in saved if not c.get('pinned')), None)
                if victim is None:
                    break
                saved.remove(victim)
                pruned.append(victim.get('checkpoint_id'))
            self._save(project, bump=False)      # saving a spot is not a draft edit
            return {'checkpoints': list(saved), 'pruned': pruned}

    def checkpoints(self, project_id):
        return list((self.get(project_id) or {}).get('checkpoints') or [])

    def get(self, project_id):
        return json.loads((self.root / (valid_id(project_id) + '.json')).read_text())

    def all(self, warnings=None):
        """Return every readable project, skipping (and reporting) broken files.

        A single truncated or hand-edited file must never take down /status, the
        enqueue busy-check, or recovery for the whole profile.
        """
        projects = []
        self.warnings = []
        for path in sorted(self.root.glob('*.json')):
            try:
                valid_id(path.stem)
            except ValueError:
                continue
            try:
                project = self.get(path.stem)
                if not isinstance(project, dict) or not isinstance(project.get('takes'), list):
                    raise ValueError('malformed project')
            except (OSError, ValueError, KeyError, TypeError):
                message = f'skipped unreadable project {path.name}'
                self.warnings.append(message)
                if warnings is not None:
                    warnings.append(message)
                continue
            projects.append(project)
        return projects

    def _save(self, project, *, bump=True):
        target = self.root / (valid_id(project['project_id']) + '.json')
        # Strictly increasing: a token minted before a save can never collide
        # with the next save's token, so a stale approval is always detectable.
        # `bump=False` is for bookkeeping that is not a draft edit — remembering
        # that a person liked a take must not revoke their approval of the text.
        previous = project.get('updated_at')
        if bump:
            stamp = time.time()
            if type(previous) in (int, float) and stamp <= previous:
                stamp = previous + 1e-6
            project['updated_at'] = stamp
        fd, name = tempfile.mkstemp(dir=self.root, prefix='.project-')
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump(project, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, target)
            fsync_dir(self.root)
        finally:
            if os.path.exists(name):
                os.unlink(name)
