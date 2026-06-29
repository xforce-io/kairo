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


def test_workspace_view_has_add_ref_upload_form(tmp_path):
    # 参考组应暴露一个上传表单(file 入口)指向 /ref,否则页面上无法添加 reference
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).get("/w/ws")
    assert r.status_code == 200
    assert 'hx-post="/w/ws/ref"' in r.text
    assert 'type="file"' in r.text
    assert 'multipart/form-data' in r.text


def test_workspace_view_has_add_ref_path_input(tmp_path):
    # 参考组除上传外,还应暴露一个路径输入入口(name=path)指向 /ref
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).get("/w/ws")
    assert r.status_code == 200
    assert 'name="path"' in r.text


def test_add_ref_nonexistent_path_returns_400(tmp_path):
    # 路径不存在应返回 400(可读错误),而非 500;且不残留半成品 ref 目录
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).post("/w/ws/ref", data={"path": str(tmp_path / "nope.txt")})
    assert r.status_code == 400
    assert Workspace.open(tmp_path / "ws").list_reference_ids() == []


def test_dialog_has_error_slot(tmp_path):
    # 弹框需有错误展示位,否则前端无处回显后端报错
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).get("/w/ws")
    assert 'id="add-ref-err"' in r.text


def test_add_ref_uses_dialog_trigger(tmp_path):
    # 添加入口收敛为一个 step 同款按钮 + 弹框,两种方式都在弹框内
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).get("/w/ws")
    assert r.status_code == 200
    assert 'btn-step btn-add-ref' in r.text  # 触发按钮沿用 step 样式
    assert "showModal()" in r.text
    assert '<dialog id="add-ref-dlg"' in r.text


def test_add_ref_path_with_empty_file_field(tmp_path):
    # 合并表单:填了 path、file 域留空时,应按 path 添加而非误存空上传
    Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "note.txt"
    src.write_text("一条笔记")
    r = _client(tmp_path).post(
        "/w/ws/ref",
        data={"path": str(src)},
        files={"file": ("", b"", "application/octet-stream")},
    )
    assert r.status_code == 200
    ws = Workspace.open(tmp_path / "ws")
    ids = ws.list_reference_ids()
    assert len(ids) == 1
    assert "note" in ws.read_manifest(ids[0]).title  # 来自 path 的文件名,不是 upload.bin


def test_add_ref_fragment_excludes_corpus(tmp_path):
    # 上传后刷新的列表片段只含 stream,不混入 corpus(否则参考组会重复显示基线)
    ws = Workspace.init(tmp_path / "ws", topic="t")
    cdir = tmp_path / "baseline"
    cdir.mkdir()
    (cdir / "a.md").write_text("基线文档")
    ws.add([cdir], source_class="corpus")
    files = {"file": ("meeting.txt", io.BytesIO("上传内容".encode()), "text/plain")}
    r = _client(tmp_path).post("/w/ws/ref", files=files)
    assert r.status_code == 200
    assert "meeting" in r.text  # 新 stream 在片段里
    assert "baseline" not in r.text  # corpus 目录名不应出现在参考片段


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
