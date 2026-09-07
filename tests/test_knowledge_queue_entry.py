from fastapi.testclient import TestClient
from kairo.workspace import Workspace
from kairo.web.server import create_app
from kairo.knowledge_review import ingest_candidates, load_review


def test_queue_selection_filter_and_review_feedback(tmp_path):
    ws = Workspace.init(tmp_path / 'topic')
    (ws.root / 'understanding.md').write_text('evidence')
    ingest_candidates(ws.root, source_kind='compose', path='understanding.md', source_text='evidence', drafts=[{'title': 'Candidate', 'quote': 'evidence'}])
    client = TestClient(create_app(tmp_path))
    response = client.get('/knowledge?workspace=topic')
    assert response.status_code == 200
    assert response.text.index('data-queue="candidates"') < response.text.index('data-queue="global"')
    candidate = load_review(ws.root).candidates[0]
    response = client.post(f'/w/topic/knowledge/candidates/{candidate.id}/ignore?queue=candidates')
    assert response.status_code == 200
    assert 'Candidate</strong>' not in response.text
    assert 'aria-current="page"' in response.text
    response = client.get('/knowledge?workspace=topic&queue=errors&filter=missing')
    assert response.text.index('data-queue="errors"') < response.text.index('data-queue="candidates"')
    response = client.get('/knowledge?workspace=topic&queue=invalid')
    assert response.text.index('data-queue="candidates"') < response.text.index('data-queue="errors"')
