from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.workspace import Workspace


def _client(root):
    return TestClient(create_app(root))


def test_healthz(tmp_path):
    r = _client(tmp_path).get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_dashboard_lists_workspaces(tmp_path):
    Workspace.init(tmp_path / "alpha-ws", topic="阿尔法")
    Workspace.init(tmp_path / "beta-ws", topic="贝塔")
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "alpha-ws" in r.text and "beta-ws" in r.text
    assert "阿尔法" in r.text


def test_dashboard_shows_create_workspace_entry(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert 'hx-post="/workspaces"' in r.text
    assert "新建 workspace" in r.text


def test_dashboard_card_link_is_url_encoded(tmp_path):
    # topic 允许空格 → 目录名含空格;卡片链接必须 url-encode,否则点击 404
    Workspace.init(tmp_path / "v1 draft", topic="v1 draft")
    c = _client(tmp_path)
    r = c.get("/")
    assert 'href="/w/v1%20draft"' in r.text
    assert 'href="/w/v1 draft"' not in r.text
    # 编码后的路径可正常打开
    assert c.get("/w/v1 draft").status_code == 200


def _ws_with_step(root, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = Workspace.init(root / "ws", topic="主题")
    (root / "m.txt").write_text("王强会议:落地优先级")
    ws.add([root / "m.txt"])
    step(ws, select_provider())
    return ws


def test_workspace_view_lists_targets_and_refs(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    r = TestClient(create_app(tmp_path)).get("/w/ws")
    assert r.status_code == 200
    assert "understanding.md" in r.text and "assessment.md" in r.text


def test_workspace_view_404_for_unknown(tmp_path):
    r = TestClient(create_app(tmp_path)).get("/w/nope")
    assert r.status_code == 404


def test_doc_renders_markdown(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    c = TestClient(create_app(tmp_path))
    r = c.get("/w/ws/doc", params={"path": "understanding.md"})
    assert r.status_code == 200
    assert "落地优先级" in r.text  # stub 把正文带进 understanding


def test_doc_rejects_path_traversal(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    c = TestClient(create_app(tmp_path))
    assert c.get("/w/ws/doc", params={"path": "../m.txt"}).status_code == 404
    assert c.get("/w/ws/doc", params={"path": "/etc/hosts"}).status_code == 404


def test_ref_detail_shows_forms(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    ref_id = next(iter(__import__("os").listdir(tmp_path / "ws" / "references")))
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{ref_id}")
    assert r.status_code == 200 and ("transcript" in r.text or "digest" in r.text)


def _add_corpus_dir(root, ws):
    cdir = root / "baseline"
    cdir.mkdir()
    (cdir / "a.md").write_text("# 基线文档")
    ws.add([cdir], source_class="corpus")


def test_workspace_view_splits_stream_and_corpus(tmp_path, monkeypatch):
    # 参考组只放 stream;corpus 单独成『基线』组置底,且不混进参考段
    ws = _ws_with_step(tmp_path, monkeypatch)
    _add_corpus_dir(tmp_path, ws)
    r = TestClient(create_app(tmp_path)).get("/w/ws")
    assert r.status_code == 200
    assert "返回总览" in r.text  # 顶部返回总览组件
    assert ">参考<" in r.text and ">基线<" in r.text
    assert "nav-group-corpus" in r.text
    ref_seg, corpus_seg = r.text.split("基线", 1)
    assert "baseline" in corpus_seg  # corpus 在基线组
    assert "baseline" not in ref_seg.split(">参考<", 1)[-1]  # 不出现在参考组


def _make_voice_ref(root, rid="2026-06-26-voice", with_digest=False):
    """造一条 whisper 式 stream:transcript.md(+可选 digest.md)在 workspace 内。"""
    refdir = root / "ws" / "references" / rid
    refdir.mkdir(parents=True)
    (refdir / "transcript.md").write_text("# 语音纪要\n\n落地优先级讨论")
    if with_digest:
        (refdir / "digest.md").write_text("# 折叠摘要\n\n核心结论一二三")
    (refdir / "manifest.yaml").write_text(
        f"id: {rid}\n"
        "title: 语音\n"
        "class: stream\n"
        "forms:\n"
        "- role: audio\n"
        "  location: data/语音.m4a\n"
        "  hash: aa\n"
        "  origin: added\n"
        "- role: transcript\n"
        f"  location: references/{rid}/transcript.md\n"
        "  hash: bb\n"
        "  origin: whisper\n"
    )
    return rid


def test_stream_ref_meta_and_oob_preview(tmp_path, monkeypatch):
    # 选 stream → 右栏元信息列形态;OOB 把主形态 transcript 渲染进中间 #reader
    _ws_with_step(tmp_path, monkeypatch)
    rid = _make_voice_ref(tmp_path)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    # 右栏元信息:列出 audio / transcript 形态
    assert 'class="meta"' in r.text and "transcript" in r.text and "audio" in r.text
    # OOB:中间预览画布带正文
    assert 'id="reader"' in r.text and 'hx-swap-oob="true"' in r.text
    assert "落地优先级讨论" in r.text


def test_stream_ref_surfaces_digest(tmp_path, monkeypatch):
    # digest.md 不在 manifest.forms,但磁盘存在 → 作为可预览形态补入元信息
    _ws_with_step(tmp_path, monkeypatch)
    rid = _make_voice_ref(tmp_path, with_digest=True)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert "digest" in r.text and "digest.md" in r.text


def test_corpus_ref_has_no_inline_preview(tmp_path, monkeypatch):
    # corpus 形态为目录/外部路径,无可内联的 md → 中间给提示而非空白
    ws = _ws_with_step(tmp_path, monkeypatch)
    _add_corpus_dir(tmp_path, ws)
    rid = next(r for r in ws.list_reference_ids() if "baseline" in r)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert 'class="meta"' in r.text and "无可内联预览" in r.text


def test_target_meta_shows_status_and_previews(tmp_path, monkeypatch):
    # 选产物 → 右栏出状态元信息;OOB 预览正文进 #reader
    _ws_with_step(tmp_path, monkeypatch)
    r = TestClient(create_app(tmp_path)).get("/w/ws/target", params={"path": "understanding.md"})
    assert r.status_code == 200
    assert "understanding.md" in r.text
    assert 'id="reader"' in r.text and 'hx-swap-oob="true"' in r.text
    assert "落地优先级" in r.text  # stub 正文进 understanding


def test_target_meta_404_for_unknown(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    r = TestClient(create_app(tmp_path)).get("/w/ws/target", params={"path": "nope.md"})
    assert r.status_code == 404
