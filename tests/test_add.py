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


def test_add_mp4_guesses_audio_role(tmp_path):
    """会议视频容器(.mp4)默认当 audio,走 ASR,不落到 transcript 兜底。"""
    ws = Workspace.init(tmp_path)
    src = tmp_path / "meeting.mp4"
    src.write_bytes(b"\x00fake video")
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


def test_add_same_stem_sequential_gets_distinct_ids(tmp_path):
    """#81 E1:同 stem 不同路径顺序 add → 两条 ref,互不吞并。"""
    ws = Workspace.init(tmp_path)
    a = tmp_path / "a" / "note.txt"
    b = tmp_path / "b" / "note.txt"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text("CONTENT_A_UNIQUE")
    b.write_text("CONTENT_B_UNIQUE")
    r1, r2 = ws.add([a]), ws.add([b])
    assert r1 != r2
    assert {r1, r2} <= set(ws.list_reference_ids())
    bodies = set()
    for rid in (r1, r2):
        loc = ws.read_manifest(rid).forms[0].location
        p = __import__("pathlib").Path(loc)
        if not p.is_absolute():
            p = ws.root / loc
        bodies.add(p.read_text())
    assert bodies == {"CONTENT_A_UNIQUE", "CONTENT_B_UNIQUE"}


def test_add_same_stem_concurrent_copy_no_silent_loss(tmp_path):
    """#81 E1:同 stem + copy 并发 add → 两条 ref,双方正文都在,不静默丢。"""
    from concurrent.futures import ThreadPoolExecutor

    ws = Workspace.init(tmp_path)
    a = tmp_path / "a" / "note.txt"
    b = tmp_path / "b" / "note.txt"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text("CONTENT_A_UNIQUE")
    b.write_text("CONTENT_B_UNIQUE")

    def _add(p):
        return ws.add([p], copy=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        r1, r2 = list(pool.map(_add, [a, b]))

    assert r1 != r2
    assert {r1, r2} <= set(ws.list_reference_ids())
    bodies = set()
    for rid in (r1, r2):
        man = ws.read_manifest(rid)
        assert len(man.forms) >= 1
        loc = man.forms[0].location
        p = __import__("pathlib").Path(loc)
        if not p.is_absolute():
            p = ws.root / loc
        bodies.add(p.read_text())
    assert "CONTENT_A_UNIQUE" in bodies
    assert "CONTENT_B_UNIQUE" in bodies


def test_add_explicit_ref_id_still_appends(tmp_path):
    """显式 --to / ref_id 追加形态语义保留(非自动 id 场景)。"""
    ws = Workspace.init(tmp_path)
    a = tmp_path / "a.txt"
    a.write_text("aaa")
    rid = ws.add([a])
    b = tmp_path / "b.md"
    b.write_text("bbb")
    assert ws.add([b], ref_id=rid) == rid
    man = ws.read_manifest(rid)
    assert len(man.forms) == 2


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
    before = _dt.datetime.now()
    rid = ws.add([d])
    after = _dt.datetime.now()
    assert rid == f"{_dt.date.today().isoformat()}-能源讨论"
    man = ws.read_manifest(rid)
    assert man.source_class == "stream"
    # #103:默认 title 为 YYYYMMDD-HH,不再用目录名
    import re as _re

    assert _re.fullmatch(r"\d{8}-\d{2}", man.title)
    assert man.title in {
        before.strftime("%Y%m%d-%H"),
        after.strftime("%Y%m%d-%H"),
    }
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


# ---- #103: 默认 title = YYYYMMDD-HH ----


def test_default_reference_title_format():
    """纯函数:本地时间 → YYYYMMDD-HH(零填充小时)。"""
    from kairo.workspace import default_reference_title

    assert default_reference_title(now=datetime.datetime(2026, 7, 28, 14, 35, 0)) == (
        "20260728-14"
    )
    assert default_reference_title(now=datetime.datetime(2026, 1, 2, 9, 0, 0)) == (
        "20260102-09"
    )
    assert default_reference_title(now=datetime.datetime(2026, 12, 31, 0, 59, 59)) == (
        "20261231-00"
    )


def test_add_default_title_is_yyyymmdd_hh(tmp_path):
    """#103 S1:未指定 title 时,manifest.title 为本地时间 YYYYMMDD-HH,非文件 stem。"""
    import re
    from unittest.mock import patch

    from kairo.workspace import default_reference_title

    ws = Workspace.init(tmp_path)
    src = tmp_path / "long-recording-name.m4a"
    src.write_bytes(b"\x00fake")
    frozen = datetime.datetime(2026, 7, 28, 14, 35, 0)
    with patch(
        "kairo.workspace.default_reference_title",
        side_effect=lambda now=None: default_reference_title(now=frozen),
    ):
        rid = ws.add([src])
    man = ws.read_manifest(rid)
    assert man.title == "20260728-14"
    assert re.fullmatch(r"\d{8}-\d{2}", man.title)
    assert man.title != "long-recording-name"
    # ref_id 仍按既有规则(日期 slug),不改成 title 格式
    assert rid == f"{datetime.date.today().isoformat()}-long-recording-name"


def test_add_default_title_live_clock_matches_local_hour(tmp_path):
    """#103 S1:真实时钟路径 — title 等于 add 前后本地小时之一(跨小时边界安全)。"""
    import re

    ws = Workspace.init(tmp_path)
    src = tmp_path / "note.txt"
    src.write_text("x")
    before = datetime.datetime.now()
    rid = ws.add([src])
    after = datetime.datetime.now()
    title = ws.read_manifest(rid).title
    assert re.fullmatch(r"\d{8}-\d{2}", title)
    assert title in {
        before.strftime("%Y%m%d-%H"),
        after.strftime("%Y%m%d-%H"),
    }


def test_add_explicit_title_preserved(tmp_path):
    """#103 S2:显式 title= 不被默认规则覆盖。"""
    ws = Workspace.init(tmp_path)
    src = tmp_path / "rec.m4a"
    src.write_bytes(b"a")
    rid = ws.add([src], title="周会")
    assert ws.read_manifest(rid).title == "周会"


def test_add_dir_stream_default_title_is_yyyymmdd_hh(tmp_path):
    """#103 S1:目录 stream 默认 title 也是时间格式,非目录名。"""
    from unittest.mock import patch

    from kairo.workspace import default_reference_title

    ws = Workspace.init(tmp_path / "ws")
    d = tmp_path / "能源讨论"
    d.mkdir()
    (d / "a.m4a").write_bytes(b"a")
    frozen = datetime.datetime(2026, 7, 28, 9, 1, 0)
    with patch(
        "kairo.workspace.default_reference_title",
        side_effect=lambda now=None: default_reference_title(now=frozen),
    ):
        rid = ws.add([d])
    man = ws.read_manifest(rid)
    assert man.title == "20260728-09"
    assert man.title != "能源讨论"
    # ref_id 仍含目录 slug
    assert "能源讨论" in rid


def test_add_corpus_tree_default_title_is_yyyymmdd_hh(tmp_path):
    """#103 S1:corpus 目录默认 title 为 YYYYMMDD-HH。"""
    from unittest.mock import patch

    from kairo.workspace import default_reference_title

    ws = Workspace.init(tmp_path)
    d = tmp_path / "baseline_docs"
    d.mkdir()
    (d / "a.md").write_text("x")
    frozen = datetime.datetime(2026, 3, 5, 23, 0, 0)
    with patch(
        "kairo.workspace.default_reference_title",
        side_effect=lambda now=None: default_reference_title(now=frozen),
    ):
        rid = ws.add([d], source_class="corpus")
    assert ws.read_manifest(rid).title == "20260305-23"


def test_add_append_does_not_rewrite_title(tmp_path):
    """#103:向既有 ref 追加 form 时不发明/覆盖 title。"""
    ws = Workspace.init(tmp_path)
    a = tmp_path / "a.txt"
    a.write_text("a")
    rid = ws.add([a], title="历史名")
    b = tmp_path / "b.png"
    b.write_bytes(b"\x89PNG")
    ws.add([b], ref_id=rid)
    man = ws.read_manifest(rid)
    assert man.title == "历史名"
    assert len(man.forms) == 2


def test_set_title_after_default_add_preserves_id_and_forms(tmp_path):
    """#103 S3:默认 title 新增后,set_title 只改展示名。"""
    ws = Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "260629_110439.txt"
    src.write_text("内容")
    rid = ws.add([src])
    before = ws.read_manifest(rid)
    assert before.title != "260629_110439"  # 不再用 stem
    ws.set_title(rid, "数字员工架构对齐")
    man = ws.read_manifest(rid)
    assert man.title == "数字员工架构对齐"
    assert man.id == before.id
    assert man.source_class == before.source_class
    assert man.forms == before.forms
