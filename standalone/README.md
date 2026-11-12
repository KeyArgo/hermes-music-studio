# Hermes Music Studio standalone

The local standalone app uses the Music Studio draft, approval, take, and music
engine code. It listens on `127.0.0.1:8765` for one local user; it is not an
authenticated multi-user or internet-facing service. The Hermes desktop pane
frames the same app through a loopback host (`music_studio/host.py`).

Use Python 3.13 (the tested target) with FastAPI and Uvicorn installed. From the repository root:

```sh
python3 -m standalone.app
```

Open <http://127.0.0.1:8765/>. Projects are stored under
`$XDG_DATA_HOME/hewn-music-studio`, or `~/.local/share/hewn-music-studio` when
`XDG_DATA_HOME` is unset. `HEWN_STUDIO_DATA_DIR` selects a different data root.
The default is separate from the Hermes plugin library; deliberately choosing
the same root allows access to the same project files.

## Create and explore

- Choose Simple or Custom; Custom exposes tempo, key, vocal language, and
  MiniMax music guidance. Save and approve a draft before rendering.
- The creative partner lists configured Hermes providers and models. It uses
  Hermes's own runtime and credentials, with agent tools and memory disabled.
  Model use may incur the provider's ordinary charges. `hermes` must be on
  `PATH` or at `~/.local/bin/hermes`; `HERMES_MUSIC_HERMES_COMMAND` can name
  another launcher. If the Hermes source is elsewhere, set
  `HERMES_MUSIC_HERMES_REPO` to its checkout.
- Tone and style influence in the idea panel shape the text draft. In Custom
  mode, MiniMax's separate guidance slider controls its text-encoder CFG from
  0.5 to 3.0 and is saved with the take. ACE-Step turbo does not support that
  adjustment, so it is not offered for ACE.
- Workspaces group songs. Cover art can be uploaded (PNG, JPEG or WebP, up to
  2 MiB) or generated with the configured Hermes image provider. Image
  generation may incur provider charges and requires a working Hermes image
  backend. It is not needed for normal song creation.
- The alternate 17-second instrumental sample was generated locally with
  ACE-Step 1.5 turbo and trimmed to remove its quiet tail. The original demo
  fixture remains available for existing tests and compatibility.

## REVamp

Open REVamp in Hermes, then use the REVamp view to search its library, create
playlists, play a library song in the open REVamp player, and add songs to a
playlist. A finished Music Studio take can be added from the Library; it streams
from the studio's local server, so keep Music Studio running while playing it in
REVamp. The integration uses the installed `revampctl` client to access REVamp;
`HERMES_MUSIC_REVAMPCTL` may point to it if it is not on `PATH`. No playback or
playlist changes happen until the user clicks an action. REVamp's playlist API
does not expose an atomic append; avoid editing the same playlist concurrently
in both applications, since one update could overwrite the other.

## Engines and limits

The ACE runtime uses `ACE_RUNTIME_DIR` and `ACE_SOURCE_DIR`; MiniMax uses
`COMFYUI_URL` and requires `ffmpeg` for conversion. Both model runtimes and
weights must be installed separately. See the main `README.md` and `LIMITS.md`.
Repaint, extend, batch and presets are not yet exposed in the standalone UI.

The interface is original project code; no third-party studio source or assets
are bundled. The original demo fixture's cloud involvement is unverified, and
its MiniMax licence notice needs the correction described in `VERIFY.md`.
