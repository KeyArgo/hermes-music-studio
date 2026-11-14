# Third-party components and licences

This plugin is MIT (`LICENSE`, `plugin.yaml: license: MIT`). Everything it
loads, talks to, or was built against is listed here, with the obligation that
comes with it.

## Required to render

### ACE-Step 1.5 — `ace_local`

- **What it is:** the local generative model behind the `ace_local` engine
  (`acestep-v15-turbo`, 8 inference steps).
- **Licence:** MIT, for the code **and** the weights. The installed checkout's
  `LICENSE` is "MIT License, Copyright (c) 2026 ACEStep".
- **Where it comes from:** <https://github.com/ACE-Step/ACE-Step-1.5>; weights
  at <https://huggingface.co/ACE-Step/Ace-Step1.5>.
- **How it is used:** not vendored and not distributed. The user installs the
  runtime and checkpoints; the plugin runs `ace_worker.py` inside that runtime's
  own virtualenv (`ACE_RUNTIME_DIR/venv/bin/python`) and imports `acestep` from
  `ACE_SOURCE_DIR`.
- **Obligation:** retain the MIT copyright and permission notice with copies of
  the code or weights you redistribute. Generated audio has no attribution
  requirement from this licence.

### MiniMax Music 3 — `minimax_m3_comfy`

- **What it is:** the model behind the `minimax_m3_comfy` engine, served by
  ComfyUI.
- **Licence:** the **MiniMax-Music3 Community License**. The adapter records the
  exact notice in `LICENCE_NOTICE`
  (`music_studio/engines/minimax_m3_comfy.py`).
- **Licence text:** <https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE>
- **How it is used:** not vendored. The plugin submits an API-format ComfyUI
  graph over HTTP and downloads the resulting audio.
- **Obligations, as stated in the notice the plugin carries:**
  1. Include the copyright and permission notice with copies of the model or
     its output.
  2. Display **“MiniMax-Music3”** in the interface of any commercial product or
     service that uses it.
- **Where the plugin satisfies (2):** every MiniMax take's stored `metadata`
  contains `licence: "MiniMax-Music3"`, `model_family`, `model_licence`,
  `licence_url` and `licence_notice`. The inspector names the engine of every
  take. If you export audio outside the studio, carry the notice with it — the
  studio cannot enforce what happens to a downloaded WAV.

## Runtime services (not distributed here)

### ComfyUI

- Needed by `minimax_m3_comfy` only. Upstream licence is GPL-3.0 at the time of
  writing; this plugin ships no ComfyUI code, only HTTP calls and node names
  taken from its public API (`comfy_extras/nodes_minimax_music.py`).
- The plugin does not install, configure or supervise ComfyUI. Its availability
  is the operator's responsibility.

### ffmpeg

- Needed by `minimax_m3_comfy` because ComfyUI cannot save WAV. The adapter
  writes FLAC and runs `ffmpeg -acodec pcm_s16le` as an external subprocess.
- Upstream licence is LGPL-2.1-or-later, or GPL-2.0-or-later for builds with
  `--enable-gpl`. It is not linked and not distributed; it must be on `PATH`.

### Hermes Agent

- The host application. Provides the plugin loader, dashboard mount, tool
  registration and `plugin_storage`. Not distributed with this plugin; its
  licence governs the host.

### `acestep.api_server` (for `ace_remote`)

- The HTTP service the undeployed `ace_remote` adapter targets. Same ACE-Step
  1.5 project and MIT licence as above.

## Python packages

The plugin declares **no Python dependencies** (`plugin.yaml` has none) and
imports only the standard library plus the host's `plugins.plugin_storage` and
FastAPI, both provided by Hermes. There is no `requirements.txt`, no lockfile,
and nothing is pip-installed by the plugin.

## Vendored assets

| Path | What it is | Licence |
|---|---|---|
| `assets/brand/stamp.svg` | the InovinLabs seal | original work, MIT |
| `assets/brand/banner.png` | Music Studio banner (InovinLabs brand pack) | original work, MIT |
| `dashboard/dist/index.js` | the studio UI | original work, MIT |

No web fonts, icon sets, JavaScript libraries or sample audio are bundled. The
banner's CSS names `Inter` and `DejaVu Sans`; they are referenced by family
name and fall back to `system-ui`, not embedded. There is no CDN reference and
nothing loads from the network at runtime except the configured engine endpoint.

## Test-only code

`tests/fake_comfy.py` and the fake ACE runtime are original test doubles. They
generate tones and minimal FLAC/WAV framing; no third-party audio or model
output is committed to the repository.
