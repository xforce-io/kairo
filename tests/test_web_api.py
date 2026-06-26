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


def test_stream_ref_inlines_markdown_transcript(tmp_path, monkeypatch):
    # whisper 式 stream:transcript.md 在 workspace 内 → 详情页内联渲染正文
    ws = _ws_with_step(tmp_path, monkeypatch)
    rid = "2026-06-26-voice"
    refdir = tmp_path / "ws" / "references" / rid
    refdir.mkdir(parents=True)
    (refdir / "transcript.md").write_text("# 语音纪要\n\n落地优先级讨论")
    (refdir / "manifest.yaml").write_text(
        "id: 2026-06-26-voice\n"
        "title: 语音\n"
        "class: stream\n"
        "forms:\n"
        "- role: transcript\n"
        f"  location: references/{rid}/transcript.md\n"
        "  hash: deadbeef\n"
        "  origin: whisper\n"
    )
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert "ref-preview" in r.text and "落地优先级讨论" in r.text


def test_corpus_ref_has_no_inline_preview(tmp_path, monkeypatch):
    # corpus 形态为目录/外部路径,无可内联的 md → 给出提示而非空白
    ws = _ws_with_step(tmp_path, monkeypatch)
    _add_corpus_dir(tmp_path, ws)
    rid = next(r for r in ws.list_reference_ids() if "baseline" in r)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert "无可内联预览" in r.text and "ref-preview" not in r.text
