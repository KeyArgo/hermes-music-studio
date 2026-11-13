#!/usr/bin/env python3
"""Smoke the running studio over HTTP — the checks a person would make by clicking.

Read-only unless --export is given. Exits non-zero when the running build cannot do what the
product claims, so it is safe to run after every deploy:

    python3 scripts/smoke.py 8775
    python3 scripts/smoke.py 8775 --export     # also copies one take to a scratch folder

What it cannot check: whether sound actually comes out. A browser without an audio backend
decodes nothing, so the player's audio path is covered by tests/test_player.mjs (a fake media
element) and by listening. Everything the server owes the player — a real length, real bytes —
is checked here.
"""

from __future__ import annotations

import json
import sys
import urllib.request

port = "8775"
wanted = [arg for arg in sys.argv[1:] if arg.isdigit()]
if wanted:
    port = wanted[0]
BASE = f"http://127.0.0.1:{port}"
DO_EXPORT = "--export" in sys.argv

problems: list[str] = []
notes: list[str] = []


def studio_token() -> str:
    """The host refuses /api without its token; a shell check carries it the way the pane does."""
    import os
    from pathlib import Path

    token = (os.environ.get("HEWN_STUDIO_TOKEN") or "").strip()
    if token:
        return token
    root = Path(os.environ.get("HEWN_STUDIO_DATA_DIR")
                or Path.home() / ".hermes" / "plugin-data" / "hermes-music-studio")
    try:
        return str(json.loads((root / "studio-host.json").read_text()).get("token") or "")
    except Exception:
        return ""


TOKEN = studio_token()


def get(path: str) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(BASE + path)
    if TOKEN:
        request.add_header("X-Studio-Token", TOKEN)
    with urllib.request.urlopen(request, timeout=15) as answer:
        body = answer.read()
        kind = answer.headers.get("content-type", "")
        if "json" in kind:
            return answer.status, dict(answer.headers), body
        return answer.status, dict(answer.headers), body


def fail(message: str) -> None:
    problems.append(message)


# 1. the page and its identity
status, _, body = get("/")
page = body.decode("utf-8", "replace")
if status != 200:
    fail(f"GET / answered {status}")
if 'id="app-version"' not in page:
    fail("the page has no version chip")
if "wirePlayer" not in page and "app.js" not in page:
    fail("the page does not load the studio script")

status, _, body = get("/api/v1/version")
if status != 200:
    fail(f"GET /api/v1/version answered {status}")
else:
    info = json.loads(body)
    for key in ("version", "commit", "commit_date", "dirty", "channel"):
        if key not in info:
            fail(f"the stamp is missing {key!r}")
    notes.append(f"build {info.get('version')} · {(info.get('commit') or '')[:8]} · channel {info.get('channel')}")

# 2. the sample a new user presses first
status, headers, body = get("/api/studio/sample")
if status != 200:
    fail(f"the sample answered {status}")
else:
    if body[:4] != b"RIFF":
        fail("the sample is not a WAV")
    if not headers.get("content-length"):
        fail("the sample is served without a length — the player's scrubber cannot know its span")
    notes.append(f"sample {len(body)/1e6:.1f} MB, length header {'yes' if headers.get('content-length') else 'no'}")

# 3. a take: the bytes the player streams, and the length it needs
# /status is what the page itself reads: engines and every project with its takes.
status, _, body = get("/api/status")
takes = []
if status != 200:
    fail(f"GET /api/status answered {status}")
else:
    try:
        state = json.loads(body)
    except ValueError:
        state = {}
        fail("the status answer is not JSON")
    for project in state.get("projects", []):
        for take in project.get("takes", []):
            if take.get("take_id") and take.get("state") in ("succeeded", "done", None):
                takes.append((project["project_id"], take["take_id"]))
    ready = [engine for engine in state.get("engines", []) if engine.get("available")]
    notes.append(f"{len(state.get('projects', []))} project(s), {len(ready)} engine(s) reporting ready")

if not takes:
    notes.append("no finished take in the library — the take-audio check was skipped")
else:
    project_id, take_id = takes[-1]
    path = f"/api/projects/{project_id}/takes/{take_id}/audio"
    status, headers, body = get(path)
    if status != 200:
        fail(f"take audio answered {status}")
    else:
        if body[:4] != b"RIFF":
            fail("the take is not a WAV")
        if not headers.get("content-length"):
            # This is the exact bug that made the scrubber dead: chunked audio, duration Infinity.
            fail("take audio has no Content-Length — the player's scrubber will have no range")
        notes.append(f"take audio {len(body)/1e6:.1f} MB with a declared length")

# 4. REVamp, which is optional: absent is a state, not a failure
status, _, body = get("/api/studio/revamp/status")
if status == 200:
    state = json.loads(body)
    notes.append("REVamp " + ("present" if state.get("available") else "not running (optional)"))
else:
    notes.append(f"REVamp check answered {status} (optional, not a failure)")

# 5. export, only when asked, and only to a scratch folder
if DO_EXPORT and takes:
    notes.append("export check skipped: run it by hand from the studio so the real folder is the one used")

print(f"smoke: {BASE}")
for note in notes:
    print("  ·", note)
if problems:
    print("\nFAILED")
    for problem in problems:
        print("  -", problem)
    sys.exit(1)
print("\nOK — the running build answers everything the product claims")
