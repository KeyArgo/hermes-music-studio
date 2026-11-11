"""Getting a finished take out of the studio and into a normal music folder.

The export is one-way and never destructive: the library keeps its copy, the file lands with a
readable name, and a second export of the same song does not overwrite the first.
"""
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_studio import export


class Jobs:
    def __init__(self, payload=b'RIFF-fake-wave'):
        self.payload = payload

    def open_audio(self, project_id, take_id, edit_id=None):
        return io.BytesIO(self.payload)


def project(state='succeeded', take_id='22222222-2222-2222-2222-222222222222'):
    return {'project_id': '11111111-1111-1111-1111-111111111111', 'title': 'Cursor Blink',
            'takes': [{'take_id': take_id, 'state': state}]}


def test_a_take_lands_in_the_folder_with_a_readable_name(tmp_path):
    result = export.export_take(Jobs(), project(), '22222222-2222-2222-2222-222222222222',
                                folder=str(tmp_path))
    saved = Path(result['path'])
    assert saved.parent == tmp_path
    assert saved.name == 'Cursor Blink.wav'
    assert saved.read_bytes() == b'RIFF-fake-wave'
    assert result['bytes'] == len(b'RIFF-fake-wave')


def test_a_second_export_does_not_overwrite_the_first(tmp_path):
    first = export.export_take(Jobs(), project(), '22222222-2222-2222-2222-222222222222', folder=str(tmp_path))
    second = export.export_take(Jobs(), project(), '22222222-2222-2222-2222-222222222222', folder=str(tmp_path))
    assert Path(first['path']).name == 'Cursor Blink.wav'
    assert Path(second['path']).name == 'Cursor Blink (2).wav'
    assert Path(first['path']).exists()


def test_an_unfinished_take_is_refused(tmp_path):
    with pytest.raises(ValueError, match='finished'):
        export.export_take(Jobs(), project(state='running'), '22222222-2222-2222-2222-222222222222',
                           folder=str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_a_relative_folder_is_refused():
    with pytest.raises(ValueError, match='absolute'):
        export.target_folder('Music')


def test_a_name_with_a_slash_cannot_escape_the_folder(tmp_path):
    evil = {'project_id': '11111111-1111-1111-1111-111111111111', 'title': '../../etc/passwd',
            'takes': [{'take_id': '22222222-2222-2222-2222-222222222222', 'state': 'succeeded'}]}
    result = export.export_take(Jobs(), evil, '22222222-2222-2222-2222-222222222222', folder=str(tmp_path))
    saved = Path(result['path'])
    assert saved.parent == tmp_path
    assert '/' not in saved.name and '..' not in saved.name


def test_the_default_folder_is_under_home_music(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    assert export.target_folder('') == tmp_path / 'Music' / 'Hermes Music Studio'


def test_exporting_the_same_take_twice_reuses_the_first_file(tmp_path, monkeypatch):
    """Adding a song to a second playlist must not fill the folder with copies."""
    from music_studio import service

    monkeypatch.setattr(service, 'default_data_dir', lambda plugin_id: tmp_path)
    monkeypatch.setattr('music_studio.export.export_take', export.export_take)
    monkeypatch.setattr(service, 'store', lambda: type('S', (), {'get': lambda self, pid: project()})())
    monkeypatch.setattr(service, 'Jobs', lambda store: Jobs())

    first = service.export_take('11111111-1111-1111-1111-111111111111',
                                '22222222-2222-2222-2222-222222222222', folder=str(tmp_path))
    second = service.export_take('11111111-1111-1111-1111-111111111111',
                                 '22222222-2222-2222-2222-222222222222', folder=str(tmp_path))
    assert first['path'] == second['path']
    assert second.get('reused') is True
    assert len([p for p in tmp_path.iterdir() if p.suffix == '.wav']) == 1


def test_a_second_export_to_a_different_folder_is_a_new_copy(tmp_path):
    """Asking for another folder is a request for another file, not a cache hit."""
    from music_studio import export

    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()

    class Jobs:
        def __init__(self): self.payload = b'RIFF' + b'\x00' * 100
        def open_audio(self, *_args, **_kwargs):
            import io
            return io.BytesIO(self.payload)

    project = {'project_id': 'p', 'title': 'Song', 'takes': [
        {'take_id': 't', 'state': 'succeeded'}]}

    a = export.export_take(Jobs(), project, 't', folder=str(first))
    b = export.export_take(Jobs(), project, 't', folder=str(second))
    assert a['path'] != b['path']
    assert b['path'].startswith(str(second))


def test_a_failed_copy_leaves_no_half_written_file(tmp_path, monkeypatch):
    from music_studio import export

    class Broken:
        def open_audio(self, *_args, **_kwargs):
            raise OSError('source vanished')

    project = {'project_id': 'p', 'title': 'Song', 'takes': [
        {'take_id': 't', 'state': 'succeeded'}]}
    try:
        export.export_take(Broken(), project, 't', folder=str(tmp_path))
    except export.ExportError:
        pass
    else:  # pragma: no cover - the failure must not be silent
        raise AssertionError('a failed export must raise ExportError')
    assert list(tmp_path.glob('*.wav')) == []
    assert list(tmp_path.glob('*.part')) == []
