"""Take a finished song out of the studio and into a normal music folder.

The library keeps its own copies; this is the one-way door the owner asked for — pick a
folder (``~/Music/Hermes Music Studio`` by default) and the take lands there as a plain
``.wav`` file with a readable name, so any player, phone sync or REVamp scan can find it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


class ExportError(RuntimeError):
    """The take could not be written; the message is meant for a person."""

_UNSAFE = re.compile(r'[^A-Za-z0-9 \-_.()\[\]]+')
# Windows still reserves these device names in every directory, with or
# without an extension: "CON" and "con.wav" both name the console, not a file.
_RESERVED_NAMES = frozenset(
    ['CON', 'PRN', 'AUX', 'NUL']
    + [f'COM{n}' for n in range(1, 10)]
    + [f'LPT{n}' for n in range(1, 10)])


def safe_name(text: str, fallback: str = 'take') -> str:
    """A file name a person would recognise, with nothing that breaks a path."""
    name = _UNSAFE.sub(' ', str(text or ''))
    # A name is a file name, not a path: no dot runs that could read as ``..``.
    name = re.sub(r'\.{2,}', '.', name)
    name = re.sub(r'\s+', ' ', name).strip().strip('.')
    name = name[:120] or fallback
    stem, dot, rest = name.partition('.')
    if stem.upper() in _RESERVED_NAMES:
        name = stem + '_' + dot + rest
    return name[:120] or fallback


def default_folder() -> Path:
    """Where an export goes when nobody has chosen a folder: the owner's own music library."""
    return Path.home() / 'Music' / 'Hermes Music Studio'


def target_folder(raw: str | None, configured: str | None = None) -> Path:
    """The folder to write into, confined to somewhere the owner already keeps music.

    Review item 5: the tool wrote to any absolute path an agent named, which is a write primitive with
    the agent's imagination as the input. Two things are allowed now — the folder configured in Settings
    (the owner's choice), or anything inside ``~/Music`` (the place music lives) — and everything else is
    refused. Symlinks are resolved first, so a link inside the music folder cannot point the write
    somewhere else.
    """
    if configured:
        root = Path(configured).expanduser()
    else:
        root = Path.home() / 'Music'
    root = root.resolve()
    chosen = (raw or '').strip()
    path = Path(chosen).expanduser() if chosen else (Path(configured).expanduser() if configured else default_folder())
    if not path.is_absolute():
        raise ValueError('folder must be an absolute path')
    path = path.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f'export folder must be inside {root}')
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError('folder is not a directory')
    return path


def free_path(folder: Path, stem: str, suffix: str = '.wav') -> Path:
    """``Song.wav``, then ``Song (2).wav``; an export never overwrites the person's file."""
    candidate = folder / (stem + suffix)
    index = 2
    while candidate.exists():
        candidate = folder / f'{stem} ({index}){suffix}'
        index += 1
    return candidate


def export_take(jobs, project: dict, take_id: str, folder: str | None = None,
                title: str | None = None, configured_folder: str | None = None) -> dict:
    """Write one finished take into ``folder`` and report where it went.

    ``configured_folder`` is the owner's Settings value; when it is set, an explicit ``folder`` must be
    inside it (see ``target_folder``).
    """
    take = next((row for row in project.get('takes', []) if row.get('take_id') == take_id), None)
    if take is None:
        raise ValueError('take not found')
    if take.get('state') != 'succeeded':
        raise ValueError('that take has not finished rendering')
    destination = target_folder(folder, configured=configured_folder)
    stem = safe_name(title or project.get('title') or 'Music Studio take')
    path = free_path(destination, stem)
    # Write beside the destination and rename when complete: a copy that fails halfway must not
    # leave a truncated .wav that a player, a phone sync or a REVamp scan will happily index.
    partial = path.with_name(path.name + '.part')
    try:
        with jobs.open_audio(project['project_id'], take_id) as source, open(partial, 'wb') as target:
            while chunk := source.read(256 * 1024):
                target.write(chunk)
        os.replace(partial, path)
    except OSError:
        try:
            partial.unlink()
        except OSError:
            pass
        raise ExportError(f'could not write the take into {destination}') from None
    except BaseException:
        try:
            partial.unlink()
        except OSError:
            pass
        raise
    return {'success': True, 'path': str(path), 'folder': str(destination),
            'title': stem, 'bytes': path.stat().st_size}
