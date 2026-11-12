# Contributing

Music Studio is small on purpose: a local-first plugin where every claim in the README is backed by a
command in [VERIFY.md](VERIFY.md) or a test that fails when the claim is false. Changes that keep that
property are welcome. Changes that add a capability without a test that proves it are not.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Security problems go through
[SECURITY.md](SECURITY.md), not a public issue.

## Dev setup

```bash
git clone https://github.com/KeyArgo/hermes-music-studio
cd hermes-music-studio
pip install -r requirements-dev.txt   # Python 3.13
npm install                           # browser-side tests only
```

To run the studio from a clone without Hermes:

```bash
python3 -m uvicorn standalone.app:app --port 8770
```

## Run the suite

```bash
# Python: contract, HTTP surface, store, jobs, engines against fakes
env -u PYTHONPATH TMPDIR=/tmp python3 -m pytest --rootdir=tests tests -q

# Browser-side: the player, the pane, the settings screen
node --test tests/*.mjs
```

- The Python suite fakes the engines (`tests/fake_ace.py`, `tests/fake_comfy.py`), so it needs no GPU,
  no model weights and no network. It must stay that way: a test that needs a real render is a manual
  check, not a test.
- Use `/usr/bin/python3.13` explicitly when the shell's ambient Python is a different minor version.
- The DOM tests load `standalone/static/index.html` in jsdom and drive the real `app.js` against a
  stubbed API. If you add a button, assert the request body it sends.
- `env -u PYTHONPATH` matters: a Hermes checkout on the path ships its own `tests` package, which wins
  over this repository's folder.

The measured counts and the exact summary lines are in [TESTING.md](TESTING.md).

## What a change needs

1. **A failing test first** — for a bug, one that reproduces it; for a feature, one that states the
   contract the UI depends on.
2. **A sentence in [LIMITS.md](LIMITS.md)** if the change narrows what the studio can honestly promise.
3. **No new runtime dependency** in the plugin itself. Dev-only tools (`scripts/spectrogram.py`) are
   fine.
4. **A [CHANGELOG.md](CHANGELOG.md) entry** under the next version heading, written for the person
   running it.
5. **Numbers stay true.** If a README count changes, update the badge, [TESTING.md](TESTING.md) and
   [VERIFY.md](VERIFY.md) in the same commit.

## Commit style

Small logical commits with a plain prefix, for example:

```
docs: explain the approval token
ci: install ffmpeg from a wheel
fix: keep the scrubber alive when the length is unknown
test: cover a take with no audio
```

Do not mix a documentation rewrite and a behavioural change in one commit.

## Pull requests

- Describe what changed, why, and which suites you ran.
- Link the issue if there is one.
- Keep a PR focused. Unrelated refactors belong in another PR.
- The PR template asks for a summary, the tests run, and a short checklist. Fill it in.

## Where things live

| Path | What it is |
| --- | --- |
| `dashboard/plugin_api.py` | The HTTP surface, mounted at `/api/plugins/hermes-music-studio` |
| `music_studio/service.py` | Operations the tool and the API share; every route is a thin call into it |
| `music_studio/jobs.py` | The take queue, the worker lease, lineage, recovery |
| `music_studio/edit.py` | Non-destructive PCM edits (section splice, fade) |
| `music_studio/engines/` | One module per engine, plus the registry and the request contract |
| `standalone/static/` | The studio UI (plain HTML/CSS/JS, no build step) |
| `desktop/plugin.js` | The Hermes pane: a frame around the studio host |
| `tests/` | Python suite, DOM tests, fake engines |

[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) has the full layout and the CI details.

## Adding an engine

Implement the `Engine` contract in `music_studio/engines/`, register it in
`music_studio/engines/registry.py`, and declare its real limits and capabilities. An engine that
cannot repaint or extend must say so — the studio refuses those operations rather than silently
rendering a fresh take. Add a fake server under `tests/` so the engine's workflow can be tested
without weights. Record any licence obligation in the take metadata and
[THIRD-PARTY.md](THIRD-PARTY.md).

## Reporting a problem

Open the studio, go to **Compute → Report a problem**, and press **Copy diagnostics**. The report
contains the version, platform, library path, project/take counts and the newest failed take. Paste
it into a GitHub issue along with what you expected to happen.

## House rules

- Never rewrite a take's audio in place. An edit is a new take, or a new file with the source hash.
- Never let an approval outlive the draft it approved (`updated_at` is the token).
- Never claim a render is local when the engine calls a hosted node.
- A control that fails must say so; do not replace a failure with a silent no-op.
