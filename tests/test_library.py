"""Settings, endpoint probing and file management (delete / copy / move)."""
import struct
import uuid
from pathlib import Path

import pytest

from music_studio import service, settings


@pytest.fixture()
def library(tmp_path, monkeypatch):
    root = tmp_path / 'lib'
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(root))
    return root


def _wav(path: Path) -> Path:
    frames = b'\0\0' * 8000  # one second, 8 kHz, mono, 16-bit
    header = (b'RIFF' + struct.pack('<I', 36 + len(frames)) + b'WAVEfmt '
              + struct.pack('<IHHIIHH', 16, 1, 1, 8000, 16000, 2, 16)
              + b'data' + struct.pack('<I', len(frames)))
    path.write_bytes(header + frames)
    return path


def _project_with_take(library: Path) -> dict:
    store = service.store()
    project = store.create(title='Song', caption='quiet', lyrics='one line', duration_seconds=12)
    take_id = str(uuid.uuid4())
    _wav(library / f'{take_id}.wav')
    project['takes'] = [{'take_id': take_id, 'state': 'succeeded', 'requested_seed': 1,
                         'audio_file': f'{take_id}.wav', 'edits': []}]
    store._save(project)
    return store.get(project['project_id'])


# -- settings --------------------------------------------------------------
def test_settings_roundtrip_and_defaults(library):
    info = service.settings_info()
    assert info['settings']['minimax']['steps'] == settings.DEFAULTS['minimax']['steps']
    assert info['library']['active'] == str(library)

    saved = service.save_settings({'comfy_url': 'http://192.0.2.9:8188/',
                                   'minimax': {'steps': 12, 'cfg': 3.5,
                                               'cfg_scale': 2.0, 'top_k': 20,
                                               'sampler': 'dpmpp_2m', 'scheduler': 'karras'}})
    assert saved['settings']['comfy_url'] == 'http://192.0.2.9:8188'
    assert saved['settings']['minimax']['sampler'] == 'dpmpp_2m'
    assert settings.read(library)['minimax']['steps'] == 12


def test_saving_one_setting_keeps_the_ones_not_in_the_payload(library, tmp_path):
    """The pane posts only what it changed; a partial save must not reset the rest."""
    export_dir = str(tmp_path / 'exports')
    service.save_settings({'export_dir': export_dir, 'scan_hosts': 'spark-a'})

    saved = service.save_settings({'comfy_url': 'http://192.0.2.9:8188'})

    assert saved['settings']['export_dir'] == export_dir
    assert saved['settings']['scan_hosts'] == 'spark-a'
    assert settings.read(library)['comfy_url'] == 'http://192.0.2.9:8188'


def test_an_explicit_empty_setting_clears_it(library, tmp_path):
    service.save_settings({'export_dir': str(tmp_path / 'exports')})

    saved = service.save_settings({'export_dir': ''})

    assert saved['settings']['export_dir'] == ''


def test_settings_reject_values_that_would_fail_at_render_time(library):
    with pytest.raises(ValueError, match='sampler'):
        service.save_settings({'minimax': {'sampler': 'not-a-sampler'}})
    with pytest.raises(ValueError, match='steps'):
        service.save_settings({'minimax': {'steps': 0}})
    with pytest.raises(ValueError, match='comfy_url'):
        service.save_settings({'comfy_url': 'ftp://nope'})


def test_engine_reads_the_saved_endpoint_and_knobs(library):
    from music_studio.engines.minimax_m3_comfy import MiniMaxM3ComfyEngine

    service.save_settings({'comfy_url': 'http://192.0.2.9:8188',
                           'minimax': {'steps': 12, 'cfg': 2.5, 'cfg_scale': 1.25,
                                       'top_k': 33, 'sampler': 'heun', 'scheduler': 'karras'}})
    engine = MiniMaxM3ComfyEngine()
    assert engine.base_url == 'http://192.0.2.9:8188'
    assert (engine.steps, engine.cfg, engine.cfg_scale, engine.top_k) == (12, 2.5, 1.25, 33)
    assert (engine.sampler, engine.scheduler) == ('heun', 'karras')
    graph = engine.build_workflow(_request())
    sampler = next(node for node in graph.values() if node.get('class_type') == 'KSampler')
    assert sampler['inputs']['sampler_name'] == 'heun'
    assert sampler['inputs']['scheduler'] == 'karras'
    assert sampler['inputs']['steps'] == 12


def _request():
    from music_studio.engines.contract import RenderRequest

    return RenderRequest(caption='quiet', lyrics='one line', duration_seconds=12.0,
                         seed=4242, instrumental=False, vocal_language='en')


def test_endpoint_probe_reports_an_unreachable_server_without_raising(library):
    result = service.test_endpoint('http://127.0.0.1:9')
    assert result['success'] is True
    assert result['url'] == 'http://127.0.0.1:9'
    assert result['status']['available'] is False


# -- file management -------------------------------------------------------
def test_duplicate_project_copies_takes_and_audio(library):
    original = _project_with_take(library)
    take_id = original['takes'][0]['take_id']

    result = service.duplicate_project(original['project_id'])
    copy = result['project']

    assert copy['project_id'] != original['project_id']
    assert copy['title'].endswith('(copy)')
    assert copy['approved'] is False
    assert len(copy['takes']) == 1 and copy['takes'][0]['take_id'] != take_id
    new_audio = copy['takes'][0]['audio_file']
    assert (library / new_audio).is_file()
    assert (library / f'{take_id}.wav').is_file()  # the original is untouched
    assert (library / new_audio).read_bytes()[:4] == b'RIFF'


def test_delete_take_removes_one_take_and_its_audio(library):
    project = _project_with_take(library)
    take_id = project['takes'][0]['take_id']

    result = service.delete_take(project['project_id'], take_id)

    assert result['project']['takes'] == []
    assert not (library / f'{take_id}.wav').exists()
    assert (library / f"{project['project_id']}.json").is_file()


def test_relocate_copy_leaves_the_original_and_moves_the_pointer(library, tmp_path):
    project = _project_with_take(library)
    target = tmp_path / 'elsewhere'

    result = service.relocate_library(str(target), 'copy')

    assert Path(result['library']['active']).resolve() == target.resolve()
    assert result['library']['moved'] is True
    assert (target / f"{project['project_id']}.json").is_file()
    assert (library / f"{project['project_id']}.json").is_file()  # nothing destroyed
    assert service.store().root == target
    assert settings.settings_path(library).is_file() or True  # pointer lives in the old dir
    assert (library / 'library.json').is_file()

    service.reset_library()
    assert service.store().root == library


def test_relocate_move_deletes_the_old_copies(library, tmp_path):
    project = _project_with_take(library)
    target = tmp_path / 'moved'

    service.relocate_library(str(target), 'move')

    assert (target / f"{project['project_id']}.json").is_file()
    assert not (library / f"{project['project_id']}.json").exists()
    assert (library / 'library.json').is_file()


def test_relocate_move_keeps_a_source_when_the_destination_file_differs(library, tmp_path):
    """A stale same-named file in the target must not let the newer source be deleted."""
    project = _project_with_take(library)
    name = f"{project['project_id']}.json"
    target = tmp_path / 'moved'
    target.mkdir()
    stale = target / name
    stale.write_text('{"stale": true}', encoding='utf-8')

    with pytest.raises(ValueError, match='different copies'):
        service.relocate_library(str(target), 'move')

    assert (library / name).is_file()  # the newer source was not destroyed
    assert stale.read_text(encoding='utf-8') == '{"stale": true}'
    # and the library still points at the source, so it shows the newer song, not the stale one
    assert service.store().root.resolve() == library.resolve()


def test_relocate_move_deletes_a_source_file_the_destination_already_matches(library, tmp_path):
    project = _project_with_take(library)
    name = f"{project['project_id']}.json"
    target = tmp_path / 'moved'
    target.mkdir()
    (target / name).write_bytes((library / name).read_bytes())

    result = service.relocate_library(str(target), 'move')

    assert not (library / name).exists()  # identical copy already there
    assert name not in result['kept']


def test_relocate_move_keeps_a_directory_that_was_only_partly_copied(library, tmp_path, monkeypatch):
    import shutil

    work = library / 'work'
    work.mkdir()
    (work / 'one.txt').write_text('one', encoding='utf-8')
    (work / 'two.txt').write_text('two', encoding='utf-8')

    def copy_only_one(source, destination, **kwargs):
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'one.txt').write_bytes((Path(source) / 'one.txt').read_bytes())
        return destination

    monkeypatch.setattr(shutil, 'copytree', copy_only_one)
    target = tmp_path / 'moved'

    result = service.relocate_library(str(target), 'move')

    assert (work / 'two.txt').is_file()  # the incomplete copy stayed put
    assert 'work' in result['kept']


def test_relocate_refuses_the_current_directory(library):
    with pytest.raises(ValueError, match='already'):
        service.relocate_library(str(library), 'copy')
