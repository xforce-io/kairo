"""#78: dashboard 删除 workspace —— 键入 slug 确认 + 路径安全。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.workspace import Workspace, WorkspaceNotFound, delete_workspace


def test_delete_workspace_removes_dir_keeps_glossary(tmp_path):
    root = tmp_path / "serve"
    root.mkdir()
    (root / "glossary.yaml").write_text("entries: []\n")
    Workspace.init(root / "能源业务", topic="能源业务")
    (root / "能源业务" / "understanding.md").write_text("body")
    other = Workspace.init(root / "other", topic="other")

    delete_workspace(root, "能源业务")

    assert not (root / "能源业务").exists()
    assert (root / "glossary.yaml").is_file()
    assert other.root.is_dir()
    assert (other.root / "constitution.yaml").is_file()


def test_delete_workspace_rejects_traversal(tmp_path):
    root = tmp_path / "serve"
    root.mkdir()
    Workspace.init(root / "ws", topic="ws")
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "constitution.yaml").write_text("topic: x\n")

    for bad in ("..", "../secret", "ws/../secret", "ws/nested", ""):
        try:
            delete_workspace(root, bad)
            assert False, f"should reject {bad!r}"
        except (ValueError, WorkspaceNotFound):
            pass
    assert outside.is_dir()
    assert (root / "ws").is_dir()


def test_delete_workspace_missing_raises(tmp_path):
    root = tmp_path / "serve"
    root.mkdir()
    try:
        delete_workspace(root, "nope")
        assert False
    except WorkspaceNotFound:
        pass


def test_dashboard_has_trash_and_confirm_dialog(tmp_path):
    Workspace.init(tmp_path / "ws", topic="ws")
    r = TestClient(create_app(tmp_path)).get("/")
    assert r.status_code == 200
    assert "card-trash" in r.text
    assert 'id="del-ws-dlg"' in r.text
    assert 'name="confirm_name"' in r.text
    assert "kairoOpenDelWs" in r.text


def test_web_delete_wrong_confirm_rejected(tmp_path):
    Workspace.init(tmp_path / "ws", topic="ws")
    r = TestClient(create_app(tmp_path)).post(
        "/workspaces/ws/delete", data={"confirm_name": "wrong"}
    )
    assert r.status_code == 400
    assert (tmp_path / "ws").is_dir()


def test_web_delete_correct_confirm_removes_and_redirects(tmp_path):
    (tmp_path / "glossary.yaml").write_text("entries: []\n")
    Workspace.init(tmp_path / "ws", topic="ws")
    client = TestClient(create_app(tmp_path))
    r = client.post("/workspaces/ws/delete", data={"confirm_name": "ws"})
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") == "/"
    assert not (tmp_path / "ws").exists()
    assert (tmp_path / "glossary.yaml").is_file()


def test_web_delete_busy_rejected(tmp_path, monkeypatch):
    Workspace.init(tmp_path / "ws", topic="ws")
    app = create_app(tmp_path)

    class FakeReg:
        def is_running(self, slug: str) -> bool:
            return slug == "ws"

    app.state.registry = FakeReg()
    r = TestClient(app).post("/workspaces/ws/delete", data={"confirm_name": "ws"})
    assert r.status_code == 409
    assert (tmp_path / "ws").is_dir()


def test_web_delete_trims_confirm_name(tmp_path):
    Workspace.init(tmp_path / "ws", topic="ws")
    r = TestClient(create_app(tmp_path)).post(
        "/workspaces/ws/delete", data={"confirm_name": "  ws  "}
    )
    assert r.status_code == 200
    assert not (tmp_path / "ws").exists()
