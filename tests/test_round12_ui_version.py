"""Round 12: the studio page must name THIS install's app.js, not a version the webview cached.

Hermes Desktop frames the page in a webview, and a webview caches ``style.css`` and ``app.js`` by
URL. An update replaced the files but not the URLs, so the pane could keep running last version's
script against this version's routes — the update looked like it had not happened, and the only cure
was restarting the whole desktop app. Both URLs now carry ``?v=<token>``, and the host swaps the real
token in when it serves the page.

The token is a hash of the three UI files, so it changes exactly when the files change. It does not
read the version stamp: plugin_api loads ui_stamp by path to dodge another profile's copy, and the
stamp read through ``music_studio`` would then describe that copy.
"""

import os
import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = '/api/plugins/hermes-music-studio'
STATIC = ROOT / 'standalone' / 'static'
URL_SAFE = re.compile(r'[A-Za-z0-9._-]+')


@pytest.fixture()
def client():
    from dashboard.plugin_api import router

    app = FastAPI()
    app.include_router(router, prefix=BASE)
    return TestClient(app)


def test_the_pane_is_served_a_versioned_app_js(client):
    """The desktop's page: both asset URLs carry a query, and the placeholder is gone."""
    answer = client.get(BASE + '/studio/ui')
    assert answer.status_code == 200, answer.text

    body = answer.text
    assert 'app.js?v=' in body, body[-400:]
    assert 'style.css?v=' in body, body[-400:]
    # Serving the marker itself would leave the URL unchanging, i.e. no cache-busting at all.
    assert '__STUDIO_STAMP__' not in body, body[-400:]
    # A query that points at a route which does not answer is worse than no query: fetch exactly
    # what the page will ask for.
    match = re.search(r'app\.js\?v=([A-Za-z0-9._-]+)', body)
    assert match, body[-400:]
    assert client.get(BASE + '/studio/assets/app.js?v=' + match.group(1)).status_code == 200


def test_the_token_changes_when_a_served_file_changes(tmp_path):
    from music_studio import ui_stamp

    for name in ('index.html', 'app.js', 'style.css'):
        (tmp_path / name).write_text('x', encoding='utf-8')
    token = ui_stamp.build_token(tmp_path)
    assert URL_SAFE.fullmatch(token), token
    assert ui_stamp.build_token(tmp_path) == token, 'same files, same URL: the cache stays useful'

    (tmp_path / 'app.js').write_text('y', encoding='utf-8')
    assert ui_stamp.build_token(tmp_path) != token


def test_the_token_ignores_the_loaded_music_studio_stamp(monkeypatch, tmp_path):
    """Another profile's copy may own ``music_studio``; its commit must not name these files."""
    from music_studio import ui_stamp

    for name in ('index.html', 'app.js', 'style.css'):
        (tmp_path / name).write_text('x', encoding='utf-8')
    before = ui_stamp.build_token(tmp_path)
    monkeypatch.setattr('music_studio.version_stamp.as_dict', lambda: {
        'version': '0.9.0', 'commit': 'foreign1234', 'built_at': '', 'channel': 'dev'})
    assert ui_stamp.build_token(tmp_path) == before


def test_about_names_the_page_files_it_serves():
    # An open page compares this with the stamp it was loaded with, and reloads when they differ.
    from music_studio import about, ui_stamp
    assert about.about()['ui'] == ui_stamp.build_token(ROOT / 'standalone' / 'static')
