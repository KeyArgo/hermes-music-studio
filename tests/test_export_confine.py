"""An export lands where the owner keeps music — never where an agent says.

Review item 5. `export.target_folder` accepted any absolute path, so the tool's `export` action was a
write primitive driven by whatever the model named. The destination is now the owner's configured folder
(or, without one, their music library), and anything outside it is refused — including a path that only
looks inside because of `..` or a symlink.

These fail on the reviewed tree: the old function created whatever directory it was handed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from music_studio import export


@pytest.fixture(autouse=True)
def _isolated_studio(tmp_path, monkeypatch):
    """Import the modules with a throwaway library, so no later test writes into the real one.

    `music_studio.service` and `standalone.app` resolve their data directory at import time; a test that
    imports them with the default in place leaves every test after it pointed at the owner's studio.
    """
    monkeypatch.setenv("HEWN_STUDIO_DATA_DIR", str(tmp_path / "studio"))
    return tmp_path

@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A throwaway home so the test never touches the real music library."""
    (tmp_path / "Music").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def test_a_folder_outside_the_music_library_is_refused(home):
    with pytest.raises(ValueError) as caught:
        export.target_folder("/etc")
    assert "inside" in str(caught.value)


def test_a_folder_inside_the_music_library_is_allowed(home):
    target = export.target_folder(str(home / "Music" / "Mixes"))
    assert target == (home / "Music" / "Mixes").resolve()
    assert target.is_dir()


def test_a_dot_dot_escape_is_refused(home):
    with pytest.raises(ValueError):
        export.target_folder(str(home / "Music" / ".." / ".." / "escape"))


def test_a_symlink_escape_is_refused(home, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    link = home / "Music" / "shortcut"
    os.symlink(outside, link)
    with pytest.raises(ValueError):
        export.target_folder(str(link / "nested"))


def test_the_configured_folder_is_the_confined_root(home, tmp_path_factory):
    """The owner may keep music somewhere else; then that folder is the root, not `~/Music`."""
    library = tmp_path_factory.mktemp("library")
    target = export.target_folder(str(library / "Albums"), configured=str(library))
    assert target == (library / "Albums").resolve()
    with pytest.raises(ValueError):
        export.target_folder(str(home / "Music"), configured=str(library))


def test_the_default_is_the_music_library(home):
    target = export.target_folder(None)
    assert target == (home / "Music" / "Hermes Music Studio").resolve()


def test_the_model_cannot_choose_the_folder():
    """The tool schema must not offer `folder`, and the handler must not read one."""
    root = Path(__file__).resolve().parents[1]
    text = (root / "__init__.py").read_text(encoding="utf-8")
    assert "'folder': {'type'" not in text, "the tool schema still invites a model to name a folder"
    assert "args.get('folder')" not in text, "the tool still honours an agent-named folder"


def test_export_reports_where_it_wrote(home, tmp_path):
    """The confinement must not break the happy path: a take still exports and reports its path."""
    project = {"project_id": "p1", "title": "Night Bus",
               "takes": [{"take_id": "t1", "state": "succeeded"}]}

    class Jobs:
        def __init__(self):
            self.root = tmp_path / "store"

        def open_audio(self, project_id, take_id):
            import io

            return io.BytesIO(b"RIFF" + b"\x00" * 64)

    result = export.export_take(Jobs(), project, "t1", folder=None)
    assert result["success"] is True
    assert Path(result["path"]).is_file()
    assert Path(result["path"]).parent == (home / "Music" / "Hermes Music Studio").resolve()
