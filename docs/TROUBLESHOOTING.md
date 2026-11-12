# Troubleshooting

The failure modes the code actually handles, the sentence it gives you, and the next check. Start with
`python3 scripts/ms.py doctor` or the **Compute** panel; both report each engine and the failing check.

Applies to 1.0.0 · revised 2026-11-11 · back to [docs/README.md](README.md).

## No engine is ready

`/status` reports each engine as available or not, with the failing check named. Common causes:

- **ACE runtime not found.** `ace_local` needs `venv/bin/python`, `checkpoints/` and `source/acestep/`
  under the runtime (defaults come from `ACE_RUNTIME_DIR` and `ACE_SOURCE_DIR`). Set both in Compute,
  or export them before starting Hermes. The health detail names the first missing path.
- **ComfyUI unreachable.** `minimax_m3_comfy` probes `/system_stats` and `/object_info`. It needs the
  MiniMax Music 3 nodes, the three loader model lists, `CLIPLoader type="minimax"` and a lossless
  audio save format. A reachable server without those nodes is reported as unavailable, not ready.
  Press **Find a renderer** to scan loopback ports 8188/8189/8190/7860, the saved endpoint and
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

## The pane is blank or the host will not start

The pane frames a loopback host, not the gateway. `GET /studio/host` starts it; the launcher needs an
interpreter with `uvicorn`, `fastapi` and `starlette` (`/usr/bin/python3.13` is preferred). The host
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

Windows is not a supported target for this release. Parts of the product use `fcntl` file locks and
`/proc` for worker recovery. A Windows port is not shipped.

## macOS

The code path is not exercised by CI, which runs Linux only. `fcntl` exists on macOS; worker recovery
uses `/proc` start times, so the recovery path is less proven there. The README states this rather
than claiming parity.
