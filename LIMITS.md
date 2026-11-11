# Limits

This page names what Music Studio does not do, what it does unreliably, and what
it needs. Every item here was found by reading the shipped code, by a test, or
by a real render — none are hedges added after a bug report.

Version 0.2.0. Measurements are from the machines named in each row.

## Take length

| Limit | Value | Where it is enforced |
|---|---|---|
| Shortest take | 10 s | `store.DURATION_MIN_SECONDS`, `Engine.limits` |
| Longest take on ACE | 180 s | `ace_local` `DEFAULT_LIMITS`; enforced by `store.engine_limits()` for a draft pinned to `ace`/`ace_local` |
| Longest take on MiniMax | 360 s | `MiniMaxM3ComfyEngine.limits`; enforced by `store.engine_limits()` for a draft pinned to `minimax_m3_comfy` |
| Longest length actually rendered | 180 s (ACE), 12 s (MiniMax) | measurement log |
| Repaint range | 3–30 s | `store.REPAINT_MIN/MAX_SECONDS`, `ace_engine.repaint` |
| Extend amount | 10–60 s, source ≥ 20 s, total ≤ 600 s | `ace_engine.plan_extension` |

- **The duration cap belongs to the engine.** A draft pinned to
  `minimax_m3_comfy` may ask for up to 360 s; the same draft on `ace`/`ace_local`
  is refused with that engine's own 10–180 s window. The top-level
  `/status.limits` describe the default drafting engine (`ace_local`); the
  per-engine numbers are in `engines[].limits`.
- **360 s is untested.** The MiniMax adapter and draft validator accept it, and
  no 360 s take has been rendered on any host. Do not treat 360 s as supported
  because the constant says so.
- The 180 s ceiling is a measurement, not the model's own limit. ACE-Step
  buckets audio higher; 180 s is what an RTX 4070 Ti (12 GB, `offload_to_cpu`)
  rendered without OOM. Raising it needs a new measurement.
- A 180 s take is a clip, not a finished song. There is no arrangement editor
  beyond sections, and no mixing.

## Engines and operations

| Capability | `ace_local` | `minimax_m3_comfy` |
|---|---|---|
| Generate | yes | yes |
| Instrumental | yes | yes |
| Repaint a range | yes | **no** (no conditioning nodes in the graph) |
| Extend | yes | **no** |
| Batch in one engine call | **no** | **no** |

- **MiniMax cannot repaint or extend.** Its ComfyUI graph has no repaint or
  continuation nodes; the engine declares `repaint=false`,
  `continuation=false` and the request is refused with 409 before any HTTP call.
  You must generate a fresh take to change a section.
- **No engine declares `batch=true`.** The studio's batch is one worker
  looping over takes; it is serial and holds the GPU lease for the whole batch.
- **`ace_remote` is not a usable engine here.** It is registered and probes
  `/health`, but no server is deployed for it in this installation and it has
  never been exercised against a live `acestep.api_server`.
- A repaint or extend is only allowed on a `succeeded` take, and the child
  inherits the parent's engine. To edit on a different engine you must generate
  a new parent there.

## Audio quality — measured, not judged

| Claim | Evidence |
|---|---|
| ACE-Step 1.5 turbo produced no intelligible words on the measured take | Whisper recovered **0 of 3** requested lines, twice, including on a Demucs-isolated vocal stem |
| MiniMax Music 3 produced recoverable words on the measured take | Whisper recovered the requested lines from the isolated stem of a MiniMax take |
| Takes can render shorter than requested | a 15 s ACE request → **11.7 s** of music + **3.0–3.3 s** tail silence |
| Repaint preserves the untouched audio only up to a global gain | measured global gain **0.89125** on a 40 s parent; head/tail residual **3.1e-5** (16-bit rounding), not bit-identical at the sample level |
| Extend keeps the parent's head | a 65 s extended take had a bit-identical head |

Caveats that belong with those numbers:

- **ASR is a proxy.** Word recovery does not measure pitch, timing, phrasing or
  whether a human would call it singing. Only a listen confirms quality.
- **One take per engine.** These are single-take observations, not a benchmark
  across prompts, languages or seeds.
- **No gain-locked composite.** A repaint returns the engine's own normalised
  master, so the child's absolute level can differ from the parent's by the
  global gain above. The splice in `extend` is equal-gain, but the material
  around it is not re-levelled to the parent.
- **Extend accepts only 48 kHz stereo pcm_s16le.** Anything else is refused
  rather than resampled.
- **MiniMax audio is transcoded.** ComfyUI cannot save WAV; the adapter saves
  FLAC and converts with ffmpeg to PCM s16le at the model's native 44.1 kHz.
  That is lossless relative to the FLAC, but it is a conversion.
- No stems, no LoRA/adapter training, no reference-audio conditioning, no
  mixing, no mastering, no vocal tuning, no lyric timing export.

## Hardware and runtime

| Requirement | Detail |
|---|---|
| OS | Linux; the plugin uses `fcntl` locks and `/proc` |
| For `ace_local` | CUDA GPU with ~12 GB free, ACE-Step 1.5 runtime venv + checkpoints, ACE source tree |
| For `minimax_m3_comfy` | reachable ComfyUI with MiniMax Music 3 nodes and weights; `ffmpeg` on the Hermes host |
| Users | one OS user, one Hermes profile |
| Concurrency | one render per profile, process-wide lease |

- Measured ACE: 180 s take in ≈ 50 s wall at ≈ 8.5 GB peak (RTX 4070 Ti).
  Whole-render times including model load were 88–120 s uncontended, and a
  180 s take took 553.5 s while another render competed for the GPU.
- Measured MiniMax: 10 s take ≈ 42 s wall; 12 s take ≈ 57 s wall (DGX Spark
  GB10 over ComfyUI).
- Other GPUs and hosts are untested. The timings are not a promise for yours.
- The MiniMax weight filenames are constructor constants, not environment
  variables. An install using different filenames needs a code change.
- The ACE subprocess gets `HF_HUB_OFFLINE=1` and friends. Those are environment
  hints, not a network sandbox; the adapter itself makes no network calls.

## Data and delivery

- **The desktop panel's `/audio-json` route refuses audio above 8 MiB** (about
  44 s at 48 kHz or 48 s at 44.1 kHz, 16-bit stereo). The dashboard tab streams
  `/audio` and has no such cap; `/download` always serves the full file.
- Audio and project files live only under the profile's plugin-data directory.
  Deleting it deletes every project, take and WAV; there is no undo.
- A take is recoverable after a crash, but a worker that is alive and wedged
  needs the guarded `POST /recover` sweep, which fails every in-flight take.
- QC is metadata. It never repairs audio, and a QC failure leaves a take
  succeeded with `qc: null`. Takes rendered by older workers have no `qc`.
- No ASR pass is wired into the worker, so `vocal_presence` is `unknown` on
  every take the product produces today.
- Project IDs are UUIDs; there is no multi-user access control. Anyone who can
  reach the dashboard API can approve and render.
- **Keep this one** is client-side state in the dashboard; it is not written to
  the project and does not survive a reload.

## Reproduction

- **A seed is a replay handle on one machine.** The same seed, draft, runtime and
  hardware reproduce a take. A different GPU, driver or checkpoint does not
  promise the same audio.
- No automated test performs a real render. The suites exercise the store, the
  state machine, the adapters against fakes (including a fake ComfyUI), the tool
  handler and the clients. Audio quality is verified by listening.

## What is not missing by accident

There is no mock engine and no synthesised fallback. If nothing can render,
`/status` says which engine is unavailable and why, and a render errors instead
of returning placeholder audio.
