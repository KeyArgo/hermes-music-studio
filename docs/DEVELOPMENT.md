# Development

How the repository is laid out, how to run each suite, how CI works, and how to add an engine.

Applies to 1.0.0 · revised 2026-11-11 · back to [docs/README.md](README.md).

## Repo layout

| Path | What it is |
|---|---|
| `__init__.py` | Plugin entrypoint: registers `music_studio` and `/song`, bundles the skill |
| `music_studio/service.py` | Operations shared by the tool, chat and HTTP; every route is a thin call into it |
| `music_studio/jobs.py` | Take queue, worker lease, lineage, recovery |
| `music_studio/worker.py` | The detached render loop and QC settle |
| `music_studio/store.py` | Local JSON store, validation, atomic writes |
| `music_studio/engines/` | The `Engine` contract, registry and one module per engine |
| `music_studio/chat.py` | `/song` parsing and replies |
| `music_studio/{checkpoints,takecheck,intent,chorus,compare,edit,longform}.py` | Save spots, take checks, plain-word changes, previews/compare, PCM edits |
| `music_studio/{settings,network,discovery,setup}.py` | Saved settings, ComfyUI targets, renderer discovery, endpoint/weight setup |
| `music_studio/{sample,demo,export,peaks,presets,about,verify}.py` | Sample/demo, export, waveforms, presets, build identity, self-checks |
| `music_studio/{host,host_launcher,lifecycle,version_stamp,updates}.py` | Loopback host, port choice, watchdog, version stamp, update notice |
| `ace_engine.py`, `ace_worker.py` | The only ACE-Step GPU code path |
| `dashboard/plugin_api.py` | The HTTP surface mounted by Hermes |
| `dashboard/dist/index.js` | Legacy dashboard entry; the pane frames the loopback studio |
| `standalone/` | The local server, ideas/workspaces/REVamp/hardware routes, and the UI |
| `standalone/static/` | Plain HTML/CSS/JS UI; no build step |
| `desktop/plugin.js` | The Hermes pane: a frame around the loopback host |
| `scripts/` | `ms.py` client, `smoke.py`, `deploy.sh`, `verify-served.py`, `spectrogram.py` |
| `skills/music-studio/` | The bundled agent guidance skill and references |
| `tests/` | Python suite, DOM tests, fake engines |
| `docs/` | This documentation and the screenshots |
| `assets/` | Brand assets, the shipped demo and the catalog banner |

## Run the suites

```bash
# Python
env -u PYTHONPATH TMPDIR=/tmp python3.13 -m pytest --rootdir=tests tests -q

# Browser-side
npm install
node --test tests/*.mjs
```

`env -u PYTHONPATH` matters: a Hermes checkout on the path ships its own `tests` package, which wins
over this repository's folder. Use `/usr/bin/python3.13` explicitly when the shell's Python is a
different minor version. `ffmpeg` on `PATH` is needed for three fake-ComfyUI tests.

The Python suite fakes the engines (`tests/fake_ace.py`, `tests/fake_comfy.py`), so it needs no GPU,
no model weights and no network. A test that needs a real render is a manual check, not a test.

The DOM tests load `standalone/static/index.html` in jsdom and drive the real `app.js` against a
stubbed API. If you add a button, assert the request body it sends.

Measured counts and exact summary lines are in [../TESTING.md](../TESTING.md).

## How CI works

`.github/workflows/tests.yml` runs on every push and pull request, on `ubuntu-latest` only:

1. **python** — Python 3.13, `pip install -r requirements-dev.txt`, an `ffmpeg` install only when the
   runner lacks one, then `python -m pytest --rootdir=tests tests -q -p no:cacheprovider`.
2. **browser** — Node 20, `npm install --no-audit --no-fund`, then `node --test tests/*.mjs`.

CI is Linux-only because parts of the product use `fcntl` and `/proc`. The workflow and the README
state that scope rather than running a matrix that cannot pass.

## What a change needs

1. **A failing test first** — for a bug, one that reproduces it; for a feature, one that states the
   contract the UI depends on.
2. **A sentence in `LIMITS.md`** if the change narrows what the studio can honestly promise.
3. **No new runtime dependency** in the plugin itself. Dev-only tools are fine.
4. **A CHANGELOG entry** under the next version heading, written for the person running it.
5. **Every claim still reproducible.** If a README number changes, update the badge, `TESTING.md` and
   `VERIFY.md` in the same commit.

## House rules

- Never rewrite a take's audio in place. An edit is a new take, or a new file with the source hash.
- Never let an approval outlive the draft it approved (`updated_at` is the token).
- Never claim a render is local when the engine calls a hosted node.
- A control that fails must say so; do not replace a failure with a silent no-op.

## Adding an engine

1. Implement the `Engine` contract in `music_studio/engines/`: `id`, `label`, `kind`, `requirements`,
   `limits`, `capabilities`, `health()` and `_render()`.
2. Register it in `music_studio/engines/registry.py` (`default_engines()`) and add any legacy alias.
3. Declare its real limits and capabilities. An engine that cannot repaint or extend must say so — the
   studio refuses those operations rather than silently rendering a fresh take.
4. Add a fake server under `tests/` so the workflow can be tested without weights.
5. If the engine has a licence obligation, record it in the take metadata and
   [../THIRD-PARTY.md](../THIRD-PARTY.md).

## Screenshots

The current screenshots in `screenshots/` were captured from this commit against a fixture library:
the standalone server on a loopback port, a temporary data directory, and the shipped demo audio
served by a fake ACE runtime. They show no personal paths or libraries. To recapture:

1. Start the standalone app on a free loopback port with a temporary `HEWN_STUDIO_DATA_DIR`.
2. Use the demo/fixture data the repository ships; never the owner's real `~/.hermes` library.
3. Capture at 1440×900 with headless Chrome (`--force-device-scale-factor=2`, downscale to 1440),
   storing PNGs under 600 KB.
4. Check every image for a blank page, an error toast, a loading state or a personal path.
5. Kill every server you started.

`assets/catalog/banner.html` renders the 1200×600 catalog banner; the screenshot it composites is
`docs/screenshots/create-form.png`.
