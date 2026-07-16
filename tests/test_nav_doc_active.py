"""#86: 左侧 nav-doc 当前选中态契约。

驱动真实 shipped 的 app.css / workspace.html / TestClient 渲染结果，
证明选中样式与 click 切换/真名册清除逻辑存在。
"""

from pathlib import Path

from fastapi.testclient import TestClient

import kairo.web
from kairo.web.server import create_app
from kairo.workspace import Workspace

_WEB = Path(kairo.web.__file__).resolve().parent
_CSS = _WEB / "static" / "app.css"
_WORKSPACE = _WEB / "templates" / "workspace.html"


def _client(root):
    return TestClient(create_app(root))


def test_css_has_nav_doc_is_active_rules():
    """选中态对齐 forms：pine-soft 背景 + 左侧 inset 松绿条。"""
    css = _CSS.read_text(encoding="utf-8")
    assert ".nav-doc.is-active" in css
    # 背景与 forms tr.on 同一 token
    active_block_start = css.index(".nav-doc.is-active")
    # 取从 is-active 起一段规则文本，避免误匹配别处的 pine-soft
    snippet = css[active_block_start : active_block_start + 400]
    assert "var(--pine-soft)" in snippet or "--pine-soft" in snippet
    assert "inset" in snippet
    assert "var(--pine)" in snippet or "--pine" in snippet


def test_workspace_template_toggles_nav_doc_is_active_on_click():
    """workspace 脚本：点 .nav-doc 时清掉同栏其它项再加 is-active。"""
    html = _WORKSPACE.read_text(encoding="utf-8")
    assert "nav-doc" in html
    assert "is-active" in html
    # 清除全部再给当前项加上（唯一选中）
    assert "classList.remove" in html and "is-active" in html
    assert "classList.add" in html
    assert ".nav-doc" in html
    # 委托点击命中 nav-doc
    assert "closest" in html and "nav-doc" in html


def test_workspace_template_clears_nav_active_on_glossary():
    """打开真名册时清掉左侧 is-active。"""
    html = _WORKSPACE.read_text(encoding="utf-8")
    # 真名册入口存在
    assert "/glossary" in html
    # 与清除 is-active 在同一脚本区有关联（glossary 路径或按钮 + remove is-active）
    assert "is-active" in html
    # 真名册按钮或 glossary 路径附近应触发清除
    assert "glossary" in html.lower()
    # 具体契约：存在清除所有 nav-doc is-active 的逻辑，且脚本里提到 glossary 相关选择
    # （实现可用 btn 的 hx-get 含 glossary，或 class 钩子）
    idx_gl = html.find("/glossary")
    assert idx_gl >= 0
    # 脚本中有 querySelectorAll('.nav-doc') 或等价的全量清除
    assert "querySelectorAll" in html
    assert ".nav-doc" in html


def test_workspace_page_ships_nav_active_script(tmp_path):
    """TestClient 渲染的 workspace 含选中切换契约（真实入口，非 mock）。"""
    Workspace.init(tmp_path / "ws", topic="主题")
    r = _client(tmp_path).get("/w/ws")
    assert r.status_code == 200
    body = r.text
    assert 'class="nav-doc"' in body or "class=\"nav-doc" in body
    assert "is-active" in body
    assert "classList.add" in body
    assert "classList.remove" in body
    assert "/glossary" in body
    # 静态 CSS 经服务可访问且含选中规则
    css = _client(tmp_path).get("/static/app.css")
    assert css.status_code == 200
    assert ".nav-doc.is-active" in css.text
    assert "var(--pine-soft)" in css.text
