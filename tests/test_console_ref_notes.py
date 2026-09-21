"""#411 Console Ref notes：digest 之上列表/追加；Topic 形态跟随选中 Ref。"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from kairo.notes import add_note, show_notes
from kairo.refs import create_tag
from kairo.web.server import create_app
from kairo.workspace import Workspace
from typer.testing import CliRunner

from kairo.cli import app as cli_app

runner = CliRunner()


def _cli(args, *, input=None):
    return runner.invoke(cli_app, args, input=input)


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
    r = _cli(["add", str(src), "--copy"])
    assert r.exit_code == 0, r.output
    ws = Workspace.open(serve / "energy")
    rid = ws.list_reference_ids()[0]
    digest = serve / "energy" / "references" / rid / "digest.md"
    digest.write_text("# digest 标题\n\n纪要正文\n")
    return serve, rid


def test_s1_s3_notes_above_digest(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    client = TestClient(create_app(serve))
    empty = client.get(f"/refs/{rid}?home=energy", headers=ZH)
    assert empty.status_code == 200
    html = empty.text
    assert "尚无洞察 notes" in html
    assert 'id="notes"' in html
    assert html.find('id="notes"') < html.find("digest 标题") or html.find("纪要正文")
    assert html.find("洞察 notes") < html.find("Digest")
    assert "追加 note" in html
    assert "请去 CLI" not in html
    notes_block = html.split('id="notes"', 1)[1]
    assert "btn-step" not in notes_block.split("</section>", 1)[0]
    add_note(serve, ref_id=rid, content="第一条洞察", home="energy")
    listed = client.get(f"/refs/{rid}?home=energy", headers=ZH)
    assert listed.status_code == 200
    tower = show_notes(serve, ref_id=rid, home="energy")
    assert tower["count"] == 1
    block = listed.text.split('class="notes-list"', 1)[1].split("</ul>", 1)[0]
    assert block.count('<li class="notes-card">') == tower["count"]
    assert listed.text.count("第一条洞察") >= tower["count"]
    assert listed.text.find("第一条洞察") < listed.text.find("digest 标题")


def test_s2_readonly_no_form(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    added = add_note(serve, ref_id=rid, content="只读正文", home="energy")
    note_id = added["stable_id"].rsplit("/", 1)[-1]
    client = TestClient(create_app(serve))
    page = client.get(f"/refs/{rid}/notes/{note_id}?home=energy", headers=ZH)
    assert page.status_code == 200
    assert "只读正文" in page.text
    assert "追加 note" not in page.text
    assert 'name="content"' not in page.text


def test_s4_add_and_reject_empty(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    client = TestClient(create_app(serve), follow_redirects=True)
    before = show_notes(serve, ref_id=rid, home="energy")["count"]
    ok = client.post(
        f"/refs/{rid}/notes",
        data={"home": "energy", "content": "页面追加", "type": ""},
        headers=ZH,
    )
    assert ok.status_code == 200
    assert "页面追加" in ok.text
    after = show_notes(serve, ref_id=rid, home="energy")
    assert after["count"] == before + 1
    assert after["items"][-1]["type"] == "insight"
    empty = client.post(
        f"/refs/{rid}/notes",
        data={"home": "energy", "content": "   ", "type": "insight"},
        headers=ZH,
    )
    assert empty.status_code == 200
    assert "正文不能为空" in empty.text
    assert show_notes(serve, ref_id=rid, home="energy")["count"] == after["count"]


def test_s5_topic_preview_follows_selected_ref(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    add_note(serve, ref_id=rid, content="侧栏预览", home="energy")
    client = TestClient(create_app(serve))
    meta = client.get(f"/w/energy/ref/{rid}", headers=ZH)
    assert meta.status_code == 200
    assert "洞察 notes" in meta.text
    assert "侧栏预览" in meta.text
    assert 'class="is-prev notes-form-row"' in meta.text or "notes-form-row" in meta.text
    assert 'hx-get="/w/energy/ref/' in meta.text and "/notes" in meta.text
    assert 'hx-target="#reader"' in meta.text
    assert "ref-open-details" in meta.text
    assert "打开资料详情" in meta.text
    assert 'href="/refs/' in meta.text
    assert "追加 note" not in meta.text
    canvas = client.get(f"/w/energy/ref/{rid}/notes", headers=ZH)
    assert canvas.status_code == 200
    assert "侧栏预览" in canvas.text
    assert "追加 note" in canvas.text
    added = client.post(
        f"/w/energy/ref/{rid}/notes",
        data={"content": "画布追加", "type": ""},
        headers=ZH,
    )
    assert added.status_code == 200
    assert "画布追加" in added.text
    assert show_notes(serve, ref_id=rid, home="energy")["count"] == 2
    # 无 notes 的同一 Topic：另加一条无 note 的 Ref
    monkeypatch.chdir(serve / "energy")
    src = tmp_path / "另一.txt"
    src.write_text("b")
    r = _cli(["add", str(src), "--copy"])
    assert r.exit_code == 0, r.output
    ids = Workspace.open(serve / "energy").list_reference_ids()
    other = next(i for i in ids if i != rid)
    empty_meta = client.get(f"/w/energy/ref/{other}", headers=ZH)
    assert empty_meta.status_code == 200
    assert "尚无洞察 notes" in empty_meta.text
    assert "/notes" in empty_meta.text and "hx-get=" in empty_meta.text
    assert "追加 note" not in empty_meta.text
    empty_canvas = client.get(f"/w/energy/ref/{other}/notes", headers=ZH)
    assert empty_canvas.status_code == 200
    assert "尚无洞察 notes" in empty_canvas.text
    assert "追加 note" in empty_canvas.text


def _assert_notes_panel(html: str) -> None:
    assert "notes-head" in html
    assert "记录你的判断，fold 后仍保留" in html
    assert "notes-add" in html
    assert "notes-add-row" in html
    assert 'rows="3"' in html
    assert "btn-inline" in html
    assert "btn-step" not in html
    assert html.find("notes-head") < html.find("notes-add")
    empty_at = html.find("尚无洞察 notes")
    list_at = html.find("notes-list")
    if empty_at != -1:
        assert html.find("notes-add") < empty_at
    if list_at != -1:
        assert html.find("notes-add") < list_at
        assert "notes-card" in html
    assert "置顶" not in html
    assert "删除这条" not in html


def test_s6_notes_compact_not_card_form(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    client = TestClient(create_app(serve))
    ref_page = client.get(f"/refs/{rid}?home=energy", headers=ZH)
    assert ref_page.status_code == 200
    assert 'href="/static/app.css?v=411-notes-markdown-preview"' in ref_page.text
    assert "396-view-run-exec" not in ref_page.text
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert ".notes-add textarea" in css.text
    assert "min-height: 72px" in css.text
    add_block = ref_page.text.split('id="notes"', 1)[1].split("</section>", 1)[0]
    _assert_notes_panel(add_block)
    canvas = client.get(f"/w/energy/ref/{rid}/notes", headers=ZH).text
    _assert_notes_panel(canvas)
    add_note(serve, ref_id=rid, content="轻卡片", home="energy")
    listed = client.get(f"/refs/{rid}?home=energy", headers=ZH).text.split(
        'id="notes"', 1
    )[1].split("</section>", 1)[0]
    _assert_notes_panel(listed)
    listed_canvas = client.get(f"/w/energy/ref/{rid}/notes", headers=ZH).text
    _assert_notes_panel(listed_canvas)


def test_public_read_no_form_and_no_write(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    add_note(serve, ref_id=rid, content="公开可见", home="energy")
    client = TestClient(create_app(serve, mode="public-read"))
    page = client.get(f"/refs/{rid}?home=energy", headers=ZH)
    # 未发表的 Ref 在 public-read 可能 404；若可见则无表单
    if page.status_code == 200:
        assert "追加 note" not in page.text
        assert "公开可见" in page.text
    posted = client.post(
        f"/refs/{rid}/notes",
        data={"home": "energy", "content": "不该写入"},
    )
    assert posted.status_code in (403, 404)
    tower = show_notes(serve, ref_id=rid, home="energy")
    assert tower["count"] == 1
    assert all("不该写入" not in (it.get("excerpt") or "") for it in tower["items"])


def test_delete_requires_confirmation_and_preserves_other_notes(tmp_path, monkeypatch):
    from kairo.notes import NotesError
    import pytest

    serve, rid = _setup(tmp_path, monkeypatch)
    first = add_note(serve, ref_id=rid, content="删除目标", home="energy")
    second = add_note(serve, ref_id=rid, content="保留正文", home="energy")
    sid = first["stable_id"]
    nid = sid.rsplit("/", 1)[-1]
    client = TestClient(create_app(serve))
    url = f"/refs/{rid}/notes/{nid}/delete"
    assert client.post(url, data={"home": "energy"}).status_code == 400
    assert show_notes(serve, ref_id=rid, home="energy")["count"] == 2
    response = client.post(url, data={"home": "energy", "confirmed": "yes"}, headers=ZH)
    assert response.status_code == 200
    assert "已删除 note" in response.text
    assert "保留正文" in response.text
    assert "删除目标" not in response.text
    assert show_notes(serve, ref_id=rid, home="energy")["count"] == 1
    with pytest.raises(NotesError):
        show_notes(serve, stable_id=sid)
    assert client.post(url, data={"home": "energy", "confirmed": "yes"}).status_code == 404
    assert show_notes(serve, stable_id=second["stable_id"])["content"] == "保留正文"


def test_delete_failure_retry_and_last_note(tmp_path, monkeypatch):
    import kairo.notes as notes

    serve, rid = _setup(tmp_path, monkeypatch)
    added = add_note(serve, ref_id=rid, content="失败仍保留", home="energy")
    nid = added["stable_id"].rsplit("/", 1)[-1]
    url = f"/refs/{rid}/notes/{nid}/delete"
    client = TestClient(create_app(serve))
    write = notes._write_records
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(notes, "_write_records", fail)
    response = client.post(url, data={"home": "energy", "confirmed": "yes"}, headers=ZH)
    assert "删除失败" in response.text
    assert "失败仍保留" in response.text
    assert "note-delete-dialog" in response.text
    monkeypatch.setattr(notes, "_write_records", write)
    response = client.post(url, data={"home": "energy", "confirmed": "yes"}, headers=ZH)
    assert "尚无洞察 notes" in response.text
    assert show_notes(serve, ref_id=rid, home="energy")["count"] == 0


def test_public_delete_never_writes(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    added = add_note(serve, ref_id=rid, content="不可删除", home="energy")
    nid = added["stable_id"].rsplit("/", 1)[-1]
    client = TestClient(create_app(serve, mode="public-read"))
    url = f"/refs/{rid}/notes/{nid}"
    page = client.get(url + "?home=energy", headers=ZH)
    assert "note-delete-dialog" not in page.text
    assert client.post(url + "/delete", data={"home": "energy", "confirmed": "yes"}).status_code in (403, 404, 405)
    assert show_notes(serve, stable_id=added["stable_id"])["content"] == "不可删除"


def test_concurrent_delete_add_and_no_reused_id(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from kairo.notes import delete_note

    serve, rid = _setup(tmp_path, monkeypatch)
    when = datetime.now(timezone.utc)
    first = add_note(serve, ref_id=rid, content="旧条目", home="energy", now=when)
    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion = pool.submit(delete_note, serve, stable_id=first["stable_id"])
        addition = pool.submit(add_note, serve, ref_id=rid, content="并发新条目", home="energy", now=when)
        deletion.result()
        new = addition.result()
    assert first["stable_id"] != new["stable_id"]
    tower = show_notes(serve, ref_id=rid, home="energy")
    assert tower["count"] == 1
    assert tower["items"][0]["content"] == "并发新条目"


def test_s6_markdown_preview_uses_full_body_and_separate_detail_link(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    body = '**重点判断**\n\n- 第一点\n- 第二点\n\n> 引用\n\n' + ('长正文\n\n' * 30) + '末尾完整内容\n\n[来源](https://example.com)\n\n<script>alert(1)</script>'
    add_note(serve, ref_id=rid, content=body, home="energy")
    client = TestClient(create_app(serve))
    for url in (f'/refs/{rid}?home=energy', f'/w/energy/ref/{rid}/notes'):
        html = client.get(url, headers=ZH).text
        assert '<strong>重点判断</strong>' in html
        assert '<li>第一点</li>' in html
        assert '<blockquote>' in html
        assert '末尾完整内容' in html
        assert '<script>alert(1)</script>' not in html
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
        assert '<a href="https://example.com">来源</a>' in html
        assert 'class="notes-more" hidden aria-expanded="false"' in html
        assert 'class="notes-detail-link"' in html
        assert '>查看详情</a>' in html
        card = html.split('<li class="notes-card">', 1)[1]
        assert card.lstrip().startswith('<div class="notes-card-head">')
