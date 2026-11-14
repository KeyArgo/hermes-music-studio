"""Studio-wide settings: where the library lives, which ComfyUI, how M3 renders.

Two things are stored next to the library (in the plugin data dir, which never
moves itself):

- ``engine-settings.json`` — the ComfyUI endpoint and the MiniMax Music 3
  sampler knobs, so an owner can point the engine at their own server without
  editing code or environment variables.
- ``library.json`` — a pointer to a different library directory, so "save my
  music somewhere else" is a setting instead of a rebuild.

Everything here is validated on the way in: a wrong sampler name should fail at
save time with a sentence, not at render time with a stack trace.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .store import replace_with_retry

SETTINGS_FILE = 'engine-settings.json'
LIBRARY_POINTER = 'library.json'

DEFAULTS = {
    'comfy_url': '',
    # Hostnames "Find a renderer" should also try, comma separated (a Spark box, a NAS,
    # another machine on the LAN). Empty means loopback only.
    'scan_hosts': '',
    # Where 'Export' writes a finished take. Empty means ~/Music/Hermes Music Studio.
    'export_dir': '',
    # Optional ACE-Step install: the runtime venv and the source checkout. Empty means "not on this
    # machine", which the engine reports rather than guessing a path that belongs to someone else.
    'ace_runtime_dir': '',
    'ace_source_dir': '',
    # An ACE-Step HTTP server (any host running acestep.api_server). Empty means "none configured".
    'ace_remote_url': '',
    'minimax': {
        'steps': 30,
        'cfg': 4.0,
        'cfg_scale': 1.5,
        'top_k': 50,
        'sampler': 'euler',
        'scheduler': 'simple',
    },
}

#: Ranges the nodes accept. ``euler``/``simple`` are ComfyUI's own names; a
#: different ComfyUI may ship more, so these stay advisory for the sampler and
#: hard limits for the numbers.
LIMITS = {
    'steps': (1, 100),
    'cfg': (0.0, 20.0),
    'cfg_scale': (0.0, 10.0),
    'top_k': (1, 200),
}

SAMPLERS = ('euler', 'euler_ancestral', 'dpmpp_2m', 'dpmpp_2m_sde', 'heun', 'lms')
SCHEDULERS = ('simple', 'normal', 'karras', 'exponential', 'sgm_uniform')


def settings_path(base: Path) -> Path:
    return Path(base) / SETTINGS_FILE


def _merge(raw: dict) -> dict:
    out = {'comfy_url': DEFAULTS['comfy_url'], 'scan_hosts': DEFAULTS['scan_hosts'],
           'export_dir': DEFAULTS['export_dir'],
           'minimax': dict(DEFAULTS['minimax'])}
    if isinstance(raw.get('comfy_url'), str):
        out['comfy_url'] = raw['comfy_url']
    for key in ('ace_runtime_dir', 'ace_source_dir', 'ace_remote_url'):
        if isinstance(raw.get(key), str):
            out[key] = raw[key]
    if isinstance(raw.get('scan_hosts'), str):
        out['scan_hosts'] = raw['scan_hosts']
    if isinstance(raw.get('export_dir'), str):
        out['export_dir'] = raw['export_dir']
    raw_minimax = raw.get('minimax')
    if isinstance(raw_minimax, dict):
        for key in DEFAULTS['minimax']:
            if key in raw_minimax:
                out['minimax'][key] = raw_minimax[key]
    return out


def read(base: Path) -> dict:
    """Current settings, defaults filled in. Never raises on a bad file."""
    try:
        raw = json.loads(settings_path(base).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raw = {}
    return _merge(raw if isinstance(raw, dict) else {})


def validate(payload: dict) -> dict:
    """Return the merged settings or raise ``ValueError`` naming the field."""
    if not isinstance(payload, dict):
        raise ValueError('settings must be an object')
    merged = _merge(payload)
    url = merged['comfy_url'].strip()
    if url and not (url.startswith('http://') or url.startswith('https://')):
        raise ValueError('comfy_url must start with http:// or https://')
    merged['comfy_url'] = url.rstrip('/')
    hosts = merged.get('scan_hosts') or ''
    if not isinstance(hosts, str):
        raise ValueError('scan_hosts must be a comma-separated string')
    merged['scan_hosts'] = ', '.join(part.strip() for part in hosts.split(',') if part.strip())
    folder = (merged.get('export_dir') or '').strip()
    if folder and not Path(folder).expanduser().is_absolute():
        raise ValueError('export_dir must be an absolute path')
    merged['export_dir'] = folder
    for key, (low, high) in LIMITS.items():
        value = merged['minimax'][key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'{key} must be a number')
        if not low <= value <= high:
            raise ValueError(f'{key} must be between {low} and {high}')
    if isinstance(merged['minimax']['top_k'], float):
        raise ValueError('top_k must be a whole number')
    for key, allowed in (('sampler', SAMPLERS), ('scheduler', SCHEDULERS)):
        value = merged['minimax'][key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{key} must be a name')
        if value not in allowed:
            raise ValueError(f'{key} must be one of: {", ".join(allowed)}')
    return merged


def write(base: Path, payload: dict) -> dict:
    """Merge ``payload`` onto the saved settings; keys it omits keep their saved value."""
    if not isinstance(payload, dict):
        raise ValueError('settings must be an object')
    current = read(base)
    merged_input = dict(current)
    for key, value in payload.items():
        if key == 'minimax' and isinstance(value, dict):
            knobs = dict(current.get('minimax') or {})
            knobs.update(value)
            merged_input['minimax'] = knobs
        else:
            merged_input[key] = value
    merged = validate(merged_input)
    path = settings_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(merged, indent=2) + '\n', encoding='utf-8')
    replace_with_retry(tmp, path)
    return merged


def comfy_url(base: Path) -> str | None:
    url = read(base)['comfy_url']
    return url or None


def minimax_kwargs(base: Path) -> dict:
    return dict(read(base)['minimax'])


# -- library pointer -------------------------------------------------------
def library_dir(base: Path) -> Path | None:
    """The library this profile points at, when it points somewhere else."""
    try:
        data = json.loads((Path(base) / LIBRARY_POINTER).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    path = data.get('path') if isinstance(data, dict) else None
    if not isinstance(path, str) or not path.strip():
        return None
    target = Path(path).expanduser()
    return target if target.is_dir() else None


def point_library_at(base: Path, target: Path) -> dict:
    """Record ``target`` as this profile's library. The caller does the copying."""
    base = Path(base)
    target = Path(target).expanduser()
    if not target.is_dir():
        raise ValueError(f'library directory does not exist: {target}')
    payload = {'path': str(target)}
    base.mkdir(parents=True, exist_ok=True)
    path = base / LIBRARY_POINTER
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    replace_with_retry(tmp, path)
    return payload


def clear_library_pointer(base: Path) -> None:
    try:
        (Path(base) / LIBRARY_POINTER).unlink()
    except FileNotFoundError:
        pass


def base_dir() -> Path | None:
    """The plugin's own data dir, resolved without importing ``service``.

    ``engines`` needs the saved settings at construction time and ``service``
    imports ``engines``, so this duplicates the three-line resolution instead of
    closing the cycle.
    """
    root = os.environ.get('HEWN_STUDIO_DATA_DIR')
    if root:
        return Path(root).expanduser()
    try:
        from plugins.plugin_storage import plugin_data_dir as hermes_plugin_data_dir
        return Path(hermes_plugin_data_dir('hermes-music-studio'))
    except Exception:  # noqa: BLE001 - outside Hermes (tests, scripts) there is no store
        return None
