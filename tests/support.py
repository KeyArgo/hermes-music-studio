"""One shared way for every test to run against a runtime the test owns.

Why this module exists
----------------------
``ace_engine`` freezes ``ACE_RUNTIME_DIR`` / ``ACE_SOURCE_DIR`` into module
constants at import time, and derives its GPU lock from them.  A test that only
patches ``os.environ`` therefore reaches the worker subprocess but *not* the
in-process health probe behind ``/status``, ``/generate`` and ``/batch``.
Engine availability then becomes a property of the machine running the suite:
green on a workstation with ACE weights installed, red on every clean host.

``pin_fixture_runtime`` pins both halves at once — the environment (children)
and the module constants (this process) — to a runtime the test creates, and
points the two remote engines at an unroutable loopback port so "server
reachable" is never an accident of the LAN.

Usage
-----
A module that touches the API/registry pins one shared runtime for the whole
module::

    from support import install_shared_runtime

    def setUpModule():
        global _RUNTIME_PIN
        _RUNTIME_PIN = install_shared_runtime()

    def tearDownModule():
        _RUNTIME_PIN.close()

A test that owns (and mutates) its own runtime pins that one instead, normally
in ``setUp``::

    self.pinned = pin_fixture_runtime(self.runtime)
    self.pinned.__enter__()
    self.addCleanup(self.pinned.close)

Tests that verify "no engine" behaviour pin an explicitly empty runtime::

    empty = tempfile.mkdtemp()
    pin_fixture_runtime(empty)   # nothing is installed here
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

from fake_ace import make_runtime, pin_runtime

# A loopback port nothing listens on: remote engines must be unavailable
# everywhere, not "available because the test host can reach Spark E".
UNREACHABLE = 'http://127.0.0.1:9'

_shared = None


def shared_runtime():
    """The process-wide fixture runtime, created once and removed at exit."""
    global _shared
    if _shared is None:
        root = tempfile.mkdtemp(prefix='ms-studio-fixture-runtime-')
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        _shared = make_runtime(root)
    return _shared


def pin_fixture_runtime(runtime=None):
    """Pin this process and its children to ``runtime``.

    ``runtime=None`` uses :func:`shared_runtime`.  Returns an ``ExitStack``:
    enter it for a test (``addCleanup(stack.close)``) or a module
    (``setUpModule`` / ``tearDownModule``).
    """
    import contextlib

    runtime = Path(runtime) if runtime is not None else shared_runtime()
    stack = contextlib.ExitStack()
    stack.enter_context(pin_runtime(runtime))
    stack.enter_context(patch_dict({
        'ACE_RUNTIME_DIR': str(runtime),
        'ACE_SOURCE_DIR': str(runtime / 'source'),
        'COMFYUI_URL': UNREACHABLE,
        'ACE_REMOTE_URL': UNREACHABLE,
    }))
    return stack


def install_shared_runtime():
    """Pin the shared fixture runtime for a whole module.

    Call from ``setUpModule`` and close the returned stack in ``tearDownModule``.
    """
    stack = pin_fixture_runtime()
    stack.__enter__()
    return stack


def patch_dict(values):
    from unittest.mock import patch
    return patch.dict(os.environ, values)
