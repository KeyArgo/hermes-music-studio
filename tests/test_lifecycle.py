"""The studio host's own life: it must end when nobody is using it, and never mid-render.

Measured before this existed: a detached host ran at 0.1% CPU and ~58 MB, and survived Hermes quitting
and the plugin being disabled, because nothing ever told it to leave.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import lifecycle  # noqa: E402


class RuleTests(unittest.TestCase):
    def decide(self, **kwargs):
        base = {'now': 1000.0, 'last_activity': 1000.0, 'limit': 900, 'parent_up': True, 'busy': False}
        base.update(kwargs)
        return lifecycle.should_exit(**base)

    def test_a_live_launcher_with_recent_activity_keeps_the_host(self):
        self.assertFalse(self.decide(last_activity=999.0))

    def test_a_dead_launcher_ends_the_host_whatever_the_clock_says(self):
        self.assertTrue(self.decide(parent_up=False, last_activity=1000.0))

    def test_an_idle_host_ends_when_the_limit_passes(self):
        self.assertTrue(self.decide(last_activity=1000.0 - 901))

    def test_an_idle_host_that_is_still_inside_the_limit_stays(self):
        self.assertFalse(self.decide(last_activity=1000.0 - 899))

    def test_a_render_in_flight_keeps_the_host_alive_even_when_idle(self):
        """Killing the host mid-render is the failure the detachment exists to prevent."""
        self.assertFalse(self.decide(last_activity=0.0, busy=True, parent_up=True))

    def test_a_dead_launcher_during_a_render_still_ends_the_host(self):
        self.assertTrue(self.decide(parent_up=False, busy=True))

    def test_zero_disables_the_idle_rule_but_not_the_parent_rule(self):
        self.assertFalse(self.decide(last_activity=0.0, limit=0))
        self.assertTrue(self.decide(parent_up=False, limit=0))


class EnvironmentTests(unittest.TestCase):
    def test_the_default_limit_is_five_minutes(self):
        saved = os.environ.pop('HEWN_STUDIO_IDLE_EXIT_S', None)
        try:
            self.assertEqual(lifecycle.idle_limit(), 300)
        finally:
            if saved is not None:
                os.environ['HEWN_STUDIO_IDLE_EXIT_S'] = saved

    def test_a_configured_limit_is_read_and_nonsense_falls_back(self):
        os.environ['HEWN_STUDIO_IDLE_EXIT_S'] = '120'
        self.assertEqual(lifecycle.idle_limit(), 120)
        os.environ['HEWN_STUDIO_IDLE_EXIT_S'] = 'soon'
        self.assertEqual(lifecycle.idle_limit(), 300)
        os.environ['HEWN_STUDIO_IDLE_EXIT_S'] = '-5'
        self.assertEqual(lifecycle.idle_limit(), 0, 'a negative limit means off, not instant exit')
        os.environ.pop('HEWN_STUDIO_IDLE_EXIT_S', None)

    def test_a_manual_run_without_a_parent_never_expires(self):
        saved = os.environ.pop('HEWN_STUDIO_PARENT_PID', None)
        try:
            self.assertIsNone(lifecycle.parent_pid())
            self.assertTrue(lifecycle.parent_alive(None))
        finally:
            if saved is not None:
                os.environ['HEWN_STUDIO_PARENT_PID'] = saved

    def test_a_dead_pid_is_detected(self):
        pid = os.fork() if hasattr(os, 'fork') else None
        if pid == 0:                      # child exits at once, so the pid is free almost immediately
            os._exit(0)
        if pid:
            os.waitpid(pid, 0)
            self.assertFalse(lifecycle.parent_alive(pid))


class BusyTests(unittest.TestCase):
    class FakeStore:
        def __init__(self, projects):
            self.projects = projects

        def all(self):
            return self.projects

    def test_a_queued_or_running_take_counts_as_busy(self):
        for state in ('queued', 'running'):
            store = self.FakeStore([{'takes': [{'state': state}]}])
            self.assertTrue(lifecycle.busy(store), state)

    def test_finished_takes_are_not_busy(self):
        store = self.FakeStore([{'takes': [{'state': 'succeeded'}, {'state': 'failed'}]}])
        self.assertFalse(lifecycle.busy(store))

    def test_an_unreadable_library_does_not_count_as_busy(self):
        class Broken:
            def all(self):
                raise OSError('library is not there')

        self.assertFalse(lifecycle.busy(Broken()))


class LauncherTests(unittest.TestCase):
    def test_the_launcher_tells_the_host_which_process_to_watch(self):
        source = (ROOT / 'music_studio' / 'host_launcher.py').read_text()
        self.assertIn("'HEWN_STUDIO_PARENT_PID': str(os.getpid())", source)
        host = (ROOT / 'music_studio' / 'host.py').read_text()
        self.assertIn('_watchdog.start()', host)
        self.assertIn('_watchdog.touch()', host, 'requests must count as activity')


if __name__ == '__main__':
    unittest.main()
