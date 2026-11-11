"""Liveness checks never signal the process they check (os.kill(pid, 0) terminates it on Windows)."""
import os
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from music_studio import jobs, lifecycle, procs  # noqa: E402


def test_pid_exists_for_self_and_not_for_a_finished_child():
    assert procs.pid_exists(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not procs.pid_exists(child.pid) or os.name != "nt"  # POSIX may still see a reaped pid briefly


def test_checking_a_live_child_does_not_kill_it():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        for _ in range(3):
            assert procs.pid_exists(child.pid)
            assert lifecycle.parent_alive(child.pid)
            assert jobs.process_alive(child.pid)
        assert child.poll() is None, "a liveness check terminated the process"
    finally:
        child.kill(); child.wait()


def test_no_liveness_check_uses_signal_zero():
    root = Path(__file__).resolve().parent.parent / "music_studio"
    for name in ("jobs.py", "lifecycle.py"):
        assert "os.kill(pid, 0)" not in (root / name).read_text(), name
