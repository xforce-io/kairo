from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.workspace import Workspace


def _client(root):
    return TestClient(create_app(root))


def test_healthz(tmp_path):
    r = _client(tmp_path).get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_dashboard_lists_workspaces(tmp_path):
    Workspace.init(tmp_path / "alpha-ws", topic="阿尔法")
    Workspace.init(tmp_path / "beta-ws", topic="贝塔")
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "alpha-ws" in r.text and "beta-ws" in r.text
    assert "阿尔法" in r.text


def test_dashboard_shows_create_workspace_entry(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert 'hx-post="/workspaces"' in r.text
    assert "新建 workspace" in r.text


def test_dashboard_card_link_is_url_encoded(tmp_path):
    # topic 允许空格 → 目录名含空格;卡片链接必须 url-encode,否则点击 404
    Workspace.init(tmp_path / "v1 draft", topic="v1 draft")
    c = _client(tmp_path)
    r = c.get("/")
    assert 'href="/w/v1%20draft"' in r.text
    assert 'href="/w/v1 draft"' not in r.text
    # 编码后的路径可正常打开
    assert c.get("/w/v1 draft").status_code == 200


def _ws_with_step(root, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = Workspace.init(root / "ws", topic="主题")
    (root / "m.txt").write_text("王强会议:落地优先级")
    ws.add([root / "m.txt"])
    step(ws, select_provider())
    return ws


def test_workspace_view_lists_targets_and_refs(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    r = TestClient(create_app(tmp_path)).get("/w/ws")
    assert r.status_code == 200
    assert "understanding.md" in r.text and "assessment.md" in r.text


def test_workspace_view_404_for_unknown(tmp_path):
    r = TestClient(create_app(tmp_path)).get("/w/nope")
    assert r.status_code == 404


def test_doc_renders_markdown(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    c = TestClient(create_app(tmp_path))
    r = c.get("/w/ws/doc", params={"path": "understanding.md"})
    assert r.status_code == 200
    assert "落地优先级" in r.text  # stub 把正文带进 understanding


def test_doc_rejects_path_traversal(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    c = TestClient(create_app(tmp_path))
    assert c.get("/w/ws/doc", params={"path": "../m.txt"}).status_code == 404
    assert c.get("/w/ws/doc", params={"path": "/etc/hosts"}).status_code == 404


def test_ref_detail_shows_forms(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    ref_id = next(iter(__import__("os").listdir(tmp_path / "ws" / "references")))
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{ref_id}")
    assert r.status_code == 200 and ("transcript" in r.text or "digest" in r.text)
