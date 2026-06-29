# tests/test_rules_multisource.py
import os
from kairo.workspace import Workspace

def test_multiple_documents_each_get_source_text(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = Workspace.init(tmp_path / "ws", topic="t")
    d1 = tmp_path / "deck.pdf"; d1.write_bytes(b"%PDF-1.4 a")
    d2 = tmp_path / "notes.pdf"; d2.write_bytes(b"%PDF-1.4 b")
    rid = ws.add([d1])
    ws.add([d2], ref_id=rid)               # 同一 ref 两个 document
    step(ws, select_provider())
    man = ws.read_manifest(rid)
    st_locs = sorted(f.location for f in man.forms if f.role == "source_text")
    assert len(st_locs) == 2, st_locs      # 两份各自派生
    assert any("deck" in l for l in st_locs) and any("notes" in l for l in st_locs)


def test_single_then_second_document_both_derived(tmp_path, monkeypatch):
    # 迁移场景:先加 1 个 document(派生 legacy source_text.md),再加第 2 个
    # → 两个都应各有 source_text 派生,且不重复(第一个不被重派生成 keyed)
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    from kairo.workspace import Workspace
    ws = Workspace.init(tmp_path / "ws", topic="t")
    d1 = tmp_path / "deck.pdf"; d1.write_bytes(b"%PDF-1.4 a")
    rid = ws.add([d1])
    step(ws, select_provider())                 # 单源 → legacy source_text.md
    d2 = tmp_path / "notes.pdf"; d2.write_bytes(b"%PDF-1.4 b")
    ws.add([d2], ref_id=rid)                     # 追加第二个 → 多源
    step(ws, select_provider())
    man = ws.read_manifest(rid)
    st = [f for f in man.forms if f.role == "source_text"]
    locs = sorted(f.location for f in st)
    assert len(st) == 2, locs                    # 恰好两份:不丢第二个、不重复第一个
    # 第二个文档确有独立派生
    assert any("notes" in l for l in locs)
