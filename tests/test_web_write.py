# tests/test_web_write.py
import io

from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.workspace import Workspace


def _client(root):
    return TestClient(create_app(root))


def test_add_ref_by_path(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "note.txt"
    src.write_text("一条笔记")
    r = _client(tmp_path).post("/w/ws/ref", data={"path": str(src)})
    assert r.status_code == 200
    assert "note" in r.text  # 列表片段含新 reference
    ws = Workspace.open(tmp_path / "ws")
    assert len(ws.list_reference_ids()) == 1


def test_add_ref_by_upload(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    files = {"file": ("meeting.txt", io.BytesIO("上传内容".encode()), "text/plain")}
    r = _client(tmp_path).post("/w/ws/ref", files=files)
    assert r.status_code == 200
    ws = Workspace.open(tmp_path / "ws")
    assert len(ws.list_reference_ids()) == 1


def test_add_corpus_dir(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    cdir = tmp_path / "baseline"
    cdir.mkdir()
    (cdir / "x.md").write_text("基线")
    r = _client(tmp_path).post("/w/ws/corpus", data={"path": str(cdir)})
    assert r.status_code == 200
    ws = Workspace.open(tmp_path / "ws")
    man = ws.read_manifest(ws.list_reference_ids()[0])
    assert man.source_class == "corpus"


def test_create_workspace(tmp_path):
    from urllib.parse import quote

    r = _client(tmp_path).post(
        "/workspaces", data={"topic": "产品规划"}, follow_redirects=False
    )
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") == "/w/" + quote("产品规划")
    ws = Workspace.open(tmp_path / "产品规划")
    assert ws.constitution.topic == "产品规划"


def test_create_workspace_rejects_bad_topic(tmp_path):
    for bad in ["../escape", "a/b", "..", ".", ".hidden", "", "  "]:
        r = _client(tmp_path).post("/workspaces", data={"topic": bad})
        assert r.status_code == 400, bad


def test_create_workspace_rejects_too_long(tmp_path):
    r = _client(tmp_path).post("/workspaces", data={"topic": "长" * 65})
    assert r.status_code == 400


def test_create_workspace_rejects_control_chars(tmp_path):
    for bad in ["a\nb", "a\tb", "a\x00b"]:
        r = _client(tmp_path).post("/workspaces", data={"topic": bad})
        assert r.status_code == 400, repr(bad)


def test_create_workspace_rejects_existing(tmp_path):
    Workspace.init(tmp_path / "产品规划", topic="产品规划")
    r = _client(tmp_path).post("/workspaces", data={"topic": "产品规划"})
    assert r.status_code == 400


def test_accept_clears_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("内容")
    ws.add([tmp_path / "m.txt"])
    step(ws, select_provider())
    # 手改 understanding,再 step → blocked:manual-edit
    (tmp_path / "ws" / "understanding.md").write_text("手改了")
    step(ws, select_provider())
    assert ws.read_state().targets["understanding.md"].status == "blocked"
    r = _client(tmp_path).post("/w/ws/accept", data={"doc": "understanding.md"})
    assert r.status_code == 200
    assert Workspace.open(tmp_path / "ws").read_state().targets["understanding.md"].status == "ok"
