# Roadmap

What is planned after 1.0.0. Nothing here ships until it is built, tested on Linux, Windows and macOS, and
listed in [CHANGELOG.md](../CHANGELOG.md).

## Next update

### First: guided help that ships with the plugin
- **Studio doctor**: one tool that Hermes (and other agents) can call to troubleshoot and tune the
  studio, shipped with the plugin so a user's own Hermes can fix their setup.
  - *Diagnose*: reads the last lines of `logs/studio.log` and the failed take's worker log, tests every
    saved engine, and checks the newest take (silence %, longest gap, clipping, length against the
    request), then answers in plain words with the fix.
  - *A/B tune*: renders the same song and seed with two settings (for example CFG 1.7 against 4.0) and
    reports which came out cleaner by those measures, so a setting is changed on evidence.
  - Same checks from the page (**Check my setup**) and from `scripts/ms.py doctor`.
- **Setup guide skill**: asks what hardware you have (NVIDIA, AMD, Intel, Apple Silicon, no graphics card)
  and where the engine should run (this computer, another computer on your network, or a hosted server),
  then walks you through it step by step.
- **Idea and sound coach skill**: helps you spitball ideas, then fine-tune how the song sounds: turns
  "warmer", "more 80s" or "less busy" into exact style words, tempo and key, and suggests A/B versions on
  the same seed.
- **Change one part so the song still flows**: render the new part a little wider than the chosen range,
  blend over 1–2 seconds, and cut on the beat using the song's tempo. Later, make the part from the song's
  own audio on ComfyUI ACE-Step (see below).
- The agent tool gets the same "render the part on its own" fallback the page has, so changing one part
  works through chat on engines without repaint (MiniMax).
- Windows test fixes: skip the FLAC fixture tests when ffmpeg is missing, compare export paths
  case-insensitively, and fix the worker-cleanup check on Windows.


### Setting up a music engine, step by step
- A guide at the top of **Music engines**: what do you have, a check of this computer's hardware, one
  recommended path, a test, done. Plain tooltips on every field, and a ready path for "ComfyUI on another
  computer". The current panels move under **Advanced**.
- Advice that follows the hardware: on a computer without a supported graphics card, recommend a server
  you already run (or ComfyUI on another computer) before offering the 14.7 GB download.
- Notice a ComfyUI already running on this computer when the page opens, and offer it.
- One place for the ComfyUI address (today Settings and the engine list keep separate copies).
- "Hosted service" renamed to what it is (an ACE-Step-compatible server), and its test checks the protocol
  the render uses.

### Let your AI do it
- **Let my AI set it up**: opens a Hermes chat with the request already written; you press Send.
- New `music_studio` tool actions so the agent can finish setup itself: list, test, add and remove engines,
  check hardware, find servers.
- **Ask Hermes to make the cover**: opens a chat asking the agent to make the artwork with its own image tool
  and hand it to the song (`set_cover`).

### Revising a song
- **Revise this song**: say what to change; Hermes' model edits the saved lyrics, style, tempo and key; the
  song is rendered again on the same engine with the same seed, as a new version listed under the song.
- Versions side by side (A/B) and a **Keep** button.
- True "same recording, small changes" on ComfyUI ACE-Step (source audio in, partial denoise): needs the
  node pack checked on a real server first.

### Reliability
- After an update, replace the running studio host automatically (through its authenticated close route)
  instead of waiting for Hermes to restart.
- Profiles that hold different copies of the plugin can no longer collide (package names unique to this
  plugin).
- Renders measured on real AMD, Intel and Apple Silicon machines (today only detection is tested there).

## Later
- Other hosted music services, each with its own tested adapter.
- A one-step installer that leaves a working engine on any supported hardware.
