"""机器 note 只接受有主次的短 Markdown，两处卡片把它渲染成 HTML。"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kairo.generated_note import (
    MAX_GENERATED_CHARS,
    _PERSONA,
    ensure_generated_note,
    list_item_count,
    prepared_body,
)
from kairo.notes import NotesError, append_generated_note, show_notes
from kairo.refs import create_tag
from kairo.web.server import create_app
from kairo.workspace import Workspace
from test_run_ref_generated_note_423 import _NoteProvider

OK = "**主点**：方案与交付先握手。\n\n- 数据分两级"


def _serve(tmp_path, monkeypatch):
    serve = tmp_path / "root"
    serve.mkdir()
    create_tag(serve, "energy")
    ws = Workspace.init(serve / "energy", topic="energy")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    monkeypatch.chdir(ws.root)
    src = tmp_path / "现场.txt"
    src.write_text("转写")
    ref_id = ws.add([src])
    (ws.references_dir() / ref_id / "digest.md").write_text("# 纪要\n\n事实。\n")
    return serve, ws, ref_id


def _preview(html: str) -> str:
    match = re.search(r'<div class="notes-preview-content">(.*?)</div>', html, re.S)
    assert match, html
    return match.group(1)


def test_markdown_parser_is_a_core_dependency():
    data = tomllib.loads(Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text())
    assert any(item.startswith("markdown-it-py") for item in data["project"]["dependencies"])


def test_renderer_list_markers_count_as_structure_and_toward_cap():
    plus = "\n".join(f"+ 第{i}点" for i in range(1, 8))
    assert prepared_body("+ 只有这一条") == "+ 只有这一条"
    assert prepared_body("1) 只有这一条") == "1) 只有这一条"
    assert prepared_body(f"**总判断**：会上对过口径。\n\n{plus}") is None
    assert list_item_count(plus) == 7


def test_persona_asks_for_markdown_structure():
    assert _PERSONA.startswith("根据下面这一份详备纪要写一条短 note")
    assert "加粗（**）" in _PERSONA
    assert "列表" in _PERSONA
    assert "零换行" in _PERSONA


def test_acceptance_rejects_empty_overlong_and_plain_paragraph(tmp_path, monkeypatch):
    serve, _ws, ref_id = _serve(tmp_path, monkeypatch)
    assert prepared_body(" \n") is None
    assert prepared_body("零换行没有标记的一整段文字") is None
    assert prepared_body("测" * (MAX_GENERATED_CHARS + 1)) is None
    assert prepared_body(OK) == OK
    ledger = "**总判断**：会上对过口径。\n\n" + "\n".join(f"- 第{i}点。" for i in range(7))
    assert prepared_body(ledger) is None
    for content, needle in (
        ("  \n", "为空"),
        ("测" * (MAX_GENERATED_CHARS + 1), "800"),
        ("零换行没有标记的一整段文字", "Markdown"),
        (ledger, "流水账"),
    ):
        with pytest.raises(NotesError) as exc:
            append_generated_note(serve, ref_id=ref_id, content=content, home="energy")
        assert needle in str(exc.value)
    assert show_notes(serve, ref_id=ref_id, home="energy")["count"] == 0
    written = append_generated_note(serve, ref_id=ref_id, content=OK, home="energy")
    assert written["type"] == "generated"
    shown = show_notes(serve, ref_id=ref_id, home="energy")
    assert shown["count"] == 1
    assert shown["items"][0]["content"] == OK
    assert shown["items"][0]["author"] == "machine"


def test_offline_stub_writes_a_structured_machine_note(tmp_path, monkeypatch):
    _serve_root, ws, ref_id = _serve(tmp_path, monkeypatch)
    monkeypatch.setenv("KAIRO_STUB", "1")
    done = ensure_generated_note(ws, ref_id)
    assert done["status"] == "succeeded", done
    item = show_notes(ws.root.parent, ref_id=ref_id, home="energy")["items"][0]
    assert item["type"] == "generated"
    assert item["author"] == "machine"
    assert item["content"].startswith("**STUB**")
    assert prepared_body(item["content"]) == item["content"]


def test_ensure_generated_note_rejects_plain_then_writes_structured(tmp_path, monkeypatch):
    _serve_root, ws, ref_id = _serve(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "kairo.generated_note.select_note_provider",
        lambda: _NoteProvider("零换行没有标记的一整段文字"),
    )
    failed = ensure_generated_note(ws, ref_id)
    assert failed["status"] == "failed"
    assert show_notes(ws.root.parent, ref_id=ref_id, home="energy")["count"] == 0
    monkeypatch.setattr(
        "kairo.generated_note.select_note_provider",
        lambda: _NoteProvider(OK),
    )
    done = ensure_generated_note(ws, ref_id, retry_failed=True)
    assert done["status"] == "succeeded"
    item = show_notes(ws.root.parent, ref_id=ref_id, home="energy")["items"][0]
    assert item["content"] == OK
    assert item["type"] == "generated"


def test_global_ref_and_topic_reader_render_note_markdown(tmp_path, monkeypatch):
    serve, _ws, ref_id = _serve(tmp_path, monkeypatch)
    append_generated_note(serve, ref_id=ref_id, content=OK, home="energy")
    client = TestClient(create_app(serve))
    pages = (
        client.get(f"/refs/{ref_id}?home=energy"),
        client.get(f"/w/energy/ref/{ref_id}/notes"),
    )
    for page in pages:
        assert page.status_code == 200, page.text
        inner = _preview(page.text)
        assert inner.lstrip().startswith("<")
        assert "<strong>主点</strong>" in inner
        assert "<li>数据分两级</li>" in inner
        assert "**" not in inner
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert ".notes-panel .notes-preview-content ul { list-style: disc; }" in css.text
    assert 'href="/static/app.css?v=442.2-image-preview"' in pages[0].text
