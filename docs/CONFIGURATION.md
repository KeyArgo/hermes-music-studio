# Configuration

Every environment variable and saved setting the code actually reads, with its default and the file
that reads it.

Applies to 1.0.0 · revised 2026-11-11 · back to [docs/README.md](README.md).

Settings saved in the studio (Compute → Render settings) win over most environment variables where
both exist; the exceptions are noted. Nothing here is a cloud credential except the optional hosted
provider entry, which is written to its own `0600` file and never sent back to the page.

## Where the data lives

| Variable | Default | Read by | Meaning |
|---|---|---|---|
| `HEWN_STUDIO_DATA_DIR` | unset | `music_studio/service.py`, `music_studio/settings.py`, `scripts/ms.py` | Overrides the data root for the plugin and the standalone server. In Hermes the plugin uses `plugin_data_dir("hermes-music-studio")`; standalone uses `$XDG_DATA_HOME/hewn-music-studio` |
| `HERMES_HOME` | `~/.hermes` | `music_studio/service.py` | Base for the plugin data path when Hermes' `plugin_storage` is unavailable |
| `XDG_DATA_HOME` | `~/.local/share` | `standalone/app.py` | Parent of the standalone `hewn-music-studio` folder |

The legacy `hewn` names are kept on purpose: renaming them would orphan every existing library.

## Studio host (loopback UI)

| Variable | Default | Read by | Meaning |
|---|---|---|---|
| `HEWN_STUDIO_HOST` | `127.0.0.1` | `music_studio/host.py` | Bind address for the loopback host |
| `HEWN_STUDIO_HOST_PORT` | `8775` | `music_studio/host.py`, `host_launcher.py` | First port to try; the launcher searches the next 12 free ports |
| `HEWN_STUDIO_HOST_PYTHON` | unset | `music_studio/host_launcher.py` | Interpreter used for the host; otherwise `/usr/bin/python3.13`, then the current interpreter |
| `HEWN_STUDIO_IDLE_EXIT_S` | `300` | `music_studio/lifecycle.py` | Seconds without a request before the host exits; `0` disables |
| `HEWN_STUDIO_PARENT_PID` | unset | `music_studio/lifecycle.py` | The launcher's pid; the host exits when that process is gone |
| `HEWN_STUDIO_NO_HOST` | unset | `standalone/app.py`, `dashboard/__init__.py` | Set to `1` so the standalone server does not start a second host |

## Engines

| Variable | Default | Read by | Meaning |
|---|---|---|---|
| `ACE_RUNTIME_DIR` | unset (`/nonexistent/ace-runtime`) | `ace_engine.py` | ACE-Step runtime root: `venv/bin/python`, `checkpoints/`, `ace-studio-gpu.lock` |
| `ACE_SOURCE_DIR` | unset (`/nonexistent/ace-source`) | `ace_engine.py` | ACE-Step source checkout containing `acestep/` |
| `COMFYUI_URL` | empty (no baked address) | `music_studio/engines/minimax_m3_comfy.py`, `discovery.py`, `service.py` | ComfyUI base URL for MiniMax Music 3 |
| `ACE_REMOTE_URL` | empty | `music_studio/engines/ace_remote.py` | `acestep.api_server` base URL for the undeployed remote adapter |
| `ACE_REMOTE_API_KEY` | unset | `music_studio/engines/ace_remote.py` | Optional bearer token for that server. Declared in `requires_env`, resolved with `agent.secret_scope.get_secret` (profile scope first, environment as the standalone fallback) |
| `MUSIC_STUDIO_SCAN_HOSTS` | empty | `music_studio/discovery.py`, `service.py` | Extra comma-separated hostnames for "Find a renderer" |
| `MUSIC_STUDIO_GPU_LOCK` | `<runtime>/ace-studio-gpu.lock` | `music_studio/engines/minimax_m3_comfy.py` | Override the local GPU lock path used when ComfyUI is on this machine |

Saved engine paths are injected into the host process by `music_studio/host_launcher.py`:
`ACE_RUNTIME_DIR`, `ACE_SOURCE_DIR` and `ACE_REMOTE_URL` come from `engine-settings.json` when set.

## Export and external tools

| Variable | Default | Read by | Meaning |
|---|---|---|---|
| `HOME` | — | `music_studio/export.py` | Default export folder is `~/Music/Hermes Music Studio` |
| `MUSIC_STUDIO_BASE` | discovered | `scripts/ms.py` | Base URL for the CLI; otherwise the port from `studio-host.json`, then `8775` |
| `HERMES_MUSIC_HERMES_COMMAND` (unused since the ctx.llm move) | `hermes` on `PATH`, then `~/.local/bin/hermes` | `standalone/ideas.py` | Launcher for the creative partner / cover generation |
| `HERMES_MUSIC_REVAMPCTL` | `revampctl` on `PATH`, then `~/.local/bin/revampctl` | `standalone/revamp.py` | REVamp client executable |
| `REVAMP_PORT` | `8114` | `standalone/app.py` | Origin allowed to read take audio for REVamp |

`ffmpeg` and `nvidia-smi` are found on `PATH`; there is no environment variable for either.

## Saved settings

`engine-settings.json` lives next to the library (or beside the plugin data when the library was
moved). It is written atomically and validated on save.

| Key | Default | Accepted | Meaning |
|---|---|---|---|
| `comfy_url` | `""` | `http://` or `https://` URL, trailing slash removed | ComfyUI endpoint |
| `scan_hosts` | `""` | comma-separated hostnames | Extra machines for "Find a renderer" |
| `export_dir` | `""` (`~/Music/Hermes Music Studio`) | absolute path | Where Export writes. Also the boundary: with it set, nothing outside it is writable; without it, anything inside `~/Music` |
| `ace_runtime_dir` | `""` | path | ACE runtime, used to start the host |
| `ace_source_dir` | `""` | path | ACE source checkout |
| `ace_remote_url` | `""` | URL | ACE remote server |
| `minimax.steps` | `30` | integer 1–100 | ComfyUI sampler steps |
| `minimax.cfg` | `4.0` | number 0–20 | CFG |
| `minimax.cfg_scale` | `1.5` | number 0–10 | MiniMax music guidance |
| `minimax.top_k` | `50` | integer 1–200 | Top K |
| `minimax.sampler` | `euler` | `euler`, `euler_ancestral`, `dpmpp_2m`, `dpmpp_2m_sde`, `heun`, `lms` | Sampler |
| `minimax.scheduler` | `simple` | `simple`, `normal`, `karras`, `exponential`, `sgm_uniform` | Scheduler |

Other files in the same folder:

| File | Contents |
|---|---|
| `library.json` | `{"path": "<absolute folder>"}`; absent means the plugin's own data folder |
| `engine-preference.json` | `{preferred, allow_fallback, updated_at}` |
| `comfy-targets.json` | list of `{id, name, url}` named ComfyUI endpoints |
| `studio-workspaces.json` | workspace names and project ids |
| `presets.json` | saved style presets |
| `studio-host.json` | `{port, pid}` for the running loopback host |
| `checkpoints` array inside `<uuid>.json` | save spots (draft, keeper take, lineage, playhead); twenty are kept |
| `update-job.json`, `update-previous.json` | update bookkeeping; the studio refuses to update itself |

## Hosted provider keys

`POST /api/studio/setup/cloud` stores an optional hosted model provider in the plugin data folder with
mode `0600`. The response reports only whether a key is present; the key is never returned to the
page. The studio is not a hosted service and has no account of its own.

## Offline hints (not a sandbox)

The ACE subprocess is started with `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`,
`HF_DATASETS_OFFLINE=1`, `HF_HOME` and `TRITON_CACHE_DIR` pinned to the runtime. These are hints to
the model libraries, not a network namespace. The adapter itself makes no network calls.
