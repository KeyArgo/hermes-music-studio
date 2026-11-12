# Verification

This page records what was measured, the exact command that checks it, the
expected output, and the status. The statuses below are the recorded results
from the run named in each section. Commands use the repository root as the
working directory.

Applies to 1.0.0 · revised 2026-11-11 · the suite breakdown is in
[TESTING.md](TESTING.md).

Environment: Python 3.13 with `requirements-dev.txt`. The full-suite command
below uses `env -u PYTHONPATH` because a Hermes checkout on the path ships its
own `tests` package and shadows this repository's folder. Some one-off commands
on this page were recorded with `PYTHONPATH=$HOME/.hermes/hermes-agent`, where
the plugin imports Hermes' `plugin_storage`. `PYTHONHOME` is unset so a stale
value cannot select another interpreter.

## Fast checks

| Check | Measured time | Scope |
|---|---|---|
| Demo-card verifier (`tests/test_verify_take.py`) | ~0.11 s | card vs shipped WAV, licence field presence |
| Full Python suite | 176.87 s | 482 passed, 1 skipped, 130 subtests |

The verifier is the quick pre-flight for the shipped demo; the full suite is the
complete test run. Times are recorded measurements, not live runs for this page.

## Full Python suite

**Claim:** the Python test suite passes.

**Command:**

```bash
env -u PYTHONPATH TMPDIR=/tmp /usr/bin/python3.13 -m pytest --rootdir=tests tests -q
```

**Expected output:** `482 passed, 1 skipped, 130 subtests passed` and exit status 0.

**Status:** PASS — measured 2026-11-11, 176.87 s. One test is skipped; that is
the recorded result, not a failure. The suite fakes the engines and does not
render real audio. The browser-side suite is a separate run: `node --test
tests/*.mjs`, 42 passed. See [TESTING.md](TESTING.md).

## Approval is required

**Claim:** a take cannot be queued without an approved draft. Approval is a
software flag checked by the store/jobs path, not proof of a human identity.

**Command:**

```bash
env -u PYTHONHOME PYTHONPATH=$HOME/.hermes/hermes-agent PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3.13 -m pytest --rootdir=tests tests/test_contract.py -q -p no:cacheprovider -k "batch_rejects_bad_counts_and_unapproved_drafts"
```

**Expected output:** `1 passed` and exit status 0.

**Status:** PASS — the test is part of the measured full suite; it was not run
separately for this page. `music_studio/jobs.py` refuses when
`project['approved']` is false. Say **approval required**, not "person-only":
the software checks that the draft was approved, not who clicked.

## Fast verifier tests

**Claim:** the demo-card verifier tests pass.

**Command:**

```bash
env -u PYTHONHOME PYTHONPATH=$HOME/.hermes/hermes-agent PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3.13 -m pytest --rootdir=tests tests/test_verify_take.py -q -p no:cacheprovider
```

**Expected output:** `5 passed` and exit status 0.

**Status:** PASS — measured 0.11 s. This is the fast check for the demo card and
its WAV.

## Demo is available and flagged as a demo

**Claim:** `demo.available()` returns true and the payload carries `demo: true`.

**Command:**

```bash
env -u PYTHONHOME PYTHONPATH=$HOME/.hermes/hermes-agent PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3.13 -c "from music_studio import demo; print(demo.available()); print(demo.payload().get('demo'))"
```

**Expected output:**

```
True
True
```

**Status:** PASS — asserted by `tests/test_demo.py::test_demo_take_ships`, which
is part of the measured suite. `music_studio/demo.py:available()` checks for the
shipped WAV and card; `payload()` sets `demo = True` and the audio URL.

## Demo card matches its WAV

**Claim:** `demo.verify_take()` compares the card against the shipped WAV and
reports no problems.

**Command:**

```bash
env -u PYTHONHOME PYTHONPATH=$HOME/.hermes/hermes-agent PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3.13 -c "import json; from music_studio import demo; print(json.dumps(demo.verify_take(), indent=2))"
```

**Expected output:** `"ok": true`, an empty `problems` list, and measured
`sample_rate: 44100`, `channels: 2`, `duration_seconds` matching the card
(13.119 s, tolerance 0.01 s).

**Status:** PASS — `tests/test_verify_take.py::test_committed_take_matches` is
part of the 5-passed fast run. `verify_take` reads the WAV header and frame
count; it does not decode or judge the audio.

## Demo provenance

The card in `assets/demo/demo-take.json` records:

- seed **4242**;
- duration **13.119 s** at 44.1 kHz stereo;
- engine **MiniMax Music 3 through ComfyUI** (engine id `minimax_m3_comfy`);
- word recovery **3/3** by faster-whisper medium.en (int8, CPU) on the full mix,
  with the transcript stored in the card.

Word recovery is ASR evidence, not a listening test. It does not measure pitch,
timing or whether a human would call it singing.

**Cloud involvement is unverified.** The card says the take was rendered on the
author's DGX Spark via ComfyUI. It does not record whether ComfyUI or the model
used a cloud service, so do not claim the demo is local-only or cloud-free.

## Licence record

**Claim:** the demo card carries a MiniMax licence field and `THIRD-PARTY.md`
records the same licence. This inspects the field values, not merely whether
unrelated files exist.

**Command:**

```bash
env -u PYTHONHOME PYTHONPATH=$HOME/.hermes/hermes-agent PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3.13 -c "
import json, pathlib
card = json.loads(pathlib.Path('assets/demo/demo-take.json').read_text())
assert card['licence'] == 'MiniMax-Music3'
assert card['licence_url'] == 'https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE'
assert card['licence_notice']
third = pathlib.Path('THIRD-PARTY.md').read_text()
assert 'MiniMax-Music3 Community License' in third
assert 'MiniMax-Music3' in third
print('licence field and THIRD-PARTY.md checked')
"
```

**Expected output:** `licence field and THIRD-PARTY.md checked` and exit status
0.

**Status:** PASS for the record's presence and values. `assets/demo/demo-take.json`
carries `licence: "MiniMax-Music3"`, `licence_url` and `licence_notice`;
`THIRD-PARTY.md` records the licence and its stated obligations.

**The wording is not verified as accurate.** The `licence_notice` says the output
is governed by the licence and that the copyright and permission notice must
accompany copies of the model **or its output**. The actual upstream licence at
the `licence_url` does not say this: it governs the model and weights, not the
generated output, and does not require the notice on outputs. The existing
`licence_notice` and the `THIRD-PARTY.md` obligations therefore **need
correction** — do not call them accurate. This remains a record of what the
plugin carries, not a legal review.

## Fast file checks

These only confirm the shipped files exist; they do not validate their content.

```bash
test -f assets/demo/demo-take.wav && echo "wav present"
test -f assets/demo/demo-take.json && echo "card present"
test -f assets/demo/demo-take-spectrogram.png && echo "spectrogram present"
```

**Expected output:**

```
wav present
card present
spectrogram present
```

**Status:** PASS — all three files were present when this page was written. The
spectrogram was generated by another worker with `scripts/spectrogram.py`; it
was not regenerated here.

## Not verified here

- **Client tests.** `node --test tests/test_desktop.mjs` is reported in the
  README, but was not re-run for this page.
- **Real renders and audio quality.** The suite uses fake engines; only a
  listening test judges the audio.
- **Cloud vs local for the demo.** Not recorded by the card.
- **Licence wording accuracy.** The record is present, but the carried notice and
  `THIRD-PARTY.md` obligations need correction against the upstream text; the
  full legal review is not claimed.
- **360 s takes.** Accepted at the engine boundary but never rendered — see
  `LIMITS.md`.
