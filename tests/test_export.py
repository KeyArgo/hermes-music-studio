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


@pytest.fixture(autouse=True)
def music_home(tmp_path, monkeypatch):
    """Exports are confined to the owner's music folder (review item 5), so give each test one."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    (tmp_path / 'Music').mkdir()
    return tmp_path / 'Music'


def project(state='succeeded', take_id='22222222-2222-2222-2222-222222222222'):
    return {'project_id': '11111111-1111-1111-1111-111111111111', 'title': 'Cursor Blink',
            'takes': [{'take_id': take_id, 'state': state}]}


def test_a_take_lands_in_the_folder_with_a_readable_name(tmp_path, music_home):
    result = export.export_take(Jobs(), project(), '22222222-2222-2222-2222-222222222222',
                                folder=str(music_home))
    saved = Path(result['path'])
    assert saved.parent == music_home
    assert saved.name == 'Cursor Blink.wav'
    assert saved.read_bytes() == b'RIFF-fake-wave'
    assert result['bytes'] == len(b'RIFF-fake-wave')


def test_a_second_export_does_not_overwrite_the_first(tmp_path, music_home):
    first = export.export_take(Jobs(), project(), '22222222-2222-2222-2222-222222222222', folder=str(music_home))
    second = export.export_take(Jobs(), project(), '22222222-2222-2222-2222-222222222222', folder=str(music_home))
    assert Path(first['path']).name == 'Cursor Blink.wav'
    assert Path(second['path']).name == 'Cursor Blink (2).wav'
    assert Path(first['path']).exists()


def test_an_unfinished_take_is_refused(tmp_path, music_home):
    with pytest.raises(ValueError, match='finished'):
        export.export_take(Jobs(), project(state='running'), '22222222-2222-2222-2222-222222222222',
                           folder=str(music_home))
    assert list(music_home.iterdir()) == []


def test_a_relative_folder_is_refused():
    with pytest.raises(ValueError, match='absolute'):
        export.target_folder('Music')


def test_a_name_with_a_slash_cannot_escape_the_folder(tmp_path, music_home):
    evil = {'project_id': '11111111-1111-1111-1111-111111111111', 'title': '../../etc/passwd',
            'takes': [{'take_id': '22222222-2222-2222-2222-222222222222', 'state': 'succeeded'}]}
    result = export.export_take(Jobs(), evil, '22222222-2222-2222-2222-222222222222', folder=str(music_home))
    saved = Path(result['path'])
    assert saved.parent == music_home
    assert '/' not in saved.name and '..' not in saved.name


def test_the_default_folder_is_under_home_music(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))  # Windows reads this, not HOME
    assert export.target_folder('') == tmp_path / 'Music' / 'Hermes Music Studio'


def test_exporting_the_same_take_twice_reuses_the_first_file(tmp_path, music_home, monkeypatch):
    """Adding a song to a second playlist must not fill the folder with copies."""
    from music_studio import service

    monkeypatch.setattr(service, 'default_data_dir', lambda plugin_id: tmp_path)
    monkeypatch.setattr('music_studio.export.export_take', export.export_take)
    monkeypatch.setattr(service, 'store', lambda: type('S', (), {'get': lambda self, pid: project()})())
    monkeypatch.setattr(service, 'Jobs', lambda store: Jobs())

    first = service.export_take('11111111-1111-1111-1111-111111111111',
                                '22222222-2222-2222-2222-222222222222', folder=str(music_home))
    second = service.export_take('11111111-1111-1111-1111-111111111111',
                                 '22222222-2222-2222-2222-222222222222', folder=str(music_home))
    assert first['path'] == second['path']
    assert second.get('reused') is True
    assert len([p for p in music_home.iterdir() if p.suffix == '.wav']) == 1


def test_a_second_export_to_a_different_folder_is_a_new_copy(tmp_path, music_home):
    """Asking for another folder is a request for another file, not a cache hit."""
    from music_studio import export

    first = music_home / 'first'
    second = music_home / 'second'
    first.mkdir()
    second.mkdir()

    class Jobs:
        def __init__(self): self.payload = b'RIFF' + b'\x00' * 100
        def open_audio(self, *_args, **_kwargs):
            import io
            return io.BytesIO(self.payload)

    project = {'project_id': 'p', 'title': 'Song', 'takes': [
        {'take_id': 't', 'state': 'succeeded'}]}

    a = export.export_take(Jobs(), project, 't', folder=str(music_home / 'first'))
    b = export.export_take(Jobs(), project, 't', folder=str(music_home / 'second'))
    assert a['path'] != b['path']
    assert b['path'].startswith(str(second))


def test_a_changed_export_folder_is_not_reused_from_the_ledger(tmp_path, music_home, monkeypatch):
    """The ledger remembers a path, not the folder that is configured now."""
    from music_studio import service, settings

    data = tmp_path / 'data'
    data.mkdir()
    first = music_home / 'first'
    second = music_home / 'second'
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(service, 'default_data_dir', lambda plugin_id: data)
    monkeypatch.setattr('music_studio.export.export_take', export.export_take)
    monkeypatch.setattr(service, 'store', lambda: type(
        'S', (), {'root': data, 'get': lambda self, pid: project()})())
    monkeypatch.setattr(service, 'Jobs', lambda store: Jobs())

    settings.write(data, {'export_dir': str(first)})
    one = service.export_take('11111111-1111-1111-1111-111111111111',
                              '22222222-2222-2222-2222-222222222222')
    assert Path(one['path']).parent == first

    settings.write(data, {'export_dir': str(second)})
    two = service.export_take('11111111-1111-1111-1111-111111111111',
                              '22222222-2222-2222-2222-222222222222')
    assert Path(two['path']).parent == second
    assert not two.get('reused')


def test_export_does_not_write_through_a_part_symlink(tmp_path, music_home):
    victim = tmp_path / 'victim.bin'
    victim.write_bytes(b'do not touch')
    link = music_home / 'Cursor Blink.wav.part'
    try:
        link.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip('this filesystem cannot make symlinks')

    result = export.export_take(Jobs(), project(), '22222222-2222-2222-2222-222222222222',
                                folder=str(music_home))

    assert victim.read_bytes() == b'do not touch'
    assert Path(result['path']).read_bytes() == b'RIFF-fake-wave'


def test_an_export_never_overwrites_a_file_that_claimed_the_name(tmp_path, music_home, monkeypatch):
    """Another export can take the free name after it is chosen; this one must step aside."""
    target = music_home / 'Cursor Blink.wav'
    target.write_bytes(b'earlier export')
    monkeypatch.setattr(export, 'free_path',
                        lambda folder, stem, suffix='.wav': target)

    result = export.export_take(Jobs(), project(), '22222222-2222-2222-2222-222222222222',
                                folder=str(music_home))

    assert target.read_bytes() == b'earlier export'
    assert Path(result['path']) != target
    assert Path(result['path']).name == 'Cursor Blink (2).wav'


def test_a_failed_copy_leaves_no_half_written_file(tmp_path, music_home, monkeypatch):
    from music_studio import export

    class Broken:
        def open_audio(self, *_args, **_kwargs):
            raise OSError('source vanished')

    project = {'project_id': 'p', 'title': 'Song', 'takes': [
        {'take_id': 't', 'state': 'succeeded'}]}
    try:
        export.export_take(Broken(), project, 't', folder=str(music_home))
    except export.ExportError:
        pass
    else:  # pragma: no cover - the failure must not be silent
        raise AssertionError('a failed export must raise ExportError')
    assert list(music_home.glob('*.wav')) == []
    assert list(music_home.glob('*.part')) == []
