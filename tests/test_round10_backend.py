"""Round 10, backend lane: the verify page, the engine handover, and the ACE template.

Each case here failed on the code as it stood: the verify page asked about a field
REVamp's status never carries, Copy my engines claimed MiniMax on every saved
server, a subfolder weight name was lost on the way to the other computer, and the
shipped ACE-Step graph hardcoded the tempo, the key and the language.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import engine_catalog, verify  # noqa: E402
from music_studio.engines import RenderRequest  # noqa: E402
from music_studio.network import TargetStore  # noqa: E402
from music_studio.workflows import (WorkflowStore, find_placeholders,  # noqa: E402
                                    substitute_placeholders)

URL = 'http://192.0.2.9:8188'
ACE_URL = 'http://192.0.2.20:8001'


# -- A. the verify page reads REVamp's real status --------------------------

def revamp_row(monkeypatch, status):
    from music_studio import about

    monkeypatch.setattr(about, 'revamp_status', lambda: status)
    rows = [row for row in verify.checks() if 'REVamp' in row['name']]
    assert len(rows) == 1
    return rows[0]


def test_verify_says_revamp_is_running_when_it_is(monkeypatch):
    row = revamp_row(monkeypatch, {'installed': True, 'running': True, 'state': 'running'})
    assert row['ok'] is True
    assert row['detail'] == 'running'


def test_verify_says_revamp_is_installed_but_not_running(monkeypatch):
    """``revamp_status`` carries no ``available`` key: the page used to read a miss."""
    row = revamp_row(monkeypatch, {'installed': True, 'running': False, 'state': 'installed'})
    assert row['ok'] is True
    assert row['detail'].startswith('installed, not running')
    assert 'playlists' in row['detail']


def test_verify_says_revamp_is_not_installed(monkeypatch):
    row = revamp_row(monkeypatch, {'installed': False, 'running': False, 'state': 'absent'})
    assert row['ok'] is True
    assert row['detail'].startswith('not installed')
    assert 'nothing is broken' in row['detail']


# -- B. Copy my engines lists only the models a server has ------------------

def add(tmp_path, models, name='Studio box', url=URL, **kwargs):
    return engine_catalog.add_engine(tmp_path, name=name, url=url, models=models, **kwargs)


def exported(tmp_path):
    return json.loads(engine_catalog.export_engines(tmp_path)['text'])


def test_copy_my_engines_does_not_claim_minimax_on_an_ace_only_server(tmp_path):
    add(tmp_path, ['ace15_turbo'], name='ACE box')
    payload = exported(tmp_path)
    assert payload['engines'] == [{'name': 'ACE box', 'url': URL, 'models': ['ace15_turbo']}]


def test_copy_my_engines_keeps_minimax_when_the_server_was_added_for_it(tmp_path):
    add(tmp_path, ['minimax_m3'], name='MiniMax box')
    assert exported(tmp_path)['engines'][0]['models'] == ['minimax_m3']


def test_copy_my_engines_keeps_both_models_when_both_were_added(tmp_path):
    add(tmp_path, ['minimax_m3', 'ace15_turbo'], name='Both box')
    assert exported(tmp_path)['engines'][0]['models'] == ['ace15_turbo', 'minimax_m3']


def test_an_engine_saved_before_this_fix_still_exports_minimax(tmp_path):
    """No record of what an old target was added for: keep the old answer."""
    TargetStore(tmp_path).create('Old box', URL)
    payload = exported(tmp_path)
    assert payload['engines'] == [{'name': 'Old box', 'url': URL, 'models': ['minimax_m3']}]


def test_a_failed_add_leaves_no_model_record_behind(tmp_path, monkeypatch):
    """The target goes back out on a failed add; so must what it was added for."""
    from music_studio.workflows import WorkflowStore

    def boom(*args, **kwargs):
        raise OSError('disk full')

    monkeypatch.setattr(WorkflowStore, 'create', boom)
    with pytest.raises(OSError):
        add(tmp_path, ['ace15_turbo'], name='ACE box')
    assert TargetStore(tmp_path).list() == []
    assert engine_catalog._models_record(tmp_path) == {}

    monkeypatch.undo()
    add(tmp_path, ['ace15_turbo'], name='ACE box')
    assert exported(tmp_path)['engines'][0]['models'] == ['ace15_turbo']


def test_removing_an_engine_forgets_the_models_it_recorded(tmp_path):
    added = add(tmp_path, ['ace15_turbo'], name='ACE box')
    engine_catalog.remove_engine(tmp_path, added['target']['id'])
    assert exported(tmp_path)['engines'] == []
    add(tmp_path, ['ace15_turbo'], name='ACE box')
    assert exported(tmp_path)['engines'][0]['models'] == ['ace15_turbo']


RESOLVED = {'acestep_v1.5_turbo.safetensors': 'diffusion_models/acestep_v1.5_turbo.safetensors',
            'ace_1.5_vae.safetensors': 'vae/ace_1.5_vae.safetensors'}


def test_the_subfolder_weight_names_travel_and_come_back(tmp_path):
    old, new = tmp_path / 'old', tmp_path / 'new'
    add(old, ['ace15_turbo'], name='ACE box', resolved=RESOLVED)
    text = engine_catalog.export_engines(old)['text']
    payload = json.loads(text)
    assert payload['engines'][0]['resolved'] == RESOLVED
    for secret_word in ('key', 'token', 'secret', 'password'):
        assert secret_word not in text.lower()

    assert engine_catalog.import_engines(new, text) == {
        'added': ['ACE box'], 'skipped': [], 'errors': []}
    graph = WorkflowStore(new).all()[0]['workflow']
    assert graph['1']['inputs']['unet_name'] == RESOLVED['acestep_v1.5_turbo.safetensors']
    assert graph['3']['inputs']['vae_name'] == RESOLVED['ace_1.5_vae.safetensors']

    again = json.loads(engine_catalog.export_engines(new)['text'])
    assert again['engines'] == [{'name': 'ACE box', 'url': URL,
                                'models': ['ace15_turbo'], 'resolved': RESOLVED}]


def test_an_export_without_resolved_still_imports(tmp_path):
    """An export written before this fix has no subfolder spellings: bare names it is."""
    text = json.dumps({'music_studio_engines': 1, 'ace_server': None,
                       'engines': [{'name': 'Old box', 'url': URL,
                                    'models': ['ace15_turbo']}]})
    assert engine_catalog.import_engines(tmp_path, text)['added'] == ['Old box']
    graph = WorkflowStore(tmp_path).all()[0]['workflow']
    assert graph['1']['inputs']['unet_name'] == 'acestep_v1.5_turbo.safetensors'


# -- C. the ACE-Step template takes the draft's tempo, key and language -----

def request(**kw):
    fields = {'caption': 'warm folk', 'lyrics': 'la la la', 'duration_seconds': 42.5,
              'seed': 7, 'title': 'Kettle song'}
    fields.update(kw)
    return RenderRequest(**fields)


def ace_graph(req):
    return substitute_placeholders(engine_catalog.load_template('ace15_turbo'), req)


def test_the_ace_template_uses_the_drafts_tempo_key_and_language():
    inputs = ace_graph(request(bpm=132, keyscale='A minor', vocal_language='es'))['5']['inputs']
    assert inputs['bpm'] == 132
    assert inputs['keyscale'] == 'A minor'
    assert inputs['language'] == 'es'


def test_the_ace_template_defaults_when_the_draft_names_none():
    inputs = ace_graph(request())['5']['inputs']
    assert inputs['bpm'] == 110
    assert inputs['keyscale'] == 'C major'
    assert inputs['language'] == 'en'


def test_the_tempo_stays_a_json_number():
    graph = ace_graph(request(bpm=132, keyscale='A minor', vocal_language='es'))
    inputs = graph['5']['inputs']
    assert type(inputs['bpm']) is int
    assert type(inputs['language']) is str
    assert json.loads(json.dumps(graph))['5']['inputs']['bpm'] == 132


def test_the_shipped_template_declares_the_new_placeholders():
    assert set(find_placeholders(engine_catalog.load_template('ace15_turbo'))) == {
        'style', 'lyrics', 'duration', 'seed', 'bpm', 'keyscale', 'vocal_language'}


def test_a_workflow_without_the_new_placeholders_still_renders():
    graph = {
        '1': {'class_type': 'TextEncode',
              'inputs': {'style': '{{style}}', 'lyrics': '{{lyrics}}', 'bpm': 90}},
        '2': {'class_type': 'SaveAudio', 'inputs': {'audio': ['1', 0]}},
    }
    out = substitute_placeholders(graph, request(bpm=132, keyscale='A minor'))
    assert out['1']['inputs'] == {'style': 'warm folk', 'lyrics': 'la la la', 'bpm': 90}


# -- D. the README describes the ACE-Step server engine as it is now ---------

def readme_text():
    return (ROOT / 'README.md').read_text(encoding='utf-8')


def test_the_readme_says_ace_remote_renders_on_a_server_you_add():
    text = readme_text()
    assert 'registered and health-probed but not deployed' not in text
    assert 'renders on a remote ACE-Step API server' in text


# ComfyUI's TextEncodeAceStepAudio1.5 takes language and keyscale as fixed lists and refuses the
# prompt otherwise (options read from a live server's /object_info on 11-17).
@pytest.mark.parametrize('raw,want', [('F#m', 'F# minor'), ('f# minor', 'F# minor'), ('Bb', 'Bb major'),
                                      ('A min', 'A minor'), ('C', 'C major'), ('nonsense', 'C major')])
def test_the_key_is_spelled_the_way_the_ace_node_lists_it(raw, want):
    assert ace_graph(request(keyscale=raw))['5']['inputs']['keyscale'] == want


@pytest.mark.parametrize('raw,want', [('auto', 'unknown'), ('ja', 'ja'), ('EN', 'en'), ('xx', 'unknown')])
def test_the_language_is_one_the_ace_node_lists(raw, want):
    assert ace_graph(request(vocal_language=raw))['5']['inputs']['language'] == want


def test_the_tempo_stays_inside_the_ace_node_range():
    assert ace_graph(request(bpm=400))['5']['inputs']['bpm'] == 300
