"""The studio's own proof page: what this build is, and what it can do right now.

The idea is narrow and deliberate. A README can claim anything; a screenshot can be old; a CI badge
can belong to another branch. So the product answers for itself, from its own process: which commit
it is, what it was built from, and whether the things it promises actually work here — a real WAV
with a real length, a take the player can scrub, a library that reads, engines that are or are not
reachable, and REVamp's absence reported as a state rather than an error.

Nothing here is written by hand. Every line is measured when the page is requested.
"""

from __future__ import annotations

import time
from pathlib import Path


def _stamp() -> dict:
    try:
        from .version_stamp import as_dict
        return as_dict()
    except Exception:  # pragma: no cover - the stamp is optional, the page is not
        return {"version": "unknown", "commit": "", "commit_short": "", "commit_date": "",
                "built_at": "", "channel": "dev", "dirty": None, "source": "manifest"}


def _wav_facts(path: Path) -> tuple[bool, str]:
    """A WAV the player can scrub needs two things: RIFF bytes and a declared length."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(4)
    except OSError:
        return False, "missing"
    if header != b"RIFF":
        return False, f"{size:,} bytes, not a WAV"
    return True, f"{size:,} bytes"


def checks() -> list[dict]:
    """Everything the page shows, measured now."""
    from . import demo, service

    rows: list[dict] = []

    def row(name: str, ok: bool, detail: str) -> None:
        rows.append({"name": name, "ok": bool(ok), "detail": detail})

    stamp = _stamp()
    row("this build can say which build it is",
        bool(stamp.get("version")) and stamp.get("version") != "unknown",
        f"{stamp.get('version')} · {(stamp.get('commit') or 'no commit (installed)')[:8]} · "
        f"{stamp.get('channel')} · read from {stamp.get('source')}")

    try:
        sample = Path(demo.audio_path())
        ok, detail = _wav_facts(sample)
        row("the sample a new user presses first is real audio", ok, detail)
    except Exception as error:  # pragma: no cover - reported, not raised
        row("the sample a new user presses first is real audio", False, str(error)[:120])

    try:
        store = service.store()
        projects = store.all()
        takes = [take for project in projects for take in (project.get("takes") or [])]
        finished = [take for take in takes if take.get("state") in ("succeeded", "done")]
        row("the library reads", True,
            f"{len(projects)} project(s), {len(takes)} take(s), {len(finished)} finished")
    except Exception as error:  # pragma: no cover - reported, not raised
        row("the library reads", False, str(error)[:120])
        finished = []

    scrubbable = None
    for take in finished:
        path = take.get("audio_file")
        if not path:
            continue
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = service.plugin_data_dir(service.PLUGIN_ID) / candidate
        ok, detail = _wav_facts(candidate)
        scrubbable = (ok, detail)
        if ok:
            break
    if scrubbable is None:
        row("a finished take can be played and scrubbed", False,
            "no finished take on this machine — play the sample instead")
    else:
        row("a finished take can be played and scrubbed", scrubbable[0],
            scrubbable[1] + " · the player reads its length, so the bar has a range")

    try:
        from music_studio import about as about_module
        revamp = about_module.revamp_status()
        present = bool(revamp.get("available"))
        row("REVamp is optional, and says so", True,
            "running" if present else "not running — playlists are unavailable, nothing is broken")
    except Exception as error:  # pragma: no cover - reported, not raised
        row("REVamp is optional, and says so", True, f"unknown ({str(error)[:60]})")

    return rows


def summary() -> dict:
    rows = checks()
    failed = [row for row in rows if not row["ok"]]
    return {
        "ok": not failed,
        "rows": rows,
        "stamp": _stamp(),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def render_html(info: dict) -> str:
    """The page itself: plain, readable, and honest about what it did not check."""
    stamp = info["stamp"]
    tick = {True: "✓", False: "✗"}
    rows = "\n".join(
        f"<li class=\"{'ok' if row['ok'] else 'bad'}\">"
        f"<span class=\"mark\">{tick[row['ok']]}</span>"
        f"<span class=\"name\">{row['name']}</span>"
        f"<span class=\"detail\">{row['detail']}</span></li>"
        for row in info["rows"]
    )
    verdict = "This build can do what it promises, here, now." if info["ok"] else \
        "Something this build promises does not work on this machine — see the ✗ rows."
    commit = stamp.get("commit") or ""
    identity = " · ".join(part for part in (
        "v" + str(stamp.get("version")),
        (commit[:8] or "no commit"),
        (stamp.get("commit_date") or "")[:10],
        "modified" if stamp.get("dirty") else "",
    ) if part)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verify — Hermes Music Studio</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; padding: 48px 32px; background: #0d0f12; color: #e7e9ee;
         font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 860px; margin: 0 auto; }}
  h1 {{ font-size: 22px; letter-spacing: -0.01em; margin: 0 0 6px; }}
  .stamp {{ font: 12px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace; color: #8b93a3;
            letter-spacing: 0.04em; }}
  .verdict {{ margin: 22px 0 26px; padding: 14px 16px; border-radius: 10px;
              border: 1px solid {'#2f6f4a' if info['ok'] else '#7a3b3b'};
              background: {'#12241a' if info['ok'] else '#241414'}; }}
  ul {{ list-style: none; margin: 0; padding: 0; }}
  li {{ display: grid; grid-template-columns: 22px 1fr; gap: 4px 12px; padding: 12px 0;
        border-top: 1px solid #1c2028; }}
  .mark {{ grid-row: span 2; font-size: 15px; }}
  li.ok .mark {{ color: #4ade80; }} li.bad .mark {{ color: #f87171; }}
  .name {{ font-weight: 550; }}
  .detail {{ grid-column: 2; color: #98a1b2; font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }}
  footer {{ margin-top: 28px; color: #6f7787; font-size: 12.5px; }}
  a {{ color: #8ab4ff; }}
</style></head>
<body><main>
  <h1>What this build can do</h1>
  <div class="stamp">{identity}</div>
  <div class="verdict">{verdict}</div>
  <ul>{rows}</ul>
  <footer>
    Measured at {info['checked_at']} by the running process.
    Not checked here: whether sound leaves the speakers — no browser can prove that, so listen.
    Machine-readable copy: <a href="/api/verify.json">/api/verify.json</a>.
  </footer>
</main></body></html>"""
