"""Process probes get the allowlisted child environment, not every provider key.

Review item 4 covered the render children and the studio host, but the small read-only probes still
inherited the whole Hermes process: ``nvidia-smi`` in the hardware panel, ``git`` in the version stamp
and the update notice, and ``ps`` in the job liveness checks. None of them has any use for a provider
key, so each must be handed ``minimal_env()``.
"""

from __future__ import annotations

import types

import pytest

SECRET = 'OPENAI_API_KEY'


def _result(stdout='', returncode=0):
    return types.SimpleNamespace(stdout=stdout, stderr='', returncode=returncode)


def test_nvidia_smi_is_run_without_provider_keys(monkeypatch):
    from standalone import hardware

    monkeypatch.setenv(SECRET, 'sk-should-not-leak')
    seen = {}

    def fake_run(args, **kwargs):
        seen['env'] = kwargs.get('env')
        return _result(returncode=1)

    monkeypatch.setattr(hardware.subprocess, 'run', fake_run)
    hardware._run(['nvidia-smi'])

    assert seen['env'] is not None, 'nvidia-smi inherited the whole environment'
    assert SECRET not in seen['env'], 'a provider key reached nvidia-smi'


def test_version_stamp_git_is_run_without_provider_keys(monkeypatch):
    from music_studio import version_stamp

    monkeypatch.setenv(SECRET, 'sk-should-not-leak')
    seen = []

    def fake_run(args, **kwargs):
        seen.append(kwargs.get('env'))
        return _result(stdout='abc123\n')

    monkeypatch.setattr(version_stamp.subprocess, 'run', fake_run)
    assert version_stamp._git('rev-parse', 'HEAD') == 'abc123'

    assert seen, 'git was never run'
    for env in seen:
        assert env is not None, 'git inherited the whole environment'
        assert SECRET not in env, 'a provider key reached git'


def test_update_check_git_is_run_without_provider_keys(monkeypatch):
    from music_studio import updates

    monkeypatch.setenv(SECRET, 'sk-should-not-leak')
    seen = {}

    def fake_run(args, **kwargs):
        seen['env'] = kwargs.get('env')
        return _result(stdout='abc123\n')

    monkeypatch.setattr(updates.subprocess, 'run', fake_run)
    assert updates._git('rev-parse', 'HEAD') == 'abc123'

    assert seen['env'] is not None, 'git inherited the whole environment'
    assert SECRET not in seen['env'], 'a provider key reached git'


def test_macos_ps_start_time_is_probed_without_provider_keys(monkeypatch):
    from music_studio import jobs

    monkeypatch.setenv(SECRET, 'sk-should-not-leak')
    seen = []

    def fake_run(args, **kwargs):
        seen.append(kwargs.get('env'))
        return _result(stdout=b'Mon Jan  1 00:00:00 2026\n')

    monkeypatch.setattr(jobs.subprocess, 'run', fake_run)
    assert jobs._macos_start_time(1234) is not None

    assert seen[0] is not None, 'ps inherited the whole environment'
    assert SECRET not in seen[0], 'a provider key reached ps'


def test_ps_process_state_is_probed_without_provider_keys(monkeypatch):
    from music_studio import jobs

    monkeypatch.setenv(SECRET, 'sk-should-not-leak')
    seen = []

    def fake_run(args, **kwargs):
        seen.append(kwargs.get('env'))
        return _result(stdout=b'S\n')

    def no_proc(*args, **kwargs):
        raise FileNotFoundError

    # Force the ps branch: Linux normally answers from /proc, so pretend not to have it.
    monkeypatch.setattr(jobs.sys, 'platform', 'darwin')
    monkeypatch.setattr(jobs, 'open', no_proc, raising=False)
    monkeypatch.setattr(jobs.subprocess, 'run', fake_run)
    assert jobs._stat_state(1234) == b'S'

    assert seen[0] is not None, 'ps inherited the whole environment'
    assert SECRET not in seen[0], 'a provider key reached ps'
