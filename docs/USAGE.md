# Usage

A walkthrough of every user-facing feature, from the first draft to a file in your music folder.

Applies to 1.0.0 · revised 2026-11-11 · back to [docs/README.md](README.md).

![The create screen with an approved draft](screenshots/create-form.png)

## The first sixty seconds

1. Open the Music Studio pane (or run the standalone host from a clone — it refuses `/api` without a token, so:

   ```bash
TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
HEWN_STUDIO_TOKEN=$TOKEN python3 -m uvicorn standalone.app:app --port 8770
# then open http://127.0.0.1:8770/?token=$TOKEN — the host refuses /api without it
   ```
2. In **Create**, type a title and a description of the sound. The description becomes the engine's
   caption, so it is the one text field that reaches the model.
3. Choose **Simple** or **Custom**. Custom exposes tempo, key, vocal language and MiniMax guidance.
4. Press **Save draft**. Read the draft. Press **Approve draft**.
5. Press **Build the full song**. The progress panel reports queued/running, an estimate, and the
   engine it is using.
6. When it finishes, press **Play** in the library or in the footer, then **Export**.

The footer player is always available: play/pause, ten seconds back and forward, a scrubber, elapsed
and total time, and a volume slider. The sample button in the top bar plays the shipped instrumental
without needing any engine.

## Create

| Control | What it does |
|---|---|
| **Song title** | Up to 200 characters; used for the project, export filename and player title |
| **Sound & style** | The engine caption, required, up to 512 characters |
| **Lyrics** | Required unless **Instrumental only**, up to 4096 characters |
| **Music engine** | `ACE-Step 1.5` (`ace_local`) or `MiniMax Music 3` (`minimax_m3_comfy`) |
| **Length** | The engine's own window: 10–180 s for ACE, 10–360 s for MiniMax |
| **Tempo / key / vocal language** | Custom mode; stored in the draft and sent to the engine |
| **MiniMax music guidance** | Custom mode, 0.5–3.0; ACE-Step turbo does not accept it, so it is hidden there |
| **Workspace** | The workspace this song belongs to |

**Save draft** creates or updates the project. **Approve draft** enables only when the form is saved
and unchanged; an edit revokes the approval. **Build the full song** is disabled until the draft is
approved and the selected engine is ready. **Hear the chorus** renders a 25 s preview without
approving the full song.

### Choosing a model

The studio uses your Hermes model by default. To use a different provider or model, run these commands once:

```bash
hermes config set plugins.entries.hermes-music-studio.llm.allow_provider_override true
hermes config set plugins.entries.hermes-music-studio.llm.allow_model_override true
```

Then restart Hermes. The picker always lists Hermes' providers and their models; without these two settings Hermes only lets the studio use your Hermes model, and the studio says so instead of quietly switching. Pick any provider and model, or type an id. Your choice is saved in your browser. Providers you have set up in Hermes are marked ready.

### Creative partner

The right-hand panel talks to your chosen model. It sends the conversation and your tone/style influence to the model and shows a suggested draft. **Fill my draft** copies the suggestion into the form without saving or approving. Model use follows the provider's ordinary charges. With no model available, the panel says so.

### Making a song

After you save and approve the draft, press the **Generate song** button. It saves your draft, approves it with a fresh token, and starts the render. The progress panel shows queued, running, and estimated time. When it finishes, the take appears in the library and plays in the footer. You can also render a preview with **Preview the chorus**, which produces a 25 second sample without a full approval.

### Set up a music engine

With no engine installed, the Create button shows **Set up a music engine**. Press it to install one:

- **Local GPU:** Install ACE-Step 1.5 on this machine. The setup walks through downloading weights and testing the local runtime.
- **ComfyUI server:** Point to a server that runs ComfyUI with MiniMax Music 3. The studio tests the connection and lists available models.
- **Hosted API:** Enter an endpoint and API key for a remote music service. The studio tests the key and saves it to Hermes' credential store.

### Cover art

Upload a PNG, JPEG or WebP up to 2 MiB. The studio only accepts images from the studio folder, the temp directory or Hermes' cache (``<hermes home>/cache/images``). It reads the file bytes, not the extension. The agent can save a picture as the cover using the `set_cover` action. The cover is stored with the project and shown in the library.

## Library

![The library with projects and takes](screenshots/library.png)

Every project shows its caption and its takes. A take row carries its state, engine and seed, and the
actions that apply to that state:

- **Play** streams the take in the footer player.
- **Export** copies the newest finished audio into the export folder.
- **Add to REVamp** hands the take to the optional REVamp integration.
- **Check this take** compares the take with the request; **Fix it** retries a measurable failure with
  a fresh seed.
- **Change this part** opens an inline form on the row: start, end, new words, render. The child is
  spliced back automatically when it finishes.
- **Fade out** adds a faded copy as a new take.
- **Delete take** removes one take and its audio; **Delete** removes the project and refuses while it
  is rendering.
- **3 versions** renders three takes from the same approved draft, one seed each.
- **Compare engines** renders the draft on every ready engine as separate takes.
- **Duplicate** copies the project and every WAV with new ids.
- **Open draft** loads the project back into Create.

The change box at the top of a project takes plain words. "add a fiddle" appends `fiddle` to the one
style caption; "go dark" expands to a small, deterministic set of descriptors; "half-time in the
bridge" is sent as a section feel because neither engine accepts a second tempo. "mute the drums" is
refused with a sentence: both engines render one mixed stereo take.

### Save spot and go back

**Save spot** stores the whole draft, the kept take with the SHA-256 of its audio, the take lineage with
seeds, anything queued or running, and the playhead. Twenty spots are kept; a pinned spot is never
pruned. **Back to last spot** restores the draft and the keeper and says plainly that the approval was
revoked — you are looking at a different draft and should approve it again.

ACE replays exactly from a seed, so an ACE spot can be rebuilt from it. MiniMax's seed replay was
never measured, so an M3 spot keeps the audio file itself instead of promising a rebuild.

## Player and export

![A take playing in the footer](screenshots/player.png)

The footer player reads the length the server declares, so the scrubber can move the audio. Export
writes a normal `.wav` into `~/Music/Hermes Music Studio` by default (a setting). Names are readable
and sanitised, and a second export of the same take reuses the file it already wrote rather than
filling the folder with "Song (2)", "Song (3)". The library keeps its master; export is a copy.

## Workspaces

![Workspaces grouping songs](screenshots/workspaces.png)

Create a workspace, then pick it in Create's **Workspace** select. **View songs** filters the library
to that workspace. Workspaces are local JSON (`studio-workspaces.json`); they do not move audio.

## Music engines

![Music engines page, targets and library folder](screenshots/compute.png)

- **Render settings** save the ComfyUI endpoint, extra scan hosts, export folder and the MiniMax
  sampler knobs (steps, CFG, guidance, top K, sampler, scheduler). Values are validated at save time.
- **Test endpoint** probes the server and lists every check; **Find a renderer** scans loopback ports,
  the saved endpoint and your scan hosts.
- **Library folder** copies or moves the library and points the profile at it; **Use default** removes
  the pointer. A copy never deletes anything.
- **ComfyUI targets** save named endpoints; a take pins the exact URL it was preflighted against.
- **Local hardware** is a read-only `nvidia-smi` inventory. A GPU is attributed to a take only when
  `nvidia-smi` reports that take's exact PID on a device.

### Setting up a music engine

The **Music engines** page (left menu) has three ways to get an engine. Each button tests before it saves and names exactly which check failed.

#### Use a server I already have

Point at any ComfyUI running MiniMax Music 3 or any ACE-Step HTTP server you already run:

1. Type the address, for example `http://192.168.1.50:8188` (include `http://` or `https://` and the port number).
2. Press **Test this address**. The studio asks the server which music models it can run.
3. If it answers, the ready models appear in a list, for example: `MiniMax Music 3` or `ACE-Step 1.5`.
4. Press **Add**. The address and its name are now saved and appear in the Generate song menu.

In the Generate menu, they show as `ACE-Step 1.5 on <server name>` or `MiniMax Music 3 on <server name>`.

Troubleshooting:

- **Nothing answered.** Check the address, the port number, and that the server on the other computer has started. On Windows, check the firewall. On a ComfyUI, you may need to restart it with `--listen` so other computers can connect.
- **It answered but not ComfyUI or ACE.** You typed the wrong port. Pick a different one and test again.
- **The server answered but says it is missing files.** The server is running but lacks the MiniMax nodes or ACE-Step weights. Copy the named files into that server's models folder (it will tell you where), then test again.
- **The graphics card is missing or broken.** The card that matters is on the computer running the server, not the one where you run the studio. Check that computer's hardware.

#### Install on this computer

Download ACE-Step 1.5 and run it locally:

1. Install ComfyUI Desktop from https://www.comfy.org/download. Start it at least once.
2. In the Music engines page, press **Look on this computer**. The studio scans for a local ComfyUI and finds its models folder.
3. Press **Check space** to see whether your disk has room. ACE-Step 1.5 is 14.7 GB.
4. Press **Download ACE-Step 1.5 (14.7 GB)**. The download shows progress. You can cancel and resume.
5. When it finishes, restart ComfyUI Desktop.
6. The studio checks and adds it automatically.

After the first download, the files stay on your disk. Restarting ComfyUI or the studio does not re-download them.

#### Move to another computer

Copy your saved engines to another computer:

1. In Music engines, press **Copy my engines**. A list of text appears with the name and address of each engine you have saved.
2. Copy that text.
3. On the other computer, open Music Studio and go to Music engines.
4. Press **Paste engines** and paste the text you copied.
5. Each computer remembers its own engines. They are stored in the plugin data folder and survive plugin updates.

Your API keys are never copied, only the names and addresses.

- **Version**, **Companion**, **Contribute** and **Report a problem** round out the panel.
  **Copy diagnostics** includes the version, platform, library path, counts and newest failed take.

## REVamp (optional)

Only takes you add appear in the REVamp screen. Create a playlist from exported takes; the studio
exports the files, adds the folder to REVamp's library, rescans, then creates the playlist. With
REVamp absent the screen says so, and playing and exporting still work. A take plays from the studio
while it is running; keep the studio open for that.

## From a chat: the `music_studio` tool

The tool has 20 actions: `status`, `doctor`, `create`, `update`, `change`, `keep`, `snippet`,
`snippet_splice`, `check_take`, `checkpoint`, `checkpoints`, `restore`, `approve`, `render`, `queue`,
`takes`, `export`, `wait`, `compare` and `compare_status`. The usual loop:

```text
status
create   {draft:{title, caption, lyrics, duration_seconds, instrumental, engine}}
approve  {project_id, updated_at}        # only after the person said yes in chat
render   {project_id, timeout_s:240}     # queues a worker and waits
takes    {project_id}
export   {project_id}
```

`render` waits so a chat can answer with a file; `queue` returns as soon as the worker is spawned. The
approval gate survives the shortcut: `approve` must carry the `updated_at` token of the draft the
person actually read, and `render` refuses an unapproved draft. `compare` renders the same approved
draft on several ready engines. The full parameter table is in [REFERENCE.md](REFERENCE.md).

## In chat: `/song`

```
/song                  what is in the library, with ids and engine per song
/song go <id>          show the draft that would render — and queue nothing
/song go <id> now      approve that exact version and queue one take
/song takes <id>       every take with its state, engine and measured length
/song play <id>        the newest finished take's audio URL
/song export <id>      copy the newest finished take into your music folder
/song change <id> …    plain words → a different draft
/song keep <id>        remember the newest finished take as this song's keeper
/song check <id> [retry] did the take do what you asked? facts only, and a retry if one failed
/song save [label]     save your spot; /song saves lists them, /song back <id> returns
/song snippet <id> 10-18 go darker   re-render only 10–18 s, then /song splice <id> <take>
/song splice <id> <take>             copy the parent back outside the range
```

The first `go` is the gate; `go … now` is the commitment. If the draft changes in between, the old
confirmation is refused rather than rendering something you did not review.

## From a shell: `scripts/ms.py`

A thin HTTP client with no dependencies beyond the standard library:

```bash
python3 scripts/ms.py status
python3 scripts/ms.py doctor
python3 scripts/ms.py create --draft '{"title": "Night drive", "caption": "synthwave", "lyrics": "…", "duration_seconds": 30}'
python3 scripts/ms.py approve --project <id> --updated-at <token>
python3 scripts/ms.py render  --project <id> --timeout 240
python3 scripts/ms.py takes   --project <id>
python3 scripts/ms.py export  --project <id>
```

It finds the loopback host from `studio-host.json` (or `--base`), prints JSON, and exits 1 when the
studio refuses and 2 when no host answers.

## Closing the studio

**Close studio** in the top bar stops the loopback host and leaves the library untouched. Reopen the
pane to start another host. The host also exits after five idle minutes, or when the Hermes process
that started it is gone.

## What is not here

No stems, mixing, mastering, vocal tuning, LoRA training, reference-audio conditioning or lyric timing
export. No arrangement editor beyond sections. No cloud audio service and no mock engine. See
[../LIMITS.md](../LIMITS.md).
