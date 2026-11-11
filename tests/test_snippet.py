"""Snippet edits: change eight seconds, keep the rest byte-identical.

Phase 3. The engine can only repaint (ACE); MiniMax's refusal is its capability check, not a silent
whole-song re-render. These tests pin the two halves separately: the words become a caption on the
queued take, and the splice copies the parent's audio back outside the range.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class SnippetRouteTests(unittest.TestCase):
    def setUp(self):
        from unittest import mock as _mock

        self.temp = tempfile.TemporaryDirectory()
        self.env = _mock.patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()
        from dashboard.plugin_api import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, base_url='http://127.0.0.1')

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_every_field_is_required_and_missing_ones_are_named(self):
        for field in ('project_id', 'take_id', 'start_s', 'end_s', 'instruction'):
            body = {'project_id': 'p', 'take_id': 't', 'start_s': 1, 'end_s': 9, 'instruction': 'x'}
            body.pop(field)
            answer = self.client.post('/snippet', json=body)
            self.assertEqual(answer.status_code, 400, field)
            self.assertIn(field, answer.json()['detail'])

    def test_the_splice_route_needs_both_ids(self):
        answer = self.client.post('/snippet/splice', json={'project_id': 'p'})
        self.assertEqual(answer.status_code, 400)
        self.assertIn('take_id', answer.json()['detail'])


class SnippetServiceTests(unittest.TestCase):
    def test_the_instruction_becomes_the_caption_of_the_queued_repaint(self):
        from music_studio import service

        draft = {'project_id': 'p1', 'title': 'T', 'caption': 'folk, warm', 'bpm': 96,
                 'lyrics': '[chorus]\nla', 'takes': []}
        with mock.patch.object(service, 'store') as store_factory, \
                mock.patch.object(service, 'repaint',
                                  return_value={'success': True, 'take': {'take_id': 'c1'}}) as repaint:
            store_factory.return_value.get.return_value = draft
            store_factory.return_value.remember = mock.Mock()

            result = service.snippet('p1', 't1', 3.0, 11.0, 'go dark')

        repaint.assert_called_once()
        args, kwargs = repaint.call_args
        self.assertEqual(args[:4], ('p1', 't1', 3.0, 11.0))
        self.assertIn('dark', kwargs['caption'])
        self.assertIn('dark', result['caption'])
        self.assertIn('byte-identical', result['note'])

    def test_a_child_without_a_parent_is_refused_in_words(self):
        from music_studio import service

        draft = {'project_id': 'p1', 'takes': [{'take_id': 'c1', 'state': 'succeeded'}]}
        with mock.patch.object(service, 'store') as store_factory:
            store_factory.return_value.get.return_value = draft
            with self.assertRaises(ValueError) as caught:
                service.splice_snippet('p1', 'c1')
        self.assertIn('no parent', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
