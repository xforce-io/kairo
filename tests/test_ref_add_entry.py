from fastapi.testclient import TestClient
from kairo.workspace import Workspace
from kairo.web.server import create_app


def test_add_entry_upload_and_path_feedback(tmp_path):
    ws = Workspace.init(tmp_path / 'topic')
    client = TestClient(create_app(tmp_path))
    page = client.get('/w/topic').text
    assert page.index('class="btn btn-step btn-add-ref"') < page.index('id="refs-list"')
    assert page.index('name="file"') < page.index('name="path"')
    response = client.post('/w/topic/ref', files={'file': ('browser.txt', b'evidence', 'text/plain')})
    assert response.status_code == 200
    assert 'hx-swap-oob="outerHTML"' in response.text
    assert 'id="ref-add-result"' in response.text
    assert '?ref=' in response.text
    response = client.post('/w/topic/ref', data={'path': str(tmp_path / 'missing.txt')})
    assert response.status_code == 400
    assert 'ref-add-result' not in response.text
    source = tmp_path / 'local.txt'
    source.write_text('local evidence')
    response = client.post('/w/topic/ref', data={'path': str(source)})
    assert response.status_code == 200
    assert 'ref-add-result' in response.text


def test_add_ref_success_states_processing_and_refreshes_run_status(tmp_path):
    Workspace.init(tmp_path / 'topic')
    client = TestClient(create_app(tmp_path))
    page = client.get('/w/topic').text
    assert 'Up to date' in page
    assert 'pending processing items' not in page
    response = client.post('/w/topic/ref', files={'file': ('browser.txt', b'evidence', 'text/plain')})
    assert response.status_code == 200
    text = response.text
    result = text.split('id="ref-add-result"', 1)[1]
    assert 'Added. Processing is needed.' in result
    assert '?ref=' in result
    assert 'check its processing status' not in text
    processing = text.split('id="processing-status"', 1)[1]
    assert 'hx-swap-oob' in processing.split('>', 1)[0]
    assert '1 pending processing items' in processing
    button = text.split('id="run-btn-wrap"', 1)[1]
    assert 'hx-swap-oob' in button.split('>', 1)[0]
    assert '▶ Run' in button
    assert 'Up to date' not in button.split('</div>', 1)[0]
