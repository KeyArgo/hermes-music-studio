# Getting started: your first song

The short, plain version. Everything here is done by clicking in the studio. For every feature, see
[USAGE.md](USAGE.md).

Applies to 1.0.0 · back to [docs/README.md](README.md).

## What you need

Music Studio writes the song with your Hermes model. To turn it into sound it needs a **music engine**:
a program that makes the audio. You need one of these:

| You have | Use |
|---|---|
| A computer on your network that runs **ComfyUI** (often a PC with a big graphics card) | **Use a server I already have** (below). This is the easiest. |
| An NVIDIA graphics card in this computer | **Install on this computer** (a 14.7 GB download) |
| Neither | You need one of the two first. Without an engine you can still write and save lyrics. |

You do not need to type any keys or settings files. The studio saves what you set up on this
computer only; nothing you enter is sent anywhere else, or shipped with the plugin.

## Use a server I already have (5 steps)

1. Open Music Studio and click **Music engines** in the left menu.
2. Under **Use a server I already have**, choose **ComfyUI** (also for ACE-Step 1.5 running on a ComfyUI), then type the server's address, for example
   `http://192.168.1.50:8188`. (Don't know it? See [Finding the address](#finding-the-address).)
3. Click **Test this address**.
4. A list of what that server can play appears, for example **ACE-Step 1.5** and **MiniMax Music 3**.
   Click **Add** next to the one you want.
5. Go back to **Create**. In the engine menu, pick **ACE-Step 1.5 on &lt;server&gt;** and make a song.

That's it. The studio remembers it next time.

### Which engine should I pick?

- **ACE-Step 1.5**: fast, and the only one that can **change just one part** of a finished song
  (open a finished song and use **Change this part**). Pick this if you are unsure.
- **MiniMax Music 3**: a different sound. Songs are at least 10 seconds. Changing one part makes a
  new short clip for that part instead of editing it in place.

## Finding the address

The address is `http://` + the computer's network number + `:` + the port. You need both numbers.

**The computer's number (IP address)**, on the computer that runs ComfyUI:

| On | Do this | Look for |
|---|---|---|
| Windows | Press Start, type `cmd`, press Enter, type `ipconfig`, press Enter | **IPv4 Address**, e.g. `192.168.1.50` |
| Mac | Apple menu → System Settings → Wi-Fi (or Network) → Details | **IP address** |
| Linux | Open a terminal, type `hostname -I` | the first number |

If ComfyUI runs on **this same computer**, use `127.0.0.1` as the number.

**The port**, the number after the colon:

| ComfyUI kind | Usual port |
|---|---|
| ComfyUI started from a folder or script (`python main.py`) | `8188` |
| ComfyUI Desktop app | `8000` (shown in its Settings → Server Config) |

The surest way: open ComfyUI in a web browser on that computer. The address bar shows the exact
address, for example `http://127.0.0.1:8188`. Use that port.

**One setting on the ComfyUI computer**: by default ComfyUI only lets its own computer connect. For other
computers to use it, start it with `--listen` (for example `python main.py --listen`), or in ComfyUI
Desktop turn on listening for the network in Settings → Server Config, then restart ComfyUI.

## Let Hermes help

In any Hermes chat you can ask in plain words, for example:

> Find the ComfyUI server on my network and tell me its address for Music Studio.

> My Music Studio says "Nothing answered" for http://192.168.1.50:8188. Help me find out why.

Hermes can run the checks above for you (ipconfig, test the port) and tell you what to type. A
button that sets everything up for you is planned for the next update.

## If something goes wrong

The **Test this address** button says what it found. The common ones:

| It says | What to do |
|---|---|
| **Nothing answered** | Wrong number or port, ComfyUI not running, or ComfyUI not started with `--listen`. On Windows, allow ComfyUI through the firewall when asked. |
| **Answers but is not ComfyUI / not an ACE-Step server** | Wrong port. Open ComfyUI in a browser to see the right one. |
| **Missing files** | ComfyUI runs, but the music model files are not on it. The message lists them and the folder they go in. |

**Settings → Engine doctor** shows a check for every engine. A red line for **ACE-Step (local)** only
means ACE-Step is not installed on *this* computer. That is fine if you use a server: the ACE-Step 1.5
that a server offers is listed separately and is what you pick.

More answers: [TROUBLESHOOTING.md](TROUBLESHOOTING.md) and [FAQ.md](FAQ.md).
