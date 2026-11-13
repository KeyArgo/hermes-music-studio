"""Process-tree kill and detach flags that work on POSIX and Windows."""
import os
import signal
import subprocess
import time

WINDOWS = os.name == 'nt'

# How long a polite SIGTERM gets before SIGKILL, and how long either platform
# waits for the process to actually disappear after the last signal/taskkill.
TERM_GRACE_SECONDS = 2.0
KILL_WAIT_SECONDS = 5.0
KILL_POLL_SECONDS = 0.1


def _windows():
    """Windows detection that also honours a monkeypatched os.name in tests."""
    return WINDOWS or os.name == 'nt'


def detach_kwargs():
    """Popen kwargs that put a child in its own session (POSIX) or process group (Windows)."""
    if _windows():
        return {'creationflags': getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)}
    return {'start_new_session': True}


def _gone(pid):
    """True when pid is no longer running (a zombie is gone: it holds no GPU/CPU)."""
    if not pid_exists(pid):
        return True
    if _windows():
        # pid_exists already asks GetExitCodeProcess, which never reports an
        # exited process as STILL_ACTIVE.
        return False
    try:
        with open(f'/proc/{pid}/stat', 'rb') as fh:
            raw = fh.read()
        return raw.rsplit(b')', 1)[1].split()[0] in (b'Z', b'X', b'x')
    except (OSError, IndexError):
        pass
    # macOS and the BSDs have no /proc; ps shows the zombie state there.
    try:
        out = subprocess.run(['ps', '-o', 'stat=', '-p', str(int(pid))],
                             capture_output=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return out[:1] in (b'Z', b'X', b'x')


def _wait_gone(pid, seconds):
    """Poll pid up to ``seconds``; return True once it is definitely gone."""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if _gone(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(KILL_POLL_SECONDS)


def kill_tree(pid):
    """Kill a process and everything it started.

    Returns True only when the process is actually gone: on Windows taskkill is
    followed by a poll (it can report success while the tree is still winding
    down), and on POSIX the process gets SIGTERM, a grace period, then SIGKILL.
    """
    if type(pid) is not int or pid <= 0:
        return False
    if _windows():
        try:
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
        except OSError:
            pass
        return _wait_gone(pid, KILL_WAIT_SECONDS)
    try:
        pgid = os.getpgid(pid)
    except OSError:
        # It went away between the caller's check and here.
        return _wait_gone(pid, 0)
    try:
        if pgid == pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        return _wait_gone(pid, 0)
    if _wait_gone(pid, TERM_GRACE_SECONDS):
        return True
    try:
        if pgid == pid:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        return _wait_gone(pid, 0)
    return _wait_gone(pid, KILL_WAIT_SECONDS)


def pid_exists(pid):
    """True when a process with this pid is running. Never signals it: on Windows os.kill(pid, 0)
    would terminate the process, so Windows asks OpenProcess/GetExitCodeProcess instead."""
    if type(pid) is not int or pid <= 0:
        return False
    if _windows():
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
        try:
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        except (AttributeError, OSError):
            # Not a real Windows host (a test simulating os.name on POSIX).
            pass
        else:
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return ctypes.get_last_error() == 5  # access denied: it exists, owned by someone else
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
