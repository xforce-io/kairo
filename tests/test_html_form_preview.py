"""Ref HTML forms share an isolated preview across both Console surfaces."""

from shutil import copy2

from fastapi.testclient import TestClient

from kairo.refs import add_global_ref, add_tag, create_tag, set_include_tags
from kairo.web.public import set_reference_public
from kairo.web.server import create_app
from kairo.workspace import Workspace


def test_copied_html_has_isolated_preview_and_stays_console_only(tmp_path):
    ws = Workspace.init(tmp_path / "topic", topic="Topic")
    create_tag(tmp_path, "reading")
    set_include_tags(tmp_path, "topic", ["reading"])
    source = tmp_path / "board.html"
    source.write_text("<!doctype html><h1>Interactive board</h1>", encoding="utf-8")
    text = tmp_path / "summary.md"
    text.write_text("# Ordinary Markdown", encoding="utf-8")
    image = tmp_path / "picture.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    rid = add_global_ref(tmp_path, [text, source, image], copy=True, ref_id="board")
    add_tag(tmp_path, home="", ref_id=rid, tag="reading")
    suffix = "?home=global"
    topic_ref = f"/w/topic/ref/{rid}"
    global_ref = f"/refs/{rid}"
    client = TestClient(create_app(tmp_path))

    for base in (topic_ref, global_ref):
        page = client.get(base + suffix)
        assert page.status_code == 200
        query = "?home=global" if base == topic_ref else "?home="
        assert f'hx-get="{base}/form/1{query}"' in page.text
        preview = client.get(f"{base}/form/1{suffix}")
        assert preview.status_code == 200
        assert f'src="{base}/file/1{suffix}"' in preview.text
        assert 'sandbox="allow-scripts"' in preview.text
        assert "Interactive board" not in preview.text
        raw = client.get(f"{base}/file/1{suffix}")
        assert raw.status_code == 200
        assert raw.content == source.read_bytes()
        assert raw.headers["content-type"].startswith("text/html")
        policy = raw.headers["content-security-policy"]
        assert {part.strip() for part in policy.split(";")} >= {
            "sandbox allow-scripts", "default-src 'none'", "frame-ancestors 'self'",
            "form-action 'none'", "base-uri 'none'",
        }
        assert raw.headers["x-content-type-options"] == "nosniff"
        assert "<h1>Ordinary Markdown</h1>" in client.get(f"{base}/form/0{suffix}").text
        assert 'class="doc-img"' in client.get(f"{base}/form/2{suffix}").text

    # A published Topic-home Ref exercises the ordinary read-only routes.
    ref_dir = ws.references_dir() / "local-board"
    ref_dir.mkdir(parents=True)
    forms = [copy2(path, ref_dir) for path in (text, source, image)]
    local = ws.add(forms, ref_id="local-board")
    add_tag(tmp_path, home="topic", ref_id=local, tag="reading")
    set_reference_public(tmp_path, ws, local, public=True)
    public = TestClient(create_app(tmp_path, mode="public-read"))
    for base, query in ((f"/w/topic/ref/{local}", ""), (f"/refs/{local}", "?home=topic")):
        page = public.get(base + query)
        assert page.status_code == 200
        assert f'hx-get="{base}/form/1' not in page.text
        assert public.get(f"{base}/form/1{query}").status_code == 404
        assert public.get(f"{base}/file/1{query}").status_code == 404
        assert public.get(f"{base}/form/0{query}").status_code == 200


def test_html_file_outside_ref_home_has_no_preview(tmp_path):
    ws = Workspace.init(tmp_path / "topic", topic="Topic")
    source = tmp_path / "linked.htm"
    source.write_text("<h1>External file</h1>", encoding="utf-8")
    rid = ws.add([source], copy=False)
    client = TestClient(create_app(tmp_path))
    base = f"/refs/{rid}"
    query = "?home=topic"
    page = client.get(base + query)
    assert page.status_code == 200
    assert f'hx-get="{base}/form/0' not in page.text
    assert client.get(f"{base}/form/0{query}").status_code == 404
    assert client.get(f"{base}/file/0{query}").status_code == 404


def test_html_only_ref_defaults_to_isolated_reader(tmp_path):
    ws = Workspace.init(tmp_path / "topic", topic="Topic")
    source = tmp_path / "standalone.HTM"
    source.write_text("<h1>Standalone board</h1>", encoding="utf-8")
    rid = ws.add([source], copy=True)
    client = TestClient(create_app(tmp_path))
    fragment = client.get(f"/w/topic/ref/{rid}")
    assert fragment.status_code == 200
    assert 'class="doc-html-preview"' in fragment.text
    assert f'src="/w/topic/ref/{rid}/file/0"' in fragment.text
