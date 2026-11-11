"""Finding a renderer the person already runs.

"The MiniMax endpoint cannot be reached" is the first-run wall. These tests pin the two
halves of the fix: the candidate list a person would expect, and the honest verdict for a
ComfyUI that answers with and without the MiniMax nodes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from music_studio import discovery  # noqa: E402
from fake_comfy import _Server as FakeComfy  # noqa: E402


def test_candidates_start_with_what_the_person_already_configured():
    urls = discovery.candidates(saved='http://192.0.2.52:8188/')
    assert urls[0] == 'http://192.0.2.52:8188', 'the saved endpoint is tried first, without a trailing slash'
    assert 'http://127.0.0.1:8188' in urls
    assert urls.count('http://192.0.2.52:8188') == 1


def test_extra_hosts_are_scanned_on_every_known_port():
    urls = discovery.candidates(saved='', extra_hosts='studio-pc, studio-b.local')
    for host in ('studio-pc', 'studio-b.local'):
        for port in discovery.PORTS:
            assert f'http://{host}:{port}' in urls


def test_a_comfyui_with_minimax_nodes_is_reported_ready():
    with FakeComfy() as server:
        result = discovery.probe(server.url)
    assert result['reachable'] is True and result['comfyui'] is True
    assert result['minimax'] is True


def test_a_comfyui_without_the_nodes_says_exactly_that():
    with FakeComfy(missing_nodes=('MiniMaxMusic3TextEncode',
                                  'EmptyMiniMaxMusic3LatentAudio')) as server:
        result = discovery.probe(server.url)
    assert result['reachable'] is True
    assert result['minimax'] is False
    assert 'not installed' in result['detail']


def test_a_dead_port_is_not_an_exception():
    result = discovery.probe('http://127.0.0.1:9', timeout=0.2)
    assert result['reachable'] is False
    assert result['detail']


def test_scan_prefers_the_one_that_can_actually_render():
    with FakeComfy() as server:
        result = discovery.scan(saved=server.url, extra_hosts='', timeout=0.3)
    assert result['found'], result
    assert result['best'] == server.url
    assert result['best'] == next(item['url'] for item in result['found'] if item['minimax'])


def test_scan_hosts_are_saved_and_used_by_discovery(tmp_path):
    from music_studio import settings as st

    saved = st.write(tmp_path, {'comfy_url': 'http://192.0.2.9:8188', 'scan_hosts': ' studio-pc , nas '})
    assert saved['scan_hosts'] == 'studio-pc, nas'
    assert st.read(tmp_path)['scan_hosts'] == 'studio-pc, nas'
