"""The standalone app must run without importing the Hermes host."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_standalone_import_and_routes_without_hermes(tmp_path):
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env['HEWN_STUDIO_DATA_DIR'] = str(tmp_path / 'studio')
    env['HEWN_STUDIO_TOKEN'] = 'test-token-abc123'
    code = '''
import json, sys
from fastapi.testclient import TestClient
from standalone.app import app
c = TestClient(app, base_url='http://127.0.0.1')
c.headers['X-Studio-Token'] = 'test-token-abc123'
assert c.get('/').status_code == 200
assert c.get('/api/demo').status_code == 200
assert c.get('/api/demo/audio').status_code == 200
assert c.get('/api/status').status_code == 200
draft = {'title': 'A test song', 'caption': 'warm folk', 'lyrics': 'A test line',
         'engine': 'ace_local', 'duration_seconds': 30, 'instrumental': False}
created = c.post('/api/create', json={'draft': draft})
assert created.status_code == 200, created.text
project = created.json()['project']
approval = c.post('/api/approve', json={'project_id': project['project_id'],
                                       'base_updated_at': project['updated_at']})
assert approval.status_code == 200, approval.text
assert approval.json()['project']['approved'] is True
assert 'plugins.plugin_storage' not in sys.modules
print(json.dumps({'ok': True}))
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ok']


def test_mutating_api_rejects_cross_site_origin(tmp_path):
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env['HEWN_STUDIO_DATA_DIR'] = str(tmp_path / 'studio')
    code = '''
from fastapi.testclient import TestClient
from standalone.app import app
c = TestClient(app, base_url='http://127.0.0.1')
r = c.post('/api/create', json={'draft': {}}, headers={'origin': 'https://evil.example'})
assert r.status_code == 403, r.text
assert c.get('/api/status', headers={'host': 'evil.example'}).status_code == 400
assert c.post('/api/create', content='{}', headers={'content-type': 'text/plain'}).status_code == 415
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
