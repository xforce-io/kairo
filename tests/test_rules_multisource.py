# tests/test_rules_multisource.py
from kairo.workspace import Workspace

def test_multiple_documents_each_get_source_text(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = Workspace.init(tmp_path / "ws", topic="t")
    d1 = tmp_path / "deck.pdf"
    d1.write_bytes(b"%PDF-1.4 a")
    d2 = tmp_path / "notes.pdf"
    d2.write_bytes(b"%PDF-1.4 b")
    rid = ws.add([d1])
    ws.add([d2], ref_id=rid)               # 同一 ref 两个 document
    step(ws, select_provider())
    man = ws.read_manifest(rid)
    st_locs = sorted(f.location for f in man.forms if f.role == "source_text")
    assert len(st_locs) == 2, st_locs      # 两份各自派生
    assert any("deck" in loc for loc in st_locs) and any("notes" in loc for loc in st_locs)


def test_same_stem_different_ext_no_collision(tmp_path, monkeypatch):
    """deck.pdf + deck.pptx 同茎名但不同扩展名,不得映射到同一 keyed 产物(无碰撞)。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "deck.pdf"
    a.write_bytes(b"%PDF-1.4 a")
    rid = ws.add([a])
    b = tmp_path / "deck.pptx"
    b.write_bytes(b"PK\x03\x04 b")
    ws.add([b], ref_id=rid)
    step(ws, select_provider())
    man = ws.read_manifest(rid)
    locs = sorted(f.location for f in man.forms if f.role == "source_text")
    assert len(set(locs)) == 2, f"两个不同产物,无碰撞: {locs}"


def test_same_basename_from_different_paths_gets_unique_keyed_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.listen_read import pair_audio_transcripts
    from kairo.provider import select_provider

    ws = Workspace.init(tmp_path / "ws", topic="t")
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    a1, a2 = left / "meeting.wav", right / "meeting.wav"
    a1.write_bytes(b"RIFF-left")
    a2.write_bytes(b"RIFF-right")
    rid = ws.add([a1], role="audio")
    ws.add([a2], ref_id=rid, role="audio")
    step(ws, select_provider())

    manifest = ws.read_manifest(rid)
    locations = [f.location for f in manifest.forms if f.role == "transcript"]
    assert len(locations) == len(set(locations)) == 2
    assert all(pair.linked for pair in pair_audio_transcripts(manifest.forms))


def test_single_then_second_document_both_derived(tmp_path, monkeypatch):
    # 迁移场景:先加 1 个 document(派生 legacy source_text.md),再加第 2 个
    # → 两个都应各有 source_text 派生,且不重复(第一个不被重派生成 keyed)
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    from kairo.workspace import Workspace
    ws = Workspace.init(tmp_path / "ws", topic="t")
    d1 = tmp_path / "deck.pdf"
    d1.write_bytes(b"%PDF-1.4 a")
    rid = ws.add([d1])
    step(ws, select_provider())                 # 单源 → legacy source_text.md
    d2 = tmp_path / "notes.pdf"
    d2.write_bytes(b"%PDF-1.4 b")
    ws.add([d2], ref_id=rid)                     # 追加第二个 → 多源
    step(ws, select_provider())
    man = ws.read_manifest(rid)
    st = [f for f in man.forms if f.role == "source_text"]
    locs = sorted(f.location for f in st)
    assert len(st) == 2, locs                    # 恰好两份:不丢第二个、不重复第一个
    # 第二个文档确有独立派生
    assert any("notes" in loc for loc in locs)
