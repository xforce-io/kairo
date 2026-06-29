# tests/test_workspace_append.py
from kairo.workspace import Workspace


def test_add_appends_to_existing_ref_without_clobber(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.txt"
    a.write_text("aaa")
    rid = ws.add([a])                      # 建一条 ref(transcript 兜底 role)
    b = tmp_path / "b.png"
    b.write_bytes(b"\x89PNG\r\n")
    rid2 = ws.add([b], ref_id=rid)         # 追加图片到同一条
    assert rid2 == rid
    man = ws.read_manifest(rid)
    roles = sorted(f.role for f in man.forms)
    assert roles == ["attachment", "transcript"]  # 原有未被覆盖


def test_add_dedups_by_location(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.txt"
    a.write_text("aaa")
    rid = ws.add([a])
    ws.add([a], ref_id=rid)                # 同一文件再加 → 不重复
    man = ws.read_manifest(rid)
    assert sum(1 for f in man.forms if f.location == str(a)) == 1
