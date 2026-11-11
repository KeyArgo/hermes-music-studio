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

`ace_local` delegates to `ace_engine` and the runtime at `ACE_RUNTIME_DIR`
(default `~/ace15-runtime`), source at `ACE_SOURCE_DIR`
(default `~/ACE-Step-1.5`). `minimax_m3_comfy` talks to
ComfyUI at `COMFYUI_URL` (default `http://192.0.2.52:8188`) and needs `ffmpeg`
for FLAC → WAV. `ace_remote` is registered but undeployed. The engine, its
limits and its failure reason are exposed in `GET /status.engines[]`; nothing
substitutes an unavailable engine silently.

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
