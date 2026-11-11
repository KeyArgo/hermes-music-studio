"""The chorus preview: the short, cheap render that decides whether the full song is worth building."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import chorus  # noqa: E402


LYRICS = """[verse]
A line of setup
Another line

[chorus]
You are the fire in the velvet growl
The whisper underneath the wall of sound

[verse]
Second verse here
"""


class ChorusTests(unittest.TestCase):
    def test_the_chorus_is_taken_with_its_tag(self):
        block = chorus.chorus_block(LYRICS)
        self.assertTrue(block.startswith('[chorus]'), 'the engine needs the section tag')
        self.assertIn('velvet growl', block)
        self.assertNotIn('A line of setup', block, 'the verse must not leak into the preview')

    def test_a_draft_without_a_chorus_says_so(self):
        self.assertIsNone(chorus.chorus_block('[verse]\nonly a verse\n'))
        self.assertIsNone(chorus.chorus_block(''))
        self.assertIsNone(chorus.chorus_block(None))

    def test_the_match_is_case_insensitive(self):
        self.assertIn('Hook', chorus.chorus_block('[Verse]\nx\n[CHORUS]\nHook\n'))

    def test_a_preview_of_a_draft_with_no_chorus_is_refused(self):
        with patch.object(chorus.service, 'store') as store:
            store.return_value.get.return_value = {'lyrics': '[verse]\nno chorus here\n'}
            with self.assertRaises(ValueError) as raised:
                chorus.preview('p1')
        self.assertIn('no [chorus] section', str(raised.exception))

    def test_a_preview_copies_the_music_decisions_and_shortens_the_time(self):
        project = {
            'project_id': 'p1', 'title': 'Velvet Growl', 'idea': 'big love',
            'caption': 'jazz ballad, metal swells', 'lyrics': LYRICS,
            'engine': 'ace_local', 'instrumental': False, 'vocal_language': 'en',
            'bpm': 75, 'keyscale': 'D minor', 'updated_at': 10.0,
        }
        created = {'project_id': 'p2', 'updated_at': 11.0}

        def fake_create(**draft):
            fake_create.draft = draft
            return created

        with patch.object(chorus.service, 'store') as store, \
                patch.object(chorus.service, 'generate', lambda pid, seed: {'take': {'take_id': 't1'}}):
            store.return_value.get.return_value = project
            store.return_value.create.side_effect = fake_create
            result = chorus.preview('p1', 30)

        draft = fake_create.draft
        self.assertEqual(draft['caption'], 'jazz ballad, metal swells')
        self.assertEqual(draft['bpm'], 75)
        self.assertEqual(draft['keyscale'], 'D minor')
        self.assertEqual(draft['engine'], 'ace_local')
        self.assertEqual(draft['duration_seconds'], 30)
        self.assertIn('[chorus]', draft['lyrics'])
        self.assertEqual(result['preview_project_id'], 'p2')
        self.assertIsInstance(result['seed'], int)

    def test_the_preview_approval_is_bound_to_the_created_version(self):
        project = {'project_id': 'p1', 'lyrics': LYRICS, 'engine': 'ace',
                   'title': 'x', 'caption': 'c', 'updated_at': 1.0}
        created = {'project_id': 'p2', 'updated_at': 2.0}
        with patch.object(chorus.service, 'store') as store, \
                patch.object(chorus.service, 'generate', lambda pid, seed: {'take': {}}):
            store.return_value.get.return_value = project
            store.return_value.create.return_value = created
            chorus.preview('p1')
            store.return_value.approve.assert_called_once_with('p2', base_updated_at=2.0)

    def test_an_absurd_preview_length_is_refused(self):
        with patch.object(chorus.service, 'store') as store:
            store.return_value.get.return_value = {'lyrics': LYRICS}
            for bad in (5, 600):
                with self.assertRaises(ValueError):
                    chorus.preview('p1', bad)


if __name__ == '__main__':
    unittest.main()
