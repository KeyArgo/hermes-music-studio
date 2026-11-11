import pytest
from music_studio.network import TargetStore, validate_base_url


def test_targets_persist_and_keep_distinct_ids(tmp_path):
    store=TargetStore(tmp_path)
    a=store.create('Spark E','http://192.0.2.52:8188')
    b=store.create('Second GPU','http://192.168.1.20:8188')
    assert a['id'] != b['id']
    assert TargetStore(tmp_path).get(a['id'])['url'] == 'http://192.0.2.52:8188'
    assert len(TargetStore(tmp_path).list()) == 2


@pytest.mark.parametrize('url', ['file:///tmp/x','http://user:pass@127.0.0.1:8188',
                                  'http://169.254.169.254/latest', 'http://example.com:8188/?token=secret',
                                  'http://127.0.0.1:8188/#fragment'])
def test_target_url_rejects_unsafe_forms(url):
    with pytest.raises(ValueError): validate_base_url(url)


def test_unknown_target_does_not_silently_fallback(tmp_path):
    with pytest.raises((KeyError, ValueError)):
        TargetStore(tmp_path).get('not-a-target')


def test_queue_rejects_target_changed_after_preflight(tmp_path):
    from music_studio.store import Store
    from music_studio.jobs import Jobs
    store = Store(tmp_path)
    targets = TargetStore(tmp_path)
    first = targets.create('first', 'http://10.0.0.1:8188')
    second = targets.create('second', 'http://10.0.0.2:8188')
    project = store.create(caption='gentle piano', instrumental=True,
                           engine='minimax_m3_comfy', comfy_target_id=second['id'])
    store.approve(project['project_id'])
    with pytest.raises(ValueError):
        Jobs(store).enqueue(project['project_id'], 1, engine_id='minimax_m3_comfy', target=first)
    assert store.get(project['project_id'])['takes'] == []


def test_queue_pins_target_url_after_deletion(tmp_path):
    from music_studio.store import Store
    from music_studio.jobs import Jobs
    store = Store(tmp_path)
    targets = TargetStore(tmp_path)
    target = targets.create('Spark', 'http://192.0.2.52:8188')
    project = store.create(caption='gentle piano', instrumental=True,
                           engine='minimax_m3_comfy', comfy_target_id=target['id'])
    store.approve(project['project_id'])
    take = Jobs(store).enqueue(project['project_id'], 1, engine_id='minimax_m3_comfy', target=target)
    targets.delete(target['id'])
    assert take['snapshot']['comfy_target_url'] == 'http://192.0.2.52:8188'
    assert store.get(project['project_id'])['takes'][0]['snapshot']['comfy_target_id'] == target['id']
