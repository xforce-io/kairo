import datetime

from kairo.workspace import Workspace


def test_add_text_derives_id_and_manifest(tmp_path):
    ws = Workspace.init(tmp_path)
    src = tmp_path / "meeting.txt"
    src.write_text("会议实录正文")
    ref_id = ws.add([src])
    today = datetime.date.today().isoformat()
    assert ref_id == f"{today}-meeting"
    man = ws.read_manifest(ref_id)
    assert man.forms[0].role == "transcript"
    assert man.forms[0].hash
    assert man.forms[0].origin == "added"


def test_add_audio_guesses_audio_role(tmp_path):
    ws = Workspace.init(tmp_path)
    src = tmp_path / "rec.m4a"
    src.write_bytes(b"\x00fake audio")
    ref_id = ws.add([src])
    man = ws.read_manifest(ref_id)
    assert man.forms[0].role == "audio"


def test_add_multiple_forms_share_one_id(tmp_path):
    ws = Workspace.init(tmp_path)
    a = tmp_path / "rec.m4a"
    a.write_bytes(b"audio")
    n = tmp_path / "notes.md"
    n.write_text("笔记")
    ref_id = ws.add([a, n])
    man = ws.read_manifest(ref_id)
    assert [f.role for f in man.forms] == ["audio", "transcript"]


def test_add_explicit_role_overrides_guess(tmp_path):
    ws = Workspace.init(tmp_path)
    doc = tmp_path / "whitepaper.md"
    doc.write_text("产品白皮书")
    ref_id = ws.add([doc], role="source_text")
    man = ws.read_manifest(ref_id)
    assert man.forms[0].role == "source_text"


def test_add_chinese_filename_keeps_readable_id(tmp_path):
    """#9:中文文件名应产生可读 id,不退化为空 slug。"""
    ws = Workspace.init(tmp_path)
    f = tmp_path / "会议实录.txt"
    f.write_text("会议内容")
    ref_id = ws.add([f])
    today = datetime.date.today().isoformat()
    assert ref_id != f"{today}-"  # 不退化为尾部空 slug
    assert "会议实录" in ref_id  # 中文保留


def test_add_two_chinese_filenames_no_collision(tmp_path):
    """#9:两个不同中文文件名不能产生相同 id(否则互相覆盖)。"""
    ws = Workspace.init(tmp_path)
    a = tmp_path / "会议甲.txt"
    a.write_text("甲")
    b = tmp_path / "会议乙.txt"
    b.write_text("乙")
    assert ws.add([a]) != ws.add([b])
