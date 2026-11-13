"""Cover art the public way: the agent makes the image, the studio accepts only a safe file.

Review item 1 asked for cover art to stay, done through public APIs — the agent uses its own image tool
and hands over a *file*, so the plugin never imports a private image generator. A path supplied by a model
is untrusted input, so `set_cover` accepts only places an image plausibly arrives in, and sniffs the bytes
instead of believing the extension.

These fail on the reviewed tree: there was no `set_cover` action at all, only a route that called the
private image tool through a spawned Hermes worker.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from music_studio import covers

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPEG = b"\xff\xd8\xff" + b"0" * 64


@pytest.fixture()
def studio(tmp_path):
    root = tmp_path / "studio"
    root.mkdir()
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Pictures").mkdir()
    (tmp_path / "tmp").mkdir()
    return root


def _call(studio, path, home, temp):
    return covers.cover_from_file(studio, "p1", str(path), home=home, temp=temp)


def test_an_image_from_downloads_is_accepted(studio, tmp_path):
    image = tmp_path / "Downloads" / "cover.png"
    image.write_bytes(PNG)
    result = _call(studio, image, tmp_path, tmp_path / "tmp")
    assert result["success"] is True and result["mime"] == "image/png"
    stored = covers.read_cover(studio, "p1")
    assert stored == ("image/png", PNG)


def test_a_file_outside_the_allowed_folders_is_refused(studio, tmp_path):
    image = tmp_path / "private" / "key.png"
    image.parent.mkdir()
    image.write_bytes(PNG)
    with pytest.raises(covers.CoverError) as caught:
        _call(studio, image, tmp_path, tmp_path / "tmp")
    assert "Downloads" in str(caught.value)


def test_a_dot_dot_path_cannot_escape(studio, tmp_path):
    outside = tmp_path / "private" / "x.png"
    outside.parent.mkdir()
    outside.write_bytes(PNG)
    sneaky = tmp_path / "Downloads" / ".." / "private" / "x.png"
    with pytest.raises(covers.CoverError):
        _call(studio, sneaky, tmp_path, tmp_path / "tmp")


def test_a_symlink_is_refused(studio, tmp_path):
    outside = tmp_path / "private" / "x.png"
    outside.parent.mkdir()
    outside.write_bytes(PNG)
    link = tmp_path / "Downloads" / "link.png"
    os.symlink(outside, link)
    with pytest.raises(covers.CoverError) as caught:
        _call(studio, link, tmp_path, tmp_path / "tmp")
    assert "link" in str(caught.value)


def test_a_file_that_is_not_an_image_is_refused(studio, tmp_path):
    fake = tmp_path / "Downloads" / "cover.png"
    fake.write_bytes(b"#!/bin/sh\necho not an image\n")
    with pytest.raises(covers.CoverError) as caught:
        _call(studio, fake, tmp_path, tmp_path / "tmp")
    assert "not a PNG" in str(caught.value)


def test_an_oversized_image_is_refused(studio, tmp_path):
    big = tmp_path / "Downloads" / "huge.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * (covers.MAX_IMAGE + 1))
    with pytest.raises(covers.CoverError) as caught:
        _call(studio, big, tmp_path, tmp_path / "tmp")
    assert "2 MiB" in str(caught.value)


def test_a_jpeg_is_stored_as_a_jpeg(studio, tmp_path):
    image = tmp_path / "Pictures" / "shot.jpg"
    image.write_bytes(JPEG)
    assert _call(studio, image, tmp_path, tmp_path / "tmp")["mime"] == "image/jpeg"


def test_the_tool_offers_set_cover_and_takes_a_path():
    root = Path(__file__).resolve().parents[1]
    text = (root / "__init__.py").read_text(encoding="utf-8")
    assert "'set_cover'" in text or "\"set_cover\"" in text or "set_cover" in text
    assert "image_path" in text, "the agent needs a way to hand over the file it made"
    assert "image_generate" not in text, "the plugin must not call a private image generator"


def test_the_host_and_the_tool_share_one_cover_rule():
    """Both entry points go through `music_studio.covers`, so the page and the agent cannot diverge."""
    root = Path(__file__).resolve().parents[1]
    host = (root / "standalone" / "workspaces.py").read_text(encoding="utf-8")
    assert "covers.write_cover" in host
