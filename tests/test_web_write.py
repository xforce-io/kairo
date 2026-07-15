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
    assert r.headers.get("HX-Refresh") == "true"
    ws = Workspace.open(tmp_path / "ws")
    man = ws.read_manifest(ws.list_reference_ids()[0])
    assert man.source_class == "corpus"


def test_workspace_view_has_add_corpus_path_input(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).get("/w/ws")
    assert r.status_code == 200
    assert 'id="add-corpus-dlg"' in r.text
    assert 'hx-post="/w/ws/corpus"' in r.text
    assert "corpus.add_failed" not in r.text


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


def test_attach_to_existing_ref_by_path(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.txt"
    a.write_text("转写")
    rid = ws.add([a])
    img = tmp_path / "board.png"
    img.write_bytes(b"\x89PNG\r\n")
    r = _client(tmp_path).post(f"/w/ws/ref/{rid}/attach", data={"path": str(img)})
    assert r.status_code == 200
    man = Workspace.open(tmp_path / "ws").read_manifest(rid)
    atts = [f for f in man.forms if f.role == "attachment"]
    assert len(atts) == 1
    # 复制进 ref 目录(自包含)
    assert atts[0].location.startswith(f"references/{rid}/")
    assert (tmp_path / "ws" / atts[0].location).is_file()

def test_attach_unknown_ref_404(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).post("/w/ws/ref/nope/attach", data={"path": "/x"})
    assert r.status_code == 404

def test_attach_bad_path_400(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rid = ws.add([(lambda p: (p.write_text('x'), p)[1])(tmp_path / "a.txt")])
    r = _client(tmp_path).post(f"/w/ws/ref/{rid}/attach", data={"path": str(tmp_path / "no.png")})
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


def test_attach_multiple_files_at_once(tmp_path):
    # 一次上传多张图片 → 各自成 attachment form(不必一张一张来)
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.txt"
    a.write_text("转写")
    rid = ws.add([a])
    files = [
        ("files", ("b1.png", io.BytesIO(b"\x89PNG\r\n1"), "image/png")),
        ("files", ("b2.png", io.BytesIO(b"\x89PNG\r\n2"), "image/png")),
        ("files", ("b3.png", io.BytesIO(b"\x89PNG\r\n3"), "image/png")),
    ]
    r = _client(tmp_path).post(f"/w/ws/ref/{rid}/attach", files=files)
    assert r.status_code == 200
    man = Workspace.open(tmp_path / "ws").read_manifest(rid)
    atts = [f for f in man.forms if f.role == "attachment"]
    assert len(atts) == 3, [f.location for f in atts]


def test_rename_ref_title(tmp_path):
    # 详情面板改名:POST title → manifest 更新,返回刷新后的详情面板含新标题
    ws = Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "260629_110439.txt"
    src.write_text("内容")
    rid = ws.add([src])
    r = _client(tmp_path).post(f"/w/ws/ref/{rid}/title", data={"title": "数字员工架构对齐"})
    assert r.status_code == 200
    assert "数字员工架构对齐" in r.text  # 刷新的详情面板含新标题
    assert Workspace.open(tmp_path / "ws").read_manifest(rid).title == "数字员工架构对齐"


def test_rename_ref_empty_returns_400_and_keeps_old(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "260629_110439.txt"
    src.write_text("内容")
    rid = ws.add([src])
    r = _client(tmp_path).post(f"/w/ws/ref/{rid}/title", data={"title": "   "})
    assert r.status_code == 400
    assert Workspace.open(tmp_path / "ws").read_manifest(rid).title == "260629_110439"


def test_rename_unknown_ref_404(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).post("/w/ws/ref/nope/title", data={"title": "x"})
    assert r.status_code == 404


def test_ref_meta_has_rename_form(tmp_path):
    # 详情面板标题可改名:含指向 title 路由的表单 + 预填当前标题的输入
    ws = Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "260629_110439.txt"
    src.write_text("内容")
    rid = ws.add([src])
    r = _client(tmp_path).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert f"/w/ws/ref/{rid}/title" in r.text
    assert 'name="title"' in r.text
    assert "260629_110439" in r.text  # 输入预填当前标题


def test_nav_has_no_source_class_tag(tmp_path):
    # 去掉冗余的 stream/corpus 英文标签(分组已表达分类),参考仍正常显示
    ws = Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "260629_110439.txt"
    src.write_text("内容")
    ws.add([src])
    r = _client(tmp_path).get("/w/ws")
    assert r.status_code == 200
    assert "260629_110439" in r.text
    assert 'class="tag' not in r.text


def test_refs_fragment_has_no_source_class_tag(tmp_path):
    # 上传后刷新的列表片段同样不再渲染 source_class 标签
    Workspace.init(tmp_path / "ws", topic="t")
    files = {"file": ("260629_110439.txt", io.BytesIO("x".encode()), "text/plain")}
    r = _client(tmp_path).post("/w/ws/ref", files=files)
    assert r.status_code == 200
    assert 'class="tag' not in r.text


def _ws_with_machine_transcript(tmp_path, monkeypatch):
    """workspace 含 ASR 派生 transcript 的音频 ref;normalize 默认关。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.models import State
    from kairo.rules import TransformRule

    ws = Workspace.init(tmp_path / "ws", topic="t")
    audio = tmp_path / "rec.m4a"
    audio.write_bytes(b"fake")
    rid = ws.add([audio])
    TransformRule(ws).discover()[0].run(State())
    return rid


def test_ref_meta_shows_generate_prose_when_eligible(tmp_path, monkeypatch):
    rid = _ws_with_machine_transcript(tmp_path, monkeypatch)
    r = _client(tmp_path).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert "生成可读文稿" in r.text or "Generate readable prose" in r.text
    assert f'hx-post="/w/ws/ref/{rid}/prose"' in r.text


def test_ref_meta_hides_generate_prose_for_human_text(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "note.txt"
    src.write_text("人给")
    rid = Workspace.open(tmp_path / "ws").add([src])
    r = _client(tmp_path).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert f'hx-post="/w/ws/ref/{rid}/prose"' not in r.text


def test_ref_meta_hides_generate_prose_when_prose_exists(tmp_path, monkeypatch):
    rid = _ws_with_machine_transcript(tmp_path, monkeypatch)
    ws = Workspace.open(tmp_path / "ws")
    from kairo.models import Form

    (ws.root / f"references/{rid}/prose.md").write_text("已有文稿")
    m = ws.read_manifest(rid)
    m.forms.append(
        Form(
            role="prose",
            location=f"references/{rid}/prose.md",
            hash="x",
            origin="normalize-from:x",
        )
    )
    ws.write_manifest(rid, m)
    r = _client(tmp_path).get(f"/w/ws/ref/{rid}")
    assert f'hx-post="/w/ws/ref/{rid}/prose"' not in r.text


def test_post_prose_starts_task(tmp_path, monkeypatch):
    """POST 启动 prose 任务(子进程);KAIRO_STUB 下最终写出 prose.md。"""
    import os
    import time

    rid = _ws_with_machine_transcript(tmp_path, monkeypatch)
    # 子进程继承环境;确保 stub
    monkeypatch.setenv("KAIRO_STUB", "1")
    os.environ["KAIRO_STUB"] = "1"
    r = _client(tmp_path).post(f"/w/ws/ref/{rid}/prose")
    assert r.status_code == 200
    assert "step/" in r.text and "stream" in r.text  # 任务区片段
    # 等子进程写完
    prose = tmp_path / "ws" / "references" / rid / "prose.md"
    for _ in range(50):
        if prose.is_file():
            break
        time.sleep(0.1)
    assert prose.is_file(), "prose.md should be written by kairo prose subprocess"
    assert "STUB TRANSCRIPT" in prose.read_text()


def test_post_prose_rejects_ineligible(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "note.txt"
    src.write_text("人给")
    rid = Workspace.open(tmp_path / "ws").add([src])
    r = _client(tmp_path).post(f"/w/ws/ref/{rid}/prose")
    assert r.status_code == 400


def test_workspace_view_has_copy_checkbox(tmp_path):
    """#64:添加参考对话框暴露 copy 选项。"""
    Workspace.init(tmp_path / "ws", topic="t")
    r = _client(tmp_path).get("/w/ws")
    assert r.status_code == 200
    assert 'name="copy"' in r.text
    assert "复制到工作区" in r.text or "Copy into workspace" in r.text


def test_add_ref_by_path_with_copy(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "note.txt"
    src.write_text("要拷贝的内容")
    r = _client(tmp_path).post("/w/ws/ref", data={"path": str(src), "copy": "1"})
    assert r.status_code == 200
    ws = Workspace.open(tmp_path / "ws")
    rid = ws.list_reference_ids()[0]
    loc = ws.read_manifest(rid).forms[0].location
    assert "uploads" in loc
    p = ws.root / loc
    assert p.is_file()
    assert p.read_text() == "要拷贝的内容"


def test_rename_does_not_change_location_after_web_copy(tmp_path):
    """#64:改显示名不动 form.location。"""
    Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "note.txt"
    src.write_text("x")
    _client(tmp_path).post("/w/ws/ref", data={"path": str(src), "copy": "1"})
    ws = Workspace.open(tmp_path / "ws")
    rid = ws.list_reference_ids()[0]
    before = [f.location for f in ws.read_manifest(rid).forms]
    r = _client(tmp_path).post(f"/w/ws/ref/{rid}/title", data={"title": "新显示名"})
    assert r.status_code == 200
    assert "新显示名" in r.text
    man = Workspace.open(tmp_path / "ws").read_manifest(rid)
    assert man.title == "新显示名"
    assert [f.location for f in man.forms] == before


def test_add_ref_directory_creates_one_multiform(tmp_path):
    """#67:Web 添加参考 + 目录 → 一条 stream,多 forms。"""
    Workspace.init(tmp_path / "ws", topic="t")
    d = tmp_path / "能源讨论"
    d.mkdir()
    (d / "a.m4a").write_bytes(b"a")
    (d / "b.png").write_bytes(b"b")
    r = _client(tmp_path).post("/w/ws/ref", data={"path": str(d)})
    assert r.status_code == 200
    ws = Workspace.open(tmp_path / "ws")
    ids = ws.list_reference_ids()
    assert len(ids) == 1
    man = ws.read_manifest(ids[0])
    assert man.source_class == "stream"
    assert man.title == "能源讨论"
    assert len(man.forms) == 2


def test_add_ref_directory_with_copy(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    d = tmp_path / "pack"
    d.mkdir()
    (d / "x.txt").write_text("hello")
    r = _client(tmp_path).post("/w/ws/ref", data={"path": str(d), "copy": "1"})
    assert r.status_code == 200
    ws = Workspace.open(tmp_path / "ws")
    rid = ws.list_reference_ids()[0]
    loc = ws.read_manifest(rid).forms[0].location
    assert loc.startswith(f"references/{rid}/")
    assert (ws.root / loc).read_text() == "hello"
