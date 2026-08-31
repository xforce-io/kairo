"""#200 public-read reuses Console shell with a permission gate."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.workspace import Workspace
from test_public_read import _full_public_root, _public_client


def test_s1_home_lists_all_workspaces_like_console(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    Workspace.init(root / "secret", topic="secret-private")
    (root / "secret" / "understanding.md").write_text("private-body\n", encoding="utf-8")
    c = _public_client(root)
    home = c.get("/")
    assert home.status_code == 200
    assert "/static/app.css" in home.text
    assert "kairo" in home.text.lower()
    assert "/w/ws" in home.text
    assert "/w/secret" in home.text
    assert "secret-private" in home.text
    assert 'hx-post="/workspaces"' not in home.text
    assert "card-trash" not in home.text
    ws = c.get("/w/ws")
    assert ws.status_code == 200
    assert "understanding.md" in ws.text
    assert "run-btn" not in ws.text
    assert "btn-add-ref" not in ws.text
    doc = c.get("/w/ws/doc", params={"path": "understanding.md"})
    assert doc.status_code == 200
    assert "alpha-secret-token" in doc.text
    secret_page = c.get("/w/secret")
    assert secret_page.status_code == 200


def test_s2_writes_denied_and_missing_workspace_404(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    c = _public_client(root)
    missing = c.get("/w/no-such-ws")
    assert missing.status_code == 404
    post = c.post("/workspaces", data={"topic": "injected"})
    assert post.status_code == 404
    run = c.post("/w/ws/run")
    assert run.status_code == 404
    assert not (root / "injected").exists()
    after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    assert after == before
    home = c.get("/")
    assert 'hx-post="/workspaces"' not in home.text


def test_console_mode_still_lists_all_and_allows_write_chrome(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    Workspace.init(root / "secret", topic="secret-private")
    c = TestClient(create_app(root, mode="console"))
    home = c.get("/")
    assert home.status_code == 200
    assert "/w/ws" in home.text
    assert "/w/secret" in home.text
    assert 'hx-post="/workspaces"' in home.text
