"""#69: glossary 追加/删除写回 constitution + Web UI。"""

from __future__ import annotations

import re

import yaml

from kairo.workspace import Workspace
from kairo.web.server import create_app
from fastapi.testclient import TestClient


def test_add_glossary_entry_roundtrip(tmp_path):
    ws = Workspace.init(tmp_path)
    e = ws.add_glossary_entry("消福中心", note="管理约束方", aka=["消福体系"])
    assert e.name == "消福中心"
    from kairo.knowledge import load_workspace
    entries = load_workspace(ws.root)[0].entries
    assert len(entries) == 1
    assert [alias.value for alias in entries[0].aliases] == ["消福体系"]
    raw = yaml.safe_load((tmp_path / "constitution.yaml").read_text())
    assert raw["knowledge"]["entries"][0]["title"] == "消福中心"
    # 其它 constitution 字段仍在
    assert raw["topic"] == "main"
    assert "targets" in raw


def test_add_glossary_rejects_empty_and_duplicate(tmp_path):
    ws = Workspace.init(tmp_path)
    try:
        ws.add_glossary_entry("  ")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    ws.add_glossary_entry("蒋总")
    try:
        ws.add_glossary_entry("蒋总")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "重复" in str(e)


def test_remove_glossary_entry(tmp_path):
    ws = Workspace.init(tmp_path)
    ws.add_glossary_entry("A")
    ws.add_glossary_entry("B")
    ws.remove_glossary_entry(0)
    from kairo.knowledge import load_workspace
    assert [e.title for e in load_workspace(ws.root)[0].entries] == ["B"]
    try:
        ws.remove_glossary_entry(9)
        raise AssertionError("expected IndexError")
    except IndexError:
        pass


def _client(root):
    return TestClient(create_app(root))


def _console_nav(html: str) -> str:
    m = re.search(r'<nav class="console-nav"[^>]*>.*?</nav>', html, re.S)
    assert m, "missing console-nav"
    return m.group(0)


def _header(html: str) -> str:
    m = re.search(r"<header class=\"top\">.*?</header>", html, re.S)
    assert m, "missing header"
    return m.group(0)


def _ws_panel(html: str) -> str | None:
    m = re.search(r'<section class="gl-ws-panel".*?</section>', html, re.S)
    return m.group(0) if m else None


def test_web_knowledge_button_on_workspace(tmp_path):
    """#182: 课题页无维护按钮；顶栏弱链去 /knowledge，不进主导航。"""
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).get("/w/ws")
    assert r.status_code == 200
    assert 'hx-get="/w/ws/glossary"' not in r.text
    assert 'class="gl-todo-hint"' not in r.text
    nav = _console_nav(r.text)
    assert 'href="/knowledge"' not in nav
    header = _header(r.text)
    assert re.search(r'href="/knowledge"', header)
    assert "Knowledge" in header or "知识" in header


def test_workspace_todo_hint_is_one_line(tmp_path):
    """#174 S2: 有待办时恰好一行，href 含 ?workspace=。"""
    from kairo.glossary_review import ingest_candidates

    root = tmp_path
    ws = Workspace.init(root / "ws", topic="t")
    src = root / "n.txt"
    src.write_text("讨论天溯系统")
    rid = ws.add([src])
    digest = ws.root / "references" / rid / "digest.md"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text("天溯系统\n")
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    html = _client(root).get("/w/ws").text
    hints = re.findall(
        r'<a class="gl-todo-hint"[^>]*href="([^"]+)"', html
    )
    assert len(hints) == 1
    assert hints[0] == "/knowledge?workspace=ws"
    assert 'hx-get="/w/ws/glossary"' not in html
    assert 'name="scope" value="workspace"' not in html


def test_glossary_page_hides_local_until_workspace_selected(tmp_path):
    """#174 S1: 未选工作区无本地表；?workspace= 只展开该区。"""
    Workspace.init(tmp_path / "a", topic="a")
    wb = Workspace.init(tmp_path / "b", topic="b")
    wb.add_glossary_entry("本区乙", note="仅 b")
    c = _client(tmp_path)
    bare = c.get("/glossary")
    assert bare.status_code == 200
    assert 'action="/knowledge/global"' in bare.text
    assert _ws_panel(bare.text) is None
    assert "本区乙" not in bare.text
    assert 'href="/knowledge?workspace=a"' in bare.text
    assert 'href="/knowledge?workspace=b"' in bare.text
    selected = c.get("/glossary?workspace=b")
    panel = _ws_panel(selected.text)
    assert panel is not None
    assert "本区乙" in panel
    assert 'action="/w/b/knowledge"' in panel
    a_page = c.get("/glossary?workspace=a")
    a_panel = _ws_panel(a_page.text)
    assert a_panel is not None
    assert "本区乙" not in a_panel
    assert 'action="/w/a/knowledge"' in a_panel


def test_glossary_invalid_workspace_query_not_selected(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    html = _client(tmp_path).get("/glossary?workspace=nope").text
    assert _ws_panel(html) is None


def test_workspace_write_redirects_to_console_get(tmp_path):
    """写成功 303 到 GET /glossary?workspace=，刷新不会重放 POST。"""
    Workspace.init(tmp_path / "ws", topic="t")
    c = _client(tmp_path)
    r = c.post(
        "/w/ws/glossary",
        data={"name": "甲", "scope": "workspace"},
        follow_redirects=False,
    )
    # 兼容 POST 返回统一知识页，旧路由不再形成第二个维护面。
    assert r.status_code == 200
    assert "甲" in r.text
    panel = _ws_panel(r.text)
    assert panel and "甲" in panel


def test_web_glossary_add_and_delete(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    c = _client(tmp_path)
    r = c.post(
        "/w/ws/glossary",
        data={"name": "中山医院", "note": "托管项目", "aka": "中山一, 中山医院联会"},
    )
    assert r.status_code == 200
    assert "中山医院" in r.text
    assert "中山一" in r.text
    from kairo.knowledge import load_workspace

    assert [a.value for a in load_workspace(tmp_path / "ws")[0].entries[0].aliases] == ["中山一", "中山医院联会"]

    r2 = c.post("/w/ws/glossary/0/delete")
    assert r2.status_code == 200
    assert Workspace.open(tmp_path / "ws").constitution.glossary == []


def test_web_glossary_empty_name_stays_inline(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).post("/w/ws/glossary", data={"name": "  "})
    assert r.status_code == 200
    assert "name 不能为空" in r.text
    assert Workspace.open(tmp_path / "ws").constitution.glossary == []
