"""#163: root/workspace 两级权威、生效 hash、歧义与校正提示。"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app
from kairo.glossary import (
    GlossaryError,
    current_effective_hash,
    effective_hash,
    effective_items,
    machine_migration_hint,
    merged_glossary_entries,
    resolve_serve_root,
)
from kairo.models import GlossaryEntry
from kairo.provider import StubProvider
from kairo.web.server import create_app
from kairo.workspace import Workspace

runner = CliRunner()


def test_effective_override_and_origins():
    root = [
        GlossaryEntry(name="天溯", note="公司"),
        GlossaryEntry(name="共享词", note="root"),
    ]
    local = [GlossaryEntry(name="天溯", note="本区")]
    items = effective_items(root, local)
    assert [(i.origin, i.entry.name, i.entry.note) for i in items] == [
        ("override", "天溯", "本区"),
        ("inherited", "共享词", "root"),
    ]


def test_alias_conflict_rejected():
    root = [GlossaryEntry(name="甲", aka=["共用"])]
    local = [GlossaryEntry(name="乙", aka=["共用"])]
    with pytest.raises(GlossaryError, match="多个规范名"):
        effective_items(root, local)


def test_alias_equals_name_rejected():
    with pytest.raises(GlossaryError, match="规范名冲突"):
        effective_items([], [GlossaryEntry(name="甲", aka=["乙"]), GlossaryEntry(name="乙")])


def test_effective_hash_ignores_tags_and_is_stable():
    a = [GlossaryEntry(name="天溯", note="公司", aka=["天溯公司"], tags=["org"])]
    b = [GlossaryEntry(name="天溯", note="公司", aka=["天溯公司"], tags=["other"])]
    assert effective_hash(a) == effective_hash(b)
    c = [GlossaryEntry(name="天溯", note="别的")]
    assert effective_hash(a) != effective_hash(c)


def test_machine_excluded_from_effective(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    from kairo.glossary import save_glossary_file

    save_glossary_file(root / "glossary.yaml", [GlossaryEntry(name="公共锚")])
    machine = tmp_path / "cfg" / "kairo" / "glossary.yaml"
    machine.parent.mkdir(parents=True)
    save_glossary_file(machine, [GlossaryEntry(name="本机词")])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    names = [e.name for e in merged_glossary_entries([], ws.root)]
    assert names == ["公共锚"]
    hint = machine_migration_hint()
    assert hint and "本机词" not in "".join(names)
    assert "不再进入正式产物" in hint


def test_serve_root_mismatch_rejected(tmp_path):
    ws = Workspace.init(tmp_path / "root" / "ws")
    with pytest.raises(GlossaryError, match="不一致"):
        resolve_serve_root(ws_root=ws.root, explicit=tmp_path / "other")


def test_restep_target_for_digest_key():
    from kairo.workspace import restep_target_for

    assert restep_target_for("references/abc/digest.md") == "abc"
    assert restep_target_for("understanding.md") == "understanding.md"


def test_web_root_add_updates_uncovered_workspace(tmp_path):
    root = tmp_path
    Workspace.init(root / "a", topic="a")
    ws_b = Workspace.init(root / "b", topic="b")
    ws_b.add_glossary_entry("天溯", note="本地")
    c = TestClient(create_app(root))
    dash = c.get("/")
    assert dash.status_code == 200
    assert "/glossary" in dash.text
    preview = c.get("/glossary")
    assert preview.status_code == 200
    assert "b" in preview.text
    preview_b = c.get("/glossary?workspace=b")
    assert "天溯" in preview_b.text
    r = c.post("/glossary", data={"name": "天溯", "note": "公共"})
    assert r.status_code == 200
    assert "天溯" in r.text
    assert "已保存" in r.text or "Saved" in r.text
    assert (root / "glossary.yaml").is_file()
    view_a = c.get("/glossary?workspace=a")
    panel_a = re.search(r'<section class="gl-ws-panel".*?</section>', view_a.text, re.S)
    assert panel_a
    assert "天溯" not in panel_a.group(0)
    shared = re.search(r'<section class="gl-shared".*?</section>', view_a.text, re.S)
    assert shared and "天溯" in shared.group(0)
    view_b = c.get("/glossary?workspace=b")
    panel_b = re.search(r'<section class="gl-ws-panel".*?</section>', view_b.text, re.S)
    assert panel_b
    assert "天溯" in panel_b.group(0)
    assert 'action="/w/b/glossary' in panel_b.group(0)


def test_web_workspace_cannot_write_shared(tmp_path):
    root = tmp_path
    Workspace.init(root / "ws", topic="t")
    c = TestClient(create_app(root))
    r = c.post("/w/ws/glossary", data={"name": "公共名", "scope": "shared"})
    assert r.status_code == 200
    assert "Root" in r.text or "本层" in r.text
    assert not (root / "glossary.yaml").exists()


def test_web_alias_conflict_not_saved(tmp_path):
    root = tmp_path
    Workspace.init(root / "ws", topic="t")
    c = TestClient(create_app(root))
    c.post("/glossary", data={"name": "甲", "aka": "共用"})
    r = c.post("/w/ws/glossary", data={"name": "乙", "aka": "共用", "scope": "workspace"})
    assert r.status_code == 200
    assert "多个规范名" in r.text
    assert Workspace.open(root / "ws").constitution.glossary == []


def test_cli_effective_hash_stable_with_machine(tmp_path, monkeypatch):
    root = tmp_path / "serve"
    root.mkdir()
    monkeypatch.chdir(root)
    runner.invoke(app, ["new", "ws", "--root", str(root)])
    monkeypatch.chdir(root / "ws")
    runner.invoke(app, ["glossary", "add", "公共锚", "--scope", "shared"])
    h1 = current_effective_hash(root / "ws")
    listed = runner.invoke(app, ["glossary", "list"])
    assert listed.exit_code == 0
    assert h1 in listed.output
    machine = tmp_path / "cfg" / "kairo" / "glossary.yaml"
    machine.parent.mkdir(parents=True)
    machine.write_text("- name: 本机词\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    h2 = current_effective_hash(root / "ws")
    assert h1 == h2
    listed2 = runner.invoke(app, ["glossary", "list"])
    assert "不再进入正式产物" in (listed2.output + (listed2.stderr or ""))


def test_glossary_change_marks_pending_without_autostep(tmp_path):
    ws = Workspace.init(tmp_path / "root" / "ws")
    src = tmp_path / "root" / "note.txt"
    src.write_text("hello")
    rid = ws.add([src])
    from kairo.engine import step

    step(ws, provider=StubProvider())
    key = f"references/{rid}/digest.md"
    assert ws.read_state().products[key].glossary_hash
    before = (ws.root / key).read_text()
    ws.add_glossary_entry("天溯", note="后加")
    pending = ws.glossary_pending()
    assert key in pending or "understanding.md" in pending
    assert (ws.root / key).read_text() == before
    # input_hash staleness must not flip solely from glossary
    from kairo.rules import DigestRule

    items = DigestRule(ws, StubProvider()).discover()
    assert items == [] or not items[0].is_stale(ws.read_state())
    page = TestClient(create_app(tmp_path / "root")).get("/glossary?workspace=ws")
    assert page.status_code == 200
    assert f'name="target" value="{rid}"' in page.text
    assert f'value="{key}"' not in page.text
    ws_page = TestClient(create_app(tmp_path / "root")).get("/w/ws")
    assert 'hx-post="/w/ws/step"' not in ws_page.text or 'name="target"' not in ws_page.text
    assert f'name="target" value="{rid}"' not in ws_page.text
