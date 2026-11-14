"""Audio QC gate (NIGHT-SHIFT T5, corrected by four-model review).

The gate must be honest about three things a render cannot promise:

* **Length** — a requested 15 s take really renders ~11.7 s of music plus a
  3.0-3.3 s dead tail. ``requested_s``, ``file_s`` and ``musical_end_s`` are
  stored separately and never conflated.
* **Tail handling** — trimming is a named, reversible edit with its own id that
  references the untouched raw WAV bytes. The raw take is never rewritten.
* **Vocals** — no band-modulation heuristic may claim vocals. The field is
  tri-state and defaults to ``unknown`` until a real ASR pass exists.

These tests use synthetic tones so every number is arithmetic, not a render.
"""
import hashlib
import io
import logging
import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from fake_ace import make_runtime, pin_runtime  # noqa: E402
from music_studio import qc  # noqa: E402
from music_studio.jobs import Jobs  # noqa: E402
from music_studio.store import Store  # noqa: E402

RATE = 8000


def wav_bytes(samples, rate=RATE, channels=1, width=2):
    """Pack float samples (-1..1) into a WAV byte string."""
    limit = 2 ** (8 * width - 1) - 1
    frames = bytearray()
    for value in samples:
        clipped = max(-1.0, min(1.0, value))
        if width == 1:
            sample = bytes([int(round(clipped * 127 + 128)) & 0xFF])
        else:
            sample = struct.pack('<h' if width == 2 else '<i',
                                 int(round(clipped * limit)))
        frames += sample * channels
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setparams((channels, width, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(bytes(frames))
    return buf.getvalue()


def pcm8_wav(values, rate=RATE, channels=1):
    """A WAV whose sample bytes are given verbatim (8-bit PCM is unsigned)."""
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setparams((channels, 1, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(bytes(values))
    return buf.getvalue()


def sine_then_silence(music_s, total_s, amplitude=0.5, frequency=220.0, rate=RATE):
    """A tone for ``music_s`` seconds followed by digital silence."""
    music = int(round(music_s * rate))
    total = int(round(total_s * rate))
    samples = [amplitude * math.sin(2 * math.pi * frequency * i / rate)
               for i in range(music)]
    samples.extend([0.0] * (total - music))
    return wav_bytes(samples, rate=rate)


def decode(data):
    with wave.open(io.BytesIO(data), 'rb') as wav:
        raw = wav.readframes(wav.getnframes())
        return struct.unpack('<%dh' % (len(raw) // 2), raw)


def duration(data):
    with wave.open(io.BytesIO(data), 'rb') as wav:
        return wav.getnframes() / wav.getframerate()


def rms(values):
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


class AnalysisTests(unittest.TestCase):
    def test_measures_the_real_dead_tail(self):
        # The measured shape of a real 15 s take: ~3.0-3.3 s of dead tail.
        report = qc.analyse_wav(sine_then_silence(11.7, 15.0))
        self.assertAlmostEqual(report['file_s'], 15.0, places=3)
        self.assertAlmostEqual(report['musical_end_s'], 11.7, delta=0.02)
        self.assertGreaterEqual(report['tail_silence_s'], 3.0)
        self.assertLessEqual(report['tail_silence_s'], 3.35)
        # ~20% of the file is dead air; that ratio is what the UI explains.
        self.assertAlmostEqual(report['tail_silence_s'] / report['file_s'], 0.22,
                               delta=0.02)

    def test_requested_length_is_not_derived_from_the_file(self):
        # request/real divergence is normal: the two numbers stay independent.
        report = qc.analyse_wav(sine_then_silence(11.7, 15.0))
        take_qc = qc.take_qc(report, requested_s=15.0)
        self.assertEqual(take_qc['requested_s'], 15.0)
        self.assertAlmostEqual(take_qc['file_s'], 15.0, places=3)
        self.assertNotEqual(take_qc['requested_s'], take_qc['musical_end_s'])

    def test_peak_and_clipped_frames_on_a_full_scale_tone(self):
        samples = [1.0 if i % 2 else -1.0 for i in range(RATE)]
        report = qc.analyse_wav(wav_bytes(samples))
        self.assertAlmostEqual(report['peak_dbfs'], 0.0, delta=0.01)
        self.assertGreater(report['clipped_frames'], 0)
        self.assertAlmostEqual(report['file_s'], 1.0, places=3)

    def test_quiet_take_has_no_clipping_and_no_tail(self):
        samples = [0.5 * math.sin(2 * math.pi * 220 * i / RATE) for i in range(RATE)]
        report = qc.analyse_wav(wav_bytes(samples))
        self.assertEqual(report['clipped_frames'], 0)
        self.assertLess(report['tail_silence_s'], 0.05)
        self.assertGreater(report['peak_dbfs'], -10.0)

    def test_digital_silence_has_no_musical_content(self):
        report = qc.analyse_wav(wav_bytes([0.0] * RATE))
        self.assertEqual(report['musical_end_s'], 0.0)
        self.assertAlmostEqual(report['tail_silence_s'], 1.0, places=3)
        self.assertLess(report['peak_dbfs'], -90.0)


class PlanTests(unittest.TestCase):
    def test_tail_is_trimmed_with_a_bounded_fade(self):
        report = qc.analyse_wav(sine_then_silence(11.7, 15.0))
        plan = qc.plan_edit(report)
        self.assertIsNotNone(plan)
        self.assertEqual(plan['kind'], 'trim')
        self.assertAlmostEqual(plan['trim_end_s'], report['musical_end_s'], delta=0.02)
        self.assertGreaterEqual(plan['fade_ms'], qc.FADE_MS_MIN)
        self.assertLessEqual(plan['fade_ms'], qc.FADE_MS_MAX)

    def test_no_tail_plan_for_a_take_that_ends_on_the_beat(self):
        samples = [0.5 * math.sin(2 * math.pi * 220 * i / RATE) for i in range(RATE)]
        self.assertIsNone(qc.plan_edit(qc.analyse_wav(wav_bytes(samples))))

    def test_over_peak_take_gets_a_cap_plan_even_without_a_tail(self):
        samples = [1.0 if i % 2 else -1.0 for i in range(RATE)]
        plan = qc.plan_edit(qc.analyse_wav(wav_bytes(samples)))
        self.assertIsNotNone(plan)
        self.assertEqual(plan['kind'], 'peak_cap')
        self.assertAlmostEqual(plan['peak_cap_dbfs'], -1.0)
        self.assertLess(plan['gain_db'], 0.0)

    def test_a_trim_with_a_peak_cap_is_recorded_as_both(self):
        # A clipped take with a dead tail needs two transforms; crediting only
        # the trim misstates what the stored file is.
        data = sine_then_silence(1.0, 3.0, amplitude=1.0)
        plan = qc.plan_edit(qc.analyse_wav(data))
        self.assertIsNotNone(plan)
        assert plan is not None  # narrowing for the assertions below
        self.assertIsNotNone(plan['trim_end_s'])
        self.assertLess(plan['gain_db'], 0.0)
        self.assertEqual(plan['kind'], 'trim+peak_cap')

    def test_fade_length_is_validated(self):
        report = qc.analyse_wav(sine_then_silence(1.0, 2.0))
        for bad in (0, 39, 151, -10, '80', 80.5):
            with self.assertRaises((ValueError, TypeError)):
                qc.plan_edit(report, fade_ms=bad)


class RenderTests(unittest.TestCase):
    def test_trim_shortens_the_file_and_fades_the_cut(self):
        data = sine_then_silence(11.7, 15.0)
        report = qc.analyse_wav(data)
        trimmed = qc.render_edit(data, qc.plan_edit(report))
        self.assertLess(duration(trimmed), 12.0)
        self.assertAlmostEqual(duration(trimmed), report['musical_end_s'], delta=0.02)
        # The final milliseconds are faded out, not chopped.
        values = decode(trimmed)
        tail = rms(values[-int(0.005 * RATE):])
        body = rms(values[-int(0.085 * RATE):-int(0.045 * RATE)])
        self.assertLess(tail, body * 0.2)

    def test_peak_cap_lands_at_minus_one_dbfs_and_removes_clipping(self):
        data = wav_bytes([1.0 if i % 2 else -1.0 for i in range(RATE)])
        report = qc.analyse_wav(data)
        capped = qc.render_edit(data, qc.plan_edit(report))
        after = qc.analyse_wav(capped)
        self.assertLessEqual(after['peak_dbfs'], -1.0 + 0.05)
        self.assertGreater(after['peak_dbfs'], -1.6)
        self.assertEqual(after['clipped_frames'], 0)

    def test_render_edit_is_pure(self):
        data = sine_then_silence(1.0, 2.0)
        before = bytes(data)
        plan = qc.plan_edit(qc.analyse_wav(data))
        qc.render_edit(data, plan)
        self.assertEqual(data, before)


class EightBitPcmTests(unittest.TestCase):
    def test_eight_bit_silence_is_measured_as_silence(self):
        report = qc.analyse_wav(pcm8_wav([0x80] * RATE))
        self.assertEqual(report['peak_dbfs'], qc.SILENCE_FLOOR_DBFS)
        self.assertEqual(report['musical_end_s'], 0.0)
        self.assertEqual(report['clipped_frames'], 0)

    def test_eight_bit_full_scale_pair_reads_as_plus_and_minus_full_scale(self):
        report = qc.analyse_wav(pcm8_wav([0x00, 0xFF] * (RATE // 2)))
        self.assertAlmostEqual(report['peak_dbfs'], 0.0, delta=0.01)
        self.assertEqual(report['clipped_frames'], RATE)

    def test_eight_bit_samples_survive_the_edit_writer_unchanged(self):
        raw = bytes([0x80, 0x00, 0xFF, 0x7F] * (RATE // 4))
        edited = qc.render_edit(pcm8_wav(raw), {})
        with wave.open(io.BytesIO(edited), 'rb') as wav:
            self.assertEqual(wav.getsampwidth(), 1)
            self.assertEqual(wav.readframes(wav.getnframes()), raw)


class VocalEvidenceTests(unittest.TestCase):
    def test_default_is_unknown(self):
        evidence = qc.vocal_evidence()
        self.assertEqual(evidence['status'], 'unknown')
        self.assertEqual(evidence['source'], 'none')
        self.assertIn('no asr', evidence['detail'].lower())

    def test_unknown_without_an_asr_pass(self):
        take_qc = qc.take_qc(qc.analyse_wav(sine_then_silence(1.0, 2.0)),
                             requested_s=2.0)
        self.assertEqual(take_qc['vocal_presence'], 'unknown')

    def test_never_claims_confirmed_from_band_modulation(self):
        # A modulation score of any value is a diagnostic, never a badge.
        self.assertFalse(hasattr(qc, 'modulation_score'))
        evidence = qc.vocal_evidence(modulation_dbfs=12.0)
        self.assertEqual(evidence['status'], 'unknown')
        self.assertAlmostEqual(evidence['modulation_dbfs'], 12.0)

    def test_confirmed_requires_a_real_asr_result(self):
        with self.assertRaises(ValueError):
            qc.vocal_evidence(asr={'words_recognised': 7, 'total': 9})
        with self.assertRaises(ValueError):
            qc.vocal_evidence(asr={'words_recognised': 9, 'total': 9,
                                   'run_id': '', 'model': 'whisper'})
        evidence = qc.vocal_evidence(asr={'words_recognised': 9, 'total': 9,
                                          'run_id': 'asr-1', 'model': 'whisper'})
        self.assertEqual(evidence['status'], 'confirmed')

    def test_low_recovery_is_not_confirmed(self):
        evidence = qc.vocal_evidence(asr={'words_recognised': 2, 'total': 9,
                                          'run_id': 'asr-2', 'model': 'whisper'})
        self.assertEqual(evidence['status'], 'not_confirmed')


class ValidateTakeQcTests(unittest.TestCase):
    """What is persisted must be derived, never accepted from the caller."""

    def _record(self, **overrides):
        record = {'requested_s': 15.0, 'file_s': 15.0, 'musical_end_s': 11.7,
                  'tail_silence_s': 3.3, 'peak_dbfs': -3.0, 'clipped_frames': 0}
        record.update(overrides)
        return record

    def _asr(self):
        return {'words_recognised': 9, 'total': 9, 'run_id': 'asr-7',
                'model': 'whisper-large-v3'}

    def test_confirmed_without_asr_evidence_is_refused(self):
        with self.assertRaises(ValueError):
            qc.validate_take_qc(self._record(vocal_presence='confirmed'))
        with self.assertRaises(ValueError):
            qc.validate_take_qc(self._record(
                vocal_presence='confirmed', vocal_evidence=qc.vocal_evidence()))

    def test_presence_is_derived_from_the_evidence(self):
        clean = qc.validate_take_qc(self._record(
            vocal_evidence=qc.vocal_evidence(asr=self._asr())))
        self.assertEqual(clean['vocal_presence'], 'confirmed')
        self.assertEqual(clean['vocal_evidence']['source'], 'asr')
        self.assertEqual(clean['vocal_evidence']['run_id'], 'asr-7')

    def test_a_verdict_that_contradicts_its_evidence_is_refused(self):
        with self.assertRaises(ValueError):
            qc.validate_take_qc(self._record(
                vocal_presence='not_confirmed', vocal_evidence=qc.vocal_evidence()))

    def test_a_forged_evidence_block_cannot_claim_confirmed(self):
        clean = qc.validate_take_qc(self._record(
            vocal_evidence={'status': 'confirmed', 'source': 'none'}))
        self.assertEqual(clean['vocal_presence'], 'unknown')
        self.assertEqual(clean['vocal_evidence']['status'], 'unknown')


class TakeEditTests(unittest.TestCase):
    """The raw take must survive a trim byte-for-byte, as a named edit."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        pid = self.store.create(title='A', caption='music', lyrics='words')['project_id']
        self.store.approve(pid)
        self.jobs = Jobs(self.store)
        self.pid = pid
        self.data = sine_then_silence(3.0, 4.0)
        take = self.jobs.enqueue(pid, seed=7)
        self.jobs.start(pid, take['take_id'])
        self.take_id = take['take_id']
        self.jobs.succeed(pid, self.take_id, self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def _take(self):
        return self.store.get(self.pid)['takes'][0]

    def test_raw_bytes_are_never_rewritten(self):
        take = self._take()
        raw_path = self.store.root / take['audio_file']
        before = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        self.jobs.record_qc(self.pid, self.take_id,
                            qc.take_qc(qc.analyse_wav(self.data), requested_s=4.0))
        edit = self.jobs.apply_qc_edit(self.pid, self.take_id)
        after = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(len(self.data), len(raw_path.read_bytes()))
        take = self._take()
        self.assertEqual(take['audio_file'], self.take_id + '.wav')
        self.assertNotEqual(edit['audio_file'], take['audio_file'])
        self.assertEqual(edit['source_sha256'], before)

    def test_edit_has_its_own_id_and_references_the_raw_take(self):
        self.jobs.record_qc(self.pid, self.take_id,
                            qc.take_qc(qc.analyse_wav(self.data), requested_s=4.0))
        edit = self.jobs.apply_qc_edit(self.pid, self.take_id)
        take = self._take()
        self.assertEqual([e['edit_id'] for e in take['edits']], [edit['edit_id']])
        self.assertNotEqual(edit['edit_id'], self.take_id)
        self.assertEqual(edit['source_take_id'], self.take_id)
        self.assertEqual(edit['kind'], 'trim')
        self.assertIn('fade_ms', edit['transform'])
        self.assertGreaterEqual(edit['transform']['fade_ms'], qc.FADE_MS_MIN)
        self.assertLessEqual(edit['transform']['fade_ms'], qc.FADE_MS_MAX)
        self.assertEqual(take['playable_edit_id'], edit['edit_id'])

    def test_edit_reports_its_own_duration_not_the_requested_one(self):
        self.jobs.record_qc(self.pid, self.take_id,
                            qc.take_qc(qc.analyse_wav(self.data), requested_s=4.0))
        edit = self.jobs.apply_qc_edit(self.pid, self.take_id)
        take = self._take()
        self.assertAlmostEqual(take['qc']['requested_s'], 4.0)
        self.assertAlmostEqual(take['qc']['file_s'], 4.0, places=3)
        self.assertAlmostEqual(edit['duration_s'], 3.0, delta=0.02)
        self.assertLess(edit['duration_s'], take['qc']['requested_s'])

    def test_edit_audio_is_served_by_edit_id(self):
        self.jobs.record_qc(self.pid, self.take_id,
                            qc.take_qc(qc.analyse_wav(self.data), requested_s=4.0))
        edit = self.jobs.apply_qc_edit(self.pid, self.take_id)
        with self.jobs.open_audio(self.pid, self.take_id, edit_id=edit['edit_id']) as f:
            served = f.read()
        self.assertAlmostEqual(duration(served), edit['duration_s'], delta=0.02)
        with self.jobs.open_audio(self.pid, self.take_id) as f:
            self.assertEqual(f.read(), self.data)

    def test_no_edit_when_there_is_nothing_to_fix(self):
        samples = [0.4 * math.sin(2 * math.pi * 220 * i / RATE) for i in range(RATE)]
        pid = self.store.create(title='B', caption='music', lyrics='words')['project_id']
        self.store.approve(pid)
        take = self.jobs.enqueue(pid, seed=1)
        self.jobs.start(pid, take['take_id'])
        self.jobs.succeed(pid, take['take_id'], wav_bytes(samples))
        self.jobs.record_qc(pid, take['take_id'],
                            qc.take_qc(qc.analyse_wav(wav_bytes(samples)), requested_s=1.0))
        self.assertIsNone(self.jobs.apply_qc_edit(pid, take['take_id']))

    def test_qc_failure_never_fails_a_succeeded_take(self):
        take = self._take()
        with self.assertRaises(ValueError):
            self.jobs.record_qc(self.pid, self.take_id, {'requested_s': float('nan')})
        self.assertEqual(self._take()['state'], 'succeeded')
        self.assertEqual(self._take()['audio_file'], take['audio_file'])

    def test_vocal_presence_is_unknown_on_a_stored_take(self):
        self.jobs.record_qc(self.pid, self.take_id,
                            qc.take_qc(qc.analyse_wav(self.data), requested_s=4.0))
        self.assertEqual(self._take()['qc']['vocal_presence'], 'unknown')


class _LogCapture(logging.Handler):
    """Keep the worker's own log lines so a missing QC edit fails loudly."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []
        self._format = logging.Formatter('%(levelname)s %(name)s: %(message)s')

    def emit(self, record):
        self.lines.append(self._format.format(record))
        if record.exc_info:
            self.lines.append(logging.Formatter().formatException(record.exc_info))


class WorkerGateTests(unittest.TestCase):
    """The gate must run on the real worker path, after the take is committed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime_dir = tempfile.TemporaryDirectory()
        self.runtime = make_runtime(self.runtime_dir.name)
        # _render_take runs in *this* process, where ace_engine's runtime paths
        # were frozen at import. Pinning them to the fixture runtime makes the
        # registry see ace_local on every host, including one with no ACE
        # install — otherwise the render is refused before QC ever runs.
        self.pinned = pin_runtime(self.runtime)
        self.log_capture = _LogCapture()
        logging.getLogger().addHandler(self.log_capture)
        self.store = Store(self.tmp.name)
        pid = self.store.create(title='A', caption='music', lyrics='words',
                                duration_seconds=15)['project_id']
        self.store.approve(pid)
        self.jobs = Jobs(self.store)
        self.pid = pid

    def tearDown(self):
        logging.getLogger().removeHandler(self.log_capture)
        self.pinned.close()
        self.runtime_dir.cleanup()
        self.tmp.cleanup()

    def _edit(self, take):
        """The take's single QC edit, or a failure that says why there is none."""
        edits = take.get('edits') or []
        if not edits:
            self.fail('the audio QC gate recorded no edit '
                      '(state={!r}, qc={!r}); worker log:\n{}'.format(
                          take.get('state'), take.get('qc'),
                          '\n'.join(self.log_capture.lines) or '(no log lines)'))
        return edits[0]

    def _render_with(self, data):
        from unittest.mock import patch
        from music_studio import worker
        take = self.jobs.enqueue(self.pid, seed=3)
        with patch.object(worker.ace_engine, 'generate', return_value=data):
            code, _ = worker._render_take(self.jobs, self.store, self.pid,
                                          take['take_id'])
        return code, self.store.get(self.pid)['takes'][0]

    def test_worker_records_qc_and_a_reversible_edit(self):
        code, take = self._render_with(sine_then_silence(11.7, 15.0))
        self.assertEqual(code, 0, 'worker failed: state={!r} error={!r}'.format(
            take.get('state'), take.get('error')))
        self.assertEqual(take['state'], 'succeeded')
        self.assertEqual(take['qc']['requested_s'], 15)
        self.assertAlmostEqual(take['qc']['file_s'], 15.0, places=3)
        self.assertGreaterEqual(take['qc']['tail_silence_s'], 3.0)
        self.assertGreaterEqual(take['qc']['tail_silence_s'], 0.2 * take['qc']['file_s'] - 0.05)
        self.assertEqual(take['qc']['vocal_presence'], 'unknown')
        self.assertEqual(len(take['edits']), 1)
        edit = self._edit(take)
        self.assertEqual(edit['kind'], 'trim')
        self.assertAlmostEqual(edit['duration_s'], 11.7, delta=0.05)
        self.assertEqual(take['playable_edit_id'], edit['edit_id'])
        # Raw file survives exactly as rendered.
        raw = (self.store.root / take['audio_file']).read_bytes()
        self.assertAlmostEqual(duration(raw), 15.0, places=3)

    def test_peaks_are_served_for_the_edit_at_its_own_length(self):
        from music_studio.peaks import PEAKS_MIN_BUCKETS, peaks_for
        _, take = self._render_with(sine_then_silence(11.7, 15.0))
        edit = self._edit(take)
        raw = peaks_for(self.jobs, self.pid, take['take_id'], PEAKS_MIN_BUCKETS)
        edit_peaks = peaks_for(self.jobs, self.pid, take['take_id'], PEAKS_MIN_BUCKETS,
                               edit_id=edit['edit_id'])
        self.assertAlmostEqual(raw['duration_seconds'], 15.0, delta=0.05)
        self.assertAlmostEqual(edit_peaks['duration_seconds'], 11.7, delta=0.1)

    def test_success_is_committed_together_with_its_edit(self):
        """A client that stops polling at 'succeeded' must already have the edit.

        The spy reads the project file after every store save: every snapshot
        that shows a succeeded take is a state a polling client can observe, so
        each one must already carry the playable edit (and its WAV).
        """
        observed = []
        original = self.store._save

        def spy(project):
            original(project)
            for take in self.store.get(project['project_id'])['takes']:
                if take['state'] == 'succeeded':
                    observed.append((take['take_id'],
                                     take.get('playable_edit_id'),
                                     list(take.get('edits') or []),
                                     take.get('qc')))
        self.store._save = spy
        try:
            code, take = self._render_with(sine_then_silence(11.7, 15.0))
        finally:
            del self.store._save
        self.assertEqual(code, 0, take.get('error'))
        self.assertTrue(observed, 'the take never reached succeeded')
        edits = [entry for entry in observed if entry[2]]
        self.assertTrue(edits, 'no succeeded snapshot carried an edit; '
                               'success was published before QC settled')
        edit = self._edit(take)
        for take_id, playable, take_edits, qc in observed:
            self.assertEqual(playable, edit['edit_id'])
            self.assertTrue((self.store.root / (playable + '.wav')).exists())
            self.assertEqual(take_edits[0]['edit_id'], edit['edit_id'])
            self.assertIsNotNone(qc)
        # The raw render is still byte-for-byte what the engine produced.
        raw = (self.store.root / take['audio_file']).read_bytes()
        self.assertAlmostEqual(duration(raw), 15.0, places=3)

    def test_delete_removes_the_edit_file_too(self):
        _, take = self._render_with(sine_then_silence(11.7, 15.0))
        edit_file = self.store.root / self._edit(take)['audio_file']
        self.assertTrue(edit_file.exists())
        self.store.delete(self.pid)
        self.assertFalse(edit_file.exists())
        self.assertFalse((self.store.root / take['audio_file']).exists())


if __name__ == '__main__':
    unittest.main()
