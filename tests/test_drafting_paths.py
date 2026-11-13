"""Where drafting actually happens — across the two processes the product runs as.

Review item: the host (`standalone/app.py`) is a separate loopback process with no `ctx`, and
`llm_bridge` is bound in the plugin process, so `/api/studio/ideas` on the host always answered 503.
The pane, the frame and the chat tool all needed a path that ends in the process that owns the model.

These tests exercise all three ends:

* the host, in a **separate process**, answers 503 with a sentence that names the working paths;
* the plugin's own route (`/studio/draft`) drafts with a bound `ctx.llm`;
* the tool action `draft` does the same for a chat, and can open the project with it.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DRAFT_JSON = {
    "title": "Night Drive",
    "caption": "warm analog synthwave, slow arpeggio, soft drums, no vocals",
    "lyrics": "[verse]\\nheadlights on the empty road",
    "bpm": 96,
    "keyscale": "A minor",
    "duration_seconds": 60,
    "instrumental": False,
}


class FakeLlm:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply or json.dumps(DRAFT_JSON)

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.reply


def test_the_host_process_says_where_drafting_lives_instead_of_a_bare_503():
    """A real second process: no ctx.llm there, and the answer has to be actionable."""
    code = textwrap.dedent(
        """
        import json, os, sys, tempfile
        os.environ['HEWN_STUDIO_DATA_DIR'] = tempfile.mkdtemp()
        os.environ['HEWN_STUDIO_TOKEN'] = 'tok'
        from fastapi.testclient import TestClient
        from standalone.app import app
        client = TestClient(app, base_url='http://127.0.0.1:8775')
        client.headers['X-Studio-Token'] = 'tok'
        answer = client.post('/api/studio/ideas', json={
            'messages': [{'role': 'user', 'content': 'a night drive'}],
            'action': 'draft', 'tone': 'warm', 'style': 50})
        print(json.dumps({'status': answer.status_code, 'detail': answer.json().get('detail', '')}))
        """
    )
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    env['PYTHONPATH'] = str(ROOT)
    finished = subprocess.run([sys.executable, '-c', code], cwd=str(ROOT), env=env,
                              capture_output=True, text=True, timeout=120)
    assert finished.returncode == 0, finished.stderr[-800:]
    answer = json.loads(finished.stdout.strip().splitlines()[-1])
    assert answer['status'] == 503
    detail = answer['detail']
    assert 'pane' in detail and 'chat' in detail and "action='draft'" in detail, (
        f"a 503 that does not say where to draft is the bug: {detail!r}"
    )


def test_the_plugin_route_drafts_with_the_model_it_owns(monkeypatch, tmp_path):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio'))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dashboard.plugin_api import router
    from music_studio import llm_bridge

    fake = FakeLlm()
    monkeypatch.setattr(llm_bridge, 'available', lambda: True)
    monkeypatch.setattr(llm_bridge, 'complete', fake.complete)

    app = FastAPI()
    app.include_router(router, prefix='/api')
    client = TestClient(app, base_url='http://127.0.0.1')
    answer = client.post('/api/studio/draft', json={'prompt': 'a night drive', 'tone': 'warm',
                                                    'style': 70})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body['draft']['title'] == 'Night Drive'
    assert body['written_by'] == 'ctx.llm'
    assert fake.calls, 'the route did not call the model it is supposed to own'


def test_the_plugin_route_says_plainly_when_no_model_is_bound(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path / 'studio2'))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dashboard.plugin_api import router
    from music_studio import llm_bridge

    monkeypatch.setattr(llm_bridge, 'available', lambda: False)
    app = FastAPI()
    app.include_router(router, prefix='/api')
    answer = TestClient(app, base_url='http://127.0.0.1').post('/api/studio/draft',
                                                              json={'prompt': 'anything'})
    assert answer.status_code == 503
    assert 'pane' in answer.json()['detail']


def test_the_tool_can_draft_and_open_the_project_with_it(monkeypatch, tmp_path):
    """A chat should not have to leave the tool to get a draft."""
    spec = importlib.util.spec_from_file_location('ms_entry_draft', ROOT / '__init__.py')
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)

    tools = []

    class Ctx:
        def register_tool(self, **kwargs):
            tools.append(kwargs)

        def register_skill(self, *args, **kwargs):
            pass

    fake = FakeLlm()
    with patch('music_studio.service.default_data_dir', lambda plugin_id: tmp_path), \
            patch('music_studio.llm_bridge.available', lambda: True), \
            patch('music_studio.llm_bridge.complete', fake.complete):
        plugin.register(Ctx())
        handler = tools[0]['handler']
        result = json.loads(handler({'action': 'draft', 'prompt': 'a night drive', 'create': True}))

    assert result['success'] is True, result
    assert result['draft']['title'] == 'Night Drive'
    assert result['project']['title'] == 'Night Drive', 'create=True opens the project'
    assert fake.calls
