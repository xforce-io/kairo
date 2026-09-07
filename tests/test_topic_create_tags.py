from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from kairo import refs
from kairo.web.server import create_app
from kairo.workspace import Workspace


def test_confirmed_creation_and_reuse(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert 'confirm_tag' in client.get('/').text
    response = client.post('/workspaces', data={'topic': '新主题', 'confirm_tag': '1'})
    assert response.status_code == 200
    assert refs.list_tags(tmp_path) == ['新主题']
    assert Workspace.open(tmp_path / '新主题').constitution.include_tags == ['新主题']
    refs.create_tag(tmp_path, '已有')
    assert client.post('/workspaces', data={'topic': '已有', 'confirm_tag': '1'}).status_code == 200
    assert refs.list_tags(tmp_path).count('已有') == 1
    assert client.post('/workspaces', data={'topic': '已有', 'confirm_tag': '1'}).status_code == 400
    assert client.post('/workspaces', data={'topic': '../bad', 'confirm_tag': '1'}).status_code == 400
    assert client.post('/workspaces', data={'topic': '修正', 'confirm_tag': '1'}).status_code == 200


@pytest.mark.parametrize('failure', ['prepare', 'catalog', 'publish'])
def test_creation_failures_preserve_existing_catalog(tmp_path, monkeypatch, failure):
    refs.create_tag(tmp_path, 'existing')
    before = refs.catalog_path(tmp_path).read_bytes()
    def fail(*args, **kwargs):
        raise OSError('injected failure')
    if failure == 'prepare':
        monkeypatch.setattr(Workspace, 'write_constitution', fail)
    elif failure == 'catalog':
        monkeypatch.setattr(refs, 'save_catalog', fail)
    else:
        monkeypatch.setattr(refs.os, 'rename', fail)
    with pytest.raises(OSError):
        refs.create_topic_with_tag(tmp_path, 'new', confirm_tag=True)
    assert not (tmp_path / 'new').exists()
    assert refs.catalog_path(tmp_path).read_bytes() == before
    assert not (tmp_path / '.kairo/topic-create.json').exists()
    assert not list((tmp_path / '.kairo').glob('topic-create-*'))


def test_concurrent_creations_do_not_lose_tags(tmp_path):
    def create(name):
        try:
            refs.create_topic_with_tag(tmp_path, name, confirm_tag=True)
            return True
        except refs.RefError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(create, ['same'] * 4)) == 1
        assert all(pool.map(create, ['one', 'two', 'three']))
    assert set(refs.list_tags(tmp_path)) == {'same', 'one', 'two', 'three'}


def test_interrupted_creation_rolls_back_before_publish(tmp_path, monkeypatch):
    refs.create_tag(tmp_path, 'existing')
    def interrupt(*args):
        raise KeyboardInterrupt
    with monkeypatch.context() as patch:
        patch.setattr(refs.os, 'rename', interrupt)
        with pytest.raises(KeyboardInterrupt):
            refs.create_topic_with_tag(tmp_path, 'new', confirm_tag=True)
    assert (tmp_path / '.kairo/topic-create.json').exists()
    assert refs.list_tags(tmp_path) == ['existing']
    assert not (tmp_path / 'new').exists()
    assert not (tmp_path / '.kairo/topic-create.json').exists()
