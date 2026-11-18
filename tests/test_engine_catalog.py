"""Which music models a server can actually run: nodes *and* loader files.

Every case is a plain ``/object_info`` dict in memory, so nothing here opens a
socket. The one HTTP helper (`test_server`) is exercised with a monkeypatched
``setup._get`` that serves those dicts and the failure shapes a real address
produces.
"""
from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import engine_catalog  # noqa: E402
from music_studio.engine_catalog import (ACE_NODES, SIGNATURES,  # noqa: E402
                                         UNDETECTABLE_NOTE, detect, load_template,
                                         test_server as probe_server)
from music_studio.engines.contract import RenderRequest  # noqa: E402
from music_studio.engines.minimax_m3_comfy import (  # noqa: E402
    MiniMaxM3ComfyEngine, _combo_options)
from music_studio.workflows import find_placeholders  # noqa: E402

MINIMAX_UNET = 'diffusion_models/minimax_music3_dit_int8_convrot.safetensors'
MINIMAX_CLIP = 'text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors'
MINIMAX_VAE = 'vae/minimax_music3_dav.safetensors'
ACE_UNET = 'acestep_v1.5_turbo.safetensors'
ACE_CLIP_1 = 'qwen_0.6b_ace15.safetensors'
ACE_CLIP_2 = 'qwen_4b_ace15.safetensors'
ACE_VAE = 'ace_1.5_vae.safetensors'


def server_info(*, minimax_missing=(), ace_missing=(), ace_nodes=True,
                drop_unet_options=False, dual_types=('ace', 'minimax')) -> dict:
    """A ComfyUI /object_info for a server holding both model families."""
    unet = [MINIMAX_UNET, f'diffusion_models/{ACE_UNET}']
    clip = [MINIMAX_CLIP]
    vae = [MINIMAX_VAE, f'vae/{ACE_VAE}']
    clip1 = [ACE_CLIP_1]
    clip2 = [ACE_CLIP_2]
    for name in minimax_missing:
        unet = [item for item in unet if item != name]
        clip = [item for item in clip if item != name]
        vae = [item for item in vae if item != name]
    for name in ace_missing:
        unet = [item for item in unet if item.rsplit('/', 1)[-1] != name]
        vae = [item for item in vae if item.rsplit('/', 1)[-1] != name]
        clip1 = [item for item in clip1 if item != name]
        clip2 = [item for item in clip2 if item != name]

    info = {
        'MiniMaxMusic3TextEncode': {'input': {'required': {}}},
        'EmptyMiniMaxMusic3LatentAudio': {'input': {'required': {}}},
        'UNETLoader': {'input': {'required': {'unet_name': [unet, {}]}}},
        'CLIPLoader': {'input': {'required': {
            'clip_name': [clip, {}],
            'type': [['minimax', 'stable_diffusion'], {}]}}},
        'VAELoader': {'input': {'required': {'vae_name': [vae, {}]}}},
        'DualCLIPLoader': {'input': {'required': {
            'clip_name1': [clip1, {}],
            'clip_name2': [clip2, {}],
            'type': [list(dual_types), {}]}}},
    }
    if ace_nodes:
        info['TextEncodeAceStepAudio1.5'] = {'input': {'required': {}}}
        info['EmptyAceStep1.5LatentAudio'] = {'input': {'required': {}}}
    # Every node the shipped graphs use, not only the two model-specific ones:
    # a server missing SaveAudio/SaveAudioAdvanced can hold the weights and
    # still fail every render.
    for node in ('ModelSamplingAuraFlow', 'ConditioningZeroOut', 'KSampler',
                 'VAEDecodeAudio', 'SaveAudio', 'SaveAudioAdvanced'):
        info[node] = {'input': {'required': {}}}
    if drop_unet_options:
        info['UNETLoader'] = {'input': {'required': {}}}
    return info


def row(rows, model_id):
    return next(item for item in rows if item['id'] == model_id)


def test_ready_needs_nodes_and_the_named_files():
    rows = detect(server_info())
    assert row(rows, 'minimax_m3')['status'] == 'ready'
    assert row(rows, 'ace15_turbo')['status'] == 'ready'
    for item in rows:
        assert item['missing_nodes'] == [] and item['missing_files'] == []


def test_nodes_alone_are_not_ready_and_every_file_is_named():
    rows = detect(server_info(ace_missing=(ACE_UNET, ACE_CLIP_1, ACE_CLIP_2, ACE_VAE)))
    ace = row(rows, 'ace15_turbo')
    assert ace['status'] == 'missing_files'
    assert ace['missing_files'] == [ACE_UNET, ACE_CLIP_1, ACE_CLIP_2, ACE_VAE]


def test_a_missing_file_is_named_not_counted():
    rows = detect(server_info(ace_missing=(ACE_CLIP_2,)))
    ace = row(rows, 'ace15_turbo')
    assert ace['status'] == 'missing_files'
    assert ace['missing_files'] == [ACE_CLIP_2]


def test_a_missing_node_is_listed():
    info = server_info()
    for node in ('TextEncodeAceStepAudio1.5', 'EmptyAceStep1.5LatentAudio', 'DualCLIPLoader'):
        info.pop(node)
    ace = row(detect(info), 'ace15_turbo')
    assert ace['status'] == 'missing_nodes'
    assert ace['missing_nodes'] == ['TextEncodeAceStepAudio1.5',
                                    'EmptyAceStep1.5LatentAudio', 'DualCLIPLoader']


def test_an_absent_option_list_is_unknown_not_missing():
    rows = detect(server_info(drop_unet_options=True))
    assert row(rows, 'ace15_turbo')['status'] == 'unknown'
    assert row(rows, 'minimax_m3')['status'] == 'unknown'


def test_folder_prefixes_and_windows_backslashes_still_match():
    info = server_info()
    info['UNETLoader']['input']['required']['unet_name'][0] = [
        MINIMAX_UNET, 'C:\\ComfyUI\\models\\diffusion_models\\' + ACE_UNET]
    info['VAELoader']['input']['required']['vae_name'][0] = [
        MINIMAX_VAE, 'vae\\' + ACE_VAE]
    assert row(detect(info), 'ace15_turbo')['status'] == 'ready'


def test_a_loader_without_the_required_clip_type_is_not_ready():
    ace = row(detect(server_info(dual_types=('minimax',))), 'ace15_turbo')
    assert ace['status'] == 'missing_files'
    assert 'DualCLIPLoader.type=ace' in ace['missing_files']


# -- readiness covers the whole shipped graph, not just its model nodes ----

def test_a_missing_save_node_keeps_ace_not_ready():
    """Weights alone are not a render: SaveAudio must be registered too."""
    info = server_info()
    info.pop('SaveAudio')
    ace = row(detect(info), 'ace15_turbo')
    assert ace['status'] == 'missing_nodes'
    assert ace['missing_nodes'] == ['SaveAudio']


def test_a_missing_minimax_sampler_node_keeps_it_not_ready():
    info = server_info()
    info.pop('SaveAudioAdvanced')
    minimax = row(detect(info), 'minimax_m3')
    assert minimax['status'] == 'missing_nodes'
    assert minimax['missing_nodes'] == ['SaveAudioAdvanced']


def test_the_ace_signature_is_exactly_the_shipped_template():
    """Drift guard: a class_type added to the template must be required here."""
    classes = {node['class_type'] for node in load_template('ace15_turbo').values()}
    assert set(SIGNATURES['ace15_turbo']['nodes']) == classes


def test_the_minimax_signature_is_exactly_the_graph_the_engine_submits():
    engine = MiniMaxM3ComfyEngine(base_url='http://127.0.0.1:9')
    graph = engine.build_workflow(RenderRequest(caption='warm strings',
                                                duration_seconds=30, seed=7))
    assert set(SIGNATURES['minimax_m3']['nodes']) == {
        node['class_type'] for node in graph.values()}


# -- the exact loader option a nested name resolved to ---------------------

def test_a_nested_loader_option_is_reported_back_exactly():
    info = server_info()
    info['UNETLoader']['input']['required']['unet_name'][0] = [
        'diffusion_models\\acestep_v1.5_turbo.safetensors']
    info['DualCLIPLoader']['input']['required']['clip_name1'][0] = [
        'sub/clip/qwen_0.6b_ace15.safetensors']
    ace = row(detect(info), 'ace15_turbo')
    assert ace['status'] == 'ready'
    assert ace['resolved'] == {
        'acestep_v1.5_turbo.safetensors': 'diffusion_models\\acestep_v1.5_turbo.safetensors',
        'qwen_0.6b_ace15.safetensors': 'sub/clip/qwen_0.6b_ace15.safetensors',
        # Listed under the vae folder, so the bare template name would not match.
        'ace_1.5_vae.safetensors': 'vae/ace_1.5_vae.safetensors',
    }


def test_a_missing_file_is_not_resolved():
    ace = row(detect(server_info(ace_missing=(ACE_CLIP_2,))), 'ace15_turbo')
    assert 'qwen_4b_ace15.safetensors' not in ace['resolved']


# -- /object_info shapes that must not crash the detector ------------------

def test_a_loader_entry_that_is_not_a_dict_is_unreadable():
    info = server_info()
    info['UNETLoader'] = 'oops'
    rows = detect(info)
    assert row(rows, 'ace15_turbo')['status'] == 'unknown'
    assert row(rows, 'minimax_m3')['status'] == 'unknown'


def test_a_non_dict_input_section_is_unreadable():
    info = server_info()
    info['UNETLoader'] = {'input': 'x'}
    assert row(detect(info), 'ace15_turbo')['status'] == 'unknown'


def test_a_non_dict_required_section_is_unreadable():
    info = server_info()
    info['UNETLoader'] = {'input': {'required': ['unet_name']}}
    assert row(detect(info), 'ace15_turbo')['status'] == 'unknown'


def test_dict_options_without_a_name_are_skipped_not_stringified():
    malformed = {'N': {'input': {'required': {'field': [[{'content': 'x'}], {}]}}}}
    assert _combo_options(malformed, 'N', 'field') is None
    mixed = {'N': {'input': {'required': {'field': [[{'content': 'x'},
                                                     {'name': 'usable'}], {}]}}}}
    assert _combo_options(mixed, 'N', 'field') == ('usable',)


def test_a_loader_listing_nameless_dicts_is_unknown_not_missing_files():
    info = server_info()
    info['UNETLoader']['input']['required']['unet_name'] = [[{'content': 'x'}], {}]
    assert row(detect(info), 'ace15_turbo')['status'] == 'unknown'


def test_yue_and_stable_audio_are_declared_undetectable():
    assert UNDETECTABLE_NOTE == (
        'This studio cannot tell yet if YuE 2 or Stable Audio is installed.')
    assert set(SIGNATURES) == {'minimax_m3', 'ace15_turbo'}


def test_the_ace_template_is_a_valid_proven_graph():
    graph = load_template('ace15_turbo')
    found = find_placeholders(graph)
    assert set(found) == {'style', 'lyrics', 'duration', 'seed', 'bpm', 'keyscale',
                          'vocal_language'}
    assert len(found) == 7
    classes = {node['class_type'] for node in graph.values()}
    assert set(ACE_NODES) <= classes
    saves = [node for node in graph.values()
             if node['class_type'].startswith('SaveAudio')]
    assert len(saves) == 1
    assert saves[0]['inputs']['filename_prefix'] == 'music-studio'


def test_an_unknown_template_is_refused():
    with pytest.raises(ValueError, match='unknown music model'):
        load_template('yue2')


# -- test_server: the HTTP shape, with no socket opened -------------------

def http_error(code=404):
    return urllib.error.HTTPError('http://192.0.2.9:8188', code, 'no', {}, None)


def fake_get(responses):
    def _get(url, headers=None, timeout=None):
        for suffix, value in responses.items():
            if url.endswith(suffix):
                if isinstance(value, Exception):
                    raise value
                if isinstance(value, bytes):
                    return value
                return json.dumps(value).encode()
        raise AssertionError(f'unexpected request: {url}')
    return _get


def test_comfyui_message_is_one_plain_sentence_per_model(monkeypatch):
    monkeypatch.setattr(engine_catalog.setup, '_get', fake_get({
        '/system_stats': {'system': {'comfyui_version': '0.3.40'}},
        '/object_info': server_info(),
    }))
    result = probe_server('comfyui', 'http://192.0.2.9:8188/')
    assert result['ok'] is True
    assert result['kind'] == 'comfyui'
    assert result['url'] == 'http://192.0.2.9:8188'
    assert result['message'] == ('This is ComfyUI. MiniMax Music 3 is ready. '
                                 'ACE-Step 1.5 is ready.')
    assert {item['id'] for item in result['models']} == {'minimax_m3', 'ace15_turbo'}
    assert 'problem' not in result


def test_message_counts_missing_nodes(monkeypatch):
    info = server_info(ace_nodes=False)
    info.pop('DualCLIPLoader')
    monkeypatch.setattr(engine_catalog.setup, '_get', fake_get({
        '/system_stats': {'system': {}},
        '/object_info': info,
    }))
    result = probe_server('comfyui', 'http://192.0.2.9:8188')
    assert result['message'] == ('This is ComfyUI. MiniMax Music 3 is ready. '
                                 'ACE-Step 1.5 is missing 3 nodes.')


def test_a_wrong_address_is_a_plain_problem_not_an_exception():
    result = probe_server('comfyui', '192.168.1.50:8188')
    assert result['ok'] is False
    assert result['problem'] == 'bad_address'
    assert 'http://' in result['message']


def test_nothing_listening_hints_firewall_and_listen(monkeypatch):
    monkeypatch.setattr(engine_catalog.setup, '_get', fake_get({
        '/system_stats': urllib.error.URLError(ConnectionRefusedError()),
    }))
    result = probe_server('comfyui', 'http://192.0.2.9:8188')
    assert result['ok'] is False
    assert result['problem'] == 'no_answer'
    assert 'firewall' in result['message']
    assert '--listen' in result['message']


def test_an_answer_without_a_node_list_is_not_comfy(monkeypatch):
    monkeypatch.setattr(engine_catalog.setup, '_get', fake_get({
        '/system_stats': {'system': {}},
        '/object_info': http_error(404),
    }))
    result = probe_server('comfyui', 'http://192.0.2.9:8188')
    assert result['ok'] is False
    assert result['problem'] == 'not_comfy'


def test_an_ace_health_json_is_the_protocol_check(monkeypatch):
    monkeypatch.setattr(engine_catalog.setup, '_get', fake_get({
        '/health': {'data': {'status': 'ok', 'loaded_model': 'ace-step'}},
    }))
    result = probe_server('ace_server', 'http://192.0.2.9:8001')
    assert result['ok'] is True
    assert result['kind'] == 'ace_server'
    assert result['message'].startswith('This is an ACE-Step server.')
    assert result['models'] == []


def test_an_http_error_on_health_is_not_an_ace_server(monkeypatch):
    monkeypatch.setattr(engine_catalog.setup, '_get', fake_get({'/health': http_error(404)}))
    result = probe_server('ace_server', 'http://192.0.2.9:8001')
    assert result['ok'] is False
    assert result['problem'] == 'not_ace'


def test_hosted_keeps_its_own_form():
    result = probe_server('hosted', 'https://example.invalid')
    assert result['ok'] is False
    assert result['problem'] == 'use_hosted_form'


def test_an_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match='unknown engine kind'):
        probe_server('lmstudio', 'http://192.0.2.9:1234')


# -- the address rule, and the shapes a real server can answer with --------

def test_a_link_local_address_is_refused_before_any_request(monkeypatch):
    """The same rule add_engine uses: never probe the cloud metadata range."""
    def refuse(*args, **kwargs):
        raise AssertionError('a refused address must not be probed')

    monkeypatch.setattr(engine_catalog.setup, '_get', refuse)
    result = probe_server('comfyui', 'http://169.254.169.254:8188')
    assert result['ok'] is False
    assert result['problem'] == 'bad_address'
    assert 'link-local' in result['message']


def test_a_body_that_is_not_json_is_not_comfy(monkeypatch):
    """It answered, so it is not a firewall problem: it is the wrong service."""
    monkeypatch.setattr(engine_catalog.setup, '_get', fake_get({
        '/system_stats': b'<html>hello</html>',
    }))
    result = probe_server('comfyui', 'http://192.0.2.9:8188')
    assert result['ok'] is False
    assert result['problem'] == 'not_comfy'


def test_a_system_stats_without_a_system_object_is_not_comfy(monkeypatch):
    monkeypatch.setattr(engine_catalog.setup, '_get', fake_get({
        '/system_stats': {'system': 'x'},
    }))
    result = probe_server('comfyui', 'http://192.0.2.9:8188')
    assert result['ok'] is False
    assert result['problem'] == 'not_comfy'


def test_the_node_list_gets_a_long_timeout(monkeypatch):
    """A big install answers /object_info slowly; 8 s called a good server bad."""
    calls = []

    def _get(url, headers=None, timeout=None):
        calls.append((url, timeout))
        if url.endswith('/system_stats'):
            return json.dumps({'system': {}}).encode()
        return json.dumps(server_info()).encode()

    monkeypatch.setattr(engine_catalog.setup, '_get', _get)
    assert probe_server('comfyui', 'http://192.0.2.9:8188')['ok'] is True
    object_info_timeout = next(timeout for url, timeout in calls
                               if url.endswith('/object_info'))
    assert object_info_timeout == 30


def test_a_connection_error_on_the_node_list_is_no_answer(monkeypatch):
    monkeypatch.setattr(engine_catalog.setup, '_get', fake_get({
        '/system_stats': {'system': {}},
        '/object_info': urllib.error.URLError(ConnectionResetError()),
    }))
    result = probe_server('comfyui', 'http://192.0.2.9:8188')
    assert result['ok'] is False
    assert result['problem'] == 'no_answer'
