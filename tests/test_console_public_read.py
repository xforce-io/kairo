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


def test_s2_ref_meta_hides_write_actions_in_public_read(tmp_path):
    """点参考时右栏仍走 Console 模板；只读面不得露出 Attach / Reprocess。"""
    root, rid, slug = _full_public_root(tmp_path)
    pub = _public_client(root)
    meta = pub.get(f"/w/{slug}/ref/{rid}")
    assert meta.status_code == 200
    assert "+ Attach material" not in meta.text
    assert "↻ Reprocess" not in meta.text
    assert f'hx-post="/w/{slug}/ref/{rid}/attach"' not in meta.text
    assert f'hx-post="/w/{slug}/ref/{rid}/retry"' not in meta.text
    assert f'hx-post="/w/{slug}/ref/{rid}/delete"' not in meta.text
    assert f'hx-post="/w/{slug}/ref/{rid}/title"' not in meta.text
    attach = pub.post(f"/w/{slug}/ref/{rid}/attach", data={"path": "/tmp/x"})
    assert attach.status_code == 404
    retry = pub.post(f"/w/{slug}/ref/{rid}/retry")
    assert retry.status_code == 404


def _assert_shareable_ref_page(client: TestClient, slug: str, rid: str) -> str:
    page = client.get(f"/w/{slug}", params={"ref": rid})
    assert page.status_code == 200
    share = f"/w/{slug}?ref={rid}"
    meta = f"/w/{slug}/ref/{rid}"
    assert f'href="{share}"' in page.text
    assert 'href="#"' not in page.text.split('id="refs-list"', 1)[-1].split("id=", 1)[0]
    assert f"is-active" in page.text
    assert share in page.text
    assert f'hx-get="{meta}"' in page.text
    assert 'hx-trigger="load"' in page.text
    assert f'hx-get="{meta}"' in page.text.split('id="meta"', 1)[-1][:400]
    return page.text


def test_share_url_selects_ref_in_public_read_and_hides_write_chrome(tmp_path):
    root, rid, slug = _full_public_root(tmp_path)
    pub = _public_client(root)
    _assert_shareable_ref_page(pub, slug, rid)
    meta = pub.get(f"/w/{slug}/ref/{rid}")
    assert meta.status_code == 200
    assert "+ Attach material" not in meta.text
    assert "↻ Reprocess" not in meta.text


def test_share_url_selects_ref_in_console_and_keeps_write_chrome(tmp_path):
    root, rid, slug = _full_public_root(tmp_path)
    c = TestClient(create_app(root, mode="console"))
    _assert_shareable_ref_page(c, slug, rid)
    meta = c.get(f"/w/{slug}/ref/{rid}")
    assert meta.status_code == 200
    assert "+ Attach material" in meta.text
    assert "↻ Reprocess" in meta.text


def _assert_one_click_share_control(html: str, slug: str, rid: str) -> None:
    share = f"/w/{slug}?ref={rid}"
    pane, _, reader = html.partition('<main id="reader"')
    assert f'data-share-path="{share}"' in pane
    assert "data-share-ref" in pane
    assert f'data-share-path="{share}"' not in reader
    assert "doc-export" not in pane
    assert "kairoPrintDoc" not in pane


def test_one_click_share_control_on_public_read_ref_meta(tmp_path):
    root, rid, slug = _full_public_root(tmp_path)
    pub = _public_client(root)
    meta = pub.get(f"/w/{slug}/ref/{rid}")
    assert meta.status_code == 200
    _assert_one_click_share_control(meta.text, slug, rid)
    assert "+ Attach material" not in meta.text
    assert "↻ Reprocess" not in meta.text
    page = pub.get(f"/w/{slug}", params={"ref": rid})
    assert page.status_code == 200
    assert "kairoCopyShareUrl" in page.text
    assert "prompt(" in page.text


def test_one_click_share_control_on_console_ref_meta(tmp_path):
    root, rid, slug = _full_public_root(tmp_path)
    c = TestClient(create_app(root, mode="console"))
    meta = c.get(f"/w/{slug}/ref/{rid}")
    assert meta.status_code == 200
    _assert_one_click_share_control(meta.text, slug, rid)
    assert "+ Attach material" in meta.text
    assert "↻ Reprocess" in meta.text
    page = c.get(f"/w/{slug}", params={"ref": rid})
    assert page.status_code == 200
    assert "kairoCopyShareUrl" in page.text
    assert "prompt(" in page.text


def test_console_mode_still_lists_all_and_allows_write_chrome(tmp_path):
    root, rid, slug = _full_public_root(tmp_path)
    Workspace.init(root / "secret", topic="secret-private")
    c = TestClient(create_app(root, mode="console"))
    home = c.get("/")
    assert home.status_code == 200
    assert "/w/ws" in home.text
    assert "/w/secret" in home.text
    assert 'hx-post="/workspaces"' in home.text
    meta = c.get(f"/w/{slug}/ref/{rid}")
    assert meta.status_code == 200
    assert "+ Attach material" in meta.text
    assert "↻ Reprocess" in meta.text
