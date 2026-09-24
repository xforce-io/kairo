"""#431 Topic notes 预览：一条由人设置的置顶。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app as cli_app
from kairo.notes import (
    NotesError,
    add_note,
    append_generated_note,
    choose_expanded_note_id,
    delete_note,
    pin_note,
    read_note_pin,
    show_notes,
    stable_id_for,
)
from kairo.refs import create_tag
from kairo.web.public import set_reference_public
from kairo.web.server import create_app
from kairo.workspace import Workspace

runner = CliRunner()
ZH = {"Accept-Language": "zh-CN"}


def _setup(tmp_path, monkeypatch):
    serve = tmp_path / "root"
    serve.mkdir()
    create_tag(serve, "energy")
    Workspace.init(serve / "energy", topic="energy")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    monkeypatch.chdir(serve / "energy")
    src = tmp_path / "现场.txt"
    src.write_text("转写")
    result = runner.invoke(cli_app, ["add", str(src), "--copy"])
    assert result.exit_code == 0, result.output
    ws = Workspace.open(serve / "energy")
    rid = ws.list_reference_ids()[0]
    digest = serve / "energy" / "references" / rid / "digest.md"
    digest.write_text("# digest 标题\n\n纪要正文\n")
    return serve, rid


def _pin_file(serve, rid):
    return serve / "energy" / "references" / rid / "note-pin.json"


def _ids(serve, rid):
    return [it["stable_id"].rsplit("/", 1)[-1] for it in show_notes(serve, ref_id=rid, home="energy")["items"]]


def test_choose_expanded_valid_dangling_and_empty():
    assert choose_expanded_note_id([], "n1") is None
    assert choose_expanded_note_id(["early", "late"], None) == "early"
    assert choose_expanded_note_id(["early", "late"], "missing") == "early"
    assert choose_expanded_note_id(["early", "late"], "late") == "late"


def test_pin_replace_delete_and_machine_do_not_leave_two(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    first = add_note(serve, ref_id=rid, content="最早", home="energy")
    second = add_note(serve, ref_id=rid, content="后写", home="energy")
    first_id = first["stable_id"].rsplit("/", 1)[-1]
    second_id = second["stable_id"].rsplit("/", 1)[-1]
    pin_note(serve, ref_id=rid, note_id=first_id, home="energy")
    pin_note(serve, ref_id=rid, note_id=second_id, home="energy")
    assert json.loads(_pin_file(serve, rid).read_text()) == {"note_id": second_id}
    before = _pin_file(serve, rid).read_bytes()
    add_note(serve, ref_id=rid, content="人工追加", home="energy")
    append_generated_note(serve, ref_id=rid, content="机器追加", home="energy")
    assert _pin_file(serve, rid).read_bytes() == before
    shown = runner.invoke(cli_app, ["notes", "show", "--ref", rid, "--home", "energy", "--json"])
    assert shown.exit_code == 0
    payload = json.loads(shown.stdout)
    assert "note_id" not in payload
    assert all("pinned" not in it for it in payload["items"])
    delete_note(serve, stable_id=stable_id_for("energy", rid, first_id))
    assert json.loads(_pin_file(serve, rid).read_text())["note_id"] == second_id
    delete_note(serve, stable_id=second["stable_id"])
    assert not _pin_file(serve, rid).exists()
    try:
        pin_note(serve, ref_id=rid, note_id=second_id, home="energy")
    except NotesError as exc:
        assert exc.code == "not_found"
    else:
        raise AssertionError("missing note was pinned")
    assert not _pin_file(serve, rid).exists()


def test_dangling_pin_file_is_kept(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    add_note(serve, ref_id=rid, content="还在", home="energy")
    path = _pin_file(serve, rid)
    path.write_text('{"note_id":"gone","extra":1}\n', encoding="utf-8")
    assert read_note_pin(serve, ref_id=rid, home="energy") == "gone"
    assert path.read_text(encoding="utf-8") == '{"note_id":"gone","extra":1}\n'
    path.write_text("not-json", encoding="utf-8")
    assert read_note_pin(serve, ref_id=rid, home="energy") is None
    assert path.read_text(encoding="utf-8") == "not-json"


def test_preview_s1_to_s6(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    client = TestClient(create_app(serve))
    empty = client.get(f"/w/energy/ref/{rid}/notes", headers=ZH)
    assert empty.status_code == 200
    assert "尚无洞察 notes" in empty.text
    assert "回到摘要" in empty.text
    assert "置顶" not in empty.text.replace("回到摘要", "")
    back = client.get(f"/w/energy/ref/{rid}", headers=ZH)
    reader = back.text.split('id="reader"', 1)[1]
    assert "纪要正文" in reader
    assert not (serve / "energy" / "references" / rid / "digest.md").read_text().count("新造")

    first = add_note(serve, ref_id=rid, content="最早\n\n**全文标记**\n\n" + ("段落\n\n" * 8), home="energy")
    second = add_note(serve, ref_id=rid, content="后写的卡片", home="energy")
    first_id = first["stable_id"].rsplit("/", 1)[-1]
    second_id = second["stable_id"].rsplit("/", 1)[-1]
    unpinned = client.get(f"/w/energy/ref/{rid}/notes", headers=ZH).text
    expanded = unpinned.split("is-expanded", 1)[1].split("</li>", 1)[0]
    assert "全文标记" in expanded
    assert "<strong>全文标记</strong>" in expanded
    assert "notes-more" not in expanded
    assert "已置顶" not in unpinned
    cards = unpinned.split('class="notes-card"')[1]
    assert "notes-preview" in cards
    assert "notes-more" in cards

    pinned_first = client.post(f"/w/energy/ref/{rid}/notes/{first_id}/pin", headers=ZH)
    assert pinned_first.status_code == 200
    assert pinned_first.text.count("已置顶") == 1
    pinned_second = client.post(f"/w/energy/ref/{rid}/notes/{second_id}/pin", headers=ZH)
    assert pinned_second.status_code == 200
    refreshed = client.get(f"/w/energy/ref/{rid}/notes", headers=ZH).text
    assert refreshed.count("已置顶") == 1
    assert refreshed.split("is-expanded", 1)[1].split("</li>", 1)[0].count("后写的卡片") == 1
    assert json.loads(_pin_file(serve, rid).read_text())["note_id"] == second_id

    missing = client.post(f"/w/energy/ref/{rid}/notes/not-a-note/pin", headers=ZH)
    assert "这条 note 已不在，置顶未改变。" in missing.text
    assert json.loads(_pin_file(serve, rid).read_text())["note_id"] == second_id

    detail = client.get(f"/refs/{rid}?home=energy", headers=ZH).text
    detail_notes = detail.split('id="notes"', 1)[1].split("</section>", 1)[0]
    assert "置顶" not in detail_notes
    assert "is-expanded" not in detail_notes

    delete_note(serve, stable_id=stable_id_for("energy", rid, second_id))
    after_delete = client.get(f"/w/energy/ref/{rid}/notes", headers=ZH).text
    assert "已置顶" not in after_delete
    assert not _pin_file(serve, rid).exists()
    assert "is-expanded" in after_delete
    assert "最早" in after_delete.split("is-expanded", 1)[1].split("</li>", 1)[0]


def test_public_read_shows_pin_without_setting_it(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    added = add_note(serve, ref_id=rid, content="公开这一条", home="energy")
    note_id = added["stable_id"].rsplit("/", 1)[-1]
    pin_note(serve, ref_id=rid, note_id=note_id, home="energy")
    ws = Workspace.open(serve / "energy")
    set_reference_public(serve, ws, rid, public=True)
    before = _pin_file(serve, rid).read_bytes()
    public = TestClient(create_app(serve, mode="public-read"))
    page = public.get(f"/w/energy/ref/{rid}/notes", headers=ZH)
    assert page.status_code == 200
    assert "已置顶" in page.text
    assert "notes-pin-btn" not in page.text
    denied = public.post(f"/w/energy/ref/{rid}/notes/{note_id}/pin", headers=ZH)
    assert denied.status_code == 404
    assert _pin_file(serve, rid).read_bytes() == before
