"""The pane stays on the plugin SDK: no desktop bridge, no raw iframe, no window.open fallback.

Review item 2. `desktop/plugin.js` read the host's state file through `window.hermesDesktop.readFileText`
(the desktop app's private bridge), opened links through the bridge, and framed the studio with a raw
`<iframe>`; the framed page did the same thing from the other side. None of that is a plugin API — the
public seams are `ctx.rest`, `ctx.os.openExternal` and the SDK's `SandboxedFrame`.

These read the shipped files, so they fail on the reviewed tree and pass once the bridge is gone.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANE = ROOT / "desktop" / "plugin.js"
PAGE = ROOT / "standalone" / "static" / "app.js"


def test_the_pane_does_not_use_the_desktop_bridge():
    text = PANE.read_text(encoding="utf-8")
    assert "hermesDesktop" not in text, (
        "the pane read the host's state file and opened links through the desktop bridge; "
        "use ctx.rest('/studio/host') and ctx.os.openExternal instead"
    )


def test_the_pane_frames_with_the_sdk_primitive():
    text = PANE.read_text(encoding="utf-8")
    assert "SandboxedFrame" in text, "the studio must be framed with the SDK's SandboxedFrame"
    assert "h('iframe'" not in text and 'h("iframe"' not in text, (
        "a raw iframe is unsandboxed content inside the app"
    )
    assert "allow-same-origin" not in text, (
        "allow-same-origin would undo the sandbox the primitive exists for"
    )


def test_the_pane_opens_links_through_ctx_os():
    text = PANE.read_text(encoding="utf-8")
    assert "ctx.os.openExternal" in text
    assert "window.open(" not in text, "the pane must not open tabs itself"


def test_the_framed_page_asks_the_pane_instead_of_the_bridge():
    text = PAGE.read_text(encoding="utf-8")
    assert "hermesDesktop" not in text, (
        "the framed page cannot reach the desktop bridge; it must postMessage the pane"
    )
    assert "hermes-music-studio:open-external" in text
    assert "postMessage(" in text


def test_the_pane_answers_the_pages_open_request():
    """The message the page sends must have a listener, or the button silently does nothing."""
    text = PANE.read_text(encoding="utf-8")
    assert "hermes-music-studio:open-external" in text
    assert "addEventListener('message'" in text


def test_the_pane_never_touches_the_dom_directly():
    """A pane is a React component: no document.body, no querySelector, no manual listeners on nodes."""
    text = PANE.read_text(encoding="utf-8")
    for forbidden in ("document.body", "document.querySelector", "document.getElementById",
                      "document.createElement"):
        assert forbidden not in text, f"the pane reaches into the DOM with {forbidden}"


def test_the_host_route_is_the_only_discovery_path():
    text = PANE.read_text(encoding="utf-8")
    assert "/studio/host" in text
    assert "studio-host.json" not in text, (
        "reading the launcher's state file was the fallback that needed the bridge"
    )
