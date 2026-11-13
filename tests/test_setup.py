"""Setting up an engine: three paths, each tested against a stub server rather than the network."""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import setup  # noqa: E402


class Stub(BaseHTTPRequestHandler):
    """A server whose answers the test dictates."""
    routes = {}
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        Stub.seen.append(('GET', self.path, dict(self.headers)))
        status, payload = Stub.routes.get(self.path, (404, {'detail': 'no'}))
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(routes):
    Stub.routes = routes
    Stub.seen = []
    server = HTTPServer(('127.0.0.1', 0), Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f'http://127.0.0.1:{server.server_port}'


class UrlTests(unittest.TestCase):
    def test_a_bare_address_is_refused_with_an_example(self):
        with self.assertRaises(ValueError) as raised:
            setup.normalise_url('192.168.1.20:8188')
        self.assertIn('http://', str(raised.exception))

    def test_credentials_in_the_address_are_refused(self):
        with self.assertRaises(ValueError) as raised:
            setup.normalise_url('http://user:pass@host:8188')
        self.assertIn('credentials', str(raised.exception))

    def test_a_trailing_slash_is_removed(self):
        self.assertEqual(setup.normalise_url('http://host:8188/'), 'http://host:8188')


class ComfyProbeTests(unittest.TestCase):
    def test_a_comfyui_with_the_music_3_nodes_is_ready(self):
        server, base = serve({
            '/system_stats': (200, {'system': {'comfyui_version': '0.3.40'}}),
            '/object_info': (200, {'MiniMaxMusic3TextEncode': {},
                                   'EmptyMiniMaxMusic3LatentAudio': {}}),
        })
        try:
            result = setup.probe_comfy(base)
        finally:
            server.shutdown()
        self.assertTrue(result['ok'])
        names = [c['name'] for c in result['checks']]
        self.assertIn('MiniMax Music 3 nodes', names)

    def test_a_comfyui_without_the_nodes_says_which_are_missing(self):
        server, base = serve({
            '/system_stats': (200, {'system': {}}),
            '/object_info': (200, {'LoadImage': {}}),
        })
        try:
            result = setup.probe_comfy(base)
        finally:
            server.shutdown()
        self.assertFalse(result['ok'])
        detail = [c for c in result['checks'] if c['name'] == 'MiniMax Music 3 nodes'][0]
        self.assertIn('MiniMaxMusic3TextEncode', detail['detail'])

    def test_the_required_nodes_are_the_ones_the_engine_builds(self):
        """A probe with invented node names fails on a good server; take them from the engine."""
        from music_studio.engines.minimax_m3_comfy import MINIMAX_NODES

        server, base = serve({
            '/system_stats': (200, {'system': {}}),
            '/object_info': (200, {name: {} for name in MINIMAX_NODES}),
        })
        try:
            result = setup.probe_comfy(base)
        finally:
            server.shutdown()
        self.assertTrue(result['ok'], result['detail'])

    def test_nothing_listening_is_a_sentence_not_a_crash(self):
        result = setup.probe_comfy('http://127.0.0.1:9')
        self.assertFalse(result['ok'])
        self.assertIn('nothing answered', result['detail'])


class AceProbeTests(unittest.TestCase):
    def test_an_ace_server_that_answers_health_is_ready(self):
        server, base = serve({'/health': (200, {'data': {'status': 'ok'}})})
        try:
            result = setup.probe_ace(base)
        finally:
            server.shutdown()
        self.assertTrue(result['ok'])

    def test_a_refused_path_still_means_something_is_listening(self):
        server, base = serve({'/health': (404, {'detail': 'no'})})
        try:
            result = setup.probe_ace(base)
        finally:
            server.shutdown()
        self.assertTrue(result['ok'], 'a 404 proves a server answered, which is the question')


class HuggingFacePlanTests(unittest.TestCase):
    def test_a_model_id_gets_a_folder_and_the_truth_about_where_it_goes(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = setup.hf_plan('MiniMaxAI/MiniMax-Music3', tmp)
        self.assertIn('MiniMaxAI/MiniMax-Music3', plan['model_id'])
        self.assertIn('models', plan['download_to'])
        self.assertIn('diffusion_models', plan['where_it_goes'])

    def test_a_url_is_not_a_model_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as raised:
                setup.hf_plan('https://huggingface.co/MiniMaxAI/MiniMax-Music3', tmp)
        self.assertIn('model id', str(raised.exception))

    def test_copying_without_a_local_comfyui_explains_why_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            from unittest.mock import patch
            with patch.object(setup, 'find_local_comfy_models', lambda: None):
                with self.assertRaises(ValueError) as raised:
                    setup.copy_into_comfy(tmp, tmp)
        self.assertIn('no local ComfyUI', str(raised.exception))


class CloudTests(unittest.TestCase):
    def test_a_key_is_stored_beside_the_endpoint_and_never_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = setup.save_cloud(tmp, 'default', 'https://api.example.com/v1', 'sk-test-123')
            self.assertTrue(saved['ok'])
            listing = setup.list_cloud(tmp)
            self.assertEqual(listing[0]['url'], 'https://api.example.com/v1')
            self.assertNotIn('sk-test-123', json.dumps(listing),
                             'the page must never receive the key back')
            mode = (Path(tmp) / setup.KEY_FILE).stat().st_mode & 0o777
            if os.name != 'nt':
                self.assertEqual(mode, 0o600)
            # Windows has no POSIX group/other bits: os.open's mode only sets the read-only flag and
            # access is governed by the inherited ACL, so there is nothing here to assert.

    def test_a_working_endpoint_is_confirmed_with_one_request(self):
        server, base = serve({'/models': (200, {'data': [{'id': 'music-3'}]})})
        try:
            with tempfile.TemporaryDirectory() as tmp:
                setup.save_cloud(tmp, 'default', base, 'sk-test')
                result = setup.probe_cloud(tmp)
        finally:
            server.shutdown()
        self.assertTrue(result['ok'])
        self.assertTrue(any(c['name'] == 'model list' for c in result['checks']))
        auth = Stub.seen[0][2].get('Authorization')
        self.assertEqual(auth, 'Bearer sk-test')

    def test_a_refused_key_says_so(self):
        server, base = serve({'/models': (401, {'detail': 'no'})})
        try:
            with tempfile.TemporaryDirectory() as tmp:
                setup.save_cloud(tmp, 'default', base, 'sk-wrong')
                result = setup.probe_cloud(tmp)
        finally:
            server.shutdown()
        self.assertFalse(result['ok'])
        self.assertIn('refused', result['detail'])


if __name__ == '__main__':
    unittest.main()
