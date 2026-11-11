# Changelog

All notable changes to Music Studio are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-11-09

- **The build states its own identity.** `music_studio/version_stamp.py` (vendored from the suite
  template) resolves `BUILD_INFO.json` → live `git` → `plugin.yaml`, and `GET /api/v1/version`
  reports version, commit, commit date, install time, dirty state and channel.
- The header chip reads that route and shows `v1.0.0-dev.N · 2026-11-09 · 19f7a2c`, amber and
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
