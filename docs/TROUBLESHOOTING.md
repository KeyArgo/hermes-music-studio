# Troubleshooting

The failure modes the code actually handles, the sentence it gives you, and the next check. Start with
`python3 scripts/ms.py doctor` or the **Music engines** panel; both report each engine and the failing check.

Applies to 1.0.0 · revised 2026-11-18 · back to [docs/README.md](README.md).

## Where the logs are

Three plain-text logs live in the plugin data folder (see [Where your data lives](../README.md#where-your-data-lives);
on Windows that is `%LOCALAPPDATA%\hermes\plugin-data\hermes-music-studio\`):

| File | What it holds |
|---|---|
| `logs/studio.log` | One line per event: render started, finished (with how long it took), failed or stopped (with the reason), engine tested (what it answered), engine added or removed. Keys, tokens and passwords are masked; lyrics and captions are never written. Grows to 1 MB, then keeps three older copies (`studio.log.1` to `.3`). |
| `worker-logs/<take id>.log` | Everything one render printed, for a take that failed. The take id is in the take's **More** menu and in `studio.log`. |
| `studio-host.log` | The studio page's own server: start, stop, and why it would not start. |

When reporting a problem, **Copy diagnostics** (Music engines page) plus the last lines of `studio.log` is usually enough.

## The ACE-Step server form says "This is ComfyUI"

You typed a ComfyUI address into **ACE-Step server**. ACE-Step 1.5 on a ComfyUI is added as a ComfyUI engine:
the form switches to **ComfyUI** and tests it for you; press **Add** next to **ACE-Step 1.5**. The
**ACE-Step server** choice is only for the separate ACE-Step API server (`acestep-api`).

## MiniMax Music 3 sounds harsh or stops between words

On **Music engines → More options → Advanced settings → MiniMax Music 3**, **CFG** and **Guidance (cfg_scale)** should both be about `1.7`, the values ComfyUI's
own MiniMax Music 3 workflow uses. Higher CFG (the old default was 4.0) over-drives the model: in a same-seed
test on a real server, CFG 4.0 left 11% of the take silent with gaps up to 0.7 s, CFG 1.7 left 3%. Settings
saved with the old defaults are moved to 1.7 automatically; a value you chose yourself is kept.

## No engine is ready

`/status` reports each engine as available or not, with the failing check named. Common causes:

- **ACE runtime not found.** `ace_local` needs `venv/bin/python` and `checkpoints/` under the runtime
  (`ACE_RUNTIME_DIR`), and `acestep/` under the source checkout (`ACE_SOURCE_DIR`) — two separate
  roots, and a path that is unset reads `/nonexistent/…`. Set both on the Music engines page,
  or export them before starting Hermes. The health detail names the first missing path.
- **ComfyUI unreachable.** `minimax_m3_comfy` probes `/system_stats` and `/object_info`. It needs the
  MiniMax Music 3 nodes, the three loader model lists, `CLIPLoader type="minimax"` and a lossless
  audio save format. A reachable server without those nodes is reported as unavailable, not ready.
  Press **Find a renderer** to scan loopback ports 8188/8000/8189/8190/7860, the saved endpoint and
  `MUSIC_STUDIO_SCAN_HOSTS`.
- **No ffmpeg.** If ComfyUI cannot save WAV, the adapter needs `ffmpeg` on `PATH` to transcode FLAC.
  Without it the engine is `degraded` with "ComfyUI cannot emit WAV and ffmpeg is not installed".

No engine ready is not fatal: the studio opens, the sample plays, and a render is refused instead of
returning placeholder audio.

## `GPU busy`

`enqueue` refuses while any take in any project is `queued` or `running`, and the worker takes the
render lease non-blockingly. Wait for the current take, or use **Cancel batch** for queued takes. A
render that died without releasing the lease is cleaned up by `reconcile_dead_workers` on the next
`GET /status`; the kernel releases the lease when the worker process exits, including SIGKILL.

A worker that is alive but wedged needs `POST /recover {"confirm": true}`. That fails every
queued/running take, so use it only when you have confirmed no ACE process is still running.

## `worker died`

A take whose recorded pid and pid start time no longer exist is marked `failed: "worker died"` on
plugin import and every status poll. Takes younger than ten seconds with no pid are left alone so a
status poll cannot kill a starting render. Check `<data>/worker-logs/<take_id>.log`.

## `draft requires approval`

The draft was saved after the last approval, or it was never approved. A change to any field revokes
the approval; a `keep` does not. Approve the exact version you read: `POST /approve` and the tool's
`approve` action need the `updated_at` token, and a stale token is refused with `stale draft`.

## `stale draft`

The `base_updated_at` you sent is not the current version. Reload the draft, read it again, and approve
or save against the new token. This is the guard against an agent rewriting text between your read and
your click.

## `duration must be between …`

The window belongs to the engine pinned to the draft: 10–180 s for `ace_local`, 10–360 s for
`minimax_m3_comfy`. A draft that names one engine and a generate call that names another is validated
against the engine that will actually render. 360 s on MiniMax is accepted but has never been rendered
(see [../LIMITS.md](../LIMITS.md)).

## `caption must contain 1–512 characters` / lyrics errors

Caption is required and capped at 512 characters; lyrics are required unless `instrumental` is true
and capped at 4096. The store enforces the same limits the engine contract does, so an approved draft
can always render.

## `CapabilityUnsupported` / 409 for repaint or extend

MiniMax declares `repaint=false` and `continuation=false`; the server refuses before queueing and the
worker re-checks. `ace_remote` is registered but has no deployed server. Generate a fresh take on an
engine that supports the operation.

## `Hermes' model did not answer within 120 seconds`

`Fill my draft` (and `Discuss idea`) ask Hermes' model through `ctx.llm.complete`, with a 120 second
limit — Hermes' own default of 30 seconds was shorter than a long draft. A timeout is not a crash: the
page says the sentence above, and the original error is written to the plugin log with its traceback.
Try again, or pick a faster model in the Create pane. An older Hermes that does not accept the
`timeout` keyword still drafts; it just keeps the host's shorter limit.

Any other model failure reads `Hermes' model call failed: <ErrorClass>: <message>`, for example
`ValueError: …` or `APIConnectionError: …`, so the reason is visible instead of
`hermes.api error 500: internal server error`.

## The scrubber has no range

The player needs `Content-Length` on the audio response. Both `/api/projects/{id}/takes/{take}/audio`
and the sample route declare it. If a proxy strips the header, the browser reports
`duration === Infinity`; the UI disables the range and asks the browser to resolve the real length.
`scripts/smoke.py` fails loudly when a running build serves take audio without a length.

## A take is shorter than requested

ACE can render less musical material than requested and leave a dead tail. A measured 15 s request
produced 11.7 s of music plus 3.0–3.3 s of tail silence. The QC block records `requested_s`, `file_s`
and `musical_end_s` separately, and a reversible trim is offered when the tail is at least 0.2 s. The
take is not a silent failure: the numbers are in the inspector.

## `audio unavailable`

The take's JSON still exists but its WAV is gone, or the audio response was refused. The audio routes
open the file with `O_NOFOLLOW` and require a regular file. Deleting a WAV is not undone by a restart.

## Library looks empty after moving it

The library pointer is `library.json` beside the plugin data. **Copy there** leaves the old files and
records the new folder; **Move there** deletes the old copies after the switch. **Use default** removes
the pointer. If the pointer names a folder that no longer exists, `library_dir()` ignores it and the
studio reads the default folder, which can look like an empty library.

## I cannot copy or paste in the studio

The studio runs in a sandboxed frame inside Hermes Desktop, and the desktop's right-click menu belongs
to the outer window. The studio page therefore has its own small menu: right-click a text field, or
select text first, and it offers **Cut**, **Copy**, **Paste** and **Select all**. Elsewhere (no field,
no selection) the right-click is left to the page as usual.

**Paste** reads the clipboard with `navigator.clipboard.readText()`. A framed page is allowed to do
that only with the person's permission, so a refusal is normal: the page then says
*Press Ctrl+V to paste (Cmd+V on a Mac).* and the keystroke still works. Conversation text in the
chat is selectable (`user-select: text`), so the same menu copies it.

## The pane is blank or the host will not start

The pane frames a loopback host, not the gateway. `GET /studio/host` starts it; the launcher needs an
interpreter with `uvicorn`, `fastapi` and `starlette` — the current interpreter first, then the Hermes
sibling, then the `python3.x` names on `PATH`, and `/usr/bin/python3.13` last. The host
log is `studio-host.log` beside the library. If Hermes was restarted, the old host exits through its
parent-pid watchdog; reopen the pane.

## `POST /api/… answered 400` with "loopback host required"

The standalone server accepts only `127.0.0.1`, `localhost` or `::1` in the `Host` header. A DNS name
that resolves to the machine is rejected on purpose. Browse the loopback name.

## Tests fail at collection with `No module named 'tests.<file>'`

A Hermes checkout on `PYTHONPATH` ships its own `tests` package, which wins over this repository's
folder. Use the command in [../TESTING.md](../TESTING.md): `env -u PYTHONPATH … pytest --rootdir=tests tests -q`.

## Browser-side tests fail immediately

Run `npm install` once. The DOM tests need `jsdom`, `@testing-library/react`, `react` and `react-dom`;
CI runs them on Node 20.

## Windows

CI runs both suites on Windows. Real-hardware use has been on Linux, and local ACE-Step rendering on
Windows is untested. File locks go through `msvcrt` on Windows, and process state comes from the
platform APIs rather than `/proc`.

## macOS

CI runs both suites on macOS, but macOS has had no hands-on test yet. Worker recovery reads `/proc`
start times on Linux and falls back to `ps` on macOS,
so that path is less proven there. The README states this rather than claiming parity.
