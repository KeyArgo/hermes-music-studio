"""Plain words → draft changes, and the journal that remembers them.

What these pin: a phrase that the engines cannot honour is refused with a sentence instead of a silent
no-op; a change revokes the approval (it is a different draft); a *like* does not; and the journal lives
on the project file, so it is still there after a reload.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import intent  # noqa: E402
from music_studio.store import Store  # noqa: E402


class IntentTests(unittest.TestCase):
    def test_add_names_the_instrument_in_the_one_caption(self):
        result = intent.apply('add a fiddle', {'caption': 'folk, warm', 'bpm': 100})
        self.assertEqual(result['patch']['caption'], 'folk, warm, fiddle')
        self.assertIn('fiddle', result['notes'][0])

    def test_go_dark_expands_into_words_both_engines_understand(self):
        patch = intent.apply('go dark', {'caption': 'folk'})['patch']
        for word in ('dark', 'minor', 'sparse'):
            self.assertIn(word, patch['caption'])

    def test_go_soft_and_delicate_is_the_opposite_palette(self):
        patch = intent.apply('go soft and delicate', {'caption': 'folk'})['patch']
        self.assertIn('delicate', patch['caption'])
        self.assertIn('brushed', patch['caption'])

    def test_half_time_is_a_section_feel_not_a_second_tempo(self):
        result = intent.apply('half-time in the bridge', {'caption': 'folk', 'bpm': 96})
        self.assertEqual(result['section'], 'bridge')
        self.assertIn('half-time', result['patch']['caption'])
        self.assertNotIn('bpm', result['patch'], 'no engine accepts a per-section tempo')

    def test_removing_something_rewrites_the_caption_and_says_what_it_did(self):
        result = intent.apply('remove the fiddle', {'caption': 'folk, fiddle, warm'})
        self.assertNotIn('fiddle', result['patch']['caption'])
        self.assertTrue(any('took "fiddle" out' in note for note in result['notes']))

    def test_a_tempo_never_leaks_into_the_style_prose(self):
        patch = intent.apply('set the tempo to 90', {'caption': 'folk', 'bpm': 100})['patch']
        self.assertEqual(patch, {'caption': 'folk', 'bpm': 90})

    def test_faster_moves_the_tempo_by_ten_within_the_engine_limits(self):
        patch = intent.apply('faster', {'caption': 'folk', 'bpm': 216})['patch']
        self.assertEqual(patch['bpm'], 220, 'bpm is capped at the engine maximum')

    def test_an_impossible_request_is_a_sentence_not_a_silent_no_op(self):
        with self.assertRaises(ValueError) as caught:
            intent.apply('mute the drums', {'caption': 'folk'})
        self.assertIn('no instrument channel', str(caught.exception))

    def test_removing_vocals_points_at_the_instrumental_switch(self):
        with self.assertRaises(ValueError) as caught:
            intent.apply('remove the vocals', {'caption': 'folk'})
        self.assertIn('Instrumental', str(caught.exception))

    def test_unrecognised_words_are_kept_rather_than_dropped(self):
        patch = intent.apply('Otyken style throat singing', {'caption': 'folk'})['patch']
        self.assertIn('Otyken style throat singing', patch['caption'])

    def test_the_caption_never_exceeds_what_the_engines_accept(self):
        patch = intent.apply('add ' + 'x' * 600, {'caption': 'folk'})['patch']
        self.assertLessEqual(len(patch['caption']), 512)

    def test_empty_instruction_says_what_to_type(self):
        with self.assertRaises(ValueError) as caught:
            intent.apply('   ', {'caption': 'folk'})
        self.assertIn('add a fiddle', str(caught.exception))


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.project = self.store.create(title='Memory', caption='folk', lyrics='[chorus]\nla',
                                         duration_seconds=30)
        self.pid = self.project['project_id']

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _approved(store, pid):
        token = store.get(pid)['updated_at']
        return store.approve(pid, base_updated_at=token)

    def test_a_change_revokes_the_approval_because_it_is_a_different_draft(self):
        self._approved(self.store, self.pid)
        self.store.update(self.pid, caption='folk, fiddle')
        self.assertFalse(self.store.get(self.pid)['approved'])

    def test_a_like_keeps_the_approval_and_records_the_keeper(self):
        self._approved(self.store, self.pid)
        memory = self.store.remember(self.pid, kind='keep', text='', take_id='t-1', bump=False)
        self.assertEqual(memory['kept_take_id'], 't-1')
        self.assertTrue(self.store.get(self.pid)['approved'], 'liking a take is not a draft edit')

    def test_the_journal_lives_on_the_project_file_and_survives_a_reload(self):
        self.store.remember(self.pid, kind='change', text='go dark', patch={'caption': 'dark'})
        reread = Store(Path(self.tmp.name)).get(self.pid)
        self.assertEqual(reread['memory']['events'][-1]['text'], 'go dark')
        self.assertEqual(reread['memory']['events'][-1]['patch'], {'caption': 'dark'})

    def test_the_journal_is_bounded_so_a_song_cannot_grow_without_end(self):
        for index in range(210):
            self.store.remember(self.pid, kind='change', text=f'change {index}', patch={})
        self.assertEqual(len(self.store.get(self.pid)['memory']['events']), 200)
        self.assertEqual(self.store.get(self.pid)['memory']['events'][-1]['text'], 'change 209')


if __name__ == '__main__':
    unittest.main()


class ChangeRouteTests(unittest.TestCase):
    """`POST /change` is the button's endpoint and the tool's logic — one implementation, so test it once."""

    def setUp(self):
        import os
        from unittest.mock import patch
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from dashboard.plugin_api import router

        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()
        app = FastAPI()
        app.include_router(router)
        # The standalone server rejects a non-loopback Host, so keep the base URL honest.
        self.client = TestClient(app, base_url='http://127.0.0.1')

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_the_button_endpoint_rewrites_the_caption_and_revokes_approval(self):
        from music_studio import service

        project = service.store().create(title='Route', caption='folk', lyrics='[chorus]\nla',
                                         duration_seconds=30)
        service.store().approve(project['project_id'],
                                base_updated_at=service.store().get(project['project_id'])['updated_at'])

        answer = self.client.post('/change', json={'project_id': project['project_id'],
                                                   'instruction': 'add a fiddle'})

        self.assertEqual(answer.status_code, 200)
        body = answer.json()
        self.assertIn('fiddle', body['project']['caption'])
        self.assertFalse(body['project']['approved'], 'a changed draft is not the approved one')
        self.assertEqual(body['project']['memory']['events'][-1]['text'], 'add a fiddle')

    def test_an_impossible_instruction_answers_400_with_the_reason(self):
        from music_studio import service

        project = service.store().create(title='Route 2', caption='folk', lyrics='[chorus]\nla',
                                         duration_seconds=30)
        answer = self.client.post('/change', json={'project_id': project['project_id'],
                                                   'instruction': 'mute the drums'})
        self.assertEqual(answer.status_code, 400)
        self.assertIn('no instrument channel', answer.json()['detail'])

    def test_a_missing_instruction_is_a_400_not_a_500(self):
        answer = self.client.post('/change', json={'project_id': 'p'})
        self.assertEqual(answer.status_code, 400)
