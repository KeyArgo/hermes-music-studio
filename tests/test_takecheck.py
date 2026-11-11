"""Did the take do what was asked? Facts where facts exist, `unknown` where they do not.

The failure this guards against is the quiet one: a change that never reached the engine's caption,
or a tempo that came back as the engine's default, while the take still says "succeeded".
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import takecheck  # noqa: E402


def project(**over):
    base = {
        'project_id': 'p1',
        'caption': 'folk, warm, fiddle',
        'memory': {'events': [
            {'kind': 'change', 'text': 'add a fiddle', 'patch': {'caption': 'folk, warm, fiddle'}},
        ]},
        'takes': [],
    }
    base.update(over)
    return base


def take(**over):
    base = {
        'take_id': 't1', 'state': 'succeeded', 'engine_id': 'ace_local',
        'snapshot': {'caption': 'folk, warm, fiddle', 'bpm': 96, 'duration_seconds': 30,
                     'instrumental': False},
        'qc': {'requested_s': 30.0, 'file_s': 30.0, 'peak_dbfs': -1.0, 'clipped_frames': 0,
               'tail_silence_s': 1.2, 'vocal_presence': 'unknown',
               'vocal_evidence': {'status': 'unknown', 'detail': 'no ASR pass has run on this take'}},
    }
    base.update(over)
    return base


class CheckTests(unittest.TestCase):
    def test_a_take_that_carries_the_request_passes(self):
        rows = takecheck.checks(project(), take())
        state, sentence = takecheck.summary(rows)
        self.assertEqual(state, 'passed', sentence)

    def test_a_request_that_never_reached_the_caption_fails_and_names_itself(self):
        rows = takecheck.checks(project(), take(snapshot={'caption': 'folk, warm', 'bpm': 96,
                                                          'duration_seconds': 30}))
        words = [r for r in rows if r['name'] == 'the words reached the engine'][0]
        self.assertFalse(words['ok'])
        self.assertIn('fiddle', words['detail'])

    def test_the_engine_default_tempo_is_not_the_tempo_that_was_set(self):
        # The tempo is a request too, so it is in the journal like any other.
        asked = project(memory={'events': [
            {'kind': 'change', 'text': 'set the tempo to 96', 'patch': {'bpm': 96}}]})
        rows = takecheck.checks(asked, take(snapshot={'caption': 'folk, fiddle', 'bpm': None,
                                                      'duration_seconds': 30}))
        tempo = [r for r in rows if r['name'].startswith('tempo')][0]
        self.assertFalse(tempo['ok'])
        self.assertIn('engine default', tempo['detail'])

    def test_a_short_file_fails_the_length_check(self):
        rows = takecheck.checks(project(), take(qc={'requested_s': 30.0, 'file_s': 12.0,
                                                    'peak_dbfs': -1.0, 'clipped_frames': 0}))
        length = [r for r in rows if r['name'].startswith('length')][0]
        self.assertFalse(length['ok'])

    def test_a_clipped_file_fails(self):
        rows = takecheck.checks(project(), take(qc={'requested_s': 30.0, 'file_s': 30.0,
                                                    'peak_dbfs': 0.0, 'clipped_frames': 41}))
        clipping = [r for r in rows if r['name'] == 'no clipping'][0]
        self.assertFalse(clipping['ok'])

    def test_a_long_silent_tail_fails(self):
        rows = takecheck.checks(project(), take(qc={'requested_s': 30.0, 'file_s': 30.0,
                                                    'peak_dbfs': -1.0, 'clipped_frames': 0,
                                                    'tail_silence_s': 9.0}))
        tail = [r for r in rows if r['name'].startswith('does not end')][0]
        self.assertFalse(tail['ok'])

    def test_instrumental_without_asr_evidence_is_unknown_not_a_pass(self):
        rows = takecheck.checks(project(), take(snapshot={'caption': 'folk', 'bpm': 96,
                                                          'duration_seconds': 30, 'instrumental': True}))
        vocal = [r for r in rows if r['name'].startswith('instrumental')][0]
        self.assertIsNone(vocal['ok'], 'no ASR ran, so the honest answer is "not measured"')

    def test_unknown_checks_do_not_turn_an_empty_report_into_a_pass(self):
        rows = [{'name': 'instrumental stayed instrumental', 'ok': None, 'detail': 'no ASR'}]
        state, sentence = takecheck.summary(rows)
        self.assertEqual(state, 'unknown')
        self.assertIn('without a model', sentence)


class RetryTests(unittest.TestCase):
    class Store:
        def __init__(self, data):
            self.data = data

        def get(self, project_id):
            return self.data

    def test_nothing_measurable_failed_means_no_gpu_minutes_are_spent(self):
        generate = mock.Mock()
        result = takecheck.retry('p1', 't1', self.Store(project(takes=[take()])), generate)
        self.assertFalse(result['retried'])
        generate.assert_not_called()

    def test_a_failed_fact_queues_a_new_take_with_a_fresh_seed(self):
        generate = mock.Mock(return_value={'success': True, 'take': {'take_id': 't9'}})
        data = project(takes=[take(snapshot={'caption': 'folk, warm', 'bpm': 96,
                                             'duration_seconds': 30})])

        result = takecheck.retry('p1', 't1', self.Store(data), generate)

        self.assertTrue(result['retried'])
        self.assertEqual(result['take'], 't9')
        self.assertIn('the words reached the engine', result['because'])
        args, _ = generate.call_args
        self.assertEqual(args[0], 'p1')
        self.assertIsInstance(args[1], int)
        self.assertTrue(1 <= args[1] < 2 ** 31)

    def test_a_missing_take_is_a_sentence_not_a_crash(self):
        with self.assertRaises(KeyError):
            takecheck.retry('p1', 'nope', self.Store(project(takes=[])), mock.Mock())


if __name__ == '__main__':
    unittest.main()
