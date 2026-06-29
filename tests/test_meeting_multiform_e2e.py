from kairo.workspace import Workspace


def test_meeting_audio_plus_doc_one_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider

    ws = Workspace.init(tmp_path / "ws", topic="t")
    audio = tmp_path / "talk.m4a"
    audio.write_bytes(b"\x00\x01")
    rid = ws.add([audio])
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    ws.add([pdf], ref_id=rid)
    img = tmp_path / "board.png"
    img.write_bytes(b"\x89PNG\r\n")
    ws.add([img], ref_id=rid)
    step(ws, select_provider())
    # 一条 ref 一份 digest;transcript + source_text 都派生
    man = ws.read_manifest(rid)
    roles = sorted(f.role for f in man.forms)
    assert "transcript" in roles and "source_text" in roles and "attachment" in roles
    assert (tmp_path / "ws" / "references" / rid / "digest.md").is_file()
