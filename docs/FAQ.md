# FAQ

Short answers, with the document that has the detail.

Applies to 1.0.0 · revised 2026-11-11 · back to [docs/README.md](README.md).

**Does it work without Hermes?**
Yes. `python3 -m uvicorn standalone.app:app --port 8770` serves the same studio and router locally.
The tool and `/song` need Hermes, but the window does not.

**Does it generate audio by itself?**
No. There is no bundled model, no mock engine and no cloud audio service. You need ACE-Step 1.5
locally or a ComfyUI server running MiniMax Music 3. The studio opens without either and says what is
missing.

**Is it cloud-free?**
The audio path is local unless you point the MiniMax adapter at a remote ComfyUI server. The creative
partner and cover generation use the Hermes provider you configure; Hugging Face is contacted only when
you press the download button. No telemetry. A seed reproduces on the same runtime and hardware.

**Why does rendering wait for approval?**
Because GPU time is the scarce thing. The store refuses an unapproved draft, and an approval is bound
to the `updated_at` version you read, so an agent cannot rewrite the text between your read and your
click.

**Can I use a take commercially?**
The plugin is MIT. The models are not this plugin's: ACE-Step 1.5 is MIT for code and weights;
MiniMax Music 3 carries the MiniMax-Music3 Community License, and the adapter records its notice on
every MiniMax take. Read [../THIRD-PARTY.md](../THIRD-PARTY.md) and the upstream licence before you
rely on either.

**Why is my take shorter than the length I asked for?**
ACE can render a shorter musical body and a dead tail. The QC block reports `requested_s`, `file_s`
and `musical_end_s` separately, and offers a reversible trim. This is measured, not a bug report
after the fact: a 15 s request produced 11.7 s of music plus about 3 s of tail silence.

**What is the longest take?**
10–180 s on ACE, 10–360 s on MiniMax. 360 s is accepted but has never been rendered; 180 s is the
measured ACE ceiling on an RTX 4070 Ti. See [../LIMITS.md](../LIMITS.md).

**Can MiniMax repaint a section?**
No. Its ComfyUI graph has no repaint or continuation nodes, so the engine declares those capabilities
false and the request is refused with 409 before any HTTP call. Generate a fresh take instead.

**What does "reproducible" mean here?**
The same draft, seed, engine, runtime and hardware produce the same take. A different GPU, driver or
checkpoint does not promise the same audio. MiniMax seed replay was never measured, which is why an M3
checkpoint keeps the audio file rather than promising a rebuild.

**Where are my files?**
`~/.hermes/profiles/<profile>/plugin-data/hermes-music-studio/` inside Hermes; the standalone app uses
`$XDG_DATA_HOME/hewn-music-studio` or `~/.local/share/hewn-music-studio`. Set
`HEWN_STUDIO_DATA_DIR` to move the root. Export copies a WAV to `~/Music/Hermes Music Studio` by
default. See [CONFIGURATION.md](CONFIGURATION.md).

**How do I delete everything?**
Delete the plugin data folder, or use **Delete** per project and **Delete take** per take. There is no
undo. Uninstalling the plugin does not delete the library.

**Does it update itself?**
No. Updates go through `hermes plugins update hermes-music-studio`. The Check button reports the
installed build and says Hermes owns updates; it does not fetch or install code.

**Can I use it on Windows?**
No, not in this release. Parts of the product use `fcntl` locks and `/proc`. Linux is the tested path;
macOS is not exercised by CI.

**Why does the browser-side test suite need `npm install`?**
The DOM tests use `jsdom` and the React testing library. The studio itself has no build step and no
runtime JavaScript dependency.

**Why is the test count in the README 524?**
482 Python tests and 42 browser-side tests passed on this commit; one Python test is skipped. The
commands and exact summary lines are in [../TESTING.md](../TESTING.md).

**Something is stuck at "GPU busy".**
A take is queued or running. Wait, cancel a batch, or use `POST /recover {"confirm": true}` only after
you have confirmed no ACE process is running. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

**How do I report a security problem?**
Use GitHub private vulnerability reporting, as described in [../SECURITY.md](../SECURITY.md). Do not
open a public issue for a vulnerability.
