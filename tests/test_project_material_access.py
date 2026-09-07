from fastapi.testclient import TestClient
from kairo.projects import create_project, link_workspace
from kairo.workspace import Workspace
from kairo.web.server import create_app


def test_members_fold_after_datasources_and_safe_return_context(tmp_path):
    ws = Workspace.init(tmp_path / 'topic')
    source = tmp_path / 'material.txt'
    source.write_text('material')
    ref = ws.add([source])
    project = create_project(tmp_path, 'Project')
    link_workspace(tmp_path, project.id, 'topic')
    client = TestClient(create_app(tmp_path))
    page = client.get(f'/projects/{project.id}').text
    assert 'id="project-member-refs"' in page
    assert page.index('/datasources"') < page.index('id="project-member-refs"')
    back = f'/projects/{project.id}?q=material&materials=1&sort=time'
    response = client.get(f'/refs/{ref}', params={'home': 'topic', 'back': back})
    assert response.status_code == 200
    assert f'href="{back.replace("&", "&amp;")}"' in response.text
    response = client.get(f'/refs/{ref}', params={'home': 'topic', 'back': '//evil.test/'})
    assert 'href="//evil.test/' not in response.text
