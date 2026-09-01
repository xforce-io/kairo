"""#200/#218 public-read reuses Console shell with a permission gate."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kairo.web.public import PUBLIC_STATE_FILENAME
from kairo.web.server import create_app
from kairo.workspace import Workspace
from test_public_read import _full_public_root, _public_client

_PRIV_REF = "2026-01-02-priv"


def _console_client(root) -> TestClient:
    return TestClient(create_app(root, mode="console"))


def _add_secret_workspace(root) -> None:
    Workspace.init(root / "secret", topic="secret-private")
    (root / "secret" / "understanding.md").write_text("private-body\n", encoding="utf-8")


def test_s1_console_lists_unpublished_public_read_does_not(tmp_path):
    root, rid, slug = _full_public_root(tmp_path)
    _add_secret_workspace(root)
    console = _console_client(root)
    pub = _public_client(root)

    chome = console.get("/")
    assert chome.status_code == 200
    assert "/w/ws" in chome.text
    assert "/w/secret" in chome.text
    assert "secret-private" in chome.text
    cws = console.get("/w/secret")
    assert cws.status_code == 200
    cpriv = console.get(f"/w/{slug}/ref/{_PRIV_REF}")
    assert cpriv.status_code == 200
    assert f'data-public-lock="locked"' in cpriv.text
    assert f'hx-post="/w/{slug}/ref/{_PRIV_REF}/public"' in cpriv.text
    pub_meta = console.get(f"/w/{slug}/ref/{rid}")
    assert pub_meta.status_code == 200
    assert f'data-public-lock="unlocked"' in pub_meta.text

    home = pub.get("/")
    assert home.status_code == 200
    assert "/static/app.css" in home.text
    assert "kairo" in home.text.lower()
    assert 'href="/knowledge"' not in home.text
    assert "/w/ws" in home.text
    assert "/w/secret" not in home.text
    assert "secret-private" not in home.text
    assert 'hx-post="/workspaces"' not in home.text
    assert "card-trash" not in home.text
    ws = pub.get("/w/ws")
    assert ws.status_code == 200
    assert "understanding.md" in ws.text
    assert "run-btn" not in ws.text
    assert "btn-add-ref" not in ws.text
    assert _PRIV_REF not in ws.text
    doc = pub.get("/w/ws/doc", params={"path": "understanding.md"})
    assert doc.status_code == 200
    assert "alpha-secret-token" in doc.text


def test_s2_unpublished_matches_missing_404_and_writes_denied(tmp_path):
    root, _, slug = _full_public_root(tmp_path)
    _add_secret_workspace(root)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    pub = _public_client(root)
    missing_ws = pub.get("/w/no-such-ws")
    hidden_ws = pub.get("/w/secret")
    assert missing_ws.status_code == 404
    assert hidden_ws.status_code == 404
    assert hidden_ws.text == missing_ws.text
    missing_ref = pub.get(f"/w/{slug}/ref/no-such-ref")
    hidden_ref = pub.get(f"/w/{slug}/ref/{_PRIV_REF}")
    assert missing_ref.status_code == 404
    assert hidden_ref.status_code == 404
    assert hidden_ref.text == missing_ref.text
    post = pub.post("/workspaces", data={"topic": "injected"})
    assert post.status_code == 404
    run = pub.post("/w/ws/run")
    assert run.status_code == 404
    lock = pub.post(f"/w/{slug}/ref/{_PRIV_REF}/public", data={"public": "1"})
    assert lock.status_code == 404
    assert not (root / "injected").exists()
    after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    assert after == before
    home = pub.get("/")
    assert 'hx-post="/workspaces"' not in home.text


def test_s3_console_lock_toggles_public_read_access(tmp_path):
    root, _, slug = _full_public_root(tmp_path)
    console = _console_client(root)
    pub = _public_client(root)
    assert pub.get(f"/w/{slug}/ref/{_PRIV_REF}").status_code == 404
    unlock = console.post(
        f"/w/{slug}/ref/{_PRIV_REF}/public", data={"public": "1"}
    )
    assert unlock.status_code == 200
    assert f'data-public-lock="unlocked"' in unlock.text
    opened = pub.get(f"/w/{slug}/ref/{_PRIV_REF}")
    assert opened.status_code == 200
    assert "data-public-lock" not in opened.text
    assert f'hx-post="/w/{slug}/ref/{_PRIV_REF}/public"' not in opened.text
    lock = console.post(
        f"/w/{slug}/ref/{_PRIV_REF}/public", data={"public": "0"}
    )
    assert lock.status_code == 200
    assert f'data-public-lock="locked"' in lock.text
    closed = pub.get(f"/w/{slug}/ref/{_PRIV_REF}")
    missing = pub.get(f"/w/{slug}/ref/no-such-ref")
    assert closed.status_code == 404
    assert closed.text == missing.text


def test_corrupt_snapshot_lock_does_not_write(tmp_path):
    root, _, slug = _full_public_root(tmp_path)
    state = root / PUBLIC_STATE_FILENAME
    state.write_text("{nope", encoding="utf-8")
    before = state.read_bytes()
    console = _console_client(root)
    r = console.post(
        f"/w/{slug}/ref/{_PRIV_REF}/public", data={"public": "1"}
    )
    assert r.status_code == 409
    assert state.read_bytes() == before


def test_s4_public_read_meta_has_no_publication_control(tmp_path):
    root, rid, slug = _full_public_root(tmp_path)
    pub = _public_client(root)
    meta = pub.get(f"/w/{slug}/ref/{rid}")
    assert meta.status_code == 200
    assert "data-public-lock" not in meta.text
    assert f'hx-post="/w/{slug}/ref/{rid}/public"' not in meta.text
    assert "+ Attach material" not in meta.text


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


def test_s1_timeline_range_hides_write_review_in_public_read(tmp_path):
    """#210: public-read 区间有 digest 也不得提交写回顾；Console 仍可写。"""
    from test_timeline_web import _two_ws

    root, wa, _ = _two_ws(tmp_path)
    (wa.references_dir() / "2026-08-25-weekly" / "digest.md").write_text("周会")
    pub = TestClient(create_app(root, mode="public-read"))
    r = pub.get("/timeline", params={"from": "2026-08-24", "to": "2026-08-25"})
    assert r.status_code == 200
    assert r.text.count('action="/timeline/review"') == 0
    assert "Write this review" not in r.text
    assert "写这段回顾" not in r.text
    assert (
        "This surface is read-only — cannot write a review." in r.text
        or "此面只读，不能写回顾。" in r.text
    )
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    post = pub.post(
        "/timeline/review",
        data={"from": "2026-08-24", "to": "2026-08-25"},
        follow_redirects=False,
    )
    assert post.status_code == 404
    after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    assert after == before

    con = TestClient(create_app(root, mode="console"))
    cr = con.get("/timeline", params={"from": "2026-08-24", "to": "2026-08-25"})
    assert cr.status_code == 200
    assert 'action="/timeline/review"' in cr.text
    assert "Write this review" in cr.text or "写这段回顾" in cr.text
