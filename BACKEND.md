# Operator note

Short version of `docs/ARCHITECTURE.md` for someone running the plugin, not
changing it.

## What runs where

The agent tool and dashboard call `music_studio.service` in the gateway process.
A generate/repaint/extend/batch call queues a take and spawns a detached
`python -m music_studio.worker` (`start_new_session=True`, no inherited file
descriptors). The worker opens the render lease itself, claims its take, renders
through the engine registry, commits `succeeded`/`failed`, runs the QC pass, and
exits. The requesting turn or HTTP request can end immediately; the render
continues. Worker stdout/stderr goes to
`<plugin-data>/worker-logs/<take_id>.log`.

## Engines

`ace_local` delegates to `ace_engine` and the runtime named by `ACE_RUNTIME_DIR`, source
by `ACE_SOURCE_DIR`. No path is baked in: unset means the engine reports itself
unavailable (`/nonexistent/ace-runtime`, `/nonexistent/ace-source`) rather than
pointing at a folder that belongs to another machine. `minimax_m3_comfy` talks to
the ComfyUI endpoint saved on the Music engines page or `COMFYUI_URL` (no baked-in address) and
needs `ffmpeg` for FLAC → WAV. `ace_remote` is registered but undeployed. The engine, its
limits and its failure reason are exposed in `GET /status.engines[]`; nothing
substitutes an unavailable engine silently.

## Custom ComfyUI workflows

Music engines → **Custom workflows** accepts a ComfyUI **Export (API)**
JSON graph (1 MiB), a name and one saved endpoint. Records live in
`comfy-workflows.json` beside `comfy-targets.json` and are written under the
same lock/atomic-replace pattern. Each record becomes an engine with id
`comfy_workflow:<uuid>`, so a draft can name exactly that graph.

The graph must be a dict of nodes with `class_type` and `inputs`, and exactly one
node must be an audio save node (`class_type` starting `SaveAudio`). Its
`filename_prefix` is forced to `music-studio/workflow`. A workflow must use
`{{style}}` or `{{lyrics}}`; `{{duration}}`, `{{seed}}`, `{{title}}` and
`{{instrumental}}` are also available. An input string that is exactly one
placeholder becomes the take's typed value (float seconds, int seed, bool
instrumental); a placeholder inside a longer string is replaced as text.
Instrumental takes send `''` for `{{lyrics}}`.

Health asks the endpoint for `/object_info`, checks every `class_type` in the
graph against it and names the missing ones (`/system_stats` only adds device
metadata when the server has it). Test in the UI does
that plus the placeholder report and never renders. A render reuses the MiniMax
ComfyUI queue/history/download/transcode path and is pinned to its endpoint like
a MiniMax take. Only the endpoint saved with the workflow is contacted.
The graph runs as-is on your own ComfyUI; only you can upload one (the agent has no tool for it), and ComfyUI keeps saves inside its output folder.

## One render at a time

The `.worker.lock` lease is held for the whole render (or batch). `enqueue`
refuses while any take is queued/running (`GPU busy`), and the worker takes the
lease non-blockingly before claiming. Kernel release on process death is what
makes crash recovery automatic.

## Recovery

Takes record `pid` and `/proc` start time. On import and every `GET /status`, a
queued/running take whose worker is gone becomes `failed: "worker died"` and the
lease frees. Takes under ten seconds old with no pid are skipped. Live workers
are never touched. `POST /recover {"confirm": true}` is the manual sweep for a
wedged-but-alive worker; it fails every in-flight take and is 409 without the
confirmation.

## Readiness

Approval requires non-blank caption (≤512), lyrics unless instrumental (≤4096),
duration 10–180 s, and sections that partition the duration. Any dashboard save
revokes approval; `/approve` must carry the `updated_at` token the form was
loaded with. A seed reproduces a take only on the same runtime and hardware.

## Status first

Every render problem starts at `music_studio {"action":"status"}`: engine
availability with the failing check, and each take's state and error. The full
route table, lock model and lineage fields are in `docs/ARCHITECTURE.md`;
measurements are in `docs/AUDIO-QC.md`; the complete limit list is `LIMITS.md`.
