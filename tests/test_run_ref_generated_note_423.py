"""#423：kairo run --ref 追加 generated 机器 note。证明走 CLI。"""
from __future__ import annotations

import json

from typer.testing import CliRunner

from kairo.cli import app
from kairo.generated_note import prepared_body
from kairo.notes import NotesError, add_note
from kairo.provider import AgentResult, StubProvider
from kairo.workspace import Workspace

runner = CliRunner()


class _NoteProvider:
    name = "stub"
    model = "stub"
    supports_read_dirs = True
    supports_project_cli = False

    def __init__(self, note):
        self.note = note
        self.contexts: list[str] = []
        self.inner = StubProvider()

    def run(self, config, signal=None):
        if config.artifact == "generated-note.txt":
            self.contexts.append(config.context)
            if isinstance(self.note, Exception):
                raise self.note
            config.artifact_dir.mkdir(parents=True, exist_ok=True)
            (config.artifact_dir / config.artifact).write_text(self.note)
            return AgentResult(result_text=self.note)
        return self.inner.run(config, signal)


def _ws(tmp_path, monkeypatch):
    serve = tmp_path / "root"
    serve.mkdir()
    ws = Workspace.init(serve / "topic", topic="单条")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    monkeypatch.chdir(ws.root)
    return serve, ws


def _bind(monkeypatch, provider):
    monkeypatch.setattr("kairo.cli.select_provider", lambda **_kw: provider)
    monkeypatch.setattr("kairo.generated_note.select_note_provider", lambda: provider)


def _add_text(ws, tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return ws.add([path])


def _show(ref_id):
    result = runner.invoke(app, ["notes", "show", "--ref", ref_id, "--json"])
    assert result.exit_code == 0, result.output + result.stderr
    return json.loads(result.stdout)


def _understanding(ws) -> bytes:
    path = ws.root / "understanding.md"
    return path.read_bytes() if path.is_file() else b""


def test_prepared_body_rejects_blank_and_overlong():
    assert prepared_body("  短记  ") == "短记"
    assert prepared_body(" \n") is None
    assert prepared_body("测" * 800) == "测" * 800
    assert prepared_body("测" * 801) is None


def test_human_add_rejects_generated(tmp_path, monkeypatch):
    _serve, ws = _ws(tmp_path, monkeypatch)
    ref_id = _add_text(ws, tmp_path, "a.txt", "只有人工")
    try:
        add_note(ws.root.parent, ref_id=ref_id, content="不该落下", note_type="generated", home="topic")
    except NotesError as exc:
        assert exc.code == "invalid_request"
    else:
        raise AssertionError("generated 不应从人工路径写入")
    result = runner.invoke(
        app,
        ["notes", "add", ref_id, "--content", "不该落下", "--type", "generated", "--json"],
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "invalid_request"
    assert _show(ref_id)["count"] == 0


def test_s1_appends_machine_note_without_touching_understanding(tmp_path, monkeypatch):
    _serve, ws = _ws(tmp_path, monkeypatch)
    ref_id = _add_text(ws, tmp_path, "a.txt", "纪要材料")
    provider = _NoteProvider("机器短记")
    _bind(monkeypatch, provider)
    before = _understanding(ws)
    folded = {
        key: dict(item.folded)
        for key, item in ws.read_state().targets.items()
    }
    result = runner.invoke(app, ["run", "--ref", ref_id])
    assert result.exit_code == 0, result.output + result.stderr
    digest = (ws.root / "references" / ref_id / "digest.md").read_text(encoding="utf-8")
    assert digest.strip()
    assert provider.contexts == [digest]
    shown = _show(ref_id)
    assert shown["count"] == 1
    item = shown["items"][0]
    assert item["type"] == "generated"
    assert item["author"] == "machine"
    assert item["content"] == "机器短记"
    assert len(item["content"]) <= 800
    assert (ws.root / "references" / ref_id / "digest.md").read_text(encoding="utf-8") == digest
    assert _understanding(ws) == before
    assert {
        key: dict(item.folded) for key, item in ws.read_state().targets.items()
    } == folded


def test_s1_again_appends_and_keeps_prior_notes(tmp_path, monkeypatch):
    _serve, ws = _ws(tmp_path, monkeypatch)
    ref_id = _add_text(ws, tmp_path, "a.txt", "再次")
    human = runner.invoke(
        app, ["notes", "add", ref_id, "--content", "人工判断", "--type", "decision", "--json"]
    )
    assert human.exit_code == 0, human.output
    provider = _NoteProvider("第一条机器")
    _bind(monkeypatch, provider)
    assert runner.invoke(app, ["run", "--ref", ref_id]).exit_code == 0
    provider.note = "第二条机器"
    again = runner.invoke(app, ["run", "--ref", ref_id])
    assert again.exit_code == 0, again.output + again.stderr
    items = _show(ref_id)["items"]
    assert [item["content"] for item in items] == ["人工判断", "第一条机器", "第二条机器"]
    assert [item["type"] for item in items] == ["decision", "generated", "generated"]
    assert items[0]["author"] != "machine"
    assert len(provider.contexts) == 2


def test_s2_failure_empty_and_overlong_keep_success(tmp_path, monkeypatch):
    _serve, ws = _ws(tmp_path, monkeypatch)
    ref_id = _add_text(ws, tmp_path, "a.txt", "会失败的纪要")
    cases = (RuntimeError("model down"), "  \n", "超" * 801)
    for note in cases:
        provider = _NoteProvider(note)
        _bind(monkeypatch, provider)
        digest = None
        path = ws.root / "references" / ref_id / "digest.md"
        if path.is_file():
            digest = path.read_bytes()
        result = runner.invoke(app, ["run", "--ref", ref_id])
        assert result.exit_code == 0, result.output + result.stderr
        assert _show(ref_id)["count"] == 0
        assert len(provider.contexts) == 1
        if digest is not None:
            assert path.read_bytes() == digest


def test_s3_does_not_backfill_other_refs_or_understanding(tmp_path, monkeypatch):
    _serve, ws = _ws(tmp_path, monkeypatch)
    target = _add_text(ws, tmp_path, "one.txt", "指定这一条")
    other = _add_text(ws, tmp_path, "two.txt", "历史那一条")
    provider = _NoteProvider("只写指定条")
    _bind(monkeypatch, provider)
    before = _understanding(ws)
    result = runner.invoke(app, ["run", "--ref", target])
    assert result.exit_code == 0, result.output + result.stderr
    assert _show(target)["count"] == 1
    assert _show(other)["count"] == 0
    assert _understanding(ws) == before
    assert other not in provider.contexts[0]
