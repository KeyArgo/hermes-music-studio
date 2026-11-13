"""The library asks only for covers that exist (one 404 per song filled the console)."""
from music_studio import service


def test_projects_say_whether_they_have_a_cover(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    s = service.store()
    with_cover = s.create(caption='a', lyrics='x', duration_seconds=10)['project_id']
    without = s.create(caption='b', lyrics='y', duration_seconds=10)['project_id']
    (tmp_path / 'studio-covers').mkdir()
    (tmp_path / 'studio-covers' / (with_cover + '.cover')).write_bytes(b'image/png\n\x89PNG')
    status = service.status()
    listed = {p['project_id']: p for p in status['projects']}
    assert listed[with_cover]['has_cover'] is True
    assert listed[without]['has_cover'] is False
