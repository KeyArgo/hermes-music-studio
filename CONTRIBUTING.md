# Contributing

Music Studio is small on purpose: a local-first plugin where every claim in the README is backed
by a command in `VERIFY.md`. Changes that keep that property are welcome. Changes that add a
capability without a test that proves it are not.

## Run the suite

```bash
python3.13 -m pytest tests -q
node --test tests/test_desktop.mjs tests/test_ui_settings.mjs
```

- The Python suite fakes the engines (`tests/fake_ace.py`, `tests/fake_comfy.py`), so it needs no
  GPU, no model weights and no network. It must stay that way: a test that needs a real render is
  a manual check, not a test.
- Use `/usr/bin/python3.13` explicitly when the shell's ambient Python is a different minor
  version; the Hermes runtime does not carry `pytest`.
- The DOM tests load `standalone/static/index.html` in jsdom and drive the real `app.js` against a
  stubbed API. If you add a button, assert the request body it sends.

## What a change needs

1. **A failing test first** — for a bug, one that reproduces it; for a feature, one that states the
   contract the UI depends on.
2. **A sentence in `LIMITS.md`** if the change narrows what the studio can honestly promise.
3. **No new runtime dependency** in the plugin itself. Dev-only tools (`scripts/spectrogram.py`) are
   fine.
4. **A CHANGELOG entry** under the next version heading, written for the person running it.

## Where things live

| Path | What it is |
| --- | --- |
| `dashboard/plugin_api.py` | the HTTP surface, mounted at `/api/plugins/hermes-music-studio` |
| `music_studio/service.py` | operations the tool and the API share; every route is a thin call into it |
| `music_studio/jobs.py` | the take queue, the worker lease, lineage, recovery |
| `music_studio/edit.py` | local non-destructive edits (section splice, fade) on stored PCM |
| `music_studio/engines/` | one module per engine, plus the registry and the request contract |
| `standalone/static/` | the studio UI (plain HTML/CSS/JS, no build step) |
| `desktop/plugin.js` | the Hermes pane: a frame around the studio host, nothing more |
| `tests/` | Python suite, DOM tests, fake engines |

## Adding an engine

Implement the `Engine` contract in `music_studio/engines/`, register it in
`music_studio/engines/registry.py`, and declare its real limits and capabilities. An engine that
cannot repaint or extend must say so — the studio refuses those operations rather than silently
rendering a fresh take. Add a fake server under `tests/` so the engine's workflow can be tested
without weights.

## Reporting a problem

Open the studio, go to **Compute → Report a problem**, and press **Copy diagnostics**. The report
already contains the version, platform, library path, project/take counts and the newest failed
take. Paste it into an issue (or anywhere else) along with what you expected to happen.

The repository is private, so the issue tracker only opens for people the owner invited; the
diagnostics text is the part that works for everyone.

## House rules

- Never rewrite a take's audio in place. An edit is a new take.
- Never let an approval outlive the draft it approved (`updated_at` is the token).
- Never claim a render is local when the engine calls a hosted node — see the note in `VERIFY.md`
  about how the demo take was produced.
