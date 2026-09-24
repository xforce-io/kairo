"""#433 主题页左侧点击参考，默认进入 notes 预览。不改置顶规则。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app as cli_app
from kairo.notes import add_note, pin_note
from kairo.refs import create_tag
from kairo.web.server import create_app
from kairo.workspace import Workspace

runner = CliRunner()
ZH = {"Accept-Language": "zh-CN", "HX-Request": "true"}


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


def _reader(html: str) -> str:
    return html.split('id="reader"', 1)[1]


def _expanded(html: str) -> str:
    return html.split("is-expanded", 1)[1].split("</li>", 1)[0]


def test_left_click_hx_lands_on_notes_panel(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    page = TestClient(create_app(serve)).get("/w/energy", params={"ref": rid}, headers=ZH)
    assert page.status_code == 200
    enc = quote(rid, safe="")
    assert f'hx-get="/w/energy/ref/{enc}?panel=notes"' in page.text
    assert f'href="/w/energy?ref={enc}"' in page.text


def test_s1_unpinned_click_expands_earliest(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    add_note(serve, ref_id=rid, content="最早正文", home="energy")
    add_note(serve, ref_id=rid, content="较晚正文", home="energy")
    client = TestClient(create_app(serve))
    landed = client.get(f"/w/energy/ref/{rid}", params={"panel": "notes"}, headers=ZH)
    assert landed.status_code == 200
    reader = _reader(landed.text)
    assert "is-expanded" in reader
    assert "最早正文" in _expanded(reader)
    assert "较晚正文" not in _expanded(reader)
    assert "已置顶" not in reader
    assert "纪要正文" not in reader
    summary = client.get(f"/w/energy/ref/{rid}", headers=ZH)
    assert "纪要正文" in _reader(summary.text)
    assert "最早正文" not in _reader(summary.text)


def test_s2_pinned_later_stays_expanded(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    add_note(serve, ref_id=rid, content="最早正文", home="energy")
    later = add_note(serve, ref_id=rid, content="较晚置顶", home="energy")
    later_id = later["stable_id"].rsplit("/", 1)[-1]
    pin_note(serve, ref_id=rid, note_id=later_id, home="energy")
    pin_path = serve / "energy" / "references" / rid / "note-pin.json"
    before = pin_path.read_bytes()
    landed = TestClient(create_app(serve)).get(
        f"/w/energy/ref/{rid}", params={"panel": "notes"}, headers=ZH
    )
    reader = _reader(landed.text)
    assert "较晚置顶" in _expanded(reader)
    assert "最早正文" not in _expanded(reader)
    assert reader.count("已置顶") == 1
    assert pin_path.read_bytes() == before


def test_s3_empty_notes_preview_can_return_to_summary(tmp_path, monkeypatch):
    serve, rid = _setup(tmp_path, monkeypatch)
    notes_file = serve / "energy" / "references" / rid / "notes.jsonl"
    client = TestClient(create_app(serve))
    landed = client.get(f"/w/energy/ref/{rid}", params={"panel": "notes"}, headers=ZH)
    reader = _reader(landed.text)
    assert "尚无洞察 notes" in reader
    enc = quote(rid, safe="")
    assert "回到摘要" in reader
    assert f'hx-get="/w/energy/ref/{enc}"' in reader
    assert "panel=notes" not in reader
    assert not notes_file.exists()
    summary = client.get(f"/w/energy/ref/{rid}", headers=ZH)
    back = _reader(summary.text)
    assert "摘要" in back
    assert "纪要正文" in back
    assert "尚无洞察 notes" not in back
