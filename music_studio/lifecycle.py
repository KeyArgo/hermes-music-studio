"""When the studio host should stop by itself.

It is spawned detached (`start_new_session=True`) so that closing the pane does not kill a render the
person asked for. The cost of that choice was a process that never ended: after Hermes quit, after the
plugin was disabled, and forever on an idle desktop. Measured: 0.1% CPU, ~58 MB RSS, seven threads.

The rule here is deliberately boring and testable — one pure function, one decision per tick:

- **the process that started it is gone** → exit. That is "Hermes quit" without trying to guess what
  quit means; the launcher passes its own pid and we watch it.
- **a render is in flight** → stay, whatever the clock says. Killing a host mid-render is the failure
  this detachment exists to prevent.
- **idle for longer than the limit** (5 minutes by default) → exit. Nothing is reading the page; the next open starts a new
  host in about a second.
"""

from __future__ import annotations

import os
import signal
import threading
import time

DEFAULT_IDLE_EXIT_S = 300  # five minutes: long enough that reopening reuses the host


def idle_limit():
    """Seconds of no requests before exiting; ``0`` disables the idle rule."""
    raw = os.environ.get('HEWN_STUDIO_IDLE_EXIT_S')
    if raw is None or raw == '':
        return DEFAULT_IDLE_EXIT_S
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_IDLE_EXIT_S
    return max(0, value)


def parent_pid():
    """The launcher's pid, or ``None`` for a manual run that should not expire."""
    raw = os.environ.get('HEWN_STUDIO_PARENT_PID')
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 1 else None


def parent_alive(pid):
    if pid is None:
        return True          # a manual `python -m music_studio.host` stays until Ctrl-C
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def should_exit(*, now, last_activity, limit, parent_up, busy):
    """One tick. Pure, so the rules can be tested without a server or a clock."""
    if not parent_up:
        return True
    if busy:
        return False
    if limit <= 0:
        return False
    return (now - last_activity) >= limit


def busy(store):
    """True while any take is queued or running — a render outlives the page that started it."""
    try:
        for project in store.all():
            for take in project.get('takes') or []:
                if take.get('state') in ('queued', 'running'):
                    return True
    except Exception:
        return False
    return False


class Watchdog:
    """Ticks every ``interval`` seconds and asks the socket for the server's life."""

    def __init__(self, *, store_factory, interval=30.0, limit=None, parent=None, clock=time.time):
        self.store_factory = store_factory
        self.interval = interval
        self.limit = idle_limit() if limit is None else limit
        self.parent = parent_pid() if parent is None else parent
        self.clock = clock
        self.last_activity = self.clock()
        self._stop = threading.Event()
        self._thread = None

    def touch(self):
        self.last_activity = self.clock()

    def decide(self):
        return should_exit(now=self.clock(), last_activity=self.last_activity, limit=self.limit,
                           parent_up=parent_alive(self.parent),
                           busy=busy(self.store_factory()))

    def start(self):
        def loop():
            while not self._stop.wait(self.interval):
                try:
                    if not self.decide():
                        continue
                except Exception:
                    continue          # a broken check must not take the host down
                self.stop_host()
                return

        self._thread = threading.Thread(target=loop, name='studio-watchdog', daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    @staticmethod
    def stop_host():
        """Ask uvicorn to shut down the way a signal would, and forget the state file."""
        try:
            from music_studio import host_launcher

            host_launcher.forget()
        except Exception:
            pass
        os.kill(os.getpid(), signal.SIGTERM)
