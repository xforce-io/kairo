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
