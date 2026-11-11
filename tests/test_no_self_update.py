"""The studio never fetches or installs its own code: Hermes owns updates (plugin catalog rule 3)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from music_studio import updates  # noqa: E402


def test_check_makes_no_network_call_and_points_at_hermes(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network call"))
    result = updates.check()
    assert result["update_available"] is False and "hermes plugins update" in result["detail"]


@pytest.mark.parametrize("action", [lambda d: updates.apply(d, "a" * 40), updates.undo])
def test_install_and_undo_are_refused(tmp_path, action):
    with pytest.raises(ValueError, match="hermes plugins update"):
        action(tmp_path)


def test_no_installer_code_remains():
    text = Path(updates.__file__).read_text()
    assert "_spawn_install" not in text and "urlopen" not in text
