# Reference

Every tool the plugin registers, every slash command, every CLI command, every hook, every HTTP route
and every configuration key.

Applies to 1.0.0 · revised 2026-11-11 · back to [docs/README.md](README.md).

## Agent tool: `music_studio`

Registered in `__init__.py` with toolset `music_studio`. One action per call; the approval gate is
enforced by the store, not by the caller.

| Parameter | Type | Required | Meaning |
|---|---|---|---|
| `action` | string enum | yes | One of `status`, `doctor`, `create`, `update`, `change`, `keep`, `snippet`, `snippet_splice`, `check_take`, `checkpoint`, `checkpoints`, `restore`, `approve`, `render`, `queue`, `takes`, `export`, `wait`, `compare`, `compare_status`, `set_cover` |
| `project_id` | string | action-dependent | Project UUID |
| `take_id` | string | action-dependent | Take UUID |
| `draft` | object | for create/update | Draft fields: `title`, `caption`, `lyrics`, `duration_seconds`, `instrumental`, `engine`, `bpm`, `keyscale`, `vocal_language`, `sections`, `minimax_guidance`, `comfy_target_id` |
| `seed` | integer | no | Render seed; omitted means a random seed |
| `updated_at` | string | for approve | The draft version the person reviewed |
| `image_path` | string | `set_cover` | Absolute path to a PNG/JPEG/WebP under 2 MiB, inside the studio folder, Downloads, Pictures or temp |
| `engines` | array of string | no | `compare`: engine ids in order; defaults to every ready engine |
| `instruction` | string | for change | Plain words, e.g. "add a fiddle" |
| `start_s`, `end_s` | number | for snippet | Range in the parent take |
| `retry` | boolean | no | `check_take`: re-render a measurable failure |
| `label`, `playhead_s` | string / number | no | `checkpoint` |
| `checkpoint_id` | string | for restore | Saved spot |
| `timeout_s` | integer | no | Wait limit, capped at 240 |

Action results (all return a JSON object; a refusal is `{"success": false, "error": "…"}`):

| Action | Required inputs | Returns |
|---|---|---|
| `status` | — | Engines, availability, limits, projects and takes with state/progress/ETA |
| `doctor` | — | Each engine with its checks and a fix hint |
| `create` | `draft` | `{success, project}` |
| `update` | `project_id`, `draft` | `{success, project}`; revokes approval |
| `change` | `project_id`, `instruction` | `{success, project, changed, notes, section, note}`; revokes approval |
| `keep` | `project_id`, `take_id` | `{success, kept_take_id}`; does not revoke approval |
| `snippet` | `project_id`, `take_id`, `start_s`, `end_s`, `instruction` | Queued child take |
| `snippet_splice` | `project_id`, `take_id` | Spliced take |
| `check_take` | `project_id`, `take_id` | Check report, or a retry take |
| `checkpoint` | `project_id`, optional `label`, `playhead_s` | `{success, checkpoint, saved, pruned}` |
| `checkpoints` | `project_id` | List of saved spots |
| `restore` | `project_id`, `checkpoint_id` | Restored project; approval revoked |
| `approve` | `project_id`, `updated_at` | Approved project |
| `render` | `project_id` | Queues and waits; reports the take or `running` |
| `queue` | `project_id` | Queues and returns immediately |
| `takes` | `project_id` | Project id, title, approval and take list |
| `export` | `project_id`, optional `take_id` | `{success, export}` with the written path — always the configured `export_dir` (or `~/Music/Hermes Music Studio`); a model cannot choose it |
| `wait` | `project_id`, optional `take_id`, `timeout_s` | Take state |
| `compare` | `project_id`, optional `engines`, `seed` | Plan of takes |
| `compare_status` | `project_id` | Plan and each take's state |
| `set_cover` | `project_id`, `image_path` | Stores the image as the project's cover; nothing is generated |

## Slash command: `/song`

Registered by `__init__.py`; implementation in `music_studio/chat.py`. It may show and queue, never
wait for a render.

| Command | Result |
|---|---|
| `/song` | The library, with short ids and engines |
| `/song go <id>` | The draft that would render; queues nothing |
| `/song go <id> now` | Approve that version and queue one take |
| `/song takes <id>` | Takes with state, engine and measured length |
| `/song play <id>` | The newest finished take's audio URL |
| `/song export <id>` | Export the newest finished take |
| `/song change <id> <words>` | Rewrite the draft deterministically |
| `/song keep <id>` | Remember the newest finished take |
| `/song check <id> [retry]` | Check the take; optional retry |
| `/song save [label]` / `/song saves` / `/song back <id>` | Save spot, list spots, restore |
| `/song snippet <id> <from>-<to> <words>` | Re-render only that range |
| `/song splice <id> <take>` | Copy the parent back outside the range |

## Hooks

None. `plugin.yaml` declares `provides_hooks: []`, and no module calls `register_hook`.

## CLI scripts

| Command | What it does |
|---|---|
| `python3 scripts/ms.py status` | Library and engine status |
| `python3 scripts/ms.py doctor` | Engine checks |
| `python3 scripts/ms.py create --draft '<json>'` | Create a draft |
| `python3 scripts/ms.py update --project <id> --draft '<json>'` | Update a draft |
| `python3 scripts/ms.py approve --project <id> --updated-at <token>` | Approve |
| `python3 scripts/ms.py render --project <id> [--take <id>] [--seed N] [--timeout N]` | Queue and wait |
| `python3 scripts/ms.py queue --project <id>` | Queue and return |
| `python3 scripts/ms.py takes --project <id>` | List takes |
| `python3 scripts/ms.py export --project <id> [--take <id>] [--folder DIR]` | Export a WAV |
| `python3 scripts/ms.py verify` | Call `/verify.json` |
| `python3 scripts/ms.py host` | Print the base URL and data directory |
| `python3 scripts/ms.py compare --project <id> [--engines a,b] [--seed N]` | Start a compare |
| `python3 scripts/ms.py compare-status --project <id>` | Compare progress |
| `python3 scripts/smoke.py [port] [--export]` | Read-only checks against a running build |
| `bash scripts/deploy.sh` | Install, stamp `BUILD_INFO.json`, restart and verify served |
| `python3 scripts/verify-served.py [port]` | Fail when the served copy is not this tree |
| `python3 scripts/spectrogram.py <wav> <png>` | Render a spectrogram (dev tool) |

`ms.py` exits `0` on success, `1` when the studio refuses and `2` when no host answers. It finds the
host from `studio-host.json`, or `--base` / `MUSIC_STUDIO_BASE`.

## HTTP routes

The plugin mounts `dashboard/plugin_api.py` at `/api/plugins/hermes-music-studio`. The standalone
server mounts the same router at `/api` and adds the routes marked *standalone*. All routes accept and
return JSON unless noted. POST bodies are capped at 256 KiB.

### Status, drafts and approval

| Method | Path | Purpose |
|---|---|---|
| GET | `/status` | Engines with limits/capabilities, default engine, projects and takes, progress/ETA, warnings; reconciles dead workers |
| GET | `/doctor` | Engine checks and fix hints |
| GET | `/demo`, `/demo/audio`, `/demo/audio-json` | Shipped demo card, WAV and base64 audio |
| POST | `/create` | `{draft}` → project |
| POST | `/update` | `{project_id, draft, base_updated_at?}`; revokes approval; stale token refused |
| POST | `/change` | `{project_id, instruction}` → deterministic draft change |
| POST | `/approve` | `{project_id, base_updated_at}`; token mandatory |
| POST | `/rename` | `{project_id, title}` |
| POST | `/delete` | `{project_id}`; refused while rendering |
| POST | `/duplicate` | `{project_id, title?}` → copy with its WAVs |
| GET | `/presets`; POST `/presets`; DELETE `/presets/{name}` | Style presets |

### Renders and take operations

| Method | Path | Purpose |
|---|---|---|
| POST | `/generate` | `{project_id, seed?, lock_seed?, engine_id?}` → queued take |
| POST | `/repaint` | `{project_id, take_id, start_s, end_s, caption?, lyrics_seed?}` → child take |
| POST | `/extend` | `{project_id, take_id, extend_seconds, caption?, lyrics?}` → child take |
| POST | `/batch` | `{project_id, count, seed_base?, engine_id?}` → serial batch |
| POST | `/batch/cancel` | `{project_id}` → cancel queued takes; the running one finishes |
| POST | `/versions` | `{project_id, count?, seed_base?}` → several takes from one draft |
| POST | `/section/render` | `{project_id, take_id, start_s, end_s, caption?}` → section child |
| POST | `/section/splice` | `{project_id, take_id, candidate_take_id, fade_ms?}` → spliced take |
| POST | `/snippet` | `{project_id, take_id, start_s, end_s, instruction}` |
| POST | `/snippet/splice` | `{project_id, take_id}` |
| POST | `/fade` | `{project_id, take_id, seconds?}` → faded child |
| POST | `/take-check` | `{project_id, take_id, retry?}` |
| POST | `/take/delete` | `{project_id, take_id}` |
| POST | `/compare` | `{project_id, engines?, seed?}` → plan |
| GET | `/compare/engines` | Engines that can take part |
| GET | `/compare/{project_id}` | Compare status |
| POST | `/preview/chorus` | `{project_id, seconds?}` → chorus preview |
| POST | `/recover` | `{confirm: true}` → fail every queued/running take; 409 without it |
| POST | `/checkpoint` | `{project_id, label?, pending?, playhead_s?, loop?}` |
| GET | `/checkpoints/{project_id}` | List saved spots |
| POST | `/checkpoint/restore` | `{project_id, checkpoint_id}` |

### Audio

| Method | Path | Purpose |
|---|---|---|
| GET | `/projects/{project_id}/takes/{take_id}/audio` | Streamed WAV, `?edit=<id>`; declares its length |
| GET | `/projects/{project_id}/takes/{take_id}/download` | Same bytes as an attachment |
| GET | `/projects/{project_id}/takes/{take_id}/audio-json` | Base64 WAV, 8 MiB cap, `?edit=` |
| GET | `/projects/{project_id}/takes/{take_id}/peaks` | Server-computed waveform buckets, `?buckets=`, `?edit=` |

### Studio UI, settings and library

| Method | Path | Purpose |
|---|---|---|
| GET | `/studio/ui` | The studio HTML |
| GET | `/studio/assets/{name}` | The studio CSS/JS |
| GET | `/studio/sample` | Shipped instrumental sample |
| GET | `/studio/covers/{project_id}/json` | Cover image as JSON |
| GET/POST/DELETE | `/studio/targets`, `/studio/targets/{id}` | Named ComfyUI endpoints |
| POST | `/studio/targets/{id}/probe` | Probe one target |
| GET | `/studio/hardware` | Read-only `nvidia-smi` inventory |
| GET | `/studio/host` | Start or find the loopback host |
| POST | `/host/close` | Stop the host (answers first) |
| GET/POST | `/settings` | Read/write engine settings |
| POST | `/settings/test` | `{url}` → probe an endpoint |
| POST | `/discover` | Find a renderer |
| POST | `/export` | `{project_id, take_id, folder?, title?}` |
| GET | `/library`; POST `/library/relocate`, `/library/reset` | Library folder info, move/copy, reset |
| POST | `/studio/setup/comfy`, `/studio/setup/ace` | Test and save an engine server |
| GET/POST | `/studio/setup/hf`; POST `/studio/setup/hf/copy` | Hugging Face weight plan/download/copy |
| GET/POST | `/studio/setup/cloud`; POST `/studio/setup/cloud/test` | Hosted provider entry and test |
| GET | `/verify`, `/verify.json` | What this build can measure |
| GET | `/v1/version` | Version, commit, date, dirty, channel |
| GET | `/about`; POST `/about/diagnostics` | Build/companion info and a copyable report |
| GET | `/update`, `/update/job`; POST `/update/apply`, `/update/undo` | Update notice; apply/undo refuse because Hermes owns updates |

### Standalone additions

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | The studio page |
| GET | `/api/studio/models`; POST `/api/studio/ideas` | Hermes model catalog and creative partner |
| GET/POST | `/api/studio/workspaces` | List/create workspaces |
| POST | `/api/studio/assign` | Assign a project to a workspace |
| GET/POST | `/api/studio/covers/{project_id}` | Read or upload cover art (no generation route) |
| GET | `/api/studio/revamp/status`, `/tracks`, `/playlists`, `/added` | REVamp state |
| POST | `/api/studio/revamp/playlists`, `/play`, `/add`, `/add-take`, `/playlist`, `/remove` | REVamp actions |

The standalone server accepts only a loopback `Host`, requires `application/json` on writes that carry
a body, and rejects cross-site requests. It has no authentication and should not be exposed to a
network.

## Configuration keys

Full detail is in [CONFIGURATION.md](CONFIGURATION.md). The defaults:

| Key | Default |
|---|---|
| `comfy_url` | `""` |
| `scan_hosts` | `""` |
| `export_dir` | `""` → `~/Music/Hermes Music Studio` |
| `ace_runtime_dir`, `ace_source_dir`, `ace_remote_url` | `""` |
| `minimax.steps` | `30` |
| `minimax.cfg` | `4.0` |
| `minimax.cfg_scale` | `1.5` |
| `minimax.top_k` | `50` |
| `minimax.sampler` | `euler` |
| `minimax.scheduler` | `simple` |

Environment variables include `HEWN_STUDIO_DATA_DIR`, `HEWN_STUDIO_HOST`, `HEWN_STUDIO_HOST_PORT`,
`HEWN_STUDIO_HOST_PYTHON`, `HEWN_STUDIO_IDLE_EXIT_S`, `HEWN_STUDIO_PARENT_PID`, `HEWN_STUDIO_NO_HOST`,
`ACE_RUNTIME_DIR`, `ACE_SOURCE_DIR`, `ACE_REMOTE_URL`, `ACE_REMOTE_API_KEY`, `COMFYUI_URL`,
`MUSIC_STUDIO_SCAN_HOSTS`, `MUSIC_STUDIO_GPU_LOCK`, `MUSIC_STUDIO_BASE`, `HERMES_HOME`,
`HERMES_MUSIC_HERMES_COMMAND`, `HERMES_MUSIC_HERMES_REPO`, `HERMES_MUSIC_REVAMPCTL`, `REVAMP_PORT`,
`XDG_DATA_HOME`.

## Error shape

Every JSON error is `{"detail": "…"}` from FastAPI, or `{"success": false, "error": "…"}` from the
tool and the CLI. Status codes: 400 malformed/unknown, 404 missing project/take, 409 approval, busy,
stale or unsupported operation, 413 oversized body, 415 wrong content type, 503 engine or spawn
failure, 500 unexpected.
