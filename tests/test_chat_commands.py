"""`/song` in a chat: it shows, it queues, and it never blocks the conversation.

The two claims worth pinning:
1. the approval gate survives the command line — the first `/song go` prints the draft and queues
   nothing, and a draft edited after that cannot be confirmed by the old token;
2. queuing is immediate — `queue` returns while the take is still `running`, so a chat is free.
"""
import importlib.util
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location('ms_plugin_entry_chat', ROOT / '__init__.py')
assert _spec is not None and _spec.loader is not None
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)
from music_studio import chat  # noqa: E402


def _project(project_id='p-1', title='Velvet Growl', updated_at=10.0, approved=False, takes=None):
    return {'project_id': project_id, 'title': title, 'updated_at': updated_at,
            'caption': 'dark folk, fiddle, low strings', 'lyrics': '[verse]\nline one\n[chorus]\nline two',
            'engine': 'ace_local', 'duration_seconds': 60, 'approved': approved,
            'takes': takes or []}


class FakeClient:
    """Records what the command asked for, so the test can prove what did *not* happen."""

    def __init__(self, projects, queued_state='running'):
        self.projects = projects
        self.queued_state = queued_state
        self.approved = []
        self.queued = []
        self.exported = []
        self.played = []
        self.changed = []
        self.kept = []
        self.snippets = []
        self.splices = []

    def status(self):
        return self.projects

    def approve(self, project_id, token):
        self.approved.append((project_id, token))
        for project in self.projects:
            if project['project_id'] == project_id:
                project['approved'] = True
        return {'success': True}

    def snippet(self, project_id, take_id, start_s, end_s, instruction):
        self.snippets.append((project_id, take_id, start_s, end_s, instruction))
        return {'take': {'take_id': 'child-1234'}}

    def splice(self, project_id, take_id):
        self.splices.append((project_id, take_id))
        return {'take': {'take_id': 'merged-9999'}}

    def change(self, project_id, instruction):
        self.changed.append((project_id, instruction))
        return {'changed': ['style: folk, fiddle'], 'notes': ['asked for: fiddle'],
                'note': 'instructions for the next render'}

    def keep(self, project_id, take_id):
        self.kept.append((project_id, take_id))
        return {'kept_take_id': take_id}

    def queue(self, project_id):
        self.queued.append(project_id)
        return {'success': True, 'engine': 'ace_local',
                'take': {'take_id': 't-1', 'state': self.queued_state}}

    def export(self, project_id, take_id):
        self.exported.append((project_id, take_id))
        return {'path': '/home/alex/Music/Hermes Music Studio/Velvet Growl.wav', 'bytes': 4_800_000}

    def play_url(self, project_id, take_id):
        self.played.append((project_id, take_id))
        return f'http://127.0.0.1:8775/api/projects/{project_id}/takes/{take_id}/audio'


class CommandTests(unittest.TestCase):
    def setUp(self):
        chat._shown.clear()

    def test_the_first_go_shows_the_draft_and_queues_nothing(self):
        client = FakeClient([_project()])

        answer = chat.handle('go', client)

        self.assertIn('Velvet Growl', answer)
        self.assertIn('caption: dark folk, fiddle, low strings', answer)
        self.assertIn('Nothing has been queued', answer)
        self.assertEqual(client.queued, [], 'the gate must not render on the first word')
        self.assertEqual(client.approved, [])

    def test_confirming_after_seeing_it_approves_that_exact_version_and_queues(self):
        client = FakeClient([_project()])
        chat.handle('go', client)

        answer = chat.handle('go p-1 now', client)

        self.assertEqual(client.approved, [('p-1', 10.0)])
        self.assertEqual(client.queued, ['p-1'])
        self.assertIn('Queued t-1', answer)
        self.assertIn('stays usable', answer)

    def test_a_draft_edited_after_it_was_shown_cannot_be_confirmed(self):
        project = _project()
        client = FakeClient([project])
        chat.handle('go', client)

        project['updated_at'] = 11.0     # an edit lands in the studio
        answer = chat.handle('go p-1 now', client)

        self.assertIn('not the version you reviewed', answer)
        self.assertEqual(client.queued, [], 'a stale review token must not render')

    def test_an_already_approved_draft_queues_without_re_approving(self):
        client = FakeClient([_project(approved=True)])
        chat.handle('go p-1', client)

        chat.handle('go p-1 now', client)

        self.assertEqual(client.approved, [], 'approving twice would move the token the person reviewed')
        self.assertEqual(client.queued, ['p-1'])

    def test_takes_says_what_is_finished_and_what_failed(self):
        takes = [{'take_id': 't-1', 'state': 'succeeded', 'engine_id': 'ace_local',
                  'audio_seconds': 59.9, 'qc': {'file_s': 59.9}},
                 {'take_id': 't-2', 'state': 'failed', 'engine_id': 'ace_local',
                  'error': 'bpm must be 40-220 or null'}]
        client = FakeClient([_project(takes=takes)])

        answer = chat.handle('takes p-1', client)

        self.assertIn('succeeded', answer)
        self.assertIn('59.9 s', answer)
        self.assertIn('bpm must be 40-220', answer)

    def test_play_refuses_when_nothing_finished_and_names_the_fix(self):
        client = FakeClient([_project(takes=[{'take_id': 't-2', 'state': 'failed'}])])

        answer = chat.handle('play p-1', client)

        self.assertIn('nothing finished to play', answer)
        self.assertEqual(client.played, [])

    def test_play_hands_back_a_url_the_pane_can_open(self):
        takes = [{'take_id': 't-9', 'state': 'succeeded', 'engine_id': 'minimax_m3_comfy'}]
        client = FakeClient([_project(takes=takes)])

        answer = chat.handle('play p-1', client)

        self.assertIn('/api/projects/p-1/takes/t-9/audio', answer)

    def test_an_empty_library_answers_with_what_to_press(self):
        answer = chat.handle('', FakeClient([]))

        self.assertIn('No songs yet', answer)
        self.assertIn('Create', answer)

    def test_an_unknown_verb_returns_the_usage_not_a_traceback(self):
        answer = chat.handle('frobnicate p-1', FakeClient([_project()]))

        self.assertIn('/song go', answer)


    def test_change_applies_plain_words_and_tells_the_person_to_review_it(self):
        client = FakeClient([_project()])

        answer = chat.handle('change p-1 add a fiddle', client)

        self.assertEqual(client.changed, [('p-1', 'add a fiddle')])
        self.assertIn('style: folk, fiddle', answer)
        self.assertIn('approval was revoked', answer)

    def test_keep_remembers_the_newest_finished_take_without_rendering(self):
        takes = [{'take_id': 't-3', 'state': 'succeeded', 'engine_id': 'ace_local'}]
        client = FakeClient([_project(takes=takes)])

        answer = chat.handle('keep p-1', client)

        self.assertEqual(client.kept, [('p-1', 't-3')])
        self.assertEqual(client.queued, [])
        self.assertIn('survives a reload', answer)

    def test_keep_refuses_when_nothing_finished(self):
        client = FakeClient([_project(takes=[{'take_id': 't-1', 'state': 'failed'}])])

        answer = chat.handle('keep p-1', client)

        self.assertIn('nothing finished to keep', answer)
        self.assertEqual(client.kept, [])


    def test_snippet_parses_the_range_and_names_the_splice_to_run_next(self):
        takes = [{'take_id': 't-8', 'state': 'succeeded', 'engine_id': 'ace_local'}]
        client = FakeClient([_project(takes=takes)])

        answer = chat.handle('snippet p-1 10-18 make it more intense', client)

        self.assertEqual(client.snippets, [('p-1', 't-8', 10.0, 18.0, 'make it more intense')])
        self.assertIn('10–18s', answer)
        self.assertIn('/song splice', answer)

    def test_snippet_without_a_range_says_the_shape(self):
        client = FakeClient([_project(takes=[{'take_id': 't-8', 'state': 'succeeded'}])])

        answer = chat.handle('snippet p-1 make it louder', client)

        self.assertIn('10-18', answer)
        self.assertEqual(client.snippets, [])

    def test_splice_reports_only_that_range_changed(self):
        client = FakeClient([_project(takes=[{'take_id': 't-8', 'state': 'succeeded'}])])

        answer = chat.handle('splice p-1 child-1234', client)

        self.assertEqual(client.splices, [('p-1', 'child-1234')])
        self.assertIn('only that range changed', answer)


class RegistrationTests(unittest.TestCase):
    """The gateway hands a command handler the typed text as a **string**.

    Assuming a dict produced `'str' object has no attribute 'get'` on every `/song` — a bug the unit
    tests could not see because they called `chat.handle` directly, one layer below the contract.
    """

    def _handler(self):
        calls = {}

        class Ctx:
            def register_tool(self, **kwargs):
                pass

            def register_skill(self, *args, **kwargs):
                pass

            def register_command(self, name, fn, desc=None, **kwargs):
                calls['name'] = name
                calls['fn'] = fn

        plugin.register(Ctx())
        self.assertEqual(calls.get('name'), 'song')
        return calls['fn']

    def test_a_typed_string_reaches_the_library_not_a_traceback(self):
        answer = self._handler()('')
        self.assertNotIn('has no attribute', answer)
        self.assertTrue(answer.strip())

    def test_a_dict_still_works_for_tool_style_callers(self):
        answer = self._handler()({'text': 'takes'})
        self.assertNotIn('has no attribute', answer)

    def test_no_argument_at_all_is_the_library(self):
        self.assertTrue(self._handler()().strip())


class QueueActionTests(unittest.TestCase):
    def test_queue_returns_while_the_take_is_still_running(self):
        """The whole point of phase 1: the chat is free the moment the worker is spawned."""
        with mock.patch.object(plugin.service, 'generate',
                                        return_value={'success': True,
                                                      'take': {'take_id': 't-7',
                                                               'state': 'running',
                                                               'engine_id': 'ace_local'}}) as gen:
            started = time.time()
            result = plugin._handler({'action': 'queue', 'project_id': 'p-1'})
            elapsed = time.time() - started

        self.assertTrue(result['success'])
        self.assertEqual(result['take']['take_id'], 't-7')
        self.assertEqual(result['take']['state'], 'running')
        self.assertLess(elapsed, 1.0, 'queue must not poll for the render')
        gen.assert_called_once_with('p-1', None)

    def test_queue_without_a_project_is_a_sentence(self):
        with self.assertRaises(ValueError) as caught:
            plugin._handler({'action': 'queue'})
        self.assertIn('project_id', str(caught.exception))


if __name__ == '__main__':
    unittest.main()


if __name__ == '__main__':
    unittest.main()
