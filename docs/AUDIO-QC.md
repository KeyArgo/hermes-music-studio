# Audio QC gate

What the measurement gate records, what it may edit, and what it refuses to claim about sound.

Applies to 1.0.0 · revised 2026-11-11 · back to [docs/README.md](README.md).

Every take is measured once, from the exact bytes the engine returned, before
it is committed. QC is metadata: if the gate fails, the take still succeeds and
simply carries no measurements. It never rewrites or replaces the audio a
render produced.

The gate is `worker._settle_qc()` → `qc.analyse_wav()` / `qc.take_qc()` /
`qc.plan_edit()` / `qc.render_edit()` → `Jobs.succeed(qc_record=…, edit=…)`.
`succeed()` writes the raw WAV, writes the edit beside it and flips the take to
`state='succeeded'` in **one locked save**, so the terminal transition already
carries its edit. A client that stops polling at `succeeded` never has to wait
for a follow-up `edits[]` entry. Failures are logged and never fail the take
(`music_studio/worker.py`).

`Jobs.record_qc()` and `Jobs.apply_qc_edit()` remain for a take that is already
succeeded (a legacy take, or a caller asking for a different fade); a fresh
render settles atomically through `succeed()`.

## Three lengths, never conflated

| field | meaning |
| --- | --- |
| `requested_s` | what the caller asked the engine for |
| `file_s` | the rendered WAV's own duration (`frames / rate`) |
| `musical_end_s` | the last frame above −50 dBFS |

`tail_silence_s = file_s − musical_end_s`. The threshold is
`TAIL_SILENCE_DBFS = -50.0`; digital silence reports `peak_dbfs = -120.0`
(`SILENCE_FLOOR_DBFS`), a JSON-safe floor, not a measurement limit.

Request/real divergence is normal, not a bug. Measured on a real ACE-Step take:
a 15 s request rendered a 11.7 s musical body and then 3.0–3.3 s of dead tail —
about 20 % of the file. Nothing in the product aliases `file_s` to `requested_s`,
and no test asserts that they are equal.

## Tail trim is a named, reversible edit

`qc.plan_edit()` decides what a report needs; `render_edit()` produces new
bytes. `Jobs.succeed()` (atomic path) or `Jobs.apply_qc_edit()` (re-settle)
writes those bytes beside the raw take under a **new uuid** and appends an
`edits[]` entry:

```
edit_id, kind (trim | peak_cap), source_take_id, source_audio_file,
source_sha256, audio_file, duration_s, peak_dbfs, clipped_frames,
transform {trim_end_s, fade_ms, gain_db, peak_cap_dbfs,
           source_file_s, source_peak_dbfs, tail_silence_s}
```

* The raw take's `audio_file` is never touched — its SHA-256 is recorded in the
  edit, so the provenance is checkable.
* The edit has its own `duration_s`, which is what playback and export report.
  `take.qc` keeps describing the raw file.
* `take.playable_edit_id` points at the edit; `GET .../audio?edit=<id>`,
  `/audio-json?edit=`, `/download?edit=` and `/peaks?edit=` serve it.
* Trimming only happens when the tail is at least `TRIM_MIN_TAIL_SECONDS`
  (0.2 s); `kind` is `peak_cap` when only the level needed work, and no edit is
  created for a clean take.

### Fade and cap

The cut is faded over 40–150 ms (default 80 ms, linear, ending at zero at the
trim point) so a trim cannot click. If the result's peak is above
`PEAK_CAP_DBFS = -1.0` a single global gain is applied so the peak lands at
−1 dBFS; the gain is stored in `transform.gain_db`. Nothing is hard-clipped.

## Stored seam/clip metrics

`qc` on the take: `clipped_frames` (frames with at least one full-scale sample
in the raw render), `tail_silence_s`, `peak_dbfs`, plus `file_s`,
`musical_end_s`, `requested_s`, and the `edit_plan` when one applies.

## Vocal evidence is tri-state and pessimistic

`vocal_presence` is `confirmed | not_confirmed | unknown` and is `unknown` for
every take that has not been through a real ASR pass. Band modulation is
recorded only as the diagnostic `vocal_evidence.modulation_dbfs`; it can never
change the verdict — the module has no scoring function to derive one.
`validate_take_qc()` rebuilds the evidence through `qc.vocal_evidence()`, so a
stored or forged `confirmed` degrades to `unknown` unless it carries a valid ASR
result.

`qc.vocal_evidence(asr=...)` is the only path to `confirmed`, and it refuses
any result that does not name its `run_id` and `model`. Fewer than
`VOCAL_CONFIRM_RATIO` (0.30) of the requested words recovered yields
`not_confirmed`.

### What the ASR runs actually found

These were measured off-product (Whisper on the rendered takes and on
Demucs-isolated stems), not by the worker, so they are evidence in this document
rather than a stored field:

- **ACE-Step 1.5 turbo:** Whisper recovered **0 of 3** requested lyric lines,
  twice, including on an isolated vocal stem. Turbo produced no intelligible
  words on that take.
- **MiniMax Music 3:** Whisper recovered the requested lines from the isolated
  stem of a MiniMax take.

ASR is a proxy, not a verdict on musical quality. Only a human listen confirms
a vocal performance, and one 3-line take per engine is not a benchmark.

## Known limits

* Analysis decodes the whole WAV in one pass; a 180 s stereo take costs a few seconds
  of worker time after the take is already playable.
* The gate runs in the worker, so takes rendered by older workers have
  `qc: null`.
* No ASR pipeline is wired into the worker. The verdict is `unknown` for every
  take the product produces today; the evidence API exists so a real run can be
  recorded, and refuses anything that is not one.
* QC measures the raw render. The edited file has its own peak, duration and
  clipped-frame count in the `edits[]` entry; those do not overwrite `take.qc`.

Back to [docs/README.md](README.md).
