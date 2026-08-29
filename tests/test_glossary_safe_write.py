"""#162: 损坏配置与非法 scope 不得误写真名册。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app
from kairo.glossary import (
    GlossaryError,
    load_glossary_file,
    parse_scope,
    save_glossary_file,
)
from kairo.models import GlossaryEntry
from kairo.web.server import create_app
from kairo.workspace import Workspace

runner = CliRunner()


def _bytes(path: Path) -> bytes:
    return path.read_bytes()


def test_missing_file_is_empty(tmp_path):
    assert load_glossary_file(tmp_path / "glossary.yaml") == []


def test_empty_and_null_are_empty(tmp_path):
    p = tmp_path / "glossary.yaml"
    p.write_text("")
    assert load_glossary_file(p) == []
    p.write_text("null\n")
    assert load_glossary_file(p) == []


def test_illegal_toplevel_raises(tmp_path):
    p = tmp_path / "glossary.yaml"
    p.write_text("not-a-list: true\n")
    with pytest.raises(GlossaryError) as ei:
        load_glossary_file(p)
    assert str(p) in str(ei.value)


def test_empty_mapping_is_illegal(tmp_path):
    p = tmp_path / "glossary.yaml"
    p.write_text("{}\n")
    with pytest.raises(GlossaryError):
        load_glossary_file(p)


def test_bad_entry_rejects_whole_file(tmp_path):
    p = tmp_path / "glossary.yaml"
    p.write_text(
        yaml.safe_dump(
            [{"name": "天溯", "note": "ok"}, "bad-item"],
            allow_unicode=True,
        )
    )
    with pytest.raises(GlossaryError):
        load_glossary_file(p)


def test_entry_missing_name_rejects(tmp_path):
    p = tmp_path / "glossary.yaml"
    p.write_text(yaml.safe_dump([{"note": "无规范名"}], allow_unicode=True))
    with pytest.raises(GlossaryError):
        load_glossary_file(p)


def test_wrapped_entries_still_ok(tmp_path):
    p = tmp_path / "glossary.yaml"
    p.write_text(yaml.safe_dump({"entries": [{"name": "X"}]}, allow_unicode=True))
    assert load_glossary_file(p)[0].name == "X"


def test_parse_scope_default_and_reject():
    assert parse_scope(None) == "workspace"
    assert parse_scope("shared") == "shared"
    assert parse_scope("workspace") == "workspace"
    with pytest.raises(GlossaryError):
        parse_scope("typo")
    with pytest.raises(GlossaryError):
        parse_scope("")


def test_add_does_not_rewrite_corrupt_shared(tmp_path, monkeypatch):
    root = tmp_path / "serve"
    root.mkdir()
    damaged = root / "glossary.yaml"
    original = b"this: is: broken: yaml: [\n"
    damaged.write_bytes(original)
    monkeypatch.chdir(root)
    Workspace.init(root / "ws", topic="t")
    monkeypatch.chdir(root / "ws")
    result = runner.invoke(app, ["glossary", "add", "新词", "--scope", "shared"])
    assert result.exit_code != 0
    assert damaged.read_bytes() == original
    listed = runner.invoke(app, ["glossary", "list"])
    assert listed.exit_code != 0
    assert damaged.read_bytes() == original


def test_corrupt_then_fixed_add_succeeds(tmp_path, monkeypatch):
    root = tmp_path / "serve"
    root.mkdir()
    path = root / "glossary.yaml"
    path.write_text("oops\n")
    monkeypatch.chdir(root)
    Workspace.init(root / "ws", topic="t")
    monkeypatch.chdir(root / "ws")
    failed = runner.invoke(app, ["glossary", "add", "天溯", "--scope", "shared"])
    assert failed.exit_code != 0
    assert path.read_text() == "oops\n"
    path.write_text("- name: 既有\n")
    ok = runner.invoke(app, ["glossary", "add", "天溯", "--scope", "shared"])
    assert ok.exit_code == 0
    names = [e.name for e in load_glossary_file(path)]
    assert names == ["既有", "天溯"]


def test_save_replace_failure_keeps_original(tmp_path, monkeypatch):
    path = tmp_path / "glossary.yaml"
    path.write_text("- name: 旧\n")
    before = path.read_bytes()

    def boom(src, dst, *args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("kairo.glossary.os.replace", boom)
    with pytest.raises(GlossaryError, match="保存失败"):
        save_glossary_file(path, [GlossaryEntry(name="新")])
    assert path.read_bytes() == before


def test_workspace_add_preserves_unknown_constitution_fields(tmp_path):
    ws = Workspace.init(tmp_path, topic="主课题")
    con = tmp_path / "constitution.yaml"
    data = yaml.safe_load(con.read_text())
    data["custom_flag"] = True
    data["extra_note"] = "keep-me"
    con.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    ws.add_glossary_entry("消福中心", note="管理约束方")
    raw = yaml.safe_load(con.read_text())
    assert raw["custom_flag"] is True
    assert raw["extra_note"] == "keep-me"
    assert raw["topic"] == "主课题"
    assert raw["knowledge"]["entries"][0]["title"] == "消福中心"
    ws.remove_glossary_entry(0)
    raw2 = yaml.safe_load(con.read_text())
    assert raw2["custom_flag"] is True
    assert raw2["extra_note"] == "keep-me"
    assert raw2["knowledge"]["entries"] == []


def test_workspace_save_failure_keeps_constitution(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path, topic="t")
    con = tmp_path / "constitution.yaml"
    before = con.read_bytes()

    def boom(src, dst, *args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("kairo.knowledge.os.replace", boom)
    with pytest.raises(ValueError, match="保存失败"):
        ws.add_glossary_entry("天溯")
    assert con.read_bytes() == before
    assert yaml.safe_load(con.read_text())["topic"] == "t"


def test_empty_constitution_not_rewritten(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    con = tmp_path / "ws" / "constitution.yaml"
    con.write_bytes(b"")
    with pytest.raises(ValueError, match="constitution"):
        ws.add_glossary_entry("新词")
    assert con.read_bytes() == b""
    con.write_text("null\n")
    before = con.read_bytes()
    with pytest.raises(ValueError, match="constitution"):
        ws.remove_glossary_entry(0)
    assert con.read_bytes() == before
    monkeypatch.chdir(tmp_path / "ws")
    listed = runner.invoke(app, ["glossary", "list"])
    assert listed.exit_code != 0
    assert con.read_bytes() == before


def test_cli_save_failure_is_locatable(tmp_path, monkeypatch):
    root = tmp_path / "serve"
    root.mkdir()
    Workspace.init(root / "ws", topic="t")
    monkeypatch.chdir(root / "ws")
    monkeypatch.setattr(
        "kairo.glossary.os.replace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("simulated replace failure")),
    )
    result = runner.invoke(app, ["glossary", "add", "天溯", "--scope", "shared"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    err = result.output + (result.stderr or "")
    assert "保存失败" in err
    assert not (root / "glossary.yaml").exists()


def test_web_save_failure_inline_error(tmp_path, monkeypatch):
    root = tmp_path
    Workspace.init(root / "ws", topic="t")
    monkeypatch.setattr(
        "kairo.glossary.os.replace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("simulated replace failure")),
    )
    r = _client(root).post(
        "/glossary",
        data={"name": "天溯", "note": "keep"},
    )
    assert r.status_code == 200
    assert "保存失败" in r.text
    assert "天溯" in r.text
    assert "keep" in r.text
    assert not (root / "glossary.yaml").exists()


def _client(root):
    return TestClient(create_app(root))


def test_web_unknown_scope_inline_error_no_write(tmp_path):
    root = tmp_path
    Workspace.init(root / "ws", topic="t")
    shared = root / "glossary.yaml"
    shared.write_text("- name: 公共\n")
    shared_before = shared.read_bytes()
    con = root / "ws" / "constitution.yaml"
    con_before = con.read_bytes()
    r = _client(root).post(
        "/w/ws/glossary",
        data={"name": "误写", "note": "should-stay", "scope": "typo"},
    )
    assert r.status_code == 200
    assert "误写" in r.text
    assert "should-stay" in r.text
    assert "typo" in r.text or "scope" in r.text.lower() or "未知" in r.text
    assert shared.read_bytes() == shared_before
    assert con.read_bytes() == con_before


def test_web_legal_scope_changes_only_one_layer(tmp_path):
    root = tmp_path
    Workspace.init(root / "ws", topic="t")
    c = _client(root)
    r = c.post("/glossary", data={"name": "公共名"})
    assert r.status_code == 200
    assert load_glossary_file(root / "glossary.yaml")[0].name == "公共名"
    assert Workspace.open(root / "ws").constitution.glossary == []

    r2 = c.post(
        "/w/ws/glossary",
        data={"name": "本区名", "scope": "workspace"},
    )
    assert r2.status_code == 200
    assert [e.name for e in load_glossary_file(root / "glossary.yaml")] == ["公共名"]
    from kairo.knowledge import load_workspace

    assert load_workspace(root / "ws")[0].entries[0].title == "本区名"


def test_web_get_corrupt_shared_shows_error_not_empty_success(tmp_path):
    root = tmp_path
    Workspace.init(root / "ws", topic="t")
    (root / "glossary.yaml").write_text("broken: [\n")
    r = _client(root).get("/w/ws/glossary")
    assert r.status_code == 200
    assert "glossary.yaml" in r.text
    assert "公共册暂无条目" not in r.text
    assert "No shared entries yet" not in r.text


def test_web_empty_name_stays_in_form(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).post("/w/ws/glossary", data={"name": "  ", "scope": "workspace"})
    assert r.status_code == 200
    assert Workspace.open(tmp_path / "ws").constitution.glossary == []
