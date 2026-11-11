# Hermes Music Studio

[![release](https://img.shields.io/github/v/release/KeyArgo/hermes-music-studio)](https://github.com/KeyArgo/hermes-music-studio/releases/latest) [![license](https://img.shields.io/badge/license-MIT-green)](LICENSE) [![Hermes Agent](https://img.shields.io/badge/Hermes%20Agent-plugin-0b6e99)](https://github.com/NousResearch/hermes-agent) [![tests](https://img.shields.io/badge/tests-482%20passing-brightgreen)](VERIFY.md) [![platforms](https://img.shields.io/badge/Linux%20%C2%B7%20macOS-supported-informational)](#install) [![stars](https://img.shields.io/github/stars/KeyArgo/hermes-music-studio?style=social)](https://github.com/KeyArgo/hermes-music-studio/stargazers)

**Status: 1.0.0, first public release.** Nothing in this file is aspirational — every number below
was measured on the release commit, and `GET /api/verify` re-measures it in front of you.

The agent writes the song. You approve it. Your own machine renders it.

![the create screen, a draft filled in](docs/screenshots/create-form.png)

---

## What it is

A studio inside Hermes. You describe a feeling, the studio turns it into a draft with a title, style
and lyrics, and nothing is rendered until you approve that draft. Every take keeps the draft it came
from, its seed and its engine, so a take is reproducible rather than a lucky accident.

The interface is one page with five places to be:

| Where | What happens there |
|---|---|
| **Create** | the draft: title, sound and style, lyrics or instrumental, engine |
| **Library** | every project and its takes, with the seed and state of each |
| **Workspaces** | group projects so a long session does not become one pile |
| **Compute** | which engine answers, and where it lives |
| **REVamp** | optional: put exported takes in a REVamp playlist |

The footer is a real player: play or pause, ten seconds back and forward, a scrubber that moves the
audio itself, and a volume slider. Exported songs go to a normal folder as ordinary `.wav` files, so
any player or phone sync can find them.

![the library](docs/screenshots/library.png)

## Why it is different

- **Approval is a step, not a warning.** The render endpoint refuses a draft that was not approved.
- **A take can be re-made.** Draft, seed and engine travel with the take.
- **The build says which build it is.** The header reads
  `v1.0.0-dev.75 · 2026-11-09 · 0a819edf`, filled from the running server, never from a constant in
  the page. A version without a date cannot tell you whether a screenshot is from this morning.
- **The product proves itself.** `GET /api/verify` measures, at request time, that the sample is
  real audio, that the library reads, that a finished take is scrubbable because its response
  declares its length, and that REVamp's absence is a state rather than an error.

![the proof page](docs/screenshots/verify.png)

## Use it from a chat

The studio is not only a window. An agent can drive it, and so can a shell:

```bash
python3 scripts/ms.py status          # projects, engines, what is rendering
python3 scripts/ms.py doctor          # is an engine actually ready here
python3 scripts/ms.py render --project <id> --timeout 240
python3 scripts/ms.py export  --project <id>     # writes a normal .wav, prints the path
```

Inside Hermes the same loop is one tool, `music_studio`, with the actions `status`, `doctor`,
`create`, `update`, `change`, `keep`, `snippet`, `approve`, `render`, `queue`, `takes`, `export` and `wait`. The approval gate
survives the shortcut: `approve` must carry the `updated_at` token of the draft the person actually
read, and `render` refuses anything unapproved. A chat can therefore do the whole job — describe a
song, show the draft, and only render once the person says yes — without opening the window.

`render` waits for the take so a chat can answer with a file; `queue` returns the moment the worker is
spawned. Use `queue` when you want the conversation to stay free and will check back with `takes`.

### Slash commands

In any Hermes chat, `/song` drives the same studio:

```
/song                  what is in the library, with ids and engine per song
/song go <id>          show the draft that would render — and queue nothing
/song go <id> now      approve that exact version and queue one take (returns immediately)
/song takes <id>       every take with its state, engine and measured length
/song play <id>        the newest finished take's audio URL
/song export <id>      copy the newest finished take into your music folder
/song change <id> …    plain words → a different draft ("add a fiddle", "go dark", "half-time in the bridge")
/song keep <id>        remember the newest finished take as this song's keeper
/song check <id> [retry] did the take do what you asked? facts only, and a retry if one failed
/song save [label]     save your spot; /song saves lists them, /song back <id> returns
/song snippet <id> 10-18 go darker   re-render only 10–18 s (ACE only), then /song splice <id> <take>
/song splice <id> <take>             copy the parent back outside the range, so only that range differs
```

### Save your spot, at any moment

**Save spot** writes a checkpoint into the song itself: the whole draft (caption, lyrics, bpm, key,
sections, engine), the kept take with the **sha256 of its audio**, the take lineage with seeds, anything
queued or running, and the playhead. Twenty are kept, a pinned one is never pruned, and saving does not
revoke an approval because it is not a draft edit.

**Back to last spot** restores the draft and the keeper, and says plainly that the approval was revoked —
you are looking at a different draft and should approve it again.

One rule differs by engine, and it matters: ACE replays exactly from a seed, so an ACE spot can be rebuilt
from it; **MiniMax's seed replay was never measured**, so an M3 spot keeps the audio file itself instead of
promising a rebuild.

### Did the take do what you asked?

Every finished take has **Check this take**: it compares the take against the request that produced it —
did the words reach the engine's caption, is the tempo the one that was set, is the length right, is
the file clipped, does it end in a silence, and does the vocal evidence contradict "instrumental".
Checks that need a speech model stay **not measured** and say why, rather than guessing.

When a measurable check fails, **Fix it** re-renders with a fresh seed and names what it is retrying.
The failed take stays in the library: it is the evidence, not garbage.

### Editing eight seconds without touching the rest

On any finished take, **Change this part** opens a form on the row — start, end, the words, a button.
The range is re-rendered as its own take, and when it finishes the studio **splices it back
automatically**: the audio outside the range is copied from the parent byte for byte, so the rest of the
song cannot drift. That is the difference between this and a hosted "replace section", which re-generates
the whole thing and hopes. ACE is the only engine that can repaint; MiniMax refuses with a sentence, and
the refusal is the engine's own capability check rather than a silent whole-song render.

`change` is deterministic, not a second model: "add a fiddle" appends `fiddle` to the one style caption,
"go dark" expands to `dark, minor key, low register, sparse percussion`, "half-time in the bridge" is sent
as a feel on that section because neither engine accepts a second tempo, and "mute the drums" is refused
with a sentence instead of silently doing nothing — both engines render one mixed stereo take. A change
revokes the approval (it is a different draft); a `keep` does not. Every change and every keep lands in the
song's journal on its own file, so the history survives a reload.

The gate is two steps on purpose: the first `go` prints title, caption, engine, lyric count and whether
the draft is approved; the second (`… now`) approves the version you were shown and queues it. If the
draft changes in between, the old confirmation is refused rather than rendering something you did not
review.

`scripts/ms.py` is a thin HTTP client with no dependencies beyond the standard library. It finds the
loopback host from `studio-host.json` (or `--base`), prints JSON for everything, and exits 1 when the
studio refuses and 2 when no host answers, because "the studio said no" and "nothing is listening"
are different problems.

## Install

You need:

- **Python 3.13** with `fastapi`, `uvicorn`, `python-multipart` (`pip install -r requirements-dev.txt`)
- **Node 20+** if you want to run the browser-side tests
- **Hermes** itself, since this ships as a plugin
- **Something to render with** — see **Limits**
- **Linux or macOS.** Windows support is in the next release.

```bash
hermes plugins install KeyArgo/hermes-music-studio
```

To pin an exact revision (what CI and the release script do):

```bash
hermes plugins install KeyArgo/hermes-music-studio --ref <full 40-character sha> --yes-deps --enable
```

A short SHA destroys the checkout; the installer needs all 40 characters. The desktop pane appears
after **Ctrl+K → Reload desktop plugins**.

Running from a clone instead:

```bash
git clone https://github.com/KeyArgo/hermes-music-studio
cd hermes-music-studio
pip install -r requirements-dev.txt
python3 -m uvicorn standalone.app:app --port 8770   # the studio on its own, no Hermes needed
```

## Run the tests

```bash
# Python: contract and HTTP surface
env -u PYTHONPATH TMPDIR=/tmp python3 -m pytest --rootdir=tests tests -q

# Browser-side logic: the player, the pane, the settings screen
npm install          # once: jsdom and the testing library
node --test tests/*.mjs
```

`env -u PYTHONPATH` matters: a Hermes checkout on the path ships its own `tests` package, which wins
over this repo's folder and makes collection fail with `No module named 'tests.<file>'`.

Measured on the release commit: **352 Python tests passed, 1 skipped** and **31 browser-side tests
passed**. `VERIFY.md` carries the commands and their output.

## Prove a deploy

```bash
bash scripts/deploy.sh          # install, stamp BUILD_INFO.json, restart, verify
python3 scripts/smoke.py 8775   # ask the running build to do what it claims
```

`verify-served.py` fails when the installed copy is not the tree you just built. Installs are copies,
so an edit that never arrived looks exactly like a change that did not work.

## Limits

- **No model ships here.** Rendering needs a reachable engine: a ComfyUI host running MiniMax Music 3,
  or a local ACE-Step checkout. With neither, the studio still opens, the sample still plays, and the
  engine panel says what is missing. It does not invent audio.
- **No address is baked in.** A ComfyUI host is per-machine; set it in Compute or with `COMFYUI_URL`.
- **REVamp is optional.** Without it, playlists are unavailable and the panel says so.
- **Export copies.** The library keeps its master; the exported file is a second copy with a readable
  name, never an overwrite of what is already in your folder.
- **Sound is checked by a person.** A browser without an audio backend decodes nothing, so no test
  here claims to hear anything. What the server owes the player — real bytes and a declared length —
  is tested.
- **macOS is untested.** Linux is used daily; Windows is not verified for this tree either.

## Licence and credits

MIT. The bundled demo (`assets/demo/`) is a real render, recorded with its seed and settings in
`demo-take.json`. See `CONTRIBUTORS.md` for the people and models that built it.
