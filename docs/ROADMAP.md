# Roadmap

What is planned after 1.0.0. Nothing here ships until it is built, tested on Linux, Windows and macOS, and
listed in [CHANGELOG.md](../CHANGELOG.md).

## Next update

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
