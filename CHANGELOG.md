# Changelog

All notable changes to Music Studio are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Live-test fixes (round 13)

- **A slow draft is no longer thrown away.** The page waited 60 seconds while the plugin allows the model 120; it now waits 150.
- **Discuss idea chats again.** The page now tells the pane which button was pressed, so Discuss idea gets a chat reply instead of a filled draft.
- **Right-click menu fixes.** Copy works on selected chat text; clicking elsewhere or pressing Tab no longer pulls the cursor back to the old field; read-only fields offer Copy and Select all; a menu opened with Shift+F10 or the menu key opens at the field, not the window corner.
- **The other-profile message is accurate.** It says "a different copy" (it cannot tell which is older), and for the default profile it prints `hermes plugins update hermes-music-studio` without `-p`.
- **An update reaches the pane without restarting Desktop.** The page asks for `app.js` and `style.css` with a `?v=` hash of the files, so a webview cannot keep running last version's script.
- **The model picker starts on your Hermes model.** With nothing saved, the Provider menu used to open on whichever provider was listed first — on a Hermes with one connected provider, that one — with its model. Choosing a provider is a provider override, which Hermes refuses until you allow it, so the first draft failed over a choice nobody made. The menu now opens on "Your Hermes model (recommended)" and sends no provider or model. A saved choice is still restored; one whose provider or model no longer exists falls back to your Hermes model instead of being half restored.
- **An address that answers is not automatically an ACE-Step server.** Testing an address counted a 404 — or a web page — as "the server answers", so a ComfyUI could be tested and then saved as the ACE-Step engine. Only a health answer in JSON, or a reply that names ACE-Step, is accepted now; anything else says "answers but is not an ACE-Step server: HTTP 404" and saves nothing.
- **Saving a hosted key no longer wipes out your ACE-Step server.** Both use the one saved server address, and the hosted save used to overwrite it without a word. If a different address is already saved, it now refuses with a sentence naming that address, and says to remove it on the Music engines page first.
- **The ACE-Step server can be removed.** Its row on the Music engines page had Test but no Remove, so an address could be replaced but never taken away. Remove asks first, then clears the address. Takes it already made stay in your library.
- **"Change this part" now starts on a range the engine will actually render.** It defaulted to 0–8 s, and MiniMax Music 3 refuses anything under 10 s, so the default was a failed take. The range now starts at the engine's own minimum, and a range typed too short is grown to it before it is sent.
- **The words you type are added to the song's description, not swapped for it.** The typed change used to replace the whole caption, so "add a climax" became the song. It is now appended — "warm folk, close vocals. Change: add a climax" — and the lyrics, tempo and key come from the take you are changing rather than from the draft as it stands now.
- **The ACE-Step setup message names a step that exists.** It told you to set the runtime and source folders "on the Music engines page", which has no such field; it now names `ACE_RUNTIME_DIR` and `ACE_SOURCE_DIR`.
- **The upgrade steps include the restart.** After `hermes plugins update hermes-music-studio`, the README now says to restart Hermes (quit Hermes Desktop fully and open it again, or `hermes gateway restart`) so the studio loads the new version.

### Model picker and provider override

- **Choose any model your Hermes knows.** The Create pane shows a model picker with your Hermes model marked recommended. After running two `hermes config set` commands and restarting Hermes, you can select any provider and model. Your choice is remembered in your browser. Configured providers are marked ready; unconfigured ones show their setup steps.
- **Trust gates for provider and model override.** If you choose a provider or model Hermes has not approved for the plugin, the studio shows the exact commands to run. This keeps the plugin from using credentials you did not authorize.
- **The studio connects through Hermes only.** API keys stay in Hermes' credential store. The studio never writes credentials to its own folder.

### One button: Generate song

- **Press Generate song to save, approve and render.** The button appears under the draft fields. It handles the save, approval with a fresh token, and render in one action. Progress is shown in a panel; the take plays and links to the library when it finishes. **Preview the chorus** remains a separate button for quick 25 second samples.
- **Set up a music engine when you need one.** When no engine is ready, the button shows **Set up a music engine** and explains what is needed. Press it to install locally, connect to ComfyUI, or add a hosted API.
- **Disabled states explain why.** If the button is unavailable, it shows the reason: write lyrics first, rendering in progress, or engine not ready.

### Music engines page

- **Three ways to get an engine.** The Music engines page (left menu) offers: point at a server you already run (ComfyUI or ACE-Step), download ACE-Step 1.5 to this computer from Comfy-Org/ACE-Step-1.5, or a hosted API. Each path tests before saving and names which check failed.
- **Server test names the ready models.** Test asks the server for its node list and reports which music models are ready to render, for example `MiniMax Music 3` or `ACE-Step 1.5`.
- **ACE-Step 1.5 ready-made graph.** The Download path finds a local ComfyUI Desktop, checks disk space, downloads the four ACE-Step-1.5 model files (14.7 GB) from Hugging Face at a pinned revision, verifies each by SHA-256, and shows progress. Downloads resume and can be cancelled.
- **Copy and Paste engines.** Copy my engines exports each saved engine's name, address and ready model list, plus the ACE-Step server address (never keys), as text; on another computer, Paste engines restores them. A custom workflow's graph stays on the computer that saved it. Each computer maintains its own list in the plugin data folder, surviving plugin updates.
- **Fixes found in review, each with a regression test.** Add works only after a passing test of the address on screen and adds only the ready models; removing an engine is refused while a song uses it; a failed Add leaves nothing behind; partial downloads are kept for resume; files already in the folder are checksum-checked, never overwritten; a project is never left half-deleted on Windows; the library cannot move while a song is being made; dead renders are cleaned up outside the library lock.
- **Ready models appear in Generate menu.** After a test passes, the model names and server appear in the Music engine dropdown as, for example, `ACE-Step 1.5 on studio-pc`. A take is pinned to the engine and address it was generated with.

### Bring your own ComfyUI workflow

- **Any music model your ComfyUI runs, not just MiniMax.** Settings -> Compute accepts a
  ComfyUI Export (API) JSON graph, a name and a saved endpoint. The record lives in
  `comfy-workflows.json` (1 MiB cap, same lock/atomic-write as `comfy-targets.json`) and becomes
  an engine choice in the draft picker. Placeholders `{{style}}`, `{{lyrics}}`, `{{duration}}`,
  `{{seed}}`, `{{title}}` and `{{instrumental}}` are filled per take; the save node's
  `filename_prefix` is forced to `music-studio/workflow`. Health lists any node type the server
  lacks, Test never renders, and a render is pinned to the workflow's endpoint.

### Fixed

- **A draft from a profile running an older copy says so, instead of `internal server error`.** Hermes
  serves every profile from one process and imports plugin code with no isolation, so the first
  `music_studio` / `standalone` to load is the one each profile's routes silently run on. When that
  copy was an older one, `/studio/draft` asked it for `trust_error_response`, a name it does not have,
  and the ImportError escaped as a bare 500. New `music_studio/copies.py` names the folder and the
  profile (`Another Hermes profile loaded an older Music Studio from … Update it with:
  hermes -p jester plugins update hermes-music-studio, then restart Hermes.`); `/studio/draft` now
  answers 502 with that sentence (or `Music Studio failed: <Class>: <message>`) for any missing name or
  unexpected error, logging the traceback, and passing its own 400/422/502/503 refusals through
  unchanged; the mismatch is also logged once at mount time, without refusing to mount. `/verify.json`
  gains `code_folder`, the folder `music_studio` actually loaded from, so a bug report names the copy
  that answered.
- **A slow draft says so instead of `internal server error`.** Hermes' `ctx.llm.complete()` defaults
  to a 30 second timeout; a longer draft raised `openai.APITimeoutError`, which the plugin route never
  caught and the page showed as a bare 500. `llm_bridge` now asks for `timeout=120` (retrying once
  without the keyword on an older facade), and turns every non-trust failure into `LlmFailed` —
  `Hermes' model did not answer within 120 seconds…` for a timeout, otherwise
  `Hermes' model call failed: <ErrorClass>: <message>` — logging the original traceback. `/studio/draft`
  returns it as a 502, the standalone ideas route passes the same sentence through instead of
  "connection failed", and the desktop pane shows the `detail` instead of raw JSON.
- **Right-click inside the studio works.** The studio page is a sandboxed frame, so the desktop's own
  context menu never reached it. `standalone/static/app.js` now has its own menu — Cut, Copy, Paste,
  Select all — over editable fields and real selections, with `execCommand` for copy/cut,
  `navigator.clipboard.readText()` for paste (falling back to *Press Ctrl+V to paste (Cmd+V on a
  Mac).*), arrow-key/Escape handling, viewport clamping, and `user-select: text` on the idea chat.
- **Hosted API keys live in Hermes, not the data directory.** `music_studio/setup.py` no longer writes
  `cloud-keys.json`; `save_cloud` saves the key with `hermes_cli.config.save_env_value` under
  `ACE_REMOTE_API_KEY` and stores only the endpoint in `engine-settings.json`. `list_cloud` reports
  `has_key` and never the key, `probe_cloud` reads the key through `agent.secret_scope`, and a legacy
  `cloud-keys.json` is migrated once and deleted only after the key reads back. Renders now use the
  saved endpoint because `save_cloud` also writes `ace_remote_url`.
- **A hosted URL saved in Setup applies immediately.** `AceRemoteEngine` resolves its address at
  health/render time (explicit argument, then the saved `ace_remote_url`, then `ACE_REMOTE_URL`), so a
  cached engine no longer needs a host restart. `ace_remote_url` is the single stored address; the
  nested `ace_remote.url` is cleared and only read as a fallback for older settings files.
- **The engine registry notices other processes' writes.** Its cache is keyed on the data directory
  plus the mtime and size of `comfy-workflows.json`, `comfy-targets.json` and
  `engine-settings.json`; deleting a target also invalidates it.
- **A workflow in use cannot be deleted.** `delete_workflow` refuses with
  `workflow is in use by a queued take` while a queued or running take names its engine id.
- **ComfyUI links are never treated as placeholder text.** Graph inputs shaped exactly like
  `[node_id, output_index]` are skipped by placeholder discovery and left unchanged on substitution.
- **User text is not rewritten by substitution.** Placeholders are replaced in one `re.sub` pass, so a
  lyric containing `{{seed}}` stays exactly that.
- **Windows workflow writes retry like the rest of the store.** `WorkflowStore._write` uses
  `store.replace_with_retry` instead of a bare `os.replace`.
- **Covers accept case-insensitive spellings of an allowed folder.** Containment is decided with
  `os.path.samefile`; files outside every allowed root and symlinks that escape one are still refused.
- **The legacy key migration and a new key save share one file lock.** A `list_cloud` racing
  `save_cloud` can no longer re-import the old key after the new one was saved.
- **Whole-number durations stay integers in workflows.** `{{duration}}` becomes an `int` for whole
  seconds and a `float` otherwise.

## 1.0.0 — in catalog review

- Add to REVamp copies the take into the export folder and hands REVamp that file, so the song plays in
  REVamp (REVamp refuses links to this computer, so the old link-based entries never played). Play here
  plays the studio's own copy. Older entries show Fix for REVamp.
- Deleting a song or take also removes it from REVamp's playlists; an entry whose song is already gone
  reads "Deleted in Music Studio" instead of failing in the browser.
- The More menu on a take closes after a choice, opens upward near the player bar, and every library
  result shows above the player instead of only at the top of the page.

### Nous review fixes

- **Drafting goes through the host's own LLM.** `standalone/hermes_worker.py` is gone: it spawned
  `hermes --print-runtime-command`, imported core modules, built an `AIAgent` from resolved provider
  credentials and called the private image tool. `music_studio/llm_bridge.py` now calls
  `ctx.llm.complete` with no provider/model override, because Hermes has no public list-models API:
  the picker is empty and drafting uses Hermes' active model. Cover *generation* is removed;
  `set_cover` accepts an image file the agent made (PNG/JPEG/WebP, 2 MiB, inside the studio folder,
  temp, or `<hermes home>/cache/images`). A public image-generation call from Hermes is the missing
  piece.
- **The pane uses the plugin SDK, not the desktop's bridge.** The state-file fallback through
  `window.hermesDesktop.readFileText` is deleted (`ctx.rest('/studio/host')` is the only discovery
  path), links open through `ctx.os.openExternal`, the framed page asks the pane over `postMessage`
  instead of reaching for a bridge it does not have, and the studio is framed with `SandboxedFrame`.
- **The loopback host requires a token.** Generated per launch, handed to the host in its environment,
  returned by `/studio/host`, carried in the framed URL and required on every `/api/*` route, the health
  probe included (a launcher reads its 401 as "alive and gated") — those routes can change the ACE runtime directory, move the library, copy folders and
  spend model credits.
- **Children get an allowlist, not our environment.** `ace_engine._render` and `service._spawn_worker`
  passed `os.environ.copy()` to the ACE runtime and the render worker, so every provider key reached
  them; `music_studio/childenv.py` keeps PATH/HOME/LANG plus the CUDA/HF/ACE knobs and drops anything
  that looks like a credential, even inside an allowed family.
- **Export writes only where the owner keeps music.** `export.target_folder` accepts the configured
  `export_dir` (or anywhere inside `~/Music`), refuses everything else including `..` and symlink
  escapes, and the tool ignores a model-supplied folder instead of obeying one.
- **The remote key is declared and scope-aware.** `ACE_REMOTE_API_KEY` is in `requires_env` and read
  with `agent.secret_scope.get_secret`; the hosted-key file is created `0600` with `os.open` rather
  than chmod-ed after writing.


- **The build states its own identity.** `music_studio/version_stamp.py` (vendored from the suite
  template) resolves `BUILD_INFO.json` → live `git` → `plugin.yaml`, and `GET /api/v1/version`
  reports version, commit, commit date, install time, dirty state and channel.
- The header chip reads that route and shows `v1.0.0-dev.N · 19f7a2c · 2026-11-09` (version, commit,
  date), amber and
  `modified` when the installed copy came from a dirty tree; hover gives the full commit and build
  time, click copies the line for a bug report.
- `scripts/deploy.sh` installs, stamps and restarts in one command and ends in
  `scripts/verify-served.py`, which fails when what is served is not this tree.
- **The scrubber now survives a take with no known length.** When the media element reports
  `Infinity`, the range is disabled and labelled instead of sitting there dead; the player asks the
  browser to resolve the real length and restores the bar the moment it arrives. Two DOM tests cover
  both the known-length and unknown-length paths.
- **`scripts/smoke.py`** checks a running studio the way a person would: the page and its chip, the
  build stamp, the sample WAV and its declared length, a real take's bytes, and REVamp's presence
  (optional). It fails loudly if take audio is served without a length — the bug that killed the
  scrubber in the first place.
- **`GET /api/verify` — the product proves itself.** The running build renders its own identity
  (version · commit · date · dirty) and measures, at request time, whether the things it promises
  work on this machine: real sample audio, a library that reads, a finished take the player can
  scrub because the response declares its length, and REVamp's absence reported as a state. It says
  out loud what it cannot check — whether sound leaves the speakers. `GET /api/verify.json` is the
  same data for a script or a bug report.
- Release target is `1.0.0` per `VERSIONING-STANDARD.md`; until `v1.0.0` is tagged, builds read
  `1.0.0-dev.N`.

### After the hackathon submission

The hackathon entry pins commit `ba18a43` ([NousResearch/hermes-agent#154029](https://github.com/NousResearch/hermes-agent/pull/154029), tag `hackathon-submission`). 1.0.0 is the commit the catalog accepts; these changes came after the submission:

- **Windows support.** Portable file locks, no POSIX-only open flags, process checks without signal 0, `taskkill` for process trees, the Windows venv interpreter, and no directory fsync on Windows.
- **The create response shows the sections the lyrics imply.** It returned an empty list until the next status poll; storage still keeps only what the writer supplied.
- **The version chip tells a release from a development build.** A catalog install of the pinned commit, or a checkout at the release tag, reads `X.Y.Z`; any other build reads `X.Y.Z-dev.N` (N = `dev_base` + commits; a shallow clone with no commit count reads `X.Y.Z-dev`). `tools/release_check.py` and the release workflow keep the version, the changelog, the tag and the GitHub release in step.
- **Documentation:** a rebuilt README with a recorded demo and current Hermes Desktop screenshots, and a full docs set (usage, reference, architecture, configuration, troubleshooting, FAQ, development and testing).
- **Project files:** contributing guide, code of conduct, security policy, issue and pull request templates, and CODEOWNERS.

## [0.3.7] — 2026-11-09

- **The scrubber moves.** Take audio was streamed chunked with no `Content-Length`, so the media
  element reported `duration === Infinity`, the slider's max stayed 0 and there was nothing to
  drag (play/pause, ±10 s and volume were unaffected). The response now declares the file's exact
  length, so duration, the total time and the drag position are all real.

## [0.3.6] — 2026-11-09

- The version is on screen in the top bar (`v0.3.6`), read from the running server's `/api/about`
  — never hard-coded — so the install you are looking at can be identified at a glance.

## [0.3.5] — 2026-11-09

- Exporting the same take again reuses the file it already wrote (`exports.json`), so adding a
  song to a second playlist no longer fills the music folder with "Song (2)", "Song (3)".

## [0.3.4] — 2026-11-09

- A folder already inside a REVamp library root is covered by that root's scan; only a folder
  outside every root is added as a new one (adding a nested root made REVamp refuse the list).
- A playlist item shows the song's title, not the exported file's name ("Song (4).wav").

## [0.3.3] — 2026-11-09

- **A REVamp playlist is built from files, in that order.** Creating a playlist exports the
  chosen takes to the person's folder first, adds that folder to REVamp's library and rescans,
  and only then creates the playlist — whose items point at REVamp's own `/api/media/<id>` for
  the exported file. Playback no longer depends on the studio being open, and a playlist can
  never point at nothing.

## [0.3.2] — 2026-11-09

- **Closing the studio closes the frame.** The page tells the Hermes pane it has closed, and the
  pane leaves the route instead of framing a dead host — no more blank window titled "Music Studio".
- **Export to your own music folder.** Every finished take has an Export button; the folder is a
  setting (default `~/Music/Hermes Music Studio`), names are readable and sanitised, and a second
  export never overwrites the first.
- **Playlists are yours, REVamp is optional.** The REVamp screen can create and pick any playlist,
  the studio playlist stays the default, and with REVamp absent the screen says so plainly instead
  of failing — playing and exporting still work.

## [0.3.1] — 2026-11-09

- **REVamp shows only what Music Studio added.** The studio keeps one playlist in REVamp named
  "Music Studio"; the REVamp screen lists that playlist's studio items and nothing else — the
  audiobooks, albums and playlists of the REVamp library are no longer browsable from here.
- **"Add to REVamp" now works in the Hermes pane.** It used to answer 503 ("authenticated plugin
  audio cannot yet be handed to REVamp"); the take is handed over with the studio's loopback URL,
  which REVamp can fetch without gateway credentials. Adding twice keeps one copy.

## [0.3.0] — 2026-11-09

- **Close studio.** A button in the top bar stops the studio and the window it is in, and the
  page explains that the library is untouched. `POST /api/host/close` answers first, clears the
  recorded host in `studio-host.json`, then stops the process — no more hand-killing a window.

## [0.2.9] — 2026-11-09

- `scan_hosts` is a saved setting: a person types the machines to scan ("studio-pc, 192.168.1.20")
  in the Compute panel instead of exporting an environment variable.

## [0.2.8] — 2026-11-09

- **A real player.** The footer is a transport, not a bare audio tag: play/pause, −10 s / +10 s,
  a draggable scrubber with elapsed and total time, and a volume slider.
- **Finding a renderer.** "MiniMax cannot be reached" now has an action: *Find a renderer* scans
  the loopback ports, the saved endpoint and any hosts in `MUSIC_STUDIO_SCAN_HOSTS`, and offers
  what answered — saying plainly whether that ComfyUI can run MiniMax Music 3.
- `music_studio/discovery.py` + `POST /studio/discover`; 6 new Python tests, 6 DOM tests.

## [0.2.7] — 2026-11-09

- **Brand:** the working name "Hewn" (and "Lathe" for this product) is retired. Manifest author is
  now `ArgoLabs`; the README carries the ArgoLabs stamp and badges; `BRAND.md` documents the
  ArgoLabs house style (Nous blue `#0000F2`, stamp, banner grid, badge covenant).
- Brand assets renamed: `assets/brand/stamp.svg` (seal), `banner.html` / `banner.png`.
- Internal identifiers (`HEWN_STUDIO_DATA_DIR`, the `hewn-music-studio` standalone data directory)
  stay as they are: renaming them would orphan existing libraries.

## [0.2.6] — 2026-11-09

- The settings and library forms were reusing the one-line workspace form, which squeezed every
  field to 26–50 px: values were in the DOM but unreadable. Both are now a two-column grid with
  195 px fields.

## [0.2.5] — 2026-11-09

**Compute → Companion, Contribute, Report a problem.**

- **REVamp plug** with its real install command (`hermes plugins install revamp`), one-click copy,
  and an honest state: running / installed / wired as an MCP server only / absent.
- **Contribute**: the test commands, the guide link, and a note that the repository is private.
- **Report a problem**: `Copy diagnostics` builds a report from this process — version, platform,
  Python, library path, project/take counts, newest failed take — and copies it. Works without
  repository access.
- New: `music_studio/about.py`, `CONTRIBUTING.md`, `GET /about`, `POST /about/diagnostics`,
  `tests/test_about.py` (3) and one more DOM test.
- Suite: 305 passed, 1 skipped, 130 subtests.

## [0.2.4] — 2026-11-09

Revisions, built around what Suno's users complain about.

- **Several versions of one draft** (`POST /versions`): N takes, one seed each, from the same
  approved draft. Every seed is stored, so a winner can be reproduced exactly.
- **Replace one part, without re-rendering the song** (`POST /section/render` then
  `POST /section/splice`): the section renders as its own auditionable take; merging copies the
  parent's bytes and swaps only that range with 40 ms equal-gain ramps. The rest of the song
  cannot drift, and the parent take stays byte-identical. This is the one thing Suno's Replace
  Section cannot promise.
- **Clean endings** (`POST /fade`): a fade-out applied as a new take, instead of an abrupt cut
  or an unwanted extra chorus.
- New: `music_studio/edit.py` (splice/fade/measure on PCM), `tests/test_revisions.py` (7),
  revision buttons in the library, 1 new DOM test.
- Suite: 302 passed, 1 skipped, 130 subtests.

## [0.2.3] — 2026-11-09

Render settings and file management, both from the Compute tab.

- **Settings live in the studio, not in environment variables**: ComfyUI endpoint plus
  MiniMax Music 3 steps / CFG / guidance / top-k / sampler / scheduler, saved to
  `engine-settings.json` next to the library and read by the engine on construction.
  Ranges and sampler names are validated at save time. "Test endpoint" probes the server
  and lists each check (nodes, weights, reachability) without rendering.
- **Library folder is a setting**: copy or move the library to any folder and point the
  profile at it (`library.json`); reset returns to the plugin's own directory. A copy never
  deletes anything.
- **File management**: duplicate a project (new ids, copies of every WAV), delete a project,
  delete a single take. All through `/duplicate`, `/delete`, `/take/delete`.
- Tests: 9 Python (`tests/test_library.py`) + 5 DOM (`tests/test_ui_settings.mjs`); full
  suite 295 passed, 1 skipped.

## [0.2.2] — 2026-11-09

The Music Studio pane inside Hermes now *is* the studio. It no longer re-implements
the interface with plugin components; `GET /studio/host` starts a loopback host
serving the standalone UI against this profile's plugin library, and the pane frames
it. Added `music_studio/host.py`, `music_studio/host_launcher.py`, the `/studio/host`
route, seven host tests and five pane tests. The host also starts with the plugin
(the dashboard module starts it in the background, skipped under pytest and in the
standalone server), and the pane falls back to the state file on disk when the running
gateway predates the route. The host is spawned with `PYTHONPATH`, `PYTHONHOME`,
`VIRTUAL_ENV` and `CONDA_PREFIX` stripped: inherited from the gateway they point at
its venv's site-packages and the system interpreter dies importing pydantic
("cannot import name 'ArgsKwargs'"). One regression test pins that. The old native pane (and its 77 tests)
is removed, not duplicated: two interfaces is how the product and the plugin drifted
apart.

## [0.2.0] — 2026-11-07

Second release. Two real engines behind a registry, section-level editing, a
measurement gate, and a written limits page. Local-first is unchanged: no cloud
service is used to generate audio.

### Added

- **Engine registry** (`music_studio/engines/`): an `Engine` contract with
  `Limits`, `Capabilities`, `RenderRequest`, `RenderResult` and a structured
  `EngineStatus`; a `Registry` with health caching (10 s), a profile-scoped
  `engine-preference.json`, honest fallback metadata, and `NoEngineAvailable`
  instead of any mock engine.
- **`ace_local`**: ACE-Step 1.5 turbo on this GPU, 10–180 s takes, instrumental
  and vocal-language support, repaint and extend. A thin delegate to the
  existing `ace_engine`/`ace_worker` path — one GPU code path, not two.
- **`minimax_m3_comfy`**: MiniMax Music 3 through a ComfyUI server. Submits the
  native `MiniMaxMusic3` API graph, polls `/history`, fetches `/view`, and
  transcodes ComfyUI's FLAC to PCM WAV with ffmpeg at the model's native
  44.1 kHz stereo. Health validates the server, every graph node, the three
  loader model lists, `CLIPLoader type="minimax"`, and the audio save format.
- **`ace_remote`**: an HTTP adapter for `acestep.api_server`, registered and
  health-probed but undeployed and untested against a live server.
- **Section repaint** (`POST /repaint`): regenerate 3–30 s of a succeeded take
  as a new child take; the engine preserves everything outside the range and
  the output keeps the parent's length.
- **Extend** (`POST /extend`): append 10–60 s to a take of at least 20 s (600 s
  total cap) by regenerating a tail overlap, then splicing with an equal-gain
  raised-cosine crossfade at 48 kHz stereo pcm_s16le.
- **Take lineage**: every take records `kind`, `parent_take_id`, `lineage[]`,
  repaint range or extension geometry, and the engine that rendered it. Parent
  takes and their WAVs are immutable.
- **Sections model**: a validated whole-song partition (`intro`, `verse`,
  `prechorus`, `chorus`, `bridge`, `outro`, `instrumental`, `custom`) with
  per-section lyric lines; derived from `[tag]` headers when not stored.
- **Audio QC gate** (`docs/AUDIO-QC.md`): per-take `requested_s`, `file_s`,
  `musical_end_s`, `tail_silence_s`, `peak_dbfs`, `clipped_frames`; a
  reversible tail trim / −1 dBFS peak-cap edit stored under a new uuid with the
  source SHA-256; tri-state `vocal_presence` that only a real ASR result can set.
- **Serial batch** (`POST /batch`, `/batch/cancel`): one detached worker renders
  queued takes back to back; a failed take does not stop the batch. Adapters
  still declare `batch=false`, because no engine renders several takes in one
  call.
- **Presets** (`GET/POST/DELETE /presets`), project rename/delete, seed lock,
  server-computed waveform peaks (`/peaks`, with `?edit=`).
- **Engine-aware status**: `/status` returns `engines[]` with limits,
  capabilities, availability and the failing health check, plus
  `default_engine_id` and the duration contract; clients render the picker from
  data and hardcode no backend name.
- **Dashboard**: project rail, draft editor, section ledger with per-card
  repaint/extend, engine picker with honest availability, take strip with
  lineage and seeds, A/B compare, QC inspector, download, presets, batch,
  guarded recovery.

### Changed

- Take duration ceiling raised from 60 s to **180 s** (measured on an RTX
  4070 Ti; the model itself buckets higher, the product does not).
- A draft's `engine` field accepts any registered engine id. The legacy value
  `ace` remains valid and resolves to `ace_local`, so drafts approved before
  engine ids existed still render.
- `generate` accepts an optional `engine_id`. An explicit or draft-named engine
  that is unavailable is refused with its health detail (409) instead of being
  silently substituted.
- Capability refusal is a 409 that names the engine and the operation, not a
  generic 400.
- Approval now requires the `updated_at` token the form was loaded with, so an
  agent rewrite between read and click cannot be approved unseen.
- QC analysis runs before commit and settles atomically with success: the QC
  block and any reversible edit are written in the same save that flips a take
  to `succeeded`, so a client that stops polling on success already has them.
  A measurement failure still has no path to failing a render.

### Fixed

- Duration validation follows the pinned engine instead of a global 180 s
  ceiling: `minimax_m3_comfy` accepts 10–360 s (and is refused with *its* bound
  when over), while `ace`/`ace_local` keeps its measured 10–180 s window.
  `/status` labels the top-level `limits` with `limits_engine_id`.
- A repaint or extend queued on an engine that declares the capability false is
  refused before enqueue, and re-checked in the worker.
- A missing ffmpeg degrades MiniMax to `degraded` with the reason shown, rather
  than failing mid-render with an opaque error.

### Known limits

See `LIMITS.md`. The short version: ACE turbo vocals were unintelligible in the
measured take, MiniMax cannot repaint or extend, 360 s is untested, there are no
stems or LoRA support, and no automated test renders real audio.

## [0.1.0] — 2026-11-07

First release. Local, single-user, approval-gated music drafting and rendering.

### Added

- `music_studio` agent tool with `status`, `create`, `update`, and `generate`
  actions over a per-profile JSON project store.
- Draft fields `title`, `idea`, `caption` (style), `lyrics`, `engine` (`ace`),
  and `duration_seconds` (10–60 s, default 30).
- Human approval gate: drafts render only after a user approves them in the
  Music Studio dashboard tab; any changed draft field revokes approval.
- Immutable per-take snapshot of the draft plus the seed that rendered it.
- Detached generation worker (`python -m music_studio.worker`) that outlives
  the agent or dashboard request that started it and commits its own final
  take state.
- Automatic recovery of takes whose worker disappeared, by `pid` and process
  start time, on plugin import and on every status read.
- Dashboard tab with project list, draft editor, approve action, generation,
  take list, and inline audio playback.
- ACE-Step 1.5 adapter driving the locally installed runtime in a bounded
  subprocess, with an offline-HF environment and a runtime-scoped GPU lock.
- Streamed WAV delivery at `GET …/audio` and bounded base64 delivery at
  `GET …/audio-json` for the desktop panel.
- Bundled `music-studio` agent skill covering the creative interview, draft
  compilation, approval gate, and tool calls.

### Known limits

- One take in flight per profile; the GPU is used serially.
- 10–60 seconds of audio per take. Longer songs are not supported.
- Rendering needs a local CUDA GPU and a complete ACE-Step runtime; a take
  typically takes 1–3 minutes.
- The desktop panel's inline playback path refuses audio above 8 MiB.
- Seed is a replay handle on one machine, not a cross-hardware
  reproducibility guarantee.
- No automated test performs a real generation; audio quality is verified by
  listening.
