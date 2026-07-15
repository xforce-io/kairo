"""#64: add 统一路径指针 + 可选 copy;title 重命名与 location 正交。"""

from __future__ import annotations

import pytest

from kairo.workspace import AddError, Workspace


def test_add_default_is_path_pointer(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    src = tmp_path / "note.txt"
    src.write_text("hello")
    rid = ws.add([src])
    loc = ws.read_manifest(rid).forms[0].location
    assert loc == str(src)  # 外置绝对路径
    assert not (ws.root / ".kairo" / "uploads").exists() or not any(
        (ws.root / ".kairo" / "uploads").iterdir()
    )


def test_add_copy_materializes_into_uploads(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    src = tmp_path / "rec.m4a"
    src.write_bytes(b"audio-bytes")
    rid = ws.add([src], copy=True)
    form = ws.read_manifest(rid).forms[0]
    loc = form.location
    # 副本在 uploads,且为 workspace 相对路径
    assert loc.startswith(".kairo/uploads/") or "uploads" in loc
    p = ws.root / loc if not loc.startswith("/") else __import__("pathlib").Path(loc)
    if not p.is_absolute():
        p = ws.root / loc
    assert p.is_file()
    assert p.read_bytes() == b"audio-bytes"
    # 源仍在原处
    assert src.is_file()


def test_add_copy_into_existing_ref_dir(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    a = tmp_path / "a.txt"
    a.write_text("aaa")
    rid = ws.add([a])
    b = tmp_path / "b.png"
    b.write_bytes(b"\x89PNG")
    ws.add([b], ref_id=rid, copy=True)
    man = ws.read_manifest(rid)
    locs = [f.location for f in man.forms]
    # 追加 copy 应落在 references/<id>/
    assert any(f"references/{rid}/" in loc or loc.endswith("b.png") for loc in locs)
    copied = [f for f in man.forms if f.role == "attachment" or f.location.endswith("b.png")]
    assert copied
    cp = ws.root / copied[-1].location if not copied[-1].location.startswith("/") else __import__("pathlib").Path(copied[-1].location)
    if not cp.is_absolute():
        cp = ws.root / copied[-1].location
    assert cp.is_file()
    assert cp.parent == ws.references_dir() / rid


def test_add_copy_corpus_dir_still_rejected(tmp_path):
    """基线目录仍禁止整树 copy;#67 只放开 stream 目录多形态。"""
    ws = Workspace.init(tmp_path / "ws")
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "x.md").write_text("x")
    with pytest.raises(AddError, match="基线目录不支持|勿勾选复制"):
        ws.add([d], source_class="corpus", copy=True)


def test_add_stream_dir_with_copy_is_multiform(tmp_path):
    """#67:stream 目录 + copy → 成功,一条 ref。"""
    ws = Workspace.init(tmp_path / "ws")
    d = tmp_path / "能源讨论"
    d.mkdir()
    (d / "a.txt").write_text("a")
    rid = ws.add([d], copy=True)
    man = ws.read_manifest(rid)
    assert man.source_class == "stream"
    assert len(man.forms) == 1


def test_set_title_does_not_change_location_after_copy(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    src = tmp_path / "meeting.txt"
    src.write_text("body")
    rid = ws.add([src], copy=True)
    before = [f.location for f in ws.read_manifest(rid).forms]
    ws.set_title(rid, "全新显示名")
    man = ws.read_manifest(rid)
    assert man.title == "全新显示名"
    assert [f.location for f in man.forms] == before
    assert man.id == rid
    assert (ws.references_dir() / rid).is_dir()


def test_copy_name_collision_gets_suffix(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    (ws.root / ".kairo" / "uploads").mkdir(parents=True)
    (ws.root / ".kairo" / "uploads" / "dup.txt").write_text("old")
    src = tmp_path / "dup.txt"
    src.write_text("new")
    rid = ws.add([src], copy=True)
    loc = ws.read_manifest(rid).forms[0].location
    assert "dup-1.txt" in loc or loc.endswith("dup-1.txt")
