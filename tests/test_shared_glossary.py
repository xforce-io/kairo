"""#71: machine + root 共享真名册与 workspace 合并覆盖。"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from kairo.glossary import (
    format_glossary_reference,
    load_glossary_file,
    merge_glossary,
    merged_glossary_entries,
    save_glossary_file,
)
from kairo.models import GlossaryEntry
from kairo.provider import StubProvider
from kairo.rules import DigestRule, State
from kairo.web.server import create_app
from kairo.workspace import Workspace


def test_merge_glossary_later_wins():
    a = [GlossaryEntry(name="天溯", note="旧"), GlossaryEntry(name="甲")]
    b = [GlossaryEntry(name="天溯", note="新")]
    m = merge_glossary(a, b)
    assert [e.name for e in m] == ["天溯", "甲"]
    assert m[0].note == "新"


def test_load_save_glossary_file(tmp_path):
    p = tmp_path / "glossary.yaml"
    save_glossary_file(
        p, [GlossaryEntry(name="消福", note="组织", aka=["消福中心"], tags=["org"])]
    )
    loaded = load_glossary_file(p)
    assert loaded[0].name == "消福"
    assert loaded[0].tags == ["org"]
    # wrapped form
    (tmp_path / "g2.yaml").write_text(
        yaml.safe_dump({"entries": [{"name": "X"}]}, allow_unicode=True)
    )
    assert load_glossary_file(tmp_path / "g2.yaml")[0].name == "X"


def test_workspace_glossary_reference_merges_parent_and_machine(tmp_path, monkeypatch):
    root = tmp_path / "kairo-root"
    ws_dir = root / "能源业务"
    root.mkdir()
    ws = Workspace.init(ws_dir, topic="能源业务")
    save_glossary_file(
        root / "glossary.yaml",
        [GlossaryEntry(name="天溯", note="公司"), GlossaryEntry(name="共享词", note="root")],
    )
    machine = tmp_path / "cfg" / "kairo" / "glossary.yaml"
    machine.parent.mkdir(parents=True)
    save_glossary_file(machine, [GlossaryEntry(name="本机词", note="machine")])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    ws.add_glossary_entry("天溯", note="本区覆盖")  # 覆盖 root 同名
    ref = ws.glossary_reference()
    assert "本机词" in ref
    assert "共享词" in ref
    assert "本区覆盖" in ref  # workspace wins
    assert ref.index("本机词") < ref.index("共享词") or "本机词" in ref


def test_digest_persona_gets_merged_glossary(tmp_path, monkeypatch):
    """注入路径走 ws.glossary_reference(含 parent root)。"""
    root = tmp_path / "root"
    ws_dir = root / "ws"
    root.mkdir()
    ws = Workspace.init(ws_dir)
    save_glossary_file(root / "glossary.yaml", [GlossaryEntry(name="公共锚")])
    t = tmp_path / "m.txt"  # outside
    # put transcript inside ref via add of file in tmp outside
    src = root / "note.txt"
    src.write_text("讨论公共锚")
    rid = ws.add([src])
    # capture persona via custom provider
    calls = []

    class P:
        name = "cap"
        model = "cap"

        def run(self, config, signal=None):
            calls.append(config.persona)
            config.artifact_dir.mkdir(parents=True, exist_ok=True)
            (config.artifact_dir / (config.artifact or "digest.md")).write_text("D")
            from kairo.provider import AgentResult, _scan_artifacts

            return AgentResult(artifacts=_scan_artifacts(config.artifact_dir))

    DigestRule(ws, P()).discover()[0].run(State())
    assert calls and "公共锚" in calls[0]


def test_web_shared_and_local_glossary(tmp_path):
    root = tmp_path
    ws = Workspace.init(root / "ws", topic="t")
    c = TestClient(create_app(root))
    r = c.post(
        "/w/ws/glossary",
        data={"name": "公共名", "note": "shared", "scope": "shared", "tags": "org"},
    )
    assert r.status_code == 200
    assert "公共名" in r.text
    assert (root / "glossary.yaml").is_file()
    raw = yaml.safe_load((root / "glossary.yaml").read_text())
    assert raw[0]["tags"] == ["org"]

    r2 = c.post(
        "/w/ws/glossary",
        data={"name": "本区名", "scope": "workspace"},
    )
    assert r2.status_code == 200
    assert "本区名" in r2.text
    assert any(e.name == "本区名" for e in Workspace.open(root / "ws").constitution.glossary)

    # delete shared
    r3 = c.post("/w/ws/glossary/0/delete", data={"scope": "shared"})
    assert r3.status_code == 200
    assert load_glossary_file(root / "glossary.yaml") == []
