# Testing

How to run every suite in this repository, what CI runs, and the results measured on this commit.

Applies to 1.0.0 · revised 2026-11-16 · back to [docs/README.md](docs/README.md).

## Python suite

The contract, HTTP surface, store, job state machine, engine adapters against fakes, the tool handler
and the clients.

```bash
env -u PYTHONPATH TMPDIR=/tmp python3 -m pytest --rootdir=tests tests -q
```

Measured on this commit:

```
903 passed, 2 skipped, 1 warning, 139 subtests passed in 106.76s (0:01:46)
```

The two skips are recorded by the suite, not collection errors: a case-insensitive-filesystem case
and the opt-in live GPU render (`MUSIC_STUDIO_LIVE_TESTS=1`). No test performs a real render.

`env -u PYTHONPATH` matters: a Hermes checkout on the path ships its own `tests` package, which wins
over this repository's folder and makes collection fail with `No module named 'tests.<file>'`.

You need Python 3.13 and the packages in [requirements-dev.txt](requirements-dev.txt). `ffmpeg` must
be on `PATH` for three fake-ComfyUI tests; CI installs a static binary from the `imageio-ffmpeg`
wheel when the runner has none.

## Browser-side suite

The player, the pane, settings and the interactive screens, driven in jsdom against a stubbed API.

```bash
npm install          # once
node --test tests/*.mjs
```

Measured on this commit:

```
tests 167
pass 167
fail 0
```

Every `tests/*.mjs` file is a Node test file; the DOM ones load `standalone/static/index.html` and
drive the real `app.js` against a stubbed API.

## What CI runs

`.github/workflows/tests.yml` has two jobs, each on `ubuntu-latest`, `macos-latest` and `windows-latest`:

| Job | Runner | Command |
|---|---|---|
| Python | Python 3.13 on all three | `python -m pytest --rootdir=tests tests -q -p no:cacheprovider` |
| Browser-side | Node 20 on all three | `npm install --no-audit --no-fund` then `node --test tests/*.mjs` |

The Python job installs `requirements-dev.txt`, and installs `ffmpeg` from `imageio-ffmpeg` only when
the runner does not already have it. CI runs both suites on Linux, macOS and Windows.

## Counting

| Suite | Passed | Skipped |
|---|---|---|
| Python | 903 | 2 |
| Browser-side | 167 | 0 |
| **Total** | **1070** | **2** |

The README's test badge uses 1070. If you add or remove a test, run both suites and update this
table, the README badge and the count in `VERIFY.md` in the same commit.

## Manual checks

These are not part of the automated suite:

- A real render on a reachable ACE-Step or MiniMax/ComfyUI engine.
- Listening to the audio. A browser without an audio backend decodes nothing, so no test claims to
  hear anything; the server's obligation (real bytes and a declared length) is tested.
- The smoke script against a running build: `bash scripts/deploy.sh` then
  `python3 scripts/smoke.py 8775`.
