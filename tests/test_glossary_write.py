"""#69: glossary 追加/删除写回 constitution + Web UI。"""

from __future__ import annotations

import yaml

from kairo.workspace import Workspace
from kairo.web.server import create_app
from fastapi.testclient import TestClient


def test_add_glossary_entry_roundtrip(tmp_path):
    ws = Workspace.init(tmp_path)
    e = ws.add_glossary_entry("消福中心", note="管理约束方", aka=["消福体系"])
    assert e.name == "消福中心"
    con = ws.constitution
    assert len(con.glossary) == 1
    assert con.glossary[0].aka == ["消福体系"]
    raw = yaml.safe_load((tmp_path / "constitution.yaml").read_text())
    assert raw["glossary"][0]["name"] == "消福中心"
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
        assert "同名" in str(e)


def test_remove_glossary_entry(tmp_path):
    ws = Workspace.init(tmp_path)
    ws.add_glossary_entry("A")
    ws.add_glossary_entry("B")
    ws.remove_glossary_entry(0)
    assert [e.name for e in ws.constitution.glossary] == ["B"]
    try:
        ws.remove_glossary_entry(9)
        raise AssertionError("expected IndexError")
    except IndexError:
        pass


def _client(root):
    return TestClient(create_app(root))


def test_web_glossary_button_on_workspace(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).get("/w/ws")
    assert r.status_code == 200
    assert 'hx-get="/w/ws/glossary"' in r.text
    assert "真名册" in r.text or "Glossary" in r.text


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
    ws = Workspace.open(tmp_path / "ws")
    assert ws.constitution.glossary[0].aka == ["中山一", "中山医院联会"]

    r2 = c.post("/w/ws/glossary/0/delete")
    assert r2.status_code == 200
    assert Workspace.open(tmp_path / "ws").constitution.glossary == []


def test_web_glossary_empty_name_400(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).post("/w/ws/glossary", data={"name": "  "})
    assert r.status_code == 400
