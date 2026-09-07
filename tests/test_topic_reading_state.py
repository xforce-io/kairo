from fastapi.testclient import TestClient

from kairo.models import Target
from kairo.web.server import create_app
from kairo.workspace import Workspace


def topic(tmp_path):
    ws = Workspace.init(tmp_path / 'topic', topic='研究')
    con = ws.constitution
    con.targets.append(Target(path='other.md', intent='另一结论'))
    ws.write_constitution(con)
    (ws.root / 'understanding.md').write_text('# Current conclusion')
    (ws.root / 'other.md').write_text('# Other conclusion')
    return ws, TestClient(create_app(tmp_path))


def test_default_and_explicit_reading_survive_get(tmp_path):
    ws, client = topic(tmp_path)
    response = client.get('/topics/topic')
    assert '<h1>Current conclusion</h1>' in response.text
    assert 'nav-doc is-active' in response.text
    selected = client.get('/w/topic/target', params={'path': 'other.md'})
    address = selected.headers['HX-Push-Url']
    for _ in range(2):
        restored = client.get(address)
        assert '<h1>Other conclusion</h1>' in restored.text
        assert '<h1>Current conclusion</h1>' not in restored.text
    alias = client.get('/topics/topic', params={'target': 'other.md'})
    assert '<h1>Other conclusion</h1>' in alias.text
    (ws.root / 'understanding.md').unlink()
    assert '<h1>Other conclusion</h1>' in client.get('/w/topic').text


def test_missing_invalid_and_unsafe_targets_do_not_fall_back(tmp_path):
    ws, client = topic(tmp_path)
    for target in ['missing.md', '', '../secret.md']:
        response = client.get('/w/topic', params={'target': target})
        assert 'This conclusion is unavailable' in response.text
        assert '<h1>Current conclusion</h1>' not in response.text
        assert 'Return to topic' in response.text
    (tmp_path / 'secret.md').write_text('SECRET BODY')
    (ws.root / 'other.md').unlink()
    (ws.root / 'other.md').symlink_to(tmp_path / 'secret.md')
    assert 'SECRET BODY' not in client.get('/w/topic?target=other.md').text
    assert client.get('/w/topic/target?path=other.md').status_code == 404
    (ws.root / 'understanding.md').unlink()
    assert 'No readable conclusion yet' in client.get('/w/topic').text


def test_explicit_ref_preserves_ref_path(tmp_path):
    _, client = topic(tmp_path)
    response = client.get('/topics/topic?ref=missing&target=other.md')
    assert '<h1>Current conclusion</h1>' not in response.text
    assert '<h1>Other conclusion</h1>' not in response.text


def test_public_read_never_defaults_to_private_conclusion(tmp_path):
    topic(tmp_path)
    client = TestClient(create_app(tmp_path, mode='public-read'))
    for path in ['/w/topic', '/topics/topic?target=understanding.md']:
        response = client.get(path)
        assert 'Current conclusion' not in response.text
