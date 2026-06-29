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
    assert "New workspace" in r.text


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


def test_target_doc_has_export_button(tmp_path, monkeypatch):
    # 产物文档(target)阅读区头部带导出 PDF 按钮 —— 两条渲染路径都要有
    _ws_with_step(tmp_path, monkeypatch)
    c = TestClient(create_app(tmp_path))
    # 路径一:点正文行 → /doc
    r = c.get("/w/ws/doc", params={"path": "understanding.md"})
    assert r.status_code == 200
    assert "doc-export" in r.text and "kairoPrintDoc()" in r.text
    # 路径二:选中产物 → /target 的 OOB 预览
    r = c.get("/w/ws/target", params={"path": "assessment.md"})
    assert r.status_code == 200
    assert "doc-export" in r.text and "kairoPrintDoc()" in r.text


def test_ref_doc_has_no_export_button(tmp_path, monkeypatch):
    # 参考(非产物)不显示导出按钮:导出仅面向 understanding/assessment
    _ws_with_step(tmp_path, monkeypatch)
    rid = _make_voice_ref(tmp_path, with_digest=True)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert "doc-export" not in r.text


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
    assert "Overview" in r.text  # 顶部返回总览组件
    assert ">References<" in r.text and ">Corpus<" in r.text
    assert "nav-group-corpus" in r.text
    ref_seg, corpus_seg = r.text.split(">Corpus<", 1)
    assert "baseline" in corpus_seg  # corpus 在基线组
    assert "baseline" not in ref_seg.split(">References<", 1)[-1]


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
    # 选 stream(无 digest)→ 右栏列形态(人读标签);OOB 把主形态 transcript 渲染进 #reader
    _ws_with_step(tmp_path, monkeypatch)
    rid = _make_voice_ref(tmp_path)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    # 右栏元信息:人读标签 转写 / 音频
    assert 'class="meta"' in r.text and "Transcript" in r.text and "Audio" in r.text
    # OOB:中间预览画布带正文
    assert 'id="reader"' in r.text and 'hx-swap-oob="true"' in r.text
    assert "落地优先级讨论" in r.text


def test_ref_meta_has_copy_button_no_origin(tmp_path, monkeypatch):
    # 形态表:去掉 origin 列,location 改为可复制按钮,预览不带箭头,整行可点
    _ws_with_step(tmp_path, monkeypatch)
    rid = _make_voice_ref(tmp_path)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert "copy-btn" in r.text and "data-copy=" in r.text  # 复制路径按钮
    assert "mf-origin" not in r.text  # 不再有 origin 列
    assert "Preview →" not in r.text and ">Preview</span>" in r.text  # 预览无箭头(span)
    assert 'class="is-prev ' in r.text or 'is-prev"' in r.text  # 整行可点


def test_external_txt_transcript_previewable(tmp_path, monkeypatch):
    # transcript 是 workspace 外的绝对路径 .txt → 仍可预览(纯文本保留换行)
    _ws_with_step(tmp_path, monkeypatch)
    ext = tmp_path / "ext"
    ext.mkdir()
    txt = ext / "260617.txt"
    txt.write_text("第一句话\n第二句话")
    rid = "2026-06-26-ext"
    refdir = tmp_path / "ws" / "references" / rid
    refdir.mkdir(parents=True)
    (refdir / "manifest.yaml").write_text(
        f"id: {rid}\n"
        "title: 外部转写\n"
        "class: stream\n"
        "forms:\n"
        "- role: transcript\n"
        f"  location: {txt}\n"
        "  hash: aa\n"
        "  origin: added\n"
    )
    c = TestClient(create_app(tmp_path))
    r = c.get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert ">Preview</span>" in r.text  # 外部 txt 可预览
    assert "第一句话" in r.text  # OOB 自动预览主形态
    # form 端点直取正文(纯文本 → doc-plain 保留换行)
    fr = c.get(f"/w/ws/ref/{rid}/form/0")
    assert fr.status_code == 200 and "doc-plain" in fr.text
    assert "第一句话" in fr.text and "第二句话" in fr.text


def test_ref_form_endpoint_guards(tmp_path, monkeypatch):
    # form key 越界 / 非法 → 404;不可借此读任意路径
    _ws_with_step(tmp_path, monkeypatch)
    rid = _make_voice_ref(tmp_path)
    c = TestClient(create_app(tmp_path))
    assert c.get(f"/w/ws/ref/{rid}/form/0").status_code == 404  # audio(.m4a)不可预览
    assert c.get(f"/w/ws/ref/{rid}/form/1").status_code == 200  # transcript(.md)
    assert c.get(f"/w/ws/ref/{rid}/form/9").status_code == 404  # 越界
    assert c.get(f"/w/ws/ref/{rid}/form/x").status_code == 404  # 非整数
    assert c.get("/w/ws/ref/nope/form/0").status_code == 404  # ref 不存在


def test_stream_ref_surfaces_digest(tmp_path, monkeypatch):
    # digest.md 不在 manifest.forms,但磁盘存在 → 补入元信息(标签 摘要)
    _ws_with_step(tmp_path, monkeypatch)
    rid = _make_voice_ref(tmp_path, with_digest=True)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert "Digest" in r.text and "digest.md" in r.text


def test_digest_is_default_preview(tmp_path, monkeypatch):
    # 有 digest 时,默认主预览是 digest(而非 transcript)
    _ws_with_step(tmp_path, monkeypatch)
    rid = _make_voice_ref(tmp_path, with_digest=True)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    # OOB 阅读区渲染的是 digest 正文,而非 transcript
    assert "核心结论一二三" in r.text  # digest 内容
    assert "落地优先级讨论" not in r.text  # transcript 未被默认渲染
    # digest 行高亮(预览 key=digest)
    assert 'hx-get="/w/ws/ref/' + rid + '/form/digest" hx-target="#reader"' in r.text


def test_corpus_ref_has_no_inline_preview(tmp_path, monkeypatch):
    # corpus 形态为目录/外部路径,无可内联的 md → 中间给提示而非空白
    ws = _ws_with_step(tmp_path, monkeypatch)
    _add_corpus_dir(tmp_path, ws)
    rid = next(r for r in ws.list_reference_ids() if "baseline" in r)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert 'class="meta"' in r.text and "no inline-previewable" in r.text


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


def test_ref_view_has_attach_entry(tmp_path):
    from kairo.workspace import Workspace
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.txt"
    a.write_text("x")
    rid = ws.add([a])
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert f'/w/ws/ref/{rid}/attach' in r.text
    assert 'type="file"' in r.text


def test_image_attachment_is_previewable_and_served(tmp_path):
    # 图片附件应可预览:form 端点返回 <img>,file 端点供原始字节
    from kairo.workspace import Workspace
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.txt"
    a.write_text("转写")
    rid = ws.add([a])
    # 真实 attach 会把图片拷进 ref 目录(相对 location,落在 workspace 内)
    img = ws.references_dir() / rid / "board.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
    ws.add([img], ref_id=rid)
    c = TestClient(create_app(tmp_path))
    # 该图片 form 在元信息里标为可预览
    r = c.get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    # 找到图片 form 的 key(audio/transcript 之后,这里 a.txt=0, board.png=1)
    img_key = "1"
    fr = c.get(f"/w/ws/ref/{rid}/form/{img_key}")
    assert fr.status_code == 200
    assert "<img" in fr.text and f"/ref/{rid}/file/{img_key}" in fr.text
    # file 端点返回原始字节
    fb = c.get(f"/w/ws/ref/{rid}/file/{img_key}")
    assert fb.status_code == 200
    assert fb.content.startswith(b"\x89PNG")


def test_ref_form_file_rejects_bad_key(tmp_path):
    from kairo.workspace import Workspace
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.txt"
    a.write_text("x")
    rid = ws.add([a])
    c = TestClient(create_app(tmp_path))
    assert c.get(f"/w/ws/ref/{rid}/file/99").status_code == 404
    assert c.get(f"/w/ws/ref/{rid}/file/abc").status_code == 404


def test_digest_listed_first_and_emphasized(tmp_path, monkeypatch):
    # digest 是目的产物:应排在 forms 最前,并带 is-primary 强调类
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    from kairo.workspace import Workspace
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("会议内容")
    ws.add([tmp_path / "m.txt"])
    step(ws, select_provider())  # 产出 transcript + digest
    rid = ws.list_reference_ids()[0]
    html = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}").text
    # digest 行(/form/digest)出现在第一个普通 form(/form/0)之前
    assert "/form/digest" in html and "/form/0" in html
    assert html.index("/form/digest") < html.index("/form/0")
    assert "is-primary" in html
