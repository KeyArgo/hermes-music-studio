"""Sections: the editable song structure, validated, snapshotted and derived."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.plugin_api import router  # noqa: E402
from music_studio import service  # noqa: E402
from music_studio.jobs import Jobs  # noqa: E402
from music_studio.sections import (MAX_SECTIONS, SECTION_TAGS,  # noqa: E402
                                   derive_sections, validate_sections)
from music_studio.store import Store  # noqa: E402


def section(index, tag, start, end, label=None, lines=None):
    return {'id': f'sec-{index}', 'tag': tag, 'label': label or tag.title(),
            'start_s': start, 'end_s': end,
            'lyric_lines': list(lines or [f'line {index}'])}


def two_sections(duration=30):
    return [section(1, 'verse', 0, duration / 2),
            section(2, 'chorus', duration / 2, duration)]


class SectionValidationTests(unittest.TestCase):
    def test_valid_contiguous_sections_cover_the_duration(self):
        validate_sections(two_sections(30), 30)
        validate_sections([], 30)
        validate_sections([section(1, 'custom', 0, 30)], 30)

    def test_every_documented_tag_is_accepted(self):
        self.assertEqual(set(SECTION_TAGS),
                         {'intro', 'verse', 'prechorus', 'chorus', 'bridge',
                          'outro', 'instrumental', 'custom'})
        for tag in SECTION_TAGS:
            with self.subTest(tag=tag):
                validate_sections([section(1, tag, 0, 30)], 30)

    def test_gap_overlap_out_of_range_and_bad_tags_are_refused(self):
        bad = {
            'gap': [section(1, 'verse', 0, 10), section(2, 'chorus', 12, 30)],
            'overlap': [section(1, 'verse', 0, 16), section(2, 'chorus', 15, 30)],
            'past_duration': [section(1, 'verse', 0, 10), section(2, 'chorus', 10, 30.5)],
            'starts_late': [section(1, 'verse', 2, 30)],
            'unordered': [section(1, 'chorus', 15, 30), section(2, 'verse', 0, 15)],
            'bad_tag': [section(1, 'guitar-solo', 0, 30)],
            'zero_length': [section(1, 'verse', 5, 5)],
        }
        for name, sections in bad.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_sections(sections, 30)

    def test_structural_problems_are_refused(self):
        base = section(1, 'verse', 0, 30)
        bad = [
            'not-a-list',
            [base, section(1, 'chorus', 15, 30)],           # duplicate id
            [{k: v for k, v in base.items() if k != 'id'}],  # missing id
            [{**base, 'unknown': 1}],
            [{**base, 'label': 7}],
            [{**base, 'lyric_lines': 'not a list'}],
            [{**base, 'lyric_lines': [1, 2]}],
            [{**base, 'start_s': True}],
            [{**base, 'end_s': '30'}],
            [section(i, 'verse', 0, 30) for i in range(1, MAX_SECTIONS + 2)],
        ]
        for case in bad:
            with self.subTest(case=case), self.assertRaises(ValueError):
                validate_sections(case, 30)

    def test_derives_contiguous_sections_from_bracket_lyrics(self):
        lyrics = ('[intro]\nsoft air\n[verse 1]\nfirst line\nsecond line\n'
                  '[chorus]\nloud now\n[outro]\nend')
        sections = derive_sections(lyrics, 40)
        self.assertEqual([s['tag'] for s in sections],
                         ['intro', 'verse', 'chorus', 'outro'])
        self.assertEqual(sections[0]['start_s'], 0)
        self.assertEqual(sections[-1]['end_s'], 40)
        for left, right in zip(sections, sections[1:]):
            self.assertEqual(left['end_s'], right['start_s'])
        self.assertEqual(sections[1]['lyric_lines'], ['first line', 'second line'])
        validate_sections(sections, 40)

    def test_derivation_respects_the_section_cap(self):
        lyrics = '\n'.join(f'[verse {i}]\nline' for i in range(1, MAX_SECTIONS + 8))
        sections = derive_sections(lyrics, 240)
        self.assertLessEqual(len(sections), MAX_SECTIONS)
        validate_sections(sections, 240)

    def test_no_headers_yields_no_invented_sections(self):
        self.assertEqual(derive_sections('just words\nno headers', 30), [])

    def test_store_refuses_invalid_sections_and_revokes_approval_on_change(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            project = store.create(caption='music', lyrics='words', duration_seconds=30,
                                   sections=two_sections(30))
            self.assertEqual(len(project['sections']), 2)
            store.approve(project['project_id'])
            changed = [section(1, 'intro', 0, 30)]
            project = store.update(project['project_id'], sections=changed)
            self.assertFalse(project['approved'])
            with self.assertRaises(ValueError):
                store.update(project['project_id'],
                             sections=[section(1, 'verse', 0, 10)])
            # Updating the duration below the sections' end is refused too.
            store.approve(project['project_id'])
            with self.assertRaises(ValueError):
                store.update(project['project_id'], duration_seconds=20)


class SectionSnapshotAndStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.env.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.store = service.store()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_take_snapshot_is_a_deep_copy_of_the_sections(self):
        project = self.store.create(caption='music', lyrics='words', duration_seconds=30,
                                    sections=two_sections(30))
        pid = project['project_id']
        self.store.approve(pid)
        take = Jobs(self.store).enqueue(pid, 1)
        self.assertEqual(take['snapshot']['sections'], two_sections(30))
        replacement = [section(1, 'intro', 0, 30)]
        self.store.update(pid, sections=replacement)
        self.assertEqual(self.store.get(pid)['takes'][0]['snapshot']['sections'],
                         two_sections(30))
        # Deep copies: mutating the project list cannot reach the snapshot.
        stored = self.store.get(pid)
        stored['sections'][0]['label'] = 'mutated'
        self.store._save(stored)
        self.assertEqual(self.store.get(pid)['takes'][0]['snapshot']['sections'][0]['label'],
                         'Verse')

    def test_status_exposes_stored_sections(self):
        project = self.store.create(caption='music', lyrics='words', duration_seconds=30,
                                    sections=two_sections(30))
        body = self.client.get('/status').json()
        exposed = next(p for p in body['projects'] if p['project_id'] == project['project_id'])
        self.assertEqual(exposed['sections_source'], 'stored')
        self.assertEqual(exposed['sections'], two_sections(30))

    def test_status_derives_sections_when_a_project_has_none(self):
        project = self.store.create(caption='music', duration_seconds=40,
                                    lyrics='[verse]\none\n[chorus]\ntwo')
        body = self.client.get('/status').json()
        exposed = next(p for p in body['projects'] if p['project_id'] == project['project_id'])
        self.assertEqual(exposed['sections_source'], 'lyrics')
        self.assertEqual([s['tag'] for s in exposed['sections']], ['verse', 'chorus'])
        self.assertEqual(exposed['sections'][-1]['end_s'], 40)

    def test_api_update_rejects_sections_that_no_longer_fit(self):
        project = self.store.create(caption='music', lyrics='words', duration_seconds=30,
                                    sections=two_sections(30))
        pid = project['project_id']
        response = self.client.post('/update', json={
            'project_id': pid, 'draft': {'duration_seconds': 20}})
        self.assertEqual(response.status_code, 400)
        self.assertIn('section', response.json()['detail'].lower())
        self.assertEqual(self.store.get(pid)['duration_seconds'], 30)

    def test_api_rejects_invalid_sections_as_a_400(self):
        response = self.client.post('/create', json={'draft': {
            'caption': 'music', 'lyrics': 'words', 'duration_seconds': 30,
            'sections': [section(1, 'verse', 0, 10)]}})
        self.assertEqual(response.status_code, 400)
        self.assertIn('section', response.json()['detail'].lower())


if __name__ == '__main__':
    unittest.main()
