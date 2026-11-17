"""Round-9 hunt: the engines page and the local installer.

Each case here failed on the code as it stood: a 500 where the page shows a
sentence, a duplicate engine, a scan that stopped on one unusable host, and a
models folder the installer approved and then refused.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from music_studio import discovery, engine_catalog  # noqa: E402
from music_studio import local_install as install  # noqa: E402
from music_studio.network import TargetStore  # noqa: E402
from music_studio.workflows import WorkflowStore  # noqa: E402

BAD_EXPORT = 'That text is not a list of music engines.'
DEEP_TEXT = '[' * 20000 + ']' * 20000


# -- a pasted export the decoder cannot walk ---------------------------------

def test_a_pasted_export_nested_past_the_decoder_limit_is_a_plain_sentence(tmp_path):
    """``json.loads`` raises RecursionError, not ValueError, on a deep blob.

    A 40 KB paste of ``[[[[…`` is inside the body cap the routes enforce and
    nests past the decoder's limit, so it reached the page as a 500 with no
    sentence to show.
    """
    with pytest.raises(ValueError, match='not a list of music engines'):
        engine_catalog.import_engines(tmp_path, DEEP_TEXT)
    assert TargetStore(tmp_path).list() == []


@pytest.fixture
def client(monkeypatch, tmp_path):
    """The studio host around the real routes, with no stand-in modules."""
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    from standalone import app as host_app
    from dashboard.plugin_api import router
    from conftest import studio_client

    app = FastAPI()
    app.middleware('http')(host_app.same_origin)
    app.include_router(router, prefix='/api')
    return studio_client(app, base_url='http://127.0.0.1')


def test_the_import_route_answers_a_deep_blob_with_400(client):
    response = client.post('/api/studio/engines/import', json={'text': DEEP_TEXT})
    assert response.status_code == 400, response.text
    assert response.json() == {'success': False, 'error': BAD_EXPORT}


# -- one server, two spellings ---------------------------------------------

def test_the_same_address_in_another_host_case_is_the_same_engine(tmp_path):
    """A host name is case-insensitive, so importing it twice must not duplicate.

    ``validate_base_url`` lowercases the scheme and keeps the host as typed, so
    the comparison of saved addresses has to ignore that case itself.
    """
    engine_catalog.add_engine(tmp_path, name='Studio PC', url='http://studio-pc:8188',
                              models=['ace15_turbo'])
    text = json.dumps({'music_studio_engines': 1, 'ace_server': None,
                       'engines': [{'name': 'Studio PC', 'url': 'http://STUDIO-PC:8188',
                                    'models': ['ace15_turbo']}]})
    result = engine_catalog.import_engines(tmp_path, text)
    assert result == {'added': [], 'skipped': ['Studio PC'], 'errors': []}
    assert [target['url'] for target in TargetStore(tmp_path).list()] == ['http://studio-pc:8188']
    assert len(WorkflowStore(tmp_path).all()) == 1


def test_two_entries_that_are_one_server_are_added_once(tmp_path):
    """The same duplicate inside one pasted export: one target, one workflow."""
    text = json.dumps({'music_studio_engines': 1, 'ace_server': None, 'engines': [
        {'name': 'A', 'url': 'http://box:8188', 'models': ['ace15_turbo']},
        {'name': 'B', 'url': 'HTTP://Box:8188', 'models': ['ace15_turbo']},
    ]})
    result = engine_catalog.import_engines(tmp_path, text)
    assert result['added'] == ['A'] and result['skipped'] == ['B']
    assert len(TargetStore(tmp_path).list()) == 1
    assert len(WorkflowStore(tmp_path).all()) == 1


# -- an address that answers, but not with HTTP -----------------------------

@pytest.fixture
def other_protocol():
    """A port whose service greets you in its own protocol, as a mail server does.

    ``http.client`` raises BadStatusLine, which is an HTTPException and not an
    OSError: the probe used to let it out as a 500.
    """
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', 0))
    server.listen(5)

    def serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.sendall(b'220 mail.example.com ESMTP\r\n')
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()
    yield f'http://127.0.0.1:{server.getsockname()[1]}'
    server.close()


def test_a_server_that_answers_in_another_protocol_is_not_comfy(other_protocol):
    result = engine_catalog.test_server('comfyui', other_protocol)
    assert result['ok'] is False
    assert result['problem'] == 'not_comfy'


def test_the_same_answer_is_not_an_ace_server_either(other_protocol):
    result = engine_catalog.test_server('ace_server', other_protocol)
    assert result['ok'] is False
    assert result['problem'] == 'not_ace'


def test_an_address_a_request_cannot_carry_is_never_probed(monkeypatch):
    """A character nobody can see makes ``http.client`` refuse the request itself.

    A NUL or a DEL (a terminal copy) raised InvalidURL and a zero-width space (a
    web-page copy) raised UnicodeEncodeError, so the page got a 500 or a codec
    message. That is the address's fault, not the server's: the address card,
    and nothing on the wire.
    """
    def refuse(*args, **kwargs):
        raise AssertionError('an address no request can carry must not be probed')

    monkeypatch.setattr(engine_catalog.setup, '_get', refuse)
    for value in ('http://192.168.1.50:8188/\x00', 'http://192.168.1.50:8188/\x7f',
                  'http://192.168.1.50:8188/\u200b', 'http://192.168.1.50:8188 /'):
        result = engine_catalog.test_server('comfyui', value)
        assert result['ok'] is False, value
        assert result['problem'] == 'bad_address', value


def test_the_test_route_reports_a_pasted_invisible_character_instead_of_failing(client):
    response = client.post('/api/studio/engines/test',
                           json={'kind': 'comfyui', 'url': 'http://192.168.1.50:8188/\x00'})
    assert response.status_code == 200, response.text
    assert response.json()['ok'] is False
    assert response.json()['problem'] == 'bad_address'


# -- one unusable host must not stop the scan -------------------------------

def test_a_host_with_a_stray_space_is_unreachable_not_an_exception():
    result = discovery.probe('http://studio pc:8188', timeout=0.05)
    assert result['reachable'] is False
    assert result['url'] == 'http://studio pc:8188'
    assert 'InvalidURL' in result['detail']


def test_one_unusable_host_does_not_stop_the_scan(monkeypatch):
    """``scan`` maps ``probe`` over every candidate: one raise lost them all."""
    monkeypatch.setattr(discovery, 'PORTS', (1,))
    result = discovery.scan('', 'studio pc', timeout=0.05)
    assert result['success'] is True
    assert result['tested'] == len(discovery.candidates('', 'studio pc'))


# -- the folder plan approved, and start refused ----------------------------

@pytest.fixture
def small_file(monkeypatch):
    """One 6 KB "weight file" served by a stand-in urlopen."""
    data = b'abcdef' * 1000
    monkeypatch.setattr(install, 'ACE15_FILES', [
        {'path': 'split/test.bin', 'folder': 'diffusion_models', 'size': len(data),
         'sha256': hashlib.sha256(data).hexdigest()}])

    class Response(io.BytesIO):
        status = 200
        headers = {}

    monkeypatch.setattr(install.urllib.request, 'urlopen', lambda *a, **k: Response(data))
    return data


def _wait(job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = install.status(job_id)
        if result['state'] not in ('running', 'cancelling'):
            return result
        time.sleep(.01)
    pytest.fail('download did not finish')


def test_start_runs_for_the_folder_plan_approved_through_a_link(small_file, tmp_path):
    """A models folder is often a link or a Windows junction to another drive.

    ``start`` resolved the link first and then judged the folder by the target's
    own name, so a folder ``plan`` had just called ready was refused with
    "Choose the folder ComfyUI calls models."
    """
    real = tmp_path / 'comfy-models'
    real.mkdir()
    link = tmp_path / 'ComfyUI' / 'models'
    link.parent.mkdir()
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows without the right
        pytest.skip('this machine cannot create a directory link')

    assert install.plan(str(link))['ok'] is True
    result = _wait(install.start(str(link))['job_id'])
    assert result['state'] == 'done', result
    assert (real / 'diffusion_models' / 'test.bin').read_bytes() == small_file
