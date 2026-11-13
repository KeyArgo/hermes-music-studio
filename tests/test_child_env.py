"""Render children get an allowlisted environment; the owner's provider keys stay home.

Review item 4. `ace_engine._render` and `service._spawn_worker` passed `os.environ.copy()` to the
ACE-Step runtime and the render worker, so every key in the Hermes process reached third-party code that
has no use for any of them.

These set real-looking secrets in the parent, drive both spawn sites, and assert the child's environment
neither carries them nor loses what it needs. They fail on the reviewed tree (the env was a full copy).
"""

from __future__ import annotations

import os
import subprocess

import pytest

from music_studio.childenv import minimal_env

SECRETS = {
    "OPENAI_API_KEY": "sk-should-not-leak",
    "ANTHROPIC_API_KEY": "sk-ant-should-not-leak",
    "ACE_REMOTE_API_KEY": "ace-remote-should-not-leak",
    "GITHUB_TOKEN": "ghp-should-not-leak",
    "AWS_SECRET_ACCESS_KEY": "aws-should-not-leak",
}


@pytest.fixture(autouse=True)
def _isolated_studio(tmp_path, monkeypatch):
    """Import the modules with a throwaway library, so no later test writes into the real one.

    `music_studio.service` and `standalone.app` resolve their data directory at import time; a test that
    imports them with the default in place leaves every test after it pointed at the owner's studio.
    """
    monkeypatch.setenv("HEWN_STUDIO_DATA_DIR", str(tmp_path / "studio"))
    return tmp_path

@pytest.fixture()
def parent(monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/someone")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("ACE_RUNTIME_DIR", "/opt/ace-runtime")
    monkeypatch.setenv("HF_HOME", "/opt/ace-runtime/hfmodules")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setenv("SYSTEMROOT", "C:\\Windows")
    return SECRETS


def test_no_secret_reaches_a_child(parent):
    env = minimal_env()
    for name in SECRETS:
        assert name not in env, f"{name} reached a render child"


def test_the_child_keeps_what_it_needs(parent):
    env = minimal_env()
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/someone"
    assert env["LANG"] == "C.UTF-8"
    assert env["ACE_RUNTIME_DIR"] == "/opt/ace-runtime", "the runtime pointer is how ACE finds itself"
    assert env["HF_HOME"].endswith("hfmodules")
    assert env["CUDA_VISIBLE_DEVICES"] == "0"
    assert env["SYSTEMROOT"] == "C:\\Windows"


def test_extra_values_are_applied_last():
    env = minimal_env({"PYTHONPATH": "/src", "HF_HUB_OFFLINE": "1"}, source={"PATH": "/bin"})
    assert env == {"PATH": "/bin", "PYTHONPATH": "/src", "HF_HUB_OFFLINE": "1"}


def test_the_ace_runtime_is_spawned_with_the_minimal_env(parent, monkeypatch, tmp_path):
    import ace_engine

    captured: dict[str, object] = {}

    class FakeProc:
        pid = 4242
        returncode = 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(args, **kwargs):
        captured["env"] = kwargs.get("env")
        return FakeProc()

    monkeypatch.setattr(ace_engine.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ace_engine, "_interpreter_cmd", lambda: ["/bin/true"])
    # The module-level runtime paths were fixed at import; point them somewhere writable so the render
    # reaches its spawn instead of dying on a lock file in an empty path.
    monkeypatch.setattr(ace_engine, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(ace_engine, "LOCK", tmp_path / "runtime" / "gpu.lock")
    (tmp_path / "runtime").mkdir()
    monkeypatch.setattr(ace_engine, "_wait_with_progress", lambda proc, *a, **k: 0)
    monkeypatch.setattr(ace_engine, "_emit", lambda *a, **k: None)

    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(Exception):
        # It may fail later (no WAV produced); the environment is what this test is about.
        ace_engine._render(job_input={"caption": "x"}, output_dir=str(out), timeout_seconds=5,
                           expected_seconds=1)

    env = captured.get("env")
    assert env is not None, "_render passed no env, so the child inherited ours"
    for name in SECRETS:
        assert name not in env, f"{name} reached the ACE runtime"


def test_the_render_worker_is_spawned_with_the_minimal_env(parent, monkeypatch, tmp_path):
    from music_studio import service

    captured: dict[str, object] = {}

    class FakeProc:
        pid = 4343

    def fake_popen(args, **kwargs):
        captured["env"] = kwargs.get("env")
        return FakeProc()

    monkeypatch.setattr(service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(service, "_reap_workers", lambda: None)

    class FakeStore:
        root = tmp_path

    try:
        service._spawn_worker(FakeStore(), "p1", "t1")
    finally:
        # Leave the module exactly as we found it: the worker list is process state, and a fake process
        # left in it makes every later test's /status fail on a poll it cannot complete.
        service._workers.clear()
    env = captured.get("env")
    assert env is not None, "_spawn_worker passed no env"
    for name in SECRETS:
        assert name not in env, f"{name} reached the render worker"
    assert env.get("PYTHONPATH"), "the worker still needs the package on its path"


def test_neither_site_copies_the_whole_environment():
    for path in ("ace_engine.py", "music_studio/service.py"):
        text = open(path, encoding="utf-8").read()
        assert "os.environ.copy()" not in text, f"{path} still hands children the whole environment"
