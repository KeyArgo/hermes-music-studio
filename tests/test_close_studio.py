"""Closing the studio from its own page.

A window with no way out is a window somebody kills by hand. The page posts
``/api/host/close``, the host forgets its recorded state and then stops — the reply is sent
first, so the page can say what happened instead of dying mid-request.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_studio import host_launcher


def test_forget_drops_the_recorded_host(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    (tmp_path / host_launcher.STATE_FILE).write_text(json.dumps({'pid': 1, 'port': 8775}))
    host_launcher.forget()
    assert not (tmp_path / host_launcher.STATE_FILE).exists(), 'the recorded host must be gone'
    assert host_launcher.ensure_host.__doc__  # the module still imports cleanly


def test_forget_is_quiet_when_nothing_was_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    host_launcher.forget()   # must not raise


def test_shutdown_self_schedules_an_exit_and_forgets_first(tmp_path, monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_DATA_DIR', str(tmp_path))
    (tmp_path / host_launcher.STATE_FILE).write_text(json.dumps({'pid': 1, 'port': 8775}))
    started = {}

    class FakeTimer:
        def __init__(self, delay, fn):
            started['delay'] = delay
            started['fn'] = fn

        def start(self):
            started['started'] = True

    monkeypatch.setattr('threading.Timer', FakeTimer)
    host_launcher.shutdown_self(delay=0.1)
    assert started['started'] is True
    assert started['delay'] == 0.1
    assert not (tmp_path / host_launcher.STATE_FILE).exists(), 'the state file is cleaned before the exit'
