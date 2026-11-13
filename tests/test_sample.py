"""Both surfaces serve the shipped sample from one implementation.

The standalone app and the dashboard router each had their own six lines for
`/api/studio/sample`; changing one skipped the other. The test compares the two
responses byte for byte, so a future drift fails here.
"""
import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from dashboard.plugin_api import router  # noqa: E402


def dashboard_client():
    app = FastAPI()
    app.include_router(router, prefix='/api/plugins/hermes-music-studio')
    # A loopback base_url: the standalone app rejects any other Host header.
    return TestClient(app, base_url='http://127.0.0.1')


class SampleRouteTests(unittest.TestCase):
    def test_the_dashboard_router_serves_the_sample(self):
        response = dashboard_client().get('/api/plugins/hermes-music-studio/studio/sample')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'audio/wav')
        self.assertGreater(len(response.content), 1024)
        self.assertEqual(response.content[:4], b'RIFF')

    def test_both_surfaces_return_the_same_bytes(self):
        from standalone.app import app as standalone_app

        dashboard = dashboard_client().get('/api/plugins/hermes-music-studio/studio/sample')
        from conftest import studio_client

        standalone = studio_client(standalone_app).get('/api/studio/sample')
        self.assertEqual(standalone.status_code, 200)
        self.assertEqual(standalone.content, dashboard.content,
                         'the two surfaces drifted: they must serve one file the same way')

    def test_a_missing_sample_is_a_404_that_names_it(self):
        from music_studio import sample

        original = sample.SAMPLE_AUDIO
        sample.SAMPLE_AUDIO = Path('/nonexistent/afterglow.wav')
        try:
            response = dashboard_client().get('/api/plugins/hermes-music-studio/studio/sample')
        finally:
            sample.SAMPLE_AUDIO = original
        self.assertEqual(response.status_code, 404)
        self.assertIn('sample not found', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
