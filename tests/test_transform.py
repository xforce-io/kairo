"""二进制/结构化源摄入(#15):TransformRule + markitdown 后端。

与 ASR 同构:stream 型二进制(docx/pptx/xlsx/pdf)→ source_text Form,下游零改动。
corpus 二进制不处理(fold=False 跳过)。真实转换用提交的 fixture,以 markitdown 可用性 gate。
"""

from pathlib import Path

import pytest

from kairo.models import State
from kairo.rules import TransformRule
from kairo.workspace import Workspace

FIXTURES = Path(__file__).parent / "fixtures"
DOC_BACKEND = dict(consumes=["document"], produces="source_text", backend="markitdown")


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
    """corpus(fold=False)是只读参考层,二进制不派生(与 Normalize/Digest 一致)。"""
    ws = Workspace.init(tmp_path)
    _add_doc(ws, source_class="corpus")
    assert TransformRule(ws, **DOC_BACKEND).discover() == []


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
