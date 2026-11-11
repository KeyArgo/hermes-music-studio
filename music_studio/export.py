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


def safe_name(text: str, fallback: str = 'take') -> str:
    """A file name a person would recognise, with nothing that breaks a path."""
    name = _UNSAFE.sub(' ', str(text or ''))
    # A name is a file name, not a path: no dot runs that could read as ``..``.
    name = re.sub(r'\.{2,}', '.', name)
    name = re.sub(r'\s+', ' ', name).strip().strip('.')
    return name[:80] or fallback


def target_folder(raw: str | None) -> Path:
    """The folder to write into: the one given, else ``~/Music/Hermes Music Studio``."""
    chosen = (raw or '').strip()
    path = Path(chosen).expanduser() if chosen else Path.home() / 'Music' / 'Hermes Music Studio'
    if not path.is_absolute():
        raise ValueError('folder must be an absolute path')
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
                title: str | None = None) -> dict:
    """Write one finished take into ``folder`` and report where it went."""
    take = next((row for row in project.get('takes', []) if row.get('take_id') == take_id), None)
    if take is None:
        raise ValueError('take not found')
    if take.get('state') != 'succeeded':
        raise ValueError('that take has not finished rendering')
    destination = target_folder(folder)
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
