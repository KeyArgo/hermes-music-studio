"""The agent-facing pipeline: the `music_studio` tool a chat calls, and the CLI a shell calls.

These tests pin the shape a chat depends on: every action answers JSON, an approval attaches to the
draft version the person reviewed, an unapproved draft never renders, and a failure is a sentence
rather than a traceback.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import importlib.util

_spec = importlib.util.spec_from_file_location('ms_plugin_entry', ROOT / '__init__.py')
assert _spec is not None and _spec.loader is not None
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)


class FakeCtx:
    """The two registration calls the entrypoint makes, and nothing else."""

    def __init__(self):
        self.tools = []
        self.skills = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_skill(self, name, path, description=None):
        self.skills.append((name, str(path), description))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self._patch = patch('music_studio.service.default_data_dir', lambda plugin_id: self.data)
        self._patch.start()
        self.ctx = FakeCtx()
        plugin.register(self.ctx)
        self.handler = self.ctx.tools[0]['handler']

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def call(self, **args):
        return json.loads(self.handler(args))

    def draft(self, **overrides):
        body = {'title': 'Pipeline test', 'caption': 'warm synthwave', 'instrumental': True,
                'duration_seconds': 10, 'engine': 'ace'}
        body.update(overrides)
        return body

    def test_the_tool_is_registered_with_every_action_a_chat_needs(self):
        self.assertEqual(self.ctx.tools[0]['name'], 'music_studio')
        actions = set(self.ctx.tools[0]['schema']['parameters']['properties']['action']['enum'])
        self.assertEqual(actions, set(plugin.ACTIONS))
        for required in ('approve', 'render', 'takes', 'export', 'status'):
            self.assertIn(required, actions)

    def test_create_then_approve_binds_the_reviewed_version(self):
        created = self.call(action='create', draft=self.draft())
        self.assertTrue(created['success'])
        project = created['project']

        stale = self.call(action='approve', project_id=project['project_id'], updated_at=1.0)
        self.assertFalse(stale['success'])
        self.assertIn('stale', stale['error'])

        approved = self.call(action='approve', project_id=project['project_id'],
                             updated_at=project['updated_at'])
        self.assertTrue(approved['success'])
        self.assertTrue(approved['project']['approved'])

    def test_a_take_list_is_readable_and_starts_empty(self):
        project = self.call(action='create', draft=self.draft())['project']
        takes = self.call(action='takes', project_id=project['project_id'])
        self.assertTrue(takes['success'])
        self.assertEqual(takes['takes'], [])

    def test_an_unapproved_draft_never_reaches_the_queue(self):
        """The gate is enforced where the take is created, not in the client."""
        from music_studio.jobs import Jobs
        from music_studio import service as studio_service

        project = self.call(action='create', draft=self.draft())['project']
        with self.assertRaises(ValueError) as raised:
            Jobs(studio_service.store()).enqueue(project['project_id'], 7)
        self.assertIn('requires approval', str(raised.exception))

    def test_a_refused_render_is_a_sentence_not_a_traceback(self):
        # With no engine configured this machine cannot render; with an engine it would still be
        # refused. Either way the chat gets one readable reason and no stack trace.
        project = self.call(action='create', draft=self.draft())['project']
        refused = self.call(action='render', project_id=project['project_id'], timeout_s=2)
        self.assertFalse(refused['success'])
        self.assertTrue(refused['error'])
        self.assertNotIn('Traceback', refused['error'])

    def test_editing_the_draft_revokes_approval(self):
        project = self.call(action='create', draft=self.draft())['project']
        self.call(action='approve', project_id=project['project_id'],
                  updated_at=project['updated_at'])
        edited = self.call(action='update', project_id=project['project_id'],
                           draft={'caption': 'harsher, more distortion'})
        self.assertTrue(edited['success'])
        self.assertFalse(edited['project']['approved'])

    def test_export_without_a_take_says_so(self):
        project = self.call(action='create', draft=self.draft())['project']
        result = self.call(action='export', project_id=project['project_id'])
        self.assertFalse(result['success'])
        self.assertIn('render first', result['error'])

    def test_unknown_action_lists_what_exists(self):
        result = self.call(action='sing')
        self.assertFalse(result['success'])
        self.assertIn('unknown action', result['error'])
        self.assertIn('render', result['error'])

    def test_a_missing_project_is_not_a_traceback(self):
        result = self.call(action='takes', project_id='no-such-project')
        self.assertFalse(result['success'])
        self.assertIn('error', result)

    def test_the_skill_is_registered_beside_the_tool(self):
        self.assertEqual(self.ctx.skills[0][0], 'music-studio')
        self.assertTrue(Path(self.ctx.skills[0][1]).is_file())


class CliTests(unittest.TestCase):
    def test_the_cli_finds_the_host_from_the_state_file(self):
        spec = importlib.util.spec_from_file_location('ms_cli', ROOT / 'scripts' / 'ms.py')
        assert spec is not None and spec.loader is not None
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'studio-host.json'
            state.write_text(json.dumps({'port': 9123}))
            with patch.object(cli, 'DATA_DIR', Path(tmp)):
                self.assertEqual(cli.discover_base(), 'http://127.0.0.1:9123')

    def test_the_cli_says_where_to_point_when_nothing_answers(self):
        spec = importlib.util.spec_from_file_location('ms_cli2', ROOT / 'scripts' / 'ms.py')
        assert spec is not None and spec.loader is not None
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with self.assertRaises(SystemExit) as raised:
            cli.call('http://127.0.0.1:9', '/api/status', timeout=1)
        self.assertEqual(raised.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
