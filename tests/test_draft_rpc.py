import importlib.util
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from music_studio import llm_bridge

ROOT = Path(__file__).resolve().parents[1]


def register(model=None, rpc=True):
    spec = importlib.util.spec_from_file_location('draft_rpc_entry', ROOT / '__init__.py')
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    methods = {}
    ctx = SimpleNamespace(llm=model, register_tool=lambda **kw: None,
                          register_skill=lambda *args, **kw: None)
    if rpc:
        ctx.register_rpc = lambda name, handler: methods.update({name: handler})
    plugin.register(ctx)
    return methods


def result(methods, job):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        value = methods['studio.draft_result']({'job': job})
        if value['state'] != 'running':
            return value
        time.sleep(.01)
    pytest.fail('draft did not finish')


class Model:
    def complete(self, **kwargs):
        return {'text': json.dumps({'title': 'Night', 'caption': 'warm synth', 'lyrics': 'hello'})}


def test_register_draft_rpc_closure_and_running():
    entered, release = threading.Event(), threading.Event()
    class Slow(Model):
        def complete(self, **kwargs):
            entered.set()
            assert release.wait(3)
            return super().complete(**kwargs)
    methods = register(Slow())
    assert set(methods) == {'studio.draft', 'studio.draft_result'}
    llm_bridge.unbind()
    job = methods['studio.draft']({'prompt': 'night'})['job']
    try:
        assert entered.wait(3)
        assert methods['studio.draft_result']({'job': job}) == {'state': 'running'}
    finally:
        release.set()
    assert result(methods, job)['draft']['title'] == 'Night'
    assert methods['studio.draft_result']({'job': 'missing'}) == {'state': 'unknown'}


@pytest.mark.parametrize('exception, expected', [
    (RuntimeError('allow_model_override'), 'Hermes refused'),
    (TimeoutError('slow'), "Hermes' model did not answer within 120 seconds."),
])
def test_draft_rpc_failures(exception, expected):
    class Broken:
        def complete(self, **kwargs):
            raise exception
    methods = register(Broken())
    job = methods['studio.draft']({'prompt': 'night', 'model': 'chosen'})['job']
    value = result(methods, job)
    assert value['state'] == 'failed' and expected in value['error']
    if 'refused' in expected:
        assert value['commands'] == list(llm_bridge.TRUST_COMMANDS)
    else:
        assert 'commands' not in value


def test_draft_rpc_cap():
    release = threading.Event()
    class Slow(Model):
        def complete(self, **kwargs):
            assert release.wait(3)
            return super().complete(**kwargs)
    methods = register(Slow())
    try:
        jobs = [methods['studio.draft']({'prompt': 'night'})['job'] for _ in range(2)]
        with pytest.raises(RuntimeError, match='Two drafts'):
            methods['studio.draft']({'prompt': 'night'})
    finally:
        release.set()
    assert all(result(methods, job)['state'] == 'done' for job in jobs)


def test_register_without_rpc():
    assert register(Model(), rpc=False) == {}


def test_reload_preserves_jobs_and_slots():
    release = threading.Event()

    class Slow(Model):
        def complete(self, **kwargs):
            assert release.wait(3)
            return super().complete(**kwargs)

    before = register(Slow())
    jobs = [before['studio.draft']({'prompt': 'night'})['job'] for _ in range(2)]
    try:
        after = register(Model())
        assert after['studio.draft_result']({'job': jobs[0]}) == {'state': 'running'}
        with pytest.raises(RuntimeError, match='Two drafts'):
            after['studio.draft']({'prompt': 'night'})
    finally:
        release.set()
        for job in jobs:
            result(before, job)
    assert result(after, jobs[0])['state'] == 'done'


def test_unhashable_job():
    assert register()['studio.draft_result']({'job': []}) == {'state': 'unknown'}


def test_zero_style():
    from music_studio.drafting import draft_song
    messages = []
    def complete(prompt, **kwargs):
        messages.extend(prompt)
        return 'OK'
    draft_song({'prompt': 'night', 'action': 'chat', 'style': 0}, complete)
    assert 'Style influence: 0/100' in str(messages)
