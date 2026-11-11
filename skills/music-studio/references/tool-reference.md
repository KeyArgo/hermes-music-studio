# `music_studio` tool reference

Grounded in `__init__.py` (schema/handler), `music_studio/store.py` (fields and
validation), `music_studio/jobs.py` (take lifecycle), and `ace_engine.py`
(engine-side limits). Error strings quoted exactly as the tool returns them.

## Schema

```json
{
  "name": "music_studio",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["status", "create", "update", "generate"]},
      "project_id": {"type": "string"},
      "draft": {"type": "object"},
      "seed": {"type": "integer"}
    },
    "required": ["action"]
  }
}
```

`draft` is a generic object in the schema; its permitted keys are enforced by
the store and listed below.

## Actions

### `status`

No other arguments.

```json
{"success": true,
 "engine_available": true,
 "engines": [{"id": "ace_local", "label": "ACE-Step 1.5 (local GPU)",
              "available": true, "limits": {"min_seconds": 10,
              "max_seconds": 180, "recommended_seconds": 120}},
             {"id": "minimax_m3_comfy", "label": "MiniMax Music 3 (ComfyUI)",
              "available": true, "limits": {"min_seconds": 10,
              "max_seconds": 360, "recommended_seconds": 120}}],
 "default_engine_id": "ace_local",
 "limits": {"min_seconds": 10, "max_seconds": 180, "recommended_seconds": 120},
 "limits_engine_id": "ace_local",
 "warnings": [],
 "projects": [{"project_id": "…", "title": "…", "idea": "…", "caption": "…",
               "lyrics": "…", "engine": "ace", "duration_seconds": 30,
               "approved": false, "updated_at": 1760000000.0,
               "takes": [{"take_id": "…", "state": "succeeded", "seed": 42,
                          "audio_file": "….wav", "error": null,
                          "pid": 1234, "pid_start": 998877,
                          "created_at": 1760000000.0,
                          "started_at": 1760000001.0,
                          "finished_at": 1760000120.0,
                          "progress": 100, "eta_seconds": 0,
                          "qc": {"requested_s": 15, "file_s": 15.0, "…": "…"},
                          "edits": [{"edit_id": "…", "kind": "trim", "…": "…"}],
                          "playable_edit_id": "…",
                          "metadata": {"engine_id": "ace_local", "…": "…"},
                          "snapshot": {"title": "…", "idea": "…", "caption": "…",
                                       "lyrics": "…", "engine": "ace",
                                       "duration_seconds": 30}}]}]}
```

The top-level `limits` are the **default drafting engine's** window (ACE
local); `limits_engine_id` names that engine. Each engine's real window is in
`engines[].limits`, and a draft pinned to an engine is validated against that
engine's numbers — MiniMax Music 3 accepts up to 360 s, ACE up to 180 s.

While a take is `running`, `progress` is a coarse percentage and
`eta_seconds` is a rough remaining time derived from this machine's mean render
duration. Both are estimates, not engine step counts. `warnings` lists project
files that could not be read and were skipped.

`engine_available` is true when at least one engine in `engines[]` reports
`available: true`. Each engine's own state/detail says what it needs — for ACE:
the runtime's `venv/bin/python`, its `checkpoints/` directory and the ACE
source's `acestep/` package; for MiniMax: a reachable ComfyUI with the MiniMax
Music 3 nodes and weights. Reading status also reconciles takes whose worker
process has died.

### `create`

```json
{"action": "create", "draft": {"title": "…", "idea": "…", "caption": "…",
                               "lyrics": "…", "engine": "ace",
                               "duration_seconds": 30}}
```

All draft keys are optional; omitted ones take defaults. Returns
`{"success": true, "project": {...}}`, `approved: false`.

### `update`

```json
{"action": "update", "project_id": "<uuid>", "draft": {"lyrics": "…"}}
```

Partial draft. Only changed values count: if every supplied value equals the
stored one, the draft and its approval are untouched. Any real change sets
`approved: false`. Returns the updated project.

### `generate`

```json
{"action": "generate", "project_id": "<uuid>", "seed": 123456}
```

`seed` is required and must be an integer `0 … 4294967295`; a missing seed
fails with `invalid seed`. Requires `approved: true` and a draft that passes
readiness. The engine is the one the draft pins (change it with `update`); the
HTTP `/generate` route also accepts an optional one-off `engine_id`, but the
chat tool schema does not expose it. Returns `{"success": true, "take": {...}}`
with `state: "queued"`.

## Draft fields and limits

| Field | Type | Limit |
|---|---|---|
| `title` | string | ≤200 characters, default `""` |
| `idea` | string | ≤2000 characters, default `""`; stored but never sent to the engine |
| `caption` | string | non-blank to render; ≤512 characters |
| `lyrics` | string | non-blank to render; ≤4096 characters |
| `engine` | string | `"ace"` (default; resolves to `ace_local`), `"ace_local"`, or `"minimax_m3_comfy"` |
| `duration_seconds` | integer | 10–180 on ACE, 10–360 on MiniMax; default 30 |
| `instrumental` | boolean | default `false`; when true, lyrics may be blank |
| `vocal_language` | string | `en es fr de it pt ja ko zh auto`; default `en` |
| `bpm` | integer or null | 40–220 |
| `keyscale` | string or null | ≤24 characters |

The duration window is the **pinned engine's**: the store validates against
`store.engine_limits(engine)`, so a 240 s draft is accepted on
`minimax_m3_comfy` and refused with `duration must be between 10 and 180
seconds` on `ace`/`ace_local`. The same rule is enforced at approval and again
when a render is queued. Unknown draft keys raise `unknown draft field`.

## Approval

- Approval happens only through the dashboard (`POST /approve`, surfaced as the
  Approve button in the Music Studio tab). The tool has no approve action by
  design.
- Approval runs readiness validation: `caption` and `lyrics` non-blank (lyrics
  may be blank when `instrumental` is true), duration inside the pinned
  engine's window (10–180 ACE, 10–360 MiniMax).
- The tool's `update` revokes approval only when a value changes. The dashboard's
  update route revokes it on every save. Expect "Approved" to become "Draft"
  after any dashboard save.

## Take lifecycle

```
queued ──claim──> running ──succeed──> succeeded
   │                  │
   │                  └──fail──> failed
   └──abort──> failed
```

- `queued`: admitted, waiting for the detached worker to claim it.
- `running`: the worker owns the GPU lease and is rendering.
- `succeeded`: `audio_file` names the WAV in the data directory, and the take
  is already settled — `qc` and (when warranted) `edits[]` with
  `playable_edit_id` were written in the same save as this state. A client that
  stops polling here has everything.
- `failed`: `error` says why; no audio.
- Each take carries `seed` and an immutable `snapshot` of the draft that was
  approved when it was queued.
- While `running`, the take carries a coarse `progress` percentage and an
  `eta_seconds` estimate; both are derived from elapsed time, not engine steps.
- Exactly one `queued`/`running` take per profile is admitted; a second attempt
  raises `GPU busy`.
- A take whose worker disappears is marked `failed` with `error: "worker died"`
  when status is read (or the plugin reloads). A take younger than ten seconds
  with no recorded pid is left alone while it is still being launched.
- A worker that exits without committing marks its own take `failed` with
  `worker exited without committing the take`.
- `POST /recover` with `{"confirm": true}` fails every queued/running take
  (`interrupted by restart`) after taking the render lease. It is the manual
  escape hatch; ordinary crashes need no operator.

## Error strings seen by the agent

| Error | Cause |
|---|---|
| `unknown action` | action not in the enum |
| `project not found` | id does not resolve to a stored project |
| `invalid UUID` | malformed project or take id |
| `unknown draft field` | draft key outside the six fields |
| `draft text fields must be strings` | non-string title/idea/caption/lyrics/engine |
| `invalid duration` | duration not an integer, or outside the pinned engine's window |
| `unsupported engine` | engine name is not one of `ace`, `ace_local`, `minimax_m3_comfy` |
| `title must be at most 200 characters` / `idea must be at most 2000 characters` | metadata over its UI cap |
| `caption must be at most 512 characters` / `lyrics must be at most 4096 characters` | text over the engine cap (checked at save and at approval) |
| `caption is required` / `lyrics is required` | approval or generate on a blank field |
| `duration must be between 10 and 180 seconds` | approval or generate outside the ACE window |
| `duration must be between 10 and 360 seconds` | approval or generate outside the MiniMax window |
| `draft requires approval` | generate before approval, or after an edit |
| `GPU busy` | another take is queued/running in this profile |
| `GPU busy: another render owns the worker lease` | a spawned worker found the lease taken and aborted its take |
| `invalid seed` | seed missing or not an int in 0 … 2^32−1 |
| `worker could not start` | the detached worker process could not be spawned |
| `worker exited without committing the take` | worker crashed before it could commit a result |
| `stale draft` | dashboard update used an outdated `base_updated_at` (reload and retry) |
| `project not found` | unknown id |
| `studio operation failed` | unexpected internal error (check the plugin log) |

## Audio delivery

Both routes take canonical UUIDs for project and take:

- `GET /projects/{project_id}/takes/{take_id}/audio` — streams the WAV.
- `GET /projects/{project_id}/takes/{take_id}/download` — same bytes with a
  `Content-Disposition` attachment filename.
- `GET /projects/{project_id}/takes/{take_id}/audio-json` — base64 JSON for the
  desktop panel, capped at 8 MiB; larger takes return 413
  `Audio exceeds the 8 MiB desktop playback limit`.

8 MiB is about 47.5 seconds of 44.1 kHz/16-bit stereo.

## What the engine actually receives

`ace_worker.py` builds one `GenerationParams` with `task_type="text2music"`,
`caption`, `lyrics`, `duration`, `seed`, `vocal_language="en"`,
`inference_steps=8`, `thinking=False`, `guidance_scale=1.0`, batch size 1. The
turbo checkpoint ignores guidance scale, so there is deliberately no CFG knob in
the product. `idea` never crosses this boundary.
