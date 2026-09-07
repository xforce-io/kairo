import re
from fastapi.testclient import TestClient
from kairo.web.server import create_app
from kairo.input_citations import line_range, numbered_content
from kairo.project_materials import content_version
from test_project_context_task import _prepare, _load, _cli, ProjectCliTestProvider


class LocatedProvider(ProjectCliTestProvider):
    locate = True


def test_task_cli_to_archived_location_and_fallback(tmp_path, monkeypatch):
    serve, pid, ds, _, ws = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr('kairo.projects.select_project_agent', lambda: LocatedProvider())
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('cite lines')
    task = _load(_cli(['task', 'create', pid, '--name', 'located', '--prompt-file', str(prompt)], serve, monkeypatch))
    run = _load(_cli(['task', 'run', pid, task['id']], serve, monkeypatch))
    assert run['status'] == 'succeeded', run
    client = TestClient(create_app(serve), headers={"accept-language": "en"})
    artifact = f"/projects/{pid}/runs/{run['id']}"
    html = client.get(artifact).text
    links = re.findall(r'href="([^"]+/inputs/[^"?]+\?lines=L1#input-location)"', html)
    assert len(links) == 2
    (ws.root / 'understanding.md').write_text('changed after Run')
    for link in links:
        page = client.get(link)
        assert page.status_code == 200
        assert 'class="input-line selected" data-line="1"' in page.text
        assert 'changed after Run' not in page.text
        assert f'href="{artifact}"' in page.text
        base = link.split('?')[0]
        for suffix in ('', '?lines=L0', '?lines=L99999', '?lines=bad'):
            fallback = client.get(base + suffix)
            assert fallback.status_code == 200
            assert 'input-line selected' not in fallback.text
            assert 'complete archived input' in fallback.text
        assert client.get(base.rsplit('/', 1)[0] + '/inp-other').status_code == 404


def test_line_locations_do_not_modify_content():
    content = 'a\r\nb\n'
    before = content_version(content)
    assert numbered_content(content) == '1: a\n2: b'
    assert content_version(content) == before
    assert line_range('L1-L2', 2) == (1, 2)
    for invalid in ('L0', 'L2-L1', 'L1-L3', 'L1x', ''):
        assert line_range(invalid, 2) is None
