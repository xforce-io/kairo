"""#442: 图片 form 预览可放大、缩小、移到被裁区域并适应预览区。"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from kairo.refs import add_global_ref
from kairo.web.image_preview import (
    PREVIEW_SCRIPT,
    ZOOM_RATIO,
    fit,
    image_point_visible,
    open_frame,
    pan_to_hidden,
    render_image_preview,
    zoom_in,
    zoom_out,
)
from kairo.web.server import create_app
from kairo.workspace import Workspace


def _png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)


def _assert_preview_chrome(html: str, file_url: str) -> None:
    assert 'class="doc-img"' in html
    assert file_url in html
    assert "data-img-zoom-in" in html
    assert "data-img-zoom-out" in html
    assert "data-img-fit" in html
    assert f'data-zoom-ratio="{format(ZOOM_RATIO, "g")}"' in html
    assert html.index("img-preview-viewport") < html.index("img-preview-bar") < html.index('class="doc-img"')
    assert "kairoScanImagePreview" in html
    assert PREVIEW_SCRIPT in html
    assert 'class="listen-read"' not in html
    assert 'class="lr-play"' not in html


def _preview_script(client: TestClient, html: str) -> str:
    """整页实际引用的预览脚本。缩放绑定只存在于这份脚本。"""
    match = re.search(r'src="(/static/image_preview\.js[^"]*)"', html)
    assert match, "topic or ref page did not load image_preview.js"
    script = client.get(match.group(1))
    assert script.status_code == 200
    start = script.text.split("function start()", 1)[-1]
    assert "scan(document)" in start
    assert 'addEventListener("htmx:oobAfterSwap", rescan)' in start
    assert "event.detail.target" not in start
    return script.text


def test_topic_shell_selects_image_into_reader(tmp_path):
    """S1 真入口：打开 Topic 整页再点参考。片段没有脚本，阅读区靠 OOB 换入。"""
    root = tmp_path / "root"
    ws = Workspace.init(root / "ws", topic="t")
    img = tmp_path / "board.png"
    _png(img)
    rid = ws.add([img], copy=True, title="Board")
    client = TestClient(create_app(root))

    shell = client.get("/w/ws")
    assert shell.status_code == 200
    assert "data-img-zoom-in" not in shell.text
    _preview_script(client, shell.text)
    row = re.search(
        rf'href="(/w/ws\?ref={re.escape(rid)}[^"]*)" hx-get="([^"]+)" hx-target="#meta"',
        shell.text,
    )
    assert row, "topic page did not offer the image reference"
    landed_url, fragment_url = row.group(1), row.group(2)

    landed = client.get(landed_url)
    assert landed.status_code == 200
    assert "data-img-zoom-in" not in landed.text
    _preview_script(client, landed.text)
    assert f'hx-get="{fragment_url}"' in landed.text
    assert 'hx-trigger="load"' in landed.text

    fragment = client.get(fragment_url)
    assert fragment.status_code == 200
    assert '<main id="reader" class="pane-read" hx-swap-oob="true">' in fragment.text
    _assert_preview_chrome(fragment.text, f"/w/ws/ref/{rid}/file/0")


def test_global_ref_page_selects_image_form(tmp_path):
    """S3 真入口：全局 Ref 整页带脚本；点 form 才把预览换进一开始隐藏的抽屉。"""
    serve = tmp_path / "root"
    serve.mkdir()
    img = tmp_path / "board.png"
    _png(img)
    rid = add_global_ref(serve, [img], ref_id="img-ref", title="Image Reference", copy=True)
    client = TestClient(create_app(serve))
    page = client.get(f"/refs/{rid}")
    assert page.status_code == 200
    assert "data-img-zoom-in" not in page.text
    assert 'id="form-drawer" class="form-drawer" hidden' in page.text
    script = _preview_script(client, page.text)
    button = re.search(
        rf'hx-get="(/refs/{re.escape(rid)}/form/[^"]+)"\s+hx-target="#form-preview"',
        page.text,
    )
    assert button, "global ref page did not offer the image form"
    preview = client.get(button.group(1))
    assert preview.status_code == 200
    _assert_preview_chrome(preview.text, f"/refs/{rid}/file/")
    assert "ResizeObserver" in script
    assert "clientWidth < 2" in script


def test_topic_image_form_preview_exposes_zoom(tmp_path):
    """S1：Topic 阅读区与 form 入口都给出图片和缩放，且不是听读。"""
    root = tmp_path / "root"
    ws = Workspace.init(root / "ws", topic="t")
    img = tmp_path / "board.png"
    _png(img)
    rid = ws.add([img], copy=True, title="Board")
    client = TestClient(create_app(root))
    page = client.get(f"/w/ws/ref/{rid}")
    form = client.get(f"/w/ws/ref/{rid}/form/0")
    assert page.status_code == 200
    assert form.status_code == 200
    file_url = f"/w/ws/ref/{rid}/file/0"
    _assert_preview_chrome(page.text, file_url)
    _assert_preview_chrome(form.text, file_url)
    assert 'id="reader"' in page.text


def test_global_ref_image_form_preview_exposes_zoom(tmp_path):
    """S3：全局 Ref 的 form 预览走同一图片预览，不是听读。"""
    serve = tmp_path / "root"
    serve.mkdir()
    img = tmp_path / "board.png"
    _png(img)
    rid = add_global_ref(serve, [img], ref_id="img-ref", title="Image Reference", copy=True)
    client = TestClient(create_app(serve))
    page = client.get(f"/refs/{rid}")
    assert page.status_code == 200
    assert f"/refs/{rid}/form/0" in page.text
    preview = client.get(f"/refs/{rid}/form/0")
    assert preview.status_code == 200
    _assert_preview_chrome(preview.text, f"/refs/{rid}/file/0")


def test_text_form_preview_is_not_image_viewer(tmp_path):
    root = tmp_path / "root"
    ws = Workspace.init(root / "ws", topic="t")
    note = tmp_path / "note.md"
    note.write_text("# 标题\n\n正文\n", encoding="utf-8")
    rid = ws.add([note], copy=True, title="Note")
    form = TestClient(create_app(root)).get(f"/w/ws/ref/{rid}/form/0")
    assert form.status_code == 200
    assert "data-img-zoom-in" not in form.text
    assert 'class="listen-read"' not in form.text
    assert "正文" in form.text


def test_scale_pan_and_fit_on_shipped_frames():
    """S1/S2：直接调用预览尺寸函数，不在测试里重算公式。"""
    opened = open_frame(800, 1600, 400, 300)
    assert opened.display_width <= opened.viewport_width
    far_x, far_y = opened.image_width - 1, opened.image_height - 1
    assert image_point_visible(opened, 1, 1)
    assert not image_point_visible(opened, far_x, far_y)

    zoomed = zoom_in(opened)
    assert zoomed.display_width >= opened.display_width * 1.5
    assert not image_point_visible(zoomed, far_x, far_y)

    shrunk = zoom_out(zoomed)
    assert shrunk.display_width <= opened.display_width

    panned = pan_to_hidden(zoomed)
    assert image_point_visible(panned, far_x, far_y)
    assert not image_point_visible(zoomed, far_x, far_y)

    fitted = fit(panned)
    assert fitted.display_width <= fitted.viewport_width
    assert fitted.display_width <= opened.display_width
    assert fitted.offset_x == 0
    assert fitted.offset_y == 0


def test_renderer_uses_module_ratio():
    html = render_image_preview(
        "/file/0",
        "board.png",
        {"toolbar": "Image preview", "zoom_in": "Zoom in", "zoom_out": "Zoom out", "fit": "Fit"},
    )
    assert f'data-zoom-ratio="{format(ZOOM_RATIO, "g")}"' in html
    assert "Zoom in" in html
    assert html.index("img-preview-viewport") < html.index("data-img-zoom-in")
    shell = Path("src/kairo/web/templates/base.html").read_text(encoding="utf-8")
    assert f'src="{PREVIEW_SCRIPT}"' in shell
    js = Path("src/kairo/web/static/image_preview.js").read_text(encoding="utf-8")
    assert "data-img-zoom-in" in js
    assert "dataset.zoomRatio" in js
    assert "dataset.maxZoom" in js
    assert "window.kairoScanImagePreview" in js
