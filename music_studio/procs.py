"""Process-tree kill and detach flags that work on POSIX and Windows."""
import os
import signal
import subprocess

WINDOWS = os.name == 'nt'


def detach_kwargs():
    """Popen kwargs that put a child in its own session (POSIX) or process group (Windows)."""
    if WINDOWS:
        return {'creationflags': getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)}
    return {'start_new_session': True}


def kill_tree(pid):
    """Kill a process and everything it started. Returns True when the kill was issued."""
    if WINDOWS:
        done = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
        return done.returncode == 0
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    if pgid == pid:
        os.killpg(pid, signal.SIGKILL)
    else:
        os.kill(pid, signal.SIGKILL)
    return True


def pid_exists(pid):
    """True when a process with this pid is running. Never signals it: on Windows os.kill(pid, 0)
    would terminate the process, so Windows asks OpenProcess/GetExitCodeProcess instead."""
    if type(pid) is not int or pid <= 0:
        return False
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
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
