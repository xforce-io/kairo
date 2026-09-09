"""#357 independent Ref details can attach forms to the Ref's own home."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient

from kairo.refs import add_global_ref, add_tag, create_tag, global_home, set_include_tags
from kairo.web.server import create_app
from kairo.workspace import Workspace
from test_public_read import _full_public_root, _public_client


def _client(root: Path) -> TestClient:
    return TestClient(create_app(root))


def _png(path: Path, payload: bytes = b"\x89PNG\r\n") -> Path:
    path.write_bytes(payload)
    return path


def _topic_with_global_member(tmp_path: Path) -> tuple[Path, str, TestClient]:
    serve = tmp_path / "root"
    serve.mkdir()
    Workspace.init(serve / "energy", topic="energy")
    create_tag(serve, "energy")
    set_include_tags(serve, "energy", ["energy"])
    src = tmp_path / "memo.txt"
    src.write_text("voice memo")
    rid = add_global_ref(serve, [src], ref_id="global-memo", copy=True)
    add_tag(serve, home="", ref_id=rid, tag="energy")
    return serve, rid, _client(serve)


def test_s1_details_attach_writes_global_home_topic_stays_read_only(tmp_path):
    serve, rid, client = _topic_with_global_member(tmp_path)
    meta = client.get(f"/w/energy/ref/{rid}", params={"home": "global"})
    assert meta.status_code == 200
    assert f'hx-post="/w/energy/ref/{rid}/attach"' not in meta.text
    assert 'id="attach-dlg"' not in meta.text
    assert f'href="/refs/{rid}?home=global"' in meta.text

    page = client.get(f"/refs/{rid}", params={"home": "global"})
    assert page.status_code == 200
    assert 'id="attach-dlg"' in page.text
    assert f'action="/refs/{rid}/attach"' in page.text

    img = _png(tmp_path / "board.png")
    posted = client.post(
        f"/refs/{rid}/attach",
        data={"path": str(img), "home": "global"},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert posted.headers["location"] == f"/refs/{rid}"

    shown = client.get(f"/refs/{rid}", params={"home": "global"})
    assert "board.png" in shown.text
    gws = global_home(serve)
    man = gws.read_manifest(rid)
    atts = [f for f in man.forms if f.role == "attachment"]
    assert len(atts) == 1
    assert atts[0].location.startswith(f"references/{rid}/")
    assert (gws.root / atts[0].location).is_file()
    assert not (serve / "energy" / "references" / rid).exists()


def test_s2_topic_home_details_attach_stays_in_topic(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    ws = Workspace.init(serve / "energy", topic="energy")
    src = tmp_path / "local.txt"
    src.write_text("local note")
    rid = ws.add([src], ref_id="local-memo", copy=True)
    client = _client(serve)
    page = client.get(f"/refs/{rid}", params={"home": "energy"})
    assert page.status_code == 200
    assert 'id="attach-dlg"' in page.text
    assert 'name="home" value="energy"' in page.text

    img = _png(tmp_path / "slide.png")
    posted = client.post(
        f"/refs/{rid}/attach",
        data={"path": str(img), "home": "energy"},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert "home=energy" in posted.headers["location"]

    shown = client.get(f"/refs/{rid}", params={"home": "energy"})
    assert "slide.png" in shown.text
    man = Workspace.open(serve / "energy").read_manifest(rid)
    atts = [f for f in man.forms if f.role == "attachment"]
    assert len(atts) == 1
    assert (serve / "energy" / atts[0].location).is_file()
    gpath = serve / ".kairo" / "global-home" / "references" / rid
    assert not gpath.exists()


def test_s3_public_read_hides_attach_and_rejects_post(tmp_path):
    root, rid, slug = _full_public_root(tmp_path)
    pub = _public_client(root)
    page = pub.get(f"/refs/{rid}", params={"home": slug})
    assert page.status_code == 200
    assert 'id="attach-dlg"' not in page.text
    assert f'action="/refs/{rid}/attach"' not in page.text
    before = Workspace.open(root / slug).read_manifest(rid).forms
    posted = pub.post(
        f"/refs/{rid}/attach",
        data={"path": "/tmp/x.png", "home": slug},
        follow_redirects=False,
    )
    assert posted.status_code == 404
    after = Workspace.open(root / slug).read_manifest(rid).forms
    assert after == before


def test_unknown_ref_attach_404(tmp_path):
    serve, _rid, client = _topic_with_global_member(tmp_path)
    r = client.post(
        "/refs/missing/attach",
        data={"path": str(tmp_path / "x.png"), "home": "global"},
        follow_redirects=False,
    )
    assert r.status_code == 404


def test_bad_path_400_keeps_forms(tmp_path):
    serve, rid, client = _topic_with_global_member(tmp_path)
    before = global_home(serve).read_manifest(rid).forms
    r = client.post(
        f"/refs/{rid}/attach",
        data={"path": str(tmp_path / "no.png"), "home": "global"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert global_home(serve).read_manifest(rid).forms == before


def test_missing_path_and_files_400(tmp_path):
    serve, rid, client = _topic_with_global_member(tmp_path)
    r = client.post(
        f"/refs/{rid}/attach",
        data={"home": "global"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_upload_multiple_files(tmp_path):
    serve, rid, client = _topic_with_global_member(tmp_path)
    files = [
        ("files", ("a.png", io.BytesIO(b"\x89PNG\r\n1"), "image/png")),
        ("files", ("b.png", io.BytesIO(b"\x89PNG\r\n2"), "image/png")),
    ]
    r = client.post(
        f"/refs/{rid}/attach",
        data={"home": "global"},
        files=files,
        follow_redirects=False,
    )
    assert r.status_code == 303
    man = global_home(serve).read_manifest(rid)
    names = [Path(f.location).name for f in man.forms if f.role == "attachment"]
    assert names == ["a.png", "b.png"]


def test_same_id_different_home_writes_only_selected(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    ws = Workspace.init(serve / "energy", topic="energy")
    src = tmp_path / "shared.txt"
    src.write_text("shared")
    ws.add([src], ref_id="shared", copy=True)
    add_global_ref(serve, [src], ref_id="shared", copy=True)
    client = _client(serve)
    img = _png(tmp_path / "only-global.png")
    r = client.post(
        "/refs/shared/attach",
        data={"path": str(img), "home": "global"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    g_forms = [f.location for f in global_home(serve).read_manifest("shared").forms]
    t_forms = [f.location for f in Workspace.open(serve / "energy").read_manifest("shared").forms]
    assert any("only-global.png" in loc for loc in g_forms)
    assert not any("only-global.png" in loc for loc in t_forms)
