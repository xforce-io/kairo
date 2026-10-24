from kairo.models import State
from kairo.rules import DigestRule
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


def test_s5_default_combo_body_plus_image_reflected(tmp_path):
    """#453 S5 默认组合：transcript + attachment(图)，不是双正文。"""
    ws = Workspace.init(tmp_path / "ws", topic="t")
    talk = tmp_path / "talk.txt"
    talk.write_text("会议决定下周完成分流上线。")
    rid = ws.add([talk])
    img = tmp_path / "board.png"
    img.write_bytes(b"\x89PNG\r\n")
    ws.add([img], ref_id=rid)
    captured = []

    class Probe:
        name = "probe"
        model = "probe"
        supports_read_dirs = True

        def run(self, config, signal=None):
            captured.append(config)
            dest = config.artifact_dir / (config.artifact or "digest.md")
            dest.write_text("纪要：下周分流上线。依据附件 board.png 的现场白板。")
            return None

    DigestRule(ws, Probe()).discover()[0].run(State())
    man = ws.read_manifest(rid)
    roles = {f.role for f in man.forms}
    assert "transcript" in roles and "attachment" in roles
    assert "source_text" not in roles
    assert captured, "digest provider was not called"
    assert "| 必读 | transcript |" in captured[0].context
    assert "| 按需 | attachment |" in captured[0].context
    assert "source_text" not in captured[0].context
    digest = (tmp_path / "ws" / "references" / rid / "digest.md").read_text()
    assert "board.png" in digest
