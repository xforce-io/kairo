import datetime

import yaml

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


# ---- #13:源分层 class(corpus 基线 / stream 观测) ----


def test_add_default_class_is_stream(tmp_path):
    """不指定 → 默认 stream(会议流)。"""
    ws = Workspace.init(tmp_path)
    f = tmp_path / "meeting.txt"
    f.write_text("会议正文")
    rid = ws.add([f])
    assert ws.read_manifest(rid).source_class == "stream"


def test_add_corpus_class_set(tmp_path):
    """--corpus 路径:source_class='corpus' → manifest 记 corpus。"""
    ws = Workspace.init(tmp_path)
    f = tmp_path / "whitepaper.md"
    f.write_text("产品白皮书")
    rid = ws.add([f], source_class="corpus")
    assert ws.read_manifest(rid).source_class == "corpus"


def test_manifest_class_yaml_key_is_class(tmp_path):
    """yaml 落盘键为 `class`(贴合概念命名)。"""
    ws = Workspace.init(tmp_path)
    f = tmp_path / "wp.md"
    f.write_text("白皮书")
    rid = ws.add([f], source_class="corpus")
    raw = (ws.references_dir() / rid / "manifest.yaml").read_text()
    assert "class: corpus" in raw


def test_legacy_manifest_without_class_defaults_stream(tmp_path):
    """旧 manifest 无 class 字段 → 默认 stream(向后兼容)。"""
    ws = Workspace.init(tmp_path)
    f = tmp_path / "m.txt"
    f.write_text("x")
    rid = ws.add([f])
    mpath = ws.references_dir() / rid / "manifest.yaml"
    data = yaml.safe_load(mpath.read_text())
    data.pop("class", None)  # 模拟旧版无该字段
    mpath.write_text(yaml.safe_dump(data, allow_unicode=True))
    assert ws.read_manifest(rid).source_class == "stream"


# ---- #24:目录摄入(corpus 目录指针) ----


def test_add_dir_corpus_creates_single_tree_form(tmp_path):
    """add <dir> --corpus → 单条 reference,corpus_tree form,hash=tree_hash。"""
    import datetime as _dt

    from kairo import corpus

    ws = Workspace.init(tmp_path)
    d = tmp_path / "corpus_docs"
    (d / "平台").mkdir(parents=True)
    (d / "平台" / "术语表.md").write_text("灵犀系统")
    (d / "方法论.md").write_text("评估")
    rid = ws.add([d], source_class="corpus")
    assert rid == f"{_dt.date.today().isoformat()}-corpus_docs"
    man = ws.read_manifest(rid)
    assert man.source_class == "corpus"
    assert len(man.forms) == 1
    assert man.forms[0].role == "corpus_tree"
    assert man.forms[0].hash == corpus.tree_hash(d)
    assert man.forms[0].origin == "added"


def test_add_dir_stream_creates_multiform_ref(tmp_path):
    """#67:add <dir> 默认 stream → 一条多形态 ref(非报错、非 corpus_tree)。"""
    import datetime as _dt
    from pathlib import Path

    ws = Workspace.init(tmp_path / "ws")
    d = tmp_path / "能源讨论"  # 在 workspace 外,模拟 Downloads 夹
    d.mkdir()
    (d / "语音A.m4a").write_bytes(b"audio-a")
    (d / "语音B.m4a").write_bytes(b"audio-b")
    (d / "board.png").write_bytes(b"\x89PNG")
    (d / ".DS_Store").write_bytes(b"skip")
    (d / "readme.unknownext").write_text("skip unknown")
    rid = ws.add([d])
    assert rid == f"{_dt.date.today().isoformat()}-能源讨论"
    man = ws.read_manifest(rid)
    assert man.source_class == "stream"
    assert man.title == "能源讨论"
    roles = sorted(f.role for f in man.forms)
    assert roles == ["attachment", "audio", "audio"]
    assert len(man.forms) == 3
    # 外置指针
    assert all(Path(f.location).is_absolute() for f in man.forms)


def test_add_dir_stream_copy_into_ref(tmp_path):
    """#67:目录 + copy → 逐文件进 references/<id>/。"""
    ws = Workspace.init(tmp_path / "ws")
    d = tmp_path / "pack"
    d.mkdir()
    (d / "a.m4a").write_bytes(b"a")
    (d / "b.png").write_bytes(b"b")
    rid = ws.add([d], copy=True)
    man = ws.read_manifest(rid)
    assert man.source_class == "stream"
    assert len(man.forms) == 2
    for f in man.forms:
        p = ws.root / f.location
        assert p.is_file()
        assert p.parent == ws.references_dir() / rid
    # title 改名不改 location
    before = [f.location for f in man.forms]
    ws.set_title(rid, "新名")
    assert [f.location for f in ws.read_manifest(rid).forms] == before


def test_add_dir_stream_empty_errors(tmp_path):
    from kairo.workspace import AddError

    ws = Workspace.init(tmp_path)
    d = tmp_path / "empty"
    d.mkdir()
    (d / ".DS_Store").write_bytes(b"x")
    try:
        ws.add([d])
    except AddError as e:
        assert "没有可添加" in str(e)
    else:
        raise AssertionError("应抛 AddError")
