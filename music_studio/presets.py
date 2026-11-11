"""Named draft presets: a caption plus the metadata the user reuses.

Presets live in their own JSON file beside the projects, never as projects: they
carry no takes, no approval and no renders, and a preset must not show up in
/status or consume a project id.
"""
import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path

from .store import fsync_dir, normalize, validate

NAME_MAX = 64
PRESET_FIELDS = ('caption', 'bpm', 'keyscale', 'vocal_language', 'instrumental')


def valid_name(name):
    if (not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX
            or any(ch in name for ch in ('/', '\\', '\x00'))
            or any(ord(ch) < 32 for ch in name)):
        raise ValueError(f'preset name must be 1-{NAME_MAX} characters without slashes')
    return name


class Presets:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'presets.json'

    @contextlib.contextmanager
    def _locked(self):
        with (self.root / '.presets.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _load(self):
        """Read the file, treating a truncated/hand-edited one as empty.

        A corrupt presets file must never break the preset panel or /status; the
        next save rewrites it atomically.
        """
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {name: preset for name, preset in data.items()
                if isinstance(name, str) and isinstance(preset, dict)
                and isinstance(preset.get('caption'), str)}

    def _save(self, data):
        fd, temp = tempfile.mkstemp(dir=self.root, prefix='.presets-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(data, out, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
            fsync_dir(self.root)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def all(self):
        data = self._load()
        return [data[name] for name in sorted(data)]

    def save(self, body):
        """Create or replace one preset (same name = upsert)."""
        if not isinstance(body, dict):
            raise ValueError('invalid preset')
        name = valid_name(body.get('name'))
        draft = normalize({key: body.get(key) for key in PRESET_FIELDS if key in body})
        if 'caption' not in draft:
            raise ValueError('caption is required')
        validate(draft)
        if not isinstance(draft['caption'], str) or not draft['caption'].strip():
            raise ValueError('caption is required')
        preset = {'name': name, 'preset_id': name, 'caption': draft['caption'],
                  'bpm': draft.get('bpm'), 'keyscale': draft.get('keyscale'),
                  'vocal_language': draft.get('vocal_language', 'en'),
                  'instrumental': draft.get('instrumental', False)}
        with self._locked():
            data = self._load()
            data[name] = preset
            self._save(data)
        return self.all()

    def delete(self, name):
        """Idempotent delete: removing an absent preset is not an error."""
        valid_name(name)
        with self._locked():
            data = self._load()
            if data.pop(name, None) is not None:
                self._save(data)
        return self.all()
