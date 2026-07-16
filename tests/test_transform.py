"""二进制/结构化源摄入(#15/#88):TransformRule + markitdown 后端。

与 ASR 同构:document(docx/pptx/xlsx/pdf)→ source_text Form。
#88:corpus 也抽 source_text(可读/可预览),仍不 digest/fold;ASR 对 corpus 仍跳过。
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


def test_transform_discovers_corpus_document(tmp_path):
    """#88:corpus 二进制也 discover doc2text(可读正文;不 fold)。"""
    ws = Workspace.init(tmp_path)
    rid = _add_doc(ws, source_class="corpus")
    items = TransformRule(ws, **DOC_BACKEND).discover()
    assert [it.key for it in items] == [f"references/{rid}/source_text.md"]


def test_transform_skips_corpus_audio_asr(tmp_path):
    """#88:corpus 仍不跑 ASR(只开放 document→source_text)。"""
    ws = Workspace.init(tmp_path)
    audio = ws.root / "meet.m4a"
    audio.write_bytes(b"fake-audio")
    ws.add([audio], source_class="corpus", role="audio")
    assert TransformRule(ws, **ASR_BACKEND).discover() == []


# ---- run:真实 markitdown 转换(markitdown 为硬依赖,缺失即失败) ----


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


# ---- #88:corpus 二进制 E2E(add → transform → collect;不 digest) ----


@pytest.mark.parametrize("name", ["sample.pptx", "sample.pdf", "sample.docx", "sample.xlsx"])
def test_corpus_binary_to_source_text_no_digest(tmp_path, name):
    """基线 pptx/pdf/docx/xlsx → source_text 落盘+manifest;不产 digest;collect 可见正文。"""
    ws = Workspace.init(tmp_path)
    rid = _add_doc(ws, name, source_class="corpus")
    # 转换前:仅 document → 不进 corpus.collect(body_roles 未命中)
    assert rid not in {r.ref_id for r in corpus.collect(ws)}

    state = State()
    items = TransformRule(ws, **DOC_BACKEND).discover()
    assert len(items) == 1
    items[0].run(state)

    out = ws.root / "references" / rid / "source_text.md"
    assert out.is_file() and out.read_text().strip()
    roles = [f.role for f in ws.read_manifest(rid).forms]
    assert "document" in roles and "source_text" in roles
    assert state.products[f"references/{rid}/source_text.md"].status == "ok"

    # 仍不 digest(认识论:基线不 fold)
    assert DigestRule(ws, StubProvider()).discover() == []
    assert not (ws.root / "references" / rid / "digest.md").exists()

    # collect 以 source_text 为 file 型 body;stamp 可读
    refs = {r.ref_id: r for r in corpus.collect(ws)}
    assert rid in refs
    assert refs[rid].kind == "file"
    assert refs[rid].path.resolve() == out.resolve()
    assert corpus.stamp([refs[rid]])  # 非空稳定戳


def test_corpus_binary_step_e2e_no_fold(tmp_path, monkeypatch):
    """完整 step 路径:corpus pptx → source_text;无 digest;stream 仍可 digest。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path)
    crid = _add_doc(ws, "sample.pptx", source_class="corpus")
    stream_txt = tmp_path / "meet.txt"
    stream_txt.write_text("观测会议正文")
    srid = ws.add([stream_txt])

    assert step(ws, StubProvider()) is True

    assert (ws.root / "references" / crid / "source_text.md").is_file()
    assert not (ws.root / "references" / crid / "digest.md").exists()
    assert (ws.root / "references" / srid / "digest.md").is_file()
    assert crid in {r.ref_id for r in corpus.collect(ws)}
    # understanding 不应把 corpus 当 fold 材料键
    state = ws.read_state()
    ts = state.targets.get("understanding.md")
    if ts and ts.folded:
        assert not any(crid in k for k in ts.folded)


def test_corpus_convert_failed_blocks(tmp_path):
    """corpus 损坏二进制 → blocked convert-failed(与 stream 同构)。"""
    ws = Workspace.init(tmp_path)
    bad = ws.root / "broken.docx"
    bad.write_bytes(b"")
    rid = ws.add([bad], source_class="corpus")
    state = State()
    TransformRule(ws, **DOC_BACKEND).discover()[0].run(state)
    ps = state.products[f"references/{rid}/source_text.md"]
    assert ps.status == "blocked" and ps.reason == "convert-failed"
    assert rid not in {r.ref_id for r in corpus.collect(ws)}
