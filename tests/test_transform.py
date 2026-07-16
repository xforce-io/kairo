"""二进制/结构化源摄入(#15) + 基线引用模型(#88):TransformRule + markitdown。

stream:document(docx/pptx/xlsx/pdf)→ source_text Form → digest 管线。
corpus:不跑 Transform;路径指针由 corpus.collect 挂出,Web 预览或系统打开。
"""

from pathlib import Path

import pytest

from kairo import corpus
from kairo.engine import step
from kairo.models import State
from kairo.provider import StubProvider
from kairo.rules import DigestRule, TransformRule
from kairo.workspace import Workspace

FIXTURES = Path(__file__).parent / "fixtures"
DOC_BACKEND = dict(consumes=["document"], produces="source_text", backend="markitdown")
ASR_BACKEND = dict(consumes=["audio"], produces="transcript", backend="asr-stub")


def _add_doc(ws, name="sample.docx", source_class=None):
    """把 fixture 拷进 tmp 工作区并 add(role 由 roles_by_ext 猜为 document)。"""
    src = FIXTURES / name
    dst = ws.root / name
    dst.write_bytes(src.read_bytes())
    return ws.add([dst], source_class=source_class)


# ---- discover / 角色识别 ----


def test_docx_gets_document_role_by_ext(tmp_path):
    ws = Workspace.init(tmp_path)
    rid = _add_doc(ws)
    roles = [f.role for f in ws.read_manifest(rid).forms]
    assert roles == ["document"]


def test_transform_discovers_document_without_source_text(tmp_path):
    ws = Workspace.init(tmp_path)
    rid = _add_doc(ws)
    items = TransformRule(ws, **DOC_BACKEND).discover()
    assert [it.key for it in items] == [f"references/{rid}/source_text.md"]


def test_transform_skips_corpus_binary(tmp_path):
    """#88 引用模型:corpus 不跑 markitdown/doc2text。"""
    ws = Workspace.init(tmp_path)
    _add_doc(ws, source_class="corpus")
    assert TransformRule(ws, **DOC_BACKEND).discover() == []


def test_transform_skips_corpus_audio_asr(tmp_path):
    """corpus 仍不跑 ASR。"""
    ws = Workspace.init(tmp_path)
    audio = ws.root / "meet.m4a"
    audio.write_bytes(b"fake-audio")
    ws.add([audio], source_class="corpus", role="audio")
    assert TransformRule(ws, **ASR_BACKEND).discover() == []


# ---- run:真实 markitdown 转换(stream only) ----


def test_markitdown_converts_docx_to_source_text(tmp_path):
    ws = Workspace.init(tmp_path)
    rid = _add_doc(ws, "sample.docx")
    state = State()
    TransformRule(ws, **DOC_BACKEND).discover()[0].run(state)
    out = ws.root / "references" / rid / "source_text.md"
    assert out.is_file()
    assert "康医通系统" in out.read_text()
    forms = {f.role: f for f in ws.read_manifest(rid).forms}
    assert "source_text" in forms
    assert forms["source_text"].origin.startswith("markitdown-from:")
    assert state.products[f"references/{rid}/source_text.md"].status == "ok"


def test_markitdown_converts_xlsx_preserving_table(tmp_path):
    """xlsx → GFM 表格(表头语义保住,pandoc 做不到)。"""
    ws = Workspace.init(tmp_path)
    rid = _add_doc(ws, "sample.xlsx")
    state = State()
    TransformRule(ws, **DOC_BACKEND).discover()[0].run(state)
    text = (ws.root / "references" / rid / "source_text.md").read_text()
    assert "蛋白质" in text and "|" in text  # GFM 表格


@pytest.mark.parametrize("name", ["sample.pptx", "sample.pdf"])
def test_markitdown_converts_pptx_and_pdf(tmp_path, name):
    ws = Workspace.init(tmp_path)
    rid = _add_doc(ws, name)
    state = State()
    TransformRule(ws, **DOC_BACKEND).discover()[0].run(state)
    assert (ws.root / "references" / rid / "source_text.md").read_text().strip()
    assert "source_text" in [f.role for f in ws.read_manifest(rid).forms]


def test_transform_blocks_convert_failed_on_garbage(tmp_path):
    """损坏二进制(空 docx → BadZipFile)→ blocked: convert-failed(终态),不写假产物。"""
    ws = Workspace.init(tmp_path)
    bad = ws.root / "broken.docx"
    bad.write_bytes(b"")  # 空 docx,markitdown DocxConverter 抛 BadZipFile
    rid = ws.add([bad])
    state = State()
    TransformRule(ws, **DOC_BACKEND).discover()[0].run(state)
    ps = state.products[f"references/{rid}/source_text.md"]
    assert ps.status == "blocked" and ps.reason == "convert-failed"
    assert not (ws.root / "references" / rid / "source_text.md").exists()
    assert "source_text" not in [f.role for f in ws.read_manifest(rid).forms]


def test_transform_blocks_missing_source(tmp_path):
    ws = Workspace.init(tmp_path)
    rid = _add_doc(ws)
    (ws.root / "sample.docx").unlink()  # 源丢失
    state = State()
    TransformRule(ws, **DOC_BACKEND).discover()[0].run(state)
    ps = state.products[f"references/{rid}/source_text.md"]
    assert ps.status == "blocked" and ps.reason == "missing-source"


def test_transform_converges_idempotent(tmp_path):
    ws = Workspace.init(tmp_path)
    _add_doc(ws)
    state = State()
    item = TransformRule(ws, **DOC_BACKEND).discover()[0]
    item.run(state)
    assert item.is_stale(state) is False
    # 已产 source_text → 不再 discover
    assert TransformRule(ws, **DOC_BACKEND).discover() == []


# ---- #88 引用模型:corpus 指针可达,不抽取 ----


@pytest.mark.parametrize("name", ["sample.pptx", "sample.pdf", "sample.docx", "sample.xlsx"])
def test_corpus_binary_pointer_collect_no_transform(tmp_path, name):
    """基线 pptx/pdf 等:不产 source_text/digest;collect 挂原件路径;stamp 可用。"""
    ws = Workspace.init(tmp_path)
    rid = _add_doc(ws, name, source_class="corpus")
    src = (ws.root / name).resolve()

    assert TransformRule(ws, **DOC_BACKEND).discover() == []
    assert DigestRule(ws, StubProvider()).discover() == []
    assert not (ws.root / "references" / rid / "source_text.md").exists()
    assert not (ws.root / "references" / rid / "digest.md").exists()

    refs = {r.ref_id: r for r in corpus.collect(ws)}
    assert rid in refs
    assert refs[rid].kind == "file"
    assert refs[rid].path.resolve() == src
    assert corpus.stamp([refs[rid]])
    section = corpus.reference_section(ws, list(refs.values()))
    assert name in section or str(src) in section


def test_corpus_binary_step_no_source_text(tmp_path, monkeypatch):
    """step 不抽 corpus;stream 仍 digest;collect 仍见基线原件。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path)
    crid = _add_doc(ws, "sample.pptx", source_class="corpus")
    stream_txt = tmp_path / "meet.txt"
    stream_txt.write_text("观测会议正文")
    srid = ws.add([stream_txt])

    assert step(ws, StubProvider()) is True

    assert not (ws.root / "references" / crid / "source_text.md").exists()
    assert not (ws.root / "references" / crid / "digest.md").exists()
    assert (ws.root / "references" / srid / "digest.md").is_file()
    assert crid in {r.ref_id for r in corpus.collect(ws)}
    state = ws.read_state()
    ts = state.targets.get("understanding.md")
    if ts and ts.folded:
        assert not any(crid in k for k in ts.folded)


def test_corpus_document_stamp_changes_on_edit(tmp_path):
    """二进制基线 stamp 跟文件字节走,不 read_text。"""
    ws = Workspace.init(tmp_path)
    rid = _add_doc(ws, "sample.pdf", source_class="corpus")
    ref = next(r for r in corpus.collect(ws) if r.ref_id == rid)
    s0 = corpus.stamp([ref])
    (ws.root / "sample.pdf").write_bytes(b"%PDF-1.4 changed")
    ref2 = next(r for r in corpus.collect(ws) if r.ref_id == rid)
    assert corpus.stamp([ref2]) != s0
