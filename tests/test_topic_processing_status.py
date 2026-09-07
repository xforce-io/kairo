from fastapi.testclient import TestClient
from kairo.models import TargetState
from kairo.web.server import create_app
from kairo.workspace import Workspace


def test_readable_body_and_processing_failure_are_separate(tmp_path):
    ws = Workspace.init(tmp_path / 'topic', topic='研究')
    (ws.root / 'understanding.md').write_text('# Preserved conclusion')
    state = ws.read_state()
    state.targets['understanding.md'] = TargetState(status='blocked', reason='provider-failed')
    ws.write_state(state)
    client = TestClient(create_app(tmp_path))
    response = client.get('/w/topic')
    assert '<h1>Preserved conclusion</h1>' in response.text
    assert 'Existing body is readable' in response.text
    assert 'Current processing is incomplete' in response.text
    assert 'Can be retried with Run' in response.text
    assert '?target=understanding.md' in response.text
    assert 'Technical details</summary>' in response.text
    assert '<details open' not in response.text
    assert ws.read_state().targets['understanding.md'].status == 'blocked'


def test_manual_and_unknown_processing_are_not_success(tmp_path):
    ws = Workspace.init(tmp_path / 'topic', topic='研究')
    (ws.root / 'understanding.md').write_text('# Old conclusion')
    state = ws.read_state()
    state.targets['understanding.md'] = TargetState(status='blocked', reason='manual-edit')
    ws.write_state(state)
    client = TestClient(create_app(tmp_path))
    response = client.get('/w/topic')
    assert 'Needs manual action' in response.text
    assert 'Accept the edit as baseline' in response.text
    assert 'Current processing is complete.' not in response.text
    state.targets.clear()
    ws.write_state(state)
    assert 'Processing freshness is not yet known.' in client.get('/w/topic').text
